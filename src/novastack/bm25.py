"""Deterministic BM25 lexical retrieval engine — Phase 3A.

Provides a transparent, zero-dependency lexical search baseline over the canonical
SearchChunk corpus (data/processed/novastack/search_chunks.json).

Preserves the strict architectural lineage:
SearchChunk -> SearchDocument -> SourceRecord -> Ground Truth / Provenance
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from novastack.models import SearchChunk

__all__ = [
    "BM25Config",
    "BM25Index",
    "RetrievalResult",
    "tokenize",
]


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric terms.

    Preserves numbers, letters, and decomposes hyphenated/underscored identifiers
    into constituent tokens for consistent lexical matching.
    """
    if not text:
        return []
    return re.findall(r"[a-z0-9]+", text.lower())


@dataclass
class BM25Config:
    """Hyperparameter configuration for the baseline BM25 retrieval engine."""

    k1: float = 1.5           # Term frequency saturation parameter
    b: float = 0.75           # Document length normalization parameter
    title_weight: float = 1.0 # Transparent linear weighting for title field
    text_weight: float = 1.0  # Transparent linear weighting for text field


@dataclass
class RetrievalResult:
    """Structured candidate result returned by BM25 retrieval."""

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

    def to_dict(self) -> dict[str, Any]:
        """Convert result to JSON-serializable dictionary."""
        return asdict(self)


