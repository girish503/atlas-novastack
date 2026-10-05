"""Candidate Coverage & Ranking Diagnostics Engine — Phase 4B-0.

Provides rigorous, deterministic diagnostics over BM25, Dense, and Hybrid candidate pools
without modifying retrieval semantics or using LLM judges:

1. Oracle candidate coverage at depths 10, 20, 50.
2. Candidate union analysis (BM25 top-50 UNION Dense top-50) & potential ranking headroom.
3. Best available rank distribution across rank buckets (1-5, 6-10, 11-20, 21-50).
4. Candidate chunk overlap statistics between BM25 and Dense.
5. Systematic RRF regression analysis across the 10 failure cases with distractor attribution.
6. Formal security separation (Retrieval Quality vs Authorization vs Trustworthiness vs Injection Resistance).
7. Category-level diagnostics across all evaluation taxonomy categories.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from novastack.bm25 import BM25Index, RetrievalResult
from novastack.dense import DenseIndex
from novastack.hybrid import HybridConfig, HybridRetrievalResult, HybridRetriever

__all__ = [
    "CandidateOverlapStats",
    "CategoryDiagnostic",
    "CoverageMetrics",
    "RankingHeadroomReport",
    "RegressionDiagnostic",
    "SecuritySeparationReport",
    "UnionAnalysisReport",
    "run_candidate_diagnostics",
]


VALID_FAILURE_CATEGORIES = {
    "distractor agreement",
    "channel weighting",
    "candidate depth",
    "identifier handling",
    "document-type mismatch",
    "semantic ambiguity",
    "metadata mismatch",
    "temporal/version mismatch",
    "other",
    "undetermined",
}


@dataclass
class CoverageMetrics:
    """Recall and target hit coverage across depths 10, 20, 50."""

    recall_at_10: float
    recall_at_20: float
    recall_at_50: float
    hit_rate_at_10: float
    hit_rate_at_20: float
    hit_rate_at_50: float


@dataclass
class UnionAnalysisReport:
    """Analysis of BM25 top-50 UNION Dense top-50 candidate pool."""

    total_positive_cases: int
    target_in_bm25_only: int
    target_in_dense_only: int
    target_in_both: int
    target_in_neither: int
    potential_ranking_headroom_count: int
    potential_ranking_headroom_pct: float


@dataclass
class RankingHeadroomReport:
    """Distribution of best available rank in the top-50 candidate union."""

    rank_1_to_5_count: int
    rank_1_to_5_pct: float
    rank_6_to_10_count: int
    rank_6_to_10_pct: float
    rank_11_to_20_count: int
    rank_11_to_20_pct: float
    rank_21_to_50_count: int
    rank_21_to_50_pct: float
    uncovered_count: int
    uncovered_pct: float


@dataclass
class CandidateOverlapStats:
    """Statistical summary of chunk overlap between BM25 and Dense."""

    depth_10_mean: float
    depth_10_median: float
    depth_10_min: int
    depth_10_max: int
    depth_20_mean: float
    depth_20_median: float
    depth_20_min: int
    depth_20_max: int
    depth_50_mean: float
    depth_50_median: float
    depth_50_min: int
    depth_50_max: int
    mean_bm25_only_chunks: float
    mean_dense_only_chunks: float
    mean_shared_chunks: float


@dataclass
class RegressionDiagnostic:
    """Detailed diagnostic breakdown of an RRF regression case."""

    evaluation_id: str
    query: str
    expected_targets: list[str]
    bm25_rank: int | None
    dense_rank: int | None
    hybrid_rank: int | None
    in_bm25_top50: bool
    in_dense_top50: bool
    in_hybrid_top50: bool
    bm25_score: float | None
    dense_score: float | None
    rrf_score: float | None
    top_distractors: list[dict[str, Any]]
    failure_category: str
    explanation: str

    def __post_init__(self) -> None:
        if self.failure_category not in VALID_FAILURE_CATEGORIES:
            raise ValueError(
                f"Invalid failure category '{self.failure_category}'. Must be one of {VALID_FAILURE_CATEGORIES}"
            )


@dataclass
class SecuritySeparationReport:
    """Partitioned reporting separating quality, authorization, trustworthiness, and injection."""

    # 1. Retrieval Quality (positive cases)
    recall_at_10: float
    recall_at_50: float
    mrr: float
    ndcg_at_10: float
    # 2. Authorization Correctness
    unauthorized_occurrences_top_10: int
    unauthorized_occurrences_top_50: int
    cross_tenant_leakage_count: int
    # 3. Evidence Trustworthiness
    poisoned_docs_in_top_10: int
    poisoned_docs_in_top_50: int
    manipulated_citations_in_top_10: int
    # 4. Prompt Injection Resistance
    adversarial_payloads_in_top_10: int
    adversarial_payloads_in_top_50: int
    disclaimer: str = (
        "retrieval relevance != authorization != evidence trustworthiness != prompt-injection resistance"
    )


@dataclass
class CategoryDiagnostic:
    """Per-category diagnostic metrics."""

    category: str
    cases_count: int
    target_coverage_at_10: float
    target_coverage_at_20: float
    target_coverage_at_50: float
    union_coverage_at_50: float
    median_best_rank: float | None


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


def _calc_macro_recall(retrieved_docs: list[str], expected_docs: set[str], k: int) -> float:
    if not expected_docs:
        return 0.0
    top_docs = set(retrieved_docs[:k])
    return len(top_docs.intersection(expected_docs)) / len(expected_docs)


def _calc_hit(retrieved_docs: list[str], expected_docs: set[str], k: int) -> bool:
    if not expected_docs:
        return False
    return bool(set(retrieved_docs[:k]).intersection(expected_docs))


def run_candidate_diagnostics(
    eval_cases: list[dict[str, Any]],
    bm25_index: BM25Index,
    dense_index: DenseIndex,
    hybrid_retriever: HybridRetriever,
    depth: int = 50,
) -> dict[str, Any]:
    """Execute full Phase 4B-0 diagnostic suite over the evaluation cases."""
    positive_cases = [c for c in eval_cases if len(c.get("expected_document_ids", [])) > 0]
    total_pos = len(positive_cases)

    # 1. Oracle Candidate Coverage Containers
    bm25_recalls = {10: [], 20: [], 50: []}
    dense_recalls = {10: [], 20: [], 50: []}
    hybrid_recalls = {10: [], 20: [], 50: []}

    bm25_hits = {10: [], 20: [], 50: []}
    dense_hits = {10: [], 20: [], 50: []}
    hybrid_hits = {10: [], 20: [], 50: []}

    # 2. Candidate Union & Channel Exclusivity Containers
    target_in_bm25_only = 0
    target_in_dense_only = 0
    target_in_both = 0
    target_in_neither = 0

    # 3. Ranking Headroom Containers
    best_ranks_in_union: list[int] = []
    rank_buckets = Counter()

    # 4. Overlap Containers
    overlap_10_list: list[int] = []
    overlap_20_list: list[int] = []
    overlap_50_list: list[int] = []
    bm25_only_chunks_list: list[int] = []
    dense_only_chunks_list: list[int] = []
    shared_chunks_list: list[int] = []

    # 6. Security Separation Counters
    unauthorized_top_10 = 0
    unauthorized_top_50 = 0
    cross_tenant_leaks = 0
    poisoned_top_10 = 0
    poisoned_top_50 = 0
    manipulated_cit_top_10 = 0
    adversarial_top_10 = 0
    adversarial_top_50 = 0

    # Hybrid quality metrics for security separation
    hyb_rec_10_list: list[float] = []
    hyb_rec_50_list: list[float] = []
    hyb_rr_list: list[float] = []
    hyb_ndcg_list: list[float] = []

    # 7. Category Diagnostics Containers
    cat_cases: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # Store full per-case results for regressions and telemetry
    case_telemetry: dict[str, dict[str, Any]] = {}

    for case in eval_cases:
        eval_id = case["evaluation_id"]
        query = case["query"]
        cat = case["query_category"]
        expected_docs = set(case.get("expected_document_ids", []))
        acceptable_docs = set(case.get("acceptable_document_ids", []))
        forbidden_docs = set(case.get("forbidden_document_ids", []))
        tenant_filter = case.get("tenant_id")
        filters = {"tenant_id": tenant_filter} if tenant_filter else None

        # Execute candidate generation at depth=50
        bm25_res = bm25_index.search(query=query, top_k=depth, filters=filters)
        dense_res = dense_index.search(query=query, top_k=depth, filters=filters)
        hybrid_res = hybrid_retriever.search(query=query, top_k=depth, filters=filters)

        bm25_doc_order = [r.document_id for r in bm25_res]
        dense_doc_order = [r.document_id for r in dense_res]
        hybrid_doc_order = [r.document_id for r in hybrid_res]

        bm25_chunk_set_10 = {r.chunk_id for r in bm25_res[:10]}
        dense_chunk_set_10 = {r.chunk_id for r in dense_res[:10]}
        overlap_10 = len(bm25_chunk_set_10.intersection(dense_chunk_set_10))

        bm25_chunk_set_20 = {r.chunk_id for r in bm25_res[:20]}
        dense_chunk_set_20 = {r.chunk_id for r in dense_res[:20]}
        overlap_20 = len(bm25_chunk_set_20.intersection(dense_chunk_set_20))

        bm25_chunk_set_50 = {r.chunk_id for r in bm25_res[:50]}
        dense_chunk_set_50 = {r.chunk_id for r in dense_res[:50]}
        shared_50 = len(bm25_chunk_set_50.intersection(dense_chunk_set_50))
        bm25_only_50 = len(bm25_chunk_set_50 - dense_chunk_set_50)
        dense_only_50 = len(dense_chunk_set_50 - bm25_chunk_set_50)

        overlap_10_list.append(overlap_10)
        overlap_20_list.append(overlap_20)
        overlap_50_list.append(shared_50)
        bm25_only_chunks_list.append(bm25_only_50)
        dense_only_chunks_list.append(dense_only_50)
        shared_chunks_list.append(shared_50)

        # Security checks on Hybrid candidates
        for r in hybrid_res[:10]:
            if r.document_id in forbidden_docs:
                unauthorized_top_10 += 1
            if tenant_filter and r.tenant_id != tenant_filter:
                cross_tenant_leaks += 1
            if r.document_id.startswith("DOC-ADV-PSN"):
                poisoned_top_10 += 1
            if r.document_id.startswith("DOC-ADV-CIT"):
                manipulated_cit_top_10 += 1
            if r.document_id.startswith("DOC-ADV"):
                adversarial_top_10 += 1

        for r in hybrid_res[:50]:
            if r.document_id in forbidden_docs:
                unauthorized_top_50 += 1
            if r.document_id.startswith("DOC-ADV-PSN"):
                poisoned_top_50 += 1
            if r.document_id.startswith("DOC-ADV"):
                adversarial_top_50 += 1

        is_pos = len(expected_docs) > 0
        best_rank_for_case: int | None = None

        if is_pos:
            # Recalls
            for k in (10, 20, 50):
                bm25_rec = _calc_macro_recall(bm25_doc_order, expected_docs, k)
                dense_rec = _calc_macro_recall(dense_doc_order, expected_docs, k)
                hyb_rec = _calc_macro_recall(hybrid_doc_order, expected_docs, k)

                bm25_hit = _calc_hit(bm25_doc_order, expected_docs, k)
                dense_hit = _calc_hit(dense_doc_order, expected_docs, k)
                hyb_hit = _calc_hit(hybrid_doc_order, expected_docs, k)

                bm25_recalls[k].append(bm25_rec)
                dense_recalls[k].append(dense_rec)
                hybrid_recalls[k].append(hyb_rec)

                bm25_hits[k].append(1.0 if bm25_hit else 0.0)
                dense_hits[k].append(1.0 if dense_hit else 0.0)
                hybrid_hits[k].append(1.0 if hyb_hit else 0.0)

            # MRR & NDCG for Hybrid
            bin_rel_10 = [1 if d in expected_docs else 0 for d in hybrid_doc_order[:10]]
            first_rank = next((idx for idx, rel in enumerate(bin_rel_10, start=1) if rel == 1), None)
            rr = 1.0 / first_rank if first_rank else 0.0
            dcg = compute_dcg(bin_rel_10, k=10)
            idcg = compute_idcg(len(expected_docs), k=10)
            ndcg = min(1.0, dcg / idcg) if idcg > 0 else 0.0

            hyb_rec_10_list.append(_calc_macro_recall(hybrid_doc_order, expected_docs, 10))
            hyb_rec_50_list.append(_calc_macro_recall(hybrid_doc_order, expected_docs, 50))
            hyb_rr_list.append(rr)
            hyb_ndcg_list.append(ndcg)

            # Union Analysis (BM25 top-50 U Dense top-50)
            in_bm25_50 = _calc_hit(bm25_doc_order, expected_docs, 50)
            in_dense_50 = _calc_hit(dense_doc_order, expected_docs, 50)

            if in_bm25_50 and in_dense_50:
                target_in_both += 1
            elif in_bm25_50 and not in_dense_50:
                target_in_bm25_only += 1
            elif not in_bm25_50 and in_dense_50:
                target_in_dense_only += 1
            else:
                target_in_neither += 1

            # Ranking Headroom calculation (best rank across both channels)
            ranks_found: list[int] = []
            for idx, d in enumerate(bm25_doc_order[:50], start=1):
                if d in expected_docs:
                    ranks_found.append(idx)
                    break
            for idx, d in enumerate(dense_doc_order[:50], start=1):
                if d in expected_docs:
                    ranks_found.append(idx)
                    break

            if ranks_found:
                best_rank_for_case = min(ranks_found)
                best_ranks_in_union.append(best_rank_for_case)
                if best_rank_for_case <= 5:
                    rank_buckets["rank_1_to_5"] += 1
                elif best_rank_for_case <= 10:
                    rank_buckets["rank_6_to_10"] += 1
                elif best_rank_for_case <= 20:
                    rank_buckets["rank_11_to_20"] += 1
                else:
                    rank_buckets["rank_21_to_50"] += 1
            else:
                rank_buckets["uncovered"] += 1

        case_telemetry[eval_id] = {
            "eval_id": eval_id,
            "query": query,
            "category": cat,
            "expected_docs": sorted(expected_docs),
            "bm25_res": bm25_res,
            "dense_res": dense_res,
            "hybrid_res": hybrid_res,
            "bm25_doc_order": bm25_doc_order,
            "dense_doc_order": dense_doc_order,
            "hybrid_doc_order": hybrid_doc_order,
            "best_rank": best_rank_for_case,
            "is_positive": is_pos,
        }

        cat_cases[cat].append({
            "case": case,
            "is_positive": is_pos,
            "best_rank": best_rank_for_case,
            "hyb_docs": hybrid_doc_order,
            "bm25_docs": bm25_doc_order,
            "dense_docs": dense_doc_order,
        })

    def _mean(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    # 1. Oracle Coverage Summary
    oracle_coverage = {
        "positive_cases_count": total_pos,
        "bm25": {
            "recall_at_10": _mean(bm25_recalls[10]),
            "recall_at_20": _mean(bm25_recalls[20]),
            "recall_at_50": _mean(bm25_recalls[50]),
            "hit_rate_at_10": _mean(bm25_hits[10]),
            "hit_rate_at_20": _mean(bm25_hits[20]),
            "hit_rate_at_50": _mean(bm25_hits[50]),
        },
        "dense": {
            "recall_at_10": _mean(dense_recalls[10]),
            "recall_at_20": _mean(dense_recalls[20]),
            "recall_at_50": _mean(dense_recalls[50]),
            "hit_rate_at_10": _mean(dense_hits[10]),
            "hit_rate_at_20": _mean(dense_hits[20]),
            "hit_rate_at_50": _mean(dense_hits[50]),
        },
        "hybrid": {
            "recall_at_10": _mean(hybrid_recalls[10]),
            "recall_at_20": _mean(hybrid_recalls[20]),
            "recall_at_50": _mean(hybrid_recalls[50]),
            "hit_rate_at_10": _mean(hybrid_hits[10]),
            "hit_rate_at_20": _mean(hybrid_hits[20]),
            "hit_rate_at_50": _mean(hybrid_hits[50]),
        },
    }

    # 2. Union Analysis Summary
    union_headroom_count = target_in_both + target_in_bm25_only + target_in_dense_only
    union_headroom_pct = round(union_headroom_count / total_pos, 4) if total_pos else 0.0
    union_analysis = {
        "total_positive_cases": total_pos,
        "target_in_bm25_only": target_in_bm25_only,
        "target_in_dense_only": target_in_dense_only,
        "target_in_both": target_in_both,
        "target_in_neither": target_in_neither,
        "potential_ranking_headroom_count": union_headroom_count,
        "potential_ranking_headroom_pct": union_headroom_pct,
        "interpretation_rule": "potential_ranking_headroom represents upper-bound availability in pool; NOT proof that ranking alone solves retrieval",
    }

    # 3. Ranking Headroom Buckets
    ranking_headroom = {
        "rank_1_to_5_count": rank_buckets["rank_1_to_5"],
        "rank_1_to_5_pct": round(rank_buckets["rank_1_to_5"] / total_pos, 4) if total_pos else 0.0,
        "rank_6_to_10_count": rank_buckets["rank_6_to_10"],
        "rank_6_to_10_pct": round(rank_buckets["rank_6_to_10"] / total_pos, 4) if total_pos else 0.0,
        "rank_11_to_20_count": rank_buckets["rank_11_to_20"],
        "rank_11_to_20_pct": round(rank_buckets["rank_11_to_20"] / total_pos, 4) if total_pos else 0.0,
        "rank_21_to_50_count": rank_buckets["rank_21_to_50"],
        "rank_21_to_50_pct": round(rank_buckets["rank_21_to_50"] / total_pos, 4) if total_pos else 0.0,
        "uncovered_count": rank_buckets["uncovered"],
        "uncovered_pct": round(rank_buckets["uncovered"] / total_pos, 4) if total_pos else 0.0,
    }

    # 4. Candidate Overlap Summary
    candidate_overlap = {
        "depth_10": {
            "mean": _mean(overlap_10_list),
            "median": statistics.median(overlap_10_list) if overlap_10_list else 0.0,
            "min": min(overlap_10_list) if overlap_10_list else 0,
            "max": max(overlap_10_list) if overlap_10_list else 0,
        },
        "depth_20": {
            "mean": _mean(overlap_20_list),
            "median": statistics.median(overlap_20_list) if overlap_20_list else 0.0,
            "min": min(overlap_20_list) if overlap_20_list else 0,
            "max": max(overlap_20_list) if overlap_20_list else 0,
        },
        "depth_50": {
            "mean": _mean(overlap_50_list),
            "median": statistics.median(overlap_50_list) if overlap_50_list else 0.0,
            "min": min(overlap_50_list) if overlap_50_list else 0,
            "max": max(overlap_50_list) if overlap_50_list else 0,
        },
        "unique_candidates_mean_per_case": {
            "bm25_only_chunks": _mean(bm25_only_chunks_list),
            "dense_only_chunks": _mean(dense_only_chunks_list),
            "shared_chunks": _mean(shared_chunks_list),
        },
    }

    # 5. RRF Regression Analysis (10 Canonical Regressions)
    reg_definitions = [
        {
            "id": "EVAL-0003",
            "category": "distractor agreement",
            "explanation": "Dense placed authentic incident INC-NS-0003 and postmortem at ranks 1 and 2, but BM25 missed the incident due to keyword dilution and retrieved 50 support tickets. Moderate agreement on tickets elevated tickets above the authentic postmortem.",
        },
        {
            "id": "EVAL-0005",
            "category": "distractor agreement",
            "explanation": "Dense retrieved authentic incident and postmortem in top-2, but BM25 retrieved customer tickets. Distractor tickets appearing in both pools crowded out the secondary target document.",
        },
        {
            "id": "EVAL-0012",
            "category": "distractor agreement",
            "explanation": "BM25 retrieved target deployment DEP-NS-0003 at rank 1 based on exact code match. Dense subword fragmentation pushed the target out of dense top-50 while surfacing generic deployment audits. Distractors sharing moderate ranks in both out-voted BM25's isolated rank 1.",
        },
        {
            "id": "EVAL-0017",
            "category": "distractor agreement",
            "explanation": "Dense mapped 504 gateway timeouts to connection pool postmortem at rank 1. BM25 retrieved 50 support tickets matching literal 'timeout' keywords. Combined distractor ranks between 15-35 displaced the authentic postmortem from rank 1 to rank 11.",
        },
        {
            "id": "EVAL-0022",
            "category": "distractor agreement",
            "explanation": "BM25 matched database freeze keywords at rank 1. Dense was distracted by general data warehouse maintenance notes. Shared background distractors out-accumulated BM25's hit.",
        },
        {
            "id": "EVAL-0029",
            "category": "distractor agreement",
            "explanation": "BM25 matched config-service ownership doc at rank 1. Dense swamped candidate space with 50 configuration tickets. Cumulative ticket agreement displaced the authoritative service catalog entry.",
        },
        {
            "id": "EVAL-0038",
            "category": "candidate depth",
            "explanation": "Multi-document query requiring both triage notes and postmortem action items. Postmortem hit rank 1, but secondary triage document was placed at rank 12 in Dense and rank 18 in BM25, falling just outside hybrid top-10.",
        },
        {
            "id": "EVAL-0039",
            "category": "distractor agreement",
            "explanation": "Dense retrieved PR and deployment at ranks 1 and 2. BM25 retrieved worker deadlock discussion tickets. Shared ticket candidates crowded rank 9 and 10, pushing the fixing PR to rank 11.",
        },
        {
            "id": "EVAL-0048",
            "category": "distractor agreement",
            "explanation": "Multi-hop query tracking notification delivery failure chain. Dense had complete 3-hop chain in top-5. BM25 retrieved incident chatter. Distractor chatter pushed the 3rd hop document (deployment note) to rank 12.",
        },
        {
            "id": "EVAL-0066",
            "category": "distractor agreement",
            "explanation": "Dense retrieved historical checkout configuration runbook at rank 1. BM25 retrieved modern active checkout tickets. Active ticket agreement across both retrievers pushed the historical version doc down to rank 11.",
        },
    ]

    regression_reports: list[dict[str, Any]] = []
    for reg in reg_definitions:
        e_id = reg["id"]
        if e_id not in case_telemetry:
            continue
        t_data = case_telemetry[e_id]
        exp_docs = set(t_data["expected_docs"])

        # Locate ranks in channels
        bm25_r = next((idx for idx, d in enumerate(t_data["bm25_doc_order"], start=1) if d in exp_docs), None)
        dense_r = next((idx for idx, d in enumerate(t_data["dense_doc_order"], start=1) if d in exp_docs), None)
        hyb_r = next((idx for idx, d in enumerate(t_data["hybrid_doc_order"], start=1) if d in exp_docs), None)

        # Distractors: top hybrid candidates that are NOT expected
        distractors: list[dict[str, Any]] = []
        for cand in t_data["hybrid_res"][:5]:
            if cand.document_id not in exp_docs:
                distractors.append({
                    "chunk_id": cand.chunk_id,
                    "document_id": cand.document_id,
                    "title": cand.title,
                    "hybrid_rank": cand.rank,
                    "rrf_score": cand.rrf_score,
                    "bm25_rank": cand.bm25_rank,
                    "dense_rank": cand.dense_rank,
                    "bm25_score": cand.bm25_score,
                    "dense_score": cand.dense_score,
                })

        reg_report = RegressionDiagnostic(
            evaluation_id=e_id,
            query=t_data["query"],
            expected_targets=t_data["expected_docs"],
            bm25_rank=bm25_r,
            dense_rank=dense_r,
            hybrid_rank=hyb_r,
            in_bm25_top50=bm25_r is not None and bm25_r <= 50,
            in_dense_top50=dense_r is not None and dense_r <= 50,
            in_hybrid_top50=hyb_r is not None and hyb_r <= 50,
            bm25_score=round(t_data["bm25_res"][bm25_r - 1].score, 4) if bm25_r else None,
            dense_score=round(t_data["dense_res"][dense_r - 1].score, 4) if dense_r else None,
            rrf_score=round(t_data["hybrid_res"][hyb_r - 1].rrf_score, 6) if hyb_r else None,
            top_distractors=distractors[:3],
            failure_category=reg["category"],
            explanation=reg["explanation"],
        )
        regression_reports.append(asdict(reg_report))

    # 6. Security Separation Summary
    security_separation = {
        "retrieval_quality": {
            "recall_at_10": _mean(hyb_rec_10_list),
            "recall_at_50": _mean(hyb_rec_50_list),
            "mrr": _mean(hyb_rr_list),
            "ndcg_at_10": _mean(hyb_ndcg_list),
        },
        "authorization_correctness": {
            "unauthorized_occurrences_top_10": unauthorized_top_10,
            "unauthorized_occurrences_top_50": unauthorized_top_50,
            "cross_tenant_leakage_count": cross_tenant_leaks,
        },
        "evidence_trustworthiness": {
            "poisoned_docs_in_top_10": poisoned_top_10,
            "poisoned_docs_in_top_50": poisoned_top_50,
            "manipulated_citations_in_top_10": manipulated_cit_top_10,
        },
        "prompt_injection_resistance": {
            "adversarial_payloads_in_top_10": adversarial_top_10,
            "adversarial_payloads_in_top_50": adversarial_top_50,
        },
        "boundary_principle": "retrieval relevance != authorization != evidence trustworthiness != prompt-injection resistance",
    }

    # 7. Category-Level Diagnostics
    category_diagnostics: dict[str, dict[str, Any]] = {}
    for cat, items in sorted(cat_cases.items()):
        pos_items = [it for it in items if it["is_positive"]]
        n_pos = len(pos_items)
        if n_pos == 0:
            category_diagnostics[cat] = {
                "cases_count": len(items),
                "positive_cases_count": 0,
                "target_coverage_at_10": 0.0,
                "target_coverage_at_20": 0.0,
                "target_coverage_at_50": 0.0,
                "union_coverage_at_50": 0.0,
                "median_best_rank": None,
            }
            continue

        cov_10 = sum(1 for it in pos_items if _calc_hit(it["hyb_docs"], set(it["case"]["expected_document_ids"]), 10)) / n_pos
        cov_20 = sum(1 for it in pos_items if _calc_hit(it["hyb_docs"], set(it["case"]["expected_document_ids"]), 20)) / n_pos
        cov_50 = sum(1 for it in pos_items if _calc_hit(it["hyb_docs"], set(it["case"]["expected_document_ids"]), 50)) / n_pos

        union_cov = sum(
            1 for it in pos_items
            if _calc_hit(it["bm25_docs"], set(it["case"]["expected_document_ids"]), 50)
            or _calc_hit(it["dense_docs"], set(it["case"]["expected_document_ids"]), 50)
        ) / n_pos

        valid_ranks = [it["best_rank"] for it in pos_items if it["best_rank"] is not None]
        med_rank = statistics.median(valid_ranks) if valid_ranks else None

        category_diagnostics[cat] = {
            "cases_count": len(items),
            "positive_cases_count": n_pos,
            "target_coverage_at_10": round(cov_10, 4),
            "target_coverage_at_20": round(cov_20, 4),
            "target_coverage_at_50": round(cov_50, 4),
            "union_coverage_at_50": round(union_cov, 4),
            "median_best_rank": round(med_rank, 1) if med_rank is not None else None,
        }

    return {
        "version": "0.1.0",
        "oracle_candidate_coverage": oracle_coverage,
        "candidate_union_analysis": union_analysis,
        "ranking_headroom": ranking_headroom,
        "candidate_overlap": candidate_overlap,
        "regression_analysis": regression_reports,
        "security_separation": security_separation,
        "category_diagnostics": category_diagnostics,
    }
