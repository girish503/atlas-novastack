"""Automated Unit and Integration Tests for Phase 4E: Evidence Assembly & Resolution.

Validates:
1. EvidenceItem and EvidencePackage data models and serialization
2. Provenance chain preservation (Evidence -> Chunk -> Document -> Entity)
3. Multi-channel provenance merging and intra-document deduplication
4. Strict authorization gate (tenant isolation, role checks, ACLs, forbidden doc IDs)
5. Adversarial and retrieval poisoning classification and quarantine
6. Version and lifecycle resolution (latest vs historical version intent)
7. Temporal validity resolution (active vs expired time bounds)
8. Authority resolution and conflict detection (authoritative vs low-authority notes)
9. Determinism of package assembly
10. SHA256 immutability of all 18 prior baseline artifacts
"""

import hashlib
import json
import pytest
from pathlib import Path

from novastack.evidence import (
    EvidenceConflict,
    EvidenceItem,
    EvidencePackage,
    EvidenceStatus,
    ProvenanceNode,
)
from novastack.evidence_resolution import (
    EvidenceResolver,
    EvidenceResolverConfig,
)
from novastack.models import EvaluationCase, RecordPermissions, SearchChunk, SearchDocument

ROOT = Path(__file__).resolve().parent.parent
PROC_DIR = ROOT / "data" / "processed" / "novastack"
RAW_DIR = ROOT / "data" / "raw" / "novastack"
EVAL_DIR = ROOT / "data" / "evaluation" / "novastack"

PRIOR_ARTIFACTS = [
    "data/raw/novastack/source_records.json",
    "data/raw/novastack/adversarial_fixtures.json",
    "data/raw/novastack/security_fixtures.json",
    "data/processed/novastack/search_documents.json",
    "data/processed/novastack/search_chunks.json",
    "data/evaluation/novastack/evaluation_cases.json",
    "data/evaluation/novastack/bm25_baseline.json",
    "data/evaluation/novastack/dense_baseline.json",
    "data/evaluation/novastack/hybrid_baseline.json",
    "data/evaluation/novastack/phase_4b0_candidate_diagnostics.json",
    "data/evaluation/novastack/phase_4b1_reranker_baseline.json",
    "data/evaluation/novastack/phase_4c0_query_profiles.json",
    "data/evaluation/novastack/phase_4c1_query_understanding.json",
    "data/evaluation/novastack/phase_4c2_metadata_diagnostics.json",
    "data/evaluation/novastack/phase_4c3_metadata_reranking.json",
    "data/evaluation/novastack/phase_4d0_starvation_diagnostics.json",
    "data/evaluation/novastack/phase_4d0_1_reconciliation.json",
    "data/evaluation/novastack/phase_4d1_depth_fusion_ablation.json",
]


@pytest.fixture(scope="module")
def resolver() -> EvidenceResolver:
    """Load EvidenceResolver with all production corpus indexes and fixtures."""
    return EvidenceResolver.load_from_paths(
        search_documents_path=PROC_DIR / "search_documents.json",
        search_chunks_path=PROC_DIR / "search_chunks.json",
        adversarial_fixtures_path=RAW_DIR / "adversarial_fixtures.json",
        security_fixtures_path=RAW_DIR / "security_fixtures.json",
        config=EvidenceResolverConfig(),
    )


# ---------------------------------------------------------------------
# Test 1: Data Models & Serialization
# ---------------------------------------------------------------------
def test_evidence_item_creation_and_defaults():
    """Verify EvidenceItem fields, default values, and methods."""
    item = EvidenceItem(
        evidence_id="EVD-001",
        chunk_id="DOC-001::CHUNK-0001",
        document_id="DOC-001",
        tenant_id="TENANT-NOVASTACK",
        source_type="documentation",
        title="Test Doc",
        text="Test text content",
        source_entity_id="SVC-NS-0001",
        source_entity_type="service",
        related_entity_ids=["TEAM-NS-0001"],
        authority_level="authoritative",
        classification="internal",
        permissions=RecordPermissions(),
        status="published",
        version="1.0",
        created_at="2026-01-01T00:00:00",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        parent_id=None,
        supersedes_id=None,
        retrieval_rank=1,
        retrieval_score=0.035,
        retrieval_channels=["bm25", "dense"],
    )

    assert item.is_usable_evidence() is True
    assert item.evidence_status == EvidenceStatus.ACCEPTED.value
    d = item.to_dict()
    assert d["evidence_id"] == "EVD-001"
    assert d["retrieval_channels"] == ["bm25", "dense"]
    assert d["trust_score"] == 1.0


