"""Phase 4X-R1: Controlled Performance Revalidation.

PURPOSE
-------
Resolve measurement ambiguity from Phase 4X by capturing per-request
timestamps at each stage of the executor/generation lifecycle:

  T0  = request sent to HTTP client
  T4  = executor submitted (immediately before HTTP call)
  T5  = generation worker entered (thread start, inside generator)
  T6  = actual model.generate() call started  (NEW measurement probe)
  T7  = actual model.generate() returned      (NEW measurement probe)
  T10 = HTTP response received by client

Critical question answered:
  "Did model.generate() actually start before the 30s service timeout?"

CONSTRAINTS
-----------
- Frozen production configuration: unchanged
- No optimization, no model change, no quantization
- No concurrency ladder (already measured in 4X)
- Minimal measurement-only probes only
- 3 sequential genuine generation requests (C=1)
- 3 retrieval-only control measurements
- 2 abstention control measurements
- Security sanity check
"""

from __future__ import annotations

import gc
import json
import logging
import platform
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
import torch

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "src"))

from novastack.generation import GroundedAnswerGenerator
from novastack.service.api import (
    AtlasServicePipeline,
    create_app,
    fuse_hybrid_and_structured,
    fuse_rrf_sum,
)
from novastack.service.identity import IdentityConfig
from novastack.service.resilience import ResilienceConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("phase_4x_r1")

_TEST_ISSUER = "https://auth.atlas.production.example"
_TEST_AUDIENCE = "atlas-service-api"
_TEST_SECRET = "production-grade-signing-secret-minimum-32-chars-long"


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def get_env_snapshot(label: str) -> Dict[str, Any]:
    proc = psutil.Process()
    vm = psutil.virtual_memory()
    return {
        "label": label,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rss_mb": round(proc.memory_info().rss / (1024 ** 2), 2),
        "available_ram_gb": round(vm.available / (1024 ** 3), 2),
        "total_ram_gb": round(vm.total / (1024 ** 3), 2),
        "used_ram_gb": round((vm.total - vm.available) / (1024 ** 3), 2),
        "thread_count": threading.active_count(),
        "pytorch_threads": torch.get_num_threads(),
    }


def get_system_specs() -> Dict[str, Any]:
    vm = psutil.virtual_memory()
    avail_gb = round(vm.available / (1024 ** 3), 2)
    return {
        "os": platform.platform(),
        "python_version": platform.python_version(),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "total_ram_gb": round(vm.total / (1024 ** 3), 2),
        "available_ram_gb": avail_gb,
        "target_available_ram_gb": 3.0,
        "target_not_met": avail_gb < 3.0,
        "torch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "pytorch_thread_count": torch.get_num_threads(),
        "initial_rss_mb": round(psutil.Process().memory_info().rss / (1024 ** 2), 2),
        "initial_thread_count": threading.active_count(),
    }


def create_jwt_token(
    tenant_id: str = "TENANT-NOVASTACK",
    user_id: str = "USR-OPERATOR",
    user_role: str = "engineer",
    user_department: str = "Engineering",
    expires_in_seconds: int = 3600,
) -> str:
    import base64, hmac, hashlib

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
    return (
        f"{signed_content}."
        f"{base64.urlsafe_b64encode(sig).rstrip(b'=').decode('ascii')}"
    )


# ---------------------------------------------------------------------------
# Measurement-only probe
#
# Wraps generator.generate_answer to inject T5/T6/T7 timestamps into a
# caller-supplied timeline dict.  The original method is preserved and
# called with identical arguments.  No production behaviour is changed.
# ---------------------------------------------------------------------------

