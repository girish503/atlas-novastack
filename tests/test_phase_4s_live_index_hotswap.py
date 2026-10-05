"""Phase 4S: deterministic live index generation publication tests.

These tests deliberately use in-memory deterministic retrieval components so
they prove lifecycle and request consistency without loading the Gemma model or
altering the certified production intelligence configuration.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from fastapi.testclient import TestClient

from novastack.bm25 import BM25Index
from novastack.chunking import chunk_document
from novastack.dense import DenseIndex
from novastack.evidence_resolution import EvidenceResolver, EvidenceResolverConfig
from novastack.generation import AnswerResult, AnswerStatus
from novastack.index_manager import IndexGenerationStatus, IndexManager
from novastack.metadata_diagnostics import build_metadata_snapshot_index
from novastack.models import RecordPermissions, SearchDocument
from novastack.observability import get_metrics, reset_metrics
from novastack.service import AtlasServicePipeline, create_app
from novastack.service.schemas import CallerContext, QueryRequest


class DeterministicEncoder:
    """Small test encoder with the certified 384-dimensional index shape."""

    dimension = 384

    def encode_passages(self, passages: list[str]) -> np.ndarray:
        if not passages:
            return np.empty((0, self.dimension), dtype=np.float32)
        vectors = np.ones((len(passages), self.dimension), dtype=np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors

    def encode_query(self, query: str) -> np.ndarray:
        vector = np.ones(self.dimension, dtype=np.float32)
        return vector / np.linalg.norm(vector)


class PassThroughReranker:
    def rerank(self, candidates, qu=None, metadata_index=None, forbidden_doc_ids=None):
        return candidates


class MarkerGenerator:
    """Returns only the selected evidence marker to make generation visible."""

    def generate_answer(self, package, **kwargs) -> AnswerResult:
        if not package.selected_evidence:
            return AnswerResult(
                answer_id=f"ANS-{package.package_id}",
                evaluation_id=package.evaluation_id,
                query=package.query,
                answer_text="Insufficient evidence to answer this question.",
                answer_status=AnswerStatus.ABSTAINED.value,
                abstention_reason="empty_selected_evidence",
            )
        item = package.selected_evidence[0]
        return AnswerResult(
            answer_id=f"ANS-{package.package_id}",
            evaluation_id=package.evaluation_id,
            query=package.query,
            answer_text=item.text,
            answer_status=AnswerStatus.ANSWERED.value,
            evidence_ids_used=[item.evidence_id],
        )


class GateReranker(PassThroughReranker):
    """Stops a request after retrieval so publication can happen mid-request."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def rerank(self, candidates, qu=None, metadata_index=None, forbidden_doc_ids=None):
        self.entered.set()
        assert self.release.wait(timeout=5), "test did not release the in-flight request"
        return candidates


def make_document(document_id: str, content: str, tenant_id: str = "TENANT-NOVA") -> SearchDocument:
    return SearchDocument(
        document_id=document_id,
        tenant_id=tenant_id,
        source_type="documentation",
        title=f"Title {document_id}",
        content=content,
        department="Engineering",
        author_id="USR-TEST",
        created_at="2026-01-01T00:00:00",
        permissions=RecordPermissions(),
        authority_level="canonical",
    )


def build_manager(documents: list[SearchDocument]) -> tuple[IndexManager, DeterministicEncoder]:
    encoder = DeterministicEncoder()
    chunks = [chunk for document in documents for chunk in chunk_document(document)]
    bm25 = BM25Index.build_index(chunks)
    dense = DenseIndex(chunks, encoder.encode_passages([chunk.text for chunk in chunks]), encoder=encoder)
    metadata = build_metadata_snapshot_index([document.to_dict() for document in documents])
    manager = IndexManager()
    manager.initialize_from_components(documents, chunks, bm25, dense, metadata, corpus_version="4S-test")
    return manager, encoder