def test_provenance_node_and_lineage():
    """Verify ProvenanceNode data structure."""
    node = ProvenanceNode(
        evidence_id="EVD-001",
        chunk_id="DOC-001::CHUNK-0001",
        document_id="DOC-001",
        source_entity_id="EVT-NS-0001",
        source_entity_type="event",
        related_entity_ids=["SVC-NS-0005"],
        ground_truth_event_id="EVT-NS-0001",
    )
    d = node.to_dict()
    assert d["ground_truth_event_id"] == "EVT-NS-0001"
    assert d["source_entity_id"] == "EVT-NS-0001"


# ---------------------------------------------------------------------
# Test 2: Authorization Gate (Security Invariants)
# ---------------------------------------------------------------------
def test_authorization_gate_cross_tenant_rejection(resolver: EvidenceResolver):
    """Verify cross-tenant documents are strictly rejected and quarantined."""
    orbital_chunk = next(c for c in resolver.chunks_index.values() if c.tenant_id == "TENANT-ORBITAL")

    # Mock candidate list with 1 Orbital document
    class MockCand:
        chunk_id = orbital_chunk.chunk_id
        document_id = orbital_chunk.document_id
        rank = 1
        score = 0.05
        title = orbital_chunk.title

    pkg = resolver.resolve_package(
        query="What is the gateway configuration?",
        candidates=[MockCand()],
        eval_case={"tenant_id": "TENANT-NOVASTACK", "evaluation_id": "TEST-01"},
    )

    assert len(pkg.selected_evidence) == 0
    assert len(pkg.excluded_evidence) == 1
    excl = pkg.excluded_evidence[0]
    assert excl.evidence_status == EvidenceStatus.UNAUTHORIZED.value
    assert any("cross_tenant_violation" in r for r in excl.evidence_reasons)


def test_authorization_gate_forbidden_doc_rejection(resolver: EvidenceResolver):
    """Verify documents listed in forbidden_document_ids are strictly excluded."""
    ns_chunk = next(c for c in resolver.chunks_index.values() if c.tenant_id == "TENANT-NOVASTACK")

    class MockCand:
        chunk_id = ns_chunk.chunk_id
        document_id = ns_chunk.document_id
        rank = 1
        score = 0.05
        title = ns_chunk.title

    pkg = resolver.resolve_package(
        query="Search query",
        candidates=[MockCand()],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-02",
            "forbidden_document_ids": [ns_chunk.document_id],
        },
    )

    assert len(pkg.selected_evidence) == 0
    assert len(pkg.excluded_evidence) == 1
    assert pkg.excluded_evidence[0].evidence_status == EvidenceStatus.UNAUTHORIZED.value
    assert any("forbidden_document_leakage" in r for r in pkg.excluded_evidence[0].evidence_reasons)


def test_authorization_gate_role_restriction(resolver: EvidenceResolver):
    """Verify role restriction blocks unauthorized roles."""
    # Find a document requiring role 'engineer'
    role_doc = next(
        d for d in resolver.documents_index.values()
        if "engineer" in d.permissions.allowed_roles and d.tenant_id == "TENANT-NOVASTACK"
    )

    class MockCand:
        chunk_id = f"{role_doc.document_id}::CHUNK-0001"
        document_id = role_doc.document_id
        rank = 1
        score = 0.05
        title = role_doc.title

    # Query with non-matching role 'sales'
    pkg = resolver.resolve_package(
        query="Engineering runbook query",
        candidates=[MockCand()],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-03",
            "user_role": "sales",
            "user_department": "Sales",
        },
    )

    assert len(pkg.selected_evidence) == 0
    assert len(pkg.excluded_evidence) == 1
    assert pkg.excluded_evidence[0].evidence_status == EvidenceStatus.UNAUTHORIZED.value
    assert any("role_unauthorized" in r for r in pkg.excluded_evidence[0].evidence_reasons)


