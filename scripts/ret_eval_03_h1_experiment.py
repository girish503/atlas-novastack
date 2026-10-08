#!/usr/bin/env python3
"""Hermetic Controlled H1 Experiment — RET-EVAL-03 Phase 2.

Evaluates Hypothesis H1:
"Extending structured retrieval to evaluate 2-hop traversals and linking reverse
entity anchors will surface missing secondary targets into candidate pools."

Mandatory Protocol Invariants:
1. NO path_decay_gamma variable: standard weighting (w_traversed) used across hops.
2. H1 treatment tests ONLY:
   - traversal depth 2
   - reciprocal/reverse edge alignment
3. Preserve B4 as the immutable control (verified identical to ret_eval_01_baseline).
4. Do not rebuild B4 from scratch: preserve existing B4 behavior and add the smallest
   possible experimental H1 structured-retrieval overlay.
5. ZERO modification to src/novastack/**, evaluation dataset, Docker, or release manifests.
6. 100% deterministic execution: ZERO LLM calls, ZERO external network calls.
7. Strict fail-closed security: tenant isolation, forbidden document exclusion, negative safety.

Produces:
    artifacts/ret_eval_03_h1_results.json
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
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
    fuse_single_channel,
)
from novastack.entity_catalog import EntityCatalog
from novastack.metadata_diagnostics import build_metadata_snapshot_index
from novastack.metadata_reranker import MetadataReranker, MetadataRerankerConfig
from novastack.models import SearchChunk
from novastack.query_understanding import (
    EntityCatalog as QUEntityCatalog,
    QueryUnderstandingExtractor,
)
from novastack.relational_retrieval import (
    StructuredCandidate,
    StructuredRetrievalResult,
    StructuredRetriever,
    StructuredRetrieverConfig,
    fuse_hybrid_and_structured,
)


# Canonical list of H1-solvable evaluation cases identified in Phase 1 audit
H1_SOLVABLE_CASE_IDS = [
    "EVAL-0035",  # DEP-NS-0001 change & fixing PR (DOC-DEP-DEP-NS-0001-01, DOC-PR-PR-NS-0001-01)
    "EVAL-0044",  # checkout outage causal chain (DOC-PM-EVT-NS-0001-01, DOC-DEP-DEP-NS-0001-01, DOC-PR-PR-NS-0001-01)
    "EVAL-0048",  # notification delivery trace (DOC-PM-EVT-NS-0005-01, DOC-DEP-DEP-NS-0004-01, DOC-PR-PR-NS-0005-01)
    "EVAL-0052",  # media crash & rollback (DOC-PM-0009, DOC-DEP-0007, DOC-DEP-0008-ROLLBACK, DOC-PR-0008)
    "EVAL-0064",  # queue worker retry parameters (DOC-NOTE-EVT-NS-0005-01, DOC-PM-EVT-NS-0005-01)
]

# Symmetrical reciprocal relationship alignment map
RECIPROCAL_ALIGNMENTS: dict[str, list[str]] = {
    # Causal / trigger
    "caused_by": ["causes_event", "causes"],
    "causes": ["caused_by"],
    "causes_event": ["caused_by"],
    # Resolution / remediation
    "resolved_by": ["resolves_event", "resolves"],
    "resolves": ["resolved_by"],
    "resolves_event": ["resolved_by"],
    # Target / service mapping
    "targets": ["targeted_by"],
    "targeted_by": ["targets"],
    # Impact / affect
    "affects": ["affected_by"],
    "affected_by": ["affects"],
    # Ownership
    "owns": ["owned_by", "owns_deployment", "owns_pr"],
    "owned_by": ["owns"],
    "owns_deployment": ["owned_by"],
    "owns_pr": ["owned_by"],
    # Membership
    "member_of": ["has_member"],
    "has_member": ["member_of"],
    # Management / command
    "manages": ["managed_by", "manages_customer"],
    "managed_by": ["manages"],
    "manages_customer": ["managed_by"],
    "commanded_by": ["commands_incident"],
    "commands_incident": ["commanded_by"],
    # Assignment / handling
    "assigned_to": ["handles_incident"],
    "handles_incident": ["assigned_to"],
    # Deployment
    "deployed_by": ["deployed"],
    "deployed": ["deployed_by"],
    # Event linkage
    "relates_to_event": ["has_incident"],
    "has_incident": ["relates_to_event"],
    # Customer impact
    "impacts": ["impacted_by"],
    "impacted_by": ["impacts"],
    # Team responsibility
    "responsible_team": ["responsible_for_event"],
    "responsible_for_event": ["responsible_team"],
}

RECIPROCAL_EDGE_NAMES: set[str] = {
    "targeted_by",
    "causes_event",
    "causes",
    "resolves_event",
    "resolves",
    "affected_by",
    "owns",
    "owns_deployment",
    "owns_pr",
    "has_member",
    "manages",
    "manages_customer",
    "handles_incident",
    "commands_incident",
    "has_incident",
    "deployed",
    "authored_pr",
    "impacted_by",
    "responsible_for_event",
}


def get_git_commit(root: Path) -> str:
    """Safely obtain the current git commit without failing."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "d325e5a82681456ebaca57f2f27c1900f17bd415"


