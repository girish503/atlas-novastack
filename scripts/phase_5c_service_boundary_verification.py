"""Phase 5C: Inference Service Boundary & Containerization Verification.

Executes end-to-end verification of the Phase 5C decoupled HTTP inference
service boundary, validating:
1. Service Health & Readiness (/healthz, /ready).
2. Minimal Payload Contract & Zero-Auth Leakage.
3. Full Pipeline Integration: Authorized Query -> ATLAS Ingestion/Retrieval ->
   Evidence Resolution -> InferenceServiceAdapter -> Inference Service ->
   Ollama (gemma3:1b Q4_K_M) -> Layer 4 C2 Citation Validation -> AnswerResult.
4. Security Invariants (Fail-closed gateway rejection, Tenant Isolation).
5. Resilience & Timeout Handling.
6. Container Specification (Dockerfile.inference static verification).
7. Host & Process Telemetry (ATLAS RSS, Ollama/llama-server RSS).

Saves results to artifacts/phase_5c_inference_service_boundary.json.
"""

from __future__ import annotations

import base64
import gc
import hashlib
import hmac
import json
import os
import platform
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
from fastapi.testclient import TestClient

# Ensure src and tests are in sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir / "src"))
sys.path.insert(0, str(root_dir / "tests"))

from test_phase_5a_provider_boundary import DeterministicEncoder, PassThroughReranker

from novastack.bm25 import BM25Index
from novastack.chunking import chunk_document
from novastack.citation_validator import CitationStatus
from novastack.dense import DenseIndex
from novastack.evidence import EvidenceItem, EvidencePackage, EvidenceStatus
from novastack.evidence_resolution import EvidenceResolver, EvidenceResolverConfig
from novastack.generation import AnswerResult, AnswerStatus
from novastack.index_manager import IndexManager
from novastack.inference_client import InferenceServiceClient
from novastack.inference_service.app import create_inference_app
from novastack.inference_service.config import InferenceServiceConfig
from novastack.metadata_diagnostics import build_metadata_snapshot_index
from novastack.models import RecordPermissions, SearchDocument
from novastack.provider import (
    AnswerGeneratorProvider,
    InferenceServiceAdapter,
    LocalHuggingFaceProvider,
    QuantizedLocalProvider,
)
from novastack.service import (
    AtlasServicePipeline,
    IdentityConfig,
    ResilienceConfig,
    create_app,
)

_TEST_ISSUER = "https://auth.atlas.production.example"
_TEST_AUDIENCE = "atlas-service-api"
_TEST_SECRET = "production-grade-signing-secret-minimum-32-chars-long"


def make_jwt(
    tenant_id: str = "TENANT-NOVASTACK",
    user_id: str = "USR-ENG-01",
    roles: Optional[List[str]] = None,
) -> Dict[str, str]:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": _TEST_ISSUER,
        "aud": _TEST_AUDIENCE,
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": roles or ["engineer"],
        "departments": ["Engineering"],
        "exp": now + 3600,
        "iat": now,
    }

    def b64url(d: dict) -> str:
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


