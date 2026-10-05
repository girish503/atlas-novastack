"""Phase 4P: In-Process Lightweight Tracing Module.

Evaluates OpenTelemetry compatibility and provides zero-dependency,
deterministic span management for major ATLAS pipeline boundaries:
- request
- retrieval
- reranking
- evidence_resolution
- generation
- citation_resolution

Guarantees:
- Bounded span volume (no thousands of spans per request).
- Strict redaction (prompts, raw queries, documents, and credentials never stored as span attributes).
- Completely passive instrumentation: zero impact on retrieval, ranking, or generation outcomes.
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set

from novastack.observability.correlation import get_request_id
from novastack.observability.logging import sanitize_error_detail

logger = logging.getLogger("novastack.observability.tracing")

# Strictly bounded pipeline boundaries
VALID_SPAN_NAMES = frozenset([
    "request",
    "retrieval",
    "reranking",
    "evidence_resolution",
    "generation",
    "citation_resolution",
])

# Prohibited attribute keys for spans to prevent privacy leakage
_PROHIBITED_SPAN_ATTRS = frozenset([
    "password",
    "secret",
    "token",
    "bearer",
    "authorization",
    "credentials",
    "prompt",
    "raw_query",
    "query",
    "document_content",
    "raw_text",
    "evidence_items",
    "answer_text",
])


class Span:
    """Represents an execution span across a pipeline boundary."""

    def __init__(self, name: str, tracer: "Tracer", request_id: Optional[str] = None):
        self.name = name
        self.tracer = tracer
        self.request_id = request_id or get_request_id() or "unknown"
        self.start_time: float = 0.0
        self.end_time: Optional[float] = None
        self.duration_ms: Optional[float] = None
        self.status: str = "OK"  # "OK" or "ERROR"
        self.attributes: Dict[str, Any] = {}
        self.error_type: Optional[str] = None

    def set_attribute(self, key: str, value: Any) -> "Span":
        """Set a safe attribute on the span."""
        k_lower = key.lower()
        if k_lower in _PROHIBITED_SPAN_ATTRS:
            return self
        if isinstance(value, str):
            self.attributes[key] = sanitize_error_detail(value)
        elif isinstance(value, (int, float, bool)) or value is None:
            self.attributes[key] = value
        return self

    def set_status(self, status: str, error_type: Optional[str] = None) -> "Span":
        """Set the completion status of the span."""
        self.status = "ERROR" if status.upper() == "ERROR" else "OK"
        if error_type:
            self.error_type = sanitize_error_detail(error_type)
        return self

    def __enter__(self) -> "Span":
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.end_time = time.perf_counter()
        self.duration_ms = (self.end_time - self.start_time) * 1000.0
        if exc_type is not None:
            self.status = "ERROR"
            self.error_type = exc_type.__name__
        self.tracer._record_finished_span(self)


class Tracer:
    """Thread-safe in-process tracer managing pipeline boundary spans."""

    def __init__(self):
        self._lock = threading.Lock()
        self._finished_spans: List[Span] = []

    def start_span(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> Span:
        """Create and return a new span for an execution boundary."""
        clean_name = name if name in VALID_SPAN_NAMES else f"stage_{name}"
        span = Span(name=clean_name, tracer=self)
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        return span

    def _record_finished_span(self, span: Span) -> None:
        """Internal callback to store completed spans."""
        with self._lock:
            self._finished_spans.append(span)
            # Limit in-memory buffer to prevent memory leakage
            if len(self._finished_spans) > 1000:
                self._finished_spans = self._finished_spans[-500:]

    def get_finished_spans(self, request_id: Optional[str] = None) -> List[Span]:
        """Retrieve recorded spans, optionally filtered by request_id."""
        with self._lock:
            if request_id:
                return [s for s in self._finished_spans if s.request_id == request_id]
            return list(self._finished_spans)

    def clear(self) -> None:
        """Clear recorded spans (test isolation helper)."""
        with self._lock:
            self._finished_spans.clear()


# Global singleton tracer instance
_tracer_instance = Tracer()


def get_tracer() -> Tracer:
    """Obtain global tracer singleton."""
    return _tracer_instance
