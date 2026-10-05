"""Phase 5A: Inference Provider & Engine Abstraction Boundary Test Suite.

Verifies:
A. Provider contract (AnswerGeneratorProvider protocol conformance)
B. LocalHuggingFaceProvider construction and compatibility
C. Default application wiring
D. Explicit provider injection
E. API query execution using injected provider
F. AnswerResult schema compatibility
G. Error propagation and timeout handling
H. Resilience and concurrency limiter compatibility
I. Structured citation formatting compatibility
J. Observability stage timing and metrics
K. Security and fail-closed authentication boundary protection
L. Index generation snapshot and ID preservation
M. Architectural decoupling (no direct Transformers imports in api.py)
"""

from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import inspect
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pytest
from fastapi.testclient import TestClient

from novastack.bm25 import BM25Index
from novastack.chunking import chunk_document
from novastack.citation_validator import Citation
from novastack.dense import DenseIndex
from novastack.evidence import EvidenceItem, EvidencePackage, EvidenceStatus
from novastack.evidence_resolution import EvidenceResolver, EvidenceResolverConfig
from novastack.generation import AnswerResult, AnswerStatus, FailureCategory, GroundedAnswerGenerator
from novastack.index_manager import IndexManager
from novastack.metadata_diagnostics import build_metadata_snapshot_index
from novastack.models import RecordPermissions, SearchDocument
from novastack.observability import reset_metrics
from novastack.provider import AnswerGeneratorProvider, LocalHuggingFaceProvider
from novastack.service import (
    AtlasServicePipeline,
    IdentityConfig,
    ResilienceConfig,
    create_app,
)

_TEST_ISSUER = "https://auth.atlas.production.example"
_TEST_AUDIENCE = "atlas-service-api"
_TEST_SECRET = "production-grade-signing-secret-minimum-32-chars-long"