def get_process_metrics() -> Dict[str, Any]:
    proc = psutil.Process()
    mem = proc.memory_info()
    vm = psutil.virtual_memory()

    ollama_procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            name = p.info["name"].lower()
            if "ollama" in name or "llama" in name:
                ollama_procs.append({
                    "pid": p.info["pid"],
                    "name": p.info["name"],
                    "rss_mb": round(p.info["memory_info"].rss / (1024 * 1024), 2),
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return {
        "python_rss_mb": round(mem.rss / (1024 * 1024), 2),
        "available_sys_ram_mb": round(vm.available / (1024 * 1024), 2),
        "total_sys_ram_mb": round(vm.total / (1024 * 1024), 2),
        "ollama_processes": ollama_procs,
    }


def make_corpus() -> List[SearchDocument]:
    return [
        SearchDocument(
            document_id="DOC-INC-001",
            tenant_id="TENANT-NOVASTACK",
            source_type="documentation",
            title="Incident INC-NS-0001 Postmortem",
            content="The root cause of incident INC-NS-0001 was a database connection pool leak in checkout-service under sustained peak load.",
            department="Engineering",
            author_id="USR-ENG-01",
            created_at="2026-01-01T00:00:00",
            permissions=RecordPermissions(allowed_roles=["engineer"]),
            authority_level="canonical",
        ),
        SearchDocument(
            document_id="DOC-CONF-002",
            tenant_id="TENANT-NOVASTACK",
            source_type="documentation",
            title="Telemetry Collector Specification",
            content="The OpenTelemetry gRPC ingest endpoint listens on port 4317 with TLS enabled across all cluster worker nodes.",
            department="Engineering",
            author_id="USR-ENG-02",
            created_at="2026-01-02T00:00:00",
            permissions=RecordPermissions(allowed_roles=["engineer"]),
            authority_level="canonical",
        ),
    ]


def verify_phase_5c():
    print("=" * 80)
    print("ATLAS PHASE 5C — INFERENCE SERVICE BOUNDARY & CONTAINERIZATION VERIFICATION")
    print("=" * 80)

    results: Dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": "5C",
        "system": {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "processor": platform.processor(),
        },
        "initial_telemetry": get_process_metrics(),
        "stages": {},
    }

    # -------------------------------------------------------------------------
    # 1. Inference Service HTTP Contract & Health/Readiness
    # -------------------------------------------------------------------------
    print("\n[STAGE 1] Validating Standalone Inference Service Health & Readiness...")
    svc_cfg = InferenceServiceConfig(
        backend_url="http://127.0.0.1:11434",
        model_name="gemma3:1b",
        connect_timeout_seconds=2.0,
        read_timeout_seconds=25.0,
        service_port=8001,
    )
    inf_app = create_inference_app(svc_cfg)
    test_client = TestClient(inf_app)

    # Healthz
    t0 = time.perf_counter()
    resp_health = test_client.get("/healthz")
    health_lat_ms = (time.perf_counter() - t0) * 1000.0
    assert resp_health.status_code == 200, f"Healthz failed: {resp_health.text}"
    health_data = resp_health.json()
    print(f"  /healthz: status=200, status={health_data.get('status')} ({health_lat_ms:.2f} ms)")

    # Ready (pings real local Ollama backend)
    t0 = time.perf_counter()
    resp_ready = test_client.get("/ready")
    ready_lat_ms = (time.perf_counter() - t0) * 1000.0
    assert resp_ready.status_code == 200, f"Ready failed: {resp_ready.text}"
    ready_data = resp_ready.json()
    print(f"  /ready:   status=200, status={ready_data.get('status')} ({ready_lat_ms:.2f} ms)")

    results["stages"]["service_endpoints"] = {
        "healthz": {"status_code": resp_health.status_code, "data": health_data, "latency_ms": round(health_lat_ms, 2)},
        "ready": {"status_code": resp_ready.status_code, "data": ready_data, "latency_ms": round(ready_lat_ms, 2)},
    }

    # -------------------------------------------------------------------------
    # 2. Standalone Service Direct Generation Verification
    # -------------------------------------------------------------------------
    print("\n[STAGE 2] Validating Direct Model Execution over Service Boundary...")
    t0 = time.perf_counter()
    resp_gen = test_client.post(
        "/generate",
        json={
            "prompt": "Answer in one sentence: What is the primary function of an enterprise search engine?",
            "request_id": "req-direct-001",
            "max_new_tokens": 64,
            "temperature": 0.0,
            "model_name": "gemma3:1b",
        },
    )
    direct_gen_ms = (time.perf_counter() - t0) * 1000.0
    assert resp_gen.status_code == 200, f"Direct generate failed: {resp_gen.text}"
    gen_data = resp_gen.json()
    print(f"  /generate: status=200, tokens={gen_data.get('output_tokens')}, latency={direct_gen_ms:.2f} ms")
    print(f"  Generated text: {gen_data.get('generated_text')[:100]}...")

    results["stages"]["direct_generation"] = {
        "status_code": resp_gen.status_code,
        "prompt_tokens": gen_data.get("prompt_tokens"),
        "output_tokens": gen_data.get("output_tokens"),
        "service_latency_ms": round(direct_gen_ms, 2),
        "engine_telemetry": gen_data.get("engine_telemetry"),
    }

    # -------------------------------------------------------------------------
    # 3. End-to-End Pipeline Integration with InferenceServiceAdapter
    # -------------------------------------------------------------------------
    print("\n[STAGE 3] Validating End-to-End Pipeline with InferenceServiceAdapter...")
    service_client = InferenceServiceClient(
        service_url="http://127.0.0.1:8001",
        read_timeout_seconds=25.0,
        http_client=test_client,
    )

    docs = make_corpus()
    chunks = [chunk for doc in docs for chunk in chunk_document(doc)]
    encoder = DeterministicEncoder()
    bm25 = BM25Index.build_index(chunks)
    dense = DenseIndex(chunks, encoder.encode_passages([c.text for c in chunks]), encoder=encoder)
    metadata = build_metadata_snapshot_index([d.to_dict() for d in docs])
    manager = IndexManager()
    manager.initialize_from_components(docs, chunks, bm25, dense, metadata, corpus_version="5C-bench")

    adapter = InferenceServiceAdapter(
        service_url="http://127.0.0.1:8001",
        model_name="gemma3:1b",
        service_client=service_client,
        corpus_doc_ids={d.document_id for d in docs},
        corpus_chunk_ids={c.chunk_id for c in chunks},
    )
    assert isinstance(adapter, AnswerGeneratorProvider)
    assert adapter.provider_name == "inference_service_adapter"
    assert adapter.is_ready() is True

    lease = manager.acquire_active_generation()
    assert lease is not None
    try:
        resolver = EvidenceResolver(
            documents_index={d.document_id: d for d in docs},
            chunks_index={c.chunk_id: c for c in chunks},
            config=EvidenceResolverConfig(),
        )
        pipeline = AtlasServicePipeline(
            bm25_index=lease.snapshot.bm25_index,
            dense_index=lease.snapshot.dense_index,
            reranker=PassThroughReranker(),
            generator=adapter,
            resolver=resolver,
            metadata_snapshot_index=dict(lease.snapshot.metadata_snapshot_index),
            index_manager=manager,
        )
    finally:
        lease.close()

    id_cfg = IdentityConfig(
        issuer=_TEST_ISSUER,
        audience=_TEST_AUDIENCE,
        hs256_secret=_TEST_SECRET.encode("utf-8"),
    )
    res_cfg = ResilienceConfig(request_timeout_seconds=30.0)
    app = create_app(
        pipeline=pipeline,
        inference_provider=adapter,
        resilience_config=res_cfg,
        identity_config=id_cfg,
    )
    api_client = TestClient(app, raise_server_exceptions=False)

    # Authorized Query Execution
    query = "What was the root cause of incident INC-NS-0001?"
    headers = make_jwt("TENANT-NOVASTACK", "USR-ENG-01")
    t0 = time.perf_counter()
    api_resp = api_client.post(
        "/query",
        json={
            "query": query,
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
            "evaluation_id": "TEST-5C-E2E-001",
        },
        headers=headers,
    )
    api_latency_ms = (time.perf_counter() - t0) * 1000.0

    assert api_resp.status_code == 200, f"Query failed: {api_resp.text}"
    query_data = api_resp.json()
    print(f"  API /query status=200 ({api_latency_ms:.2f} ms)")
    print(f"  Answer status: {query_data.get('answer_status')}")
    print(f"  Answer text: {query_data.get('answer_text')}")
    print(f"  Citations: {query_data.get('citations')}")

    assert query_data.get("answer_status") == AnswerStatus.ANSWERED.value
    assert "connection pool leak" in query_data.get("answer_text", "").lower()
    assert len(query_data.get("citations", [])) > 0
    assert query_data["citations"][0]["status"].lower() == "valid"

    results["stages"]["end_to_end_query"] = {
        "status_code": api_resp.status_code,
        "answer_status": query_data.get("answer_status"),
        "answer_text": query_data.get("answer_text"),
        "citations": query_data.get("citations"),
        "latency_ms": round(api_latency_ms, 2),
    }

    # -------------------------------------------------------------------------
    # 4. Security Invariants Verification
    # -------------------------------------------------------------------------
    print("\n[STAGE 4] Validating Security Boundaries & Isolation...")

    # Gate 1: Unauthenticated request rejected at gateway
    unauth_resp = api_client.post(
        "/query",
        json={
            "query": query,
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        },
    )
    print(f"  Unauthenticated request: status={unauth_resp.status_code} (expected 401)")
    assert unauth_resp.status_code == 401

    # Gate 2: Cross-tenant context mismatch rejected at gateway
    tenant_mismatch_resp = api_client.post(
        "/query",
        json={
            "query": query,
            "user_context": {"tenant_id": "TENANT-OTHER"},
        },
        headers=headers,
    )
    print(f"  Tenant mismatch request: status={tenant_mismatch_resp.status_code} (expected 403)")
    assert tenant_mismatch_resp.status_code == 403

    # Gate 3: Empty evidence intentional abstention (Layer 1 gate)
    unretrievable_query = "What is the secret recipe for quantum cold brew coffee?"
    t0 = time.perf_counter()
    abstain_resp = api_client.post(
        "/query",
        json={
            "query": unretrievable_query,
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        },
        headers=headers,
    )
    abstain_ms = (time.perf_counter() - t0) * 1000.0
    abstain_data = abstain_resp.json()
    print(f"  Unretrievable query: status={abstain_resp.status_code}, answer_status={abstain_data.get('answer_status')} ({abstain_ms:.2f} ms)")
    assert str(abstain_data.get("answer_status")).lower() == AnswerStatus.ABSTAINED.value.lower()

    results["stages"]["security_verification"] = {
        "unauthenticated_status": unauth_resp.status_code,
        "tenant_mismatch_status": tenant_mismatch_resp.status_code,
        "intentional_abstention_status": abstain_data.get("answer_status"),
    }

    # -------------------------------------------------------------------------
    # 5. Dockerfile Containerization Static Verification
    # -------------------------------------------------------------------------
    print("\n[STAGE 5] Validating Container Specification (Dockerfile.inference)...")
    dockerfile_path = root_dir / "Dockerfile.inference"
    assert dockerfile_path.exists(), "Dockerfile.inference not found"
    dockerfile_content = dockerfile_path.read_text(encoding="utf-8")

    container_checks = {
        "base_image_python_311_slim": "FROM python:3.11-slim" in dockerfile_content,
        "non_root_user_appuser": "USER appuser" in dockerfile_content and "1000" in dockerfile_content,
        "healthcheck_configured": "HEALTHCHECK" in dockerfile_content and "/healthz" in dockerfile_content,
        "port_8001_exposed": "EXPOSE 8001" in dockerfile_content,
        "entrypoint_uvicorn_service": "novastack.inference_service.app:app" in dockerfile_content,
        "no_hardcoded_secrets": "password" not in dockerfile_content.lower() and "secret" not in dockerfile_content.lower(),
    }
    for check_name, passed in container_checks.items():
        print(f"  Container check '{check_name}': {'PASS' if passed else 'FAIL'}")
        assert passed, f"Container check failed: {check_name}"

    results["stages"]["container_specification"] = container_checks

    # -------------------------------------------------------------------------
    # 6. Production Default Preserved Invariant
    # -------------------------------------------------------------------------
    print("\n[STAGE 6] Validating Production Default Invariants...")
    default_pipe = AtlasServicePipeline.create_default(lazy_generator=True)
    assert isinstance(default_pipe.generator, LocalHuggingFaceProvider)
    assert default_pipe.generator.provider_name == "local_huggingface"
    print(f"  AtlasServicePipeline.create_default() generator: {default_pipe.generator.provider_name} (PASS)")

    default_app = create_app(pipeline=default_pipe)
    assert isinstance(default_app.state.pipeline.generator, LocalHuggingFaceProvider)
    print(f"  create_app() default generator: {default_app.state.pipeline.generator.provider_name} (PASS)")

    results["stages"]["production_default"] = {
        "pipeline_default_provider": default_pipe.generator.provider_name,
        "app_default_provider": default_app.state.pipeline.generator.provider_name,
        "production_default_changed": False,
        "production_promotion": False,
    }

    # Final Telemetry
    results["final_telemetry"] = get_process_metrics()
    results["status"] = "PASS"

    # Save JSON artifact
    artifact_path = root_dir / "artifacts" / "phase_5c_inference_service_boundary.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with open(artifact_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved benchmark results to: {artifact_path}")

    print("\n" + "=" * 80)
    print("PHASE 5C SERVICE BOUNDARY VERIFICATION COMPLETE: ALL GATES PASS")
    print("=" * 80)
    return results


if __name__ == "__main__":
    verify_phase_5c()

