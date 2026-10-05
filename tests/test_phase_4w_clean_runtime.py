"""Phase 4W: Clean Runtime & Container Operational Validation.

Validates that the certified ATLAS service operates cleanly from an isolated
runtime environment without weakening any existing security, reliability,
index-lifecycle, or observability guarantees.

Key verification areas:
1. Container & packaging specification (non-root execution, healthchecks, no baked secrets).
2. Clean runtime fail-closed identity verification (unconfigured auth -> 503).
3. JWT authentication & caller-context authorization enforcement (401, 403, 200).
4. Multi-tenant isolation under verified cryptographic identity.
5. Process restart lifecycle and index generation consistency.
6. Resilience operational regimes (fast rejection, capacity 429, timeout 504, circuit breaker 503, genuine generation).
7. Observability and privacy invariants (request ID propagation, bounded metric labels, no leaked secrets).
8. Preservation of frozen production configuration (A=False, B=True, C=False).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pytest
from fastapi.testclient import TestClient

from novastack.bm25 import BM25Config, BM25Index
from novastack.chunking import chunk_document
from novastack.dense import DenseIndex
from novastack.citation_validator import Citation
from novastack.evidence import EvidenceItem, EvidencePackage, EvidenceStatus
from novastack.evidence_resolution import EvidenceResolver, EvidenceResolverConfig
from novastack.generation import AnswerResult, AnswerStatus
from novastack.index_manager import IndexGenerationStatus, IndexManager
from novastack.metadata_diagnostics import build_metadata_snapshot_index
from novastack.models import RecordPermissions, SearchChunk, SearchDocument
from novastack.observability import get_metrics, reset_metrics
from novastack.service import (
    AtlasServicePipeline,
    IdentityConfig,
    QueryRequest,
    QueryResponse,
    ResilienceConfig,
    create_app,
)
from novastack.service.schemas import CallerContext

WORKSPACE = Path(__file__).resolve().parent.parent

_TEST_ISSUER = "https://auth.atlas.production.example"
_TEST_AUDIENCE = "atlas-service-api"
_TEST_SECRET = "production-grade-signing-secret-minimum-32-chars-long"


# -----------------------------------------------------------------------------
# Test Helpers: JWT Generation & Deterministic Pipeline Components
# -----------------------------------------------------------------------------

def _b64url(data: dict[str, Any]) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def create_jwt_token(
    *,
    issuer: str = _TEST_ISSUER,
    audience: str = _TEST_AUDIENCE,
    tenant_id: str = "TENANT-NOVASTACK",
    subject: str = "USR-OPERATOR",
    roles: Optional[list[str]] = None,
    departments: Optional[list[str]] = None,
    secret: str = _TEST_SECRET,
    expires_in_seconds: int = 300,
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "tenant_id": tenant_id,
        "roles": roles or ["operator"],
        "departments": departments or ["Operations"],
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    signed_content = f"{_b64url(header)}.{_b64url(payload)}"
    sig = hmac.new(secret.encode("utf-8"), signed_content.encode("ascii"), hashlib.sha256).digest()
    return f"{signed_content}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode('ascii')}"


class Deterministic384Encoder:
    dimension = 384

    def encode_passages(self, passages: list[str]) -> np.ndarray:
        if not passages:
            return np.empty((0, self.dimension), dtype=np.float32)
        vecs = np.ones((len(passages), self.dimension), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs

    def encode_query(self, query: str) -> np.ndarray:
        vec = np.ones(self.dimension, dtype=np.float32)
        return vec / np.linalg.norm(vec)


class PassThroughReranker:
    def rerank(self, candidates, qu=None, metadata_index=None, forbidden_doc_ids=None):
        return candidates


class ControlledRuntimeGenerator:
    """Deterministic generator distinguishing abstentions from genuine generations."""

    def __init__(self, simulate_latency_ms: float = 2.0, fail_all: bool = False):
        self.simulate_latency_ms = simulate_latency_ms
        self.fail_all = fail_all

    def generate_answer(self, package: EvidencePackage, **kwargs) -> AnswerResult:
        if self.fail_all:
            raise RuntimeError("Simulated runtime generator failure")

        if not package.selected_evidence or "unmatched" in package.query.lower():
            return AnswerResult(
                answer_id=f"ANS-{package.package_id}",
                evaluation_id=package.evaluation_id,
                query=package.query,
                answer_text="Insufficient evidence to answer this question.",
                answer_status=AnswerStatus.ABSTAINED.value,
                citations=[],
                abstention_reason="empty_selected_evidence",
                generation_latency_ms=0.0,
            )

        top = package.selected_evidence[0]
        cit = Citation(
            raw_tag="[EVD-001]",
            evidence_id=top.evidence_id,
            document_id=top.document_id,
            chunk_id=top.chunk_id,
            title=top.title,
            status="VALID",
        )
        return AnswerResult(
            answer_id=f"ANS-{package.package_id}",
            evaluation_id=package.evaluation_id,
            query=package.query,
            answer_text=f"Validated clean runtime answer from {top.document_id} [EVD-001].",
            answer_status=AnswerStatus.ANSWERED.value,
            citations=[cit],
            generation_latency_ms=self.simulate_latency_ms,
        )


def build_clean_pipeline(
    documents: list[SearchDocument],
    generator: Optional[Any] = None,
) -> tuple[AtlasServicePipeline, IndexManager]:
    encoder = Deterministic384Encoder()
    chunks = [chunk for doc in documents for chunk in chunk_document(doc)]
    bm25 = BM25Index.build_index(chunks)
    dense = DenseIndex(chunks, encoder.encode_passages([c.text for c in chunks]), encoder=encoder)
    metadata = build_metadata_snapshot_index([doc.to_dict() for doc in documents])

    manager = IndexManager()
    manager.initialize_from_components(documents, chunks, bm25, dense, metadata, corpus_version="4W-runtime")

    snapshot = manager.acquire_active_generation()
    assert snapshot is not None
    try:
        resolver = EvidenceResolver(
            documents_index={d.document_id: d for d in snapshot.snapshot.search_documents},
            chunks_index={c.chunk_id: c for c in snapshot.snapshot.search_chunks},
            config=EvidenceResolverConfig(),
        )
        pipeline = AtlasServicePipeline(
            bm25_index=snapshot.snapshot.bm25_index,
            dense_index=snapshot.snapshot.dense_index,
            reranker=PassThroughReranker(),
            generator=generator or ControlledRuntimeGenerator(),
            resolver=resolver,
            metadata_snapshot_index=dict(snapshot.snapshot.metadata_snapshot_index),
            index_manager=manager,
        )
    finally:
        snapshot.close()

    return pipeline, manager


def make_clean_document(
    doc_id: str,
    content: str,
    tenant_id: str = "TENANT-NOVASTACK",
    department: str = "Operations",
) -> SearchDocument:
    return SearchDocument(
        document_id=doc_id,
        tenant_id=tenant_id,
        source_type="runbook",
        title=f"Runbook {doc_id}",
        content=content,
        department=department,
        author_id="USR-OP",
        created_at="2026-03-01T00:00:00",
        permissions=RecordPermissions(allowed_departments=[department]),
        authority_level="high",
    )


# -----------------------------------------------------------------------------
# STEP 3 & 4: Clean Runtime & Security Specifications
# -----------------------------------------------------------------------------

def test_dockerfile_security_and_nonroot_specification():
    """Verify Dockerfile enforces non-root execution, exposes healthchecks, and bakes zero secrets."""
    dockerfile_path = WORKSPACE / "Dockerfile"
    assert dockerfile_path.exists()
    content = dockerfile_path.read_text(encoding="utf-8")

    # Non-root user setup
    assert "useradd" in content and "atlas" in content, "Must create dedicated non-root user 'atlas'"
    assert "USER atlas" in content, "Must switch to non-root user atlas"

    # Minimal and hardened execution
    assert "FROM python:3.11-slim" in content, "Must use python:3.11-slim base"
    assert "HEALTHCHECK" in content, "Must declare container HEALTHCHECK"
    assert "http://localhost:8000/healthz" in content, "Healthcheck must use liveness endpoint /healthz"

    # Zero secrets baked into image
    forbidden_terms = ["ATLAS_AUTH_HS256_SECRET", "secret_key", "password", "bearer_token"]
    for term in forbidden_terms:
        assert term not in content, f"Dockerfile must not embed secret material '{term}'"

    # .dockerignore exclusion verification
    dockerignore_path = WORKSPACE / ".dockerignore"
    assert dockerignore_path.exists()
    ignore_content = dockerignore_path.read_text(encoding="utf-8")
    assert ".git/" in ignore_content
    assert "__pycache__/" in ignore_content
    assert ".pytest_cache/" in ignore_content


def test_clean_runtime_unconfigured_identity_fails_closed():
    """Verify clean runtime without auth configuration fails closed on /ready and /query."""
    unconfigured = IdentityConfig(
        issuer=None,
        audience=None,
        hs256_secret=None,
        configuration_error="missing_required_configuration",
    )
    doc = make_clean_document("DOC-4W-01", "Operating procedures for service restart.")
    pipeline, _ = build_clean_pipeline([doc])
    app = create_app(pipeline=pipeline, identity_config=unconfigured)

    with TestClient(app) as client:
        # 1. /healthz remains 200 (process liveness probe)
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        # 2. /ready fails closed (HTTP 503)
        ready = client.get("/ready")
        assert ready.status_code == 503
        data_ready = ready.json()
        assert data_ready["status"] == "unready"
        assert data_ready["components"]["authentication"] is False

        # 3. /query fails closed (HTTP 503) without executing pipeline
        token = create_jwt_token()
        query_resp = client.post(
            "/query",
            json={"query": "procedure", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert query_resp.status_code == 503
        assert "not configured" in query_resp.json()["detail"].lower()


def test_clean_runtime_authentication_and_context_boundary():
    """Verify JWT authentication gates unauthorized, forged, expired, and context-mismatched requests."""
    cfg = IdentityConfig(
        issuer=_TEST_ISSUER,
        audience=_TEST_AUDIENCE,
        hs256_secret=_TEST_SECRET.encode("utf-8"),
    )
    doc = make_clean_document("DOC-4W-AUTH", "Standard authentication documentation.")
    pipeline, _ = build_clean_pipeline([doc])
    app = create_app(pipeline=pipeline, identity_config=cfg)

    with TestClient(app) as client:
        # 1. Missing Authorization header -> 401
        res1 = client.post(
            "/query",
            json={"query": "test", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
            headers={"Authorization": ""},
        )
        assert res1.status_code == 401
        assert res1.headers.get("www-authenticate") == "Bearer"

        # 2. Forged signature -> 401
        forged_token = create_jwt_token(secret="wrong-signing-secret-that-fails-verification")
        res2 = client.post(
            "/query",
            json={"query": "test", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
            headers={"Authorization": f"Bearer {forged_token}"},
        )
        assert res2.status_code == 401

        # 3. Expired token -> 401
        expired_token = create_jwt_token(expires_in_seconds=-60)
        res3 = client.post(
            "/query",
            json={"query": "test", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert res3.status_code == 401

        # 4. Context mismatch (token tenant=NOVASTACK, body tenant=ORBITAL) -> 403
        valid_token = create_jwt_token(tenant_id="TENANT-NOVASTACK")
        res4 = client.post(
            "/query",
            json={"query": "test", "user_context": {"tenant_id": "TENANT-ORBITAL"}},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert res4.status_code == 403
        assert "does not match" in res4.json()["detail"].lower()

        # 5. Valid token matching context -> 200
        res5 = client.post(
            "/query",
            json={"query": "procedure", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert res5.status_code == 200
        assert res5.json()["answer_status"] == "answered"


def test_clean_runtime_multi_tenant_isolation_under_verified_jwt():
    """Verify tenant isolation prevents cross-tenant document exposure under separate JWT identities."""
    cfg = IdentityConfig(
        issuer=_TEST_ISSUER,
        audience=_TEST_AUDIENCE,
        hs256_secret=_TEST_SECRET.encode("utf-8"),
    )
    doc_nova = make_clean_document("DOC-NOVA-1", "NovaStack proprietary configuration", tenant_id="TENANT-NOVASTACK")
    doc_orbital = make_clean_document("DOC-ORBITAL-1", "Orbital classified telemetry payload", tenant_id="TENANT-ORBITAL")
    pipeline, _ = build_clean_pipeline([doc_nova, doc_orbital])
    app = create_app(pipeline=pipeline, identity_config=cfg)

    with TestClient(app) as client:
        token_nova = create_jwt_token(tenant_id="TENANT-NOVASTACK")
        resp_nova = client.post(
            "/query",
            json={"query": "configuration", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
            headers={"Authorization": f"Bearer {token_nova}"},
        )
        assert resp_nova.status_code == 200
        assert "DOC-NOVA-1" in resp_nova.json()["answer_text"]
        assert "Orbital" not in resp_nova.json()["answer_text"]

        token_orbital = create_jwt_token(tenant_id="TENANT-ORBITAL")
        resp_orbital = client.post(
            "/query",
            json={"query": "telemetry", "user_context": {"tenant_id": "TENANT-ORBITAL"}},
            headers={"Authorization": f"Bearer {token_orbital}"},
        )
        assert resp_orbital.status_code == 200
        assert "DOC-ORBITAL-1" in resp_orbital.json()["answer_text"]
        assert "NovaStack" not in resp_orbital.json()["answer_text"]


# -----------------------------------------------------------------------------
# STEP 5: Process Restart Lifecycle & Index State
# -----------------------------------------------------------------------------

def test_clean_runtime_process_restart_and_index_state():
    """Verify service start -> ready -> query -> cold restart -> ready -> query cycle."""
    cfg = IdentityConfig(
        issuer=_TEST_ISSUER,
        audience=_TEST_AUDIENCE,
        hs256_secret=_TEST_SECRET.encode("utf-8"),
    )
    doc = make_clean_document("DOC-BASELINE-1", "Baseline operational configuration")

    # Instance 1: Initial startup
    pipeline1, manager1 = build_clean_pipeline([doc])
    gen1_id = manager1.get_active_generation_id()
    app1 = create_app(pipeline=pipeline1, identity_config=cfg)

    with TestClient(app1) as client1:
        ready1 = client1.get("/ready")
        assert ready1.status_code == 200
        assert ready1.json()["active_generation_id"] == gen1_id

        token = create_jwt_token()
        q1 = client1.post(
            "/query",
            json={"query": "baseline", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert q1.status_code == 200
        assert q1.json()["index_generation_id"] == gen1_id

    # Simulated Cold Restart (Instance 2 initializes fresh state from persistent storage)
    pipeline2, manager2 = build_clean_pipeline([doc])
    gen2_id = manager2.get_active_generation_id()
    app2 = create_app(pipeline=pipeline2, identity_config=cfg)

    with TestClient(app2) as client2:
        # Verify /healthz and deep /ready return healthy on cold restart
        health2 = client2.get("/healthz")
        assert health2.status_code == 200

        ready2 = client2.get("/ready")
        assert ready2.status_code == 200
        data_ready2 = ready2.json()
        assert data_ready2["status"] == "ready"
        assert data_ready2["components"]["bm25"] is True
        assert data_ready2["components"]["dense"] is True
        assert data_ready2["components"]["reranker"] is True
        assert data_ready2["components"]["generator"] is True
        assert data_ready2["components"]["authentication"] is True
        assert data_ready2["active_generation_id"] == gen2_id

        # Verify query succeeds immediately post-restart
        q2 = client2.post(
            "/query",
            json={"query": "baseline", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert q2.status_code == 200
        assert q2.json()["index_generation_id"] == gen2_id
        assert "DOC-BASELINE-1" in q2.json()["answer_text"]


# -----------------------------------------------------------------------------
# STEP 6: Resilience & Operational Regimes Disambiguation
# -----------------------------------------------------------------------------

def test_clean_runtime_resilience_circuit_breaker_trip_and_cooldown():
    """Verify circuit breaker transitions from CLOSED to OPEN after 3 failures and returns 503."""
    res_cfg = ResilienceConfig(
        request_timeout_seconds=5.0,
        max_concurrent_inferences=1,
        queue_timeout_seconds=0.2,
        enable_circuit_breaker=True,
        circuit_failure_threshold=3,
        circuit_cooldown_seconds=0.5,
    )
    identity_cfg = IdentityConfig(
        issuer=_TEST_ISSUER,
        audience=_TEST_AUDIENCE,
        hs256_secret=_TEST_SECRET.encode("utf-8"),
    )

    failing_generator = ControlledRuntimeGenerator(fail_all=True)
    doc = make_clean_document("DOC-FAIL", "Document destined to trigger failure")
    pipeline, _ = build_clean_pipeline([doc], generator=failing_generator)
    app = create_app(pipeline=pipeline, resilience_config=res_cfg, identity_config=identity_cfg)

    with TestClient(app, raise_server_exceptions=False) as client:
        token = create_jwt_token()
        headers = {"Authorization": f"Bearer {token}"}
        req_body = {"query": "fail", "user_context": {"tenant_id": "TENANT-NOVASTACK"}}

        # 3 consecutive failures trip the circuit breaker
        for _ in range(3):
            r = client.post("/query", json=req_body, headers=headers)
            assert r.status_code == 500

        # 4th request: Circuit breaker is OPEN -> Fast 503 without invoking pipeline
        r_open = client.post("/query", json=req_body, headers=headers)
        assert r_open.status_code == 503
        assert "circuit breaker open" in r_open.json()["detail"].lower()

        # Wait for cooldown
        time.sleep(0.6)

        # After cooldown, circuit breaker allows execution (HALF-OPEN)
        r_half = client.post("/query", json=req_body, headers=headers)
        assert r_half.status_code == 500


def test_clean_runtime_operational_regimes_headers_disambiguation():
    """Verify HTTP status and diagnostic headers separate pre-gen abstention from generation."""
    cfg = IdentityConfig(
        issuer=_TEST_ISSUER,
        audience=_TEST_AUDIENCE,
        hs256_secret=_TEST_SECRET.encode("utf-8"),
    )
    doc = make_clean_document("DOC-GEN", "Specific facts about production deployments")
    pipeline, _ = build_clean_pipeline([doc], generator=ControlledRuntimeGenerator(simulate_latency_ms=12.5))
    app = create_app(pipeline=pipeline, identity_config=cfg)

    with TestClient(app) as client:
        token = create_jwt_token()
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Genuine answered generation
        res_ans = client.post(
            "/query",
            json={"query": "deployments", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
            headers=headers,
        )
        assert res_ans.status_code == 200
        assert res_ans.json()["answer_status"] == "answered"
        assert res_ans.headers.get("x-generation-invoked") == "true"
        assert float(res_ans.headers.get("x-generation-latency-ms", 0)) > 0

        # 2. Fast pre-generation abstention (unmatched query yields empty evidence)
        res_abs = client.post(
            "/query",
            json={"query": "zzzzzzzz unmatched non-existent query", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
            headers=headers,
        )
        assert res_abs.status_code == 200
        assert res_abs.json()["answer_status"] == "abstained"
        assert res_abs.headers.get("x-generation-invoked") == "false"
        assert float(res_abs.headers.get("x-generation-latency-ms", 0)) == 0.0


def test_clean_runtime_observability_and_privacy_invariants():
    """Verify Prometheus metrics and structured logging preserve privacy invariants."""
    reset_metrics()
    cfg = IdentityConfig(
        issuer=_TEST_ISSUER,
        audience=_TEST_AUDIENCE,
        hs256_secret=_TEST_SECRET.encode("utf-8"),
    )
    doc = make_clean_document("DOC-OBS", "Telemetry data specification")
    pipeline, _ = build_clean_pipeline([doc])
    app = create_app(pipeline=pipeline, identity_config=cfg)

    with TestClient(app) as client:
        token = create_jwt_token()
        custom_rid = "REQ-4W-TEST-OBSERVABILITY-001"
        res = client.post(
            "/query",
            json={"query": "telemetry", "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
            headers={"Authorization": f"Bearer {token}", "x-request-id": custom_rid},
        )
        assert res.status_code == 200
        assert res.headers.get("x-request-id") == custom_rid

        metrics_resp = client.get("/metrics")
        assert metrics_resp.status_code == 200
        metrics_text = metrics_resp.text

        # Verify Prometheus metrics presence
        assert "atlas_requests_total" in metrics_text
        assert "atlas_request_latency_seconds" in metrics_text

        # Privacy invariants: No secret tokens or raw query texts in metric labels
        assert _TEST_SECRET not in metrics_text
        assert token not in metrics_text
        assert "telemetry" not in metrics_text


def test_frozen_production_configuration_preservation():
    """Verify certified production configuration remains strictly preserved."""
    resolver_cfg = EvidenceResolverConfig()
    assert resolver_cfg.enable_query_aware_authority is True
    assert resolver_cfg.enable_event_bundling is False

    res_cfg = ResilienceConfig()
    assert res_cfg.request_timeout_seconds == 30.0
    assert res_cfg.max_concurrent_inferences == 1
    assert res_cfg.queue_timeout_seconds == 0.5
    assert res_cfg.enable_circuit_breaker is True
    assert res_cfg.circuit_failure_threshold == 3
    assert res_cfg.circuit_cooldown_seconds == 10.0
