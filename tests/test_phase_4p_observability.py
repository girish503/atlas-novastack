"""Phase 4P: Structured Logging & Observability Tests.

Verifies:
1. Request produces structured log (machine-readable JSON).
2. Request ID is generated when absent.
3. Request ID propagates correctly across API, logs, and spans.
4. Supplied safe request ID is propagated; malicious ID is rejected.
5. Logs contain all required fields.
6. Credentials are not logged (strict redaction).
7. Sensitive document contents are not logged.
8. Metrics endpoint exists and returns Prometheus exposition format.
9. Request counter increments.
10. Error counter increments.
11. Timeout metric increments.
12. Capacity metric increments.
13. Latency metric records summary/histogram data.
14. Stage latency labels are strictly bounded.
15. No raw query appears in metric labels.
16. No arbitrary tenant/user IDs appear in metric labels.
17. Tracing instrumentation does not alter query results.
18. Production flags remain frozen: A=False, B=True, C=False.
"""
import inspect
import json
import logging
from pathlib import Path
import re
import sys
import time
from typing import Any
from uuid import UUID

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
from novastack.observability import (
    ALLOWED_STAGES,
    CONTENT_TYPE_PROMETHEUS,
    REQUEST_ID_HEADER,
    LogCaptureHandler,
    Span,
    StructuredJsonFormatter,
    Tracer,
    get_metrics,
    get_tracer,
    reset_metrics,
    validate_request_id,
)
from novastack.service import (
    AtlasServiceError,
    AtlasServicePipeline,
    AtlasTimeoutError,
    CallerContext,
    CapacityExhaustedError,
    CircuitBreaker,
    InferenceConcurrencyLimiter,
    ModelUnavailableError,
    QueryRequest,
    QueryResponse,
    ResilienceConfig,
    create_app,
)


# ---------------------------------------------------------------------
# Test Doubles & Fixtures
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


from dataclasses import asdict
from novastack.bm25 import RetrievalResult


def create_sample_docs() -> dict[str, SearchDocument]:
    doc = SearchDocument.from_dict({
        "document_id": "DOC-CORP-01",
        "title": "Corporate Policy",
        "content": "Enterprise policy requires strict adherence to security bounds.",
        "tenant_id": "TENANT-NOVASTACK",
        "source_type": "policy",
        "department": "infrastructure",
        "author_id": "eng-01",
        "classification": "internal",
        "authority_level": "high",
        "status": "active",
        "version": "1.0",
        "created_at": "2026-01-01T00:00:00Z",
        "source_entity_id": "ENT-001",
        "related_entity_ids": [],
        "permissions": {
            "allowed_roles": ["engineer", "admin"],
            "allowed_departments": ["infrastructure"],
            "allowed_users": ["eng-01"],
            "cross_tenant_denied": True,
        },
    })
    return {"DOC-CORP-01": doc}


