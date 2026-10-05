import json
from pathlib import Path
import pytest

WORKSPACE = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = WORKSPACE / "artifacts" / "phase_4k_d_regression_attribution.json"


@pytest.fixture(scope="module")
def attribution_data():
    assert ARTIFACT_PATH.exists(), f"Attribution artifact missing: {ARTIFACT_PATH}"
    data = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    return data


def test_metadata_and_structure(attribution_data):
    meta = attribution_data["metadata"]
    assert meta["phase"] == "PHASE_4K_D_REGRESSION_ATTRIBUTION"
    assert len(meta["tested_cases"]) == 4
    assert set(meta["tested_cases"]) == {"EVAL-0035", "EVAL-0041", "EVAL-0043", "EVAL-0075"}
    assert len(meta["tested_configurations"]) == 8
    assert set(meta["tested_configurations"]) == {
        "CONTROL", "A", "B", "C", "A+B", "A+C", "B+C", "A+B+C"
    }


def test_runs_completeness_and_required_fields(attribution_data):
    runs = attribution_data["runs"]
    assert len(runs) == 32, f"Expected 32 runs, got {len(runs)}"

    required_fields = [
        "evaluation_id",
        "configuration",
        "answer_status",
        "answer",
        "top3_document_ids",
        "selected_evidence_ids",
        "citations",
        "citation_validity",
        "failure_category",
        "stitch_count",
        "bundle_created",
        "bundle_documents",
        "authority_changes",
        "latency_ms",
    ]

    for run in runs:
        for field in required_fields:
            assert field in run, f"Run {run.get('evaluation_id')} {run.get('configuration')} missing field {field}"
        assert run["answer_status"] in ("answered", "abstained", "denied")
        assert run["failure_category"] in ("none", "insufficient_evidence", "unsupported_claim", "unauthorized")
        assert isinstance(run["top3_document_ids"], list)
        assert isinstance(run["citations"], list)
        assert isinstance(run["stitch_count"], int)
        assert isinstance(run["bundle_created"], bool)


def test_control_and_unified_invariants(attribution_data):
    causal = attribution_data["causal_attributions"]
    for eid in ["EVAL-0035", "EVAL-0041", "EVAL-0043", "EVAL-0075"]:
        tt = causal[eid]["truth_table"]
        assert tt["CONTROL"] is True, f"Control must succeed for regression case {eid}"
        assert tt["A+B+C"] is False, f"Unified treatment A+B+C must fail for regression case {eid}"


def test_causal_attributions(attribution_data):
    causal = attribution_data["causal_attributions"]

    # EVAL-0035: A+C interaction
    assert causal["EVAL-0035"]["causal_attribution"] == "A+C interaction"
    tt_35 = causal["EVAL-0035"]["truth_table"]
    assert tt_35["CONTROL"] is True
    assert tt_35["A"] is True
    assert tt_35["B"] is True
    assert tt_35["C"] is True
    assert tt_35["A+B"] is True
    assert tt_35["A+C"] is False
    assert tt_35["B+C"] is True
    assert tt_35["A+B+C"] is False

    # EVAL-0041: A-only
    assert causal["EVAL-0041"]["causal_attribution"] == "A-only"
    tt_41 = causal["EVAL-0041"]["truth_table"]
    assert tt_41["CONTROL"] is True
    assert tt_41["A"] is False
    assert tt_41["B"] is True
    assert tt_41["C"] is True
    assert tt_41["A+B"] is False
    assert tt_41["A+C"] is False
    assert tt_41["B+C"] is True
    assert tt_41["A+B+C"] is False

    # EVAL-0043: C-only
    assert causal["EVAL-0043"]["causal_attribution"] == "C-only"
    tt_43 = causal["EVAL-0043"]["truth_table"]
    assert tt_43["CONTROL"] is True
    assert tt_43["A"] is True
    assert tt_43["B"] is True
    assert tt_43["C"] is False
    assert tt_43["A+B"] is True
    assert tt_43["A+C"] is False
    assert tt_43["B+C"] is False
    assert tt_43["A+B+C"] is False

    # EVAL-0075: A-only
    assert causal["EVAL-0075"]["causal_attribution"] == "A-only"
    tt_75 = causal["EVAL-0075"]["truth_table"]
    assert tt_75["CONTROL"] is True
    assert tt_75["A"] is False
    assert tt_75["B"] is True
    assert tt_75["C"] is True
    assert tt_75["A+B"] is False
    assert tt_75["A+C"] is False
    assert tt_75["B+C"] is True
    assert tt_75["A+B+C"] is False


def test_mechanism_b_innocence(attribution_data):
    """Verify Mechanism B (Query-Aware Authority) caused zero regressions."""
    causal = attribution_data["causal_attributions"]
    for eid, info in causal.items():
        tt = info["truth_table"]
        assert tt["B"] is True, f"Mechanism B alone must not fail on {eid}"
        assert "B-only" != info["causal_attribution"], f"Mechanism B cannot be the sole cause of {eid}"
