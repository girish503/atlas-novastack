"""Typed data models for NovaStack organisational, event, and source record entities.

All models are plain dataclasses — no ORM, no framework dependency.
Milestone 1: User, Team, Customer, Service
Milestone 2: Deployment, PullRequest, Incident, Event, EventRelationship
Milestone 3: RecordPermissions, SourceRecord
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class User:
    """An employee within a tenant organisation."""

    user_id: str
    tenant_id: str
    name: str
    email: str
    role: str
    department: str
    team_ids: list[str] = field(default_factory=list)
    status: str = "active"
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Team:
    """A named team within a department."""

    team_id: str
    tenant_id: str
    name: str
    department: str
    manager_id: str = ""
    member_ids: list[str] = field(default_factory=list)
    service_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Customer:
    """An external customer account."""

    customer_id: str
    tenant_id: str
    name: str
    segment: str = ""
    industry: str = ""
    account_owner_id: str = ""
    status: str = "active"
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Service:
    """A technical service owned by a team."""

    service_id: str
    tenant_id: str
    name: str
    description: str = ""
    owner_team_id: str = ""
    repository: str = ""
    environment: str = "production"
    criticality: str = "medium"
    status: str = "active"
    created_at: datetime = field(default_factory=datetime.utcnow)


# ===================================================================
# Milestone 2 — Ground-truth event layer
# ===================================================================


@dataclass
class Deployment:
    """A code or configuration deployment to a service."""

    deployment_id: str
    tenant_id: str
    service_id: str
    deployed_by: str          # user_id
    team_id: str
    version: str
    status: str = "succeeded"  # succeeded | failed | rolled_back
    deployed_at: datetime = field(default_factory=datetime.utcnow)
    description: str = ""


@dataclass
class PullRequest:
    """A pull request that fixes or changes a service."""

    pull_request_id: str
    tenant_id: str
    service_id: str
    repository: str
    title: str
    author_id: str            # user_id
    team_id: str
    status: str = "merged"    # merged | open | closed
    created_at: datetime = field(default_factory=datetime.utcnow)
    merged_at: datetime | None = None
    description: str = ""


@dataclass
class Incident:
    """An incident record linked to a ground-truth event."""

    incident_id: str
    tenant_id: str
    event_id: str
    title: str
    severity: str = "medium"   # critical | high | medium | low
    status: str = "resolved"   # resolved | investigating | mitigated
    reported_at: datetime = field(default_factory=datetime.utcnow)
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    assigned_team_id: str = ""
    incident_commander_id: str = ""  # user_id


@dataclass
class Event:
    """A ground-truth enterprise event — the authoritative 'what happened'."""

    event_id: str
    tenant_id: str
    title: str
    event_type: str            # outage | degradation | failure | etc.
    severity: str
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: datetime | None = None
    affected_service_ids: list[str] = field(default_factory=list)
    root_cause: str = ""
    triggering_deployment_id: str | None = None
    fixing_pull_request_id: str | None = None
    impacted_customer_ids: list[str] = field(default_factory=list)
    responsible_team_id: str = ""
    final_resolution: str = ""
    status: str = "resolved"


@dataclass
class EventRelationship:
    """A typed, directional relationship between ground-truth entities."""

    relationship_id: str
    tenant_id: str
    source_type: str           # event | incident | deployment | pull_request
    source_id: str
    target_type: str           # service | customer | team | deployment | etc.
    target_id: str
    relationship_type: str     # caused_by | resolved_by | affects | etc.


# ===================================================================
# Milestone 3 — Observational source-record layer
# ===================================================================

# Controlled source types
SOURCE_TYPES: set[str] = {
    "documentation",
    "incident",
    "postmortem",
    "support_ticket",
    "engineering_note",
    "conversation",
    "meeting",
    "policy",
    "deployment_note",
    "pull_request_note",
}

# Controlled authority levels
AUTHORITY_LEVELS: set[str] = {
    "authoritative",
    "high",
    "medium",
    "low",
    "draft",
}

# Controlled security classifications
CLASSIFICATION_LEVELS: set[str] = {
    "public",
    "internal",
    "confidential",
    "restricted",
}

# Controlled record lifecycle statuses
RECORD_STATUSES: set[str] = {
    "draft",
    "published",
    "archived",
    "deprecated",
    "superseded",
}

# Default authority level by source type
DEFAULT_AUTHORITY_BY_SOURCE_TYPE: dict[str, str] = {
    "policy": "authoritative",
    "documentation": "high",
    "postmortem": "high",
    "incident": "high",
    "deployment_note": "high",
    "pull_request_note": "medium",
    "engineering_note": "medium",
    "meeting": "medium",
    "support_ticket": "low",
    "conversation": "low",
}


@dataclass
class RecordPermissions:
    """Access control metadata for an observational source record."""

    allowed_roles: list[str] = field(default_factory=list)
    allowed_departments: list[str] = field(default_factory=list)
    allowed_teams: list[str] = field(default_factory=list)
    allowed_user_ids: list[str] = field(default_factory=list)


@dataclass
class SourceRecord:
    """An observational source record describing enterprise events or knowledge.

    Observational records represent what people or systems recorded about events.
    They may be partial, uncertain, conflicting, or stale, and reference ground-truth
    entities via provenance fields without replacing or mutating ground truth.
    """

    document_id: str
    tenant_id: str
    source_type: str                     # From SOURCE_TYPES
    title: str
    content: str
    author_id: str                       # user_id
    department: str
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    version: str = "1.0"
    status: str = "published"            # From RECORD_STATUSES
    classification: str = "internal"     # From CLASSIFICATION_LEVELS
    permissions: RecordPermissions = field(default_factory=RecordPermissions)
    parent_id: str | None = None
    supersedes_id: str | None = None
    source_entity_id: str | None = None
    source_entity_type: str | None = None
    related_entity_ids: list[str] = field(default_factory=list)
    authority_level: str = "medium"      # From AUTHORITY_LEVELS

    def to_dict(self) -> dict[str, Any]:
        """Convert SourceRecord to a JSON-serializable dictionary."""
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d


# ===================================================================
# Milestone 4D-1 — Security & Authorization Ground Truth
# ===================================================================

# Controlled security scenarios
SECURITY_SCENARIOS: set[str] = {
    "cross_tenant",
    "role_based",
    "classification",
    "department",
    "document_permission",
    "version_specific",
    "duplicate_acl",
    "superseded_restricted",
    "classification_and_permission",
}

# Controlled role classes for authorization fixtures
SECURITY_ROLES: set[str] = {
    "employee",
    "engineer",
    "support",
    "manager",
    "finance",
    "hr",
    "security_admin",
}


@dataclass
class SecurityFixture:
    """Security ground-truth test fixture evaluating authorization outcomes.

    Encodes expected access ('allow' or 'deny') for a specific test user
    evaluating a specific target source record under controlled enterprise
    security rules (tenant, classification, role, department, document ACL).
    """

    fixture_id: str
    security_scenario: str           # From SECURITY_SCENARIOS
    target_document_id: str
    expected_access: str             # "allow" | "deny"
    test_user_id: str
    test_user_role: str
    test_user_department: str
    test_user_tenant: str
    expected_tenant: str
    classification: str
    required_roles: list[str] = field(default_factory=list)
    required_department: str | None = None
    allowed_user_ids: list[str] = field(default_factory=list)
    security_reason: str = ""
    related_document_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert SecurityFixture to a JSON-serializable dictionary."""
        return asdict(self)


