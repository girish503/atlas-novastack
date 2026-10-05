"""Deterministic Dense Semantic Retrieval Engine — Phase 3B.

Provides a clean, independently measurable dense retrieval baseline using
BAAI/bge-small-en-v1.5 over the canonical SearchChunk corpus
(data/processed/novastack/search_chunks.json).

Preserves the strict architectural lineage:
SearchChunk -> SearchDocument -> SourceRecord -> Ground Truth / Provenance
and enforces identical pre-scoring security filtering boundaries as BM25.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from novastack.bm25 import RetrievalResult
from novastack.models import SearchChunk

__all__ = [
    "DenseConfig",
    "DenseEncoder",
    "DenseIndex",
    "RetrievalResult",
    "format_passage_text",
]


def format_passage_text(chunk: SearchChunk) -> str:
    """Format chunk into passage text for dense embedding.

    Combines document title and chunk text with a double newline to give the
    encoder document-level context while maintaining paragraph separation.
    """
    if chunk.title and chunk.title.strip():
        return f"{chunk.title.strip()}\n\n{chunk.text.strip()}"
    return chunk.text.strip()


@dataclass
class DenseConfig:
    """Hyperparameter configuration for dense semantic retrieval."""

    model_name: str = "BAAI/bge-small-en-v1.5"
    query_instruction: str = "Represent this sentence for searching relevant passages: "
    dimension: int = 384
    normalize_embeddings: bool = True
    batch_size: int = 32


class DenseEncoder:
    """Encapsulates embedding model loading and inference.

    Employs lazy initialization so lightweight index loading and inspection
    can occur without downloading or loading model weights.
    """

    def __init__(self, config: DenseConfig | None = None, device: str = "cpu") -> None:
        self.config = config or DenseConfig()
        self.device = device
        self._model: Any = None

    def get_model(self) -> Any:
        """Lazily load SentenceTransformer model."""
        if self._model is None:
            import os
            os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
            os.environ["HF_HUB_OFFLINE"] = "1"
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(
                self.config.model_name,
                device=self.device,
                model_kwargs={"local_files_only": True},
            )
        return self._model

    def encode_passages(self, passages: list[str]) -> np.ndarray:
        """Encode a batch of passages without instruction prefix.

        Returns an (N, dimension) float32 numpy array.
        """
        if not passages:
            return np.empty((0, self.config.dimension), dtype=np.float32)
        model = self.get_model()
        embeddings = model.encode(
            passages,
            batch_size=self.config.batch_size,
            normalize_embeddings=self.config.normalize_embeddings,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return embeddings.astype(np.float32)

    def encode_query(self, query: str) -> np.ndarray:
        """Encode query string prepending official BGE instruction prefix.

        Returns a 1D (dimension,) float32 numpy array.
        """
        if not query or not query.strip():
            return np.zeros(self.config.dimension, dtype=np.float32)
        model = self.get_model()
        instruction = self.config.query_instruction or ""
        text = f"{instruction}{query.strip()}"
        embedding = model.encode(
            text,
            normalize_embeddings=self.config.normalize_embeddings,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return embedding.astype(np.float32).flatten()


class DenseIndex:
    """Local, in-memory dense vector index for SearchChunk corpus.

    Computes normalized cosine similarity via exact dot product and enforces
    strict pre-scoring filtering boundaries across metadata fields.
    """

    def __init__(
        self,
        chunks: list[SearchChunk],
        vectors: np.ndarray,
        config: DenseConfig | None = None,
        encoder: DenseEncoder | None = None,
    ) -> None:
        if len(chunks) != vectors.shape[0]:
            raise ValueError(
                f"Mismatch between chunks count ({len(chunks)}) and vectors shape ({vectors.shape[0]})"
            )
        self.chunks = chunks
        self.vectors = vectors.astype(np.float32)
        self.config = config or DenseConfig()
        self.encoder = encoder or DenseEncoder(self.config)
        self.chunk_id_to_idx = {c.chunk_id: i for i, c in enumerate(chunks)}

    def validate_integrity(self) -> tuple[bool, list[str]]:
        """Verify internal structural integrity and numeric validity of dense vectors."""
        errors: list[str] = []
        if len(self.chunks) == 0:
            errors.append("Dense index has zero chunks")
        if self.vectors is None:
            errors.append("Dense vectors array is None")
        else:
            v_shape = self.vectors.shape
            if len(v_shape) != 2:
                errors.append(f"Dense vectors array must be 2-dimensional, found shape {v_shape}")
            else:
                if v_shape[0] != len(self.chunks):
                    errors.append(
                        f"Vector count ({v_shape[0]}) does not match chunks count ({len(self.chunks)})"
                    )
                if v_shape[1] != self.config.dimension:
                    errors.append(
                        f"Vector dimension ({v_shape[1]}) does not match config dimension ({self.config.dimension})"
                    )
                if np.isnan(self.vectors).any():
                    errors.append("Dense vectors contain NaN values")
                if np.isinf(self.vectors).any():
                    errors.append("Dense vectors contain Infinite values")
        if len(self.chunk_id_to_idx) != len(self.chunks):
            errors.append("Duplicate chunk IDs detected in dense index chunks")
        return len(errors) == 0, errors

    @classmethod
    def build_from_chunks(
        cls,
        chunks: list[SearchChunk],
        encoder: DenseEncoder | None = None,
        config: DenseConfig | None = None,
    ) -> DenseIndex:
        """Construct dense index from SearchChunk corpus by encoding all passages."""
        cfg = config or DenseConfig()
        enc = encoder or DenseEncoder(cfg)
        passages = [format_passage_text(c) for c in chunks]
        vectors = enc.encode_passages(passages)
        return cls(chunks=chunks, vectors=vectors, config=cfg, encoder=enc)

    def save(self, embeddings_path: Path, metadata_path: Path) -> None:
        """Serialize embedding vectors and metadata to disk."""
        embeddings_path.parent.mkdir(parents=True, exist_ok=True)
        chunk_ids = np.array([c.chunk_id for c in self.chunks])
        np.savez_compressed(
            embeddings_path,
            vectors=self.vectors,
            chunk_ids=chunk_ids,
        )

        metadata = {
            "model_name": self.config.model_name,
            "dimension": self.config.dimension,
            "total_chunks": len(self.chunks),
            "normalize_embeddings": self.config.normalize_embeddings,
            "query_instruction": self.config.query_instruction,
        }
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

    @classmethod
    def load(
        cls,
        chunks_path: Path,
        embeddings_path: Path,
        metadata_path: Path | None = None,
        encoder: DenseEncoder | None = None,
        config: DenseConfig | None = None,
    ) -> DenseIndex:
        """Load precomputed dense vector index and associate with SearchChunks."""
        with open(chunks_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        chunks = [SearchChunk.from_dict(c) for c in raw_data["search_chunks"]]

        npz = np.load(embeddings_path)
        vectors = npz["vectors"].astype(np.float32)
        saved_chunk_ids = npz["chunk_ids"].tolist()

        if len(chunks) != len(saved_chunk_ids):
            raise ValueError(
                f"Chunks count ({len(chunks)}) does not match stored vector count ({len(saved_chunk_ids)})"
            )

        # Verify chunk ID ordering integrity
        for i, (chunk, saved_id) in enumerate(zip(chunks, saved_chunk_ids)):
            if chunk.chunk_id != saved_id:
                raise ValueError(
                    f"Chunk ID mismatch at index {i}: chunk has '{chunk.chunk_id}' but index has '{saved_id}'"
                )

        cfg = config or DenseConfig()
        if metadata_path and metadata_path.exists():
            with open(metadata_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            cfg.model_name = meta.get("model_name", cfg.model_name)
            cfg.dimension = meta.get("dimension", cfg.dimension)
            cfg.normalize_embeddings = meta.get("normalize_embeddings", cfg.normalize_embeddings)
            cfg.query_instruction = meta.get("query_instruction", cfg.query_instruction)

        enc = encoder or DenseEncoder(cfg)
        return cls(chunks=chunks, vectors=vectors, config=cfg, encoder=enc)

    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievalResult]:
        """Search dense vector index with query and optional pre-scoring metadata filters."""
        if not query or not query.strip():
            return []
        query_vector = self.encoder.encode_query(query)
        return self.search_vector(query_vector=query_vector, top_k=top_k, filters=filters)

    def search_vector(
        self,
        query_vector: np.ndarray,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievalResult]:
        """Execute vector similarity search given pre-computed query vector."""
        if top_k <= 0 or len(self.chunks) == 0:
            return []

        # 1. Pre-scoring Candidate Selection (Security & Metadata Boundary)
        if filters:
            candidate_indices = [
                idx for idx, c in enumerate(self.chunks)
                if self._matches_filters(c, filters)
            ]
        else:
            candidate_indices = list(range(len(self.chunks)))

        if not candidate_indices:
            return []

        # 2. Compute Cosine Similarity (Dot product on normalized vectors)
        candidate_vectors = self.vectors[candidate_indices]  # Shape (M, D)
        scores = np.dot(candidate_vectors, query_vector)     # Shape (M,)

        # 3. Deterministic Sorting: Primary by -score, Secondary by chunk_id
        candidate_items = []
        for local_idx, score in enumerate(scores):
            global_idx = candidate_indices[local_idx]
            chunk = self.chunks[global_idx]
            candidate_items.append((float(score), chunk.chunk_id, global_idx))

        # Sort descending by score, ascending by chunk_id
        candidate_items.sort(key=lambda x: (-x[0], x[1]))

        # 4. Take top-k and build RetrievalResults
        results: list[RetrievalResult] = []
        for rank, (score, _, global_idx) in enumerate(candidate_items[:top_k], start=1):
            chunk = self.chunks[global_idx]
            preview = chunk.text[:120].replace("\n", " ").strip()
            if len(chunk.text) > 120:
                preview += "..."

            results.append(
                RetrievalResult(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    score=round(score, 4),
                    rank=rank,
                    title=chunk.title,
                    text_preview=preview,
                    tenant_id=chunk.tenant_id,
                    source_type=chunk.source_type,
                    department=chunk.department,
                    classification=chunk.classification,
                    authority_level=chunk.authority_level,
                    status=chunk.status,
                    version=chunk.version,
                    created_at=chunk.created_at,
                    source_entity_id=chunk.source_entity_id,
                    related_entity_ids=list(chunk.related_entity_ids),
                )
            )

        return results

    def _matches_filters(self, chunk: SearchChunk, filters: dict[str, Any]) -> bool:
        """Validate if a SearchChunk matches all specified metadata filters."""
        for key, expected in filters.items():
            if expected is None:
                continue
            if key == "tenant_id" and chunk.tenant_id != expected:
                return False
            if key == "classification":
                if isinstance(expected, (list, set, tuple)):
                    if chunk.classification not in expected:
                        return False
                elif chunk.classification != expected:
                    return False
            if key == "department" and chunk.department != expected:
                return False
            if key == "source_type" and chunk.source_type != expected:
                return False
            if key == "status" and chunk.status != expected:
                return False
        return True

    def get_index_statistics(self) -> dict[str, Any]:
        """Return diagnostic index statistics."""
        return {
            "total_chunks": len(self.chunks),
            "vector_shape": list(self.vectors.shape),
            "model_name": self.config.model_name,
            "dimension": self.config.dimension,
            "normalize_embeddings": self.config.normalize_embeddings,
            "query_instruction": self.config.query_instruction,
        }
