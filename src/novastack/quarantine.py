"""Phase 4Q: Dead-Letter Queue (DLQ) and Quarantine Subsystem.

Provides:
- QuarantinedRecord: Structured representation of rejected/failed ingestion records.
- DeadLetterQueue: Durable, local JSONL store for quarantined records with retryable classification.
- Fail-safe isolation: Preserves diagnostic context without leaking sensitive enterprise payloads.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from novastack.observability import get_metrics


@dataclass
class QuarantinedRecord:
    """Represents an observational source record that failed ingestion validation.

    Preserves failure context, error taxonomy, and safe attribution without
    storing unvetted credentials, secrets, or unbounded payload data.
    """

    document_id: str
    tenant_id: str
    source_type: str
    ingestion_stage: str  # e.g., "validation", "normalization", "chunking", "integrity"
    error_type: str       # e.g., "ValidationError", "CrossTenantViolation", "SchemaError"
    error_message: str
    retryable: bool       # True if failure may resolve on retry (e.g. transient dependency), False for schema/ACL violation
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    payload_preview: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert quarantined record to dictionary representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuarantinedRecord:
        """Instantiate QuarantinedRecord from dictionary."""
        return cls(
            document_id=str(data.get("document_id", "")),
            tenant_id=str(data.get("tenant_id", "")),
            source_type=str(data.get("source_type", "unknown")),
            ingestion_stage=str(data.get("ingestion_stage", "validation")),
            error_type=str(data.get("error_type", "ValidationError")),
            error_message=str(data.get("error_message", "")),
            retryable=bool(data.get("retryable", False)),
            timestamp=str(data.get("timestamp", datetime.now(timezone.utc).isoformat())),
            payload_preview=dict(data.get("payload_preview", {})),
        )


class DeadLetterQueue:
    """Local, file-backed dead-letter queue for quarantined ingestion records."""

    def __init__(self, dlq_path: Optional[Path | str] = None) -> None:
        if dlq_path is None:
            # Default to data/ingestion/dlq/quarantined_records.jsonl relative to project root
            cur = Path(__file__).resolve()
            candidate = cur.parent.parent.parent
            for parent in [cur] + list(cur.parents):
                if (parent / "data").exists():
                    candidate = parent
                    break
            self.dlq_path = candidate / "data" / "ingestion" / "dlq" / "quarantined_records.jsonl"
        else:
            self.dlq_path = Path(dlq_path)

    def quarantine(self, record: QuarantinedRecord) -> None:
        """Append a quarantined record to the DLQ file and update observability metrics."""
        self.dlq_path.parent.mkdir(parents=True, exist_ok=True)
        record_json = json.dumps(record.to_dict()) + "\n"
        with open(self.dlq_path, "a", encoding="utf-8") as f:
            f.write(record_json)

        # Update Phase 4Q observability metrics
        try:
            metrics = get_metrics()
            metrics.record_ingestion_document(status="quarantined")
            metrics.record_ingestion_failure(
                error_type=record.error_type,
                retryable=str(record.retryable).lower(),
            )
            metrics.record_ingestion_dlq(stage=record.ingestion_stage)
        except Exception:
            pass  # Fail-safe: DLQ logging must not abort on metrics recording failure

    def list_records(
        self,
        tenant_id: Optional[str] = None,
        retryable_only: bool = False,
    ) -> list[QuarantinedRecord]:
        """Read all quarantined records from the DLQ file, with optional tenant filtering."""
        if not self.dlq_path.exists():
            return []

        records: list[QuarantinedRecord] = []
        with open(self.dlq_path, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    record = QuarantinedRecord.from_dict(data)
                    if tenant_id and record.tenant_id != tenant_id:
                        continue
                    if retryable_only and not record.retryable:
                        continue
                    records.append(record)
                except (json.JSONDecodeError, KeyError):
                    continue

        return records

    def get_record(self, document_id: str, tenant_id: str) -> Optional[QuarantinedRecord]:
        """Lookup specific quarantined record by document_id and tenant_id."""
        records = self.list_records(tenant_id=tenant_id)
        for r in reversed(records):  # Most recent first
            if r.document_id == document_id:
                return r
        return None

    def clear(self) -> None:
        """Clear all records from DLQ (used for testing or maintenance)."""
        if self.dlq_path.exists():
            self.dlq_path.unlink()
