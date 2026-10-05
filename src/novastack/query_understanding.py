"""Deterministic Query Understanding & Entity Recognition — Phase 4C-1.

Extracts structured enterprise signals from queries using deterministic
methods only (compiled regex, controlled vocabularies, ground-truth catalogs):
- Entity identification (Services, Teams, Customers, Incidents, Deployments, PRs, Events)
- Identifier recognition (INC-, DEP-, PR-, EVT-, SVC-, USR-, TEAM-, CUST-)
- Catalog-based alias resolution (e.g. 'checkout' -> 'checkout-service')
- Temporal constraint extraction (dates, ranges, sequence markers)
- Lifecycle/version constraints (latest, active, deprecated, superseded, v1-v3)
- Relationship signal extraction (owns, caused by, affects, deploys, fixes)
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "AliasMention",
    "EntityCatalog",
    "EntityMention",
    "IdentifierMention",
    "LifecycleConstraint",
    "QueryUnderstanding",
    "QueryUnderstandingExtractor",
    "RelationshipSignal",
    "TemporalConstraint",
    "TemporalInterval",
    "extract_temporal_interval",
    "is_temporally_valid",
    "parse_iso_timestamp",
]

# Controlled Identifier Pattern
_ID_PATTERN = re.compile(
    r"\b(INC|DEP|PR|EVT|SVC|USR|TEAM|CUST|TKT|DOC)-[A-Z0-9-]+\b"
)

# Explicit Relationship Vocabulary
RELATIONSHIP_VERB_MAP: dict[str, str] = {
    "owned by": "owned_by",
    "owns": "owns",
    "caused by": "caused_by",
    "caused": "caused",
    "affects": "affects",
    "affected by": "affected_by",
    "contains": "contains",
    "contained in": "contained_in",
    "related to": "related_to",
    "documented by": "documented_by",
    "discussed in": "discussed_in",
    "deployed in": "deployed_in",
    "deploys": "deploys",
    "deployed": "deployed",
    "fixed by": "fixed_by",
    "fixes": "fixes",
    "escalation": "escalated_to",
}

# Explicit Temporal Markers
TEMPORAL_MARKERS = [
    "before",
    "after",
    "during",
    "between",
    "timeline",
    "duration",
    "how long",
    "as of",
    "historical",
    "latest",
    "current",
    "most recent",
]

# Explicit Lifecycle Markers
LIFECYCLE_MARKERS = [
    "latest",
    "current",
    "active",
    "published",
    "deprecated",
    "superseded",
    "previous",
    "historical",
    "version",
    "v1",
    "v2",
    "v3",
]


@dataclass
class EntityMention:
    """An entity recognized from the query text."""

    entity_type: str  # service, team, customer, incident, deployment, pull_request, event
    entity_id: str
    matched_text: str
    match_method: str  # exact_name, exact_id, alias


@dataclass
class IdentifierMention:
    """An exact formal identifier recognized in the query."""

    identifier_type: str  # INC, DEP, PR, EVT, SVC, USR, TEAM, CUST, etc.
    identifier: str
    matched_text: str


@dataclass
class AliasMention:
    """A recognized enterprise alias mapped to its canonical entity ID."""

    canonical_entity_id: str
    alias: str
    matched_text: str


@dataclass
class TemporalConstraint:
    """An extracted temporal constraint."""

    type: str  # date, interval, relative_sequence, recency
    value: str
    start: str | None = None
    end: str | None = None


MONTH_LOOKUP: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_iso_timestamp(text: str | None) -> str | None:
    """Normalize a date or timestamp expression into an ISO-8601 string (YYYY-MM-DDTHH:MM:SS)."""
    if not text:
        return None
    s = str(text).strip()
    # 1. ISO format already
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?", s):
        if "T" not in s:
            return f"{s}T00:00:00"
        return s

    # 2. Month Day, Year [at HH:MM [UTC]]
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})(?:\s+at\s+(\d{1,2}):(\d{2})(?:\s*UTC)?)?", s, re.IGNORECASE)
    if m:
        mon_str, day, year, hr, mn = m.groups()
        mon = MONTH_LOOKUP.get(mon_str.lower())
        if mon:
            h = int(hr) if hr else 0
            minute = int(mn) if mn else 0
            return f"{int(year):04d}-{mon:02d}-{int(day):02d}T{h:02d}:{minute:02d}:00"

    # 3. Month Year (e.g. July 2025)
    m2 = re.search(r"([A-Za-z]+)\s+(\d{4})", s, re.IGNORECASE)
    if m2:
        mon_str, year = m2.groups()
        mon = MONTH_LOOKUP.get(mon_str.lower())
        if mon:
            return f"{int(year):04d}-{mon:02d}-01T00:00:00"

    # 4. Year only
    m3 = re.search(r"\b(20\d{2})\b", s)
    if m3:
        return f"{m3.group(1)}-01-01T00:00:00"

    return None


@dataclass
class TemporalInterval:
    """Structured representation of a temporal constraint or interval."""

    start: str | None = None          # ISO-8601 start timestamp (inclusive)
    end: str | None = None            # ISO-8601 end timestamp (exclusive)
    point_in_time: str | None = None  # Specific ISO-8601 target timestamp
    operator: str = "contains"        # 'contains', 'before', 'after', 'between', 'during', 'point_in_time'
    raw_expression: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_temporally_valid(
    doc_valid_from: str | None,
    doc_valid_until: str | None,
    query_start: str | None = None,
    query_end: str | None = None,
    point_in_time: str | None = None,
) -> bool:
    """Evaluate whether document interval [valid_from, valid_until) is valid for query.

    Missing valid_from represents -infinity.
    Missing valid_until represents +infinity.
    Point-in-time check requires valid_from <= t < valid_until.
    Interval overlap check requires valid_from < query_end and valid_until > query_start.
    """
    if point_in_time:
        p_iso = parse_iso_timestamp(point_in_time) or point_in_time
        if doc_valid_from:
            vf_iso = parse_iso_timestamp(doc_valid_from) or doc_valid_from
            if vf_iso > p_iso:
                return False
        if doc_valid_until:
            vu_iso = parse_iso_timestamp(doc_valid_until) or doc_valid_until
            if vu_iso <= p_iso:
                return False
        return True

    # Interval overlap check:
    if query_end and doc_valid_from:
        qe_iso = parse_iso_timestamp(query_end) or query_end
        vf_iso = parse_iso_timestamp(doc_valid_from) or doc_valid_from
        if vf_iso >= qe_iso:
            return False

    if query_start and doc_valid_until:
        qs_iso = parse_iso_timestamp(query_start) or query_start
        vu_iso = parse_iso_timestamp(doc_valid_until) or doc_valid_until
        if vu_iso <= qs_iso:
            return False

    return True


def extract_temporal_interval(query: str) -> TemporalInterval | None:
    """Extract structured temporal interval or point-in-time constraint from query string."""
    q = query.strip()

    # 1. Point in time with specific hour: e.g. January 14, 2025 at 10:30 UTC
    m_pit = re.search(r"([A-Za-z]+\s+\d{1,2},?\s+\d{4}\s+at\s+\d{1,2}:\d{2}(?:\s*UTC)?)", q, re.IGNORECASE)
    if m_pit:
        iso = parse_iso_timestamp(m_pit.group(1))
        return TemporalInterval(point_in_time=iso, operator="point_in_time", raw_expression=m_pit.group(1))

    # 2. Prior to / before [date]
    m_before = re.search(r"\b(?:prior\s+to|before)\s+(?:the\s+)?([A-Za-z]+\s+\d{1,2},?\s+\d{4}|[A-Za-z]+\s+\d{4}|20\d{2})", q, re.IGNORECASE)
    if m_before:
        iso = parse_iso_timestamp(m_before.group(1))
        return TemporalInterval(end=iso, operator="before", raw_expression=m_before.group(0))

    # 3. After / since [date]
    m_after = re.search(r"\b(?:after|since)\s+(?:the\s+)?([A-Za-z]+\s+\d{1,2},?\s+\d{4}|[A-Za-z]+\s+\d{4}|20\d{2})", q, re.IGNORECASE)
    if m_after:
        iso = parse_iso_timestamp(m_after.group(1))
        return TemporalInterval(start=iso, operator="after", raw_expression=m_after.group(0))

    # 4. Quarter: during Q1 2025
    m_q = re.search(r"\bQ([1-4])\s+(20\d{2})\b", q, re.IGNORECASE)
    if m_q:
        q_num, yr = int(m_q.group(1)), int(m_q.group(2))
        q_starts = {1: (1, 1), 2: (4, 1), 3: (7, 1), 4: (10, 1)}
        q_ends = {1: (4, 1), 2: (7, 1), 3: (10, 1), 4: (1, 1)}
        s_m, s_d = q_starts[q_num]
        e_m, e_d = q_ends[q_num]
        e_yr = yr if q_num < 4 else yr + 1
        return TemporalInterval(
            start=f"{yr:04d}-{s_m:02d}-{s_d:02d}T00:00:00",
            end=f"{e_yr:04d}-{e_m:02d}-{e_d:02d}T00:00:00",
            operator="during",
            raw_expression=m_q.group(0),
        )

    # 5. Late 2024
    m_late = re.search(r"\blate\s+(20\d{2})\b", q, re.IGNORECASE)
    if m_late:
        yr = int(m_late.group(1))
        return TemporalInterval(
            start=f"{yr:04d}-09-01T00:00:00",
            end=f"{yr+1:04d}-01-01T00:00:00",
            operator="during",
            raw_expression=m_late.group(0),
        )

    # 6. FY24 / FY2024
    m_fy = re.search(r"\bFY(20)?(\d{2})\b", q, re.IGNORECASE)
    if m_fy:
        yr = 2000 + int(m_fy.group(2))
        return TemporalInterval(
            start=f"{yr:04d}-01-01T00:00:00",
            end=f"{yr+1:04d}-01-01T00:00:00",
            operator="during",
            raw_expression=m_fy.group(0),
        )

    # 7. Between [date1] and [date2]
    m_between = re.search(r"\bbetween\s+([A-Za-z0-9\s,-]+)\s+and\s+([A-Za-z0-9\s,-]+)\b", q, re.IGNORECASE)
    if m_between:
        iso1 = parse_iso_timestamp(m_between.group(1))
        iso2 = parse_iso_timestamp(m_between.group(2))
        if iso1 or iso2:
            return TemporalInterval(
                start=iso1,
                end=iso2,
                operator="between",
                raw_expression=m_between.group(0),
            )

    return None


@dataclass
class LifecycleConstraint:
    """Extracted lifecycle and version constraint signals."""

    latest: bool = False
    active: bool = False
    published: bool = False
    deprecated: bool = False
    superseded: bool = False
    version: str | None = None


@dataclass
class RelationshipSignal:
    """An extracted relationship phrase connecting enterprise entities."""

    relationship_type: str
    subject_entity: str | None
    object_entity: str | None
    matched_phrase: str


@dataclass
class QueryUnderstanding:
    """Structured deterministic query-understanding output for an evaluation case."""

    evaluation_id: str
    original_query: str
    normalized_query: str
    entities: list[EntityMention] = field(default_factory=list)
    identifiers: list[IdentifierMention] = field(default_factory=list)
    aliases: list[AliasMention] = field(default_factory=list)
    temporal_constraints: list[TemporalConstraint] = field(default_factory=list)
    temporal_interval: TemporalInterval | None = None
    lifecycle_constraints: LifecycleConstraint = field(default_factory=LifecycleConstraint)
    relationship_signals: list[RelationshipSignal] = field(default_factory=list)
    expanded_query: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert QueryUnderstanding object to JSON-compatible dictionary."""
        return {
            "evaluation_id": self.evaluation_id,
            "original_query": self.original_query,
            "normalized_query": self.normalized_query,
            "entities": [asdict(e) for e in self.entities],
            "identifiers": [asdict(i) for i in self.identifiers],
            "aliases": [asdict(a) for a in self.aliases],
            "temporal_constraints": [asdict(t) for t in self.temporal_constraints],
            "temporal_interval": self.temporal_interval.to_dict() if self.temporal_interval else None,
            "lifecycle_constraints": asdict(self.lifecycle_constraints),
            "relationship_signals": [asdict(r) for r in self.relationship_signals],
            "expanded_query": self.expanded_query,
        }


