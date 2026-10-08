#!/usr/bin/env python3
"""Isolated Shadow-Evaluation Harness: Production Resolver vs H5.1 Candidate.

Track 1 — H5.1 Shadow Evaluation.
Evaluates H5.1 in shadow mode alongside the production resolver across all 120
evaluation queries without granting H5.1 any authority to modify production answers.

For every evaluation query:
1. Run the current production resolver.
2. Run H5.1 candidate resolver.
3. Compare resolved entities.
4. Compare missing/wrong/ambiguous outcomes against labels.
5. Compare H1 structured seeds.
6. Compare structured candidate sets.
7. Compare final retrieval candidates.
8. Compare Recall@K/MRR/NDCG where labels exist.
9. Measure high-resolution latency for both paths.
10. Verify security and tenant decisions are identical or safer.

Invariant: H5.1 has ZERO authority to modify the production answer.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from novastack.bm25 import BM25Config, BM25Index
from novastack.dense import DenseIndex
from novastack.depth_fusion_ablation import fuse_rrf_sum
from novastack.entity_catalog import CanonicalEntity, EntityCatalog, IDENTIFIER_PATTERN
from novastack.metadata_diagnostics import build_metadata_snapshot_index
from novastack.metadata_reranker import MetadataReranker, MetadataRerankerConfig
from novastack.models import SearchChunk
from novastack.query_understanding import (
    EntityCatalog as QUEntityCatalog,
    EntityMention,
    QueryUnderstanding,
    QueryUnderstandingExtractor,
)
from novastack.relational_retrieval import (
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
from scripts.ret_eval_05_h2_experiment import PRIMARY_H2_SLICE_IDS
from scripts.ret_eval_06_h3_experiment import H3RoleDiversificationOverlay
from scripts.ret_eval_08_h5_1_experiment import (
    H5_1EntityResolver,
    H5_1QueryUnderstandingOverlay,
    H5_1StructuredRetrieverAdapter,
    H5Resolution,
    ResolvedEntity,
)


@dataclass
class PathTelemetry:
    """Telemetry captured for one evaluation path."""

    resolved_entities: list[dict[str, Any]]
    expanded_query: str
    h1_seed_entity_ids: list[str]
    structured_candidates: list[str]
    fused_candidates: list[str]
    final_ranked_candidates: list[str]
    top10_candidates: list[str]
    resolution_latency_us: float
    structured_latency_us: float
    total_retrieval_latency_ms: float
    metrics: dict[str, float]
    tenant_rejected_ids: list[str]
    ambiguities: list[dict[str, Any]]


def _percentile(values: Sequence[float], p: float) -> float:
    """Compute empirical percentile."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def _latency_stats(latencies: Sequence[float]) -> dict[str, float]:
    """Compute p50, p90, p95, p99, mean, min, max."""
    if not latencies:
        return {}
    return {
        "p50": round(_percentile(latencies, 50.0), 3),
        "p90": round(_percentile(latencies, 90.0), 3),
        "p95": round(_percentile(latencies, 95.0), 3),
        "p99": round(_percentile(latencies, 99.0), 3),
        "mean": round(sum(latencies) / len(latencies), 3),
        "min": round(min(latencies), 3),
        "max": round(max(latencies), 3),
    }


