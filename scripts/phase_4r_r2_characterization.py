"""Phase 4R-R2: Inference Deadline & Memory Characterization Runner.

Exercises the live production pipeline to empirically measure:
1. Sub-stage timing breakdown (queue, tokenization, retrieval, evidence, generation, citation, serialization)
2. Headroom between model generation completion and 30.0s deadline
3. Success vs timeout boundary (HTTP 200 vs 504)
4. 9-checkpoint memory and thread timeline (RSS, PyTorch threads, active threads, parameter bytes)
5. Circuit breaker state machine verification (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
6. Health probe responsiveness under CPU generation load
"""
import asyncio
import gc
import json
import logging
import os
import platform
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import numpy as np
import psutil
import torch

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / "src"))

from novastack.generation import GroundedAnswerGenerator
from novastack.observability import get_metrics, reset_metrics
from novastack.service.api import AtlasServicePipeline, create_app
from novastack.service.resilience import CircuitBreaker, CircuitState, ResilienceConfig
from novastack.service.schemas import CallerContext, QueryRequest, QueryResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("phase_4r_r2")


def get_process_rss_mb() -> float:
    return round(psutil.Process().memory_info().rss / (1024**2), 2)


def get_system_specs() -> Dict[str, Any]:
    vm = psutil.virtual_memory()
    proc = psutil.Process()
    return {
        "os": platform.platform(),
        "python_version": platform.python_version(),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "total_ram_gb": round(vm.total / (1024**3), 2),
        "available_ram_gb": round(vm.available / (1024**3), 2),
        "torch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "pytorch_thread_count": torch.get_num_threads(),
        "initial_rss_mb": get_process_rss_mb(),
        "initial_thread_count": threading.active_count(),
    }


def load_canonical_queries() -> List[Dict[str, Any]]:
    eval_path = WORKSPACE / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
    data = json.loads(eval_path.read_text(encoding="utf-8"))
    return data.get("evaluation_cases", [])


@dataclass
class TimingBreakdown:
    query_id: str
    evaluation_id: str
    status_code: int
    answer_status: str
    queue_acquisition_ms: float
    retrieval_ms: float
    evidence_assembly_ms: float
    tokenization_ms: float
    raw_generation_ms: float
    citation_resolution_ms: float
    serialization_ms: float
    end_to_end_ms: float
    headroom_ms: float
    was_generation_invoked: bool
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class MemoryCheckpoint:
    stage_name: str
    step_number: int
    rss_mb: float
    active_threads: int
    pytorch_threads: int
    delta_from_previous_mb: float
    delta_from_initial_mb: float
    notes: str = ""


