"""Candidate Depth & Fusion Ablation Engine — Phase 4D-1.

Implements controlled ablations across candidate depths (50, 75, 100) and
fusion mechanisms (Standard RRF, CombMAX-RRF, Round-Robin Interleaving, Single-Channel)
to test:
- Hypothesis A: Increasing candidate depth recovers genuine targets within top-100.
- Hypothesis B: Standard RRF can suppress single-channel candidates when channels disagree.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from novastack.bm25 import RetrievalResult
from novastack.hybrid import HybridRetrievalResult

__all__ = [
    "AblationCaseResult",
    "CandidateDepthConfig",
    "compute_ir_metrics",
    "fuse_interleaving",
    "fuse_rrf_max",
    "fuse_rrf_sum",
    "fuse_single_channel",
]


@dataclass
class CandidateDepthConfig:
    """Configuration for candidate depth and fusion experiments."""

    depths: tuple[int, ...] = (50, 75, 100)
    rrf_k: int = 60
    bm25_weight: float = 1.0
    dense_weight: float = 1.0


@dataclass
class AblationCaseResult:
    """Detailed evaluation result for a single case under a specific configuration."""

    evaluation_id: str
    query_category: str
    configuration: str
    depth: int
    retrieved_doc_ids: list[str]
    metrics: dict[str, Any]
    target_in_pool: bool
    target_rank_in_pool: int | None
    raw_results: list[HybridRetrievalResult] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialize case result without bulky raw objects."""
        return {
            "evaluation_id": self.evaluation_id,
            "query_category": self.query_category,
            "configuration": self.configuration,
            "depth": self.depth,
            "retrieved_doc_ids": self.retrieved_doc_ids,
            "metrics": self.metrics,
            "target_in_pool": self.target_in_pool,
            "target_rank_in_pool": self.target_rank_in_pool,
        }


def compute_ir_metrics(
    retrieved_doc_ids: list[str],
    expected_doc_ids: list[str],
    acceptable_doc_ids: list[str] | None = None,
    forbidden_doc_ids: list[str] | None = None,
    k_values: Sequence[int] = (1, 3, 5, 10, 20, 50, 100),
) -> dict[str, Any]:
    """Compute standard IR metrics: Recall@k, MRR, NDCG@10, HitRate@k, and forbidden leaks."""
    exp_set = set(expected_doc_ids)
    acc_set = set(acceptable_doc_ids or [])
    forb_set = set(forbidden_doc_ids or [])

    metrics: dict[str, Any] = {}

    if not exp_set:
        # Negative / Authorization Denied case
        for k in k_values:
            metrics[f"recall_at_{k}"] = 0.0
            metrics[f"hit_at_{k}"] = 0.0
        metrics["mrr"] = 0.0
        metrics["ndcg_at_10"] = 0.0
        metrics["forbidden_leaks_top10"] = sum(1 for d in retrieved_doc_ids[:10] if d in forb_set)
        metrics["forbidden_in_pool"] = sum(1 for d in retrieved_doc_ids if d in forb_set)
        return metrics

    total_expected = len(exp_set)

    for k in k_values:
        sub = retrieved_doc_ids[:k]
        hits = sum(1 for d in set(sub) if d in exp_set)
        metrics[f"recall_at_{k}"] = round(hits / total_expected, 6)
        metrics[f"hit_at_{k}"] = 1.0 if hits > 0 else 0.0

    # Mean Reciprocal Rank (MRR) based on first expected target hit
    first_rank = next(
        (idx for idx, d in enumerate(retrieved_doc_ids, start=1) if d in exp_set),
        None,
    )
    metrics["mrr"] = round(1.0 / first_rank, 6) if first_rank is not None else 0.0

    # NDCG@10 with graded relevance: 2.0 for expected, 1.0 for acceptable
    dcg = 0.0
    for idx, d in enumerate(retrieved_doc_ids[:10], start=1):
        if d in exp_set:
            rel = 2.0
        elif d in acc_set:
            rel = 1.0
        else:
            rel = 0.0
        dcg += rel / math.log2(idx + 1)

    ideal_rels = sorted(
        [2.0] * min(len(exp_set), 10)
        + [1.0] * max(0, min(len(acc_set) - len(exp_set), 10 - len(exp_set))),
        reverse=True,
    )
    idcg = sum(rel / math.log2(idx + 1) for idx, rel in enumerate(ideal_rels[:10], start=1))
    metrics["ndcg_at_10"] = round(dcg / idcg, 6) if idcg > 0 else 0.0

    # Security audits
    metrics["forbidden_leaks_top10"] = sum(1 for d in retrieved_doc_ids[:10] if d in forb_set)
    metrics["forbidden_in_pool"] = sum(1 for d in retrieved_doc_ids if d in forb_set)

    return metrics


