"""Deterministic Entity Catalog & Relational Graph Index — Phase 4D-2.

Provides an in-memory, deterministic knowledge graph and catalog index
built strictly from canonical NovaStack ground-truth entities, relationships,
and search documents/chunks:
- 8 canonical entity types (user, team, customer, service, incident, deployment, pull_request, event)
- Explicit typed relationships from event_relationships.json (92 edges)
- Schema-level relational edges (service ownership, team membership, incident causes, deployment targets)
- Forward and inverse relationship adjacency graphs
- Reverse index mapping entities to supporting search documents and chunks
- Strict authorization boundary filter preventing security bypass
- 100% deterministic execution without external databases or LLMs
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from novastack.models import RecordPermissions, SearchChunk, SearchDocument

__all__ = [
    "CanonicalEntity",
    "EntityCatalog",
    "TypedRelationship",
    "normalize_entity_name",
]

# Regex for standard canonical NovaStack identifiers
IDENTIFIER_PATTERN = re.compile(
    r"\b(SVC|TEAM|USR|CUST|INC|DEP|PR|EVT|DOC)-[A-Z0-9-]+\b"
)


def normalize_entity_name(name: str) -> str:
    """Deterministically normalize an entity name for lookup matching."""
    if not name:
        return ""
    # Lowercase, replace underscores/slashes with hyphens, collapse spaces
    s = name.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s


@dataclass
class CanonicalEntity:
    """A canonical enterprise entity represented in NovaStack ground truth."""

    entity_id: str
    entity_type: str  # service, team, user, customer, incident, deployment, pull_request, event
    name: str
    tenant_id: str
    department: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert CanonicalEntity to a dictionary."""
        return asdict(self)


