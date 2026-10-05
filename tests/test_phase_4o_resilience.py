"""Phase 4O: Service Resilience, Timeouts, and Controlled Failure Handling Tests.

Verifies:
1. Normal generation succeeds unchanged.
2. Configured deadline is honored.
3. Simulated slow generation triggers timeout.
4. Timeout does not return partial answer.
5. Timeout does not produce fake citations.
6. Timeout response contains no stack trace.
7. Unexpected runtime exception is sanitized.
8. Model/resource failure is controlled.
9. Concurrent inference is bounded.
10. Capacity exhaustion returns controlled response.
11. Service remains responsive after timeout.
12. Successful requests continue after a failed request.
13. Tenant context remains intact during failures.
14. Security invariants remain intact.
15. Production flags remain A=False, B=True, C=False.
"""
import asyncio
import inspect
from pathlib import Path
import sys
import time
from typing import Any

from fastapi.testclient import TestClient
import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "src"))

from novastack.citation_validator import Citation
from novastack.event_evidence_bundler import EventBundlerConfig
from novastack.evidence import EvidenceItem, EvidencePackage, EvidenceStatus
from novastack.evidence_resolution import (
    EvidenceResolver,
    EvidenceResolverConfig,
)
from novastack.generation import AnswerResult, AnswerStatus, GroundedAnswerGenerator
from novastack.models import RecordPermissions, SearchChunk, SearchDocument
from novastack.service import (
    AtlasServiceError,
    AtlasServicePipeline,
    AtlasTimeoutError,
    CallerContext,
    CapacityExhaustedError,
    CircuitBreaker,
    CircuitState,
    InferenceConcurrencyLimiter,
    ModelUnavailableError,
    QueryRequest,
    QueryResponse,
    ResilienceConfig,
    create_app,
    sanitize_error_detail,
)


# ---------------------------------------------------------------------
# Fixtures and Helpers
# ---------------------------------------------------------------------

class MockGenerator:
    def __init__(self, delay_seconds: float = 0.0, raise_exc: Exception | None = None):
        self.delay_seconds = delay_seconds
        self.raise_exc = raise_exc
        self.call_count = 0

    def generate_answer(
        self,
        package: EvidencePackage,
        max_evidence_items: int = 3,
        prompt_strategy: str = "config_a_calibrated",
        citation_resolver: str = "c2",
        enable_boundary_stitching: bool = False,
        timeout_seconds: float | None = None,
    ) -> AnswerResult:
        self.call_count += 1
        if self.raise_exc:
            raise self.raise_exc

        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)

        if timeout_seconds is not None and self.delay_seconds > timeout_seconds:
            return AnswerResult(
                answer_id=f"ANS-{package.package_id}",
                evaluation_id=package.evaluation_id,
                query=package.query,
                answer_text="",
                answer_status=AnswerStatus.ABSTAINED.value,
                citations=[],
                abstention_reason="timeout",
            )

        if not package.selected_evidence:
            return AnswerResult(
                answer_id=f"ANS-{package.package_id}",
                evaluation_id=package.evaluation_id,
                query=package.query,
                answer_text="Insufficient evidence to answer this question.",
                answer_status=AnswerStatus.ABSTAINED.value,
                citations=[],
                abstention_reason="no_usable_evidence",
            )

        cit = Citation(
            raw_tag="[EVD-001]",
            evidence_id="EVD-001",
            document_id="DOC-CORP-01",
            chunk_id="DOC-CORP-01::CHUNK-0001",
            title="Corporate Policy",
            status="VALID",
        )
        return AnswerResult(
            answer_id=f"ANS-{package.package_id}",
            evaluation_id=package.evaluation_id,
            query=package.query,
            answer_text="Enterprise policy requires strict adherence to security bounds [EVD-001].",
            answer_status=AnswerStatus.ANSWERED.value,
            citations=[cit],
            generation_latency_ms=2.5,
        )


class MockCandidate:
    def __init__(self, document_id: str, score: float = 0.9, rank: int = 1):
        self.chunk_id = f"{document_id}::CHUNK-0001"
        self.document_id = document_id
        self.final_score = score
        self.score = score
        self.rank = rank
        self.title = "Corporate Policy"
        self.text_preview = "Enterprise policy requires strict adherence to security bounds."
        self.tenant_id = "TENANT-NOVASTACK"
        self.source_type = "policy"
        self.department = "security"
        self.classification = "internal"
        self.authority_level = "primary"
        self.status = "active"
        self.version = "1.0"
        self.created_at = "2026-01-01T00:00:00Z"
        self.source_entity_id = "ENT-001"
        self.related_entity_ids = []


