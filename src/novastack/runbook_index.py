"""Deterministic Entity-to-Runbook Reverse Index — Phase 0.5 Milestone M3.

Provides an in-memory, deterministic, tenant-isolated reverse index mapping
canonical enterprise entities (services, incidents, deployments, teams, policies)
to relevant runbooks, standard operating procedures, recovery guides, and documentation.

Guarantees:
1. 100% deterministic, inspectable index built from structured ground truth.
2. Tenant isolation enforced at query time.
3. RBAC and department-level authorization filtering.
4. Structured provenance tracing (direct link vs 1-hop relational derivation).
5. Zero dependency on probabilistic heuristics or external databases.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from novastack.entity_catalog import CanonicalEntity, EntityCatalog, TypedRelationship
from novastack.models import RecordPermissions, SearchChunk, SearchDocument

logger = logging.getLogger("novastack.runbook_index")

__all__ = [
    "EntityRunbookIndex",
    "RunbookEntry",
]

# Title patterns indicating runbooks, procedures, and recovery documents
RUNBOOK_TITLE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(runbook|operational\s+runbook|recovery\s+procedure|mitigation\s+runbook)\b", re.IGNORECASE),
    re.compile(r"\b(standard\s+operating\s+procedure|sop|playbook|incident\s+response\s+guide)\b", re.IGNORECASE),
    re.compile(r"\b(handbook|failover\s+guide|emergency\s+procedure|disaster\s+recovery)\b", re.IGNORECASE),
)


@dataclass
class RunbookEntry:
    """A structured runbook, procedure, or policy document entry linked to an entity."""

    document_id: str
    chunk_ids: list[str]
    title: str
    source_type: str  # documentation, engineering_note, policy
    category: str  # runbook, procedure, policy, recovery_guide
    tenant_id: str
    permissions: RecordPermissions
    primary_entity_id: str | None
    related_entity_ids: list[str]
    traversal_hops: int  # 0 for direct entity link, 1 for 1-hop relational
    relationship_path: str  # e.g. "direct", "incident->service->runbook", "deployment->service->runbook"
    department: str = "Engineering"
    authority_level: str = "authoritative"
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert RunbookEntry to dictionary."""
        d = asdict(self)
        d["permissions"] = self.permissions.to_dict() if hasattr(self.permissions, "to_dict") else asdict(self.permissions)
        return d