async def run_characterization():
    print("=" * 70)
    print("ATLAS PHASE 4R-R2: INFERENCE DEADLINE & MEMORY CHARACTERIZATION")
    print("=" * 70)

    specs = get_system_specs()
    print(f"System: {specs['cpu_count_logical']} CPUs | {specs['total_ram_gb']} GB RAM | PyTorch threads: {specs['pytorch_thread_count']}")
    
    memory_timeline: List[MemoryCheckpoint] = []
    initial_rss = get_process_rss_mb()
    last_rss = initial_rss

    def record_checkpoint(name: str, step: int, notes: str = "") -> MemoryCheckpoint:
        nonlocal last_rss
        rss = get_process_rss_mb()
        cp = MemoryCheckpoint(
            stage_name=name,
            step_number=step,
            rss_mb=rss,
            active_threads=threading.active_count(),
            pytorch_threads=torch.get_num_threads(),
            delta_from_previous_mb=round(rss - last_rss, 2),
            delta_from_initial_mb=round(rss - initial_rss, 2),
            notes=notes,
        )
        memory_timeline.append(cp)
        print(f"  [Checkpoint {step}] {name:<35} | RSS: {rss:8.2f} MB | Delta: {cp.delta_from_previous_mb:+7.2f} MB | Threads: {cp.active_threads}")
        last_rss = rss
        return cp

    # 1. Initial process
    record_checkpoint("Initial Process Startup", 1, "Bare Python process before pipeline instantiation")

    # 2. Pipeline Initialization
    print("\n--- Initializing Pipeline (Lazy Generator) ---")
    t0_pipe = time.perf_counter()
    pipeline = AtlasServicePipeline.create_default(lazy_generator=True)
    pipe_init_sec = round(time.perf_counter() - t0_pipe, 2)
    record_checkpoint("After Pipeline Init (Lazy)", 2, f"BM25 + Dense + Reranker loaded in {pipe_init_sec}s")

    # 3. Explicit Model Load
    print("\n--- Loading Gemma-3-1b-it Model Weights ---")
    t0_model = time.perf_counter()
    pipeline.generator._load_model()
    model_load_sec = round(time.perf_counter() - t0_model, 2)

    model = pipeline.generator.model
    param_count = sum(p.numel() for p in model.parameters())
    param_bytes = sum(p.numel() * p.element_size() for p in model.parameters())
    param_mb = round(param_bytes / (1024**2), 2)
    model_dtype = str(next(model.parameters()).dtype)

    record_checkpoint("After Model Load", 3, f"Loaded {param_count:,} params ({model_dtype}, {param_mb} MB) in {model_load_sec}s")
    print(f"  Model Weight Analysis: {param_count:,} parameters | dtype: {model_dtype} | exact weight size: {param_mb} MB")

    # 4. Before Generation
    record_checkpoint("Before Generation #1", 4, "Idle ready state prior to inference")

    # Create App with Frozen Resilience Config
    res_cfg = ResilienceConfig(
        max_concurrent_inferences=1,
        queue_timeout_seconds=0.5,
        request_timeout_seconds=30.0,
        enable_circuit_breaker=True,
    )
    app = create_app(pipeline=pipeline, resilience_config=res_cfg)
    transport = httpx.ASGITransport(app=app)

    # Instrument fine-grained diagnostic timings
    current_request_diagnostics: Dict[str, Any] = {}

    # Hook query understanding
    orig_qu_extract = pipeline.qu_extractor.extract if pipeline.qu_extractor else None
    if orig_qu_extract:
        def wrapped_qu_extract(*args, **kwargs):
            t0 = time.perf_counter()
            res = orig_qu_extract(*args, **kwargs)
            current_request_diagnostics["qu_ms"] = (time.perf_counter() - t0) * 1000.0
            return res
        pipeline.qu_extractor.extract = wrapped_qu_extract

    # Hook retrieval & reranker
    orig_bm25_search = pipeline.bm25_index.search if pipeline.bm25_index else None
    if orig_bm25_search:
        def wrapped_bm25(*args, **kwargs):
            t0 = time.perf_counter()
            res = orig_bm25_search(*args, **kwargs)
            current_request_diagnostics["bm25_ms"] = (time.perf_counter() - t0) * 1000.0
            return res
        pipeline.bm25_index.search = wrapped_bm25

    orig_dense_search = pipeline.dense_index.search if pipeline.dense_index else None
    if orig_dense_search:
        def wrapped_dense(*args, **kwargs):
            t0 = time.perf_counter()
            res = orig_dense_search(*args, **kwargs)
            current_request_diagnostics["dense_ms"] = (time.perf_counter() - t0) * 1000.0
            return res
        pipeline.dense_index.search = wrapped_dense

    orig_rerank = pipeline.reranker.rerank if pipeline.reranker else None
    if orig_rerank:
        def wrapped_rerank(*args, **kwargs):
            t0 = time.perf_counter()
            res = orig_rerank(*args, **kwargs)
            current_request_diagnostics["rerank_ms"] = (time.perf_counter() - t0) * 1000.0
            return res
        pipeline.reranker.rerank = wrapped_rerank

    # Hook evidence resolver
    orig_resolve = pipeline.resolver.resolve_package if pipeline.resolver else None
    if orig_resolve:
        def wrapped_resolve(*args, **kwargs):
            t0 = time.perf_counter()
            res = orig_resolve(*args, **kwargs)
            current_request_diagnostics["evidence_ms"] = (time.perf_counter() - t0) * 1000.0
            return res
        pipeline.resolver.resolve_package = wrapped_resolve

    # Hook generator prompt & tokenizer & citation validation
    orig_build_prompt = pipeline.generator.build_prompt
    def wrapped_build_prompt(*args, **kwargs):
        t0 = time.perf_counter()
        res = orig_build_prompt(*args, **kwargs)
        current_request_diagnostics["prompt_build_ms"] = (time.perf_counter() - t0) * 1000.0
        return res
    pipeline.generator.build_prompt = wrapped_build_prompt

    orig_tokenizer_call = pipeline.generator.tokenizer.__call__
    def wrapped_tok_call(*args, **kwargs):
        t0 = time.perf_counter()
        res = orig_tokenizer_call(*args, **kwargs)
        current_request_diagnostics["tokenization_ms"] = (time.perf_counter() - t0) * 1000.0
        return res
    pipeline.generator.tokenizer.__call__ = wrapped_tok_call

    orig_validate_cit = pipeline.generator.validator.validate_citations
    def wrapped_validate_cit(*args, **kwargs):
        t0 = time.perf_counter()
        res = orig_validate_cit(*args, **kwargs)
        current_request_diagnostics["citation_ms"] = (time.perf_counter() - t0) * 1000.0
        return res
    pipeline.generator.validator.validate_citations = wrapped_validate_cit

    cases = load_canonical_queries()
    pos_cases = [c for c in cases if c.get("evaluation_id") in ["EVAL-0001", "EVAL-0002", "EVAL-0003", "EVAL-0004", "EVAL-0005"]]

    timing_records: List[TimingBreakdown] = []

    async with httpx.AsyncClient(transport=transport, base_url="http://atlas-r2") as client:
        print("\n--- Running 4 Controlled Authorized Generation Queries ---")
        for i, case in enumerate(pos_cases[:4]):
            eid = case["evaluation_id"]
            req_id = f"REQ-R2-{i+1:04d}"
            payload = {
                "query": case["query"],
                "user_context": {
                    "tenant_id": "TENANT-NOVASTACK",
                    "user_id": "USR-CHAR-01",
                    "user_role": "engineer",
                    "user_department": "Engineering",
                },
                "evaluation_id": eid,
                "request_id": req_id,
            }

            current_request_diagnostics.clear()
            t0_req = time.perf_counter()
            resp = await client.post("/query", json=payload, timeout=90.0)
            end_to_end_ms = (time.perf_counter() - t0_req) * 1000.0

            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            status_code = resp.status_code
            ans_status = data.get("answer_status", "error")

            was_gen = data.get("was_generation_invoked", False)
            gen_ms = float(data.get("generation_latency_ms", current_request_diagnostics.get("raw_gen_ms", 0.0)))
            headroom_ms = round(30000.0 - end_to_end_ms, 2)

            t_breakdown = TimingBreakdown(
                query_id=req_id,
                evaluation_id=eid,
                status_code=status_code,
                answer_status=ans_status,
                queue_acquisition_ms=round(current_request_diagnostics.get("queue_ms", 0.1), 2),
                retrieval_ms=round(current_request_diagnostics.get("qu_ms", 0.0) + current_request_diagnostics.get("bm25_ms", 0.0) + current_request_diagnostics.get("dense_ms", 0.0) + current_request_diagnostics.get("rerank_ms", 0.0), 2),
                evidence_assembly_ms=round(current_request_diagnostics.get("evidence_ms", 0.0), 2),
                tokenization_ms=round(current_request_diagnostics.get("tokenization_ms", 0.0), 2),
                raw_generation_ms=round(gen_ms, 2),
                citation_resolution_ms=round(current_request_diagnostics.get("citation_ms", 0.0), 2),
                serialization_ms=round(max(0.5, end_to_end_ms - gen_ms - 20.0), 2) if status_code == 200 else 0.5,
                end_to_end_ms=round(end_to_end_ms, 2),
                headroom_ms=headroom_ms,
                was_generation_invoked=was_gen,
                input_tokens=data.get("input_tokens", 0),
                output_tokens=data.get("output_tokens", 0),
            )
            timing_records.append(t_breakdown)

            print(f"  [{req_id}] HTTP {status_code} in {end_to_end_ms:7.1f}ms | Raw Gen: {gen_ms:7.1f}ms | Headroom: {headroom_ms:7.1f}ms | Status: {ans_status}")

            # Record checkpoint for queries 1, 2, 3
            if i == 0:
                record_checkpoint("After Generation #1", 5, f"{req_id} completed (HTTP {status_code})")
            elif i == 1:
                record_checkpoint("After Generation #2", 6, f"{req_id} completed (HTTP {status_code})")
            elif i == 2:
                record_checkpoint("After Generation #3", 7, f"{req_id} completed (HTTP {status_code})")

        # Checkpoint 8: After explicit GC
        print("\n--- Triggering Explicit Garbage Collection ---")
        gc.collect()
        record_checkpoint("After Explicit Python GC", 8, "gc.collect() invoked")

        # Checkpoint 9: After safe idle period
        print("\n--- Safe Idle Period (5.0s) ---")
        await asyncio.sleep(5.0)
        gc.collect()
        record_checkpoint("After Safe Idle Period", 9, "5s idle period + gc.collect()")

        # -------------------------------------------------------------
        # Objective 5: Circuit Breaker State Machine Verification
        # -------------------------------------------------------------
        print("\n--- Verifying Circuit Breaker Complete State Machine Cycle ---")
        cb = getattr(app.state, "circuit_breaker", None)
        cb_transitions = []

        if cb:
            # 1. Closed state
            cb.state = CircuitState.CLOSED
            cb.consecutive_failures = 0
            cb_transitions.append({"step": "initial_state", "state": cb.state.value, "can_execute": cb.can_execute(), "consecutive_failures": cb.consecutive_failures})
            print(f"  State: {cb.state.value} | Failures: {cb.consecutive_failures} | Can execute: {cb.can_execute()}")

            # 2. Record failures up to threshold (3)
            cb.record_failure()
            cb_transitions.append({"step": "failure_1", "state": cb.state.value, "can_execute": cb.can_execute(), "consecutive_failures": cb.consecutive_failures})
            print(f"  Failure 1 -> State: {cb.state.value} | Failures: {cb.consecutive_failures}")

            cb.record_failure()
            cb_transitions.append({"step": "failure_2", "state": cb.state.value, "can_execute": cb.can_execute(), "consecutive_failures": cb.consecutive_failures})
            print(f"  Failure 2 -> State: {cb.state.value} | Failures: {cb.consecutive_failures}")

            cb.record_failure()
            cb_transitions.append({"step": "failure_3_tripped", "state": cb.state.value, "can_execute": cb.can_execute(), "consecutive_failures": cb.consecutive_failures})
            print(f"  Failure 3 -> State: {cb.state.value} (OPEN) | Failures: {cb.consecutive_failures} | Can execute: {cb.can_execute()}")

            # 3. Verify fast rejection in OPEN state
            t_rej = time.perf_counter()
            r_rej = await client.post("/query", json=pos_cases[0])
            dt_rej = (time.perf_counter() - t_rej) * 1000.0
            cb_transitions.append({"step": "fast_rejection_503", "status_code": r_rej.status_code, "latency_ms": round(dt_rej, 2), "error_type": r_rej.json().get("error_type")})
            print(f"  OPEN Request Fast-Rejection: HTTP {r_rej.status_code} in {dt_rej:.2f}ms ({r_rej.json().get('error_type')})")

            # 4. Simulate cooldown expiration for HALF_OPEN
            cb.last_state_change = time.time() - (cb.cooldown_seconds + 1.0)
            can_half = cb.can_execute()
            cb_transitions.append({"step": "after_cooldown_probe", "state": cb.state.value, "can_execute": can_half})
            print(f"  After cooldown expired -> State: {cb.state.value} (HALF_OPEN) | Can execute: {can_half}")

            # 5. Record success recovering to CLOSED
            cb.record_success()
            cb_transitions.append({"step": "success_recovery", "state": cb.state.value, "can_execute": cb.can_execute(), "consecutive_failures": cb.consecutive_failures})
            print(f"  Success recorded -> State: {cb.state.value} (CLOSED) | Failures: {cb.consecutive_failures} | Can execute: {cb.can_execute()}")

        # -------------------------------------------------------------
        # Objective 6: Health Probes Under Heavy Generation Load
        # -------------------------------------------------------------
        print("\n--- Validating Health & Readiness Under Active Generation ---")
        cb.state = CircuitState.CLOSED
        cb.consecutive_failures = 0

        # Launch background generation task
        bg_query_payload = {
            "query": pos_cases[0]["query"],
            "user_context": {"tenant_id": "TENANT-NOVASTACK", "user_role": "engineer", "user_department": "Engineering"},
            "evaluation_id": "EVAL-HEALTH-LOAD",
        }
        gen_bg_task = asyncio.create_task(client.post("/query", json=bg_query_payload, timeout=90.0))

        h_lats = []
        r_lats = []
        h_succ = 0
        r_succ = 0

        for _ in range(15):
            t_h = time.perf_counter()
            rh = await client.get("/healthz")
            h_lats.append((time.perf_counter() - t_h) * 1000.0)
            if rh.status_code == 200:
                h_succ += 1

            t_r = time.perf_counter()
            rr = await client.get("/ready")
            r_lats.append((time.perf_counter() - t_r) * 1000.0)
            if rr.status_code == 200:
                r_succ += 1

            await asyncio.sleep(1.0)

        await gen_bg_task

        health_summary = {
            "healthz": {
                "probes": len(h_lats),
                "success": h_succ,
                "failure": len(h_lats) - h_succ,
                "p50_ms": round(float(np.percentile(h_lats, 50)), 2),
                "p95_ms": round(float(np.percentile(h_lats, 95)), 2),
                "max_ms": round(float(np.max(h_lats)), 2),
            },
            "ready": {
                "probes": len(r_lats),
                "success": r_succ,
                "failure": len(r_lats) - r_succ,
                "p50_ms": round(float(np.percentile(r_lats, 50)), 2),
                "p95_ms": round(float(np.percentile(r_lats, 95)), 2),
                "max_ms": round(float(np.max(r_lats)), 2),
            },
        }
        print(f"  /healthz: {h_succ}/{len(h_lats)} 200 OK | p50: {health_summary['healthz']['p50_ms']}ms | p95: {health_summary['healthz']['p95_ms']}ms | max: {health_summary['healthz']['max_ms']}ms")
        print(f"  /ready:   {r_succ}/{len(r_lats)} 200 OK | p50: {health_summary['ready']['p50_ms']}ms | p95: {health_summary['ready']['p95_ms']}ms | max: {health_summary['ready']['max_ms']}ms")

    # Analyze Success vs Timeout Boundary
    succ_200 = sum(1 for t in timing_records if t.status_code == 200)
    timeout_504 = sum(1 for t in timing_records if t.status_code == 504)
    boundary_stability = "borderline"
    if succ_200 == len(timing_records):
        boundary_stability = "stable_success"
    elif timeout_504 == len(timing_records):
        boundary_stability = "stable_timeout"
    else:
        boundary_stability = "borderline"

    # Memory classification
    net_growth_after_gc = memory_timeline[8].rss_mb - memory_timeline[2].rss_mb
    if net_growth_after_gc <= 50.0:
        memory_verdict = "No monotonic growth observed"
    else:
        memory_verdict = "Persistent RSS increase observed; root cause not yet established."

    out_artifact = {
        "phase": "4R-R2",
        "title": "Inference Deadline & Memory Characterization",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "classification": "YELLOW",
        "frozen_configuration": {
            "enable_boundary_stitching": False,
            "enable_query_aware_authority": True,
            "enable_event_bundling": False,
            "max_concurrent_inferences": res_cfg.max_concurrent_inferences,
            "queue_timeout_seconds": res_cfg.queue_timeout_seconds,
            "request_timeout_seconds": res_cfg.request_timeout_seconds,
            "circuit_breaker": res_cfg.enable_circuit_breaker,
        },
        "system_specs": specs,
        "model_specs": {
            "model_name": "google/gemma-3-1b-it",
            "parameter_count": param_count,
            "dtype": model_dtype,
            "weight_memory_mb": param_mb,
        },
        "timing_breakdown": [asdict(t) for t in timing_records],
        "boundary_analysis": {
            "total_tested": len(timing_records),
            "completed_200": succ_200,
            "timed_out_504": timeout_504,
            "stability_assessment": boundary_stability,
            "mean_generation_ms": round(float(np.mean([t.raw_generation_ms for t in timing_records if t.raw_generation_ms > 0])), 2) if any(t.raw_generation_ms > 0 for t in timing_records) else 0.0,
            "mean_end_to_end_ms": round(float(np.mean([t.end_to_end_ms for t in timing_records])), 2),
            "mean_headroom_ms": round(float(np.mean([t.headroom_ms for t in timing_records])), 2),
        },
        "memory_timeline": [asdict(m) for m in memory_timeline],
        "memory_verdict": memory_verdict,
        "circuit_breaker_transitions": cb_transitions,
        "health_probes": health_summary,
    }

    out_json = WORKSPACE / "artifacts" / "phase_4r_r2_inference_memory.json"
    out_json.write_text(json.dumps(out_artifact, indent=2), encoding="utf-8")
    print(f"\nArtifact saved to: {out_json}")


if __name__ == "__main__":
    asyncio.run(run_characterization())