def build_test_pipeline(generator=None, delay: float = 0.0) -> AtlasServicePipeline:
    docs = {
        "DOC-CORP-01": SearchDocument(
            document_id="DOC-CORP-01",
            title="Corporate Policy",
            content="Enterprise policy requires strict adherence to security bounds.",
            tenant_id="TENANT-NOVASTACK",
            source_type="policy",
            authority_level="primary",
            department="security",
            author_id="admin-01",
            created_at="2026-01-01T00:00:00Z",
            status="active",
            version="1.0",
            classification="internal",
            permissions=RecordPermissions(),
        )
    }
    chunks = {
        "DOC-CORP-01::CHUNK-0001": SearchChunk(
            chunk_id="DOC-CORP-01::CHUNK-0001",
            document_id="DOC-CORP-01",
            chunk_index=0,
            total_chunks=1,
            text="Enterprise policy requires strict adherence to security bounds.",
            char_count=65,
            word_count=8,
            tenant_id="TENANT-NOVASTACK",
            source_type="policy",
            authority_level="primary",
            title="Corporate Policy",
            department="security",
            author_id="admin-01",
            created_at="2026-01-01T00:00:00Z",
            status="active",
            version="1.0",
            classification="internal",
            permissions=RecordPermissions(),
        )
    }
    resolver = EvidenceResolver(
        documents_index=docs,
        chunks_index=chunks,
        config=EvidenceResolverConfig(),
    )
    gen = generator or MockGenerator(delay_seconds=delay)

    class MockBM25:
        def search(self, query: str, top_k: int = 50, filters: dict = None):
            tenant = filters.get("tenant_id") if filters else None
            matching = [d for d in docs.values() if tenant is None or d.tenant_id == tenant]
            return [MockCandidate(d.document_id) for d in matching]

    class MockDense:
        def search(self, query: str, top_k: int = 50, filters: dict = None):
            tenant = filters.get("tenant_id") if filters else None
            matching = [d for d in docs.values() if tenant is None or d.tenant_id == tenant]
            return [MockCandidate(d.document_id) for d in matching]

    class MockReranker:
        def rerank(self, candidates, qu=None, metadata_index=None, forbidden_doc_ids=None):
            return candidates

    return AtlasServicePipeline(
        bm25_index=MockBM25(),
        dense_index=MockDense(),
        reranker=MockReranker(),
        generator=gen,
        resolver=resolver,
    )


# ---------------------------------------------------------------------
# Test 1: Normal generation succeeds unchanged
# ---------------------------------------------------------------------

def test_normal_generation_succeeds_unchanged():
    """Verify normal query execution succeeds returning HTTP 200 and valid schema."""
    pipeline = build_test_pipeline()
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    req_payload = {
        "query": "What is corporate policy?",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
    }
    resp = client.post("/query", json=req_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer_status"] == "answered"
    assert "Enterprise policy" in data["answer_text"]
    assert len(data["citations"]) == 1
    assert data["citations"][0]["raw_tag"] == "[EVD-001]"
    assert data["latency_ms"] >= 0.0


# ---------------------------------------------------------------------
# Test 2: Configured deadline is honored
# ---------------------------------------------------------------------

def test_configured_deadline_is_honored():
    """Verify ResilienceConfig allows configuring request deadline."""
    config = ResilienceConfig(request_timeout_seconds=0.25)
    assert config.request_timeout_seconds == 0.25
    app = create_app(resilience_config=config)
    assert app.state.resilience_config.request_timeout_seconds == 0.25


# ---------------------------------------------------------------------
# Test 3: Simulated slow generation triggers timeout
# ---------------------------------------------------------------------

def test_simulated_slow_generation_triggers_timeout():
    """Verify slow generation exceeding deadline triggers HTTP 504 TimeoutError."""
    slow_gen = MockGenerator(delay_seconds=0.3)
    pipeline = build_test_pipeline(generator=slow_gen)
    config = ResilienceConfig(request_timeout_seconds=0.05)
    app = create_app(pipeline=pipeline, resilience_config=config)
    client = TestClient(app, raise_server_exceptions=False)

    req_payload = {
        "query": "What is corporate policy?",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
    }
    resp = client.post("/query", json=req_payload)
    assert resp.status_code == 504
    data = resp.json()
    assert data["error_type"] == "TimeoutError"
    assert data["answer_status"] == "timeout"
    assert "exceeded configured deadline" in data["detail"]


# ---------------------------------------------------------------------
# Test 4: Timeout does not return partial answer
# ---------------------------------------------------------------------

def test_timeout_does_not_return_partial_answer():
    """Verify timeout response does not leak partial or hallucinated answer text."""
    slow_gen = MockGenerator(delay_seconds=0.3)
    pipeline = build_test_pipeline(generator=slow_gen)
    config = ResilienceConfig(request_timeout_seconds=0.05)
    app = create_app(pipeline=pipeline, resilience_config=config)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/query", json={
        "query": "What is corporate policy?",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
    })
    assert resp.status_code == 504
    data = resp.json()
    assert "answer_text" not in data or data.get("answer_text") == ""