def build_pipeline(
    documents: list[SearchDocument],
    reranker: PassThroughReranker | None = None,
) -> tuple[AtlasServicePipeline, IndexManager, DeterministicEncoder]:
    manager, encoder = build_manager(documents)
    initial_snapshot = manager.acquire_active_generation()
    assert initial_snapshot is not None
    try:
        resolver = EvidenceResolver(
            documents_index={document.document_id: document for document in initial_snapshot.snapshot.search_documents},
            chunks_index={chunk.chunk_id: chunk for chunk in initial_snapshot.snapshot.search_chunks},
            config=EvidenceResolverConfig(),
        )
        pipeline = AtlasServicePipeline(
            bm25_index=initial_snapshot.snapshot.bm25_index,
            dense_index=initial_snapshot.snapshot.dense_index,
            reranker=reranker or PassThroughReranker(),
            generator=MarkerGenerator(),
            resolver=resolver,
            metadata_snapshot_index=dict(initial_snapshot.snapshot.metadata_snapshot_index),
            index_manager=manager,
        )
    finally:
        initial_snapshot.close()
    return pipeline, manager, encoder


def request(tenant_id: str = "TENANT-NOVA") -> QueryRequest:
    return QueryRequest(
        query="controlled marker",
        user_context=CallerContext(tenant_id=tenant_id, user_id="USR-TEST", user_role="engineer"),
        evaluation_id="PHASE-4S",
    )


def test_4s_initial_active_generation_is_served_and_leased():
    """A: initial active generation is what the pipeline uses for a new request."""
    pipeline, manager, _ = build_pipeline([make_document("DOC-N", "controlled marker generation-N")])

    response = pipeline.execute_query(request())

    assert response.answer_status == "answered"
    assert response.answer_text == "controlled marker generation-N"
    assert response.index_generation_id == manager.get_active_generation_id()
    assert manager.get_generation_reference_count(response.index_generation_id) == 0


def test_4s_validated_publish_is_visible_without_api_restart_and_ready_is_current():
    """B/C/D/E/M/N: valid N+1 atomically becomes live in the same app process."""
    original = make_document("DOC-N", "controlled marker generation-N")
    pipeline, manager, encoder = build_pipeline([original])
    app = create_app(pipeline=pipeline)

    with TestClient(app) as client:
        first = client.post("/query", json=request().model_dump())
        assert first.status_code == 200
        initial_generation_id = first.json()["index_generation_id"]
        assert first.json()["answer_text"] == "controlled marker generation-N"

        replacement = make_document("DOC-N", "controlled marker generation-N-plus-1")
        published, generation_n_plus_1, errors = manager.apply_incremental_update([replacement], encoder=encoder)

        assert published is True
        assert errors == []
        assert generation_n_plus_1 is not None
        assert generation_n_plus_1.generation_id != initial_generation_id

        # The same app/client instance remains running: no lifecycle restart.
        second = client.post("/query", json=request().model_dump())
        readiness = client.get("/ready")

    assert second.status_code == 200
    assert second.json()["answer_text"] == "controlled marker generation-N-plus-1"
    assert second.json()["index_generation_id"] == generation_n_plus_1.generation_id
    assert readiness.status_code == 200
    assert readiness.json()["active_generation_id"] == generation_n_plus_1.generation_id
    assert manager.get_active_generation_id() == generation_n_plus_1.generation_id