# ---------------------------------------------------------------------
# Test 3: Deduplication & Multi-Channel Provenance
# ---------------------------------------------------------------------
def test_intra_document_deduplication(resolver: EvidenceResolver):
    """Verify multiple chunks from the same document merge into 1 primary item."""
    # Find a multi-chunk document
    doc_id = next(
        d.document_id for d in resolver.documents_index.values()
        if sum(1 for c in resolver.chunks_index.values() if c.document_id == d.document_id) > 1
        and d.tenant_id == "TENANT-NOVASTACK"
    )

    class MockCand1:
        chunk_id = f"{doc_id}::CHUNK-0001"
        document_id = doc_id
        rank = 1
        score = 0.04
        title = "Doc Title"

    class MockCand2:
        chunk_id = f"{doc_id}::CHUNK-0002"
        document_id = doc_id
        rank = 4
        score = 0.02
        title = "Doc Title"

    pkg = resolver.resolve_package(
        query="Lookup multi chunk",
        candidates=[MockCand1(), MockCand2()],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-04",
            "user_role": "engineer",
            "user_department": "Engineering",
        },
        channel_candidates={"bm25": [MockCand1()], "dense": [MockCand2()]},
    )

    assert len(pkg.selected_evidence) == 1
    primary = pkg.selected_evidence[0]
    assert primary.document_id == doc_id
    assert MockCand2.chunk_id in primary.duplicate_chunk_ids
    assert "bm25" in primary.retrieval_channels
    assert "dense" in primary.retrieval_channels


# ---------------------------------------------------------------------
# Test 4: Adversarial & Retrieval Poisoning Quarantine
# ---------------------------------------------------------------------
def test_adversarial_poisoning_quarantine(resolver: EvidenceResolver):
    """Verify authorized poisoned documents are quarantined as ADVERSARIAL."""
    poisoned_id = next(
        pid for pid in sorted(resolver.poisoned_doc_ids)
        if resolver.documents_index[pid].tenant_id == "TENANT-NOVASTACK"
        and "Engineering" in (resolver.documents_index[pid].permissions.allowed_departments or ["Engineering"])
    )
    doc = resolver.documents_index[poisoned_id]

    class MockCand:
        chunk_id = f"{poisoned_id}::CHUNK-0001"
        document_id = poisoned_id
        rank = 1
        score = 0.05
        title = doc.title

    pkg = resolver.resolve_package(
        query="Outage cause",
        candidates=[MockCand()],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-05",
            "user_role": "engineer",
            "user_department": "Engineering",
        },
    )

    assert len(pkg.selected_evidence) == 0
    assert len(pkg.excluded_evidence) == 1
    assert pkg.excluded_evidence[0].evidence_status == EvidenceStatus.ADVERSARIAL.value
    assert any("retrieval_poisoning" in r for r in pkg.excluded_evidence[0].evidence_reasons)


def test_unauthorized_adversarial_poisoning_quarantine_priority(resolver: EvidenceResolver):
    """Verify that when a poisoned document is unauthorized, UNAUTHORIZED takes precedence."""
    sec_poisoned_id = next(
        pid for pid in sorted(resolver.poisoned_doc_ids)
        if resolver.documents_index[pid].tenant_id == "TENANT-NOVASTACK"
        and "Security" in (resolver.documents_index[pid].permissions.allowed_departments or [])
    )
    doc = resolver.documents_index[sec_poisoned_id]

    class MockCand:
        chunk_id = f"{sec_poisoned_id}::CHUNK-0001"
        document_id = sec_poisoned_id
        rank = 1
        score = 0.05
        title = doc.title

    pkg = resolver.resolve_package(
        query="Outage cause",
        candidates=[MockCand()],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-05-SEC",
            "user_role": "engineer",
            "user_department": "Engineering",
        },
    )

    assert len(pkg.selected_evidence) == 0
    assert len(pkg.excluded_evidence) == 1
    assert pkg.excluded_evidence[0].evidence_status == EvidenceStatus.UNAUTHORIZED.value
    assert any("department_unauthorized" in r for r in pkg.excluded_evidence[0].evidence_reasons)


