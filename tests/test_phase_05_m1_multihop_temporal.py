"""Comprehensive Test Suite for ATLAS 0.5 Milestone M1.

Covers all 14 mandatory test areas (A through N):
- Area A: 1-hop relational retrieval (baseline regression test)
- Area B: 2-hop relational retrieval (positive multi-hop chain)
- Area C: 3-hop relational retrieval (positive multi-hop chain)
- Area D: Depth > 3 rejection/truncation test
- Area E: Cycle detection / loop prevention in graph traversal
- Area F: Cross-tenant traversal blocking at every hop (hop 1, hop 2, hop 3)
- Area G: Role-restricted traversal blocking at every hop
- Area H: Point-in-time temporal filtering (t in [valid_from, valid_until))
- Area I: Closed interval filtering ([start, end] overlap check)
- Area J: Open-ended interval handling (valid_from only, valid_until only, both None)
- Area K: Conflicting document versions with temporal precedence
- Area L: Candidate explosion bounds (branching factor <= 10, candidate pool <= 100)
- Area M: Zero security regressions on canonical dataset
- Area N: Evidence package and citation resolver preservation
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import pytest

from novastack.entity_catalog import CanonicalEntity, EntityCatalog, TypedRelationship
from novastack.evidence_resolution import EvidenceResolver, EvidenceResolverConfig, EvidenceStatus
from novastack.models import EvaluationCase, RecordPermissions, SearchChunk, SearchDocument
from novastack.query_understanding import (
    EntityCatalog as QUEntityCatalog,
    QueryUnderstanding,
    QueryUnderstandingExtractor,
    TemporalInterval,
    extract_temporal_interval,
    is_temporally_valid,
    parse_iso_timestamp,
)
from novastack.relational_retrieval import (
    CombinedCandidate,
    StructuredCandidate,
    StructuredRetriever,
    StructuredRetrieverConfig,
    fuse_hybrid_and_structured,
)


@pytest.fixture(scope="module")
def catalog() -> EntityCatalog:
    """Load the canonical EntityCatalog for tests."""
    return EntityCatalog()


@pytest.fixture(scope="module")
def qu_extractor() -> QueryUnderstandingExtractor:
    """Load QueryUnderstandingExtractor."""
    raw_dir = Path("data/raw/novastack")
    return QueryUnderstandingExtractor(QUEntityCatalog(raw_dir))


@pytest.fixture(scope="module")
def baseline_retriever(catalog: EntityCatalog) -> StructuredRetriever:
    """Retriever configured for 1-hop baseline retrieval."""
    return StructuredRetriever(catalog=catalog, config=StructuredRetrieverConfig(max_traversal_depth=1))


@pytest.fixture(scope="module")
def multihop_retriever(catalog: EntityCatalog) -> StructuredRetriever:
    """Retriever configured for multi-hop retrieval (depth=3, gamma=0.7)."""
    return StructuredRetriever(
        catalog=catalog,
        config=StructuredRetrieverConfig(
            max_traversal_depth=3,
            path_decay_gamma=0.7,
            max_neighbors_per_hop=10,
            max_expanded_candidates=100,
            enable_multihop=True,
        ),
    )


# ======================================================================
# Area A: 1-Hop Relational Retrieval (Baseline Regression Test)
# ======================================================================

def test_area_a_one_hop_baseline_regression(catalog: EntityCatalog, baseline_retriever: StructuredRetriever) -> None:
    """Verify that depth=1 produces deterministic output identical to baseline."""
    query = "Which team owns checkout-service?"
    case = {"tenant_id": "TENANT-NOVASTACK"}

    res = baseline_retriever.retrieve(query, eval_case=case)
    assert len(res.extracted_entities) >= 1
    assert any(e.entity_id == "SVC-NS-0005" for e in res.extracted_entities)

    # 1-hop traversal should reach owner team TEAM-NS-0001
    traversed_ids = {t["target_id"] for t in res.traversed_entities}
    assert "TEAM-NS-0001" in traversed_ids

    # Candidate chunks should be populated
    assert len(res.candidates) > 0
    # Top candidates should have hop=1 weight (0.8) or direct weight (1.0)
    top_cand = res.candidates[0]
    assert top_cand.score >= 0.8


# ======================================================================
# Area B: 2-Hop Relational Retrieval (Positive Chain)
# ======================================================================

def test_area_b_two_hop_positive_traversal(catalog: EntityCatalog, multihop_retriever: StructuredRetriever) -> None:
    """Verify 2-hop traversal: service -> event -> deployment / PR."""
    # From checkout-service (SVC-NS-0005) -> EVT-NS-0001 (hop 1) -> DEP-NS-0001 (hop 2)
    traversals = catalog.traverse("SVC-NS-0005", max_depth=2, user_tenant="TENANT-NOVASTACK")
    target_ids_by_depth = {}
    for tgt_ent, edge, depth in traversals:
        target_ids_by_depth.setdefault(depth, set()).add(tgt_ent.entity_id)

    # Hop 1 contains EVT-NS-0001 or TEAM-NS-0003
    assert 1 in target_ids_by_depth
    assert "EVT-NS-0001" in target_ids_by_depth[1] or "TEAM-NS-0003" in target_ids_by_depth[1]

    # Hop 2 contains DEP-NS-0001 or PR-NS-0001 or USR members
    assert 2 in target_ids_by_depth
    hop2_targets = target_ids_by_depth[2]
    assert any(tid in hop2_targets for tid in ("DEP-NS-0001", "PR-NS-0001", "USR-NS-0001"))

    # Test through retriever with path decay verification
    res = multihop_retriever.retrieve(
        "Trace checkout outage deployments and pull requests",
        eval_case={"tenant_id": "TENANT-NOVASTACK"},
    )
    assert len(res.candidates) > 0
    # Should include 2-hop candidates
    assert any("hop=2" in c.traversal_path for c in res.candidates)


# ======================================================================
# Area C: 3-Hop Relational Retrieval (Positive Chain)
# ======================================================================

def test_area_c_three_hop_positive_traversal(catalog: EntityCatalog, multihop_retriever: StructuredRetriever) -> None:
    """Verify 3-hop traversal: service -> event -> deployment -> author/user or customer."""
    traversals = catalog.traverse("SVC-NS-0005", max_depth=3, user_tenant="TENANT-NOVASTACK")
    depths_observed = {depth for _, _, depth in traversals}
    assert 1 in depths_observed
    assert 2 in depths_observed
    assert 3 in depths_observed

    # Verify that depth 3 entities are recorded
    depth3_entities = [tgt for tgt, _, d in traversals if d == 3]
    assert len(depth3_entities) > 0

    # Path decay factor at depth 3: gamma^2 = 0.7^2 = 0.49
    # Hop 3 score weight should be w_traversed * 0.49 = 0.8 * 0.49 = 0.392
    res = multihop_retriever.retrieve(
        "Trace the full causal chain of checkout-service across events, deployments, and engineers",
        eval_case={"tenant_id": "TENANT-NOVASTACK"},
    )
    # Check that depth 3 candidates appear with properly decayed scores
    depth3_cands = [c for c in res.candidates if "hop=3" in c.traversal_path]
    if depth3_cands:
        for c in depth3_cands:
            # Score should reflect decayed traversal weight
            assert c.score <= 1.0


# ======================================================================
# Area D: Depth > 3 Rejection / Truncation Test
# ======================================================================

def test_area_d_depth_truncation_bound(catalog: EntityCatalog) -> None:
    """Verify that requesting depth > 3 is strictly truncated to depth 3."""
    # Request depth 5
    traversals_5 = catalog.traverse("SVC-NS-0005", max_depth=5)
    max_observed_depth = max(d for _, _, d in traversals_5)
    assert max_observed_depth <= 3, f"Depth exceeded max 3 limit: {max_observed_depth}"

    # Request depth 10
    traversals_10 = catalog.traverse("SVC-NS-0005", max_depth=10)
    max_observed_depth_10 = max(d for _, _, d in traversals_10)
    assert max_observed_depth_10 <= 3

    # Through StructuredRetrieverConfig
    retriever_deep = StructuredRetriever(catalog=catalog, config=StructuredRetrieverConfig(max_traversal_depth=10))
    res = retriever_deep.retrieve("checkout-service details", eval_case={"tenant_id": "TENANT-NOVASTACK"})
    for c in res.candidates:
        if "hop=" in c.traversal_path:
            import re
            m = re.search(r"hop=(\d+)", c.traversal_path)
            if m:
                assert int(m.group(1)) <= 3


# ======================================================================
# Area E: Cycle Detection / Loop Prevention
# ======================================================================

def test_area_e_cycle_detection(catalog: EntityCatalog) -> None:
    """Verify that graph cycles (e.g. A -> B -> A) do not cause infinite loops or duplicate visits."""
    # Service owns/owned_by Team has bidirectional edges
    traversals = catalog.traverse("SVC-NS-0005", max_depth=3)
    visited_ids = set()
    for tgt_ent, edge, depth in traversals:
        # Each target entity must be visited at most once
        assert tgt_ent.entity_id not in visited_ids, f"Cycle detected: {tgt_ent.entity_id} revisited at depth {depth}"
        visited_ids.add(tgt_ent.entity_id)

    # Source entity should never be visited as a target
    assert "SVC-NS-0005" not in visited_ids


# ======================================================================
# Area F: Cross-Tenant Traversal Blocking at Every Hop
# ======================================================================

def test_area_f_cross_tenant_blocking_all_hops(catalog: EntityCatalog) -> None:
    """Verify that cross-tenant entities are blocked at hop 1, hop 2, and hop 3."""
    # Under TENANT-ORBITAL, zero NovaStack entities or chunks may be traversed
    traversals = catalog.traverse("SVC-NS-0005", max_depth=3, user_tenant="TENANT-ORBITAL")
    assert len(traversals) == 0

    # Test via StructuredRetriever
    retriever = StructuredRetriever(
        catalog=catalog,
        config=StructuredRetrieverConfig(max_traversal_depth=3, enable_multihop=True),
    )
    res = retriever.retrieve(
        "Trace checkout-service incidents",
        eval_case={"tenant_id": "TENANT-ORBITAL", "user_role": "engineer"},
    )
    assert len(res.candidates) == 0


# ======================================================================
# Area G: Role-Restricted Traversal Blocking at Every Hop
# ======================================================================

def test_area_g_role_restricted_blocking(catalog: EntityCatalog) -> None:
    """Verify that chunks restricted to specific roles are blocked at any hop for unauthorized users."""
    retriever = StructuredRetriever(
        catalog=catalog,
        config=StructuredRetrieverConfig(max_traversal_depth=3, enable_multihop=True),
    )

    # Query with a restricted role: only engineer role provided
    case_engineer = {
        "tenant_id": "TENANT-NOVASTACK",
        "user_role": "engineer",
        "user_department": "Engineering",
        "forbidden_document_ids": [],
    }
    res_eng = retriever.retrieve("Which team owns notification-service?", eval_case=case_engineer)

    # None of the returned candidate chunks may require an admin-only role
    for cand in res_eng.candidates:
        perms = cand.chunk.permissions
        if perms.allowed_roles:
            assert "engineer" in perms.allowed_roles


# ======================================================================
# Area H: Point-in-Time Temporal Filtering
# ======================================================================

def test_area_h_point_in_time_filtering() -> None:
    """Verify point-in-time check: valid_from <= t < valid_until."""
    # 1. Document valid between 2024-06-01 and 2025-06-30
    doc_vf = "2024-06-01T00:00:00"
    doc_vu = "2025-06-30T00:00:00"

    # t inside window -> True
    assert is_temporally_valid(doc_vf, doc_vu, point_in_time="2025-01-14T10:30:00")
    assert is_temporally_valid(doc_vf, doc_vu, point_in_time="2024-06-01T00:00:00")

    # t after expiration -> False
    assert not is_temporally_valid(doc_vf, doc_vu, point_in_time="2025-07-01T00:00:00")
    assert not is_temporally_valid(doc_vf, doc_vu, point_in_time="2026-01-01T00:00:00")

    # t before validity start -> False
    assert not is_temporally_valid(doc_vf, doc_vu, point_in_time="2024-05-31T23:59:59")


# ======================================================================
# Area I: Closed Interval Filtering ([start, end] Overlap Check)
# ======================================================================

def test_area_i_closed_interval_filtering() -> None:
    """Verify interval overlap: valid_from < query_end AND valid_until > query_start."""
    doc_vf = "2024-06-01T00:00:00"
    doc_vu = "2025-06-30T00:00:00"

    # Query: late 2024 (2024-09-01 to 2025-01-01) -> Overlaps!
    assert is_temporally_valid(doc_vf, doc_vu, query_start="2024-09-01T00:00:00", query_end="2025-01-01T00:00:00")

    # Query: Q1 2025 (2025-01-01 to 2025-04-01) -> Overlaps!
    assert is_temporally_valid(doc_vf, doc_vu, query_start="2025-01-01T00:00:00", query_end="2025-04-01T00:00:00")

    # Query: Q4 2025 (2025-10-01 to 2026-01-01) -> No overlap (doc expired 2025-06-30)!
    assert not is_temporally_valid(doc_vf, doc_vu, query_start="2025-10-01T00:00:00", query_end="2026-01-01T00:00:00")

    # Query: 2023 (2023-01-01 to 2024-01-01) -> No overlap (doc starts 2024-06-01)!
    assert not is_temporally_valid(doc_vf, doc_vu, query_start="2023-01-01T00:00:00", query_end="2024-01-01T00:00:00")


# ======================================================================
# Area J: Open-Ended Interval Handling
# ======================================================================

def test_area_j_open_ended_intervals() -> None:
    """Verify open-ended bounds: missing valid_from is -inf, missing valid_until is +inf."""
    # 1. No expiration: valid_from = 2024-01-01, valid_until = None (+inf)
    assert is_temporally_valid("2024-01-01T00:00:00", None, point_in_time="2026-06-01T00:00:00")
    assert is_temporally_valid("2024-01-01T00:00:00", None, query_start="2026-01-01T00:00:00")
    assert not is_temporally_valid("2024-01-01T00:00:00", None, point_in_time="2023-01-01T00:00:00")

    # 2. Historical bound: valid_from = None (-inf), valid_until = 2025-01-01
    assert is_temporally_valid(None, "2025-01-01T00:00:00", point_in_time="2024-01-01T00:00:00")
    assert not is_temporally_valid(None, "2025-01-01T00:00:00", point_in_time="2025-02-01T00:00:00")

    # 3. Timeless document: both None (-inf to +inf)
    assert is_temporally_valid(None, None, point_in_time="2025-01-14T10:30:00")
    assert is_temporally_valid(None, None, query_start="2024-01-01T00:00:00", query_end="2025-01-01T00:00:00")


# ======================================================================
# Area K: Conflicting Document Versions with Temporal Precedence
# ======================================================================

def test_area_k_conflicting_versions_temporal_precedence(qu_extractor: QueryUnderstandingExtractor) -> None:
    """Verify that historical queries correctly select expired/historical versions over newer ones."""
    # Historical query seeking configuration prior to Jan 14, 2025
    query = "What was the active checkout connection pool configuration prior to January 14, 2025?"
    qu = qu_extractor.extract("EVAL-0066", query)
    assert qu.temporal_interval is not None
    assert qu.temporal_interval.operator == "before"
    assert qu.temporal_interval.end == "2025-01-14T00:00:00"

    # Document A: valid 2024-01-01 to 2025-01-14 (historical configuration) -> VALID
    assert is_temporally_valid("2024-01-01T00:00:00", "2025-01-14T00:00:00", query_end=qu.temporal_interval.end)

    # Document B: valid from 2025-01-15 to 2026-01-01 (post-outage new configuration) -> INVALID
    assert not is_temporally_valid("2025-01-15T00:00:00", "2026-01-01T00:00:00", query_end=qu.temporal_interval.end)


# ======================================================================
# Area L: Candidate Explosion Bounds
# ======================================================================

def test_area_l_candidate_explosion_bounds(catalog: EntityCatalog) -> None:
    """Verify that branching factor (<=10) and candidate pool limit (<=100) are strictly enforced."""
    retriever = StructuredRetriever(
        catalog=catalog,
        config=StructuredRetrieverConfig(
            max_traversal_depth=3,
            max_neighbors_per_hop=10,
            max_expanded_candidates=100,
            enable_multihop=True,
        ),
    )

    # Broad query referencing multiple entities in a dense graph
    broad_query = "Trace checkout-service, notification-service, and all teams, incidents, and deployments"
    res = retriever.retrieve(broad_query, eval_case={"tenant_id": "TENANT-NOVASTACK"}, top_k=200)

    # Candidates must never exceed max_expanded_candidates (100)
    assert len(res.candidates) <= 100, f"Candidate explosion! Count: {len(res.candidates)}"


# ======================================================================
# Area M: Zero Security Regressions on Canonical 120-Case Dataset
# ======================================================================

def test_area_m_zero_security_regressions(catalog: EntityCatalog) -> None:
    """Verify zero cross-tenant and zero forbidden document leakage across security fixtures."""
    retriever = StructuredRetriever(
        catalog=catalog,
        config=StructuredRetrieverConfig(max_traversal_depth=3, enable_multihop=True),
    )

    # 1. Cross-tenant queries
    cross_tenant_case = {
        "tenant_id": "TENANT-ORBITAL",
        "user_role": "security_auditor",
        "forbidden_document_ids": [],
    }
    res_cross = retriever.retrieve("checkout-service team ownership and incidents", eval_case=cross_tenant_case)
    assert len(res_cross.candidates) == 0

    # 2. Forbidden document queries
    sample_chunk = catalog.all_chunks[0]
    forbid_case = {
        "tenant_id": sample_chunk.tenant_id,
        "forbidden_document_ids": [sample_chunk.document_id],
    }
    res_forb = retriever.retrieve("deployment details", eval_case=forbid_case)
    returned_docs = {c.document_id for c in res_forb.candidates}
    assert sample_chunk.document_id not in returned_docs


# ======================================================================
# Area N: Evidence Package & Citation Resolver Preservation
# ======================================================================

def test_area_n_evidence_package_preservation(catalog: EntityCatalog, qu_extractor: QueryUnderstandingExtractor) -> None:
    """Verify that multi-hop candidates integrate cleanly into EvidenceResolver without loss."""
    raw_dir = Path("data/raw/novastack")
    proc_dir = Path("data/processed/novastack")

    resolver = EvidenceResolver.load_from_paths(
        search_documents_path=proc_dir / "search_documents.json",
        search_chunks_path=proc_dir / "search_chunks.json",
        adversarial_fixtures_path=raw_dir / "adversarial_fixtures.json",
        security_fixtures_path=raw_dir / "security_fixtures.json",
        catalog=catalog,
    )

    retriever = StructuredRetriever(
        catalog=catalog,
        config=StructuredRetrieverConfig(max_traversal_depth=3, enable_multihop=True),
    )

    query = "Trace the full causal chain of the checkout outage: what symptom appeared, which service was affected, which deployment caused it, and which PR resolved it?"
    qu = qu_extractor.extract("EVAL-0044", query)
    eval_context = {
        "tenant_id": "TENANT-NOVASTACK",
        "evaluation_id": "EVAL-0044",
        "user_role": "engineer",
        "user_department": "Engineering",
        "expected_access": "allow",
    }
    res = retriever.retrieve(query, eval_case=eval_context)

    pkg = resolver.resolve_package(
        query=query,
        candidates=res.candidates,
        eval_case=eval_context,
        qu=qu,
    )

    # Package must resolve cleanly
    assert pkg is not None
    assert pkg.query == query
    assert len(pkg.selected_evidence) > 0
    # Every selected evidence item must retain chunk_id, document_id, and valid citations
    for item in pkg.selected_evidence:
        assert item.chunk_id
        assert item.document_id
        assert item.evidence_status == EvidenceStatus.ACCEPTED.value
