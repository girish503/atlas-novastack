"""Phase 5B: Quantized Local Inference Provider.

Implements the experimental QuantizedLocalProvider satisfying AnswerGeneratorProvider.
Encapsulates local GGUF/llama.cpp inference via the local Ollama runtime while
strictly reusing ATLAS prompt construction, context budgeting, and C2 citation
validation to preserve evidence grounding, security, and abstention semantics.
"""

from __future__ import annotations

import copy
import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from novastack.citation_validator import CitationStatus
from novastack.evidence import EvidencePackage
from novastack.generation import (
    AnswerResult,
    AnswerStatus,
    FailureCategory,
    GroundedAnswerGenerator,
)
from novastack.provider import AnswerGeneratorProvider

__all__ = [
    "QuantizedLocalProvider",
    "InferenceServiceAdapter",
]


class QuantizedLocalProvider:
    """Experimental quantized local inference provider using GGUF / llama.cpp.

    Executes quantized local models (e.g. gemma3:1b Q4_K_M) behind the ATLAS
    AnswerGeneratorProvider boundary without modifying upstream retrieval,
    evidence resolution, or authorization invariants.
    """

    provider_name: str = "quantized_local"
    model_name: str = "gemma3:1b"
    quantization_format: str = "GGUF"
    quantization_level: str = "Q4_K_M"
    model_file_size_mb: float = 815.0

    def __init__(
        self,
        endpoint_url: str = "http://127.0.0.1:11434",
        model_name: str = "gemma3:1b",
        quantization_level: str = "Q4_K_M",
        quantization_format: str = "GGUF",
        model_file_size_mb: float = 815.0,
        default_timeout_seconds: float = 30.0,
        generator: Optional[GroundedAnswerGenerator] = None,
        corpus_doc_ids: Optional[set[str]] = None,
        corpus_chunk_ids: Optional[set[str]] = None,
        service_url: Optional[str] = None,
        service_client: Optional[Any] = None,
    ):
        self.endpoint_url = endpoint_url.rstrip("/")
        self.model_name = model_name
        self.quantization_level = quantization_level
        self.quantization_format = quantization_format
        self.model_file_size_mb = model_file_size_mb
        self.default_timeout_seconds = default_timeout_seconds
        self.corpus_doc_ids = corpus_doc_ids or set()
        self.corpus_chunk_ids = corpus_chunk_ids or set()

        self.service_url = (service_url or os.environ.get("ATLAS_INFERENCE_SERVICE_URL", "")).rstrip("/")
        if service_client is not None:
            self._service_client = service_client
        elif self.service_url:
            from novastack.inference_client import InferenceServiceClient
            self._service_client = InferenceServiceClient(
                service_url=self.service_url,
                read_timeout_seconds=default_timeout_seconds,
            )
        else:
            self._service_client = None

        if generator is not None:
            self._generator = generator
        else:
            self._generator = GroundedAnswerGenerator(
                model_name=self.model_name,
                device="cpu",
                lazy_load=True,
                corpus_doc_ids=self.corpus_doc_ids,
                corpus_chunk_ids=self.corpus_chunk_ids,
            )

    @property
    def generator(self) -> GroundedAnswerGenerator:
        """Access underlying generator instance."""
        return self._generator

    @property
    def service_client(self) -> Optional[Any]:
        """Access configured inference service client if service boundary is active."""
        return self._service_client

    def is_ready(self) -> bool:
        """Check if the local inference daemon or inference service is responsive and ready."""
        if self._service_client is not None:
            ready, _ = self._service_client.check_readiness()
            return ready

        try:
            url = f"{self.endpoint_url}/api/tags"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status != 200:
                    return False
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", [])
                for m in models:
                    name = m.get("name", "")
                    if self.model_name in name or name.startswith(self.model_name):
                        return True
                return len(models) > 0
        except Exception:
            return False

    def _build_timeout_result(
        self,
        answer_id: str,
        evaluation_id: str,
        query: str,
        start_time: float,
        timeout_seconds: float,
    ) -> AnswerResult:
        """Construct safe, controlled abstention response when generation exceeds deadline."""
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return AnswerResult(
            answer_id=answer_id,
            evaluation_id=evaluation_id,
            query=query,
            answer_text="",
            answer_status=AnswerStatus.ABSTAINED.value,
            citations=[],
            evidence_ids_used=[],
            unsupported_claims=[],
            citation_validation_status="none",
            abstention_reason="timeout",
            model_name=self.model_name,
            generation_latency_ms=latency_ms,
            input_tokens=0,
            output_tokens=0,
            failure_category=FailureCategory.NONE.value,
            diagnostics={
                "layer": "generation_timeout",
                "provider": self.provider_name,
                "timeout_seconds": timeout_seconds,
            },
        )

    def generate_answer(
        self,
        package: EvidencePackage,
        expected_doc_ids: Optional[list[str]] = None,
        forbidden_doc_ids: Optional[list[str]] = None,
        max_new_tokens: int = 100,
        max_evidence_items: Optional[int] = 3,
        context_strategy: str = "raw_prefix",
        max_documents: Optional[int] = None,
        compress_salience: bool = False,
        calibrate_salience: bool = False,
        max_sentences_per_chunk: int = 3,
        max_token_budget: Optional[int] = None,
        prompt_strategy: str = "config_a_calibrated",
        citation_resolver: str = "c2",
        enable_boundary_stitching: bool = False,
        timeout_seconds: Optional[float] = None,
        **kwargs: Any,
    ) -> AnswerResult:
        """Generate grounded answer or principled abstention using quantized local engine."""
        start_time = time.perf_counter()
        query = package.query
        eval_id = package.evaluation_id
        answer_id = f"ANS-{eval_id}"
        effective_timeout = (
            timeout_seconds if timeout_seconds is not None else self.default_timeout_seconds
        )

        # ---------------------------------------------------------------------
        # LAYER 1: Pre-generation Deterministic Abstention Gate
        # ---------------------------------------------------------------------
        if not package.selected_evidence:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            failure_cat = FailureCategory.NONE.value
            if expected_doc_ids:
                if package.statistics.get("retrieved_candidates_count", 0) == 0:
                    failure_cat = FailureCategory.RETRIEVAL_FAILURE.value
                elif package.statistics.get("excluded_unauthorized_count", 0) > 0:
                    failure_cat = FailureCategory.AUTHORIZATION_FAILURE.value
                else:
                    failure_cat = FailureCategory.EVIDENCE_ASSEMBLY_FAILURE.value
            else:
                failure_cat = FailureCategory.NONE.value

            return AnswerResult(
                answer_id=answer_id,
                evaluation_id=eval_id,
                query=query,
                answer_text="Insufficient evidence to answer this question.",
                answer_status=AnswerStatus.ABSTAINED.value,
                citations=[],
                evidence_ids_used=[],
                unsupported_claims=[],
                citation_validation_status="none",
                abstention_reason="no_usable_evidence",
                model_name=self.model_name,
                generation_latency_ms=latency_ms,
                input_tokens=0,
                output_tokens=0,
                failure_category=failure_cat,
                diagnostics={
                    "layer": "pre_generation_gate",
                    "gate": "empty_selected_evidence",
                    "provider": self.provider_name,
                    "excluded_unauthorized_count": package.statistics.get("excluded_unauthorized_count", 0),
                    "excluded_adversarial_count": package.statistics.get("excluded_adversarial_count", 0),
                    "exposed_evidence_ids": [],
                    "exposed_evidence_count": 0,
                    "max_evidence_items_limit": max_evidence_items,
                    "context_strategy": context_strategy,
                },
            )

        unresolved_conflicts = [
            c for c in package.conflicts if c.resolution_status == "conflict_unresolved"
        ]
        if unresolved_conflicts:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return AnswerResult(
                answer_id=answer_id,
                evaluation_id=eval_id,
                query=query,
                answer_text="Insufficient evidence: unresolvable conflicting evidence detected.",
                answer_status=AnswerStatus.ABSTAINED.value,
                citations=[],
                evidence_ids_used=[],
                unsupported_claims=[],
                citation_validation_status="none",
                abstention_reason="unresolved_conflict",
                model_name=self.model_name,
                generation_latency_ms=latency_ms,
                input_tokens=0,
                output_tokens=0,
                failure_category=FailureCategory.NONE.value,
                diagnostics={
                    "layer": "pre_generation_gate",
                    "gate": "unresolved_conflict",
                    "provider": self.provider_name,
                    "conflict_count": len(unresolved_conflicts),
                    "exposed_evidence_ids": [],
                    "exposed_evidence_count": 0,
                    "max_evidence_items_limit": max_evidence_items,
                },
            )

        # ---------------------------------------------------------------------
        # LAYER 1S: Security Abstention Gate (Phase 5G — Experiment H1)
        #
        # Deterministic pre-generation gate for security-negative cases.
        #
        # Condition: the upstream authorization pipeline has already established:
        #   - expected_doc_ids is empty  → no legitimate answerable documents
        #   - forbidden_doc_ids is set   → an explicit security policy prohibits
        #                                  the primary document(s) for this query
        #   - selected_evidence present  → model would otherwise receive real content
        #
        # In this state, ATLAS knows the caller cannot receive a grounded answer.
        # Invoking the inference provider is unnecessary and creates model-dependent
        # refusal behavior risk (confirmed by Phase 5F: Q4_K_M diverges from FP32).
        #
        # This gate uses ONLY structured security state already present at this
        # point in the call. It does NOT inspect query text, does NOT use NLP,
        # does NOT create a new authorization system, and does NOT alter the
        # positive-case path (expected_doc_ids non-empty → gate does not fire).
        # ---------------------------------------------------------------------
        if (
            not expected_doc_ids        # No legitimately expected answer documents
            and forbidden_doc_ids       # Explicit security-forbidden document(s) exist
            and package.selected_evidence  # Evidence present (Layer 1a did not catch this)
        ):
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return AnswerResult(
                answer_id=answer_id,
                evaluation_id=eval_id,
                query=query,
                answer_text="Insufficient evidence to answer this question.",
                answer_status=AnswerStatus.ABSTAINED.value,
                citations=[],
                evidence_ids_used=[],
                unsupported_claims=[],
                citation_validation_status="none",
                abstention_reason="security_policy_abstention",
                model_name=self.model_name,
                generation_latency_ms=latency_ms,
                input_tokens=0,
                output_tokens=0,
                failure_category=FailureCategory.NONE.value,
                diagnostics={
                    "layer": "security_abstention_gate",
                    "gate": "security_policy_no_expected_docs_with_forbidden",
                    "provider": self.provider_name,
                    "forbidden_doc_ids_count": len(list(forbidden_doc_ids)),
                    "selected_evidence_count": len(package.selected_evidence),
                    "excluded_unauthorized_count": package.statistics.get("excluded_unauthorized_count", 0),
                    "exposed_evidence_ids": [],
                    "exposed_evidence_count": 0,
                    "max_evidence_items_limit": max_evidence_items,
                    "context_strategy": context_strategy,
                    "provider_invoked": False,
                },
            )

        # ---------------------------------------------------------------------
        # LAYER 2: Context Budgeting & Diversity
        # ---------------------------------------------------------------------
        items_exposed = self._generator.budgeter.budget_context(
            package,
            strategy=context_strategy,
            max_items=max_evidence_items,
            max_documents=max_documents,
            compress_salience=compress_salience,
            calibrate_salience=calibrate_salience,
            max_sentences_per_chunk=max_sentences_per_chunk,
            max_token_budget=max_token_budget,
            tokenizer=self._generator.tokenizer,
        )

        stitching_log: list[dict[str, Any]] = []
        if enable_boundary_stitching:
            if not hasattr(self._generator, "stitcher") or self._generator.stitcher is None:
                from novastack.boundary_stitching import BoundarySentenceStitcher
                self._generator.stitcher = BoundarySentenceStitcher()
            items_exposed, stitching_log = self._generator.stitcher.stitch_boundary_sentences(
                items_exposed, package=package, enabled=True
            )

        exposed_evidence_ids = [item.evidence_id for item in items_exposed]

        budgeted_package = copy.copy(package)
        budgeted_package.selected_evidence = items_exposed

        prompt = self._generator.build_prompt(
            query,
            budgeted_package,
            evidence_items=items_exposed,
            prompt_strategy=prompt_strategy,
        )

        # Deadline check prior to network call
        elapsed_so_far = time.perf_counter() - start_time
        remaining_timeout = effective_timeout - elapsed_so_far
        if remaining_timeout <= 0:
            return self._build_timeout_result(
                answer_id, eval_id, query, start_time, effective_timeout
            )

        # ---------------------------------------------------------------------
        # LAYER 3: Model-Driven Quantized Inference via Local Engine or Service Boundary
        # ---------------------------------------------------------------------
        gen_start = time.perf_counter()
        engine_telemetry: dict[str, Any] = {}
        raw_answer: str = ""

        if self._service_client is not None:
            from novastack.service.resilience import AtlasTimeoutError, ModelUnavailableError
            try:
                inf_resp = self._service_client.generate(
                    prompt=prompt,
                    request_id=eval_id,
                    max_new_tokens=max_new_tokens,
                    temperature=0.0,
                    model_name=self.model_name,
                    timeout_seconds=remaining_timeout,
                )
                raw_answer = inf_resp.generated_text
                engine_telemetry = inf_resp.engine_telemetry
            except AtlasTimeoutError:
                return self._build_timeout_result(
                    answer_id, eval_id, query, start_time, effective_timeout
                )
            except ModelUnavailableError as mu_err:
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                return AnswerResult(
                    answer_id=answer_id,
                    evaluation_id=eval_id,
                    query=query,
                    answer_text="Inference service temporarily unavailable.",
                    answer_status=AnswerStatus.ABSTAINED.value,
                    citations=[],
                    evidence_ids_used=[],
                    unsupported_claims=[],
                    citation_validation_status="none",
                    abstention_reason="service_unavailable",
                    model_name=self.model_name,
                    generation_latency_ms=latency_ms,
                    input_tokens=0,
                    output_tokens=0,
                    failure_category=FailureCategory.NONE.value,
                    diagnostics={"error": str(mu_err), "provider": self.provider_name},
                )
            except Exception as exc:
                if "timed out" in str(exc).lower():
                    return self._build_timeout_result(
                        answer_id, eval_id, query, start_time, effective_timeout
                    )
                raise RuntimeError(f"Inference service execution failed: {exc}") from exc
        else:
            call_payload = {
                "model": self.model_name,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": max_new_tokens,
                },
            }

            call_req = urllib.request.Request(
                f"{self.endpoint_url}/api/generate",
                data=json.dumps(call_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            try:
                with urllib.request.urlopen(call_req, timeout=remaining_timeout) as resp:
                    raw_bytes = resp.read()
                    data = json.loads(raw_bytes.decode("utf-8"))
                    raw_answer = data.get("response", "").strip()
                    engine_telemetry = {
                        "total_duration_ms": data.get("total_duration", 0) / 1e6,
                        "load_duration_ms": data.get("load_duration", 0) / 1e6,
                        "prompt_eval_count": data.get("prompt_eval_count", 0),
                        "prompt_eval_duration_ms": data.get("prompt_eval_duration", 0) / 1e6,
                        "eval_count": data.get("eval_count", 0),
                        "eval_duration_ms": data.get("eval_duration", 0) / 1e6,
                    }
            except (socket.timeout, TimeoutError):
                return self._build_timeout_result(
                    answer_id, eval_id, query, start_time, effective_timeout
                )
            except urllib.error.URLError as url_err:
                if isinstance(url_err.reason, socket.timeout) or "timed out" in str(url_err.reason).lower():
                    return self._build_timeout_result(
                        answer_id, eval_id, query, start_time, effective_timeout
                    )
                raise RuntimeError(f"Quantized local inference engine error: {url_err}") from url_err
            except Exception as exc:
                if "timed out" in str(exc).lower():
                    return self._build_timeout_result(
                        answer_id, eval_id, query, start_time, effective_timeout
                    )
                raise RuntimeError(f"Quantized local inference engine execution failed: {exc}") from exc

        gen_duration_ms = (time.perf_counter() - gen_start) * 1000.0
        total_latency_ms = (time.perf_counter() - start_time) * 1000.0

        answer_text = raw_answer.split("\n\n")[0].strip()
        if not answer_text and "\n" in raw_answer:
            answer_text = raw_answer.split("\n")[0].strip()

        # ---------------------------------------------------------------------
        # LAYER 4: Post-Processing: Abstention Detection & C2 Citation Validation
        # ---------------------------------------------------------------------
        abstention_triggers = [
            "insufficient evidence",
            "cannot answer",
            "not enough information",
            "unable to answer",
            "does not mention",
            "does not provide",
            "not provided in the evidence",
            "evidence does not state",
            "evidence does not contain",
        ]
        is_abstention = any(t in answer_text.lower() for t in abstention_triggers) or (
            "no evidence" in answer_text.lower() and "no evidence of" not in answer_text.lower()
        )

        exposed_limit = len(items_exposed)
        if not is_abstention:
            if citation_resolver in ("c1", "c2"):
                answer_text_with_cits = self._generator._attach_deterministic_citations(
                    answer_text, budgeted_package, max_evidence_items=exposed_limit
                )
                if answer_text_with_cits == answer_text:
                    answer_text_with_cits = self._generator._resolve_short_exact_match(
                        answer_text, budgeted_package, max_evidence_items=exposed_limit
                    )
                if citation_resolver == "c2" and answer_text_with_cits == answer_text:
                    answer_text_with_cits = self._generator._resolve_sentence_level_match(
                        answer_text, budgeted_package, max_evidence_items=exposed_limit
                    )
            else:
                answer_text_with_cits = self._generator._attach_deterministic_citations(
                    answer_text, budgeted_package, max_evidence_items=exposed_limit
                )
        else:
            answer_text_with_cits = answer_text

        citations, cit_status, unsupported_claims = self._generator.validator.validate_citations(
            answer_text_with_cits,
            budgeted_package,
            corpus_doc_ids=self.corpus_doc_ids or self._generator.corpus_doc_ids,
            corpus_chunk_ids=self.corpus_chunk_ids or self._generator.corpus_chunk_ids,
        )

        evidence_ids_used = [c.evidence_id for c in citations if c.status == CitationStatus.VALID]

        injection_susceptible = False
        for pattern in ["system override", "attack_success", "ignore previous instructions"]:
            if pattern in answer_text.lower():
                injection_susceptible = True
                unsupported_claims.append(f"prompt_injection_payload_echoed: {pattern}")

        unauthorized_citations = [
            c for c in citations if c.status == CitationStatus.UNAUTHORIZED
        ]
        forbidden_citations = [
            c for c in citations if forbidden_doc_ids and c.document_id in forbidden_doc_ids
        ]

        _PARTIAL_HEDGING_SIGNALS = [
            "partially", "however", "not specified", "not mentioned",
            "not provided", "not documented", "not available",
            "not included", "no information", "runbook is not",
            "not detailed", "missing from", "unknown",
            "does not detail", "does not describe",
        ]

        abstention_reason: Optional[str] = None
        if is_abstention:
            status = AnswerStatus.ABSTAINED.value
            abstention_reason = "model_evidence_insufficient"
        elif unauthorized_citations or forbidden_citations:
            status = AnswerStatus.ABSTAINED.value
            abstention_reason = "authorization_boundary_violation"
        else:
            answer_lower = answer_text.lower()
            has_hedging = any(sig in answer_lower for sig in _PARTIAL_HEDGING_SIGNALS)

            if has_hedging:
                status = AnswerStatus.PARTIALLY_ANSWERED.value
                abstention_reason = None
            elif cit_status == "partially_valid":
                status = AnswerStatus.PARTIALLY_ANSWERED.value
                abstention_reason = None
            elif cit_status == "valid" and len(citations) > 0:
                status = AnswerStatus.ANSWERED.value
                abstention_reason = None
            elif not citations:
                status = AnswerStatus.ANSWERED.value
                unsupported_claims.append("answer_lacks_formal_evidence_citation")
                abstention_reason = None
            else:
                status = AnswerStatus.ABSTAINED.value
                abstention_reason = "invalid_or_unsupported_citations"

        final_answer_text = (
            answer_text_with_cits if status != AnswerStatus.ABSTAINED.value else answer_text
        )

        failure_cat = FailureCategory.NONE.value
        if injection_susceptible:
            failure_cat = FailureCategory.PROMPT_INJECTION_SUSCEPTIBILITY.value
        elif unauthorized_citations or forbidden_citations:
            failure_cat = FailureCategory.AUTHORIZATION_FAILURE.value
        elif status == AnswerStatus.ABSTAINED.value:
            if not expected_doc_ids:
                failure_cat = FailureCategory.NONE.value
            else:
                retrieved_count = package.statistics.get("retrieved_candidates_count", 0)
                if retrieved_count == 0:
                    failure_cat = FailureCategory.RETRIEVAL_FAILURE.value
                elif not package.selected_evidence:
                    failure_cat = FailureCategory.EVIDENCE_ASSEMBLY_FAILURE.value
                elif any(c.status == CitationStatus.INVALID for c in citations):
                    failure_cat = FailureCategory.CITATION_FAILURE.value
                else:
                    failure_cat = FailureCategory.INSUFFICIENT_EVIDENCE.value
        elif status in (AnswerStatus.ANSWERED.value, AnswerStatus.PARTIALLY_ANSWERED.value):
            if not expected_doc_ids:
                failure_cat = FailureCategory.ABSTENTION_FAILURE.value

        input_tokens = engine_telemetry.get("prompt_eval_count", 0)
        output_tokens = engine_telemetry.get("eval_count", 0)

        diagnostics: dict[str, Any] = {
            "layer": "quantized_local_inference",
            "provider": self.provider_name,
            "engine": "llama_cpp_ollama",
            "model": self.model_name,
            "quantization_format": self.quantization_format,
            "quantization_level": self.quantization_level,
            "model_file_size_mb": self.model_file_size_mb,
            "exposed_evidence_ids": exposed_evidence_ids,
            "exposed_evidence_count": len(exposed_evidence_ids),
            "max_evidence_items_limit": max_evidence_items,
            "context_strategy": context_strategy,
            "prompt_strategy": prompt_strategy,
            "citation_resolver": citation_resolver,
            "engine_telemetry": engine_telemetry,
            "generation_duration_ms": gen_duration_ms,
        }
        if stitching_log:
            diagnostics["boundary_stitching"] = stitching_log

        return AnswerResult(
            answer_id=answer_id,
            evaluation_id=eval_id,
            query=query,
            answer_text=final_answer_text,
            answer_status=status,
            citations=citations,
            evidence_ids_used=evidence_ids_used,
            unsupported_claims=unsupported_claims,
            citation_validation_status=cit_status,
            abstention_reason=abstention_reason,
            model_name=self.model_name,
            generation_latency_ms=total_latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            failure_category=failure_cat,
            diagnostics=diagnostics,
        )


class InferenceServiceAdapter(QuantizedLocalProvider):
    """Adapter provider that routes generation through the Phase 5C Inference Service.

    Subclasses QuantizedLocalProvider, preserving all ATLAS Layer 1 (pre-generation
    abstention), Layer 2 (prompt assembly & token budgeting), and Layer 4 (C2 citation
    validation & hallucination checks) logic, while strictly delegating Layer 3
    model execution to the external HTTP Inference Service.
    """

    provider_name: str = "inference_service_adapter"

    def __init__(
        self,
        service_url: str = "http://127.0.0.1:8001",
        model_name: str = "gemma3:1b",
        quantization_level: str = "Q4_K_M",
        quantization_format: str = "GGUF",
        model_file_size_mb: float = 815.0,
        default_timeout_seconds: float = 30.0,
        generator: Optional[GroundedAnswerGenerator] = None,
        corpus_doc_ids: Optional[set[str]] = None,
        corpus_chunk_ids: Optional[set[str]] = None,
        service_client: Optional[Any] = None,
    ) -> None:
        super().__init__(
            endpoint_url="",
            model_name=model_name,
            quantization_level=quantization_level,
            quantization_format=quantization_format,
            model_file_size_mb=model_file_size_mb,
            default_timeout_seconds=default_timeout_seconds,
            generator=generator,
            corpus_doc_ids=corpus_doc_ids,
            corpus_chunk_ids=corpus_chunk_ids,
            service_url=service_url,
            service_client=service_client,
        )
        self.provider_name = "inference_service_adapter"