def fuse_rrf_sum(
    bm25_results: list[RetrievalResult],
    dense_results: list[RetrievalResult],
    top_k: int = 50,
    k: int = 60,
    bm25_weight: float = 1.0,
    dense_weight: float = 1.0,
    deduplicate_docs: bool = True,
) -> list[HybridRetrievalResult]:
    """Standard Reciprocal Rank Fusion summing reciprocal ranks across channels.

    Formula: score(c) = sum_m w_m / (k + rank_m(c))
    """
    candidate_map: dict[str, dict[str, Any]] = {}

    for r in bm25_results:
        rrf_contrib = bm25_weight / (k + r.rank)
        candidate_map[r.chunk_id] = {
            "result": r,
            "rrf_score": rrf_contrib,
            "bm25_contrib": rrf_contrib,
            "dense_contrib": 0.0,
            "bm25_rank": r.rank,
            "bm25_score": r.score,
            "dense_rank": None,
            "dense_score": None,
        }

    for r in dense_results:
        rrf_contrib = dense_weight / (k + r.rank)
        if r.chunk_id in candidate_map:
            candidate_map[r.chunk_id]["rrf_score"] += rrf_contrib
            candidate_map[r.chunk_id]["dense_contrib"] = rrf_contrib
            candidate_map[r.chunk_id]["dense_rank"] = r.rank
            candidate_map[r.chunk_id]["dense_score"] = r.score
        else:
            candidate_map[r.chunk_id] = {
                "result": r,
                "rrf_score": rrf_contrib,
                "bm25_contrib": 0.0,
                "dense_contrib": rrf_contrib,
                "bm25_rank": None,
                "bm25_score": None,
                "dense_rank": r.rank,
                "dense_score": r.score,
            }

    sorted_candidates = sorted(
        candidate_map.values(),
        key=lambda x: (-x["rrf_score"], x["result"].chunk_id),
    )

    final_results: list[HybridRetrievalResult] = []
    seen_docs: set[str] = set()

    for cand in sorted_candidates:
        base = cand["result"]
        if deduplicate_docs:
            if base.document_id in seen_docs:
                continue
            seen_docs.add(base.document_id)

        final_results.append(
            HybridRetrievalResult(
                chunk_id=base.chunk_id,
                document_id=base.document_id,
                score=round(cand["rrf_score"], 6),
                rank=len(final_results) + 1,
                title=base.title,
                text_preview=base.text_preview,
                tenant_id=base.tenant_id,
                source_type=base.source_type,
                department=base.department,
                classification=base.classification,
                authority_level=base.authority_level,
                status=base.status,
                version=base.version,
                created_at=base.created_at,
                source_entity_id=base.source_entity_id,
                related_entity_ids=list(base.related_entity_ids),
                bm25_rank=cand["bm25_rank"],
                dense_rank=cand["dense_rank"],
                bm25_score=cand["bm25_score"],
                dense_score=cand["dense_score"],
                rrf_score=round(cand["rrf_score"], 6),
            )
        )
        if len(final_results) >= top_k:
            break

    return final_results