def _make_auth_headers(
    tenant_id: str = "TENANT-NOVASTACK",
    user_id: str = "USR-ENG-01",
    roles: Optional[List[str]] = None,
    departments: Optional[List[str]] = None,
) -> Dict[str, str]:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": _TEST_ISSUER,
        "aud": _TEST_AUDIENCE,
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": roles or ["engineer"],
        "departments": departments or ["Engineering"],
        "iat": now,
        "exp": now + 3600,
    }

    def b64url(d):
        return (
            base64.urlsafe_b64encode(
                json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            .rstrip(b"=")
            .decode("ascii")
        )

    signed_content = f"{b64url(header)}.{b64url(payload)}"
    sig = hmac.new(
        _TEST_SECRET.encode("utf-8"), signed_content.encode("ascii"), hashlib.sha256
    ).digest()
    token = f"{signed_content}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode('ascii')}"
    return {"Authorization": f"Bearer {token}"}


class FakeAnswerGeneratorProvider:
    """Deterministic fake provider for architectural decoupling verification."""

    provider_name: str = "fake_test_provider"

    def __init__(
        self,
        answer_text: str = "Verified enterprise facts for NovaStack [EVD-001].",
        answer_status: str = AnswerStatus.ANSWERED.value,
        abstention_reason: Optional[str] = None,
        raise_exc: Optional[Exception] = None,
        latency_ms: float = 12.5,
    ):
        self.answer_text = answer_text
        self.answer_status = answer_status
        self.abstention_reason = abstention_reason
        self.raise_exc = raise_exc
        self.latency_ms = latency_ms
        self.call_count = 0
        self.last_package: Optional[EvidencePackage] = None
        self.last_kwargs: Dict[str, Any] = {}

    def is_ready(self) -> bool:
        return True

    def generate_answer(
        self, package: EvidencePackage, **kwargs: Any
    ) -> AnswerResult:
        self.call_count += 1
        self.last_package = package
        self.last_kwargs = kwargs

        if self.raise_exc:
            raise self.raise_exc

        cit = Citation(
            raw_tag="[EVD-001]",
            evidence_id="EVD-001",
            document_id="DOC-NOVASTACK-01",
            chunk_id="DOC-NOVASTACK-01#c0",
            title="NovaStack Standard Documentation",
            status="VALID",
        )

        return AnswerResult(
            answer_id=f"ANS-{package.evaluation_id}",
            evaluation_id=package.evaluation_id,
            query=package.query,
            answer_text=self.answer_text,
            answer_status=self.answer_status,
            citations=[cit],
            evidence_ids_used=["EVD-001"],
            unsupported_claims=[],
            citation_validation_status="valid",
            abstention_reason=self.abstention_reason,
            generation_latency_ms=self.latency_ms,
            diagnostics={
                "layer": "model_inference",
                "inference_duration_ms": self.latency_ms,
                "provider": self.provider_name,
            },
        )



class DeterministicEncoder:
    """Fast in-memory encoder with the certified 384-dimensional shape."""

    dimension = 384

    def encode_passages(self, passages: list[str]) -> np.ndarray:
        if not passages:
            return np.empty((0, self.dimension), dtype=np.float32)
        vectors = np.ones((len(passages), self.dimension), dtype=np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors

    def encode_query(self, query: str) -> np.ndarray:
        vector = np.ones(self.dimension, dtype=np.float32)
        return vector / np.linalg.norm(vector)


class PassThroughReranker:
    def rerank(self, candidates, qu=None, metadata_index=None, forbidden_doc_ids=None):
        return candidates


def make_test_documents() -> list[SearchDocument]:
    return [
        SearchDocument(
            document_id="DOC-NOVASTACK-01",
            tenant_id="TENANT-NOVASTACK",
            source_type="documentation",
            title="NovaStack Core Gateway Topology",
            content="NovaStack core topology is active-passive with redundant links across all zones.",
            department="Engineering",
            author_id="USR-ENG-01",
            created_at="2026-01-01T00:00:00",
            permissions=RecordPermissions(),
            authority_level="canonical",
        )
    ]


def build_fast_pipeline(generator: Optional[AnswerGeneratorProvider] = None) -> AtlasServicePipeline:
    """Build a fast, deterministic, in-memory pipeline for decoupling tests."""
    docs = make_test_documents()
    encoder = DeterministicEncoder()
    chunks = [chunk for doc in docs for chunk in chunk_document(doc)]
    bm25 = BM25Index.build_index(chunks)
    dense = DenseIndex(chunks, encoder.encode_passages([c.text for c in chunks]), encoder=encoder)
    metadata = build_metadata_snapshot_index([d.to_dict() for d in docs])
    manager = IndexManager()
    manager.initialize_from_components(docs, chunks, bm25, dense, metadata, corpus_version="5A-test")

    lease = manager.acquire_active_generation()
    assert lease is not None
    try:
        resolver = EvidenceResolver(
            documents_index={d.document_id: d for d in lease.snapshot.search_documents},
            chunks_index={c.chunk_id: c for c in lease.snapshot.search_chunks},
            config=EvidenceResolverConfig(),
        )
        pipeline = AtlasServicePipeline(
            bm25_index=lease.snapshot.bm25_index,
            dense_index=lease.snapshot.dense_index,
            reranker=PassThroughReranker(),
            generator=generator or FakeAnswerGeneratorProvider(),
            resolver=resolver,
            metadata_snapshot_index=dict(lease.snapshot.metadata_snapshot_index),
            index_manager=manager,
        )
    finally:
        lease.close()
    return pipeline


# =============================================================================
# A. Provider Contract (Protocol Conformance)
# =============================================================================

class TestProviderContract:
    def test_local_provider_conforms_to_protocol(self):
        prov = LocalHuggingFaceProvider(lazy_load=True)
        assert isinstance(prov, AnswerGeneratorProvider)
        assert prov.provider_name == "local_huggingface"
        assert prov.is_ready() is True

    def test_fake_provider_conforms_to_protocol(self):
        fake = FakeAnswerGeneratorProvider()
        assert isinstance(fake, AnswerGeneratorProvider)
        assert fake.provider_name == "fake_test_provider"
        assert fake.is_ready() is True

    def test_grounded_generator_conforms_to_protocol(self):
        gen = GroundedAnswerGenerator(lazy_load=True)
        assert isinstance(gen, AnswerGeneratorProvider)
        assert gen.provider_name == "local_huggingface"
        assert gen.is_ready() is True


# =============================================================================
# B. LocalHuggingFaceProvider Construction & Compatibility
# =============================================================================

class TestLocalHuggingFaceProviderConstruction:
    def test_construction_with_defaults(self):
        prov = LocalHuggingFaceProvider(lazy_load=True)
        assert prov.provider_name == "local_huggingface"
        assert prov.generator is not None
        assert prov.model is None  # lazy load
        assert prov.is_ready() is True

    def test_construction_wrapping_existing_generator(self):
        gen = GroundedAnswerGenerator(lazy_load=True)
        prov = LocalHuggingFaceProvider(generator=gen)
        assert prov.generator is gen
        assert prov.provider_name == "local_huggingface"

    def test_model_property_setter(self):
        prov = LocalHuggingFaceProvider(lazy_load=True)
        dummy_model = object()
        prov.model = dummy_model
        assert prov.model is dummy_model
        assert prov.generator.model is dummy_model


# =============================================================================
# C. Default Application Wiring
# =============================================================================

class TestDefaultApplicationWiring:
    def test_pipeline_default_uses_local_provider(self):
        import os
        from unittest.mock import patch
        # In Phase 5J, Backend A remains selectable as rollback control via ATLAS_INFERENCE_PROVIDER=local_huggingface
        with patch.dict(os.environ, {"ATLAS_INFERENCE_PROVIDER": "local_huggingface"}):
            pipe = AtlasServicePipeline.create_default(lazy_generator=True)
            assert isinstance(pipe.generator, LocalHuggingFaceProvider)
            assert pipe.generator.provider_name == "local_huggingface"

    def test_pipeline_is_ready_checks_generator(self):
        pipe = AtlasServicePipeline.create_default(lazy_generator=True)
        all_ready, components = pipe.is_ready()
        assert "generator" in components
        assert components["generator"] is True


# =============================================================================
# D. Explicit Provider Injection
# =============================================================================

class TestExplicitProviderInjection:
    def test_inject_provider_via_create_default(self):
        fake = FakeAnswerGeneratorProvider()
        pipe = AtlasServicePipeline.create_default(lazy_generator=True, generator=fake)
        assert pipe.generator is fake

    def test_inject_provider_via_create_app(self):
        pipe = AtlasServicePipeline.create_default(lazy_generator=True)
        fake = FakeAnswerGeneratorProvider()
        app = create_app(pipeline=pipe, inference_provider=fake)
        assert pipe.generator is fake
        assert app.state.inference_provider is fake


# =============================================================================
# E. API Query Execution Using Injected Provider (Decoupling Verification)
# =============================================================================

class TestApiQueryExecutionWithInjectedProvider:
    def test_end_to_end_query_with_fake_provider(self):
        fake = FakeAnswerGeneratorProvider(
            answer_text="NovaStack core topology is active-passive [EVD-001].",
            latency_ms=18.4,
        )
        pipe = build_fast_pipeline(fake)

        id_cfg = IdentityConfig(
            issuer=_TEST_ISSUER,
            audience=_TEST_AUDIENCE,
            hs256_secret=_TEST_SECRET.encode("utf-8"),
        )
        res_cfg = ResilienceConfig(request_timeout_seconds=30.0)
        app = create_app(pipeline=pipe, resilience_config=res_cfg, identity_config=id_cfg)
        client = TestClient(app, raise_server_exceptions=False)

        headers = _make_auth_headers()
        resp = client.post(
            "/query",
            json={
                "query": "What is the network topology of core-gateway?",
                "user_context": {"tenant_id": "TENANT-NOVASTACK"},
                "evaluation_id": "TEST-5A-001",
            },
            headers=headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["answer_status"] == "answered"
        assert "NovaStack core topology" in data["answer_text"]
        assert len(data["citations"]) > 0
        assert data["was_generation_invoked"] is True
        assert data["generation_latency_ms"] == 18.4
        assert fake.call_count == 1
        assert fake.last_package is not None
        assert fake.last_package.query == "What is the network topology of core-gateway?"

    def test_fake_provider_receives_timeout_kwarg(self):
        fake = FakeAnswerGeneratorProvider()
        pipe = build_fast_pipeline(fake)

        id_cfg = IdentityConfig(
            issuer=_TEST_ISSUER,
            audience=_TEST_AUDIENCE,
            hs256_secret=_TEST_SECRET.encode("utf-8"),
        )
        app = create_app(pipeline=pipe, identity_config=id_cfg)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/query",
            json={
                "query": "Authentication policy requirements",
                "user_context": {"tenant_id": "TENANT-NOVASTACK"},
            },
            headers=_make_auth_headers(),
        )

        assert resp.status_code == 200
        assert "timeout_seconds" in fake.last_kwargs
        assert fake.last_kwargs["timeout_seconds"] > 0


# =============================================================================
# F. AnswerResult Schema Compatibility
# =============================================================================

class TestAnswerResultCompatibility:
    def test_abstention_result_propagates_correctly(self):
        fake = FakeAnswerGeneratorProvider(
            answer_text="Insufficient evidence to answer this question.",
            answer_status=AnswerStatus.ABSTAINED.value,
            abstention_reason="insufficient_evidence",
        )
        pipe = build_fast_pipeline(fake)

        id_cfg = IdentityConfig(
            issuer=_TEST_ISSUER,
            audience=_TEST_AUDIENCE,
            hs256_secret=_TEST_SECRET.encode("utf-8"),
        )
        app = create_app(pipeline=pipe, identity_config=id_cfg)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/query",
            json={
                "query": "Unknown fabricated query",
                "user_context": {"tenant_id": "TENANT-NOVASTACK"},
            },
            headers=_make_auth_headers(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["answer_status"] == "abstained"
        assert data["abstention_reason"] == "insufficient_evidence"


# =============================================================================
# G. Error Propagation
# =============================================================================

class TestErrorPropagation:
    def test_provider_exception_sanitized_to_500(self):
        fake = FakeAnswerGeneratorProvider(
            raise_exc=RuntimeError("Internal inference engine crash"),
        )
        pipe = build_fast_pipeline(fake)

        id_cfg = IdentityConfig(
            issuer=_TEST_ISSUER,
            audience=_TEST_AUDIENCE,
            hs256_secret=_TEST_SECRET.encode("utf-8"),
        )
        app = create_app(pipeline=pipe, identity_config=id_cfg)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/query",
            json={
                "query": "What is the policy?",
                "user_context": {"tenant_id": "TENANT-NOVASTACK"},
            },
            headers=_make_auth_headers(),
        )

        assert resp.status_code == 500
        data = resp.json()
        # Verifies no stack trace leakage
        assert "Internal inference engine crash" not in data.get("detail", "")
        assert "Internal server" in data.get("detail", "")

    def test_provider_timeout_abstention_converts_to_504(self):
        fake = FakeAnswerGeneratorProvider(
            answer_status=AnswerStatus.ABSTAINED.value,
            abstention_reason="timeout",
        )
        pipe = build_fast_pipeline(fake)

        id_cfg = IdentityConfig(
            issuer=_TEST_ISSUER,
            audience=_TEST_AUDIENCE,
            hs256_secret=_TEST_SECRET.encode("utf-8"),
        )
        app = create_app(pipeline=pipe, identity_config=id_cfg)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/query",
            json={
                "query": "Database failover runbook",
                "user_context": {"tenant_id": "TENANT-NOVASTACK"},
            },
            headers=_make_auth_headers(),
        )

        assert resp.status_code == 504
        assert "timed out" in resp.json().get("detail", "").lower() or "deadline" in resp.json().get("detail", "").lower()


# =============================================================================
# H. Resilience & Concurrency Limiter Compatibility
# =============================================================================

class TestResilienceCompatibility:
    def test_circuit_breaker_triggers_on_repeated_provider_failures(self):
        fake = FakeAnswerGeneratorProvider(
            raise_exc=RuntimeError("Provider failure"),
        )
        pipe = build_fast_pipeline(fake)

        id_cfg = IdentityConfig(
            issuer=_TEST_ISSUER,
            audience=_TEST_AUDIENCE,
            hs256_secret=_TEST_SECRET.encode("utf-8"),
        )
        res_cfg = ResilienceConfig(
            circuit_failure_threshold=2,
            circuit_cooldown_seconds=10.0,
            enable_circuit_breaker=True,
        )
        app = create_app(pipeline=pipe, resilience_config=res_cfg, identity_config=id_cfg)
        client = TestClient(app, raise_server_exceptions=False)

        headers = _make_auth_headers()
        req_body = {
            "query": "test query",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        }

        # 2 failures trigger OPEN state
        r1 = client.post("/query", json=req_body, headers=headers)
        assert r1.status_code == 500
        r2 = client.post("/query", json=req_body, headers=headers)
        assert r2.status_code == 500

        # Next request must be rejected with 503 by Circuit Breaker
        r3 = client.post("/query", json=req_body, headers=headers)
        assert r3.status_code == 503
        assert "circuit breaker" in r3.json().get("detail", "").lower()


# =============================================================================
# I. Structured Citation Compatibility
# =============================================================================

class TestCitationCompatibility:
    def test_citations_formatted_as_citation_items(self):
        fake = FakeAnswerGeneratorProvider()
        pipe = build_fast_pipeline(fake)

        id_cfg = IdentityConfig(
            issuer=_TEST_ISSUER,
            audience=_TEST_AUDIENCE,
            hs256_secret=_TEST_SECRET.encode("utf-8"),
        )
        app = create_app(pipeline=pipe, identity_config=id_cfg)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/query",
            json={
                "query": "What is the network topology?",
                "user_context": {"tenant_id": "TENANT-NOVASTACK"},
            },
            headers=_make_auth_headers(),
        )

        assert resp.status_code == 200
        citations = resp.json()["citations"]
        assert len(citations) == 1
        assert citations[0]["evidence_id"] == "EVD-001"
        assert citations[0]["document_id"] == "DOC-NOVASTACK-01"

