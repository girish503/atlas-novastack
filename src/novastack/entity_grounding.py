"""Deterministic Entity Grounding & Calibrated Compaction Gate — Phase 0.5 Milestone M3.

Provides deterministic query entity grounding and evidence-domain validation to
govern when salience context compaction is safe to apply:
1. Recognizes catalog entities, identifiers, services, incidents, deployments, and teams.
2. Identifies ungrounded / out-of-scope queries (e.g. satellite downlinks, uncatalogued systems).
3. Identifies sensitive / secret-seeking queries seeking credentials, private keys, or raw tokens.
4. Determines evidence chunk domain compatibility and compaction safety.
5. Critical Safety Invariant: When grounding is insufficient or when querying sensitive topics,
   compaction is strictly disabled (preserving full contextual evidence for safe abstention).
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from novastack.entity_catalog import CanonicalEntity, EntityCatalog, normalize_entity_name
from novastack.evidence import EvidenceItem
from novastack.models import SearchChunk

logger = logging.getLogger("novastack.entity_grounding")

__all__ = [
    "EntityGroundingGate",
    "GroundingDecision",
    "QueryGroundingResult",
]

# Sensitive secret-seeking query patterns (raw credentials, tokens, private keys)
SECRET_SEEKING_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(api\s+tokens?|authorization\s+tokens?|bearer\s+tokens?|secret\s+keys?|private\s+keys?)\b", re.IGNORECASE),
    re.compile(r"\b(ssh\s+keys?|encryption\s+keys?|master\s+keys?|rotation\s+secrets?|vault\s+passwords?)\b", re.IGNORECASE),
    re.compile(r"\b(production\s+passwords?|root\s+credentials?|admin\s+credentials?|signing\s+secrets?)\b", re.IGNORECASE),
    re.compile(r"\b(access\s+tokens?|session\s+tokens?|trunk(?:ing)?\s+tokens?|api\s+secrets?)\b", re.IGNORECASE),
)

# Known out-of-scope domain signals (topics completely uncatalogued in NovaStack cloud software domain)
OUT_OF_SCOPE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(satellite\s+downlink|antenna|ground\s+station|orbital\s+decay|spacecraft)\b", re.IGNORECASE),
    re.compile(r"\b(quantum\s+cryptography|particle\s+accelerator|nuclear\s+reactor)\b", re.IGNORECASE),
    re.compile(r"\b(flight\s+control|avionics|radar\s+tracking)\b", re.IGNORECASE),
)


@dataclass
class QueryGroundingResult:
    """Structured deterministic grounding analysis for a query."""

    query: str
    tenant_id: str
    grounded_entities: list[CanonicalEntity] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)
    entity_types: set[str] = field(default_factory=set)
    is_grounded: bool = False
    is_secret_seeking: bool = False
    is_out_of_scope: bool = False
    grounding_confidence: float = 0.0
    matched_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert QueryGroundingResult to dictionary."""
        d = asdict(self)
        d["entity_types"] = list(self.entity_types)
        d["grounded_entities"] = [e.to_dict() if hasattr(e, "to_dict") else e for e in self.grounded_entities]
        return d


