"""Cross-Encoder Reranking Engine — Phase 4B-1.

Provides local, deterministic cross-encoder reranking using
cross-encoder/ms-marco-MiniLM-L-6-v2 over candidate pools:
1. BM25 top-50
2. Dense top-50
3. BM25 top-50 UNION Dense top-50 (Primary System F)

Preserves rich provenance (BM25 rank/score, Dense rank/score, RRF score)
and enforces deterministic tie-breaking.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from novastack.bm25 import BM25Index, RetrievalResult
from novastack.dense import DenseIndex
from novastack.models import SearchChunk

__all__ = [
    "CrossEncoderReranker",
    "RerankedCandidate",
    "RerankerConfig",
    "RerankingPipeline",
    "format_reranker_passage",
]


def format_reranker_passage(chunk: SearchChunk | RetrievalResult) -> str:
    """Format candidate chunk into passage text for cross-encoder scoring."""
    title = chunk.title.strip() if chunk.title else ""
    # Use full text if available on SearchChunk, otherwise text_preview
    text = getattr(chunk, "text", getattr(chunk, "text_preview", "")).strip()
    if title:
        return f"{title}\n\n{text}"
    return text


@dataclass
class RerankerConfig:
    """Configuration for cross-encoder reranker inference."""

    model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    max_length: int = 512
    batch_size: int = 32
    device: str = "cpu"


@dataclass
class RerankedCandidate:
    """A candidate chunk ranked by the cross-encoder with full channel provenance."""

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
    rrf_score: float | None = None
    rrf_rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert candidate to JSON-serializable dictionary."""
        return asdict(self)


class CrossEncoderReranker:
    """Encapsulates local CrossEncoder model loading and pair scoring."""

    def __init__(self, config: RerankerConfig | None = None) -> None:
        self.config = config or RerankerConfig()
        self._model: Any = None

    def get_model(self) -> Any:
        """Lazily initialize the CrossEncoder model."""
        if self._model is None:
            os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
            os.environ["HF_HUB_OFFLINE"] = "1"
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(
                self.config.model_name,
                max_length=self.config.max_length,
                device=self.config.device,
            )
        return self._model

    def score_pairs(self, pairs: list[list[str]]) -> list[float]:
        """Score query-passage pairs using the cross-encoder model."""
        if not pairs:
            return []
        model = self.get_model()
        scores = model.predict(
            pairs,
            batch_size=self.config.batch_size,
            show_progress_bar=False,
        )
        return [float(s) for s in scores]


