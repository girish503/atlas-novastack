"""Canonical ingestion and normalization layer — Phase 2A.

Transforms raw enterprise SourceRecord objects into stable, canonical SearchDocument
representations consumable by downstream retrieval systems (lexical BM25, dense
embeddings, hybrid search, authorization middleware, and rerankers).

Establishes a clean architectural boundary between:
- RAW ENTERPRISE EVIDENCE (immutable observational source records)
- SEARCH-READY DOCUMENT REPRESENTATION (normalized, validated search documents)
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from novastack.models import (
    AUTHORITY_LEVELS,
    CLASSIFICATION_LEVELS,
    RECORD_STATUSES,
    SOURCE_TYPES,
    RecordPermissions,
    SearchDocument,
    SourceRecord,
)
from novastack.observability import get_metrics
from novastack.quarantine import DeadLetterQueue, QuarantinedRecord

VALID_TENANTS: set[str] = {
    "TENANT-NOVASTACK",
    "TENANT-ORBITAL",
    "TENANT-PINECONE",
}


@dataclass
class IngestionBatchResult:
    """Outcome of an isolated, fault-tolerant ingestion run."""

    accepted_documents: list[SearchDocument] = field(default_factory=list)
    quarantined_records: list[QuarantinedRecord] = field(default_factory=list)
    report: IngestionReport = field(default_factory=lambda: IngestionReport())


@dataclass
class IngestionReport:
    """Observability report capturing metrics from an ingestion and normalization run."""

    input_count: int = 0
    output_count: int = 0
    successful_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_type_distribution: dict[str, int] = field(default_factory=dict)
    classification_distribution: dict[str, int] = field(default_factory=dict)
    tenant_distribution: dict[str, int] = field(default_factory=dict)
    records_with_temporal_metadata: int = 0
    records_with_permissions: int = 0
    records_with_provenance: int = 0

    def format_report(self) -> str:
        """Format the report into a structured console summary."""
        lines = [
            "============================================================",
            "ATLAS Ingestion & Normalization Report (Phase 2A)",
            "============================================================",
            f"  Input Records:                  {self.input_count:>5}",
            f"  Output Search Documents:        {self.output_count:>5}",
            f"  Successful Records:             {self.successful_count:>5}",
            f"  Validation Errors:              {len(self.errors):>5}",
            f"  Validation Warnings:            {len(self.warnings):>5}",
            "",
            "  Source-Type Distribution:",
        ]
        for st, cnt in sorted(self.source_type_distribution.items()):
            lines.append(f"    - {st:<25} {cnt:>5}")

        lines.append("\n  Classification Distribution:")
        for cls, cnt in sorted(self.classification_distribution.items()):
            lines.append(f"    - {cls:<25} {cnt:>5}")

        lines.append("\n  Tenant Distribution:")
        for tid, cnt in sorted(self.tenant_distribution.items()):
            lines.append(f"    - {tid:<25} {cnt:>5}")

        lines.extend([
            "",
            "  Metadata Preservation:",
            f"    - Records with Temporal Bounds: {self.records_with_temporal_metadata:>5}",
            f"    - Records with Permissions:     {self.records_with_permissions:>5}",
            f"    - Records with Provenance:      {self.records_with_provenance:>5}",
            "============================================================",
        ])

        if self.errors:
            lines.append("\n  Errors Encountered:")
            for err in self.errors[:10]:
                lines.append(f"    [ERROR] {err}")
            if len(self.errors) > 10:
                lines.append(f"    ... and {len(self.errors) - 10} more errors")

        return "\n".join(lines)


def _format_timestamp(ts: datetime | str | None) -> str | None:
    """Standardize timestamp to canonical ISO 8601 string (YYYY-MM-DDTHH:MM:SS) or None."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.isoformat()
    if isinstance(ts, str):
        s = ts.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s)
            return dt.isoformat()
        except ValueError:
            return s
    return str(ts)


def normalize_content(text: str) -> str:
    """Normalize textual content deterministically without altering semantic meaning.

    1. Unicode NFC normalization.
    2. Standardize line endings (\r\n and \r -> \n).
    3. Trim trailing whitespace from each line.
    4. Strip outer leading and trailing blank padding.
    """
    if not text:
        return ""

    # 1. Unicode NFC normalization
    normalized = unicodedata.normalize("NFC", text)

    # 2. Line ending normalization
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")

    # 3. Strip trailing whitespace per line
    lines = [line.rstrip() for line in normalized.split("\n")]

    # 4. Strip outer blank lines/whitespace
    result = "\n".join(lines).strip()
    return result


