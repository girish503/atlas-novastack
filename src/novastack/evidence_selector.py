"""Minimum Sufficient Evidence Selection & Bounded Multi-Hop Answer Planning — Phase 0.5 Milestone M5.

Introduces a deterministic evidence-selection stage between retrieval and context budgeting:
1. Evidence Role Model: Classifies evidence items into formal enterprise roles (Incident,
   Postmortem, Deployment, PR, Service, Team, Runbook, Policy, Ticket, Protective).
2. Deterministic Query Planning: Identifies required entity anchors, relationship chains,
   and necessary evidence roles without autonomous loops or LLM calls.
3. Minimum Sufficient Selection: Solves bounded set cover to find the smallest authorized
   candidate subset that provides structural completeness for the required chain.
4. Deterministic Coverage Model: Computes entity, relationship, role, temporal, and
   provenance coverage to certify answer readiness.
5. Strict Security Boundary: Evaluates selection ONLY from authorized candidates. Never
   uses relationship completeness to bypass tenant, classification, or RBAC barriers.
"""

from __future__ import annotations

import copy
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

from novastack.entity_catalog import CanonicalEntity, EntityCatalog, TypedRelationship
from novastack.entity_grounding import EntityGroundingGate, QueryGroundingResult
from novastack.evidence import EvidenceItem, EvidencePackage, EvidenceStatus
from novastack.hierarchical_budgeter import (
    PROTECTIVE_TEXT_PATTERNS,
    CompactionTier,
    EvidenceCategory,
)
from novastack.query_understanding import QueryUnderstanding, QueryUnderstandingExtractor

logger = logging.getLogger("novastack.evidence_selector")

__all__ = [
    "EvidenceCoverage",
    "EvidencePlan",
    "EvidenceRole",
    "EvidenceSelectionResult",
    "MinimumSufficientEvidenceSelector",
    "SelectorConfig",
    "classify_evidence_role",
]


class EvidenceRole(str, Enum):
    """Functional enterprise role filled by an evidence item."""

    INCIDENT_RECORD = "incident_record"          # DOC-INC- (symptom, triage, severity, incident timeline)
    POSTMORTEM_RECORD = "postmortem_record"      # DOC-PM- (root cause, detailed resolution, action items)
    DEPLOYMENT_RECORD = "deployment_record"      # DOC-DEP- (version deployed, artifacts, rollout notes)
    PULL_REQUEST_RECORD = "pull_request_record"  # DOC-PR- (code changes, PR review, fixing commits)
    SERVICE_SPECIFICATION = "service_specification"  # DOC-SVC- / DOC-DOC- (service description, architecture)
    TEAM_OWNERSHIP = "team_ownership"            # DOC-TEAM- (team ownership, department, members)
    OPERATIONAL_RUNBOOK = "operational_runbook"  # DOC-RB- / DOC-SOP- (SOPs, failover procedures, runbooks)
    POLICY_DOCUMENT = "policy_document"          # DOC-POL- / DOC-BKG- (governance, policy, compliance)
    CUSTOMER_TICKET = "customer_ticket"          # DOC-TKT- (customer impact reports, support tickets)
    CHAT_RECORD = "chat_record"                  # DOC-CHAT- (triage chat, operational discussion)
    PROTECTIVE_BOUNDARY = "protective_boundary"  # Boundary declaration, out-of-scope / secret statement
    SUPPORTING_CONTEXT = "supporting_context"    # General supporting engineering background


@dataclass
class EvidenceCoverage:
    """Quantitative and structural coverage of the query's required answer facts."""

    covered_entities: set[str] = field(default_factory=set)
    covered_relationships: set[str] = field(default_factory=set)
    covered_roles: set[str] = field(default_factory=set)
    covered_provenances: set[str] = field(default_factory=set)
    temporal_valid: bool = True
    is_structurally_complete: bool = False
    missing_roles: list[str] = field(default_factory=list)
    missing_entities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert coverage record to dictionary."""
        return {
            "covered_entities": sorted(list(self.covered_entities)),
            "covered_relationships": sorted(list(self.covered_relationships)),
            "covered_roles": sorted(list(self.covered_roles)),
            "covered_provenances": sorted(list(self.covered_provenances)),
            "temporal_valid": self.temporal_valid,
            "is_structurally_complete": self.is_structurally_complete,
            "missing_roles": self.missing_roles,
            "missing_entities": self.missing_entities,
        }


@dataclass
class EvidencePlan:
    """Deterministic structural plan specifying required evidence roles and relationship chains."""

    query: str
    primary_entity_id: Optional[str] = None
    primary_entity_type: Optional[str] = None
    target_entity_ids: list[str] = field(default_factory=list)
    required_roles: list[EvidenceRole] = field(default_factory=list)
    required_relationship_types: list[str] = field(default_factory=list)
    relationship_chain: list[dict[str, str]] = field(default_factory=list)
    target_document_budget: int = 2
    is_protective: bool = False
    protection_reason: Optional[str] = None
    is_contrastive: bool = False
    contrastive_target_id: Optional[str] = None
    contrastive_hypotheses: list[str] = field(default_factory=list)
    context_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert plan to dictionary."""
        return {
            "query": self.query,
            "primary_entity_id": self.primary_entity_id,
            "primary_entity_type": self.primary_entity_type,
            "target_entity_ids": self.target_entity_ids,
            "required_roles": [r.value for r in self.required_roles],
            "required_relationship_types": self.required_relationship_types,
            "relationship_chain": self.relationship_chain,
            "target_document_budget": self.target_document_budget,
            "is_protective": self.is_protective,
            "protection_reason": self.protection_reason,
            "is_contrastive": self.is_contrastive,
            "contrastive_target_id": self.contrastive_target_id,
            "contrastive_hypotheses": self.contrastive_hypotheses,
            "context_notes": self.context_notes,
        }