class EntityRunbookIndex:
    """In-memory reverse index mapping entities to structured runbooks and procedures."""

    def __init__(
        self,
        catalog: Optional[EntityCatalog] = None,
        raw_data_dir: Path | str = Path("data/raw/novastack"),
        chunks_path: Path | str = Path("data/processed/novastack/search_chunks.json"),
    ) -> None:
        self.catalog = catalog or EntityCatalog(raw_data_dir=raw_data_dir, chunks_path=chunks_path)
        self.chunks_path = Path(chunks_path)

        # Primary reverse index: entity_id -> list[RunbookEntry]
        self._entity_to_runbooks: dict[str, list[RunbookEntry]] = defaultdict(list)
        # All runbook entries indexed by document_id
        self._runbooks_by_doc_id: dict[str, RunbookEntry] = {}
        # Document ID to SearchChunks mapping
        self._doc_chunks: dict[str, list[SearchChunk]] = defaultdict(list)

        self._build_index()

    def _classify_document_category(self, title: str, source_type: str) -> str | None:
        """Classify document into runbook/procedure/policy category, or None if not an operational guide."""
        title_lower = title.lower()
        if "recovery" in title_lower or "mitigation" in title_lower:
            return "recovery_guide"
        if "runbook" in title_lower or "playbook" in title_lower:
            return "runbook"
        if "procedure" in title_lower or "sop" in title_lower:
            return "procedure"
        if source_type == "policy" or "policy" in title_lower:
            return "policy"
        if source_type == "documentation" and any(p.search(title) for p in RUNBOOK_TITLE_PATTERNS):
            return "runbook"
        if source_type == "engineering_note" and "runbook" in title_lower:
            return "runbook"
        return None

    def _build_index(self) -> None:
        """Build deterministic reverse mappings from chunks and catalog relationships."""
        # 1. Group all chunks by document_id
        for chunk in self.catalog.all_chunks:
            self._doc_chunks[chunk.document_id].append(chunk)

        # 2. Identify all runbook/procedure documents
        runbook_docs: dict[str, tuple[str, SearchChunk]] = {}
        for doc_id, chunks in self._doc_chunks.items():
            first_chunk = chunks[0]
            cat = self._classify_document_category(first_chunk.title, first_chunk.source_type)
            if cat is not None:
                runbook_docs[doc_id] = (cat, first_chunk)

        # 3. Direct Indexing: Entity -> Runbooks (0 hops)
        for doc_id, (cat, sample_chunk) in runbook_docs.items():
            chunk_ids = [c.chunk_id for c in self._doc_chunks[doc_id]]
            entry = RunbookEntry(
                document_id=doc_id,
                chunk_ids=chunk_ids,
                title=sample_chunk.title,
                source_type=sample_chunk.source_type,
                category=cat,
                tenant_id=sample_chunk.tenant_id,
                permissions=sample_chunk.permissions,
                primary_entity_id=sample_chunk.source_entity_id,
                related_entity_ids=list(sample_chunk.related_entity_ids),
                traversal_hops=0,
                relationship_path="direct",
                department=sample_chunk.department,
                authority_level=sample_chunk.authority_level,
                created_at=sample_chunk.created_at,
            )
            self._runbooks_by_doc_id[doc_id] = entry

            # Direct mapping from source_entity_id
            if sample_chunk.source_entity_id:
                self._entity_to_runbooks[sample_chunk.source_entity_id].append(entry)

            # Direct mapping from related_entity_ids
            for reid in sample_chunk.related_entity_ids:
                if reid != sample_chunk.source_entity_id:
                    self._entity_to_runbooks[reid].append(entry)

        # 4. Structured Relational Derivation (1 hop):
        # Service -> Incident / Deployment / PR / Team
        for ent_id, ent in self.catalog.entities.items():
            # A. Incident -> Service Runbook (Incident -> affects / targets -> Service -> Runbook)
            if ent.entity_type == "incident":
                evt_id = ent.metadata.get("event_id")
                if evt_id:
                    evt = self.catalog.get_entity(evt_id)
                    if evt:
                        for svc_id in evt.metadata.get("affected_service_ids", []):
                            for r in self._entity_to_runbooks.get(svc_id, []):
                                if r.traversal_hops == 0 and r.document_id not in {x.document_id for x in self._entity_to_runbooks[ent_id]}:
                                    derived = RunbookEntry(
                                        document_id=r.document_id,
                                        chunk_ids=r.chunk_ids,
                                        title=r.title,
                                        source_type=r.source_type,
                                        category=r.category,
                                        tenant_id=r.tenant_id,
                                        permissions=r.permissions,
                                        primary_entity_id=r.primary_entity_id,
                                        related_entity_ids=r.related_entity_ids,
                                        traversal_hops=1,
                                        relationship_path="incident->service->runbook",
                                        department=r.department,
                                        authority_level=r.authority_level,
                                        created_at=r.created_at,
                                    )
                                    self._entity_to_runbooks[ent_id].append(derived)

            # B. Deployment -> Service Runbook (Deployment -> targets -> Service -> Runbook)
            elif ent.entity_type == "deployment":
                svc_id = ent.metadata.get("service_id")
                if svc_id:
                    for r in self._entity_to_runbooks.get(svc_id, []):
                        if r.traversal_hops == 0 and r.document_id not in {x.document_id for x in self._entity_to_runbooks[ent_id]}:
                            derived = RunbookEntry(
                                document_id=r.document_id,
                                chunk_ids=r.chunk_ids,
                                title=r.title,
                                source_type=r.source_type,
                                category=r.category,
                                tenant_id=r.tenant_id,
                                permissions=r.permissions,
                                primary_entity_id=r.primary_entity_id,
                                related_entity_ids=r.related_entity_ids,
                                traversal_hops=1,
                                relationship_path="deployment->service->runbook",
                                department=r.department,
                                authority_level=r.authority_level,
                                created_at=r.created_at,
                            )
                            self._entity_to_runbooks[ent_id].append(derived)

            # C. Team -> Service Runbook (Team -> owns -> Service -> Runbook)
            elif ent.entity_type == "team":
                for svc_id in ent.metadata.get("service_ids", []):
                    for r in self._entity_to_runbooks.get(svc_id, []):
                        if r.traversal_hops == 0 and r.document_id not in {x.document_id for x in self._entity_to_runbooks[ent_id]}:
                            derived = RunbookEntry(
                                document_id=r.document_id,
                                chunk_ids=r.chunk_ids,
                                title=r.title,
                                source_type=r.source_type,
                                category=r.category,
                                tenant_id=r.tenant_id,
                                permissions=r.permissions,
                                primary_entity_id=r.primary_entity_id,
                                related_entity_ids=r.related_entity_ids,
                                traversal_hops=1,
                                relationship_path="team->owns_service->runbook",
                                department=r.department,
                                authority_level=r.authority_level,
                                created_at=r.created_at,
                            )
                            self._entity_to_runbooks[ent_id].append(derived)

    def lookup_runbooks_for_entity(
        self,
        entity_id: str,
        user_tenant: str = "TENANT-NOVASTACK",
        user_role: str | None = None,
        user_department: str | None = None,
        user_id: str | None = None,
        forbidden_docs: set[str] | None = None,
        max_hops: int = 1,
    ) -> list[RunbookEntry]:
        """Retrieve runbooks for an entity with strict tenant and RBAC authorization filtering.

        Args:
            entity_id: Canonical entity ID (e.g. 'SVC-NS-0005', 'INC-NS-0001').
            user_tenant: Tenant ID of the requesting user.
            user_role: Role of the user.
            user_department: Department of the user.
            user_id: User identifier.
            forbidden_docs: Set of forbidden document IDs.
            max_hops: 0 for direct only, 1 for 1-hop relational links.

        Returns:
            list of authorized RunbookEntry objects ordered deterministically by hops, category, document_id.
        """
        raw_entries = self._entity_to_runbooks.get(entity_id, [])
        if not raw_entries:
            return []

        authorized: list[RunbookEntry] = []
        seen_doc_ids: set[str] = set()

        for entry in raw_entries:
            if entry.traversal_hops > max_hops:
                continue

            # Deduplicate by document ID
            if entry.document_id in seen_doc_ids:
                continue

            # 1. Tenant boundary check
            if user_tenant and entry.tenant_id != user_tenant:
                continue

            # 2. Forbidden document check
            if forbidden_docs and entry.document_id in forbidden_docs:
                continue

            # 3. RBAC permission check
            perms = entry.permissions
            if perms.allowed_roles and user_role is not None:
                if user_role not in perms.allowed_roles:
                    continue

            # 4. Department permission check
            if perms.allowed_departments and user_department is not None:
                if user_department not in perms.allowed_departments:
                    continue

            # 5. User ACL check
            if perms.allowed_user_ids and user_id is not None:
                if user_id not in perms.allowed_user_ids:
                    continue

            seen_doc_ids.add(entry.document_id)
            authorized.append(entry)

        # Sort deterministically: direct first (hops ascending), then category, then doc_id
        authorized.sort(key=lambda r: (r.traversal_hops, r.category, r.document_id))
        return authorized

    def get_chunks_for_runbook(self, document_id: str) -> list[SearchChunk]:
        """Retrieve all SearchChunks for a runbook document."""
        return self._doc_chunks.get(document_id, [])

    def get_index_stats(self) -> dict[str, Any]:
        """Return diagnostic statistics of the reverse index."""
        distinct_entities = len(self._entity_to_runbooks)
        total_mappings = sum(len(entries) for entries in self._entity_to_runbooks.values())
        runbook_count = len(self._runbooks_by_doc_id)
        categories = Counter_categories = defaultdict(int)
        for r in self._runbooks_by_doc_id.values():
            categories[r.category] += 1

        return {
            "indexed_runbook_documents": runbook_count,
            "distinct_entities_with_runbooks": distinct_entities,
            "total_entity_runbook_mappings": total_mappings,
            "categories": dict(categories),
            "estimated_memory_kb": round(total_mappings * 0.25, 2),
        }
