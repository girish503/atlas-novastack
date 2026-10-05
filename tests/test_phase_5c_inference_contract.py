"""Phase 5C Test Suite: InferenceServiceAdapter Contract Tests.

Verifies that InferenceServiceAdapter:
  1. Satisfies the AnswerGeneratorProvider protocol.
  2. Correctly delegates Layer 3 to the inference service client.
  3. Preserves Layer 1/2/4 behavior.
  4. Maps timeout / unavailable exceptions to valid AnswerResult abstentions.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from novastack.evidence import (
    EvidenceConflict,
    EvidenceItem,
    EvidencePackage,
    EvidenceStatus,
)
from novastack.generation import AnswerResult, AnswerStatus
from novastack.models import RecordPermissions
from novastack.provider import AnswerGeneratorProvider, InferenceServiceAdapter
from novastack.inference_service.schemas import InferenceGenerationResponse
from novastack.service.resilience import AtlasTimeoutError, ModelUnavailableError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DOC_ID = "doc-contract-001"
CHUNK_ID = "chunk-contract-001"
TENANT_ID = "TENANT-CONTRACT"


def _make_evidence_item(
    evidence_id: str = CHUNK_ID,
    chunk_id: str = CHUNK_ID,
    document_id: str = DOC_ID,
    text: str = "ATLAS evidence grounding ensures all answers are sourced from authorized documents.",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        chunk_id=chunk_id,
        document_id=document_id,
        tenant_id=TENANT_ID,
        source_type="documentation",
        title="ATLAS Design Doc",
        text=text,
        source_entity_id=None,
        source_entity_type=None,
        related_entity_ids=[],
        authority_level="authoritative",
        classification="internal",
        permissions=RecordPermissions(allowed_roles=["engineer"]),
        status="published",
        version="v1.0",
        created_at="2026-01-01T00:00:00Z",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        parent_id=None,
        supersedes_id=None,
        retrieval_rank=1,
        retrieval_score=0.95,
        retrieval_channels=["bm25", "dense"],
        evidence_status=EvidenceStatus.ACCEPTED.value,
        evidence_reasons=[],
    )


def _make_package(has_evidence: bool = True) -> EvidencePackage:
    evidence = [_make_evidence_item()] if has_evidence else []
    return EvidencePackage(
        package_id="PKG-CONTRACT-01",
        evaluation_id="EVAL-CONTRACT-01",
        query="What is ATLAS evidence grounding?",
        tenant_id=TENANT_ID,
        user_context={"tenant_id": TENANT_ID, "roles": ["engineer"]},
        selected_evidence=evidence,
        excluded_evidence=[],
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={"retrieved_candidates_count": 1 if has_evidence else 0},
    )


def _make_inf_response(text: str = "ATLAS grounding is verified.") -> InferenceGenerationResponse:
    return InferenceGenerationResponse(
        generated_text=text,
        request_id="req-contract",
        model_name="gemma3:1b",
        generation_latency_ms=800.0,
        engine_telemetry={"prompt_eval_count": 80, "eval_count": 20},
    )


def _make_adapter(service_client: Any = None) -> InferenceServiceAdapter:
    return InferenceServiceAdapter(
        service_url="http://127.0.0.1:8001",
        corpus_doc_ids={DOC_ID},
        corpus_chunk_ids={CHUNK_ID},
        service_client=service_client,
    )


# ---------------------------------------------------------------------------
# Protocol Conformance
# ---------------------------------------------------------------------------

class TestProviderConformance:
    def test_adapter_satisfies_provider_protocol(self):
        adapter = InferenceServiceAdapter(service_url="http://127.0.0.1:8001")
        assert isinstance(adapter, AnswerGeneratorProvider)

    def test_adapter_has_provider_name(self):
        adapter = _make_adapter()
        assert adapter.provider_name == "inference_service_adapter"

    def test_adapter_has_generate_answer(self):
        adapter = _make_adapter()
        assert callable(getattr(adapter, "generate_answer", None))

    def test_adapter_has_is_ready(self):
        adapter = _make_adapter()
        assert callable(getattr(adapter, "is_ready", None))


# ---------------------------------------------------------------------------
# is_ready
# ---------------------------------------------------------------------------

class TestIsReady:
    def test_is_ready_returns_true_when_service_client_ready(self):
        mock_client = MagicMock()
        mock_client.check_readiness.return_value = (True, {"status": "ready"})
        adapter = _make_adapter(service_client=mock_client)
        assert adapter.is_ready() is True

    def test_is_ready_returns_false_when_service_client_not_ready(self):
        mock_client = MagicMock()
        mock_client.check_readiness.return_value = (False, {"status": "not_ready"})
        adapter = _make_adapter(service_client=mock_client)
        assert adapter.is_ready() is False


# ---------------------------------------------------------------------------
# generate_answer — Layer 1 abstention (no evidence)
# ---------------------------------------------------------------------------

class TestGenerateAnswerLayer1:
    def test_abstains_when_no_evidence(self):
        mock_client = MagicMock()
        adapter = _make_adapter(service_client=mock_client)
        pkg = _make_package(has_evidence=False)

        result = adapter.generate_answer(pkg)

        assert isinstance(result, AnswerResult)
        assert result.answer_status == AnswerStatus.ABSTAINED.value
        mock_client.generate.assert_not_called()

    def test_abstains_reason_in_result(self):
        mock_client = MagicMock()
        adapter = _make_adapter(service_client=mock_client)
        pkg = _make_package(has_evidence=False)

        result = adapter.generate_answer(pkg)

        assert result.abstention_reason is not None


# ---------------------------------------------------------------------------
# generate_answer — Layer 3 delegation
# ---------------------------------------------------------------------------

class TestGenerateAnswerLayer3:
    def test_calls_service_client_generate(self):
        mock_client = MagicMock()
        mock_client.generate.return_value = _make_inf_response()
        adapter = _make_adapter(service_client=mock_client)
        pkg = _make_package()

        adapter.generate_answer(pkg)

        mock_client.generate.assert_called_once()

    def test_generate_call_has_no_jwt_or_auth(self):
        call_kwargs: dict = {}

        def capture(**kw):
            call_kwargs.update(kw)
            return _make_inf_response()

        mock_client = MagicMock()
        mock_client.generate.side_effect = capture
        adapter = _make_adapter(service_client=mock_client)
        pkg = _make_package()

        adapter.generate_answer(pkg)

        # Payload must NOT contain any authorization materials
        payload_str = str(call_kwargs).lower()
        for forbidden in ("jwt", "authorization", "bearer", "credential", "tenant_db"):
            assert forbidden not in payload_str, (
                f"Forbidden field '{forbidden}' found in inference service call"
            )

    def test_returns_answer_result_with_generated_text(self):
        mock_client = MagicMock()
        mock_client.generate.return_value = _make_inf_response("Grounding verified by evidence.")
        adapter = _make_adapter(service_client=mock_client)
        pkg = _make_package()

        result = adapter.generate_answer(pkg)

        assert isinstance(result, AnswerResult)
        assert result.answer_text  # non-empty

    def test_provider_name_in_diagnostics(self):
        mock_client = MagicMock()
        mock_client.generate.return_value = _make_inf_response()
        adapter = _make_adapter(service_client=mock_client)
        pkg = _make_package()

        result = adapter.generate_answer(pkg)

        assert "provider" in (result.diagnostics or {})


# ---------------------------------------------------------------------------
# generate_answer — timeout / unavailable mappings
# ---------------------------------------------------------------------------

class TestGenerateAnswerExceptionMapping:
    def test_timeout_maps_to_abstained(self):
        mock_client = MagicMock()
        mock_client.generate.side_effect = AtlasTimeoutError("deadline exceeded")
        adapter = _make_adapter(service_client=mock_client)
        pkg = _make_package()

        result = adapter.generate_answer(pkg)

        assert result.answer_status == AnswerStatus.ABSTAINED.value
        assert result.abstention_reason == "timeout"

    def test_unavailable_maps_to_abstained(self):
        mock_client = MagicMock()
        mock_client.generate.side_effect = ModelUnavailableError("service down")
        adapter = _make_adapter(service_client=mock_client)
        pkg = _make_package()

        result = adapter.generate_answer(pkg)

        assert result.answer_status == AnswerStatus.ABSTAINED.value
        assert result.abstention_reason == "service_unavailable"

    def test_result_is_valid_answer_result_on_timeout(self):
        mock_client = MagicMock()
        mock_client.generate.side_effect = AtlasTimeoutError("timed out")
        adapter = _make_adapter(service_client=mock_client)
        pkg = _make_package()

        result = adapter.generate_answer(pkg)

        assert isinstance(result, AnswerResult)
        assert result.answer_id
        assert result.query
