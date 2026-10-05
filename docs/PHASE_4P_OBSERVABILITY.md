# ATLAS Phase 4P: Structured Logging & Observability Report

**Status**: Certified & Accepted  
**Phase**: 4P  
**Date**: 2026-09-12  
**Target Environment**: Enterprise Production Service  
**Frozen Production Configuration**:
- `enable_boundary_stitching = False`
- `enable_query_aware_authority = True` (Mechanism B)
- `enable_event_bundling = False`

---

## 1. Executive Summary

Phase 4P delivers end-to-end production observability for the ATLAS enterprise search service. Operators gain real-time visibility into incoming traffic, failure modes, timeouts, capacity contention, and stage-by-stage pipeline performance without exposing sensitive corporate data or altering underlying retrieval and ranking semantics.

Key accomplishments:
1. **Centralized Structured Logging**: Standard Python logging emits sanitized, machine-readable JSON records with ISO-8601 UTC timestamps, log levels, event names, request correlation IDs, HTTP status codes, answer classifications, and latency metrics.
2. **Context-Local Request Correlation**: Correlation IDs (`X-Request-ID`) are generated (UUID4) when absent or validated when supplied by the caller, propagating seamlessly across asynchronous and thread-pool execution boundaries without global mutable state.
3. **Prometheus-Compatible Metrics (`GET /metrics`)**: Standard text exposition format capturing request totals, latency distributions, error categories, answer outcomes, and stage timings under strict cardinality bounding.
4. **Passive Error & Resilience Metrics**: Independent counters for timeouts (504), capacity exhaustions (429), model unavailabilities (503), validation errors (422), and internal errors (500).
5. **OpenTelemetry-Compatible In-Process Tracing**: Minimal, bounded spans tracking the 6 major pipeline boundaries (`request`, `retrieval`, `reranking`, `evidence_resolution`, `generation`, `citation_resolution`) without external network dependencies.
6. **Strict Privacy & Cardinality Enforcement**: Zero credential, prompt, document, or raw query leakage in logs, spans, or metric labels.
7. **Empirical Overhead**: Absolute telemetry overhead measured at ~1.96ms (<0.4% of typical LLM generation latency).
8. **100% Regression Certification**: 18/18 Phase 4P tests passed; 86/86 full regression suite passed across all 9 historical modules with zero regressions.

---

## 2. Structured Logging Architecture

Centralized in `src/novastack/observability/logging.py`, the logging system implements `StructuredJsonFormatter(logging.Formatter)` using standard library Python logging.

### 2.1 Emitted Log Schema
Every request and error produces a structured JSON record:
```json
{
  "timestamp": "2026-09-12T11:03:55.123456+00:00",
  "level": "INFO",
  "event": "query_completed",
  "request_id": "c7a6e19b-4356-47b2-8c90-951b14b8f521",
  "endpoint": "/query",
  "status_code": 200,
  "answer_status": "answered",
  "latency_ms": 24.50,
  "tenant_id": "TENANT-NOVASTACK",
  "evaluation_id": "BENCH-001"
}
```

### 2.2 Mandatory & Optional Log Fields
| Field Name | Type | Requirement | Description |
| :--- | :--- | :--- | :--- |
| `timestamp` | string | Mandatory | ISO-8601 UTC timestamp format |
| `level` | string | Mandatory | Standard Python log level (`INFO`, `WARNING`, `ERROR`) |
| `event` | string | Mandatory | Normalized event (`query_completed`, `request_timeout`, etc.) |
| `request_id` | string | Mandatory | Correlation UUID or validated client token |
| `endpoint` | string | Mandatory | Target HTTP path (`/query`, `/healthz`, `/ready`) |
| `status_code` | integer | Mandatory | Standard HTTP status code (200, 422, 429, 503, 504, 500) |
| `answer_status` | string | Mandatory | Query outcome (`answered`, `abstained`, `timeout`, `error`) |
| `latency_ms` | float | Mandatory | End-to-end execution duration in milliseconds |
| `error_type` | string | Optional | Exception class name (for failures) |
| `tenant_id` | string | Optional | Sanitized tenant identifier |
| `evaluation_id` | string | Optional | Evaluation fixture identifier when present |

---

## 3. Request Correlation & Context Propagation

Located in `src/novastack/observability/correlation.py`:

```
Client Request (optional X-Request-ID)
      │
      ▼
validate_or_generate_request_id()
      │ (Validation: ^[A-Za-z0-9_\-]{1,64}$, reject secrets/paths)
      ▼
set_request_id(rid)  <-- contextvars.ContextVar
      │
      ├─► Propagated to ThreadPoolExecutor via contextvars.copy_context().run()
      ├─► Bound to root trace span: tracer.start_span("request")
      ├─► Attached to pipeline execution: pipe.execute_query(..., request_id=rid)
      ├─► Injected into all structured JSON log records
      └─► Returned in HTTP response header: X-Request-ID: <rid>
```