# ===================================================================
# Milestone 4D-2 — Adversarial, Prompt Injection & Poisoning Ground Truth
# ===================================================================

# Controlled attack categories
ATTACK_CATEGORIES: set[str] = {
    "direct_instruction",
    "indirect_prompt_injection",
    "retrieval_poisoning",
    "evidence_manipulation",
    "instruction_data_confusion",
    "citation_manipulation",
    "hidden_obfuscated_variant",
    "cross_tenant_adversarial",
}


@dataclass
class AdversarialFixture:
    """Adversarial ground-truth test fixture evaluating prompt injection & poisoning defenses.

    Encodes expected safe behavior for downstream retrieval and reasoning systems
    when encountering untrusted, manipulated, or instructional documents.
    """

    fixture_id: str
    attack_category: str             # From ATTACK_CATEGORIES
    target_document_id: str
    query: str
    expected_behavior: str           # e.g. "treat_as_untrusted_data"
    attack_payload_location: str     # e.g. "content", "title", "metadata_header", "comment"
    is_poisoned: bool
    is_instructional: bool
    legitimate_evidence_document_ids: list[str] = field(default_factory=list)
    poisoned_document_ids: list[str] = field(default_factory=list)
    expected_safe_answer_behavior: str = ""
    expected_citation_behavior: str = ""
    tenant_id: str = "TENANT-NOVASTACK"

    def to_dict(self) -> dict[str, Any]:
        """Convert AdversarialFixture to a JSON-serializable dictionary."""
        return asdict(self)


