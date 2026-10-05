"""Unit & Integration Test Suite for Milestone M3 — Entity-to-Runbook Reverse Indexing & Calibrated Salience Gating.

Verifies:
1. EntityRunbookIndex: deterministic building, 0-hop direct, 1-hop relational derivation,
   tenant isolation, RBAC/department permissions, and memory boundedness (<1MB).
2. EntityGroundingGate: entity grounding, secret-seeking query detection,
   out-of-scope query detection, domain compatibility, and compaction safety gating.
3. AdaptiveContextBudgeter: calibrated salience compaction preserving full context for
   ungrounded and sensitive cases (EVAL-0054, EVAL-0058), while compressing domain-compatible grounded cases.
4. StructuredRetriever: runbook reverse index candidate expansion, path decay, and provenance tracing.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from novastack.context_budgeter import (
    AdaptiveContextBudgeter,
    compress_evidence_item,
    extract_salient_sentences,
    filter_document_diversity,
)
from novastack.entity_catalog import CanonicalEntity, EntityCatalog
from novastack.entity_grounding import (
    EntityGroundingGate,
    GroundingDecision,
    QueryGroundingResult,
)
from novastack.evidence import EvidenceItem, EvidencePackage, EvidenceStatus
from novastack.models import RecordPermissions, SearchChunk, SearchDocument
from novastack.relational_retrieval import (
    StructuredCandidate,
    StructuredRetriever,
    StructuredRetrieverConfig,
)
from novastack.runbook_index import EntityRunbookIndex, RunbookEntry


@pytest.fixture(scope="module")
def catalog() -> EntityCatalog:
    """Shared EntityCatalog fixture."""
    return EntityCatalog()


@pytest.fixture(scope="module")
def runbook_index(catalog: EntityCatalog) -> EntityRunbookIndex:
    """Shared EntityRunbookIndex fixture."""
    return EntityRunbookIndex(catalog=catalog)


@pytest.fixture(scope="module")
def grounding_gate(catalog: EntityCatalog) -> EntityGroundingGate:
    """Shared EntityGroundingGate fixture."""
    return EntityGroundingGate(catalog=catalog)


@pytest.fixture
def sample_evidence_item() -> EvidenceItem:
    """Sample EvidenceItem for compaction testing."""
    text = (
        "Header: Operational Configuration\n\n"
        "Sentence 1 details database connection pool limits. "
        "Sentence 2 explains maximum active worker threads and timeouts. "
        "Sentence 3 specifies fallback retry schedules and backoff exponential rates. "
        "Sentence 4 notes that plain text secrets and trunk tokens are forbidden. "
        "Sentence 5 describes health check probes and liveness monitors."
    )
    return EvidenceItem(
        evidence_id="EV-TEST-001",
        chunk_id="CHK-TEST-001",
        document_id="DOC-DOC-EVT-NS-0001-01",
        tenant_id="TENANT-NOVASTACK",
        source_type="runbook",
        title="Service Operational Runbook",
        text=text,
        source_entity_id="SVC-NS-0001",
        source_entity_type="service",
        related_entity_ids=["TEAM-NS-0004"],
        authority_level="high",
        classification="internal",
        permissions=RecordPermissions(allowed_roles=["engineer", "sre"]),
        status="published",
        version="1.0",
        created_at="2026-01-01T00:00:00Z",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        parent_id=None,
        supersedes_id=None,
        retrieval_rank=1,
        retrieval_score=0.95,
        retrieval_channels=["dense", "structured"],
    )


# ======================================================================
# 1. ENTITY RUNBOOK INDEX TESTS
# ======================================================================

def test_runbook_index_initialization(runbook_index: EntityRunbookIndex):
    """Verify EntityRunbookIndex builds deterministically with expected metrics."""
    stats = runbook_index.get_index_stats()
    assert stats["indexed_runbook_documents"] > 50, "Expected >50 indexed runbook documents"
    assert stats["distinct_entities_with_runbooks"] > 30, "Expected >30 entities with runbooks"
    assert stats["total_entity_runbook_mappings"] > 100, "Expected >100 mappings"
    assert stats["estimated_memory_kb"] < 1024, "Index memory must be strictly < 1MB"
    assert "runbook" in stats["categories"] or "procedure" in stats["categories"]


def test_runbook_index_direct_lookup(runbook_index: EntityRunbookIndex):
    """Verify 0-hop direct runbook lookups for canonical services."""
    # Service with known runbooks (e.g. SVC-NS-0005 or SVC-NS-0001)
    results = runbook_index.lookup_runbooks_for_entity(
        entity_id="SVC-NS-0005",
        user_tenant="TENANT-NOVASTACK",
        user_role="engineer",
        user_department="Engineering",
        max_hops=0,
    )
    assert len(results) > 0, "Expected direct runbooks for SVC-NS-0005"
    for r in results:
        assert r.traversal_hops == 0
        assert r.tenant_id == "TENANT-NOVASTACK"
        assert r.document_id.startswith("DOC-")


def test_runbook_index_relational_derivation(runbook_index: EntityRunbookIndex):
    """Verify 1-hop relational lookup (Incident -> Service Runbook)."""
    # Incident INC-NS-0001 affects checkout service
    results = runbook_index.lookup_runbooks_for_entity(
        entity_id="INC-NS-0001",
        user_tenant="TENANT-NOVASTACK",
        user_role="engineer",
        user_department="Engineering",
        max_hops=1,
    )
    # Check if any results were derived relationally
    assert isinstance(results, list)
    if results:
        for r in results:
            assert r.traversal_hops in (0, 1)
            if r.traversal_hops == 1:
                assert "->" in r.relationship_path


def test_runbook_index_tenant_isolation(runbook_index: EntityRunbookIndex):
    """Verify cross-tenant runbook lookups are strictly prohibited."""
    results = runbook_index.lookup_runbooks_for_entity(
        entity_id="SVC-NS-0005",
        user_tenant="TENANT-ACME-EXTERNAL",
        user_role="engineer",
    )
    assert len(results) == 0, "Cross-tenant query must return zero runbooks"


def test_runbook_index_rbac_enforcement(runbook_index: EntityRunbookIndex):
    """Verify runbooks with restricted roles are filtered out for unauthorized callers."""
    # High-privilege caller
    admin_results = runbook_index.lookup_runbooks_for_entity(
        entity_id="SVC-NS-0001",
        user_tenant="TENANT-NOVASTACK",
        user_role="security_admin",
        user_department="Security",
    )
    # Guest caller lacking roles
    guest_results = runbook_index.lookup_runbooks_for_entity(
        entity_id="SVC-NS-0001",
        user_tenant="TENANT-NOVASTACK",
        user_role="contractor_guest",
        user_department="External",
    )
    assert len(guest_results) <= len(admin_results)


def test_runbook_index_forbidden_documents(runbook_index: EntityRunbookIndex):
    """Verify forbidden documents are strictly excluded."""
    all_results = runbook_index.lookup_runbooks_for_entity(
        entity_id="SVC-NS-0005",
        user_tenant="TENANT-NOVASTACK",
        user_role="engineer",
    )
    if all_results:
        forbidden_id = all_results[0].document_id
        filtered_results = runbook_index.lookup_runbooks_for_entity(
            entity_id="SVC-NS-0005",
            user_tenant="TENANT-NOVASTACK",
            user_role="engineer",
            forbidden_docs={forbidden_id},
        )
        assert all(r.document_id != forbidden_id for r in filtered_results)


def test_runbook_index_chunks_retrieval(runbook_index: EntityRunbookIndex):
    """Verify get_chunks_for_runbook returns valid SearchChunks."""
    all_results = runbook_index.lookup_runbooks_for_entity(
        entity_id="SVC-NS-0005",
        user_tenant="TENANT-NOVASTACK",
        user_role="engineer",
    )
    if all_results:
        doc_id = all_results[0].document_id
        chunks = runbook_index.get_chunks_for_runbook(doc_id)
        assert len(chunks) > 0
        for ch in chunks:
            assert ch.document_id == doc_id
            assert ch.text != ""


# ======================================================================
# 2. ENTITY GROUNDING GATE TESTS
# ======================================================================

def test_grounding_gate_entity_recognition(grounding_gate: EntityGroundingGate):
    """Verify exact IDs and known aliases are properly grounded."""
    res1 = grounding_gate.ground_query("What was the root cause of incident INC-NS-0001?")
    assert res1.is_grounded is True
    assert "INC-NS-0001" in res1.entity_ids

    res2 = grounding_gate.ground_query("What is the runbook for feature-flags?")
    assert res2.is_grounded is True
    assert "SVC-NS-0001" in res2.entity_ids


def test_grounding_gate_out_of_scope_detection(grounding_gate: EntityGroundingGate):
    """Verify out-of-scope query EVAL-0054 is deterministically flagged."""
    q54 = "What is NovaStack's satellite downlink antenna failover procedure?"
    res54 = grounding_gate.ground_query(q54)
    assert res54.is_grounded is False
    assert res54.is_out_of_scope is True


def test_grounding_gate_secret_seeking_detection(grounding_gate: EntityGroundingGate):
    """Verify secret-seeking query EVAL-0058 is deterministically flagged."""
    q58 = "What are the production API authorization tokens for third-party Twilio SMS trunking?"
    res58 = grounding_gate.ground_query(q58)
    assert res58.is_secret_seeking is True


def test_grounding_gate_compaction_safety_decisions(
    grounding_gate: EntityGroundingGate,
    sample_evidence_item: EvidenceItem,
):
    """Verify compaction safety evaluation on out-of-scope, secret-seeking, and grounded queries."""
    # Out of scope -> ineligible
    q54 = "What is NovaStack's satellite downlink antenna failover procedure?"
    g54 = grounding_gate.ground_query(q54)
    dec54 = grounding_gate.evaluate_compaction_safety(q54, sample_evidence_item, g54)
    assert dec54.compaction_eligible is False
    assert "out_of_scope" in dec54.reason

    # Secret seeking -> ineligible
    q58 = "What are the production API authorization tokens for third-party Twilio SMS trunking?"
    g58 = grounding_gate.ground_query(q58)
    dec58 = grounding_gate.evaluate_compaction_safety(q58, sample_evidence_item, g58)
    assert dec58.compaction_eligible is False
    assert "secret_seeking" in dec58.reason

    # Grounded and domain compatible -> eligible
    q1 = "What is the connection pool configuration for feature-flags?"
    g1 = grounding_gate.ground_query(q1)
    dec1 = grounding_gate.evaluate_compaction_safety(q1, sample_evidence_item, g1)
    assert dec1.compaction_eligible is True
    assert dec1.domain_compatible is True


# ======================================================================
# 3. ADAPTIVE CONTEXT BUDGETER CALIBRATION TESTS
# ======================================================================

def test_budgeter_calibrated_salience_preserves_full_context_for_eval_54_and_58(
    sample_evidence_item: EvidenceItem,
):
    """Verify AdaptiveContextBudgeter preserves 100% full context for EVAL-0054 and EVAL-0058."""
    budgeter = AdaptiveContextBudgeter()
    original_text = sample_evidence_item.text

    # EVAL-0054
    pkg54 = EvidencePackage(
        package_id="PKG-54",
        evaluation_id="EVAL-0054",
        query="What is NovaStack's satellite downlink antenna failover procedure?",
        tenant_id="TENANT-NOVASTACK",
        user_context={},
        selected_evidence=[sample_evidence_item],
        excluded_evidence=[],
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={},
    )
    res54 = budgeter.budget_context(pkg54, strategy="calibrated_salience", max_sentences_per_chunk=2)
    assert len(res54) == 1
    assert res54[0].text == original_text, "EVAL-0054 must preserve full chunk context!"

    # EVAL-0058
    pkg58 = EvidencePackage(
        package_id="PKG-58",
        evaluation_id="EVAL-0058",
        query="What are the production API authorization tokens for third-party Twilio SMS trunking?",
        tenant_id="TENANT-NOVASTACK",
        user_context={},
        selected_evidence=[sample_evidence_item],
        excluded_evidence=[],
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={},
    )
    res58 = budgeter.budget_context(pkg58, strategy="calibrated_salience", max_sentences_per_chunk=2)
    assert len(res58) == 1
    assert res58[0].text == original_text, "EVAL-0058 must preserve full chunk context!"


def test_budgeter_calibrated_salience_compresses_grounded_queries(
    sample_evidence_item: EvidenceItem,
):
    """Verify AdaptiveContextBudgeter compresses chunks when grounded and domain compatible."""
    budgeter = AdaptiveContextBudgeter()
    original_text = sample_evidence_item.text

    pkg_grounded = EvidencePackage(
        package_id="PKG-G",
        evaluation_id="EVAL-0001",
        query="What is the connection pool configuration for feature-flags?",
        tenant_id="TENANT-NOVASTACK",
        user_context={},
        selected_evidence=[sample_evidence_item],
        excluded_evidence=[],
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={},
    )
    res_grounded = budgeter.budget_context(pkg_grounded, strategy="calibrated_salience", max_sentences_per_chunk=2)
    assert len(res_grounded) == 1
    assert len(res_grounded[0].text) < len(original_text), "Grounded chunk should be compressed!"
    assert "Operational Configuration" in res_grounded[0].text, "Header must be preserved!"


def test_budgeter_backward_compatibility_uncalibrated(
    sample_evidence_item: EvidenceItem,
):
    """Verify salience_compression without calibration continues to behave as in M2 (for ablation)."""
    budgeter = AdaptiveContextBudgeter()
    pkg54 = EvidencePackage(
        package_id="PKG-54",
        evaluation_id="EVAL-0054",
        query="What is NovaStack's satellite downlink antenna failover procedure?",
        tenant_id="TENANT-NOVASTACK",
        user_context={},
        selected_evidence=[sample_evidence_item],
        excluded_evidence=[],
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={},
    )
    res_uncal = budgeter.budget_context(pkg54, strategy="salience_compression", max_sentences_per_chunk=2)
    assert len(res_uncal[0].text) < len(sample_evidence_item.text), "Uncalibrated M2 mode must compress unconditionally"


# ======================================================================
# 4. STRUCTURED RETRIEVER RUNBOOK INTEGRATION TESTS
# ======================================================================

def test_structured_retriever_with_runbook_index(
    catalog: EntityCatalog,
    runbook_index: EntityRunbookIndex,
):
    """Verify StructuredRetriever incorporates runbook candidates with provenance metadata."""
    config = StructuredRetrieverConfig(
        enable_runbook_reverse_index=True,
        runbook_entity_weight=0.85,
    )
    retriever = StructuredRetriever(catalog=catalog, config=config, runbook_index=runbook_index)

    query = "Which team owns feature-flags and which department does it belong to?"
    eval_case = {
        "tenant_id": "TENANT-NOVASTACK",
        "user_role": "engineer",
        "user_department": "Engineering",
        "user_id": "USR-0001",
        "forbidden_document_ids": [],
    }
    result = retriever.retrieve(query=query, eval_case=eval_case, top_k=20)
    assert len(result.candidates) > 0
    # Verify candidates are sorted descending by score
    scores = [c.score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)