@dataclass
class TypedRelationship:
    """A directed, typed relationship connecting two canonical entities."""

    relationship_id: str
    source_id: str
    source_type: str
    relationship_type: str  # owned_by, owns, member_of, affects, caused_by, targets, etc.
    target_id: str
    target_type: str
    tenant_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert TypedRelationship to a dictionary."""
        return asdict(self)


class EntityCatalog:
    """In-memory deterministic entity catalog and relational graph index."""

    def __init__(
        self,
        raw_data_dir: Path | str = Path("data/raw/novastack"),
        chunks_path: Path | str = Path("data/processed/novastack/search_chunks.json"),
    ) -> None:
        self.raw_dir = Path(raw_data_dir)
        self.chunks_path = Path(chunks_path)

        # Entity indexes
        self.entities: dict[str, CanonicalEntity] = {}
        self.entities_by_type: dict[str, list[CanonicalEntity]] = {
            "user": [],
            "team": [],
            "customer": [],
            "service": [],
            "incident": [],
            "deployment": [],
            "pull_request": [],
            "event": [],
        }
        self.name_to_id: dict[str, str] = {}
        self.normalized_to_id: dict[str, str] = {}
        self.alias_to_id: dict[str, str] = {}

        # Relational graph (adjacency lists)
        self.forward_graph: dict[str, list[TypedRelationship]] = {}
        self.inverse_graph: dict[str, list[TypedRelationship]] = {}
        self.all_relationships: list[TypedRelationship] = []

        # Document/Chunk mapping
        self.entity_to_chunks: dict[str, list[SearchChunk]] = {}
        self.doc_to_chunks: dict[str, list[SearchChunk]] = {}
        self.chunk_by_id: dict[str, SearchChunk] = {}
        self.all_chunks: list[SearchChunk] = []

        # Load all catalogs and build graph
        self._load_all_entities()
        self._load_all_relationships()
        self._load_search_chunks()
        self._build_alias_index()

    # ------------------------------------------------------------------
    # Loading & Ingestion
    # ------------------------------------------------------------------

    def _load_all_entities(self) -> None:
        """Load all 8 canonical entity types from raw JSON catalogs."""
        # 1. Services
        svc_file = self.raw_dir / "services.json"
        if svc_file.exists():
            with open(svc_file, "r", encoding="utf-8") as f:
                for s in json.load(f).get("services", []):
                    ent = CanonicalEntity(
                        entity_id=s["service_id"],
                        entity_type="service",
                        name=s["name"],
                        tenant_id=s.get("tenant_id", "TENANT-NOVASTACK"),
                        department=s.get("department"),
                        metadata={
                            "owner_team_id": s.get("owner_team_id"),
                            "tier": s.get("tier"),
                            "environment": s.get("environment"),
                            "criticality": s.get("criticality"),
                            "status": s.get("status"),
                        },
                    )
                    self._register_entity(ent)

        # 2. Teams
        team_file = self.raw_dir / "teams.json"
        if team_file.exists():
            with open(team_file, "r", encoding="utf-8") as f:
                for t in json.load(f).get("teams", []):
                    ent = CanonicalEntity(
                        entity_id=t["team_id"],
                        entity_type="team",
                        name=t["name"],
                        tenant_id=t.get("tenant_id", "TENANT-NOVASTACK"),
                        department=t.get("department"),
                        metadata={
                            "manager_id": t.get("manager_id"),
                            "member_ids": t.get("member_ids", []),
                            "service_ids": t.get("service_ids", []),
                        },
                    )
                    self._register_entity(ent)

        # 3. Users
        user_file = self.raw_dir / "users.json"
        if user_file.exists():
            with open(user_file, "r", encoding="utf-8") as f:
                for u in json.load(f).get("users", []):
                    ent = CanonicalEntity(
                        entity_id=u["user_id"],
                        entity_type="user",
                        name=u["name"],
                        tenant_id=u.get("tenant_id", "TENANT-NOVASTACK"),
                        department=u.get("department"),
                        metadata={
                            "email": u.get("email"),
                            "role": u.get("role"),
                            "team_ids": u.get("team_ids", []),
                            "status": u.get("status"),
                        },
                    )
                    self._register_entity(ent)

        # 4. Customers
        cust_file = self.raw_dir / "customers.json"
        if cust_file.exists():
            with open(cust_file, "r", encoding="utf-8") as f:
                for c in json.load(f).get("customers", []):
                    ent = CanonicalEntity(
                        entity_id=c["customer_id"],
                        entity_type="customer",
                        name=c["name"],
                        tenant_id=c.get("tenant_id", "TENANT-NOVASTACK"),
                        metadata={
                            "segment": c.get("segment"),
                            "industry": c.get("industry"),
                            "account_owner_id": c.get("account_owner_id"),
                            "status": c.get("status"),
                        },
                    )
                    self._register_entity(ent)

        # 5. Incidents
        inc_file = self.raw_dir / "incidents.json"
        if inc_file.exists():
            with open(inc_file, "r", encoding="utf-8") as f:
                for i in json.load(f).get("incidents", []):
                    ent = CanonicalEntity(
                        entity_id=i["incident_id"],
                        entity_type="incident",
                        name=i["title"],
                        tenant_id=i.get("tenant_id", "TENANT-NOVASTACK"),
                        metadata={
                            "event_id": i.get("event_id"),
                            "severity": i.get("severity"),
                            "status": i.get("status"),
                            "assigned_team_id": i.get("assigned_team_id"),
                            "incident_commander_id": i.get("incident_commander_id"),
                        },
                    )
                    self._register_entity(ent)

        # 6. Deployments
        dep_file = self.raw_dir / "deployments.json"
        if dep_file.exists():
            with open(dep_file, "r", encoding="utf-8") as f:
                for d in json.load(f).get("deployments", []):
                    ent = CanonicalEntity(
                        entity_id=d["deployment_id"],
                        entity_type="deployment",
                        name=d.get("description", f"Deployment {d['deployment_id']}"),
                        tenant_id=d.get("tenant_id", "TENANT-NOVASTACK"),
                        metadata={
                            "service_id": d.get("service_id"),
                            "deployed_by": d.get("deployed_by"),
                            "team_id": d.get("team_id"),
                            "version": d.get("version"),
                            "status": d.get("status"),
                        },
                    )
                    self._register_entity(ent)

        # 7. Pull Requests
        pr_file = self.raw_dir / "pull_requests.json"
        if pr_file.exists():
            with open(pr_file, "r", encoding="utf-8") as f:
                for p in json.load(f).get("pull_requests", []):
                    ent = CanonicalEntity(
                        entity_id=p["pull_request_id"],
                        entity_type="pull_request",
                        name=p["title"],
                        tenant_id=p.get("tenant_id", "TENANT-NOVASTACK"),
                        metadata={
                            "service_id": p.get("service_id"),
                            "author_id": p.get("author_id"),
                            "team_id": p.get("team_id"),
                            "status": p.get("status"),
                        },
                    )
                    self._register_entity(ent)

        # 8. Events
        evt_file = self.raw_dir / "events.json"
        if evt_file.exists():
            with open(evt_file, "r", encoding="utf-8") as f:
                for e in json.load(f).get("events", []):
                    ent = CanonicalEntity(
                        entity_id=e["event_id"],
                        entity_type="event",
                        name=e["title"],
                        tenant_id=e.get("tenant_id", "TENANT-NOVASTACK"),
                        metadata={
                            "event_type": e.get("event_type"),
                            "severity": e.get("severity"),
                            "affected_service_ids": e.get("affected_service_ids", []),
                            "triggering_deployment_id": e.get("triggering_deployment_id"),
                            "fixing_pull_request_id": e.get("fixing_pull_request_id"),
                            "impacted_customer_ids": e.get("impacted_customer_ids", []),
                            "responsible_team_id": e.get("responsible_team_id"),
                            "status": e.get("status"),
                        },
                    )
                    self._register_entity(ent)

    def _register_entity(self, entity: CanonicalEntity) -> None:
        """Register an entity in all internal indices."""
        self.entities[entity.entity_id] = entity
        if entity.entity_type in self.entities_by_type:
            self.entities_by_type[entity.entity_type].append(entity)

        # Exact and normalized name indexes
        exact_lower = entity.name.strip().lower()
        if exact_lower:
            self.name_to_id[exact_lower] = entity.entity_id

        norm = normalize_entity_name(entity.name)
        if norm:
            self.normalized_to_id[norm] = entity.entity_id

        # Also index entity_id itself
        self.name_to_id[entity.entity_id.lower()] = entity.entity_id
        self.normalized_to_id[entity.entity_id.lower()] = entity.entity_id

    def _load_all_relationships(self) -> None:
        """Load explicit event_relationships and synthesize schema-level relationships."""
        rel_counter = 1

        def _add_rel(
            src_id: str,
            src_type: str,
            rel_type: str,
            tgt_id: str,
            tgt_type: str,
            tenant_id: str = "TENANT-NOVASTACK",
            metadata: dict[str, Any] | None = None,
            inverse_type: str | None = None,
        ) -> None:
            nonlocal rel_counter
            if not src_id or not tgt_id:
                return
            rid = f"REL-SYN-{rel_counter:05d}"
            rel_counter += 1
            rel = TypedRelationship(
                relationship_id=rid,
                source_id=src_id,
                source_type=src_type,
                relationship_type=rel_type,
                target_id=tgt_id,
                target_type=tgt_type,
                tenant_id=tenant_id,
                metadata=metadata or {},
            )
            self.all_relationships.append(rel)
            self.forward_graph.setdefault(src_id, []).append(rel)
            self.inverse_graph.setdefault(tgt_id, []).append(rel)

            if inverse_type:
                inv_rid = f"REL-INV-{rel_counter:05d}"
                rel_counter += 1
                inv_rel = TypedRelationship(
                    relationship_id=inv_rid,
                    source_id=tgt_id,
                    source_type=tgt_type,
                    relationship_type=inverse_type,
                    target_id=src_id,
                    target_type=src_type,
                    tenant_id=tenant_id,
                    metadata=metadata or {},
                )
                self.all_relationships.append(inv_rel)
                self.forward_graph.setdefault(tgt_id, []).append(inv_rel)
                self.inverse_graph.setdefault(src_id, []).append(inv_rel)

        # 1. Ingest explicit event relationships (92 edges)
        evt_rel_file = self.raw_dir / "event_relationships.json"
        if evt_rel_file.exists():
            with open(evt_rel_file, "r", encoding="utf-8") as f:
                for r in json.load(f).get("event_relationships", []):
                    rel = TypedRelationship(
                        relationship_id=r["relationship_id"],
                        source_id=r["source_id"],
                        source_type=r["source_type"],
                        relationship_type=r["relationship_type"],
                        target_id=r["target_id"],
                        target_type=r["target_type"],
                        tenant_id=r.get("tenant_id", "TENANT-NOVASTACK"),
                        metadata={"confidence": r.get("confidence", 1.0)},
                    )
                    self.all_relationships.append(rel)
                    self.forward_graph.setdefault(rel.source_id, []).append(rel)
                    self.inverse_graph.setdefault(rel.target_id, []).append(rel)

                    # Add explicit inverse
                    inv_map = {
                        "targets": "targeted_by",
                        "caused_by": "causes",
                        "resolved_by": "resolves",
                        "affects": "affected_by",
                    }
                    inv_type = inv_map.get(rel.relationship_type)
                    if inv_type:
                        inv_rel = TypedRelationship(
                            relationship_id=f"INV-{rel.relationship_id}",
                            source_id=rel.target_id,
                            source_type=rel.target_type,
                            relationship_type=inv_type,
                            target_id=rel.source_id,
                            target_type=rel.source_type,
                            tenant_id=rel.tenant_id,
                            metadata={"confidence": r.get("confidence", 1.0)},
                        )
                        self.all_relationships.append(inv_rel)
                        self.forward_graph.setdefault(inv_rel.source_id, []).append(inv_rel)
                        self.inverse_graph.setdefault(inv_rel.target_id, []).append(inv_rel)

        # 2. Ingest Schema-Level Relationships
        # Services -> owner_team
        for s in self.entities_by_type["service"]:
            owner_team_id = s.metadata.get("owner_team_id")
            if owner_team_id:
                _add_rel(
                    s.entity_id, "service", "owned_by",
                    owner_team_id, "team",
                    tenant_id=s.tenant_id,
                    inverse_type="owns",
                )

        # Teams -> members, manager, services
        for t in self.entities_by_type["team"]:
            mgr_id = t.metadata.get("manager_id")
            if mgr_id:
                _add_rel(
                    t.entity_id, "team", "managed_by",
                    mgr_id, "user",
                    tenant_id=t.tenant_id,
                    inverse_type="manages",
                )
            for mem_id in t.metadata.get("member_ids", []):
                _add_rel(
                    mem_id, "user", "member_of",
                    t.entity_id, "team",
                    tenant_id=t.tenant_id,
                    inverse_type="has_member",
                )
            for svc_id in t.metadata.get("service_ids", []):
                _add_rel(
                    t.entity_id, "team", "owns",
                    svc_id, "service",
                    tenant_id=t.tenant_id,
                    inverse_type="owned_by",
                )

        # Users -> team_ids
        for u in self.entities_by_type["user"]:
            for team_id in u.metadata.get("team_ids", []):
                _add_rel(
                    u.entity_id, "user", "member_of",
                    team_id, "team",
                    tenant_id=u.tenant_id,
                    inverse_type="has_member",
                )

        # Customers -> account_owner_id
        for c in self.entities_by_type["customer"]:
            owner_id = c.metadata.get("account_owner_id")
            if owner_id:
                _add_rel(
                    c.entity_id, "customer", "managed_by",
                    owner_id, "user",
                    tenant_id=c.tenant_id,
                    inverse_type="manages_customer",
                )

        # Incidents -> assigned_team, commander, event_id
        for i in self.entities_by_type["incident"]:
            team_id = i.metadata.get("assigned_team_id")
            if team_id:
                _add_rel(
                    i.entity_id, "incident", "assigned_to",
                    team_id, "team",
                    tenant_id=i.tenant_id,
                    inverse_type="handles_incident",
                )
            cmd_id = i.metadata.get("incident_commander_id")
            if cmd_id:
                _add_rel(
                    i.entity_id, "incident", "commanded_by",
                    cmd_id, "user",
                    tenant_id=i.tenant_id,
                    inverse_type="commands_incident",
                )
            evt_id = i.metadata.get("event_id")
            if evt_id:
                _add_rel(
                    i.entity_id, "incident", "relates_to_event",
                    evt_id, "event",
                    tenant_id=i.tenant_id,
                    inverse_type="has_incident",
                )

        # Deployments -> service, deployed_by, team
        for d in self.entities_by_type["deployment"]:
            svc_id = d.metadata.get("service_id")
            if svc_id:
                _add_rel(
                    d.entity_id, "deployment", "targets",
                    svc_id, "service",
                    tenant_id=d.tenant_id,
                    inverse_type="targeted_by",
                )
            user_id = d.metadata.get("deployed_by")
            if user_id:
                _add_rel(
                    d.entity_id, "deployment", "deployed_by",
                    user_id, "user",
                    tenant_id=d.tenant_id,
                    inverse_type="deployed",
                )
            team_id = d.metadata.get("team_id")
            if team_id:
                _add_rel(
                    d.entity_id, "deployment", "owned_by",
                    team_id, "team",
                    tenant_id=d.tenant_id,
                    inverse_type="owns_deployment",
                )

        # Pull Requests -> service, author, team
        for p in self.entities_by_type["pull_request"]:
            svc_id = p.metadata.get("service_id")
            if svc_id:
                _add_rel(
                    p.entity_id, "pull_request", "targets",
                    svc_id, "service",
                    tenant_id=p.tenant_id,
                    inverse_type="targeted_by",
                )
            author_id = p.metadata.get("author_id")
            if author_id:
                _add_rel(
                    p.entity_id, "pull_request", "authored_by",
                    author_id, "user",
                    tenant_id=p.tenant_id,
                    inverse_type="authored_pr",
                )
            team_id = p.metadata.get("team_id")
            if team_id:
                _add_rel(
                    p.entity_id, "pull_request", "owned_by",
                    team_id, "team",
                    tenant_id=p.tenant_id,
                    inverse_type="owns_pr",
                )

        # Events -> affected_services, deployment, pr, customers, team
        for e in self.entities_by_type["event"]:
            for svc_id in e.metadata.get("affected_service_ids", []):
                _add_rel(
                    e.entity_id, "event", "affects",
                    svc_id, "service",
                    tenant_id=e.tenant_id,
                    inverse_type="affected_by",
                )
            dep_id = e.metadata.get("triggering_deployment_id")
            if dep_id:
                _add_rel(
                    e.entity_id, "event", "caused_by",
                    dep_id, "deployment",
                    tenant_id=e.tenant_id,
                    inverse_type="causes_event",
                )
            pr_id = e.metadata.get("fixing_pull_request_id")
            if pr_id:
                _add_rel(
                    e.entity_id, "event", "resolved_by",
                    pr_id, "pull_request",
                    tenant_id=e.tenant_id,
                    inverse_type="resolves_event",
                )
            for cust_id in e.metadata.get("impacted_customer_ids", []):
                _add_rel(
                    e.entity_id, "event", "impacts",
                    cust_id, "customer",
                    tenant_id=e.tenant_id,
                    inverse_type="impacted_by",
                )
            team_id = e.metadata.get("responsible_team_id")
            if team_id:
                _add_rel(
                    e.entity_id, "event", "responsible_team",
                    team_id, "team",
                    tenant_id=e.tenant_id,
                    inverse_type="responsible_for_event",
                )

    def _load_search_chunks(self) -> None:
        """Load and index SearchChunks by entity and document ID."""
        if not self.chunks_path.exists():
            return

        with open(self.chunks_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            chunks_list = data.get("search_chunks", [])

        for item in chunks_list:
            chunk = SearchChunk.from_dict(item)
            self.all_chunks.append(chunk)
            self.chunk_by_id[chunk.chunk_id] = chunk

            # Map by document_id
            self.doc_to_chunks.setdefault(chunk.document_id, []).append(chunk)

            # Map by source_entity_id
            if chunk.source_entity_id:
                self.entity_to_chunks.setdefault(chunk.source_entity_id, []).append(chunk)

            # Map by related_entity_ids
            for reid in chunk.related_entity_ids:
                self.entity_to_chunks.setdefault(reid, []).append(chunk)

            # If document_id itself matches an entity pattern, register it
            self.entity_to_chunks.setdefault(chunk.document_id, []).append(chunk)

    def _build_alias_index(self) -> None:
        """Build deterministic alias mappings for common shorthand references."""
        # Short names for services (e.g. 'checkout' -> 'checkout-service')
        for s in self.entities_by_type["service"]:
            svc_name = s.name.lower()
            if svc_name.endswith("-service"):
                short = svc_name[:-8]
                if len(short) >= 3:
                    self.alias_to_id[short] = s.entity_id
                    self.alias_to_id[short.replace("-", " ")] = s.entity_id

        # Hyphenated aliases for multi-word team names
        for t in self.entities_by_type["team"]:
            norm = normalize_entity_name(t.name)
            self.alias_to_id[norm] = t.entity_id
            self.alias_to_id[t.name.lower()] = t.entity_id

    # ------------------------------------------------------------------
    # Query & Lookup API
    # ------------------------------------------------------------------

    def get_entity(self, entity_id: str) -> CanonicalEntity | None:
        """Retrieve an entity by exact canonical identifier."""
        return self.entities.get(entity_id)

    def lookup_entity(self, term: str) -> CanonicalEntity | None:
        """Resolve a term to a CanonicalEntity using exact ID, name, normalized name, or alias."""
        if not term:
            return None

        clean = term.strip()
        upper = clean.upper()

        # 1. Canonical ID exact lookup
        if upper in self.entities:
            return self.entities[upper]

        lower = clean.lower()

        # 2. Exact name lookup
        if lower in self.name_to_id:
            return self.entities[self.name_to_id[lower]]

        # 3. Normalized name lookup
        norm = normalize_entity_name(clean)
        if norm in self.normalized_to_id:
            return self.entities[self.normalized_to_id[norm]]

        # 4. Catalog alias lookup
        if lower in self.alias_to_id:
            return self.entities[self.alias_to_id[lower]]
        if norm in self.alias_to_id:
            return self.entities[self.alias_to_id[norm]]

        return None

    def get_relationships(
        self,
        entity_id: str,
        direction: str = "both",
        rel_types: list[str] | set[str] | None = None,
    ) -> list[TypedRelationship]:
        """Retrieve direct relationships for an entity.

        Args:
            entity_id: Canonical entity ID.
            direction: 'forward', 'inverse', or 'both'.
            rel_types: Optional filter on relationship_type.
        """
        results: list[TypedRelationship] = []
        rel_set = set(rel_types) if rel_types else None

        if direction in ("forward", "both"):
            for r in self.forward_graph.get(entity_id, []):
                if rel_set is None or r.relationship_type in rel_set:
                    results.append(r)

        if direction in ("inverse", "both"):
            for r in self.inverse_graph.get(entity_id, []):
                if rel_set is None or r.relationship_type in rel_set:
                    results.append(r)

        return results

    def traverse(
        self,
        source_entity_id: str,
        rel_types: list[str] | set[str] | None = None,
        max_depth: int = 1,
        max_neighbors_per_hop: int = 10,
        user_tenant: str | None = None,
    ) -> list[tuple[CanonicalEntity, TypedRelationship, int]]:
        """Traverse the relational graph starting from source_entity_id up to max_depth (capped at d <= 3).

        Applies:
        - Bounded traversal depth: min(max_depth, 3)
        - Cycle detection / loop prevention via visited_nodes
        - Branching factor limit: max_neighbors_per_hop per node
        - Strict tenant isolation boundary at every hop

        Returns:
            list of (target_entity, relationship_edge, depth).
        """
        effective_depth = min(max(max_depth, 1), 3)
        visited_nodes: set[str] = {source_entity_id}
        traversed: list[tuple[CanonicalEntity, TypedRelationship, int]] = []
        queue: list[tuple[str, int]] = [(source_entity_id, 0)]

        rel_set = set(rel_types) if rel_types else None
        # Schema-constrained relationship categories (Milestone M2)
        organizational_rel_types: set[str] = {
            "owns", "owned_by", "member_of", "has_member", "manages", "managed_by",
            "owns_deployment", "manages_customer",
        }
        operational_rel_types: set[str] = {
            "caused_by", "causes", "causes_event", "targets", "targeted_by",
            "resolved_by", "resolves", "resolves_event", "affects", "affected_by",
            "deployed_by", "deployed", "relates_to_event", "has_incident",
            "impacts", "impacted_by", "handles_incident", "responsible_team",
            "responsible_for_event", "commanded_by", "commands_incident",
        }
        structural_connectors: set[str] = organizational_rel_types | operational_rel_types

        # Determine schema constraint mode
        is_pure_org = bool(rel_set and rel_set.issubset(organizational_rel_types))
        is_pure_op = bool(rel_set and rel_set.issubset(operational_rel_types))

        while queue:
            curr_id, curr_depth = queue.pop(0)
            if curr_depth >= effective_depth:
                continue

            raw_edges = self.forward_graph.get(curr_id, [])
            # Deterministic edge ordering
            sorted_edges = sorted(raw_edges, key=lambda e: (e.relationship_type, e.target_id))

            followed_count = 0
            for edge in sorted_edges:
                if followed_count >= max_neighbors_per_hop:
                    break

                # Schema-constrained edge gating:
                if rel_set:
                    if curr_depth == 0:
                        # Hop 1: strictly require edge in requested rel_set
                        if edge.relationship_type not in rel_set:
                            continue
                    else:
                        # Hop > 1: constrain to matching schema cluster to prevent cross-domain pollution
                        if is_pure_org:
                            if edge.relationship_type not in organizational_rel_types:
                                continue
                        elif is_pure_op:
                            if edge.relationship_type not in operational_rel_types:
                                continue
                        else:
                            if edge.relationship_type not in rel_set and edge.relationship_type not in structural_connectors:
                                continue
                else:
                    if edge.relationship_type not in structural_connectors:
                        continue

                tgt_id = edge.target_id
                if tgt_id not in visited_nodes:
                    tgt_ent = self.entities.get(tgt_id)
                    if tgt_ent:
                        # Enforce strict tenant boundary at graph traversal hop
                        if user_tenant and tgt_ent.tenant_id != user_tenant:
                            continue

                        visited_nodes.add(tgt_id)
                        traversed.append((tgt_ent, edge, curr_depth + 1))
                        queue.append((tgt_id, curr_depth + 1))
                        followed_count += 1

        return traversed

    def get_chunks_for_entity(self, entity_id: str) -> list[SearchChunk]:
        """Return all SearchChunks directly linked to an entity."""
        return self.entity_to_chunks.get(entity_id, [])

    # ------------------------------------------------------------------
    # Security Boundary Enforcement
    # ------------------------------------------------------------------

    @staticmethod
    def is_authorized(
        chunk: SearchChunk,
        user_tenant: str | None = "TENANT-NOVASTACK",
        user_role: str | None = None,
        user_department: str | None = None,
        user_id: str | None = None,
        forbidden_docs: set[str] | None = None,
    ) -> bool:
        """Enforce strict authorization boundaries on candidate chunks.

        Rules:
        1. Explicit forbidden_document_ids match -> REJECT.
        2. Tenant mismatch -> REJECT (strict tenant isolation).
        3. Allowed roles specified and user_role not matching -> REJECT.
        4. Allowed departments specified and user_department not matching -> REJECT.
        5. Allowed user_ids specified and user_id not matching -> REJECT.
        """
        # 1. Forbidden check
        if forbidden_docs and chunk.document_id in forbidden_docs:
            return False

        # 2. Tenant isolation
        if user_tenant and chunk.tenant_id != user_tenant:
            return False

        perms = chunk.permissions

        # 3. Role restriction (when user_role is explicitly provided)
        if perms.allowed_roles and user_role is not None:
            if user_role not in perms.allowed_roles:
                return False

        # 4. Department restriction (when user_department is explicitly provided)
        if perms.allowed_departments and user_department is not None:
            if user_department not in perms.allowed_departments:
                return False

        # 5. User ACL restriction (when user_id is explicitly provided)
        if perms.allowed_user_ids and user_id is not None:
            if user_id not in perms.allowed_user_ids:
                return False

        return True

    def filter_chunks_by_security(
        self,
        chunks: list[SearchChunk],
        user_tenant: str | None = "TENANT-NOVASTACK",
        user_role: str | None = None,
        user_department: str | None = None,
        user_id: str | None = None,
        forbidden_docs: set[str] | None = None,
    ) -> list[SearchChunk]:
        """Filter a list of chunks through the strict security boundary."""
        return [
            c
            for c in chunks
            if self.is_authorized(
                c,
                user_tenant=user_tenant,
                user_role=user_role,
                user_department=user_department,
                user_id=user_id,
                forbidden_docs=forbidden_docs,
            )
        ]
