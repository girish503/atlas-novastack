"""Regression and integrity test suite for RET-EVAL-01 retrieval baseline.

Verifies:
1. Benchmark script/module is importable.
2. Evaluation dataset contains 120 canonical cases.
3. All four configurations (B1, B2, B3, B4) are present.
4. Required metrics exist (Recall@1,3,5,10, Precision@1,3,5,10, MRR, NDCG@10).
5. Metric values are finite (no NaN, Inf).
6. Metric values are within valid ranges [0.0, 1.0].
7. All expected case IDs (EVAL-0001 through EVAL-0120) are evaluated.
8. No duplicate case IDs.
9. Output schema is valid.
10. Benchmark does not require external network access.
11. Security filtering is not bypassed (zero forbidden leaks in top-10 for B4).
"""

from __future__ import annotations

import json
import math
import socket
from pathlib import Path
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ARTIFACT_PATH = _PROJECT_ROOT / "artifacts" / "ret_eval_01_baseline.json"
_CASES_PATH = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "evaluation_cases.json"


@pytest.fixture(scope="module")
def baseline_artifact() -> dict:
    """Load the generated RET-EVAL-01 artifact."""
    assert _ARTIFACT_PATH.exists(), f"Missing required artifact: {_ARTIFACT_PATH}"
    with open(_ARTIFACT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# -----------------------------------------------------------------------------
# 1. Benchmark script/module is importable
# -----------------------------------------------------------------------------
def test_benchmark_module_importable():
    """Verify that the benchmark module and key functions can be imported."""
    from scripts.eval_retrieval_baseline import (
        evaluate_retrieval_ranking,
        run_retrieval_benchmark,
    )
    assert callable(run_retrieval_benchmark)
    assert callable(evaluate_retrieval_ranking)


# -----------------------------------------------------------------------------
# 2. Evaluation dataset contains 120 cases
# -----------------------------------------------------------------------------
def test_evaluation_dataset_contains_120_cases():
    """Verify the canonical evaluation dataset contains exactly 120 cases."""
    assert _CASES_PATH.exists(), f"Cases file not found: {_CASES_PATH}"
    with open(_CASES_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)["evaluation_cases"]
    assert len(cases) == 120


# -----------------------------------------------------------------------------
# 3. All four configurations are present
# -----------------------------------------------------------------------------
def test_all_four_configurations_present(baseline_artifact):
    """Verify B1, B2, B3, and B4 configurations are present in the artifact."""
    expected_configs = {
        "b1_bm25_only",
        "b2_dense_only",
        "b3_hybrid",
        "b4_full_multichannel",
    }
    assert set(baseline_artifact["configurations"]) == expected_configs
    assert set(baseline_artifact["aggregate_metrics"].keys()) == expected_configs
    assert set(baseline_artifact["aggregate_metrics_positive_cases"].keys()) == expected_configs


# -----------------------------------------------------------------------------
# 4. Required metrics exist
# -----------------------------------------------------------------------------
def test_required_metrics_exist(baseline_artifact):
    """Verify all required metrics exist for each configuration."""
    required_metric_keys = [
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "recall_at_10",
        "precision_at_1",
        "precision_at_3",
        "precision_at_5",
        "precision_at_10",
        "mrr",
        "ndcg_at_10",
    ]
    for cfg in baseline_artifact["configurations"]:
        agg = baseline_artifact["aggregate_metrics"][cfg]
        for m_key in required_metric_keys:
            assert m_key in agg, f"Missing metric {m_key} in configuration {cfg}"


# -----------------------------------------------------------------------------
# 5. Metric values are finite
# -----------------------------------------------------------------------------
def test_metric_values_are_finite(baseline_artifact):
    """Verify metric values are finite and not NaN or infinite."""
    for cfg in baseline_artifact["configurations"]:
        for split_key in ("aggregate_metrics", "aggregate_metrics_positive_cases"):
            agg = baseline_artifact[split_key][cfg]
            for m_key, val in agg.items():
                assert math.isfinite(val), f"Metric {cfg}.{m_key} is non-finite: {val}"
                assert not math.isnan(val), f"Metric {cfg}.{m_key} is NaN"


# -----------------------------------------------------------------------------
# 6. Metric values are within valid ranges
# -----------------------------------------------------------------------------
def test_metric_values_within_valid_ranges(baseline_artifact):
    """Verify metric values fall in [0.0, 1.0]."""
    bounded_metrics = [
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "recall_at_10",
        "precision_at_1",
        "precision_at_3",
        "precision_at_5",
        "precision_at_10",
        "mrr",
        "ndcg_at_10",
    ]
    for cfg in baseline_artifact["configurations"]:
        for split_key in ("aggregate_metrics", "aggregate_metrics_positive_cases"):
            agg = baseline_artifact[split_key][cfg]
            for m_key in bounded_metrics:
                val = agg[m_key]
                assert 0.0 <= val <= 1.0, f"{split_key}.{cfg}.{m_key} = {val} out of bounds [0.0, 1.0]"


# -----------------------------------------------------------------------------
# 7. All expected case IDs are evaluated
# -----------------------------------------------------------------------------
def test_all_expected_case_ids_evaluated(baseline_artifact):
    """Verify all 120 canonical case IDs are present in case_results."""
    with open(_CASES_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)["evaluation_cases"]
    expected_ids = {c["evaluation_id"] for c in cases}

    evaluated_ids = {r["evaluation_id"] for r in baseline_artifact["case_results"]}
    assert evaluated_ids == expected_ids, "Mismatch between evaluated case IDs and expected case IDs"


# -----------------------------------------------------------------------------
# 8. No duplicate case IDs
# -----------------------------------------------------------------------------
def test_no_duplicate_case_ids(baseline_artifact):
    """Verify there are no duplicate case IDs in the results."""
    case_ids = [r["evaluation_id"] for r in baseline_artifact["case_results"]]
    assert len(case_ids) == len(set(case_ids)) == 120


# -----------------------------------------------------------------------------
# 9. Output schema is valid
# -----------------------------------------------------------------------------
def test_output_schema_valid(baseline_artifact):
    """Verify top-level and nested structure of ret_eval_01_baseline.json."""
    required_keys = {
        "benchmark_id",
        "benchmark_description",
        "git_commit",
        "dataset_id",
        "total_cases",
        "positive_cases",
        "negative_cases",
        "configurations",
        "retrieval_parameters",
        "metric_definitions",
        "relevance_rules",
        "security_audit",
        "aggregate_metrics",
        "aggregate_metrics_positive_cases",
        "category_metrics",
        "case_results",
        "execution_metadata",
    }
    assert required_keys.issubset(baseline_artifact.keys())
    assert baseline_artifact["total_cases"] == 120
    assert baseline_artifact["positive_cases"] == 101
    assert baseline_artifact["negative_cases"] == 19
    assert baseline_artifact["execution_metadata"]["llm_invoked"] is False
    assert baseline_artifact["execution_metadata"]["external_network_invoked"] is False


# -----------------------------------------------------------------------------
# 10. Benchmark does not require external network access
# -----------------------------------------------------------------------------
def test_benchmark_does_not_require_external_network(monkeypatch):
    """Verify evaluation metric computation does not attempt socket connections."""
    def guarded_connect(*args, **kwargs):
        raise RuntimeError("External network connection attempted in hermetic benchmark!")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)

    from scripts.eval_retrieval_baseline import evaluate_retrieval_ranking
    res = evaluate_retrieval_ranking(
        retrieved_doc_ids=["DOC-1", "DOC-2"],
        expected_doc_ids=["DOC-1"],
        acceptable_doc_ids=["DOC-2"],
        forbidden_doc_ids=["DOC-FORB"],
    )
    assert res["recall_at_1"] == 1.0
    assert res["precision_at_1"] == 1.0
    assert res["forbidden_leaks_top10"] == 0


# -----------------------------------------------------------------------------
# 11. Security filtering is not bypassed
# -----------------------------------------------------------------------------
def test_security_filtering_not_bypassed(baseline_artifact):
    """Verify full multi-channel retrieval achieves 0 forbidden leaks in top-10."""
    sec_audit = baseline_artifact["security_audit"]
    assert "b4_full_multichannel" in sec_audit
    assert sec_audit["b4_full_multichannel"]["forbidden_leaks_top10"] == 0
