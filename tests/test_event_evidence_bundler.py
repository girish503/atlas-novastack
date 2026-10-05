"""Unit and Regression Tests for Event-Centric Evidence Bundler — Phase 4K-C."""

import pytest
from pathlib import Path
import json

from novastack.entity_catalog import EntityCatalog
from novastack.event_evidence_bundler import (
    EventBundlerConfig,
    EventEvidenceBundler,
    MULTI_PERSPECTIVE_PATTERNS,
)
from novastack.evidence import EvidenceItem, EvidenceStatus

WORKSPACE = Path(__file__).resolve().parents[1]
proc_dir = WORKSPACE / "data" / "processed" / "novastack"
raw_dir = WORKSPACE / "data" / "raw" / "novastack"


@pytest.fixture(scope="module")
def catalog():
    return EntityCatalog(raw_dir, proc_dir / "search_chunks.json")


@pytest.fixture
def bundler(catalog):
    return EventEvidenceBundler(
        catalog=catalog,
        config=EventBundlerConfig(enable_event_bundling=True),
    )


def make_dummy_item(
    doc_id: str,
    chunk_id: str,
    source_type: str,
    entity_id: str | None = None,
    related_ids: list[str] | None = None,
    auth: str = "high",
    status: str = "accepted",
    rank: int = 1,
    tenant_id: str = "TENANT-NOVASTACK",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"EVD-{doc_id}",
        chunk_id=chunk_id,
        document_id=doc_id,
        tenant_id=tenant_id,
        source_type=source_type,
        title=f"Title for {doc_id}",
        text=f"Sample text for {doc_id}",
        source_entity_id=entity_id,
        source_entity_type="event" if entity_id and entity_id.startswith("EVT-") else "incident",
        related_entity_ids=related_ids or [],
        authority_level=auth,
        classification="internal",
        permissions={},
        status="published",
        version="v1",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        valid_from=None,
        valid_until=None,
        parent_id=None,
        supersedes_id=None,
        retrieval_rank=rank,
        retrieval_score=1.0 / rank,
        retrieval_channels=["bm25", "dense"],
        evidence_status=status,
        evidence_reasons=[],
        trust_score=0.9,
    )


def test_multi_perspective_query_detection(bundler):
    # Positive multi-perspective queries
    assert bundler.is_multi_perspective_query("What did support tickets report about customer billing errors and what PR fixed the analytics calculation?")
    assert bundler.is_multi_perspective_query("Trace the full causal chain of the checkout outage: what symptom appeared, which service was affected, which deployment caused it, and which PR resolved it?")
    assert bundler.is_multi_perspective_query("What did triage channel notes say about search latency and what did postmortem action items require?")

    # Negative single-perspective queries
    assert not bundler.is_multi_perspective_query("What was the incident start time for INC-NS-0001?")
    assert not bundler.is_multi_perspective_query("Who approved pull request PR-NS-0002?")
    assert not bundler.is_multi_perspective_query("What is the deprecation policy for API v1?")


def test_event_affinity_mapping(bundler):
    # Direct event ID
    it1 = make_dummy_item("DOC-PM-EVT-NS-0008-01", "C-01", "postmortem", entity_id="EVT-NS-0008")
    assert "EVT-NS-0008" in bundler.get_event_affinity(it1)

    # Relational entity (PR-NS-0007 fixes EVT-NS-0008)
    it2 = make_dummy_item("DOC-PR-PR-NS-0007-01", "C-01", "pull_request_note", entity_id="PR-NS-0007")
    assert "EVT-NS-0008" in bundler.get_event_affinity(it2)

    # Document ID embedding
    it3 = make_dummy_item("DOC-TKT-EVT-NS-0008-CUST-NS-0011", "C-01", "support_ticket", entity_id="INC-NS-0008")
    assert "EVT-NS-0008" in bundler.get_event_affinity(it3)


def test_dominant_event_identification(bundler):
    candidates = [
        make_dummy_item("DOC-PR-PR-NS-0007-01", "C-01", "pull_request_note", entity_id="PR-NS-0007", rank=1),
        make_dummy_item("DOC-PM-EVT-NS-0008-01", "C-01", "postmortem", entity_id="EVT-NS-0008", rank=2),
        make_dummy_item("DOC-TKT-EVT-NS-0008-CUST-NS-0011", "C-01", "support_ticket", entity_id="INC-NS-0008", rank=3),
        make_dummy_item("DOC-PM-EVT-NS-0003-01", "C-01", "postmortem", entity_id="EVT-NS-0003", rank=4),
    ]
    query = "What did support tickets report and what PR fixed the analytics calculation?"
    dominant = bundler.identify_dominant_event(candidates, query)
    assert dominant == "EVT-NS-0008"


