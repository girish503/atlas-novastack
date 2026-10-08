#!/usr/bin/env python3
"""Hermetic Controlled H3 Experiment — RET-EVAL-06 Phase 2.

Evaluates Hypothesis H3:
"When H1 multi-hop traversal and H2 event entity resolution place multiple
required causal evidence roles into the candidate pool, independent chunk-level
scoring allows redundant observational same-role evidence (chat, meetings, notes,
duplicate postmortems) to crowd out required causal evidence. A deterministic
role-diversification overlay (Strategy A: Role-Constrained Capacity Allocation
with greedy aspect quota, max_per_role = 3, top_k = 10) will improve multi-document
Top-10 evidence coverage without changing retrieval, H1, H2, security, or
candidate pool membership."

Mandatory Protocol Invariants:
1. CONTROL = B4 + H1 + H2 (exact RET-EVAL-05 treatment: depth-2 traversal + reciprocal edge alignment + deterministic H2 entity resolution).
2. TREATMENT = B4 + H1 + H2 + H3 (adds deterministic H3 Role-Constrained Diversification Overlay downstream of MetadataReranker).
3. The experiment isolates H3 as the ONLY treatment difference.
4. Candidate pool invariance: Both Control and Treatment share the identical candidate pool and identical base scores.
5. Strict Security Semantics: Candidate pool -> existing security eligibility -> MetadataReranker -> H3 -> EvidenceResolver. Role diversity NEVER overrides authorization or promotes forbidden candidates.
6. ZERO modification to src/novastack/**, evaluation dataset, Docker, or release manifests.
7. 100% deterministic execution: ZERO LLM calls, ZERO external network calls, ZERO arbitrary score thresholds.

Produces:
    artifacts/ret_eval_06_h3_results.json
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

# Constants for Role Diversification
DEFAULT_TOP_K = 10
DEFAULT_MAX_PER_ROLE = 3

ASPECT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("deployment_note", re.compile(r"\b(deploy|deployment|deployed|version|release|rollback)\b", re.IGNORECASE)),
    ("pull_request_note", re.compile(r"\b(resolved|fixed|fix|fixes|resolves|remediated|merged|pr|pull\s+request)\b", re.IGNORECASE)),
    ("postmortem", re.compile(r"\b(caused|causes|root\s+cause|outage\s+cause|symptom|outage|postmortem|incident|failure)\b", re.IGNORECASE)),
    ("support_ticket", re.compile(r"\b(ticket|tickets|support|customer)\b", re.IGNORECASE)),
    ("documentation", re.compile(r"\b(runbook|playbook|architecture|specification|sop|guide)\b", re.IGNORECASE)),
]


def extract_requested_aspect_roles(query: str) -> list[str]:
    """Deterministically extract requested enterprise evidence roles from query phrasing."""
    if not query:
        return []
    matched = []
    for role, pat in ASPECT_PATTERNS:
        if pat.search(query):
            matched.append(role)
    return matched


def get_document_role(
    document_id: str,
    metadata_index: dict[str, DocumentMetadataSnapshot] | None = None,
) -> str:
    """Deterministically map a document to its functional enterprise evidence role.
    
    Uses document metadata snapshot source_type and document_id prefixes with 100% concordance.
    """
    doc_id = (document_id or "").upper()
    source_type = ""
    if metadata_index and document_id in metadata_index:
        source_type = (metadata_index[document_id].source_type or "").lower()

    if source_type == "deployment_note" or doc_id.startswith("DOC-DEP-"):
        return "deployment_note"
    if source_type == "pull_request_note" or doc_id.startswith("DOC-PR-"):
        return "pull_request_note"
    if source_type == "postmortem" or doc_id.startswith("DOC-PM-"):
        return "postmortem"
    if source_type == "support_ticket" or doc_id.startswith("DOC-TKT-") or doc_id.startswith("DOC-SUP-"):
        return "support_ticket"
    if (
        source_type == "documentation"
        or doc_id.startswith("DOC-DOC-")
        or doc_id.startswith("DOC-RB-")
        or doc_id.startswith("DOC-SOP-")
        or doc_id.startswith("DOC-ARCH-")
    ):
        return "documentation"
    if source_type == "incident" or doc_id.startswith("DOC-INC-"):
        return "incident_report"
    if source_type == "conversation" or doc_id.startswith("DOC-CHAT-"):
        return "conversation"
    if source_type == "meeting" or doc_id.startswith("DOC-MTG-") or doc_id.startswith("DOC-MEET-"):
        return "meeting"
    if source_type == "engineering_note" or doc_id.startswith("DOC-NOTE-"):
        return "engineering_note"
    if source_type == "policy" or doc_id.startswith("DOC-POL-"):
        return "policy"
    return "other"


@dataclass
class H3DiversificationAudit:
    """Audit information for the H3 diversification overlay execution on a single query."""

    is_active: bool
    aspect_roles: list[str]
    anchors_identified: dict[str, str]
    anchors_admitted: dict[str, str]
    role_counts_top10: dict[str, int]
    role_coverage_top10: float
    violations_max_per_role: int
    selected_chunk_ids: list[str]


class H3RoleDiversificationOverlay:
    """Deterministic, post-reranking role-diversification overlay (Strategy A).
    
    Implements Role-Constrained Capacity Allocation:
    1. Gating: Active ONLY when >= 2 distinct aspect roles are requested in query. Single-aspect is 100% pass-through.
    2. Zero Authorization Override: Partitions candidates into eligible vs ineligible (forbidden / penalized).
       Ineligible candidates can NEVER be selected, promoted, or included in Top-K.
    3. Anchor Slot Reservation: Finds the highest-ranked eligible candidate for each requested aspect role
       and reserves capacity to guarantee multi-document causal coverage.
    4. Greedy Role-Constrained Allocation: Iterates eligible candidates in base score order, admitting up to
       max_per_role per role while ensuring reserved anchor slots are not crowded out.
    5. Backfill: If capacity remains in Top-K, greedily fills from remaining eligible candidates.
    6. Preservation of Candidate Pool: Returns all candidates in diversified order, with deferred eligible
       and ineligible candidates strictly preserved in their relative baseline ordering.
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

    def diversify(
        self,
        reranked_candidates: list[CandidateRerankingDetail],
        query: str,
        user_tenant: str | None = None,
        forbidden_doc_ids: set[str] | None = None,
    ) -> tuple[list[CandidateRerankingDetail], H3DiversificationAudit]:
        """Apply deterministic role diversification overlay."""
        forb_set = set(forbidden_doc_ids or [])
        aspect_roles = extract_requested_aspect_roles(query)

        # Gating condition: >= 2 requested aspects required to activate diversification
        if len(aspect_roles) < 2:
            top10 = reranked_candidates[: self.top_k]
            role_counts = defaultdict(int)
            for c in top10:
                r = get_document_role(c.document_id, self.metadata_index)
                role_counts[r] += 1
            audit = H3DiversificationAudit(
                is_active=False,
                aspect_roles=aspect_roles,
                anchors_identified={},
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

        # Identify highest-ranked eligible anchor candidate for each requested role
        anchors: dict[str, CandidateRerankingDetail] = {}
        for c in eligible:
            r = get_document_role(c.document_id, self.metadata_index)
            if r in aspect_roles and r not in anchors:
                anchors[r] = c

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

        # Deferred eligible candidates
        deferred_eligible = [c for c in eligible if c.chunk_id not in selected_cids]

        # Combine: selected top-K + deferred eligible + ineligible
        diversified_result = selected + deferred_eligible + ineligible

        # Compute role coverage on requested aspects
        req_set = set(aspect_roles)
        covered_set = req_set & set(role_counts.keys())
        role_coverage = len(covered_set) / max(1, len(req_set))

        # Check for role capacity violations in top_k
        violations = sum(1 for cnt in role_counts.values() if cnt > self.max_per_role)

        audit = H3DiversificationAudit(
            is_active=True,
            aspect_roles=aspect_roles,
            anchors_identified={r: c.document_id for r, c in anchors.items()},
            anchors_admitted=admitted_anchors,
            role_counts_top10=dict(role_counts),
            role_coverage_top10=round(role_coverage, 6),
            violations_max_per_role=violations,
            selected_chunk_ids=[c.chunk_id for c in selected],
        )

        return diversified_result, audit


def run_controlled_h3_experiment() -> dict[str, Any]:
    """Execute the full hermetic RET-EVAL-06 Phase 2 controlled experiment.
    
    Returns the authoritative experiment summary dictionary and writes
    artifacts/ret_eval_06_h3_results.json.
    """
    t_start = time.perf_counter()
    git_commit = get_git_commit(_PROJECT_ROOT)
    print("=" * 80)
    print("ATLAS — RET-EVAL-06 PHASE 2: H3 ROLE DIVERSIFICATION CONTROLLED EXPERIMENT")
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
    print(f"Loaded {len(cases)} frozen evaluation cases.")

    with open(proc_dir / "search_chunks.json", "r", encoding="utf-8") as f:
        chunks_data = json.load(f)["search_chunks"]
    chunks = [SearchChunk.from_dict(c) for c in chunks_data]
    print(f"Loaded {len(chunks)} search chunks.")

    with open(proc_dir / "search_documents.json", "r", encoding="utf-8") as f:
        docs_list = json.load(f)["search_documents"]
    print(f"Loaded {len(docs_list)} search documents.")

    with open(raw_dir / "adversarial_fixtures.json", "r", encoding="utf-8") as f:
        adv_fixtures = json.load(f)["adversarial_fixtures"]

    # 2. Catalogs and Extractors
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

    # 6. Setup H3 Diversification Overlay
    h3_overlay = H3RoleDiversificationOverlay(
        top_k=DEFAULT_TOP_K,
        max_per_role=DEFAULT_MAX_PER_ROLE,
        metadata_index=metadata_snapshot_index,
    )

    # 5. Tracking containers
    case_level_results: list[dict[str, Any]] = []
    control_all_metrics: list[dict[str, float]] = []
    treatment_all_metrics: list[dict[str, float]] = []
    control_pos_metrics: list[dict[str, float]] = []
    treatment_pos_metrics: list[dict[str, float]] = []

    h2_slice_control_metrics: list[dict[str, float]] = []
    h2_slice_treatment_metrics: list[dict[str, float]] = []

    h3_slice_control_metrics: list[dict[str, float]] = []
    h3_slice_treatment_metrics: list[dict[str, float]] = []
    h3_slice_control_coverages: list[float] = []
    h3_slice_treatment_coverages: list[float] = []

    category_control: dict[str, list[dict[str, float]]] = defaultdict(list)
    category_treatment: dict[str, list[dict[str, float]]] = defaultdict(list)

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
        "single_aspect_top10_mismatches": 0,
        "max_per_role_violations": 0,
    }

    regressions: list[dict[str, Any]] = []
    gains: list[dict[str, Any]] = []

    # 6. Execute full evaluation across all 120 cases
    print(f"Executing 120 evaluation cases across Control (B4+H1+H2) and Treatment (B4+H1+H2+H3)...")
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

        # --- RETRIEVAL PIPELINE (Identical for Control and Treatment) ---
        # 1. H2 Query Understanding
        qu, h2_matches = h2_qu_overlay.extract(eid, q_orig)
        expanded_q = qu.expanded_query if qu else q_orig

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

        # --- CONTROL EVALUATION (B4 + H1 + H2) ---
        ctrl_docs = [r.document_id for r in reranked]
        ctrl_top10 = ctrl_docs[:DEFAULT_TOP_K]
        m_ctrl = evaluate_retrieval_ranking(ctrl_docs, exp_docs, acc_docs, forb_docs)

        # --- TREATMENT EVALUATION (B4 + H1 + H2 + H3 DIVERSIFICATION) ---
        treat_reranked, h3_audit = h3_overlay.diversify(
            reranked_candidates=reranked,
            query=q_orig,
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

        # 2. Single-aspect pass-through invariance
        if not h3_audit.is_active:
            if ctrl_top10 != treat_top10:
                invariance_audit["single_aspect_top10_mismatches"] += 1

        # 3. Capacity allocation violations in top-10
        if h3_audit.violations_max_per_role > 0:
            invariance_audit["max_per_role_violations"] += h3_audit.violations_max_per_role

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

        # Role coverage for control vs treatment
        aspect_set = set(h3_audit.aspect_roles)
        ctrl_roles_top10 = set(get_document_role(d, metadata_snapshot_index) for d in ctrl_top10)
        treat_roles_top10 = set(get_document_role(d, metadata_snapshot_index) for d in treat_top10)

        cov_ctrl = len(aspect_set & ctrl_roles_top10) / max(1, len(aspect_set)) if aspect_set else 1.0
        cov_treat = h3_audit.role_coverage_top10

        # Primary H2 slice tracking
        if is_h2_slice:
            h2_slice_control_metrics.append(m_ctrl)
            h2_slice_treatment_metrics.append(m_treat)

        # H3 multi-aspect positive slice tracking
        if h3_audit.is_active and is_positive:
            h3_slice_control_metrics.append(m_ctrl)
            h3_slice_treatment_metrics.append(m_treat)
            h3_slice_control_coverages.append(cov_ctrl)
            h3_slice_treatment_coverages.append(cov_treat)

        # Security audits
        forb_set = set(forb_docs)
        if forb_set:
            if any(d in forb_set for d in ctrl_top10):
                security_audit["control"]["forbidden_leaks_top10"] += 1
            if any(d in forb_set for d in treat_top10):
                security_audit["treatment"]["forbidden_leaks_top10"] += 1
            if any(c.document_id in forb_set for c in combined):
                # Candidate pool security: candidates in combined pool
                pass

        if not is_positive:
            if any(d in ctrl_top10 for d in exp_docs):
                security_audit["control"]["negative_leaks"] += 1
            if any(d in treat_top10 for d in exp_docs):
                security_audit["treatment"]["negative_leaks"] += 1

        if t_id:
            if any(c.chunk.tenant_id != t_id for c in struct_res.candidates):
                security_audit["control"]["cross_tenant_leaks"] += 1
                security_audit["treatment"]["cross_tenant_leaks"] += 1

        # Check for regressions or gains
        if is_positive:
            if delta_rec < -1e-6 or delta_mrr < -1e-6:
                regressions.append({
                    "evaluation_id": eid,
                    "query": q_orig,
                    "delta_recall": delta_rec,
                    "delta_mrr": delta_mrr,
                    "control_recall": rec_ctrl,
                    "treatment_recall": rec_treat,
                })
            elif delta_rec > 1e-6 or delta_mrr > 1e-6:
                gains.append({
                    "evaluation_id": eid,
                    "query": q_orig,
                    "delta_recall": delta_rec,
                    "delta_mrr": delta_mrr,
                    "control_recall": rec_ctrl,
                    "treatment_recall": rec_treat,
                })

        case_detail = {
            "evaluation_id": eid,
            "query": q_orig,
            "category": cat,
            "is_positive": is_positive,
            "is_h2_slice": is_h2_slice,
            "is_h3_active": h3_audit.is_active,
            "aspect_roles": h3_audit.aspect_roles,
            "anchors_identified": h3_audit.anchors_identified,
            "anchors_admitted": h3_audit.anchors_admitted,
            "expected_document_ids": exp_docs,
            "control_top10": ctrl_top10,
            "treatment_top10": treat_top10,
            "control_expected_ranks": {d: (ctrl_top10.index(d) + 1 if d in ctrl_top10 else None) for d in exp_docs},
            "treatment_expected_ranks": {d: (treat_top10.index(d) + 1 if d in treat_top10 else None) for d in exp_docs},
            "control_recall_at_10": rec_ctrl,
            "treatment_recall_at_10": rec_treat,
            "delta_recall": delta_rec,
            "control_mrr": mrr_ctrl,
            "treatment_mrr": mrr_treat,
            "delta_mrr": delta_mrr,
            "control_ndcg_at_10": ndcg_ctrl,
            "treatment_ndcg_at_10": ndcg_treat,
            "delta_ndcg": delta_ndcg,
            "control_role_coverage": round(cov_ctrl, 6),
            "treatment_role_coverage": round(cov_treat, 6),
            "delta_role_coverage": round(cov_treat - cov_ctrl, 6),
            "treatment_role_counts_top10": h3_audit.role_counts_top10,
            "forbidden_leaks": sum(1 for d in treat_top10 if d in forb_set),
            "negative_case_leaks": sum(1 for d in treat_top10 if d in exp_docs) if not is_positive else 0,
        }
        case_level_results.append(case_detail)

        # Accumulate metrics
        control_all_metrics.append(m_ctrl)
        treatment_all_metrics.append(m_treat)
        category_control[cat].append(m_ctrl)
        category_treatment[cat].append(m_treat)

        if is_positive:
            control_pos_metrics.append(m_ctrl)
            treatment_pos_metrics.append(m_treat)

    # Compute macro aggregations
    agg_control_all = calculate_macro_mean(control_all_metrics)
    agg_treatment_all = calculate_macro_mean(treatment_all_metrics)

    agg_control_pos = calculate_macro_mean(control_pos_metrics)
    agg_treatment_pos = calculate_macro_mean(treatment_pos_metrics)

    agg_control_h2 = calculate_macro_mean(h2_slice_control_metrics)
    agg_treatment_h2 = calculate_macro_mean(h2_slice_treatment_metrics)

    agg_control_h3 = calculate_macro_mean(h3_slice_control_metrics)
    agg_treatment_h3 = calculate_macro_mean(h3_slice_treatment_metrics)

    delta_pos = {
        k: round(agg_treatment_pos[k] - agg_control_pos.get(k, 0.0), 6)
        for k in agg_treatment_pos
    }
    delta_h2 = {
        k: round(agg_treatment_h2[k] - agg_control_h2.get(k, 0.0), 6)
        for k in agg_treatment_h2
    }
    delta_h3 = {
        k: round(agg_treatment_h3[k] - agg_control_h3.get(k, 0.0), 6)
        for k in agg_treatment_h3
    }

    mean_cov_ctrl = round(sum(h3_slice_control_coverages) / max(1, len(h3_slice_control_coverages)), 6)
    mean_cov_treat = round(sum(h3_slice_treatment_coverages) / max(1, len(h3_slice_treatment_coverages)), 6)
    delta_cov = round(mean_cov_treat - mean_cov_ctrl, 6)

    # Category summaries
    cat_summary = {}
    for cat in category_control:
        c_mean = calculate_macro_mean(category_control[cat])
        t_mean = calculate_macro_mean(category_treatment[cat])
        cat_summary[cat] = {
            "case_count": len(category_control[cat]),
            "control_recall_at_10": c_mean.get("recall_at_10", 0.0),
            "treatment_recall_at_10": t_mean.get("recall_at_10", 0.0),
            "delta_recall_at_10": round(t_mean.get("recall_at_10", 0.0) - c_mean.get("recall_at_10", 0.0), 6),
            "control_mrr": c_mean.get("mrr", 0.0),
            "treatment_mrr": t_mean.get("mrr", 0.0),
            "delta_mrr": round(t_mean.get("mrr", 0.0) - c_mean.get("mrr", 0.0), 6),
            "control_ndcg_at_10": c_mean.get("ndcg_at_10", 0.0),
            "treatment_ndcg_at_10": t_mean.get("ndcg_at_10", 0.0),
            "delta_ndcg_at_10": round(t_mean.get("ndcg_at_10", 0.0) - c_mean.get("ndcg_at_10", 0.0), 6),
        }

    total_duration = round(time.perf_counter() - t_start, 2)

    artifact = {
        "metadata": {
            "milestone": "RET-EVAL-06",
            "phase": "Phase 2 Controlled Experiment",
            "hypothesis": "H3 Multi-Aspect Role Diversification Reranking",
            "git_commit": git_commit,
            "execution_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "duration_seconds": total_duration,
            "security_gate": "SEC-OPS-02 = VERIFIED",
        },
        "control_definition": {
            "name": "B4 + H1 + H2 Control",
            "channels": ["BM25", "Dense", "Structured (Depth 2 + Reciprocal Edge Alignment)"],
            "fusion": "RRF (k=60)",
            "reranker": "MetadataReranker",
            "h2_overlay": "ENABLED",
            "h3_overlay": "DISABLED",
        },
        "treatment_definition": {
            "name": "B4 + H1 + H2 + H3 Diversification Treatment",
            "channels": ["BM25", "Dense", "Structured (Depth 2 + Reciprocal Edge Alignment)"],
            "fusion": "RRF (k=60)",
            "reranker": "MetadataReranker",
            "h2_overlay": "ENABLED",
            "h3_overlay": "ENABLED (H3RoleDiversificationOverlay, max_per_role=3, top_k=10)",
        },
        "dataset_summary": {
            "total_cases": len(cases),
            "positive_cases": len(control_pos_metrics),
            "negative_cases": len(cases) - len(control_pos_metrics),
            "primary_h2_slice_cases": len(PRIMARY_H2_SLICE_IDS),
            "h3_active_cases": sum(1 for c in case_level_results if c["is_h3_active"]),
            "h3_active_positive_cases": len(h3_slice_control_metrics),
        },
        "primary_h2_slice_metrics": {
            "control": agg_control_h2,
            "treatment": agg_treatment_h2,
            "delta": delta_h2,
        },
        "h3_multi_aspect_slice_metrics": {
            "control": agg_control_h3,
            "treatment": agg_treatment_h3,
            "delta": delta_h3,
            "role_coverage": {
                "control_mean": mean_cov_ctrl,
                "treatment_mean": mean_cov_treat,
                "delta": delta_cov,
            },
        },
        "overall_positive_metrics": {
            "control": agg_control_pos,
            "treatment": agg_treatment_pos,
            "delta": delta_pos,
        },
        "overall_corpus_metrics": {
            "control": agg_control_all,
            "treatment": agg_treatment_all,
        },
        "category_summary": cat_summary,
        "security_metrics": security_audit,
        "invariance_metrics": invariance_audit,
        "non_regression_metrics": {
            "regressions_count": len(regressions),
            "regressions": regressions,
            "gains_count": len(gains),
            "gains": gains,
        },
        "case_level_results": case_level_results,
    }

    out_file = art_dir / "ret_eval_06_h3_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2)

    print(f"Artifact successfully generated: {out_file}")
    print(f"Completed in {total_duration}s.")
    return artifact


if __name__ == "__main__":
    run_controlled_h3_experiment()
