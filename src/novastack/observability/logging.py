"""Phase 4P: Centralized Structured JSON Logging Module.

Provides:
- StructuredJsonFormatter: standard library logging formatter producing machine-readable JSON.
- Strict data privacy filters redacting passwords, tokens, full documents, prompts, and raw queries.
- log_event helper for emitting consistent telemetry events.
- LogCaptureHandler for deterministic test verification.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional

from novastack.observability.correlation import get_request_id

# Regex patterns for sanitizing paths and tracebacks
_PATH_PATTERN = re.compile(r"([A-Za-z]:\\[^\s\n\"']+|/[A-Za-z0-9_.\-]+/[^\s\n\"']+)")
_STACK_PATTERN = re.compile(r"(traceback \(most recent call last\):|file \"[^\"]+\", line \d+|in <module>)", re.IGNORECASE)
_SECRET_VALUE_RE = re.compile(
    r"(password|secret|token|key|bearer|authorization)\s*[:=]\s*[^\s\n\"']+",
    re.IGNORECASE,
)


def sanitize_error_detail(detail: str) -> str:
    """Sanitize error messages to prevent leakage of paths, secrets, or stack traces."""
    if not detail or not str(detail).strip():
        return "Internal server processing failure"
    text = str(detail)
    if _STACK_PATTERN.search(text):
        return "Internal server processing failure"
    sanitized = _SECRET_VALUE_RE.sub("[REDACTED_CREDENTIAL]", text)
    sanitized = _PATH_PATTERN.sub("[REDACTED_PATH]", sanitized)
    return sanitized.strip()


# Explicit list of prohibited keys that must never appear in structured logs
_PROHIBITED_KEYS = {
    "password",
    "secret",
    "token",
    "key",
    "bearer",
    "authorization",
    "credentials",
    "prompt",
    "raw_query",
    "query",
    "document_content",
    "raw_text",
    "evidence_package",
    "evidence_items",
    "answer_text",
}

# Regex to detect credentials in any arbitrary string value
_SECRET_VALUE_RE = re.compile(
    r"(password|secret|token|key|bearer|authorization)\s*[:=]\s*[^\s\n\"']+",
    re.IGNORECASE,
)


class StructuredJsonFormatter(logging.Formatter):
    """Formats Python logging records as strictly sanitized JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        record_dict: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", "log_message"),
            "request_id": getattr(record, "request_id", None) or get_request_id() or None,
            "endpoint": getattr(record, "endpoint", None),
            "status_code": getattr(record, "status_code", None),
            "answer_status": getattr(record, "answer_status", None),
            "latency_ms": getattr(record, "latency_ms", None),
            "error_type": getattr(record, "error_type", None),
        }

        # Safe optional metadata
        tenant_id = getattr(record, "tenant_id", None)
        if tenant_id and isinstance(tenant_id, str):
            record_dict["tenant_id"] = sanitize_error_detail(tenant_id)

        eval_id = getattr(record, "evaluation_id", None)
        if eval_id and isinstance(eval_id, str):
            record_dict["evaluation_id"] = eval_id

        # Merge additional custom attributes if explicitly attached to record
        custom_attrs = getattr(record, "custom_attrs", {})
        if isinstance(custom_attrs, dict):
            for k, v in custom_attrs.items():
                k_lower = k.lower()
                if k_lower in _PROHIBITED_KEYS:
                    continue
                if isinstance(v, str):
                    if _SECRET_VALUE_RE.search(v):
                        record_dict[k] = "[REDACTED_CREDENTIAL]"
                    else:
                        record_dict[k] = sanitize_error_detail(v)
                elif isinstance(v, (int, float, bool)) or v is None:
                    record_dict[k] = v

        # If standard message is present and not generic
        msg = record.getMessage()
        if msg and msg != record_dict["event"]:
            sanitized_msg = sanitize_error_detail(msg)
            # Ensure sanitized message does not leak credentials
            if _SECRET_VALUE_RE.search(sanitized_msg):
                sanitized_msg = "[REDACTED_CREDENTIAL]"
            record_dict["message"] = sanitized_msg

        # Filter out None values to keep JSON crisp
        filtered_dict = {k: v for k, v in record_dict.items() if v is not None}
        return json.dumps(filtered_dict, separators=(",", ":"))


def log_event(
    logger: logging.Logger,
    event: str,
    level: int = logging.INFO,
    request_id: Optional[str] = None,
    endpoint: Optional[str] = None,
    status_code: Optional[int] = None,
    answer_status: Optional[str] = None,
    latency_ms: Optional[float] = None,
    error_type: Optional[str] = None,
    tenant_id: Optional[str] = None,
    evaluation_id: Optional[str] = None,
    **extra_attrs: Any,
) -> None:
    """Helper to emit structured telemetry events safely with strict sanitization."""
    # Filter out prohibited keys from extra_attrs
    safe_extras: Dict[str, Any] = {}
    for k, v in extra_attrs.items():
        k_lower = k.lower()
        if k_lower in _PROHIBITED_KEYS:
            continue
        if isinstance(v, str):
            if _SECRET_VALUE_RE.search(v):
                safe_extras[k] = "[REDACTED_CREDENTIAL]"
            else:
                safe_extras[k] = sanitize_error_detail(v)
        elif isinstance(v, (int, float, bool)) or v is None:
            safe_extras[k] = v

    extra = {
        "event": event,
        "request_id": request_id or get_request_id() or None,
        "endpoint": endpoint,
        "status_code": status_code,
        "answer_status": answer_status,
        "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
        "error_type": error_type,
        "tenant_id": tenant_id,
        "evaluation_id": evaluation_id,
        "custom_attrs": safe_extras,
    }

    logger.log(level, event, extra=extra)


class LogCaptureHandler(logging.Handler):
    """In-memory logging handler that captures structured JSON output lines for testing."""

    def __init__(self):
        super().__init__()
        self.setFormatter(StructuredJsonFormatter())
        self.records: List[Dict[str, Any]] = []
        self.raw_lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            self.raw_lines.append(line)
            self.records.append(json.loads(line))
        except Exception:
            self.handleError(record)

    def clear(self) -> None:
        self.records.clear()
        self.raw_lines.clear()


def get_logger(name: str = "novastack") -> logging.Logger:
    """Obtain or configure a logger instance."""
    return logging.getLogger(name)