def fuse_rrf_max(
    bm25_results: list[RetrievalResult],
    dense_results: list[RetrievalResult],
    top_k: int = 50,
    k: int = 60,
    bm25_weight: float = 1.0,
    dense_weight: float = 1.0,
    deduplicate_docs: bool = True,
) -> list[HybridRetrievalResult]:
    """CombMAX Reciprocal Rank Fusion taking max reciprocal rank across channels.

    Formula: score(c) = max_m w_m / (k + rank_m(c))
    Guarantees that a candidate with a top single-channel rank cannot be demoted
    below candidates whose best single-channel ranks are strictly worse.
    """
    candidate_map: dict[str, dict[str, Any]] = {}

    for r in bm25_results:
        rrf_contrib = bm25_weight / (k + r.rank)
        candidate_map[r.chunk_id] = {
            "result": r,
            "rrf_score": rrf_contrib,
            "bm25_contrib": rrf_contrib,
            "dense_contrib": 0.0,
            "bm25_rank": r.rank,
            "bm25_score": r.score,
            "dense_rank": None,
            "dense_score": None,
        }

    for r in dense_results:
        rrf_contrib = dense_weight / (k + r.rank)
        if r.chunk_id in candidate_map:
            candidate_map[r.chunk_id]["dense_contrib"] = rrf_contrib
            candidate_map[r.chunk_id]["rrf_score"] = max(
                candidate_map[r.chunk_id]["bm25_contrib"],
                rrf_contrib,
            )
            candidate_map[r.chunk_id]["dense_rank"] = r.rank
            candidate_map[r.chunk_id]["dense_score"] = r.score
        else:
            candidate_map[r.chunk_id] = {
                "result": r,
                "rrf_score": rrf_contrib,
                "bm25_contrib": 0.0,
                "dense_contrib": rrf_contrib,
                "bm25_rank": None,
                "bm25_score": None,
                "dense_rank": r.rank,
                "dense_score": r.score,
            }

    sorted_candidates = sorted(
        candidate_map.values(),
        key=lambda x: (-x["rrf_score"], x["result"].chunk_id),
    )

    final_results: list[HybridRetrievalResult] = []
    seen_docs: set[str] = set()

    for cand in sorted_candidates:
        base = cand["result"]
        if deduplicate_docs:
            if base.document_id in seen_docs:
                continue
            seen_docs.add(base.document_id)

        final_results.append(
            HybridRetrievalResult(
                chunk_id=base.chunk_id,
                document_id=base.document_id,
                score=round(cand["rrf_score"], 6),
                rank=len(final_results) + 1,
                title=base.title,
                text_preview=base.text_preview,
                tenant_id=base.tenant_id,
                source_type=base.source_type,
                department=base.department,
                classification=base.classification,
                authority_level=base.authority_level,
                status=base.status,
                version=base.version,
                created_at=base.created_at,
                source_entity_id=base.source_entity_id,
                related_entity_ids=list(base.related_entity_ids),
                bm25_rank=cand["bm25_rank"],
                dense_rank=cand["dense_rank"],
                bm25_score=cand["bm25_score"],
                dense_score=cand["dense_score"],
                rrf_score=round(cand["rrf_score"], 6),
            )
        )
        if len(final_results) >= top_k:
            break

    return final_results


