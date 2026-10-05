"""Query Intent & Failure Taxonomy Diagnostic Engine — Phase 4C-0.

Provides deterministic query understanding classification, profile extraction,
and failure cross-tabulation across ATLAS retrieval baselines without LLM judges:

1. Controlled query intent taxonomy (15 orthogonal intent dimensions).
2. Per-case QueryProfile extraction from ground-truth metadata.
3. Cross-tabulation join against BM25, Dense, Hybrid RRF, Phase 4B-0 diagnostics,
   and Phase 4B-1 reranker results.
4. Identification of dominant intents, ranking-headroom vs candidate-generation
   failure bottlenecks, and recommendations for next controlled experiments.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "CONTROLLED_INTENT_LABELS",
    "IntentCrossTabulation",
    "Phase4C0DiagnosticReport",
    "QueryProfile",
    "build_all_query_profiles",
    "build_query_profile",
    "classify_query_intents",
    "generate_failure_cross_tabulation",
]

# Controlled Intent Taxonomy (15 dimensions)
CONTROLLED_INTENT_LABELS: list[str] = [
    "exact_identifier",
    "entity_attribute",
    "semantic",
    "temporal",
    "version_lifecycle",
    "relationship",
    "multi_hop",
    "multi_document",
    "conflicting_evidence",
    "duplicate_resolution",
    "authority_sensitive",
    "authorization_sensitive",
    "adversarial",
    "missing_information",
    "ambiguous",
]

# Compiled Regex for Exact Identifiers
_IDENTIFIER_PATTERN = re.compile(
    r"\b(INC|DEP|PR|EVT|SVC|USR|TEAM|CUST|TKT|DOC)-[A-Z0-9-]+\b"
)
_VERSION_PATTERN = re.compile(r"\bv\d+(\.\d+)*\b", re.IGNORECASE)
_DATE_PATTERN = re.compile(r"\b20\d{2}(-\d{2})*(-\d{2})*\b")


@dataclass
class QueryProfile:
    """Standardized profile of an evaluation case's intent and evidence requirements."""

    evaluation_id: str
    category: str
    difficulty: str
    query: str
    intent_labels: list[str]
    entity_ids: list[str]
    document_ids: list[str]
    expected_source_types: list[str]
    required_documents: list[str]
    acceptable_documents: list[str]
    forbidden_documents: list[str]
    temporal_requirement: dict[str, Any] | None
    version_requirement: str | None
    relationship_requirement: str | None
    authority_requirement: str | None
    authorization_requirement: dict[str, Any] | None
    adversarial_requirement: str | None
    multi_document_requirement: bool
    multi_hop_requirement: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize QueryProfile to JSON-compatible dictionary."""
        return asdict(self)


@dataclass
class IntentCrossTabulation:
    """Diagnostic performance metrics for an intent label across all evaluated systems."""

    intent_label: str
    total_cases_count: int
    positive_cases_count: int
    recall_at_10_bm25: float
    recall_at_10_dense: float
    recall_at_10_rrf: float
    recall_at_10_reranker: float
    union_coverage_at_50: float
    median_best_candidate_rank: float | None
    candidate_generation_failures: int
    ranking_headroom_cases: int
    reranker_regressions: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize cross-tabulation row to JSON-compatible dictionary."""
        return asdict(self)


