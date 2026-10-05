# ATLAS Phase 4M: API Service & Fail-Closed Security Hardening

## Executive Summary

Pursuant to the ATLAS CTO Directive, **Phase 4M: API Service & Fail-Closed Security Hardening** has been implemented and validated. The ATLAS enterprise search and answering engine has been converted into a production-style, low-overhead HTTP service built on FastAPI while transitioning the authorization boundary from fail-open to strictly **fail-closed**.

All production configuration defaults remain frozen:
- `enable_boundary_stitching = False` (Mechanism A: OFF)
- `enable_query_aware_authority = True` (Mechanism B: ON, certified production default)
- `enable_event_bundling = False` (Mechanism C: OFF)

Across the complete test suite (Phase 4M API Service, Phase 4M Fail-Closed Authorization, Phase 4K-G Promotion, Phase 4K-F Red-Team, Phase 4K-E Validation, and Canonical Baseline Freeze), **45 out of 45 tests passed with 0 regressions**.

---

## 1. Architecture of API Service

The service layer is implemented in `src/novastack/service/` with clean modular boundaries:

```
src/novastack/service/
    __init__.py          # Package exports (app, create_app, AtlasServicePipeline, schemas)
    api.py               # FastAPI application, lifecycle management, route endpoints, exception handlers
    schemas.py           # Pydantic v2 request/response data models with fail-closed validation
```

### 1.1 Service Endpoints

| Method | Path | Purpose | Success Status | Error Codes |
|---|---|---|---|---|
| `GET` | `/healthz` | Process liveness probe | `200 OK` (`{"status": "ok"}`) | — |
| `GET` | `/ready` | Component readiness check (BM25, Dense, Reranker, Generator) | `200 OK` (`{"status": "ready", "components": {...}}`) | `503 Service Unavailable` |
| `POST` | `/query` | Validated grounded query execution under caller context | `200 OK` (Structured `QueryResponse`) | `422 Unprocessable Entity`, `503 Service Unavailable`, `500 Internal Server Error` |

### 1.2 Data Schemas (`schemas.py`)

- **`CallerContext`**:
  - `tenant_id`: `str` (**mandatory, non-empty**; whitespace-only rejected).
  - `user_id`: `Optional[str]` (caller identity).
  - `user_role`: `Optional[str]` (primary role credential).
  - `roles`: `Optional[list[str]]` (role list fallback).
  - `user_department`: `Optional[str]` (primary department credential).
  - `departments`: `Optional[list[str]]` (department list fallback).
  - `effective_role` and `effective_department` properties dynamically resolve credentials.
- **`QueryRequest`**:
  - `query`: `str` (**mandatory, non-empty**; whitespace-only rejected via Pydantic validator).
  - `user_context`: `CallerContext` (mandatory security context).
  - `evaluation_id`: `Optional[str]` (correlation / evaluation identifier).
- **`QueryResponse`**:
  - `answer_id`: `str` (unique answer identifier).
  - `query`: `str` (normalized query string).
  - `answer_text`: `str` (grounded answer or principled abstention).
  - `answer_status`: `str` (`answered`, `partially_answered`, `abstained`).
  - `citations`: `list[dict[str, Any]]` (structured citation metadata including `raw_tag`, `document_id`, `chunk_id`, `status`).
  - `abstention_reason`: `Optional[str]` (reason for abstention if applicable).
  - `latency_ms`: `float` (total server-side processing latency).
- **`HealthResponse`**: `{"status": "ok"}`
- **`ReadyResponse`**: `{"status": "ready", "components": {"bm25": bool, "dense": bool, "reranker": bool, "generator": bool}}`
- **`ErrorResponse`**: `{"detail": str, "error_type": Optional[str], "answer_status": "error"}`

### 1.3 Concurrency & Isolation Invariants