def fuse_interleaving(
    bm25_results: list[RetrievalResult],
    dense_results: list[RetrievalResult],
    top_k: int = 50,
    k: int = 60,
    deduplicate_docs: bool = True,
) -> list[HybridRetrievalResult]:
    """Round-Robin Interleaving (CombUnion) across BM25 and Dense channels.

    Alternates selection between BM25 and Dense channels:
    Turn 1: BM25 candidate #1
    Turn 2: Dense candidate #1
    Turn 3: BM25 candidate #2
    Turn 4: Dense candidate #2
    ...
    Deduplicating documents until top_k unique documents are collected.
    """
    # Group results by unique document preserving channel rank order
    bm_best_by_doc: list[RetrievalResult] = []
    seen_bm: set[str] = set()
    for r in bm25_results:
        if r.document_id not in seen_bm:
            seen_bm.add(r.document_id)
            bm_best_by_doc.append(r)

    dn_best_by_doc: list[RetrievalResult] = []
    seen_dn: set[str] = set()
    for r in dense_results:
        if r.document_id not in seen_dn:
            seen_dn.add(r.document_id)
            dn_best_by_doc.append(r)

    # Rank lookup maps for telemetry
    bm_rank_map = {r.chunk_id: r.rank for r in bm25_results}
    bm_score_map = {r.chunk_id: r.score for r in bm25_results}
    dn_rank_map = {r.chunk_id: r.rank for r in dense_results}
    dn_score_map = {r.chunk_id: r.score for r in dense_results}

    final_results: list[HybridRetrievalResult] = []
    seen_docs: set[str] = set()
    max_turns = max(len(bm_best_by_doc), len(dn_best_by_doc))

    for turn in range(max_turns):
        # 1. BM25 pick
        if turn < len(bm_best_by_doc):
            base = bm_best_by_doc[turn]
            if not deduplicate_docs or base.document_id not in seen_docs:
                seen_docs.add(base.document_id)
                rank = len(final_results) + 1
                synthetic_score = round(1.0 / (k + rank), 6)
                final_results.append(
                    HybridRetrievalResult(
                        chunk_id=base.chunk_id,
                        document_id=base.document_id,
                        score=synthetic_score,
                        rank=rank,
                        title=base.title,
                        text_preview=base.text_preview,
                        tenant_id=base.tenant_id,
                        source_type=base.source_type,
                        department=base.department,
                        classification=base.classification,
                        authority_level=base.authority_level,
                        status=base.status,
                        version=base.version,
                        created_at=base.created_at,
                        source_entity_id=base.source_entity_id,
                        related_entity_ids=list(base.related_entity_ids),
                        bm25_rank=bm_rank_map.get(base.chunk_id),
                        dense_rank=dn_rank_map.get(base.chunk_id),
                        bm25_score=bm_score_map.get(base.chunk_id),
                        dense_score=dn_score_map.get(base.chunk_id),
                        rrf_score=synthetic_score,
                    )
                )
                if len(final_results) >= top_k:
                    break

        # 2. Dense pick
        if turn < len(dn_best_by_doc):
            base = dn_best_by_doc[turn]
            if not deduplicate_docs or base.document_id not in seen_docs:
                seen_docs.add(base.document_id)
                rank = len(final_results) + 1
                synthetic_score = round(1.0 / (k + rank), 6)
                final_results.append(
                    HybridRetrievalResult(
                        chunk_id=base.chunk_id,
                        document_id=base.document_id,
                        score=synthetic_score,
                        rank=rank,
                        title=base.title,
                        text_preview=base.text_preview,
                        tenant_id=base.tenant_id,
                        source_type=base.source_type,
                        department=base.department,
                        classification=base.classification,
                        authority_level=base.authority_level,
                        status=base.status,
                        version=base.version,
                        created_at=base.created_at,
                        source_entity_id=base.source_entity_id,
                        related_entity_ids=list(base.related_entity_ids),
                        bm25_rank=bm_rank_map.get(base.chunk_id),
                        dense_rank=dn_rank_map.get(base.chunk_id),
                        bm25_score=bm_score_map.get(base.chunk_id),
                        dense_score=dn_score_map.get(base.chunk_id),
                        rrf_score=synthetic_score,
                    )
                )
                if len(final_results) >= top_k:
                    break

    return final_results


def fuse_single_channel(
    results: list[RetrievalResult],
    channel_name: str,
    top_k: int = 50,
    k: int = 60,
    deduplicate_docs: bool = True,
) -> list[HybridRetrievalResult]:
    """Wrap single-channel results into HybridRetrievalResult objects for consistent evaluation."""
    final_results: list[HybridRetrievalResult] = []
    seen_docs: set[str] = set()

    for r in results:
        if deduplicate_docs:
            if r.document_id in seen_docs:
                continue
            seen_docs.add(r.document_id)

        rank = len(final_results) + 1
        synthetic_score = round(1.0 / (k + rank), 6)
        bm25_rank = r.rank if channel_name == "bm25" else None
        dense_rank = r.rank if channel_name == "dense" else None
        bm25_score = r.score if channel_name == "bm25" else None
        dense_score = r.score if channel_name == "dense" else None

        final_results.append(
            HybridRetrievalResult(
                chunk_id=r.chunk_id,
                document_id=r.document_id,
                score=synthetic_score,
                rank=rank,
                title=r.title,
                text_preview=r.text_preview,
                tenant_id=r.tenant_id,
                source_type=r.source_type,
                department=r.department,
                classification=r.classification,
                authority_level=r.authority_level,
                status=r.status,
                version=r.version,
                created_at=r.created_at,
                source_entity_id=r.source_entity_id,
                related_entity_ids=list(r.related_entity_ids),
                bm25_rank=bm25_rank,
                dense_rank=dense_rank,
                bm25_score=bm25_score,
                dense_score=dense_score,
                rrf_score=synthetic_score,
            )
        )
        if len(final_results) >= top_k:
            break

    return final_results