def normalize_title(title: str) -> str:
    """Normalize title deterministically."""
    if not title:
        return ""
    normalized = unicodedata.normalize("NFC", title)
    normalized = normalized.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return normalized.strip()


def normalize_source_record(record: SourceRecord) -> SearchDocument:
    """Deterministically normalize a SourceRecord into a canonical SearchDocument.

    Preserves:
    - Identity: document_id remains 100% stable
    - Tenancy: tenant_id preserved without modification
    - Security: classification and RecordPermissions preserved with sorted lists
    - Temporal: created_at, updated_at, valid_from, valid_until in standardized ISO 8601
    - Provenance: source_entity_id, source_entity_type, and sorted related_entity_ids
    - Content: Unicode NFC, normalized newlines, trimmed line whitespace
    - Hierarchy: parent_id, supersedes_id, and version preserved
    """
    # 1. Normalized text
    norm_title = normalize_title(record.title)
    norm_content = normalize_content(record.content)

    # 2. Normalized timestamps
    created_at_str = _format_timestamp(record.created_at) or datetime.utcnow().isoformat()
    updated_at_str = _format_timestamp(record.updated_at)
    valid_from_str = _format_timestamp(record.valid_from)
    valid_until_str = _format_timestamp(record.valid_until)

    # 3. Normalized null/empty string handling for optional references
    parent_id = str(record.parent_id).strip() if record.parent_id and str(record.parent_id).strip() else None
    supersedes_id = (
        str(record.supersedes_id).strip() if record.supersedes_id and str(record.supersedes_id).strip() else None
    )
    source_entity_id = (
        str(record.source_entity_id).strip() if record.source_entity_id and str(record.source_entity_id).strip() else None
    )
    source_entity_type = (
        str(record.source_entity_type).strip()
        if record.source_entity_type and str(record.source_entity_type).strip()
        else None
    )

    # 4. Deterministic sorting and deduplication of list metadata
    norm_related_entities = sorted(
        {str(ent).strip() for ent in record.related_entity_ids if str(ent).strip()}
    )

    # Permissions normalization: deduplicate and sort all access lists
    perms = record.permissions or RecordPermissions()
    norm_permissions = RecordPermissions(
        allowed_roles=sorted({str(r).strip() for r in perms.allowed_roles if str(r).strip()}),
        allowed_departments=sorted({str(d).strip() for d in perms.allowed_departments if str(d).strip()}),
        allowed_teams=sorted({str(t).strip() for t in perms.allowed_teams if str(t).strip()}),
        allowed_user_ids=sorted({str(u).strip() for u in perms.allowed_user_ids if str(u).strip()}),
    )

    return SearchDocument(
        document_id=str(record.document_id).strip(),
        tenant_id=str(record.tenant_id).strip(),
        source_type=str(record.source_type).strip(),
        title=norm_title,
        content=norm_content,
        department=str(record.department).strip(),
        author_id=str(record.author_id).strip(),
        created_at=created_at_str,
        updated_at=updated_at_str,
        valid_from=valid_from_str,
        valid_until=valid_until_str,
        version=str(record.version).strip() if record.version else "1.0",
        status=str(record.status).strip() if record.status else "published",
        classification=str(record.classification).strip() if record.classification else "internal",
        permissions=norm_permissions,
        parent_id=parent_id,
        supersedes_id=supersedes_id,
        source_entity_id=source_entity_id,
        source_entity_type=source_entity_type,
        related_entity_ids=norm_related_entities,
        authority_level=str(record.authority_level).strip() if record.authority_level else "medium",
    )