def build_test_pipeline(generator: Any = None) -> AtlasServicePipeline:
    docs = create_sample_docs()
    chunks = {}
    for did, d in docs.items():
        cid = f"{did}::CHUNK-0001"
        chunks[cid] = SearchChunk.from_dict({
            "chunk_id": cid,
            "document_id": did,
            "chunk_index": 0,
            "total_chunks": 1,
            "text": d.content,
            "char_count": len(d.content),
            "word_count": len(d.content.split()),
            "tenant_id": d.tenant_id,
            "source_type": d.source_type,
            "authority_level": d.authority_level,
            "source_entity_id": d.source_entity_id,
            "title": d.title,
            "department": d.department,
            "author_id": d.author_id,
            "created_at": d.created_at,
            "classification": d.classification,
            "status": d.status,
            "version": d.version,
            "permissions": asdict(d.permissions) if isinstance(d.permissions, RecordPermissions) else {},
        })

    resolver = EvidenceResolver(
        documents_index=docs,
        chunks_index=chunks,
        config=EvidenceResolverConfig(),
    )

    class MockBM25:
        def search(self, query: str, top_k: int = 50, filters: dict = None):
            tenant = filters.get("tenant_id") if filters else None
            matching = [d for d in docs.values() if tenant is None or d.tenant_id == tenant]
            return [
                RetrievalResult(
                    chunk_id=f"{d.document_id}::CHUNK-0001",
                    document_id=d.document_id,
                    score=0.9 - i * 0.05,
                    rank=i + 1,
                    title=d.title,
                    text_preview=d.content[:200],
                    tenant_id=d.tenant_id,
                    source_type=d.source_type,
                    department=d.department,
                    classification=d.classification,
                    authority_level=d.authority_level,
                    status=d.status,
                    version=d.version,
                    created_at=d.created_at,
                    source_entity_id=d.source_entity_id,
                    related_entity_ids=d.related_entity_ids,
                )
                for i, d in enumerate(matching)
            ]

    class MockDense:
        def search(self, query: str, top_k: int = 50, filters: dict = None):
            tenant = filters.get("tenant_id") if filters else None
            matching = [d for d in docs.values() if tenant is None or d.tenant_id == tenant]
            return [
                RetrievalResult(
                    chunk_id=f"{d.document_id}::CHUNK-0001",
                    document_id=d.document_id,
                    score=0.9 - i * 0.05,
                    rank=i + 1,
                    title=d.title,
                    text_preview=d.content[:200],
                    tenant_id=d.tenant_id,
                    source_type=d.source_type,
                    department=d.department,
                    classification=d.classification,
                    authority_level=d.authority_level,
                    status=d.status,
                    version=d.version,
                    created_at=d.created_at,
                    source_entity_id=d.source_entity_id,
                    related_entity_ids=d.related_entity_ids,
                )
                for i, d in enumerate(matching)
            ]

    class MockReranker:
        def rerank(self, candidates, qu=None, metadata_index=None, forbidden_doc_ids=None):
            return candidates

    gen = generator or MockGenerator()
    setattr(gen, "model", object())
    return AtlasServicePipeline(
        bm25_index=MockBM25(),
        dense_index=MockDense(),
        reranker=MockReranker(),
        generator=gen,
        resolver=resolver,
    )


@pytest.fixture
def log_capture():
    """Attach LogCaptureHandler to service logger and yield captured records."""
    logger = logging.getLogger("novastack.service")
    handler = LogCaptureHandler()
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old_level)


@pytest.fixture(autouse=True)
def clean_observability():
    """Reset metrics and tracer before each test."""
    reset_metrics()
    get_tracer().clear()
    yield
    reset_metrics()
    get_tracer().clear()


# ---------------------------------------------------------------------
# Test 1: Request produces structured log
# ---------------------------------------------------------------------
def test_request_produces_structured_log(log_capture):
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    with TestClient(app) as client:
        resp = client.post("/query", json={
            "query": "What is the corporate policy?",
            "user_context": {"tenant_id": "TENANT-NOVASTACK", "roles": ["engineer"]},
            "evaluation_id": "EVAL-LOG-01",
        })
        assert resp.status_code == 200

    assert len(log_capture.records) >= 1
    found = False
    for rec in log_capture.records:
        if rec.get("event") == "query_completed":
            found = True
            assert rec.get("status_code") == 200
            assert rec.get("answer_status") == "answered"
            assert "latency_ms" in rec
            assert rec.get("tenant_id") == "TENANT-NOVASTACK"
            assert rec.get("evaluation_id") == "EVAL-LOG-01"
    assert found, "Expected query_completed structured log record"