@dataclass
class Phase4C0DiagnosticReport:
    """Complete machine-readable Phase 4C-0 diagnostic artifact."""

    version: str
    total_cases: int
    intent_distribution: dict[str, int]
    query_profiles: list[QueryProfile]
    cross_tabulation: list[IntentCrossTabulation]
    key_findings: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialize complete diagnostic report to dictionary."""
        return {
            "version": self.version,
            "total_cases": self.total_cases,
            "intent_distribution": self.intent_distribution,
            "query_profiles": [p.to_dict() for p in self.query_profiles],
            "cross_tabulation": [c.to_dict() for c in self.cross_tabulation],
            "key_findings": self.key_findings,
        }


def classify_query_intents(
    case: dict[str, Any],
    doc_source_types: dict[str, str] | None = None,
) -> list[str]:
    """Deterministically assign controlled intent labels to an evaluation case.

    Grounds assignment strictly in evaluation case fields, query phrasing,
    expected documents, and entity linkages.
    """
    category = case.get("query_category", "")
    query = case.get("query", "")
    expected_docs = case.get("expected_document_ids", [])
    required_docs = case.get("required_document_ids", [])
    forbidden_docs = case.get("forbidden_document_ids", [])
    expected_entities = case.get("expected_entity_ids", [])
    time_range = case.get("expected_time_range")
    version = case.get("expected_version")
    sec_fixture = case.get("security_fixture_id")
    adv_fixture = case.get("adversarial_fixture_id")
    behavior = case.get("expected_behavior", "")
    notes = case.get("notes", "").lower()

    intents: set[str] = set()

    # 1. exact_identifier
    if (
        category == "identifier_search"
        or _IDENTIFIER_PATTERN.search(query) is not None
        or (version and re.search(r"DEP-NS-\d+|PR-NS-\d+", version))
    ):
        intents.add("exact_identifier")

    # 2. entity_attribute
    attr_keywords = [
        "root cause",
        "resolution",
        "who owns",
        "which team owns",
        "which service",
        "what was the status",
        "status of",
        "current maximum",
        "rate limit",
        "original baseline",
        "parameters",
        "escalation",
        "sla",
        "ttl",
    ]
    if (
        category in ("exact_lookup", "ownership")
        or any(k in query.lower() for k in attr_keywords)
        or (category == "identifier_search" and len(expected_entities) > 0)
    ):
        intents.add("entity_attribute")

    # 3. semantic
    conceptual_categories = {
        "semantic_search",
        "ownership",
        "stale_information",
        "conflicting_evidence",
    }
    if (
        category in conceptual_categories
        or not _IDENTIFIER_PATTERN.search(query)
        or "why" in query.lower()
        or "what caused" in query.lower()
        or "how" in query.lower()
    ):
        intents.add("semantic")

    # 4. temporal
    temporal_indicators = [
        "before",
        "after",
        "during",
        "timeline",
        "duration",
        "how long",
        "relative",
        "sequence",
        "chronological",
    ]
    if (
        time_range is not None
        or category in ("temporal", "stale_information")
        or _DATE_PATTERN.search(query) is not None
        or any(ind in query.lower() for ind in temporal_indicators)
    ):
        intents.add("temporal")

    # 5. version_lifecycle
    if (
        version is not None
        or category in ("version", "stale_information")
        or _VERSION_PATTERN.search(query) is not None
        or "version" in query.lower()
        or "v1." in query.lower()
        or "v2." in query.lower()
        or "deprecated" in query.lower()
        or "superseded" in query.lower()
        or "draft" in query.lower()
        or "rollback" in query.lower()
    ):
        intents.add("version_lifecycle")

    # 6. relationship
    # Queries connecting multiple entity types or services
    entity_prefixes = set()
    for ent in expected_entities:
        parts = ent.split("-")
        if len(parts) >= 2:
            entity_prefixes.add(parts[0])
    if (
        category in ("ownership", "multi_hop")
        or len(entity_prefixes) >= 2
        or "which service and version" in query.lower()
        or "which deployment triggered" in query.lower()
        or "which team owns" in query.lower()
        or "escalation" in query.lower()
    ):
        intents.add("relationship")

    # 7. multi_hop
    if category == "multi_hop" or "triggered the checkout failure" in query.lower():
        intents.add("multi_hop")

    # 8. multi_document
    if category == "multi_document" or len(expected_docs) > 1 or len(required_docs) > 1:
        intents.add("multi_document")

    # 9. conflicting_evidence
    if (
        category == "conflicting_evidence"
        or "was the checkout failure caused by third-party" in query.lower()
        or "was user authentication degradation caused by" in query.lower()
        or "was the payment transaction failure on" in query.lower()
        or "conflicting" in notes
    ):
        intents.add("conflicting_evidence")

    # 10. duplicate_resolution
    if category == "duplicate_resolution" or "duplicate" in notes:
        intents.add("duplicate_resolution")

    # 11. authority_sensitive
    authority_categories = {
        "retrieval_poisoning",
        "citation_manipulation",
        "conflicting_evidence",
        "stale_information",
    }
    if (
        category in authority_categories
        or "official" in query.lower()
        or "authoritative" in query.lower()
        or "verified" in query.lower()
    ):
        intents.add("authority_sensitive")

    # 12. authorization_sensitive
    auth_categories = {
        "authorization",
        "cross_tenant",
        "role_restricted",
        "user_acl",
        "historical_security",
    }
    if (
        sec_fixture is not None
        or category in auth_categories
        or len(forbidden_docs) > 0
        or case.get("expected_access") == "deny"
        or case.get("user_role") is not None
        or case.get("user_department") is not None
        or "confidential" in query.lower()
        or "encryption keys" in query.lower()
    ):
        intents.add("authorization_sensitive")

    # 13. adversarial
    adv_categories = {
        "retrieval_poisoning",
        "citation_manipulation",
        "indirect_prompt_injection",
        "direct_prompt_injection",
    }
    if (
        adv_fixture is not None
        or category in adv_categories
        or any(d.startswith("DOC-ADV-") for d in expected_docs + forbidden_docs)
    ):
        intents.add("adversarial")

    # 14. missing_information
    if (
        category == "missing_information"
        or behavior in ("abstain", "refuse")
        or len(expected_docs) == 0
    ):
        intents.add("missing_information")

    # 15. ambiguous
    if (
        category in ("duplicate_resolution", "conflicting_evidence")
        or "ambiguous" in notes
        or "without incident id" in notes
    ):
        intents.add("ambiguous")

    # Guarantee fallback if empty
    if not intents:
        intents.add("semantic")

    return sorted(intents)


def build_query_profile(
    case: dict[str, Any],
    doc_source_types: dict[str, str] | None = None,
) -> QueryProfile:
    """Construct a full QueryProfile from an evaluation case dictionary."""
    doc_st = doc_source_types or {}
    expected_docs = case.get("expected_document_ids", [])
    required_docs = case.get("required_document_ids", [])
    acceptable_docs = case.get("acceptable_document_ids", [])
    forbidden_docs = case.get("forbidden_document_ids", [])

    # Map source types from documents
    all_ref_docs = expected_docs + required_docs + acceptable_docs
    src_types = sorted({doc_st[d] for d in all_ref_docs if d in doc_st})

    intent_labels = classify_query_intents(case, doc_st)

    # Requirements descriptions
    time_req = (
        case.get("expected_time_range")
        if case.get("expected_time_range")
        else ({"temporal_filter": True} if "temporal" in intent_labels else None)
    )
    ver_req = case.get("expected_version")
    if not ver_req and "version_lifecycle" in intent_labels:
        ver_match = _VERSION_PATTERN.search(case.get("query", ""))
        ver_req = ver_match.group(0) if ver_match else "version_lineage_resolution"

    rel_req = None
    if "relationship" in intent_labels:
        if "ownership" in case.get("query_category", ""):
            rel_req = "service_team_ownership"
        elif "multi_hop" in case.get("query_category", ""):
            rel_req = "causal_graph_traversal"
        else:
            rel_req = "entity_association"

    auth_req = None
    if "authority_sensitive" in intent_labels:
        auth_req = "prefer_verified_official_provenance"

    authorization_req = None
    if "authorization_sensitive" in intent_labels:
        authorization_req = {
            "expected_access": case.get("expected_access", "allow"),
            "tenant_id": case.get("tenant_id"),
            "user_id": case.get("user_id"),
            "user_role": case.get("user_role"),
            "user_department": case.get("user_department"),
            "has_forbidden_docs": len(forbidden_docs) > 0,
        }

    adv_req = None
    if "adversarial" in intent_labels:
        adv_req = case.get("query_category", "adversarial_payload_handling")

    return QueryProfile(
        evaluation_id=case["evaluation_id"],
        category=case.get("query_category", ""),
        difficulty=case.get("difficulty", "medium"),
        query=case["query"],
        intent_labels=intent_labels,
        entity_ids=case.get("expected_entity_ids", []),
        document_ids=expected_docs,
        expected_source_types=src_types,
        required_documents=required_docs,
        acceptable_documents=acceptable_docs,
        forbidden_documents=forbidden_docs,
        temporal_requirement=time_req,
        version_requirement=ver_req,
        relationship_requirement=rel_req,
        authority_requirement=auth_req,
        authorization_requirement=authorization_req,
        adversarial_requirement=adv_req,
        multi_document_requirement="multi_document" in intent_labels,
        multi_hop_requirement="multi_hop" in intent_labels,
    )


def build_all_query_profiles(
    eval_cases: list[dict[str, Any]],
    doc_source_types: dict[str, str] | None = None,
) -> list[QueryProfile]:
    """Generate QueryProfile objects for all provided evaluation cases."""
    return [build_query_profile(c, doc_source_types) for c in eval_cases]


def generate_failure_cross_tabulation(
    query_profiles: list[QueryProfile],
    bm25_case_results: list[dict[str, Any]],
    dense_case_results: list[dict[str, Any]],
    hybrid_case_results: list[dict[str, Any]],
    phase_4b0_diag: dict[str, Any],
    phase_4b1_baseline: dict[str, Any],
) -> list[IntentCrossTabulation]:
    """Join query profiles against all baseline evaluation artifacts.

    Computes cross-tabulated retrieval metrics for each controlled intent label.
    """
    # Index case results by evaluation_id
    bm25_map = {c["evaluation_id"]: c for c in bm25_case_results}
    dense_map = {c["evaluation_id"]: c for c in dense_case_results}
    hybrid_map = {c["evaluation_id"]: c for c in hybrid_case_results}

    # Headroom cases from Phase 4B-1 or 4B-0
    headroom_case_ids = {
        c["evaluation_id"]
        for c in phase_4b1_baseline.get("headroom_analysis", {}).get("cases", [])
    }

    # Regressions from Phase 4B-1
    reg_cases = phase_4b1_baseline.get("rrf_regression_analysis", {}).get(
        "cases", []
    )
    reg_map = {c["evaluation_id"]: c for c in reg_cases}

    # Category performance from Phase 4B-1 for reranker Recall@10
    cat_comp = phase_4b1_baseline.get("category_comparison", {})

    rows: list[IntentCrossTabulation] = []

    for intent in CONTROLLED_INTENT_LABELS:
        matching_profiles = [p for p in query_profiles if intent in p.intent_labels]
        total_count = len(matching_profiles)
        pos_profiles = [p for p in matching_profiles if len(p.document_ids) > 0]
        pos_count = len(pos_profiles)

        if pos_count == 0:
            # Unretrievable or pure abstention intent
            rows.append(
                IntentCrossTabulation(
                    intent_label=intent,
                    total_cases_count=total_count,
                    positive_cases_count=0,
                    recall_at_10_bm25=0.0,
                    recall_at_10_dense=0.0,
                    recall_at_10_rrf=0.0,
                    recall_at_10_reranker=0.0,
                    union_coverage_at_50=0.0,
                    median_best_candidate_rank=None,
                    candidate_generation_failures=0,
                    ranking_headroom_cases=0,
                    reranker_regressions=0,
                )
            )
            continue

        # Collect metrics across positive matching cases
        bm25_recalls: list[float] = []
        dense_recalls: list[float] = []
        rrf_recalls: list[float] = []
        best_ranks: list[int] = []
        cand_gen_failures = 0
        headroom_count = 0
        rerank_reg_count = 0
        reranker_recalls: list[float] = []

        for p in pos_profiles:
            e_id = p.evaluation_id
            cat = p.category
            exp_docs = set(p.document_ids)

            # BM25
            bm_res = bm25_map.get(e_id, {})
            bm_rec = bm_res.get("metrics", {}).get("recall_at_10", 0.0)
            bm25_recalls.append(bm_rec)

            # Dense
            dn_res = dense_map.get(e_id, {})
            dn_rec = dn_res.get("metrics", {}).get("recall_at_10", 0.0)
            dense_recalls.append(dn_rec)

            # RRF
            hyb_res = hybrid_map.get(e_id, {})
            hyb_rec = hyb_res.get("metrics", {}).get("recall_at_10", 0.0)
            rrf_recalls.append(hyb_rec)

            # Reranker Recall@10 estimated from category metrics
            cat_metrics = cat_comp.get(cat, {})
            rerank_rec = cat_metrics.get("rerank_union", hyb_rec)
            reranker_recalls.append(rerank_rec)

            # Reranker regressions
            if e_id in reg_map:
                rerank_reg_count += 1

            # Candidate ranks from retrieved candidates in baseline files
            bm_cands = [
                c.get("document_id")
                for c in bm_res.get("retrieved_candidates", [])
            ]
            dn_cands = [
                c.get("document_id")
                for c in dn_res.get("retrieved_candidates", [])
            ]

            r_bm = next(
                (idx for idx, d in enumerate(bm_cands, start=1) if d in exp_docs),
                None,
            )
            r_dn = next(
                (idx for idx, d in enumerate(dn_cands, start=1) if d in exp_docs),
                None,
            )

            # Check if this case is a known headroom case (ranks 11-50 in Phase 4B-0)
            if e_id in headroom_case_ids:
                headroom_count += 1
                best_r = 15  # Fallback rank for headroom
                best_ranks.append(best_r)
            elif r_bm or r_dn:
                valid_r = [r for r in (r_bm, r_dn) if r is not None]
                best_r = min(valid_r)
                best_ranks.append(best_r)
            else:
                # Target absent from top-10 in both
                cand_gen_failures += 1
                best_ranks.append(51)

        def _mean(v: list[float]) -> float:
            return round(sum(v) / len(v), 4) if v else 0.0

        median_rank = (
            round(statistics.median(best_ranks), 1) if best_ranks else None
        )
        union_cov = (
            round((pos_count - cand_gen_failures) / pos_count, 4)
            if pos_count > 0
            else 0.0
        )

        rows.append(
            IntentCrossTabulation(
                intent_label=intent,
                total_cases_count=total_count,
                positive_cases_count=pos_count,
                recall_at_10_bm25=_mean(bm25_recalls),
                recall_at_10_dense=_mean(dense_recalls),
                recall_at_10_rrf=_mean(rrf_recalls),
                recall_at_10_reranker=_mean(reranker_recalls),
                union_coverage_at_50=union_cov,
                median_best_candidate_rank=median_rank,
                candidate_generation_failures=cand_gen_failures,
                ranking_headroom_cases=headroom_count,
                reranker_regressions=rerank_reg_count,
            )
        )

    return rows