# ===================================================================
# Milestone 4E-1 — Evaluation Dataset Foundation
# ===================================================================

# Controlled query categories (20 categories)
QUERY_CATEGORIES: set[str] = {
    "exact_lookup",
    "identifier_search",
    "semantic_search",
    "ownership",
    "temporal",
    "version",
    "stale_information",
    "conflicting_evidence",
    "multi_document",
    "multi_hop",
    "missing_information",
    "authorization",
    "cross_tenant",
    "role_restricted",
    "user_acl",
    "historical_security",
    "retrieval_poisoning",
    "indirect_prompt_injection",
    "citation_manipulation",
    "duplicate_resolution",
}

EVALUATION_DIFFICULTIES: set[str] = {
    "easy",
    "medium",
    "hard",
}

EVALUATION_BEHAVIORS: set[str] = {
    "retrieve_and_answer",
    "abstain_insufficient_evidence",
    "deny_unauthorized",
    "prefer_authoritative_ground_truth",
    "treat_as_untrusted_data",
    "reject_self_declared_authority",
    "prefer_latest_version",
}


@dataclass
class EvaluationCase:
    """Structured evaluation test case for evaluating retrieval and generation.

    Decouples evidence retrieval verification from generated answer evaluation,
    supporting authorization boundaries, temporal validity, conflict resolution,
    and adversarial resilience.
    """

    evaluation_id: str
    query: str
    query_category: str              # From QUERY_CATEGORIES
    difficulty: str                  # "easy" | "medium" | "hard"
    tenant_id: str = "TENANT-NOVASTACK"
    user_id: str | None = None
    user_role: str | None = None
    user_department: str | None = None
    expected_access: str = "allow"   # "allow" | "deny" | "abstain"
    expected_answer_facts: list[str] = field(default_factory=list)
    expected_document_ids: list[str] = field(default_factory=list)
    acceptable_document_ids: list[str] = field(default_factory=list)
    required_document_ids: list[str] = field(default_factory=list)
    forbidden_document_ids: list[str] = field(default_factory=list)
    expected_entity_ids: list[str] = field(default_factory=list)
    expected_time_range: dict[str, Any] | None = None
    expected_version: str | None = None
    expected_behavior: str = "retrieve_and_answer"
    expected_citation_document_ids: list[str] = field(default_factory=list)
    security_fixture_id: str | None = None
    adversarial_fixture_id: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert EvaluationCase to a JSON-serializable dictionary."""
        return asdict(self)


# ===================================================================
# Phase 2A — Canonical Ingestion & Search Document Representation
# ===================================================================

@dataclass
class SearchDocument:
    """Canonical search-ready document representation for ATLAS downstream retrieval.

    Created during Phase 2A ingestion by validating and deterministically
    normalizing raw SourceRecord objects. Preserves all security, temporal,
    identity, authority, and provenance metadata needed for lexical search,
    dense vector indexing, authorization filtering, and answer generation.
    """

    document_id: str
    tenant_id: str
    source_type: str                     # From SOURCE_TYPES
    title: str
    content: str
    department: str
    author_id: str                       # user_id
    created_at: str                      # Standardized ISO 8601 string: YYYY-MM-DDTHH:MM:SS
    updated_at: str | None = None        # Standardized ISO 8601 string or None
    valid_from: str | None = None        # Standardized ISO 8601 string or None
    valid_until: str | None = None       # Standardized ISO 8601 string or None
    version: str = "1.0"
    status: str = "published"            # From RECORD_STATUSES
    classification: str = "internal"     # From CLASSIFICATION_LEVELS
    permissions: RecordPermissions = field(default_factory=RecordPermissions)
    parent_id: str | None = None
    supersedes_id: str | None = None
    source_entity_id: str | None = None
    source_entity_type: str | None = None
    related_entity_ids: list[str] = field(default_factory=list)
    authority_level: str = "medium"      # From AUTHORITY_LEVELS

    def to_dict(self) -> dict[str, Any]:
        """Convert SearchDocument to a JSON-serializable dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchDocument:
        """Construct a SearchDocument from a dictionary."""
        d = dict(data)
        perms_data = d.get("permissions")
        if isinstance(perms_data, dict):
            d["permissions"] = RecordPermissions(
                allowed_roles=list(perms_data.get("allowed_roles", [])),
                allowed_departments=list(perms_data.get("allowed_departments", [])),
                allowed_teams=list(perms_data.get("allowed_teams", [])),
                allowed_user_ids=list(perms_data.get("allowed_user_ids", [])),
            )
        elif perms_data is None:
            d["permissions"] = RecordPermissions()
        return cls(**d)


