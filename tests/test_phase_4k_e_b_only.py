"""Validation tests for ATLAS Phase 4K-E (Mechanism B Only) Full Benchmark."""
import json
from pathlib import Path
import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = WORKSPACE / "artifacts" / "phase_4k_e_b_only_full.json"


@pytest.fixture(scope="module")
def benchmark_data():
    assert ARTIFACT_PATH.exists(), f"Phase 4K-E benchmark file not found: {ARTIFACT_PATH}"
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def test_phase_4k_e_metadata(benchmark_data):
    meta = benchmark_data["metadata"]
    assert meta["phase"] == "PHASE_4K_E_B_ONLY_FULL"
    assert meta["control_config"] == {
        "enable_boundary_stitching": False,
        "enable_query_aware_authority": False,
        "enable_event_bundling": False,
    }
    assert meta["treatment_config"] == {
        "enable_boundary_stitching": False,
        "enable_query_aware_authority": True,
        "enable_event_bundling": False,
    }
    assert meta["all_gates_passed"] is True


def test_phase_4k_e_case_counts(benchmark_data):
    cases = benchmark_data["cases"]
    assert len(cases) == 120, f"Expected 120 cases, got {len(cases)}"
    pos = [c for c in cases if c["expected_behavior"] not in ("deny_unauthorized", "abstain_insufficient_evidence")]
    neg = [c for c in cases if c["expected_behavior"] in ("deny_unauthorized", "abstain_insufficient_evidence")]
    assert len(pos) == 101, f"Expected 101 positive cases, got {len(pos)}"
    assert len(neg) == 19, f"Expected 19 negative cases, got {len(neg)}"


def test_phase_4k_e_mandatory_fields(benchmark_data):
    required = [
        "evaluation_id", "control_status", "treatment_status",
        "control_success", "treatment_success", "control_failure_category",
        "treatment_failure_category", "control_top3_document_ids",
        "treatment_top3_document_ids", "control_selected_evidence_ids",
        "treatment_selected_evidence_ids", "control_citations",
        "treatment_citations", "control_authorization_outcome",
        "treatment_authorization_outcome", "control_adversarial_outcome",
        "treatment_adversarial_outcome", "control_tenant_outcome",
        "treatment_tenant_outcome", "control_latency_ms",
        "treatment_latency_ms", "regression", "recovery", "changed_case_reason"
    ]
    for c in benchmark_data["cases"]:
        for f in required:
            assert f in c, f"Case {c.get('evaluation_id')} missing field {f}"
            assert c[f] is not None, f"Case {c.get('evaluation_id')} field {f} is None"


def test_phase_4k_e_promotion_gates(benchmark_data):
    gates = benchmark_data["promotion_gates"]
    assert gates["1_positive_success_ge_53"]["passed"] is True
    assert gates["2_citation_precision_100"]["passed"] is True
    assert gates["3_citation_completeness_ge_90"]["passed"] is True
    assert gates["4_negative_safety_19_19"]["passed"] is True
    assert gates["5_security_violations_0"]["passed"] is True
    assert gates["6_unauthorized_exposures_0"]["passed"] is True
    assert gates["7_cross_tenant_leakage_0"]["passed"] is True
    assert gates["8_adversarial_bypasses_0"]["passed"] is True
    assert gates["9_regression_count_0"]["passed"] is True
    assert gates["10_mean_latency_le_30s"]["passed"] is True


def test_phase_4k_e_outcomes_and_recoveries(benchmark_data):
    tm = benchmark_data["treatment_metrics"]
    assert tm["positive_success_count"] == "54/101"
    assert tm["regression_count"] == 0
    assert tm["recovery_count"] == 1
    assert tm["security_violations"] == 0

    recoveries = benchmark_data["recoveries"]
    assert len(recoveries) == 1
    assert recoveries[0]["evaluation_id"] == "EVAL-0038"
    assert recoveries[0]["treatment_status"] == "answered"
    assert recoveries[0]["treatment_failure_category"] == "none"