class EntityCatalog:
    """Deterministic catalog index built directly from NovaStack ground-truth entity files."""

    def __init__(self, raw_data_dir: Path) -> None:
        self.raw_dir = raw_data_dir

        # Maps
        self.id_to_entity: dict[str, dict[str, Any]] = {}
        self.name_to_entity: dict[str, dict[str, Any]] = {}
        self.alias_to_entity_id: dict[str, str] = {}

        self._load_catalogs()
        self._build_alias_rules()

    def _load_catalogs(self) -> None:
        # 1. Services
        svc_path = self.raw_dir / "services.json"
        if svc_path.exists():
            with open(svc_path, "r", encoding="utf-8") as f:
                for s in json.load(f).get("services", []):
                    data = {"type": "service", "id": s["service_id"], "name": s["name"], "owner_team_id": s.get("owner_team_id")}
                    self.id_to_entity[s["service_id"]] = data
                    self.name_to_entity[s["name"].lower()] = data

        # 2. Teams
        team_path = self.raw_dir / "teams.json"
        if team_path.exists():
            with open(team_path, "r", encoding="utf-8") as f:
                for t in json.load(f).get("teams", []):
                    data = {"type": "team", "id": t["team_id"], "name": t["name"], "department": t.get("department")}
                    self.id_to_entity[t["team_id"]] = data
                    self.name_to_entity[t["name"].lower()] = data

        # 3. Incidents
        inc_path = self.raw_dir / "incidents.json"
        if inc_path.exists():
            with open(inc_path, "r", encoding="utf-8") as f:
                for i in json.load(f).get("incidents", []):
                    data = {"type": "incident", "id": i["incident_id"], "title": i.get("title", ""), "service_id": i.get("service_id")}
                    self.id_to_entity[i["incident_id"]] = data

        # 4. Deployments
        dep_path = self.raw_dir / "deployments.json"
        if dep_path.exists():
            with open(dep_path, "r", encoding="utf-8") as f:
                for d in json.load(f).get("deployments", []):
                    data = {"type": "deployment", "id": d["deployment_id"], "service_id": d.get("service_id"), "version": d.get("version")}
                    self.id_to_entity[d["deployment_id"]] = data

        # 5. Pull Requests
        pr_path = self.raw_dir / "pull_requests.json"
        if pr_path.exists():
            with open(pr_path, "r", encoding="utf-8") as f:
                for p in json.load(f).get("pull_requests", []):
                    data = {"type": "pull_request", "id": p["pull_request_id"], "service_id": p.get("service_id"), "title": p.get("title", "")}
                    self.id_to_entity[p["pull_request_id"]] = data

        # 6. Events
        evt_path = self.raw_dir / "events.json"
        if evt_path.exists():
            with open(evt_path, "r", encoding="utf-8") as f:
                for e in json.load(f).get("events", []):
                    data = {"type": "event", "id": e["event_id"], "title": e.get("title", "")}
                    self.id_to_entity[e["event_id"]] = data

        # 7. Customers
        cust_path = self.raw_dir / "customers.json"
        if cust_path.exists():
            with open(cust_path, "r", encoding="utf-8") as f:
                for c in json.load(f).get("customers", []):
                    data = {"type": "customer", "id": c["customer_id"], "name": c["name"]}
                    self.id_to_entity[c["customer_id"]] = data
                    self.name_to_entity[c["name"].lower()] = data

    def _build_alias_rules(self) -> None:
        """Deterministic, testable alias rules derived from service names and common terms."""
        # For services ending in '-service', strip suffix to create service alias
        # e.g. 'checkout-service' -> alias 'checkout'
        for name, ent in list(self.name_to_entity.items()):
            if ent["type"] == "service" and name.endswith("-service"):
                base_alias = name[:-8]  # strip '-service'
                if len(base_alias) >= 3:
                    self.alias_to_entity_id[base_alias] = ent["id"]

        # Common unambiguous enterprise alias mappings
        explicit_aliases = {
            "checkout": "SVC-NS-0005",
            "payments": "SVC-NS-0007",
            "payment": "SVC-NS-0007",
            "auth": "SVC-NS-0006",
            "authentication": "SVC-NS-0006",
            "media": "SVC-NS-0009",
            "cart": "SVC-NS-0008",
            "notification": "SVC-NS-0010",
            "notifications": "SVC-NS-0010",
            "feature-flags": "SVC-NS-0001",
            "flags": "SVC-NS-0001",
            "analytics": "SVC-NS-0002",
            "data warehouse": "SVC-NS-0003",
            "warehouse": "SVC-NS-0003",
            "email": "SVC-NS-0004",
            "inventory": "SVC-NS-0011",
            "search": "SVC-NS-0012",
            "recommendation": "SVC-NS-0013",
            "billing": "SVC-NS-0014",
            "shipping": "SVC-NS-0015",
            "sre": "TEAM-NS-0005",
            "devops": "TEAM-NS-0004",
            "security": "TEAM-NS-0007",
        }
        for alias, s_id in explicit_aliases.items():
            if s_id in self.id_to_entity:
                self.alias_to_entity_id[alias] = s_id


