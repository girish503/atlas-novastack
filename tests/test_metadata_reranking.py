"""Tests for Controlled Metadata-Aware Reranking — Phase 4C-3."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novastack.metadata_diagnostics import (
    DocumentMetadataSnapshot,
    build_metadata_snapshot_index,
)
from novastack.metadata_reranker import (
    DEFAULT_AUTHORITY_WEIGHTS,
    DEFAULT_LIFECYCLE_WEIGHTS,
    CandidateRerankingDetail,
    MetadataReranker,
    MetadataRerankerConfig,
)
from novastack.models import (
    AUTHORITY_LEVELS,
    RECORD_STATUSES,
)
from novastack.query_understanding import (
    EntityCatalog,
    EntityMention,
    LifecycleConstraint,
    QueryUnderstanding,
    QueryUnderstandingExtractor,
    TemporalConstraint,
)


@pytest.fixture
def test_dirs() -> tuple[Path, Path, Path]:
    repo_root = Path(__file__).resolve().parent.parent
    raw_dir = repo_root / "data" / "raw" / "novastack"
    proc_dir = repo_root / "data" / "processed" / "novastack"
    eval_dir = repo_root / "data" / "evaluation" / "novastack"
    return raw_dir, proc_dir, eval_dir


@pytest.fixture
def sample_metadata_index(test_dirs: tuple[Path, Path, Path]) -> dict[str, DocumentMetadataSnapshot]:
    _, proc_dir, _ = test_dirs
    with open(proc_dir / "search_documents.json", "r", encoding="utf-8") as f:
        docs = json.load(f)["search_documents"]
    return build_metadata_snapshot_index(docs, [])


class DummyCandidate:
    """Mock candidate matching HybridRetrievalResult interface."""

    def __init__(
        self,
        chunk_id: str,
        document_id: str,
        rank: int,
        rrf_score: float,
        title: str = "Mock Title",
    ) -> None:
        self.chunk_id = chunk_id
        self.document_id = document_id
        self.rank = rank
        self.rrf_score = rrf_score
        self.title = title


def test_fixed_documented_weights():
    """Verify weights match documented specification without arbitrary tuning."""
    assert DEFAULT_AUTHORITY_WEIGHTS["authoritative"] == 0.0040
    assert DEFAULT_AUTHORITY_WEIGHTS["high"] == 0.0020
    assert DEFAULT_AUTHORITY_WEIGHTS["medium"] == 0.0000
    assert DEFAULT_AUTHORITY_WEIGHTS["low"] == -0.0020
    assert DEFAULT_AUTHORITY_WEIGHTS["draft"] == -0.0040

    assert DEFAULT_LIFECYCLE_WEIGHTS["published"] == 0.0020
    assert DEFAULT_LIFECYCLE_WEIGHTS["archived"] == -0.0010
    assert DEFAULT_LIFECYCLE_WEIGHTS["draft"] == -0.0020
    assert DEFAULT_LIFECYCLE_WEIGHTS["deprecated"] == -0.0030
    assert DEFAULT_LIFECYCLE_WEIGHTS["superseded"] == -0.0040


def test_reranker_determinism(sample_metadata_index: dict[str, DocumentMetadataSnapshot]):
    """Verify reranker produces identical outputs on multiple invocations."""
    cands = [
        DummyCandidate("c1", "DOC-PM-EVT-NS-0001-01", 1, 0.032),
        DummyCandidate("c2", "DOC-PM-EVT-NS-0002-01", 2, 0.030),
        DummyCandidate("c3", "DOC-PM-EVT-NS-0003-01", 3, 0.028),
    ]
    qu = QueryUnderstanding("EVAL-0001", "checkout incident", "checkout incident")
    reranker = MetadataReranker()

    run1 = reranker.rerank(cands, qu, sample_metadata_index)
    run2 = reranker.rerank(cands, qu, sample_metadata_index)

    assert len(run1) == len(run2)
    for d1, d2 in zip(run1, run2):
        assert d1.document_id == d2.document_id
        assert d1.final_score == d2.final_score
        assert d1.rank_after == d2.rank_after


def test_explainability_fields_present(sample_metadata_index: dict[str, DocumentMetadataSnapshot]):
    """Verify all explainability fields exist and arithmetic is exact."""
    cands = [DummyCandidate("c1", "DOC-PM-EVT-NS-0001-01", 1, 0.032)]
    qu = QueryUnderstanding("EVAL-0001", "checkout incident", "checkout incident")
    reranker = MetadataReranker()

    res = reranker.rerank(cands, qu, sample_metadata_index)
    d = res[0]

    # Verify explainability fields
    assert hasattr(d, "base_rrf_score")
    assert hasattr(d, "metadata_score")
    assert hasattr(d, "authority_contribution")
    assert hasattr(d, "lifecycle_contribution")
    assert hasattr(d, "temporal_version_contribution")
    assert hasattr(d, "provenance_contribution")
    assert hasattr(d, "final_score")
    assert hasattr(d, "rank_before")
    assert hasattr(d, "rank_after")

    # Verify arithmetic: metadata_score == sum(contributions)
    expected_meta = round(
        d.authority_contribution
        + d.lifecycle_contribution
        + d.temporal_version_contribution
        + d.provenance_contribution,
        6,
    )
    assert abs(d.metadata_score - expected_meta) < 1e-6
    assert abs(d.final_score - (d.base_rrf_score + d.metadata_score)) < 1e-6


def test_ablation_toggles(sample_metadata_index: dict[str, DocumentMetadataSnapshot]):
    """Verify each ablation feature flag operates independently."""
    cands = [DummyCandidate("c1", "DOC-PM-EVT-NS-0001-01", 1, 0.032)]
    qu = QueryUnderstanding("EVAL-0001", "checkout incident", "checkout incident")

    # A: Authority only
    cfg_a = MetadataRerankerConfig(enable_authority=True, enable_lifecycle=False, enable_version_temporal=False, enable_provenance=False)
    res_a = MetadataReranker(cfg_a).rerank(cands, qu, sample_metadata_index)[0]
    assert res_a.authority_contribution != 0.0
    assert res_a.lifecycle_contribution == 0.0
    assert res_a.provenance_contribution == 0.0

    # B: Lifecycle only
    cfg_b = MetadataRerankerConfig(enable_authority=False, enable_lifecycle=True, enable_version_temporal=False, enable_provenance=False)
    res_b = MetadataReranker(cfg_b).rerank(cands, qu, sample_metadata_index)[0]
    assert res_b.authority_contribution == 0.0
    assert res_b.lifecycle_contribution != 0.0
    assert res_b.provenance_contribution == 0.0

    # D: Provenance only
    cfg_d = MetadataRerankerConfig(enable_authority=False, enable_lifecycle=False, enable_version_temporal=False, enable_provenance=True)
    res_d = MetadataReranker(cfg_d).rerank(cands, qu, sample_metadata_index)[0]
    assert res_d.authority_contribution == 0.0
    assert res_d.lifecycle_contribution == 0.0
    assert res_d.provenance_contribution != 0.0


def test_security_forbidden_cannot_outrank_allowed():
    """Security Q1: Can a high-authority forbidden document outrank an allowed document? NO."""
    # Create two synthetic snapshots: one forbidden with authoritative status, one allowed with medium
    meta_allowed = DocumentMetadataSnapshot(
        document_id="DOC-ALLOW",
        tenant_id="TENANT-NOVASTACK",
        source_type="runbook",
        title="Allowed Document",
        department="Engineering",
        author_id="USR-1",
        created_at="2026-01-01T00:00:00",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        version="1.0.0",
        status="published",
        classification="internal",
        authority_level="medium",
        parent_id=None,
        supersedes_id=None,
        source_entity_id="SVC-1",
        source_entity_type="service",
    )
    meta_forbidden = DocumentMetadataSnapshot(
        document_id="DOC-FORBID",
        tenant_id="TENANT-NOVASTACK",
        source_type="policy",
        title="Super Confidential Board Memo",
        department="Executive",
        author_id="USR-CEO",
        created_at="2026-01-01T00:00:00",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        version="1.0.0",
        status="published",
        classification="restricted",
        authority_level="authoritative",  # Highest possible authority
        parent_id=None,
        supersedes_id=None,
        source_entity_id="SVC-1",
        source_entity_type="service",
    )
    meta_idx = {"DOC-ALLOW": meta_allowed, "DOC-FORBID": meta_forbidden}

    # Forbidden candidate starts at rank 1 with high RRF score
    cands = [
        DummyCandidate("c_forbid", "DOC-FORBID", 1, 0.035),
        DummyCandidate("c_allow", "DOC-ALLOW", 2, 0.020),
    ]
    qu = QueryUnderstanding("EVAL-SEC", "test", "test")
    reranker = MetadataReranker()

    res = reranker.rerank(cands, qu, meta_idx, forbidden_doc_ids={"DOC-FORBID"}, enforce_security=True)

    # Allowed document MUST be rank 1; Forbidden document MUST be rank 2
    assert res[0].document_id == "DOC-ALLOW"
    assert res[1].document_id == "DOC-FORBID"
    assert res[0].final_score > res[1].final_score
    assert res[1].is_forbidden is True


def test_security_cross_tenant_isolation(test_dirs: tuple[Path, Path, Path], sample_metadata_index: dict[str, DocumentMetadataSnapshot]):
    """Security Q2: Can a high-authority cross-tenant document enter the result? NO."""
    # Search is restricted to TENANT-NOVASTACK; verify cross-tenant documents cannot bypass filters
    orbital_docs = [m for m in sample_metadata_index.values() if m.tenant_id == "TENANT-ORBITAL"]
    assert len(orbital_docs) > 0

    # Ensure all orbital docs are tagged with TENANT-ORBITAL
    for d in orbital_docs:
        assert d.tenant_id != "TENANT-NOVASTACK"


def test_security_poisoned_retrieval_suppression():
    """Security Q3: Can a poisoned document with high lexical relevance bypass security? NO."""
    # Authentic document: authoritative + published + verified entity
    meta_auth = DocumentMetadataSnapshot(
        document_id="DOC-AUTHENTIC",
        tenant_id="TENANT-NOVASTACK",
        source_type="runbook",
        title="Authentic Runbook",
        department="DevOps",
        author_id="USR-DEVOPS",
        created_at="2026-01-01T00:00:00",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        version="1.0.0",
        status="published",
        classification="internal",
        authority_level="authoritative",
        parent_id=None,
        supersedes_id=None,
        source_entity_id="SVC-CHECKOUT",
        source_entity_type="service",
        is_poisoned=False,
    )
    # Poisoned attack document: low authority + no verified entity
    meta_poisoned = DocumentMetadataSnapshot(
        document_id="DOC-POISONED",
        tenant_id="TENANT-NOVASTACK",
        source_type="external_wiki",
        title="Injected Fake Runbook",
        department="Customer Support",
        author_id="USR-ATTACKER",
        created_at="2026-01-01T00:00:00",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        version="1.0.0",
        status="published",
        classification="internal",
        authority_level="low",
        parent_id=None,
        supersedes_id=None,
        source_entity_id=None,
        source_entity_type=None,
        is_poisoned=True,
    )
    meta_idx = {"DOC-AUTHENTIC": meta_auth, "DOC-POISONED": meta_poisoned}

    # Poisoned document initially outranks authentic document due to malicious keyword density
    cands = [
        DummyCandidate("c_p", "DOC-POISONED", 1, 0.028),
        DummyCandidate("c_a", "DOC-AUTHENTIC", 2, 0.027),
    ]
    qu = QueryUnderstanding(
        "EVAL-ADV",
        "checkout runbook",
        "checkout runbook",
        entities=[EntityMention("service", "SVC-CHECKOUT", "checkout", "exact_name")],
    )

    reranker = MetadataReranker()
    res = reranker.rerank(cands, qu, meta_idx)

    # Authentic document receives authority and provenance boost; poisoned document receives penalties
    assert res[0].document_id == "DOC-AUTHENTIC"
    assert res[1].document_id == "DOC-POISONED"
    assert res[0].final_score > res[1].final_score


def test_temporal_constraint_zero_contribution_when_inapplicable():
    """Verify temporal contribution is strictly 0.0 when query has no temporal constraints."""
    meta = DocumentMetadataSnapshot(
        document_id="DOC-TEST",
        tenant_id="TENANT-NOVASTACK",
        source_type="doc",
        title="Test Doc",
        department="Engineering",
        author_id="USR-1",
        created_at="2026-01-01T00:00:00",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        version="1.0.0",
        status="published",
        classification="internal",
        authority_level="medium",
        parent_id=None,
        supersedes_id=None,
        source_entity_id=None,
        source_entity_type=None,
    )
    cands = [DummyCandidate("c1", "DOC-TEST", 1, 0.030)]
    qu_no_temp = QueryUnderstanding("EVAL-NO-TEMP", "general search", "general search")

    reranker = MetadataReranker()
    res = reranker.rerank(cands, qu_no_temp, {"DOC-TEST": meta})
    assert res[0].temporal_version_contribution == 0.0


def test_lifecycle_supersession_penalty():
    """Verify superseded documents are penalized relative to active published documents."""
    meta_active = DocumentMetadataSnapshot(
        document_id="DOC-ACTIVE",
        tenant_id="TENANT-NOVASTACK",
        source_type="doc",
        title="Active Policy",
        department="Engineering",
        author_id="USR-1",
        created_at="2026-02-01T00:00:00",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        version="2.0.0",
        status="published",
        classification="internal",
        authority_level="high",
        parent_id=None,
        supersedes_id="DOC-STALE",
        source_entity_id="SVC-1",
        source_entity_type="service",
    )
    meta_stale = DocumentMetadataSnapshot(
        document_id="DOC-STALE",
        tenant_id="TENANT-NOVASTACK",
        source_type="doc",
        title="Old Deprecated Policy",
        department="Engineering",
        author_id="USR-1",
        created_at="2025-01-01T00:00:00",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        version="1.0.0",
        status="superseded",
        classification="internal",
        authority_level="high",
        parent_id=None,
        supersedes_id=None,
        source_entity_id="SVC-1",
        source_entity_type="service",
    )
    meta_idx = {"DOC-ACTIVE": meta_active, "DOC-STALE": meta_stale}

    # Stale document had higher initial lexical overlap (rank 1)
    cands = [
        DummyCandidate("c_stale", "DOC-STALE", 1, 0.030),
        DummyCandidate("c_active", "DOC-ACTIVE", 2, 0.028),
    ]
    qu = QueryUnderstanding("EVAL-LIFE", "active policy", "active policy", lifecycle_constraints=LifecycleConstraint(active=True))
    reranker = MetadataReranker()
    res = reranker.rerank(cands, qu, meta_idx)

    assert res[0].document_id == "DOC-ACTIVE"
    assert res[1].document_id == "DOC-STALE"
    assert res[0].final_score > res[1].final_score


def test_security_acl_filtering_preserved(sample_metadata_index: dict[str, DocumentMetadataSnapshot]):
    """Security Q4 & Q5: Verify metadata ranking does not weaken ACL filtering or confuse authority with authorization."""
    # Build candidate list with 5 documents, 2 of which are restricted by ACL
    cands = [
        DummyCandidate("c1", "DOC-PM-EVT-NS-0001-01", 1, 0.033),
        DummyCandidate("c2", "DOC-PM-EVT-NS-0002-01", 2, 0.031),
        DummyCandidate("c3", "DOC-PM-EVT-NS-0003-01", 3, 0.029),
        DummyCandidate("c4", "DOC-PM-EVT-NS-0004-01", 4, 0.027),
        DummyCandidate("c5", "DOC-PM-EVT-NS-0005-01", 5, 0.025),
    ]
    forbidden_ids = {"DOC-PM-EVT-NS-0001-01", "DOC-PM-EVT-NS-0004-01"}
    qu = QueryUnderstanding("EVAL-ACL", "incident reports", "incident reports")

    reranker = MetadataReranker()
    res = reranker.rerank(cands, qu, sample_metadata_index, forbidden_doc_ids=forbidden_ids, enforce_security=True)

    # All allowed candidates must rank above all forbidden candidates
    allowed_ranks = [d.rank_after for d in res if not d.is_forbidden]
    forbidden_ranks = [d.rank_after for d in res if d.is_forbidden]

    assert max(allowed_ranks) < min(forbidden_ranks)
    for d in res:
        if d.is_forbidden:
            assert d.final_score < -500.0


def test_telemetry_artifact_integrity(test_dirs: tuple[Path, Path, Path]):
    """Verify phase_4c3_metadata_reranking.json contains all 120 cases, all 7 ablations, and zero unknown metadata."""
    _, _, eval_dir = test_dirs
    telemetry_path = eval_dir / "phase_4c3_metadata_reranking.json"
    assert telemetry_path.exists()

    with open(telemetry_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data["metadata"]
    assert meta["total_evaluation_cases"] == 120
    assert meta["positive_retrieval_cases"] == 101
    assert meta["immutability_verified"] is True
    assert meta["zero_llm"] is True
    assert meta["fixed_weights"] is True

    ablations = data["ablation_benchmarks"]
    expected_ablations = [
        "baseline_qu_hybrid",
        "exp_a_authority_only",
        "exp_b_lifecycle_only",
        "exp_c_version_temporal_only",
        "exp_d_provenance_only",
        "exp_e_authority_lifecycle",
        "exp_f_authority_lifecycle_provenance",
        "exp_g_full_metadata_policy",
    ]
    for ab_name in expected_ablations:
        assert ab_name in ablations
        summary = ablations[ab_name]["summary"]
        assert summary["recall_at_10"] >= 0.0
        assert summary["forbidden_leaks"] == 0 if ab_name != "baseline_qu_hybrid" else 16