def install_generation_probe(
    generator: GroundedAnswerGenerator, timeline: Dict[str, Any]
) -> Any:
    """Install measurement probe; returns original method for restoration."""
    original_generate = generator.generate_answer

    def probed_generate_answer(package, **kwargs):
        # T5: worker thread entered the generate_answer function
        timeline["T5_worker_entered_s"] = time.perf_counter()
        timeline["T5_worker_entered_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Wrap model.generate() to capture T6/T7
        original_model_generate = None
        if generator.model is not None:
            original_model_generate = generator.model.generate

            def probed_model_generate(*args, **mkwargs):
                timeline["T6_model_call_started_s"] = time.perf_counter()
                timeline["T6_model_call_started_utc"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                )
                result = original_model_generate(*args, **mkwargs)
                timeline["T7_model_call_returned_s"] = time.perf_counter()
                timeline["T7_model_call_returned_utc"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                )
                return result

            generator.model.generate = probed_model_generate

        try:
            result = original_generate(package, **kwargs)
        finally:
            # Always restore original model.generate even on exception
            if original_model_generate is not None:
                generator.model.generate = original_model_generate

        timeline["T_worker_returned_s"] = time.perf_counter()
        diag = getattr(result, "diagnostics", {}) or {}
        timeline["worker_result_layer"] = diag.get("layer", "unknown")
        timeline["worker_result_answer_status"] = getattr(result, "answer_status", "unknown")
        timeline["worker_generation_latency_ms"] = getattr(result, "generation_latency_ms", 0.0)
        timeline["worker_inference_duration_ms"] = diag.get("inference_duration_ms", 0.0)

        if diag.get("layer") == "model_inference":
            timeline["generation_completed_in_worker"] = True
        elif diag.get("layer") in ("generation_timeout", "pre_generation_gate"):
            timeline["generation_timed_out_in_worker"] = True

        return result

    generator.generate_answer = probed_generate_answer
    return original_generate


# ---------------------------------------------------------------------------
# Data model for one generation measurement
# ---------------------------------------------------------------------------

@dataclass
class GenerationMeasurement:
    eval_id: str
    request_id: str
    query: str
    # HTTP-level
    http_status: int
    answer_status: str
    # Timeline event presence
    executor_submitted: bool = False
    worker_entered: bool = False
    model_call_started: bool = False
    model_call_returned: bool = False
    generation_completed_in_worker: bool = False
    generation_timed_out_in_worker: bool = False
    generation_timed_out_at_service: bool = False
    # Latencies (ms)
    total_latency_ms: float = 0.0
    deadline_ms: float = 30000.0
    deadline_headroom_ms: float = 0.0
    model_call_duration_ms: Optional[float] = None  # None = NOT_OBSERVED
    worker_total_duration_ms: Optional[float] = None
    generation_latency_ms_service: float = 0.0
    worker_inference_duration_ms: Optional[float] = None
    # Memory
    memory_before_mb: float = 0.0
    memory_after_mb: float = 0.0
    memory_delta_mb: float = 0.0
    available_ram_before_gb: float = 0.0
    available_ram_after_gb: float = 0.0
    # Thread counts
    threads_before: int = 0
    threads_after: int = 0
    # Index
    index_generation_id: Optional[str] = None
    # Worker diagnostics
    worker_diagnostics: Dict[str, Any] = field(default_factory=dict)
    # Full timeline (offsets from T0)
    timeline: Dict[str, Any] = field(default_factory=dict)
    # Timeline annotations
    timeline_annotations: Dict[str, str] = field(default_factory=dict)


def run_single_generation(
    client,
    app,
    pipeline: AtlasServicePipeline,
    headers: Dict[str, str],
    eval_id: str,
    query: str,
    deadline_s: float = 30.0,
) -> GenerationMeasurement:
    """Run one generation request with full T0-T10 timeline instrumentation."""
    rid = f"R1-{uuid.uuid4().hex[:8]}"
    tl: Dict[str, Any] = {}

    # Install probe; save original for restoration
    original_fn = install_generation_probe(pipeline.generator, tl)

    m = GenerationMeasurement(
        eval_id=eval_id,
        request_id=rid,
        query=query,
        http_status=0,
        answer_status="unknown",
        deadline_ms=deadline_s * 1000.0,
    )

    env_before = get_env_snapshot(f"{eval_id}_before")
    m.memory_before_mb = env_before["rss_mb"]
    m.available_ram_before_gb = env_before["available_ram_gb"]
    m.threads_before = env_before["thread_count"]

    T0 = time.perf_counter()
    tl["T0_request_sent_s"] = T0

    # T4: mark executor submitted immediately before HTTP call
    tl["T4_executor_submitted_s"] = time.perf_counter()
    m.executor_submitted = True

    resp = client.post(
        "/query",
        json={
            "query": query,
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
            "evaluation_id": eval_id,
            "request_id": rid,
        },
        headers=headers,
        timeout=45.0,
    )
    T10 = time.perf_counter()
    tl["T10_response_received_s"] = T10
    total_ms = (T10 - T0) * 1000.0

    m.http_status = resp.status_code
    body = resp.json()
    m.answer_status = body.get("answer_status", "unknown")
    m.generation_latency_ms_service = body.get("generation_latency_ms", 0.0)
    m.index_generation_id = body.get("index_generation_id")
    m.total_latency_ms = round(total_ms, 2)
    m.deadline_headroom_ms = round(deadline_s * 1000.0 - total_ms, 2)

    # Decode timeline events
    m.worker_entered = "T5_worker_entered_s" in tl
    m.model_call_started = "T6_model_call_started_s" in tl
    m.model_call_returned = "T7_model_call_returned_s" in tl
    m.generation_completed_in_worker = tl.get("generation_completed_in_worker", False)
    m.generation_timed_out_in_worker = tl.get("generation_timed_out_in_worker", False)
    m.generation_timed_out_at_service = resp.status_code == 504

    if m.model_call_started and m.model_call_returned:
        m.model_call_duration_ms = round(
            (tl["T7_model_call_returned_s"] - tl["T6_model_call_started_s"]) * 1000.0, 2
        )
    # If model_call_started but NOT model_call_returned: the asyncio.wait_for
    # fired and abandoned the future — model.generate() was running in the
    # worker thread but the HTTP response was already sent to the client.
    # This is the critical distinction: generation STARTED but SERVICE timed out.

    if "T5_worker_entered_s" in tl and "T_worker_returned_s" in tl:
        m.worker_total_duration_ms = round(
            (tl["T_worker_returned_s"] - tl["T5_worker_entered_s"]) * 1000.0, 2
        )

    m.worker_inference_duration_ms = tl.get("worker_inference_duration_ms")

    m.worker_diagnostics = {
        "layer": tl.get("worker_result_layer", "NOT_OBSERVED"),
        "answer_status": tl.get("worker_result_answer_status", "NOT_OBSERVED"),
        "generation_latency_ms": tl.get("worker_generation_latency_ms", "NOT_OBSERVED"),
        "inference_duration_ms": tl.get("worker_inference_duration_ms", "NOT_OBSERVED"),
    }

    # Build timeline display with ms offsets from T0
    timeline_display: Dict[str, Any] = {}
    for k, v in tl.items():
        if k.endswith("_s") and isinstance(v, float):
            offset_ms = round((v - T0) * 1000.0, 2)
            timeline_display[k.replace("_s", "_offset_ms")] = offset_ms
        else:
            timeline_display[k] = v

    m.timeline = timeline_display

    # Annotate each key event as OBSERVED or NOT_OBSERVED
    events = {
        "T0_request_sent": "T0_request_sent_s",
        "T4_executor_submitted": "T4_executor_submitted_s",
        "T5_worker_entered": "T5_worker_entered_s",
        "T6_model_call_started": "T6_model_call_started_s",
        "T7_model_call_returned": "T7_model_call_returned_s",
        "T_worker_returned": "T_worker_returned_s",
        "T10_response_received": "T10_response_received_s",
    }
    m.timeline_annotations = {
        name: ("OBSERVED" if key in tl else "NOT_OBSERVED")
        for name, key in events.items()
    }

    # Restore original generator
    pipeline.generator.generate_answer = original_fn

    env_after = get_env_snapshot(f"{eval_id}_after")
    m.memory_after_mb = env_after["rss_mb"]
    m.memory_delta_mb = round(m.memory_after_mb - m.memory_before_mb, 2)
    m.available_ram_after_gb = env_after["available_ram_gb"]
    m.threads_after = env_after["thread_count"]

    logger.info(
        "GEN %s | HTTP=%d | status=%s | total=%.2fms | headroom=%.2fms | "
        "worker_entered=%s | model_call_started=%s | model_call_returned=%s | "
        "gen_completed=%s | timed_out_in_worker=%s | timed_out_at_service=%s | "
        "model_call_ms=%s",
        eval_id,
        m.http_status,
        m.answer_status,
        m.total_latency_ms,
        m.deadline_headroom_ms,
        m.worker_entered,
        m.model_call_started,
        m.model_call_returned,
        m.generation_completed_in_worker,
        m.generation_timed_out_in_worker,
        m.generation_timed_out_at_service,
        str(m.model_call_duration_ms),
    )
    return m


# ---------------------------------------------------------------------------
# Main revalidation runner
# ---------------------------------------------------------------------------

def run_revalidation():
    logger.info("=" * 60)
    logger.info("Phase 4X-R1: Controlled Performance Revalidation")
    logger.info("=" * 60)

    specs = get_system_specs()
    if specs["target_not_met"]:
        logger.warning(
            "RAM TARGET NOT MET: %.2f GB available < 3.0 GB target. "
            "Environment is contaminated. Recording target_not_met=true.",
            specs["available_ram_gb"],
        )
    else:
        logger.info("RAM target met: %.2f GB available.", specs["available_ram_gb"])

    identity_cfg = IdentityConfig(
        issuer=_TEST_ISSUER,
        audience=_TEST_AUDIENCE,
        hs256_secret=_TEST_SECRET.encode("utf-8"),
    )
    res_cfg = ResilienceConfig(
        request_timeout_seconds=30.0,
        max_concurrent_inferences=1,
        queue_timeout_seconds=0.5,
        enable_circuit_breaker=True,
        circuit_failure_threshold=3,
        circuit_cooldown_seconds=10.0,
    )

    # M0: clean startup
    M0 = get_env_snapshot("M0_startup")
    logger.info("M0 startup: RSS=%.2f MB avail=%.2f GB", M0["rss_mb"], M0["available_ram_gb"])

    # Pipeline init (lazy generator — Gemma not loaded yet)
    t0_pipe = time.perf_counter()
    pipeline = AtlasServicePipeline.create_default(lazy_generator=True)
    t_pipe_ms = (time.perf_counter() - t0_pipe) * 1000.0

    M1 = get_env_snapshot("M1_pipeline_loaded")
    logger.info(
        "M1 pipeline loaded: %.2f ms | RSS=%.2f MB | avail=%.2f GB",
        t_pipe_ms, M1["rss_mb"], M1["available_ram_gb"],
    )

    from fastapi.testclient import TestClient
    app = create_app(
        pipeline=pipeline, resilience_config=res_cfg, identity_config=identity_cfg
    )
    client = TestClient(app, raise_server_exceptions=False)

    ready = client.get("/ready")
    assert ready.status_code == 200, f"/ready failed: {ready.text}"
    logger.info("/ready OK: %s", ready.json())

    token_eng = create_jwt_token(user_department="Engineering")
    headers_eng = {"Authorization": f"Bearer {token_eng}"}

    # -----------------------------------------------------------------------
    # STEP 7: Retrieval Control (3 queries, no generation)
    # -----------------------------------------------------------------------
    logger.info("\n=== RETRIEVAL CONTROL (3 queries, no generation) ===")
    control_queries = [
        "What is the network topology of core-gateway?",
        "Authentication policy version 1.0 requirements",
        "Database failover runbook and replica sync lag",
    ]
    eval_case_dict = {
        "tenant_id": "TENANT-NOVASTACK",
        "user_role": "engineer",
        "user_department": "Engineering",
        "expected_access": "allow",
    }
    retrieval_controls = []
    for q in control_queries:
        t0 = time.perf_counter()
        qu = pipeline.qu_extractor.extract("R1-CTRL", q) if pipeline.qu_extractor else None
        t_qu = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        bm = pipeline.bm25_index.search(
            qu.expanded_query if qu else q, top_k=50,
            filters={"tenant_id": "TENANT-NOVASTACK"},
        )
        t_bm = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        dn = pipeline.dense_index.search(
            q, top_k=50, filters={"tenant_id": "TENANT-NOVASTACK"}
        )
        t_dn = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        rel_cands = []
        if pipeline.structured_retriever:
            rel_res = pipeline.structured_retriever.retrieve(
                q, eval_case=eval_case_dict, top_k=50
            )
            rel_cands = rel_res.candidates
        t_rel = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        hybrid = fuse_rrf_sum(bm, dn, top_k=50, k=60, deduplicate_docs=True)
        fused = fuse_hybrid_and_structured(
            hybrid, rel_cands, k=60, w_hybrid=1.0, w_struct=1.0,
            top_k=50, deduplicate_docs=True, catalog=pipeline.catalog,
        )
        t_fus = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        reranked = (
            pipeline.reranker.rerank(fused, qu=qu, metadata_index=pipeline.metadata_snapshot_index)
            if pipeline.reranker
            else fused
        )
        t_rank = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        _ = pipeline.resolver.resolve_package(
            query=q, candidates=reranked, eval_case=eval_case_dict, qu=qu,
            channel_candidates={"bm25": bm, "dense": dn, "structured": rel_cands},
        )
        t_ev = (time.perf_counter() - t0) * 1000.0

        total = t_qu + t_bm + t_dn + t_rel + t_fus + t_rank + t_ev
        logger.info(
            "Retrieval '%s': total=%.2fms (qu=%.2f bm=%.2f dn=%.2f "
            "rel=%.2f fus=%.2f rank=%.2f ev=%.2f)",
            q[:40], total, t_qu, t_bm, t_dn, t_rel, t_fus, t_rank, t_ev,
        )
        retrieval_controls.append({
            "query": q,
            "query_understanding_ms": round(t_qu, 2),
            "bm25_ms": round(t_bm, 2),
            "dense_ms": round(t_dn, 2),
            "relational_retrieval_ms": round(t_rel, 2),
            "fusion_ms": round(t_fus, 2),
            "metadata_ranking_ms": round(t_rank, 2),
            "evidence_resolution_ms": round(t_ev, 2),
            "total_retrieval_ms": round(total, 2),
            "was_generation_invoked": False,
        })

    avg_retrieval_ms = round(
        sum(c["total_retrieval_ms"] for c in retrieval_controls) / len(retrieval_controls), 2
    )
    logger.info("Average retrieval: %.2f ms", avg_retrieval_ms)

    # -----------------------------------------------------------------------
    # Load real Gemma model
    # -----------------------------------------------------------------------
    logger.info("\n=== LOADING GEMMA 3 1B MODEL ===")
    M2_before = get_env_snapshot("M2_before_model_load")
    t0_model = time.perf_counter()
    doc_ids = {d.document_id for d in pipeline.resolver.documents_index.values()}
    chunk_ids = {c.chunk_id for c in pipeline.resolver.chunks_index.values()}
    real_generator = GroundedAnswerGenerator(
        model_name="google/gemma-3-1b-it",
        device="cpu",
        lazy_load=False,
        corpus_doc_ids=doc_ids,
        corpus_chunk_ids=chunk_ids,
    )
    t_model_load_s = time.perf_counter() - t0_model
    pipeline.generator = real_generator

    M2 = get_env_snapshot("M2_model_loaded")
    model_dtype = (
        str(next(real_generator.model.parameters()).dtype)
        if real_generator.model else "unknown"
    )
    logger.info(
        "Model loaded in %.2f s | dtype=%s | RSS=%.2f MB (delta=%.2f MB) | avail=%.2f GB",
        t_model_load_s, model_dtype, M2["rss_mb"],
        M2["rss_mb"] - M2_before["rss_mb"], M2["available_ram_gb"],
    )

    # -----------------------------------------------------------------------
    # STEP 8: Abstention Control (2 queries)
    # -----------------------------------------------------------------------
    logger.info("\n=== ABSTENTION CONTROL (2 queries) ===")
    abstention_queries = [
        "xk9_nonexistent_unique_token_with_no_match_in_corpus_zq7",
        "zzz_fabricated_query_guaranteed_zero_retrieval_results_abc",
    ]
    abstention_controls = []
    for i, aq in enumerate(abstention_queries):
        env_a = get_env_snapshot(f"abstention_{i}_before")
        t0 = time.perf_counter()
        resp_ab = client.post(
            "/query",
            json={"query": aq, "user_context": {"tenant_id": "TENANT-NOVASTACK"}},
            headers=headers_eng,
            timeout=45.0,
        )
        lat_ab_ms = (time.perf_counter() - t0) * 1000.0
        body_ab = resp_ab.json()
        env_a_after = get_env_snapshot(f"abstention_{i}_after")
        logger.info(
            "Abstention #%d: HTTP=%d status=%s lat=%.2fms avail_ram=%.2f GB",
            i + 1, resp_ab.status_code, body_ab.get("answer_status"),
            lat_ab_ms, env_a_after["available_ram_gb"],
        )
        abstention_controls.append({
            "eval_id": f"ABS-{i+1}",
            "query": aq,
            "http_status": resp_ab.status_code,
            "answer_status": body_ab.get("answer_status", "unknown"),
            "was_generation_invoked": body_ab.get("was_generation_invoked", False),
            "total_latency_ms": round(lat_ab_ms, 2),
            "available_ram_gb": env_a_after["available_ram_gb"],
            "rss_mb": env_a_after["rss_mb"],
        })

    # -----------------------------------------------------------------------
    # STEP 4: Three Sequential Genuine Generations (C=1)
    # -----------------------------------------------------------------------
    logger.info("\n=== GENUINE GENERATION (3 sequential, C=1) ===")
    generation_queries = [
        (
            "GEN-R1-001",
            "What is the primary network topology and failover configuration of core-gateway?",
        ),
        (
            "GEN-R1-002",
            "Authentication policy version 1.0 requirements and enforcement mechanism",
        ),
        (
            "GEN-R1-003",
            "Database replica sync lag thresholds and failover runbook procedure",
        ),
    ]

    generation_measurements: List[GenerationMeasurement] = []
    memory_checkpoints: List[Dict[str, Any]] = [M0, M1, M2]

    for i, (eval_id, query) in enumerate(generation_queries):
        M_pre = get_env_snapshot(f"M{3 + i * 2}_pre_gen_{i+1}")
        memory_checkpoints.append(M_pre)
        logger.info(
            "\n--- Generation #%d %s ---\nPre-gen: RSS=%.2f MB avail=%.2f GB threads=%d",
            i + 1, eval_id, M_pre["rss_mb"], M_pre["available_ram_gb"], M_pre["thread_count"],
        )

        # Always reset circuit breaker to CLOSED before each attempt
        app.state.circuit_breaker.record_success()

        meas = run_single_generation(
            client=client,
            app=app,
            pipeline=pipeline,
            headers=headers_eng,
            eval_id=eval_id,
            query=query,
            deadline_s=30.0,
        )
        generation_measurements.append(meas)

        M_post = get_env_snapshot(f"M{4 + i * 2}_post_gen_{i+1}")
        memory_checkpoints.append(M_post)
        logger.info(
            "Post-gen: RSS=%.2f MB (delta=%+.2f MB) avail=%.2f GB",
            M_post["rss_mb"], M_post["rss_mb"] - M_pre["rss_mb"], M_post["available_ram_gb"],
        )

        # Wait 5s between requests to allow the executor worker thread to
        # finish naturally (it continues after asyncio.wait_for fires)
        logger.info("Waiting 5s between requests (worker completion window)...")
        time.sleep(5.0)

    gc.collect()
    M9 = get_env_snapshot("M9_after_gc")
    memory_checkpoints.append(M9)
    logger.info("M9 post-GC: RSS=%.2f MB avail=%.2f GB", M9["rss_mb"], M9["available_ram_gb"])

    time.sleep(10.0)
    M10 = get_env_snapshot("M10_after_idle")
    memory_checkpoints.append(M10)
    logger.info("M10 after idle: RSS=%.2f MB avail=%.2f GB", M10["rss_mb"], M10["available_ram_gb"])

    # -----------------------------------------------------------------------
    # STEP 10: Security Sanity Check
    # -----------------------------------------------------------------------
    logger.info("\n=== SECURITY SANITY CHECK ===")
    app.state.circuit_breaker.record_success()

    t0 = time.perf_counter()
    resp_noauth = client.post(
        "/query",
        json={
            "query": "What is the network topology?",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        },
        headers={"Authorization": ""},
    )
    lat_noauth_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    resp_auth = client.post(
        "/query",
        json={
            "query": "What is the network topology?",
            "user_context": {"tenant_id": "TENANT-NOVASTACK"},
        },
        headers=headers_eng,
        timeout=45.0,
    )
    lat_auth_ms = (time.perf_counter() - t0) * 1000.0

    logger.info(
        "No-auth: HTTP=%d %.2fms | Authorized: HTTP=%d %.2fms",
        resp_noauth.status_code, lat_noauth_ms, resp_auth.status_code, lat_auth_ms,
    )
    security_check = {
        "missing_auth_http_status": resp_noauth.status_code,
        "missing_auth_latency_ms": round(lat_noauth_ms, 2),
        "missing_auth_fails_closed": resp_noauth.status_code == 401,
        "authorized_http_status": resp_auth.status_code,
        "authorized_latency_ms": round(lat_auth_ms, 2),
    }

    # -----------------------------------------------------------------------
    # Comparison 4X vs 4X-R1
    # -----------------------------------------------------------------------
    phase_4x_ref = {
        "available_ram_gb": 0.51,
        "retrieval_latency_ms": 44.71,
        "abstention_latency_ms_1": 30326.02,
        "generation_1_total_ms": 30745.18,
        "generation_2_total_ms": "NOT_MEASURED",
        "generation_3_total_ms": "NOT_MEASURED",
        "model_call_started": "NOT_OBSERVED (instrumentation ambiguity)",
        "generation_completed": False,
        "peak_rss_mb": 1535.54,
        "memory_after_gc_mb": 1594.06,
        "http_status_gen1": 504,
        "deadline_headroom_ms_gen1": -745.18,
    }

    def _gen_val(idx: int, attr: str):
        if idx < len(generation_measurements):
            return getattr(generation_measurements[idx], attr)
        return "NOT_MEASURED"

    comparison = {
        "available_ram_gb": {
            "4X": phase_4x_ref["available_ram_gb"],
            "4X_R1": specs["available_ram_gb"],
            "target_not_met_r1": specs["target_not_met"],
        },
        "retrieval_latency_ms": {
            "4X": phase_4x_ref["retrieval_latency_ms"],
            "4X_R1": avg_retrieval_ms,
        },
        "abstention_latency_ms": {
            "4X": phase_4x_ref["abstention_latency_ms_1"],
            "4X_R1_abs1": abstention_controls[0]["total_latency_ms"] if abstention_controls else None,
            "4X_R1_abs2": abstention_controls[1]["total_latency_ms"] if len(abstention_controls) > 1 else None,
        },
        "generation_1_total_ms": {
            "4X": phase_4x_ref["generation_1_total_ms"],
            "4X_R1": _gen_val(0, "total_latency_ms"),
        },
        "generation_2_total_ms": {
            "4X": phase_4x_ref["generation_2_total_ms"],
            "4X_R1": _gen_val(1, "total_latency_ms"),
        },
        "generation_3_total_ms": {
            "4X": phase_4x_ref["generation_3_total_ms"],
            "4X_R1": _gen_val(2, "total_latency_ms"),
        },
        "model_call_started_gen1": {
            "4X": phase_4x_ref["model_call_started"],
            "4X_R1": _gen_val(0, "model_call_started"),
        },
        "model_call_started_gen2": {
            "4X": phase_4x_ref["model_call_started"],
            "4X_R1": _gen_val(1, "model_call_started"),
        },
        "model_call_started_gen3": {
            "4X": phase_4x_ref["model_call_started"],
            "4X_R1": _gen_val(2, "model_call_started"),
        },
        "generation_completed_gen1": {
            "4X": phase_4x_ref["generation_completed"],
            "4X_R1": _gen_val(0, "generation_completed_in_worker"),
        },
        "generation_completed_gen2": {
            "4X": phase_4x_ref["generation_completed"],
            "4X_R1": _gen_val(1, "generation_completed_in_worker"),
        },
        "generation_completed_gen3": {
            "4X": phase_4x_ref["generation_completed"],
            "4X_R1": _gen_val(2, "generation_completed_in_worker"),
        },
        "peak_rss_mb": {
            "4X": phase_4x_ref["peak_rss_mb"],
            "4X_R1": max((m.memory_after_mb for m in generation_measurements), default=0.0),
        },
        "memory_after_gc_mb": {
            "4X": phase_4x_ref["memory_after_gc_mb"],
            "4X_R1": M9["rss_mb"],
        },
        "http_status_gen1": {
            "4X": phase_4x_ref["http_status_gen1"],
            "4X_R1": _gen_val(0, "http_status"),
        },
        "deadline_headroom_ms_gen1": {
            "4X": phase_4x_ref["deadline_headroom_ms_gen1"],
            "4X_R1": _gen_val(0, "deadline_headroom_ms"),
        },
    }

    # -----------------------------------------------------------------------
    # Verdict
    # -----------------------------------------------------------------------
    all_model_calls = all(m.model_call_started for m in generation_measurements)
    any_completed = any(m.generation_completed_in_worker for m in generation_measurements)
    env_clean = not specs["target_not_met"]
    three_measured = len(generation_measurements) == 3

    if not three_measured:
        verdict = "HOLD"
        verdict_reason = "Fewer than 3 generation measurements were collected."
    elif not env_clean:
        verdict = "HOLD"
        verdict_reason = (
            f"Available RAM {specs['available_ram_gb']:.2f} GB < 3.0 GB target. "
            "Environmental contamination persists. Measurements recorded but "
            "target_not_met=true. CTO should treat figures as lower-bound estimates."
        )
    elif all_model_calls and three_measured:
        verdict = "PASS"
        verdict_reason = (
            "model.generate() confirmed started on all 3 requests (T6 observed). "
            "Phase 4X instrumentation ambiguity is resolved. "
            "Measurements are internally consistent and sufficient for CTO decision."
        )
    else:
        verdict = "HOLD"
        verdict_reason = (
            "model.generate() was NOT observed (T6) on one or more requests. "
            "Insufficient evidence to characterize generation behaviour."
        )

    # -----------------------------------------------------------------------
    # Build and write artifact
    # -----------------------------------------------------------------------
    artifact_data = {
        "phase": "4X-R1",
        "title": "Controlled Performance Revalidation",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "frozen_configuration": {
            "enable_boundary_stitching": False,
            "enable_query_aware_authority": True,
            "enable_event_bundling": False,
            "max_concurrent_inferences": res_cfg.max_concurrent_inferences,
            "queue_timeout_seconds": res_cfg.queue_timeout_seconds,
            "request_timeout_seconds": res_cfg.request_timeout_seconds,
            "enable_circuit_breaker": res_cfg.enable_circuit_breaker,
            "circuit_failure_threshold": res_cfg.circuit_failure_threshold,
            "circuit_cooldown_seconds": res_cfg.circuit_cooldown_seconds,
        },
        "system_specs": specs,
        "model_dtype": model_dtype,
        "model_load_seconds": round(t_model_load_s, 2),
        "memory_checkpoints": memory_checkpoints,
        "retrieval_controls": retrieval_controls,
        "retrieval_avg_ms": avg_retrieval_ms,
        "abstention_controls": abstention_controls,
        "generation_measurements": [asdict(m) for m in generation_measurements],
        "security_check": security_check,
        "comparison_4x_vs_4x_r1": comparison,
    }

    out_path = WORKSPACE / "artifacts" / "phase_4x_r1_performance_revalidation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact_data, indent=2, default=str), encoding="utf-8")
    logger.info("Artifact written to %s", out_path)
    print(f"\nPHASE 4X-R1 CHARACTERIZATION COMPLETE.")
    print(f"VERDICT: {verdict}")
    print(f"REASON:  {verdict_reason}")
    return artifact_data


if __name__ == "__main__":
    run_revalidation()
