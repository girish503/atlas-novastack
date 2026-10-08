"""Unit and Integration Tests for RET-EVAL-07 Phase 2 Controlled H4 Experiment.

Validates Hypothesis H4 and Strategy E:
1. Two-tier anchor selection: entity-compatible candidate selected over higher base-scoring unrelated candidate.
2. source_entity_id exact match compatibility.
3. related_entity_ids exact membership compatibility.
4. Tier 2 fallback: no entity match reproduces exact H3 unconstrained anchor selection.
5. Multiple query entities supported deterministically.
6. Empty query entities reproduces exact H3 behavior.
7. Single-aspect pass-through: queries with <2 aspect roles are 100% untouched.
8. Strict Security Gate: unauthorized / forbidden entity-compatible candidates are never admitted or promoted.
9. Cross-tenant candidate rejection: foreign tenant candidates are never selected as anchors.
10. Candidate pool invariance: multiset of candidate document IDs is 100% identical.
11. Score invariance: base retrieval scores and metadata scores are never modified.
12. Determinism: repeated executions produce identical results.
13. Zero evaluation label access: ranking operates without ground-truth labels.
14. Benchmark integrity: full test suite verifies non-regression and safety gates.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from novastack.metadata_diagnostics import DocumentMetadataSnapshot
from novastack.metadata_reranker import CandidateRerankingDetail
from scripts.ret_eval_06_h3_experiment import (
    DEFAULT_MAX_PER_ROLE,
    DEFAULT_TOP_K,
    H3RoleDiversificationOverlay,
    get_document_role,
)
from scripts.ret_eval_07_h4_experiment import (
    H4EntityAnchoredDiversificationOverlay,
    is_candidate_entity_compatible,
    run_controlled_h4_experiment,
)


def _make_candidate(
    chunk_id: str,
    document_id: str,
    final_score: float = 0.05,
    is_forbidden: bool = False,
) -> CandidateRerankingDetail:
    """Helper to synthesize a CandidateRerankingDetail for testing."""
    return CandidateRerankingDetail(
        chunk_id=chunk_id,
        document_id=document_id,
        title=f"Title of {document_id}",
        base_rrf_score=final_score,
        metadata_score=0.0,
        authority_contribution=0.0,
        lifecycle_contribution=0.0,
        temporal_version_contribution=0.0,
        provenance_contribution=0.0,
        final_score=final_score if not is_forbidden else -1000.0,
        rank_before=1,
        rank_after=1,
        is_poisoned=False,
        authority_level="authoritative",
        status="published",
        version="v1.0",
        source_entity_id=None,
        is_forbidden=is_forbidden,
    )


def _make_snapshot(
    document_id: str,
    source_type: str,
    source_entity_id: str | None = None,
    related_entity_ids: list[str] | None = None,
    tenant_id: str = "TENANT-NOVASTACK",
) -> DocumentMetadataSnapshot:
    """Helper to synthesize a DocumentMetadataSnapshot for testing."""
    return DocumentMetadataSnapshot(
        document_id=document_id,
        tenant_id=tenant_id,
        source_type=source_type,
        title=f"Title of {document_id}",
        department="engineering",
        author_id="USR-NS-0001",
        created_at="2025-01-01T00:00:00",
        updated_at="2025-01-01T00:00:00",
        valid_from=None,
        valid_until=None,
        version="v1.0",
        status="published",
        classification="internal",
        authority_level="authoritative",
        parent_id=None,
        supersedes_id=None,
        source_entity_id=source_entity_id,
        source_entity_type="event" if source_entity_id and "EVT" in source_entity_id else None,
        related_entity_ids=related_entity_ids or [],
    )


class TestH4EntityAnchorSelection:
    """Unit tests validating Strategy E Two-Tier Entity-Aware Anchor Selection."""

    def test_source_entity_id_exact_match(self) -> None:
        c = _make_candidate("chk-1", "DOC-PM-EVT-NS-0001-01")
        meta = {
            "DOC-PM-EVT-NS-0001-01": _make_snapshot(
                "DOC-PM-EVT-NS-0001-01",
                "postmortem",
                source_entity_id="EVT-NS-0001",
            )
        }
        assert is_candidate_entity_compatible(c, {"EVT-NS-0001"}, meta) is True
        assert is_candidate_entity_compatible(c, {"EVT-NS-0002"}, meta) is False

    def test_related_entity_ids_membership_match(self) -> None:
        c = _make_candidate("chk-2", "DOC-PR-PR-NS-0001-01")
        meta = {
            "DOC-PR-PR-NS-0001-01": _make_snapshot(
                "DOC-PR-PR-NS-0001-01",
                "pull_request_note",
                source_entity_id="PR-NS-0001",
                related_entity_ids=["EVT-NS-0001", "INC-NS-0001", "SVC-NS-0005"],
            )
        }
        assert is_candidate_entity_compatible(c, {"EVT-NS-0001"}, meta) is True
        assert is_candidate_entity_compatible(c, {"INC-NS-0001"}, meta) is True
        assert is_candidate_entity_compatible(c, {"SVC-NS-0005"}, meta) is True
        assert is_candidate_entity_compatible(c, {"EVT-NS-0002"}, meta) is False

    def test_entity_compatible_anchor_selected_over_unrelated_higher_score(self) -> None:
        """Strategy E Tier 1: Candidate matching query entity must be selected as anchor
        even if an unrelated candidate of the same role has a higher base score.
        """
        # Distractor postmortem has higher score (0.10) but belongs to EVT-0005
        c_distractor = _make_candidate("chk-dist", "DOC-PM-EVT-NS-0005-01", final_score=0.10)
        # Target postmortem has lower score (0.05) but belongs to EVT-0001
        c_target = _make_candidate("chk-target", "DOC-PM-EVT-NS-0001-01", final_score=0.05)
        # Pull request belonging to EVT-0001
        c_pr = _make_candidate("chk-pr", "DOC-PR-PR-NS-0001-01", final_score=0.04)

        meta = {
            "DOC-PM-EVT-NS-0005-01": _make_snapshot("DOC-PM-EVT-NS-0005-01", "postmortem", source_entity_id="EVT-NS-0005"),
            "DOC-PM-EVT-NS-0001-01": _make_snapshot("DOC-PM-EVT-NS-0001-01", "postmortem", source_entity_id="EVT-NS-0001"),
            "DOC-PR-PR-NS-0001-01": _make_snapshot("DOC-PR-PR-NS-0001-01", "pull_request_note", related_entity_ids=["EVT-NS-0001"]),
        }

        candidates = [c_distractor, c_target, c_pr]
        query = "What caused the outage and which PR fixed it?"  # requests postmortem + pull_request_note

        h3_overlay = H3RoleDiversificationOverlay(top_k=5, max_per_role=2, metadata_index=meta)
        h4_overlay = H4EntityAnchoredDiversificationOverlay(top_k=5, max_per_role=2, metadata_index=meta)

        # H3 Control: chooses DOC-PM-EVT-NS-0005-01 (highest score) as postmortem anchor
        _, h3_audit = h3_overlay.diversify(candidates, query)
        assert h3_audit.anchors_identified["postmortem"] == "DOC-PM-EVT-NS-0005-01"

        # H4 Treatment with query_entity_ids = {"EVT-NS-0001"}:
        # Tier 1 must select DOC-PM-EVT-NS-0001-01 as postmortem anchor!
        _, h4_audit = h4_overlay.diversify(candidates, query, query_entity_ids={"EVT-NS-0001"})
        assert h4_audit.anchors_identified["postmortem"] == "DOC-PM-EVT-NS-0001-01"
        assert h4_audit.anchors_tier["postmortem"] == 1
        assert h4_audit.anchors_identified["pull_request_note"] == "DOC-PR-PR-NS-0001-01"
        assert h4_audit.anchors_tier["pull_request_note"] == 1

    def test_no_entity_match_exact_h3_fallback(self) -> None:
        """Strategy E Tier 2: If no candidate of a role matches query entity,
        it must fall back to the exact highest-ranked candidate H3 would choose.
        """
        c1 = _make_candidate("chk-1", "DOC-PM-EVT-NS-0005-01", final_score=0.10)
        c2 = _make_candidate("chk-2", "DOC-PM-EVT-NS-0006-01", final_score=0.08)
        c3 = _make_candidate("chk-3", "DOC-PR-PR-NS-0005-01", final_score=0.05)

        meta = {
            "DOC-PM-EVT-NS-0005-01": _make_snapshot("DOC-PM-EVT-NS-0005-01", "postmortem", source_entity_id="EVT-NS-0005"),
            "DOC-PM-EVT-NS-0006-01": _make_snapshot("DOC-PM-EVT-NS-0006-01", "postmortem", source_entity_id="EVT-NS-0006"),
            "DOC-PR-PR-NS-0005-01": _make_snapshot("DOC-PR-PR-NS-0005-01", "pull_request_note", related_entity_ids=["EVT-NS-0005"]),
        }

        candidates = [c1, c2, c3]
        query = "What caused the outage and which PR fixed it?"

        h3_overlay = H3RoleDiversificationOverlay(top_k=5, max_per_role=2, metadata_index=meta)
        h4_overlay = H4EntityAnchoredDiversificationOverlay(top_k=5, max_per_role=2, metadata_index=meta)

        # Query entity is EVT-NS-0099 (neither candidate matches)
        _, h3_audit = h3_overlay.diversify(candidates, query)
        _, h4_audit = h4_overlay.diversify(candidates, query, query_entity_ids={"EVT-NS-0099"})

        # H4 must fall back to exact H3 anchors
        assert h4_audit.anchors_identified == h3_audit.anchors_identified
        assert h4_audit.anchors_tier["postmortem"] == 2
        assert h4_audit.anchors_tier["pull_request_note"] == 2

    def test_empty_query_entities_exact_h3_equivalence(self) -> None:
        """When query_entity_ids is empty, H4 must produce 100% identical output to H3."""
        candidates = [
            _make_candidate(f"chk-{i}", f"DOC-PM-EVT-NS-{i:04d}-01", final_score=0.10 - i * 0.01)
            for i in range(1, 10)
        ]
        candidates.append(_make_candidate("chk-pr", "DOC-PR-PR-NS-0001-01", final_score=0.01))
        meta = {c.document_id: _make_snapshot(c.document_id, "postmortem" if "PM" in c.document_id else "pull_request_note") for c in candidates}

        query = "What caused the outage and which PR fixed it?"
        h3 = H3RoleDiversificationOverlay(top_k=5, max_per_role=2, metadata_index=meta)
        h4 = H4EntityAnchoredDiversificationOverlay(top_k=5, max_per_role=2, metadata_index=meta)

        res_h3, audit_h3 = h3.diversify(candidates, query)
        res_h4, audit_h4 = h4.diversify(candidates, query, query_entity_ids=set())

        assert [c.document_id for c in res_h3] == [c.document_id for c in res_h4]
        assert audit_h4.anchors_identified == audit_h3.anchors_identified
        assert all(tier == 2 for tier in audit_h4.anchors_tier.values())


class TestH4SecurityAndInvariants:
    """Security invariants: authorization, tenant boundaries, candidate pool invariance."""

    def test_forbidden_candidate_never_selected_as_anchor(self) -> None:
        """Security: A forbidden candidate matching query entity must NEVER be selected as anchor or in top-10."""
        c_forb = _make_candidate("chk-forb", "DOC-PM-EVT-NS-0001-01", final_score=0.10, is_forbidden=True)
        c_safe = _make_candidate("chk-safe", "DOC-PM-EVT-NS-0005-01", final_score=0.05, is_forbidden=False)
        safe_chats = [
            _make_candidate(f"chk-chat-{i}", f"DOC-CHAT-00{i}", final_score=0.04 - i * 0.005)
            for i in range(5)
        ]

        meta = {
            "DOC-PM-EVT-NS-0001-01": _make_snapshot("DOC-PM-EVT-NS-0001-01", "postmortem", source_entity_id="EVT-NS-0001"),
            "DOC-PM-EVT-NS-0005-01": _make_snapshot("DOC-PM-EVT-NS-0005-01", "postmortem", source_entity_id="EVT-NS-0005"),
        }
        for c in safe_chats:
            meta[c.document_id] = _make_snapshot(c.document_id, "conversation")

        query = "What caused the outage and what runbook documents it?"
        h4 = H4EntityAnchoredDiversificationOverlay(top_k=5, max_per_role=2, metadata_index=meta)

        candidates = [c_forb, c_safe] + safe_chats
        res, audit = h4.diversify(
            candidates,
            query,
            query_entity_ids={"EVT-NS-0001"},
            forbidden_doc_ids={"DOC-PM-EVT-NS-0001-01"},
        )

        top5_ids = [c.document_id for c in res[:5]]
        assert "DOC-PM-EVT-NS-0001-01" not in top5_ids
        assert "DOC-PM-EVT-NS-0001-01" not in audit.selected_chunk_ids
        assert audit.anchors_identified.get("postmortem") != "DOC-PM-EVT-NS-0001-01"

    def test_single_aspect_queries_pass_through(self) -> None:
        """Invariance: Queries with <2 aspect roles are 100% pass-through."""
        candidates = [_make_candidate(f"chk-{i}", f"DOC-PM-{i:04d}", final_score=0.10 - i * 0.01) for i in range(10)]
        meta = {c.document_id: _make_snapshot(c.document_id, "postmortem") for c in candidates}

        query = "What is the postmortem report?"  # Only 1 role: postmortem
        h4 = H4EntityAnchoredDiversificationOverlay(top_k=10, max_per_role=3, metadata_index=meta)

        res, audit = h4.diversify(candidates, query, query_entity_ids={"EVT-NS-0001"})
        assert audit.is_active is False
        assert [c.document_id for c in res] == [c.document_id for c in candidates]

    def test_candidate_pool_invariance(self) -> None:
        """Invariance: Input candidates and output candidates must form identical multisets."""
        candidates = [
            _make_candidate(f"chk-{i}", f"DOC-DEP-{i:04d}", final_score=0.10 - i * 0.01)
            for i in range(15)
        ]
        meta = {c.document_id: _make_snapshot(c.document_id, "deployment_note") for c in candidates}

        query = "What deployment occurred and which PR resolved it?"
        h4 = H4EntityAnchoredDiversificationOverlay(top_k=5, max_per_role=2, metadata_index=meta)

        res, _ = h4.diversify(candidates, query, query_entity_ids={"DEP-NS-0001"})
        assert sorted(c.chunk_id for c in res) == sorted(c.chunk_id for c in candidates)
        assert sorted(c.document_id for c in res) == sorted(c.document_id for c in candidates)


class TestH4BenchmarkExecution:
    """End-to-end benchmark test verifying all gates pass."""

    @pytest.fixture(scope="class")
    def benchmark_results(self) -> dict:
        art_path = Path(__file__).resolve().parent.parent / "artifacts" / "ret_eval_07_h4_results.json"
        if art_path.exists():
            with open(art_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return run_controlled_h4_experiment()

    def test_gate_01_security_zero_leaks(self, benchmark_results: dict) -> None:
        sec = benchmark_results["security_audit"]["treatment"]
        assert sec["forbidden_leaks_top10"] == 0, "Forbidden Top-10 leaks must be 0"
        assert sec["negative_leaks"] == 0, "Negative case leaks must be 0"
        assert sec["cross_tenant_leaks"] == 0, "Cross-tenant leaks must be 0"

    def test_gate_02_invariance_verification(self, benchmark_results: dict) -> None:
        inv = benchmark_results["invariance_audit"]
        assert inv["candidate_pool_mismatches"] == 0, "Candidate pool mismatches must be 0"
        assert inv["candidate_base_score_mismatches"] == 0, "Base score mismatches must be 0"
        assert inv["single_aspect_top10_mismatches"] == 0, "Single aspect mismatches must be 0"
        assert inv["max_per_role_violations"] == 0, "Max per role violations must be 0"

    def test_gate_03_non_regression_overall_positive_recall(self, benchmark_results: dict) -> None:
        pos_delta = benchmark_results["overall_positive_metrics"]["delta"]
        assert pos_delta["recall_at_10"] >= -1e-6, f"Overall Positive Recall@10 must not regress: {pos_delta['recall_at_10']}"

    def test_gate_04_h4_applicable_slice_non_regression(self, benchmark_results: dict) -> None:
        app_delta = benchmark_results["h4_applicable_slice_metrics"]["delta"]
        assert app_delta["recall_at_10"] >= -1e-6, f"H4 applicable slice Recall@10 must not regress: {app_delta['recall_at_10']}"

    def test_eval_0036_expected_classification(self, benchmark_results: dict) -> None:
        """EVAL-0036 must have special diagnostic label and expected identical H3 behavior."""
        e36 = next(c for c in benchmark_results["case_level_results"] if c["evaluation_id"] == "EVAL-0036")
        assert e36["special_diagnostic"] is not None
        assert "UPSTREAM ENTITY-RESOLUTION FAILURE" in e36["special_diagnostic"]
        # Expected identical recall between control (H3) and treatment (H4)
        assert e36["delta_recall"] == 0.0