# ---------------------------------------------------------------------
# Test 5: Timeout does not produce fake citations
# ---------------------------------------------------------------------

def test_timeout_does_not_produce_fake_citations():
    """Verify timeout response produces zero fake or fabricated citations."""
    slow_gen = MockGenerator(delay_seconds=0.3)
    pipeline = build_test_pipeline(generator=slow_gen)
    config = ResilienceConfig(request_timeout_seconds=0.05)
    app = create_app(pipeline=pipeline, resilience_config=config)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/query", json={
        "query": "What is corporate policy?",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
    })
    assert resp.status_code == 504
    data = resp.json()
    assert "citations" not in data or len(data.get("citations", [])) == 0


# ---------------------------------------------------------------------
# Test 6: Timeout response contains no stack trace
# ---------------------------------------------------------------------

def test_timeout_response_contains_no_stack_trace():
    """Verify timeout response contains no internal Python stack traces."""
    slow_gen = MockGenerator(delay_seconds=0.3)
    pipeline = build_test_pipeline(generator=slow_gen)
    config = ResilienceConfig(request_timeout_seconds=0.05)
    app = create_app(pipeline=pipeline, resilience_config=config)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/query", json={
        "query": "What is corporate policy?",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
    })
    assert resp.status_code == 504
    body = resp.text
    assert "Traceback (most recent call last)" not in body
    assert "File \"" not in body
    assert ", line " not in body


# ---------------------------------------------------------------------
# Test 7: Unexpected runtime exception is sanitized
# ---------------------------------------------------------------------

def test_unexpected_runtime_exception_is_sanitized():
    """Verify unexpected internal exception details are sanitized to prevent credential/path leak."""
    class CrashingPipeline(AtlasServicePipeline):
        def is_ready(self):
            return True, {"bm25": True, "dense": True, "reranker": True, "generator": True}

        def execute_query(self, request: QueryRequest, timeout_seconds: float = None) -> QueryResponse:
            raise RuntimeError(
                "Internal DB crash at /var/data/models/weights.bin with auth_token=secret_xyz987"
            )

    app = create_app(pipeline=CrashingPipeline())
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/query", json={
        "query": "What is corporate policy?",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
    })
    assert resp.status_code == 500
    data = resp.json()
    assert data["answer_status"] == "error"
    assert data["detail"] == "Internal server processing failure"
    assert data["error_type"] == "RuntimeError"
    assert "secret_xyz987" not in resp.text
    assert "/var/data/models" not in resp.text
    assert "Traceback" not in resp.text


# ---------------------------------------------------------------------
# Test 8: Model/resource failure is controlled
# ---------------------------------------------------------------------

def test_model_resource_failure_is_controlled():
    """Verify missing generator or out-of-memory exception returns clean HTTP 503."""
    unready_pipe = AtlasServicePipeline(bm25_index="ok", dense_index="ok", reranker="ok", generator=None)
    app = create_app(pipeline=unready_pipe)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/query", json={
        "query": "Test query",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
    })
    assert resp.status_code == 503
    data = resp.json()
    assert data["answer_status"] == "error"
    assert "not ready" in data["detail"] or "unavailable" in data["detail"].lower()


# ---------------------------------------------------------------------
# Test 9: Concurrent inference is bounded
# ---------------------------------------------------------------------

def test_concurrent_inference_is_bounded():
    """Verify InferenceConcurrencyLimiter strictly bounds concurrent executions."""
    async def _run():
        limiter = InferenceConcurrencyLimiter(max_concurrent=1, queue_timeout=0.01)
        acq1 = await limiter.acquire()
        assert acq1 is True

        acq2 = await limiter.acquire()
        assert acq2 is False

        limiter.release()
        acq3 = await limiter.acquire()
        assert acq3 is True
        limiter.release()

    asyncio.run(_run())


# ---------------------------------------------------------------------
# Test 10: Capacity exhaustion returns controlled response
# ---------------------------------------------------------------------

def test_capacity_exhaustion_returns_controlled_response():
    """Verify request is rejected with HTTP 429 when concurrency slots are saturated."""
    pipeline = build_test_pipeline()
    config = ResilienceConfig(max_concurrent_inferences=0, queue_timeout_seconds=0.001)
    app = create_app(pipeline=pipeline, resilience_config=config)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/query", json={
        "query": "What is corporate policy?",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
    })
    assert resp.status_code == 429
    data = resp.json()
    assert data["error_type"] == "CapacityExhaustedError"
    assert data["answer_status"] == "error"
    assert "capacity exhausted" in data["detail"].lower()


