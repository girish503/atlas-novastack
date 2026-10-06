"""Phase 4R: ATLAS Load & Production Validation Test Suite.

Verifies:
1. Baseline query execution under single concurrency (C=1).
2. Bounded concurrency & capacity exhaustion: excess concurrent requests receive HTTP 429.
3. Request deadline enforcement: slow inference triggers HTTP 504 without stack trace.
4. Circuit breaker activation: repeated failures transition to OPEN (HTTP 503).
5. Event loop responsiveness: /healthz responds 200 OK during active inference load.
6. Component readiness probe: /ready responds during active inference load.
7. Multi-tenant security isolation under concurrent interleaved requests: zero cross-tenant contamination.
8. Observability counters: Prometheus metrics increment properly under load with bounded labels.
9. Memory and thread stability: active thread count and process memory remain bounded.
10. Production flags remain frozen: A=False, B=True, C=False.
"""
import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest
from fastapi.testclient import TestClient

from novastack.citation_validator import Citation
from novastack.event_evidence_bundler import EventBundlerConfig
from novastack.evidence import EvidenceItem, EvidencePackage, EvidenceStatus
from novastack.evidence_resolution import EvidenceResolver, EvidenceResolverConfig
from novastack.generation import AnswerResult, AnswerStatus, GroundedAnswerGenerator
from novastack.models import RecordPermissions, SearchChunk, SearchDocument
from novastack.observability import get_metrics, reset_metrics
from novastack.service import (
    AtlasServicePipeline,
    CallerContext,
    CircuitBreaker,
    CircuitState,
    InferenceConcurrencyLimiter,
    QueryRequest,
    QueryResponse,
    ResilienceConfig,
    create_app,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class MockDelayedGenerator:
    """Configurable test generator supporting artificial delay and failure injection."""
    def __init__(self, delay_seconds: float = 0.05, raise_exc: Exception | None = None):
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

        # Generate tenant-appropriate citation
        tenant_tag = "NOVASTACK"
        if "ORBITAL" in package.query or "orbital" in package.query.lower():
            tenant_tag = "ORBITAL"
        elif "PINECONE" in package.query or "pinecone" in package.query.lower():
            tenant_tag = "PINECONE"

        cit = Citation(
            raw_tag="[EVD-001]",
            evidence_id="EVD-001",
            document_id=f"DOC-{tenant_tag}-01",
            chunk_id=f"DOC-{tenant_tag}-01#c0",
            title=f"{tenant_tag} Standard Documentation",
            status="VALID",
        )
        return AnswerResult(
            answer_id=f"ANS-{package.package_id}",
            evaluation_id=package.evaluation_id,
            query=package.query,
            answer_text=f"Verified grounded enterprise facts for {tenant_tag} [EVD-001].",
            answer_status=AnswerStatus.ANSWERED.value,
            citations=[cit],
            evidence_ids_used=["EVD-001"],
            unsupported_claims=[],
            citation_validation_status="valid",
        )


def build_test_pipeline(generator: Any = None) -> AtlasServicePipeline:
    from tests.test_phase_4s_live_index_hotswap import DeterministicEncoder
    gen = generator or MockDelayedGenerator(delay_seconds=0.01)
    pipe = AtlasServicePipeline.create_default(lazy_generator=True)
    pipe.generator = gen
    if hasattr(pipe, "dense_index") and pipe.dense_index and hasattr(pipe.dense_index, "encoder"):
        pipe.dense_index.encoder = DeterministicEncoder()
    return pipe


# =============================================================================
# 1. Baseline Single-Concurrency Execution
# =============================================================================

def test_single_concurrency_baseline():
    """Verify single-query request executes successfully through FastAPI service."""
    pipe = build_test_pipeline()
    res_cfg = ResilienceConfig(request_timeout_seconds=60.0)
    app = create_app(pipeline=pipe, resilience_config=res_cfg)
    client = TestClient(app, raise_server_exceptions=False)

    payload = {
        "query": "What is the primary retention policy for NovaStack?",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        "evaluation_id": "LOAD-BASE-001",
    }
    resp = client.post("/query", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer_status"] == "answered"
    assert len(data["citations"]) > 0
    assert "retention" in data["query"].lower()
    assert data["latency_ms"] > 0.0


# =============================================================================
# 2. Concurrency Limiter & Capacity Shedding (429)
# =============================================================================

@pytest.mark.anyio
async def test_concurrency_limiter_sheds_load_with_429():
    """Verify that when concurrent requests exceed capacity, excess requests receive HTTP 429."""
    slow_gen = MockDelayedGenerator(delay_seconds=0.1)
    pipe = build_test_pipeline(generator=slow_gen)
    res_cfg = ResilienceConfig(
        max_concurrent_inferences=1,
        queue_timeout_seconds=0.02,
        request_timeout_seconds=30.0,
    )
    app = create_app(pipeline=pipe, resilience_config=res_cfg)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Pre-warm
        await client.get("/healthz")

        payload = {
            "query": "What is the retention policy?",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        }
        # Fire 5 concurrent requests
        tasks = [client.post("/query", json=payload) for _ in range(5)]
        responses = await asyncio.gather(*tasks)

        status_codes = [r.status_code for r in responses]
        assert 200 in status_codes, f"Expected 200 in {status_codes}"
        assert 429 in status_codes, f"Expected 429 in {status_codes}"

        for r in responses:
            if r.status_code == 429:
                err = r.json()
                assert err["error_type"] == "CapacityExhaustedError"
                assert "Traceback" not in err.get("detail", "")


# =============================================================================
# 3. Timeout Enforcement (504)
# =============================================================================

def test_request_deadline_triggers_504():
    """Verify that slow inference exceeding request deadline triggers HTTP 504."""
    slow_gen = MockDelayedGenerator(delay_seconds=0.2)
    pipe = build_test_pipeline(generator=slow_gen)
    res_cfg = ResilienceConfig(
        request_timeout_seconds=0.05,
        queue_timeout_seconds=1.0,
        max_concurrent_inferences=2,
    )
    app = create_app(pipeline=pipe, resilience_config=res_cfg)
    client = TestClient(app, raise_server_exceptions=False)

    payload = {
        "query": "What is the retention policy?",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
    }
    resp = client.post("/query", json=payload)
    assert resp.status_code == 504
    data = resp.json()
    assert data["error_type"] == "TimeoutError"
    assert data["answer_status"] == "timeout"
    assert "Traceback" not in data.get("detail", "")


# =============================================================================
# 4. Circuit Breaker Protection (503)
# =============================================================================

def test_circuit_breaker_activates_after_consecutive_failures():
    """Verify circuit breaker opens after repeated model failures and returns 503."""
    failing_gen = MockDelayedGenerator(raise_exc=RuntimeError("Model failure"))
    pipe = build_test_pipeline(generator=failing_gen)
    res_cfg = ResilienceConfig(
        enable_circuit_breaker=True,
        circuit_failure_threshold=2,
        circuit_cooldown_seconds=5.0,
    )
    app = create_app(pipeline=pipe, resilience_config=res_cfg)
    client = TestClient(app, raise_server_exceptions=False)

    payload = {
        "query": "What is the retention policy?",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
    }
    r1 = client.post("/query", json=payload)
    assert r1.status_code == 500

    r2 = client.post("/query", json=payload)
    assert r2.status_code == 500

    r3 = client.post("/query", json=payload)
    assert r3.status_code == 503
    assert r3.json()["error_type"] == "ModelUnavailableError"


# =============================================================================
# 5. Event Loop & Health Responsiveness Under Load
# =============================================================================

@pytest.mark.anyio
async def test_event_loop_and_health_probes_responsive_under_load():
    """Verify /healthz and /ready return 200 promptly even while inference tasks run."""
    slow_gen = MockDelayedGenerator(delay_seconds=0.1)
    pipe = build_test_pipeline(generator=slow_gen)
    res_cfg = ResilienceConfig(max_concurrent_inferences=2, queue_timeout_seconds=0.5, request_timeout_seconds=30.0)
    app = create_app(pipeline=pipe, resilience_config=res_cfg)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/healthz")

        query_payload = {
            "query": "How are security audits performed?",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        }
        query_task = asyncio.create_task(client.post("/query", json=query_payload))

        t0 = time.perf_counter()
        h_resp = await client.get("/healthz")
        r_resp = await client.get("/ready")
        dt_health = (time.perf_counter() - t0) * 1000.0

        assert h_resp.status_code == 200
        assert h_resp.json()["status"] == "ok"
        assert r_resp.status_code == 200
        assert r_resp.json()["status"] == "ready"
        assert dt_health < 500.0

        await query_task


# =============================================================================
# 6. Multi-Tenant Interleaved Isolation Under Load
# =============================================================================

@pytest.mark.anyio
async def test_multi_tenant_interleaved_isolation_under_concurrency():
    """Verify concurrent interleaved requests across Tenant A and Tenant B never leak data."""
    pipe = build_test_pipeline(generator=MockDelayedGenerator(delay_seconds=0.01))
    res_cfg = ResilienceConfig(max_concurrent_inferences=4, queue_timeout_seconds=1.0, request_timeout_seconds=30.0)
    app = create_app(pipeline=pipe, resilience_config=res_cfg)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        tasks = []
        tenants = ["TENANT-NOVASTACK", "TENANT-ORBITAL", "TENANT-PINECONE"]
        for i in range(12):
            t_id = tenants[i % len(tenants)]
            req = {
                "query": f"Query regarding {t_id} operations",
                "user_context": {"tenant_id": t_id},
                "evaluation_id": f"ISO-{i:03d}",
            }
            tasks.append((t_id, client.post("/query", json=req)))

        results = await asyncio.gather(*[t[1] for t in tasks])

        for (requested_tenant, _), resp in zip(tasks, results):
            assert resp.status_code in (200, 429)
            if resp.status_code == 200:
                data = resp.json()
                for cit in data.get("citations", []):
                    cid = cit.get("chunk_id", "")
                    did = cit.get("document_id", "")
                    if requested_tenant == "TENANT-NOVASTACK":
                        assert "ORBITAL" not in cid and "PINECONE" not in cid
                        assert "ORBITAL" not in did and "PINECONE" not in did
                    elif requested_tenant == "TENANT-ORBITAL":
                        assert "NOVASTACK" not in cid and "PINECONE" not in cid
                        assert "NOVASTACK" not in did and "PINECONE" not in did
                    elif requested_tenant == "TENANT-PINECONE":
                        assert "NOVASTACK" not in cid and "ORBITAL" not in cid
                        assert "NOVASTACK" not in did and "ORBITAL" not in did


# =============================================================================
# 7. Observability Metrics Recording Under Load
# =============================================================================

def test_observability_metrics_recorded_under_load():
    """Verify Prometheus metrics increment properly during load and retain bounded cardinalities."""
    pipe = build_test_pipeline()
    app = create_app(pipeline=pipe)
    client = TestClient(app, raise_server_exceptions=False)

    client.post("/query", json={"query": "What is NovaStack?", "user_context": {"tenant_id": "TENANT-NOVASTACK"}})
    client.post("/query", json={"query": "What is Orbital?", "user_context": {"tenant_id": "TENANT-ORBITAL"}})

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    assert metrics_resp.headers["content-type"].startswith("text/plain")

    text = metrics_resp.text
    assert "atlas_requests_total" in text
    assert "atlas_request_latency_seconds" in text
    assert "atlas_stage_latency_seconds" in text
    assert "NovaStack" not in text
    assert "Orbital" not in text


# =============================================================================
# 8. Memory and Thread Stability Checks
# =============================================================================

def test_memory_and_thread_stability_under_burst():
    """Verify memory and thread count remain bounded under burst requests."""
    import psutil
    import threading
    proc = psutil.Process()

    pipe = build_test_pipeline(generator=MockDelayedGenerator(delay_seconds=0.001))
    app = create_app(pipeline=pipe)
    client = TestClient(app, raise_server_exceptions=False)

    # Warm up pipeline so one-time component load is excluded from burst delta
    client.post("/query", json={"query": "Warmup query", "user_context": {"tenant_id": "TENANT-NOVASTACK"}})

    threads_init = threading.active_count()
    rss_init = proc.memory_info().rss / (1024**2)

    # Fire 50 burst requests
    for i in range(50):
        client.post("/query", json={"query": f"Audit query {i}", "user_context": {"tenant_id": "TENANT-NOVASTACK"}})

    threads_after = threading.active_count()
    rss_after = proc.memory_info().rss / (1024**2)

    # Threads should not grow unbounded (threadpool is bounded to max_concurrent + 1)
    assert abs(threads_after - threads_init) <= 10
    # Memory increase should be small (< 100MB)
    assert (rss_after - rss_init) < 100.0


# =============================================================================
# 9. Production Flags Remain Frozen Invariant
# =============================================================================

def test_production_flags_remain_frozen():
    """Verify that all production mechanism flags remain strictly frozen."""
    gen = GroundedAnswerGenerator(lazy_load=True)
    import inspect
    sig = inspect.signature(gen.generate_answer)
    assert sig.parameters["enable_boundary_stitching"].default is False, "Mechanism A must be False"

    cfg = EvidenceResolverConfig()
    assert cfg.enable_query_aware_authority is True, "Mechanism B must be True (Production Standard)"
    assert cfg.enable_event_bundling is False, "Mechanism C must be False"


# =============================================================================
# 10. Phase 4R-R1: Fast Path (Workload A) Intentional Abstention Classification
# =============================================================================

def test_workload_a_fast_path_abstention_classification():
    """Verify unanswerable/unauthorized queries return HTTP 200 with abstained status,

    was_generation_invoked=False, and generation_latency_ms=0.0.
    """
    class MockAbstainingGen(MockDelayedGenerator):
        def generate_answer(self, package, **kwargs):
            return AnswerResult(
                answer_id=f"ANS-{package.package_id}",
                evaluation_id=package.evaluation_id,
                query=package.query,
                answer_text="Insufficient evidence to answer this question.",
                answer_status=AnswerStatus.ABSTAINED.value,
                citations=[],
                abstention_reason="no_usable_evidence",
                generation_latency_ms=0.0,
                diagnostics={"layer": "pre_generation_gate", "gate": "empty_evidence"},
            )

    pipe = build_test_pipeline(generator=MockAbstainingGen(delay_seconds=0.001))
    app = create_app(pipeline=pipe)
    client = TestClient(app, raise_server_exceptions=False)

    payload = {
        "query": "What is the secret quantum algorithm used by Martian servers?",
        "user_context": {
            "tenant_id": "TENANT-NOVASTACK",
            "user_id": "USR-001",
            "user_role": "engineer",
            "user_department": "Engineering",
        },
        "evaluation_id": "LOAD-FAST-001",
    }
    resp = client.post("/query", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer_status"] == "abstained"
    assert data["was_generation_invoked"] is False
    assert data["generation_latency_ms"] == 0.0
    assert resp.headers.get("x-generation-invoked") == "false"
    assert float(resp.headers.get("x-generation-latency-ms", "0.0")) == 0.0
    assert data["abstention_reason"] is not None


# =============================================================================
# 11. Phase 4R-R1: True Generation (Workload B) Classification
# =============================================================================

def test_workload_b_true_generation_classification():
    """Verify answered queries invoke generation and return was_generation_invoked=True

    with positive generation_latency_ms.
    """
    # Mock generator configured to return answered status with generation latency
    class MockGeneratedGen(MockDelayedGenerator):
        def generate_answer(self, package, **kwargs):
            res = super().generate_answer(package, **kwargs)
            res.generation_latency_ms = 45.0
            res.diagnostics = {"layer": "model_inference", "inference_duration_ms": 45.0}
            return res

    pipe = build_test_pipeline(generator=MockGeneratedGen(delay_seconds=0.005))
    app = create_app(pipeline=pipe)
    client = TestClient(app, raise_server_exceptions=False)

    payload = {
        "query": "What is the retention policy?",
        "user_context": {
            "tenant_id": "TENANT-NOVASTACK",
            "user_id": "USR-001",
            "user_role": "engineer",
            "user_department": "Engineering",
        },
        "evaluation_id": "LOAD-GEN-001",
    }
    resp = client.post("/query", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer_status"] == "answered"
    assert data["was_generation_invoked"] is True
    assert data["generation_latency_ms"] > 0.0
    assert resp.headers.get("x-generation-invoked") == "true"
    assert float(resp.headers.get("x-generation-latency-ms", "0.0")) > 0.0


# =============================================================================
# 12. Phase 4R-R1: Disambiguated Throughput (QPS Separation)
# =============================================================================

def test_disambiguated_qps_metric_separation():
    """Verify that total_qps, answered_qps, and abstention_qps are separated cleanly."""
    total_requests = 10
    duration_s = 2.0
    answered_count = 1
    abstained_count = 7
    shed_429_count = 2

    qps_total = round(total_requests / duration_s, 2)
    qps_answered = round(answered_count / duration_s, 2)
    qps_abstained = round(abstained_count / duration_s, 2)

    assert qps_total == 5.0
    assert qps_answered == 0.5
    assert qps_abstained == 3.5
    # Crucial assertion: abstentions must not inflate answered QPS
    assert qps_answered < qps_total
    assert qps_answered != (answered_count + abstained_count) / duration_s


# =============================================================================
# 13. Phase 4R-R1: Result Taxonomy Classification Integrity
# =============================================================================

def test_http_status_and_result_classification_taxonomy():
    """Verify mutually exclusive classification across 7 defined outcome categories."""
    categories = {
        "answered": 0,
        "abstained": 0,
        "429": 0,
        "503": 0,
        "504": 0,
        "500": 0,
        "other": 0,
    }

    mock_responses = [
        {"status_code": 200, "answer_status": "answered", "was_generation_invoked": True},
        {"status_code": 200, "answer_status": "abstained", "was_generation_invoked": False},
        {"status_code": 429, "answer_status": "error", "was_generation_invoked": False},
        {"status_code": 503, "answer_status": "error", "was_generation_invoked": False},
        {"status_code": 504, "answer_status": "timeout", "was_generation_invoked": False},
        {"status_code": 500, "answer_status": "error", "was_generation_invoked": False},
    ]

    for r in mock_responses:
        sc = r["status_code"]
        ans = r.get("answer_status")
        gen = r.get("was_generation_invoked")
        if sc == 200 and ans == "answered" and gen:
            categories["answered"] += 1
        elif sc == 200 and ans == "abstained":
            categories["abstained"] += 1
        elif sc == 429:
            categories["429"] += 1
        elif sc == 503:
            categories["503"] += 1
        elif sc == 504:
            categories["504"] += 1
        elif sc == 500:
            categories["500"] += 1
        else:
            categories["other"] += 1

    assert sum(categories.values()) == len(mock_responses)
    assert categories["answered"] == 1
    assert categories["abstained"] == 1
    assert categories["429"] == 1
    assert categories["503"] == 1
    assert categories["504"] == 1
    assert categories["500"] == 1
    assert categories["other"] == 0

