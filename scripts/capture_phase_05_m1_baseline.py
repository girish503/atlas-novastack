"""Capture Immutable Baseline Artifact for ATLAS 0.5 Milestone M1.

Reads Phase 4D-2 relational retrieval benchmark results (0.4.14-rc1 production baseline),
computes overall IR metrics, relational slice metrics (multi_hop + ownership),
temporal slice metrics (temporal + version + stale_information), latency distributions,
and zero-trust security audits, writing artifacts/phase_05_m1_baseline.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def calculate_slice_metrics(cases: list[dict[str, Any]], field_name: str) -> dict[str, float]:
    """Calculate average metrics for a subset of cases using metric dictionaries."""
    if not cases:
        return {}
    
    # We aggregate ranks from case telemetry
    total = len(cases)
    r1, r3, r5, r10, r20, r50 = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    h1, h3, h5, h10, h20, h50 = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    rr_sum = 0.0
    dcg_sum = 0.0
    forbidden_top10 = 0

    import math

    for c in cases:
        ranks = c.get(field_name, {})
        rank = ranks.get("reranked_rank")  # Rank in downstream reranker
        forb = c.get("forbidden_leaks_top10", 0)
        forbidden_top10 += forb

        if rank is not None and rank > 0:
            if rank <= 1:
                r1 += 1.0; h1 += 1.0
            if rank <= 3:
                r3 += 1.0; h3 += 1.0
            if rank <= 5:
                r5 += 1.0; h5 += 1.0
            if rank <= 10:
                r10 += 1.0; h10 += 1.0
                dcg_sum += 1.0 / math.log2(rank + 1)
            if rank <= 20:
                r20 += 1.0; h20 += 1.0
            if rank <= 50:
                r50 += 1.0; h50 += 1.0
            rr_sum += 1.0 / rank

    return {
        "case_count": total,
        "recall_at_1": round(r1 / total, 4),
        "hit_at_1": round(h1 / total, 4),
        "recall_at_3": round(r3 / total, 4),
        "hit_at_3": round(h3 / total, 4),
        "recall_at_5": round(r5 / total, 4),
        "hit_at_5": round(h5 / total, 4),
        "recall_at_10": round(r10 / total, 4),
        "hit_at_10": round(h10 / total, 4),
        "recall_at_20": round(r20 / total, 4),
        "hit_at_20": round(h20 / total, 4),
        "recall_at_50": round(r50 / total, 4),
        "hit_at_50": round(h50 / total, 4),
        "mrr": round(rr_sum / total, 4),
        "ndcg_at_10": round(dcg_sum / total, 4),
        "forbidden_leaks_top10": forbidden_top10,
    }


def main() -> None:
    eval_path = ROOT / "data" / "evaluation" / "novastack" / "phase_4d2_relational_retrieval.json"
    with open(eval_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cases = data.get("cases", [])
    summary = data.get("summary", {})
    cat_metrics = data.get("category_metrics", {})
    latency = data.get("latency_profile", {})
    sec_audit = data.get("security_audit", {})

    # Categorize cases
    multi_hop_cases = [c for c in cases if c.get("category") == "multi_hop"]
    ownership_cases = [c for c in cases if c.get("category") == "ownership"]
    all_relational_cases = multi_hop_cases + ownership_cases

    temporal_cases = [c for c in cases if c.get("category") == "temporal"]
    version_cases = [c for c in cases if c.get("category") == "version"]
    stale_cases = [c for c in cases if c.get("category") == "stale_information"]
    all_temporal_lifecycle_cases = temporal_cases + version_cases + stale_cases

    # Compute baseline metrics for relational slices (using combined_ranks or baseline_ranks)
    # In 0.4.14 baseline, the production pipeline runs combined (Hybrid + Structured d=1)
    baseline_artifact = {
        "metadata": {
            "milestone": "ATLAS 0.5-M1",
            "artifact_type": "baseline_characterization",
            "production_version": "0.4.14-rc1",
            "freeze_state": "IMMUTABLE_LOCKED",
            "description": "Baseline performance envelope of ATLAS 0.4.14-rc1 before Milestone M1 modifications",
            "total_benchmark_cases": len(cases),
            "traversal_depth_baseline": 1,
            "path_decay_gamma_baseline": 1.0,
            "temporal_filtering_baseline": "naive_prefix_and_recency",
        },
        "overall_metrics": {
            "baseline_hybrid_reranked": summary.get("baseline_reranked", {}),
            "structured_alone_reranked": summary.get("structured_reranked", {}),
            "combined_production_reranked": summary.get("combined_reranked", {}),
        },
        "challenge_slices": {
            "relational_multi_hop_slice": {
                "case_count": len(multi_hop_cases),
                "evaluation_ids": [c["evaluation_id"] for c in multi_hop_cases],
                "baseline_hybrid_metrics": cat_metrics.get("multi_hop", {}).get("baseline_reranked", {}),
                "combined_production_metrics": cat_metrics.get("multi_hop", {}).get("combined_reranked", {}),
            },
            "relational_ownership_slice": {
                "case_count": len(ownership_cases),
                "evaluation_ids": [c["evaluation_id"] for c in ownership_cases],
                "baseline_hybrid_metrics": cat_metrics.get("ownership", {}).get("baseline_reranked", {}),
                "combined_production_metrics": cat_metrics.get("ownership", {}).get("combined_reranked", {}),
            },
            "relational_total_challenge_slice": {
                "case_count": len(all_relational_cases),
                "evaluation_ids": [c["evaluation_id"] for c in all_relational_cases],
                "baseline_hybrid_metrics": calculate_slice_metrics(all_relational_cases, "baseline_ranks"),
                "combined_production_metrics": calculate_slice_metrics(all_relational_cases, "combined_ranks"),
            },
            "temporal_point_and_interval_slice": {
                "case_count": len(temporal_cases),
                "evaluation_ids": [c["evaluation_id"] for c in temporal_cases],
                "baseline_hybrid_metrics": cat_metrics.get("temporal", {}).get("baseline_reranked", {}),
                "combined_production_metrics": cat_metrics.get("temporal", {}).get("combined_reranked", {}),
            },
            "version_slice": {
                "case_count": len(version_cases),
                "evaluation_ids": [c["evaluation_id"] for c in version_cases],
                "baseline_hybrid_metrics": cat_metrics.get("version", {}).get("baseline_reranked", {}),
                "combined_production_metrics": cat_metrics.get("version", {}).get("combined_reranked", {}),
            },
            "stale_information_slice": {
                "case_count": len(stale_cases),
                "evaluation_ids": [c["evaluation_id"] for c in stale_cases],
                "baseline_hybrid_metrics": cat_metrics.get("stale_information", {}).get("baseline_reranked", {}),
                "combined_production_metrics": cat_metrics.get("stale_information", {}).get("combined_reranked", {}),
            },
            "temporal_and_lifecycle_total_slice": {
                "case_count": len(all_temporal_lifecycle_cases),
                "evaluation_ids": [c["evaluation_id"] for c in all_temporal_lifecycle_cases],
                "baseline_hybrid_metrics": calculate_slice_metrics(all_temporal_lifecycle_cases, "baseline_ranks"),
                "combined_production_metrics": calculate_slice_metrics(all_temporal_lifecycle_cases, "combined_ranks"),
            },
        },
        "all_category_metrics": cat_metrics,
        "security_audit": {
            "total_forbidden_leaks_top10": sec_audit.get("total_forbidden_leaks_top10", 0),
            "total_poisoned_docs_top10": sec_audit.get("total_poisoned_docs_top10", 0),
            "cross_tenant_violations": sec_audit.get("cross_tenant_violations", 0),
        },
        "latency_profile": latency,
        "case_telemetry_summary": [
            {
                "evaluation_id": c["evaluation_id"],
                "query": c["query"],
                "category": c["category"],
                "expected_document_ids": c["expected_document_ids"],
                "baseline_rank": c["baseline_ranks"]["reranked_rank"],
                "combined_rank": c["combined_ranks"]["reranked_rank"],
                "failure_classification": c["failure_classification"],
            }
            for c in cases
        ],
    }

    out_file = ROOT / "artifacts" / "phase_05_m1_baseline.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(baseline_artifact, f, indent=2)

    print(f"Captured immutable baseline artifact to {out_file} ({out_file.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