@dataclass
class GroundingDecision:
    """Decision on whether salience compaction is safe to apply to a specific evidence chunk."""

    chunk_id: str
    document_id: str
    compaction_eligible: bool
    reason: str
    entity_overlap_detected: bool = False
    domain_compatible: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert GroundingDecision to dictionary."""
        return asdict(self)


class EntityGroundingGate:
    """Deterministic entity/topic grounding and compaction eligibility gate."""

    def __init__(self, catalog: Optional[EntityCatalog] = None) -> None:
        self.catalog = catalog or EntityCatalog()

    def ground_query(self, query: str, tenant_id: str = "TENANT-NOVASTACK") -> QueryGroundingResult:
        """Deterministically determine if a query has valid catalog grounding anchors.

        Inspects:
        - Canonical identifiers (SVC-, INC-, DEP-, PR-, TEAM-, USR-, CUST-, EVT-)
        - Exact entity names and catalog aliases
        - Secret/credential seeking patterns
        - Out-of-scope domain markers
        """
        clean_query = query.strip()
        matched_entities: list[CanonicalEntity] = []
        matched_ids: list[str] = []
        matched_terms: list[str] = []

        # 1. Check for sensitive secret seeking patterns
        is_secret = any(p.search(clean_query) for p in SECRET_SEEKING_PATTERNS)

        # 2. Check for out-of-scope domain markers
        is_out_of_scope = any(p.search(clean_query) for p in OUT_OF_SCOPE_PATTERNS)

        # 3. Direct identifier regex matching
        id_matches = re.findall(r"\b(SVC|TEAM|USR|CUST|INC|DEP|PR|EVT)-[A-Z0-9-]+\b", clean_query)
        for mid in id_matches:
            ent = self.catalog.get_entity(mid)
            if ent and (not tenant_id or ent.tenant_id == tenant_id):
                matched_entities.append(ent)
                matched_ids.append(ent.entity_id)
                matched_terms.append(mid)

        # 4. Multi-word and single-word catalog entity / alias lookups
        words = re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", clean_query)
        # Check n-grams up to 4 words
        for n in range(min(4, len(words)), 0, -1):
            for i in range(len(words) - n + 1):
                ngram = " ".join(words[i : i + n])
                ent = self.catalog.lookup_entity(ngram)
                if ent and ent.entity_id not in matched_ids:
                    if not tenant_id or ent.tenant_id == tenant_id:
                        matched_entities.append(ent)
                        matched_ids.append(ent.entity_id)
                        matched_terms.append(ngram)

        # 5. Incident numbers in query (e.g. "INC-NS-0001", "incident 1")
        m_inc = re.search(r"\bincident\s+(\d+)\b", clean_query, re.IGNORECASE)
        if m_inc:
            inc_id = f"INC-NS-{int(m_inc.group(1)):04d}"
            ent = self.catalog.get_entity(inc_id)
            if ent and ent.entity_id not in matched_ids:
                matched_entities.append(ent)
                matched_ids.append(ent.entity_id)
                matched_terms.append(m_inc.group(0))

        entity_types = {e.entity_type for e in matched_entities}
        is_grounded = len(matched_entities) > 0 and not is_out_of_scope

        confidence = 1.0 if len(matched_entities) >= 2 else (0.85 if len(matched_entities) == 1 else 0.0)
        if is_out_of_scope:
            confidence = 0.0

        return QueryGroundingResult(
            query=clean_query,
            tenant_id=tenant_id,
            grounded_entities=matched_entities,
            entity_ids=matched_ids,
            entity_types=entity_types,
            is_grounded=is_grounded,
            is_secret_seeking=is_secret,
            is_out_of_scope=is_out_of_scope,
            grounding_confidence=confidence,
            matched_terms=matched_terms,
        )

    def evaluate_compaction_safety(
        self,
        query: str,
        chunk: SearchChunk | EvidenceItem,
        grounding: Optional[QueryGroundingResult] = None,
        tenant_id: str = "TENANT-NOVASTACK",
    ) -> GroundingDecision:
        """Determine whether salience compaction is safe to apply to a specific evidence chunk.

        Rules:
        1. If query is out-of-scope -> REJECT compaction (preserve full context for abstention).
        2. If query is secret-seeking -> REJECT compaction (preserve full context for abstention).
        3. If query is ungrounded (no recognized catalog entities) -> REJECT compaction.
        4. If chunk has NO entity or domain relation to the query -> REJECT compaction.
        5. If chunk matches query's grounded entities or 1-hop neighborhood -> ALLOW compaction.
        """
        cid = getattr(chunk, "chunk_id", "")
        did = getattr(chunk, "document_id", "")

        g = grounding or self.ground_query(query, tenant_id=tenant_id)

        # Safety Rule 1: Out of scope queries must preserve uncompacted context (fixes EVAL-0054)
        if g.is_out_of_scope:
            return GroundingDecision(
                chunk_id=cid,
                document_id=did,
                compaction_eligible=False,
                reason="query_out_of_scope_preserves_abstention_context",
                entity_overlap_detected=False,
                domain_compatible=False,
            )

        # Safety Rule 2: Secret-seeking queries must preserve uncompacted context (fixes EVAL-0058)
        if g.is_secret_seeking:
            return GroundingDecision(
                chunk_id=cid,
                document_id=did,
                compaction_eligible=False,
                reason="secret_seeking_query_preserves_policy_context",
                entity_overlap_detected=False,
                domain_compatible=False,
            )

        # Safety Rule 3: Ungrounded queries must preserve full context
        if not g.is_grounded:
            return GroundingDecision(
                chunk_id=cid,
                document_id=did,
                compaction_eligible=False,
                reason="ungrounded_query_preserves_full_evidence",
                entity_overlap_detected=False,
                domain_compatible=False,
            )

        # Check chunk entity linkage
        chunk_source_ent = getattr(chunk, "source_entity_id", None)
        chunk_related_ents = set(getattr(chunk, "related_entity_ids", []) or [])
        chunk_all_ents = {chunk_source_ent} | chunk_related_ents if chunk_source_ent else chunk_related_ents

        query_ent_set = set(g.entity_ids)

        # Direct entity overlap
        direct_overlap = bool(chunk_all_ents & query_ent_set)

        # 1-hop relational neighborhood overlap
        relational_overlap = False
        if not direct_overlap:
            for q_ent_id in g.entity_ids:
                rels = self.catalog.get_relationships(q_ent_id, direction="both")
                neighbor_ids = {r.target_id for r in rels} | {r.source_id for r in rels}
                if chunk_all_ents & neighbor_ids:
                    relational_overlap = True
                    break

        domain_compatible = direct_overlap or relational_overlap

        # Title keyword overlap for procedure/runbook chunks of the same service
        title = getattr(chunk, "title", "").lower()
        title_overlap = any(term.lower() in title for term in g.matched_terms if len(term) >= 4)

        if domain_compatible or title_overlap:
            return GroundingDecision(
                chunk_id=cid,
                document_id=did,
                compaction_eligible=True,
                reason="entity_domain_compatible_compaction_safe",
                entity_overlap_detected=direct_overlap,
                domain_compatible=True,
            )

        # Background chunk without entity overlap -> preserve full text to avoid superficial sentence leakage
        return GroundingDecision(
            chunk_id=cid,
            document_id=did,
            compaction_eligible=False,
            reason="unrelated_background_chunk_compaction_ineligible",
            entity_overlap_detected=False,
            domain_compatible=False,
        )
