"""Tests for Phase 4A Hybrid Retrieval and Reciprocal Rank Fusion (novastack.hybrid).

Validates RRF scoring mathematics, single-channel contribution, configurable k,
relative channel weights, deterministic tie-breaking, pre-scoring metadata filtering,
tenant isolation, telemetry attribution, and corpus immutability.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.slow

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.bm25 import BM25Config, BM25Index
from novastack.dense import DenseConfig, DenseEncoder, DenseIndex
from novastack.hybrid import HybridConfig, HybridRetrievalResult, HybridRetriever
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
def bm25_index() -> BM25Index:
    return BM25Index.build_index(_CHUNKS_PATH, config=BM25Config())


@pytest.fixture(scope="module")
def dense_index() -> DenseIndex:
    if not _EMBEDDINGS_PATH.exists():
        pytest.skip("Dense embeddings artifact not found.")
    cfg = DenseConfig(model_name="BAAI/bge-small-en-v1.5")
    encoder = DenseEncoder(config=cfg, device="cpu")
    return DenseIndex.load(
        chunks_path=_CHUNKS_PATH,
        embeddings_path=_EMBEDDINGS_PATH,
        metadata_path=_METADATA_PATH,
        encoder=encoder,
    )


@pytest.fixture(scope="module")
def hybrid_retriever(bm25_index: BM25Index, dense_index: DenseIndex) -> HybridRetriever:
    return HybridRetriever(bm25_index=bm25_index, dense_index=dense_index)


class TestRRFMathematics:
    """Validate mathematical formula and properties of Reciprocal Rank Fusion."""

    def test_rrf_scoring_both_channels(self, hybrid_retriever: HybridRetriever) -> None:
        """Verify that when a chunk is retrieved by both channels, its score equals the sum of reciprocal ranks."""
        # Query known to hit common incident docs across both BM25 and Dense
        results = hybrid_retriever.search("database connection pool timeout 504", top_k=5)
        assert len(results) > 0

        k = hybrid_retriever.config.rrf_k
        for r in results:
            expected_score = 0.0
            if r.bm25_rank is not None:
                expected_score += hybrid_retriever.config.bm25_weight / (k + r.bm25_rank)
            if r.dense_rank is not None:
                expected_score += hybrid_retriever.config.dense_weight / (k + r.dense_rank)
            assert pytest.approx(r.rrf_score, abs=1e-5) == expected_score

    def test_rrf_configurable_k(self, bm25_index: BM25Index, dense_index: DenseIndex) -> None:
        """Verify changing rrf_k alters score magnitude according to 1/(k + rank)."""
        retriever_k20 = HybridRetriever(bm25_index, dense_index, HybridConfig(rrf_k=20))
        retriever_k60 = HybridRetriever(bm25_index, dense_index, HybridConfig(rrf_k=60))

        res_20 = retriever_k20.search("database connection timeout", top_k=1)
        res_60 = retriever_k60.search("database connection timeout", top_k=1)

        assert len(res_20) == 1 and len(res_60) == 1
        # With smaller k, reciprocal ranks 1/(k+r) are strictly larger
        assert res_20[0].rrf_score > res_60[0].rrf_score

    def test_rrf_channel_weighting(self, bm25_index: BM25Index, dense_index: DenseIndex) -> None:
        """Verify channel weights scale the contribution of respective retrievers."""
        retriever_dense_heavy = HybridRetriever(
            bm25_index,
            dense_index,
            HybridConfig(rrf_k=60, bm25_weight=0.0, dense_weight=2.0),
        )
        res = retriever_dense_heavy.search("database connection timeout", top_k=5)
        for r in res:
            if r.dense_rank is not None:
                assert pytest.approx(r.rrf_score, abs=1e-5) == 2.0 / (60 + r.dense_rank)
            else:
                assert r.rrf_score == 0.0

    def test_deterministic_tie_breaking(self, bm25_index: BM25Index, dense_index: DenseIndex) -> None:
        """Verify identical scores are tie-broken deterministically by chunk_id ascending."""
        retriever = HybridRetriever(bm25_index, dense_index)
        results = retriever.search("incident postmortem report", top_k=20)
        for i in range(len(results) - 1):
            curr = results[i]
            nxt = results[i + 1]
            if curr.rrf_score == nxt.rrf_score:
                assert curr.chunk_id < nxt.chunk_id


class TestHybridRetrieverQueries:
    """Validate query execution, edge cases, and result structures."""

    def test_empty_query_returns_empty(self, hybrid_retriever: HybridRetriever) -> None:
        assert hybrid_retriever.search("", top_k=10) == []
        assert hybrid_retriever.search("   ", top_k=10) == []

    def test_invalid_top_k(self, hybrid_retriever: HybridRetriever) -> None:
        assert hybrid_retriever.search("incident", top_k=0) == []
        assert hybrid_retriever.search("incident", top_k=-5) == []

    def test_result_structure_and_serialization(self, hybrid_retriever: HybridRetriever) -> None:
        results = hybrid_retriever.search("incident", top_k=3)
        assert len(results) == 3
        for i, r in enumerate(results, start=1):
            assert isinstance(r, HybridRetrievalResult)
            assert r.rank == i
            assert "::CHUNK-" in r.chunk_id
            assert r.document_id.startswith("DOC-")
            assert r.title != ""
            assert r.text_preview != ""
            assert r.tenant_id.startswith("TENANT-")
            assert r.score > 0.0

            # Telemetry verification
            assert (r.bm25_rank is not None) or (r.dense_rank is not None)
            d = r.to_dict()
            assert isinstance(d, dict)
            assert d["chunk_id"] == r.chunk_id
            assert d["rrf_score"] == r.rrf_score


class TestHybridFiltering:
    """Validate pre-scoring filtering in hybrid retrieval."""

    def test_tenant_isolation(self, hybrid_retriever: HybridRetriever) -> None:
        """Tenant filtering must guarantee zero cross-tenant candidates."""
        results = hybrid_retriever.search(
            "database connection latency",
            top_k=10,
            filters={"tenant_id": "TENANT-ORBITAL"},
        )
        assert len(results) > 0
        for r in results:
            assert r.tenant_id == "TENANT-ORBITAL"

    def test_classification_filtering(self, hybrid_retriever: HybridRetriever) -> None:
        results = hybrid_retriever.search(
            "service architecture",
            top_k=10,
            filters={"classification": "public"},
        )
        assert len(results) > 0
        for r in results:
            assert r.classification == "public"

    def test_combined_filters(self, hybrid_retriever: HybridRetriever) -> None:
        results = hybrid_retriever.search(
            "checkout service failure",
            top_k=10,
            filters={
                "tenant_id": "TENANT-NOVASTACK",
                "source_type": "postmortem",
            },
        )
        assert len(results) > 0
        for r in results:
            assert r.tenant_id == "TENANT-NOVASTACK"
            assert r.source_type == "postmortem"


class TestCorpusImmutability:
    """Verify that hybrid search does not mutate any underlying corpora or indexes."""

    def test_immutability(self) -> None:
        raw_hash_before = _compute_hash(_RAW_PATH)
        docs_hash_before = _compute_hash(_DOCS_PATH)
        chunks_hash_before = _compute_hash(_CHUNKS_PATH)
        eval_hash_before = _compute_hash(_EVAL_PATH)

        # Run an operation
        bm25_idx = BM25Index.build_index(_CHUNKS_PATH)
        dense_idx = DenseIndex.load(
            chunks_path=_CHUNKS_PATH,
            embeddings_path=_EMBEDDINGS_PATH,
            metadata_path=_METADATA_PATH,
        )
        retriever = HybridRetriever(bm25_idx, dense_idx)
        _ = retriever.search("Kafka partition lag deployment", top_k=5)

        assert _compute_hash(_RAW_PATH) == raw_hash_before
        assert _compute_hash(_DOCS_PATH) == docs_hash_before
        assert _compute_hash(_CHUNKS_PATH) == chunks_hash_before
        assert _compute_hash(_EVAL_PATH) == eval_hash_before
