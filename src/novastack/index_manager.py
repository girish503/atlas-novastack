"""Phase 4Q: Ingestion & Index Reliability — Atomic Index Generation Manager.

Provides:
- Incremental indexing lifecycle: NEW, UPDATED, DELETED, and UNCHANGED detection via content hashing.
- Immutable index generations (IndexGeneration) with strict status transitions.
- Deep index integrity validation (BM25 vs Dense chunk alignment, vector dimensions, NaN/Inf checks, orphan chunks).
- Atomic publish: Candidate indexes are validated out-of-band; invalid or corrupted builds are rejected with zero mutation to the active generation.
- Rollback and fail-safe recovery.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional, Set

import numpy as np

from novastack.bm25 import BM25Config, BM25Index
from novastack.chunking import ChunkingConfig, chunk_document
from novastack.dense import DenseConfig, DenseEncoder, DenseIndex, format_passage_text
from novastack.metadata_diagnostics import build_metadata_snapshot_index
from novastack.models import RecordPermissions, SearchChunk, SearchDocument, SourceRecord
from novastack.observability import get_metrics, log_event


logger = logging.getLogger("novastack.index_manager")


class IndexGenerationStatus(str, Enum):
    BUILDING = "BUILDING"
    VALIDATING = "VALIDATING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    RETIRED = "RETIRED"


def compute_document_content_hash(doc: SearchDocument | SourceRecord | dict[str, Any]) -> str:
    """Compute deterministic SHA-256 fingerprint of a document's semantic and security fields.

    Detects whether a document has genuinely changed or is UNCHANGED.
    """
    if isinstance(doc, (SearchDocument, SourceRecord)):
        data = {
            "document_id": str(doc.document_id).strip(),
            "tenant_id": str(doc.tenant_id).strip(),
            "source_type": str(doc.source_type).strip(),
            "title": str(doc.title).strip(),
            "content": str(doc.content).strip(),
            "version": str(doc.version).strip(),
            "status": str(doc.status).strip(),
            "classification": str(doc.classification).strip(),
            "authority_level": str(doc.authority_level).strip(),
            "parent_id": str(doc.parent_id).strip() if doc.parent_id else None,
            "supersedes_id": str(doc.supersedes_id).strip() if doc.supersedes_id else None,
            "allowed_roles": sorted(doc.permissions.allowed_roles) if doc.permissions else [],
            "allowed_departments": sorted(doc.permissions.allowed_departments) if doc.permissions else [],
            "allowed_teams": sorted(doc.permissions.allowed_teams) if doc.permissions else [],
            "allowed_user_ids": sorted(doc.permissions.allowed_user_ids) if doc.permissions else [],
        }
    elif isinstance(doc, dict):
        perms = doc.get("permissions") or {}
        if isinstance(perms, RecordPermissions):
            perms_dict = asdict(perms)
        elif isinstance(perms, dict):
            perms_dict = perms
        else:
            perms_dict = {}

        data = {
            "document_id": str(doc.get("document_id", "")).strip(),
            "tenant_id": str(doc.get("tenant_id", "")).strip(),
            "source_type": str(doc.get("source_type", "")).strip(),
            "title": str(doc.get("title", "")).strip(),
            "content": str(doc.get("content", "")).strip(),
            "version": str(doc.get("version", "1.0")).strip(),
            "status": str(doc.get("status", "published")).strip(),
            "classification": str(doc.get("classification", "internal")).strip(),
            "authority_level": str(doc.get("authority_level", "medium")).strip(),
            "parent_id": str(doc.get("parent_id", "")).strip() or None,
            "supersedes_id": str(doc.get("supersedes_id", "")).strip() or None,
            "allowed_roles": sorted(perms_dict.get("allowed_roles", [])),
            "allowed_departments": sorted(perms_dict.get("allowed_departments", [])),
            "allowed_teams": sorted(perms_dict.get("allowed_teams", [])),
            "allowed_user_ids": sorted(perms_dict.get("allowed_user_ids", [])),
        }
    else:
        raise TypeError(f"Unsupported document type for hashing: {type(doc)}")

    canonical_json = json.dumps(data, sort_keys=True)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


@dataclass
class IndexGeneration:
    """Metadata descriptor for an immutable index generation snapshot."""

    generation_id: str
    corpus_version: str
    created_at: str
    document_count: int
    chunk_count: int
    status: str
    checksum: str
    content_hashes: dict[str, str] = field(default_factory=dict)
    active_document_ids: list[str] = field(default_factory=list)
    tombstoned_document_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IndexGeneration:
        return cls(**data)


@dataclass
class IndexValidationResult:
    """Result of deep index integrity verification."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IndexGenerationSnapshot:
    """Immutable component bundle used by one request for its full lifetime.

    The objects inside a snapshot are built and validated before publication and
    are never modified after it is published.  This makes a single pointer
    exchange sufficient to publish a new retrieval generation safely.
    """

    generation: IndexGeneration
    bm25_index: BM25Index
    dense_index: DenseIndex
    search_documents: tuple[SearchDocument, ...]
    search_chunks: tuple[SearchChunk, ...]
    metadata_snapshot_index: Mapping[str, Any]


