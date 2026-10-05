"""Unit tests for Phase 4H-3 Sentence-Level Citation Resolution.

Verifies the 14 mandatory testing scenarios:
1. exact sentence match
2. near-exact sentence match
3. short answer sentence match
4. long chunk / short sentence denominator failure
5. equal-authority collision (safe refusal)
6. authority collision (strictly higher authority dominance)
7. query-entity disambiguation (query-aligned entity wins over higher authority)
8. non-coverage answer guard (refuses citation for explicit non-coverage)
9. unsupported answer (fails coverage threshold)
10. adversarial evidence (refuses citation)
11. unauthorized evidence (refuses citation)
12. superseded/stale evidence (refuses citation)
13. multi-claim answer
14. deterministic repeated resolution (byte-identical across 5 runs)
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
    source_entity_id: str = "DOC-001",
    source_type: str = "document",
) -> EvidenceItem:
    reasons = ["adversarial"] if is_adversarial else []
    return EvidenceItem(
        evidence_id=evidence_id,
        chunk_id=chunk_id,
        document_id=doc_id,
        tenant_id="tenant-alpha",
        source_type="adversarial_fixture" if is_adversarial else source_type,
        title=title,
        text=text,
        source_entity_id=source_entity_id,
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
    query: str = "Which team owns checkout-service?",
    excluded: list[EvidenceItem] | None = None,
    conflicts: list[EvidenceConflict] | None = None,
) -> EvidencePackage:
    return EvidencePackage(
        package_id="PKG-TEST-4H3",
        evaluation_id="EVAL-TEST-4H3",
        query=query,
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
def mock_generator():
    gen = GroundedAnswerGenerator.__new__(GroundedAnswerGenerator)
    gen.corpus_doc_ids = {
        "DOC-PR-0001",
        "DOC-PR-0002",
        "DOC-DEP-0001",
        "DOC-PM-0001",
        "DOC-PM-0002",
        "DOC-RUN-0001",
        "DOC-AUTH-0001",
        "DOC-ADV-0001",
        "DOC-UNAUTH-0001",
        "DOC-OLD-0001",
        "DOC-BKG-0001",
        "DOC-BKG-0002",
    }
    gen.corpus_chunk_ids = {
        f"{doc}::CHUNK-0001" for doc in gen.corpus_doc_ids
    }
    return gen


def test_1_exact_sentence_match(mock_generator):
    text = (
        "Pull Request Overview:\n"
        "Repository: novastack/feature-flags\n"
        "Problem Statement:\n"
        "Fixes operational outage observed under EVT-NS-0002.\n"
        "Merge status: succeeded by lead maintainer."
    )
    item = make_test_item("EVD-001", "DOC-PR-0001", "DOC-PR-0001::CHUNK-0001", text)
    pkg = make_test_package([item], query="What did PR-NS-0002 fix?")
    answer = "Fixes operational outage observed under EVT-NS-0002."

    result = mock_generator._resolve_sentence_level_match(answer, pkg)
    assert result == "Fixes operational outage observed under EVT-NS-0002. [EVD-001]"


def test_2_near_exact_sentence_match(mock_generator):
    text = (
        "Changelog Summary:\n"
        "Update rate-limiter thresholds for new API tier pricing\n"
        "Deployer: USR-NS-0052"
    )
    item = make_test_item("EVD-001", "DOC-DEP-0001", "DOC-DEP-0001::CHUNK-0001", text)
    pkg = make_test_package([item], query="What changed in rate-limiter deployment?")
    answer = "The rate-limiter thresholds were updated for new API tier pricing."

    result = mock_generator._resolve_sentence_level_match(answer, pkg)
    assert result == "The rate-limiter thresholds were updated for new API tier pricing. [EVD-001]"


def test_3_short_answer_sentence_match(mock_generator):
    text = (
        "EMERGENCY ROLLBACK DEPLOYMENT — DEP-NS-0008\n"
        "Rollback Rationale:\n"
        "Emergency rollback media-service to v2.9.5\n"
        "Executed at: 2026-06-14"
    )
    item = make_test_item("EVD-001", "DOC-DEP-0001", "DOC-DEP-0001::CHUNK-0001", text)
    pkg = make_test_package([item], query="What did rollback deployment accomplish?")
    answer = "Emergency rollback media-service to v2.9.5"

    result = mock_generator._resolve_sentence_level_match(answer, pkg)
    assert result == "Emergency rollback media-service to v2.9.5 [EVD-001]"


def test_4_long_chunk_short_sentence_denominator_failure(mock_generator):
    long_filler = " ".join([f"telemetry_metric_variable_{i}" for i in range(100)])
    sentence = "Primary database connection pool maximum size is configured to 100 connections."
    full_text = f"{long_filler}\n{sentence}\n{long_filler}"

    item = make_test_item("EVD-001", "DOC-PM-0001", "DOC-PM-0001::CHUNK-0001", full_text)
    pkg = make_test_package([item], query="What is the database connection pool size?")
    answer = "Primary database connection pool maximum size is configured to 100 connections."

    legacy_res = mock_generator._attach_deterministic_citations(answer, pkg, min_overlap_ratio=0.15)
    assert legacy_res == answer

    sentence_res = mock_generator._resolve_sentence_level_match(answer, pkg)
    assert sentence_res == f"{answer} [EVD-001]"


def test_5_equal_authority_collision(mock_generator):
    sentence = "Airfare: Standard economy class for all flights under 6 hours duration."
    item1 = make_test_item("EVD-001", "DOC-BKG-0001", "DOC-BKG-0001::CHUNK-0001", sentence, authority="authoritative")
    item2 = make_test_item("EVD-002", "DOC-BKG-0002", "DOC-BKG-0002::CHUNK-0001", sentence, authority="authoritative")

    pkg = make_test_package([item1, item2], query="What are valid travel reimbursement rates?")
    answer = "Standard economy class for all flights under 6 hours duration."

    result = mock_generator._resolve_sentence_level_match(answer, pkg)
    assert result == answer


def test_6_authority_collision(mock_generator):
    sentence = "Feature flag reverted and Redis eviction policy changed to volatile-lru."
    item_low = make_test_item("EVD-001", "DOC-RUN-0001", "DOC-RUN-0001::CHUNK-0001", sentence, authority="medium")
    item_high = make_test_item("EVD-002", "DOC-PM-0001", "DOC-PM-0001::CHUNK-0001", sentence, authority="high")

    pkg = make_test_package([item_low, item_high], query="What was the Redis cache mitigation?")
    answer = "Feature flag reverted and Redis eviction policy changed to volatile-lru."

    result = mock_generator._resolve_sentence_level_match(answer, pkg)
    assert result == f"{answer} [EVD-002]"


def test_7_query_entity_disambiguation(mock_generator):
    sentence = "Implemented staggered cache invalidation with configurable rate limiting."
    pm_item = make_test_item(
        "EVD-001", "DOC-PM-0001", "DOC-PM-0001::CHUNK-0001", sentence,
        authority="high", source_entity_id="EVT-NS-0004", source_type="postmortem"
    )
    pr_item = make_test_item(
        "EVD-002", "DOC-PR-0002", "DOC-PR-0002::CHUNK-0001", sentence,
        authority="medium", source_entity_id="PR-NS-0004", source_type="pull_request"
    )

    query = "What performance issue did pull request PR-NS-0004 address?"
    pkg = make_test_package([pm_item, pr_item], query=query)
    answer = "Implemented staggered cache invalidation with rate limiting."

    result = mock_generator._resolve_sentence_level_match(answer, pkg)
    assert result == f"{answer} [EVD-002]"


def test_8_non_coverage_answer_guard(mock_generator):
    text = "Database Optimization Notes: Table lock contention in PostgreSQL was observed on payment_transactions."
    item = make_test_item("EVD-001", "DOC-PM-0001", "DOC-PM-0001::CHUNK-0001", text)
    pkg = make_test_package([item], query="How do DevOps engineers resolve table lock contention?")

    ans1 = "The provided evidence does not detail how DevOps engineers resolve table lock contention in PostgreSQL."
    assert mock_generator._resolve_sentence_level_match(ans1, pkg) == ans1

    ans2 = "Insufficient evidence to answer this question."
    assert mock_generator._resolve_sentence_level_match(ans2, pkg) == ans2

    ans3 = "There is no information regarding table lock contention resolution."
    assert mock_generator._resolve_sentence_level_match(ans3, pkg) == ans3


def test_9_unsupported_answer(mock_generator):
    text = (
        "Changelog Summary:\n"
        "Deploy media-service v3.0.0 with new image processing API\n"
        "Checklist verified by lead."
    )
    item = make_test_item("EVD-001", "DOC-DEP-0001", "DOC-DEP-0001::CHUNK-0001", text)
    pkg = make_test_package([item], query="What was added in the deployment readiness checklist?")
    answer = "The deployment readiness checklist was updated to include new image processing API."

    result = mock_generator._resolve_sentence_level_match(answer, pkg)
    assert result == answer


def test_10_adversarial_evidence(mock_generator):
    text = "Fixes operational outage observed under EVT-NS-0002."
    item = make_test_item("EVD-001", "DOC-ADV-0001", "DOC-ADV-0001::CHUNK-0001", text, is_adversarial=True)
    pkg = make_test_package([item], query="What did PR-NS-0002 fix?")
    answer = "Fixes operational outage observed under EVT-NS-0002."

    result = mock_generator._resolve_sentence_level_match(answer, pkg)
    assert result == answer


def test_11_unauthorized_evidence(mock_generator):
    text = "Fixes operational outage observed under EVT-NS-0002."
    item = make_test_item(
        "EVD-001", "DOC-UNAUTH-0001", "DOC-UNAUTH-0001::CHUNK-0001", text,
        evidence_status=EvidenceStatus.UNAUTHORIZED.value
    )
    pkg = make_test_package([item], query="What did PR-NS-0002 fix?")
    answer = "Fixes operational outage observed under EVT-NS-0002."

    result = mock_generator._resolve_sentence_level_match(answer, pkg)
    assert result == answer


def test_12_superseded_stale_evidence(mock_generator):
    text = "Fixes operational outage observed under EVT-NS-0002."
    item = make_test_item("EVD-001", "DOC-OLD-0001", "DOC-OLD-0001::CHUNK-0001", text, status="superseded")
    pkg = make_test_package([item], query="What did PR-NS-0002 fix?")
    answer = "Fixes operational outage observed under EVT-NS-0002."

    result = mock_generator._resolve_sentence_level_match(answer, pkg)
    assert result == answer


def test_13_multi_claim_answer(mock_generator):
    text = (
        "Deployment DEP-NS-0001 deployed checkout-service v2.4.1.\n"
        "Deployment contained misconfigured maximum connections parameter set to 10."
    )
    item = make_test_item("EVD-001", "DOC-DEP-0001", "DOC-DEP-0001::CHUNK-0001", text)
    pkg = make_test_package([item], query="What was deployed in DEP-NS-0001?")
    answer = "Deployment DEP-NS-0001 deployed checkout-service v2.4.1."

    result = mock_generator._resolve_sentence_level_match(answer, pkg)
    assert "[EVD-001]" in result


def test_14_deterministic_repeated_resolution(mock_generator):
    text = "Fixes operational outage observed under EVT-NS-0002."
    item = make_test_item("EVD-001", "DOC-PR-0001", "DOC-PR-0001::CHUNK-0001", text)
    pkg = make_test_package([item], query="What did PR fix?")
    answer = "Fixes operational outage observed under EVT-NS-0002."

    outputs = [mock_generator._resolve_sentence_level_match(answer, pkg) for _ in range(5)]
    assert len(set(outputs)) == 1
    assert outputs[0] == "Fixes operational outage observed under EVT-NS-0002. [EVD-001]"


# =============================================================================
# CTO DIRECTIVE: PHASE 4H-3 INTEGRATION REGRESSION TESTS
# =============================================================================

import copy
import json
from pathlib import Path
from novastack.citation_validator import CitationValidator, CitationStatus

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def benchmark_integration_data():
    eval_cases = json.loads((WORKSPACE_ROOT / "data" / "evaluation" / "novastack" / "evaluation_cases.json").read_text(encoding="utf-8"))["evaluation_cases"]
    cases_map = {c["evaluation_id"]: c for c in eval_cases}
    p4e_cases = json.loads((WORKSPACE_ROOT / "data" / "evaluation" / "novastack" / "phase_4e_evidence_assembly.json").read_text(encoding="utf-8"))["cases"]
    p4e_map = {c["evaluation_id"]: c for c in p4e_cases}
    p4h1_data = json.loads((WORKSPACE_ROOT / "data" / "evaluation" / "novastack" / "phase_4h1_prompt_calibration.json").read_text(encoding="utf-8"))
    p4h1_map = {c["evaluation_id"]: c for c in p4h1_data["evaluated_cases"]}
    corpus_docs = json.loads((WORKSPACE_ROOT / "data" / "processed" / "novastack" / "search_documents.json").read_text(encoding="utf-8"))["search_documents"]
    corpus_chunks = json.loads((WORKSPACE_ROOT / "data" / "processed" / "novastack" / "search_chunks.json").read_text(encoding="utf-8"))["search_chunks"]
    doc_ids = {d["document_id"] for d in corpus_docs}
    chunk_ids = {d["chunk_id"] for d in corpus_chunks}
    
    gen = GroundedAnswerGenerator.__new__(GroundedAnswerGenerator)
    gen.corpus_doc_ids = doc_ids
    gen.corpus_chunk_ids = chunk_ids
    gen.validator = CitationValidator(corpus_doc_ids=doc_ids, corpus_chunk_ids=chunk_ids)

    return {
        "cases_map": cases_map,
        "p4e_map": p4e_map,
        "p4h1_map": p4h1_map,
        "doc_ids": doc_ids,
        "chunk_ids": chunk_ids,
        "generator": gen,
    }


def _make_benchmark_pkg(bdata, cid):
    c = bdata["cases_map"][cid]
    p4e_entry = bdata["p4e_map"][cid]["evidence_package"]
    selected = []
    for d in p4e_entry.get("selected_evidence", []):
        perms_data = d.get("permissions")
        if isinstance(perms_data, dict):
            perms = RecordPermissions(
                allowed_roles=perms_data.get("allowed_roles", []),
                allowed_departments=perms_data.get("allowed_departments", []),
                allowed_teams=perms_data.get("allowed_teams", []),
                allowed_user_ids=perms_data.get("allowed_user_ids", []),
            )
        else:
            perms = RecordPermissions()
        selected.append(
            EvidenceItem(
                evidence_id=d.get("evidence_id", ""),
                chunk_id=d.get("chunk_id", ""),
                document_id=d.get("document_id", ""),
                tenant_id=d.get("tenant_id", "tenant-alpha"),
                source_type=d.get("source_type", "document"),
                title=d.get("title", ""),
                text=d.get("text", ""),
                source_entity_id=d.get("source_entity_id", ""),
                source_entity_type=d.get("source_entity_type", "document"),
                related_entity_ids=d.get("related_entity_ids", []),
                authority_level=d.get("authority_level", "high"),
                classification=d.get("classification", "internal"),
                permissions=perms,
                status=d.get("status", "published"),
                version=d.get("version", "v1.0"),
                created_at=d.get("created_at", "2026-01-01T00:00:00Z"),
                updated_at=d.get("updated_at"),
                valid_from=d.get("valid_from"),
                valid_until=d.get("valid_until"),
                parent_id=d.get("parent_id"),
                supersedes_id=d.get("supersedes_id"),
                retrieval_rank=d.get("retrieval_rank", 0),
                retrieval_score=d.get("retrieval_score", 0.0),
                retrieval_channels=d.get("retrieval_channels", []),
                evidence_status=d.get("evidence_status", EvidenceStatus.ACCEPTED.value),
                evidence_reasons=d.get("evidence_reasons", []),
            )
        )
    return EvidencePackage(
        package_id=f"PKG-{cid}",
        evaluation_id=cid,
        query=c["query"],
        tenant_id=c["tenant_id"],
        user_context={"tenant_id": c["tenant_id"], "roles": ["engineer"], "classification_limit": "internal"},
        selected_evidence=selected,
        excluded_evidence=p4e_entry.get("excluded_evidence_summary", []),
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={},
    )


def _resolve_with_tiers(gen, answer, pkg, max_items=3):
    budgeted = copy.copy(pkg)
    budgeted.selected_evidence = pkg.selected_evidence[:max_items]
    # Tier 1
    ans_t1 = gen._attach_deterministic_citations(answer, budgeted, max_evidence_items=max_items)
    if ans_t1 != answer:
        return ans_t1, 1
    # Tier 2
    ans_t2 = gen._resolve_short_exact_match(answer, budgeted, max_evidence_items=max_items)
    if ans_t2 != answer:
        return ans_t2, 2
    # Tier 3
    ans_t3 = gen._resolve_sentence_level_match(answer, budgeted, max_evidence_items=max_items)
    if ans_t3 != answer:
        return ans_t3, 3
    return answer, 0


def test_resolver_order_preserved(mock_generator):
    """Prove Tier 1 -> Tier 2 -> Tier 3 execution hierarchy."""
    # 1. Tier 1 matches when chunk overlap is high
    chunk_text = "Primary database connection pool maximum size is configured to 100 connections."
    item1 = make_test_item("EVD-001", "DOC-PM-0001", "DOC-PM-0001::CHUNK-0001", chunk_text)
    pkg1 = make_test_package([item1], query="What is the database connection pool size?")
    ans1 = "Primary database connection pool maximum size is configured to 100 connections."
    res1, tier1 = _resolve_with_tiers(mock_generator, ans1, pkg1)
    assert tier1 == 1
    assert "[EVD-001]" in res1

    # 2. Tier 2 matches when answer is ultra-short (<= 2 tokens) and fails Tier 1 chunk overlap
    filler = " ".join([f"telemetry_metric_{i}" for i in range(100)])
    short_chunk = filler + "\nPlatform Engineering manages this cluster.\n" + filler
    item2 = make_test_item("EVD-002", "DOC-PR-0001", "DOC-PR-0001::CHUNK-0001", short_chunk)
    pkg2 = make_test_package([item2], query="Which team manages cluster?")
    ans2 = "Platform Engineering"
    res2, tier2 = _resolve_with_tiers(mock_generator, ans2, pkg2)
    assert tier2 == 2
    assert "[EVD-001]" in res2

    # 3. Tier 3 matches when answer is concise sentence (>2 tokens) and fails Tier 1 chunk overlap
    sentence = "Fixes operational outage observed under EVT-NS-0002."
    long_chunk = filler + "\n" + sentence + "\n" + filler
    item3 = make_test_item("EVD-003", "DOC-PR-0002", "DOC-PR-0002::CHUNK-0001", long_chunk)
    pkg3 = make_test_package([item3], query="What did PR fix?")
    ans3 = "Fixes operational outage observed under EVT-NS-0002."
    res3, tier3 = _resolve_with_tiers(mock_generator, ans3, pkg3)
    assert tier3 == 3
    assert "[EVD-001]" in res3


def test_eval_0011_gets_citation(benchmark_integration_data):
    """EVAL-0011 gets a citation referencing DOC-PR-PR-NS-0002-01."""
    gen = benchmark_integration_data["generator"]
    pkg = _make_benchmark_pkg(benchmark_integration_data, "EVAL-0011")
    raw_ans = benchmark_integration_data["p4h1_map"]["EVAL-0011"]["answer_result"]["answer_text"]
    res, tier = _resolve_with_tiers(gen, raw_ans, pkg)
    assert tier == 3
    assert "[EVD-001]" in res
    cits, _, _ = gen.validator.validate_citations(res, pkg, corpus_doc_ids=gen.corpus_doc_ids, corpus_chunk_ids=gen.corpus_chunk_ids)
    assert len(cits) == 1
    assert cits[0].document_id == "DOC-PR-PR-NS-0002-01"


def test_eval_0013_cites_pr_rather_than_postmortem(benchmark_integration_data):
    """EVAL-0013 cites the PR (DOC-PR-PR-NS-0004-01) rather than the postmortem (DOC-PM-EVT-NS-0004-01)."""
    gen = benchmark_integration_data["generator"]
    pkg = _make_benchmark_pkg(benchmark_integration_data, "EVAL-0013")
    raw_ans = benchmark_integration_data["p4h1_map"]["EVAL-0013"]["answer_result"]["answer_text"]
    res, tier = _resolve_with_tiers(gen, raw_ans, pkg)
    assert tier == 3
    assert "[EVD-002]" in res
    cits, _, _ = gen.validator.validate_citations(res, pkg, corpus_doc_ids=gen.corpus_doc_ids, corpus_chunk_ids=gen.corpus_chunk_ids)
    assert len(cits) == 1
    assert cits[0].document_id == "DOC-PR-PR-NS-0004-01"
    assert cits[0].document_id != "DOC-PM-EVT-NS-0004-01"


def test_eval_0015_gets_citation(benchmark_integration_data):
    """EVAL-0015 gets a citation referencing DOC-DEP-DEP-NS-0006-01."""
    gen = benchmark_integration_data["generator"]
    pkg = _make_benchmark_pkg(benchmark_integration_data, "EVAL-0015")
    raw_ans = benchmark_integration_data["p4h1_map"]["EVAL-0015"]["answer_result"]["answer_text"]
    res, tier = _resolve_with_tiers(gen, raw_ans, pkg)
    assert tier == 3
    assert "[EVD-002]" in res
    cits, _, _ = gen.validator.validate_citations(res, pkg, corpus_doc_ids=gen.corpus_doc_ids, corpus_chunk_ids=gen.corpus_chunk_ids)
    assert len(cits) == 1
    assert cits[0].document_id == "DOC-DEP-DEP-NS-0006-01"


def test_eval_0016_gets_citation(benchmark_integration_data):
    """EVAL-0016 gets a citation referencing DOC-PR-PR-NS-0008-01."""
    gen = benchmark_integration_data["generator"]
    pkg = _make_benchmark_pkg(benchmark_integration_data, "EVAL-0016")
    raw_ans = benchmark_integration_data["p4h1_map"]["EVAL-0016"]["answer_result"]["answer_text"]
    res, tier = _resolve_with_tiers(gen, raw_ans, pkg)
    assert tier == 3
    assert "[EVD-001]" in res
    cits, _, _ = gen.validator.validate_citations(res, pkg, corpus_doc_ids=gen.corpus_doc_ids, corpus_chunk_ids=gen.corpus_chunk_ids)
    assert len(cits) == 1
    assert cits[0].document_id == "DOC-PR-PR-NS-0008-01"


def test_eval_0043_gets_citation(benchmark_integration_data):
    """EVAL-0043 gets a citation referencing DOC-DEP-DEP-NS-0008-ROLLBACK."""
    gen = benchmark_integration_data["generator"]
    pkg = _make_benchmark_pkg(benchmark_integration_data, "EVAL-0043")
    raw_ans = benchmark_integration_data["p4h1_map"]["EVAL-0043"]["answer_result"]["answer_text"]
    res, tier = _resolve_with_tiers(gen, raw_ans, pkg)
    assert tier == 3
    assert "[EVD-001]" in res
    cits, _, _ = gen.validator.validate_citations(res, pkg, corpus_doc_ids=gen.corpus_doc_ids, corpus_chunk_ids=gen.corpus_chunk_ids)
    assert len(cits) == 1
    assert cits[0].document_id == "DOC-DEP-DEP-NS-0008-ROLLBACK"


def test_eval_0061_refuses_ambiguous_citation(benchmark_integration_data):
    """EVAL-0061 refuses ambiguous citation under equal authority."""
    gen = benchmark_integration_data["generator"]
    pkg = _make_benchmark_pkg(benchmark_integration_data, "EVAL-0061")
    raw_ans = benchmark_integration_data["p4h1_map"]["EVAL-0061"]["answer_result"]["answer_text"]
    res, tier = _resolve_with_tiers(gen, raw_ans, pkg)
    assert tier == 0
    assert res == raw_ans


def test_eval_0069_refuses_ambiguous_citation(benchmark_integration_data):
    """EVAL-0069 refuses ambiguous citation under equal authority."""
    gen = benchmark_integration_data["generator"]
    pkg = _make_benchmark_pkg(benchmark_integration_data, "EVAL-0069")
    raw_ans = benchmark_integration_data["p4h1_map"]["EVAL-0069"]["answer_result"]["answer_text"]
    res, tier = _resolve_with_tiers(gen, raw_ans, pkg)
    assert tier == 0
    assert res == raw_ans


def test_eval_0112_refuses_ambiguous_citation(benchmark_integration_data):
    """EVAL-0112 refuses ambiguous citation under equal authority."""
    gen = benchmark_integration_data["generator"]
    pkg = _make_benchmark_pkg(benchmark_integration_data, "EVAL-0112")
    raw_ans = benchmark_integration_data["p4h1_map"]["EVAL-0112"]["answer_result"]["answer_text"]
    res, tier = _resolve_with_tiers(gen, raw_ans, pkg)
    assert tier == 0
    assert res == raw_ans


def test_eval_0072_remains_uncited(benchmark_integration_data):
    """EVAL-0072 remains uncited due to unsupported / conflated generation."""
    gen = benchmark_integration_data["generator"]
    pkg = _make_benchmark_pkg(benchmark_integration_data, "EVAL-0072")
    raw_ans = benchmark_integration_data["p4h1_map"]["EVAL-0072"]["answer_result"]["answer_text"]
    res, tier = _resolve_with_tiers(gen, raw_ans, pkg)
    assert tier == 0
    assert res == raw_ans


def test_eval_0113_remains_uncited(benchmark_integration_data):
    """EVAL-0113 remains uncited due to non-coverage answer guard."""
    gen = benchmark_integration_data["generator"]
    pkg = _make_benchmark_pkg(benchmark_integration_data, "EVAL-0113")
    raw_ans = benchmark_integration_data["p4h1_map"]["EVAL-0113"]["answer_result"]["answer_text"]
    res, tier = _resolve_with_tiers(gen, raw_ans, pkg)
    assert tier == 0
    assert res == raw_ans


def test_unauthorized_evidence_cannot_be_cited_regression(mock_generator):
    """Prove unauthorized evidence cannot be cited across any tier."""
    text = "Fixes operational outage observed under EVT-NS-0002."
    item = make_test_item("EVD-001", "DOC-UNAUTH-0001", "DOC-UNAUTH-0001::CHUNK-0001", text, evidence_status=EvidenceStatus.UNAUTHORIZED.value)
    pkg = make_test_package([item], query="What did PR-NS-0002 fix?")
    answer = "Fixes operational outage observed under EVT-NS-0002."
    res, tier = _resolve_with_tiers(mock_generator, answer, pkg)
    assert tier == 0
    assert res == answer


def test_adversarial_evidence_cannot_be_cited_regression(mock_generator):
    """Prove adversarial evidence cannot be cited across any tier."""
    text = "Fixes operational outage observed under EVT-NS-0002."
    item = make_test_item("EVD-001", "DOC-ADV-0001", "DOC-ADV-0001::CHUNK-0001", text, is_adversarial=True)
    pkg = make_test_package([item], query="What did PR-NS-0002 fix?")
    answer = "Fixes operational outage observed under EVT-NS-0002."
    res, tier = _resolve_with_tiers(mock_generator, answer, pkg)
    assert tier == 0
    assert res == answer


def test_default_citation_resolver_is_c2():
    """Prove C2 is the default citation resolver in GroundedAnswerGenerator.generate_answer."""
    import inspect
    sig = inspect.signature(GroundedAnswerGenerator.generate_answer)
    assert sig.parameters["citation_resolver"].default == "c2"

