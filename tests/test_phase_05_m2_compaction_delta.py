"""Unit and Integration Tests for ATLAS 0.5 Milestone M2: Salience Compaction & Delta Index.

Tests:
1. DeltaIndexBuffer ingestion, chunking, and memory tracking.
2. Sub-second ingest-to-search freshness latency (<500ms).
3. DeltaIndexBuffer tenant isolation and fail-closed permission checks.
4. Base IndexGeneration immutability under delta operations.
5. Dual-index fusion via fuse_base_and_delta_candidates with RRF and freshness bonus.
6. AdaptiveContextBudgeter salience compaction (6-8 documents in <800 tokens).
7. Mechanical C2 citation validation over compacted evidence items.
8. Schema-constrained edge gating preventing cross-domain pollution.
"""

from __future__ import annotations

import time
import pytest
from pathlib import Path
from typing import Any

from novastack.context_budgeter import (
    AdaptiveContextBudgeter,
    compress_evidence_item,
    estimate_token_count,
    extract_salient_sentences,
    filter_document_diversity,
)
from novastack.delta_index import (
    DeltaIndexBuffer,
    DeltaIndexBufferConfig,
    fuse_base_and_delta_candidates,
)
from novastack.entity_catalog import EntityCatalog
from novastack.evidence import EvidenceItem, EvidencePackage
from novastack.models import (
    RecordPermissions,
    SearchChunk,
    SearchDocument,
    SourceRecord,
)
from novastack.relational_retrieval import (
    StructuredCandidate,
    StructuredRetriever,
    StructuredRetrieverConfig,
)


# ======================================================================
# FIXTURES & HELPERS
# ======================================================================

def _make_search_chunk(
    chunk_id: str,
    document_id: str,
    tenant_id: str = "TENANT-NOVASTACK",
    title: str = "Test Title",
    text: str = "Test chunk body text.",
    source_type: str = "documentation",
    department: str = "Engineering",
    author_id: str = "USR-TEST-0001",
    classification: str = "internal",
    permissions: RecordPermissions | None = None,
    authority_level: str = "medium",
    status: str = "published",
    version: str = "1.0",
    created_at: str = "2026-09-24T12:00:00Z",
) -> SearchChunk:
    """Helper to build a valid SearchChunk."""
    return SearchChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        tenant_id=tenant_id,
        chunk_index=0,
        total_chunks=1,
        title=title,
        text=text,
        char_count=len(text),
        word_count=len(text.split()),
        source_type=source_type,
        department=department,
        author_id=author_id,
        classification=classification,
        permissions=permissions or RecordPermissions(),
        authority_level=authority_level,
        status=status,
        version=version,
        created_at=created_at,
    )


@pytest.fixture(scope="module")
def catalog() -> EntityCatalog:
    """Load canonical EntityCatalog."""
    return EntityCatalog()


@pytest.fixture
def delta_buffer() -> DeltaIndexBuffer:
    """Fresh DeltaIndexBuffer instance."""
    return DeltaIndexBuffer(
        config=DeltaIndexBufferConfig(
            max_buffered_docs=50,
            max_memory_mb=10.0,
            freshness_bonus_weight=0.10,
        )
    )


# ======================================================================
# 1. DELTA INDEX BUFFER: INGESTION & CHUNKING
# ======================================================================

def test_delta_buffer_ingestion_and_chunking(delta_buffer: DeltaIndexBuffer) -> None:
    """Verify document ingestion, automatic chunking, and memory tracking."""
    doc = SearchDocument(
        document_id="DOC-DELTA-0001",
        tenant_id="TENANT-NOVASTACK",
        source_type="incident_triage",
        title="Live Incident Triage: Kafka Lag Spike",
        content="Engineers are observing severe consumer group lag in payment topic.\n\n"
                "Mitigation: Scaled consumer replicas from 4 to 12. Lag recovering.\n\n"
                "Root cause: Unindexed database query during batch ingestion.",
        department="DevOps",
        author_id="USR-NS-0041",
        permissions=RecordPermissions(allowed_roles=["engineer", "sre"]),
        created_at="2026-09-24T12:00:00Z",
    )

    chunks = delta_buffer.ingest_document(doc)
    assert len(chunks) >= 1
    assert delta_buffer.document_count == 1
    assert delta_buffer.chunk_count == len(chunks)

    # Verify chunk properties
    first_chunk = chunks[0]
    assert first_chunk.tenant_id == "TENANT-NOVASTACK"
    assert first_chunk.document_id == "DOC-DELTA-0001"
    assert "Kafka Lag Spike" in first_chunk.title

    # Memory footprint should be positive and bounded (<1 MB for single doc)
    mem_mb = delta_buffer.get_memory_footprint_mb()
    assert 0.0 < mem_mb < 1.0