# =============================================================================
# J. Observability Compatibility
# =============================================================================

class TestObservabilityCompatibility:
    def test_metrics_endpoint_records_generation(self):
        reset_metrics()
        fake = FakeAnswerGeneratorProvider(latency_ms=25.0)
        pipe = build_fast_pipeline(fake)

        id_cfg = IdentityConfig(
            issuer=_TEST_ISSUER,
            audience=_TEST_AUDIENCE,
            hs256_secret=_TEST_SECRET.encode("utf-8"),
        )
        app = create_app(pipeline=pipe, identity_config=id_cfg)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/query",
            json={
                "query": "What is the network topology?",
                "user_context": {"tenant_id": "TENANT-NOVASTACK"},
            },
            headers=_make_auth_headers(),
        )
        assert resp.status_code == 200

        metrics_resp = client.get("/metrics")
        assert metrics_resp.status_code == 200
        assert b"atlas_stage_latency_seconds" in metrics_resp.content


# =============================================================================
# K. Security & Auth Compatibility (Upstream Boundary Protection)
# =============================================================================

class TestSecurityCompatibility:
    def test_missing_auth_rejected_before_provider_invocation(self):
        fake = FakeAnswerGeneratorProvider()
        pipe = build_fast_pipeline(fake)

        id_cfg = IdentityConfig(
            issuer=_TEST_ISSUER,
            audience=_TEST_AUDIENCE,
            hs256_secret=_TEST_SECRET.encode("utf-8"),
        )
        app = create_app(pipeline=pipe, identity_config=id_cfg)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/query",
            json={
                "query": "Attempt unauthorized query",
                "user_context": {"tenant_id": "TENANT-NOVASTACK"},
            },
        )
        assert resp.status_code == 401
        # Crucial security invariant: provider was NEVER touched
        assert fake.call_count == 0

    def test_tenant_context_mismatch_rejected_before_provider_invocation(self):
        fake = FakeAnswerGeneratorProvider()
        pipe = build_fast_pipeline(fake)

        id_cfg = IdentityConfig(
            issuer=_TEST_ISSUER,
            audience=_TEST_AUDIENCE,
            hs256_secret=_TEST_SECRET.encode("utf-8"),
        )
        app = create_app(pipeline=pipe, identity_config=id_cfg)
        client = TestClient(app, raise_server_exceptions=False)

        # JWT token says TENANT-NOVASTACK, payload says TENANT-ORBITAL
        headers = _make_auth_headers(tenant_id="TENANT-NOVASTACK")
        resp = client.post(
            "/query",
            json={
                "query": "Attempt cross-tenant query",
                "user_context": {"tenant_id": "TENANT-ORBITAL"},
            },
            headers=headers,
        )
        assert resp.status_code == 403
        assert fake.call_count == 0


