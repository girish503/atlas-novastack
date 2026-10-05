"""Deterministic Evidence Object Model — Phase 4E.

Defines the core data structures for Evidence Assembly & Evidence Resolution:
- EvidenceStatus: Controlled vocabulary for evidence trustworthiness and classification
- EvidenceItem: Unified, auditable evidence representation preserving retrieval provenance
- EvidenceConflict: Structured conflict representation for contradictory claims
- ProvenanceNode: Hierarchical lineage tracking (Evidence -> Chunk -> Doc -> Entity -> Ground Truth)
- EvidencePackage: Final immutable evidence bundle for future grounded LLM answer generation
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from novastack.models import RecordPermissions, SearchChunk, SearchDocument

__all__ = [
    "CONTROLLED_EVIDENCE_STATUSES",
    "EvidenceConflict",
    "EvidenceItem",
    "EvidencePackage",
    "EvidenceStatus",
    "ProvenanceNode",
]


class EvidenceStatus(str, Enum):
    """Controlled vocabulary for evidence classification."""

    ACCEPTED = "accepted"
    ACCEPTED_WITH_CAVEAT = "accepted_with_caveat"
    DOWNGRADED = "downgraded"
    SUPERSEDED = "superseded"
    STALE = "stale"
    DRAFT = "draft"
    CONFLICTING = "conflicting"
    UNAUTHORIZED = "unauthorized"
    ADVERSARIAL = "adversarial"
    DUPLICATE = "duplicate"
    EXCLUDED = "excluded"


CONTROLLED_EVIDENCE_STATUSES: set[str] = {s.value for s in EvidenceStatus}


@dataclass
class ProvenanceNode:
    """Lineage trace connecting an evidence item back to canonical ground truth."""

    evidence_id: str
    chunk_id: str
    document_id: str
    source_entity_id: str | None = None
    source_entity_type: str | None = None
    related_entity_ids: list[str] = field(default_factory=list)
    parent_id: str | None = None
    supersedes_id: str | None = None
    ground_truth_event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert provenance node to dictionary."""
        return asdict(self)


@dataclass
class EvidenceConflict:
    """Record of a detected contradiction or divergence between evidence items."""

    conflict_id: str
    conflict_type: str  # authoritative_vs_low_authority, stale_vs_current, version_superseded, adversarial_poisoning, unresolved_divergence
    entity_id: str | None
    primary_evidence_id: str
    conflicting_evidence_ids: list[str]
    resolution_status: str  # resolved_by_authority, resolved_by_version, resolved_by_temporal, resolved_by_adversarial_filter, conflict_unresolved
    resolution_reason: str

    def to_dict(self) -> dict[str, Any]:
        """Convert conflict record to dictionary."""
        return asdict(self)


@dataclass
class EvidenceItem:
    """Unified, deterministic evidence representation.

    Decouples evidence trustworthiness, authorization, and temporal validity
    from raw retrieval relevance scores.
    """

    evidence_id: str
    chunk_id: str
    document_id: str
    tenant_id: str
    source_type: str
    title: str
    text: str
    source_entity_id: str | None
    source_entity_type: str | None
    related_entity_ids: list[str]
    authority_level: str
    classification: str
    permissions: RecordPermissions
    status: str
    version: str
    created_at: str
    updated_at: str | None
    valid_from: str | None
    valid_until: str | None
    parent_id: str | None
    supersedes_id: str | None
    retrieval_rank: int
    retrieval_score: float
    retrieval_channels: list[str]
    evidence_status: str = EvidenceStatus.ACCEPTED.value
    evidence_reasons: list[str] = field(default_factory=list)
    conflict_ids: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    duplicate_chunk_ids: list[str] = field(default_factory=list)
    trust_score: float = 1.0

    def is_usable_evidence(self) -> bool:
        """Check if evidence item is in an accepted state for grounding."""
        return self.evidence_status in {
            EvidenceStatus.ACCEPTED.value,
            EvidenceStatus.ACCEPTED_WITH_CAVEAT.value,
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert evidence item to dictionary."""
        d = asdict(self)
        if isinstance(self.permissions, RecordPermissions):
            d["permissions"] = asdict(self.permissions)
        d["trust_score"] = round(self.trust_score, 4)
        d["retrieval_score"] = round(self.retrieval_score, 6)
        return d

    @classmethod
    def from_search_chunk(
        cls,
        chunk: SearchChunk,
        evidence_id: str,
        retrieval_rank: int,
        retrieval_score: float,
        retrieval_channels: list[str] | None = None,
        doc_metadata: SearchDocument | None = None,
    ) -> EvidenceItem:
        """Factory method constructing an EvidenceItem from a SearchChunk and document metadata."""
        return cls(
            evidence_id=evidence_id,
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            tenant_id=chunk.tenant_id,
            source_type=chunk.source_type,
            title=chunk.title,
            text=chunk.text,
            source_entity_id=chunk.source_entity_id,
            source_entity_type=chunk.source_entity_type,
            related_entity_ids=list(chunk.related_entity_ids),
            authority_level=chunk.authority_level,
            classification=chunk.classification,
            permissions=chunk.permissions,
            status=chunk.status,
            version=chunk.version,
            created_at=chunk.created_at,
            updated_at=chunk.updated_at,
            valid_from=chunk.valid_from,
            valid_until=chunk.valid_until,
            parent_id=chunk.parent_id,
            supersedes_id=chunk.supersedes_id,
            retrieval_rank=retrieval_rank,
            retrieval_score=retrieval_score,
            retrieval_channels=list(retrieval_channels or []),
            evidence_status=EvidenceStatus.ACCEPTED.value,
            evidence_reasons=[],
        )


@dataclass
class EvidencePackage:
    """Structured, immutable evidence package passed to future grounded LLMs.

    Contains selected evidence, excluded evidence, conflict logs, and provenance traces
    without containing any LLM-generated answer text.
    """

    package_id: str
    evaluation_id: str
    query: str
    tenant_id: str
    user_context: dict[str, Any]
    selected_evidence: list[EvidenceItem]
    excluded_evidence: list[EvidenceItem]
    conflicts: list[EvidenceConflict]
    provenance_graph: list[ProvenanceNode]
    resolution_decisions: list[str]
    statistics: dict[str, Any]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert evidence package to dictionary."""
        return {
            "package_id": self.package_id,
            "evaluation_id": self.evaluation_id,
            "query": self.query,
            "tenant_id": self.tenant_id,
            "user_context": self.user_context,
            "selected_evidence_count": len(self.selected_evidence),
            "excluded_evidence_count": len(self.excluded_evidence),
            "selected_evidence": [e.to_dict() for e in self.selected_evidence],
            "excluded_evidence": [e.to_dict() for e in self.excluded_evidence],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "provenance_graph": [p.to_dict() for p in self.provenance_graph],
            "resolution_decisions": self.resolution_decisions,
            "statistics": self.statistics,
            "diagnostics": self.diagnostics,
        }
