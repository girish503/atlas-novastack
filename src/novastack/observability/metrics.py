"""Phase 4P: Prometheus-Compatible Metrics Module.

Provides:
- Thread-safe, minimal-dependency metrics registry (AtlasMetricsRegistry).
- Strictly bounded label validation preventing cardinality explosion.
- Standard Prometheus exposition format for GET /metrics.
- Complete measurement catalog for requests, errors, answer statuses, resilience events, and pipeline stages.
"""
from __future__ import annotations

import threading
from typing import Dict, FrozenSet, List, Optional, Tuple

CONTENT_TYPE_PROMETHEUS = "text/plain; version=0.0.4; charset=utf-8"

# Strictly bounded set of pipeline stages
ALLOWED_STAGES: FrozenSet[str] = frozenset([
    "query_understanding",
    "bm25",
    "dense",
    "fusion",
    "metadata_ranking",
    "relational_retrieval",
    "evidence_resolution",
    "generation",
    "citation_resolution",
    "total",
])

# Prohibited unbounded label keys
_FORBIDDEN_LABEL_KEYS: FrozenSet[str] = frozenset([
    "query",
    "raw_query",
    "query_text",
    "request_id",
    "user_id",
    "tenant_id",
    "evaluation_id",
    "session_id",
    "prompt",
    "answer",
])

# Standard latency buckets for histograms (in seconds)
_DEFAULT_BUCKETS: Tuple[float, ...] = (
    0.01,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
)


