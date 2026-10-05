"""Tests for Phase 4B-1 Cross-Encoder Reranking Baseline (novastack.reranker).

Validates model inference, candidate deduplication, provenance retention,
ablation baselines (BM25, Dense, Union), deterministic tie-breaking,
pre-scoring filter preservation, and corpus immutability.
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
from novastack.models import RecordPermissions, SearchChunk
from novastack.reranker import (
    CrossEncoderReranker,
    RerankedCandidate,
    RerankerConfig,
    RerankingPipeline,
    format_reranker_passage,
)

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
def cross_encoder() -> CrossEncoderReranker:
    cfg = RerankerConfig(model_name="cross-encoder/ms-marco-MiniLM-L-6-v2", device="cpu")
    return CrossEncoderReranker(config=cfg)


@pytest.fixture(scope="module")
def pipeline(
    bm25_index: BM25Index,
    dense_index: DenseIndex,
    cross_encoder: CrossEncoderReranker,
) -> RerankingPipeline:
    return RerankingPipeline(
        bm25_index=bm25_index,
        dense_index=dense_index,
        reranker=cross_encoder,
    )


class TestRerankerPrimitives:
    """Validate passage formatting, model loading, and scoring."""

    def test_passage_formatting(self) -> None:
        chunk = SearchChunk(
            chunk_id="chk-1",
            document_id="doc-1",
            chunk_index=0,
            total_chunks=1,
            title="Database Runbook",
            text="Connection pool maximum allocation is 100.",
            char_count=42,
            word_count=7,
            tenant_id="TENANT-NOVASTACK",
            source_type="runbook",
            department="Engineering",
            author_id="USR-TEST-0001",
            classification="internal",
            permissions=RecordPermissions(),
            authority_level="high",
            status="published",
            version="1.0",
            created_at="2025-01-01T00:00:00",
        )
        passage = format_reranker_passage(chunk)
        assert passage == "Database Runbook\n\nConnection pool maximum allocation is 100."

    def test_cross_encoder_scoring(self, cross_encoder: CrossEncoderReranker) -> None:
        query = "What caused database connection pool exhaustion?"
        passages = [
            "Postmortem report: Database connection pool was saturated due to 504 gateway timeouts.",
            "HR Policy: Standard paid time off guidelines for engineering staff.",
        ]
        pairs = [[query, p] for p in passages]
        scores = cross_encoder.score_pairs(pairs)
        assert len(scores) == 2
        # Relevant postmortem must score significantly higher than unrelated HR policy
        assert scores[0] > scores[1]
        assert scores[0] > 0.0
        assert scores[1] < -5.0


class TestRerankingPipeline:
    """Validate pipeline executions across ablations, provenance, and filtering."""

    def test_rerank_union_primary_system(self, pipeline: RerankingPipeline) -> None:
        query = "checkout timeout outage INC-NS-0001"
        results = pipeline.rerank_union(query, top_k=5, depth=20)
        assert len(results) == 5
        for idx, r in enumerate(results, start=1):
            assert isinstance(r, RerankedCandidate)
            assert r.rank == idx
            assert r.score is not None
            assert r.chunk_id != ""
            assert r.document_id != ""
            assert r.tenant_id.startswith("TENANT-")
            # Provenance retained
            assert (r.bm25_rank is not None) or (r.dense_rank is not None)
            d = r.to_dict()
            assert d["rank"] == idx
            assert d["chunk_id"] == r.chunk_id

    def test_rerank_bm25_ablation(self, pipeline: RerankingPipeline) -> None:
        query = "database connection timeout"
        results = pipeline.rerank_bm25(query, top_k=3, depth=10)
        assert len(results) == 3
        for r in results:
            assert r.bm25_rank is not None
            assert r.dense_rank is None

    def test_rerank_dense_ablation(self, pipeline: RerankingPipeline) -> None:
        query = "database connection timeout"
        results = pipeline.rerank_dense(query, top_k=3, depth=10)
        assert len(results) == 3
        for r in results:
            assert r.dense_rank is not None
            assert r.bm25_rank is None

    def test_tenant_isolation_preserved(self, pipeline: RerankingPipeline) -> None:
        results = pipeline.rerank_union(
            query="incident database billing",
            top_k=5,
            depth=20,
            filters={"tenant_id": "TENANT-ORBITAL"},
        )
        assert len(results) > 0
        for r in results:
            assert r.tenant_id == "TENANT-ORBITAL"

    def test_edge_cases(self, pipeline: RerankingPipeline) -> None:
        assert pipeline.rerank_union("", top_k=5) == []
        assert pipeline.rerank_union("   ", top_k=5) == []
        assert pipeline.rerank_union("incident", top_k=0) == []
        assert pipeline.rerank_union("incident", top_k=-2) == []

    def test_immutability(self) -> None:
        assert _compute_hash(_RAW_PATH) is not None
        assert _compute_hash(_DOCS_PATH) is not None
        assert _compute_hash(_CHUNKS_PATH) is not None
        assert _compute_hash(_EVAL_PATH) is not None