The service guarantees multi-tenant safety and state isolation:
1. **Stateless Request Execution**: No mutable state is shared across requests. All retrieval buffers, candidates, evidence packages, and generation contexts are allocated strictly per request.
2. **Explicit Context Propagation**: The caller context (`tenant_id`, `user_role`, `user_department`, `user_id`) is explicitly passed down the call stack to retrieval filtering and the Stage 2 evidence authorization gate.
3. **No Shared Cache of Restricted Records**: No global cross-request caches exist that could inadvertently serve unauthorized documents to subsequent requests.

### 1.4 Error Handling & Information Leakage Prevention

- **Schema Validation Errors**: Intercepted by FastAPI `RequestValidationError` handler, returning `422 Unprocessable Entity` with structured JSON.
- **Service Unreadiness**: If any of the four core components (BM25, Dense, Reranker, Generator) fail to initialize or are unready, `/ready` and `/query` return `503 Service Unavailable`.
- **Top-Level Exception Sanitization**: All unhandled internal runtime exceptions are intercepted by a global FastAPI exception handler returning `500 Internal Server Error` with `ErrorResponse`. **Internal stack traces, code frames, and sensitive connection strings are never leaked to the client**.

---

## 2. Authorization Model Changes (Fail-Closed Transition)

### 2.1 The Fail-Open Flaw (Phase 4L Audit Finding)

Prior to Phase 4M, `src/novastack/evidence_resolution.py` Stage 2 authorization gate evaluated access constraints as follows:

```python
# PRE-4M (VULNERABLE: FAIL-OPEN)
if perms.allowed_roles and user_role is not None:
    if user_role not in perms.allowed_roles:
        is_auth = False

if perms.allowed_departments and user_department is not None:
    if user_department not in perms.allowed_departments:
        is_auth = False

if perms.allowed_user_ids and user_id is not None:
    if user_id not in perms.allowed_user_ids:
        is_auth = False
```

Under this logic, an unauthenticated caller or a caller with `user_role = None` would bypass the condition `user_role is not None`, erroneously granting access to role-restricted confidential documents.

### 2.2 The Fail-Closed Fix (`evidence_resolution.py`)

Stage 2 has been updated to enforce strict **fail-closed** semantics:

```python
# POST-4M (SECURE: FAIL-CLOSED)
# 3. Role Restriction Check (Fail-Closed)
perms = item.permissions
if perms.allowed_roles:
    if user_role is None:
        is_auth = False
        auth_reasons.append(f"role_unauthorized:missing_user_role_required_in_{perms.allowed_roles}")
    elif user_role not in perms.allowed_roles:
        is_auth = False
        auth_reasons.append(f"role_unauthorized:user_role='{user_role}'_not_in_{perms.allowed_roles}")

# 4. Department Restriction Check (Fail-Closed)
if perms.allowed_departments:
    if user_department is None:
        is_auth = False
        auth_reasons.append(f"department_unauthorized:missing_user_dept_required_in_{perms.allowed_departments}")
    elif user_department not in perms.allowed_departments:
        is_auth = False
        auth_reasons.append(f"department_unauthorized:user_dept='{user_department}'_not_in_{perms.allowed_departments}")

# 5. User ACL Restriction Check (Fail-Closed)
if perms.allowed_user_ids:
    if user_id is None:
        is_auth = False
        auth_reasons.append(f"user_acl_unauthorized:missing_user_id_required_in_{perms.allowed_user_ids}")
    elif user_id not in perms.allowed_user_ids:
        is_auth = False
        auth_reasons.append(f"user_acl_unauthorized:user_id='{user_id}'_not_in_{perms.allowed_user_ids}")
```

### 2.3 Mandatory Tenant Requirement at API Boundary

- The API request model (`CallerContext`) requires `tenant_id` as a mandatory, non-empty field.
- Requests omitting `tenant_id` or providing empty/whitespace strings are immediately rejected with `422 Unprocessable Entity`.
- No silent fallback to default tenants occurs at the service boundary.

---

## 3. Verification & Security Test Results

