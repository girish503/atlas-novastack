"""Tests for Phase 3B Dense Retrieval Baseline (novastack.dense).

Validates model loading, embedding dimensions, vector normalization, in-memory indexing,
top-k ranking, pre-scoring metadata filtering, persistence round-trips, determinism,
and corpus immutability.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.slow

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.dense import (
    DenseConfig,
    DenseEncoder,
    DenseIndex,
    RetrievalResult,
    format_passage_text,
)
from novastack.models import SearchChunk
_CHUNKS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_chunks.json"
_EMBEDDINGS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "dense_embeddings.npz"
_METADATA_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "dense_index_metadata.json"
_EVAL_PATH = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
_RAW_PATH = _PROJECT_ROOT / "data" / "raw" / "novastack" / "source_records.json"
_DOCS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_documents.json"


def _compute_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def sample_chunks() -> list[SearchChunk]:
    with open(_CHUNKS_PATH, "r", encoding="utf-8") as f:
        raw_chunks = json.load(f)["search_chunks"]
    return [SearchChunk.from_dict(c) for c in raw_chunks]


@pytest.fixture(scope="module")
def encoder() -> DenseEncoder:
    cfg = DenseConfig(model_name="BAAI/bge-small-en-v1.5")
    return DenseEncoder(config=cfg, device="cpu")


@pytest.fixture(scope="module")
def loaded_index(encoder: DenseEncoder) -> DenseIndex:
    if not _EMBEDDINGS_PATH.exists():
        pytest.skip(f"Embeddings artifact not found at {_EMBEDDINGS_PATH}. Run build_dense_index.py first.")
    return DenseIndex.load(
        chunks_path=_CHUNKS_PATH,
        embeddings_path=_EMBEDDINGS_PATH,
        metadata_path=_METADATA_PATH,
        encoder=encoder,
    )


class TestDenseEncoder:
    """Test model loading, embedding dimensions, normalization, and determinism."""

    def test_encoder_model_loading(self, encoder: DenseEncoder) -> None:
        model = encoder.get_model()
        assert model is not None

    def test_embedding_dimensions(self, encoder: DenseEncoder) -> None:
        text = "Test database latency and connection pool timeout"
        vec = encoder.encode_query(text)
        assert isinstance(vec, np.ndarray)
        assert vec.shape == (384,)
        assert vec.dtype == np.float32

    def test_normalized_query_vector(self, encoder: DenseEncoder) -> None:
        text = "Sample enterprise query"
        vec = encoder.encode_query(text)
        norm = np.linalg.norm(vec)
        assert pytest.approx(norm, abs=1e-4) == 1.0

    def test_normalized_passage_vector(self, encoder: DenseEncoder) -> None:
        passages = ["Incident INC-NS-0001 triage log", "Database failover runbook"]
        vecs = encoder.encode_passages(passages)
        assert vecs.shape == (2, 384)
        for v in vecs:
            norm = np.linalg.norm(v)
            assert pytest.approx(norm, abs=1e-4) == 1.0

    def test_deterministic_embedding_generation(self, encoder: DenseEncoder) -> None:
        text = "Deterministic embedding test across identical queries"
        v1 = encoder.encode_query(text)
        v2 = encoder.encode_query(text)
        np.testing.assert_allclose(v1, v2, rtol=1e-5, atol=1e-6)

    def test_empty_query_encoding(self, encoder: DenseEncoder) -> None:
        vec = encoder.encode_query("")
        assert vec.shape == (384,)
        assert np.all(vec == 0.0)

    def test_format_passage_text(self, sample_chunks: list[SearchChunk]) -> None:
        chunk = sample_chunks[0]
        formatted = format_passage_text(chunk)
        assert chunk.title in formatted
        assert chunk.text in formatted
        assert "\n\n" in formatted


class TestDenseIndexAndRetrieval:
    """Test index shape, mapping stability, search, ranking, and metadata filtering."""

    def test_index_shape(self, loaded_index: DenseIndex) -> None:
        assert len(loaded_index.chunks) == 1663
        assert loaded_index.vectors.shape == (1663, 384)

    def test_stable_chunk_to_vector_mapping(self, loaded_index: DenseIndex, sample_chunks: list[SearchChunk]) -> None:
        for i, chunk in enumerate(sample_chunks[:50]):
            assert loaded_index.chunks[i].chunk_id == chunk.chunk_id
            assert loaded_index.chunk_id_to_idx[chunk.chunk_id] == i

    def test_top_k_retrieval(self, loaded_index: DenseIndex) -> None:
        query = "database connection pool timeout checkout service"
        results = loaded_index.search(query=query, top_k=5)
        assert len(results) == 5
        # Check strictly descending scores
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)
        # Check rank ordering
        ranks = [r.rank for r in results]
        assert ranks == [1, 2, 3, 4, 5]

    def test_retrieval_result_metadata_envelope(self, loaded_index: DenseIndex) -> None:
        results = loaded_index.search(query="INC-NS-0001", top_k=1)
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, RetrievalResult)
        assert r.chunk_id.startswith("DOC-")
        assert r.document_id.startswith("DOC-")
        assert r.tenant_id in {"TENANT-NOVASTACK", "TENANT-ORBITAL", "TENANT-PINECONE"}
        assert r.source_type is not None
        assert r.classification in {"public", "internal", "confidential", "restricted"}
        d = r.to_dict()
        assert isinstance(d, dict)
        assert d["chunk_id"] == r.chunk_id

    def test_tenant_isolation_filtering(self, loaded_index: DenseIndex) -> None:
        # Search restricted to TENANT-ORBITAL
        results = loaded_index.search(
            query="database connection latency",
            top_k=10,
            filters={"tenant_id": "TENANT-ORBITAL"},
        )
        assert len(results) > 0
        for r in results:
            assert r.tenant_id == "TENANT-ORBITAL"

    def test_classification_filtering(self, loaded_index: DenseIndex) -> None:
        results = loaded_index.search(
            query="incident postmortem root cause",
            top_k=10,
            filters={"classification": "public"},
        )
        assert len(results) > 0
        for r in results:
            assert r.classification == "public"

    def test_department_filtering(self, loaded_index: DenseIndex) -> None:
        results = loaded_index.search(
            query="security policy review",
            top_k=10,
            filters={"department": "Security"},
        )
        assert len(results) > 0
        for r in results:
            assert r.department == "Security"

    def test_source_type_filtering(self, loaded_index: DenseIndex) -> None:
        results = loaded_index.search(
            query="checkout service failure",
            top_k=5,
            filters={"source_type": "postmortem"},
        )
        assert len(results) > 0
        for r in results:
            assert r.source_type == "postmortem"

    def test_status_filtering(self, loaded_index: DenseIndex) -> None:
        results = loaded_index.search(
            query="migration runbook",
            top_k=5,
            filters={"status": "published"},
        )
        assert len(results) > 0
        for r in results:
            assert r.status == "published"

    def test_empty_results_when_filters_exclude_everything(self, loaded_index: DenseIndex) -> None:
        results = loaded_index.search(
            query="checkout timeout",
            top_k=5,
            filters={"tenant_id": "NON_EXISTENT_TENANT"},
        )
        assert results == []

    def test_empty_query_returns_empty_list(self, loaded_index: DenseIndex) -> None:
        assert loaded_index.search(query="") == []
        assert loaded_index.search(query="   ") == []

    def test_deterministic_ranking(self, loaded_index: DenseIndex) -> None:
        query = "Kubernetes ingress pod eviction"
        r1 = loaded_index.search(query=query, top_k=5)
        r2 = loaded_index.search(query=query, top_k=5)
        assert len(r1) == len(r2)
        for a, b in zip(r1, r2):
            assert a.chunk_id == b.chunk_id
            assert a.score == b.score
            assert a.rank == b.rank

    def test_index_persistence_roundtrip(
        self,
        loaded_index: DenseIndex,
        encoder: DenseEncoder,
        tmp_path: Path,
    ) -> None:
        tmp_emb = tmp_path / "test_embeddings.npz"
        tmp_meta = tmp_path / "test_metadata.json"

        loaded_index.save(embeddings_path=tmp_emb, metadata_path=tmp_meta)
        assert tmp_emb.exists()
        assert tmp_meta.exists()

        reloaded = DenseIndex.load(
            chunks_path=_CHUNKS_PATH,
            embeddings_path=tmp_emb,
            metadata_path=tmp_meta,
            encoder=encoder,
        )
        assert len(reloaded.chunks) == len(loaded_index.chunks)
        np.testing.assert_allclose(reloaded.vectors, loaded_index.vectors, rtol=1e-6, atol=1e-6)


class TestCorpusImmutability:
    """Verify that building or evaluating the dense index preserves raw evidence and ground truth."""

    def test_raw_corpus_remains_unchanged(self) -> None:
        assert _RAW_PATH.exists()
        with open(_RAW_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["source_records"]) == 1393

    def test_search_documents_remain_unchanged(self) -> None:
        assert _DOCS_PATH.exists()
        with open(_DOCS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["search_documents"]) == 1393

    def test_search_chunks_remain_unchanged(self) -> None:
        assert _CHUNKS_PATH.exists()
        with open(_CHUNKS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["search_chunks"]) == 1663

    def test_evaluation_cases_remain_unchanged(self) -> None:
        assert _EVAL_PATH.exists()
        with open(_EVAL_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["evaluation_cases"]) == 120