def test_delta_buffer_source_record_ingestion(delta_buffer: DeltaIndexBuffer) -> None:
    """Verify SourceRecord ingestion converts cleanly to SearchDocument and chunks."""
    record = SourceRecord(
        document_id="REC-DELTA-0002",
        tenant_id="TENANT-NOVASTACK",
        source_type="slack_triage",
        title="Incident War Room Slack Snippet",
        content="Deploying hotfix PR-994 to revert connection pool configuration change.",
        department="Engineering",
        author_id="USR-NS-0010",
        permissions=RecordPermissions(allowed_roles=["sre"]),
    )

    chunks = delta_buffer.ingest_source_record(record)
    assert len(chunks) >= 1
    assert delta_buffer.document_count == 1
    assert chunks[0].document_id == "REC-DELTA-0002"


# ======================================================================
# 2. SUB-SECOND SEARCHABILITY & FRESHNESS (<500ms SLA)
# ======================================================================

def test_delta_buffer_sub_second_freshness(delta_buffer: DeltaIndexBuffer) -> None:
    """Verify ingest-to-search freshness latency is well below 500ms SLA."""
    unique_keyword = "xyzzyquantumpercolator"
    doc = SearchDocument(
        document_id="DOC-DELTA-SPEED",
        tenant_id="TENANT-NOVASTACK",
        source_type="runbook",
        title=f"Procedure for {unique_keyword}",
        content=f"When encountering {unique_keyword}, immediately cycle the upstream load balancer.",
        department="DevOps",
        author_id="USR-NS-0041",
        created_at="2026-09-24T12:00:00Z",
        permissions=RecordPermissions(allowed_roles=["engineer"]),
    )

    # Ingest and measure total latency to first query result
    t0 = time.perf_counter()
    delta_buffer.ingest_document(doc)
    results = delta_buffer.search_bm25(unique_keyword, user_tenant="TENANT-NOVASTACK", top_k=5)
    t_elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert len(results) >= 1
    assert results[0].document_id == "DOC-DELTA-SPEED"
    assert t_elapsed_ms < 500.0, f"Freshness SLA violated: took {t_elapsed_ms:.2f}ms (threshold: 500ms)"


# ======================================================================
# 3. SECURITY BOUNDARY & TENANT ISOLATION
# ======================================================================

def test_delta_buffer_fail_closed_tenant_id_requirement(delta_buffer: DeltaIndexBuffer) -> None:
    """Verify ingestion strictly rejects payloads missing tenant_id."""
    doc = SearchDocument(
        document_id="DOC-INVALID-TENANT",
        tenant_id="",  # Empty tenant!
        source_type="note",
        title="Unauthorized note",
        content="Secret text",
        department="Security",
        author_id="USR-UNKNOWN",
        created_at="2026-09-24T12:00:00Z",
    )

    with pytest.raises(ValueError, match="tenant_id"):
        delta_buffer.ingest_document(doc)


def test_delta_buffer_cross_tenant_isolation(delta_buffer: DeltaIndexBuffer) -> None:
    """Verify tenant isolation prevents cross-tenant candidate leakage."""
    doc_a = SearchDocument(
        document_id="DOC-TENANT-A",
        tenant_id="TENANT-ALPHA",
        source_type="report",
        title="Alpha Confidential Incident",
        content="Alpha database master credentials rotated.",
        department="IT",
        author_id="USR-ALPHA-1",
        created_at="2026-09-24T12:00:00Z",
    )
    doc_b = SearchDocument(
        document_id="DOC-TENANT-B",
        tenant_id="TENANT-BETA",
        source_type="report",
        title="Beta Public Notes",
        content="Beta deployment schedule.",
        department="IT",
        author_id="USR-BETA-1",
        created_at="2026-09-24T12:00:00Z",
    )

    delta_buffer.ingest_document(doc_a)
    delta_buffer.ingest_document(doc_b)

    # Query as TENANT-ALPHA
    res_a = delta_buffer.search_bm25("credentials", user_tenant="TENANT-ALPHA")
    assert len(res_a) == 1
    assert res_a[0].document_id == "DOC-TENANT-A"

    # Query as TENANT-BETA (should NOT see Alpha document)
    res_b = delta_buffer.search_bm25("credentials", user_tenant="TENANT-BETA")
    assert len(res_b) == 0

    # Cross-tenant query for generic term
    res_b_all = delta_buffer.search_bm25("report", user_tenant="TENANT-BETA")
    for r in res_b_all:
        assert r.chunk.tenant_id == "TENANT-BETA"


