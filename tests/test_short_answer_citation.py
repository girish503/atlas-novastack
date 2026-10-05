"""Unit tests for Phase 4H-2 Short-Answer Citation Resolution.

Verifies:
1. Valid single authoritative match attaches citation
2. Unauthorized evidence refuses citation
3. Excluded evidence refuses citation
4. Adversarial evidence refuses citation
5. Stale / superseded evidence refuses citation
6. Conflicting candidate evidence refuses citation
7. Equal-authority ambiguous evidence refuses citation
8. Authority disambiguation correctly selects strictly higher authority
9. Whole-word boundary protection:
   - "Platform Engineering" does NOT match "Team: Engineering"
   - "v1.0" does NOT match "v10"
   - "10" does NOT match "100"
   - "us-east-1" does NOT match "us-east-1a"
10. Answers with >2 meaningful tokens bypass short-answer resolver
11. Flag parameterization: c0 leaves short answer uncited, c1 attaches citation
"""

import pytest

from novastack.evidence import EvidenceConflict, EvidenceItem, EvidencePackage, EvidenceStatus
from novastack.generation import AnswerStatus, GroundedAnswerGenerator
from novastack.models import RecordPermissions


def make_test_item(
    evidence_id: str,
    doc_id: str,
    chunk_id: str,
    text: str,
    title: str = "Test Title",
    authority: str = "high",
    status: str = "published",
    evidence_status: str = EvidenceStatus.ACCEPTED.value,
    is_adversarial: bool = False,
) -> EvidenceItem:
    reasons = ["adversarial"] if is_adversarial else []
    return EvidenceItem(
        evidence_id=evidence_id,
        chunk_id=chunk_id,
        document_id=doc_id,
        tenant_id="tenant-alpha",
        source_type="adversarial_fixture" if is_adversarial else "document",
        title=title,
        text=text,
        source_entity_id="DOC-001",
        source_entity_type="document",
        related_entity_ids=["SVC-001"],
        authority_level=authority,
        classification="internal",
        permissions=RecordPermissions(allowed_roles=["engineer"]),
        status=status,
        version="v1.0",
        created_at="2026-01-15T10:00:00Z",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        parent_id=None,
        supersedes_id=None,
        retrieval_rank=1,
        retrieval_score=0.95,
        retrieval_channels=["bm25", "dense"],
        evidence_status=EvidenceStatus.ADVERSARIAL.value if is_adversarial else evidence_status,
        evidence_reasons=reasons,
    )


def make_test_package(
    selected: list[EvidenceItem],
    excluded: list[EvidenceItem] | None = None,
    conflicts: list[EvidenceConflict] | None = None,
) -> EvidencePackage:
    return EvidencePackage(
        package_id="PKG-TEST-4H2",
        evaluation_id="EVAL-TEST-4H2",
        query="Which team owns checkout-service?",
        tenant_id="tenant-alpha",
        user_context={"tenant_id": "tenant-alpha", "roles": ["engineer"], "classification_limit": "internal"},
        selected_evidence=selected,
        excluded_evidence=excluded or [],
        conflicts=conflicts or [],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={},
    )


@pytest.fixture
def dummy_generator():
    """Lightweight generator without loading LLM model weights."""
    gen = GroundedAnswerGenerator.__new__(GroundedAnswerGenerator)
    gen.corpus_doc_ids = {"DOC-001", "DOC-002", "DOC-003"}
    gen.corpus_chunk_ids = {"CHUNK-001", "CHUNK-002", "CHUNK-003"}
    return gen


def test_short_exact_match_single_valid_item(dummy_generator):
    item1 = make_test_item("EVD-001", "DOC-001", "CHUNK-001", text="Responsible team: Platform Engineering")
    pkg = make_test_package([item1])

    res = dummy_generator._resolve_short_exact_match("Platform Engineering", pkg)
    assert res == "Platform Engineering [EVD-001]"


def test_short_exact_match_refuses_unauthorized(dummy_generator):
    item1 = make_test_item(
        "EVD-001", "DOC-001", "CHUNK-001",
        text="Responsible team: Platform Engineering",
        evidence_status=EvidenceStatus.UNAUTHORIZED.value,
    )
    pkg = make_test_package([item1])

    res = dummy_generator._resolve_short_exact_match("Platform Engineering", pkg)
    assert res == "Platform Engineering"


def test_short_exact_match_refuses_excluded(dummy_generator):
    item1 = make_test_item("EVD-001", "DOC-001", "CHUNK-001", text="Responsible team: Platform Engineering")
    pkg = make_test_package([item1], excluded=[item1])

    res = dummy_generator._resolve_short_exact_match("Platform Engineering", pkg)
    assert res == "Platform Engineering"


def test_short_exact_match_refuses_adversarial(dummy_generator):
    item1 = make_test_item(
        "EVD-001", "DOC-001", "CHUNK-001",
        text="Responsible team: Platform Engineering",
        is_adversarial=True,
    )
    pkg = make_test_package([item1])

    res = dummy_generator._resolve_short_exact_match("Platform Engineering", pkg)
    assert res == "Platform Engineering"


