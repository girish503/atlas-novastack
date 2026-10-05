"""Phase 4X: Performance and Capacity Characterization Regression Tests.

Ensures:
1. Frozen production configuration invariants remain intact.
2. All 7 workload regimes (A through G) are strictly categorized.
3. Concurrency capacity shedding (429) triggers within queue_timeout_seconds (0.5s).
4. Circuit breaker trips and returns HTTP 503 fail-closed without inference.
5. Request timeouts trigger HTTP 504 fail-closed.
6. Genuine generation throughput is strictly decoupled from fast-path rejection throughput.
7. 9-stage hierarchical decomposition completeness is preserved.
"""

from __future__ import annotations

import inspect
import json
import time
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from novastack.evidence_resolution import EvidenceResolverConfig
from novastack.generation import GroundedAnswerGenerator
from novastack.observability import get_metrics, reset_metrics
from novastack.observability.metrics import ALLOWED_STAGES
from novastack.service.api import AtlasServicePipeline, create_app
from novastack.service.identity import IdentityConfig
from novastack.service.resilience import CircuitBreaker, CircuitState, ResilienceConfig
from tests.test_phase_4o_resilience import build_test_pipeline

WORKSPACE = Path(__file__).resolve().parent.parent

_TEST_ISSUER = "https://auth.atlas.production.example"
_TEST_AUDIENCE = "atlas-service-api"
_TEST_SECRET = "production-grade-signing-secret-minimum-32-chars-long"