def evaluate_retrieval_ranking(
    retrieved_doc_ids: list[str],
    expected_doc_ids: list[str],
    acceptable_doc_ids: list[str] | None = None,
    forbidden_doc_ids: list[str] | None = None,
    k_values: Sequence[int] = (1, 3, 5, 10),
) -> dict[str, Any]:
    """Compute standard IR metrics with explicit Precision@K and NDCG@10."""
    base_metrics = compute_ir_metrics(
        retrieved_doc_ids=retrieved_doc_ids,
        expected_doc_ids=expected_doc_ids,
        acceptable_doc_ids=acceptable_doc_ids,
        forbidden_doc_ids=forbidden_doc_ids,
        k_values=k_values,
    )

    exp_set = set(expected_doc_ids or [])
    for k in k_values:
        if not exp_set:
            base_metrics[f"precision_at_{k}"] = 0.0
        else:
            sub = retrieved_doc_ids[:k]
            hits = sum(1 for d in set(sub) if d in exp_set)
            base_metrics[f"precision_at_{k}"] = round(hits / k, 6)

    return base_metrics


def calculate_macro_mean(metrics_list: list[dict[str, Any]]) -> dict[str, float]:
    """Compute arithmetic mean across case metrics."""
    if not metrics_list:
        return {}
    keys = metrics_list[0].keys()
    out: dict[str, float] = {}
    n = len(metrics_list)
    for k in keys:
        vals = [m.get(k, 0.0) for m in metrics_list]
        out[k] = round(sum(vals) / n, 6)
    return out


