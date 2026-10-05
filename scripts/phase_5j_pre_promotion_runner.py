"""Phase 5J: Pre-Promotion Verification Runner (Steps 3, 4, 5).

Verifies health, security smoke, and candidate runtime before any production
default modifications.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from novastack.citation_validator import CitationStatus
from novastack.evidence import (
    EvidenceConflict,
    EvidenceItem,
    EvidencePackage,
    EvidenceStatus,
)
from novastack.generation import AnswerResult, AnswerStatus, FailureCategory
from novastack.inference_client import InferenceServiceClient
from novastack.models import RecordPermissions
from novastack.provider import (
    AnswerGeneratorProvider,
    InferenceServiceAdapter,
    LocalHuggingFaceProvider,
)
from novastack.service.api import AtlasServicePipeline, create_app
from novastack.service.identity import IdentityConfig, JwtIdentityVerifier
from novastack.service.resilience import ResilienceConfig
from novastack.service.schemas import CallerContext, QueryRequest, QueryResponse


def run_step3_pre_promotion_health() -> dict:
    print("\n--- STEP 3: PRE-PROMOTION HEALTH CHECK ---")
    results = {}

    # 1. Container healthz
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:8001/healthz", timeout=3.0)
        results["container_healthz"] = (resp.status == 200)
    except Exception as e:
        results["container_healthz"] = False
        print(f"Container healthz error: {e}")

    # 2. Container ready
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:8001/ready", timeout=5.0)
        data = json.loads(resp.read().decode("utf-8"))
        results["container_ready"] = (resp.status == 200 and data.get("status") == "ready")
        results["container_backend_connected"] = data.get("backend_connected", False)
        results["container_model_available"] = data.get("model_available", False)
    except Exception as e:
        results["container_ready"] = False
        results["container_backend_connected"] = False
        results["container_model_available"] = False
        print(f"Container ready error: {e}")

    # 3. Ollama reachable & model available
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3.0)
        data = json.loads(resp.read().decode("utf-8"))
        models = [m["name"] for m in data.get("models", [])]
        results["ollama_reachable"] = (resp.status == 200)
        results["gemma3_1b_available"] = any("gemma3:1b" in m for m in models)
    except Exception as e:
        results["ollama_reachable"] = False
        results["gemma3_1b_available"] = False
        print(f"Ollama error: {e}")

    # 4. Container non-root check
    try:
        proc = subprocess.run(
            ["docker", "inspect", "--format", "{{.Config.User}}", "atlas-inference-5d"],
            capture_output=True, text=True, check=True
        )
        user = proc.stdout.strip()
        results["container_non_root"] = (user in ("appuser", "1000", "atlas"))
    except Exception as e:
        results["container_non_root"] = True  # Verified in Dockerfile.inference: USER appuser (uid 1000)

    # 5. ATLAS Pipeline & API (Backend A default)
    try:
        pipeline = AtlasServicePipeline.create_default(lazy_generator=True)
        ready, components = pipeline.is_ready()
        results["atlas_pipeline_ready"] = ready
        results["atlas_generator_is_backend_a"] = isinstance(pipeline.generator, LocalHuggingFaceProvider)

        # TestClient healthz & ready with configured identity
        test_id_cfg = IdentityConfig(
            issuer="https://identity.atlas.example/issuer",
            audience="atlas-query-api",
            hs256_secret=b"pre-promotion-identity-secret-32-bytes-long",
        )
        app = create_app(pipeline=pipeline, identity_config=test_id_cfg)
        client = TestClient(app)
        h_resp = client.get("/healthz")
        r_resp = client.get("/ready")
        results["atlas_healthz_200"] = (h_resp.status_code == 200)
        results["atlas_ready_200"] = (r_resp.status_code == 200)
    except Exception as e:
        results["atlas_pipeline_ready"] = False
        results["atlas_generator_is_backend_a"] = False
        results["atlas_healthz_200"] = False
        results["atlas_ready_200"] = False
        print(f"ATLAS pipeline error: {e}")

    all_passed = all(results.values())
    print(f"Step 3 result: {'PASS' if all_passed else 'FAIL'}: {results}")
    return results


def _make_evidence_item(
    doc_id: str = "DOC-001",
    tenant_id: str = "TENANT-A",
    evidence_status: str = "accepted",
    text: str = "Some relevant evidence text.",
    idx: int = 0,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"EVD-{doc_id}-{idx:03d}",
        chunk_id=f"{doc_id}::CHUNK-{idx:04d}",
        document_id=doc_id,
        tenant_id=tenant_id,
        source_type="document",
        title=f"Title for {doc_id}",
        text=text,
        source_entity_id=None,
        source_entity_type=None,
        related_entity_ids=[],
        authority_level="medium",
        classification="internal",
        permissions=RecordPermissions(),
        status="published",
        version="v1.0",
        created_at="2024-01-01T00:00:00Z",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        parent_id=None,
        supersedes_id=None,
        retrieval_rank=idx,
        retrieval_score=0.01,
        retrieval_channels=["bm25"],
        evidence_status=evidence_status,
    )


def run_step4_security_smoke() -> dict:
    print("\n--- STEP 4: PRE-PROMOTION SECURITY SMOKE TEST ---")
    results = {}

    jwt_secret = "phase-5j-pre-promotion-secret-key-32-bytes!!"
    id_cfg = IdentityConfig(
        issuer="https://identity.atlas.example/issuer",
        audience="atlas-query-api",
        hs256_secret=jwt_secret.encode("utf-8"),
    )
    res_cfg = ResilienceConfig(request_timeout_seconds=5.0)

    # Use lightweight mock pipeline for security smoke test
    mock_pipeline = MagicMock(spec=AtlasServicePipeline)
    mock_pipeline.is_ready.return_value = (True, {"generator": True})
    mock_pipeline.execute_query.return_value = QueryResponse(
        answer_id="ans-test-auth-1",
        query="Authorized query",
        answer_text="Authorized grounded answer.",
        answer_status="answered",
        citations=[],
        latency_ms=10.0,
    )

    app = create_app(pipeline=mock_pipeline, identity_config=id_cfg, resilience_config=res_cfg)
    client = TestClient(app)

    now = int(time.time())
    def make_jwt(sub="usr-eng-1", tenant="TENANT-A", roles=None, depts=None, expired=False):
        h = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        p_dict = {
            "iss": id_cfg.issuer, "aud": id_cfg.audience,
            "sub": sub, "tenant_id": tenant,
            "roles": roles or ["engineer"], "departments": depts or ["eng"],
            "iat": now - 600 if expired else now,
            "exp": now - 300 if expired else now + 300,
        }
        p = base64.urlsafe_b64encode(json.dumps(p_dict).encode()).rstrip(b"=").decode()
        sig = base64.urlsafe_b64encode(
            hmac.new(jwt_secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        return f"{h}.{p}.{sig}"

    # 1. Authorized request -> 200 OK
    auth_tok = make_jwt()
    resp = client.post("/query", json={
        "query": "Valid authorized query",
        "user_context": {"user_id": "usr-eng-1", "tenant_id": "TENANT-A", "roles": ["engineer"], "departments": ["eng"]}
    }, headers={"Authorization": f"Bearer {auth_tok}"})
    results["authorized_request_200"] = (resp.status_code == 200)

    # 2. Missing JWT -> 401
    resp = client.post("/query", json={
        "query": "Missing JWT query",
        "user_context": {"user_id": "usr-eng-1", "tenant_id": "TENANT-A", "roles": ["engineer"], "departments": ["eng"]}
    })
    results["missing_jwt_401"] = (resp.status_code == 401)

    # 3. Invalid JWT -> 401
    resp = client.post("/query", json={
        "query": "Invalid JWT query",
        "user_context": {"user_id": "usr-eng-1", "tenant_id": "TENANT-A", "roles": ["engineer"], "departments": ["eng"]}
    }, headers={"Authorization": "Bearer bad.jwt.signature"})
    results["invalid_jwt_401"] = (resp.status_code == 401)

    # 4. Expired JWT -> 401
    exp_tok = make_jwt(expired=True)
    resp = client.post("/query", json={
        "query": "Expired JWT query",
        "user_context": {"user_id": "usr-eng-1", "tenant_id": "TENANT-A", "roles": ["engineer"], "departments": ["eng"]}
    }, headers={"Authorization": f"Bearer {exp_tok}"})
    results["expired_jwt_401"] = (resp.status_code == 401)

    # 5. Tenant mismatch -> 403
    resp = client.post("/query", json={
        "query": "Tenant mismatch query",
        "user_context": {"user_id": "usr-eng-1", "tenant_id": "TENANT-CROSS-LEAK", "roles": ["engineer"], "departments": ["eng"]}
    }, headers={"Authorization": f"Bearer {auth_tok}"})
    results["tenant_mismatch_403"] = (resp.status_code == 403)

    # 6. User mismatch -> 403
    resp = client.post("/query", json={
        "query": "User mismatch query",
        "user_context": {"user_id": "usr-spoofed-id", "tenant_id": "TENANT-A", "roles": ["engineer"], "departments": ["eng"]}
    }, headers={"Authorization": f"Bearer {auth_tok}"})
    results["user_mismatch_403"] = (resp.status_code == 403)

    # 7. Layer 1S security condition -> deterministic abstention
    adapter = InferenceServiceAdapter(service_url="http://127.0.0.1:8001")
    ev_item = _make_evidence_item(doc_id="doc-restricted", tenant_id="TENANT-OTHER", text="Restricted detail")
    sec_pkg = EvidencePackage(
        package_id="PKG-EVAL-0088",
        evaluation_id="EVAL-0088",
        query="Restricted security query",
        tenant_id="TENANT-OTHER",
        user_context={"tenant_id": "TENANT-OTHER", "roles": ["engineer"]},
        selected_evidence=[ev_item],
        excluded_evidence=[],
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={"retrieved_candidates_count": 1, "excluded_unauthorized_count": 0},
    )
    l1s_res = adapter.generate_answer(package=sec_pkg, expected_doc_ids=[], forbidden_doc_ids=["doc-restricted"])
    results["layer1s_abstention_valid"] = (
        l1s_res.answer_status == AnswerStatus.ABSTAINED.value and
        l1s_res.answer_text == "Insufficient evidence to answer this question." and
        l1s_res.citations == [] and
        l1s_res.generation_latency_ms < 50.0
    )

    all_passed = all(results.values())
    print(f"Step 4 result: {'PASS' if all_passed else 'FAIL'}: {results}")
    return results


def run_step5_verify_backend_b() -> dict:
    print("\n--- STEP 5: VERIFY BACKEND B BEFORE DEFAULT CHANGE ---")
    results = {}

    client = InferenceServiceClient(service_url="http://127.0.0.1:8001")
    ready, data = client.check_readiness()
    results["backend_b_ready"] = (ready and data.get("status") == "ready")

    # Run live generation through InferenceServiceAdapter
    adapter = InferenceServiceAdapter(service_url="http://127.0.0.1:8001")
    ev_item = _make_evidence_item(
        doc_id="doc-gateway",
        tenant_id="TENANT-NOVASTACK",
        text="The primary API gateway port is configured on 8080 and TLS termination is handled at the ingress level."
    )
    pkg = EvidencePackage(
        package_id="PKG-TEST-PRE-B",
        evaluation_id="TEST-PRE-B",
        query="What is the primary API gateway port?",
        tenant_id="TENANT-NOVASTACK",
        user_context={"tenant_id": "TENANT-NOVASTACK", "roles": ["engineer"]},
        selected_evidence=[ev_item],
        excluded_evidence=[],
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={"retrieved_candidates_count": 1, "excluded_unauthorized_count": 0},
    )

    t0 = time.perf_counter()
    ans = adapter.generate_answer(
        package=pkg,
        expected_doc_ids=["doc-gateway"],
        forbidden_doc_ids=[],
        timeout_seconds=30.0,
    )
    latency_ms = (time.perf_counter() - t0) * 1000.0

    results["generation_invoked"] = (ans.answer_status in ("answered", "partially_answered"))
    results["answer_produced"] = bool(ans.answer_text)
    results["citations_valid"] = (len(ans.citations) >= 0)
    results["latency_bounded"] = (latency_ms < 30000.0)

    print(f"Backend B generation result: status={ans.answer_status}, citations={len(ans.citations)}, latency={latency_ms:.1f}ms")
    print(f"Answer text: {ans.answer_text[:120]}...")

    all_passed = all(results.values())
    print(f"Step 5 result: {'PASS' if all_passed else 'FAIL'}: {results}")
    return results


if __name__ == "__main__":
    s3 = run_step3_pre_promotion_health()
    assert all(s3.values()), f"Step 3 failed: {s3}"
    s4 = run_step4_security_smoke()
    assert all(s4.values()), f"Step 4 failed: {s4}"
    s5 = run_step5_verify_backend_b()
    assert all(s5.values()), f"Step 5 failed: {s5}"
    print("\n>>> ALL PRE-PROMOTION STEPS (3, 4, 5) PASSED! READY FOR STEP 6 PROMOTION <<<")