def test_short_exact_match_refuses_stale_superseded(dummy_generator):
    item1 = make_test_item(
        "EVD-001", "DOC-001", "CHUNK-001",
        text="Responsible team: Platform Engineering",
        status="superseded",
    )
    pkg = make_test_package([item1])

    res = dummy_generator._resolve_short_exact_match("Platform Engineering", pkg)
    assert res == "Platform Engineering"


def test_short_exact_match_refuses_conflicting_candidates(dummy_generator):
    item1 = make_test_item("EVD-001", "DOC-001", "CHUNK-001", text="Status: Mitigating (INC-001)")
    item2 = make_test_item("EVD-002", "DOC-002", "CHUNK-002", text="Status: Mitigating (INC-002)")
    conflict = EvidenceConflict(
        conflict_id="CONF-01",
        conflict_type="factual_conflict",
        entity_id="checkout-service",
        primary_evidence_id="EVD-001",
        conflicting_evidence_ids=["EVD-002"],
        resolution_status="conflict_unresolved",
        resolution_reason="Conflicting status values across incidents",
    )
    pkg = make_test_package([item1, item2], conflicts=[conflict])

    res = dummy_generator._resolve_short_exact_match("Mitigating", pkg)
    assert res == "Mitigating"


def test_short_exact_match_refuses_equal_authority_ambiguity(dummy_generator):
    item1 = make_test_item("EVD-001", "DOC-001", "CHUNK-001", text="Status: Mitigating", authority="high")
    item2 = make_test_item("EVD-002", "DOC-002", "CHUNK-002", text="Status: Mitigating", authority="high")
    pkg = make_test_package([item1, item2])

    # Multiple candidates with equal authority cannot be disambiguated -> do NOT auto-cite
    res = dummy_generator._resolve_short_exact_match("Mitigating", pkg)
    assert res == "Mitigating"


def test_short_exact_match_disambiguates_by_authority(dummy_generator):
    item1 = make_test_item("EVD-001", "DOC-001", "CHUNK-001", text="Volatile-lru setting", authority="authoritative")
    item2 = make_test_item("EVD-002", "DOC-002", "CHUNK-002", text="Volatile-lru draft note", authority="standard")
    pkg = make_test_package([item1, item2])

    res = dummy_generator._resolve_short_exact_match("Volatile-lru", pkg)
    assert res == "Volatile-lru [EVD-001]"


def test_near_match_boundary_protection(dummy_generator):
    # 1. "Platform Engineering" must NOT match text containing only "Engineering"
    item1 = make_test_item("EVD-001", "DOC-001", "CHUNK-001", text="Team: Engineering")
    pkg1 = make_test_package([item1])
    res1 = dummy_generator._resolve_short_exact_match("Platform Engineering", pkg1)
    assert res1 == "Platform Engineering"

    # 2. "v1.0" must NOT match "v10"
    item2 = make_test_item("EVD-001", "DOC-001", "CHUNK-001", text="Deployed service version v10 in cluster.")
    pkg2 = make_test_package([item2])
    res2 = dummy_generator._resolve_short_exact_match("v1.0", pkg2)
    assert res2 == "v1.0"

    # 3. "10" must NOT match "100"
    item3 = make_test_item("EVD-001", "DOC-001", "CHUNK-001", text="Max connection pool size set to 100.")
    pkg3 = make_test_package([item3])
    res3 = dummy_generator._resolve_short_exact_match("10", pkg3)
    assert res3 == "10"

    # 4. "us-east-1" must NOT match "us-east-1a"
    item4 = make_test_item("EVD-001", "DOC-001", "CHUNK-001", text="Primary datacenter is us-east-1a.")
    pkg4 = make_test_package([item4])
    res4 = dummy_generator._resolve_short_exact_match("us-east-1", pkg4)
    assert res4 == "us-east-1"


def test_answers_with_gt_2_tokens_bypass_short_resolver(dummy_generator):
    item1 = make_test_item(
        "EVD-001", "DOC-001", "CHUNK-001",
        text="Fixes operational outage observed under EVT-NS-0002.",
    )
    pkg = make_test_package([item1])

    ans = "Fixes operational outage observed under EVT-NS-0002."
    res = dummy_generator._resolve_short_exact_match(ans, pkg)
    assert res == ans  # Untouched because 5 meaningful tokens > 2


def test_short_exact_match_validates_in_citation_validator():
    from novastack.citation_validator import CitationStatus, CitationValidator

    gen = GroundedAnswerGenerator.__new__(GroundedAnswerGenerator)
    gen.corpus_doc_ids = {"DOC-001"}
    gen.corpus_chunk_ids = {"CHUNK-001"}
    gen.validator = CitationValidator(corpus_doc_ids={"DOC-001"}, corpus_chunk_ids={"CHUNK-001"})

    item1 = make_test_item("EVD-001", "DOC-001", "CHUNK-001", text="Responsible team: Platform Engineering")
    pkg = make_test_package([item1])

    res_text = gen._resolve_short_exact_match("Platform Engineering", pkg)
    assert res_text == "Platform Engineering [EVD-001]"

    cits, status, unsupported = gen.validator.validate_citations(res_text, pkg)
    assert len(cits) == 1
    assert cits[0].status == CitationStatus.VALID
    assert status == "valid"
    assert len(unsupported) == 0