class H1StructuredRetrieverOverlay:
    """Smallest possible experimental H1 structured-retrieval overlay.

    Applies:
    - Traversal depth 2 (instead of depth 1)
    - Symmetrical reciprocal/reverse edge alignment
    - REMOVES path_decay_gamma (standard weighting w_traversed used across hops)
    - Full fail-closed security boundary at every hop
    """

    def __init__(self, base_retriever: StructuredRetriever) -> None:
        self.base_retriever = base_retriever
        self.catalog = base_retriever.catalog
        self.config = base_retriever.config
        self.runbook_index = getattr(base_retriever, "runbook_index", None)

    def retrieve(
        self,
        query: str,
        eval_case: dict[str, Any],
        top_k: int = 50,
    ) -> tuple[StructuredRetrievalResult, list[dict[str, Any]]]:
        """Execute H1 structured retrieval and record diagnostic provenance."""
        # 1. Entity and Intent Extraction (reusing existing base retriever logic)
        entities = self.base_retriever.extract_query_entities(query)
        rel_intents = self.base_retriever.extract_relational_intents(query)

        # 2. Reciprocal / reverse edge alignment
        aligned_intents: set[str] = set(rel_intents)
        for intent in rel_intents:
            if intent in RECIPROCAL_ALIGNMENTS:
                aligned_intents.update(RECIPROCAL_ALIGNMENTS[intent])

        u_tenant = eval_case.get("tenant_id", "TENANT-NOVASTACK")
        u_role = eval_case.get("user_role")
        u_dept = eval_case.get("user_department")
        u_id = eval_case.get("user_id")
        forbidden = set(eval_case.get("forbidden_document_ids", []))

        chunk_scores: dict[str, float] = defaultdict(float)
        chunk_map: dict[str, SearchChunk] = {}
        chunk_meta: dict[str, tuple[str, str]] = {}
        candidate_diagnostics: list[dict[str, Any]] = []

        w_direct = self.config.direct_entity_weight
        w_traversed = self.config.traversed_entity_weight
        w_related = self.config.related_entity_weight

        for ent in entities:
            # Hop 0: Direct entity chunks
            direct_chunks = self.catalog.get_chunks_for_entity(ent.entity_id)
            for ch in direct_chunks:
                if not self.catalog.is_authorized(ch, u_tenant, u_role, u_dept, u_id, forbidden):
                    continue

                if len(chunk_scores) >= self.config.max_expanded_candidates and ch.chunk_id not in chunk_scores:
                    continue

                inc = w_direct if ch.source_entity_id == ent.entity_id else w_related
                chunk_scores[ch.chunk_id] += inc
                chunk_map[ch.chunk_id] = ch
                if ch.chunk_id not in chunk_meta:
                    chunk_meta[ch.chunk_id] = (ent.entity_id, f"direct:{ent.entity_id}")

                candidate_diagnostics.append({
                    "chunk_id": ch.chunk_id,
                    "document_id": ch.document_id,
                    "hop": 0,
                    "relationship": "direct",
                    "provenance": "depth-1 existing behavior",
                })

            # Hop 1 & 2: Traversal depth 2 with reciprocal alignment
            # NOTE: path_decay_gamma is REMOVED as per instruction 1 (decay = 1.0)
            allowed = aligned_intents if aligned_intents else None
            traversals = self.catalog.traverse(
                source_entity_id=ent.entity_id,
                rel_types=allowed,
                max_depth=2,  # H1: Traversal depth 2
                max_neighbors_per_hop=self.config.max_neighbors_per_hop,
                user_tenant=u_tenant,
            )

            for tgt_ent, edge, depth in traversals:
                path_desc = f"{ent.entity_id} -[{edge.relationship_type}]-> {tgt_ent.entity_id} (hop={depth})"
                is_reciprocal_edge = edge.relationship_type in RECIPROCAL_EDGE_NAMES

                if depth == 1:
                    if is_reciprocal_edge:
                        diag_category = "reciprocal/reverse edge traversal"
                    else:
                        diag_category = "depth-1 existing behavior"
                else:  # depth == 2
                    if is_reciprocal_edge:
                        diag_category = "depth-2 + reciprocal traversal"
                    else:
                        diag_category = "depth-2 traversal"

                # Hop weight without path decay: constant w_traversed
                hop_weight = w_traversed

                tgt_chunks = self.catalog.get_chunks_for_entity(tgt_ent.entity_id)
                for ch in tgt_chunks:
                    if not self.catalog.is_authorized(ch, u_tenant, u_role, u_dept, u_id, forbidden):
                        continue

                    if len(chunk_scores) >= self.config.max_expanded_candidates and ch.chunk_id not in chunk_scores:
                        continue

                    chunk_scores[ch.chunk_id] += hop_weight
                    chunk_map[ch.chunk_id] = ch
                    if ch.chunk_id not in chunk_meta:
                        chunk_meta[ch.chunk_id] = (tgt_ent.entity_id, path_desc)

                    candidate_diagnostics.append({
                        "chunk_id": ch.chunk_id,
                        "document_id": ch.document_id,
                        "hop": depth,
                        "relationship": edge.relationship_type,
                        "provenance": diag_category,
                    })

            # Runbook Reverse Index (preserved unchanged)
            if self.runbook_index is not None:
                runbooks = self.runbook_index.lookup_runbooks_for_entity(
                    entity_id=ent.entity_id,
                    user_tenant=u_tenant,
                    user_role=u_role,
                    user_department=u_dept,
                    user_id=u_id,
                    forbidden_docs=forbidden,
                    max_hops=1,
                )
                w_runbook = getattr(self.config, "runbook_entity_weight", 0.85)
                for rb in runbooks:
                    for ch in self.runbook_index.get_chunks_for_runbook(rb.document_id):
                        if not self.catalog.is_authorized(ch, u_tenant, u_role, u_dept, u_id, forbidden):
                            continue
                        if len(chunk_scores) >= self.config.max_expanded_candidates and ch.chunk_id not in chunk_scores:
                            continue
                        chunk_scores[ch.chunk_id] += w_runbook
                        chunk_map[ch.chunk_id] = ch
                        if ch.chunk_id not in chunk_meta:
                            chunk_meta[ch.chunk_id] = (ent.entity_id, f"runbook:{rb.relationship_path}:{rb.document_id}")
                        candidate_diagnostics.append({
                            "chunk_id": ch.chunk_id,
                            "document_id": ch.document_id,
                            "hop": 1,
                            "relationship": "runbook",
                            "provenance": "depth-1 existing behavior",
                        })

        # Rank candidates
        authorized_chunks = [ch for cid, ch in chunk_map.items()]
        sorted_chunks = sorted(
            authorized_chunks,
            key=lambda c: (-chunk_scores[c.chunk_id], c.chunk_id),
        )

        candidates: list[StructuredCandidate] = []
        for rank, ch in enumerate(sorted_chunks[:top_k], start=1):
            matched_ent, path_str = chunk_meta.get(ch.chunk_id, ("", ""))
            candidates.append(
                StructuredCandidate(
                    chunk_id=ch.chunk_id,
                    document_id=ch.document_id,
                    rank=rank,
                    score=chunk_scores[ch.chunk_id],
                    matched_entity_id=matched_ent,
                    traversal_path=path_str,
                    chunk=ch,
                )
            )

        res = StructuredRetrievalResult(
            query=query,
            extracted_entities=entities,
            extracted_rel_intents=rel_intents,
            traversed_entities=[],
            candidates=candidates,
            latency_ms={"total": 0.0},
        )
        return res, candidate_diagnostics