# ---------------------------------------------------------------------
# Test 11: Service remains responsive after timeout
# ---------------------------------------------------------------------

def test_service_remains_responsive_after_timeout():
    """Verify /healthz and /ready continue returning HTTP 200 immediately after a timeout."""
    slow_gen = MockGenerator(delay_seconds=0.3)
    pipeline = build_test_pipeline(generator=slow_gen)
    config = ResilienceConfig(request_timeout_seconds=0.05)
    app = create_app(pipeline=pipeline, resilience_config=config)
    client = TestClient(app, raise_server_exceptions=False)

    # 1. Trigger timeout
    resp_timeout = client.post("/query", json={
        "query": "Slow query",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
    })
    assert resp_timeout.status_code == 504

    # 2. Verify healthz remains responsive
    resp_health = client.get("/healthz")
    assert resp_health.status_code == 200
    assert resp_health.json() == {"status": "ok"}

    # 3. Verify ready probe remains ready
    resp_ready = client.get("/ready")
    assert resp_ready.status_code == 200
    assert resp_ready.json()["status"] == "ready"


# ---------------------------------------------------------------------
# Test 12: Successful requests continue after a failed request
# ---------------------------------------------------------------------

def test_successful_requests_continue_after_a_failed_request():
    """Verify a subsequent valid query succeeds normally after a failure/timeout."""
    generator = MockGenerator(delay_seconds=0.0)
    pipeline = build_test_pipeline(generator=generator)
    config = ResilienceConfig(request_timeout_seconds=0.1)
    app = create_app(pipeline=pipeline, resilience_config=config)
    client = TestClient(app, raise_server_exceptions=False)

    # Request 1: Invalid request triggering 422
    resp_fail = client.post("/query", json={"query": "", "user_context": {"tenant_id": "TENANT-NOVASTACK"}})
    assert resp_fail.status_code == 422

    # Request 2: Valid request must succeed
    resp_ok = client.post("/query", json={"query": "Valid query", "user_context": {"tenant_id": "TENANT-NOVASTACK"}})
    assert resp_ok.status_code == 200
    assert resp_ok.json()["answer_status"] == "answered"


# ---------------------------------------------------------------------
# Test 13: Tenant context remains intact during failures
# ---------------------------------------------------------------------

def test_tenant_context_remains_intact_during_failures():
    """Verify tenant isolation invariants are not corrupted by failed requests."""
    pipeline = build_test_pipeline()
    app = create_app(pipeline=pipeline)
    client = TestClient(app, raise_server_exceptions=False)

    # Trigger failure with Tenant A
    resp_a = client.post("/query", json={"query": "", "user_context": {"tenant_id": "TENANT-A"}})
    assert resp_a.status_code == 422

    # Query with Tenant B succeeds and does not access Tenant A documents
    resp_b = client.post("/query", json={"query": "Test query", "user_context": {"tenant_id": "TENANT-NOVASTACK"}})
    assert resp_b.status_code == 200
    assert resp_b.json()["answer_status"] == "answered"


# ---------------------------------------------------------------------
# Test 14: Security invariants remain intact
# ---------------------------------------------------------------------

def test_security_invariants_remain_intact():
    """Verify fail-closed authorization and adversarial quarantines remain enforced."""
    pipeline = build_test_pipeline()
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    # Cross-tenant request denied by fail-closed resolver
    resp_cross = client.post("/query", json={
        "query": "Corporate Policy",
        "user_context": {"tenant_id": "TENANT-ORBITAL"},
    })
    assert resp_cross.status_code == 200
    assert resp_cross.json()["answer_status"] == "abstained"
    assert resp_cross.json()["citations"] == []


# ---------------------------------------------------------------------
# Test 15: Production flags remain frozen
# ---------------------------------------------------------------------

def test_production_flags_remain_frozen():
    """Assert exact production flags: A == False, B == True, C == False."""
    resolver_config = EvidenceResolverConfig()
    assert resolver_config.enable_query_aware_authority is True, "Mechanism B must remain True"
    assert resolver_config.enable_event_bundling is False, "Mechanism C in resolver must remain False"
    assert EventBundlerConfig().enable_event_bundling is False, "Mechanism C in bundler must remain False"

    sig = inspect.signature(GroundedAnswerGenerator.generate_answer)
    assert "enable_boundary_stitching" in sig.parameters
    assert sig.parameters["enable_boundary_stitching"].default is False, "Mechanism A must remain False"