class AtlasMetricsRegistry:
    """Thread-safe Prometheus-compatible metrics collector."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Reset all metrics to initial states (used for test isolation)."""
        with getattr(self, "_lock", threading.Lock()):
            # Counters
            self._requests_total: Dict[Tuple[str, str, str], int] = {}  # (endpoint, method, status) -> count
            self._request_errors_total: Dict[Tuple[str, str], int] = {}  # (error_type, status) -> count
            self._query_answers_total: int = 0
            self._query_abstentions_total: int = 0
            self._query_timeouts_total: int = 0
            self._capacity_exhausted_total: int = 0
            self._model_errors_total: int = 0
            # Phase 4T: bounded identity rejection outcomes.  Never include a
            # subject, tenant, token, issuer, or any other caller-provided data.
            self._identity_verification_failures_total: Dict[str, int] = {}

            # Request Latency Histogram
            self._request_latency_count: int = 0
            self._request_latency_sum: float = 0.0
            self._request_latency_buckets: Dict[float, int] = {b: 0 for b in _DEFAULT_BUCKETS}

            # Stage Latencies
            self._stage_latency_count: Dict[str, int] = {s: 0 for s in ALLOWED_STAGES}
            self._stage_latency_sum: Dict[str, float] = {s: 0.0 for s in ALLOWED_STAGES}
            self._stage_latency_buckets: Dict[str, Dict[float, int]] = {
                s: {b: 0 for b in _DEFAULT_BUCKETS} for s in ALLOWED_STAGES
            }

            # Phase 4Q Ingestion & Index Metrics
            self._ingestion_documents_total: Dict[str, int] = {}  # status -> count ("accepted", "quarantined")
            self._ingestion_failures_total: Dict[Tuple[str, str], int] = {}  # (error_type, retryable) -> count
            self._ingestion_dlq_total: Dict[str, int] = {}  # stage -> count
            self._index_builds_total: Dict[str, int] = {}  # status -> count ("success", "failed")
            self._index_build_failures_total: Dict[str, int] = {}  # stage -> count
            self._index_publish_total: int = 0
            self._index_validation_failures_total: Dict[str, int] = {}  # rule -> count
            self._index_publish_failures_total: Dict[str, int] = {}  # bounded publication stage -> count
            self._index_retirements_total: int = 0
            self._active_generation_present: int = 0

    # -----------------------------------------------------------------
    # Cardinality & Label Validation
    # -----------------------------------------------------------------
    def _validate_label_keys(self, labels: Dict[str, str]) -> None:
        """Ensure no unbounded or sensitive label keys are used."""
        for k in labels:
            if k.lower() in _FORBIDDEN_LABEL_KEYS:
                raise ValueError(f"Unbounded metric label '{k}' is strictly prohibited.")

    # -----------------------------------------------------------------
    # Recording Methods
    # -----------------------------------------------------------------
    def record_request(self, endpoint: str = "/query", method: str = "POST", status: int = 200, duration_seconds: float = 0.0) -> None:
        """Record an incoming request completion and its latency."""
        endpoint_clean = str(endpoint).split("?")[0]
        status_str = str(status)
        method_clean = method.upper()

        with self._lock:
            key = (endpoint_clean, method_clean, status_str)
            self._requests_total[key] = self._requests_total.get(key, 0) + 1

            self._request_latency_count += 1
            self._request_latency_sum += duration_seconds
            for b in _DEFAULT_BUCKETS:
                if duration_seconds <= b:
                    self._request_latency_buckets[b] += 1

    def record_error(self, error_type: str, status: int = 500) -> None:
        """Record an error occurrence with bounded classification."""
        clean_type = str(error_type).strip() or "InternalServerError"
        status_str = str(status)

        with self._lock:
            key = (clean_type, status_str)
            self._request_errors_total[key] = self._request_errors_total.get(key, 0) + 1

    def record_answer_status(self, status: str) -> None:
        """Record query answer outcome (answered, abstained, timeout, capacity_exhausted, model_error)."""
        clean_status = str(status).lower().strip()
        with self._lock:
            if clean_status == "answered":
                self._query_answers_total += 1
            elif clean_status == "abstained":
                self._query_abstentions_total += 1
            elif clean_status == "timeout":
                self._query_timeouts_total += 1
            elif clean_status in ("capacity_exhausted", "capacityexhaustederror"):
                self._capacity_exhausted_total += 1
            elif clean_status in ("model_error", "modelunavailableerror"):
                self._model_errors_total += 1

    def record_timeout(self) -> None:
        """Record query timeout occurrence."""
        with self._lock:
            self._query_timeouts_total += 1

    def record_capacity_exhausted(self) -> None:
        """Record capacity exhaustion occurrence."""
        with self._lock:
            self._capacity_exhausted_total += 1

    def record_model_error(self) -> None:
        """Record model failure or unavailability."""
        with self._lock:
            self._model_errors_total += 1

    def record_identity_verification_failure(self, outcome: str) -> None:
        """Record a bounded Phase 4T authentication/identity rejection."""
        allowed = {"configuration", "authentication", "context_mismatch"}
        if outcome not in allowed:
            raise ValueError(f"Unknown identity verification outcome: {outcome}")
        with self._lock:
            self._identity_verification_failures_total[outcome] = (
                self._identity_verification_failures_total.get(outcome, 0) + 1
            )

    def record_stage_latency(self, stage: str, duration_seconds: float) -> None:
        """Record latency for a specific pipeline stage within the allowed bounded set."""
        if stage not in ALLOWED_STAGES:
            raise ValueError(f"Unknown or unbounded pipeline stage '{stage}'. Must be one of {sorted(ALLOWED_STAGES)}")

        with self._lock:
            self._stage_latency_count[stage] += 1
            self._stage_latency_sum[stage] += duration_seconds
            for b in _DEFAULT_BUCKETS:
                if duration_seconds <= b:
                    self._stage_latency_buckets[stage][b] += 1

    def record_ingestion_document(self, status: str = "accepted") -> None:
        """Record ingestion document acceptance or quarantine."""
        clean_status = str(status).lower().strip()
        with self._lock:
            self._ingestion_documents_total[clean_status] = (
                self._ingestion_documents_total.get(clean_status, 0) + 1
            )

    def record_ingestion_failure(self, error_type: str, retryable: str = "false") -> None:
        """Record an ingestion failure with error type and retryability flag."""
        clean_type = str(error_type).strip() or "ValidationError"
        clean_retry = str(retryable).lower().strip()
        with self._lock:
            key = (clean_type, clean_retry)
            self._ingestion_failures_total[key] = (
                self._ingestion_failures_total.get(key, 0) + 1
            )

    def record_ingestion_dlq(self, stage: str = "validation") -> None:
        """Record a document being routed to the DLQ/quarantine."""
        clean_stage = str(stage).lower().strip()
        with self._lock:
            self._ingestion_dlq_total[clean_stage] = (
                self._ingestion_dlq_total.get(clean_stage, 0) + 1
            )

    def record_index_build(self, status: str = "success") -> None:
        """Record index build execution (success or failed)."""
        clean_status = str(status).lower().strip()
        with self._lock:
            self._index_builds_total[clean_status] = (
                self._index_builds_total.get(clean_status, 0) + 1
            )

    def record_index_build_failure(self, stage: str = "embedding") -> None:
        """Record index build failure stage."""
        clean_stage = str(stage).lower().strip()
        with self._lock:
            self._index_build_failures_total[clean_stage] = (
                self._index_build_failures_total.get(clean_stage, 0) + 1
            )

    def record_index_publish(self) -> None:
        """Record atomic promotion of candidate index to active generation."""
        with self._lock:
            self._index_publish_total += 1

    def record_index_publish_failure(self, stage: str = "validation") -> None:
        """Record a rejected candidate publication using a bounded stage label."""
        clean_stage = str(stage).lower().strip()
        with self._lock:
            self._index_publish_failures_total[clean_stage] = (
                self._index_publish_failures_total.get(clean_stage, 0) + 1
            )

    def record_index_retirement(self) -> None:
        """Record safe retirement after all request leases release."""
        with self._lock:
            self._index_retirements_total += 1

    def set_active_generation(self, active: bool) -> None:
        """Expose whether a validated active generation is published.

        The generation identifier is intentionally not a metric label because
        it is unbounded.  It is available through the readiness response and
        bounded structured lifecycle events instead.
        """
        with self._lock:
            self._active_generation_present = 1 if active else 0

    def record_index_validation_failure(self, rule: str = "chunk_alignment") -> None:
        """Record an index integrity validation rule violation."""
        clean_rule = str(rule).strip()
        with self._lock:
            self._index_validation_failures_total[clean_rule] = (
                self._index_validation_failures_total.get(clean_rule, 0) + 1
            )

    # -----------------------------------------------------------------
    # Exposition
    # -----------------------------------------------------------------
    def generate_prometheus_text(self) -> str:
        """Generate standard Prometheus 0.0.4 text exposition format."""
        lines: List[str] = []

        with self._lock:
            # 1. atlas_requests_total
            lines.append("# HELP atlas_requests_total Total number of HTTP requests processed by ATLAS.")
            lines.append("# TYPE atlas_requests_total counter")
            if not self._requests_total:
                lines.append('atlas_requests_total{endpoint="/query",method="POST",status="200"} 0')
            else:
                for (ep, method, st), count in sorted(self._requests_total.items()):
                    lines.append(f'atlas_requests_total{{endpoint="{ep}",method="{method}",status="{st}"}} {count}')

            # 2. atlas_request_errors_total
            lines.append("# HELP atlas_request_errors_total Total number of request errors by error type.")
            lines.append("# TYPE atlas_request_errors_total counter")
            if not self._request_errors_total:
                lines.append('atlas_request_errors_total{error_type="none",status="200"} 0')
            else:
                for (et, st), count in sorted(self._request_errors_total.items()):
                    lines.append(f'atlas_request_errors_total{{error_type="{et}",status="{st}"}} {count}')

            # 3. atlas_request_latency_seconds
            lines.append("# HELP atlas_request_latency_seconds Total request latency in seconds.")
            lines.append("# TYPE atlas_request_latency_seconds histogram")
            for b in _DEFAULT_BUCKETS:
                cnt = self._request_latency_buckets[b]
                lines.append(f'atlas_request_latency_seconds_bucket{{le="{b}"}} {cnt}')
            lines.append(f'atlas_request_latency_seconds_bucket{{le="+Inf"}} {self._request_latency_count}')
            lines.append(f"atlas_request_latency_seconds_sum {self._request_latency_sum:.6f}")
            lines.append(f"atlas_request_latency_seconds_count {self._request_latency_count}")

            # 4. atlas_query_answers_total
            lines.append("# HELP atlas_query_answers_total Total queries answered with ground evidence.")
            lines.append("# TYPE atlas_query_answers_total counter")
            lines.append(f"atlas_query_answers_total {self._query_answers_total}")

            # 5. atlas_query_abstentions_total
            lines.append("# HELP atlas_query_abstentions_total Total queries safely abstained.")
            lines.append("# TYPE atlas_query_abstentions_total counter")
            lines.append(f"atlas_query_abstentions_total {self._query_abstentions_total}")

            # 6. atlas_query_timeouts_total
            lines.append("# HELP atlas_query_timeouts_total Total queries halted due to execution timeout.")
            lines.append("# TYPE atlas_query_timeouts_total counter")
            lines.append(f"atlas_query_timeouts_total {self._query_timeouts_total}")

            # 7. atlas_capacity_exhausted_total
            lines.append("# HELP atlas_capacity_exhausted_total Total queries rejected due to saturated worker capacity.")
            lines.append("# TYPE atlas_capacity_exhausted_total counter")
            lines.append(f"atlas_capacity_exhausted_total {self._capacity_exhausted_total}")

            # 8. atlas_model_errors_total
            lines.append("# HELP atlas_model_errors_total Total model-level failures or unavailabilities.")
            lines.append("# TYPE atlas_model_errors_total counter")
            lines.append(f"atlas_model_errors_total {self._model_errors_total}")

            # 9. Phase 4T identity rejections.  The outcome vocabulary is
            # intentionally closed to prevent cardinality or data leakage.
            lines.append("# HELP atlas_identity_verification_failures_total Total rejected caller identity verifications by bounded outcome.")
            lines.append("# TYPE atlas_identity_verification_failures_total counter")
            if not self._identity_verification_failures_total:
                lines.append('atlas_identity_verification_failures_total{outcome="authentication"} 0')
            else:
                for outcome, count in sorted(self._identity_verification_failures_total.items()):
                    lines.append(f'atlas_identity_verification_failures_total{{outcome="{outcome}"}} {count}')

            # 10. atlas_stage_latency_seconds
            lines.append("# HELP atlas_stage_latency_seconds Pipeline stage latencies in seconds.")
            lines.append("# TYPE atlas_stage_latency_seconds histogram")
            for stage in sorted(ALLOWED_STAGES):
                for b in _DEFAULT_BUCKETS:
                    cnt = self._stage_latency_buckets[stage][b]
                    lines.append(f'atlas_stage_latency_seconds_bucket{{stage="{stage}",le="{b}"}} {cnt}')
                lines.append(f'atlas_stage_latency_seconds_bucket{{stage="{stage}",le="+Inf"}} {self._stage_latency_count[stage]}')
                lines.append(f'atlas_stage_latency_seconds_sum{{stage="{stage}"}} {self._stage_latency_sum[stage]:.6f}')
                lines.append(f'atlas_stage_latency_seconds_count{{stage="{stage}"}} {self._stage_latency_count[stage]}')

            # 10. atlas_ingestion_documents_total
            lines.append("# HELP atlas_ingestion_documents_total Total documents accepted or quarantined during ingestion.")
            lines.append("# TYPE atlas_ingestion_documents_total counter")
            if not self._ingestion_documents_total:
                lines.append('atlas_ingestion_documents_total{status="accepted"} 0')
            else:
                for st, count in sorted(self._ingestion_documents_total.items()):
                    lines.append(f'atlas_ingestion_documents_total{{status="{st}"}} {count}')

            # 11. atlas_ingestion_failures_total
            lines.append("# HELP atlas_ingestion_failures_total Total ingestion validation and processing failures.")
            lines.append("# TYPE atlas_ingestion_failures_total counter")
            if not self._ingestion_failures_total:
                lines.append('atlas_ingestion_failures_total{error_type="none",retryable="false"} 0')
            else:
                for (et, ret), count in sorted(self._ingestion_failures_total.items()):
                    lines.append(f'atlas_ingestion_failures_total{{error_type="{et}",retryable="{ret}"}} {count}')

            # 12. atlas_ingestion_dlq_total
            lines.append("# HELP atlas_ingestion_dlq_total Total records routed to dead-letter queue / quarantine.")
            lines.append("# TYPE atlas_ingestion_dlq_total counter")
            if not self._ingestion_dlq_total:
                lines.append('atlas_ingestion_dlq_total{stage="validation"} 0')
            else:
                for stage, count in sorted(self._ingestion_dlq_total.items()):
                    lines.append(f'atlas_ingestion_dlq_total{{stage="{stage}"}} {count}')

            # 13. atlas_index_builds_total
            lines.append("# HELP atlas_index_builds_total Total index generation builds initiated.")
            lines.append("# TYPE atlas_index_builds_total counter")
            if not self._index_builds_total:
                lines.append('atlas_index_builds_total{status="success"} 0')
            else:
                for st, count in sorted(self._index_builds_total.items()):
                    lines.append(f'atlas_index_builds_total{{status="{st}"}} {count}')

            # 14. atlas_index_build_failures_total
            lines.append("# HELP atlas_index_build_failures_total Total index generation build failures by stage.")
            lines.append("# TYPE atlas_index_build_failures_total counter")
            if not self._index_build_failures_total:
                lines.append('atlas_index_build_failures_total{stage="validation"} 0')
            else:
                for stage, count in sorted(self._index_build_failures_total.items()):
                    lines.append(f'atlas_index_build_failures_total{{stage="{stage}"}} {count}')

            # 15. atlas_index_publish_total
            lines.append("# HELP atlas_index_publish_total Total successful atomic index promotions.")
            lines.append("# TYPE atlas_index_publish_total counter")
            lines.append(f"atlas_index_publish_total {self._index_publish_total}")

            # 16. atlas_index_validation_failures_total
            lines.append("# HELP atlas_index_validation_failures_total Total index candidate integrity validation failures.")
            lines.append("# TYPE atlas_index_validation_failures_total counter")
            if not self._index_validation_failures_total:
                lines.append('atlas_index_validation_failures_total{rule="none"} 0')
            else:
                for rule, count in sorted(self._index_validation_failures_total.items()):
                    lines.append(f'atlas_index_validation_failures_total{{rule="{rule}"}} {count}')

            # 17. Phase 4S generation lifecycle signals.  No generation IDs
            # are labels so the metric cardinality remains bounded.
            lines.append("# HELP atlas_index_publish_failures_total Total rejected index generation publications by stage.")
            lines.append("# TYPE atlas_index_publish_failures_total counter")
            if not self._index_publish_failures_total:
                lines.append('atlas_index_publish_failures_total{stage="none"} 0')
            else:
                for stage, count in sorted(self._index_publish_failures_total.items()):
                    lines.append(f'atlas_index_publish_failures_total{{stage="{stage}"}} {count}')

            lines.append("# HELP atlas_index_retirements_total Total generations safely retired after request leases released.")
            lines.append("# TYPE atlas_index_retirements_total counter")
            lines.append(f"atlas_index_retirements_total {self._index_retirements_total}")

            lines.append("# HELP atlas_active_generation Whether one validated index generation is currently active.")
            lines.append("# TYPE atlas_active_generation gauge")
            lines.append(f"atlas_active_generation {self._active_generation_present}")

        return "\n".join(lines) + "\n"


# Global singleton registry instance
_metrics_instance = AtlasMetricsRegistry()


def get_metrics() -> AtlasMetricsRegistry:
    """Obtain global metrics registry singleton."""
    return _metrics_instance


def reset_metrics() -> None:
    """Reset global metrics registry (test isolation helper)."""
    _metrics_instance.reset()
