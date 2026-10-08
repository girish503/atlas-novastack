"""Canonical Generation & Citation Evaluator.

Default scope is the certified Phase 05 M8 offline artifact (120 cases,
gemma3:1b via inference service). This is NOT live HTTP production traffic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

WORKSPACE = Path(__file__).resolve().parent.parent.parent
M8_ARTIFACT = WORKSPACE / "artifacts" / "phase_05_m8_benchmark_results.json"


def _load_m8_cases() -> List[Dict[str, Any]]:
    if not M8_ARTIFACT.exists():
        return []
    with open(M8_ARTIFACT, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("case_results") or payload.get("results") or []
    return []


def _normalize_citations(raw: Any) -> List[Dict[str, Any]]:
    citations: List[Dict[str, Any]] = []
    for item in raw or []:
        status = str(item.get("status", "INVALID")).upper()
        citations.append(
            {
                "raw_tag": item.get("raw_tag") or f"[{item.get('evidence_id', '')}]",
                "evidence_id": item.get("evidence_id"),
                "document_id": item.get("document_id"),
                "chunk_id": item.get("chunk_id"),
                "status": status,
                "is_unauthorized": status == "UNAUTHORIZED" or bool(item.get("is_unauthorized", False)),
            }
        )
    return citations


def run_canonical_generation_evaluation(
    data_dir: Path | str = "data",
    artifacts_dir: Path | str = "artifacts",
    use_live_inference: bool = False,
    write_artifact: bool = True,
) -> Dict[str, Any]:
    """Score grounded generation from the certified M8 artifact unless live inference is requested."""
    from scripts.eval.datasets import load_canonical_dataset
    from scripts.eval.metrics import compute_generation_metrics

    canonical_cases = load_canonical_dataset()
    m8_cases = _load_m8_cases()
    case_map = {item.get("evaluation_id"): item for item in m8_cases}

    if use_live_inference:
        raise RuntimeError(
            "Live generation evaluation is not enabled in the canonical harness. "
            "Use the certified Phase 05 M8 artifact or a dedicated live inference job."
        )

    results: List[Dict[str, Any]] = []
    missing_ids: List[str] = []
    input_tokens = 0
    output_tokens = 0
    model_calls = 0
    latencies: List[float] = []

    for case in canonical_cases:
        cached = case_map.get(case.evaluation_id)
        if not cached:
            missing_ids.append(case.evaluation_id)
            results.append(
                {
                    "evaluation_id": case.evaluation_id,
                    "query": case.query,
                    "category": case.category,
                    "is_positive": case.is_positive,
                    "is_negative": case.is_negative,
                    "answer_status": "not_evaluated",
                    "answer_text": "",
                    "citations": [],
                    "latency_ms": None,
                    "source": "missing_from_certified_artifact",
                }
            )
            continue

        status = cached.get("answer_status", "abstained")
        citations = _normalize_citations(cached.get("citations"))
        latency = cached.get("generation_latency_ms", cached.get("latency_ms"))
        if latency is not None:
            latencies.append(float(latency))
        input_tokens += int(cached.get("input_tokens") or 0)
        output_tokens += int(cached.get("output_tokens") or 0)
        model_calls += 1
        results.append(
            {
                "evaluation_id": case.evaluation_id,
                "query": case.query,
                "category": case.category,
                "is_positive": case.is_positive,
                "is_negative": case.is_negative,
                "answer_status": status,
                "answer_text": cached.get("answer_text", ""),
                "citations": citations,
                "latency_ms": latency,
                "source": "phase_05_m8_certified_artifact",
                "model": (cached.get("diagnostics") or {}).get("model"),
            }
        )

    scored = [item for item in results if item["answer_status"] != "not_evaluated"]
    metrics = compute_generation_metrics(scored)
    yield_ok = metrics.get("positive_answer_yield", 0.0) >= 0.70
    safety_ok = metrics.get("negative_safety_rate", 0.0) >= 1.0
    cite_ok = metrics.get("citation_precision", 0.0) >= 1.0
    unauth_ok = metrics.get("unauthorized_citations", 0) == 0
    fab_ok = metrics.get("fabricated_citations", 0) == 0

    output = {
        "benchmark_name": "ATLAS Canonical Grounded Generation Benchmark",
        "evaluation_scope": "OFFLINE_CERTIFIED_ARTIFACT",
        "artifact_path": str(M8_ARTIFACT.relative_to(WORKSPACE)) if M8_ARTIFACT.exists() else None,
        "model": "gemma3:1b",
        "evaluator": "deterministic_citation_validator_plus_m8_labels",
        "llm_as_judge": False,
        "total_cases_evaluated": len(scored),
        "missing_from_artifact": missing_ids,
        "metrics": metrics,
        "cost_accounting": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "model_calls": model_calls,
            "estimated_monetary_cost": None,
            "monetary_cost_status": "UNAVAILABLE",
        },
        "generation_latency_ms_from_artifact": {
            "count": len(latencies),
            "mean": (sum(latencies) / len(latencies)) if latencies else None,
        },
        "results_summary": {
            "positive_yield_pct": f"{metrics.get('positive_answer_yield', 0.0) * 100:.2f}%",
            "negative_safety_pct": f"{metrics.get('negative_safety_rate', 0.0) * 100:.2f}%",
            "citation_precision_pct": f"{metrics.get('citation_precision', 0.0) * 100:.2f}%",
            "citation_completeness_pct": f"{metrics.get('citation_completeness', 0.0) * 100:.2f}%",
        },
        "gate_evaluation": {
            "pass": yield_ok and safety_ok and cite_ok and unauth_ok and fab_ok and not missing_ids,
            "positive_yield_ge_70": yield_ok,
            "negative_safety_100": safety_ok,
            "citation_precision_100": cite_ok,
            "zero_unauthorized_citations": unauth_ok,
            "zero_fabricated_citations": fab_ok,
            "complete_artifact_coverage": not missing_ids,
        },
    }

    if write_artifact:
        out_dir = WORKSPACE / Path(artifacts_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "canonical_generation_benchmark.json", "w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2)
        citation_audit = {
            "citation_precision": metrics.get("citation_precision"),
            "citation_completeness": metrics.get("citation_completeness"),
            "unauthorized_citations": metrics.get("unauthorized_citations"),
            "fabricated_citations": metrics.get("fabricated_citations"),
            "evaluation_scope": "OFFLINE_CERTIFIED_ARTIFACT",
        }
        with open(out_dir / "canonical_citation_audit.json", "w", encoding="utf-8") as handle:
            json.dump(citation_audit, handle, indent=2)

    return output
