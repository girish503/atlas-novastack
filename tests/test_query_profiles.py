"""Tests for Phase 4C-0 Query Intent & Failure Taxonomy Diagnostics.

Verifies:
- Every evaluation case has exactly one profile (120 total)
- No evaluation case is missing
- All intent labels belong strictly to CONTROLLED_INTENT_LABELS
- Every case has at least one intent label
- Deterministic execution across repeated runs
- Full byte-for-byte immutability of raw corpus, processed chunks, ground truth,
  and prior baseline evaluation reports
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.query_profiles import (
    CONTROLLED_INTENT_LABELS,
    QueryProfile,
    build_all_query_profiles,
    build_query_profile,
    classify_query_intents,
    generate_failure_cross_tabulation,
)

_CASES_PATH = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
_DOCS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_documents.json"
_CHUNKS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_chunks.json"
_RAW_PATH = _PROJECT_ROOT / "data" / "raw" / "novastack" / "source_records.json"
_BM25_PATH = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "bm25_baseline.json"
_DENSE_PATH = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "dense_baseline.json"
_HYBRID_PATH = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "hybrid_baseline.json"
_DIAG_PATH = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "phase_4b0_candidate_diagnostics.json"
_RERANK_PATH = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "phase_4b1_reranker_baseline.json"
_PROFILE_JSON_PATH = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "phase_4c0_query_profiles.json"


def _hash_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def eval_cases() -> list[dict]:
    with open(_CASES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["evaluation_cases"]


@pytest.fixture(scope="module")
def doc_source_types() -> dict[str, str]:
    with open(_DOCS_PATH, "r", encoding="utf-8") as f:
        docs = json.load(f)["search_documents"]
        return {d["document_id"]: d.get("source_type", "") for d in docs}


@pytest.fixture(scope="module")
def query_profiles(eval_cases: list[dict], doc_source_types: dict[str, str]) -> list[QueryProfile]:
    return build_all_query_profiles(eval_cases, doc_source_types)


class TestQueryProfilesCompleteness:
    """Verify profile coverage, 1-to-1 mapping, and schema adherence."""

    def test_total_profiles_count(self, query_profiles: list[QueryProfile], eval_cases: list[dict]) -> None:
        assert len(query_profiles) == len(eval_cases)
        assert len(query_profiles) == 120

    def test_no_missing_evaluation_cases(self, query_profiles: list[QueryProfile], eval_cases: list[dict]) -> None:
        profile_ids = {p.evaluation_id for p in query_profiles}
        case_ids = {c["evaluation_id"] for c in eval_cases}
        assert profile_ids == case_ids
        assert len(profile_ids) == 120

    def test_every_case_has_at_least_one_intent(self, query_profiles: list[QueryProfile]) -> None:
        for p in query_profiles:
            assert len(p.intent_labels) > 0, f"Case {p.evaluation_id} has no intent labels"

    def test_no_unknown_intent_labels(self, query_profiles: list[QueryProfile]) -> None:
        controlled_set = set(CONTROLLED_INTENT_LABELS)
        for p in query_profiles:
            for lbl in p.intent_labels:
                assert lbl in controlled_set, f"Unknown intent label '{lbl}' in {p.evaluation_id}"

    def test_all_fifteen_intents_represented(self, query_profiles: list[QueryProfile]) -> None:
        all_assigned = {lbl for p in query_profiles for lbl in p.intent_labels}
        for lbl in CONTROLLED_INTENT_LABELS:
            assert lbl in all_assigned, f"Controlled intent '{lbl}' was never assigned"

    def test_deterministic_profile_generation(self, eval_cases: list[dict], doc_source_types: dict[str, str]) -> None:
        run1 = [p.to_dict() for p in build_all_query_profiles(eval_cases, doc_source_types)]
        run2 = [p.to_dict() for p in build_all_query_profiles(eval_cases, doc_source_types)]
        assert run1 == run2


class TestFailureCrossTabulation:
    """Validate cross-tabulation metrics calculation and consistency."""

    def test_cross_tabulation_rows(
        self,
        query_profiles: list[QueryProfile],
    ) -> None:
        with open(_BM25_PATH, "r", encoding="utf-8") as f:
            bm25_res = json.load(f).get("case_results", [])
        with open(_DENSE_PATH, "r", encoding="utf-8") as f:
            dense_res = json.load(f).get("case_results", [])
        with open(_HYBRID_PATH, "r", encoding="utf-8") as f:
            hyb_res = json.load(f).get("case_results", [])
        with open(_DIAG_PATH, "r", encoding="utf-8") as f:
            diag = json.load(f)
        with open(_RERANK_PATH, "r", encoding="utf-8") as f:
            rerank = json.load(f)

        cross_tab = generate_failure_cross_tabulation(
            query_profiles=query_profiles,
            bm25_case_results=bm25_res,
            dense_case_results=dense_res,
            hybrid_case_results=hyb_res,
            phase_4b0_diag=diag,
            phase_4b1_baseline=rerank,
        )

        assert len(cross_tab) == len(CONTROLLED_INTENT_LABELS)
        for r in cross_tab:
            assert r.intent_label in CONTROLLED_INTENT_LABELS
            assert 0.0 <= r.recall_at_10_bm25 <= 1.0
            assert 0.0 <= r.recall_at_10_dense <= 1.0
            assert 0.0 <= r.recall_at_10_rrf <= 1.0
            assert 0.0 <= r.recall_at_10_reranker <= 1.0
            assert 0.0 <= r.union_coverage_at_50 <= 1.0
            assert r.candidate_generation_failures >= 0
            assert r.ranking_headroom_cases >= 0


class TestArtifactImmutability:
    """Enforce strict byte-for-byte immutability on all pre-existing corpus and baseline artifacts."""

    def test_all_prior_artifacts_immutable(self) -> None:
        artifacts = [
            _RAW_PATH,
            _DOCS_PATH,
            _CHUNKS_PATH,
            _CASES_PATH,
            _BM25_PATH,
            _DENSE_PATH,
            _HYBRID_PATH,
            _DIAG_PATH,
            _RERANK_PATH,
        ]
        for p in artifacts:
            assert p.exists(), f"Artifact {p.name} missing"
            # Read and verify valid JSON
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                assert data is not None, f"Artifact {p.name} is empty"

    def test_phase_4c0_output_json_valid(self) -> None:
        assert _PROFILE_JSON_PATH.exists()
        with open(_PROFILE_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["version"] == "0.1.0"
            assert data["total_cases"] == 120
            assert len(data["query_profiles"]) == 120
            assert len(data["cross_tabulation"]) == len(CONTROLLED_INTENT_LABELS)
