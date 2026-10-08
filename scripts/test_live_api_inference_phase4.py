"""Phase 4 verification script: API -> Inference -> Ollama end-to-end live test."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from pathlib import Path

# Setup paths
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient
from novastack.provider import InferenceServiceAdapter
from novastack.service.api import AtlasServicePipeline, create_app
from novastack.service.identity import IdentityConfig
from novastack.service.schemas import CallerContext, QueryRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("phase4_verification")

ISSUER = "https://identity.atlas.example/issuer"
AUDIENCE = "atlas-query-api"
SECRET = "phase-4-verification-secret-32bytes-long-ok"


def _b64url(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_jwt(tenant_id: str = "TENANT-NOVASTACK") -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "USR-PHASE4-VERIFIER",
        "tenant_id": tenant_id,
        "roles": ["engineer"],
        "departments": ["Engineering"],
        "role": "engineer",
        "department": "Engineering",
        "iat": now,
        "exp": now + 600,
    }
    signed = f"{_b64url(header)}.{_b64url(payload)}"
    sig = hmac.new(SECRET.encode("utf-8"), signed.encode("ascii"), hashlib.sha256).digest()
    return f"{signed}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode('ascii')}"


def run_phase_4_verification():
    logger.info("Initializing Phase 4 Verification...")

    # 1. Setup Identity Config
    id_cfg = IdentityConfig(
        issuer=ISSUER,
        audience=AUDIENCE,
        hs256_secret=SECRET.encode("utf-8"),
        clock_skew_seconds=0,
    )

    # 2. Setup Inference Provider pointing to live container at 127.0.0.1:8001
    service_url = os.environ.get("ATLAS_INFERENCE_SERVICE_URL", "http://127.0.0.1:8001")
    logger.info(f"Using inference service URL: {service_url}")

    # Load corpus document IDs for citation verification
    proc_dir = ROOT / "data" / "processed" / "novastack"
    docs_list = json.loads((proc_dir / "search_documents.json").read_text(encoding="utf-8"))["search_documents"]
    chunks_list = json.loads((proc_dir / "search_chunks.json").read_text(encoding="utf-8"))["search_chunks"]
    doc_ids = {d["document_id"] for d in docs_list}
    chunk_ids = {c["chunk_id"] for c in chunks_list}

    provider = InferenceServiceAdapter(
        service_url=service_url,
        corpus_doc_ids=doc_ids,
        corpus_chunk_ids=chunk_ids,
    )

    # Verify provider health/readiness first
    is_ready = provider.is_ready()
    logger.info(f"Provider is_ready: {is_ready}")
    assert is_ready, f"Provider at {service_url} is not ready"

    # 3. Build Full Pipeline
    logger.info("Building AtlasServicePipeline...")
    pipeline = AtlasServicePipeline.create_default(lazy_generator=False, generator=provider)

    # Warm up dense index model
    logger.info("Warming up dense index...")
    _ = pipeline.dense_index.search("warmup query", top_k=1)

    # 4. Create App
    logger.info("Creating FastAPI App...")
    from novastack.service.resilience import ResilienceConfig
    res_cfg = ResilienceConfig(request_timeout_seconds=45.0)
    app = create_app(
        pipeline=pipeline,
        resilience_config=res_cfg,
        identity_config=id_cfg,
        inference_provider=provider,
    )

    # 5. Issue Authenticated Query Request via TestClient
    client = TestClient(app)

    # First test /ready
    ready_resp = client.get("/ready")
    logger.info(f"/ready status code: {ready_resp.status_code}, body: {ready_resp.json()}")
    assert ready_resp.status_code == 200, f"/ready returned {ready_resp.status_code}"

    # Test authenticated query
    token = make_jwt()
    query_payload = {
        "query": "What was the root cause and resolution of incident INC-NS-0001?",
        "user_context": {
            "tenant_id": "TENANT-NOVASTACK",
            "roles": ["engineer"],
            "departments": ["Engineering"],
        },
        "evaluation_id": "EVAL-0001",
    }

    logger.info("Sending authenticated query to /query...")
    t0 = time.perf_counter()
    resp = client.post(
        "/query",
        headers={"Authorization": f"Bearer {token}"},
        json=query_payload,
    )
    t1 = time.perf_counter()
    latency_ms = (t1 - t0) * 1000

    logger.info(f"Query completed in {latency_ms:.2f} ms")
    logger.info(f"HTTP Status: {resp.status_code}")
    logger.info(f"Response: {resp.text}")

    result_data = {
        "status_code": resp.status_code,
        "latency_ms": latency_ms,
        "response": resp.json() if resp.status_code == 200 else resp.text,
        "provider_readiness": is_ready,
    }

    out_file = ROOT / "artifacts" / "phase_4_api_inference_verification.json"
    out_file.write_text(json.dumps(result_data, indent=2), encoding="utf-8")
    logger.info(f"Results written to {out_file}")

    assert resp.status_code == 200, f"Expected HTTP 200, got {resp.status_code}"
    body = resp.json()
    assert body.get("answer_status") in ("answered", "abstained")
    logger.info(f"PHASE 4 SUCCESS! Answer status: {body.get('answer_status')}, Answer text: {body.get('answer_text')}")


if __name__ == "__main__":
    run_phase_4_verification()
