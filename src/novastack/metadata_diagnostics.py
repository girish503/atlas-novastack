"""Authority, Lifecycle & Provenance-Aware Ranking Diagnostics — Phase 4C-2.

Provides diagnostic analysis to test Hypothesis 2 (H2):
> Existing enterprise metadata such as authority, lifecycle, status, version,
> provenance, and supersession contains enough signal to explain and potentially
> improve ranking among retrieved candidates.

Includes:
- Experiment 1: Metadata Distribution Analysis (Target vs Distractor vs Forbidden)
- Experiment 2: Authority Discrimination
- Experiment 3: Lifecycle & Recency Discrimination
- Experiment 4: Provenance Discrimination
- Experiment 5: Metadata Oracle Upper-Bound Analysis
- Experiment 6: Query-Understanding + Metadata Constraint Alignment
- Experiment 7: Security Separation (Authority vs Authorization)
- Failure Mode Classification (A-F)
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from novastack.models import (
    AUTHORITY_LEVELS,
    CLASSIFICATION_LEVELS,
    RECORD_STATUSES,
    SOURCE_TYPES,
)

__all__ = [
    "AuthorityDiscriminationResult",
    "CandidateMetadataRecord",
    "DocumentMetadataSnapshot",
    "FAILURE_CLASSIFICATIONS",
    "LifecycleDiscriminationResult",
    "MetadataDistribution",
    "OracleEvaluationResult",
    "ProvenanceDiscriminationResult",
    "build_metadata_snapshot_index",
    "classify_failure_mode",
    "compute_metadata_distribution",
    "evaluate_metadata_oracle",
]

FAILURE_CLASSIFICATIONS = {
    "A_absent_from_candidate_pool": "Target absent from candidate pool (candidate-generation limitation)",
    "B_target_present_metadata_indistinguishable": "Target present but metadata cannot distinguish it from distractors",
    "C_target_present_metadata_distinguishable": "Target present and metadata provides a useful distinction",
    "D_security_filter_issue": "Security filter / authorization boundary prevents retrieval",
    "E_evaluation_ground_truth_issue": "Evaluation ground truth artifact or misalignment",
    "F_ambiguous_insufficient_evidence": "Ambiguous query or insufficient evidence in corpus",
}


@dataclass
class DocumentMetadataSnapshot:
    """Consolidated metadata view of a document in the index."""

    document_id: str
    tenant_id: str
    source_type: str
    title: str
    department: str
    author_id: str
    created_at: str
    updated_at: str | None
    valid_from: str | None
    valid_until: str | None
    version: str
    status: str
    classification: str
    authority_level: str
    parent_id: str | None
    supersedes_id: str | None
    source_entity_id: str | None
    source_entity_type: str | None
    related_entity_ids: list[str] = field(default_factory=list)
    is_poisoned: bool = False
    is_instructional: bool = False
    attack_category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MetadataDistribution:
    """Summary distributions across metadata dimensions for a group of documents."""

    count: int
    authority: dict[str, int] = field(default_factory=dict)
    status: dict[str, int] = field(default_factory=dict)
    source_type: dict[str, int] = field(default_factory=dict)
    classification: dict[str, int] = field(default_factory=dict)
    version_distribution: dict[str, int] = field(default_factory=dict)
    provenance_presence: dict[str, int] = field(default_factory=dict)
    poisoned_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateMetadataRecord:
    """Record of a retrieved candidate and its diagnostic attributes."""

    rank: int
    document_id: str
    authority_level: str
    status: str
    source_type: str
    version: str
    valid_from: str | None
    valid_until: str | None
    parent_id: str | None
    supersedes_id: str | None
    source_entity_id: str | None
    source_entity_type: str | None
    related_entity_ids: list[str]
    classification: str
    is_target: bool
    is_acceptable: bool
    is_forbidden: bool
    is_poisoned: bool
    rrf_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuthorityDiscriminationResult:
    """Aggregated authority discrimination metrics."""

    target_authority_distribution: dict[str, int]
    distractor_authority_distribution: dict[str, int]
    poisoned_authority_distribution: dict[str, int]
    mean_rank_by_authority: dict[str, float]
    cases_where_target_has_higher_authority: int
    cases_where_target_has_equal_authority: int
    cases_where_target_has_lower_authority: int
    total_evaluated_cases: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LifecycleDiscriminationResult:
    """Aggregated lifecycle, version, and temporal discrimination metrics."""

    total_lifecycle_cases: int
    target_is_active_or_published: int
    distractor_is_superseded_or_deprecated: int
    stale_distractor_ranked_above_target_cases: int
    version_chain_cases_count: int
    target_has_newer_version_count: int
    temporal_interval_distinguishable_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProvenanceDiscriminationResult:
    """Aggregated provenance presence and linkage metrics."""

    target_has_source_entity: int
    distractor_has_source_entity: int
    target_has_parent_or_supersedes: int
    distractor_has_parent_or_supersedes: int
    poisoned_has_valid_provenance: int
    poisoned_lacks_valid_provenance: int
    total_target_docs: int
    total_distractor_docs: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OracleEvaluationResult:
    """Metrics for the Metadata Oracle upper bound experiment."""

    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    recall_at_20: float
    recall_at_50: float
    mrr: float
    ndcg_at_10: float
    hit_at_10: float
    hit_at_50: float
    forbidden_leaks_top10: int
    recoverable_cases_count: int
    unrecoverable_cases_count: int  # Absent from top-50 pool
    total_positive_cases: int
    per_category_recall_at_10: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_metadata_snapshot_index(
    documents: list[dict[str, Any]],
    adversarial_fixtures: list[dict[str, Any]] | None = None,
) -> dict[str, DocumentMetadataSnapshot]:
    """Construct an in-memory index mapping document_id to DocumentMetadataSnapshot."""
    # Map poisoned docs
    poisoned_docs: dict[str, dict[str, Any]] = {}
    if adversarial_fixtures:
        for fix in adversarial_fixtures:
            for p_id in fix.get("poisoned_document_ids", []):
                poisoned_docs[p_id] = {
                    "is_poisoned": True,
                    "is_instructional": fix.get("is_instructional", False),
                    "attack_category": fix.get("attack_category"),
                }

    index: dict[str, DocumentMetadataSnapshot] = {}
    for d in documents:
        doc_id = d["document_id"]
        adv_info = poisoned_docs.get(doc_id, {})
        index[doc_id] = DocumentMetadataSnapshot(
            document_id=doc_id,
            tenant_id=d.get("tenant_id", "TENANT-NOVASTACK"),
            source_type=d.get("source_type", "documentation"),
            title=d.get("title", ""),
            department=d.get("department", ""),
            author_id=d.get("author_id", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at"),
            valid_from=d.get("valid_from"),
            valid_until=d.get("valid_until"),
            version=d.get("version", "1.0"),
            status=d.get("status", "published"),
            classification=d.get("classification", "internal"),
            authority_level=d.get("authority_level", "medium"),
            parent_id=d.get("parent_id"),
            supersedes_id=d.get("supersedes_id"),
            source_entity_id=d.get("source_entity_id"),
            source_entity_type=d.get("source_entity_type"),
            related_entity_ids=list(d.get("related_entity_ids", [])),
            is_poisoned=adv_info.get("is_poisoned", False),
            is_instructional=adv_info.get("is_instructional", False),
            attack_category=adv_info.get("attack_category"),
        )
    return index


def compute_metadata_distribution(
    doc_ids: list[str],
    metadata_index: dict[str, DocumentMetadataSnapshot],
) -> MetadataDistribution:
    """Compute distribution of metadata values across a list of document IDs."""
    auth_counts: dict[str, int] = {k: 0 for k in AUTHORITY_LEVELS}
    status_counts: dict[str, int] = {k: 0 for k in RECORD_STATUSES}
    source_counts: dict[str, int] = {k: 0 for k in SOURCE_TYPES}
    class_counts: dict[str, int] = {k: 0 for k in CLASSIFICATION_LEVELS}
    ver_counts: dict[str, int] = {}
    prov_counts: dict[str, int] = {
        "has_source_entity": 0,
        "has_parent": 0,
        "has_supersedes": 0,
        "has_related_entities": 0,
    }
    poisoned = 0

    valid_docs = 0
    for doc_id in doc_ids:
        meta = metadata_index.get(doc_id)
        if not meta:
            continue
        valid_docs += 1
        auth_counts[meta.authority_level] = auth_counts.get(meta.authority_level, 0) + 1
        status_counts[meta.status] = status_counts.get(meta.status, 0) + 1
        source_counts[meta.source_type] = source_counts.get(meta.source_type, 0) + 1
        class_counts[meta.classification] = class_counts.get(meta.classification, 0) + 1

        v_major = meta.version.split(".")[0] if "." in meta.version else meta.version
        ver_counts[v_major] = ver_counts.get(v_major, 0) + 1

        if meta.source_entity_id:
            prov_counts["has_source_entity"] += 1
        if meta.parent_id:
            prov_counts["has_parent"] += 1
        if meta.supersedes_id:
            prov_counts["has_supersedes"] += 1
        if meta.related_entity_ids:
            prov_counts["has_related_entities"] += 1
        if meta.is_poisoned:
            poisoned += 1

    return MetadataDistribution(
        count=valid_docs,
        authority=auth_counts,
        status=status_counts,
        source_type=source_counts,
        classification=class_counts,
        version_distribution=ver_counts,
        provenance_presence=prov_counts,
        poisoned_count=poisoned,
    )


def classify_failure_mode(
    evaluation_id: str,
    category: str,
    expected_docs: list[str],
    candidate_doc_ids: list[str],
    metadata_index: dict[str, DocumentMetadataSnapshot],
    notes: str = "",
) -> tuple[str, str]:
    """Classify a retrieval failure into one of A-F categories."""
    exp_set = set(expected_docs)
    in_top50 = any(d in exp_set for d in candidate_doc_ids[:50])

    # A: Absent from candidate pool
    if not in_top50:
        if category in ("authorization", "role_restricted", "user_acl", "cross_tenant"):
            return "D_security_filter_issue", "Candidate missing due to strict authorization or tenant boundary enforcement"
        return "A_absent_from_candidate_pool", "Target document was not retrieved in top-50 pool (candidate starvation)"

    # Check for known synthetic evaluation artifacts (e.g. EVAL-0029, EVAL-0034 pointing to checkout runbook)
    if category == "ownership" and "DOC-DOC-EVT-NS-0001-01" in exp_set:
        for ed in exp_set:
            if ed == "DOC-DOC-EVT-NS-0001-01" and ("config" in notes or "media" in notes or "flags" in notes or "proxy" in notes):
                return "E_evaluation_ground_truth_issue", "Ground truth points to checkout runbook for unrelated service query"

    # Check security / authorization
    if category in ("authorization", "role_restricted", "user_acl"):
        return "D_security_filter_issue", "Target document is restricted under security permissions"

    # Check if metadata provides distinction (C vs B)
    # Inspect target vs top distractors
    targets_meta = [metadata_index[d] for d in exp_set if d in metadata_index]
    distractors_meta = [
        metadata_index[d] for d in candidate_doc_ids[:10] if d not in exp_set and d in metadata_index
    ]

    has_distinction = False
    distinction_reason = []

    if targets_meta and distractors_meta:
        t_auth = max(t.authority_level for t in targets_meta)
        d_auths = [d.authority_level for d in distractors_meta]
        # Authority distinction
        if t_auth in ("authoritative", "high") and any(da in ("low", "draft", "medium") for da in d_auths):
            has_distinction = True
            distinction_reason.append("authority_level")

        # Lifecycle distinction (target published vs distractors superseded)
        t_statuses = {t.status for t in targets_meta}
        d_statuses = {d.status for d in distractors_meta}
        if "published" in t_statuses and ("superseded" in d_statuses or "deprecated" in d_statuses):
            has_distinction = True
            distinction_reason.append("status_lifecycle")

        # Poisoning distinction
        if any(d.is_poisoned for d in distractors_meta):
            has_distinction = True
            distinction_reason.append("poisoning_penalty")

        # Provenance distinction
        if any(t.source_entity_id for t in targets_meta) and any(not d.source_entity_id for d in distractors_meta):
            has_distinction = True
            distinction_reason.append("source_entity_provenance")

    if has_distinction:
        return "C_target_present_metadata_distinguishable", f"Metadata provides useful distinction via {', '.join(distinction_reason)}"
    else:
        return "B_target_present_metadata_indistinguishable", "Target present in candidate pool but metadata fields are identical or insufficient to separate from distractors"


def evaluate_metadata_oracle(
    candidate_pools: dict[str, list[CandidateMetadataRecord]],
    metadata_index: dict[str, DocumentMetadataSnapshot],
    cases: list[dict[str, Any]],
    qu_dict: dict[str, Any] | None = None,
) -> OracleEvaluationResult:
    """Evaluate deterministic Metadata Oracle upper bound across all positive cases."""
    authority_weights = {
        "authoritative": 5.0,
        "high": 4.0,
        "medium": 3.0,
        "low": 1.0,
        "draft": 0.5,
    }

    recalls = {1: [], 5: [], 10: [], 20: [], 50: []}
    mrrs: list[float] = []
    ndcgs: list[float] = []
    hits10: list[float] = []
    hits50: list[float] = []
    forbidden_top10 = 0

    recoverable_count = 0
    unrecoverable_count = 0
    total_pos = 0

    category_recalls: dict[str, list[float]] = {}

    for c in cases:
        e_id = c["evaluation_id"]
        exp_docs = c.get("expected_document_ids", [])
        acc_docs = c.get("acceptable_document_ids", [])
        forb_docs = c.get("forbidden_document_ids", [])
        cat = c.get("query_category", "")

        if not exp_docs:
            continue
        total_pos += 1
        exp_set = set(exp_docs)
        acc_set = set(acc_docs)
        forb_set = set(forb_docs)

        candidates = candidate_pools.get(e_id, [])
        in_pool = any(cand.document_id in exp_set for cand in candidates[:50])

        if not in_pool:
            unrecoverable_count += 1
            for k in recalls:
                recalls[k].append(0.0)
            mrrs.append(0.0)
            ndcgs.append(0.0)
            hits10.append(0.0)
            hits50.append(0.0)
            category_recalls.setdefault(cat, []).append(0.0)
            continue

        # Target is present in pool -> Apply transparent deterministic oracle scoring
        qu = qu_dict.get(e_id) if qu_dict else None
        requires_latest = False
        requires_active = False
        if qu and hasattr(qu, "lifecycle_constraints"):
            requires_latest = qu.lifecycle_constraints.latest or qu.lifecycle_constraints.active
            requires_active = qu.lifecycle_constraints.active

        scored_candidates: list[tuple[float, CandidateMetadataRecord]] = []
        for cand in candidates[:50]:
            doc_id = cand.document_id
            meta = metadata_index.get(doc_id)
            if not meta:
                continue

            # Base score from RRF rank
            score = 100.0 - cand.rank

            # Rule 1: Strictly eliminate forbidden documents
            if doc_id in forb_set:
                score -= 1000.0

            # Rule 2: Penalize poisoned documents
            if cand.is_poisoned:
                score -= 500.0

            # Rule 3: Authority weighting
            auth_val = authority_weights.get(meta.authority_level, 2.0)
            score += auth_val * 10.0

            # Rule 4: Lifecycle & Supersession
            if meta.status == "superseded":
                score -= 40.0
            elif meta.status == "deprecated":
                score -= 30.0
            elif meta.status == "draft":
                score -= 20.0
            elif meta.status == "published":
                score += 15.0

            if requires_latest and meta.status == "published":
                score += 25.0

            # Rule 5: Provenance completeness
            if meta.source_entity_id:
                score += 5.0
            if meta.parent_id or meta.supersedes_id:
                score += 5.0

            scored_candidates.append((score, cand))

        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        reranked_doc_ids = [cand.document_id for _, cand in scored_candidates]

        # Check if target reached top-10
        target_in_top10 = any(d in exp_set for d in reranked_doc_ids[:10])
        first_orig_rank = next((cand.rank for cand in candidates[:50] if cand.document_id in exp_set), None)
        if target_in_top10 and first_orig_rank and first_orig_rank > 10:
            recoverable_count += 1

        # Calculate metrics
        for k in (1, 5, 10, 20, 50):
            hits = sum(1 for d in set(reranked_doc_ids[:k]) if d in exp_set)
            recalls[k].append(hits / len(exp_set))

        first_rank = next((idx for idx, d in enumerate(reranked_doc_ids, start=1) if d in exp_set), None)
        mrrs.append(1.0 / first_rank if first_rank else 0.0)

        # NDCG@10
        dcg = 0.0
        for idx, d in enumerate(reranked_doc_ids[:10], start=1):
            if d in exp_set:
                rel = 2.0
            elif d in acc_set:
                rel = 1.0
            else:
                rel = 0.0
            dcg += rel / math.log2(idx + 1)

        ideal_rels = sorted(
            [2.0] * min(len(exp_set), 10)
            + [1.0] * max(0, min(len(acc_set) - len(exp_set), 10 - len(exp_set))),
            reverse=True,
        )
        idcg = sum(rel / math.log2(idx + 1) for idx, rel in enumerate(ideal_rels[:10], start=1))
        ndcgs.append((dcg / idcg) if idcg > 0 else 0.0)

        hits10.append(1.0 if target_in_top10 else 0.0)
        hits50.append(1.0 if any(d in exp_set for d in reranked_doc_ids[:50]) else 0.0)

        forbidden_top10 += sum(1 for d in reranked_doc_ids[:10] if d in forb_set)
        category_recalls.setdefault(cat, []).append(recalls[10][-1])

    avg_cat_r10 = {
        cat: round(sum(vals) / len(vals), 4) if vals else 0.0
        for cat, vals in category_recalls.items()
    }

    return OracleEvaluationResult(
        recall_at_1=round(sum(recalls[1]) / total_pos, 4) if total_pos else 0.0,
        recall_at_5=round(sum(recalls[5]) / total_pos, 4) if total_pos else 0.0,
        recall_at_10=round(sum(recalls[10]) / total_pos, 4) if total_pos else 0.0,
        recall_at_20=round(sum(recalls[20]) / total_pos, 4) if total_pos else 0.0,
        recall_at_50=round(sum(recalls[50]) / total_pos, 4) if total_pos else 0.0,
        mrr=round(sum(mrrs) / total_pos, 4) if total_pos else 0.0,
        ndcg_at_10=round(sum(ndcgs) / total_pos, 4) if total_pos else 0.0,
        hit_at_10=round(sum(hits10) / total_pos, 4) if total_pos else 0.0,
        hit_at_50=round(sum(hits50) / total_pos, 4) if total_pos else 0.0,
        forbidden_leaks_top10=forbidden_top10,
        recoverable_cases_count=recoverable_count,
        unrecoverable_cases_count=unrecoverable_count,
        total_positive_cases=total_pos,
        per_category_recall_at_10=avg_cat_r10,
    )
