"""Unit and Integration Tests for Phase 4D-1: Candidate Depth & Fusion Ablation.

Verifies:
1. Mathematical correctness of fusion algorithms (RRF-Sum, CombMAX-RRF, Round-Robin Interleaving).
2. IR metrics calculations (Recall@k, MRR, NDCG@10, HitRate@k, forbidden leaks).
3. Artifact schema validation for phase_4d1_depth_fusion_ablation.json and PHASE_4D1_REPORT.md.
4. Recovery of fusion-suppressed cases at depth 100 (EVAL-0076, EVAL-0086, EVAL-0105).
5. Ground-truth defect isolation (EVAL-0028, 0029, 0030, 0034).
6. Security invariants: zero forbidden leaks across all depths and fusion configurations.
7. 100% SHA256 immutability of all 15 prior baseline artifacts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

from novastack.bm25 import RetrievalResult
from novastack.depth_fusion_ablation import (
    compute_ir_metrics,
    fuse_interleaving,
    fuse_rrf_max,
    fuse_rrf_sum,
    fuse_single_channel,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

PRIOR_ARTIFACTS = [
    "data/raw/novastack/source_records.json",
    "data/processed/novastack/search_documents.json",
    "data/processed/novastack/search_chunks.json",
    "data/evaluation/novastack/evaluation_cases.json",
    "data/evaluation/novastack/bm25_baseline.json",
    "data/evaluation/novastack/dense_baseline.json",
    "data/evaluation/novastack/hybrid_baseline.json",
    "data/evaluation/novastack/phase_4b0_candidate_diagnostics.json",
    "data/evaluation/novastack/phase_4b1_reranker_baseline.json",
    "data/evaluation/novastack/phase_4c0_query_profiles.json",
    "data/evaluation/novastack/phase_4c1_query_understanding.json",
    "data/evaluation/novastack/phase_4c2_metadata_diagnostics.json",
    "data/evaluation/novastack/phase_4c3_metadata_reranking.json",
    "data/evaluation/novastack/phase_4d0_starvation_diagnostics.json",
    "data/evaluation/novastack/phase_4d0_1_reconciliation.json",
]


def _make_dummy_result(chunk_id: str, doc_id: str, rank: int, score: float) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_id=doc_id,
        score=score,
        rank=rank,
        title="Title",
        text_preview="Preview",
        tenant_id="default",
        source_type="runbook",
        department="platform",
        classification="internal",
        authority_level="medium",
        status="published",
        version="1.0",
        created_at="2026-01-01T00:00:00Z",
        source_entity_id="SVC-001",
        related_entity_ids=["TEAM-001"],
    )


# -------------------------------------------------------------------------
# 1. Unit Tests for Fusion Logic
# -------------------------------------------------------------------------

def test_fuse_rrf_sum_scoring():
    """Verify standard RRF summing reciprocal ranks and document deduplication."""
    bm = [
        _make_dummy_result("c1", "doc1", rank=1, score=10.0),
        _make_dummy_result("c2", "doc2", rank=2, score=8.0),
        _make_dummy_result("c3", "doc1", rank=3, score=6.0),  # Duplicate doc1 chunk
    ]
    dn = [
        _make_dummy_result("c2", "doc2", rank=1, score=0.9),  # Dual hit on c2/doc2
        _make_dummy_result("c4", "doc3", rank=2, score=0.8),
    ]

    fused = fuse_rrf_sum(bm, dn, top_k=10, k=60, deduplicate_docs=True)
    # doc2 has dual hits: BM25 rank 2 + Dense rank 1 -> 1/62 + 1/61 = 0.016129 + 0.016393 = 0.032522
    # doc1 has BM25 rank 1 -> 1/61 = 0.016393
    # doc3 has Dense rank 2 -> 1/62 = 0.016129
    assert len(fused) == 3
    assert fused[0].document_id == "doc2"
    assert fused[0].rank == 1
    assert fused[1].document_id == "doc1"
    assert fused[1].rank == 2
    assert fused[2].document_id == "doc3"
    assert fused[2].rank == 3


def test_fuse_rrf_max_scoring():
    """Verify CombMAX-RRF takes max reciprocal rank without dual-hit summing."""
    bm = [
        _make_dummy_result("c1", "doc1", rank=1, score=10.0),
        _make_dummy_result("c2", "doc2", rank=2, score=8.0),
    ]
    dn = [
        _make_dummy_result("c2", "doc2", rank=2, score=0.8),
        _make_dummy_result("c3", "doc3", rank=3, score=0.7),
    ]

    fused = fuse_rrf_max(bm, dn, top_k=10, k=60, deduplicate_docs=True)
    # doc1: max(1/61, 0) = 1/61 = 0.016393
    # doc2: max(1/62, 1/62) = 1/62 = 0.016129
    # In CombMAX, doc1 stays ahead of doc2 even though doc2 appeared in both channels!
    assert len(fused) == 3
    assert fused[0].document_id == "doc1"
    assert fused[1].document_id == "doc2"
    assert fused[2].document_id == "doc3"


def test_fuse_interleaving():
    """Verify round-robin interleaving selects turn by turn deduplicating documents."""
    bm = [
        _make_dummy_result("c1", "doc1", rank=1, score=10.0),
        _make_dummy_result("c2", "doc2", rank=2, score=8.0),
    ]
    dn = [
        _make_dummy_result("c3", "doc2", rank=1, score=0.9),  # doc2 shared
        _make_dummy_result("c4", "doc3", rank=2, score=0.8),
    ]

    # Turn 0: BM25 pick -> doc1
    # Turn 0: Dense pick -> doc2
    # Turn 1: BM25 pick -> doc2 (already in seen, skipped)
    # Turn 1: Dense pick -> doc3
    fused = fuse_interleaving(bm, dn, top_k=10, deduplicate_docs=True)
    assert len(fused) == 3
    assert [f.document_id for f in fused] == ["doc1", "doc2", "doc3"]
    assert fused[0].rank == 1
    assert fused[1].rank == 2
    assert fused[2].rank == 3


def test_compute_ir_metrics():
    """Verify IR metrics edge cases and calculations."""
    retrieved = ["docA", "docB", "docC", "docD", "docE"]
    expected = ["docB", "docE"]
    acceptable = ["docA"]
    forbidden = ["docC"]

    m = compute_ir_metrics(retrieved, expected, acceptable, forbidden, k_values=(1, 2, 3, 5))
    assert m["recall_at_1"] == 0.0
    assert m["recall_at_2"] == 0.5  # docB
    assert m["recall_at_5"] == 1.0  # docB and docE
    assert m["mrr"] == 0.5          # first hit at rank 2
    assert m["hit_at_1"] == 0.0
    assert m["hit_at_2"] == 1.0
    assert m["forbidden_leaks_top10"] == 1
    assert m["forbidden_in_pool"] == 1


# -------------------------------------------------------------------------
# 2. Integration Tests for Phase 4D-1 Output Artifacts
# -------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ablation_data():
    artifact_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "phase_4d1_depth_fusion_ablation.json"
    assert artifact_path.exists(), f"Phase 4D-1 artifact not found at {artifact_path}"
    with open(artifact_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_artifact_metadata_and_structure(ablation_data):
    meta = ablation_data["metadata"]
    assert meta["total_evaluation_cases"] == 120
    assert meta["positive_evaluation_cases"] == 101
    assert meta["starvation_cases_analyzed"] == 23
    assert meta["synthetic_ground_truth_defects"] == 4
    assert meta["genuine_starvation_cases"] == 19


def test_hypothesis_a_candidate_depth_progression(ablation_data):
    """Hypothesis A: Coverage increases, but downstream Recall@10 exhibits rank dilution."""
    pool_metrics = ablation_data["experiment_a_candidate_depth"]["summary_pool_metrics"]
    downstream_metrics = ablation_data["experiment_a_candidate_depth"]["summary_downstream_reranked_metrics"]

    cov_50 = pool_metrics["depth_50"]["candidate_coverage"]
    cov_75 = pool_metrics["depth_75"]["candidate_coverage"]
    cov_100 = pool_metrics["depth_100"]["candidate_coverage"]

    # Monotonic increase in candidate pool coverage
    assert cov_50 < cov_75 < cov_100
    assert cov_100 > cov_50

    # Candidate pool Recall@10 improves or remains flat
    assert pool_metrics["depth_50"]["recall_at_10"] <= pool_metrics["depth_100"]["recall_at_10"]

    # Downstream Recall@10 drops due to rank dilution from deep distractors
    down_r10_50 = downstream_metrics["depth_50"]["recall_at_10"]
    down_r10_100 = downstream_metrics["depth_100"]["recall_at_10"]
    assert down_r10_100 < down_r10_50, "Expanding candidate depth to 100 dilutes downstream ranking precision"


def test_fusion_suppressed_cases_recovered_at_depth_100(ablation_data):
    """Verify EVAL-0076, EVAL-0086, EVAL-0105 are recovered in candidate pool at depth 100."""
    starvation_cases = {s["evaluation_id"]: s for s in ablation_data["starvation_tracking"]}

    for eid in ["EVAL-0076", "EVAL-0086", "EVAL-0105"]:
        assert eid in starvation_cases
        s = starvation_cases[eid]
        # At depth 50, target is absent from pool
        assert s["depth_pool_ranks"]["depth_50"] is None, f"{eid} should be suppressed at depth 50"
        # At depth 100, target enters pool
        assert s["depth_pool_ranks"]["depth_100"] is not None, f"{eid} must enter pool at depth 100"


def test_synthetic_ground_truth_defects_isolated(ablation_data):
    """Ensure the 4 synthetic ground-truth defects are marked and isolated from genuine recovery."""
    defect_ids = {"EVAL-0028", "EVAL-0029", "EVAL-0030", "EVAL-0034"}
    starvation_cases = {s["evaluation_id"]: s for s in ablation_data["starvation_tracking"]}

    for did in defect_ids:
        assert did in starvation_cases
        assert starvation_cases[did]["is_defect"] is True
        # Verify defects are not promoted into downstream top-10
        assert starvation_cases[did]["depth_reranked_ranks"]["depth_100"] is None or starvation_cases[did]["depth_reranked_ranks"]["depth_100"] > 10


def test_hypothesis_b_fusion_superiority(ablation_data):
    """Hypothesis B: Standard RRF k=60 strongly outperforms CombMAX and Interleaving."""
    downstream_fusion = ablation_data["experiment_b_fusion_ablation_depth50"]["summary_downstream_reranked_metrics"]

    rrf_r10 = downstream_fusion["rrf_k60"]["recall_at_10"]
    comb_max_r10 = downstream_fusion["comb_max_rrf"]["recall_at_10"]
    robin_r10 = downstream_fusion["round_robin"]["recall_at_10"]

    assert rrf_r10 > comb_max_r10, "Standard RRF consensus must outperform CombMAX"
    assert rrf_r10 > robin_r10, "Standard RRF consensus must outperform Round-Robin"
    assert downstream_fusion["rrf_k60"]["mrr"] > downstream_fusion["comb_max_rrf"]["mrr"]


def test_security_invariants(ablation_data):
    """Zero forbidden document leaks into top-10 across all depths and fusion variants."""
    downstream_depth = ablation_data["experiment_a_candidate_depth"]["summary_downstream_reranked_metrics"]
    for d in [50, 75, 100]:
        assert downstream_depth[f"depth_{d}"]["forbidden_leaks_top10"] == 0

    downstream_fusion = ablation_data["experiment_b_fusion_ablation_depth50"]["summary_downstream_reranked_metrics"]
    for v in downstream_fusion:
        assert downstream_fusion[v]["forbidden_leaks_top10"] == 0


def test_prior_15_artifacts_sha256_immutability(ablation_data):
    """Verify that all 15 prior baseline artifacts have identical pre/post SHA256 hashes."""
    recorded_hashes = ablation_data["prior_artifacts_integrity"]
    assert len(recorded_hashes) == 15

    for rel_path, expected_hash in recorded_hashes.items():
        full_path = _PROJECT_ROOT / rel_path
        assert full_path.exists(), f"Missing artifact: {rel_path}"
        h = hashlib.sha256()
        with open(full_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        actual_hash = h.hexdigest()
        assert actual_hash == expected_hash, f"MUTATION in {rel_path}: {actual_hash} != {expected_hash}"


def test_report_exists_and_answers_questions():
    """Verify docs/PHASE_4D1_REPORT.md exists and answers all 15 questions."""
    report_path = _PROJECT_ROOT / "docs" / "PHASE_4D1_REPORT.md"
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert len(content) > 2000
    for q_num in range(1, 16):
        assert f"### Q{q_num}." in content, f"Report missing answer to Q{q_num}"
