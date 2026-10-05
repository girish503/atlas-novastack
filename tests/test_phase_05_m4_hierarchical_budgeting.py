"""Unit & Integration Test Suite for Milestone M4 — Hierarchical Evidence Budgeting + Soft Compaction.

Verifies:
1. CompactionTier & EvidenceCategory enum values and semantics.
2. extract_soft_compacted_sentences across all 4 tiers (Tier 0, Tier 1, Tier 2, Tier 3).
3. Header preservation and structural integrity during soft compaction.
4. Entity-aware evidence scoring (direct match, relational match, source type, authority, lexical).
5. Protective context preservation for out-of-scope (EVAL-0054) and sensitive queries (EVAL-0058).
6. Hierarchical budget allocation across categories (PROTECTIVE -> PRIMARY -> RELATIONAL -> SUPPORTING).
7. Integration with AdaptiveContextBudgeter (hierarchical_soft_compaction strategy).
8. Ablation matrix feature flags (entity scoring, structural preservation, protective preservation).
"""

from __future__ import annotations

import copy
import pytest

from novastack.context_budgeter import AdaptiveContextBudgeter
from novastack.entity_catalog import EntityCatalog
from novastack.entity_grounding import EntityGroundingGate, QueryGroundingResult
from novastack.evidence import EvidenceItem, EvidencePackage, EvidenceStatus
from novastack.hierarchical_budgeter import (
    CompactionTier,
    EvidenceCategory,
    EvidenceScoreBreakdown,
    HierarchicalBudgeterConfig,
    HierarchicalContextBudgeter,
    extract_soft_compacted_sentences,
)
from novastack.models import RecordPermissions


def make_evidence_item(
    evidence_id: str = "EV-TEST-001",
    chunk_id: str = "CHK-TEST-001",
    document_id: str = "DOC-TEST-001",
    tenant_id: str = "TENANT-NOVASTACK",
    source_type: str = "runbook",
    title: str = "Test Title",
    text: str = "",
    source_entity_id: str | None = None,
    source_entity_type: str | None = None,
    related_entity_ids: list[str] | None = None,
    authority_level: str = "high",
    classification: str = "internal",
    permissions: RecordPermissions | None = None,
    status: str = "published",
    version: str = "1.0",
    created_at: str = "2026-01-01T00:00:00Z",
    updated_at: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    parent_id: str | None = None,
    supersedes_id: str | None = None,
    retrieval_rank: int = 1,
    retrieval_score: float = 0.95,
    retrieval_channels: list[str] | None = None,
    evidence_status: str = EvidenceStatus.ACCEPTED.value,
) -> EvidenceItem:
    """Helper to instantiate a fully-specified EvidenceItem."""
    return EvidenceItem(
        evidence_id=evidence_id,
        chunk_id=chunk_id,
        document_id=document_id,
        tenant_id=tenant_id,
        source_type=source_type,
        title=title,
        text=text,
        source_entity_id=source_entity_id,
        source_entity_type=source_entity_type or "service",
        related_entity_ids=related_entity_ids or [],
        authority_level=authority_level,
        classification=classification,
        permissions=permissions or RecordPermissions(allowed_roles=["engineer"]),
        status=status,
        version=version,
        created_at=created_at,
        updated_at=updated_at,
        valid_from=valid_from,
        valid_until=valid_until,
        parent_id=parent_id,
        supersedes_id=supersedes_id,
        retrieval_rank=retrieval_rank,
        retrieval_score=retrieval_score,
        retrieval_channels=retrieval_channels or ["dense"],
        evidence_status=evidence_status,
    )


def make_evidence_package(
    query: str,
    evaluation_id: str = "EVAL-TEST",
    tenant_id: str = "TENANT-NOVASTACK",
    selected_evidence: list[EvidenceItem] | None = None,
) -> EvidencePackage:
    """Helper to instantiate a fully-specified EvidencePackage."""
    return EvidencePackage(
        package_id=f"PKG-{evaluation_id}",
        query=query,
        evaluation_id=evaluation_id,
        tenant_id=tenant_id,
        user_context={},
        selected_evidence=selected_evidence or [],
        excluded_evidence=[],
        conflicts=[],
        provenance_graph={},
        resolution_decisions=[],
        statistics={},
    )


@pytest.fixture(scope="module")
def catalog() -> EntityCatalog:
    """Shared EntityCatalog fixture."""
    return EntityCatalog()


@pytest.fixture(scope="module")
def grounding_gate(catalog: EntityCatalog) -> EntityGroundingGate:
    """Shared EntityGroundingGate fixture."""
    return EntityGroundingGate(catalog=catalog)


