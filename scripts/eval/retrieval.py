"""Canonical Retrieval Evaluator — Baseline (H5) vs Candidate (H5.1)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

WORKSPACE = Path(__file__).resolve().parent.parent.parent


def _nested_metric(block: Dict[str, Any], *path: str, default: Any = None) -> Any:
    current: Any = block
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def summarize_h5_1_results(results: Dict[str, Any]) -> Dict[str, Any]:
    """Map the H5.1 experiment artifact onto the canonical comparison schema."""
    entity = results.get("entity_metrics", {})
    retrieval = results.get("retrieval_metrics", {})
    security = results.get("security_metrics", {})
    control_entity = entity.get("control", {})
    treatment_entity = entity.get("treatment", {})

    return {
        "benchmark_name": "ATLAS Canonical Retrieval Benchmark",
        "evaluation_scope": "OFFLINE_RETRIEVAL_ONLY",
        "baseline_architecture": "B4 + H1(H5) + H3 (0.4.14-rc1)",
        "candidate_architecture": "B4 + H1(H5.1) + H3 (0.4.14-rc1+h5.1)",
        "dataset": "data/evaluation/novastack/evaluation_cases.json",
        "dataset_summary": results.get("dataset_summary", {}),
        "metrics": {
            "entity_resolution": {
                "baseline_expected_entity_recall": control_entity.get("expected_entity_recall"),
                "candidate_expected_entity_recall": treatment_entity.get("expected_entity_recall"),
                "baseline_wrong_entities": control_entity.get("wrong_entity_cases"),
                "candidate_wrong_entities": treatment_entity.get("wrong_entity_cases"),
                "baseline_missing_cases": control_entity.get("missing_entity_cases"),
                "candidate_missing_cases": treatment_entity.get("missing_entity_cases"),
            },
            "information_retrieval": {
                "baseline_recall_at_1": _nested_metric(retrieval, "overall_positive", "control", "metrics", "recall_at_1"),
                "candidate_recall_at_1": _nested_metric(retrieval, "overall_positive", "treatment", "metrics", "recall_at_1"),
                "baseline_recall_at_3": _nested_metric(retrieval, "overall_positive", "control", "metrics", "recall_at_3"),
                "candidate_recall_at_3": _nested_metric(retrieval, "overall_positive", "treatment", "metrics", "recall_at_3"),
                "baseline_recall_at_5": _nested_metric(retrieval, "overall_positive", "control", "metrics", "recall_at_5"),
                "candidate_recall_at_5": _nested_metric(retrieval, "overall_positive", "treatment", "metrics", "recall_at_5"),
                "baseline_positive_recall_at_10": _nested_metric(retrieval, "overall_positive", "control", "metrics", "recall_at_10"),
                "candidate_positive_recall_at_10": _nested_metric(retrieval, "overall_positive", "treatment", "metrics", "recall_at_10"),
                "baseline_precision_at_10": _nested_metric(retrieval, "overall_positive", "control", "metrics", "precision_at_10"),
                "candidate_precision_at_10": _nested_metric(retrieval, "overall_positive", "treatment", "metrics", "precision_at_10"),
                "baseline_hit_at_10": _nested_metric(retrieval, "overall_positive", "control", "metrics", "hit_at_10"),
                "candidate_hit_at_10": _nested_metric(retrieval, "overall_positive", "treatment", "metrics", "hit_at_10"),
                "baseline_mrr": _nested_metric(retrieval, "overall_positive", "control", "metrics", "mrr"),
                "candidate_mrr": _nested_metric(retrieval, "overall_positive", "treatment", "metrics", "mrr"),
                "baseline_ndcg_at_10": _nested_metric(retrieval, "overall_positive", "control", "metrics", "ndcg_at_10"),
                "candidate_ndcg_at_10": _nested_metric(retrieval, "overall_positive", "treatment", "metrics", "ndcg_at_10"),
                "baseline_multi_aspect_recall_at_10": _nested_metric(retrieval, "multi_aspect", "control", "metrics", "recall_at_10"),
                "candidate_multi_aspect_recall_at_10": _nested_metric(retrieval, "multi_aspect", "treatment", "metrics", "recall_at_10"),
                "baseline_h2_recall_at_10": _nested_metric(retrieval, "h2_slice", "control", "metrics", "recall_at_10"),
                "candidate_h2_recall_at_10": _nested_metric(retrieval, "h2_slice", "treatment", "metrics", "recall_at_10"),
            },
            "security": {
                "cross_tenant_leaks": security.get("cross_tenant_top10_leaks", 0),
                "unauthorized_retrievals": security.get("forbidden_top10_leaks", 0),
                "negative_case_leaks": security.get("negative_case_leaks", 0),
            },
        },
        "gates": results.get("gate_results", {}),
        "recommendation": results.get("recommendation"),
        "runtime_fingerprint": results.get("runtime_fingerprint") or results.get("determinism"),
        "metadata": results.get("metadata", {}),
        "h5_1_is_production_default": False,
        "canary_traffic_percent": 0.0,
    }


def evaluate_retrieval_gates(summary: Dict[str, Any]) -> Dict[str, Any]:
    metrics = summary.get("metrics", {})
    ir = metrics.get("information_retrieval", {})
    entity = metrics.get("entity_resolution", {})
    security = metrics.get("security", {})
    experiment_gates = summary.get("gates", {})
    candidate_r10 = ir.get("candidate_positive_recall_at_10") or 0.0
    candidate_mrr = ir.get("candidate_mrr") or 0.0
    wrong = entity.get("candidate_wrong_entities")
    leaks = (security.get("cross_tenant_leaks") or 0) + (security.get("unauthorized_retrievals") or 0)
    passed = bool(experiment_gates) and all(bool(v) for v in experiment_gates.values()) and leaks == 0
    if wrong is not None and wrong > 1:
        passed = False
    return {
        "pass": passed,
        "project_defined_thresholds": {
            "candidate_positive_recall_at_10_min": 0.66,
            "candidate_mrr_reported": candidate_mrr,
            "candidate_recall_at_10_reported": candidate_r10,
            "wrong_entities_max": 1,
            "security_leaks_max": 0,
        },
        "experiment_gates": experiment_gates,
    }


def run_canonical_retrieval_evaluation(
    data_dir: Path | str = "data",
    artifacts_dir: Path | str = "artifacts",
    write_artifact: bool = True,
    reuse_artifact: bool | None = None,
) -> Dict[str, Any]:
    """Execute canonical side-by-side retrieval evaluation comparing Baseline vs Candidate."""
    artifacts_path = WORKSPACE / Path(artifacts_dir)
    existing = artifacts_path / "ret_eval_08_h5_1_results.json"
    if reuse_artifact is None:
        reuse_artifact = os.environ.get("ATLAS_EVAL_REUSE_ARTIFACTS", "").lower() in {"1", "true", "yes"}

    if reuse_artifact and existing.exists():
        with open(existing, "r", encoding="utf-8") as handle:
            results = json.load(handle)
        results.setdefault("metadata", {})["reused_existing_artifact"] = True
    else:
        from scripts.ret_eval_08_h5_1_experiment import run_controlled_h5_1_experiment

        results = run_controlled_h5_1_experiment(
            data_dir=data_dir,
            artifacts_dir=artifacts_dir,
            write_artifact=write_artifact,
        )

    canonical_summary = summarize_h5_1_results(results)
    canonical_summary["gate_evaluation"] = evaluate_retrieval_gates(canonical_summary)

    if write_artifact:
        out_file = artifacts_path / "canonical_retrieval_benchmark.json"
        artifacts_path.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as handle:
            json.dump(canonical_summary, handle, indent=2)

    return canonical_summary