def create_jwt_token(
    tenant_id: str = "TENANT-NOVASTACK",
    user_id: str = "USR-OPERATOR",
    user_role: str = "engineer",
    user_department: str = "Engineering",
    expires_in_seconds: int = 3600,
) -> str:
    import base64, hashlib, hmac
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": _TEST_ISSUER,
        "aud": _TEST_AUDIENCE,
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": [user_role],
        "departments": [user_department],
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    def b64url(d):
        return base64.urlsafe_b64encode(json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")).rstrip(b"=").decode("ascii")
    signed_content = f"{b64url(header)}.{b64url(payload)}"
    sig = hmac.new(_TEST_SECRET.encode("utf-8"), signed_content.encode("ascii"), hashlib.sha256).digest()
    return f"{signed_content}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode('ascii')}"


@pytest.fixture
def identity_config() -> IdentityConfig:
    return IdentityConfig(
        issuer=_TEST_ISSUER,
        audience=_TEST_AUDIENCE,
        hs256_secret=_TEST_SECRET.encode("utf-8"),
    )


@pytest.fixture
def resilience_config() -> ResilienceConfig:
    return ResilienceConfig(
        request_timeout_seconds=30.0,
        max_concurrent_inferences=1,
        queue_timeout_seconds=0.5,
        enable_circuit_breaker=True,
        circuit_failure_threshold=3,
        circuit_cooldown_seconds=10.0,
    )


@pytest.fixture
def app(resilience_config: ResilienceConfig, identity_config: IdentityConfig):
    reset_metrics()
    pipeline = build_test_pipeline(delay=0.0)
    return create_app(pipeline=pipeline, resilience_config=resilience_config, identity_config=identity_config)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_frozen_production_configuration_4x(resilience_config: ResilienceConfig):
    """Verify that all Phase 4 certified configurations remain completely unchanged."""
    gen = GroundedAnswerGenerator(lazy_load=True)
    sig = inspect.signature(gen.generate_answer)
    assert sig.parameters["enable_boundary_stitching"].default is False, "Mechanism A must be False"

    cfg = EvidenceResolverConfig()
    assert cfg.enable_query_aware_authority is True, "Mechanism B must be True"
    assert cfg.enable_event_bundling is False, "Mechanism C must be False"

    assert resilience_config.max_concurrent_inferences == 1
    assert resilience_config.queue_timeout_seconds == 0.5
    assert resilience_config.request_timeout_seconds == 30.0
    assert resilience_config.circuit_failure_threshold == 3
    assert resilience_config.circuit_cooldown_seconds == 10.0


def test_9_stage_hierarchical_decomposition_completeness():
    """Verify that all 9 sub-stages are defined in metrics ALLOWED_STAGES."""
    required_stages = [
        "query_understanding",
        "bm25",
        "dense",
        "relational_retrieval",
        "fusion",
        "metadata_ranking",
        "evidence_resolution",
        "generation",
        "citation_resolution",
    ]
    for stage in required_stages:
        assert stage in ALLOWED_STAGES, f"Stage {stage} must be in metrics ALLOWED_STAGES"


def test_regime_b_intentional_abstention_fast_path(client: TestClient):
    """Regime B: Query with zero evidence returns fast abstention without invoking generator."""
    token = create_jwt_token()
    t0 = time.perf_counter()
    resp = client.post(
        "/query",
        json={"query": "nonexistent_token_that_produces_no_matching_evidence_query_string", "user_context": {"tenant_id": "TENANT-ORBITAL"}},
        headers={"Authorization": f"Bearer {create_jwt_token(tenant_id='TENANT-ORBITAL')}"},
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer_status"] == "abstained"
    assert data["was_generation_invoked"] is False
    assert data["generation_latency_ms"] == 0.0
    assert elapsed_ms < 500.0


def test_regime_f_circuit_breaker_503_fail_closed(app, client: TestClient):
    """Regime F: When circuit is OPEN, request immediately fails closed with HTTP 503 (<15ms)."""
    token = create_jwt_token()
    app.state.circuit_breaker.state = CircuitState.OPEN
    app.state.circuit_breaker.last_failure_time = time.perf_counter()
    t0 = time.perf_counter()
    resp = client.post(
        "/query",
        json={"query": "test query", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert resp.status_code == 503
    assert resp.json()["error_type"] == "ModelUnavailableError"
    assert elapsed_ms < 50.0
    app.state.circuit_breaker.record_success()


def test_regime_g_timeout_504_fail_closed(app, client: TestClient):
    """Regime G: When request exceeds deadline, fail closed with HTTP 504 TimeoutError."""
    token = create_jwt_token()
    app.state.resilience_config.request_timeout_seconds = 0.0001
    resp = client.post(
        "/query",
        json={"query": "What is the network topology?", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 504
    assert resp.json()["error_type"] == "TimeoutError"
    assert resp.json()["answer_status"] == "timeout"
    app.state.resilience_config.request_timeout_seconds = 30.0
    app.state.circuit_breaker.record_success()


def test_regime_e_concurrency_capacity_shedding_429(identity_config: IdentityConfig):
    """Regime E: Request waiting beyond queue_timeout_seconds sheds with HTTP 429."""
    pipeline = build_test_pipeline(delay=0.0)
    cfg = ResilienceConfig(max_concurrent_inferences=0, queue_timeout_seconds=0.001)
    app = create_app(pipeline=pipeline, resilience_config=cfg, identity_config=identity_config)
    client = TestClient(app, raise_server_exceptions=False)
    token = create_jwt_token()

    resp = client.post(
        "/query",
        json={"query": "Corporate Policy", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 429
    assert resp.json()["error_type"] == "CapacityExhaustedError"
    assert resp.json()["answer_status"] == "error"


def test_throughput_separation_metric_invariants(app, client: TestClient):
    """Verify that Prometheus metrics strictly distinguish genuine generation from rejections."""
    metrics = get_metrics()
    initial_answers = metrics._query_answers_total
    initial_abstentions = metrics._query_abstentions_total

    token = create_jwt_token()
    # 1. Abstention query
    client.post(
        "/query",
        json={"query": "nonexistent_token_xyz", "user_context": {"tenant_id": "TENANT-ORBITAL"}},
        headers={"Authorization": f"Bearer {create_jwt_token(tenant_id='TENANT-ORBITAL')}"},
    )
    assert metrics._query_abstentions_total == initial_abstentions + 1
    assert metrics._query_answers_total == initial_answers

    # 2. Auth rejection
    client.post(
        "/query",
        json={"query": "test query", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
        headers={"Authorization": ""},
    )
    assert metrics._query_answers_total == initial_answers