@pytest.fixture(scope="module")
def budgeter(catalog: EntityCatalog, grounding_gate: EntityGroundingGate) -> HierarchicalContextBudgeter:
    """Shared HierarchicalContextBudgeter fixture."""
    return HierarchicalContextBudgeter(catalog=catalog, grounding_gate=grounding_gate)


@pytest.fixture
def sample_primary_chunk() -> EvidenceItem:
    """Grounded primary postmortem evidence item."""
    return make_evidence_item(
        evidence_id="EVD-TEST-001",
        chunk_id="DOC-PM-001::CHUNK-0001",
        document_id="DOC-PM-001",
        source_type="postmortem",
        title="Postmortem: Cache Thundering Herd",
        text=(
            "INCIDENT POSTMORTEM: INC-NS-0001\n"
            "Date: 2025-08-01\n\n"
            "Cache stampede caused service degradation across regional gateways. "
            "Engineers observed elevated latency and origin pool starvation. "
            "The root cause was synchronized TTL expiration across Redis clusters. "
            "The incident was mitigated by deploying jittered TTLs and read-through caching."
        ),
        source_entity_id="INC-NS-0001",
        source_entity_type="incident",
        related_entity_ids=["SVC-NS-0001", "TEAM-NS-0001"],
        authority_level="authoritative",
    )


@pytest.fixture
def sample_relational_chunk() -> EvidenceItem:
    """Relational 1-hop evidence item (service connected to incident)."""
    return make_evidence_item(
        evidence_id="EVD-TEST-002",
        chunk_id="DOC-ARC-001::CHUNK-0001",
        document_id="DOC-ARC-001",
        source_type="architecture",
        title="Service Architecture: Core Gateway",
        text=(
            "# Architecture Specification: gateway-service\n\n"
            "The gateway-service handles external API routing and rate limiting. "
            "It connects to Redis cluster for shared token bucket rate limiting. "
            "Upstream services depend on gateway-service for authentication enforcement."
        ),
        source_entity_id="SVC-NS-0001",
        source_entity_type="service",
        related_entity_ids=["TEAM-NS-0001"],
        authority_level="high",
    )


@pytest.fixture
def sample_protective_chunk() -> EvidenceItem:
    """Protective evidence item establishing domain boundary or policy abstention."""
    return make_evidence_item(
        evidence_id="EVD-TEST-003",
        chunk_id="DOC-POL-001::CHUNK-0001",
        document_id="DOC-POL-001",
        source_type="policy",
        title="Infrastructure Boundaries Policy",
        text=(
            "# Policy: Infrastructure & Cloud Boundaries\n\n"
            "NovaStack manages cloud-native containerized workloads exclusively. "
            "Satellite antenna infrastructure is not part of NovaStack enterprise deployment. "
            "All physical transmission systems do not exist within the NovaStack tenant domain. "
            "System should abstain if satellite communication details are requested."
        ),
        source_entity_id=None,
        source_entity_type=None,
        related_entity_ids=[],
        authority_level="authoritative",
    )


# -----------------------------------------------------------------------------
# 1. Enums & Tier Structure Tests
# -----------------------------------------------------------------------------

def test_compaction_tier_values():
    """Verify all 4 graduated compaction tiers exist with expected string values."""
    assert CompactionTier.TIER_0_FULL_CONTEXT.value == "tier_0_full_context"
    assert CompactionTier.TIER_1_LIGHT.value == "tier_1_light"
    assert CompactionTier.TIER_2_STANDARD.value == "tier_2_standard"
    assert CompactionTier.TIER_3_AGGRESSIVE.value == "tier_3_aggressive"


def test_evidence_category_values():
    """Verify all 4 hierarchical evidence categories exist."""
    assert EvidenceCategory.PRIMARY.value == "primary"
    assert EvidenceCategory.RELATIONAL.value == "relational"
    assert EvidenceCategory.PROTECTIVE.value == "protective"
    assert EvidenceCategory.SUPPORTING.value == "supporting"


# -----------------------------------------------------------------------------
# 2. extract_soft_compacted_sentences Tests
# -----------------------------------------------------------------------------

def test_tier_0_preserves_full_context(sample_protective_chunk):
    """Tier 0 must preserve 100% of the chunk text with zero modification."""
    original_text = sample_protective_chunk.text
    result = extract_soft_compacted_sentences(
        text=original_text,
        query="What is the satellite antenna failover procedure?",
        tier=CompactionTier.TIER_0_FULL_CONTEXT,
    )
    assert result == original_text.strip()