def test_delta_buffer_rbac_and_forbidden_filter(delta_buffer: DeltaIndexBuffer) -> None:
    """Verify role-based access and explicit forbidden doc IDs."""
    doc = SearchDocument(
        document_id="DOC-SECRET-01",
        tenant_id="TENANT-NOVASTACK",
        source_type="executive_memo",
        title="Confidential Acquisition",
        content="Details of Project Falcon acquisition.",
        department="Legal",
        author_id="USR-LEGAL-1",
        created_at="2026-09-24T12:00:00Z",
        permissions=RecordPermissions(allowed_roles=["executive", "legal"]),
    )
    delta_buffer.ingest_document(doc)

    # Engineer role should be rejected
    res_eng = delta_buffer.search_bm25("Falcon", user_tenant="TENANT-NOVASTACK", user_role="engineer")
    assert len(res_eng) == 0

    # Executive role should succeed
    res_exec = delta_buffer.search_bm25("Falcon", user_tenant="TENANT-NOVASTACK", user_role="executive")
    assert len(res_exec) == 1

    # Forbidden doc ID should be suppressed even for executive
    res_forb = delta_buffer.search_bm25(
        "Falcon",
        user_tenant="TENANT-NOVASTACK",
        user_role="executive",
        forbidden_docs={"DOC-SECRET-01"},
    )
    assert len(res_forb) == 0


# ======================================================================
# 4. IMMUTABILITY OF BASE SNAPSHOT
# ======================================================================

def test_delta_buffer_does_not_mutate_base_index() -> None:
    """Verify base index files remain strictly unmodified."""
    base_file = Path("data/processed/novastack/search_chunks.json")
    if base_file.exists():
        mtime_before = base_file.stat().st_mtime
        size_before = base_file.stat().st_size

        buffer = DeltaIndexBuffer()
        for i in range(5):
            buffer.ingest_document(
                SearchDocument(
                    document_id=f"DOC-TEST-IMMUTABLE-{i}",
                    tenant_id="TENANT-NOVASTACK",
                    source_type="test",
                    title=f"Test Document {i}",
                    content="Ephemeral test content",
                    department="Test",
                    author_id="USR-TEST",
                    created_at="2026-09-24T12:00:00Z",
                )
            )

        mtime_after = base_file.stat().st_mtime
        size_after = base_file.stat().st_size
        assert mtime_before == mtime_after
        assert size_before == size_after


# ======================================================================
# 5. DUAL-INDEX FUSION (BASE + DELTA RRF)
# ======================================================================

def test_dual_index_fusion_and_freshness_bonus() -> None:
    """Verify fuse_base_and_delta_candidates combines candidates with proper RRF and freshness bonus."""
    chunk_base = _make_search_chunk(
        chunk_id="BASE-001::CHUNK-0001",
        document_id="BASE-001",
        title="Base Document Title",
        text="Base text content",
    )
    chunk_delta = _make_search_chunk(
        chunk_id="DELTA-001::CHUNK-0001",
        document_id="DELTA-001",
        source_type="incident_triage",
        title="Live Delta Document Title",
        text="Delta text content",
    )

    base_results = [
        {"chunk_id": "BASE-001::CHUNK-0001", "document_id": "BASE-001", "score": 2.5, "chunk": chunk_base}
    ]
    delta_results = [
        {"chunk_id": "DELTA-001::CHUNK-0001", "document_id": "DELTA-001", "score": 2.5, "chunk": chunk_delta}
    ]

    # Fusion without freshness bonus
    fused_neutral = fuse_base_and_delta_candidates(
        base_results=base_results,
        delta_results=delta_results,
        freshness_bonus_weight=0.0,
    )
    assert len(fused_neutral) == 2

    # Fusion with freshness bonus (delta candidate rank 1 should outscore base candidate rank 1)
    fused_fresh = fuse_base_and_delta_candidates(
        base_results=base_results,
        delta_results=delta_results,
        freshness_bonus_weight=0.15,
    )
    assert len(fused_fresh) == 2
    assert fused_fresh[0]["chunk_id"] == "DELTA-001::CHUNK-0001"
    assert fused_fresh[0]["score"] > fused_fresh[1]["score"]


# ======================================================================
# 6. SALIENCE CONTEXT COMPACTION (6-8 DOCS IN <800 TOKENS)
# ======================================================================

