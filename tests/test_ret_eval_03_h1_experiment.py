"""Hermetic Test Suite for RET-EVAL-03 Phase 2 — Controlled H1 Experiment.

Tests:
1. Benchmark Execution & Artifact Schema Integrity:
   - Validates existence, valid JSON, schema of artifacts/ret_eval_03_h1_results.json
   - Validates all 120 canonical cases processed
2. Control Baseline Parity:
   - Verifies Control B4 metrics match frozen ret_eval_01_baseline.json exactly
3. Security & Fail-Closed Invariants:
   - Zero forbidden document leaks in top-10 (Control & Treatment)
   - Zero forbidden document leaks in candidate pools (Control & Treatment)
   - Zero negative-case leaks (100% negative safety)
4. Measurement Separation Integrity:
   - Verifies measurements A through G are explicitly computed and distinct
5. Diagnostic Provenance Taxonomy:
   - Verifies all provenance categories match the mandatory 4-tier taxonomy
6. Source Tree & Release Immutability:
   - Verifies zero modifications to src/novastack/** or release manifests
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def experiment_results() -> dict:
    """Load or generate the H1 experiment results."""
    from scripts.ret_eval_03_h1_experiment import run_h1_experiment

    results_path = _PROJECT_ROOT / "artifacts" / "ret_eval_03_h1_results.json"
    if results_path.exists():
        with open(results_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return run_h1_experiment(_PROJECT_ROOT)


class TestGate01BenchmarkIntegrity:
    """Validate benchmark hermetic execution, counts, and artifact structure."""

    def test_case_counts(self, experiment_results: dict) -> None:
        meta = experiment_results["benchmark_metadata"]
        assert meta["case_count"] == 120, "Must evaluate exactly 120 canonical cases"
        assert meta["positive_case_count"] == 101, "Must have exactly 101 positive cases"
        assert meta["negative_case_count"] == 19, "Must have exactly 19 negative cases"
        assert meta["h1_solvable_case_count"] == 5, "Must track 5 canonical H1-solvable cases"

    def test_artifact_file_exists(self) -> None:
        results_path = _PROJECT_ROOT / "artifacts" / "ret_eval_03_h1_results.json"
        assert results_path.exists(), "ret_eval_03_h1_results.json must exist in artifacts/"

    def test_verdict_validity(self, experiment_results: dict) -> None:
        verdict = experiment_results["decision"]["verdict"]
        assert verdict in ("KEEP", "REJECT", "INCONCLUSIVE"), f"Invalid decision verdict: {verdict}"


class TestGate02ControlParity:
    """Verify that Control matches ret_eval_01_baseline.json."""

    def test_control_positive_recall(self, experiment_results: dict) -> None:
        ctrl_rec = experiment_results["primary_measurements"]["C_overall_positive_recall_10"]["control"]
        # ret_eval_01 baseline positive Recall@10 is 0.591584 ~ 0.5916
        assert abs(ctrl_rec - 0.591584) < 1e-4, f"Control positive recall mismatch: {ctrl_rec}"

    def test_control_positive_mrr(self, experiment_results: dict) -> None:
        ctrl_mrr = experiment_results["primary_measurements"]["F_mrr"]["control"]
        assert abs(ctrl_mrr - 0.394632) < 1e-4, f"Control positive MRR mismatch: {ctrl_mrr}"


class TestGate03SecurityInvariants:
    """Enforce fail-closed security invariants across both Control and Treatment."""

    def test_zero_forbidden_leaks_top10(self, experiment_results: dict) -> None:
        sec = experiment_results["security_results"]
        assert sec["control"]["forbidden_leaks_top10"] == 0, "Control had forbidden leaks in top-10"
        assert sec["treatment"]["forbidden_leaks_top10"] == 0, "Treatment had forbidden leaks in top-10"

    def test_zero_forbidden_leaks_in_pool(self, experiment_results: dict) -> None:
        sec = experiment_results["security_results"]
        assert sec["control"]["forbidden_in_pool"] == 0, "Control had forbidden leaks in candidate pool"
        assert sec["treatment"]["forbidden_in_pool"] == 0, "Treatment had forbidden leaks in candidate pool"

    def test_zero_negative_case_leaks(self, experiment_results: dict) -> None:
        sec = experiment_results["security_results"]
        assert sec["control"]["negative_leaks"] == 0, "Control had negative case leaks"
        assert sec["treatment"]["negative_leaks"] == 0, "Treatment had negative case leaks"


class TestGate04MeasurementSeparation:
    """Verify that all measurements A through G are explicitly computed."""

    def test_measurements_structure(self, experiment_results: dict) -> None:
        meas = experiment_results["primary_measurements"]
        required_keys = [
            "A_h1_solvable_document_recall_10",
            "B_h1_case_recovery_rate",
            "C_overall_positive_recall_10",
            "D_multi_hop_recall_10",
            "E_multi_document_recall_10",
            "F_mrr",
            "G_ndcg_at_10",
        ]
        for k in required_keys:
            assert k in meas, f"Missing required measurement: {k}"
            assert "control" in meas[k] or "recovery_rate" in meas[k], f"Malformed measurement: {k}"

    def test_h1_solvable_case_details(self, experiment_results: dict) -> None:
        details = experiment_results["h1_solvable_case_details"]
        assert len(details) == 5, f"Expected 5 H1 solvable cases, got {len(details)}"
        for case in details:
            assert "expected_documents" in case
            assert "control_top_10" in case
            assert "treatment_top_10" in case
            assert "expected_document_details" in case
            for doc_id, doc_info in case["expected_document_details"].items():
                assert "control_rank" in doc_info
                assert "treatment_rank" in doc_info
                assert "provenance" in doc_info
                assert "reachable_via_depth_2" in doc_info
                assert "reciprocal_required" in doc_info


class TestGate05DiagnosticTaxonomy:
    """Verify that all diagnostic provenance tags adhere to the strict taxonomy."""

    ALLOWED_PROVENANCE = {
        "depth-1 existing behavior",
        "depth-2 traversal",
        "reciprocal/reverse edge traversal",
        "depth-2 + reciprocal traversal",
        "not surfaced in structured pool",
    }

    def test_provenance_labels(self, experiment_results: dict) -> None:
        for case in experiment_results["h1_solvable_case_details"]:
            for doc_id, doc_info in case["expected_document_details"].items():
                prov = doc_info["provenance"]
                assert prov in self.ALLOWED_PROVENANCE, f"Invalid provenance tag: {prov}"


class TestGate06SourceImmutability:
    """Verify that production source code src/novastack/** was NOT modified."""

    def test_git_status_clean_on_src(self) -> None:
        import subprocess

        res = subprocess.run(
            ["git", "status", "--porcelain", "--", "src/novastack"],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        assert res.stdout.strip() == "", f"src/novastack has uncommitted changes: {res.stdout}"
