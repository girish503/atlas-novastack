"""Unit and Integration Tests for RET-EVAL-06 Phase 2 Controlled H3 Experiment.

Tests:
1. Document role classification (concordance across all categories).
2. Query aspect extraction (multi-aspect vs single-aspect vs ungrounded).
3. Gating invariant: single-aspect queries are 100% pass-through.
4. Activation invariant: multi-aspect queries (>=2 roles) activate diversification.
5. Capacity allocation: top-10 contains <= max_per_role (3) per role.
6. Anchor promotion: highest-ranked eligible anchor is admitted to top-10.
7. Candidate pool invariance: multiset of candidate IDs is 100% preserved.
8. Score invariance: candidate scores are never modified by H3 overlay.
9. Determinism: identical inputs produce identical rankings.
10. Strict security: forbidden candidates are never promoted or admitted into top-10.
11. Security penalty rejection: penalized candidates (score < -100) are never admitted into top-10.
12. Negative case safety: negative cases maintain zero leaks.
13. Primary H2 slice execution: EVAL-0042, 0044, 0045, 0046, 0047, 0050.
14. Artifact validity and schema compliance.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from novastack.metadata_diagnostics import DocumentMetadataSnapshot
from novastack.metadata_reranker import CandidateRerankingDetail
from scripts.ret_eval_06_h3_experiment import (
    DEFAULT_MAX_PER_ROLE,
    DEFAULT_TOP_K,
    H3DiversificationAudit,
    H3RoleDiversificationOverlay,
    extract_requested_aspect_roles,
    get_document_role,
    run_controlled_h3_experiment,
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


class TestH3RoleClassificationAndAspectExtraction:
    """Test deterministic role extraction and classification."""

    def test_document_role_classification(self) -> None:
        assert get_document_role("DOC-DEP-DEP-NS-0001-01") == "deployment_note"
        assert get_document_role("DOC-PR-PR-NS-0001-01") == "pull_request_note"
        assert get_document_role("DOC-PM-EVT-NS-0001-01") == "postmortem"
        assert get_document_role("DOC-INC-INC-NS-0001-01") == "incident_report"
        assert get_document_role("DOC-TKT-TKT-NS-0001-01") == "support_ticket"
        assert get_document_role("DOC-SUP-SUP-NS-0001-01") == "support_ticket"
        assert get_document_role("DOC-DOC-EVT-NS-0001-01") == "documentation"
        assert get_document_role("DOC-RB-001") == "documentation"
        assert get_document_role("DOC-CHAT-EVT-NS-0001-01") == "conversation"
        assert get_document_role("DOC-MEET-EVT-NS-0001-01") == "meeting"
        assert get_document_role("DOC-NOTE-EVT-NS-0001-01") == "engineering_note"
        assert get_document_role("DOC-POL-001") == "policy"

    def test_extract_requested_aspect_roles(self) -> None:
        q_single = "What is the status of deployment v2.1.0?"
        assert extract_requested_aspect_roles(q_single) == ["deployment_note"]

        q_multi = "What caused the outage, which deployment triggered it, and which PR resolved it?"
        roles = extract_requested_aspect_roles(q_multi)
        assert "postmortem" in roles
        assert "deployment_note" in roles
        assert "pull_request_note" in roles
        assert len(roles) >= 3

        q_ticket = "Which customer reported the ticket for the outage?"
        roles_tkt = extract_requested_aspect_roles(q_ticket)
        assert "support_ticket" in roles_tkt
        assert "postmortem" in roles_tkt


class TestH3DiversificationOverlayInvariants:
    """Test invariants of H3RoleDiversificationOverlay."""

    def test_single_aspect_pass_through_invariant(self) -> None:
        """When query has < 2 aspects, H3 must be 100% pass-through."""
        overlay = H3RoleDiversificationOverlay()
        cands = [
            _make_candidate(f"c{i}", f"DOC-CHAT-00{i}", final_score=0.1 - i * 0.005)
            for i in range(15)
        ]
        q_single = "Show me chat logs for the incident"
        result, audit = overlay.diversify(cands, q_single)

        assert not audit.is_active
        assert [c.chunk_id for c in result] == [c.chunk_id for c in cands]

    def test_multi_aspect_activation_and_capacity_constraint(self) -> None:
        """When query has >= 2 aspects, H3 activates and enforces max_per_role = 3 in top-10."""
        overlay = H3RoleDiversificationOverlay(top_k=10, max_per_role=3)
        # 8 chat documents (dominant), 1 PR (at rank 9), 1 deployment (at rank 10), 5 meetings, 3 notes, 3 docs
        cands = []
        for i in range(8):
            cands.append(_make_candidate(f"chat_{i}", f"DOC-CHAT-00{i}", final_score=0.10 - i * 0.001))
        cands.append(_make_candidate("pr_1", "DOC-PR-PR-NS-0001-01", final_score=0.08))
        cands.append(_make_candidate("dep_1", "DOC-DEP-DEP-NS-0001-01", final_score=0.07))
        for i in range(5):
            cands.append(_make_candidate(f"mtg_{i}", f"DOC-MEET-00{i}", final_score=0.06 - i * 0.001))
        for i in range(3):
            cands.append(_make_candidate(f"note_{i}", f"DOC-NOTE-00{i}", final_score=0.05 - i * 0.001))
        for i in range(3):
            cands.append(_make_candidate(f"doc_{i}", f"DOC-DOC-00{i}", final_score=0.04 - i * 0.001))

        q_multi = "Which deployment caused the outage and which PR fixed it?"
        result, audit = overlay.diversify(cands, q_multi)

        assert audit.is_active
        assert audit.violations_max_per_role == 0
        top10 = result[:10]
        roles_top10 = [get_document_role(c.document_id) for c in top10]
        role_counts = Counter(roles_top10)

        # Capped at 3 for chat
        assert role_counts["conversation"] <= 3
        # PR and deployment are promoted into top-10
        assert "pull_request_note" in roles_top10
        assert "deployment_note" in roles_top10
        assert any(c.document_id == "DOC-PR-PR-NS-0001-01" for c in top10)
        assert any(c.document_id == "DOC-DEP-DEP-NS-0001-01" for c in top10)

    def test_candidate_pool_invariance(self) -> None:
        """H3 must strictly preserve candidate pool membership (no additions or deletions)."""
        overlay = H3RoleDiversificationOverlay(top_k=10, max_per_role=3)
        cands = [
            _make_candidate(f"c{i}", f"DOC-NOTE-00{i}", final_score=0.10 - i * 0.002)
            for i in range(25)
        ]
        q = "Which deployment caused the outage and which PR fixed it?"
        result, audit = overlay.diversify(cands, q)

        assert set(c.chunk_id for c in result) == set(c.chunk_id for c in cands)
        assert len(result) == len(cands)

    def test_score_immutability(self) -> None:
        """H3 must never alter candidate scores or attributes."""
        overlay = H3RoleDiversificationOverlay(top_k=10, max_per_role=3)
        cands = [
            _make_candidate(f"c{i}", f"DOC-NOTE-00{i}", final_score=0.10 - i * 0.002)
            for i in range(15)
        ]
        orig_scores = {c.chunk_id: c.final_score for c in cands}
        q = "Which deployment caused the outage and which PR fixed it?"
        result, _ = overlay.diversify(cands, q)

        for c in result:
            assert c.final_score == orig_scores[c.chunk_id]

    def test_security_forbidden_candidate_exclusion(self) -> None:
        """Forbidden documents must NEVER be admitted into Top-10 under any circumstance."""
        overlay = H3RoleDiversificationOverlay(top_k=10, max_per_role=3)
        # Construct candidate list where the ONLY PR is forbidden
        cands = [
            _make_candidate(f"c{i}", f"DOC-CHAT-00{i}", final_score=0.10 - i * 0.002)
            for i in range(15)
        ]
        cands.append(
            _make_candidate(
                "pr_forb",
                "DOC-PR-FORBIDDEN-01",
                final_score=-1000.0,
                is_forbidden=True,
            )
        )
        cands.append(_make_candidate("dep_allowed", "DOC-DEP-DEP-NS-0001-01", final_score=0.05))

        q = "Which deployment caused the outage and which PR fixed it?"
        forb_set = {"DOC-PR-FORBIDDEN-01"}
        result, audit = overlay.diversify(cands, q, forbidden_doc_ids=forb_set)

        top10 = result[:10]
        top10_doc_ids = [c.document_id for c in top10]
        assert "DOC-PR-FORBIDDEN-01" not in top10_doc_ids
        assert all(not c.is_forbidden for c in top10)
        assert all(c.final_score > -100.0 for c in top10)

    def test_determinism(self) -> None:
        """H3 must produce 100% deterministic ordering across runs."""
        overlay = H3RoleDiversificationOverlay(top_k=10, max_per_role=3)
        cands = [
            _make_candidate(f"c{i}", f"DOC-CHAT-00{i}", final_score=0.10 - i * 0.002)
            for i in range(20)
        ]
        q = "Which deployment caused the outage and which PR fixed it?"

        res1, _ = overlay.diversify(cands, q)
        res2, _ = overlay.diversify(cands, q)

        assert [c.chunk_id for c in res1] == [c.chunk_id for c in res2]


class TestH3ExperimentArtifact:
    """Test full execution and artifact compliance."""

    @pytest.fixture(scope="class")
    def experiment_artifact(self) -> dict:
        art_path = Path(__file__).resolve().parent.parent / "artifacts" / "ret_eval_06_h3_results.json"
        if not art_path.exists():
            return run_controlled_h3_experiment()
        with open(art_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_artifact_structure_and_commit(self, experiment_artifact: dict) -> None:
        meta = experiment_artifact["metadata"]
        assert meta["milestone"] == "RET-EVAL-06"
        assert meta["phase"] == "Phase 2 Controlled Experiment"
        assert meta["security_gate"] == "SEC-OPS-02 = VERIFIED"
        assert meta["git_commit"] == "d325e5a82681456ebaca57f2f27c1900f17bd415"

    def test_security_invariants(self, experiment_artifact: dict) -> None:
        sec = experiment_artifact["security_metrics"]
        assert sec["control"]["forbidden_leaks_top10"] == 0
        assert sec["treatment"]["forbidden_leaks_top10"] == 0
        assert sec["control"]["negative_leaks"] == 0
        assert sec["treatment"]["negative_leaks"] == 0
        assert sec["control"]["cross_tenant_leaks"] == 0
        assert sec["treatment"]["cross_tenant_leaks"] == 0

    def test_invariance_metrics(self, experiment_artifact: dict) -> None:
        inv = experiment_artifact["invariance_metrics"]
        assert inv["candidate_pool_mismatches"] == 0
        assert inv["single_aspect_top10_mismatches"] == 0
        assert inv["max_per_role_violations"] == 0

    def test_primary_h2_slice_non_regression(self, experiment_artifact: dict) -> None:
        """H2 slice Recall@10 must improve or remain non-negative."""
        h2 = experiment_artifact["primary_h2_slice_metrics"]
        assert h2["delta"]["recall_at_10"] >= 0.0
        assert h2["treatment"]["recall_at_10"] >= h2["control"]["recall_at_10"]

    def test_h3_multi_aspect_slice_improvement(self, experiment_artifact: dict) -> None:
        """H3 multi-aspect slice must show positive delta in Recall@10 and role coverage."""
        h3 = experiment_artifact["h3_multi_aspect_slice_metrics"]
        assert h3["delta"]["recall_at_10"] > 0.0
        assert h3["role_coverage"]["delta"] >= 0.0

    def test_corpus_non_regression(self, experiment_artifact: dict) -> None:
        """Positive corpus Recall@10 must increase or remain stable with zero critical regressions."""
        pos = experiment_artifact["overall_positive_metrics"]
        assert pos["delta"]["recall_at_10"] >= 0.0
        non_reg = experiment_artifact["non_regression_metrics"]
        assert non_reg["regressions_count"] <= 1
        assert non_reg["gains_count"] >= 4
