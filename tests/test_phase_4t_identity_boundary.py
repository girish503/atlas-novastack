"""Focused Phase 4T tests for the cryptographic FastAPI identity boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novastack.observability import reset_metrics
from novastack.service import IdentityConfig, QueryRequest, QueryResponse, create_app


ISSUER = "https://identity.atlas.example/issuer"
AUDIENCE = "atlas-query-api"
SECRET = "phase-4t-verification-secret-that-is-long-enough"


def _b64url(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_jwt(
    *,
    payload_overrides: dict[str, Any] | None = None,
    header_overrides: dict[str, Any] | None = None,
    signing_secret: str = SECRET,
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "USR-4T-ENGINEER",
        "tenant_id": "TENANT-4T-A",
        "roles": ["engineer"],
        "departments": ["Engineering"],
        "role": "engineer",
        "department": "Engineering",
        "iat": now,
        "exp": now + 300,
    }
    if header_overrides:
        header.update(header_overrides)
    if payload_overrides:
        for key, value in payload_overrides.items():
            if value is None:
                payload.pop(key, None)
            else:
                payload[key] = value
    signed = f"{_b64url(header)}.{_b64url(payload)}"
    signature = hmac.new(
        signing_secret.encode("utf-8"), signed.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signed}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


def auth_config() -> IdentityConfig:
    return IdentityConfig(
        issuer=ISSUER,
        audience=AUDIENCE,
        hs256_secret=SECRET.encode("utf-8"),
        clock_skew_seconds=0,
    )


@dataclass
class RecordingPipeline:
    """Minimal deterministic API pipeline that records the trusted context."""

    requests: list[QueryRequest] = field(default_factory=list)

    def is_ready(self) -> tuple[bool, dict[str, bool]]:
        return True, {"bm25": True, "dense": True, "reranker": True, "generator": True}

    def get_active_generation_id(self) -> str:
        return "GEN-4T-TEST"

    def execute_query(
        self,
        request: QueryRequest,
        timeout_seconds: float | None = None,
        request_id: str | None = None,
    ) -> QueryResponse:
        self.requests.append(request)
        return QueryResponse(
            answer_id="ANS-4T-TEST",
            query=request.query,
            answer_text="Deterministic test response.",
            answer_status="abstained",
            citations=[],
            abstention_reason="test",
            latency_ms=0.1,
            request_id=request_id,
            index_generation_id="GEN-4T-TEST",
        )


def make_client(
    pipeline: RecordingPipeline | None = None,
    identity_config: IdentityConfig | None = None,
) -> tuple[TestClient, RecordingPipeline]:
    recording = pipeline or RecordingPipeline()
    return (
        TestClient(create_app(pipeline=recording, identity_config=identity_config or auth_config())),
        recording,
    )


def request_payload(**context_overrides: Any) -> dict[str, Any]:
    context = {"tenant_id": "TENANT-4T-A"}
    context.update(context_overrides)
    return {"query": "phase 4t identity check", "user_context": context}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_verified_jwt_claims_replace_client_context_before_pipeline_execution():
    """The pipeline receives roles, user and tenant exclusively from the JWT."""
    client, pipeline = make_client()

    response = client.post("/query", json=request_payload(), headers=bearer(make_jwt()))

    assert response.status_code == 200
    assert response.json()["index_generation_id"] == "GEN-4T-TEST"
    assert len(pipeline.requests) == 1
    verified_context = pipeline.requests[0].user_context
    assert verified_context.tenant_id == "TENANT-4T-A"
    assert verified_context.user_id == "USR-4T-ENGINEER"
    assert verified_context.user_role == "engineer"
    assert verified_context.roles == ["engineer"]
    assert verified_context.user_department == "Engineering"


def test_missing_bearer_token_is_rejected_and_challenge_header_is_preserved():
    client, pipeline = make_client()

    # An explicit empty header prevents the test-only legacy-client fixture from
    # supplying a credential; production has no such fixture or fallback.
    response = client.post("/query", json=request_payload(), headers={"Authorization": ""})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "authentication failed" in response.json()["detail"].lower()
    assert pipeline.requests == []


@pytest.mark.parametrize(
    ("payload_overrides", "header_overrides", "signing_secret"),
    [
        ({"iss": "https://other.example/issuer"}, None, SECRET),
        ({"aud": "other-audience"}, None, SECRET),
        ({"exp": int(time.time()) - 1}, None, SECRET),
        ({"sub": None}, None, SECRET),
        ({"nbf": int(time.time()) + 600}, None, SECRET),
        ({}, {"alg": "none"}, SECRET),
        ({}, None, "not-the-configured-verification-secret"),
    ],
    ids=("wrong_issuer", "wrong_audience", "expired", "missing_subject", "not_before", "wrong_algorithm", "bad_signature"),
)
def test_invalid_or_untrusted_jwts_never_reach_retrieval(
    payload_overrides: dict[str, Any],
    header_overrides: dict[str, Any] | None,
    signing_secret: str,
):
    client, pipeline = make_client()

    response = client.post(
        "/query",
        json=request_payload(),
        headers=bearer(
            make_jwt(
                payload_overrides=payload_overrides,
                header_overrides=header_overrides,
                signing_secret=signing_secret,
            )
        ),
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert pipeline.requests == []


@pytest.mark.parametrize(
    "body_context",
    [
        {"tenant_id": "TENANT-4T-B"},
        {"tenant_id": "TENANT-4T-A", "user_id": "USR-SPOOFED"},
        {"tenant_id": "TENANT-4T-A", "user_role": "administrator"},
        {"tenant_id": "TENANT-4T-A", "roles": ["administrator"]},
        {"tenant_id": "TENANT-4T-A", "departments": ["Finance"]},
    ],
    ids=("tenant", "subject", "primary_role", "roles", "departments"),
)
def test_client_context_conflicts_are_forbidden_before_retrieval(body_context: dict[str, Any]):
    client, pipeline = make_client()

    response = client.post("/query", json={"query": "check", "user_context": body_context}, headers=bearer(make_jwt()))

    assert response.status_code == 403
    assert pipeline.requests == []


def test_unconfigured_identity_makes_ready_and_query_fail_closed():
    unconfigured = IdentityConfig(
        issuer=None,
        audience=None,
        hs256_secret=None,
        configuration_error="missing_required_configuration",
    )
    client, pipeline = make_client(identity_config=unconfigured)

    readiness = client.get("/ready")
    query = client.post("/query", json=request_payload(), headers=bearer(make_jwt()))

    assert readiness.status_code == 503
    assert readiness.json()["components"]["authentication"] is False
    assert query.status_code == 503
    assert "not configured" in query.json()["detail"].lower()
    assert pipeline.requests == []


def test_bearer_credential_is_not_emitted_to_service_logs(caplog: pytest.LogCaptureFixture):
    client, _ = make_client()
    raw_token = "this-is-a-deliberately-raw-test-credential"
    caplog.set_level(logging.DEBUG, logger="novastack.service")

    response = client.post("/query", json=request_payload(), headers=bearer(raw_token))

    assert response.status_code == 401
    assert raw_token not in caplog.text


def test_identity_rejection_metric_uses_only_a_bounded_non_sensitive_outcome():
    reset_metrics()
    client, _ = make_client()

    rejected = client.post("/query", json=request_payload(), headers={"Authorization": ""})
    metrics = client.get("/metrics")

    assert rejected.status_code == 401
    assert 'atlas_identity_verification_failures_total{outcome="authentication"} 1' in metrics.text
    assert "TENANT-4T-A" not in metrics.text