### Correlation Integrity Guarantees:
- **No Global Mutable State**: Built entirely on `contextvars.ContextVar` and explicit parameter passing.
- **Cross-Thread Synchronization**: When offloading CPU-heavy inference to the `ThreadPoolExecutor`, `ctx.run(_run_query)` preserves the calling coroutine's context.
- **Injection Protection**: Request IDs containing spaces, paths (`/`, `\`), or secret indicators (`password`, `secret`, `bearer`) are immediately discarded and replaced with fresh UUID4 tokens.

---

## 4. Prometheus Metric Catalog

Available at `GET /metrics` in standard Prometheus 0.0.4 exposition format (`CONTENT_TYPE_PROMETHEUS`):

| Metric Name | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `atlas_requests_total` | Counter | `endpoint`, `method`, `status` | Total HTTP requests handled |
| `atlas_request_errors_total` | Counter | `error_type`, `status` | Total errors broken down by error type and status |
| `atlas_request_latency_seconds` | Histogram | `le` buckets (0.01 to 60.0s) | Request latency distribution + count + sum |
| `atlas_query_answers_total` | Counter | *None* | Queries successfully answered with ground evidence |
| `atlas_query_abstentions_total` | Counter | *None* | Queries safely abstained |
| `atlas_query_timeouts_total` | Counter | *None* | Queries terminated due to execution deadline |
| `atlas_capacity_exhausted_total`| Counter | *None* | Queries rejected due to saturated worker queue |
| `atlas_model_errors_total` | Counter | *None* | Queries failed due to model/component unreadiness |
| `atlas_stage_latency_seconds` | Histogram | `stage`, `le` buckets | Latency per pipeline stage (bounded set) |

---

## 5. Stage Timing Catalog & Bounded Cardinality

To avoid metric cardinality explosion and memory exhaustion in Prometheus, stage labels are strictly whitelisted.

### Allowed Pipeline Stages:
1. `query_understanding`: Entity extraction, query intent, query expansion.
2. `bm25`: BM25 inverted index lexical candidate scoring.
3. `dense`: BGE dense vector embedding retrieval.
4. `relational_retrieval`: Relational entity catalog candidate scoring.
5. `fusion`: Reciprocal Rank Fusion (`fuse_rrf_sum` + hybrid fusion).
6. `metadata_ranking`: Authority, freshness, caveat, and source intent reranking.
7. `evidence_resolution`: Access control enforcement, boundary validation, evidence item assembly.
8. `generation`: Prompt construction, stopping criteria, Gemma greedy token generation.
9. `citation_resolution`: C2 citation validation and structured item mapping.
10. `total`: End-to-end pipeline execution time.

Any attempt to record an unwhitelisted stage raises a `ValueError`. Metric labels strictly prohibit user IDs, request IDs, queries, or arbitrary tenant IDs.

---

## 6. OpenTelemetry Evaluation & Tracing Decision

### 6.1 OpenTelemetry Evaluation
- **Analysis**: The base production runtime image does not bundle the heavy `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, or gRPC transport libraries. Introducing external network exporters would increase container size, introduce external network failure modes, and violate minimal-dependency constraints.
- **Decision**: ATLAS implements an **in-process OpenTelemetry-compatible tracer** (`src/novastack/observability/tracing.py`). It defines standard span semantics (`start_span`, `set_attribute`, `set_status`, context-managed boundaries) that map directly to OpenTelemetry APIs without requiring external pip packages.

### 6.2 Major Pipeline Boundaries
Exactly 6 spans are created per request:
1. `"request"`: Root HTTP span encompassing request lifetime.
2. `"retrieval"`: Encompasses BM25, dense, structured retrieval, and fusion.
3. `"reranking"`: Encompasses metadata and authority ranking.
4. `"evidence_resolution"`: Encompasses candidate filtering and evidence package creation.
5. `"generation"`: Encompasses prompt assembly and local model generation.
6. `"citation_resolution"`: Encompasses C2 citation mapping and verification.

---

## 7. Data Privacy & Security Audit

The observability system was audited against security and privacy guidelines:

| Privacy Boundary | Verification Finding | Compliance Status |
| :--- | :--- | :--- |
| **Credentials & Tokens** | Stripped from logs and span attributes. Passwords and bearer tokens redacted via regex. | **PASS** |
| **Authorization ACLs** | User roles/departments recorded only as safe context; raw permission bitmaps excluded. | **PASS** |
| **Raw Documents & Chunks**| Document texts and full evidence packages are explicitly prohibited from log serialization. | **PASS** |
| **Model Prompts** | Raw prompt texts are never logged or stored as span attributes. | **PASS** |
| **Generated Answers** | Output text excluded from logs; only outcome status (`answered`, `abstained`, `timeout`) recorded. | **PASS** |
| **Query Text** | Raw query strings excluded from metric labels and logs. | **PASS** |
| **Request ID Injection** | Client-supplied request IDs validated against `^[A-Za-z0-9_\-]{1,64}$`; paths and secrets rejected. | **PASS** |
| **Metric Cardinality** | All label dimensions are bounded enums; no high-cardinality strings permitted. | **PASS** |

---

## 8. Empirical Overhead Measurement

Measured over 50 consecutive query iterations comparing full HTTP execution with observability active against the raw in-memory pipeline:

| Measurement | Result | Notes |
| :--- | :--- | :--- |
| **Query Iterations** | 50 | 100% success rate (50/50) |
| **With Observability (Mean)** | 2.67 ms | Includes JSON formatting, ContextVar, spans, Prometheus recording |
| **With Observability (p95)** | 4.22 ms | Bounded upper tail |
| **Raw Pipeline Baseline (Mean)** | 0.71 ms | Pure compute without FastAPI/observability |
| **Raw Pipeline Baseline (p95)** | 0.94 ms | Pure compute baseline |
| **Absolute Overhead** | **1.96 ms** | Combined cost of async dispatch + JSON logging + metrics + spans |
| **Impact on Real LLM Inference** | **< 0.4%** | Negligible compared to typical Gemma inference latency (500–30,000ms) |

---

## 9. Test Suite & Full Regression Breakdown

### 9.1 Phase 4P Dedicated Suite (`tests/test_phase_4p_observability.py`) — 18/18 PASSED
1. `test_request_produces_structured_log`: **PASSED**
2. `test_request_id_is_generated`: **PASSED**
3. `test_request_id_propagates_correctly`: **PASSED**
4. `test_supplied_safe_request_id_is_propagated`: **PASSED**
5. `test_logs_contain_required_fields`: **PASSED**
6. `test_credentials_are_not_logged`: **PASSED**
7. `test_sensitive_document_contents_are_not_logged`: **PASSED**
8. `test_metrics_endpoint_exists`: **PASSED**
9. `test_request_counter_increments`: **PASSED**
10. `test_error_counter_increments`: **PASSED**
11. `test_timeout_metric_increments`: **PASSED**
12. `test_capacity_metric_increments`: **PASSED**
13. `test_latency_metric_records`: **PASSED**
14. `test_stage_latency_labels_are_bounded`: **PASSED**
15. `test_no_raw_query_appears_in_metric_labels`: **PASSED**
16. `test_no_arbitrary_tenant_user_ids_appear_in_metric_labels`: **PASSED**
17. `test_tracing_instrumentation_does_not_alter_query_result`: **PASSED**
18. `test_production_flags_remain_frozen`: **PASSED**

### 9.2 Full Regression Suite Across All Phases — 86/86 PASSED (0 Regressions)
- `tests/test_phase_4p_observability.py`: 18 passed
- `tests/test_phase_4o_resilience.py`: 15 passed
- `tests/test_phase_4n_packaging.py`: 8 passed
- `tests/test_phase_4m_api_service.py`: 16 passed
- `tests/test_phase_4m_auth_fail_closed.py`: 11 passed
- `tests/test_phase_4k_g_b_promotion.py`: 3 passed
- `tests/test_phase_4k_f_b_security_redteam.py`: 5 passed
- `tests/test_phase_4k_e_b_only.py`: 5 passed
- `tests/test_canonical_baseline.py`: 5 passed
- **Total**: **86 passed, 0 failed, 0 regressions.**

---

## 10. Rollback Procedure

If observability instrumentation must be rolled back:
1. In `src/novastack/service/api.py`, remove `/metrics` route, remove `log_event` and `tracer.start_span` calls, and revert `execute_query` to its Phase 4O timing structure.
2. In `src/novastack/service/schemas.py`, remove the optional `request_id` fields.
3. Remove `src/novastack/observability/`.
4. Run `pytest tests/test_phase_4o_resilience.py tests/test_phase_4m_api_service.py` to confirm Phase 4O operational parity.

---

## 11. Conclusion

Phase 4P successfully establishes structured JSON logging, correlation ID tracking, Prometheus metrics, and in-process tracing with zero external dependencies, zero privacy leaks, and zero regression across the canonical baseline.
