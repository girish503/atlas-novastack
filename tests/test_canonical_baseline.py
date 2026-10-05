"""Validation tests for ATLAS Phase 4K Canonical Evaluation Baseline Freeze."""
import json
from pathlib import Path
import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
BASELINE_PATH = WORKSPACE / "artifacts" / "phase_4k_canonical_baseline.json"


@pytest.fixture(scope="module")
def baseline_data():
    assert BASELINE_PATH.exists(), f"Canonical baseline file not found: {BASELINE_PATH}"
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def test_case_count_and_composition(baseline_data):
    cases = baseline_data["cases"]
    assert len(cases) == 120, f"Expected exactly 120 cases, got {len(cases)}"

    pos = [c for c in cases if c["expected_behavior"] not in ("deny_unauthorized", "abstain_insufficient_evidence")]
    neg = [c for c in cases if c["expected_behavior"] in ("deny_unauthorized", "abstain_insufficient_evidence")]
    assert len(pos) == 101, f"Expected 101 positive cases, got {len(pos)}"
    assert len(neg) == 19, f"Expected 19 negative cases, got {len(neg)}"


def test_certified_performance_outcomes(baseline_data):
    cases = baseline_data["cases"]
    pos = [c for c in cases if c["expected_behavior"] not in ("deny_unauthorized", "abstain_insufficient_evidence")]
    neg = [c for c in cases if c["expected_behavior"] in ("deny_unauthorized", "abstain_insufficient_evidence")]

    succ_pos = [c for c in pos if c["baseline_answer_status"] == "answered" and len(c["citations"]) > 0 and c["baseline_failure_category"] == "none"]
    unsucc_pos = [c for c in pos if c["baseline_failure_category"] != "none"]
    assert len(succ_pos) == 53, f"Expected 53 successful positive cases, got {len(succ_pos)}"
    assert len(unsucc_pos) == 48, f"Expected 48 unsuccessful positive cases, got {len(unsucc_pos)}"

    neg_abst = [c for c in neg if c["baseline_answer_status"] == "abstained" and c["baseline_failure_category"] == "none"]
    assert len(neg_abst) == 19, f"Expected 19 intentional abstentions, got {len(neg_abst)}"


def test_mandatory_record_schema(baseline_data):
    cases = baseline_data["cases"]
    required_fields = [
        "evaluation_id", "query", "category", "expected_behavior",
        "expected_document_ids", "forbidden_document_ids", "baseline_answer_status",
        "baseline_failure_category", "selected_evidence_ids", "top3_document_ids",
        "citations", "authorization_outcome", "adversarial_outcome",
        "tenant_outcome", "latency_ms", "artifact_version_identifier"
    ]
    for c in cases:
        for f in required_fields:
            assert f in c, f"Case {c.get('evaluation_id')} missing field {f}"
            assert c[f] is not None, f"Case {c.get('evaluation_id')} field {f} is None"


def test_security_and_governance_invariants(baseline_data):
    cases = baseline_data["cases"]
    for c in cases:
        assert c["authorization_outcome"] in ("allow", "denied_unauthorized")
        assert c["adversarial_outcome"] in ("clean", "quarantined")
        assert c["tenant_outcome"] == "isolated"


def test_metric_definitions_completeness(baseline_data):
    metric_defs = baseline_data.get("metric_definitions", {})
    required_metrics = [
        "positive_success_rate", "retrieval_Recall@K", "citation_precision",
        "citation_completeness", "intentional_abstention", "false_abstention",
        "security_violation", "regression", "context_sufficiency"
    ]
    for m in required_metrics:
        assert m in metric_defs, f"Missing metric definition: {m}"
        assert "formula" in metric_defs[m], f"Missing formula for {m}"
        assert "description" in metric_defs[m], f"Missing description for {m}"