# ===================================================================
# Phase 2B — Semantic-Aware Document Chunking
# ===================================================================

@dataclass
class SearchChunk:
    """Canonical search chunk representing a retrieval-ready segment of a SearchDocument.

    Preserves full document lineage (SearchChunk -> SearchDocument -> SourceRecord -> Ground Truth),
    denormalizing all security, temporal, and provenance metadata needed for zero-join
    retrieval filtering, BM25 indexing, dense vector search, and citation generation.
    """

    chunk_id: str                        # Deterministic ID: {document_id}::CHUNK-{chunk_index+1:04d}
    document_id: str                     # Parent SearchDocument ID
    tenant_id: str                       # Strictly inherited from parent document
    chunk_index: int                     # 0-indexed position within parent document
    total_chunks: int                    # Total chunks generated for parent document
    title: str                           # Parent document title (essential for retrieval context)
    text: str                            # Non-empty chunk text content
    char_count: int                      # Length of text in characters
    word_count: int                      # Word count in text: len(text.split())
    source_type: str                     # From SOURCE_TYPES (inherited)
    department: str                      # Inherited
    author_id: str                       # Inherited
    classification: str                  # From CLASSIFICATION_LEVELS (strictly inherited)
    permissions: RecordPermissions       # Access metadata (strictly inherited)
    authority_level: str                 # From AUTHORITY_LEVELS (inherited)
    status: str                          # From RECORD_STATUSES (inherited)
    version: str                         # Inherited
    created_at: str                      # Standardized ISO 8601 string (inherited)
    updated_at: str | None = None        # Standardized ISO 8601 string or None (inherited)
    valid_from: str | None = None        # Standardized ISO 8601 string or None (inherited)
    valid_until: str | None = None       # Standardized ISO 8601 string or None (inherited)
    parent_id: str | None = None         # Inherited
    supersedes_id: str | None = None     # Inherited
    source_entity_id: str | None = None  # Inherited
    source_entity_type: str | None = None# Inherited
    related_entity_ids: list[str] = field(default_factory=list) # Inherited

    def to_dict(self) -> dict[str, Any]:
        """Convert SearchChunk to a JSON-serializable dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchChunk:
        """Construct a SearchChunk from a dictionary."""
        d = dict(data)
        perms_data = d.get("permissions")
        if isinstance(perms_data, dict):
            d["permissions"] = RecordPermissions(
                allowed_roles=list(perms_data.get("allowed_roles", [])),
                allowed_departments=list(perms_data.get("allowed_departments", [])),
                allowed_teams=list(perms_data.get("allowed_teams", [])),
                allowed_user_ids=list(perms_data.get("allowed_user_ids", [])),
            )
        elif perms_data is None:
            d["permissions"] = RecordPermissions()
        return cls(**d)