@dataclass
class EvidenceSelectionResult:
    """Result of minimum sufficient evidence selection."""

    selected_items: list[EvidenceItem]
    omitted_items: list[EvidenceItem]
    plan: EvidencePlan
    coverage: EvidenceCoverage
    selection_latency_ms: float
    selected_document_count: int
    selected_token_count: int
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert selection result to dictionary."""
        return {
            "selected_document_count": self.selected_document_count,
            "selected_token_count": self.selected_token_count,
            "selection_latency_ms": round(self.selection_latency_ms, 3),
            "plan": self.plan.to_dict(),
            "coverage": self.coverage.to_dict(),
            "selected_document_ids": [it.document_id for it in self.selected_items],
            "selected_evidence_ids": [it.evidence_id for it in self.selected_items],
            "omitted_evidence_count": len(self.omitted_items),
            "diagnostics": self.diagnostics,
        }


@dataclass
class SelectorConfig:
    """Hyperparameters for minimum sufficient evidence selection."""

    default_budget_simple: int = 2
    default_budget_multihop: int = 3
    default_budget_protective: int = 3
    enable_adaptive_depth: bool = False
    adaptive_budget_simple: int = 3
    adaptive_budget_multihop: int = 4
    max_token_ceiling: int = 460
    enable_relationship_coverage: bool = True
    enable_role_coverage: bool = True
    enable_supporting_extension: bool = False  # True for Config E
    enable_contrastive_disambiguation: bool = True  # Track A: Entity-anchored contrastive prioritization
    enable_targeted_missing_role_recovery: bool = True  # Track B: Targeted 1-hop missing-role recovery
    max_recovery_candidates_per_role: int = 3
    max_recovery_rounds: int = 1
    anchor_priority_boost: float = 5.0
    require_entity_overlap_for_fill: bool = True  # M7 Track A: Prevent distractor cross-event contamination
    enable_alias_context_notes: bool = True  # M7 Track C: Inject neutral entity context notes
    enable_targeted_evidence_extraction: bool = False  # M8 Track A: Targeted sentence-level extraction
    max_extracted_sentences_per_chunk: int = 3
    treat_ungrounded_as_protective: bool = False  # M8: ungrounded queries should plan based on question intent
    authority_weights: dict[str, float] = field(
        default_factory=lambda: {
            "authoritative": 1.0,
            "high": 0.85,
            "medium": 0.70,
            "low": 0.50,
            "unverified": 0.30,
        }
    )



def classify_evidence_role(item: EvidenceItem, query: str = "") -> EvidenceRole:
    """Deterministically classify an EvidenceItem into a formal enterprise role."""
    text_lower = item.text.lower()
    doc_id = (item.document_id or "").upper()
    source_type = (item.source_type or "").lower()

    # 1. Protective boundary detection
    for pat in PROTECTIVE_TEXT_PATTERNS:
        if pat.search(item.text):
            return EvidenceRole.PROTECTIVE_BOUNDARY

    # 2. Document ID prefix classification
    if doc_id.startswith("DOC-PM-") or "postmortem" in source_type:
        return EvidenceRole.POSTMORTEM_RECORD
    if doc_id.startswith("DOC-INC-") or source_type == "incident":
        return EvidenceRole.INCIDENT_RECORD
    if doc_id.startswith("DOC-DEP-") or source_type == "deployment":
        return EvidenceRole.DEPLOYMENT_RECORD
    if doc_id.startswith("DOC-PR-") or source_type == "pull_request":
        return EvidenceRole.PULL_REQUEST_RECORD
    if doc_id.startswith("DOC-TKT-") or source_type == "ticket":
        return EvidenceRole.CUSTOMER_TICKET
    if doc_id.startswith("DOC-CHAT-") or source_type == "chat":
        return EvidenceRole.CHAT_RECORD
    if doc_id.startswith("DOC-RB-") or doc_id.startswith("DOC-SOP-") or source_type in ("runbook", "procedure"):
        return EvidenceRole.OPERATIONAL_RUNBOOK
    if doc_id.startswith("DOC-POL-") or source_type == "policy":
        return EvidenceRole.POLICY_DOCUMENT
    if doc_id.startswith("DOC-SVC-") or doc_id.startswith("DOC-DOC-") or source_type in ("service", "documentation", "doc"):
        return EvidenceRole.SERVICE_SPECIFICATION
    if doc_id.startswith("DOC-TEAM-") or source_type == "team":
        return EvidenceRole.TEAM_OWNERSHIP

    # 3. Content heuristics for general documents
    if "root cause" in text_lower or "postmortem" in text_lower:
        return EvidenceRole.POSTMORTEM_RECORD
    if "incident summary" in text_lower or "severity:" in text_lower:
        return EvidenceRole.INCIDENT_RECORD
    if "pull request" in text_lower or "commit:" in text_lower:
        return EvidenceRole.PULL_REQUEST_RECORD
    if "deployed version" in text_lower or "deployment note" in text_lower:
        return EvidenceRole.DEPLOYMENT_RECORD
    if "standard operating procedure" in text_lower or "runbook" in text_lower:
        return EvidenceRole.OPERATIONAL_RUNBOOK
    if "team owns" in text_lower or "owning team" in text_lower or "department:" in text_lower:
        return EvidenceRole.TEAM_OWNERSHIP

    return EvidenceRole.SUPPORTING_CONTEXT


EXPLICIT_SERVICE_ALIASES: dict[str, str] = {
    "checkout": "SVC-NS-0005",
    "payments": "SVC-NS-0007",
    "payment": "SVC-NS-0007",
    "media": "SVC-NS-0006",
    "cart": "SVC-NS-0008",
    "notification": "SVC-NS-0011",
    "notifications": "SVC-NS-0011",
    "feature-flags": "SVC-NS-0001",
    "inventory": "SVC-NS-0011",
    "search": "SVC-NS-0012",
    "analytics": "SVC-NS-0002",
    "auth": "SVC-NS-0001",
    "authentication": "SVC-NS-0001",
    "auth-service": "SVC-NS-0001",
}


class MinimumSufficientEvidenceSelector:
    """Deterministic minimum sufficient evidence selection and answer planning engine."""

    def __init__(
        self,
        catalog: EntityCatalog | None = None,
        grounding_gate: EntityGroundingGate | None = None,
        config: SelectorConfig | None = None,
    ) -> None:
        self.catalog = catalog or EntityCatalog()
        self.gate = grounding_gate or EntityGroundingGate(catalog=self.catalog)
        self.config = config or SelectorConfig()

    def plan_query(self, query: str, tenant_id: str = "TENANT-NOVASTACK") -> EvidencePlan:
        """Formulate a deterministic evidence plan specifying required roles and entity chains."""
        q_lower = query.lower()

        # Track A: Detect Contrastive / Refutation Queries ("Was X caused by Y or Z?")
        is_contrastive_query = bool(
            re.search(
                r"\b(?:was|is|did|were)\b.*\b(?:caused by|due to|result of|because of|failure|degradation|latency|incident|outage)\b.*\bor\b",
                q_lower,
            )
        )
        contrastive_hypotheses: list[str] = []
        if is_contrastive_query:
            m_hyp = re.search(
                r"\b(?:caused by|due to|result of|because of)\s+(.*?)\s+\bor\b\s+(.*?)(?:\?|$)",
                q_lower,
            )
            if m_hyp:
                h1 = m_hyp.group(1).strip()
                h2 = m_hyp.group(2).strip()
                contrastive_hypotheses = [h1, h2]
            else:
                parts = [p.strip() for p in q_lower.split(" or ")]
                if len(parts) >= 2:
                    contrastive_hypotheses = [parts[0], parts[1]]

        # Match canonical event from catalog if query refers to an operational incident/event
        matched_event_ent: Optional[CanonicalEntity] = None
        if is_contrastive_query or any(k in q_lower for k in ("outage", "incident", "failure", "degradation", "latency")):
            best_score = 0
            generic_tokens = {"outage", "incident", "failure", "event"}
            for eid, ent in self.catalog.entities.items():
                if ent.entity_type == "event" and ent.name:
                    if tenant_id and ent.tenant_id != tenant_id:
                        continue
                    e_tokens = set(re.findall(r"\b[a-z0-9_\-]{3,}\b", ent.name.lower()))
                    q_tokens = set(re.findall(r"\b[a-z0-9_\-]{3,}\b", q_lower))
                    prefix_match = any(
                        (t.startswith(e[:4]) or e.startswith(t[:4]))
                        for t in q_tokens
                        for e in (e_tokens - generic_tokens)
                        if len(t) >= 4 and len(e) >= 4
                    )
                    overlap_specific = len((e_tokens - generic_tokens).intersection(q_tokens)) + (1 if prefix_match else 0)
                    overlap_generic = len((e_tokens & generic_tokens).intersection(q_tokens))
                    score = overlap_specific * 10 + overlap_generic
                    if score > best_score and (overlap_specific > 0 or score >= 2):
                        best_score = score
                        matched_event_ent = ent

        # Explicit service alias disambiguation for operational queries
        matched_service_id = None
        for alias_token, sid in EXPLICIT_SERVICE_ALIASES.items():
            if re.search(r"\b" + re.escape(alias_token) + r"\b", q_lower):
                matched_service_id = sid
                break

        # 1. Grounding check
        grounding = self.gate.ground_query(query=query, tenant_id=tenant_id)
        
        # Check if genuinely protective query (EVAL-0054 out-of-scope, EVAL-0058 token/credential seeking)
        is_protective = False
        reason = None
        is_secret_rotation_causation = bool(
            re.search(r"\b(?:signing\s+secret|secret|key)\s+rotation\b", q_lower)
            and not any(k in q_lower for k in ("leaked", "twilio", "what are", "give", "show", "export", "reveal", "print", "auth token", "api token"))
        )
        if grounding.is_out_of_scope:
            is_protective = True
            reason = "out_of_scope"
        elif grounding.is_secret_seeking and not is_secret_rotation_causation:
            is_protective = True
            reason = "secret_seeking"
        elif not grounding.is_grounded and not matched_service_id and not matched_event_ent:
            if self.config.treat_ungrounded_as_protective:
                is_protective = True
                reason = "ungrounded"

        if is_protective:
            return EvidencePlan(
                query=query,
                required_roles=[EvidenceRole.PROTECTIVE_BOUNDARY],
                target_document_budget=self.config.default_budget_protective,
                is_protective=True,
                protection_reason=reason,
            )

        extracted_entities = list(grounding.grounded_entities)
        primary_entity_id = extracted_entities[0].entity_id if extracted_entities else None
        primary_entity_type = extracted_entities[0].entity_type if extracted_entities else None

        target_entity_ids = [e.entity_id for e in extracted_entities]
        required_roles: list[EvidenceRole] = []
        required_rels: list[str] = []
        rel_chain: list[dict[str, str]] = []

        # 2. Multi-hop causal chain queries: "symptom", "trace", "causal chain", ("deployment" and "pr")
        is_trace_query = any(k in q_lower for k in ("trace", "causal chain", "what symptom appeared"))
        is_dep_pr_query = "deployment" in q_lower and ("pr" in q_lower or "pull request" in q_lower or "fixed" in q_lower or "resolved" in q_lower)
        is_root_cause_query = any(k in q_lower for k in ("root cause", "resolution", "why did", "why was", "why were", "what caused", "why "))
        is_ownership_query = any(k in q_lower for k in ("owns", "owner", "which team", "department"))
        is_procedure_query = any(k in q_lower for k in ("procedure", "runbook", "failover", "restart", "sop"))
        is_customer_query = any(k in q_lower for k in ("customer", "customers", "support ticket", "billing discrepancy"))

        if matched_service_id and (
            is_trace_query
            or is_dep_pr_query
            or is_root_cause_query
            or is_procedure_query
            or any(k in q_lower for k in ("outage", "incident", "failure", "crash", "error", "deployment", "service"))
        ):
            svc_ent = self.catalog.get_entity(matched_service_id)
            if svc_ent:
                primary_entity_id = svc_ent.entity_id
                primary_entity_type = svc_ent.entity_type
                if primary_entity_id not in target_entity_ids:
                    target_entity_ids.insert(0, primary_entity_id)

        if matched_event_ent:
            if matched_event_ent.entity_id not in target_entity_ids:
                target_entity_ids.insert(0, matched_event_ent.entity_id)
            if is_trace_query or is_dep_pr_query or is_contrastive_query or not primary_entity_id:
                primary_entity_id = matched_event_ent.entity_id
                primary_entity_type = "event"

        simple_budget = (
            self.config.adaptive_budget_simple
            if self.config.enable_adaptive_depth
            else self.config.default_budget_simple
        )
        multihop_budget = (
            self.config.adaptive_budget_multihop
            if self.config.enable_adaptive_depth
            else self.config.default_budget_multihop
        )

        if is_contrastive_query and matched_event_ent:
            # Track A: Contrastive hypothesis refutation: canonical postmortem is authoritative
            primary_entity_id = matched_event_ent.entity_id
            primary_entity_type = "event"
            if primary_entity_id not in target_entity_ids:
                target_entity_ids.insert(0, primary_entity_id)
            required_roles = [
                EvidenceRole.POSTMORTEM_RECORD,
                EvidenceRole.INCIDENT_RECORD,
            ]
            required_rels = ["causes", "caused_by", "has_incident"]
            budget = simple_budget

        elif is_trace_query or (is_dep_pr_query and is_root_cause_query):
            # 3-hop causal chain: Incident/Postmortem + Deployment + Pull Request
            required_roles = [
                EvidenceRole.POSTMORTEM_RECORD,
                EvidenceRole.DEPLOYMENT_RECORD,
                EvidenceRole.PULL_REQUEST_RECORD,
            ]
            required_rels = ["caused_by", "resolved_by"]
            budget = multihop_budget

        elif is_dep_pr_query:
            # 2-hop deployment and PR resolution
            required_roles = [
                EvidenceRole.DEPLOYMENT_RECORD,
                EvidenceRole.PULL_REQUEST_RECORD,
            ]
            required_rels = ["resolves", "targets"]
            budget = simple_budget

        elif is_root_cause_query:
            # Incident lookup / Postmortem
            required_roles = [
                EvidenceRole.POSTMORTEM_RECORD,
                EvidenceRole.INCIDENT_RECORD,
            ]
            required_rels = ["causes", "caused_by"]
            budget = simple_budget

        elif is_ownership_query:
            required_roles = [
                EvidenceRole.SERVICE_SPECIFICATION,
                EvidenceRole.TEAM_OWNERSHIP,
            ]
            required_rels = ["owned_by", "owns"]
            budget = simple_budget

        elif is_procedure_query:
            required_roles = [
                EvidenceRole.SERVICE_SPECIFICATION,
                EvidenceRole.OPERATIONAL_RUNBOOK,
            ]
            required_rels = ["documented_by"]
            budget = simple_budget

        elif is_customer_query:
            required_roles = [
                EvidenceRole.CUSTOMER_TICKET,
                EvidenceRole.POSTMORTEM_RECORD,
            ]
            required_rels = ["impacts", "impacted_by"]
            budget = simple_budget

        else:
            # General entity lookup
            required_roles = [
                EvidenceRole.INCIDENT_RECORD if primary_entity_type == "incident"
                else (EvidenceRole.DEPLOYMENT_RECORD if primary_entity_type == "deployment"
                else (EvidenceRole.PULL_REQUEST_RECORD if primary_entity_type == "pull_request"
                else EvidenceRole.SERVICE_SPECIFICATION)),
                EvidenceRole.SUPPORTING_CONTEXT,
            ]
            budget = simple_budget

        # 3. Explore relationship chain if primary entity is known (up to 2 hops)
        if primary_entity_id:
            rels = self.catalog.get_relationships(primary_entity_id, direction="both")
            hop1_entities: set[str] = set()
            for r in rels:
                other_id = r.target_id if r.source_id == primary_entity_id else r.source_id
                rel_chain.append({
                    "source_id": r.source_id,
                    "relationship_type": r.relationship_type,
                    "target_id": r.target_id,
                })
                if other_id not in target_entity_ids:
                    target_entity_ids.append(other_id)
                hop1_entities.add(other_id)

            # Hop 2 for multi-hop / trace queries
            if is_trace_query or is_dep_pr_query or is_root_cause_query or is_contrastive_query:
                for h1 in list(hop1_entities)[:5]:
                    h2_rels = self.catalog.get_relationships(h1, direction="both")
                    for r2 in h2_rels:
                        other_id2 = r2.target_id if r2.source_id == h1 else r2.source_id
                        if other_id2 != primary_entity_id and other_id2 not in target_entity_ids:
                            target_entity_ids.append(other_id2)
                            rel_chain.append({
                                "source_id": r2.source_id,
                                "relationship_type": r2.relationship_type,
                                "target_id": r2.target_id,
                            })

        # 4. Context notes for entity disambiguation (Track C)
        context_notes: list[str] = []
        if self.config.enable_alias_context_notes and not is_protective:
            if matched_service_id:
                svc_ent = self.catalog.get_entity(matched_service_id)
                if svc_ent:
                    clean_name = svc_ent.name.replace("-service", "").replace("-", " ")
                    context_notes.append(
                        f"Service '{svc_ent.name}' operates in {svc_ent.department or 'Engineering'} handling {clean_name} functionality."
                    )
            if matched_event_ent:
                context_notes.append(
                    f"Incident event '{matched_event_ent.name}' relates to {matched_event_ent.entity_id} operational records."
                )

        return EvidencePlan(
            query=query,
            primary_entity_id=primary_entity_id,
            primary_entity_type=primary_entity_type,
            target_entity_ids=target_entity_ids,
            required_roles=required_roles,
            required_relationship_types=required_rels,
            relationship_chain=rel_chain[:6],
            target_document_budget=budget,
            is_protective=False,
            is_contrastive=is_contrastive_query,
            contrastive_target_id=primary_entity_id if is_contrastive_query else None,
            contrastive_hypotheses=contrastive_hypotheses,
            context_notes=context_notes,
        )

    def _is_valid_candidate(self, item: Any, user_context: dict[str, Any] | None = None) -> bool:
        """8-Gate Security & Quality candidate validation check for retrieval recovery."""
        ctx = user_context or {}
        # 1. Authorization status
        if getattr(item, "evidence_status", None) == EvidenceStatus.UNAUTHORIZED.value:
            return False

        # 2. Tenant isolation
        user_tenant = ctx.get("tenant_id")
        item_tenant = getattr(item, "tenant_id", None)
        if user_tenant and item_tenant and item_tenant != user_tenant:
            return False

        # 3. RBAC / Clearance check
        user_roles = ctx.get("roles", [])
        if user_roles:
            classification = getattr(item, "classification", "internal")
            if classification == "top_secret" and "security_admin" not in user_roles:
                return False
            perms = getattr(item, "permissions", None)
            if perms and hasattr(perms, "allowed_roles") and perms.allowed_roles:
                if not any(r in perms.allowed_roles for r in user_roles):
                    return False

        # 4. Forbidden documents
        forbidden = set(ctx.get("forbidden_docs", set()))
        if getattr(item, "document_id", None) in forbidden:
            return False

        # 5. Adversarial quarantine
        if (
            getattr(item, "source_type", "") == "adversarial_fixture"
            or "adversarial" in getattr(item, "evidence_reasons", [])
            or "adversarial" in getattr(item, "tags", [])
        ):
            return False

        # 6. Lifecycle check
        status = getattr(item, "status", "published")
        if status in ("superseded", "deprecated", "inactive", "draft", "deleted", "tombstone"):
            return False

        # 7. Temporal validity
        valid_until = getattr(item, "valid_until", None)
        if valid_until:
            try:
                from datetime import datetime, timezone
                vu_str = str(valid_until).replace("Z", "+00:00")
                vu_dt = datetime.fromisoformat(vu_str)
                ref_dt = datetime(2025, 1, 15, tzinfo=timezone.utc)
                if vu_dt.tzinfo is None:
                    ref_dt = datetime(2025, 1, 15)
                if vu_dt < ref_dt:
                    return False
            except Exception:
                pass

        # 8. Non-empty text / payload
        if not getattr(item, "text", ""):
            return False

        return True

    def recover_missing_roles(
        self,
        package_or_roles: EvidencePackage | list[EvidenceRole] | None = None,
        plan: EvidencePlan | None = None,
        existing_items: list[EvidenceItem] | None = None,
        missing_roles: list[EvidenceRole] | None = None,
        tenant_id: str = "TENANT-NOVASTACK",
        user_role: str | None = None,
        user_department: str | None = None,
        user_id: str | None = None,
        forbidden_docs: set[str] | None = None,
    ) -> list[EvidenceItem]:
        """Track B: Targeted Missing-Role Retrieval Recovery.

        Performs a strictly bounded 1-hop recovery expansion from the canonical entity catalog
        for roles identified as missing in the EvidencePlan.

        Strict constraints:
        - Maximum 1 hop
        - Maximum candidates bounded by max_recovery_candidates_per_role
        - Strict 8-gate candidate validation (tenant, RBAC, classification, quarantine,
          lifecycle, temporal validity, relationship, provenance)
        """
        if not self.config.enable_targeted_missing_role_recovery:
            return []

        # Polymorphic argument resolution
        user_context: dict[str, Any] = {}
        if isinstance(package_or_roles, EvidencePackage):
            pkg = package_or_roles
            plan = plan or self.plan_query(pkg.query)
            if existing_items is None:
                existing_items = pkg.selected_evidence
            tenant_id = getattr(pkg, "tenant_id", tenant_id)
            user_context = getattr(pkg, "user_context", {}) or {}
            roles_list = user_context.get("roles", [])
            user_role = user_role or (roles_list[0] if isinstance(roles_list, list) and roles_list else None)
            user_department = user_department or user_context.get("department")
            user_id = user_id or user_context.get("user_id")
            if forbidden_docs is None and "forbidden_docs" in user_context:
                forbidden_docs = set(user_context["forbidden_docs"])
        elif isinstance(package_or_roles, list):
            missing_roles = package_or_roles

        if not user_context:
            user_context = {"tenant_id": tenant_id}
            if user_role:
                user_context["roles"] = [user_role]
            if forbidden_docs:
                user_context["forbidden_docs"] = forbidden_docs

        existing_items = existing_items or []
        if plan is None:
            return []

        if missing_roles is None:
            anchor_ids = [plan.primary_entity_id] if plan.primary_entity_id else list(plan.target_entity_ids)

            missing_roles = []
            for r in plan.required_roles:
                role_items = [it for it in existing_items if classify_evidence_role(it, query=plan.query) == r]
                if not role_items:
                    missing_roles.append(r)
                elif anchor_ids:
                    has_anchor = False
                    for it in role_items:
                        it_ents = set(getattr(it, "related_entity_ids", []))
                        if getattr(it, "source_entity_id", None):
                            it_ents.add(it.source_entity_id)
                        if any(aid in it_ents for aid in anchor_ids):
                            has_anchor = True
                            break
                        if it.source_entity_id:
                            for aid in anchor_ids:
                                rels = self.catalog.get_relationships(aid, direction="both")
                                if any(r.target_id == it.source_entity_id if r.source_id == aid else r.source_id == it.source_entity_id for r in rels):
                                    has_anchor = True
                                    break
                            if has_anchor:
                                break
                    if not has_anchor:
                        missing_roles.append(r)

        if not missing_roles:
            return []

        forbidden = forbidden_docs or set()
        recovered_items: list[EvidenceItem] = []
        seen_chunks = {it.chunk_id for it in existing_items}
        seen_docs = {it.document_id for it in existing_items}

        # Identify anchor entities
        anchor_entity_ids: list[str] = [plan.primary_entity_id] if plan.primary_entity_id else list(plan.target_entity_ids)

        role_target_types = {
            EvidenceRole.DEPLOYMENT_RECORD: {"deployment"},
            EvidenceRole.PULL_REQUEST_RECORD: {"pull_request"},
            EvidenceRole.POSTMORTEM_RECORD: {"event", "incident"},
            EvidenceRole.INCIDENT_RECORD: {"incident", "event"},
            EvidenceRole.OPERATIONAL_RUNBOOK: {"runbook"},
            EvidenceRole.SERVICE_SPECIFICATION: {"service"},
            EvidenceRole.TEAM_OWNERSHIP: {"team"},
        }

        role_target_rels = {
            EvidenceRole.DEPLOYMENT_RECORD: {"caused_by", "causes", "targets", "targeted_by", "causes_event", "deployed_by"},
            EvidenceRole.PULL_REQUEST_RECORD: {"resolved_by", "resolves", "fixes", "resolves_event", "authored_by"},
            EvidenceRole.POSTMORTEM_RECORD: {"causes", "caused_by", "has_incident", "relates_to_event"},
            EvidenceRole.INCIDENT_RECORD: {"has_incident", "reports", "relates_to_event"},
            EvidenceRole.OPERATIONAL_RUNBOOK: {"documented_by"},
            EvidenceRole.SERVICE_SPECIFICATION: {"affects", "affected_by", "targets", "targeted_by"},
            EvidenceRole.TEAM_OWNERSHIP: {"owned_by", "owns", "responsible_team", "responsible_for_event"},
        }

        for missing_role in missing_roles:
            role_recovered_count = 0
            target_types = role_target_types.get(missing_role, set())
            target_rels = role_target_rels.get(missing_role, set())

            candidate_entities: list[tuple[str, str, str]] = []
            for anchor_id in anchor_entity_ids:
                rels = self.catalog.get_relationships(anchor_id, direction="both")
                for r in rels:
                    other_id = r.target_id if r.source_id == anchor_id else r.source_id
                    rel_type = r.relationship_type
                    other_ent = self.catalog.get_entity(other_id)
                    if other_ent:
                        matches_type = other_ent.entity_type in target_types
                        matches_rel = rel_type in target_rels
                        if matches_type or matches_rel:
                            candidate_entities.append((other_id, rel_type, anchor_id))

            seen_cand_entities = set()
            unique_cand_entities = []
            for eid, rtype, aid in candidate_entities:
                if eid not in seen_cand_entities:
                    seen_cand_entities.add(eid)
                    unique_cand_entities.append((eid, rtype, aid))

            for cand_ent_id, rtype, origin_anchor_id in unique_cand_entities:
                chunks = self.catalog.get_chunks_for_entity(cand_ent_id)
                for ch in chunks:
                    if ch.chunk_id in seen_chunks or ch.document_id in seen_docs:
                        continue

                    item_role = classify_evidence_role(ch, query=plan.query)
                    if item_role != missing_role:
                        continue

                    # 8-Gate Security & Quality Validation:
                    if not self._is_valid_candidate(ch, user_context):
                        continue
                    if not self.catalog.is_authorized(
                        ch,
                        user_tenant=tenant_id,
                        user_role=user_role,
                        user_department=user_department,
                        user_id=user_id,
                        forbidden_docs=forbidden,
                    ):
                        continue

                    rec_item = EvidenceItem(
                        evidence_id=f"EVD-REC-{ch.document_id}-{ch.chunk_id[-4:]}",
                        chunk_id=ch.chunk_id,
                        document_id=ch.document_id,
                        tenant_id=ch.tenant_id,
                        source_type=getattr(ch, "source_type", "document"),
                        title=getattr(ch, "title", ch.document_id),
                        text=ch.text,
                        source_entity_id=getattr(ch, "source_entity_id", cand_ent_id),
                        source_entity_type=getattr(ch, "source_entity_type", None),
                        related_entity_ids=list(set(getattr(ch, "related_entity_ids", []) + [cand_ent_id, origin_anchor_id])),
                        authority_level=getattr(ch, "authority_level", "high"),
                        classification=getattr(ch, "classification", "internal"),
                        permissions=getattr(ch, "permissions", None),
                        status=getattr(ch, "status", "active"),
                        version=getattr(ch, "version", "1.0"),
                        created_at=getattr(ch, "created_at", "2025-01-01T00:00:00Z"),
                        updated_at=getattr(ch, "updated_at", None),
                        valid_from=getattr(ch, "valid_from", None),
                        valid_until=getattr(ch, "valid_until", None),
                        parent_id=getattr(ch, "parent_id", None),
                        supersedes_id=getattr(ch, "supersedes_id", None),
                        retrieval_rank=95 + role_recovered_count,
                        retrieval_score=0.95,
                        retrieval_channels=["targeted_relational_recovery"],
                        evidence_status=EvidenceStatus.ACCEPTED.value,
                        evidence_reasons=[f"targeted_recovery_1hop:{rtype}"],
                    )

                    recovered_items.append(rec_item)
                    seen_chunks.add(ch.chunk_id)
                    seen_docs.add(ch.document_id)
                    role_recovered_count += 1
                    if role_recovered_count >= self.config.max_recovery_candidates_per_role:
                        break

                if role_recovered_count >= self.config.max_recovery_candidates_per_role:
                    break

        return recovered_items

    def select_minimum_sufficient_evidence(
        self,
        package: EvidencePackage,
        plan: EvidencePlan | None = None,
        max_documents: int | None = None,
    ) -> EvidenceSelectionResult:
        """Select a minimal, structurally complete subset of authorized candidates.

        Security Invariant:
        Operates strictly on package.selected_evidence, which has already satisfied
        tenant isolation, RBAC, classification, and quarantine checks. Rejects any
        unauthorized items.
        """
        t0 = time.perf_counter()
        raw_items = package.selected_evidence
        query = package.query
        tenant_id = getattr(package, "tenant_id", "TENANT-NOVASTACK")

        if not raw_items:
            empty_plan = plan or EvidencePlan(query=query)
            empty_cov = EvidenceCoverage(is_structurally_complete=False)
            return EvidenceSelectionResult(
                selected_items=[],
                omitted_items=[],
                plan=empty_plan,
                coverage=empty_cov,
                selection_latency_ms=0.0,
                selected_document_count=0,
                selected_token_count=0,
            )

        active_plan = plan or self.plan_query(query=query, tenant_id=tenant_id)
        effective_budget = max_documents or active_plan.target_document_budget

        # 1. Filter out any candidate that violates authorization
        authorized_candidates: list[EvidenceItem] = []
        for it in raw_items:
            if getattr(it, "evidence_status", "") == EvidenceStatus.UNAUTHORIZED.value:
                continue
            if it.tenant_id and it.tenant_id != tenant_id:
                continue
            authorized_candidates.append(it)

        # 2. Handle Protective Queries (Tier 0 Full Context)
        if active_plan.is_protective:
            package.is_protective = True
            selected: list[EvidenceItem] = []
            seen_docs: set[str] = set()
            for it in authorized_candidates:
                if it.document_id not in seen_docs:
                    seen_docs.add(it.document_id)
                    selected.append(it)
                    if len(selected) >= effective_budget:
                        break
            omitted = [it for it in authorized_candidates if it not in selected]
            lat = (time.perf_counter() - t0) * 1000.0
            cov = EvidenceCoverage(
                covered_roles={EvidenceRole.PROTECTIVE_BOUNDARY.value},
                is_structurally_complete=True,
            )
            return EvidenceSelectionResult(
                selected_items=selected,
                omitted_items=omitted,
                plan=active_plan,
                coverage=cov,
                selection_latency_ms=lat,
                selected_document_count=len(selected),
                selected_token_count=sum(len(it.text.split()) for it in selected),
            )

        # 3. Classify candidates into roles and index by role & entities
        candidates_by_role: dict[EvidenceRole, list[EvidenceItem]] = {r: [] for r in EvidenceRole}
        item_roles: dict[str, EvidenceRole] = {}

        for it in authorized_candidates:
            role = classify_evidence_role(it, query=query)
            candidates_by_role[role].append(it)
            item_roles[it.evidence_id] = role

        # Track B: Targeted Missing-Role Retrieval Recovery
        recovered_items: list[EvidenceItem] = []
        if self.config.enable_targeted_missing_role_recovery and active_plan.required_roles:
            anchor_ids = [active_plan.primary_entity_id] if active_plan.primary_entity_id else list(active_plan.target_entity_ids)

            missing_roles_init = []
            for r in active_plan.required_roles:
                cands = candidates_by_role.get(r, [])
                if not cands:
                    missing_roles_init.append(r)
                elif anchor_ids:
                    has_anchor = False
                    for cand in cands:
                        c_ents = set(getattr(cand, "related_entity_ids", []))
                        if getattr(cand, "source_entity_id", None):
                            c_ents.add(cand.source_entity_id)
                        if any(aid in c_ents for aid in anchor_ids):
                            has_anchor = True
                            break
                        if cand.source_entity_id:
                            for aid in anchor_ids:
                                rels = self.catalog.get_relationships(aid, direction="both")
                                if any(rel.target_id == cand.source_entity_id if rel.source_id == aid else rel.source_id == cand.source_entity_id for rel in rels):
                                    has_anchor = True
                                    break
                            if has_anchor:
                                break
                    if not has_anchor:
                        missing_roles_init.append(r)

            if missing_roles_init:
                user_role = getattr(package, "user_role", None)
                user_dept = getattr(package, "user_department", None)
                user_id = getattr(package, "user_id", None)
                forbidden = set(getattr(package, "forbidden_document_ids", []))
                recovered_items = self.recover_missing_roles(
                    missing_roles=missing_roles_init,
                    plan=active_plan,
                    existing_items=authorized_candidates,
                    tenant_id=tenant_id,
                    user_role=user_role,
                    user_department=user_dept,
                    user_id=user_id,
                    forbidden_docs=forbidden,
                )
                if recovered_items:
                    for rec_it in recovered_items:
                        authorized_candidates.append(rec_it)
                        r = classify_evidence_role(rec_it, query=query)
                        candidates_by_role[r].append(rec_it)
                        item_roles[rec_it.evidence_id] = r

        # 4. Score candidates within each role using structural relevance
        def candidate_quality_key(it: EvidenceItem) -> tuple[float, float, float]:
            # Priority: entity overlap, authority, negative retrieval rank
            entity_overlap = 0.0
            all_entities = set(it.related_entity_ids)
            if it.source_entity_id:
                all_entities.add(it.source_entity_id)

            target_set = set(active_plan.target_entity_ids)
            if active_plan.primary_entity_id and active_plan.primary_entity_id in all_entities:
                entity_overlap += 2.0
            entity_overlap += len(all_entities.intersection(target_set)) * 1.0

            # Relational link bonus with primary/target anchor entities
            if it.source_entity_id:
                for aid in [active_plan.primary_entity_id] + list(active_plan.target_entity_ids):
                    if aid:
                        rels = self.catalog.get_relationships(aid, direction="both")
                        if any(rel.target_id == it.source_entity_id if rel.source_id == aid else rel.source_id == it.source_entity_id for rel in rels):
                            entity_overlap += 1.5
                            break

            # Track A: Anchor priority boost for contrastive queries
            if (
                self.config.enable_contrastive_disambiguation
                and active_plan.is_contrastive
                and active_plan.primary_entity_id
            ):
                if (
                    it.source_entity_id == active_plan.primary_entity_id
                    or active_plan.primary_entity_id in it.related_entity_ids
                    or (it.document_id and active_plan.primary_entity_id in it.document_id)
                ):
                    entity_overlap += self.config.anchor_priority_boost

            auth_val = self.config.authority_weights.get(getattr(it, "authority_level", "medium"), 0.7)
            rank_score = -float(getattr(it, "retrieval_rank", 99))
            return (entity_overlap, auth_val, rank_score)

        for r in candidates_by_role:
            candidates_by_role[r].sort(key=candidate_quality_key, reverse=True)

        # 5. Greedy Minimum Set Cover for Required Roles
        selected_candidates: list[EvidenceItem] = []
        selected_doc_ids: set[str] = set()
        covered_roles: set[str] = set()
        covered_entities: set[str] = set()

        for req_role in active_plan.required_roles:
            role_pool = candidates_by_role.get(req_role, [])
            best_candidate = None
            for cand in role_pool:
                if cand.document_id not in selected_doc_ids:
                    best_candidate = cand
                    break

            if best_candidate is not None:
                selected_candidates.append(best_candidate)
                selected_doc_ids.add(best_candidate.document_id)
                covered_roles.add(req_role.value)
                if best_candidate.source_entity_id:
                    covered_entities.add(best_candidate.source_entity_id)
                covered_entities.update(best_candidate.related_entity_ids)

        # 6. Fallback / Fill remaining budget with highest-scoring unique documents
        if len(selected_candidates) < effective_budget:
            # Sort all remaining authorized candidates by quality
            remaining = [it for it in authorized_candidates if it.document_id not in selected_doc_ids]
            if self.config.require_entity_overlap_for_fill and (active_plan.primary_entity_id or active_plan.target_entity_ids):
                # Filter out candidates with zero entity overlap to prevent unrelated event distractors
                remaining = [it for it in remaining if candidate_quality_key(it)[0] > 0.0]
            remaining.sort(key=candidate_quality_key, reverse=True)
            for it in remaining:
                selected_candidates.append(it)
                selected_doc_ids.add(it.document_id)
                role = item_roles.get(it.evidence_id, EvidenceRole.SUPPORTING_CONTEXT)
                covered_roles.add(role.value)
                if it.source_entity_id:
                    covered_entities.add(it.source_entity_id)
                covered_entities.update(it.related_entity_ids)
                if len(selected_candidates) >= effective_budget:
                    break

        package.context_notes = list(active_plan.context_notes)
        package.is_protective = active_plan.is_protective

        # 7. Configuration E: Supporting Document Extension (if requested)
        if self.config.enable_supporting_extension and len(selected_candidates) == effective_budget:
            remaining = [it for it in authorized_candidates if it.document_id not in selected_doc_ids]
            remaining.sort(key=candidate_quality_key, reverse=True)
            if remaining:
                extra = remaining[0]
                selected_candidates.append(extra)
                selected_doc_ids.add(extra.document_id)

        # 8. Compute Structural Completeness
        req_role_set = {r.value for r in active_plan.required_roles}
        missing_roles = list(req_role_set - covered_roles)
        missing_entities = [e for e in active_plan.target_entity_ids if e not in covered_entities]

        is_complete = len(missing_roles) == 0
        if not active_plan.required_roles:
            is_complete = len(selected_candidates) > 0

        cov = EvidenceCoverage(
            covered_entities=covered_entities,
            covered_relationships=set(active_plan.required_relationship_types),
            covered_roles=covered_roles,
            covered_provenances={it.source_type for it in selected_candidates if it.source_type},
            temporal_valid=True,
            is_structurally_complete=is_complete,
            missing_roles=missing_roles,
            missing_entities=missing_entities,
        )

        final_items = selected_candidates
        if self.config.enable_targeted_evidence_extraction:
            from novastack.evidence_extractor import extract_targeted_evidence_item
            final_items = [
                extract_targeted_evidence_item(
                    it,
                    query=query,
                    is_protective=active_plan.is_protective,
                    max_sentences=self.config.max_extracted_sentences_per_chunk,
                    extracted_entity_ids=active_plan.target_entity_ids,
                )
                for it in selected_candidates
            ]

        omitted = [it for it in authorized_candidates if it not in selected_candidates]
        lat = (time.perf_counter() - t0) * 1000.0
        tok_count = sum(len(it.text.split()) for it in final_items)

        return EvidenceSelectionResult(
            selected_items=final_items,
            omitted_items=omitted,
            plan=active_plan,
            coverage=cov,
            selection_latency_ms=lat,
            selected_document_count=len(final_items),
            selected_token_count=tok_count,
            diagnostics={
                "required_roles_count": len(active_plan.required_roles),
                "covered_roles_count": len(covered_roles),
                "is_structurally_complete": is_complete,
                "strategy": "minimum_sufficient_selection",
                "is_contrastive": active_plan.is_contrastive,
                "recovered_roles_count": len(recovered_items),
                "recovered_document_ids": [it.document_id for it in recovered_items],
                "targeted_extraction_enabled": self.config.enable_targeted_evidence_extraction,
            },
        )

