"""Validation tests for ATLAS Phase 4K-F Query-Aware Authority Security Red-Team."""
import json
from pathlib import Path
import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = WORKSPACE / "artifacts" / "phase_4k_f_b_security_redteam.json"


@pytest.fixture(scope="module")
def redteam_data():
    assert ARTIFACT_PATH.exists(), f"Phase 4K-F red-team artifact missing: {ARTIFACT_PATH}"
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def test_redteam_metadata(redteam_data):
    meta = redteam_data["metadata"]
    assert meta["phase"] == "PHASE_4K_F_B_SECURITY_REDTEAM"
    assert meta["configuration"] == {
        "enable_boundary_stitching": False,
        "enable_query_aware_authority": True,
        "enable_event_bundling": False,
    }
    assert meta["redteam_case_count"] == 40
    assert meta["canonical_case_count"] == 120
    assert meta["all_security_gates_passed"] is True
    assert len(meta["pipeline_security_order"]) == 8


def test_redteam_required_metrics(redteam_data):
    m = redteam_data["required_metrics"]
    assert m["unauthorized_exposure_count"] == 0
    assert m["cross_tenant_leakage_count"] == 0
    assert m["adversarial_bypass_count"] == 0
    assert m["restricted_exposure_count"] == 0
    assert m["stale_resurrection_count"] == 0
    assert m["superseded_resurrection_count"] == 0
    assert m["invalid_citation_count"] == 0
    assert m["citation_spoofing_count"] == 0
    assert m["source_intent_false_positive_count"] == 0
    assert m["source_intent_false_negative_count"] == 0


def test_redteam_promotion_security_gates(redteam_data):
    gates = redteam_data["promotion_security_gates"]
    assert gates["unauthorized_eq_0"]["passed"] is True
    assert gates["cross_tenant_eq_0"]["passed"] is True
    assert gates["adversarial_bypass_eq_0"]["passed"] is True
    assert gates["restricted_exposure_eq_0"]["passed"] is True
    assert gates["stale_resurrection_eq_0"]["passed"] is True
    assert gates["superseded_resurrection_eq_0"]["passed"] is True
    assert gates["invalid_citations_eq_0"]["passed"] is True
    assert gates["citation_spoofing_eq_0"]["passed"] is True
    assert gates["canonical_cases_unbroken"]["passed"] is True


def test_canonical_120_verification(redteam_data):
    c120 = redteam_data["canonical_120_verification"]
    assert c120["canonical_security_preserved"] is True
    assert c120["negative_safety_count"] == "19/19"
    assert c120["security_violations"] == 0
    assert c120["unauthorized_exposures"] == 0
    assert c120["cross_tenant_leakage"] == 0
    assert c120["adversarial_bypasses"] == 0


def test_redteam_test_classes_coverage(redteam_data):
    cases = redteam_data["redteam_cases"]
    assert len(cases) == 40
    classes = set(c["test_class"] for c in cases)
    assert len(classes) == 10
    for c in cases:
        assert c["passed"] is True, f"Redteam case {c['redteam_id']} failed security audit"
        assert len(c["top3_document_ids"]) <= 3