def validate_single_source_record(
    r: SourceRecord,
    valid_tenants: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Validate a single SourceRecord for intra-record schema and integrity rules.

    Checks:
    - Missing or empty document_id
    - Invalid tenant_id
    - Invalid source_type
    - Invalid classification
    - Invalid status
    - Invalid authority_level
    - Empty title or content
    - Invalid permissions structure
    - Self-referencing parent or supersedes
    - Temporal relationship validity (valid_from <= valid_until)
    - Provenance structure check

    Returns:
        tuple[list[str], list[str]]: (errors, warnings)
    """
    errors: list[str] = []
    warnings: list[str] = []
    tenants = valid_tenants or VALID_TENANTS

    if not r.document_id or not str(r.document_id).strip():
        errors.append("missing or empty document_id")
        return errors, warnings

    doc_id = str(r.document_id).strip()

    # 3. Invalid tenant_id
    if not r.tenant_id or r.tenant_id not in tenants:
        errors.append(f"{doc_id}: invalid tenant_id '{r.tenant_id}' (expected one of {sorted(tenants)})")

    # 4. Invalid source_type
    if not r.source_type or r.source_type not in SOURCE_TYPES:
        errors.append(f"{doc_id}: invalid source_type '{r.source_type}' (expected one of {sorted(SOURCE_TYPES)})")

    # 5. Invalid classification
    if not r.classification or r.classification not in CLASSIFICATION_LEVELS:
        errors.append(
            f"{doc_id}: invalid classification '{r.classification}' (expected one of {sorted(CLASSIFICATION_LEVELS)})"
        )

    # 6. Invalid status
    if not r.status or r.status not in RECORD_STATUSES:
        errors.append(f"{doc_id}: invalid status '{r.status}' (expected one of {sorted(RECORD_STATUSES)})")

    # 7. Invalid authority_level
    if not r.authority_level or r.authority_level not in AUTHORITY_LEVELS:
        errors.append(
            f"{doc_id}: invalid authority_level '{r.authority_level}' (expected one of {sorted(AUTHORITY_LEVELS)})"
        )

    # 8. Empty title or content
    if not r.title or not str(r.title).strip():
        errors.append(f"{doc_id}: title is empty or missing")
    if not r.content or not str(r.content).strip():
        errors.append(f"{doc_id}: content is empty or missing")

    # 9. Permissions structure
    if r.permissions is not None and not isinstance(r.permissions, RecordPermissions):
        errors.append(f"{doc_id}: invalid permissions type '{type(r.permissions)}'")

    # 10. Self-referencing parent or supersedes
    if r.parent_id and str(r.parent_id).strip() == doc_id:
        errors.append(f"{doc_id}: self-referencing parent_id '{r.parent_id}'")
    if r.supersedes_id and str(r.supersedes_id).strip() == doc_id:
        errors.append(f"{doc_id}: self-referencing supersedes_id '{r.supersedes_id}'")

    # 13. Temporal relationship validity (valid_from <= valid_until)
    if r.valid_from and r.valid_until:
        vf = r.valid_from if isinstance(r.valid_from, datetime) else None
        vu = r.valid_until if isinstance(r.valid_until, datetime) else None
        if vf is None:
            try:
                vf = datetime.fromisoformat(str(r.valid_from).strip())
            except ValueError:
                pass
        if vu is None:
            try:
                vu = datetime.fromisoformat(str(r.valid_until).strip())
            except ValueError:
                pass

        if vf and vu and vf > vu:
            errors.append(f"{doc_id}: invalid temporal bounds: valid_from ({vf}) > valid_until ({vu})")

    # 14. Provenance structure check
    if (r.source_entity_id and not r.source_entity_type) or (not r.source_entity_id and r.source_entity_type):
        warnings.append(
            f"{doc_id}: asymmetric provenance: source_entity_id='{r.source_entity_id}', "
            f"source_entity_type='{r.source_entity_type}'"
        )

    return errors, warnings


def validate_source_record_batch(
    records: list[SourceRecord],
    valid_tenants: set[str] | None = None,
    enforce_tenant_hierarchy: bool = True,
    existing_tenants_by_doc_id: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """Validate a batch of SourceRecord objects before ingestion.

    Checks:
    - Missing or empty document_id
    - Duplicate document_id within batch
    - Cross-tenant document ID collision against existing corpus
    - Invalid tenant_id
    - Invalid source_type
    - Invalid classification
    - Invalid status
    - Invalid authority_level
    - Invalid permissions structure
    - Self-referencing parent or supersedes
    - Dangling parent_id (must reference existing document_id in batch or existing corpus if set)
    - Dangling supersedes_id (must reference existing document_id in batch or existing corpus if set)
    - Cross-tenant parent or supersedes references
    - Invalid temporal relationships (valid_from > valid_until)
    - Empty title or content

    Returns:
        tuple[list[str], list[str]]: (errors, warnings)
    """
    errors: list[str] = []
    warnings: list[str] = []
    tenants = valid_tenants or VALID_TENANTS

    seen_ids: set[str] = set()
    all_batch_ids = {r.document_id for r in records if r.document_id and str(r.document_id).strip()}
    batch_tenants_by_id = {
        str(r.document_id).strip(): str(r.tenant_id).strip()
        for r in records
        if r.document_id and str(r.document_id).strip()
    }

    for idx, r in enumerate(records):
        doc_ref = r.document_id or f"index_{idx}"

        # 1. Missing or empty document_id
        if not r.document_id or not str(r.document_id).strip():
            errors.append(f"Record #{idx}: missing or empty document_id")
            continue

        doc_id = str(r.document_id).strip()

        # 2. Duplicate document_id within batch
        if doc_id in seen_ids:
            errors.append(f"{doc_id}: duplicate document_id detected in batch")
        seen_ids.add(doc_id)

        # 2b. Cross-tenant ID collision check against existing corpus
        if existing_tenants_by_doc_id and doc_id in existing_tenants_by_doc_id:
            existing_tenant = existing_tenants_by_doc_id[doc_id]
            if existing_tenant != r.tenant_id:
                errors.append(
                    f"{doc_id}: cross-tenant ID collision: document_id belongs to '{existing_tenant}', "
                    f"cannot be overwritten by '{r.tenant_id}'"
                )

        # Intra-record schema validation
        rec_errors, rec_warnings = validate_single_source_record(r, valid_tenants=tenants)
        errors.extend(rec_errors)
        warnings.extend(rec_warnings)

        # 11. Dangling parent reference
        if r.parent_id and str(r.parent_id).strip():
            pid = str(r.parent_id).strip()
            parent_exists = pid in all_batch_ids or (existing_tenants_by_doc_id is not None and pid in existing_tenants_by_doc_id)
            if not parent_exists:
                errors.append(f"{doc_id}: parent_id '{r.parent_id}' not found in batch")
            elif enforce_tenant_hierarchy:
                parent_tenant = batch_tenants_by_id.get(pid)
                if parent_tenant is None and existing_tenants_by_doc_id:
                    parent_tenant = existing_tenants_by_doc_id.get(pid)
                if parent_tenant is not None and parent_tenant != r.tenant_id:
                    errors.append(
                        f"{doc_id}: cross-tenant parent reference: record tenant '{r.tenant_id}' != "
                        f"parent tenant '{parent_tenant}'"
                    )

        # 12. Dangling supersedes reference
        if r.supersedes_id and str(r.supersedes_id).strip():
            sid = str(r.supersedes_id).strip()
            supersedes_exists = sid in all_batch_ids or (existing_tenants_by_doc_id is not None and sid in existing_tenants_by_doc_id)
            if not supersedes_exists:
                errors.append(f"{doc_id}: supersedes_id '{r.supersedes_id}' not found in batch")
            elif enforce_tenant_hierarchy:
                superseded_tenant = batch_tenants_by_id.get(sid)
                if superseded_tenant is None and existing_tenants_by_doc_id:
                    superseded_tenant = existing_tenants_by_doc_id.get(sid)
                if superseded_tenant is not None and superseded_tenant != r.tenant_id:
                    errors.append(
                        f"{doc_id}: cross-tenant supersedes reference: record tenant '{r.tenant_id}' != "
                        f"superseded tenant '{superseded_tenant}'"
                    )

    return errors, warnings


def _parse_source_record_dict(d: dict[str, Any]) -> SourceRecord:
    """Parse a single source record dictionary into a SourceRecord instance."""
    perms_data = d.get("permissions")
    if isinstance(perms_data, dict):
        perms = RecordPermissions(
            allowed_roles=list(perms_data.get("allowed_roles", [])),
            allowed_departments=list(perms_data.get("allowed_departments", [])),
            allowed_teams=list(perms_data.get("allowed_teams", [])),
            allowed_user_ids=list(perms_data.get("allowed_user_ids", [])),
        )
    elif isinstance(perms_data, RecordPermissions):
        perms = perms_data
    else:
        perms = RecordPermissions()

    created_at = d.get("created_at")
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at)
        except ValueError:
            pass

    updated_at = d.get("updated_at")
    if isinstance(updated_at, str) and updated_at.strip():
        try:
            updated_at = datetime.fromisoformat(updated_at)
        except ValueError:
            pass
    elif not updated_at:
        updated_at = None

    valid_from = d.get("valid_from")
    if isinstance(valid_from, str) and valid_from.strip():
        try:
            valid_from = datetime.fromisoformat(valid_from)
        except ValueError:
            pass
    elif not valid_from:
        valid_from = None

    valid_until = d.get("valid_until")
    if isinstance(valid_until, str) and valid_until.strip():
        try:
            valid_until = datetime.fromisoformat(valid_until)
        except ValueError:
            pass
    elif not valid_until:
        valid_until = None

    return SourceRecord(
        document_id=d["document_id"],
        tenant_id=d["tenant_id"],
        source_type=d["source_type"],
        title=d["title"],
        content=d["content"],
        author_id=d.get("author_id", ""),
        department=d.get("department", ""),
        created_at=created_at or datetime.utcnow(),
        updated_at=updated_at,
        valid_from=valid_from,
        valid_until=valid_until,
        version=d.get("version", "1.0"),
        status=d.get("status", "published"),
        classification=d.get("classification", "internal"),
        permissions=perms,
        parent_id=d.get("parent_id"),
        supersedes_id=d.get("supersedes_id"),
        source_entity_id=d.get("source_entity_id"),
        source_entity_type=d.get("source_entity_type"),
        related_entity_ids=list(d.get("related_entity_ids", [])),
        authority_level=d.get("authority_level", "medium"),
    )


def load_source_records(source: str | Path | dict | list[dict] | list[SourceRecord]) -> list[SourceRecord]:
    """Load raw source records from a JSON file, dictionary, or list.

    Supports:
    - Path or str: reads JSON file (supporting top-level dict with "source_records" key or list)
    - dict: dictionary with "source_records" key
    - list[dict]: list of record dictionaries
    - list[SourceRecord]: already instantiated list
    """
    if isinstance(source, (str, Path)):
        p = Path(source)
        if not p.exists():
            raise FileNotFoundError(f"Source records file not found: {p}")
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return load_source_records(data)

    if isinstance(source, dict):
        records_data = source.get("source_records", [])
        return [_parse_source_record_dict(r) for r in records_data]

    if isinstance(source, list):
        if not source:
            return []
        if isinstance(source[0], SourceRecord):
            return source  # type: ignore[return-value]
        if isinstance(source[0], dict):
            return [_parse_source_record_dict(r) for r in source]  # type: ignore[arg-type]

    raise TypeError(f"Unsupported source type for load_source_records: {type(source)}")


def ingest_records(
    source: list[SourceRecord] | list[dict] | Path | str,
    strict: bool = True,
    valid_tenants: set[str] | None = None,
) -> tuple[list[SearchDocument], IngestionReport]:
    """Execute the canonical ingestion and normalization pipeline.

    Workflow:
    1. Load raw records
    2. Validate batch referential integrity and schema rules
    3. Normalize records into canonical SearchDocument objects
    4. Compile comprehensive IngestionReport

    Args:
        source: Raw records source (path, dict, list of dicts, or list of SourceRecord).
        strict: If True and validation errors are found, raise ValueError.
        valid_tenants: Optional custom set of valid tenant IDs.

    Returns:
        tuple[list[SearchDocument], IngestionReport]: Normalized search documents and report.
    """
    records = load_source_records(source)
    report = IngestionReport(input_count=len(records))

    # 1. Validation
    errors, warnings = validate_source_record_batch(records, valid_tenants=valid_tenants)
    report.errors = errors
    report.warnings = warnings

    if errors and strict:
        raise ValueError(
            f"Ingestion batch validation failed with {len(errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in errors[:5])
        )

    # 2. Normalization
    search_documents: list[SearchDocument] = []
    source_type_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    tenant_counts: Counter[str] = Counter()

    temporal_count = 0
    permissions_count = 0
    provenance_count = 0

    for r in records:
        doc = normalize_source_record(r)
        search_documents.append(doc)

        source_type_counts[doc.source_type] += 1
        class_counts[doc.classification] += 1
        tenant_counts[doc.tenant_id] += 1

        if doc.valid_from or doc.valid_until:
            temporal_count += 1

        perms = doc.permissions
        if perms and (perms.allowed_roles or perms.allowed_departments or perms.allowed_teams or perms.allowed_user_ids):
            permissions_count += 1

        if doc.source_entity_id or doc.related_entity_ids:
            provenance_count += 1

    report.output_count = len(search_documents)
    report.successful_count = len(search_documents)
    report.source_type_distribution = dict(source_type_counts)
    report.classification_distribution = dict(class_counts)
    report.tenant_distribution = dict(tenant_counts)
    report.records_with_temporal_metadata = temporal_count
    report.records_with_permissions = permissions_count
    report.records_with_provenance = provenance_count

    return search_documents, report


def ingest_records_isolated(
    source: list[SourceRecord] | list[dict] | Path | str,
    valid_tenants: set[str] | None = None,
    existing_documents: dict[str, SearchDocument | str] | None = None,
    dlq: DeadLetterQueue | None = None,
) -> IngestionBatchResult:
    """Execute fault-tolerant ingestion with per-record failure isolation.

    Guarantees:
    - One bad document never destroys the valid corpus (no batch-wide abortion).
    - Malformed, invalid, or cross-tenant violating records are routed to DLQ.
    - Valid records are normalized and returned in accepted_documents.
    - Cross-tenant ID collisions and referential links are strictly quarantined.
    - Observability metrics are emitted for accepted, quarantined, and failure counts.

    Args:
        source: Raw records source (path, dict, list of dicts, or list of SourceRecord).
        valid_tenants: Optional custom set of valid tenant IDs.
        existing_documents: Optional mapping of doc_id -> SearchDocument or doc_id -> tenant_id
                            representing the active knowledge corpus for cross-tenant collision detection.
        dlq: Optional DeadLetterQueue instance (defaults to standard DLQ store).

    Returns:
        IngestionBatchResult: Struct containing accepted_documents, quarantined_records, and report.
    """
    records = load_source_records(source)
    if dlq is None:
        dlq = DeadLetterQueue()

    tenants = valid_tenants or VALID_TENANTS
    existing_tenants: dict[str, str] = {}
    if existing_documents:
        for k, v in existing_documents.items():
            existing_tenants[str(k).strip()] = (
                v.tenant_id if isinstance(v, SearchDocument) else str(v).strip()
            )

    all_batch_ids = {
        r.document_id for r in records if r.document_id and str(r.document_id).strip()
    }
    batch_tenants_by_id = {
        str(r.document_id).strip(): str(r.tenant_id).strip()
        for r in records
        if r.document_id and str(r.document_id).strip()
    }

    seen_ids: set[str] = set()
    accepted_documents: list[SearchDocument] = []
    quarantined_records: list[QuarantinedRecord] = []

    source_type_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    tenant_counts: Counter[str] = Counter()
    temporal_count = 0
    permissions_count = 0
    provenance_count = 0

    for idx, r in enumerate(records):
        # 1. Missing or empty document_id
        if not r.document_id or not str(r.document_id).strip():
            q = QuarantinedRecord(
                document_id=f"missing_id_idx_{idx}",
                tenant_id=str(r.tenant_id) if r.tenant_id else "unknown",
                source_type=str(r.source_type) if r.source_type else "unknown",
                ingestion_stage="validation",
                error_type="MissingDocumentId",
                error_message=f"Record #{idx}: missing or empty document_id",
                retryable=False,
                payload_preview={"title": r.title, "tenant_id": r.tenant_id},
            )
            dlq.quarantine(q)
            quarantined_records.append(q)
            continue

        doc_id = str(r.document_id).strip()

        # 2. Duplicate document_id within batch
        if doc_id in seen_ids:
            q = QuarantinedRecord(
                document_id=doc_id,
                tenant_id=str(r.tenant_id) if r.tenant_id else "unknown",
                source_type=str(r.source_type) if r.source_type else "unknown",
                ingestion_stage="validation",
                error_type="DuplicateDocumentId",
                error_message=f"{doc_id}: duplicate document_id detected in batch",
                retryable=False,
                payload_preview={"title": r.title, "tenant_id": r.tenant_id},
            )
            dlq.quarantine(q)
            quarantined_records.append(q)
            continue
        seen_ids.add(doc_id)

        # 2b. Cross-tenant ID collision against existing active corpus
        if doc_id in existing_tenants and existing_tenants[doc_id] != r.tenant_id:
            q = QuarantinedRecord(
                document_id=doc_id,
                tenant_id=str(r.tenant_id) if r.tenant_id else "unknown",
                source_type=str(r.source_type) if r.source_type else "unknown",
                ingestion_stage="validation",
                error_type="CrossTenantCollision",
                error_message=(
                    f"{doc_id}: cross-tenant ID collision: document belongs to tenant "
                    f"'{existing_tenants[doc_id]}', cannot be overwritten by '{r.tenant_id}'"
                ),
                retryable=False,
                payload_preview={"title": r.title, "tenant_id": r.tenant_id},
            )
            dlq.quarantine(q)
            quarantined_records.append(q)
            continue

        # 3. Intra-record schema validation
        rec_errors, rec_warnings = validate_single_source_record(r, valid_tenants=tenants)

        # 4. Referential & hierarchy checks
        is_retryable = False
        if r.parent_id and str(r.parent_id).strip():
            pid = str(r.parent_id).strip()
            parent_exists = pid in all_batch_ids or pid in existing_tenants
            if not parent_exists:
                rec_errors.append(f"{doc_id}: parent_id '{pid}' not found in batch or active corpus")
                is_retryable = True  # Parent may arrive in subsequent batch
            else:
                parent_tenant = batch_tenants_by_id.get(pid) or existing_tenants.get(pid)
                if parent_tenant and parent_tenant != r.tenant_id:
                    rec_errors.append(
                        f"{doc_id}: cross-tenant parent reference: record tenant '{r.tenant_id}' != "
                        f"parent tenant '{parent_tenant}'"
                    )

        if r.supersedes_id and str(r.supersedes_id).strip():
            sid = str(r.supersedes_id).strip()
            supersedes_exists = sid in all_batch_ids or sid in existing_tenants
            if not supersedes_exists:
                rec_errors.append(f"{doc_id}: supersedes_id '{sid}' not found in batch or active corpus")
                is_retryable = True  # Superseded record may arrive in subsequent batch
            else:
                superseded_tenant = batch_tenants_by_id.get(sid) or existing_tenants.get(sid)
                if superseded_tenant and superseded_tenant != r.tenant_id:
                    rec_errors.append(
                        f"{doc_id}: cross-tenant supersedes reference: record tenant '{r.tenant_id}' != "
                        f"superseded tenant '{superseded_tenant}'"
                    )

        # If any validation errors occurred, isolate and quarantine
        if rec_errors:
            if any("cross-tenant" in e.lower() for e in rec_errors):
                err_type = "CrossTenantViolation"
                is_retryable = False
            elif any("temporal" in e.lower() for e in rec_errors):
                err_type = "TemporalBoundsViolation"
                is_retryable = False
            elif any("invalid tenant" in e.lower() for e in rec_errors):
                err_type = "InvalidTenantError"
                is_retryable = False
            elif any("invalid permissions" in e.lower() for e in rec_errors):
                err_type = "PermissionStructureError"
                is_retryable = False
            else:
                err_type = "ValidationError"

            q = QuarantinedRecord(
                document_id=doc_id,
                tenant_id=str(r.tenant_id) if r.tenant_id else "unknown",
                source_type=str(r.source_type) if r.source_type else "unknown",
                ingestion_stage="validation",
                error_type=err_type,
                error_message="; ".join(rec_errors),
                retryable=is_retryable,
                payload_preview={"title": r.title, "tenant_id": r.tenant_id},
            )
            dlq.quarantine(q)
            quarantined_records.append(q)
            continue

        # Record is valid: normalize and accept
        doc = normalize_source_record(r)
        accepted_documents.append(doc)

        source_type_counts[doc.source_type] += 1
        class_counts[doc.classification] += 1
        tenant_counts[doc.tenant_id] += 1

        if doc.valid_from or doc.valid_until:
            temporal_count += 1
        perms = doc.permissions
        if perms and (perms.allowed_roles or perms.allowed_departments or perms.allowed_teams or perms.allowed_user_ids):
            permissions_count += 1
        if doc.source_entity_id or doc.related_entity_ids:
            provenance_count += 1

        try:
            get_metrics().record_ingestion_document(status="accepted")
        except Exception:
            pass

    report = IngestionReport(
        input_count=len(records),
        output_count=len(accepted_documents),
        successful_count=len(accepted_documents),
        errors=[q.error_message for q in quarantined_records],
        warnings=[],
        source_type_distribution=dict(source_type_counts),
        classification_distribution=dict(class_counts),
        tenant_distribution=dict(tenant_counts),
        records_with_temporal_metadata=temporal_count,
        records_with_permissions=permissions_count,
        records_with_provenance=provenance_count,
    )

    return IngestionBatchResult(
        accepted_documents=accepted_documents,
        quarantined_records=quarantined_records,
        report=report,
    )

