"""Canonical Evaluation Dataset Loader & Taxonomy Classifier."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKSPACE = Path(__file__).resolve().parent.parent.parent
EVAL_CASES_PATH = WORKSPACE / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
SECURITY_FIXTURES_PATH = WORKSPACE / "data" / "raw" / "novastack" / "security_fixtures.json"
ADVERSARIAL_FIXTURES_PATH = WORKSPACE / "data" / "raw" / "novastack" / "adversarial_fixtures.json"

CANONICAL_CATEGORIES = [
    "exact_lookup",
    "semantic_lookup",
    "identifier_lookup",
    "temporal_query",
    "multi_document_query",
    "multi_hop_query",
    "analytical_query",
    "contradictory_evidence",
    "missing_information",
    "permission_sensitive_query",
    "cross_tenant_query",
    "prompt_injection",
    "stale_data",
    "duplicate_data",
    "long_context",
    "ambiguous_query",
    "entity_alias_query",
    "operational_incident_query",
    "deployment_query",
    "relationship_query",
]


@dataclass
class CanonicalEvaluationCase:
    evaluation_id: str
    query: str
    category: str
    user_context: Dict[str, Any]
    expected_facts: List[str]
    expected_document_ids: List[str]
    acceptable_document_ids: List[str]
    required_document_ids: List[str]
    forbidden_document_ids: List[str]
    expected_entity_ids: List[str]
    expected_behavior: str
    expected_citation_document_ids: List[str]
    security_fixture_id: Optional[str] = None
    adversarial_fixture_id: Optional[str] = None
    notes: str = ""
    is_positive: bool = True
    is_negative: bool = False
    is_security_sensitive: bool = False


def classify_case_category(case: Dict[str, Any]) -> str:
    """Classify an evaluation case into one of the 20 canonical categories."""
    eid = case["evaluation_id"]
    try:
        num = int(eid.replace("EVAL-", ""))
    except ValueError:
        num = 0

    notes = case.get("notes", "").lower()
    query = case.get("query", "").lower()
    beh = case.get("expected_behavior", "")
    forbidden = case.get("forbidden_document_ids", [])
    expected_docs = case.get("expected_document_ids", [])

    # Adversarial / Injection / Security first
    if case.get("adversarial_fixture_id") or "injection" in notes or "injection" in query:
        return "prompt_injection"
    if "cross-tenant" in notes or (forbidden and "tenant" in notes):
        return "cross_tenant_query"
    if beh == "deny_unauthorized" or (forbidden and ("role" in notes or "department" in notes or "acl" in notes or "user" in notes)):
        return "permission_sensitive_query"
    if beh == "abstain_insufficient_evidence" or len(expected_docs) == 0:
        return "missing_information"

    # Numeric range mappings aligned with dataset generation slices
    if 1 <= num <= 5:
        return "exact_lookup"
    elif 6 <= num <= 10:
        return "operational_incident_query"
    elif 11 <= num <= 15:
        return "identifier_lookup"
    elif 16 <= num <= 20:
        return "deployment_query"
    elif 21 <= num <= 25:
        return "semantic_lookup"
    elif 26 <= num <= 30:
        return "analytical_query"
    elif 31 <= num <= 35:
        return "entity_alias_query"
    elif 36 <= num <= 40:
        return "relationship_query"
    elif 41 <= num <= 50:
        return "multi_document_query"
    elif 51 <= num <= 60:
        return "multi_hop_query"
    elif 61 <= num <= 65:
        return "duplicate_data"
    elif 66 <= num <= 70:
        return "contradictory_evidence"
    elif 71 <= num <= 75:
        return "temporal_query"
    elif 76 <= num <= 80:
        return "stale_data"
    elif 81 <= num <= 85:
        return "ambiguous_query"
    elif 116 <= num <= 120:
        return "long_context"

    # Keyword fallbacks
    if "temporal" in notes or "date" in notes or "version" in notes:
        return "temporal_query"
    if "multi-hop" in notes or "hop" in notes:
        return "multi_hop_query"
    if "alias" in notes or "ownership" in notes:
        return "entity_alias_query"
    if "incident" in notes:
        return "operational_incident_query"
    if "deployment" in notes or "pr" in notes:
        return "deployment_query"
    if "symptom" in notes or "semantic" in notes:
        return "semantic_lookup"

    return "exact_lookup"


def load_canonical_dataset() -> List[CanonicalEvaluationCase]:
    """Load the canonical 120-case evaluation dataset with taxonomy tags."""
    with open(EVAL_CASES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_cases = data["evaluation_cases"] if "evaluation_cases" in data else data
    canonical_cases: List[CanonicalEvaluationCase] = []

    for raw in raw_cases:
        category = classify_case_category(raw)
        exp_docs = raw.get("expected_document_ids", [])
        is_pos = len(exp_docs) > 0
        is_neg = len(exp_docs) == 0
        is_sec = len(raw.get("forbidden_document_ids", [])) > 0 or raw.get("expected_behavior") == "deny_unauthorized"

        case = CanonicalEvaluationCase(
            evaluation_id=raw["evaluation_id"],
            query=raw["query"],
            category=category,
            user_context=raw.get("user_context", {"tenant_id": "TENANT-NOVASTACK", "roles": ["engineer"]}),
            expected_facts=raw.get("expected_facts", []),
            expected_document_ids=exp_docs,
            acceptable_document_ids=raw.get("acceptable_document_ids", []),
            required_document_ids=raw.get("required_document_ids", []),
            forbidden_document_ids=raw.get("forbidden_document_ids", []),
            expected_entity_ids=raw.get("expected_entity_ids", []),
            expected_behavior=raw.get("expected_behavior", "retrieve_and_answer"),
            expected_citation_document_ids=raw.get("expected_citation_document_ids", []),
            security_fixture_id=raw.get("security_fixture_id"),
            adversarial_fixture_id=raw.get("adversarial_fixture_id"),
            notes=raw.get("notes", ""),
            is_positive=is_pos,
            is_negative=is_neg,
            is_security_sensitive=is_sec,
        )
        canonical_cases.append(case)

    return canonical_cases


def load_security_fixtures() -> Dict[str, Any]:
    """Load security fixtures."""
    if SECURITY_FIXTURES_PATH.exists():
        with open(SECURITY_FIXTURES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_adversarial_fixtures() -> Dict[str, Any]:
    """Load adversarial fixtures."""
    if ADVERSARIAL_FIXTURES_PATH.exists():
        with open(ADVERSARIAL_FIXTURES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}