class IndexGenerationLease:
    """Reference-counted ownership of one active generation snapshot."""

    def __init__(self, manager: "IndexManager", snapshot: IndexGenerationSnapshot) -> None:
        self._manager = manager
        self.snapshot = snapshot
        self._closed = False

    @property
    def generation_id(self) -> str:
        return self.snapshot.generation.generation_id

    def close(self) -> None:
        """Release this request's reference exactly once."""
        if not self._closed:
            self._closed = True
            self._manager._release_generation(self.snapshot)

    def __enter__(self) -> "IndexGenerationLease":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def validate_index_integrity(
    bm25_index: Optional[BM25Index],
    dense_index: Optional[DenseIndex],
    search_documents: list[SearchDocument],
    search_chunks: list[SearchChunk],
    metadata_snapshot_index: Optional[dict[str, Any]] = None,
    expected_dimension: int = 384,
) -> IndexValidationResult:
    """Execute deep structural, semantic, and alignment integrity verification across indexes.

    Checks:
    1. Null checks: BM25 and Dense indexes must be instantiated.
    2. Chunk count alignment: BM25 chunks == Dense chunks == search_chunks.
    3. 1:1 chunk ID ordering: bm25.chunks[i].chunk_id == dense.chunks[i].chunk_id in exact sequence.
    4. Dense vector dimension: shape[1] must match expected_dimension (384).
    5. Vector finite values: strictly zero NaN, Inf, or -Inf values in vector matrix.
    6. Vector row count matches chunk count.
    7. No duplicate chunk IDs within chunk sequence.
    8. No orphan chunks: all chunk.document_id must exist in search_documents.
    9. Cross-tenant chunk consistency: chunk.tenant_id must match parent SearchDocument.tenant_id.
    10. Metadata snapshot alignment: all documents present in metadata_snapshot_index if provided.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Null checks
    if bm25_index is None:
        errors.append("bm25_index is None")
    if dense_index is None:
        errors.append("dense_index is None")

    if bm25_index is None or dense_index is None:
        return IndexValidationResult(is_valid=False, errors=errors)

    # 2. Chunk count alignment
    n_bm25 = len(bm25_index.chunks)
    n_dense = len(dense_index.chunks)
    n_chunks = len(search_chunks)

    if n_bm25 != n_dense:
        errors.append(
            f"Chunk count mismatch between BM25 ({n_bm25}) and Dense ({n_dense})"
        )
    if n_chunks != n_bm25:
        errors.append(
            f"SearchChunk count ({n_chunks}) does not match BM25 chunk count ({n_bm25})"
        )

    # 3. 1:1 chunk ID sequence matching
    if n_bm25 == n_dense:
        for i in range(n_bm25):
            c_bm25 = bm25_index.chunks[i].chunk_id
            c_dense = dense_index.chunks[i].chunk_id
            if c_bm25 != c_dense:
                errors.append(
                    f"Chunk ID misalignment at index {i}: BM25='{c_bm25}' vs Dense='{c_dense}'"
                )
                break

    # 4. Dense vector dimensions and shape
    if dense_index.vectors is None:
        errors.append("dense_index.vectors is None")
    else:
        v_shape = dense_index.vectors.shape
        if len(v_shape) != 2:
            errors.append(f"Dense vector matrix must be 2D, found shape {v_shape}")
        else:
            if v_shape[0] != n_dense:
                errors.append(
                    f"Dense vector row count ({v_shape[0]}) does not match dense chunks count ({n_dense})"
                )
            if v_shape[1] != expected_dimension:
                errors.append(
                    f"Dense vector dimension ({v_shape[1]}) does not match expected ({expected_dimension})"
                )

            # 5. NaN and Inf checks
            if np.isnan(dense_index.vectors).any():
                errors.append("Dense vector matrix contains NaN values")
            if np.isinf(dense_index.vectors).any():
                errors.append("Dense vector matrix contains Infinite values")

    # 7. Duplicate chunk IDs
    chunk_ids = [c.chunk_id for c in search_chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
        errors.append("Duplicate chunk IDs detected in SearchChunk sequence")

    # 8. Orphan chunks check
    doc_ids_set = {d.document_id for d in search_documents}
    doc_tenants = {d.document_id: d.tenant_id for d in search_documents}
    orphan_count = 0
    tenant_mismatch_count = 0

    for c in search_chunks:
        if c.document_id not in doc_ids_set:
            orphan_count += 1
        elif doc_tenants.get(c.document_id) != c.tenant_id:
            tenant_mismatch_count += 1

    if orphan_count > 0:
        errors.append(f"{orphan_count} orphan chunk(s) detected: referenced document_id does not exist")
    if tenant_mismatch_count > 0:
        errors.append(
            f"{tenant_mismatch_count} cross-tenant chunk anomaly: chunk.tenant_id does not match parent document"
        )

    # 10. Metadata snapshot index alignment
    if metadata_snapshot_index is not None:
        missing_meta = [d_id for d_id in doc_ids_set if d_id not in metadata_snapshot_index]
        if missing_meta:
            errors.append(f"{len(missing_meta)} document(s) missing from metadata snapshot index")

    is_valid = len(errors) == 0
    return IndexValidationResult(is_valid=is_valid, errors=errors, warnings=warnings)


class IndexManager:
    """Thread-safe manager for active and candidate ATLAS index generations."""

    def __init__(
        self,
        dense_config: Optional[DenseConfig] = None,
        bm25_config: Optional[BM25Config] = None,
        chunking_config: Optional[ChunkingConfig] = None,
    ) -> None:
        self.dense_config = dense_config or DenseConfig()
        self.bm25_config = bm25_config or BM25Config()
        self.chunking_config = chunking_config or ChunkingConfig()

        self._lock = threading.RLock()
        self._active_snapshot: Optional[IndexGenerationSnapshot] = None
        self._generation_ref_counts: dict[str, int] = {}
        self._retiring_snapshots: dict[str, IndexGenerationSnapshot] = {}
        self.active_generation: Optional[IndexGeneration] = None
        self.bm25_index: Optional[BM25Index] = None
        self.dense_index: Optional[DenseIndex] = None
        self.search_documents: list[SearchDocument] = []
        self.search_chunks: list[SearchChunk] = []
        self.metadata_snapshot_index: dict[str, Any] = {}
        self.content_hashes: dict[str, str] = {}
        self.tombstoned_doc_ids: set[str] = set()

    @staticmethod
    def _log_generation_event(
        event: str,
        generation_id: Optional[str] = None,
        **attributes: Any,
    ) -> None:
        """Emit bounded lifecycle telemetry without document or query content."""
        log_event(
            logger,
            event,
            generation_id=generation_id,
            **attributes,
        )

    def _make_snapshot(
        self,
        generation: IndexGeneration,
        bm25_index: BM25Index,
        dense_index: DenseIndex,
        search_documents: list[SearchDocument],
        search_chunks: list[SearchChunk],
        metadata_snapshot_index: dict[str, Any],
    ) -> IndexGenerationSnapshot:
        """Freeze references that have already passed deep integrity validation."""
        return IndexGenerationSnapshot(
            generation=generation,
            bm25_index=bm25_index,
            dense_index=dense_index,
            search_documents=tuple(search_documents),
            search_chunks=tuple(search_chunks),
            metadata_snapshot_index=MappingProxyType(dict(metadata_snapshot_index)),
        )

    def _retire_if_unreferenced_locked(
        self,
        snapshot: IndexGenerationSnapshot,
    ) -> Optional[str]:
        """Retire a displaced generation only after its final lease releases."""
        generation_id = snapshot.generation.generation_id
        if self._generation_ref_counts.get(generation_id, 0) != 0:
            return None

        self._retiring_snapshots.pop(generation_id, None)
        self._generation_ref_counts.pop(generation_id, None)
        snapshot.generation.status = IndexGenerationStatus.RETIRED.value
        return generation_id

    def _release_generation(self, snapshot: IndexGenerationSnapshot) -> None:
        """Release a request lease and safely retire a displaced generation."""
        retired_generation_id: Optional[str] = None
        with self._lock:
            generation_id = snapshot.generation.generation_id
            ref_count = self._generation_ref_counts.get(generation_id, 0)
            if ref_count <= 1:
                self._generation_ref_counts.pop(generation_id, None)
            else:
                self._generation_ref_counts[generation_id] = ref_count - 1

            retiring_snapshot = self._retiring_snapshots.get(generation_id)
            if retiring_snapshot is not None:
                retired_generation_id = self._retire_if_unreferenced_locked(retiring_snapshot)

        if retired_generation_id:
            try:
                get_metrics().record_index_retirement()
            except Exception:
                pass
            self._log_generation_event(
                "old_generation_retirement",
                retired_generation_id,
            )

    def acquire_active_generation(self) -> Optional[IndexGenerationLease]:
        """Atomically acquire the active generation for a complete request.

        Callers must close the returned lease once they no longer reference the
        snapshot.  A candidate can be published while a lease is held; that
        request still owns the original immutable component bundle.
        """
        with self._lock:
            snapshot = self._active_snapshot
            if snapshot is None or snapshot.generation.status != IndexGenerationStatus.ACTIVE.value:
                return None
            generation_id = snapshot.generation.generation_id
            self._generation_ref_counts[generation_id] = self._generation_ref_counts.get(generation_id, 0) + 1
            return IndexGenerationLease(self, snapshot)

    def get_active_generation_id(self) -> Optional[str]:
        """Return the currently published generation identifier."""
        with self._lock:
            if self._active_snapshot is None:
                return None
            return self._active_snapshot.generation.generation_id

    def get_retiring_generation_ids(self) -> list[str]:
        """Return displaced generations still retained by in-flight requests."""
        with self._lock:
            return sorted(self._retiring_snapshots)

    def get_generation_reference_count(self, generation_id: str) -> int:
        """Return the active request lease count for lifecycle diagnostics/tests."""
        with self._lock:
            return self._generation_ref_counts.get(generation_id, 0)

    def initialize_from_components(
        self,
        search_documents: list[SearchDocument],
        search_chunks: list[SearchChunk],
        bm25_index: BM25Index,
        dense_index: DenseIndex,
        metadata_snapshot_index: dict[str, Any],
        corpus_version: str = "1.0",
    ) -> IndexGeneration:
        """Initialize active index generation from pre-computed pipeline components."""
        validation = validate_index_integrity(
            bm25_index=bm25_index,
            dense_index=dense_index,
            search_documents=search_documents,
            search_chunks=search_chunks,
            metadata_snapshot_index=metadata_snapshot_index,
            expected_dimension=self.dense_config.dimension,
        )
        if not validation.is_valid:
            try:
                metrics = get_metrics()
                for err in validation.errors:
                    rule = err.split(":")[0].split()[0]
                    metrics.record_index_validation_failure(rule=rule)
                metrics.record_index_build_failure(stage="validation")
                metrics.record_index_build(status="failed")
                metrics.record_index_publish_failure(stage="validation")
            except Exception:
                pass
            self._log_generation_event(
                "index_generation_validation",
                None,
                status="failed",
                error_count=len(validation.errors),
            )
            self._log_generation_event("index_generation_publish_failure", None, stage="validation")
            raise ValueError(f"Index initialization failed validation: {validation.errors}")

        gen_id = f"GEN-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        hashes = {d.document_id: compute_document_content_hash(d) for d in search_documents}
        checksum_source = "".join(c.chunk_id for c in search_chunks)
        checksum = hashlib.sha256(checksum_source.encode("utf-8")).hexdigest()

        generation = IndexGeneration(
            generation_id=gen_id,
            corpus_version=corpus_version,
            created_at=datetime.now(timezone.utc).isoformat(),
            document_count=len(search_documents),
            chunk_count=len(search_chunks),
            status=IndexGenerationStatus.ACTIVE.value,
            checksum=checksum,
            content_hashes=hashes,
            active_document_ids=[d.document_id for d in search_documents],
            tombstoned_document_ids=[],
        )

        snapshot = self._make_snapshot(
            generation=generation,
            bm25_index=bm25_index,
            dense_index=dense_index,
            search_documents=search_documents,
            search_chunks=search_chunks,
            metadata_snapshot_index=metadata_snapshot_index,
        )

        with self._lock:
            # Initialization has no predecessor, but publication is still a
            # single pointer assignment so readers never observe partial state.
            self._active_snapshot = snapshot
            self.active_generation = generation
            self.bm25_index = bm25_index
            self.dense_index = dense_index
            self.search_documents = list(search_documents)
            self.search_chunks = list(search_chunks)
            self.metadata_snapshot_index = dict(metadata_snapshot_index)
            self.content_hashes = hashes
            self.tombstoned_doc_ids = set()

        try:
            metrics = get_metrics()
            metrics.record_index_publish()
            metrics.record_index_build(status="success")
            metrics.set_active_generation(True)
        except Exception:
            pass
        self._log_generation_event(
            "index_generation_build",
            generation.generation_id,
            status="success",
        )
        self._log_generation_event(
            "index_generation_validation",
            generation.generation_id,
            status="success",
        )
        self._log_generation_event("index_generation_publish", generation.generation_id)

        return generation

    def apply_incremental_update(
        self,
        new_or_updated_documents: list[SearchDocument],
        deleted_document_ids: Optional[list[str]] = None,
        encoder: Optional[DenseEncoder] = None,
        adversarial_fixtures: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[bool, Optional[IndexGeneration], list[str]]:
        """Apply an incremental update with atomic publish and rollback protection.

        Execution Model:
        1. Identifies UNCHANGED, UPDATED, NEW, and DELETED documents via content hash.
        2. Unchanged documents skip re-chunking and re-embedding.
        3. Builds candidate SearchDocuments, SearchChunks, BM25Index, and DenseIndex in staging.
        4. Validates candidate integrity completely out-of-band.
        5. If validation passes: atomically promotes candidate to ACTIVE generation.
        6. If validation fails: candidate is REJECTED, active generation remains untouched.
        """
        deleted_set = set(deleted_document_ids or [])
        candidate_gen_id = f"GEN-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self._log_generation_event("index_generation_build", candidate_gen_id, status="started")

        with self._lock:
            base_snapshot = self._active_snapshot
            if base_snapshot is None:
                self._log_generation_event(
                    "index_generation_publish_failure",
                    candidate_gen_id,
                    stage="no_active_generation",
                )
                return False, None, ["No active generation found to update incrementally."]

            curr_docs_map = {d.document_id: d for d in base_snapshot.search_documents}
            curr_hashes = dict(base_snapshot.generation.content_hashes)
            curr_tombstones = set(base_snapshot.generation.tombstoned_document_ids)

        # 1. Classify updates
        unchanged_doc_ids: Set[str] = set()
        updated_doc_ids: Set[str] = set()
        new_doc_ids: Set[str] = set()

        for doc in new_or_updated_documents:
            doc_id = doc.document_id
            new_hash = compute_document_content_hash(doc)
            if doc_id in curr_docs_map:
                if curr_hashes.get(doc_id) == new_hash and doc_id not in deleted_set:
                    unchanged_doc_ids.add(doc_id)
                else:
                    updated_doc_ids.add(doc_id)
            else:
                new_doc_ids.add(doc_id)

        # 2. Check if no-op
        if not updated_doc_ids and not new_doc_ids and not deleted_set:
            return True, base_snapshot.generation, ["No changes detected; active generation preserved."]

        # 3. Assemble candidate documents
        candidate_docs_map = dict(curr_docs_map)
        # Apply deletions / tombstones
        for d_id in deleted_set:
            candidate_docs_map.pop(d_id, None)
            curr_tombstones.add(d_id)

        # Apply updates and additions
        for doc in new_or_updated_documents:
            if doc.document_id not in deleted_set:
                candidate_docs_map[doc.document_id] = doc
                curr_tombstones.discard(doc.document_id)

        candidate_documents = list(candidate_docs_map.values())
        candidate_hashes = {d.document_id: compute_document_content_hash(d) for d in candidate_documents}

        # 4. Assemble candidate chunks
        # Reuse existing chunks for unchanged documents; generate new chunks for new/updated documents
        candidate_chunks: list[SearchChunk] = []
        active_chunks = list(base_snapshot.search_chunks)
        active_dense = base_snapshot.dense_index

        # Map active chunks by document_id
        active_chunks_by_doc: dict[str, list[SearchChunk]] = {}
        for c in active_chunks:
            active_chunks_by_doc.setdefault(c.document_id, []).append(c)

        # Collect chunks to index
        reused_chunks: list[SearchChunk] = []
        reused_chunk_indices: list[int] = []
        fresh_chunks: list[SearchChunk] = []

        for doc in candidate_documents:
            if doc.document_id in unchanged_doc_ids and doc.document_id in active_chunks_by_doc:
                for c in active_chunks_by_doc[doc.document_id]:
                    reused_chunks.append(c)
                    if active_dense and c.chunk_id in active_dense.chunk_id_to_idx:
                        reused_chunk_indices.append(active_dense.chunk_id_to_idx[c.chunk_id])
            else:
                doc_chunks = chunk_document(doc, self.chunking_config)
                fresh_chunks.extend(doc_chunks)

        candidate_chunks = reused_chunks + fresh_chunks

        # 5. Build candidate BM25 index
        try:
            candidate_bm25 = BM25Index.build_index(candidate_chunks, config=self.bm25_config)
        except Exception as e:
            try:
                metrics = get_metrics()
                metrics.record_index_build_failure(stage="bm25")
                metrics.record_index_build(status="failed")
                metrics.record_index_publish_failure(stage="bm25")
            except Exception:
                pass
            self._log_generation_event("index_generation_publish_failure", candidate_gen_id, stage="bm25")
            return False, None, [f"Candidate BM25 construction failed: {e}"]

        # 6. Build candidate Dense index
        enc = encoder or active_dense.encoder
        try:
            reused_vectors = active_dense.vectors[reused_chunk_indices] if (active_dense and reused_chunk_indices) else np.empty((0, self.dense_config.dimension), dtype=np.float32)
            if fresh_chunks:
                fresh_passages = [format_passage_text(c) for c in fresh_chunks]
                fresh_vectors = enc.encode_passages(fresh_passages)
            else:
                fresh_vectors = np.empty((0, self.dense_config.dimension), dtype=np.float32)

            if len(candidate_chunks) == 0:
                candidate_vectors = np.empty((0, self.dense_config.dimension), dtype=np.float32)
            elif len(reused_chunks) == 0:
                candidate_vectors = fresh_vectors
            elif len(fresh_chunks) == 0:
                candidate_vectors = reused_vectors
            else:
                candidate_vectors = np.vstack([reused_vectors, fresh_vectors])

            candidate_dense = DenseIndex(
                chunks=candidate_chunks,
                vectors=candidate_vectors,
                config=self.dense_config,
                encoder=enc,
            )
        except Exception as e:
            try:
                metrics = get_metrics()
                metrics.record_index_build_failure(stage="dense_embedding")
                metrics.record_index_build(status="failed")
                metrics.record_index_publish_failure(stage="dense_embedding")
            except Exception:
                pass
            self._log_generation_event("index_generation_publish_failure", candidate_gen_id, stage="dense_embedding")
            return False, None, [f"Candidate dense index construction failed: {e}"]

        # 7. Build candidate metadata snapshot index
        raw_docs_dict = [d.to_dict() for d in candidate_documents]
        candidate_metadata_index = build_metadata_snapshot_index(
            raw_docs_dict,
            adversarial_fixtures or [],
        )

        # 8. Out-of-band deep integrity validation
        validation = validate_index_integrity(
            bm25_index=candidate_bm25,
            dense_index=candidate_dense,
            search_documents=candidate_documents,
            search_chunks=candidate_chunks,
            metadata_snapshot_index=candidate_metadata_index,
            expected_dimension=self.dense_config.dimension,
        )

        if not validation.is_valid:
            try:
                metrics = get_metrics()
                for err in validation.errors:
                    rule = err.split(":")[0].split()[0]
                    metrics.record_index_validation_failure(rule=rule)
                metrics.record_index_build_failure(stage="validation")
                metrics.record_index_build(status="failed")
                metrics.record_index_publish_failure(stage="validation")
            except Exception:
                pass
            self._log_generation_event(
                "index_generation_validation",
                candidate_gen_id,
                status="failed",
                error_count=len(validation.errors),
            )
            self._log_generation_event("index_generation_publish_failure", candidate_gen_id, stage="validation")
            # Abort: Active generation remains 100% untouched
            return False, None, validation.errors

        self._log_generation_event("index_generation_validation", candidate_gen_id, status="success")

        # 9. Atomic Publish / Reference Swap
        checksum_source = "".join(c.chunk_id for c in candidate_chunks)
        checksum = hashlib.sha256(checksum_source.encode("utf-8")).hexdigest()

        candidate_generation = IndexGeneration(
            generation_id=candidate_gen_id,
            corpus_version=f"{base_snapshot.generation.corpus_version}.1",
            created_at=datetime.now(timezone.utc).isoformat(),
            document_count=len(candidate_documents),
            chunk_count=len(candidate_chunks),
            status=IndexGenerationStatus.ACTIVE.value,
            checksum=checksum,
            content_hashes=candidate_hashes,
            active_document_ids=[d.document_id for d in candidate_documents],
            tombstoned_document_ids=sorted(curr_tombstones),
        )

        candidate_snapshot = self._make_snapshot(
            generation=candidate_generation,
            bm25_index=candidate_bm25,
            dense_index=candidate_dense,
            search_documents=candidate_documents,
            search_chunks=candidate_chunks,
            metadata_snapshot_index=candidate_metadata_index,
        )

        retired_generation_id: Optional[str] = None
        with self._lock:
            # A competing builder published first.  Do not overwrite it with a
            # candidate derived from an older base generation.
            if self._active_snapshot is not base_snapshot:
                try:
                    metrics = get_metrics()
                    metrics.record_index_build(status="failed")
                    metrics.record_index_publish_failure(stage="stale_candidate")
                except Exception:
                    pass
                self._log_generation_event(
                    "index_generation_publish_failure",
                    candidate_gen_id,
                    stage="stale_candidate",
                )
                return False, None, ["Candidate was built from a superseded active generation."]

            # Atomic publication: one lock covers the active snapshot pointer
            # and legacy compatibility fields.  Acquired request leases retain
            # the old immutable snapshot until they close.
            self._active_snapshot = candidate_snapshot
            self.active_generation = candidate_generation
            self.bm25_index = candidate_bm25
            self.dense_index = candidate_dense
            self.search_documents = candidate_documents
            self.search_chunks = candidate_chunks
            self.metadata_snapshot_index = candidate_metadata_index
            self.content_hashes = candidate_hashes
            self.tombstoned_doc_ids = curr_tombstones

            self._retiring_snapshots[base_snapshot.generation.generation_id] = base_snapshot
            retired_generation_id = self._retire_if_unreferenced_locked(base_snapshot)

        try:
            metrics = get_metrics()
            metrics.record_index_publish()
            metrics.record_index_build(status="success")
            metrics.set_active_generation(True)
        except Exception:
            pass
        self._log_generation_event("index_generation_build", candidate_gen_id, status="success")
        self._log_generation_event("index_generation_publish", candidate_gen_id)

        if retired_generation_id:
            try:
                get_metrics().record_index_retirement()
            except Exception:
                pass
            self._log_generation_event("old_generation_retirement", retired_generation_id)

        return True, candidate_generation, []

    def get_active_generation(self) -> Optional[IndexGeneration]:
        """Return metadata for the generation currently published to new requests."""
        with self._lock:
            return self._active_snapshot.generation if self._active_snapshot else None

    def is_ready(self) -> tuple[bool, dict[str, bool]]:
        """Deep readiness verification of active index components."""
        with self._lock:
            snapshot = self._active_snapshot
            bm25_index = snapshot.bm25_index if snapshot else None
            dense_index = snapshot.dense_index if snapshot else None
            generation = snapshot.generation if snapshot else None

        bm25_ok = bm25_index is not None and len(bm25_index.chunks) > 0
        dense_ok = False
        if dense_index is not None and dense_index.vectors is not None:
            shape = dense_index.vectors.shape
            has_rows = shape[0] > 0
            has_dim = shape[1] == self.dense_config.dimension
            has_no_nan = not np.isnan(dense_index.vectors).any()
            has_no_inf = not np.isinf(dense_index.vectors).any()
            dense_ok = has_rows and has_dim and has_no_nan and has_no_inf

        aligned = False
        if bm25_ok and dense_ok:
            aligned = len(bm25_index.chunks) == len(dense_index.chunks)

        components = {
            "bm25": bm25_ok,
            "dense": dense_ok,
            "index_alignment": aligned,
            "generation_active": generation is not None and generation.status == IndexGenerationStatus.ACTIVE.value,
        }
        all_ready = all(components.values())
        return all_ready, components