class BM25Index:
    """In-memory inverted index and scoring engine implementing standard BM25."""

    def __init__(self, config: BM25Config | None = None) -> None:
        self.config = config or BM25Config()
        self.chunks: list[SearchChunk] = []
        self.doc_lens: list[int] = []
        self.avgdl: float = 0.0
        self.total_chunks: int = 0

        # Inverted index: term -> list of (chunk_index, weighted_term_frequency)
        self.inverted_index: dict[str, list[tuple[int, float]]] = defaultdict(list)
        # Document frequency: term -> number of chunks containing term
        self.document_frequencies: Counter[str] = Counter()
        # Precomputed IDF table
        self.idf: dict[str, float] = {}

    @classmethod
    def build_index(
        cls,
        chunks: list[SearchChunk] | list[dict[str, Any]] | Path | str | dict[str, Any],
        config: BM25Config | None = None,
    ) -> BM25Index:
        """Construct a BM25 index from SearchChunk objects or file source."""
        index = cls(config=config)
        chunk_objs = cls._load_chunks(chunks)
        index._index_chunks(chunk_objs)
        return index

    @staticmethod
    def _load_chunks(
        source: list[SearchChunk] | list[dict[str, Any]] | Path | str | dict[str, Any],
    ) -> list[SearchChunk]:
        """Load SearchChunk list from varied input types."""
        if isinstance(source, (str, Path)):
            p = Path(source)
            if not p.exists():
                raise FileNotFoundError(f"Search chunks file not found at: {p}")
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return BM25Index._load_chunks(data)

        if isinstance(source, dict):
            chunks_data = source.get("search_chunks", [])
            return [SearchChunk.from_dict(c) for c in chunks_data]

        if isinstance(source, list):
            if not source:
                return []
            if isinstance(source[0], SearchChunk):
                return source  # type: ignore[return-value]
            if isinstance(source[0], dict):
                return [SearchChunk.from_dict(c) for c in source]  # type: ignore[arg-type]

        raise TypeError(f"Unsupported source type for BM25Index: {type(source)}")

    def _index_chunks(self, chunks: list[SearchChunk]) -> None:
        """Index chunks and precompute corpus statistics and IDFs."""
        self.chunks = list(chunks)
        self.total_chunks = len(chunks)
        self.doc_lens = []
        self.inverted_index.clear()
        self.document_frequencies.clear()
        self.idf.clear()

        if self.total_chunks == 0:
            self.avgdl = 0.0
            return

        total_tokens = 0
        w_title = self.config.title_weight
        w_text = self.config.text_weight

        # Accumulate postings
        for idx, chunk in enumerate(self.chunks):
            title_tokens = tokenize(chunk.title)
            text_tokens = tokenize(chunk.text)

            title_counts = Counter(title_tokens)
            text_counts = Counter(text_tokens)

            unique_terms = set(title_counts.keys()) | set(text_counts.keys())
            doc_len = len(title_tokens) + len(text_tokens)
            self.doc_lens.append(doc_len)
            total_tokens += doc_len

            for term in unique_terms:
                self.document_frequencies[term] += 1
                weighted_tf = (title_counts.get(term, 0) * w_title) + (text_counts.get(term, 0) * w_text)
                self.inverted_index[term].append((idx, weighted_tf))

        self.avgdl = total_tokens / self.total_chunks

        # Precompute Lucene/Robertson-Spärck Jones IDF:
        # IDF(q) = ln(1 + (N - df + 0.5) / (df + 0.5))
        N = self.total_chunks
        for term, df in self.document_frequencies.items():
            self.idf[term] = math.log(1.0 + (N - df + 0.5) / (df + 0.5))

    def get_document_frequency(self, term: str) -> int:
        """Return the document frequency of a term across indexed chunks."""
        return self.document_frequencies.get(term.lower(), 0)

    def get_corpus_statistics(self) -> dict[str, Any]:
        """Return aggregate corpus statistics from the index."""
        return {
            "total_chunks": self.total_chunks,
            "unique_terms": len(self.document_frequencies),
            "average_document_length": round(self.avgdl, 2),
            "k1": self.config.k1,
            "b": self.config.b,
            "title_weight": self.config.title_weight,
            "text_weight": self.config.text_weight,
        }

    def validate_integrity(self) -> tuple[bool, list[str]]:
        """Verify internal structural integrity of the BM25 inverted index."""
        errors: list[str] = []
        if len(self.chunks) == 0:
            errors.append("BM25 index has zero chunks")
        if len(self.doc_lens) != len(self.chunks):
            errors.append(f"doc_lens length ({len(self.doc_lens)}) does not match chunks ({len(self.chunks)})")
        if self.total_chunks != len(self.chunks):
            errors.append(f"total_chunks count ({self.total_chunks}) does not match chunks ({len(self.chunks)})")
        if self.total_chunks > 0 and self.avgdl <= 0:
            errors.append(f"Non-positive average document length: {self.avgdl}")
        if self.total_chunks > 0 and len(self.document_frequencies) == 0:
            errors.append("Empty document frequencies table in non-empty index")
        return len(errors) == 0, errors

    def _matches_filters(self, chunk: SearchChunk, filters: dict[str, Any]) -> bool:
        """Evaluate pre-retrieval boundary filters against chunk metadata."""
        for key, filter_val in filters.items():
            if filter_val is None:
                continue

            chunk_val = getattr(chunk, key, None)
            if chunk_val is None:
                return False

            if isinstance(filter_val, (list, set, tuple)):
                if chunk_val not in filter_val:
                    return False
            else:
                if chunk_val != filter_val:
                    return False

        return True

    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievalResult]:
        """Execute BM25 lexical search over indexed chunks with pre-scoring filtering.

        Args:
            query: Natural language query string.
            top_k: Maximum number of candidates to return.
            filters: Optional dict of metadata filters (tenant_id, classification, department, etc.)
                     evaluated before candidate scoring.

        Returns:
            list[RetrievalResult]: Ranked candidates ordered strictly by descending score.
        """
        if top_k <= 0 or not query:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        query_counts = Counter(query_tokens)
        candidate_scores: dict[int, float] = defaultdict(float)

        k1 = self.config.k1
        b = self.config.b
        avgdl = self.avgdl if self.avgdl > 0 else 1.0

        # Pre-filter candidate indices if filters provided
        valid_indices: set[int] | None = None
        if filters:
            valid_indices = {
                idx for idx, c in enumerate(self.chunks) if self._matches_filters(c, filters)
            }
            if not valid_indices:
                return []

        # Accumulate BM25 scores via inverted index lookup
        for term, q_tf in query_counts.items():
            term_idf = self.idf.get(term)
            if term_idf is None or term not in self.inverted_index:
                continue

            postings = self.inverted_index[term]
            for chunk_idx, tf in postings:
                if valid_indices is not None and chunk_idx not in valid_indices:
                    continue

                doc_len = self.doc_lens[chunk_idx]
                # Standard BM25 term score component:
                # IDF * (TF * (k1 + 1)) / (TF + k1 * (1 - b + b * (doc_len / avgdl)))
                numerator = tf * (k1 + 1.0)
                denominator = tf + k1 * (1.0 - b + b * (doc_len / avgdl))
                candidate_scores[chunk_idx] += term_idf * (numerator / denominator)

        if not candidate_scores:
            return []

        # Sort candidates deterministically: descending score, then ascending chunk_id
        sorted_candidates = sorted(
            candidate_scores.items(),
            key=lambda item: (-item[1], self.chunks[item[0]].chunk_id),
        )

        results: list[RetrievalResult] = []
        for rank, (chunk_idx, score) in enumerate(sorted_candidates[:top_k], start=1):
            c = self.chunks[chunk_idx]
            preview = c.text[:140] + "..." if len(c.text) > 140 else c.text
            res = RetrievalResult(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                score=round(score, 4),
                rank=rank,
                title=c.title,
                text_preview=preview,
                tenant_id=c.tenant_id,
                source_type=c.source_type,
                department=c.department,
                classification=c.classification,
                authority_level=c.authority_level,
                status=c.status,
                version=c.version,
                created_at=c.created_at,
                source_entity_id=c.source_entity_id,
                related_entity_ids=list(c.related_entity_ids),
            )
            results.append(res)

        return results