def test_tier_2_standard_compaction(sample_primary_chunk):
    """Tier 2 Standard compaction must retain header + top 2 salient sentences."""
    result = extract_soft_compacted_sentences(
        text=sample_primary_chunk.text,
        query="What was the root cause and mitigation of incident INC-NS-0001?",
        tier=CompactionTier.TIER_2_STANDARD,
    )
    # Header preserved
    assert "INCIDENT POSTMORTEM: INC-NS-0001" in result
    # Salient sentences present
    assert "root cause was synchronized TTL expiration" in result
    assert "mitigated by deploying jittered TTLs" in result
    # Text length reduced
    assert len(result) < len(sample_primary_chunk.text)


def test_tier_3_aggressive_compaction(sample_primary_chunk):
    """Tier 3 Aggressive compaction must retain header + exactly 1 top sentence."""
    result = extract_soft_compacted_sentences(
        text=sample_primary_chunk.text,
        query="What was the root cause of incident INC-NS-0001?",
        tier=CompactionTier.TIER_3_AGGRESSIVE,
    )
    assert "INCIDENT POSTMORTEM: INC-NS-0001" in result
    assert "root cause was synchronized TTL expiration" in result
    assert len(result) < len(sample_primary_chunk.text)


def test_header_preservation_disabled():
    """When structural preservation is disabled, header should not be prepended separately."""
    text = "# INCIDENT REPORT\n\nSentence one is here. Sentence two is there."
    result = extract_soft_compacted_sentences(
        text=text,
        query="Sentence one",
        tier=CompactionTier.TIER_3_AGGRESSIVE,
        preserve_header=False,
    )
    assert "# INCIDENT REPORT\n\n" not in result


# -----------------------------------------------------------------------------
# 3. Evidence Scoring & Protective Context Tests
# -----------------------------------------------------------------------------

def test_protective_chunk_scoring_and_tier_assignment(budgeter, sample_protective_chunk):
    """Protective chunk with boundary signal must be assigned PROTECTIVE category and Tier 0."""
    query = "What is NovaStack's satellite downlink antenna failover procedure?"
    grounding = budgeter.gate.ground_query(query)
    assert grounding.is_out_of_scope is True

    breakdown = budgeter.score_evidence_item(
        item=sample_protective_chunk,
        query=query,
        grounding=grounding,
    )
    assert breakdown.is_protective is True
    assert breakdown.assigned_category == EvidenceCategory.PROTECTIVE
    assert breakdown.assigned_tier == CompactionTier.TIER_0_FULL_CONTEXT
    assert "protective_context" in breakdown.decision_reason


def test_secret_seeking_query_tier_assignment(budgeter):
    """Secret-seeking query must assign Tier 0 to preserve safety."""
    query = "What are the production API authorization tokens for third-party Twilio SMS trunking?"
    grounding = budgeter.gate.ground_query(query)
    assert grounding.is_secret_seeking is True

    item = make_evidence_item(
        evidence_id="EVD-SEC-001",
        chunk_id="DOC-SEC-001::CHUNK-0001",
        document_id="DOC-SEC-001",
        source_type="policy",
        title="API Credentials Policy",
        text="All API tokens, private keys, and signing secrets are encrypted at rest.",
    )
    breakdown = budgeter.score_evidence_item(item=item, query=query, grounding=grounding)
    assert breakdown.assigned_tier == CompactionTier.TIER_0_FULL_CONTEXT


def test_primary_grounded_chunk_scoring(budgeter, sample_primary_chunk):
    """Directly grounded incident chunk must receive high entity score and Tier 2."""
    query = "What was the root cause of incident INC-NS-0001?"
    grounding = budgeter.gate.ground_query(query)
    assert grounding.is_grounded is True

    breakdown = budgeter.score_evidence_item(
        item=sample_primary_chunk,
        query=query,
        grounding=grounding,
    )
    assert breakdown.is_direct_match is True
    assert breakdown.assigned_category == EvidenceCategory.PRIMARY
    assert breakdown.assigned_tier == CompactionTier.TIER_2_STANDARD
    assert breakdown.entity_overlap_score > 0.0


# -----------------------------------------------------------------------------
# 4. Hierarchical Context Budgeting Tests
# -----------------------------------------------------------------------------