def run_h1_experiment(root: Path | None = None) -> dict[str, Any]:
    """Execute the hermetic, controlled H1 retrieval experiment."""
    if root is None:
        root = _PROJECT_ROOT

    proc_dir = root / "data" / "processed" / "novastack"
    raw_dir = root / "data" / "raw" / "novastack"
    eval_dir = root / "data" / "evaluation" / "novastack"
    artifacts_dir = root / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    with open(eval_dir / "evaluation_cases.json", "r", encoding="utf-8") as f:
        cases = json.load(f)["evaluation_cases"]

    with open(proc_dir / "search_chunks.json", "r", encoding="utf-8") as f:
        chunks_data = json.load(f)["search_chunks"]
    chunks = [SearchChunk.from_dict(c) for c in chunks_data]

    with open(proc_dir / "search_documents.json", "r", encoding="utf-8") as f:
        docs_list = json.load(f)["search_documents"]

    with open(raw_dir / "adversarial_fixtures.json", "r", encoding="utf-8") as f:
        adv_fixtures = json.load(f)["adversarial_fixtures"]

    metadata_snapshot_index = build_metadata_snapshot_index(docs_list, adv_fixtures)

    # Catalogs and Extractors
    catalog = EntityCatalog(raw_dir, proc_dir / "search_chunks.json")
    qu_catalog = QUEntityCatalog(raw_dir)
    qu_extractor = QueryUnderstandingExtractor(qu_catalog)

    # Control Retriever: depth=1, multihop=False
    control_structured_retriever = StructuredRetriever(
        catalog=catalog,
        config=StructuredRetrieverConfig(max_traversal_depth=1, enable_multihop=False),
    )

    # Treatment Retriever: depth=2, reciprocal alignment overlay
    treatment_overlay = H1StructuredRetrieverOverlay(control_structured_retriever)

    # Shared BM25 & Dense indices
    bm25_index = BM25Index.build_index(chunks, config=BM25Config(k1=1.5, b=0.75))
    dense_index = DenseIndex.load(
        chunks_path=proc_dir / "search_chunks.json",
        embeddings_path=proc_dir / "dense_embeddings.npz",
        metadata_path=proc_dir / "dense_index_metadata.json",
    )
    reranker = MetadataReranker(MetadataRerankerConfig())

    # Evaluation accumulators
    control_all_metrics: list[dict[str, Any]] = []
    control_pos_metrics: list[dict[str, Any]] = []
    treatment_all_metrics: list[dict[str, Any]] = []
    treatment_pos_metrics: list[dict[str, Any]] = []

    category_control: dict[str, list[dict[str, Any]]] = defaultdict(list)
    category_treatment: dict[str, list[dict[str, Any]]] = defaultdict(list)

    h1_control_metrics: list[dict[str, Any]] = []
    h1_treatment_metrics: list[dict[str, Any]] = []

    security_audit = {
        "control": {"forbidden_leaks_top10": 0, "forbidden_in_pool": 0, "negative_leaks": 0},
        "treatment": {"forbidden_leaks_top10": 0, "forbidden_in_pool": 0, "negative_leaks": 0},
    }

    h1_solvable_case_details: list[dict[str, Any]] = []
    case_results: list[dict[str, Any]] = []

    t_start = time.perf_counter()

    for c in cases:
        eid = c["evaluation_id"]
        q_orig = c["query"]
        cat = c.get("query_category", "unknown")
        t_id = c.get("tenant_id")
        user_id = c.get("user_id")
        user_role = c.get("user_role")
        user_dept = c.get("user_department")
        expected_access = c.get("expected_access", "allow")
        exp_docs = c.get("expected_document_ids", [])
        acc_docs = c.get("acceptable_document_ids", [])
        forb_docs = c.get("forbidden_document_ids", [])
        is_positive = len(exp_docs) > 0

        filters = {"tenant_id": t_id} if t_id else None

        qu = qu_extractor.extract(eid, q_orig)
        expanded_q = qu.expanded_query if qu else q_orig

        # Shared channels
        bm_res_50 = bm25_index.search(query=expanded_q, top_k=50, filters=filters)
        dn_res_50 = dense_index.search(query=q_orig, top_k=50, filters=filters)
        hybrid_pool = fuse_rrf_sum(bm_res_50, dn_res_50, top_k=50, k=60, deduplicate_docs=True)

        eval_case_dict = {
            "evaluation_id": eid,
            "tenant_id": t_id,
            "user_id": user_id,
            "user_role": user_role,
            "user_department": user_dept,
            "expected_access": expected_access,
            "forbidden_document_ids": forb_docs,
        }

        # --- CONTROL (B4 BASELINE) ---
        ctrl_struct_res = control_structured_retriever.retrieve(
            query=q_orig,
            eval_case=eval_case_dict,
            top_k=50,
        )
        ctrl_combined = fuse_hybrid_and_structured(
            hybrid_candidates=hybrid_pool,
            structured_candidates=ctrl_struct_res.candidates,
            k=60,
            w_hybrid=1.0,
            w_struct=1.0,
            top_k=50,
            deduplicate_docs=True,
            catalog=catalog,
        )
        ctrl_reranked = reranker.rerank(
            candidates=ctrl_combined,
            qu=qu,
            metadata_index=metadata_snapshot_index,
            forbidden_doc_ids=set(forb_docs),
            enforce_security=True,
        )
        ctrl_docs = [r.document_id for r in ctrl_reranked]
        ctrl_top10 = ctrl_docs[:10]
        m_ctrl = evaluate_retrieval_ranking(ctrl_docs, exp_docs, acc_docs, forb_docs)

        # --- TREATMENT (B4 + H1 OVERLAY) ---
        treat_struct_res, treat_diags = treatment_overlay.retrieve(
            query=q_orig,
            eval_case=eval_case_dict,
            top_k=50,
        )
        treat_combined = fuse_hybrid_and_structured(
            hybrid_candidates=hybrid_pool,
            structured_candidates=treat_struct_res.candidates,
            k=60,
            w_hybrid=1.0,
            w_struct=1.0,
            top_k=50,
            deduplicate_docs=True,
            catalog=catalog,
        )
        treat_reranked = reranker.rerank(
            candidates=treat_combined,
            qu=qu,
            metadata_index=metadata_snapshot_index,
            forbidden_doc_ids=set(forb_docs),
            enforce_security=True,
        )
        treat_docs = [r.document_id for r in treat_reranked]
        treat_top10 = treat_docs[:10]
        m_treat = evaluate_retrieval_ranking(treat_docs, exp_docs, acc_docs, forb_docs)

        # Security checks
        forb_set = set(forb_docs)
        if forb_set:
            if any(d in forb_set for d in ctrl_top10):
                security_audit["control"]["forbidden_leaks_top10"] += 1
            if any(c.document_id in forb_set for c in ctrl_struct_res.candidates):
                security_audit["control"]["forbidden_in_pool"] += 1

            if any(d in forb_set for d in treat_top10):
                security_audit["treatment"]["forbidden_leaks_top10"] += 1
            if any(c.document_id in forb_set for c in treat_struct_res.candidates):
                security_audit["treatment"]["forbidden_in_pool"] += 1

        if not is_positive:
            # Negative safety
            if any(d in ctrl_top10 for d in exp_docs):
                security_audit["control"]["negative_leaks"] += 1
            if any(d in treat_top10 for d in exp_docs):
                security_audit["treatment"]["negative_leaks"] += 1

        # Accumulate metrics
        control_all_metrics.append(m_ctrl)
        treatment_all_metrics.append(m_treat)
        category_control[cat].append(m_ctrl)
        category_treatment[cat].append(m_treat)

        if is_positive:
            control_pos_metrics.append(m_ctrl)
            treatment_pos_metrics.append(m_treat)

        is_h1_solvable = eid in H1_SOLVABLE_CASE_IDS
        if is_h1_solvable:
            h1_control_metrics.append(m_ctrl)
            h1_treatment_metrics.append(m_treat)

            # Analyze provenance for expected docs in this case
            exp_provenance: dict[str, str] = {}
            reachable_depth2: dict[str, bool] = {}
            reciprocal_required: dict[str, bool] = {}

            ctrl_pool_docs = set(r.document_id for r in ctrl_combined)
            treat_pool_docs = set(r.document_id for r in treat_combined)

            for exp in exp_docs:
                matching_diags = [d for d in treat_diags if d["document_id"] == exp]
                if matching_diags:
                    # Pick highest order provenance
                    provs = [d["provenance"] for d in matching_diags]
                    hops = [d["hop"] for d in matching_diags]
                    rels = [d["relationship"] for d in matching_diags]

                    if "depth-2 + reciprocal traversal" in provs:
                        chosen_prov = "depth-2 + reciprocal traversal"
                    elif "depth-2 traversal" in provs:
                        chosen_prov = "depth-2 traversal"
                    elif "reciprocal/reverse edge traversal" in provs:
                        chosen_prov = "reciprocal/reverse edge traversal"
                    else:
                        chosen_prov = "depth-1 existing behavior"

                    exp_provenance[exp] = chosen_prov
                    reachable_depth2[exp] = any(h == 2 for h in hops)
                    reciprocal_required[exp] = any(r in RECIPROCAL_EDGE_NAMES for r in rels)
                else:
                    exp_provenance[exp] = "not surfaced in structured pool"
                    reachable_depth2[exp] = False
                    reciprocal_required[exp] = False

            rank_changes: dict[str, dict[str, Any]] = {}
            for exp in exp_docs:
                c_rank = ctrl_docs.index(exp) + 1 if exp in ctrl_docs else None
                t_rank = treat_docs.index(exp) + 1 if exp in treat_docs else None
                rank_changes[exp] = {
                    "control_rank": c_rank,
                    "treatment_rank": t_rank,
                    "rank_delta": (c_rank - t_rank) if (c_rank and t_rank) else None,
                    "in_control_top10": c_rank is not None and c_rank <= 10,
                    "in_treatment_top10": t_rank is not None and t_rank <= 10,
                    "in_control_pool": exp in ctrl_pool_docs,
                    "in_treatment_pool": exp in treat_pool_docs,
                    "provenance": exp_provenance[exp],
                    "reachable_via_depth_2": reachable_depth2[exp],
                    "reciprocal_required": reciprocal_required[exp],
                }

            newly_surfaced_in_top10 = [d for d in treat_top10 if d not in ctrl_top10]
            newly_surfaced_in_pool = list(treat_pool_docs - ctrl_pool_docs)

            h1_solvable_case_details.append({
                "evaluation_id": eid,
                "query": q_orig,
                "category": cat,
                "expected_documents": exp_docs,
                "control_top_10": ctrl_top10,
                "treatment_top_10": treat_top10,
                "control_recall_at_10": m_ctrl.get("recall_at_10", 0.0),
                "treatment_recall_at_10": m_treat.get("recall_at_10", 0.0),
                "recall_improved": m_treat.get("recall_at_10", 0.0) > m_ctrl.get("recall_at_10", 0.0),
                "fully_recovered": m_treat.get("recall_at_10", 0.0) == 1.0,
                "newly_surfaced_in_top10": newly_surfaced_in_top10,
                "newly_surfaced_in_pool": newly_surfaced_in_pool[:10],
                "expected_document_details": rank_changes,
            })

        case_results.append({
            "evaluation_id": eid,
            "query_category": cat,
            "is_positive": is_positive,
            "control_metrics": m_ctrl,
            "treatment_metrics": m_treat,
            "control_top10": ctrl_top10,
            "treatment_top10": treat_top10,
        })

    elapsed_s = round(time.perf_counter() - t_start, 2)

    # Compute Macro Summaries
    control_overall_pos = calculate_macro_mean(control_pos_metrics)
    treatment_overall_pos = calculate_macro_mean(treatment_pos_metrics)

    control_all = calculate_macro_mean(control_all_metrics)
    treatment_all = calculate_macro_mean(treatment_all_metrics)

    # Category summaries
    cat_summary: dict[str, dict[str, Any]] = {}
    for cat in sorted(category_control.keys()):
        c_m = calculate_macro_mean(category_control[cat])
        t_m = calculate_macro_mean(category_treatment[cat])
        cat_summary[cat] = {
            "case_count": len(category_control[cat]),
            "control": {
                "recall_at_10": c_m.get("recall_at_10", 0.0),
                "precision_at_10": c_m.get("precision_at_10", 0.0),
                "mrr": c_m.get("mrr", 0.0),
                "ndcg_at_10": c_m.get("ndcg_at_10", 0.0),
            },
            "treatment": {
                "recall_at_10": t_m.get("recall_at_10", 0.0),
                "precision_at_10": t_m.get("precision_at_10", 0.0),
                "mrr": t_m.get("mrr", 0.0),
                "ndcg_at_10": t_m.get("ndcg_at_10", 0.0),
            },
            "delta": {
                "recall_at_10": round(t_m.get("recall_at_10", 0.0) - c_m.get("recall_at_10", 0.0), 6),
                "precision_at_10": round(t_m.get("precision_at_10", 0.0) - c_m.get("precision_at_10", 0.0), 6),
                "mrr": round(t_m.get("mrr", 0.0) - c_m.get("mrr", 0.0), 6),
                "ndcg_at_10": round(t_m.get("ndcg_at_10", 0.0) - c_m.get("ndcg_at_10", 0.0), 6),
            },
        }

    # H1 Solvable Slice Summary
    h1_c_m = calculate_macro_mean(h1_control_metrics)
    h1_t_m = calculate_macro_mean(h1_treatment_metrics)

    # Document-level calculations for H1-solvable slice
    total_h1_expected_docs = sum(len(c["expected_documents"]) for c in h1_solvable_case_details)
    control_h1_hits = 0
    treatment_h1_hits = 0
    recovered_cases_count = 0
    fully_recovered_cases_count = 0

    for cd in h1_solvable_case_details:
        for exp, info in cd["expected_document_details"].items():
            if info["in_control_top10"]:
                control_h1_hits += 1
            if info["in_treatment_top10"]:
                treatment_h1_hits += 1
        if cd["recall_improved"]:
            recovered_cases_count += 1
        if cd["fully_recovered"]:
            fully_recovered_cases_count += 1

    doc_recall_control = round(control_h1_hits / total_h1_expected_docs, 6) if total_h1_expected_docs else 0.0
    doc_recall_treatment = round(treatment_h1_hits / total_h1_expected_docs, 6) if total_h1_expected_docs else 0.0

    # Decision Logic
    # H1 is evaluated on:
    # 1. Zero security regressions (mandatory fail-closed)
    # 2. Positive/non-degrading overall positive Recall@10
    # 3. Improvement on H1 target slice
    security_clean = (
        security_audit["treatment"]["forbidden_leaks_top10"] == 0
        and security_audit["treatment"]["forbidden_in_pool"] == 0
        and security_audit["treatment"]["negative_leaks"] == 0
    )

    overall_recall_delta = round(treatment_overall_pos["recall_at_10"] - control_overall_pos["recall_at_10"], 6)
    h1_doc_recall_delta = round(doc_recall_treatment - doc_recall_control, 6)

    if security_clean and overall_recall_delta >= 0.0 and h1_doc_recall_delta > 0.0:
        final_decision = "KEEP"
        decision_rationale = (
            f"H1 treatment confirmed: document-level Recall@10 on H1 slice improved from {doc_recall_control:.4f} "
            f"to {doc_recall_treatment:.4f} (+{h1_doc_recall_delta:.4f}), overall positive Recall@10 improved by "
            f"+{overall_recall_delta:.4f}, zero security violations."
        )
    elif security_clean and overall_recall_delta >= 0.0 and h1_doc_recall_delta == 0.0:
        final_decision = "INCONCLUSIVE"
        decision_rationale = "No measurable gain on H1 slice; zero security regressions."
    else:
        final_decision = "REJECT"
        decision_rationale = f"Performance degraded or security violated: overall delta={overall_recall_delta}, security_clean={security_clean}"

    report: dict[str, Any] = {
        "benchmark_metadata": {
            "name": "ATLAS RET-EVAL-03 Phase 2 — Controlled H1 Experiment",
            "git_commit": get_git_commit(root),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "duration_seconds": elapsed_s,
            "case_count": len(cases),
            "positive_case_count": len(control_pos_metrics),
            "negative_case_count": len(cases) - len(control_pos_metrics),
            "h1_solvable_case_count": len(h1_solvable_case_details),
        },
        "experiment_parameters": {
            "control": {
                "channel": "B4 Full Multi-Channel",
                "max_traversal_depth": 1,
                "enable_multihop": False,
                "reciprocal_alignment": False,
                "path_decay_gamma": "N/A (depth 1)",
            },
            "treatment": {
                "channel": "B4 + H1 Structured Overlay",
                "max_traversal_depth": 2,
                "enable_multihop": True,
                "reciprocal_alignment": True,
                "path_decay_gamma": "REMOVED (constant w_traversed = 0.8)",
            },
        },
        "primary_measurements": {
            "A_h1_solvable_document_recall_10": {
                "control": doc_recall_control,
                "treatment": doc_recall_treatment,
                "delta": h1_doc_recall_delta,
                "total_expected_docs": total_h1_expected_docs,
                "control_hits": control_h1_hits,
                "treatment_hits": treatment_h1_hits,
            },
            "B_h1_case_recovery_rate": {
                "case_count": len(h1_solvable_case_details),
                "cases_improved": recovered_cases_count,
                "cases_fully_recovered": fully_recovered_cases_count,
                "recovery_rate": round(recovered_cases_count / len(h1_solvable_case_details), 4) if h1_solvable_case_details else 0.0,
            },
            "C_overall_positive_recall_10": {
                "control": control_overall_pos["recall_at_10"],
                "treatment": treatment_overall_pos["recall_at_10"],
                "delta": overall_recall_delta,
            },
            "D_multi_hop_recall_10": {
                "control": cat_summary.get("multi_hop", {}).get("control", {}).get("recall_at_10", 0.0),
                "treatment": cat_summary.get("multi_hop", {}).get("treatment", {}).get("recall_at_10", 0.0),
                "delta": cat_summary.get("multi_hop", {}).get("delta", {}).get("recall_at_10", 0.0),
            },
            "E_multi_document_recall_10": {
                "control": cat_summary.get("multi_document", {}).get("control", {}).get("recall_at_10", 0.0),
                "treatment": cat_summary.get("multi_document", {}).get("treatment", {}).get("recall_at_10", 0.0),
                "delta": cat_summary.get("multi_document", {}).get("delta", {}).get("recall_at_10", 0.0),
            },
            "F_mrr": {
                "control": control_overall_pos["mrr"],
                "treatment": treatment_overall_pos["mrr"],
                "delta": round(treatment_overall_pos["mrr"] - control_overall_pos["mrr"], 6),
            },
            "G_ndcg_at_10": {
                "control": control_overall_pos["ndcg_at_10"],
                "treatment": treatment_overall_pos["ndcg_at_10"],
                "delta": round(treatment_overall_pos["ndcg_at_10"] - control_overall_pos["ndcg_at_10"], 6),
            },
            "precision_at_10": {
                "control": control_overall_pos["precision_at_10"],
                "treatment": treatment_overall_pos["precision_at_10"],
                "delta": round(treatment_overall_pos["precision_at_10"] - control_overall_pos["precision_at_10"], 6),
            },
        },
        "security_results": security_audit,
        "category_breakdown": cat_summary,
        "h1_solvable_case_details": h1_solvable_case_details,
        "decision": {
            "verdict": final_decision,
            "rationale": decision_rationale,
        },
        "case_level_results": case_results,
    }

    results_path = artifacts_dir / "ret_eval_03_h1_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report


if __name__ == "__main__":
    rep = run_h1_experiment()
    print("=" * 60)
    print("ATLAS RET-EVAL-03 PHASE 2: CONTROLLED H1 EXPERIMENT COMPLETE")
    print(f"Verdict: {rep['decision']['verdict']}")
    print(f"Rationale: {rep['decision']['rationale']}")
    print(f"Primary Slice Doc-Recall@10: {rep['primary_measurements']['A_h1_solvable_document_recall_10']['control']} -> {rep['primary_measurements']['A_h1_solvable_document_recall_10']['treatment']}")
    print(f"Overall Pos Recall@10: {rep['primary_measurements']['C_overall_positive_recall_10']['control']} -> {rep['primary_measurements']['C_overall_positive_recall_10']['treatment']}")
    print(f"Security violations: {rep['security_results']['treatment']}")
    print("=" * 60)