The suite was executed via `pytest`:

```
tests/test_phase_4m_api_service.py ................                      [ 35%]
tests/test_phase_4m_auth_fail_closed.py ...........                      [ 60%]
tests/test_phase_4k_g_b_promotion.py ...                                 [ 66%]
tests/test_phase_4k_f_b_security_redteam.py .....                        [ 77%]
tests/test_phase_4k_e_b_only.py .....                                    [ 88%]
tests/test_canonical_baseline.py .....                                   [100%]

======================= 45 passed, 3 warnings in 3.28s ========================
```

### 3.1 Breakdown of Test Results

| Test Suite | Total Tests | Passed | Failed | Key Invariants Verified |
|---|---|---|---|---|
| `test_phase_4m_api_service.py` | 16 | 16 | 0 | `/healthz` (200), `/ready` (200/503), `/query` validation, empty query rejection (422), missing tenant rejection (422), role/dept/user enforcement at API boundary, cross-tenant denial, 500 error stack trace masking. |
| `test_phase_4m_auth_fail_closed.py` | 11 | 11 | 0 | Fail-closed role checks (none/wrong/match), fail-closed dept checks (none/wrong/match), fail-closed user ACL checks (none/wrong/match), cross-tenant isolation under all contexts, multi-constraint conjuncts. |
| `test_phase_4k_g_b_promotion.py` | 3 | 3 | 0 | Production flag defaults (`A=False, B=True, C=False`), EvidenceResolver default instance initialization, security barriers executing strictly before Mechanism B. |
| `test_phase_4k_f_b_security_redteam.py` | 5 | 5 | 0 | 40 security red-team vectors passed, 0 unauthorized exposure, 0 cross-tenant leakage, 0 adversarial bypass. |
| `test_phase_4k_e_b_only.py` | 5 | 5 | 0 | Full 120-case canonical benchmark verified with Mechanism B only, 54/101 positive success (+1 recovery over baseline), 0 regressions. |
| `test_canonical_baseline.py` | 5 | 5 | 0 | Canonical 120-case frozen baseline integrity, 101 positive, 19 negative, certified metric definitions. |
| **Total** | **45** | **45** | **0** | **100% Pass Rate, 0 Regressions** |

---

## 4. Architectural Boundaries Maintained

Per Part F of the CTO Directive, architectural boundaries were strictly respected:
- **No external relational databases** (PostgreSQL, SQLite, MySQL) added.
- **No Redis / distributed caching** added.
- **No message brokers** (Kafka, RabbitMQ) added.
- **No vector DBs** (Pinecone, Qdrant, Milvus) added.
- **No Prometheus / metrics exporters** added.
- **No distributed tracing** (OpenTelemetry) added.
- **No circuit breaker frameworks** added.
- **No pipeline rewrites**: Retrieval, metadata reranking, evidence assembly, and answer generation mechanisms were preserved in their frozen, certified state.

---

## 5. Remaining Production Gaps

While Phase 4M successfully hardens the service boundary and authorization gate, the following operational dimensions (first identified in Phase 4L) remain open for future phases:

1. **Authentication Gateway / Identity Provider**: Phase 4M accepts `CallerContext` as trusted JSON from the ingress caller. Production deployment will require an API gateway or middleware validating JWT tokens / mTLS certificates to authenticate callers before constructing `CallerContext`.
2. **Rate Limiting & Throttling**: The service currently lacks token-bucket or sliding-window rate limiters per tenant.
3. **Persistent Query / Audit Logging**: Request/response execution is logged to standard Python logging; structured tamper-proof audit trails for compliance remain pending.
4. **Streaming Answers**: Current `/query` returns synchronous buffered JSON; streaming generation tokens over Server-Sent Events (SSE) is not yet implemented.

---

## 6. CTO Stop Condition

**Phase 4M is COMPLETE.**
In accordance with the CTO directive:
- Feature development is STOPPED.
- Phase 4N has NOT been started.
