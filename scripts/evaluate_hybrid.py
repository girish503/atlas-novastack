#!/usr/bin/env python3
"""Execute the Hybrid Retrieval (BM25 + Dense + RRF) evaluation benchmark (Phase 4A).

Evaluates the HybridRetriever over the canonical SearchChunk corpus using
Reciprocal Rank Fusion (default k=60), calculating Recall@1/3/5/10, MRR, NDCG@10
across all 120 evaluation cases. Compares performance directly against both
BM25 (Phase 3A) and Dense (Phase 3B) baselines, generating a three-way comparison
table, failure-overlap breakdown, category analysis, and case study telemetry.

Writes the evaluation report artifact to:
    data/evaluation/novastack/hybrid_baseline.json

Usage:
    python scripts/evaluate_hybrid.py
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

# Ensure src/ is importable when running as a standalone script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.bm25 import BM25Config, BM25Index
from novastack.config import DATASET_VERSION, RANDOM_SEED
from novastack.dense import DenseConfig, DenseEncoder, DenseIndex
from novastack.hybrid import HybridConfig, HybridRetriever


def _compute_hash(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def compute_dcg(relevances: list[int], k: int = 10) -> float:
    """Compute Discounted Cumulative Gain at rank k."""
    dcg = 0.0
    for i, rel in enumerate(relevances[:k], start=1):
        if rel > 0:
            dcg += rel / math.log2(i + 1)
    return dcg


def compute_idcg(num_relevant: int, k: int = 10) -> float:
    """Compute Ideal Discounted Cumulative Gain at rank k."""
    ideal_count = min(num_relevant, k)
    if ideal_count <= 0:
        return 0.0
    idcg = 0.0
    for i in range(1, ideal_count + 1):
        idcg += 1.0 / math.log2(i + 1)
    return idcg


def evaluate_hybrid_corpus(
    retriever: HybridRetriever,
    eval_cases: list[dict[str, Any]],
    bm25_report: dict[str, Any],
    dense_report: dict[str, Any],
    top_k: int = 10,
) -> dict[str, Any]:
    """Run hybrid evaluation benchmark across all 120 evaluation cases."""
    case_results: list[dict[str, Any]] = []
    category_metrics: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    overall_metrics: dict[str, list[float]] = defaultdict(list)

    total_forbidden_leaks = 0

    bm25_cases = {c["evaluation_id"]: c for c in bm25_report.get("case_results", [])}
    dense_cases = {c["evaluation_id"]: c for c in dense_report.get("case_results", [])}

    for case in eval_cases:
        eval_id = case["evaluation_id"]
        query = case["query"]
        category = case["query_category"]
        expected_docs = set(case.get("expected_document_ids", []))
        acceptable_docs = set(case.get("acceptable_document_ids", []))
        forbidden_docs = set(case.get("forbidden_document_ids", []))
        tenant_filter = case.get("tenant_id")

        filters = {"tenant_id": tenant_filter} if tenant_filter else None
        retrieved = retriever.search(query=query, top_k=top_k, filters=filters)

        retrieved_candidates = []
        retrieved_doc_ids = []
        binary_relevances = []
        first_relevant_rank = None

        for r in retrieved:
            retrieved_doc_ids.append(r.document_id)
            if r.document_id in expected_docs:
                cand_cat = "expected"
                rel = 1
            elif r.document_id in acceptable_docs:
                cand_cat = "acceptable"
                rel = 0
            elif r.document_id in forbidden_docs:
                cand_cat = "forbidden"
                rel = 0
                total_forbidden_leaks += 1
            else:
                cand_cat = "unrelated"
                rel = 0

            binary_relevances.append(rel)
            if rel == 1 and first_relevant_rank is None:
                first_relevant_rank = r.rank

            retrieved_candidates.append({
                "rank": r.rank,
                "score": r.score,
                "rrf_score": r.rrf_score,
                "bm25_rank": r.bm25_rank,
                "dense_rank": r.dense_rank,
                "bm25_score": r.bm25_score,
                "dense_score": r.dense_score,
                "chunk_id": r.chunk_id,
                "document_id": r.document_id,
                "title": r.title,
                "source_type": r.source_type,
                "tenant_id": r.tenant_id,
                "category": cand_cat,
            })

        # Calculate metrics
        num_expected = len(expected_docs)
        is_positive_case = num_expected > 0

        if is_positive_case:
            docs_at_1 = set(retrieved_doc_ids[:1])
            docs_at_3 = set(retrieved_doc_ids[:3])
            docs_at_5 = set(retrieved_doc_ids[:5])
            docs_at_10 = set(retrieved_doc_ids[:10])

            r_at_1 = len(docs_at_1.intersection(expected_docs)) / num_expected
            r_at_3 = len(docs_at_3.intersection(expected_docs)) / num_expected
            r_at_5 = len(docs_at_5.intersection(expected_docs)) / num_expected
            r_at_10 = len(docs_at_10.intersection(expected_docs)) / num_expected
            rr = 1.0 / first_relevant_rank if first_relevant_rank else 0.0

            dcg = compute_dcg(binary_relevances, k=10)
            idcg = compute_idcg(num_expected, k=10)
            ndcg = min(1.0, dcg / idcg) if idcg > 0 else 0.0

            overall_metrics["recall_at_1"].append(r_at_1)
            overall_metrics["recall_at_3"].append(r_at_3)
            overall_metrics["recall_at_5"].append(r_at_5)
            overall_metrics["recall_at_10"].append(r_at_10)
            overall_metrics["mrr"].append(rr)
            overall_metrics["ndcg_at_10"].append(ndcg)

            category_metrics[category]["recall_at_1"].append(r_at_1)
            category_metrics[category]["recall_at_3"].append(r_at_3)
            category_metrics[category]["recall_at_5"].append(r_at_5)
            category_metrics[category]["recall_at_10"].append(r_at_10)
            category_metrics[category]["mrr"].append(rr)
            category_metrics[category]["ndcg_at_10"].append(ndcg)
        else:
            r_at_1 = 0.0
            r_at_3 = 0.0
            r_at_5 = 0.0
            r_at_10 = 0.0
            rr = 0.0
            ndcg = 0.0

        is_hybrid_failure = (is_positive_case and r_at_10 < 1.0) or any(
            c["category"] == "forbidden" for c in retrieved_candidates
        )

        bm25_c = bm25_cases.get(eval_id)
        dense_c = dense_cases.get(eval_id)

        bm25_failed = bm25_c.get("is_failure", False) if bm25_c else None
        dense_failed = dense_c.get("is_failure", False) if dense_c else None

        # Determine exact failure-overlap classification
        if not is_positive_case:
            failure_group = "unclassified_non_retrieval"
        elif not is_hybrid_failure:
            if bm25_failed and dense_failed:
                failure_group = "hybrid_exclusive_win"
            elif bm25_failed and not dense_failed:
                failure_group = "recovered_bm25_failure"
            elif not bm25_failed and dense_failed:
                failure_group = "recovered_dense_failure"
            else:
                failure_group = "all_three_succeeded"
        else:
            if not bm25_failed or not dense_failed:
                failure_group = "hybrid_regression"
            else:
                failure_group = "shared_failure_all_three"

        case_results.append({
            "evaluation_id": eval_id,
            "query": query,
            "query_category": category,
            "difficulty": case.get("difficulty"),
            "expected_access": case.get("expected_access"),
            "expected_document_ids": sorted(expected_docs),
            "acceptable_document_ids": sorted(acceptable_docs),
            "forbidden_document_ids": sorted(forbidden_docs),
            "metrics": {
                "recall_at_1": round(r_at_1, 4),
                "recall_at_3": round(r_at_3, 4),
                "recall_at_5": round(r_at_5, 4),
                "recall_at_10": round(r_at_10, 4),
                "mrr": round(rr, 4),
                "ndcg_at_10": round(ndcg, 4),
            },
            "retrieved_candidates": retrieved_candidates,
            "is_failure": is_hybrid_failure,
            "failure_overlap_group": failure_group,
            "channel_comparison": {
                "bm25_failed": bm25_failed,
                "dense_failed": dense_failed,
                "hybrid_failed": is_hybrid_failure,
                "bm25_recall_at_10": bm25_c.get("metrics", {}).get("recall_at_10") if bm25_c else None,
                "dense_recall_at_10": dense_c.get("metrics", {}).get("recall_at_10") if dense_c else None,
                "hybrid_recall_at_10": round(r_at_10, 4),
                "bm25_mrr": bm25_c.get("metrics", {}).get("mrr") if bm25_c else None,
                "dense_mrr": dense_c.get("metrics", {}).get("mrr") if dense_c else None,
                "hybrid_mrr": round(rr, 4),
            },
        })

    def _mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    aggregated_overall = {
        "positive_eval_cases_evaluated": len(overall_metrics["recall_at_1"]),
        "total_eval_cases_evaluated": len(eval_cases),
        "recall_at_1": _mean(overall_metrics["recall_at_1"]),
        "recall_at_3": _mean(overall_metrics["recall_at_3"]),
        "recall_at_5": _mean(overall_metrics["recall_at_5"]),
        "recall_at_10": _mean(overall_metrics["recall_at_10"]),
        "mrr": _mean(overall_metrics["mrr"]),
        "ndcg_at_10": _mean(overall_metrics["ndcg_at_10"]),
        "total_forbidden_candidate_occurrences": total_forbidden_leaks,
    }

    aggregated_by_category: dict[str, dict[str, Any]] = {}
    for cat, m_dict in sorted(category_metrics.items()):
        aggregated_by_category[cat] = {
            "cases_count": len(m_dict["recall_at_1"]),
            "recall_at_1": _mean(m_dict["recall_at_1"]),
            "recall_at_3": _mean(m_dict["recall_at_3"]),
            "recall_at_5": _mean(m_dict["recall_at_5"]),
            "recall_at_10": _mean(m_dict["recall_at_10"]),
            "mrr": _mean(m_dict["mrr"]),
            "ndcg_at_10": _mean(m_dict["ndcg_at_10"]),
        }

    # Compile failure overlap counts across all 101 positive cases
    pos_cases = [c for c in case_results if c["failure_overlap_group"] != "unclassified_non_retrieval"]
    overlap_counts = Counter(c["failure_overlap_group"] for c in pos_cases)

    failure_overlap_summary = {
        "positive_cases_count": len(pos_cases),
        "bm25_failures_total": sum(1 for c in pos_cases if c["channel_comparison"]["bm25_failed"] is True),
        "dense_failures_total": sum(1 for c in pos_cases if c["channel_comparison"]["dense_failed"] is True),
        "hybrid_failures_total": sum(1 for c in pos_cases if c["channel_comparison"]["hybrid_failed"] is True),
        "hybrid_exclusive_wins": overlap_counts.get("hybrid_exclusive_win", 0),
        "recovered_bm25_failures": overlap_counts.get("recovered_bm25_failure", 0),
        "recovered_dense_failures": overlap_counts.get("recovered_dense_failure", 0),
        "hybrid_regressions": overlap_counts.get("hybrid_regression", 0),
        "shared_failures_all_three": overlap_counts.get("shared_failure_all_three", 0),
        "all_three_succeeded": overlap_counts.get("all_three_succeeded", 0),
    }

    return {
        "version": DATASET_VERSION,
        "seed": RANDOM_SEED,
        "configuration": asdict(retriever.config),
        "aggregate_metrics": aggregated_overall,
        "category_metrics": aggregated_by_category,
        "failure_overlap": failure_overlap_summary,
        "case_results": case_results,
    }


def main() -> None:
    raw_path = _PROJECT_ROOT / "data" / "raw" / "novastack" / "source_records.json"
    docs_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_documents.json"
    chunks_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_chunks.json"
    eval_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
    bm25_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "bm25_baseline.json"
    dense_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "dense_baseline.json"

    embeddings_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "dense_embeddings.npz"
    metadata_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "dense_index_metadata.json"
    out_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "hybrid_baseline.json"

    for p, name in [
        (raw_path, "Raw records"),
        (docs_path, "Search documents"),
        (chunks_path, "Search chunks"),
        (eval_path, "Evaluation cases"),
        (bm25_path, "BM25 baseline report"),
        (dense_path, "Dense baseline report"),
        (embeddings_path, "Dense embeddings"),
    ]:
        if not p.exists():
            raise FileNotFoundError(f"{name} not found at {p}")

    raw_hash_before = _compute_hash(raw_path)
    docs_hash_before = _compute_hash(docs_path)
    chunks_hash_before = _compute_hash(chunks_path)
    eval_hash_before = _compute_hash(eval_path)

    with open(eval_path, "r", encoding="utf-8") as f:
        eval_cases = json.load(f)["evaluation_cases"]

    with open(bm25_path, "r", encoding="utf-8") as f:
        bm25_report = json.load(f)

    with open(dense_path, "r", encoding="utf-8") as f:
        dense_report = json.load(f)

    # Initialize BM25 and Dense indexes
    bm25_config = BM25Config()
    bm25_index = BM25Index.build_index(chunks_path, config=bm25_config)

    dense_config = DenseConfig()
    encoder = DenseEncoder(config=dense_config, device="cpu")
    dense_index = DenseIndex.load(
        chunks_path=chunks_path,
        embeddings_path=embeddings_path,
        metadata_path=metadata_path if metadata_path.exists() else None,
        encoder=encoder,
        config=dense_config,
    )

    hybrid_config = HybridConfig(rrf_k=60, retriever_top_k=50)
    retriever = HybridRetriever(bm25_index=bm25_index, dense_index=dense_index, config=hybrid_config)

    report_payload = evaluate_hybrid_corpus(
        retriever=retriever,
        eval_cases=eval_cases,
        bm25_report=bm25_report,
        dense_report=dense_report,
        top_k=10,
    )

    agg = report_payload["aggregate_metrics"]
    bm25_agg = bm25_report["aggregate_metrics"]
    dense_agg = dense_report["aggregate_metrics"]
    overlap = report_payload["failure_overlap"]

    print("\n==========================================================================================")
    print("THREE-WAY RETRIEVAL BENCHMARK COMPARISON (101 Positive Cases / 120 Total)")
    print("==========================================================================================")
    print(f"{'METRIC':<18} {'BM25':>10} {'DENSE':>10} {'HYBRID(RRF)':>12} {'HYB-BM25':>12} {'HYB-DENSE':>12}")
    print("------------------------------------------------------------------------------------------")
    for m_key, m_name in [
        ("recall_at_1", "Recall@1"),
        ("recall_at_3", "Recall@3"),
        ("recall_at_5", "Recall@5"),
        ("recall_at_10", "Recall@10"),
        ("mrr", "MRR"),
        ("ndcg_at_10", "NDCG@10"),
    ]:
        b_val = bm25_agg[m_key]
        d_val = dense_agg[m_key]
        h_val = agg[m_key]
        print(
            f"{m_name:<18} {b_val:>10.4f} {d_val:>10.4f} {h_val:>12.4f} "
            f"{h_val - b_val:>+12.4f} {h_val - d_val:>+12.4f}"
        )
    b_leaks = bm25_agg["total_forbidden_candidate_occurrences"]
    d_leaks = dense_agg["total_forbidden_candidate_occurrences"]
    h_leaks = agg["total_forbidden_candidate_occurrences"]
    print(f"{'Forbidden Leaks':<18} {b_leaks:>10} {d_leaks:>10} {h_leaks:>12} {h_leaks - b_leaks:>+12} {h_leaks - d_leaks:>+12}")
    print("==========================================================================================")

    print("\n==========================================================================================")
    print("HYBRID FAILURE OVERLAP BREAKDOWN (101 Positive Cases)")
    print("==========================================================================================")
    print(f"  BM25 Total Failures:                                  {overlap['bm25_failures_total']:>4}")
    print(f"  Dense Total Failures:                                 {overlap['dense_failures_total']:>4}")
    print(f"  Hybrid Total Failures:                                {overlap['hybrid_failures_total']:>4}")
    print("------------------------------------------------------------------------------------------")
    print(f"  1. Hybrid Exclusive Wins (BOTH failed, Hybrid won):   {overlap['hybrid_exclusive_wins']:>4}")
    print(f"  2. Recovered BM25-only Failures:                      {overlap['recovered_bm25_failures']:>4}")
    print(f"  3. Recovered Dense-only Failures:                     {overlap['recovered_dense_failures']:>4}")
    print(f"  4. Hybrid Regressions (Lost ground vs BM25/Dense):    {overlap['hybrid_regressions']:>4}")
    print(f"  5. Shared Failures Across All Three:                  {overlap['shared_failures_all_three']:>4}")
    print(f"  6. All Three Succeeded:                               {overlap['all_three_succeeded']:>4}")
    print("==========================================================================================")

    print("\n==========================================================================================")
    print(f"{'CATEGORY':<25} {'CASES':>5} {'BM25 R10':>9} {'DENSE R10':>10} {'HYB R10':>9} {'DIFF_BM':>8} {'DIFF_DN':>8}")
    print("==========================================================================================")
    bm25_cats = bm25_report.get("category_metrics", {})
    dense_cats = dense_report.get("category_metrics", {})
    for cat, m in report_payload["category_metrics"].items():
        b_r10 = bm25_cats.get(cat, {}).get("recall_at_10", 0.0)
        d_r10 = dense_cats.get(cat, {}).get("recall_at_10", 0.0)
        h_r10 = m["recall_at_10"]
        print(
            f"{cat:<25} {m['cases_count']:>5} "
            f"{b_r10:>9.4f} {d_r10:>10.4f} {h_r10:>9.4f} "
            f"{h_r10 - b_r10:>+8.4f} {h_r10 - d_r10:>+8.4f}"
        )
    print("==========================================================================================")

    # Write evaluation output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report_payload, f, indent=2, ensure_ascii=False)

    print(f"\n[SUCCESS] Wrote hybrid evaluation report artifact to:")
    print(f"  {out_path.relative_to(_PROJECT_ROOT)}")

    # Verify immutability
    assert raw_hash_before == _compute_hash(raw_path), "Raw source records were mutated!"
    assert docs_hash_before == _compute_hash(docs_path), "Search documents were mutated!"
    assert chunks_hash_before == _compute_hash(chunks_path), "Search chunks were mutated!"
    assert eval_hash_before == _compute_hash(eval_path), "Evaluation cases were mutated!"

    print("[VERIFIED] All input artifacts remained 100% immutable.")


if __name__ == "__main__":
    main()