def test_bundling_creates_bounded_diverse_bundle(bundler):
    candidates = [
        make_dummy_item("DOC-PR-PR-NS-0007-01", "DOC-PR-PR-NS-0007-01::CHUNK-0001", "pull_request_note", entity_id="PR-NS-0007", rank=1, auth="medium"),
        make_dummy_item("DOC-PM-EVT-NS-0008-01", "DOC-PM-EVT-NS-0008-01::CHUNK-0001", "postmortem", entity_id="EVT-NS-0008", rank=2, auth="high"),
        make_dummy_item("DOC-DOC-EVT-NS-0008-02", "DOC-DOC-EVT-NS-0008-02::CHUNK-0001", "documentation", entity_id="SVC-NS-0002", rank=3, auth="high"),
        make_dummy_item("DOC-TKT-EVT-NS-0008-CUST-NS-0011", "DOC-TKT-EVT-NS-0008-CUST-NS-0011::CHUNK-0001", "support_ticket", entity_id="INC-NS-0008", rank=4, auth="low"),
        make_dummy_item("DOC-PM-EVT-NS-0003-01", "DOC-PM-EVT-NS-0003-01::CHUNK-0001", "postmortem", entity_id="EVT-NS-0003", rank=5, auth="high"),
    ]
    query = "What did support tickets report about customer billing errors and what PR fixed the analytics calculation?"
    result = bundler.bundle_evidence(candidates, query)

    assert result.is_bundled
    assert result.canonical_event_id == "EVT-NS-0008"
    assert len(result.perspectives) == 3

    # Top-3 documents should be: Postmortem, Support ticket, and PR note
    top3_doc_ids = [p.document_id for p in result.perspectives]
    assert "DOC-PM-EVT-NS-0008-01" in top3_doc_ids
    assert "DOC-TKT-EVT-NS-0008-CUST-NS-0011" in top3_doc_ids
    assert "DOC-PR-PR-NS-0007-01" in top3_doc_ids

    # The unrelated EVT-NS-0003 document should be excluded from the bundle
    assert "DOC-PM-EVT-NS-0003-01" not in top3_doc_ids

    # Support ticket should have accepted_with_caveat and low authority
    tkt_item = [it for it in result.bundled_items if it.document_id == "DOC-TKT-EVT-NS-0008-CUST-NS-0011"][0]
    assert tkt_item.evidence_status == EvidenceStatus.ACCEPTED_WITH_CAVEAT.value
    assert tkt_item.authority_level == "low"


def test_causal_chain_bundling(bundler):
    candidates = [
        make_dummy_item("DOC-PM-EVT-NS-0001-01", "DOC-PM-EVT-NS-0001-01::CHUNK-0001", "postmortem", entity_id="EVT-NS-0001", rank=1, auth="high"),
        make_dummy_item("DOC-PM-EVT-NS-0003-01", "DOC-PM-EVT-NS-0003-01::CHUNK-0001", "postmortem", entity_id="EVT-NS-0003", rank=2, auth="high"),
        make_dummy_item("DOC-PR-PR-NS-0003-01", "DOC-PR-PR-NS-0003-01::CHUNK-0001", "pull_request_note", entity_id="PR-NS-0003", rank=3, auth="medium"),
        make_dummy_item("DOC-PM-EVT-NS-0001-01", "DOC-PM-EVT-NS-0001-01::CHUNK-0002", "postmortem", entity_id="EVT-NS-0001", rank=4, auth="high"),
        make_dummy_item("DOC-PR-PR-NS-0001-01", "DOC-PR-PR-NS-0001-01::CHUNK-0001", "pull_request_note", entity_id="PR-NS-0001", rank=5, auth="medium"),
    ]
    query = "Trace the full causal chain of the checkout outage: what symptom appeared, which service was affected, which deployment caused it, and which PR resolved it?"
    result = bundler.bundle_evidence(candidates, query)

    assert result.is_bundled
    assert result.canonical_event_id == "EVT-NS-0001"

    top3_chunk_ids = [p.chunk_id for p in result.perspectives]
    assert "DOC-PM-EVT-NS-0001-01::CHUNK-0001" in top3_chunk_ids
    assert "DOC-PM-EVT-NS-0001-01::CHUNK-0002" in top3_chunk_ids
    assert "DOC-PR-PR-NS-0001-01::CHUNK-0001" in top3_chunk_ids

    # Excluded divergent event EVT-NS-0003
    excluded_reasons = {ex["document_id"]: ex["reason"] for ex in result.excluded_candidates}
    assert "cross_event_divergence" in excluded_reasons.get("DOC-PM-EVT-NS-0003-01", "")


def test_control_invariance_when_disabled(catalog):
    disabled_bundler = EventEvidenceBundler(
        catalog=catalog,
        config=EventBundlerConfig(enable_event_bundling=False),
    )
    candidates = [
        make_dummy_item("DOC-PR-PR-NS-0007-01", "C-01", "pull_request_note", rank=1),
        make_dummy_item("DOC-PM-EVT-NS-0008-01", "C-01", "postmortem", rank=2),
    ]
    query = "What did support tickets report and what PR fixed the analytics calculation?"
    result = disabled_bundler.bundle_evidence(candidates, query)
    assert not result.is_bundled
    assert result.bundled_items == candidates
