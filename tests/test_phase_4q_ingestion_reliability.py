"""Phase 4Q: Ingestion & Index Reliability Test Suite.

Comprehensive validation covering:
1. Ingestion Failure Isolation (one bad record does not abort batch; per-record quarantine).
2. Dead-Letter Queue (DLQ) tracking, retryable vs non-retryable classification, and safe storage.
3. Incremental Indexing Lifecycle (NEW, UNCHANGED, UPDATED, DELETED with content hash verification).
4. Atomic Generation Management & Out-of-Band Publish (failed candidate preserves active generation).
5. Deep Index Integrity Verification (BM25 vs Dense chunk alignment, vector dimensions, NaN/Inf checks, orphan chunks).
6. Multi-Tenant Ingestion Isolation (cross-tenant collision, cross-tenant referential links).
7. Startup Recovery & Deep Readiness (/ready returns 200 when healthy, 503 when desynchronized/corrupt).
8. Frozen Production Configuration Verification (A=False, B=True, C=False).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import numpy as np
import pytest
from fastapi.testclient import TestClient

from novastack.bm25 import BM25Config, BM25Index
from novastack.chunking import chunk_document
from novastack.dense import DenseConfig, DenseEncoder, DenseIndex
from novastack.evidence_resolution import EvidenceResolverConfig
from novastack.index_manager import (
    IndexGeneration,
    IndexGenerationStatus,
    IndexManager,
    compute_document_content_hash,
    validate_index_integrity,
)
from novastack.ingestion import (
    IngestionBatchResult,
    IngestionReport,
    ingest_records,
    ingest_records_isolated,
    normalize_source_record,
    validate_single_source_record,
    validate_source_record_batch,
)
from novastack.models import RecordPermissions, SearchChunk, SearchDocument, SourceRecord
from novastack.observability import get_metrics, reset_metrics
from novastack.quarantine import DeadLetterQueue, QuarantinedRecord
from novastack.service import AtlasServicePipeline, create_app


# ---------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------

class MockDeterministicEncoder:
    """Fast deterministic encoder producing normalized (N, 384) vectors for testing."""

    def __init__(self, dimension: int = 384):
        self.dimension = dimension

    def encode_passages(self, passages: list[str]) -> np.ndarray:
        if not passages:
            return np.empty((0, self.dimension), dtype=np.float32)
        n = len(passages)
        rng = np.random.RandomState(42)
        vectors = rng.randn(n, self.dimension).astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / norms

    def encode_query(self, query: str) -> np.ndarray:
        rng = np.random.RandomState(hash(query) % (2**31))
        vec = rng.randn(self.dimension).astype(np.float32)
        return vec / np.linalg.norm(vec)


def make_valid_record(
    doc_id: str,
    tenant_id: str = "TENANT-NOVASTACK",
    title: str = "Valid Title",
    content: str = "Valid content body describing technical procedure.",
    source_type: str = "documentation",
    version: str = "1.0",
    parent_id: str | None = None,
    supersedes_id: str | None = None,
) -> SourceRecord:
    return SourceRecord(
        document_id=doc_id,
        tenant_id=tenant_id,
        source_type=source_type,
        title=title,
        content=content,
        author_id="usr_test_01",
        department="Engineering",
        created_at=datetime(2026, 1, 15, 10, 0, 0),
        version=version,
        status="published",
        classification="internal",
        permissions=RecordPermissions(
            allowed_roles=["engineer"],
            allowed_departments=["Engineering"],
            allowed_teams=["Core"],
            allowed_user_ids=["usr_test_01"],
        ),
        parent_id=parent_id,
        supersedes_id=supersedes_id,
        authority_level="high",
    )


# =====================================================================
# 1. INGESTION FAILURE ISOLATION & VALIDATION
# =====================================================================

class TestIngestionFailureIsolation:
    """Verifies that one bad document never destroys the valid corpus."""

    def test_valid_document_accepted(self, tmp_path: Path):
        dlq = DeadLetterQueue(tmp_path / "dlq.jsonl")
        rec = make_valid_record("DOC-VALID-01")
        res = ingest_records_isolated([rec], dlq=dlq)

        assert len(res.accepted_documents) == 1
        assert len(res.quarantined_records) == 0
        assert res.accepted_documents[0].document_id == "DOC-VALID-01"
        assert res.report.successful_count == 1

    def test_one_bad_document_does_not_abort_batch(self, tmp_path: Path):
        """Invariant: Batch containing 1 bad record and 3 good records results in 3 accepted and 1 quarantined."""
        dlq = DeadLetterQueue(tmp_path / "dlq.jsonl")
        records = [
            make_valid_record("DOC-GOOD-01"),
            make_valid_record("DOC-BAD-02", content=""),  # Empty content violates schema
            make_valid_record("DOC-GOOD-03"),
            make_valid_record("DOC-GOOD-04"),
        ]
        res = ingest_records_isolated(records, dlq=dlq)

        assert len(res.accepted_documents) == 3
        assert len(res.quarantined_records) == 1
        assert res.accepted_documents[0].document_id == "DOC-GOOD-01"
        assert res.accepted_documents[1].document_id == "DOC-GOOD-03"
        assert res.accepted_documents[2].document_id == "DOC-GOOD-04"
        assert res.quarantined_records[0].document_id == "DOC-BAD-02"
        assert "content is empty or missing" in res.quarantined_records[0].error_message

    def test_invalid_tenant_rejected(self, tmp_path: Path):
        dlq = DeadLetterQueue(tmp_path / "dlq.jsonl")
        rec = make_valid_record("DOC-BAD-TENANT", tenant_id="TENANT-MALICIOUS-HACKER")
        res = ingest_records_isolated([rec], dlq=dlq)

        assert len(res.accepted_documents) == 0
        assert len(res.quarantined_records) == 1
        assert res.quarantined_records[0].error_type == "InvalidTenantError"
        assert "invalid tenant_id" in res.quarantined_records[0].error_message

    def test_duplicate_document_detected_in_batch(self, tmp_path: Path):
        dlq = DeadLetterQueue(tmp_path / "dlq.jsonl")
        records = [
            make_valid_record("DOC-DUP-01", title="Original"),
            make_valid_record("DOC-DUP-01", title="Duplicate instance"),
        ]
        res = ingest_records_isolated(records, dlq=dlq)

        assert len(res.accepted_documents) == 1
        assert len(res.quarantined_records) == 1
        assert res.quarantined_records[0].error_type == "DuplicateDocumentId"
        assert res.accepted_documents[0].title == "Original"

    def test_invalid_temporal_metadata_quarantined(self, tmp_path: Path):
        dlq = DeadLetterQueue(tmp_path / "dlq.jsonl")
        rec = make_valid_record("DOC-TIME-INVALID")
        rec.valid_from = datetime(2026, 5, 1)
        rec.valid_until = datetime(2026, 4, 1)  # valid_from > valid_until
        res = ingest_records_isolated([rec], dlq=dlq)

        assert len(res.accepted_documents) == 0
        assert len(res.quarantined_records) == 1
        assert res.quarantined_records[0].error_type == "TemporalBoundsViolation"


# =====================================================================
# 2. DEAD-LETTER QUEUE (DLQ) TRACKING
# =====================================================================

class TestDeadLetterQueue:
    """Verifies durable quarantine storage, error classification, and retry semantics."""

    def test_failed_document_persisted_to_dlq(self, tmp_path: Path):
        dlq_file = tmp_path / "quarantined_records.jsonl"
        dlq = DeadLetterQueue(dlq_file)

        record = QuarantinedRecord(
            document_id="DOC-FAIL-100",
            tenant_id="TENANT-NOVASTACK",
            source_type="policy",
            ingestion_stage="validation",
            error_type="ValidationError",
            error_message="Missing department metadata",
            retryable=False,
            payload_preview={"title": "Test Title"},
        )
        dlq.quarantine(record)

        assert dlq_file.exists()
        persisted = dlq.list_records()
        assert len(persisted) == 1
        assert persisted[0].document_id == "DOC-FAIL-100"
        assert persisted[0].error_message == "Missing department metadata"
        assert persisted[0].retryable is False

    def test_retryable_classification_on_dangling_reference(self, tmp_path: Path):
        dlq = DeadLetterQueue(tmp_path / "dlq.jsonl")
        # Record referencing parent not yet present in active corpus
        rec = make_valid_record("DOC-CHILD", parent_id="DOC-PARENT-UNINGESTED")
        res = ingest_records_isolated([rec], dlq=dlq)

        assert len(res.accepted_documents) == 0
        assert len(res.quarantined_records) == 1
        assert res.quarantined_records[0].retryable is True
        assert "parent_id 'DOC-PARENT-UNINGESTED' not found" in res.quarantined_records[0].error_message

    def test_non_retryable_classification_on_structural_violation(self, tmp_path: Path):
        dlq = DeadLetterQueue(tmp_path / "dlq.jsonl")
        rec = make_valid_record("DOC-STRUCT-BAD", source_type="invalid_custom_type")
        res = ingest_records_isolated([rec], dlq=dlq)

        assert len(res.quarantined_records) == 1
        assert res.quarantined_records[0].retryable is False

    def test_dlq_lookup_by_tenant(self, tmp_path: Path):
        dlq = DeadLetterQueue(tmp_path / "dlq.jsonl")
        dlq.quarantine(QuarantinedRecord("D1", "TENANT-NOVASTACK", "p", "v", "err", "msg", False))
        dlq.quarantine(QuarantinedRecord("D2", "TENANT-ORBITAL", "p", "v", "err", "msg", False))

        novastack_fails = dlq.list_records(tenant_id="TENANT-NOVASTACK")
        orbital_fails = dlq.list_records(tenant_id="TENANT-ORBITAL")

        assert len(novastack_fails) == 1
        assert novastack_fails[0].document_id == "D1"
        assert len(orbital_fails) == 1
        assert orbital_fails[0].document_id == "D2"


# =====================================================================
# 3. INCREMENTAL INDEXING & CONTENT HASHING
# =====================================================================

class TestIncrementalIndexing:
    """Verifies NEW, UNCHANGED, UPDATED, and DELETED document lifecycle handling."""

    def test_content_hash_identifies_unchanged_and_changed(self):
        doc1 = normalize_source_record(make_valid_record("DOC-1", content="Original content"))
        doc1_same = normalize_source_record(make_valid_record("DOC-1", content="Original content"))
        doc1_modified = normalize_source_record(make_valid_record("DOC-1", content="Updated content modified"))

        hash1 = compute_document_content_hash(doc1)
        hash1_same = compute_document_content_hash(doc1_same)
        hash1_mod = compute_document_content_hash(doc1_modified)

        assert hash1 == hash1_same, "Identical documents must produce identical hash"
        assert hash1 != hash1_mod, "Modified document must produce different hash"

    def test_incremental_update_adds_new_document(self):
        manager = IndexManager()
        encoder = MockDeterministicEncoder()

        # Initial baseline: 2 documents
        docA = normalize_source_record(make_valid_record("DOC-A", content="First document"))
        docB = normalize_source_record(make_valid_record("DOC-B", content="Second document"))
        chunks = chunk_document(docA) + chunk_document(docB)
        bm25 = BM25Index.build_index(chunks)
        dense = DenseIndex(chunks, encoder.encode_passages([c.text for c in chunks]), encoder=encoder)
        manager.initialize_from_components([docA, docB], chunks, bm25, dense, {"DOC-A": {}, "DOC-B": {}})

        # Incremental update: Add DOC-C
        docC = normalize_source_record(make_valid_record("DOC-C", content="Third document"))
        success, gen, errors = manager.apply_incremental_update(
            new_or_updated_documents=[docA, docB, docC],
            encoder=encoder,
        )

        assert success is True
        assert gen is not None
        assert gen.document_count == 3
        assert len(manager.search_documents) == 3
        assert any(d.document_id == "DOC-C" for d in manager.search_documents)

    def test_incremental_update_skips_unchanged_document(self):
        manager = IndexManager()
        encoder = MockDeterministicEncoder()

        docA = normalize_source_record(make_valid_record("DOC-A", content="Doc A Content"))
        chunks = chunk_document(docA)
        bm25 = BM25Index.build_index(chunks)
        dense = DenseIndex(chunks, encoder.encode_passages([c.text for c in chunks]), encoder=encoder)
        manager.initialize_from_components([docA], chunks, bm25, dense, {"DOC-A": {}})

        # Re-apply exact same document
        success, gen, msg = manager.apply_incremental_update([docA], encoder=encoder)
        assert success is True
        assert "No changes detected" in msg[0]

    def test_incremental_deletion_removes_document(self):
        manager = IndexManager()
        encoder = MockDeterministicEncoder()

        docA = normalize_source_record(make_valid_record("DOC-A"))
        docB = normalize_source_record(make_valid_record("DOC-B"))
        chunks = chunk_document(docA) + chunk_document(docB)
        bm25 = BM25Index.build_index(chunks)
        dense = DenseIndex(chunks, encoder.encode_passages([c.text for c in chunks]), encoder=encoder)
        manager.initialize_from_components([docA, docB], chunks, bm25, dense, {"DOC-A": {}, "DOC-B": {}})

        # Delete DOC-B
        success, gen, errors = manager.apply_incremental_update(
            new_or_updated_documents=[docA],
            deleted_document_ids=["DOC-B"],
            encoder=encoder,
        )

        assert success is True
        assert gen.document_count == 1
        assert len(manager.search_documents) == 1
        assert manager.search_documents[0].document_id == "DOC-A"
        assert "DOC-B" in gen.tombstoned_document_ids


# =====================================================================
# 4. ATOMIC PUBLISH & ROLLBACK PROTECTION
# =====================================================================

class TestAtomicPublishAndRollback:
    """Verifies that candidate failures never corrupt or overwrite the active generation."""

    def test_failed_candidate_preserves_active_generation(self):
        manager = IndexManager()
        encoder = MockDeterministicEncoder()

        docA = normalize_source_record(make_valid_record("DOC-A", content="Content A"))
        chunks = chunk_document(docA)
        bm25 = BM25Index.build_index(chunks)
        dense = DenseIndex(chunks, encoder.encode_passages([c.text for c in chunks]), encoder=encoder)
        initial_gen = manager.initialize_from_components([docA], chunks, bm25, dense, {"DOC-A": {}})

        # Attempt incremental update with broken encoder returning NaN
        class CorruptEncoder:
            def encode_passages(self, passages):
                arr = np.ones((len(passages), 384), dtype=np.float32)
                arr[0, 0] = np.nan  # Inject NaN
                return arr

        docB = normalize_source_record(make_valid_record("DOC-B", content="Content B"))
        success, gen, errors = manager.apply_incremental_update(
            new_or_updated_documents=[docA, docB],
            encoder=CorruptEncoder(),
        )

        # Update must fail, and active generation must remain intact
        assert success is False
        assert gen is None
        assert any("NaN" in e for e in errors)

        active = manager.get_active_generation()
        assert active.generation_id == initial_gen.generation_id
        assert active.document_count == 1
        assert len(manager.search_documents) == 1
        assert manager.search_documents[0].document_id == "DOC-A"

    def test_corrupted_candidate_vector_dimension_rejected(self):
        manager = IndexManager()
        encoder = MockDeterministicEncoder()

        docA = normalize_source_record(make_valid_record("DOC-A"))
        chunks = chunk_document(docA)
        bm25 = BM25Index.build_index(chunks)
        dense = DenseIndex(chunks, encoder.encode_passages([c.text for c in chunks]), encoder=encoder)
        manager.initialize_from_components([docA], chunks, bm25, dense, {"DOC-A": {}})

        # Attempt candidate with 512 dimensions instead of 384
        class MismatchedDimEncoder:
            def encode_passages(self, passages):
                return np.zeros((len(passages), 512), dtype=np.float32)

        docB = normalize_source_record(make_valid_record("DOC-B"))
        success, gen, errors = manager.apply_incremental_update([docA, docB], encoder=MismatchedDimEncoder())

        assert success is False
        assert any("dimension" in e.lower() for e in errors)
        assert manager.active_generation.document_count == 1


# =====================================================================
# 5. DEEP INDEX INTEGRITY VERIFICATION
# =====================================================================

class TestDeepIndexIntegrity:
    """Verifies structural, semantic, and alignment validation rules."""

    def test_chunk_count_mismatch_detected(self):
        encoder = MockDeterministicEncoder()
        doc = normalize_source_record(make_valid_record("DOC-1"))
        chunks = chunk_document(doc)

        bm25 = BM25Index.build_index(chunks)
        # Artificially shorten dense index chunks
        dense_vectors = encoder.encode_passages([c.text for c in chunks[:-1]])
        dense = DenseIndex(chunks[:-1], dense_vectors)

        res = validate_index_integrity(bm25, dense, [doc], chunks)
        assert res.is_valid is False
        assert any("Chunk count mismatch" in e for e in res.errors)

    def test_chunk_id_sequence_mismatch_detected(self):
        encoder = MockDeterministicEncoder()
        paragraph_1 = "First chunk text paragraph with ample enterprise context. " * 15
        paragraph_2 = "Second chunk text paragraph discussing distinct technical details. " * 15
        doc = normalize_source_record(make_valid_record("DOC-1", content=f"{paragraph_1}\n\n{paragraph_2}"))
        chunks = chunk_document(doc)
        assert len(chunks) >= 2

        bm25 = BM25Index.build_index(chunks)
        # Reverse order in dense index
        reversed_chunks = list(reversed(chunks))
        dense_vectors = encoder.encode_passages([c.text for c in reversed_chunks])
        dense = DenseIndex(reversed_chunks, dense_vectors)

        res = validate_index_integrity(bm25, dense, [doc], chunks)
        assert res.is_valid is False
        assert any("Chunk ID misalignment" in e for e in res.errors)

    def test_orphan_chunk_detected(self):
        encoder = MockDeterministicEncoder()
        doc = normalize_source_record(make_valid_record("DOC-1"))
        chunks = chunk_document(doc)
        # Make chunk reference non-existent document
        orphan_chunk = SearchChunk.from_dict(chunks[0].to_dict())
        orphan_chunk.document_id = "DOC-GHOST"
        chunks.append(orphan_chunk)

        bm25 = BM25Index.build_index(chunks)
        dense = DenseIndex(chunks, encoder.encode_passages([c.text for c in chunks]))

        res = validate_index_integrity(bm25, dense, [doc], chunks)
        assert res.is_valid is False
        assert any("orphan chunk" in e.lower() for e in res.errors)


# =====================================================================
# 6. MULTI-TENANT INGESTION ISOLATION
# =====================================================================

class TestMultiTenantIngestionIsolation:
    """Verifies that tenant boundaries cannot be corrupted during ingestion."""

    def test_cross_tenant_document_id_collision_rejected(self, tmp_path: Path):
        dlq = DeadLetterQueue(tmp_path / "dlq.jsonl")
        # Active corpus has DOC-001 owned by TENANT-NOVASTACK
        active_docs = {"DOC-001": "TENANT-NOVASTACK"}

        # Malicious record from TENANT-ORBITAL attempting to overwrite DOC-001
        spoofed_record = make_valid_record("DOC-001", tenant_id="TENANT-ORBITAL")
        res = ingest_records_isolated([spoofed_record], existing_documents=active_docs, dlq=dlq)

        assert len(res.accepted_documents) == 0
        assert len(res.quarantined_records) == 1
        assert res.quarantined_records[0].error_type == "CrossTenantCollision"
        assert "cross-tenant ID collision" in res.quarantined_records[0].error_message

    def test_cross_tenant_parent_reference_rejected(self, tmp_path: Path):
        dlq = DeadLetterQueue(tmp_path / "dlq.jsonl")
        # Record in TENANT-ORBITAL claiming parent in TENANT-NOVASTACK
        records = [
            make_valid_record("PARENT-NOVA", tenant_id="TENANT-NOVASTACK"),
            make_valid_record("CHILD-ORBITAL", tenant_id="TENANT-ORBITAL", parent_id="PARENT-NOVA"),
        ]
        res = ingest_records_isolated(records, dlq=dlq)

        assert len(res.accepted_documents) == 1
        assert res.accepted_documents[0].document_id == "PARENT-NOVA"
        assert len(res.quarantined_records) == 1
        assert res.quarantined_records[0].document_id == "CHILD-ORBITAL"
        assert res.quarantined_records[0].error_type == "CrossTenantViolation"

    def test_cross_tenant_supersedes_reference_rejected(self, tmp_path: Path):
        dlq = DeadLetterQueue(tmp_path / "dlq.jsonl")
        # Record in TENANT-PINECONE claiming to supersede a record in TENANT-NOVASTACK
        records = [
            make_valid_record("ORIGINAL-NOVA", tenant_id="TENANT-NOVASTACK"),
            make_valid_record("REVISED-PINECONE", tenant_id="TENANT-PINECONE", supersedes_id="ORIGINAL-NOVA"),
        ]
        res = ingest_records_isolated(records, dlq=dlq)

        assert len(res.accepted_documents) == 1
        assert len(res.quarantined_records) == 1
        assert res.quarantined_records[0].document_id == "REVISED-PINECONE"
        assert res.quarantined_records[0].error_type == "CrossTenantViolation"


# =====================================================================
# 7. STARTUP RECOVERY & DEEP READINESS PROBE
# =====================================================================

class TestStartupAndReadiness:
    """Verifies that /ready reflects actual index health and prevents serving on corruption."""

    def test_healthy_pipeline_returns_ready_200(self):
        encoder = MockDeterministicEncoder()
        doc = normalize_source_record(make_valid_record("DOC-1"))
        chunks = chunk_document(doc)
        bm25 = BM25Index.build_index(chunks)
        dense = DenseIndex(chunks, encoder.encode_passages([c.text for c in chunks]))

        pipe = AtlasServicePipeline(
            bm25_index=bm25,
            dense_index=dense,
            reranker="mock_reranker",
            generator="mock_generator",
        )
        app = create_app(pipeline=pipe)
        with TestClient(app) as client:
            resp = client.get("/ready")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ready"

    def test_desynchronized_indexes_cause_ready_503(self):
        encoder = MockDeterministicEncoder()
        doc = normalize_source_record(make_valid_record("DOC-1"))
        chunks = chunk_document(doc)

        bm25 = BM25Index.build_index(chunks)
        # Dense index has only 0 chunks
        dense = DenseIndex([], np.empty((0, 384), dtype=np.float32))

        pipe = AtlasServicePipeline(
            bm25_index=bm25,
            dense_index=dense,
            reranker="mock_reranker",
            generator="mock_generator",
        )
        app = create_app(pipeline=pipe)
        with TestClient(app) as client:
            resp = client.get("/ready")
            assert resp.status_code == 503
            assert resp.json()["status"] == "unready"
            assert resp.json()["components"]["bm25"] is False or resp.json()["components"]["dense"] is False

    def test_nan_vector_causes_ready_503(self):
        encoder = MockDeterministicEncoder()
        doc = normalize_source_record(make_valid_record("DOC-1"))
        chunks = chunk_document(doc)

        bm25 = BM25Index.build_index(chunks)
        corrupted_vectors = encoder.encode_passages([c.text for c in chunks])
        corrupted_vectors[0, 0] = np.nan
        dense = DenseIndex(chunks, corrupted_vectors)

        pipe = AtlasServicePipeline(
            bm25_index=bm25,
            dense_index=dense,
            reranker="mock_reranker",
            generator="mock_generator",
        )
        app = create_app(pipeline=pipe)
        with TestClient(app) as client:
            resp = client.get("/ready")
            assert resp.status_code == 503
            assert resp.json()["status"] == "unready"


# =====================================================================
# 8. OBSERVABILITY & FROZEN CONFIGURATION INVARIANTS
# =====================================================================

class TestObservabilityAndFrozenInvariants:
    """Verifies metrics recording and frozen production configuration invariants."""

    def test_ingestion_metrics_recorded(self, tmp_path: Path):
        reset_metrics()
        dlq = DeadLetterQueue(tmp_path / "dlq.jsonl")
        records = [
            make_valid_record("DOC-OK-1"),
            make_valid_record("DOC-FAIL-1", title=""),
        ]
        ingest_records_isolated(records, dlq=dlq)

        metrics_text = get_metrics().generate_prometheus_text()
        assert 'atlas_ingestion_documents_total{status="accepted"} 1' in metrics_text
        assert 'atlas_ingestion_documents_total{status="quarantined"} 1' in metrics_text
        assert "atlas_ingestion_dlq_total" in metrics_text

    def test_production_flags_remain_frozen(self):
        """Hard safety gate: Mechanism A=OFF, Mechanism B=ON, Mechanism C=OFF."""
        import inspect
        from novastack.event_evidence_bundler import EventBundlerConfig
        from novastack.evidence_resolution import EvidenceResolverConfig
        from novastack.generation import GroundedAnswerGenerator

        # Mechanism B: Query-Aware Authority Preservation (Frozen default: True)
        resolver_cfg = EvidenceResolverConfig()
        assert resolver_cfg.enable_query_aware_authority is True, "Mechanism B default must remain True"

        # Mechanism C: Event-Centric Evidence Bundling (Frozen default: False)
        assert resolver_cfg.enable_event_bundling is False, "Mechanism C in resolver must remain False"
        assert EventBundlerConfig().enable_event_bundling is False, "Mechanism C in bundler must remain False"

        # Mechanism A: Boundary Sentence Stitching (Frozen default: False)
        sig = inspect.signature(GroundedAnswerGenerator.generate_answer)
        assert sig.parameters["enable_boundary_stitching"].default is False, "Mechanism A must remain False"
