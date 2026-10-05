"""Deterministic Event-Centric Evidence Bundling — Phase 4K-C.

Provides bounded, explainable evidence bundling for multi-perspective and
causal-chain queries belonging to the same canonical enterprise event:
1. Recognizes canonical event/entity relationships using the existing EntityCatalog.
2. Activates bundling ONLY when a query exhibits multi-perspective demand and candidate
   evidence establishes a sufficiently strong relationship to a dominant event.
3. Operates strictly downstream of security gates (tenant isolation, RBAC, adversarial quarantine,
   lifecycle, and temporal validity are never bypassed).
4. Preserves authority semantics: observational records remain low/medium authority with caveat.
5. Deterministically selects bounded perspectives (max 3 items) preventing context starvation
   without increasing global Top-K.
6. Records explicit explainability traces for bundle inclusion and exclusion.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from novastack.entity_catalog import EntityCatalog
from novastack.evidence import EvidenceItem, EvidenceStatus

__all__ = [
    "BundledPerspective",
    "EventBundleResult",
    "EventBundlerConfig",
    "EventEvidenceBundler",
]

# Query patterns indicating multi-perspective or causal-chain information demand
MULTI_PERSPECTIVE_PATTERNS: list[re.Pattern] = [
    # Support tickets + PR/fix
    re.compile(r"\b(?:support\s+tickets?|tickets?).*?\band\b.*?\b(?:pr|pull\s+request|fix|resolved)\b", re.IGNORECASE),
    re.compile(r"\b(?:pr|pull\s+request).*?\band\b.*?\b(?:support\s+tickets?|tickets?)\b", re.IGNORECASE),
    # Causal chain / outage trace
    re.compile(r"\b(?:trace|causal\s+chain|causal\s+path|full\s+causal)\b", re.IGNORECASE),
    re.compile(r"\b(?:symptom|service|deployment|pr|resolved).*?\b(?:symptom|service|deployment|pr|resolved)\b", re.IGNORECASE),
    # Triage / notes + postmortem / action items
    re.compile(r"\b(?:triage|channel\s+notes?).*?\band\b.*?\b(?:postmortem|action\s+items?)\b", re.IGNORECASE),
]


@dataclass
class BundledPerspective:
    """Explainability record for an evidence item included in the event bundle."""

    document_id: str
    chunk_id: str
    role: str  # event_overview, causal_trigger, resolution_fix, observational_impact, causal_continuation
    source_entity_id: str | None
    authority_level: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EventBundlerConfig:
    """Hyperparameters and feature flags for event-centric evidence bundling."""

    enable_event_bundling: bool = False
    max_bundle_size: int = 3
    min_event_candidates: int = 2
    top_candidate_rank_threshold: int = 10
    boost_bundle_trust: bool = True


@dataclass
class EventBundleResult:
    """Result of event-centric evidence bundling."""

    is_bundled: bool
    canonical_event_id: str | None
    bundled_items: list[EvidenceItem]
    perspectives: list[BundledPerspective]
    excluded_candidates: list[dict[str, str]]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_bundled": self.is_bundled,
            "canonical_event_id": self.canonical_event_id,
            "bundled_items_count": len(self.bundled_items),
            "perspectives": [p.to_dict() for p in self.perspectives],
            "excluded_candidates": self.excluded_candidates,
            "diagnostics": self.diagnostics,
        }


class EventEvidenceBundler:
    """Deterministic, graph-aware event evidence bundler."""

    def __init__(
        self,
        catalog: EntityCatalog | None = None,
        config: EventBundlerConfig | None = None,
    ) -> None:
        self.catalog = catalog
        self.config = config or EventBundlerConfig()

    def is_multi_perspective_query(self, query: str) -> bool:
        """Deterministically determine if query requires multiple event perspectives."""
        for pattern in MULTI_PERSPECTIVE_PATTERNS:
            if pattern.search(query):
                return True
        return False

    def get_event_affinity(self, item: EvidenceItem) -> set[str]:
        """Find all canonical event IDs associated with an evidence item."""
        events: set[str] = set()

        # 1. Direct source_entity_id
        ent = item.source_entity_id
        if ent:
            if ent.startswith("EVT-"):
                events.add(ent)
            elif self.catalog:
                # Check entity metadata and inverse relations
                if ent in self.catalog.entities:
                    c_ent = self.catalog.entities[ent]
                    if c_ent.entity_type == "event":
                        events.add(c_ent.entity_id)

                # Inverse graph lookup (e.g. INC -> relates_to_event -> EVT)
                for r in self.catalog.inverse_graph.get(ent, []):
                    if r.source_id.startswith("EVT-"):
                        events.add(r.source_id)
                    elif r.target_id.startswith("EVT-"):
                        events.add(r.target_id)
                # Forward graph lookup (e.g. DEP -> causes -> EVT)
                for r in self.catalog.forward_graph.get(ent, []):
                    if r.target_id.startswith("EVT-"):
                        events.add(r.target_id)
                    elif r.source_id.startswith("EVT-"):
                        events.add(r.source_id)

        # 2. Check related_entity_ids
        for rel in item.related_entity_ids:
            if rel.startswith("EVT-"):
                events.add(rel)

        # 3. Check document_id naming conventions (e.g. DOC-PM-EVT-NS-0008-01)
        m = re.search(r"(EVT-NS-\d{4})", item.document_id)
        if m:
            events.add(m.group(1))

        return events

    def identify_dominant_event(
        self,
        candidates: list[EvidenceItem],
        query: str,
    ) -> str | None:
        """Identify if a single canonical event dominates the candidate pool and query."""
        if not candidates:
            return None

        event_scores: dict[str, float] = defaultdict(float)
        event_candidate_counts: dict[str, int] = defaultdict(int)
        top_ranks: dict[str, int] = {}

        for rank, item in enumerate(candidates, start=1):
            affinities = self.get_event_affinity(item)
            for evt_id in affinities:
                event_candidate_counts[evt_id] += 1
                # Weight by reciprocal rank
                event_scores[evt_id] += 1.0 / rank
                if evt_id not in top_ranks:
                    top_ranks[evt_id] = rank

        if not event_scores:
            return None

        # Sort by total score
        sorted_events = sorted(
            event_scores.keys(),
            key=lambda e: (event_scores[e], event_candidate_counts[e]),
            reverse=True,
        )

        dominant_id = sorted_events[0]
        dominant_count = event_candidate_counts[dominant_id]
        dominant_top_rank = top_ranks.get(dominant_id, 999)

        # Enforce affinity threshold:
        # 1. At least min_event_candidates
        # 2. At least one candidate in top rank threshold
        if (
            dominant_count >= self.config.min_event_candidates
            and dominant_top_rank <= self.config.top_candidate_rank_threshold
        ):
            return dominant_id

        return None

    def bundle_evidence(
        self,
        candidates: list[EvidenceItem],
        query: str,
        qu: Any = None,
    ) -> EventBundleResult:
        """Construct a bounded event-centric evidence bundle if conditions are met."""
        if not self.config.enable_event_bundling:
            return EventBundleResult(
                is_bundled=False,
                canonical_event_id=None,
                bundled_items=candidates,
                perspectives=[],
                excluded_candidates=[],
                diagnostics={"reason": "event_bundling_disabled"},
            )

        # Check multi-perspective query demand
        if not self.is_multi_perspective_query(query):
            return EventBundleResult(
                is_bundled=False,
                canonical_event_id=None,
                bundled_items=candidates,
                perspectives=[],
                excluded_candidates=[],
                diagnostics={"reason": "query_not_multi_perspective"},
            )

        # Identify dominant event
        canonical_event_id = self.identify_dominant_event(candidates, query)
        if not canonical_event_id:
            return EventBundleResult(
                is_bundled=False,
                canonical_event_id=None,
                bundled_items=candidates,
                perspectives=[],
                excluded_candidates=[],
                diagnostics={"reason": "no_dominant_canonical_event_identified"},
            )

        # Partition candidates into event members vs non-event members
        event_items: list[EvidenceItem] = []
        other_items: list[EvidenceItem] = []

        for item in candidates:
            if canonical_event_id in self.get_event_affinity(item):
                event_items.append(item)
            else:
                other_items.append(item)

        # Classify event items by perspective role
        role_buckets: dict[str, list[EvidenceItem]] = {
            "event_overview": [],     # postmortem, incident
            "resolution_fix": [],     # pull_request_note
            "support_ticket": [],     # support_ticket
            "conversation": [],       # conversation
            "trigger_cause": [],      # deployment_note
            "causal_continuation": [], # secondary chunk of postmortem
            "other": [],
        }

        seen_docs: set[str] = set()
        primary_postmortem_doc: str | None = None

        for item in event_items:
            stype = item.source_type
            if stype == "postmortem":
                if primary_postmortem_doc is None:
                    primary_postmortem_doc = item.document_id
                    role_buckets["event_overview"].append(item)
                elif item.document_id == primary_postmortem_doc and "CHUNK-0002" in item.chunk_id:
                    role_buckets["causal_continuation"].append(item)
                else:
                    role_buckets["event_overview"].append(item)
            elif stype == "incident":
                role_buckets["event_overview"].append(item)
            elif stype == "pull_request_note":
                role_buckets["resolution_fix"].append(item)
            elif stype == "support_ticket":
                role_buckets["support_ticket"].append(item)
            elif stype == "conversation":
                role_buckets["conversation"].append(item)
            elif stype == "deployment_note":
                role_buckets["trigger_cause"].append(item)
            else:
                role_buckets["other"].append(item)

        # Determine roles requested by query
        wants_ticket = bool(re.search(r"\b(support\s+tickets?|tickets?)\b", query, re.IGNORECASE))
        wants_chat = bool(re.search(r"\b(triage|chat|channel|slack)\b", query, re.IGNORECASE))
        wants_resolution = bool(re.search(r"\b(pr|pull\s+request|fix|resolved|resolv)\b", query, re.IGNORECASE))
        wants_trigger = bool(re.search(r"\b(deploy|deployment|cause|caused|trigger)\b", query, re.IGNORECASE))
        wants_causal_chain = bool(re.search(r"\b(causal\s+chain|causal\s+path|full\s+causal|trace)\b", query, re.IGNORECASE))

        # Select bundle members deterministically up to max_bundle_size (3)
        bundle_items: list[EvidenceItem] = []
        perspectives: list[BundledPerspective] = []
        selected_chunk_ids: set[str] = set()

        def _add_item(it: EvidenceItem, role_name: str, reason_str: str) -> None:
            if it.chunk_id in selected_chunk_ids:
                return
            if len(bundle_items) >= self.config.max_bundle_size:
                return
            # Preserve observational items with caveat
            if it.authority_level in ("low", "draft") or it.source_type in ("support_ticket", "conversation"):
                it.evidence_status = EvidenceStatus.ACCEPTED_WITH_CAVEAT.value
                if "accepted_with_low_authority_caveat" not in it.evidence_reasons:
                    it.evidence_reasons.append("accepted_with_low_authority_caveat")
                it.evidence_reasons.append(f"preserved_in_event_bundle_{canonical_event_id}")
            bundle_items.append(it)
            selected_chunk_ids.add(it.chunk_id)
            perspectives.append(
                BundledPerspective(
                    document_id=it.document_id,
                    chunk_id=it.chunk_id,
                    role=role_name,
                    source_entity_id=it.source_entity_id,
                    authority_level=it.authority_level,
                    reason=reason_str,
                )
            )

        # 1. Primary Event Overview
        if role_buckets["event_overview"]:
            _add_item(
                role_buckets["event_overview"][0],
                "event_overview",
                f"Canonical event overview for {canonical_event_id}",
            )

        # 2. Causal Continuation / Trigger (for causal chain queries)
        if wants_causal_chain:
            if role_buckets["trigger_cause"]:
                _add_item(
                    role_buckets["trigger_cause"][0],
                    "trigger_cause",
                    f"Triggering deployment note for {canonical_event_id}",
                )
            elif role_buckets["causal_continuation"]:
                _add_item(
                    role_buckets["causal_continuation"][0],
                    "causal_continuation",
                    f"Causal chain trigger and resolution details for {canonical_event_id}",
                )

        # 3. Observational Impact (if requested)
        if wants_ticket and role_buckets["support_ticket"]:
            _add_item(
                role_buckets["support_ticket"][0],
                "support_ticket",
                f"Explicitly requested support ticket for {canonical_event_id}",
            )
        elif wants_chat and role_buckets["conversation"]:
            _add_item(
                role_buckets["conversation"][0],
                "conversation",
                f"Explicitly requested conversation / triage notes for {canonical_event_id}",
            )

        # 4. Resolution Fix (if requested or causal chain)
        if (wants_resolution or wants_causal_chain) and role_buckets["resolution_fix"]:
            _add_item(
                role_buckets["resolution_fix"][0],
                "resolution_fix",
                f"Fixing pull request verification for {canonical_event_id}",
            )

        # Fill remaining slots from event items by retrieval rank if bundle not full
        for it in event_items:
            if len(bundle_items) >= self.config.max_bundle_size:
                break
            if it.chunk_id not in selected_chunk_ids:
                _add_item(it, "event_supporting", f"Supporting evidence for {canonical_event_id}")

        # Calibrate bundle trust scores so bundled perspectives take the top slots
        if self.config.boost_bundle_trust:
            for idx, b_item in enumerate(bundle_items):
                b_item.trust_score = round(0.96 - (idx * 0.02), 4)

        # Record excluded candidates
        excluded_candidates: list[dict[str, str]] = []
        for o in other_items:
            other_affinities = self.get_event_affinity(o)
            if other_affinities:
                reason = f"cross_event_divergence (belongs to {','.join(sorted(other_affinities))}, query focused on {canonical_event_id})"
            else:
                reason = f"excluded_from_bundle_{canonical_event_id}"
            excluded_candidates.append({
                "document_id": o.document_id,
                "chunk_id": o.chunk_id,
                "reason": reason,
            })

        for remaining_evt_item in event_items:
            if remaining_evt_item.chunk_id not in selected_chunk_ids:
                excluded_candidates.append({
                    "document_id": remaining_evt_item.document_id,
                    "chunk_id": remaining_evt_item.chunk_id,
                    "reason": f"excluded_by_bundle_capacity_limit (max {self.config.max_bundle_size})",
                })

        # Assemble final ordered evidence: bundle members FIRST, then remaining items
        final_selected = bundle_items + [
            it for it in candidates if it.chunk_id not in selected_chunk_ids
        ]

        return EventBundleResult(
            is_bundled=True,
            canonical_event_id=canonical_event_id,
            bundled_items=final_selected,
            perspectives=perspectives,
            excluded_candidates=excluded_candidates,
            diagnostics={
                "canonical_event_id": canonical_event_id,
                "bundle_size": len(bundle_items),
                "event_candidates_count": len(event_items),
                "other_candidates_count": len(other_items),
                "perspectives_selected": [p.role for p in perspectives],
            },
        )