# ---------------------------------------------------------------------
# Test 2: Request ID is generated when absent
# ---------------------------------------------------------------------
def test_request_id_is_generated():
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    with TestClient(app) as client:
        resp = client.post("/query", json={
            "query": "What is the security protocol?",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        })
        assert resp.status_code == 200
        header_rid = resp.headers.get("x-request-id")
        body_rid = resp.json().get("request_id")

        assert header_rid is not None and len(header_rid) > 0
        assert body_rid is not None and len(body_rid) > 0
        assert header_rid == body_rid
        # Should be a valid UUID
        val = UUID(header_rid)
        assert str(val) == header_rid


# ---------------------------------------------------------------------
# Test 3: Request ID propagates correctly across API, logs, and spans
# ---------------------------------------------------------------------
def test_request_id_propagates_correctly(log_capture):
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    with TestClient(app) as client:
        resp = client.post("/query", json={
            "query": "Check propagation",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        })
        assert resp.status_code == 200
        rid = resp.headers.get("x-request-id")

    # Verify log record has this exact request_id
    query_log = next(r for r in log_capture.records if r.get("event") == "query_completed")
    assert query_log["request_id"] == rid

    # Verify spans recorded have this exact request_id
    spans = get_tracer().get_finished_spans(request_id=rid)
    assert len(spans) >= 1
    for s in spans:
        assert s.request_id == rid


# ---------------------------------------------------------------------
# Test 4: Supplied safe request ID is propagated; malicious ID is rejected
# ---------------------------------------------------------------------
def test_supplied_safe_request_id_is_propagated():
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    with TestClient(app) as client:
        # Case A: Safe alphanumeric/hyphen ID supplied in header
        safe_id = "req-custom-prod-12345"
        resp_safe = client.post("/query", json={
            "query": "Safe query",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        }, headers={"x-request-id": safe_id})
        assert resp_safe.status_code == 200
        assert resp_safe.headers.get("x-request-id") == safe_id
        assert resp_safe.json().get("request_id") == safe_id

        # Case B: Malicious ID containing password pattern
        bad_id = "token_bearer_secret_123"
        resp_bad = client.post("/query", json={
            "query": "Bad ID query",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        }, headers={"x-request-id": bad_id})
        assert resp_bad.status_code == 200
        # Malicious ID must be rejected and replaced with safe generated UUID
        returned_id = resp_bad.headers.get("x-request-id")
        assert returned_id != bad_id
        assert UUID(returned_id)  # Valid UUID generated instead


# ---------------------------------------------------------------------
# Test 5: Logs contain all required fields
# ---------------------------------------------------------------------
def test_logs_contain_required_fields(log_capture):
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    with TestClient(app) as client:
        client.post("/query", json={
            "query": "Check log fields",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        })

    rec = next(r for r in log_capture.records if r.get("event") == "query_completed")
    required = ["timestamp", "level", "event", "request_id", "endpoint", "status_code", "answer_status", "latency_ms"]
    for field in required:
        assert field in rec, f"Mandatory log field '{field}' missing from record: {rec}"


# ---------------------------------------------------------------------
# Test 6: Credentials are not logged
# ---------------------------------------------------------------------
def test_credentials_are_not_logged(log_capture):
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    secret_value = "SuperSecretBearerToken12345"
    with TestClient(app) as client:
        client.post("/query", json={
            "query": "Query with auth header",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        }, headers={"Authorization": f"Bearer {secret_value}"})

    for raw_line in log_capture.raw_lines:
        assert secret_value not in raw_line, "Raw credential token leaked into structured log!"


# ---------------------------------------------------------------------
# Test 7: Sensitive document contents are not logged
# ---------------------------------------------------------------------
def test_sensitive_document_contents_are_not_logged(log_capture):
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    with TestClient(app) as client:
        client.post("/query", json={
            "query": "What is the confidential policy?",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        })

    for raw_line in log_capture.raw_lines:
        # Full documents or evidence packages must not be dumped into log lines
        assert "evidence_package" not in raw_line.lower()
        assert "document_content" not in raw_line.lower()
        assert "evidence_items" not in raw_line.lower()


