"""Phase 4P: Observability Package for ATLAS.

Provides:
- Structured machine-readable JSON logging.
- Correlation ID generation and task-local propagation.
- Prometheus-compatible metrics exposition (GET /metrics).
- Bounded in-process pipeline boundary tracing.
"""
from novastack.observability.correlation import (
    REQUEST_ID_HEADER,
    generate_request_id,
    get_request_id,
    reset_request_id,
    set_request_id,
    validate_or_generate_request_id,
    validate_request_id,
)
from novastack.observability.logging import (
    LogCaptureHandler,
    StructuredJsonFormatter,
    get_logger,
    log_event,
)
from novastack.observability.metrics import (
    ALLOWED_STAGES,
    CONTENT_TYPE_PROMETHEUS,
    AtlasMetricsRegistry,
    get_metrics,
    reset_metrics,
)
from novastack.observability.tracing import (
    VALID_SPAN_NAMES,
    Span,
    Tracer,
    get_tracer,
)

__all__ = [
    "REQUEST_ID_HEADER",
    "generate_request_id",
    "get_request_id",
    "set_request_id",
    "reset_request_id",
    "validate_request_id",
    "validate_or_generate_request_id",
    "StructuredJsonFormatter",
    "LogCaptureHandler",
    "log_event",
    "get_logger",
    "AtlasMetricsRegistry",
    "get_metrics",
    "reset_metrics",
    "ALLOWED_STAGES",
    "CONTENT_TYPE_PROMETHEUS",
    "Span",
    "Tracer",
    "get_tracer",
    "VALID_SPAN_NAMES",
]