def test_4s_inflight_request_retains_n_until_release_then_retires_safely():
    """F/J/K: a concurrent publication cannot create a mixed-generation request."""
    original = make_document("DOC-N", "controlled marker generation-N")
    gate = GateReranker()
    pipeline, manager, encoder = build_pipeline([original], reranker=gate)
    generation_n = manager.get_active_generation()
    assert generation_n is not None

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(pipeline.execute_query, request())
        assert gate.entered.wait(timeout=5), "request did not enter its leased generation"
        assert manager.get_generation_reference_count(generation_n.generation_id) == 1

        replacement = make_document("DOC-N", "controlled marker generation-N-plus-1")
        published, generation_n_plus_1, errors = manager.apply_incremental_update([replacement], encoder=encoder)

        assert published is True
        assert errors == []
        assert generation_n_plus_1 is not None
        assert generation_n.status == IndexGenerationStatus.ACTIVE.value
        assert manager.get_retiring_generation_ids() == [generation_n.generation_id]

        gate.release.set()
        in_flight_response = future.result(timeout=5)

    assert in_flight_response.answer_text == "controlled marker generation-N"
    assert in_flight_response.index_generation_id == generation_n.generation_id
    assert generation_n.status == IndexGenerationStatus.RETIRED.value
    assert manager.get_retiring_generation_ids() == []


def test_4s_failed_build_validation_and_partial_candidate_leave_n_active():
    """G/H/I: build and validation failures never expose candidate components."""
    original = make_document("DOC-N", "controlled marker generation-N")
    pipeline, manager, encoder = build_pipeline([original])
    generation_n = manager.get_active_generation()
    assert generation_n is not None
    replacement = make_document("DOC-N", "controlled marker generation-N-plus-1")

    class FailingEncoder:
        def encode_passages(self, passages):
            raise RuntimeError("controlled dense build failure")

    built, candidate, errors = manager.apply_incremental_update([replacement], encoder=FailingEncoder())
    assert built is False
    assert candidate is None
    assert "construction failed" in errors[0]
    assert manager.get_active_generation_id() == generation_n.generation_id
    assert pipeline.execute_query(request()).answer_text == "controlled marker generation-N"

    class InvalidEncoder:
        def encode_passages(self, passages):
            vectors = encoder.encode_passages(passages)
            vectors[0, 0] = np.nan
            return vectors

    validated, candidate, errors = manager.apply_incremental_update([replacement], encoder=InvalidEncoder())
    assert validated is False
    assert candidate is None
    assert any("NaN" in error for error in errors)
    assert manager.get_active_generation_id() == generation_n.generation_id
    assert manager.search_documents[0].content == "controlled marker generation-N"
    assert pipeline.execute_query(request()).answer_text == "controlled marker generation-N"


def test_4s_tenant_isolation_remains_intact_after_swap():
    """L: the newly published BM25/dense snapshot retains tenant boundaries."""
    nova = make_document("DOC-NOVA", "controlled marker nova-N", "TENANT-NOVA")
    orbital = make_document("DOC-ORBITAL", "controlled marker orbital-N", "TENANT-ORBITAL")
    pipeline, manager, encoder = build_pipeline([nova, orbital])

    orbital_updated = make_document("DOC-ORBITAL", "controlled marker orbital-N-plus-1", "TENANT-ORBITAL")
    published, generation_n_plus_1, errors = manager.apply_incremental_update([nova, orbital_updated], encoder=encoder)

    assert published is True
    assert errors == []
    assert generation_n_plus_1 is not None
    response = pipeline.execute_query(request("TENANT-NOVA"))
    assert response.answer_status == "answered"
    assert response.answer_text == "controlled marker nova-N"
    assert "orbital" not in response.answer_text
    assert response.index_generation_id == generation_n_plus_1.generation_id


def test_4s_lifecycle_metrics_remain_bounded_and_do_not_label_generation_ids():
    """Observability exposes publication state without unbounded metric labels."""
    reset_metrics()
    original = make_document("DOC-N", "controlled marker generation-N")
    _, manager, encoder = build_pipeline([original])
    replacement = make_document("DOC-N", "controlled marker generation-N-plus-1")

    published, _, errors = manager.apply_incremental_update([replacement], encoder=encoder)

    assert published is True
    assert errors == []
    metrics = get_metrics().generate_prometheus_text()
    assert "atlas_active_generation 1" in metrics
    assert "atlas_index_publish_failures_total" in metrics
    assert "generation_id=" not in metrics