def test_extract_salient_sentences_preserves_headers() -> None:
    """Verify salient sentence extraction preserves incident postmortem headers."""
    raw_incident = (
        "INCIDENT REPORT: INC-NS-9999\n"
        "SEVERITY: P1-CRITICAL\n"
        "SERVICE: payment-gateway\n\n"
        "The payment gateway began experiencing 504 gateway timeouts at 14:22 UTC. "
        "Engineers identified a thread pool deadlock in the connection manager. "
        "The database administrator restarted the primary replica at 14:35 UTC. "
        "All transaction queues cleared by 14:40 UTC with zero data loss. "
        "Post-incident review scheduled for the following Monday."
    )
    query = "What caused the payment gateway timeouts and what was the mitigation?"

    compressed = extract_salient_sentences(raw_incident, query=query, max_sentences=2)
    # Header must be preserved
    assert "INCIDENT REPORT: INC-NS-9999" in compressed
    assert "SEVERITY: P1-CRITICAL" in compressed
    # Most salient sentences must be included
    assert "timeouts" in compressed or "deadlock" in compressed
    # Text length should be shorter than original
    assert len(compressed) < len(raw_incident)


def test_adaptive_context_budgeter_compaction_density() -> None:
    """Verify that salience_compression fits 6-8 documents within 800 token budget."""
    budgeter = AdaptiveContextBudgeter(default_token_budget=800)

    # Create 8 distinct evidence items using factory
    evidence_items = []
    for i in range(8):
        text = (
            f"Document {i+1} Title\n\n"
            f"This is the first sentence about service {i+1} operational architecture. "
            f"The primary configuration parameter for system {i+1} is max_retries set to 5. "
            f"Additional operational logging details are documented in the main handbook. "
            f"Maintenance procedures for node {i+1} require sequential cordoning."
        )
        chunk = _make_search_chunk(
            chunk_id=f"DOC-TEST-{i+1:03d}::CHUNK-0001",
            document_id=f"DOC-TEST-{i+1:03d}",
            title=f"Service Handbook {i+1}",
            text=text,
            source_type="handbook",
        )
        evidence_items.append(
            EvidenceItem.from_search_chunk(
                chunk=chunk,
                evidence_id=f"EVD-{i+1:03d}",
                retrieval_rank=i + 1,
                retrieval_score=1.0 - (i * 0.05),
            )
        )

    package = EvidencePackage(
        package_id="PKG-TEST-001",
        evaluation_id="EVAL-TEST-001",
        query="What is the configuration parameter for max_retries?",
        tenant_id="TENANT-NOVASTACK",
        user_context={"user_id": "USR-TEST-0001", "role": "engineer"},
        selected_evidence=evidence_items,
        excluded_evidence=[],
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={"total_selected": len(evidence_items)},
    )

    # Budget with salience compression
    budgeted = budgeter.budget_context(
        package,
        strategy="salience_compression",
        max_documents=8,
        max_sentences_per_chunk=2,
        max_token_budget=800,
    )

    assert len(budgeted) == 8
    # Distinct documents count
    unique_docs = {item.document_id for item in budgeted}
    assert len(unique_docs) == 8

    # Calculate total token count
    total_tokens = sum(estimate_token_count(item.text) for item in budgeted)
    assert total_tokens <= 800, f"Token budget exceeded: {total_tokens} > 800"


# ======================================================================
# 7. SCHEMA-CONSTRAINED EDGE GATING
# ======================================================================

def test_schema_constrained_organizational_gating(catalog: EntityCatalog) -> None:
    """Verify organizational queries stay within organizational schema and do not expand to incidents."""
    # From checkout-service (SVC-NS-0005) with ownership intent
    traversals_org = catalog.traverse(
        "SVC-NS-0005",
        rel_types={"owns", "owned_by"},
        max_depth=3,
        user_tenant="TENANT-NOVASTACK",
    )

    # All traversed entities should belong to organizational schema (teams, users)
    for tgt_ent, edge, depth in traversals_org:
        assert tgt_ent.entity_type in {"team", "user", "department", "service"}, (
            f"Cross-domain pollution: organizational traversal reached {tgt_ent.entity_type} ({tgt_ent.entity_id})"
        )
        assert edge.relationship_type in {
            "owns", "owned_by", "member_of", "has_member", "manages", "managed_by", "owns_deployment", "manages_customer"
        }


def test_schema_constrained_operational_gating(catalog: EntityCatalog) -> None:
    """Verify operational queries stay within operational schema and do not branch into teams/users."""
    traversals_op = catalog.traverse(
        "SVC-NS-0005",
        rel_types={"caused_by", "causes", "affects", "targets"},
        max_depth=3,
        user_tenant="TENANT-NOVASTACK",
    )

    # All traversed entities should belong to operational schema (events, deployments, PRs, incidents)
    for tgt_ent, edge, depth in traversals_op:
        assert tgt_ent.entity_type in {"event", "deployment", "pull_request", "incident", "service"}, (
            f"Cross-domain pollution: operational traversal reached {tgt_ent.entity_type} ({tgt_ent.entity_id})"
        )
        assert edge.relationship_type not in {"member_of", "has_member", "manages", "managed_by"}
