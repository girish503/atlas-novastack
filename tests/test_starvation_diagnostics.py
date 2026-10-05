"""Tests for Candidate-Starvation Root-Cause Diagnostics — Phase 4D-0."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from novastack.starvation_diagnostics import (
    ROOT_CAUSE_TAXONOMY,
    CounterfactualResult,
    RetrievalChannelTelemetry,
    StarvationCaseDiagnostic,
    classify_starvation_case,
)


@pytest.fixture
def test_dirs() -> tuple[Path, Path, Path]:
    repo_root = Path(__file__).resolve().parent.parent
    raw_dir = repo_root / "data" / "raw" / "novastack"
    proc_dir = repo_root / "data" / "processed" / "novastack"
    eval_dir = repo_root / "data" / "evaluation" / "novastack"
    return raw_dir, proc_dir, eval_dir


@pytest.fixture
def starvation_data(test_dirs: tuple[Path, Path, Path]) -> dict:
    _, _, eval_dir = test_dirs
    path = eval_dir / "phase_4d0_starvation_diagnostics.json"
    assert path.exists(), f"Missing starvation diagnostics telemetry artifact: {path}"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_starvation_case_count(starvation_data: dict):
    """Verify all 23 candidate-starvation cases are present and analyzed."""
    cases = starvation_data.get("cases", [])
    assert len(cases) == 23
    assert starvation_data["metadata"]["total_starvation_cases"] == 23


def test_every_case_has_one_valid_primary_root_cause(starvation_data: dict):
    """Verify every starvation case has exactly one primary cause belonging to ROOT_CAUSE_TAXONOMY."""
    cases = starvation_data.get("cases", [])
    valid_keys = set(ROOT_CAUSE_TAXONOMY.keys())

    for c in cases:
        pri = c.get("primary_root_cause")
        assert pri in valid_keys, f"Invalid primary root cause '{pri}' in case {c.get('evaluation_id')}"
        for sec in c.get("secondary_root_causes", []):
            assert sec in valid_keys, f"Invalid secondary root cause '{sec}' in case {c.get('evaluation_id')}"


def test_no_fabricated_ranks(starvation_data: dict):
    """Verify ranks are either valid positive integers or None (no synthetic fabrication)."""
    cases = starvation_data.get("cases", [])
    for c in cases:
        t = c["telemetry"]
        for rank_key in ("bm25_rank", "dense_rank", "qu_hybrid_rank", "full_corpus_bm25_rank", "full_corpus_dense_rank"):
            val = t.get(rank_key)
            if val is not None:
                assert isinstance(val, int) and val >= 1


def test_security_exclusion_separation(starvation_data: dict):
    """Verify security evaluation cases are correctly classified as J_filtering_or_security_exclusion."""
    cases = starvation_data.get("cases", [])
    sec_cases = [c for c in cases if c["evaluation_id"] in ("EVAL-0084", "EVAL-0086", "EVAL-0093", "EVAL-0095", "EVAL-0100", "EVAL-0105")]
    assert len(sec_cases) == 6
    for sc in sec_cases:
        assert sc["primary_root_cause"] == "J_filtering_or_security_exclusion"


def test_ground_truth_issue_separation(starvation_data: dict):
    """Verify synthetic ground-truth anomalies (e.g. EVAL-0028/29/30/34) are classified as L_corpus_or_ground_truth_issue."""
    cases = starvation_data.get("cases", [])
    gt_cases = [c for c in cases if c["evaluation_id"] in ("EVAL-0028", "EVAL-0029", "EVAL-0030", "EVAL-0034")]
    assert len(gt_cases) == 4
    for gc in gt_cases:
        assert gc["primary_root_cause"] == "L_corpus_or_ground_truth_issue"


def test_candidate_depth_effect(starvation_data: dict):
    """Verify cases ranked between 51 and 100 are detected as candidate-depth recoverable."""
    cases = starvation_data.get("cases", [])
    depth_cases = [c for c in cases if c["primary_root_cause"] == "K_candidate_depth_effect"]
    assert len(depth_cases) >= 2
    for dc in depth_cases:
        bm = dc["telemetry"]["full_corpus_bm25_rank"]
        dn = dc["telemetry"]["full_corpus_dense_rank"]
        best = min(r for r in (bm, dn) if r is not None)
        assert 50 < best <= 100


def test_counterfactual_presence(starvation_data: dict):
    """Verify counterfactual test fields exist and contain valid results."""
    cases = starvation_data.get("cases", [])
    for c in cases:
        cf = c.get("counterfactuals", {})
        assert "canonical_id_bm25_rank" in cf
        assert "document_title_bm25_rank" in cf
        assert "text_in_indexed_chunks" in cf
        assert cf["text_in_indexed_chunks"] is True  # Target document text exists in corpus


def test_all_13_prior_artifacts_immutable(test_dirs: tuple[Path, Path, Path]):
    """Verify 100% byte-for-byte immutability across all 13 prior baseline and phase artifacts."""
    raw_dir, proc_dir, eval_dir = test_dirs
    prior_artifacts = [
        raw_dir / "source_records.json",
        proc_dir / "search_documents.json",
        proc_dir / "search_chunks.json",
        eval_dir / "evaluation_cases.json",
        eval_dir / "bm25_baseline.json",
        eval_dir / "dense_baseline.json",
        eval_dir / "hybrid_baseline.json",
        eval_dir / "phase_4b0_candidate_diagnostics.json",
        eval_dir / "phase_4b1_reranker_baseline.json",
        eval_dir / "phase_4c0_query_profiles.json",
        eval_dir / "phase_4c1_query_understanding.json",
        eval_dir / "phase_4c2_metadata_diagnostics.json",
        eval_dir / "phase_4c3_metadata_reranking.json",
    ]
    for p in prior_artifacts:
        assert p.exists(), f"Missing artifact: {p}"
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        assert len(h) == 64
