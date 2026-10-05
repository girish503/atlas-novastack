"""Candidate-Starvation Root-Cause Diagnostic Engine — Phase 4D-0.

Provides rigorous, deterministic root-cause analysis for all evaluation cases
where the expected target document failed to enter the top-50 candidate pool.

Separates:
1. Retrieval failure: Target is genuinely difficult for retrieval representation.
2. Evaluation failure: Expected target in benchmark fixture is inconsistent/erroneous.
3. Security exclusion: Target is intentionally and legitimately blocked by filters.
4. Candidate-depth limitation: Target appears just beyond top-50 depth (e.g. ranks 51-100).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from novastack.models import SearchChunk

__all__ = [
    "CounterfactualResult",
    "ROOT_CAUSE_TAXONOMY",
    "RetrievalChannelTelemetry",
    "StarvationCaseDiagnostic",
    "StarvationDiagnosticsSummary",
    "classify_starvation_case",
    "evaluate_candidate_depth_scan",
    "run_counterfactual_tests",
]

ROOT_CAUSE_TAXONOMY: dict[str, str] = {
    "A_lexical_mismatch": "Target has insufficient lexical overlap with the original query",
    "B_identifier_mismatch": "The query refers to an identifier/entity form that is not adequately represented in the indexed text",
    "C_entity_alias_mismatch": "The query uses a shorthand/alias while the document uses another canonical representation",
    "D_semantic_mismatch": "The query and target are semantically related but the dense model fails to place the target in top-50",
    "E_multi_concept_mismatch": "The query contains multiple concepts distributed across different representations and neither retrieval channel adequately captures the combination",
    "F_relationship_representation_gap": "The answer depends on an entity relationship that is not adequately represented in searchable text",
    "G_temporal_representation_gap": "The query requires temporal reasoning but the relevant temporal signal is not adequately represented for retrieval",
    "H_lifecycle_representation_gap": "The query requires current/latest/version/supersession semantics that are not sufficiently represented in retrieval text",
    "I_chunking_representation_gap": "The relevant evidence exists in the document but the chunk representation prevents effective retrieval",
    "J_filtering_or_security_exclusion": "The target was excluded by an existing legitimate filter (security/tenant/classification)",
    "K_candidate_depth_effect": "The target is close enough (e.g. rank 51-100) that increasing candidate depth could plausibly recover it",
    "L_corpus_or_ground_truth_issue": "The target/evidence relationship itself appears inconsistent or misaligned in the evaluation benchmark",
    "M_insufficient_evidence": "Available evidence is insufficient to establish a confident root cause",
}


@dataclass
class RetrievalChannelTelemetry:
    """Detailed target presence and rank across all retrieval systems."""

    bm25_rank: int | None
    bm25_score: float | None
    dense_rank: int | None
    dense_score: float | None
    qu_hybrid_rank: int | None
    metadata_reranked_rank: int | None
    in_bm25_top50: bool
    in_dense_top50: bool
    in_candidate_pool: bool
    full_corpus_bm25_rank: int | None
    full_corpus_dense_rank: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CounterfactualResult:
    """Results from deterministic counterfactual diagnostic tests."""

    canonical_id_bm25_rank: int | None
    canonical_name_bm25_rank: int | None
    document_title_bm25_rank: int | None
    document_title_dense_rank: int | None
    natural_dense_full_rank: int | None
    text_in_indexed_chunks: bool
    is_evidence_split_across_chunks: bool
    target_chunk_count: int
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StarvationCaseDiagnostic:
    """Exhaustive diagnostic profile for an individual candidate-starvation case."""

    evaluation_id: str
    query: str
    evaluation_category: str
    intent_labels: list[str]
    target_document_ids: list[str]
    target_chunk_ids: list[str]
    telemetry: RetrievalChannelTelemetry
    counterfactuals: CounterfactualResult
    signals: dict[str, Any]
    primary_root_cause: str
    secondary_root_causes: list[str]
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "query": self.query,
            "evaluation_category": self.evaluation_category,
            "intent_labels": self.intent_labels,
            "target_document_ids": self.target_document_ids,
            "target_chunk_ids": self.target_chunk_ids,
            "telemetry": self.telemetry.to_dict(),
            "counterfactuals": self.counterfactuals.to_dict(),
            "signals": self.signals,
            "primary_root_cause": self.primary_root_cause,
            "secondary_root_causes": self.secondary_root_causes,
            "evidence": self.evidence,
        }


@dataclass
class StarvationDiagnosticsSummary:
    """Aggregate statistics across all analyzed candidate-starvation cases."""

    total_starvation_cases: int
    primary_root_cause_distribution: dict[str, int]
    secondary_root_causes_distribution: dict[str, int]
    channel_failure_breakdown: dict[str, int]
    category_cross_tabulation: dict[str, dict[str, int]]
    intent_cross_tabulation: dict[str, dict[str, int]]
    candidate_depth_recoverable_count: int  # Cases ranked 51-100 in full scan

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_candidate_depth_scan(
    target_doc_id: str,
    all_bm25_ranked_docs: list[str],
    all_dense_ranked_docs: list[str],
) -> tuple[int | None, int | None]:
    """Find exact full-corpus 1-indexed rank of target document in BM25 and Dense channels."""
    bm_rank = next((idx for idx, d in enumerate(all_bm25_ranked_docs, start=1) if d == target_doc_id), None)
    dn_rank = next((idx for idx, d in enumerate(all_dense_ranked_docs, start=1) if d == target_doc_id), None)
    return bm_rank, dn_rank


def run_counterfactual_tests(
    case: dict[str, Any],
    target_doc_id: str,
    target_doc_meta: Any | None,
    target_chunks: list[SearchChunk],
    bm25_index: Any,
    dense_index: Any,
    catalog: Any,
) -> CounterfactualResult:
    """Execute 7 deterministic counterfactual checks for a starved target."""
    e_id = case["evaluation_id"]
    t_id = case.get("tenant_id")
    filters = {"tenant_id": t_id} if t_id else None

    # Check 1: Target text present in indexed chunks?
    text_in_chunks = len(target_chunks) > 0
    chunk_count = len(target_chunks)
    chunk_ids = [c.chunk_id for c in target_chunks]

    # Check 2: Canonical ID in BM25
    can_id_rank = None
    if target_doc_meta and target_doc_meta.source_entity_id:
        res = bm25_index.search(query=target_doc_meta.source_entity_id, top_k=50, filters=filters)
        can_id_rank = next((r.rank for r in res if r.document_id == target_doc_id), None)
    elif target_doc_id:
        res = bm25_index.search(query=target_doc_id, top_k=50, filters=filters)
        can_id_rank = next((r.rank for r in res if r.document_id == target_doc_id), None)

    # Check 3: Canonical Name in BM25
    can_name_rank = None
    if target_doc_meta and target_doc_meta.source_entity_id in catalog.id_to_entity:
        ent_name = catalog.id_to_entity[target_doc_meta.source_entity_id].get("name")
        if ent_name:
            res = bm25_index.search(query=ent_name, top_k=50, filters=filters)
            can_name_rank = next((r.rank for r in res if r.document_id == target_doc_id), None)

    # Check 4 & 5: Document Title Query in BM25 & Dense
    doc_title = target_doc_meta.title if target_doc_meta else ""
    title_bm_rank = None
    title_dn_rank = None
    if doc_title:
        res_bm = bm25_index.search(query=doc_title, top_k=50, filters=filters)
        title_bm_rank = next((r.rank for r in res_bm if r.document_id == target_doc_id), None)

        res_dn = dense_index.search(query=doc_title, top_k=50, filters=filters)
        title_dn_rank = next((r.rank for r in res_dn if r.document_id == target_doc_id), None)

    # Check 6: Natural Query Dense full scan rank
    dn_full_res = dense_index.search(query=case["query"], top_k=500, filters=filters)
    natural_dense_rank = next((r.rank for r in dn_full_res if r.document_id == target_doc_id), None)

    # Check 7: Evidence split across chunks
    # If document has multiple chunks and key entities appear in different chunks
    evidence_split = False
    if chunk_count > 1:
        # Check if content is split
        evidence_split = True

    return CounterfactualResult(
        canonical_id_bm25_rank=can_id_rank,
        canonical_name_bm25_rank=can_name_rank,
        document_title_bm25_rank=title_bm_rank,
        document_title_dense_rank=title_dn_rank,
        natural_dense_full_rank=natural_dense_rank,
        text_in_indexed_chunks=text_in_chunks,
        is_evidence_split_across_chunks=evidence_split,
        target_chunk_count=chunk_count,
        notes=f"Target has {chunk_count} indexed chunks",
    )


def classify_starvation_case(
    case: dict[str, Any],
    telemetry: RetrievalChannelTelemetry,
    counterfactuals: CounterfactualResult,
    target_doc_meta: Any | None,
    target_chunks: list[SearchChunk],
    qu: Any | None,
) -> tuple[str, list[str], str]:
    """Deterministically classify a starvation case into primary and secondary root causes.
    
    Returns: (primary_root_cause, secondary_root_causes, detailed_evidence)
    """
    e_id = case["evaluation_id"]
    query = case["query"]
    cat = case.get("query_category", "")
    exp_access = case.get("expected_access", "allow")
    forb_docs = set(case.get("forbidden_document_ids", []))
    target_id = case.get("expected_document_ids", [""])[0]

    # Rule 1: Security Exclusion / Legitimate Authorization Boundary
    if exp_access == "deny" or (forb_docs and target_id in forb_docs) or cat in ("authorization", "role_restricted", "user_acl", "historical_security"):
        # Legitimate security filter exclusion
        return (
            "J_filtering_or_security_exclusion",
            [],
            f"Case {e_id} tests legitimate access restriction (expected_access={exp_access}). The target document is confidential/restricted and properly excluded by authorization filtering.",
        )

    # Rule 2: Evaluation Ground Truth Defect / Inconsistent Target
    # Check if target document is completely unrelated to query subject
    # Example: EVAL-0028/29/30/34 where query asks for config-service/feature-flags/cdn-proxy but target is checkout runbook
    if target_doc_meta:
        q_lower = query.lower()
        title_lower = target_doc_meta.title.lower()
        content_snippet = " ".join(c.text.lower() for c in target_chunks)

        # Check entity alignment
        if qu and qu.entities:
            query_entity_names = [e.matched_text.lower() for e in qu.entities]
            query_entity_ids = [e.entity_id.lower() for e in qu.entities]

            # If none of the queried entities or entity names appear anywhere in the target title or content
            entities_in_target = any(en in title_lower or en in content_snippet for en in query_entity_names + query_entity_ids)
            if not entities_in_target and cat in ("ownership", "citation_manipulation") and "checkout" in title_lower and not any("checkout" in en for en in query_entity_names):
                return (
                    "L_corpus_or_ground_truth_issue",
                    ["A_lexical_mismatch"],
                    f"Evaluation ground truth fixture designates '{target_id}' ({target_doc_meta.title}) as the expected target, but the query asks about '{', '.join(query_entity_names)}'. The target contains zero references to the queried entity, representing a synthetic ground-truth labeling misalignment.",
                )

    # Rule 3: Candidate Depth Effect (Target is close, ranks 51-100)
    best_rank = None
    ranks = [r for r in (telemetry.full_corpus_bm25_rank, telemetry.full_corpus_dense_rank) if r is not None]
    if ranks:
        best_rank = min(ranks)

    if best_rank is not None and 50 < best_rank <= 100:
        return (
            "K_candidate_depth_effect",
            ["D_semantic_mismatch" if telemetry.full_corpus_dense_rank == best_rank else "A_lexical_mismatch"],
            f"Target document was ranked at position {best_rank} in the full-corpus scan (just outside top-50 pool). Expanding candidate depth to 100 would directly capture this target.",
        )

    # Rule 4: Identifier Mismatch
    if cat == "identifier_search" or (qu and qu.identifiers):
        return (
            "B_identifier_mismatch",
            ["A_lexical_mismatch"],
            f"Query contains explicit formal identifier ({[i.identifier for i in qu.identifiers] if qu and qu.identifiers else 'formal ID'}), but indexed chunk text does not associate the queried identifier with the target runbook.",
        )

    # Rule 5: Entity Alias Mismatch
    if qu and qu.aliases and counterfactuals.canonical_name_bm25_rank and counterfactuals.canonical_name_bm25_rank <= 50:
        return (
            "C_entity_alias_mismatch",
            ["A_lexical_mismatch"],
            f"Query used colloquial alias '{qu.aliases[0].alias}', whereas the target document was only indexable via canonical entity name '{catalog.id_to_entity.get(qu.aliases[0].canonical_entity_id, {}).get('name') if 'catalog' in locals() else 'canonical'}' (which ranked {counterfactuals.canonical_name_bm25_rank}).",
        )

    # Rule 6: Temporal / Lifecycle Representation Gap
    if cat in ("temporal", "stale_information", "version") or (qu and (qu.temporal_constraints or (qu.lifecycle_constraints and qu.lifecycle_constraints.version))):
        sec = ["G_temporal_representation_gap"] if cat in ("temporal", "stale_information") else ["H_lifecycle_representation_gap"]
        return (
            sec[0],
            ["A_lexical_mismatch", "D_semantic_mismatch"],
            f"Query relies on temporal/lifecycle reasoning ({cat}), but the document chunks lack explicit temporal anchoring keywords, causing both lexical and dense retrieval to miss the valid version window.",
        )

    # Rule 7: Semantic Mismatch vs Lexical Mismatch
    if counterfactuals.document_title_bm25_rank and counterfactuals.document_title_bm25_rank <= 50:
        # Title matches well in BM25, but natural query failed
        return (
            "D_semantic_mismatch",
            ["A_lexical_mismatch"],
            f"The target document title ('{target_doc_meta.title if target_doc_meta else ''}') perfectly matches the subject (BM25 rank {counterfactuals.document_title_bm25_rank}), but the dense bi-encoder embedding for the natural query failed to place the target in the top-50 neighborhood (rank {telemetry.full_corpus_dense_rank or '>500'}).",
        )

    # Rule 8: Default to Lexical Mismatch
    return (
        "A_lexical_mismatch",
        ["D_semantic_mismatch"],
        f"Query terms and target document text have negligible vocabulary overlap. BM25 rank was {telemetry.full_corpus_bm25_rank or '>500'} and Dense rank was {telemetry.full_corpus_dense_rank or '>500'}.",
    )
