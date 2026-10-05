"""Unit tests for Phase 4F Deterministic Citation Validator."""

import pytest

from novastack.citation_validator import Citation, CitationStatus, CitationValidator
from novastack.evidence import EvidenceConflict, EvidenceItem, EvidencePackage, EvidenceStatus
from novastack.models import RecordPermissions


def make_sample_item(
    evidence_id: str,
    doc_id: str,
    chunk_id: str,
    status: str = EvidenceStatus.ACCEPTED.value,
    authority: str = "authoritative",
    classification: str = "internal",
    text: str = "Checkout service connection pool leak caused incident INC-NS-0001.",
    title: str = "Incident Postmortem",
    source_type: str = "incident_postmortem",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        chunk_id=chunk_id,
        document_id=doc_id,
        tenant_id="tenant-alpha",
        source_type=source_type,
        title=title,
        text=text,
        source_entity_id="INC-NS-0001",
        source_entity_type="incident",
        related_entity_ids=["checkout-service"],
        authority_level=authority,
        classification=classification,
        permissions=RecordPermissions(allowed_roles=["engineer"]),
        status="published",
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
        evidence_status=status,
    )


def make_package(selected: list[EvidenceItem], excluded: list[EvidenceItem] | None = None) -> EvidencePackage:
    return EvidencePackage(
        package_id="PKG-TEST-001",
        evaluation_id="EVAL-TEST-0001",
        query="What caused incident INC-NS-0001?",
        tenant_id="tenant-alpha",
        user_context={"tenant_id": "tenant-alpha", "roles": ["engineer"], "classification_limit": "internal"},
        selected_evidence=selected,
        excluded_evidence=excluded or [],
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={},
    )


def test_valid_citation_matching_selected_evidence():
    item1 = make_sample_item("EVD-001", "DOC-001", "CHUNK-001")
    pkg = make_package([item1])
    validator = CitationValidator(corpus_doc_ids={"DOC-001"}, corpus_chunk_ids={"CHUNK-001"})

    text = "The incident was caused by a connection pool leak in checkout-service [EVD-001]."
    citations, overall_status, unsupported = validator.validate_citations(text, pkg)

    assert len(citations) == 1
    assert citations[0].status == CitationStatus.VALID
    assert citations[0].phrase_match_detected is True
    assert overall_status == "valid"
    assert len(unsupported) == 0


def test_shorthand_citation_index():
    item1 = make_sample_item("EVD-FULL-ID-01", "DOC-001", "CHUNK-001")
    pkg = make_package([item1])
    validator = CitationValidator(corpus_doc_ids={"DOC-001"}, corpus_chunk_ids={"CHUNK-001"})

    text = "The issue was connection pool exhaustion [1]."
    citations, overall_status, unsupported = validator.validate_citations(text, pkg)

    assert len(citations) == 1
    assert citations[0].status == CitationStatus.VALID
    assert citations[0].evidence_id == "EVD-FULL-ID-01"
    assert overall_status == "valid"


def test_invalid_citation_corpus_absence():
    item1 = make_sample_item("EVD-001", "DOC-GHOST", "CHUNK-GHOST")
    pkg = make_package([item1])
    validator = CitationValidator(corpus_doc_ids={"DOC-REAL"}, corpus_chunk_ids={"CHUNK-REAL"})

    text = "Grounded fact [EVD-001]."
    citations, overall_status, unsupported = validator.validate_citations(text, pkg)

    assert len(citations) == 1
    assert citations[0].status == CitationStatus.INVALID
    assert any("not_in_corpus" in r for r in citations[0].reasons)
    assert overall_status == "invalid"
    assert len(unsupported) > 0


def test_unauthorized_excluded_citation():
    item1 = make_sample_item("EVD-001", "DOC-001", "CHUNK-001")
    unauth_item = make_sample_item(
        "EVD-SEC-001", "DOC-SEC-001", "CHUNK-SEC-001",
        status=EvidenceStatus.UNAUTHORIZED.value,
        classification="restricted",
    )
    unauth_item.evidence_reasons = ["unauthorized_classification_mismatch"]
    pkg = make_package([item1], excluded=[unauth_item])

    validator = CitationValidator(corpus_doc_ids={"DOC-001", "DOC-SEC-001"})
    text = "Leaked internal secret [DOC-SEC-001]."
    citations, overall_status, unsupported = validator.validate_citations(text, pkg)

    assert len(citations) == 1
    assert citations[0].status == CitationStatus.UNAUTHORIZED
    assert overall_status == "invalid"
    assert "unauthorized" in unsupported[0]


def test_adversarial_quarantined_citation():
    adv_item = make_sample_item(
        "EVD-ADV-001", "DOC-ADV-001", "CHUNK-ADV-001",
        status=EvidenceStatus.ADVERSARIAL.value,
        source_type="adversarial_fixture",
    )
    adv_item.evidence_reasons = ["retrieval_poisoning_falsified_ground_truth"]
    pkg = make_package([], excluded=[adv_item])

    validator = CitationValidator(corpus_doc_ids={"DOC-ADV-001"})
    text = "Falsified claim [DOC-ADV-001]."
    citations, overall_status, unsupported = validator.validate_citations(text, pkg)

    assert len(citations) == 1
    assert citations[0].status == CitationStatus.INVALID
    assert citations[0].is_adversarial is True
    assert overall_status == "invalid"


def test_unknown_phantom_citation():
    item1 = make_sample_item("EVD-001", "DOC-001", "CHUNK-001")
    pkg = make_package([item1])
    validator = CitationValidator(corpus_doc_ids={"DOC-001"})

    text = "Hallucinated claim [DOC-FAKE-9999]."
    citations, overall_status, unsupported = validator.validate_citations(text, pkg)

    assert len(citations) == 1
    assert citations[0].status == CitationStatus.UNKNOWN
    assert overall_status == "invalid"


def test_mixed_citations_partially_valid():
    item1 = make_sample_item("EVD-001", "DOC-001", "CHUNK-001")
    pkg = make_package([item1])
    validator = CitationValidator(corpus_doc_ids={"DOC-001"})

    text = "Fact one [EVD-001] and hallucinated fact two [EVD-999]."
    citations, overall_status, unsupported = validator.validate_citations(text, pkg)

    assert len(citations) == 2
    assert citations[0].status == CitationStatus.VALID
    assert citations[1].status == CitationStatus.UNKNOWN
    assert overall_status == "partially_valid"
    assert len(unsupported) == 1