# ---------------------------------------------------------------------
# Test 8: Metrics endpoint exists and returns Prometheus exposition format
# ---------------------------------------------------------------------
def test_metrics_endpoint_exists():
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")
        text = resp.text
        assert "# HELP atlas_requests_total" in text
        assert "# TYPE atlas_requests_total counter"
        assert "# HELP atlas_request_latency_seconds" in text


# ---------------------------------------------------------------------
# Test 9: Request counter increments
# ---------------------------------------------------------------------
def test_request_counter_increments():
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    with TestClient(app) as client:
        resp = client.post("/query", json={
            "query": "Increment request metric",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        })
        assert resp.status_code == 200

        m_resp = client.get("/metrics")
        text = m_resp.text
        assert 'atlas_requests_total{endpoint="/query",method="POST",status="200"} 1' in text
        assert "atlas_query_answers_total 1" in text


# ---------------------------------------------------------------------
# Test 10: Error counter increments
# ---------------------------------------------------------------------
def test_error_counter_increments():
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    with TestClient(app) as client:
        # Invalid query (empty query string)
        resp = client.post("/query", json={
            "query": "   ",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        })
        assert resp.status_code == 422

        m_resp = client.get("/metrics")
        text = m_resp.text
        assert 'atlas_request_errors_total{error_type="ValidationError",status="422"} 1' in text


# ---------------------------------------------------------------------
# Test 11: Timeout metric increments
# ---------------------------------------------------------------------
def test_timeout_metric_increments():
    pipe = build_test_pipeline(generator=MockGenerator(delay_seconds=1.0))
    cfg = ResilienceConfig(request_timeout_seconds=0.05, enable_circuit_breaker=False)
    app = create_app(pipeline=pipe, resilience_config=cfg)

    with TestClient(app) as client:
        resp = client.post("/query", json={
            "query": "Will timeout",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        })
        assert resp.status_code == 504

        m_resp = client.get("/metrics")
        text = m_resp.text
        assert "atlas_query_timeouts_total 1" in text
        assert 'atlas_request_errors_total{error_type="TimeoutError",status="504"} 1' in text


# ---------------------------------------------------------------------
# Test 12: Capacity metric increments
# ---------------------------------------------------------------------
def test_capacity_metric_increments():
    pipe = build_test_pipeline()
    cfg = ResilienceConfig(max_concurrent_inferences=1, queue_timeout_seconds=0.01)
    app = create_app(pipeline=pipe, resilience_config=cfg)

    # Artificially exhaust limiter capacity
    app.state.limiter.max_concurrent = 0

    with TestClient(app) as client:
        resp = client.post("/query", json={
            "query": "Will exhaust capacity",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        })
        assert resp.status_code == 429

        m_resp = client.get("/metrics")
        text = m_resp.text
        assert "atlas_capacity_exhausted_total 1" in text
        assert 'atlas_request_errors_total{error_type="CapacityExhaustedError",status="429"} 1' in text


# ---------------------------------------------------------------------
# Test 13: Latency metric records summary/histogram data
# ---------------------------------------------------------------------
def test_latency_metric_records():
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    with TestClient(app) as client:
        client.post("/query", json={
            "query": "Record latency test",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        })
        m_resp = client.get("/metrics")
        text = m_resp.text
        assert "atlas_request_latency_seconds_count 1" in text
        assert "atlas_request_latency_seconds_sum" in text
        assert 'atlas_request_latency_seconds_bucket{le="+Inf"} 1' in text


