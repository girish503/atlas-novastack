"""RET-EVAL-09: Live HTTP Integration Tests for Canary Routing and Rollback Safety.

Tests the full FastAPI /query endpoint via TestClient(app) under authenticated JWTs:
- TEST 1: Canary disabled -> baseline resolver
- TEST 2: Canary enabled 100% -> H5.1 resolver
- TEST 3: Canary enabled 0% -> baseline resolver
- TEST 4: Canary enabled 50% -> deterministic repeated request produces same variant
- TEST 5: Live kill-switch -> disable canary returns immediate baseline without service restart
- TEST 6: Missing / malformed canary config -> fail-closed to baseline
- TEST 7: Authentication failure -> HTTP 401, CanaryRouter NOT reached
- TEST 8: Tenant mismatch -> HTTP 403, CanaryRouter NOT reached
- TEST 9: Cross-tenant retrieval -> zero unauthorized documents/evidence
- TEST 10: Telemetry audit -> variant recorded, zero secrets/tokens logged
- TEST 11: Phase 7 Live Rollback Test sequence
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any
import pytest
from fastapi.testclient import TestClient

from novastack.canary import CanaryConfig, CanaryRouter
from novastack.observability import LogCaptureHandler, reset_metrics
from novastack.service.api import AtlasServicePipeline, create_app
from novastack.service.identity import IdentityConfig
from novastack.service.resilience import ResilienceConfig
from novastack.service.schemas import QueryRequest, QueryResponse
from novastack.generation import AnswerResult, AnswerStatus
from novastack.citation_validator import Citation

ISSUER = "https://identity.atlas.example/issuer"
AUDIENCE = "atlas-query-api"
SECRET = "ret-eval-09-test-secret-that-is-long-enough-32-chars"


def _b64url(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_jwt(
    *,
    sub: str = "USR-CANARY-01",
    tenant_id: str = "TENANT-NOVASTACK",
    role: str = "engineer",
    department: str = "Engineering",
    signing_secret: str = SECRET,
    exp_offset: int = 300,
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "tenant_id": tenant_id,
        "roles": [role],
        "departments": [department],
        "role": role,
        "department": department,
        "iat": now,
        "exp": now + exp_offset,
    }
    signed = f"{_b64url(header)}.{_b64url(payload)}"
    signature = hmac.new(
        signing_secret.encode("utf-8"), signed.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signed}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


class FastDeterministicTestGenerator:
    """Mock answer generator providing fast, deterministic answering for HTTP tests."""

    def is_ready(self) -> bool:
        return True

    def generate_answer(self, package: Any, **kwargs: Any) -> AnswerResult:
        items = getattr(package, "items", None) or getattr(package, "selected_evidence", None)
        if not package or not items:
            return AnswerResult(
                answer_id=f"ANS-{int(time.time() * 1000)}",
                evaluation_id=getattr(package, "evaluation_id", "TEST-EVAL"),
                query=getattr(package, "query", ""),
                answer_text="Insufficient evidence.",
                answer_status=AnswerStatus.ABSTAINED.value,
                citations=[],
                abstention_reason="insufficient_evidence",
                generation_latency_ms=1.0,
            )

        top_ev = items[0]
        cit = Citation(
            raw_tag="[EVD-001]",
            evidence_id=getattr(top_ev, "evidence_id", "EVD-001"),
            document_id=top_ev.document_id,
            chunk_id=top_ev.chunk_id,
            title=getattr(top_ev, "title", "Test Document"),
            status="VALID",
        )
        return AnswerResult(
            answer_id=f"ANS-{int(time.time() * 1000)}",
            evaluation_id=getattr(package, "evaluation_id", "TEST-EVAL"),
            query=getattr(package, "query", ""),
            answer_text=f"Deterministic statement from {top_ev.document_id}.",
            answer_status=AnswerStatus.ANSWERED.value,
            citations=[cit],
            generation_latency_ms=1.5,
        )


@pytest.fixture(scope="module")
def shared_pipeline():
    """Create a real pipeline with real indexes and catalogs, using fast test generator."""
    pipe = AtlasServicePipeline.create_default(
        lazy_generator=False,
        generator=FastDeterministicTestGenerator(),
    )
    # Warmup search to ensure dense embeddings model is paged into CPU memory
    _ = pipe.dense_index.search(query="warmup", top_k=2, filters={"tenant_id": "TENANT-NOVASTACK"})
    _ = pipe.bm25_index.search(query="warmup", top_k=2, filters={"tenant_id": "TENANT-NOVASTACK"})
    return pipe


@pytest.fixture
def identity_config():
    return IdentityConfig(
        issuer=ISSUER,
        audience=AUDIENCE,
        hs256_secret=SECRET.encode("utf-8"),
        clock_skew_seconds=0,
    )


@pytest.fixture
def resilience_config():
    return ResilienceConfig(
        request_timeout_seconds=60.0,
        max_concurrent_inferences=4,
    )


@pytest.fixture
def client(shared_pipeline, identity_config, resilience_config):
    app = create_app(
        pipeline=shared_pipeline,
        identity_config=identity_config,
        resilience_config=resilience_config,
    )
    return TestClient(app)


def test_01_canary_disabled_routes_to_baseline(client, monkeypatch):
    monkeypatch.setenv("ATLAS_CANARY_ENABLED", "false")
    monkeypatch.setenv("ATLAS_CANARY_TRAFFIC_PERCENTAGE", "0.0")

    token = make_jwt(tenant_id="TENANT-NOVASTACK")
    resp = client.post(
        "/query",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": "What is the checkout service configuration?",
            "user_context": {
                "tenant_id": "TENANT-NOVASTACK",
                "user_id": "USR-CANARY-01",
                "roles": ["engineer"],
                "departments": ["Engineering"],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["canary_variant"] == "baseline"
    assert data["canary_bucket"] == -1
    assert resp.headers.get("x-canary-variant") == "baseline"
    assert resp.headers.get("x-canary-bucket") == "-1"


def test_02_canary_enabled_100pct_routes_to_h5_1(client, monkeypatch):
    monkeypatch.setenv("ATLAS_CANARY_ENABLED", "true")
    monkeypatch.setenv("ATLAS_CANARY_TRAFFIC_PERCENTAGE", "100.0")

    token = make_jwt(tenant_id="TENANT-NOVASTACK")
    resp = client.post(
        "/query",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": "What is the checkout service configuration?",
            "user_context": {
                "tenant_id": "TENANT-NOVASTACK",
                "user_id": "USR-CANARY-01",
                "roles": ["engineer"],
                "departments": ["Engineering"],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["canary_variant"] == "h5_1"
    assert 0 <= data["canary_bucket"] < 100
    assert resp.headers.get("x-canary-variant") == "h5_1"
    assert resp.headers.get("x-canary-bucket") == str(data["canary_bucket"])


def test_03_canary_enabled_0pct_routes_to_baseline(client, monkeypatch):
    monkeypatch.setenv("ATLAS_CANARY_ENABLED", "true")
    monkeypatch.setenv("ATLAS_CANARY_TRAFFIC_PERCENTAGE", "0.0")

    token = make_jwt(tenant_id="TENANT-NOVASTACK")
    resp = client.post(
        "/query",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": "What is the checkout service configuration?",
            "user_context": {
                "tenant_id": "TENANT-NOVASTACK",
                "user_id": "USR-CANARY-01",
                "roles": ["engineer"],
                "departments": ["Engineering"],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["canary_variant"] == "baseline"
    assert data["canary_bucket"] == -1
    assert resp.headers.get("x-canary-variant") == "baseline"


def test_04_canary_deterministic_routing_repeated_requests(client, monkeypatch):
    monkeypatch.setenv("ATLAS_CANARY_ENABLED", "true")
    monkeypatch.setenv("ATLAS_CANARY_TRAFFIC_PERCENTAGE", "50.0")

    token = make_jwt(tenant_id="TENANT-NOVASTACK")
    payload = {
        "query": "What incident affected SVC-NS-0005?",
        "evaluation_id": "EVAL-DETERMINISTIC-01",
        "user_context": {
            "tenant_id": "TENANT-NOVASTACK",
            "user_id": "USR-CANARY-01",
            "roles": ["engineer"],
            "departments": ["Engineering"],
        },
    }

    r1 = client.post("/query", headers={"Authorization": f"Bearer {token}"}, json=payload)
    r2 = client.post("/query", headers={"Authorization": f"Bearer {token}"}, json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200

    d1 = r1.json()
    d2 = r2.json()
    assert d1["canary_variant"] == d2["canary_variant"]
    assert d1["canary_bucket"] == d2["canary_bucket"]


def test_05_live_kill_switch_through_real_http_path(client, monkeypatch):
    # Step 1: Start with H5.1 enabled at 100%
    monkeypatch.setenv("ATLAS_CANARY_ENABLED", "true")
    monkeypatch.setenv("ATLAS_CANARY_TRAFFIC_PERCENTAGE", "100.0")

    token = make_jwt(tenant_id="TENANT-NOVASTACK")
    payload = {
        "query": "What is incident INC-NS-0001?",
        "user_context": {
            "tenant_id": "TENANT-NOVASTACK",
            "user_id": "USR-CANARY-01",
            "roles": ["engineer"],
            "departments": ["Engineering"],
        },
    }

    r1 = client.post("/query", headers={"Authorization": f"Bearer {token}"}, json=payload)
    assert r1.status_code == 200
    assert r1.json()["canary_variant"] == "h5_1"

    # Step 2: Trip live kill-switch
    monkeypatch.setenv("ATLAS_CANARY_ENABLED", "false")

    r2 = client.post("/query", headers={"Authorization": f"Bearer {token}"}, json=payload)
    assert r2.status_code == 200
    assert r2.json()["canary_variant"] == "baseline"
    assert r2.json()["canary_bucket"] == -1
    assert r2.headers.get("x-canary-variant") == "baseline"


def test_06_missing_or_invalid_configuration_fails_closed(client, monkeypatch):
    monkeypatch.setenv("ATLAS_CANARY_ENABLED", "true")
    monkeypatch.setenv("ATLAS_CANARY_TRAFFIC_PERCENTAGE", "not-a-number-bad-input")

    token = make_jwt(tenant_id="TENANT-NOVASTACK")
    resp = client.post(
        "/query",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": "Test query with bad canary percentage",
            "user_context": {
                "tenant_id": "TENANT-NOVASTACK",
                "user_id": "USR-CANARY-01",
                "roles": ["engineer"],
                "departments": ["Engineering"],
            },
        },
    )
    assert resp.status_code == 200
    assert resp.json()["canary_variant"] == "baseline"
    assert resp.json()["canary_bucket"] == -1


def test_07_authentication_failure_returns_401_canary_not_reached(client, shared_pipeline):
    initial_count = shared_pipeline.canary_router.route_call_count

    # Request with invalid token
    resp = client.post(
        "/query",
        headers={"Authorization": "Bearer invalid.fake.token"},
        json={
            "query": "Unauthorized attempt",
            "user_context": {
                "tenant_id": "TENANT-NOVASTACK",
                "user_id": "USR-ATTACKER",
                "roles": ["engineer"],
                "departments": ["Engineering"],
            },
        },
    )
    assert resp.status_code == 401
    # Canary router must NOT have been called
    assert shared_pipeline.canary_router.route_call_count == initial_count


def test_08_tenant_mismatch_returns_403_canary_not_reached(client, shared_pipeline):
    initial_count = shared_pipeline.canary_router.route_call_count

    # JWT specifies TENANT-A, but request body asks for TENANT-B
    token = make_jwt(tenant_id="TENANT-A")
    resp = client.post(
        "/query",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": "Context mismatch attempt",
            "user_context": {
                "tenant_id": "TENANT-B",
                "user_id": "USR-CANARY-01",
                "roles": ["engineer"],
                "departments": ["Engineering"],
            },
        },
    )
    assert resp.status_code == 403
    # Canary router must NOT have been called
    assert shared_pipeline.canary_router.route_call_count == initial_count


def test_09_cross_tenant_retrieval_returns_zero_unauthorized_evidence(client, monkeypatch):
    monkeypatch.setenv("ATLAS_CANARY_ENABLED", "true")
    monkeypatch.setenv("ATLAS_CANARY_TRAFFIC_PERCENTAGE", "100.0")

    # Authenticate as TENANT-ALPHA
    token = make_jwt(sub="USR-ALPHA", tenant_id="TENANT-ALPHA")
    resp = client.post(
        "/query",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": "What are the secret keys for TENANT-NOVASTACK?",
            "user_context": {
                "tenant_id": "TENANT-ALPHA",
                "user_id": "USR-ALPHA",
                "roles": ["engineer"],
                "departments": ["Engineering"],
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    # Any cited documents must not belong to other tenants
    for cit in data.get("citations", []):
        doc_id = cit.get("document_id", "")
        # Should never cite forbidden or cross-tenant docs
        assert "NOVASTACK" not in doc_id or "TENANT-ALPHA" in doc_id


def test_10_telemetry_audit_no_credentials_logged(client, monkeypatch):
    logger = logging.getLogger("novastack.service")
    handler = LogCaptureHandler()
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)

    monkeypatch.setenv("ATLAS_CANARY_ENABLED", "true")
    monkeypatch.setenv("ATLAS_CANARY_TRAFFIC_PERCENTAGE", "100.0")

    secret_bearer_token = make_jwt(tenant_id="TENANT-NOVASTACK")
    try:
        resp = client.post(
            "/query",
            headers={"Authorization": f"Bearer {secret_bearer_token}"},
            json={
                "query": "Query for telemetry logging verification",
                "user_context": {
                    "tenant_id": "TENANT-NOVASTACK",
                    "user_id": "USR-CANARY-01",
                    "roles": ["engineer"],
                    "departments": ["Engineering"],
                },
            },
        )
        assert resp.status_code == 200

        # Raw log lines check
        raw_text = " ".join(handler.raw_lines)
        assert secret_bearer_token not in raw_text
        assert SECRET not in raw_text

        # Find query_completed record among captured structured events
        completed_records = [r for r in handler.records if r.get("event") == "query_completed"]
        assert len(completed_records) >= 1, "Expected query_completed structured log event"

        completed = completed_records[-1]
        assert completed.get("canary_variant") == "h5_1"
        assert completed.get("candidate_version") == "0.4.14-rc1+h5.1"
        assert 0 <= completed.get("canary_bucket", -1) < 100
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


def test_11_live_rollback_sequence(client, monkeypatch):
    """Phase 7 Mandatory Live Rollback Sequence:
    1. Canary enabled (100%).
    2. Route request to H5.1.
    3. Record: request_id, variant, citations, answer_status.
    4. Trip kill-switch (ATLAS_CANARY_ENABLED=false).
    5. Repeat identical authenticated request.
    6. Verify baseline routing.
    7. Verify zero stale H5.1 state.
    8. Verify downstream citations and answer status remain valid.
    """
    # 1. Enable canary at 100%
    monkeypatch.setenv("ATLAS_CANARY_ENABLED", "true")
    monkeypatch.setenv("ATLAS_CANARY_TRAFFIC_PERCENTAGE", "100.0")

    token = make_jwt(tenant_id="TENANT-NOVASTACK")
    query_payload = {
        "query": "What was the root cause of incident INC-NS-0001?",
        "evaluation_id": "EVAL-ROLLBACK-LIVE",
        "user_context": {
            "tenant_id": "TENANT-NOVASTACK",
            "user_id": "USR-CANARY-01",
            "roles": ["engineer"],
            "departments": ["Engineering"],
        },
    }

    # 2. Route request under H5.1
    resp_h5_1 = client.post(
        "/query",
        headers={"Authorization": f"Bearer {token}"},
        json=query_payload,
    )
    assert resp_h5_1.status_code == 200
    h5_1_data = resp_h5_1.json()

    # 3. Record candidate state
    rid_1 = h5_1_data["request_id"]
    var_1 = h5_1_data["canary_variant"]
    cits_1 = h5_1_data["citations"]
    status_1 = h5_1_data["answer_status"]
    assert var_1 == "h5_1"
    assert status_1 in ("answered", "partially_answered", "abstained")

    # 4. Trip kill-switch
    monkeypatch.setenv("ATLAS_CANARY_ENABLED", "false")

    # 5. Repeat identical request
    resp_base = client.post(
        "/query",
        headers={"Authorization": f"Bearer {token}"},
        json=query_payload,
    )
    assert resp_base.status_code == 200
    base_data = resp_base.json()

    # 6. Verify baseline routing
    assert base_data["canary_variant"] == "baseline"
    assert base_data["canary_bucket"] == -1
    assert resp_base.headers.get("x-canary-variant") == "baseline"

    # 7. Verify no stale H5.1 state
    assert base_data["canary_variant"] != var_1

    # 8. Verify downstream behavior remains valid
    assert base_data["answer_status"] in ("answered", "partially_answered", "abstained")
    assert "latency_ms" in base_data
    assert base_data["request_id"] is not None