# =============================================================================
# L. Index Generation ID Compatibility
# =============================================================================

class TestIndexGenerationIdCompatibility:
    def test_index_generation_id_preserved_with_injected_provider(self):
        fake = FakeAnswerGeneratorProvider()
        pipe = build_fast_pipeline(fake)

        id_cfg = IdentityConfig(
            issuer=_TEST_ISSUER,
            audience=_TEST_AUDIENCE,
            hs256_secret=_TEST_SECRET.encode("utf-8"),
        )
        app = create_app(pipeline=pipe, identity_config=id_cfg)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/query",
            json={
                "query": "Network topology query",
                "user_context": {"tenant_id": "TENANT-NOVASTACK"},
            },
            headers=_make_auth_headers(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["index_generation_id"] is not None
        assert data["index_generation_id"].startswith("GEN-")


# =============================================================================
# M. Architectural Decoupling (Static Code Analysis)
# =============================================================================

class TestArchitecturalDecoupling:
    def test_api_module_does_not_import_transformers(self):
        """Verify src/novastack/service/api.py does not import Transformers or AutoModel classes."""
        api_path = Path(__file__).resolve().parent.parent / "src" / "novastack" / "service" / "api.py"
        source = api_path.read_text(encoding="utf-8")
        parsed = ast.parse(source)

        imported_modules = []
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.append(node.module)

        # Asserts no direct coupling to heavy ML framework or concrete generator
        assert "transformers" not in imported_modules
        assert "torch" not in imported_modules
        for mod in imported_modules:
            assert "AutoModelForCausalLM" not in mod
            assert "AutoTokenizer" not in mod

    def test_api_module_does_not_import_grounded_answer_generator(self):
        """Verify api.py depends on AnswerGeneratorProvider, not GroundedAnswerGenerator."""
        api_path = Path(__file__).resolve().parent.parent / "src" / "novastack" / "service" / "api.py"
        source = api_path.read_text(encoding="utf-8")
        assert "from novastack.generation import GroundedAnswerGenerator" not in source
        assert "from novastack.provider import AnswerGeneratorProvider, LocalHuggingFaceProvider" in source