class RerankingPipeline:
    """Orchestrates candidate generation, deduplication, and cross-encoder reranking."""

    def __init__(
        self,
        bm25_index: BM25Index,
        dense_index: DenseIndex,
        reranker: CrossEncoderReranker,
        chunks_map: dict[str, SearchChunk] | None = None,
    ) -> None:
        self.bm25_index = bm25_index
        self.dense_index = dense_index
        self.reranker = reranker
        self.chunks_map = chunks_map or {c.chunk_id: c for c in dense_index.chunks}

    def _rerank_candidate_map(
        self,
        query: str,
        cand_map: dict[str, dict[str, Any]],
        top_k: int = 10,
    ) -> list[RerankedCandidate]:
        """Score and sort candidate items using the cross-encoder."""
        if not cand_map or not query.strip() or top_k <= 0:
            return []

        chunk_ids = list(cand_map.keys())
        pairs = []
        for cid in chunk_ids:
            chunk = self.chunks_map.get(cid)
            if chunk:
                passage = format_reranker_passage(chunk)
            else:
                base = cand_map[cid]["base"]
                passage = format_reranker_passage(base)
            pairs.append([query, passage])

        scores = self.reranker.score_pairs(pairs)

        # Build list for deterministic sorting
        scored_candidates = []
        for cid, score in zip(chunk_ids, scores):
            entry = cand_map[cid]
            scored_candidates.append({
                "cid": cid,
                "score": score,
                "entry": entry,
            })

        # Deterministic sorting: primary by -score, secondary by cid ascending
        scored_candidates.sort(key=lambda x: (-x["score"], x["cid"]))

        final_results: list[RerankedCandidate] = []
        for rank, item in enumerate(scored_candidates[:top_k], start=1):
            e = item["entry"]
            base: RetrievalResult = e["base"]
            final_results.append(
                RerankedCandidate(
                    chunk_id=base.chunk_id,
                    document_id=base.document_id,
                    score=round(item["score"], 4),
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
                    bm25_rank=e.get("bm25_rank"),
                    dense_rank=e.get("dense_rank"),
                    bm25_score=e.get("bm25_score"),
                    dense_score=e.get("dense_score"),
                    rrf_score=e.get("rrf_score"),
                    rrf_rank=e.get("rrf_rank"),
                )
            )

        return final_results

    def rerank_bm25(
        self,
        query: str,
        top_k: int = 10,
        depth: int = 50,
        filters: dict[str, Any] | None = None,
    ) -> list[RerankedCandidate]:
        """Ablation D: Rerank BM25 top-50 candidates."""
        bm25_res = self.bm25_index.search(query=query, top_k=depth, filters=filters)
        cand_map: dict[str, dict[str, Any]] = {}
        for r in bm25_res:
            cand_map[r.chunk_id] = {
                "base": r,
                "bm25_rank": r.rank,
                "bm25_score": r.score,
                "dense_rank": None,
                "dense_score": None,
            }
        return self._rerank_candidate_map(query, cand_map, top_k=top_k)

    def rerank_dense(
        self,
        query: str,
        top_k: int = 10,
        depth: int = 50,
        filters: dict[str, Any] | None = None,
    ) -> list[RerankedCandidate]:
        """Ablation E: Rerank Dense top-50 candidates."""
        dense_res = self.dense_index.search(query=query, top_k=depth, filters=filters)
        cand_map: dict[str, dict[str, Any]] = {}
        for r in dense_res:
            cand_map[r.chunk_id] = {
                "base": r,
                "bm25_rank": None,
                "bm25_score": None,
                "dense_rank": r.rank,
                "dense_score": r.score,
            }
        return self._rerank_candidate_map(query, cand_map, top_k=top_k)

    def rerank_union(
        self,
        query: str,
        top_k: int = 10,
        depth: int = 50,
        filters: dict[str, Any] | None = None,
        rrf_k: int = 60,
    ) -> list[RerankedCandidate]:
        """Primary System F: Rerank BM25 top-50 UNION Dense top-50 candidates."""
        bm25_res = self.bm25_index.search(query=query, top_k=depth, filters=filters)
        dense_res = self.dense_index.search(query=query, top_k=depth, filters=filters)

        cand_map: dict[str, dict[str, Any]] = {}
        for r in bm25_res:
            cand_map[r.chunk_id] = {
                "base": r,
                "bm25_rank": r.rank,
                "bm25_score": r.score,
                "dense_rank": None,
                "dense_score": None,
                "rrf_score": 1.0 / (rrf_k + r.rank),
            }

        for r in dense_res:
            contrib = 1.0 / (rrf_k + r.rank)
            if r.chunk_id in cand_map:
                cand_map[r.chunk_id]["dense_rank"] = r.rank
                cand_map[r.chunk_id]["dense_score"] = r.score
                cand_map[r.chunk_id]["rrf_score"] += contrib
            else:
                cand_map[r.chunk_id] = {
                    "base": r,
                    "bm25_rank": None,
                    "bm25_score": None,
                    "dense_rank": r.rank,
                    "dense_score": r.score,
                    "rrf_score": contrib,
                }

        # Calculate pre-reranker RRF ranks for telemetry
        rrf_sorted = sorted(
            cand_map.values(),
            key=lambda x: (-x["rrf_score"], x["base"].chunk_id),
        )
        for rrf_rank, item in enumerate(rrf_sorted, start=1):
            item["rrf_rank"] = rrf_rank

        return self._rerank_candidate_map(query, cand_map, top_k=top_k)