def _classify_shadow_failure_taxonomy(
    prod_entities: set[str],
    shadow_entities: set[str],
    expected_entities: set[str],
    prod_top10: list[str],
    shadow_top10: list[str],
    delta_recall_10: float,
    delta_mrr: float,
) -> tuple[str, str]:
    """Classify the query into a deterministic failure taxonomy category and description."""
    if prod_entities == shadow_entities and prod_top10 == shadow_top10:
        return (
            "EXACT_AGREEMENT",
            "Production and shadow paths resolved identical entities and produced identical top-10 candidate sets.",
        )

    if delta_recall_10 > 0.0:
        return (
            "RECALL_RECOVERY",
            f"H5.1 resolved missing canonical entities {sorted(shadow_entities - prod_entities)} recovering +{delta_recall_10:.4f} Recall@10.",
        )

    if not prod_entities and shadow_entities:
        if expected_entities and expected_entities.intersection(shadow_entities):
            return (
                "RESOLVER_COVERAGE_GAP_RESOLVED",
                f"Production resolver failed to extract entity (missing: {sorted(expected_entities)}), but H5.1 resolved {sorted(shadow_entities)}.",
            )
        return (
            "SHADOW_ENTITY_EXPANSION",
            f"Production had 0 entities; H5.1 resolved ungrounded catalog entities {sorted(shadow_entities)} without recall penalty.",
        )

    if prod_entities != shadow_entities:
        if expected_entities and not expected_entities.intersection(prod_entities) and expected_entities.intersection(shadow_entities):
            return (
                "RESOLVER_WRONG_ENTITY_CORRECTED",
                f"Production mapped to wrong entity {sorted(prod_entities)}, corrected by H5.1 to {sorted(shadow_entities)}.",
            )
        if delta_mrr < -0.05 and delta_recall_10 == 0.0:
            return (
                "RANK_SHIFT_DENSE_POOL",
                f"Recall@10 intact ({prod_top10[0] if prod_top10 else 'None'}), but MRR shifted by {delta_mrr:.4f} due to added structural candidates.",
            )
        return (
            "SEED_DIVERGENCE_BENIGN",
            f"Seed entities diverged (prod: {sorted(prod_entities)} vs shadow: {sorted(shadow_entities)}) with neutral retrieval outcome.",
        )

    if prod_top10 != shadow_top10:
        return (
            "CANDIDATE_POOL_DRIFT",
            "Identical entity seeds, but minor downstream tie-breaking or hybrid candidate pool drift.",
        )

    return (
        "NEUTRAL_DIFFERENCE",
        "Minor internal telemetry variation with no candidate impact.",
    )


