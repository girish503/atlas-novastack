#!/usr/bin/env python3
"""Execute the Dense Retrieval evaluation benchmark and BM25 failure-overlap analysis (Phase 3B).

Evaluates the dense semantic retrieval engine over the canonical SearchChunk corpus,
calculating Recall@1/3/5/10, MRR, and NDCG@10 across all 120 evaluation cases.
Compares performance directly against the Phase 3A BM25 baseline and performs
a comprehensive failure-overlap analysis (recovered, regressed, shared failures).

Writes the evaluation report artifact to:
    data/evaluation/novastack/dense_baseline.json

Usage:
    python scripts/evaluate_dense.py
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

from novastack.config import DATASET_VERSION, RANDOM_SEED
from novastack.dense import DenseConfig, DenseEncoder, DenseIndex


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


def evaluate_dense_corpus(
    index: DenseIndex,
    eval_cases: list[dict[str, Any]],
    bm25_report: dict[str, Any] | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """Run evaluation benchmark across all evaluation cases and compare with BM25."""
    case_results: list[dict[str, Any]] = []
    category_metrics: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    overall_metrics: dict[str, list[float]] = defaultdict(list)

    total_forbidden_leaks = 0

    # Index BM25 results by evaluation_id if available
    bm25_cases_by_id: dict[str, dict[str, Any]] = {}
    if bm25_report:
        for c in bm25_report.get("case_results", []):
            bm25_cases_by_id[c["evaluation_id"]] = c

    for case in eval_cases:
        eval_id = case["evaluation_id"]
        query = case["query"]
        category = case["query_category"]
        expected_docs = set(case.get("expected_document_ids", []))
        acceptable_docs = set(case.get("acceptable_document_ids", []))
        forbidden_docs = set(case.get("forbidden_document_ids", []))
        tenant_filter = case.get("tenant_id")

        # Execute search with pre-retrieval tenant filtering if case specifies tenant
        filters = {"tenant_id": tenant_filter} if tenant_filter else None
        retrieved = index.search(query=query, top_k=top_k, filters=filters)

        # Categorize retrieved chunks and evaluate relevance
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

        is_dense_failure = (is_positive_case and r_at_10 < 1.0) or any(
            c["category"] == "forbidden" for c in retrieved_candidates
        )

        # Comparison with BM25
        bm25_case = bm25_cases_by_id.get(eval_id)
        overlap_status = None
        if bm25_case:
            bm25_failed = bm25_case.get("is_failure", False)
            if bm25_failed and not is_dense_failure:
                overlap_status = "recovered"      # Dense succeeded where BM25 failed
            elif not bm25_failed and is_dense_failure:
                overlap_status = "regressed"      # BM25 succeeded but Dense failed
            elif bm25_failed and is_dense_failure:
                overlap_status = "both_failed"    # Both failed
            else:
                overlap_status = "both_succeeded" # Both succeeded

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
            "is_failure": is_dense_failure,
            "bm25_comparison": {
                "bm25_failed": bm25_case.get("is_failure", False) if bm25_case else None,
                "overlap_status": overlap_status,
                "bm25_recall_at_10": bm25_case.get("metrics", {}).get("recall_at_10") if bm25_case else None,
                "bm25_mrr": bm25_case.get("metrics", {}).get("mrr") if bm25_case else None,
            },
        })

    # Compile aggregate metrics
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

    # Compile Failure Overlap Summary
    overlap_counts = Counter(
        c["bm25_comparison"]["overlap_status"]
        for c in case_results
        if c["bm25_comparison"]["overlap_status"] is not None
    )

    bm25_total_failures = sum(1 for c in case_results if c["bm25_comparison"]["bm25_failed"] is True)
    dense_total_failures = sum(1 for c in case_results if c["is_failure"])

    failure_overlap_summary = {
        "bm25_failures_total": bm25_total_failures,
        "dense_failures_total": dense_total_failures,
        "bm25_only_failures_recovered_by_dense": overlap_counts.get("recovered", 0),
        "dense_only_failures_regressed_from_bm25": overlap_counts.get("regressed", 0),
        "shared_failures_both_failed": overlap_counts.get("both_failed", 0),
        "both_succeeded": overlap_counts.get("both_succeeded", 0),
    }

    return {
        "version": DATASET_VERSION,
        "seed": RANDOM_SEED,
        "configuration": asdict(index.config),
        "corpus_statistics": index.get_index_statistics(),
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

    embeddings_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "dense_embeddings.npz"
    metadata_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "dense_index_metadata.json"
    out_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "dense_baseline.json"

    for p, name in [
        (raw_path, "Raw records"),
        (docs_path, "Search documents"),
        (chunks_path, "Search chunks"),
        (eval_path, "Evaluation cases"),
        (bm25_path, "BM25 baseline report"),
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

    config = DenseConfig(model_name="BAAI/bge-small-en-v1.5")
    encoder = DenseEncoder(config=config, device="cpu")
    index = DenseIndex.load(
        chunks_path=chunks_path,
        embeddings_path=embeddings_path,
        metadata_path=metadata_path if metadata_path.exists() else None,
        encoder=encoder,
        config=config,
    )

    report_payload = evaluate_dense_corpus(
        index=index,
        eval_cases=eval_cases,
        bm25_report=bm25_report,
        top_k=10,
    )
    agg = report_payload["aggregate_metrics"]
    bm25_agg = bm25_report["aggregate_metrics"]
    overlap = report_payload["failure_overlap"]

    print("\n================================================================================")
    print("DENSE VS BM25 OVERALL RETRIEVAL BENCHMARK METRICS (101 Positive / 120 Total)")
    print("================================================================================")
    print(f"{'METRIC':<20} {'BM25 BASELINE':>15} {'DENSE BASELINE':>15} {'DELTA':>12}")
    print("--------------------------------------------------------------------------------")
    print(f"{'Recall@1':<20} {bm25_agg['recall_at_1']:>15.4f} {agg['recall_at_1']:>15.4f} {agg['recall_at_1'] - bm25_agg['recall_at_1']:>+12.4f}")
    print(f"{'Recall@3':<20} {bm25_agg['recall_at_3']:>15.4f} {agg['recall_at_3']:>15.4f} {agg['recall_at_3'] - bm25_agg['recall_at_3']:>+12.4f}")
    print(f"{'Recall@5':<20} {bm25_agg['recall_at_5']:>15.4f} {agg['recall_at_5']:>15.4f} {agg['recall_at_5'] - bm25_agg['recall_at_5']:>+12.4f}")
    print(f"{'Recall@10':<20} {bm25_agg['recall_at_10']:>15.4f} {agg['recall_at_10']:>15.4f} {agg['recall_at_10'] - bm25_agg['recall_at_10']:>+12.4f}")
    print(f"{'MRR':<20} {bm25_agg['mrr']:>15.4f} {agg['mrr']:>15.4f} {agg['mrr'] - bm25_agg['mrr']:>+12.4f}")
    print(f"{'NDCG@10':<20} {bm25_agg['ndcg_at_10']:>15.4f} {agg['ndcg_at_10']:>15.4f} {agg['ndcg_at_10'] - bm25_agg['ndcg_at_10']:>+12.4f}")
    print(f"{'Forbidden Leaks':<20} {bm25_agg['total_forbidden_candidate_occurrences']:>15} {agg['total_forbidden_candidate_occurrences']:>15} {agg['total_forbidden_candidate_occurrences'] - bm25_agg['total_forbidden_candidate_occurrences']:>+12}")
    print("================================================================================")

    print("\n================================================================================")
    print("FAILURE OVERLAP ANALYSIS")
    print("================================================================================")
    print(f"  BM25 Failures:                            {overlap['bm25_failures_total']:>4}")
    print(f"  Dense Failures:                           {overlap['dense_failures_total']:>4}")
    print(f"  BM25-only Failures (Recovered by Dense):  {overlap['bm25_only_failures_recovered_by_dense']:>4}")
    print(f"  Dense-only Failures (Regressed from BM25):{overlap['dense_only_failures_regressed_from_bm25']:>4}")
    print(f"  Shared Failures (Both Failed):            {overlap['shared_failures_both_failed']:>4}")
    print(f"  Both Succeeded:                           {overlap['both_succeeded']:>4}")
    print("================================================================================")

    print("\n================================================================================")
    print(f"{'CATEGORY':<28} {'CASES':>5} {'BM25 R@10':>10} {'DENSE R@10':>11} {'DELTA':>8} {'BM25 MRR':>10} {'DENSE MRR':>10}")
    print("================================================================================")
    bm25_cats = bm25_report.get("category_metrics", {})
    for cat, m in report_payload["category_metrics"].items():
        b_m = bm25_cats.get(cat, {})
        b_r10 = b_m.get("recall_at_10", 0.0)
        d_r10 = m["recall_at_10"]
        b_mrr = b_m.get("mrr", 0.0)
        d_mrr = m["mrr"]
        diff = d_r10 - b_r10
        print(
            f"{cat:<28} {m['cases_count']:>5} "
            f"{b_r10:>10.4f} {d_r10:>11.4f} {diff:>+8.4f} {b_mrr:>10.4f} {d_mrr:>10.4f}"
        )
    print("================================================================================")

    # Write evaluation output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report_payload, f, indent=2, ensure_ascii=False)

    print(f"\n[SUCCESS] Wrote evaluation report artifact to:")
    print(f"  {out_path.relative_to(_PROJECT_ROOT)}")

    # Verify immutability
    assert raw_hash_before == _compute_hash(raw_path), "Raw source records were mutated!"
    assert docs_hash_before == _compute_hash(docs_path), "Search documents were mutated!"
    assert chunks_hash_before == _compute_hash(chunks_path), "Search chunks were mutated!"
    assert eval_hash_before == _compute_hash(eval_path), "Evaluation cases were mutated!"

    print("[VERIFIED] All input artifacts remained 100% immutable.")


if __name__ == "__main__":
    main()