# ---------------------------------------------------------------------
# Test 14: Stage latency labels are strictly bounded
# ---------------------------------------------------------------------
def test_stage_latency_labels_are_bounded():
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    with TestClient(app) as client:
        client.post("/query", json={
            "query": "Check stage labels",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        })
        m_resp = client.get("/metrics")
        text = m_resp.text

        # Parse all stages present in atlas_stage_latency_seconds
        stage_matches = re.findall(r'atlas_stage_latency_seconds_count\{stage="([^"]+)"\}', text)
        assert len(stage_matches) > 0
        for s in stage_matches:
            assert s in ALLOWED_STAGES, f"Stage label '{s}' is not in allowed bounded stages {ALLOWED_STAGES}"

        # Directly testing that an invalid stage is rejected by metrics registry
        registry = get_metrics()
        with pytest.raises(ValueError, match="Unknown or unbounded pipeline stage"):
            registry.record_stage_latency("arbitrary_unbounded_stage_name", 0.1)


# ---------------------------------------------------------------------
# Test 15: No raw query appears in metric labels
# ---------------------------------------------------------------------
def test_no_raw_query_appears_in_metric_labels():
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    sensitive_query = "What is the secret roadmap for project X999?"
    with TestClient(app) as client:
        client.post("/query", json={
            "query": sensitive_query,
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        })
        m_resp = client.get("/metrics")
        assert sensitive_query not in m_resp.text, "Raw query string leaked into Prometheus metrics output!"


# ---------------------------------------------------------------------
# Test 16: No arbitrary tenant/user IDs appear in metric labels
# ---------------------------------------------------------------------
def test_no_arbitrary_tenant_user_ids_appear_in_metric_labels():
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    user_id = "usr-special-classified-987"
    tenant_id = "TENANT-CLASSIFIED-456"
    with TestClient(app) as client:
        client.post("/query", json={
            "query": "Check label isolation",
            "user_context": {"tenant_id": tenant_id, "user_id": user_id},
        })
        m_resp = client.get("/metrics")
        assert user_id not in m_resp.text, "User ID leaked into Prometheus metric labels!"
        assert tenant_id not in m_resp.text, "Tenant ID leaked into Prometheus metric labels!"


# ---------------------------------------------------------------------
# Test 17: Tracing instrumentation does not alter query results
# ---------------------------------------------------------------------
def test_tracing_instrumentation_does_not_alter_query_result():
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    with TestClient(app) as client:
        resp = client.post("/query", json={
            "query": "What is the corporate policy?",
            "user_context": {"tenant_id": "TENANT-NOVASTACK", "roles": ["engineer"]},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer_status"] == "answered"
        assert "Enterprise policy requires strict adherence to security bounds" in data["answer_text"]
        assert len(data["citations"]) == 1
        assert data["citations"][0]["document_id"] == "DOC-CORP-01"

    spans = get_tracer().get_finished_spans()
    span_names = {s.name for s in spans}
    assert "request" in span_names
    assert "retrieval" in span_names
    assert "reranking" in span_names
    assert "evidence_resolution" in span_names
    assert "generation" in span_names
    assert "citation_resolution" in span_names


# ---------------------------------------------------------------------
# Test 18: Production flags remain frozen (A=False, B=True, C=False)
# ---------------------------------------------------------------------
def test_production_flags_remain_frozen():
    from novastack.event_evidence_bundler import EventBundlerConfig
    from novastack.evidence_resolution import EvidenceResolverConfig
    from novastack.generation import GroundedAnswerGenerator

    # Mechanism B: Query-Aware Authority Preservation (Frozen default: True)
    resolver_cfg = EvidenceResolverConfig()
    assert resolver_cfg.enable_query_aware_authority is True, "Mechanism B default must remain True"

    # Mechanism C: Event-Centric Evidence Bundling (Frozen default: False)
    assert resolver_cfg.enable_event_bundling is False, "Mechanism C in resolver must remain False"
    assert EventBundlerConfig().enable_event_bundling is False, "Mechanism C in bundler must remain False"

    # Mechanism A: Boundary Sentence Stitching (Frozen default: False)
    sig = inspect.signature(GroundedAnswerGenerator.generate_answer)
    assert sig.parameters["enable_boundary_stitching"].default is False, "Mechanism A must remain False"