def run_shadow_evaluation(
    data_dir: Path | str = "data",
    artifacts_dir: Path | str = "artifacts",
    *,
    write_artifact: bool = True,
) -> dict[str, Any]:
    """Execute the full isolated shadow evaluation harness across all 120 queries."""
    started_ts = time.time()
    t_start = time.perf_counter()

    data_dir = _PROJECT_ROOT / Path(data_dir)
    artifacts_dir = _PROJECT_ROOT / Path(artifacts_dir)
    raw_dir = data_dir / "raw" / "novastack"
    processed_dir = data_dir / "processed" / "novastack"
    evaluation_dir = data_dir / "evaluation" / "novastack"

    with open(evaluation_dir / "evaluation_cases.json", encoding="utf-8") as handle:
        cases = json.load(handle)["evaluation_cases"]
    with open(processed_dir / "search_chunks.json", encoding="utf-8") as handle:
        chunks = [SearchChunk.from_dict(item) for item in json.load(handle)["search_chunks"]]
    with open(processed_dir / "search_documents.json", encoding="utf-8") as handle:
        documents = json.load(handle)["search_documents"]
    with open(raw_dir / "adversarial_fixtures.json", encoding="utf-8") as handle:
        adversarial = json.load(handle)["adversarial_fixtures"]

    # Shared frozen retrieval indexes
    catalog = EntityCatalog(raw_dir, processed_dir / "search_chunks.json")
    qu_catalog = QUEntityCatalog(raw_dir)
    metadata_index = build_metadata_snapshot_index(documents, adversarial)
    bm25_index = BM25Index.build_index(chunks, config=BM25Config(k1=1.5, b=0.75))
    dense_index = DenseIndex.load(
        chunks_path=processed_dir / "search_chunks.json",
        embeddings_path=processed_dir / "dense_embeddings.npz",
        metadata_path=processed_dir / "dense_index_metadata.json",
    )
    reranker = MetadataReranker(MetadataRerankerConfig())
    h3_overlay = H3RoleDiversificationOverlay(
        top_k=10, max_per_role=3, metadata_index=metadata_index
    )

    # Path 1: Production Resolver
    prod_qu_extractor = QueryUnderstandingExtractor(qu_catalog)
    prod_base_structured = StructuredRetriever(
        catalog=catalog,
        config=StructuredRetrieverConfig(
            max_neighbors_per_hop=10,
            enable_runbook_reverse_index=True,
            runbook_entity_weight=0.85,
            direct_entity_weight=1.0,
            traversed_entity_weight=0.7,
            related_entity_weight=0.5,
        ),
    )
    prod_h1_overlay = H1StructuredRetrieverOverlay(prod_base_structured)

    # Path 2: Shadow H5.1 Resolver
    shadow_resolver = H5_1EntityResolver(catalog)
    shadow_qu_overlay = H5_1QueryUnderstandingOverlay(
        prod_qu_extractor, shadow_resolver, catalog
    )
    shadow_structured_adapter = H5_1StructuredRetrieverAdapter(
        prod_base_structured, shadow_resolver, catalog
    )
    shadow_h1_overlay = H1StructuredRetrieverOverlay(shadow_structured_adapter)

    per_query_results: list[dict[str, Any]] = []
    prod_res_latencies_us: list[float] = []
    shadow_res_latencies_us: list[float] = []
    prod_struct_latencies_us: list[float] = []
    shadow_struct_latencies_us: list[float] = []
    prod_total_latencies_ms: list[float] = []
    shadow_total_latencies_ms: list[float] = []

    security_prod = {
        "forbidden_top10_leaks": 0,
        "negative_case_leaks": 0,
        "cross_tenant_top10_leaks": 0,
        "denied_or_abstain_forbidden_top10_leaks": 0,
    }
    security_shadow = {
        "forbidden_top10_leaks": 0,
        "negative_case_leaks": 0,
        "cross_tenant_top10_leaks": 0,
        "denied_or_abstain_forbidden_top10_leaks": 0,
    }

    entity_stats_prod = {"exact": 0, "correct": 0, "partial": 0, "wrong": 0, "missing": 0, "ambiguous": 0, "false_pos": 0, "recalls": []}
    entity_stats_shadow = {"exact": 0, "correct": 0, "partial": 0, "wrong": 0, "missing": 0, "ambiguous": 0, "false_pos": 0, "recalls": []}
    labeled_case_count = 0

    human_review_cases: list[dict[str, Any]] = []
    taxonomy_counts: Counter[str] = Counter()

    # Warm-up pass to ensure encoder weights are loaded prior to latency measurement
    _ = dense_index.search(query="warmup query", top_k=5, filters={"tenant_id": "TENANT-NOVASTACK"})
    _ = bm25_index.search(query="warmup query", top_k=5, filters={"tenant_id": "TENANT-NOVASTACK"})

    for case in cases:
        query = case["query"]
        eval_id = case["evaluation_id"]
        category = case.get("category", "unknown")
        tenant_id = case["tenant_id"]
        user_id = case.get("user_id")
        user_role = case.get("user_role")
        user_department = case.get("user_department")
        forbidden_docs = case.get("forbidden_document_ids", [])
        expected_docs = case.get("expected_document_ids", [])
        acceptable_docs = case.get("acceptable_document_ids", [])
        expected_entities = set(case.get("expected_entity_ids", []))
        is_positive = bool(expected_docs)

        sec_context = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "user_role": user_role,
            "user_department": user_department,
            "forbidden_document_ids": forbidden_docs,
        }

        # ---------------------------------------------------------------------
        # 1. RUN PRODUCTION RESOLVER PATH (Authoritative)
        # ---------------------------------------------------------------------
        t0 = time.perf_counter_ns()
        prod_qu = prod_qu_extractor.extract(eval_id, query)
        t_prod_res = (time.perf_counter_ns() - t0) / 1000.0  # microseconds

        t_total_prod_start = time.perf_counter()
        prod_bm25 = bm25_index.search(
            query=prod_qu.expanded_query, top_k=50, filters={"tenant_id": tenant_id}
        )
        prod_dense = dense_index.search(
            query=query, top_k=50, filters={"tenant_id": tenant_id}
        )
        prod_hybrid = fuse_rrf_sum(prod_bm25, prod_dense, top_k=50, k=60, deduplicate_docs=True)

        t_struct_0 = time.perf_counter_ns()
        prod_struct, prod_h1_diag = prod_h1_overlay.retrieve(
            query=query, eval_case=sec_context, top_k=50
        )
        t_prod_struct = (time.perf_counter_ns() - t_struct_0) / 1000.0  # microseconds

        prod_combined = fuse_hybrid_and_structured(
            hybrid_candidates=prod_hybrid,
            structured_candidates=prod_struct.candidates,
            k=60,
            w_hybrid=1.0,
            w_struct=1.0,
            top_k=50,
            deduplicate_docs=True,
            catalog=catalog,
        )
        prod_reranked = reranker.rerank(
            candidates=prod_combined,
            qu=prod_qu,
            metadata_index=metadata_index,
            forbidden_doc_ids=set(forbidden_docs),
            enforce_security=True,
        )
        prod_final, prod_h3_audit = h3_overlay.diversify(
            reranked_candidates=prod_reranked,
            query=query,
            user_tenant=tenant_id,
            forbidden_doc_ids=set(forbidden_docs),
        )
        t_prod_total = (time.perf_counter() - t_total_prod_start) * 1000.0  # milliseconds

        prod_top10 = [c.document_id for c in prod_final[:10]]
        prod_ranked = [c.document_id for c in prod_final]
        prod_struct_docs = [c.document_id for c in prod_struct.candidates]
        prod_fused_docs = [c.document_id for c in prod_combined]

        prod_resolved_entities_list = [
            {
                "entity_id": e.entity_id,
                "entity_type": e.entity_type,
                "matched_text": e.matched_text,
                "match_method": "production_qu_catalog",
            }
            for e in prod_qu.entities
        ]
        # Also include any entities extracted by StructuredRetriever
        struct_extracted = prod_struct.extracted_entities
        prod_seeds = [e.entity_id for e in struct_extracted]

        prod_metrics = evaluate_retrieval_ranking(
            prod_ranked, expected_docs, acceptable_docs, forbidden_docs
        )

        # ---------------------------------------------------------------------
        # 2. RUN SHADOW H5.1 CANDIDATE PATH (Observed, Zero Authority)
        # ---------------------------------------------------------------------
        t0_s = time.perf_counter_ns()
        shadow_qu, shadow_resolution = shadow_qu_overlay.extract(query, tenant_id)
        t_shadow_res = (time.perf_counter_ns() - t0_s) / 1000.0  # microseconds

        t_total_shadow_start = time.perf_counter()
        shadow_structured_adapter.set_tenant_context(tenant_id)
        shadow_bm25 = bm25_index.search(
            query=shadow_qu.expanded_query, top_k=50, filters={"tenant_id": tenant_id}
        )
        shadow_dense = dense_index.search(
            query=query, top_k=50, filters={"tenant_id": tenant_id}
        )
        shadow_hybrid = fuse_rrf_sum(shadow_bm25, shadow_dense, top_k=50, k=60, deduplicate_docs=True)

        t_struct_s = time.perf_counter_ns()
        shadow_struct, shadow_h1_diag = shadow_h1_overlay.retrieve(
            query=query, eval_case=sec_context, top_k=50
        )
        t_shadow_struct = (time.perf_counter_ns() - t_struct_s) / 1000.0  # microseconds

        shadow_combined = fuse_hybrid_and_structured(
            hybrid_candidates=shadow_hybrid,
            structured_candidates=shadow_struct.candidates,
            k=60,
            w_hybrid=1.0,
            w_struct=1.0,
            top_k=50,
            deduplicate_docs=True,
            catalog=catalog,
        )
        shadow_reranked = reranker.rerank(
            candidates=shadow_combined,
            qu=shadow_qu,
            metadata_index=metadata_index,
            forbidden_doc_ids=set(forbidden_docs),
            enforce_security=True,
        )
        shadow_final, shadow_h3_audit = h3_overlay.diversify(
            reranked_candidates=shadow_reranked,
            query=query,
            user_tenant=tenant_id,
            forbidden_doc_ids=set(forbidden_docs),
        )
        t_shadow_total = (time.perf_counter() - t_total_shadow_start) * 1000.0  # milliseconds

        shadow_top10 = [c.document_id for c in shadow_final[:10]]
        shadow_ranked = [c.document_id for c in shadow_final]
        shadow_struct_docs = [c.document_id for c in shadow_struct.candidates]
        shadow_fused_docs = [c.document_id for c in shadow_combined]
        shadow_seeds = [e.entity_id for e in shadow_struct.extracted_entities]

        shadow_resolved_entities_list = [
            {
                "entity_id": e.entity_id,
                "entity_type": e.entity_type,
                "matched_text": e.matched_text,
                "canonical_phrase": e.canonical_phrase,
                "match_method": e.match_method,
            }
            for e in shadow_resolution.entities
        ]

        shadow_metrics = evaluate_retrieval_ranking(
            shadow_ranked, expected_docs, acceptable_docs, forbidden_docs
        )

        # ---------------------------------------------------------------------
        # 3. LATENCY RECORDING
        # ---------------------------------------------------------------------
        prod_res_latencies_us.append(t_prod_res)
        shadow_res_latencies_us.append(t_shadow_res)
        prod_struct_latencies_us.append(t_prod_struct)
        shadow_struct_latencies_us.append(t_shadow_struct)
        prod_total_latencies_ms.append(t_prod_total)
        shadow_total_latencies_ms.append(t_shadow_total)

        # ---------------------------------------------------------------------
        # 4. SECURITY & TENANT AUDITING
        # ---------------------------------------------------------------------
        # Production security check
        prod_forb = set(prod_top10).intersection(forbidden_docs)
        prod_cross = [
            doc_id for doc_id in prod_top10
            if (m := metadata_index.get(doc_id)) is not None and m.tenant_id != tenant_id
        ]
        if prod_forb:
            security_prod["forbidden_top10_leaks"] += len(prod_forb)
        if prod_cross:
            security_prod["cross_tenant_top10_leaks"] += len(prod_cross)
        if not is_positive and prod_metrics.get("hit_at_10", 0.0) > 0.0:
            security_prod["negative_case_leaks"] += 1

        # Shadow security check
        shadow_forb = set(shadow_top10).intersection(forbidden_docs)
        shadow_cross = [
            doc_id for doc_id in shadow_top10
            if (m := metadata_index.get(doc_id)) is not None and m.tenant_id != tenant_id
        ]
        if shadow_forb:
            security_shadow["forbidden_top10_leaks"] += len(shadow_forb)
        if shadow_cross:
            security_shadow["cross_tenant_top10_leaks"] += len(shadow_cross)
        if not is_positive and shadow_metrics.get("hit_at_10", 0.0) > 0.0:
            security_shadow["negative_case_leaks"] += 1

        # ---------------------------------------------------------------------
        # 5. ENTITY ACCURACY AUDITING (Where Labels Exist)
        # ---------------------------------------------------------------------
        prod_ent_ids = {e["entity_id"] for e in prod_resolved_entities_list}
        shadow_ent_ids = {e["entity_id"] for e in shadow_resolved_entities_list}

        if expected_entities:
            labeled_case_count += 1
            # Production entity audit
            p_rec = len(expected_entities.intersection(prod_ent_ids)) / len(expected_entities)
            entity_stats_prod["recalls"].append(p_rec)
            if prod_ent_ids == expected_entities:
                entity_stats_prod["exact"] += 1
                entity_stats_prod["correct"] += 1
            elif not prod_ent_ids:
                entity_stats_prod["missing"] += 1
            elif expected_entities.intersection(prod_ent_ids):
                entity_stats_prod["partial"] += 1
            else:
                entity_stats_prod["wrong"] += 1

            # Shadow entity audit
            s_rec = len(expected_entities.intersection(shadow_ent_ids)) / len(expected_entities)
            entity_stats_shadow["recalls"].append(s_rec)
            if shadow_resolution.ambiguities:
                entity_stats_shadow["ambiguous"] += 1
            if shadow_ent_ids == expected_entities:
                entity_stats_shadow["exact"] += 1
                entity_stats_shadow["correct"] += 1
            elif not shadow_ent_ids:
                entity_stats_shadow["missing"] += 1
            elif expected_entities.intersection(shadow_ent_ids):
                entity_stats_shadow["partial"] += 1
            else:
                entity_stats_shadow["wrong"] += 1
        else:
            if prod_ent_ids:
                entity_stats_prod["false_pos"] += 1
            if shadow_ent_ids:
                entity_stats_shadow["false_pos"] += 1

        # ---------------------------------------------------------------------
        # 6. CANDIDATE DELTA & JACCARD COMPARISON
        # ---------------------------------------------------------------------
        set_prod_top10 = set(prod_top10)
        set_shadow_top10 = set(shadow_top10)
        union_top10 = set_prod_top10.union(set_shadow_top10)
        inter_top10 = set_prod_top10.intersection(set_shadow_top10)
        jaccard_top10 = len(inter_top10) / len(union_top10) if union_top10 else 1.0

        set_prod_struct = set(prod_struct_docs)
        set_shadow_struct = set(shadow_struct_docs)
        union_struct = set_prod_struct.union(set_shadow_struct)
        inter_struct = set_prod_struct.intersection(set_shadow_struct)
        jaccard_struct = len(inter_struct) / len(union_struct) if union_struct else 1.0

        delta_r10 = shadow_metrics.get("recall_at_10", 0.0) - prod_metrics.get("recall_at_10", 0.0)
        delta_mrr = shadow_metrics.get("mrr", 0.0) - prod_metrics.get("mrr", 0.0)

        # ---------------------------------------------------------------------
        # 7. FAILURE TAXONOMY CLASSIFICATION
        # ---------------------------------------------------------------------
        tax_category, tax_desc = _classify_shadow_failure_taxonomy(
            prod_ent_ids,
            shadow_ent_ids,
            expected_entities,
            prod_top10,
            shadow_top10,
            delta_r10,
            delta_mrr,
        )
        taxonomy_counts[tax_category] += 1

        # ---------------------------------------------------------------------
        # 8. HUMAN REVIEW FLAGGING
        # ---------------------------------------------------------------------
        needs_review = False
        review_reasons = []

        if jaccard_top10 < 0.5 and is_positive:
            needs_review = True
            review_reasons.append(f"Low Top-10 Jaccard similarity ({jaccard_top10:.2f})")
        if delta_mrr < -0.1 and delta_r10 == 0.0:
            needs_review = True
            review_reasons.append(f"MRR dropped by {delta_mrr:.4f} despite stable Recall@10")
        if shadow_resolution.ambiguities:
            needs_review = True
            review_reasons.append("H5.1 reported ambiguous candidate entities")
        if shadow_resolution.tenant_rejected_identifiers:
            needs_review = True
            review_reasons.append(f"Tenant rejected IDs: {shadow_resolution.tenant_rejected_identifiers}")
        if not expected_entities and len(shadow_ent_ids) >= 3:
            needs_review = True
            review_reasons.append(f"Extracted 3+ entities ({len(shadow_ent_ids)}) on unlabeled query")

        query_record = {
            "evaluation_id": eval_id,
            "category": category,
            "query": query,
            "tenant_id": tenant_id,
            "is_positive": is_positive,
            "expected_entity_ids": sorted(expected_entities),
            "production": {
                "resolved_entities": prod_resolved_entities_list,
                "h1_seeds": prod_seeds,
                "structured_candidate_count": len(prod_struct_docs),
                "top10": prod_top10,
                "metrics": {
                    "recall_at_1": prod_metrics.get("recall_at_1", 0.0),
                    "recall_at_3": prod_metrics.get("recall_at_3", 0.0),
                    "recall_at_5": prod_metrics.get("recall_at_5", 0.0),
                    "recall_at_10": prod_metrics.get("recall_at_10", 0.0),
                    "mrr": prod_metrics.get("mrr", 0.0),
                    "ndcg_at_10": prod_metrics.get("ndcg_at_10", 0.0),
                    "hit_at_10": prod_metrics.get("hit_at_10", 0.0),
                },
                "latency_us": {
                    "resolution": round(t_prod_res, 2),
                    "structured": round(t_prod_struct, 2),
                },
                "total_latency_ms": round(t_prod_total, 2),
                "h3_active": prod_h3_audit.is_active,
            },
            "shadow_h5_1": {
                "resolved_entities": shadow_resolved_entities_list,
                "h1_seeds": shadow_seeds,
                "structured_candidate_count": len(shadow_struct_docs),
                "top10": shadow_top10,
                "metrics": {
                    "recall_at_1": shadow_metrics.get("recall_at_1", 0.0),
                    "recall_at_3": shadow_metrics.get("recall_at_3", 0.0),
                    "recall_at_5": shadow_metrics.get("recall_at_5", 0.0),
                    "recall_at_10": shadow_metrics.get("recall_at_10", 0.0),
                    "mrr": shadow_metrics.get("mrr", 0.0),
                    "ndcg_at_10": shadow_metrics.get("ndcg_at_10", 0.0),
                    "hit_at_10": shadow_metrics.get("hit_at_10", 0.0),
                },
                "latency_us": {
                    "resolution": round(t_shadow_res, 2),
                    "structured": round(t_shadow_struct, 2),
                },
                "total_latency_ms": round(t_shadow_total, 2),
                "h3_active": shadow_h3_audit.is_active,
                "ambiguities": shadow_resolution.ambiguities,
                "tenant_rejected_ids": shadow_resolution.tenant_rejected_identifiers,
            },
            "comparison": {
                "entity_agreement": prod_ent_ids == shadow_ent_ids,
                "prod_entities_only": sorted(prod_ent_ids - shadow_ent_ids),
                "shadow_entities_only": sorted(shadow_ent_ids - prod_ent_ids),
                "jaccard_top10": round(jaccard_top10, 4),
                "jaccard_structured": round(jaccard_struct, 4),
                "delta_recall_at_10": round(delta_r10, 6),
                "delta_mrr": round(delta_mrr, 6),
                "taxonomy_category": tax_category,
                "taxonomy_description": tax_desc,
                "needs_human_review": needs_review,
                "review_reasons": review_reasons,
            },
        }

        per_query_results.append(query_record)
        if needs_review:
            human_review_cases.append({
                "evaluation_id": eval_id,
                "query": query,
                "reasons": review_reasons,
                "jaccard_top10": round(jaccard_top10, 4),
                "delta_recall_10": round(delta_r10, 4),
                "delta_mrr": round(delta_mrr, 4),
                "prod_entities": sorted(prod_ent_ids),
                "shadow_entities": sorted(shadow_ent_ids),
            })

    # -------------------------------------------------------------------------
    # AGGREGATE SUMMARY CALCULATIONS
    # -------------------------------------------------------------------------
    positive_cases = [c for c in per_query_results if c["is_positive"]]
    multi_aspect_cases = [c for c in positive_cases if c["shadow_h5_1"]["h3_active"]]
    h2_slice_cases = [c for c in positive_cases if c["evaluation_id"] in PRIMARY_H2_SLICE_IDS]

    def _macro_agg(case_list: list[dict[str, Any]], path_key: str) -> dict[str, float]:
        if not case_list:
            return {}
        metrics_keys = ["recall_at_1", "recall_at_3", "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10", "hit_at_10"]
        return {
            k: round(sum(c[path_key]["metrics"][k] for c in case_list) / len(case_list), 6)
            for k in metrics_keys
        }

    aggregate_summary = {
        "dataset_summary": {
            "total_queries": len(cases),
            "positive_queries": len(positive_cases),
            "negative_queries": len(cases) - len(positive_cases),
            "labeled_entity_queries": labeled_case_count,
        },
        "entity_resolution_comparison": {
            "production": {
                "labeled_count": labeled_case_count,
                "exact_entity_set_match": entity_stats_prod["exact"],
                "expected_entity_recall": round(sum(entity_stats_prod["recalls"]) / max(1, len(entity_stats_prod["recalls"])), 6),
                "correct_cases": entity_stats_prod["correct"],
                "partial_cases": entity_stats_prod["partial"],
                "wrong_cases": entity_stats_prod["wrong"],
                "missing_cases": entity_stats_prod["missing"],
                "false_positive_cases": entity_stats_prod["false_pos"],
            },
            "shadow_h5_1": {
                "labeled_count": labeled_case_count,
                "exact_entity_set_match": entity_stats_shadow["exact"],
                "expected_entity_recall": round(sum(entity_stats_shadow["recalls"]) / max(1, len(entity_stats_shadow["recalls"])), 6),
                "correct_cases": entity_stats_shadow["correct"],
                "partial_cases": entity_stats_shadow["partial"],
                "wrong_cases": entity_stats_shadow["wrong"],
                "missing_cases": entity_stats_shadow["missing"],
                "ambiguous_cases": entity_stats_shadow["ambiguous"],
                "false_positive_cases": entity_stats_shadow["false_pos"],
            },
            "entity_set_agreement_count": sum(1 for c in per_query_results if c["comparison"]["entity_agreement"]),
            "entity_set_disagreement_count": sum(1 for c in per_query_results if not c["comparison"]["entity_agreement"]),
        },
        "retrieval_metrics_comparison": {
            "overall_positive": {
                "count": len(positive_cases),
                "production": _macro_agg(positive_cases, "production"),
                "shadow_h5_1": _macro_agg(positive_cases, "shadow_h5_1"),
            },
            "multi_aspect_slice": {
                "count": len(multi_aspect_cases),
                "production": _macro_agg(multi_aspect_cases, "production"),
                "shadow_h5_1": _macro_agg(multi_aspect_cases, "shadow_h5_1"),
            },
            "h2_slice": {
                "count": len(h2_slice_cases),
                "production": _macro_agg(h2_slice_cases, "production"),
                "shadow_h5_1": _macro_agg(h2_slice_cases, "shadow_h5_1"),
            },
        },
        "candidate_set_delta": {
            "mean_top10_jaccard": round(
                sum(c["comparison"]["jaccard_top10"] for c in per_query_results) / len(per_query_results), 4
            ),
            "mean_structured_jaccard": round(
                sum(c["comparison"]["jaccard_structured"] for c in per_query_results) / len(per_query_results), 4
            ),
            "top10_identical_candidate_count": sum(1 for c in per_query_results if c["comparison"]["jaccard_top10"] == 1.0),
            "top10_divergent_candidate_count": sum(1 for c in per_query_results if c["comparison"]["jaccard_top10"] < 1.0),
        },
        "latency_comparison": {
            "resolver_latency_microseconds": {
                "production": _latency_stats(prod_res_latencies_us),
                "shadow_h5_1": _latency_stats(shadow_res_latencies_us),
            },
            "structured_retrieval_latency_microseconds": {
                "production": _latency_stats(prod_struct_latencies_us),
                "shadow_h5_1": _latency_stats(shadow_struct_latencies_us),
            },
            "total_retrieval_latency_milliseconds": {
                "production": _latency_stats(prod_total_latencies_ms),
                "shadow_h5_1": _latency_stats(shadow_total_latencies_ms),
            },
        },
        "security_and_tenant_comparison": {
            "production": security_prod,
            "shadow_h5_1": security_shadow,
            "parity_verified": (
                security_shadow["forbidden_top10_leaks"] <= security_prod["forbidden_top10_leaks"]
                and security_shadow["cross_tenant_top10_leaks"] <= security_prod["cross_tenant_top10_leaks"]
                and security_shadow["negative_case_leaks"] <= security_prod["negative_case_leaks"]
            ),
        },
        "failure_taxonomy_distribution": dict(taxonomy_counts),
        "human_review_count": len(human_review_cases),
    }

    final_payload = {
        "metadata": {
            "milestone": "RET-EVAL-08",
            "track": "Track 1 — H5.1 Shadow Evaluation",
            "git_commit": get_git_commit(_PROJECT_ROOT),
            "timestamp": started_ts,
            "duration_seconds": round(time.perf_counter() - t_start, 2),
            "platform": sys.platform,
            "python_version": sys.version,
            "authority_invariant": "H5.1 has ZERO authority to modify production answers. Production answers are 100% determined by Production Pipeline.",
        },
        "aggregate_summary": aggregate_summary,
        "cases_requiring_human_review": human_review_cases,
        "per_query_results": per_query_results,
    }

    if write_artifact:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        out_path = artifacts_dir / "h5_1_shadow_evaluation_results.json"
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(final_payload, handle, indent=2)
        print(f"Shadow evaluation results written to: {out_path}")

    return final_payload


if __name__ == "__main__":
    res = run_shadow_evaluation()
    print("=== SHADOW EVALUATION COMPLETE ===")
    agg = res["aggregate_summary"]
    print("Total queries:", agg["dataset_summary"]["total_queries"])
    print("Entity Agreement:", agg["entity_resolution_comparison"]["entity_set_agreement_count"], "/", agg["dataset_summary"]["total_queries"])
    print("Production Positive Recall@10:", agg["retrieval_metrics_comparison"]["overall_positive"]["production"]["recall_at_10"])
    print("Shadow H5.1 Positive Recall@10:", agg["retrieval_metrics_comparison"]["overall_positive"]["shadow_h5_1"]["recall_at_10"])
    print("Mean Top-10 Jaccard:", agg["candidate_set_delta"]["mean_top10_jaccard"])
    print("Human Review Cases:", agg["human_review_count"])
    print("Security Parity:", agg["security_and_tenant_comparison"]["parity_verified"])