class QueryUnderstandingExtractor:
    """Deterministic extractor for query understanding signals."""

    def __init__(self, catalog: EntityCatalog) -> None:
        self.catalog = catalog

    def extract(self, evaluation_id: str, query: str) -> QueryUnderstanding:
        """Extract structured entities, identifiers, aliases, and constraints from query."""
        normalized_q = query.strip()

        entities: list[EntityMention] = []
        identifiers: list[IdentifierMention] = []
        aliases: list[AliasMention] = []
        temporal: list[TemporalConstraint] = []
        relationship_signals: list[RelationshipSignal] = []

        seen_entities: set[str] = set()

        # 1. Exact Identifiers
        for m in _ID_PATTERN.finditer(query):
            matched_id = m.group(0)
            prefix = matched_id.split("-")[0]
            identifiers.append(
                IdentifierMention(
                    identifier_type=prefix,
                    identifier=matched_id,
                    matched_text=matched_id,
                )
            )
            # If in catalog, also record as entity
            if matched_id in self.catalog.id_to_entity:
                ent_info = self.catalog.id_to_entity[matched_id]
                if matched_id not in seen_entities:
                    entities.append(
                        EntityMention(
                            entity_type=ent_info["type"],
                            entity_id=matched_id,
                            matched_text=matched_id,
                            match_method="exact_id",
                        )
                    )
                    seen_entities.add(matched_id)

        # 2. Exact Names & Aliases
        q_lower = query.lower()

        # Check full catalog names (longer names first to avoid partial matches)
        sorted_names = sorted(self.catalog.name_to_entity.keys(), key=len, reverse=True)
        for name in sorted_names:
            pattern = rf"\b{re.escape(name)}\b"
            m = re.search(pattern, q_lower)
            if m:
                ent_info = self.catalog.name_to_entity[name]
                e_id = ent_info["id"]
                if e_id not in seen_entities:
                    entities.append(
                        EntityMention(
                            entity_type=ent_info["type"],
                            entity_id=e_id,
                            matched_text=m.group(0),
                            match_method="exact_name",
                        )
                    )
                    seen_entities.add(e_id)

        # Check aliases
        sorted_aliases = sorted(self.catalog.alias_to_entity_id.keys(), key=len, reverse=True)
        for alias in sorted_aliases:
            pattern = rf"\b{re.escape(alias)}\b"
            m = re.search(pattern, q_lower)
            if m:
                e_id = self.catalog.alias_to_entity_id[alias]
                aliases.append(
                    AliasMention(
                        canonical_entity_id=e_id,
                        alias=alias,
                        matched_text=m.group(0),
                    )
                )
                if e_id not in seen_entities and e_id in self.catalog.id_to_entity:
                    ent_info = self.catalog.id_to_entity[e_id]
                    entities.append(
                        EntityMention(
                            entity_type=ent_info["type"],
                            entity_id=e_id,
                            matched_text=m.group(0),
                            match_method="alias",
                        )
                    )
                    seen_entities.add(e_id)

        # 3. Temporal Constraints
        # Explicit date match: YYYY-MM-DD or YYYY-MM or YYYY
        date_matches = re.finditer(r"\b(20\d{2})(?:-(\d{2}))?(?:-(\d{2}))?\b", query)
        for dm in date_matches:
            temporal.append(
                TemporalConstraint(
                    type="explicit_date",
                    value=dm.group(0),
                    start=dm.group(0),
                    end=dm.group(0),
                )
            )

        for marker in TEMPORAL_MARKERS:
            pattern = rf"\b{re.escape(marker)}\b"
            m = re.search(pattern, q_lower)
            if m:
                temporal.append(
                    TemporalConstraint(
                        type="relative_marker",
                        value=marker,
                    )
                )

        # 4. Lifecycle Constraints
        lifecycle = LifecycleConstraint()
        if re.search(r"\blatest\b", q_lower):
            lifecycle.latest = True
        if re.search(r"\bcurrent\b|\bactive\b", q_lower):
            lifecycle.active = True
        if re.search(r"\bpublished\b", q_lower):
            lifecycle.published = True
        if re.search(r"\bdeprecated\b", q_lower):
            lifecycle.deprecated = True
        if re.search(r"\bsuperseded\b", q_lower):
            lifecycle.superseded = True

        ver_match = re.search(r"\bv\d+(\.\d+)*\b|\bversion\s+(\d+(\.\d+)*)\b", q_lower)
        if ver_match:
            lifecycle.version = ver_match.group(0)

        # 5. Relationship Signals
        for verb_phrase, rel_type in RELATIONSHIP_VERB_MAP.items():
            pattern = rf"\b{re.escape(verb_phrase)}\b"
            m = re.search(pattern, q_lower)
            if m:
                # Associate subject and object if available from recognized entities
                subj = entities[0].entity_id if len(entities) > 0 else None
                obj = entities[1].entity_id if len(entities) > 1 else None
                relationship_signals.append(
                    RelationshipSignal(
                        relationship_type=rel_type,
                        subject_entity=subj,
                        object_entity=obj,
                        matched_phrase=m.group(0),
                    )
                )

        # 6. Construct Expanded Lexical Query
        # Augment original query with recognized canonical entity IDs and names
        exp_tokens = []
        for ent in entities:
            # Add entity ID
            if ent.entity_id not in exp_tokens:
                exp_tokens.append(ent.entity_id)
            # Add canonical name if not already in query
            if ent.entity_id in self.catalog.id_to_entity:
                canon_name = self.catalog.id_to_entity[ent.entity_id].get("name", "")
                if canon_name and canon_name.lower() not in q_lower and canon_name not in exp_tokens:
                    exp_tokens.append(canon_name)
                # If service, also add owner team ID
                owner_team = self.catalog.id_to_entity[ent.entity_id].get("owner_team_id")
                if owner_team and owner_team not in exp_tokens:
                    exp_tokens.append(owner_team)

        for id_men in identifiers:
            if id_men.identifier not in exp_tokens:
                exp_tokens.append(id_men.identifier)

        if exp_tokens:
            expanded_q = f"{normalized_q} {' '.join(exp_tokens)}"
        else:
            expanded_q = normalized_q

        temporal_interval = extract_temporal_interval(query)

        return QueryUnderstanding(
            evaluation_id=evaluation_id,
            original_query=query,
            normalized_query=normalized_q,
            entities=entities,
            identifiers=identifiers,
            aliases=aliases,
            temporal_constraints=temporal,
            temporal_interval=temporal_interval,
            lifecycle_constraints=lifecycle,
            relationship_signals=relationship_signals,
            expanded_query=expanded_q,
        )
