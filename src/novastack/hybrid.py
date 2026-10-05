"""Deterministic Hybrid Retrieval Engine with Reciprocal Rank Fusion (RRF) — Phase 4A.

Combines BM25 lexical retrieval and Dense semantic retrieval (BAAI/bge-small-en-v1.5)
over the canonical SearchChunk corpus using the standard Cormack et al. (2009)
Reciprocal Rank Fusion formula:

    RRF_score(d) = sum_{m in {BM25, Dense}} w_m / (k + rank_m(d))

Preserves strict pre-scoring filtering boundaries across tenancy, classification,
department, source type, and status on both individual retrievers prior to fusion.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from novastack.bm25 import BM25Index, RetrievalResult
from novastack.dense import DenseIndex

__all__ = [
    "HybridConfig",
    "HybridRetrievalResult",
    "HybridRetriever",
]


@dataclass
class HybridConfig:
    """Hyperparameter configuration for hybrid retrieval and rank fusion."""

    rrf_k: int = 60            # Reciprocal rank smoothing constant (default: 60)
    retriever_top_k: int = 50  # Candidate retrieval depth per channel before fusion
    bm25_weight: float = 1.0   # Relative weighting factor for BM25 channel
    dense_weight: float = 1.0  # Relative weighting factor for Dense channel


@dataclass
class HybridRetrievalResult:
    """Ranked candidate returned by hybrid retrieval, including rank telemetry."""

    chunk_id: str
    document_id: str
    score: float
    rank: int
    title: str
    text_preview: str
    tenant_id: str
    source_type: str
    department: str
    classification: str
    authority_level: str
    status: str
    version: str
    created_at: str
    source_entity_id: str | None = None
    related_entity_ids: list[str] = field(default_factory=list)
    bm25_rank: int | None = None
    dense_rank: int | None = None
    bm25_score: float | None = None
    dense_score: float | None = None
    rrf_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert result to JSON-serializable dictionary."""
        return asdict(self)


class HybridRetriever:
    """Dual-channel hybrid search orchestrator combining BM25 and Dense retrieval."""

    def __init__(
        self,
        bm25_index: BM25Index,
        dense_index: DenseIndex,
        config: HybridConfig | None = None,
    ) -> None:
        self.bm25_index = bm25_index
        self.dense_index = dense_index
        self.config = config or HybridConfig()

    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[HybridRetrievalResult]:
        """Execute hybrid search using Reciprocal Rank Fusion over BM25 and Dense channels."""
        if not query or not query.strip() or top_k <= 0:
            return []

        # 1. Retrieve candidates from both independent channels under identical filters
        cand_depth = max(top_k, self.config.retriever_top_k)
        bm25_results = self.bm25_index.search(query=query, top_k=cand_depth, filters=filters)
        dense_results = self.dense_index.search(query=query, top_k=cand_depth, filters=filters)

        if not bm25_results and not dense_results:
            return []

        # 2. Compute Reciprocal Rank Fusion (RRF) scores
        # candidate_map: chunk_id -> dict with metadata, ranks, and rrf_score
        candidate_map: dict[str, dict[str, Any]] = {}

        for r in bm25_results:
            rrf_contrib = self.config.bm25_weight / (self.config.rrf_k + r.rank)
            candidate_map[r.chunk_id] = {
                "result": r,
                "rrf_score": rrf_contrib,
                "bm25_rank": r.rank,
                "bm25_score": r.score,
                "dense_rank": None,
                "dense_score": None,
            }

        for r in dense_results:
            rrf_contrib = self.config.dense_weight / (self.config.rrf_k + r.rank)
            if r.chunk_id in candidate_map:
                candidate_map[r.chunk_id]["rrf_score"] += rrf_contrib
                candidate_map[r.chunk_id]["dense_rank"] = r.rank
                candidate_map[r.chunk_id]["dense_score"] = r.score
            else:
                candidate_map[r.chunk_id] = {
                    "result": r,
                    "rrf_score": rrf_contrib,
                    "bm25_rank": None,
                    "bm25_score": None,
                    "dense_rank": r.rank,
                    "dense_score": r.score,
                }

        # 3. Deterministic Sorting: Primary by -rrf_score, Secondary by chunk_id
        sorted_candidates = sorted(
            candidate_map.values(),
            key=lambda x: (-x["rrf_score"], x["result"].chunk_id),
        )

        # 4. Construct unified HybridRetrievalResult objects
        final_results: list[HybridRetrievalResult] = []
        for rank, cand in enumerate(sorted_candidates[:top_k], start=1):
            base: RetrievalResult = cand["result"]
            rrf_score = cand["rrf_score"]
            final_results.append(
                HybridRetrievalResult(
                    chunk_id=base.chunk_id,
                    document_id=base.document_id,
                    score=round(rrf_score, 6),
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
                    bm25_rank=cand["bm25_rank"],
                    dense_rank=cand["dense_rank"],
                    bm25_score=cand["bm25_score"],
                    dense_score=cand["dense_score"],
                    rrf_score=round(rrf_score, 6),
                )
            )

        return final_results
