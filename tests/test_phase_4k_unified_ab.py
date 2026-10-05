"""Validation tests for Phase 4K Unified A/B Benchmark artifact."""
import json
from pathlib import Path
import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
AB_PATH = WORKSPACE / "artifacts" / "phase_4k_unified_ab.json"


@pytest.fixture(scope="module")
def ab_data():
    assert AB_PATH.exists(), f"Unified A/B benchmark file not found: {AB_PATH}"
    return json.loads(AB_PATH.read_text(encoding="utf-8"))


def test_unified_ab_case_counts(ab_data):
    cases = ab_data["cases"]
    assert len(cases) == 120, f"Expected 120 cases, got {len(cases)}"
    pos = [c for c in cases if c["expected_behavior"] not in ("deny_unauthorized", "abstain_insufficient_evidence")]
    neg = [c for c in cases if c["expected_behavior"] in ("deny_unauthorized", "abstain_insufficient_evidence")]
    assert len(pos) == 101
    assert len(neg) == 19


def test_unified_ab_mandatory_fields(ab_data):
    required = [
        "evaluation_id", "query", "baseline_status", "treatment_status",
        "baseline_success", "treatment_success", "baseline_failure_category",
        "treatment_failure_category", "baseline_top3_document_ids",
        "treatment_top3_document_ids", "baseline_selected_evidence_ids",
        "treatment_selected_evidence_ids", "baseline_citations",
        "treatment_citations", "baseline_authorization_outcome",
        "treatment_authorization_outcome", "baseline_adversarial_outcome",
        "treatment_adversarial_outcome", "baseline_tenant_outcome",
        "treatment_tenant_outcome", "baseline_latency_ms",
        "treatment_latency_ms", "regression", "recovery", "changed_case_reason"
    ]
    for c in ab_data["cases"]:
        for f in required:
            assert f in c, f"Case {c.get('evaluation_id')} missing field {f}"
            assert c[f] is not None, f"Case {c.get('evaluation_id')} field {f} is None"


def test_unified_ab_transition_matrix(ab_data):
    tm = ab_data["transition_matrix"]
    total_transitions = tm["SUCCESS_to_SUCCESS"] + tm["SUCCESS_to_FAILURE"] + tm["FAILURE_to_SUCCESS"] + tm["FAILURE_to_FAILURE"]
    assert total_transitions == 120, f"Expected 120 transitions, got {total_transitions}"
    assert tm["SUCCESS_to_FAILURE"] == 4, "Expected exactly 4 regressions"
    assert tm["FAILURE_to_SUCCESS"] == 7, "Expected exactly 7 recoveries"


def test_unified_ab_safety_gates(ab_data):
    gates = ab_data["mandatory_safety_gates"]
    assert gates["negative_safety"] == "19/19"
    assert gates["security_violations"] == 0
    assert gates["cross_tenant_leakage"] == 0
    assert gates["unauthorized_exposure"] == 0
    assert gates["adversarial_bypass"] == 0