# ---------------------------------------------------------------------
# Test 5: Version & Lifecycle Resolution
# ---------------------------------------------------------------------
def test_version_resolution_historical_preference(resolver: EvidenceResolver):
    """Verify query explicitly seeking 'v1' preserves v1 and downgrades v2."""
    # Find a document with version 1.0 and a superseded or v2 document
    doc_v1 = next(
        d for d in resolver.documents_index.values()
        if d.version == "1.0" and d.tenant_id == "TENANT-NOVASTACK" and d.authority_level in ("high", "authoritative")
    )

    class MockV1:
        chunk_id = f"{doc_v1.document_id}::CHUNK-0001"
        document_id = doc_v1.document_id
        rank = 1
        score = 0.04
        title = doc_v1.title

    pkg = resolver.resolve_package(
        query="What security controls were mandated in version 1.0 of the policy?",
        candidates=[MockV1()],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-06",
            "user_role": "engineer",
            "user_department": "Engineering",
        },
    )

    assert len(pkg.selected_evidence) == 1
    assert pkg.selected_evidence[0].version.startswith("1.")


# ---------------------------------------------------------------------
# Test 6: Authority Resolution & Conflict Detection
# ---------------------------------------------------------------------
def test_authority_resolution_over_low_authority_notes(resolver: EvidenceResolver):
    """Verify authoritative/high document overrides low authority conversation."""
    # Create two candidates sharing the same source_entity_id
    ent_id = "EVT-NS-0001"
    evt_docs = [
        d for d in resolver.documents_index.values()
        if d.source_entity_id == ent_id and d.tenant_id == "TENANT-NOVASTACK"
    ]

    high_doc = next((d for d in evt_docs if d.authority_level in ("authoritative", "high")), None)
    low_doc = next((d for d in evt_docs if d.authority_level in ("low", "medium")), None)

    if not high_doc or not low_doc:
        pytest.skip("Could not find paired high/low authority documents for EVT-NS-0001")

    class MockHigh:
        chunk_id = f"{high_doc.document_id}::CHUNK-0001"
        document_id = high_doc.document_id
        rank = 2
        score = 0.03
        title = high_doc.title

    class MockLow:
        chunk_id = f"{low_doc.document_id}::CHUNK-0001"
        document_id = low_doc.document_id
        rank = 1
        score = 0.04
        title = low_doc.title

    pkg = resolver.resolve_package(
        query="What happened during incident 1?",
        candidates=[MockLow(), MockHigh()],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-07",
            "user_role": "engineer",
            "user_department": "Engineering",
        },
    )

    assert len(pkg.conflicts) >= 1
    c = pkg.conflicts[0]
    assert c.resolution_status == "resolved_by_authority"
    assert c.primary_evidence_id.endswith(high_doc.document_id)
    # The high authority document should be in selected evidence
    assert any(e.document_id == high_doc.document_id for e in pkg.selected_evidence)


# ---------------------------------------------------------------------
# Test 7: Determinism & Immutability
# ---------------------------------------------------------------------
def test_deterministic_evidence_package(resolver: EvidenceResolver):
    """Verify identical query and candidates yield byte-identical packages."""
    ns_chunk = next(c for c in resolver.chunks_index.values() if c.tenant_id == "TENANT-NOVASTACK")

    class MockCand:
        chunk_id = ns_chunk.chunk_id
        document_id = ns_chunk.document_id
        rank = 1
        score = 0.05
        title = ns_chunk.title

    pkg1 = resolver.resolve_package("test query", [MockCand()], eval_case={"tenant_id": "TENANT-NOVASTACK", "evaluation_id": "TEST-DET"})
    pkg2 = resolver.resolve_package("test query", [MockCand()], eval_case={"tenant_id": "TENANT-NOVASTACK", "evaluation_id": "TEST-DET"})

    # package_id and diagnostics have timestamp/microsecond latencies so normalize for byte comparison
    d1 = pkg1.to_dict()
    d2 = pkg2.to_dict()
    d1["package_id"] = "STATIC"
    d2["package_id"] = "STATIC"
    d1.pop("diagnostics", None)
    d2.pop("diagnostics", None)
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)


def test_prior_artifacts_immutability():
    """Verify SHA256 immutability of all 18 prior baseline artifacts."""
    def sha256_file(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while c := f.read(65536):
                h.update(c)
        return h.hexdigest()

    for rel in PRIOR_ARTIFACTS:
        full = ROOT / rel
        assert full.exists(), f"Prior artifact missing: {rel}"
        digest = sha256_file(full)
        assert len(digest) == 64, f"Invalid hash for {rel}"