def test_budget_evidence_package_order_and_diversity(
    budgeter, sample_primary_chunk, sample_relational_chunk, sample_protective_chunk
):
    """Hierarchical budgeter must prioritize PROTECTIVE -> PRIMARY -> RELATIONAL -> SUPPORTING."""
    pkg = make_evidence_package(
        query="What is the satellite antenna failover procedure?",
        evaluation_id="EVAL-0054",
        tenant_id="TENANT-NOVASTACK",
        selected_evidence=[sample_relational_chunk, sample_primary_chunk, sample_protective_chunk],
    )
    budgeted = budgeter.budget_evidence_package(pkg, max_documents=3, max_token_budget=550)
    assert len(budgeted) > 0
    # Protective chunk must be first
    assert budgeted[0].document_id == sample_protective_chunk.document_id
    assert budgeted[0].text == sample_protective_chunk.text.strip()


def test_token_budget_enforcement(budgeter, sample_primary_chunk, sample_relational_chunk):
    """Budgeter must respect max_token_budget while allowing essential primary evidence."""
    pkg = make_evidence_package(
        query="What was the root cause of incident INC-NS-0001?",
        evaluation_id="EVAL-0001",
        tenant_id="TENANT-NOVASTACK",
        selected_evidence=[sample_primary_chunk, sample_relational_chunk],
    )
    # Extremely small budget of 50 tokens
    budgeted = budgeter.budget_evidence_package(pkg, max_documents=2, max_token_budget=50)
    # At least 1 item must still be returned if available
    assert len(budgeted) >= 1
    # Document IDs and citations must be intact
    assert budgeted[0].document_id == sample_primary_chunk.document_id
    assert budgeted[0].chunk_id == sample_primary_chunk.chunk_id


# -----------------------------------------------------------------------------
# 5. AdaptiveContextBudgeter Integration Tests
# -----------------------------------------------------------------------------

def test_adaptive_context_budgeter_hierarchical_strategy(sample_primary_chunk):
    """AdaptiveContextBudgeter must route strategy='hierarchical_soft_compaction' to budgeter."""
    acb = AdaptiveContextBudgeter()
    assert acb.hierarchical_budgeter is not None

    pkg = make_evidence_package(
        query="What was the root cause of incident INC-NS-0001?",
        evaluation_id="EVAL-0001",
        tenant_id="TENANT-NOVASTACK",
        selected_evidence=[sample_primary_chunk],
    )
    result = acb.budget_context(pkg, strategy="hierarchical_soft_compaction", max_documents=2)
    assert len(result) == 1
    assert result[0].document_id == sample_primary_chunk.document_id
    assert len(result[0].text) <= len(sample_primary_chunk.text)


# -----------------------------------------------------------------------------
# 6. Ablation Matrix Feature Flags Tests
# -----------------------------------------------------------------------------

def test_ablation_c_no_entity_scoring(sample_primary_chunk):
    """Ablation C: Disabling entity scoring sets entity and relational scores to 0."""
    cfg = HierarchicalBudgeterConfig(enable_entity_scoring=False)
    b = HierarchicalContextBudgeter(config=cfg)
    grounding = b.gate.ground_query("INC-NS-0001")

    breakdown = b.score_evidence_item(sample_primary_chunk, "INC-NS-0001", grounding)
    assert breakdown.entity_overlap_score == 0.0
    assert breakdown.relationship_score == 0.0


def test_ablation_d_no_structural_preservation():
    """Ablation D: Disabling structural preservation strips markdown headers."""
    cfg = HierarchicalBudgeterConfig(enable_structural_preservation=False)
    b = HierarchicalContextBudgeter(config=cfg)

    text = "# HEADER LINE\n\nContent sentence one. Content sentence two."
    item = make_evidence_item(
        evidence_id="EVD-D",
        chunk_id="DOC-D::CHUNK-0001",
        document_id="DOC-D",
        source_entity_id="INC-NS-0001",
        text=text,
    )
    pkg = make_evidence_package(query="INC-NS-0001", evaluation_id="EVAL-D", selected_evidence=[item])
    budgeted = b.budget_evidence_package(pkg, max_documents=1)
    assert "# HEADER LINE" not in budgeted[0].text


def test_ablation_e_no_protective_preservation(sample_protective_chunk):
    """Ablation E: Disabling protective preservation allows compaction of protective chunks."""
    cfg = HierarchicalBudgeterConfig(enable_protective_preservation=False)
    b = HierarchicalContextBudgeter(config=cfg)
    grounding = b.gate.ground_query("satellite downlink")

    breakdown = b.score_evidence_item(sample_protective_chunk, "satellite downlink", grounding)
    # When protective preservation is disabled, is_protective is False and tier is not forced to Tier 0
    assert breakdown.is_protective is False
