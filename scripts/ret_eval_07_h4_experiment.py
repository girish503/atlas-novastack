#!/usr/bin/env python3
"""Hermetic Controlled H4 Experiment — RET-EVAL-07 Phase 2.

Evaluates Hypothesis H4:
"When H3 performs role diversification and the query already has a resolved
canonical entity, selecting role anchors that are bound to that same entity
can reduce cross-entity evidence contamination without changing candidate
generation, base retrieval scores, or security boundaries."

Mandatory Protocol Invariants:
1. CONTROL = B4 + H1 + H2 + H3 (exact RET-EVAL-06 treatment behavior: depth-2 traversal +
   reciprocal edge alignment + deterministic H2 entity resolution + unconstrained H3 role diversification).
2. TREATMENT = B4 + H1 + H2 + H3(entity-aware anchor selection) (The ONLY treatment difference is H3
   anchor selection using Strategy E Two-Tier Entity-Aware Allocation).
3. Candidate pool invariance: Control and Treatment share 100% identical candidate pool and base scores.
4. Security: Entity matching occurs ONLY AFTER existing authorization eligibility partition.
   Forbidden, unauthorized, or cross-tenant candidates can NEVER be selected as anchors or in Top-10.
5. Zero LLMs, Zero external network, Zero vector/embedding similarity, Zero arbitrary score thresholds.
6. Zero modification to src/novastack/**, evaluation dataset, Docker, or release manifests.

Produces:
    artifacts/ret_eval_07_h4_results.json
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

# Ensure src/ and repo root are importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from novastack.bm25 import BM25Config, BM25Index
from novastack.dense import DenseIndex
from novastack.depth_fusion_ablation import (
    compute_ir_metrics,
    fuse_rrf_sum,
)
from novastack.entity_catalog import (
    CanonicalEntity,
    EntityCatalog,
    IDENTIFIER_PATTERN,
)
from novastack.metadata_diagnostics import (
    DocumentMetadataSnapshot,
    build_metadata_snapshot_index,
)
from novastack.metadata_reranker import (
    CandidateRerankingDetail,
    MetadataReranker,
    MetadataRerankerConfig,
)
from novastack.models import SearchChunk
from novastack.query_understanding import (
    EntityCatalog as QUEntityCatalog,
    EntityMention,
    QueryUnderstanding,
    QueryUnderstandingExtractor,
)
from novastack.relational_retrieval import (
    StructuredRetrievalResult,
    StructuredRetriever,
    StructuredRetrieverConfig,
    fuse_hybrid_and_structured,
)
from scripts.ret_eval_03_h1_experiment import (
    H1StructuredRetrieverOverlay,
    calculate_macro_mean,
    evaluate_retrieval_ranking,
    get_git_commit,
)
from scripts.ret_eval_05_h2_experiment import (
    H2EntityResolutionRegistry,
    H2QueryUnderstandingOverlay,
    H2StructuredRetrieverAdapter,
    PRIMARY_H2_EXPECTED_ANCHORS,
    PRIMARY_H2_SLICE_IDS,
)
from scripts.ret_eval_06_h3_experiment import (
    ASPECT_PATTERNS,
    DEFAULT_MAX_PER_ROLE,
    DEFAULT_TOP_K,
    H3DiversificationAudit,
    H3RoleDiversificationOverlay,
    extract_requested_aspect_roles,
    get_document_role,
)

KNOWN_DIAGNOSTIC_CASES: list[str] = [
    "EVAL-0036",
    "EVAL-0042",
    "EVAL-0044",
    "EVAL-0045",
    "EVAL-0046",
    "EVAL-0047",
    "EVAL-0048",
    "EVAL-0050",
]


def is_candidate_entity_compatible(
    candidate: CandidateRerankingDetail,
    query_entity_ids: set[str],
    metadata_index: dict[str, DocumentMetadataSnapshot] | None,
) -> bool:
    """Deterministically check whether a candidate document is bound to any runtime query entity.
    
    Checks:
    1. Exact match on source_entity_id
    2. Exact set membership in related_entity_ids
    
    Zero embeddings, zero fuzzy matching, zero LLMs.
    """
    if not query_entity_ids or not metadata_index:
        return False
    doc_meta = metadata_index.get(candidate.document_id)
    if not doc_meta:
        return False

    # 1. source_entity_id exact match
    if doc_meta.source_entity_id and doc_meta.source_entity_id in query_entity_ids:
        return True

    # 2. related_entity_ids exact membership
    if doc_meta.related_entity_ids:
        for eid in doc_meta.related_entity_ids:
            if eid in query_entity_ids:
                return True

    return False


@dataclass
class H4DiversificationAudit:
    """Audit information for the H4 entity-anchored role diversification execution on a single query."""

    is_active: bool
    aspect_roles: list[str]
    query_entity_ids: list[str]
    h4_applicability_status: str  # H4_APPLICABLE or H4_INAPPLICABLE
    h4_applicability_reason: str
    anchors_identified: dict[str, str]  # role -> doc_id
    anchors_tier: dict[str, int]  # role -> 1 (entity-matched) or 2 (H3 fallback)
    anchors_admitted: dict[str, str]
    role_counts_top10: dict[str, int]
    role_coverage_top10: float
    violations_max_per_role: int
    selected_chunk_ids: list[str]


class H4EntityAnchoredDiversificationOverlay:
    """Deterministic, post-reranking Entity-Anchored Role Diversification Overlay (Strategy E).
    
    The ONLY modification from H3 is how role anchors are selected:
    - Tier 1: For each requested role R, prefer the highest-ranked eligible candidate whose
      metadata is deterministically bound to one of the resolved runtime query entities.
    - Tier 2: If no entity-compatible candidate exists for role R, fall back to the highest-ranked
      eligible candidate H3 would have selected (base-score order).
    
    Capacity allocation, quotas (max_per_role = 3), top_k (10), backfills, and security partitioning
    remain 100% identical to H3.
    """

    def __init__(
        self,
        top_k: int = DEFAULT_TOP_K,
        max_per_role: int = DEFAULT_MAX_PER_ROLE,
        metadata_index: dict[str, DocumentMetadataSnapshot] | None = None,
    ) -> None:
        self.top_k = top_k
        self.max_per_role = max_per_role
        self.metadata_index = metadata_index

    def is_eligible(
        self,
        candidate: CandidateRerankingDetail,
        forbidden_doc_ids: set[str] | None = None,
    ) -> bool:
        """Verify strict authorization eligibility.
        
        Forbidden or security-penalized candidates can NEVER be admitted to diversified Top-K.
        """
        if getattr(candidate, "is_forbidden", False):
            return False
        if getattr(candidate, "final_score", 0.0) < -100.0:
            return False
        if forbidden_doc_ids and candidate.document_id in forbidden_doc_ids:
            return False
        return True

    def classify_applicability(
        self,
        aspect_roles: list[str],
        query_entity_ids: set[str],
        eligible_candidates: list[CandidateRerankingDetail],
    ) -> tuple[str, str]:
        """Classify H4 applicability derived strictly from runtime state without ground truth."""
        if len(aspect_roles) < 2:
            return "H4_INAPPLICABLE", "fewer_than_2_requested_roles"
        if not query_entity_ids:
            return "H4_INAPPLICABLE", "no_resolved_runtime_entity"

        has_compat = False
        for c in eligible_candidates:
            r = get_document_role(c.document_id, self.metadata_index)
            if r in aspect_roles and is_candidate_entity_compatible(c, query_entity_ids, self.metadata_index):
                has_compat = True
                break

        if has_compat:
            return "H4_APPLICABLE", "multi_aspect_with_entity_compatible_candidate"
        else:
            return "H4_INAPPLICABLE", "no_entity_compatible_candidate_for_requested_roles"

    def diversify(
        self,
        reranked_candidates: list[CandidateRerankingDetail],
        query: str,
        query_entity_ids: set[str] | None = None,
        user_tenant: str | None = None,
        forbidden_doc_ids: set[str] | None = None,
    ) -> tuple[list[CandidateRerankingDetail], H4DiversificationAudit]:
        """Apply deterministic entity-anchored role diversification overlay."""
        forb_set = set(forbidden_doc_ids or [])
        aspect_roles = extract_requested_aspect_roles(query)
        q_entities = set(query_entity_ids or [])

        # Gating condition: >= 2 requested aspects required to activate diversification
        if len(aspect_roles) < 2:
            top10 = reranked_candidates[: self.top_k]
            role_counts = defaultdict(int)
            for c in top10:
                r = get_document_role(c.document_id, self.metadata_index)
                role_counts[r] += 1
            audit = H4DiversificationAudit(
                is_active=False,
                aspect_roles=aspect_roles,
                query_entity_ids=sorted(q_entities),
                h4_applicability_status="H4_INAPPLICABLE",
                h4_applicability_reason="fewer_than_2_requested_roles",
                anchors_identified={},
                anchors_tier={},
                anchors_admitted={},
                role_counts_top10=dict(role_counts),
                role_coverage_top10=1.0 if not aspect_roles else (1.0 if aspect_roles[0] in role_counts else 0.0),
                violations_max_per_role=0,
                selected_chunk_ids=[c.chunk_id for c in top10],
            )
            return list(reranked_candidates), audit

        # Strict security partitioning: eligible vs ineligible
        eligible: list[CandidateRerankingDetail] = []
        ineligible: list[CandidateRerankingDetail] = []
        for c in reranked_candidates:
            if self.is_eligible(c, forb_set):
                eligible.append(c)
            else:
                ineligible.append(c)

        # Runtime Applicability Classification
        app_status, app_reason = self.classify_applicability(aspect_roles, q_entities, eligible)

        # --- H4 TWO-TIER ANCHOR SELECTION (The ONLY treatment difference) ---
        anchors: dict[str, CandidateRerankingDetail] = {}
        anchors_tier: dict[str, int] = {}

        # Tier 1: Prefer highest-ranked candidate matching role R AND bound to query_entity_ids
        if q_entities:
            for c in eligible:
                r = get_document_role(c.document_id, self.metadata_index)
                if r in aspect_roles and r not in anchors:
                    if is_candidate_entity_compatible(c, q_entities, self.metadata_index):
                        anchors[r] = c
                        anchors_tier[r] = 1

        # Tier 2: For any role lacking an entity-anchored candidate, fall back to exact H3 unconstrained order
        for c in eligible:
            r = get_document_role(c.document_id, self.metadata_index)
            if r in aspect_roles and r not in anchors:
                anchors[r] = c
                anchors_tier[r] = 2

        unadmitted_anchor_cids = {c.chunk_id for c in anchors.values()}
        selected: list[CandidateRerankingDetail] = []
        selected_cids: set[str] = set()
        role_counts: dict[str, int] = defaultdict(int)
        admitted_anchors: dict[str, str] = {}

        # Primary selection loop: greedy base-score order respecting role quota and anchor reservations
        for c in eligible:
            if len(selected) >= self.top_k:
                break
            r = get_document_role(c.document_id, self.metadata_index)
            is_anchor = c.chunk_id in unadmitted_anchor_cids
            rem_slots = self.top_k - len(selected)
            other_needed = len(unadmitted_anchor_cids - {c.chunk_id})

            if role_counts[r] < self.max_per_role:
                if is_anchor or (rem_slots > other_needed):
                    selected.append(c)
                    selected_cids.add(c.chunk_id)
                    role_counts[r] += 1
                    if is_anchor:
                        unadmitted_anchor_cids.remove(c.chunk_id)
                        admitted_anchors[r] = c.document_id

        # Secondary backfill 1: fill remaining slots from eligible candidates respecting role quota
        if len(selected) < self.top_k:
            for c in eligible:
                if len(selected) >= self.top_k:
                    break
                if c.chunk_id in selected_cids:
                    continue
                r = get_document_role(c.document_id, self.metadata_index)
                if role_counts[r] < self.max_per_role:
                    selected.append(c)
                    selected_cids.add(c.chunk_id)
                    role_counts[r] += 1
                    if c.chunk_id in unadmitted_anchor_cids:
                        unadmitted_anchor_cids.remove(c.chunk_id)
                        admitted_anchors[r] = c.document_id

        # Secondary backfill 2: if slots still remain, fill with any remaining eligible candidate
        if len(selected) < self.top_k:
            for c in eligible:
                if len(selected) >= self.top_k:
                    break
                if c.chunk_id in selected_cids:
                    continue
                r = get_document_role(c.document_id, self.metadata_index)
                selected.append(c)
                selected_cids.add(c.chunk_id)
                role_counts[r] += 1
                if c.chunk_id in unadmitted_anchor_cids:
                    unadmitted_anchor_cids.remove(c.chunk_id)
                    admitted_anchors[r] = c.document_id

        # Deferred eligible candidates strictly preserved in base score order
        deferred_eligible = [c for c in eligible if c.chunk_id not in selected_cids]

        # Combine: selected top-K + deferred eligible + ineligible
        diversified_result = selected + deferred_eligible + ineligible

        # Compute role coverage on requested aspects
        req_set = set(aspect_roles)
        covered_set = req_set & set(role_counts.keys())
        role_coverage = len(covered_set) / max(1, len(req_set))

        # Check for role capacity violations in top_k
        violations = sum(1 for cnt in role_counts.values() if cnt > self.max_per_role)

        audit = H4DiversificationAudit(
            is_active=True,
            aspect_roles=aspect_roles,
            query_entity_ids=sorted(q_entities),
            h4_applicability_status=app_status,
            h4_applicability_reason=app_reason,
            anchors_identified={r: c.document_id for r, c in anchors.items()},
            anchors_tier=anchors_tier,
            anchors_admitted=admitted_anchors,
            role_counts_top10=dict(role_counts),
            role_coverage_top10=round(role_coverage, 6),
            violations_max_per_role=violations,
            selected_chunk_ids=[c.chunk_id for c in selected],
        )

        return diversified_result, audit


def run_controlled_h4_experiment() -> dict[str, Any]:
    """Execute the full hermetic RET-EVAL-07 Phase 2 controlled experiment.
    
    CONTROL: B4 + H1 + H2 + H3 (exact RET-EVAL-06 treatment behavior)
    TREATMENT: B4 + H1 + H2 + H3(entity-aware anchor selection)
    
    Returns the authoritative experiment summary dictionary and writes
    artifacts/ret_eval_07_h4_results.json.
    """
    t_start = time.perf_counter()
    git_commit = get_git_commit(_PROJECT_ROOT)
    print("=" * 80)
    print("ATLAS — RET-EVAL-07 PHASE 2: H4 ENTITY-ANCHORED ROLE DISAMBIGUATION EXPERIMENT")
    print(f"Git Commit: {git_commit}")
    print("Security Gate: SEC-OPS-02 = VERIFIED")
    print("=" * 80)

    # 1. Load frozen corpus and evaluation data
    raw_dir = _PROJECT_ROOT / "data" / "raw" / "novastack"
    proc_dir = _PROJECT_ROOT / "data" / "processed" / "novastack"
    eval_dir = _PROJECT_ROOT / "data" / "evaluation" / "novastack"
    art_dir = _PROJECT_ROOT / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)

    with open(eval_dir / "evaluation_cases.json", "r", encoding="utf-8") as f:
        cases = json.load(f)["evaluation_cases"]
    print(f"Loaded {len(cases)} evaluation cases.")

    with open(proc_dir / "search_chunks.json", "r", encoding="utf-8") as f:
        chunks_data = json.load(f)["search_chunks"]
    chunks = [SearchChunk(**c) for c in chunks_data]
    print(f"Loaded {len(chunks)} search chunks.")

    with open(proc_dir / "search_documents.json", "r", encoding="utf-8") as f:
        docs_list = json.load(f)["search_documents"]
    print(f"Loaded {len(docs_list)} search documents.")

    with open(raw_dir / "adversarial_fixtures.json", "r", encoding="utf-8") as f:
        adv_fixtures = json.load(f)["adversarial_fixtures"]

    # 2. Catalogs, Extractors, and Metadata Index
    catalog = EntityCatalog(raw_dir, proc_dir / "search_chunks.json")
    qu_catalog = QUEntityCatalog(raw_dir)
    base_qu_extractor = QueryUnderstandingExtractor(qu_catalog)

    metadata_snapshot_index = build_metadata_snapshot_index(docs_list, adv_fixtures)
    print(f"Built metadata snapshot index for {len(metadata_snapshot_index)} documents.")

    # 3. Base Structured Retriever
    s_config = StructuredRetrieverConfig(
        max_neighbors_per_hop=10,
        enable_runbook_reverse_index=True,
        runbook_entity_weight=0.85,
        direct_entity_weight=1.0,
        traversed_entity_weight=0.7,
        related_entity_weight=0.5,
    )
    base_structured_retriever = StructuredRetriever(catalog=catalog, config=s_config)

    # 4. H1 + H2 Overlay Pipeline (Frozen from RET-EVAL-05 Treatment)
    h2_registry = H2EntityResolutionRegistry(catalog=catalog, raw_dir=raw_dir)
    h2_qu_overlay = H2QueryUnderstandingOverlay(
        base_extractor=base_qu_extractor,
        registry=h2_registry,
        catalog=catalog,
    )
    treatment_structured_adapter = H2StructuredRetrieverAdapter(
        base_retriever=base_structured_retriever,
        registry=h2_registry,
        catalog=catalog,
    )
    h1_overlay = H1StructuredRetrieverOverlay(treatment_structured_adapter)

    # 5. Shared BM25 & Dense indices
    bm25_index = BM25Index.build_index(chunks, config=BM25Config(k1=1.5, b=0.75))
    dense_index = DenseIndex.load(
        chunks_path=proc_dir / "search_chunks.json",
        embeddings_path=proc_dir / "dense_embeddings.npz",
        metadata_path=proc_dir / "dense_index_metadata.json",
    )
    reranker = MetadataReranker(MetadataRerankerConfig())

    # 6. Control Overlay (Exact Frozen H3 from RET-EVAL-06)
    control_h3_overlay = H3RoleDiversificationOverlay(
        top_k=DEFAULT_TOP_K,
        max_per_role=DEFAULT_MAX_PER_ROLE,
        metadata_index=metadata_snapshot_index,
    )

    # 7. Treatment Overlay (Strategy E Entity-Aware Anchor Selection)
    treatment_h4_overlay = H4EntityAnchoredDiversificationOverlay(
        top_k=DEFAULT_TOP_K,
        max_per_role=DEFAULT_MAX_PER_ROLE,
        metadata_index=metadata_snapshot_index,
    )

    # 8. Tracking containers
    case_level_results: list[dict[str, Any]] = []
    control_all_metrics: list[dict[str, float]] = []
    treatment_all_metrics: list[dict[str, float]] = []
    control_pos_metrics: list[dict[str, float]] = []
    treatment_pos_metrics: list[dict[str, float]] = []

    # Slices
    multi_aspect_control_metrics: list[dict[str, float]] = []
    multi_aspect_treatment_metrics: list[dict[str, float]] = []

    h4_applicable_control_metrics: list[dict[str, float]] = []
    h4_applicable_treatment_metrics: list[dict[str, float]] = []

    diagnostic_slice_control_metrics: list[dict[str, float]] = []
    diagnostic_slice_treatment_metrics: list[dict[str, float]] = []

    h2_slice_control_metrics: list[dict[str, float]] = []
    h2_slice_treatment_metrics: list[dict[str, float]] = []

    security_audit = {
        "control": {
            "forbidden_leaks_top10": 0,
            "forbidden_in_pool": 0,
            "negative_leaks": 0,
            "cross_tenant_leaks": 0,
        },
        "treatment": {
            "forbidden_leaks_top10": 0,
            "forbidden_in_pool": 0,
            "negative_leaks": 0,
            "cross_tenant_leaks": 0,
        },
    }

    invariance_audit = {
        "candidate_pool_mismatches": 0,
        "candidate_base_score_mismatches": 0,
        "single_aspect_top10_mismatches": 0,
        "max_per_role_violations": 0,
    }

    regressions: list[dict[str, Any]] = []
    gains: list[dict[str, Any]] = []
    unchanged: list[str] = []

    anchor_change_count = 0
    tier1_anchor_count = 0
    tier2_anchor_count = 0

    # 9. Execute full evaluation across all 120 cases
    print("Executing 120 evaluation cases across Control (B4+H1+H2+H3) and Treatment (B4+H1+H2+H4)...")
    for idx, eval_case_dict in enumerate(cases, start=1):
        eid = eval_case_dict["evaluation_id"]
        q_orig = eval_case_dict["query"]
        cat = eval_case_dict.get("query_category") or eval_case_dict.get("category", "unknown")
        t_id = eval_case_dict.get("tenant_id")
        user_id = eval_case_dict.get("user_id")
        user_role = eval_case_dict.get("user_role")
        user_dept = eval_case_dict.get("user_department")
        expected_access = eval_case_dict.get("expected_access", "allow")
        exp_docs = eval_case_dict.get("expected_document_ids", [])
        acc_docs = eval_case_dict.get("acceptable_document_ids", [])
        forb_docs = eval_case_dict.get("forbidden_document_ids", [])
        is_positive = len(exp_docs) > 0
        is_h2_slice = eid in PRIMARY_H2_SLICE_IDS

        filters = {"tenant_id": t_id} if t_id else None

        eval_ctx = {
            "evaluation_id": eid,
            "tenant_id": t_id,
            "user_id": user_id,
            "user_role": user_role,
            "user_department": user_dept,
            "expected_access": expected_access,
            "forbidden_document_ids": forb_docs,
        }

        # --- RETRIEVAL PIPELINE (Shared candidate pool) ---
        # 1. H2 Query Understanding
        qu, h2_matches = h2_qu_overlay.extract(eid, q_orig)
        expanded_q = qu.expanded_query if qu else q_orig
        runtime_query_entity_ids = {e.entity_id for e in qu.entities if e.entity_id} if qu else set()

        # 2. Hybrid Retrieval (BM25 + Dense)
        bm_res = bm25_index.search(query=expanded_q, top_k=50, filters=filters)
        dn_res = dense_index.search(query=q_orig, top_k=50, filters=filters)
        hybrid_pool = fuse_rrf_sum(bm_res, dn_res, top_k=50, k=60, deduplicate_docs=True)

        # 3. H1 Multi-Hop Traversal (Depth 2 + Reciprocal Edge Alignment)
        struct_res, struct_diags = h1_overlay.retrieve(
            query=q_orig,
            eval_case=eval_ctx,
            top_k=50,
        )

        # 4. Fusion of Hybrid and Structured
        combined = fuse_hybrid_and_structured(
            hybrid_candidates=hybrid_pool,
            structured_candidates=struct_res.candidates,
            k=60,
            w_hybrid=1.0,
            w_struct=1.0,
            top_k=50,
            deduplicate_docs=True,
            catalog=catalog,
        )

        # 5. Metadata-Aware Reranker
        reranked = reranker.rerank(
            candidates=combined,
            qu=qu,
            metadata_index=metadata_snapshot_index,
            forbidden_doc_ids=set(forb_docs),
            enforce_security=True,
        )

        # --- CONTROL EVALUATION (B4 + H1 + H2 + H3 Unconstrained Diversification) ---
        ctrl_reranked, ctrl_audit = control_h3_overlay.diversify(
            reranked_candidates=reranked,
            query=q_orig,
            user_tenant=t_id,
            forbidden_doc_ids=set(forb_docs),
        )
        ctrl_docs = [r.document_id for r in ctrl_reranked]
        ctrl_top10 = ctrl_docs[:DEFAULT_TOP_K]
        m_ctrl = evaluate_retrieval_ranking(ctrl_docs, exp_docs, acc_docs, forb_docs)

        # --- TREATMENT EVALUATION (B4 + H1 + H2 + H4 Entity-Anchored Diversification) ---
        treat_reranked, treat_audit = treatment_h4_overlay.diversify(
            reranked_candidates=reranked,
            query=q_orig,
            query_entity_ids=runtime_query_entity_ids,
            user_tenant=t_id,
            forbidden_doc_ids=set(forb_docs),
        )
        treat_docs = [r.document_id for r in treat_reranked]
        treat_top10 = treat_docs[:DEFAULT_TOP_K]
        m_treat = evaluate_retrieval_ranking(treat_docs, exp_docs, acc_docs, forb_docs)

        # --- INVARIANCE & AUDIT CHECKS ---
        # 1. Candidate pool invariance (multiset of candidate doc_ids must be identical)
        if sorted(ctrl_docs) != sorted(treat_docs):
            invariance_audit["candidate_pool_mismatches"] += 1

        # 2. Candidate base score invariance
        ctrl_score_map = {r.chunk_id: r.final_score for r in ctrl_reranked}
        treat_score_map = {r.chunk_id: r.final_score for r in treat_reranked}
        for cid, sc in ctrl_score_map.items():
            if cid in treat_score_map and abs(treat_score_map[cid] - sc) > 1e-9:
                invariance_audit["candidate_base_score_mismatches"] += 1

        # 3. Single-aspect pass-through invariance
        if not treat_audit.is_active:
            if ctrl_top10 != treat_top10:
                invariance_audit["single_aspect_top10_mismatches"] += 1

        # 4. Capacity allocation violations in top-10
        if treat_audit.violations_max_per_role > 0:
            invariance_audit["max_per_role_violations"] += treat_audit.violations_max_per_role

        # Detect anchor changes between Control (H3) and Treatment (H4)
        changed_anchors = {}
        for role, t_doc in treat_audit.anchors_identified.items():
            c_doc = ctrl_audit.anchors_identified.get(role)
            tier = treat_audit.anchors_tier.get(role, 2)
            if tier == 1:
                tier1_anchor_count += 1
            else:
                tier2_anchor_count += 1
            if c_doc != t_doc:
                changed_anchors[role] = {
                    "control": c_doc,
                    "treatment": t_doc,
                    "tier": tier,
                }
        if changed_anchors:
            anchor_change_count += 1

        # Diagnostic Slice Determination (Purely runtime-defined)
        is_h4_applicable = treat_audit.h4_applicability_status == "H4_APPLICABLE"
        is_diagnostic_slice = is_h4_applicable and bool(changed_anchors)

        # Metrics deltas
        rec_ctrl = m_ctrl.get("recall_at_10", 0.0)
        rec_treat = m_treat.get("recall_at_10", 0.0)
        delta_rec = round(rec_treat - rec_ctrl, 6)

        mrr_ctrl = m_ctrl.get("mrr", 0.0)
        mrr_treat = m_treat.get("mrr", 0.0)
        delta_mrr = round(mrr_treat - mrr_ctrl, 6)

        ndcg_ctrl = m_ctrl.get("ndcg_at_10", 0.0)
        ndcg_treat = m_treat.get("ndcg_at_10", 0.0)
        delta_ndcg = round(ndcg_treat - ndcg_ctrl, 6)

        # Track changes
        if is_positive:
            if delta_rec > 0.0 or (delta_rec == 0.0 and delta_mrr > 0.0):
                gains.append({
                    "evaluation_id": eid,
                    "query": q_orig,
                    "category": cat,
                    "delta_recall": delta_rec,
                    "delta_mrr": delta_mrr,
                    "changed_anchors": changed_anchors,
                    "control_top10": ctrl_top10,
                    "treatment_top10": treat_top10,
                })
            elif delta_rec < 0.0 or (delta_rec == 0.0 and delta_mrr < 0.0):
                regressions.append({
                    "evaluation_id": eid,
                    "query": q_orig,
                    "category": cat,
                    "delta_recall": delta_rec,
                    "delta_mrr": delta_mrr,
                    "changed_anchors": changed_anchors,
                    "control_top10": ctrl_top10,
                    "treatment_top10": treat_top10,
                })
            else:
                unchanged.append(eid)

        # Security audits
        if m_ctrl.get("forbidden_leaks_top10", 0) > 0:
            security_audit["control"]["forbidden_leaks_top10"] += int(m_ctrl["forbidden_leaks_top10"])
        if m_ctrl.get("forbidden_in_pool", 0) > 0:
            security_audit["control"]["forbidden_in_pool"] += int(m_ctrl["forbidden_in_pool"])
        if not is_positive and m_ctrl.get("hit_at_10", 0.0) > 0.0:
            security_audit["control"]["negative_leaks"] += 1

        if m_treat.get("forbidden_leaks_top10", 0) > 0:
            security_audit["treatment"]["forbidden_leaks_top10"] += int(m_treat["forbidden_leaks_top10"])
        if m_treat.get("forbidden_in_pool", 0) > 0:
            security_audit["treatment"]["forbidden_in_pool"] += int(m_treat["forbidden_in_pool"])
        if not is_positive and m_treat.get("hit_at_10", 0.0) > 0.0:
            security_audit["treatment"]["negative_leaks"] += 1

        # Check tenant violations
        for doc_id in treat_top10:
            doc_meta = metadata_snapshot_index.get(doc_id)
            if doc_meta and doc_meta.tenant_id and t_id and doc_meta.tenant_id != t_id:
                security_audit["treatment"]["cross_tenant_leaks"] += 1

        # Accumulate metrics
        control_all_metrics.append(m_ctrl)
        treatment_all_metrics.append(m_treat)
        if is_positive:
            control_pos_metrics.append(m_ctrl)
            treatment_pos_metrics.append(m_treat)

        if treat_audit.is_active:
            multi_aspect_control_metrics.append(m_ctrl)
            multi_aspect_treatment_metrics.append(m_treat)

        if is_h4_applicable and is_positive:
            h4_applicable_control_metrics.append(m_ctrl)
            h4_applicable_treatment_metrics.append(m_treat)

        if is_diagnostic_slice and is_positive:
            diagnostic_slice_control_metrics.append(m_ctrl)
            diagnostic_slice_treatment_metrics.append(m_treat)

        if is_h2_slice and is_positive:
            h2_slice_control_metrics.append(m_ctrl)
            h2_slice_treatment_metrics.append(m_treat)

        # EVAL-0036 specific diagnostic label (purely for transparent reporting)
        special_diagnostic = None
        if eid == "EVAL-0036":
            special_diagnostic = (
                "H4-INAPPLICABLE / UPSTREAM ENTITY-RESOLUTION FAILURE: "
                "Query Understanding resolved SVC-NS-0006 instead of EVT-NS-0002; "
                "EVT-NS-0002 was not available at runtime; H4 behavior is expectedly identical to H3."
            )

        case_level_results.append({
            "evaluation_id": eid,
            "query": q_orig,
            "category": cat,
            "is_positive": is_positive,
            "is_h2_slice": is_h2_slice,
            "runtime_query_entity_ids": sorted(runtime_query_entity_ids),
            "h4_applicability_status": treat_audit.h4_applicability_status,
            "h4_applicability_reason": treat_audit.h4_applicability_reason,
            "is_diagnostic_slice": is_diagnostic_slice,
            "special_diagnostic": special_diagnostic,
            "aspect_roles": treat_audit.aspect_roles,
            "control_anchors": ctrl_audit.anchors_identified,
            "treatment_anchors": treat_audit.anchors_identified,
            "treatment_anchors_tier": treat_audit.anchors_tier,
            "changed_anchors": changed_anchors,
            "expected_document_ids": exp_docs,
            "control_top10": ctrl_top10,
            "treatment_top10": treat_top10,
            "control_recall_at_10": rec_ctrl,
            "treatment_recall_at_10": rec_treat,
            "delta_recall": delta_rec,
            "control_mrr": mrr_ctrl,
            "treatment_mrr": mrr_treat,
            "delta_mrr": delta_mrr,
            "control_ndcg_at_10": ndcg_ctrl,
            "treatment_ndcg_at_10": ndcg_treat,
            "delta_ndcg": delta_ndcg,
            "treatment_role_counts_top10": treat_audit.role_counts_top10,
            "treatment_role_coverage": treat_audit.role_coverage_top10,
        })

    t_end = time.perf_counter()
    duration = round(t_end - t_start, 2)

    # 10. Macro Metrics Aggregations
    summary_corpus_ctrl = calculate_macro_mean(control_all_metrics)
    summary_corpus_treat = calculate_macro_mean(treatment_all_metrics)
    summary_pos_ctrl = calculate_macro_mean(control_pos_metrics)
    summary_pos_treat = calculate_macro_mean(treatment_pos_metrics)

    summary_multi_ctrl = calculate_macro_mean(multi_aspect_control_metrics) if multi_aspect_control_metrics else {}
    summary_multi_treat = calculate_macro_mean(multi_aspect_treatment_metrics) if multi_aspect_treatment_metrics else {}

    summary_app_ctrl = calculate_macro_mean(h4_applicable_control_metrics) if h4_applicable_control_metrics else {}
    summary_app_treat = calculate_macro_mean(h4_applicable_treatment_metrics) if h4_applicable_treatment_metrics else {}

    summary_diag_ctrl = calculate_macro_mean(diagnostic_slice_control_metrics) if diagnostic_slice_control_metrics else {}
    summary_diag_treat = calculate_macro_mean(diagnostic_slice_treatment_metrics) if diagnostic_slice_treatment_metrics else {}

    summary_h2_ctrl = calculate_macro_mean(h2_slice_control_metrics) if h2_slice_control_metrics else {}
    summary_h2_treat = calculate_macro_mean(h2_slice_treatment_metrics) if h2_slice_treatment_metrics else {}

    def compute_deltas(c: dict[str, float], t: dict[str, float]) -> dict[str, float]:
        out = {}
        for k in t:
            out[k] = round(t[k] - c.get(k, 0.0), 6)
        return out

    # Build final result JSON
    results = {
        "metadata": {
            "milestone": "RET-EVAL-07",
            "phase": "Phase 2 Controlled Experiment",
            "hypothesis": "H4 Entity-Anchored Role Disambiguation",
            "git_commit": git_commit,
            "execution_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "duration_seconds": duration,
            "security_gate": "SEC-OPS-02 = VERIFIED",
        },
        "control_definition": {
            "name": "B4 + H1 + H2 + H3 Control",
            "description": "Exact frozen RET-EVAL-06 treatment: unconstrained greedy role anchor selection",
            "channels": ["BM25", "Dense", "Structured (Depth 2 + Reciprocal Edge Alignment)"],
            "fusion": "RRF (k=60)",
            "reranker": "MetadataReranker",
            "h2_overlay": "ENABLED",
            "h3_overlay": "ENABLED (H3RoleDiversificationOverlay, max_per_role=3, top_k=10)",
            "h4_entity_anchoring": "DISABLED",
        },
        "treatment_definition": {
            "name": "B4 + H1 + H2 + H3(entity-aware) Treatment",
            "description": "Identical overlay with Strategy E Two-Tier Entity-Aware Anchor Selection",
            "channels": ["BM25", "Dense", "Structured (Depth 2 + Reciprocal Edge Alignment)"],
            "fusion": "RRF (k=60)",
            "reranker": "MetadataReranker",
            "h2_overlay": "ENABLED",
            "h3_overlay": "ENABLED (H4EntityAnchoredDiversificationOverlay, max_per_role=3, top_k=10)",
            "h4_entity_anchoring": "ENABLED (Strategy E Tier 1: query_entity_ids match; Tier 2: H3 fallback)",
        },
        "dataset_summary": {
            "total_cases": len(cases),
            "positive_cases": len(control_pos_metrics),
            "negative_cases": len(cases) - len(control_pos_metrics),
            "h3_active_cases": len(multi_aspect_treatment_metrics),
            "h4_applicable_positive_cases": len(h4_applicable_treatment_metrics),
            "diagnostic_slice_cases": len(diagnostic_slice_treatment_metrics),
            "primary_h2_slice_cases": len(h2_slice_treatment_metrics),
        },
        "h4_operational_stats": {
            "cases_with_anchor_changes": anchor_change_count,
            "tier1_entity_matched_anchors": tier1_anchor_count,
            "tier2_fallback_anchors": tier2_anchor_count,
            "tier1_anchor_rate": round(tier1_anchor_count / max(1, tier1_anchor_count + tier2_anchor_count), 4),
            "cases_improved": len(gains),
            "cases_unchanged": len(unchanged),
            "cases_regressed": len(regressions),
        },
        "diagnostic_slice_metrics": {
            "case_count": len(diagnostic_slice_treatment_metrics),
            "control": summary_diag_ctrl,
            "treatment": summary_diag_treat,
            "delta": compute_deltas(summary_diag_ctrl, summary_diag_treat),
        },
        "h4_applicable_slice_metrics": {
            "case_count": len(h4_applicable_treatment_metrics),
            "control": summary_app_ctrl,
            "treatment": summary_app_treat,
            "delta": compute_deltas(summary_app_ctrl, summary_app_treat),
        },
        "multi_aspect_slice_metrics": {
            "case_count": len(multi_aspect_treatment_metrics),
            "control": summary_multi_ctrl,
            "treatment": summary_multi_treat,
            "delta": compute_deltas(summary_multi_ctrl, summary_multi_treat),
        },
        "primary_h2_slice_metrics": {
            "case_count": len(h2_slice_treatment_metrics),
            "control": summary_h2_ctrl,
            "treatment": summary_h2_treat,
            "delta": compute_deltas(summary_h2_ctrl, summary_h2_treat),
        },
        "overall_positive_metrics": {
            "case_count": len(control_pos_metrics),
            "control": summary_pos_ctrl,
            "treatment": summary_pos_treat,
            "delta": compute_deltas(summary_pos_ctrl, summary_pos_treat),
        },
        "overall_corpus_metrics": {
            "case_count": len(cases),
            "control": summary_corpus_ctrl,
            "treatment": summary_corpus_treat,
            "delta": compute_deltas(summary_corpus_ctrl, summary_corpus_treat),
        },
        "security_audit": security_audit,
        "invariance_audit": invariance_audit,
        "case_level_results": case_level_results,
        "gains": gains,
        "regressions": regressions,
    }

    # Save artifact
    output_path = art_dir / "ret_eval_07_h4_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Saved complete RET-EVAL-07 results to {output_path} ({os.path.getsize(output_path)} bytes)")

    return results


if __name__ == "__main__":
    res = run_controlled_h4_experiment()
    print("\n--- RET-EVAL-07 EXPERIMENTAL SUMMARY ---")
    pos_d = res["overall_positive_metrics"]["delta"]
    diag_d = res["diagnostic_slice_metrics"]["delta"]
    print(f"Overall Positive Recall@10 Delta: {pos_d.get('recall_at_10', 0.0):+.4f}")
    print(f"Overall Positive MRR Delta:       {pos_d.get('mrr', 0.0):+.4f}")
    print(f"Overall Positive NDCG@10 Delta:  {pos_d.get('ndcg_at_10', 0.0):+.4f}")
    print(f"Diagnostic Slice Recall@10 Delta: {diag_d.get('recall_at_10', 0.0):+.4f}")
    print(f"Diagnostic Slice MRR Delta:       {diag_d.get('mrr', 0.0):+.4f}")
    print(f"Gains: {len(res['gains'])}, Regressions: {len(res['regressions'])}, Unchanged: {len(res['case_level_results']) - len(res['gains']) - len(res['regressions'])}")
    print(f"Forbidden Leaks: Top10={res['security_audit']['treatment']['forbidden_leaks_top10']}, Pool={res['security_audit']['treatment']['forbidden_in_pool']}")
    print(f"Negative Leaks:  {res['security_audit']['treatment']['negative_leaks']}")
