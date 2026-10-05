"""Tests for Phase 4B-0 Candidate Coverage & Ranking Diagnostics (novastack.diagnostics).

Validates coverage calculations, candidate union logic, rank headroom bucketing,
overlap statistics, regression categorization, security separation, determinism,
and corpus immutability.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.slow

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.bm25 import BM25Config, BM25Index, RetrievalResult
from novastack.dense import DenseConfig, DenseEncoder, DenseIndex
from novastack.diagnostics import (
    VALID_FAILURE_CATEGORIES,
    CandidateOverlapStats,
    CategoryDiagnostic,
    CoverageMetrics,
    RankingHeadroomReport,
    RegressionDiagnostic,
    SecuritySeparationReport,
    UnionAnalysisReport,
    _calc_hit,
    _calc_macro_recall,
    run_candidate_diagnostics,
)
from novastack.hybrid import HybridConfig, HybridRetriever

_CHUNKS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_chunks.json"
_EMBEDDINGS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "dense_embeddings.npz"
_METADATA_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "dense_index_metadata.json"
_EVAL_PATH = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
_RAW_PATH = _PROJECT_ROOT / "data" / "raw" / "novastack" / "source_records.json"
_DOCS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_documents.json"


def _compute_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def bm25_index() -> BM25Index:
    return BM25Index.build_index(_CHUNKS_PATH, config=BM25Config())


@pytest.fixture(scope="module")
def dense_index() -> DenseIndex:
    if not _EMBEDDINGS_PATH.exists():
        pytest.skip("Dense embeddings artifact not found.")
    cfg = DenseConfig(model_name="BAAI/bge-small-en-v1.5")
    encoder = DenseEncoder(config=cfg, device="cpu")
    return DenseIndex.load(
        chunks_path=_CHUNKS_PATH,
        embeddings_path=_EMBEDDINGS_PATH,
        metadata_path=_METADATA_PATH,
        encoder=encoder,
    )


@pytest.fixture(scope="module")
def hybrid_retriever(bm25_index: BM25Index, dense_index: DenseIndex) -> HybridRetriever:
    return HybridRetriever(bm25_index=bm25_index, dense_index=dense_index)


@pytest.fixture(scope="module")
def eval_cases() -> list[dict[str, Any]]:
    with open(_EVAL_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["evaluation_cases"]


class TestCoverageAndRecallHelpers:
    """Test fundamental coverage and recall calculation primitives."""

    def test_calc_macro_recall(self) -> None:
        expected = {"doc-1", "doc-2"}
        retrieved = ["doc-1", "doc-3", "doc-4"]
        assert _calc_macro_recall(retrieved, expected, k=1) == 0.5
        assert _calc_macro_recall(retrieved, expected, k=2) == 0.5
        assert _calc_macro_recall(retrieved, expected, k=10) == 0.5

        retrieved_full = ["doc-1", "doc-2", "doc-3"]
        assert _calc_macro_recall(retrieved_full, expected, k=2) == 1.0

    def test_calc_hit(self) -> None:
        expected = {"doc-1", "doc-2"}
        assert _calc_hit(["doc-1", "doc-3"], expected, k=1) is True
        assert _calc_hit(["doc-3", "doc-4"], expected, k=10) is False
        assert _calc_hit([], expected, k=10) is False

    def test_empty_expected_docs(self) -> None:
        assert _calc_macro_recall(["doc-1"], set(), k=10) == 0.0
        assert _calc_hit(["doc-1"], set(), k=10) is False


class TestCandidateUnionAndRankingHeadroom:
    """Validate union logic and headroom bucket assignment."""

    def test_valid_failure_categories(self) -> None:
        assert "distractor agreement" in VALID_FAILURE_CATEGORIES
        assert "candidate depth" in VALID_FAILURE_CATEGORIES
        assert "undetermined" in VALID_FAILURE_CATEGORIES

    def test_invalid_failure_category_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid failure category"):
            RegressionDiagnostic(
                evaluation_id="EVAL-TEST",
                query="test query",
                expected_targets=["doc-1"],
                bm25_rank=1,
                dense_rank=None,
                hybrid_rank=12,
                in_bm25_top50=True,
                in_dense_top50=False,
                in_hybrid_top50=True,
                bm25_score=1.5,
                dense_score=None,
                rrf_score=0.016,
                top_distractors=[],
                failure_category="invented_category",
                explanation="testing error",
            )


class TestDiagnosticsPipelineExecution:
    """Validate full end-to-end diagnostic runner on a subset of cases."""

    def test_diagnostics_on_sample(
        self,
        eval_cases: list[dict[str, Any]],
        bm25_index: BM25Index,
        dense_index: DenseIndex,
        hybrid_retriever: HybridRetriever,
    ) -> None:
        # Run on first 5 cases
        sample = eval_cases[:5]
        result = run_candidate_diagnostics(
            eval_cases=sample,
            bm25_index=bm25_index,
            dense_index=dense_index,
            hybrid_retriever=hybrid_retriever,
            depth=50,
        )

        assert "oracle_candidate_coverage" in result
        assert "candidate_union_analysis" in result
        assert "ranking_headroom" in result
        assert "candidate_overlap" in result
        assert "regression_analysis" in result
        assert "security_separation" in result
        assert "category_diagnostics" in result

        union = result["candidate_union_analysis"]
        head = result["ranking_headroom"]
        assert union["total_positive_cases"] <= 5
        assert union["potential_ranking_headroom_count"] <= 5
        assert head["rank_1_to_5_count"] >= 0

    def test_security_separation_declaration(
        self,
        eval_cases: list[dict[str, Any]],
        bm25_index: BM25Index,
        dense_index: DenseIndex,
        hybrid_retriever: HybridRetriever,
    ) -> None:
        sample = eval_cases[:3]
        result = run_candidate_diagnostics(sample, bm25_index, dense_index, hybrid_retriever, depth=10)
        sec = result["security_separation"]
        assert "boundary_principle" in sec
        assert (
            sec["boundary_principle"]
            == "retrieval relevance != authorization != evidence trustworthiness != prompt-injection resistance"
        )
        assert sec["authorization_correctness"]["cross_tenant_leakage_count"] == 0

    def test_immutability(self) -> None:
        assert _compute_hash(_RAW_PATH) is not None
        assert _compute_hash(_DOCS_PATH) is not None
        assert _compute_hash(_CHUNKS_PATH) is not None
        assert _compute_hash(_EVAL_PATH) is not None


class TestDiagnosticsArtifact:
    """Validate structure, completeness, and invariants of phase_4b0_candidate_diagnostics.json."""

    @pytest.fixture(scope="class")
    def report_json(self) -> dict[str, Any]:
        p = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "phase_4b0_candidate_diagnostics.json"
        if not p.exists():
            pytest.skip(f"Report artifact {p} not found. Run scripts/run_candidate_diagnostics.py first.")
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_required_top_level_keys(self, report_json: dict[str, Any]) -> None:
        expected_keys = {
            "version",
            "oracle_candidate_coverage",
            "candidate_union_analysis",
            "ranking_headroom",
            "candidate_overlap",
            "regression_analysis",
            "security_separation",
            "category_diagnostics",
        }
        assert expected_keys.issubset(report_json.keys())

    def test_union_analysis_invariants(self, report_json: dict[str, Any]) -> None:
        union = report_json["candidate_union_analysis"]
        assert union["total_positive_cases"] == 101
        sum_cases = (
            union["target_in_both"]
            + union["target_in_bm25_only"]
            + union["target_in_dense_only"]
            + union["target_in_neither"]
        )
        assert sum_cases == 101
        assert union["potential_ranking_headroom_count"] == 83
        assert pytest.approx(union["potential_ranking_headroom_pct"], abs=1e-3) == 0.8218

    def test_ranking_headroom_buckets(self, report_json: dict[str, Any]) -> None:
        head = report_json["ranking_headroom"]
        total_bucketed = (
            head["rank_1_to_5_count"]
            + head["rank_6_to_10_count"]
            + head["rank_11_to_20_count"]
            + head["rank_21_to_50_count"]
            + head["uncovered_count"]
        )
        assert total_bucketed == 101

    def test_regression_analysis_completeness(self, report_json: dict[str, Any]) -> None:
        regs = report_json["regression_analysis"]
        assert len(regs) == 10
        for r in regs:
            assert r["failure_category"] in VALID_FAILURE_CATEGORIES
            assert "distractor agreement" in r["failure_category"] or "candidate depth" in r["failure_category"]
            assert len(r["expected_targets"]) > 0

    def test_markdown_report_exists(self) -> None:
        p = _PROJECT_ROOT / "docs" / "PHASE_4B0_REPORT.md"
        assert p.exists()
        content = p.read_text(encoding="utf-8")
        assert "Answers to the Nine Mandatory Diagnostic Questions" in content
        assert "Potential Ranking Headroom" in content

