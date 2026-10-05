#!/usr/bin/env python3
"""Execute the BM25 retrieval evaluation benchmark against NovaStack evaluation cases (Phase 3A).

Evaluates the BM25 lexical retrieval engine over the canonical SearchChunk corpus,
computing Recall@1, Recall@3, Recall@5, Recall@10, MRR, and NDCG@10 across all 120
evaluation cases and reporting category-level breakdowns and mechanical failure diagnostics.

Writes the evaluation report artifact to:
    data/evaluation/novastack/bm25_baseline.json

Usage:
    python scripts/evaluate_bm25.py
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

from novastack.bm25 import BM25Config, BM25Index, tokenize
from novastack.config import DATASET_VERSION, RANDOM_SEED


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


def evaluate_bm25_corpus(
    index: BM25Index,
    eval_cases: list[dict[str, Any]],
    top_k: int = 10,
) -> dict[str, Any]:
    """Run evaluation benchmark across all evaluation cases."""
    case_results: list[dict[str, Any]] = []
    category_metrics: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    overall_metrics: dict[str, list[float]] = defaultdict(list)

    total_forbidden_leaks = 0

    # Build document text cache for mechanical failure analysis
    doc_text_tokens: dict[str, set[str]] = defaultdict(set)
    for c in index.chunks:
        tokens = set(tokenize(c.title)) | set(tokenize(c.text))
        doc_text_tokens[c.document_id].update(tokens)

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
                rel = 0  # Expected docs drive primary binary relevance; acceptable tracked separately
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
            # For cases with 0 expected documents (abstain/deny cases)
            r_at_1 = 0.0
            r_at_3 = 0.0
            r_at_5 = 0.0
            r_at_10 = 0.0
            rr = 0.0
            ndcg = 0.0

        is_failure = (is_positive_case and r_at_10 < 1.0) or any(
            c["category"] == "forbidden" for c in retrieved_candidates
        )

        # Mechanical Failure Attribution
        diagnosis = None
        if is_failure:
            query_tokens = set(tokenize(query))
            missing_expected = sorted(expected_docs - set(retrieved_doc_ids[:10]))
            
            # Check lexical overlap with missing expected documents
            terms_overlap = {}
            for doc_id in missing_expected:
                expected_tokens = doc_text_tokens.get(doc_id, set())
                common = query_tokens.intersection(expected_tokens)
                terms_overlap[doc_id] = {
                    "common_tokens": sorted(common),
                    "has_overlap": len(common) > 0,
                }

            # Determine mechanical failure mode
            if any(c["category"] == "forbidden" for c in retrieved_candidates):
                mode = "forbidden_document_leakage"
            elif not any(terms_overlap[d]["has_overlap"] for d in missing_expected):
                mode = "zero_lexical_overlap"
            elif all(len(terms_overlap[d]["common_tokens"]) <= 1 for d in missing_expected):
                mode = "weak_lexical_signal"
            else:
                mode = "semantic_distraction_or_competing_matches"

            diagnosis = {
                "failure_mode": mode,
                "missing_expected_docs": missing_expected,
                "terms_overlap_in_missing_docs": terms_overlap,
                "first_relevant_rank": first_relevant_rank,
            }

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
            "is_failure": is_failure,
            "failure_diagnosis": diagnosis,
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

    return {
        "version": DATASET_VERSION,
        "seed": RANDOM_SEED,
        "configuration": asdict(index.config),
        "corpus_statistics": index.get_corpus_statistics(),
        "aggregate_metrics": aggregated_overall,
        "category_metrics": aggregated_by_category,
        "case_results": case_results,
    }


def main() -> None:
    raw_path = _PROJECT_ROOT / "data" / "raw" / "novastack" / "source_records.json"
    docs_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_documents.json"
    chunks_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_chunks.json"
    eval_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
    out_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "bm25_baseline.json"

    for p, name in [
        (raw_path, "Raw records"),
        (docs_path, "Search documents"),
        (chunks_path, "Search chunks"),
        (eval_path, "Evaluation cases"),
    ]:
        if not p.exists():
            print(f"[ERROR] {name} not found at {p}")
            sys.exit(1)

    raw_hash_before = _compute_hash(raw_path)
    docs_hash_before = _compute_hash(docs_path)
    chunks_hash_before = _compute_hash(chunks_path)
    eval_hash_before = _compute_hash(eval_path)

    print("================================================================================")
    print("ATLAS BM25 Lexical Retrieval Baseline Benchmark (Phase 3A)")
    print("================================================================================")
    print(f"Loading chunks from:     {chunks_path.relative_to(_PROJECT_ROOT)}")
    print(f"Loading eval cases from: {eval_path.relative_to(_PROJECT_ROOT)}")

    with open(eval_path, "r", encoding="utf-8") as f:
        eval_cases = json.load(f)["evaluation_cases"]

    config = BM25Config(k1=1.5, b=0.75, title_weight=1.0, text_weight=1.0)
    index = BM25Index.build_index(chunks_path, config=config)

    report_payload = evaluate_bm25_corpus(index=index, eval_cases=eval_cases, top_k=10)
    agg = report_payload["aggregate_metrics"]

    print("\n================================================================================")
    print("OVERALL RETRIEVAL BENCHMARK METRICS (101 Positive Cases / 120 Total Cases)")
    print("================================================================================")
    print(f"  Recall@1:       {agg['recall_at_1']:>7.4f}")
    print(f"  Recall@3:       {agg['recall_at_3']:>7.4f}")
    print(f"  Recall@5:       {agg['recall_at_5']:>7.4f}")
    print(f"  Recall@10:      {agg['recall_at_10']:>7.4f}")
    print(f"  MRR:            {agg['mrr']:>7.4f}")
    print(f"  NDCG@10:        {agg['ndcg_at_10']:>7.4f}")
    print(f"  Forbidden Leaks:{agg['total_forbidden_candidate_occurrences']:>7}")

    print("\n================================================================================")
    print(f"{'CATEGORY':<30} {'CASES':>5} {'R@1':>7} {'R@5':>7} {'R@10':>7} {'MRR':>7} {'NDCG@10':>8}")
    print("================================================================================")
    for cat, m in report_payload["category_metrics"].items():
        print(
            f"{cat:<30} {m['cases_count']:>5} "
            f"{m['recall_at_1']:>7.4f} {m['recall_at_5']:>7.4f} {m['recall_at_10']:>7.4f} "
            f"{m['mrr']:>7.4f} {m['ndcg_at_10']:>8.4f}"
        )
    print("================================================================================")

    # Mechanical Failure Analysis Summary
    failures = [c for c in report_payload["case_results"] if c["is_failure"]]
    failure_modes = Counter(c["failure_diagnosis"]["failure_mode"] for c in failures if c.get("failure_diagnosis"))
    print("\nMECHANICAL FAILURE ATTRIBUTION:")
    print(f"  Total Failed Cases (Recall@10 < 1.0 or forbidden leak): {len(failures)}")
    for mode, count in failure_modes.most_common():
        print(f"    - {mode:<45} {count:>3} cases")

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
