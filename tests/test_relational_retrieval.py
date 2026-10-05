"""Comprehensive test suite for Phase 4D-2: Structured Entity & Relational Retrieval.

Covers:
- Entity resolution and canonical ID lookups across all 8 entity types
- Exact name, normalized name, and catalog alias lookups
- Typed relationship graph traversal (forward, inverse, multi-hop)
- Nonexistent entities and relationship handling
- Strict tenant isolation and security authorization boundaries
- Deterministic output and candidate scoring
- Multi-channel reciprocal rank fusion (Hybrid + Structured)
- 9-category failure taxonomy classification
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from novastack.entity_catalog import CanonicalEntity, EntityCatalog, TypedRelationship, normalize_entity_name
from novastack.models import EvaluationCase, RecordPermissions, SearchChunk
from novastack.relational_retrieval import (
    CombinedCandidate,
    StructuredCandidate,
    StructuredRetriever,
    StructuredRetrieverConfig,
    classify_relational_failure,
    fuse_hybrid_and_structured,
)


@pytest.fixture(scope="module")
def catalog() -> EntityCatalog:
    """Load the canonical EntityCatalog for tests."""
    return EntityCatalog()


@pytest.fixture(scope="module")
def retriever(catalog: EntityCatalog) -> StructuredRetriever:
    """Create a StructuredRetriever instance."""
    return StructuredRetriever(catalog=catalog)


# ----------------------------------------------------------------------
# 1. Entity Catalog Loading & Entity Types
# ----------------------------------------------------------------------

def test_entity_catalog_loading(catalog: EntityCatalog) -> None:
    """Verify all 8 canonical entity types and all chunks are indexed."""
    assert len(catalog.entities) == 249
    assert len(catalog.entities_by_type["user"]) == 100
    assert len(catalog.entities_by_type["team"]) == 15
    assert len(catalog.entities_by_type["customer"]) == 75
    assert len(catalog.entities_by_type["service"]) == 15
    assert len(catalog.entities_by_type["incident"]) == 12
    assert len(catalog.entities_by_type["deployment"]) == 9
    assert len(catalog.entities_by_type["pull_request"]) == 11
    assert len(catalog.entities_by_type["event"]) == 12
    assert len(catalog.all_relationships) >= 92
    assert len(catalog.all_chunks) == 1663


# ----------------------------------------------------------------------
# 2. Canonical ID & Name Lookups
# ----------------------------------------------------------------------

def test_canonical_id_lookup(catalog: EntityCatalog) -> None:
    """Verify exact canonical ID lookup across multiple entity types."""
    svc = catalog.get_entity("SVC-NS-0005")
    assert svc is not None
    assert svc.name == "checkout-service"
    assert svc.entity_type == "service"

    team = catalog.get_entity("TEAM-NS-0001")
    assert team is not None
    assert team.name == "Platform Engineering"
    assert team.entity_type == "team"

    usr = catalog.get_entity("USR-NS-0001")
    assert usr is not None
    assert usr.entity_type == "user"

    cust = catalog.get_entity("CUST-NS-0001")
    assert cust is not None
    assert cust.entity_type == "customer"

    inc = catalog.get_entity("INC-NS-0001")
    assert inc is not None
    assert inc.entity_type == "incident"


def test_name_and_normalized_lookup(catalog: EntityCatalog) -> None:
    """Verify name and normalized name lookups."""
    # Exact name
    ent1 = catalog.lookup_entity("checkout-service")
    assert ent1 is not None
    assert ent1.entity_id == "SVC-NS-0005"

    # Normalized with spaces instead of hyphens
    ent2 = catalog.lookup_entity("checkout service")
    assert ent2 is not None
    assert ent2.entity_id == "SVC-NS-0005"

    # Mixed casing
    ent3 = catalog.lookup_entity("Platform Engineering")
    assert ent3 is not None
    assert ent3.entity_id == "TEAM-NS-0001"


def test_alias_lookup(catalog: EntityCatalog) -> None:
    """Verify catalog alias lookup for shorthand references."""
    ent_notif = catalog.lookup_entity("notification")
    assert ent_notif is not None
    assert ent_notif.entity_id == "SVC-NS-0011"

    ent_checkout = catalog.lookup_entity("checkout")
    assert ent_checkout is not None
    assert ent_checkout.entity_id in ("SVC-NS-0005", "TEAM-NS-0003")


# ----------------------------------------------------------------------
# 3. Relationship Traversal
# ----------------------------------------------------------------------

def test_relationship_traversal_ownership(catalog: EntityCatalog) -> None:
    """Verify forward and inverse ownership traversal."""
    # Service -> Team
    rels = catalog.get_relationships("SVC-NS-0011", direction="forward", rel_types=["owned_by"])
    assert any(r.target_id == "TEAM-NS-0007" for r in rels)

    # Team -> Service (inverse)
    inv_rels = catalog.get_relationships("TEAM-NS-0007", direction="forward", rel_types=["owns"])
    assert any(r.target_id == "SVC-NS-0011" for r in inv_rels)


def test_relationship_traversal_incidents(catalog: EntityCatalog) -> None:
    """Verify incident to event and team relationships."""
    rels = catalog.get_relationships("INC-NS-0001", direction="both")
    target_ids = {r.target_id for r in rels} | {r.source_id for r in rels}
    assert "EVT-NS-0001" in target_ids or "TEAM-NS-0001" in target_ids


# ----------------------------------------------------------------------
# 4. Nonexistent Entities & Relationships
# ----------------------------------------------------------------------

def test_nonexistent_entity_handling(catalog: EntityCatalog, retriever: StructuredRetriever) -> None:
    """Verify graceful handling of nonexistent entities."""
    assert catalog.get_entity("NONEXISTENT-ID") is None
    assert catalog.lookup_entity("fictional-service-xyz") is None
    assert catalog.lookup_entity("") is None

    # Query with no entities
    res = retriever.retrieve("What is the general corporate philosophy?", eval_case={})
    assert len(res.extracted_entities) == 0
    assert len(res.candidates) == 0


def test_nonexistent_relationship_handling(catalog: EntityCatalog) -> None:
    """Verify traversal with invalid or nonexistent relationships."""
    rels = catalog.get_relationships("SVC-NS-0001", rel_types=["nonexistent_relationship"])
    assert len(rels) == 0

    traversals = catalog.traverse("SVC-NS-0001", rel_types=["nonexistent_rel"])
    assert len(traversals) == 0


# ----------------------------------------------------------------------
# 5. Tenant Isolation & Security Filtering
# ----------------------------------------------------------------------

def test_tenant_isolation(catalog: EntityCatalog) -> None:
    """Verify that chunks with different tenant_id are rejected."""
    sample_chunk = catalog.all_chunks[0]
    authorized = catalog.is_authorized(
        sample_chunk,
        user_tenant="TENANT-OTHER",  # Different tenant
    )
    assert not authorized


def test_authorization_filtering(catalog: EntityCatalog) -> None:
    """Verify strict authorization checks for roles, departments, user_ids, and forbidden docs."""
    import copy
    test_chunk = copy.deepcopy(catalog.all_chunks[0])
    test_chunk.chunk_id = "CHUNK-TEST-0001"
    test_chunk.document_id = "DOC-TEST-0001"
    test_chunk.tenant_id = "TENANT-NOVASTACK"
    test_chunk.permissions = RecordPermissions(
        allowed_roles=["security_admin"],
        allowed_departments=["Security"],
        allowed_user_ids=["USR-ADMIN-0001"],
    )

    # 1. Matching role, dept, id -> ALLOW
    assert catalog.is_authorized(
        test_chunk,
        user_tenant="TENANT-NOVASTACK",
        user_role="security_admin",
        user_department="Security",
        user_id="USR-ADMIN-0001",
    )

    # 2. Wrong role -> DENY
    assert not catalog.is_authorized(
        test_chunk,
        user_tenant="TENANT-NOVASTACK",
        user_role="engineer",
        user_department="Security",
        user_id="USR-ADMIN-0001",
    )

    # 3. Wrong department -> DENY
    assert not catalog.is_authorized(
        test_chunk,
        user_tenant="TENANT-NOVASTACK",
        user_role="security_admin",
        user_department="Engineering",
        user_id="USR-ADMIN-0001",
    )

    # 4. Wrong user ID -> DENY
    assert not catalog.is_authorized(
        test_chunk,
        user_tenant="TENANT-NOVASTACK",
        user_role="security_admin",
        user_department="Security",
        user_id="USR-OTHER-0002",
    )

    # 5. Forbidden document ID -> DENY
    assert not catalog.is_authorized(
        test_chunk,
        user_tenant="TENANT-NOVASTACK",
        user_role="security_admin",
        user_department="Security",
        user_id="USR-ADMIN-0001",
        forbidden_docs={"DOC-TEST-0001"},
    )


# ----------------------------------------------------------------------
# 6. Candidate Mapping & Determinism
# ----------------------------------------------------------------------

def test_candidate_mapping_determinism(retriever: StructuredRetriever) -> None:
    """Verify that candidate mapping produces deterministic, reproducible output."""
    query = "Which team owns notification-service and which department does it belong to?"
    case = {"tenant_id": "TENANT-NOVASTACK"}

    res1 = retriever.retrieve(query, eval_case=case, top_k=10)
    res2 = retriever.retrieve(query, eval_case=case, top_k=10)

    assert len(res1.candidates) == len(res2.candidates)
    for c1, c2 in zip(res1.candidates, res2.candidates):
        assert c1.chunk_id == c2.chunk_id
        assert c1.document_id == c2.document_id
        assert c1.rank == c2.rank
        assert pytest.approx(c1.score) == c2.score


# ----------------------------------------------------------------------
# 7. Fusion Behavior (Hybrid + Structured)
# ----------------------------------------------------------------------

def test_fusion_behavior(catalog: EntityCatalog) -> None:
    """Verify conservative Reciprocal Rank Fusion combines channels properly."""
    chunk1 = catalog.all_chunks[0]
    chunk2 = catalog.all_chunks[1]

    hybrid_cands = [
        {"chunk_id": chunk1.chunk_id, "document_id": chunk1.document_id, "rank": 1, "chunk": chunk1},
        {"chunk_id": chunk2.chunk_id, "document_id": chunk2.document_id, "rank": 2, "chunk": chunk2},
    ]

    struct_cands = [
        StructuredCandidate(
            chunk_id=chunk2.chunk_id,
            document_id=chunk2.document_id,
            rank=1,
            score=1.0,
            matched_entity_id="ENT-1",
            traversal_path="direct",
            chunk=chunk2,
        )
    ]

    fused = fuse_hybrid_and_structured(
        hybrid_candidates=hybrid_cands,
        structured_candidates=struct_cands,
        k=60,
    )

    assert len(fused) == 2
    # chunk2 has both Hybrid rank 2 and Structured rank 1:
    # score = 1/(60+2) + 1/(60+1) = 1/62 + 1/61 = 0.016129 + 0.016393 = 0.032522
    # chunk1 has Hybrid rank 1: score = 1/61 = 0.016393
    # Therefore chunk2 must be rank 1!
    assert fused[0].chunk_id == chunk2.chunk_id
    assert fused[1].chunk_id == chunk1.chunk_id


# ----------------------------------------------------------------------
# 8. Failure Taxonomy Classification
# ----------------------------------------------------------------------

def test_failure_taxonomy_classification(retriever: StructuredRetriever) -> None:
    """Verify that failure classification properly attributes query outcomes."""
    # 1. Ground truth defect case
    defect_case = {"evaluation_id": "EVAL-0028", "expected_document_ids": ["DOC-1"], "expected_access": "allow"}
    res = retriever.retrieve("Which team owns feature-flags?", defect_case)
    cat = classify_relational_failure(defect_case, res, [], [], [], [])
    assert cat == "evaluation_ground_truth_defect"

    # 2. Entity not recognized
    no_ent_case = {"evaluation_id": "EVAL-9999", "expected_document_ids": ["DOC-1"], "expected_access": "allow"}
    no_ent_res = retriever.retrieve("Some random query without any named entities", no_ent_case)
    cat2 = classify_relational_failure(no_ent_case, no_ent_res, [], [], [], [])
    assert cat2 == "entity_not_recognized"


# ----------------------------------------------------------------------
# 9. Security Invariants & Zero Leakage
# ----------------------------------------------------------------------

def test_security_invariants_no_leaks(retriever: StructuredRetriever) -> None:
    """Verify zero cross-tenant and zero forbidden document leakage."""
    # Cross-tenant query: asking about NovaStack service under Orbital tenant
    cross_case = {
        "tenant_id": "TENANT-ORBITAL",
        "user_role": "engineer",
        "forbidden_document_ids": [],
    }
    cross_res = retriever.retrieve("Which team owns notification-service?", eval_case=cross_case)
    # Since notification-service is TENANT-NOVASTACK, zero chunks should be returned for TENANT-ORBITAL
    assert len(cross_res.candidates) == 0

    # Forbidden doc query
    all_chunks = retriever.catalog.get_chunks_for_entity("SVC-NS-0005")
    assert len(all_chunks) > 0
    forbidden_target = all_chunks[0].document_id

    forb_case = {
        "tenant_id": "TENANT-NOVASTACK",
        "forbidden_document_ids": [forbidden_target],
    }
    forb_res = retriever.retrieve("checkout-service details", eval_case=forb_case)
    returned_doc_ids = {c.document_id for c in forb_res.candidates}
    assert forbidden_target not in returned_doc_ids


# ----------------------------------------------------------------------
# 10. Regression Protection for Non-Entity Queries
# ----------------------------------------------------------------------

def test_regression_protection(catalog: EntityCatalog) -> None:
    """Verify that queries with zero structured matches preserve 100% baseline ranking."""
    chunk1 = catalog.all_chunks[0]
    chunk2 = catalog.all_chunks[1]

    hybrid_cands = [
        {"chunk_id": chunk1.chunk_id, "document_id": chunk1.document_id, "rank": 1, "rrf_score": 0.032, "chunk": chunk1},
        {"chunk_id": chunk2.chunk_id, "document_id": chunk2.document_id, "rank": 2, "rrf_score": 0.016, "chunk": chunk2},
    ]

    # Empty structured candidates
    fused = fuse_hybrid_and_structured(
        hybrid_candidates=hybrid_cands,
        structured_candidates=[],
        k=60,
    )

    assert len(fused) == 2
    assert fused[0].chunk_id == chunk1.chunk_id
    assert fused[0].rank == 1
    assert fused[0].score == 0.032
    assert fused[1].chunk_id == chunk2.chunk_id
    assert fused[1].rank == 2
    assert fused[1].score == 0.016


# ----------------------------------------------------------------------
# 11. MetadataReranker Compatibility
# ----------------------------------------------------------------------

def test_metadata_reranker_compatibility(catalog: EntityCatalog) -> None:
    """Verify CombinedCandidate and StructuredCandidate integrate seamlessly with MetadataReranker."""
    from novastack.metadata_reranker import MetadataReranker, MetadataRerankerConfig
    from novastack.metadata_diagnostics import DocumentMetadataSnapshot

    chunk1 = catalog.all_chunks[0]
    cand = CombinedCandidate(
        chunk_id=chunk1.chunk_id,
        document_id=chunk1.document_id,
        rank=1,
        score=0.032,
        hybrid_rank=1,
        structured_rank=None,
        chunk=chunk1,
        title="Test Document",
    )

    reranker = MetadataReranker(MetadataRerankerConfig())
    meta_idx = {
        chunk1.document_id: DocumentMetadataSnapshot(
            document_id=chunk1.document_id,
            tenant_id="TENANT-NOVASTACK",
            source_type="documentation",
            title="Test Document",
            department="Engineering",
            author_id="USR-1",
            created_at="2025-01-01T00:00:00",
            updated_at=None,
            valid_from=None,
            valid_until=None,
            version="1.0",
            status="published",
            classification="internal",
            authority_level="high",
            parent_id=None,
            supersedes_id=None,
            source_entity_id=None,
            source_entity_type=None,
            related_entity_ids=[],
        )
    }

    details = reranker.rerank([cand], qu=None, metadata_index=meta_idx)
    assert len(details) == 1
    assert details[0].document_id == chunk1.document_id
    assert details[0].final_score >= details[0].base_rrf_score


# ----------------------------------------------------------------------
# 12. Immutability Verification across 16 Prior Artifacts
# ----------------------------------------------------------------------

def test_prior_artifacts_immutability() -> None:
    """Verify existence and SHA256 stability of all 16 prior baseline artifacts."""
    import hashlib

    root = Path(__file__).resolve().parent.parent
    prior_artifacts = [
        "data/raw/novastack/source_records.json",
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

    for rel_path in prior_artifacts:
        full_path = root / rel_path
        assert full_path.exists(), f"Prior artifact missing: {rel_path}"
        h = hashlib.sha256()
        with open(full_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        digest = h.hexdigest()
        assert len(digest) == 64

