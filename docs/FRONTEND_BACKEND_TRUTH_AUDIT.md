# ATLAS — Frontend / Backend Truth Audit

**Project**: ATLAS — Evidence-Grounded Enterprise Search Platform  
**Enterprise**: NovaStack  
**Role**: Principal Frontend Engineer + Product Designer + AI Systems Integration Engineer  
**Date**: October 2026  
**Status**: Authoritative Truth Audit  

---

## 1. Executive Summary & Audit Philosophy

The primary objective of ATLAS is to be **truthful, defensible, and enterprise-grade**. Every value presented in the user interface must have a distinct, verifiable, and documented **Source of Truth**. 

### Classification Taxonomy
Every piece of data rendered in the UI is classified under one of the following seven truth tiers:
1. `LIVE_BACKEND`: Dynamically retrieved from the running FastAPI service (`POST /query`).
2. `LIVE_HEALTH`: Dynamically queried from `/healthz` or `/ready`.
3. `LIVE_METRICS`: Dynamically parsed from `/metrics` Prometheus exposition.
4. `CANONICAL_EVALUATION`: Derived from frozen, certified benchmark artifacts (`artifacts/canonical_evaluation_manifest.json`).
5. `DEMO_FIXTURE`: Pre-computed deterministic scenario fixture matching certified offline evaluation scripts (`scripts/demo_scenario.py`).
6. `UI_STATE`: Ephemeral client-side browser state (active tab, search input, drawer open/close).
7. `SIMULATED`: Synthetically generated client-side behavior (e.g., artificial timers or fake pipeline step animations). **MUST NOT be represented as live backend telemetry.**

---

## 2. Comprehensive Component Audit

### Section A: Header & Navigation Bar

| Element | UI Representation | Source of Truth | Audit Finding & Truth Assessment |
|---|---|---|---|
| **Platform Name & Logo** | `ATLAS` | `UI_STATE` | Branding identifier. |
| **System Version** | `0.4.14-rc1+h5.1` | `CANONICAL_EVALUATION` | Sourced from `pyproject.toml` and release freeze documentation. Matches baseline release `0.4.14-rc1` with `H5.1` shadow candidate. |
| **API Connection Pill** | `Connected: Live FastAPI` vs `DEMO MODE` | `LIVE_HEALTH` / `UI_STATE` | **Defect Identified**: Previously displayed ambiguous *"Engine: Verified Standby"*. Must be refactored to explicitly distinguish `● ATLAS LIVE` (FastAPI reachable), `● DEMO MODE` (offline replay), or `● BACKEND OFFLINE` (unreachable). |
| **Canary Indicator** | `0.0% (Standby)` | `CANONICAL_EVALUATION` / `LIVE_BACKEND` | Production canary traffic is strictly held at `0.0%`. Verified by `CanaryRouter` configuration in backend. |
| **Active Identity Pill** | `Zara Reyes (engineer @ Engineering)` | `UI_STATE` | Displays currently selected persona context. |

---

### Section B: Persona & Identity Switcher Modal

**Critical Principle**: **`DISPLAY TITLE != AUTHORIZATION ROLE`**.

In enterprise RBAC/ABAC, an employee's organizational title (e.g., "Incident Commander") is a business designation, while their authorization role (`engineer`) governs ACL evaluation at the Pre-Evidence security gate. The backend authorizes access based on `CallerContext.user_role` and `CallerContext.roles`, never display titles.

| Persona Name | User ID (`sub`) | Tenant ID (`tenant_id`) | Auth Role (`roles`) | Auth Dept (`departments`) | Display Title | Source of Truth |
|---|---|---|---|---|---|---|
| **Zara Reyes** | `USR-NS-0008` | `TENANT-NOVASTACK` | `engineer` | `Engineering` | Incident Commander | `DEMO_FIXTURE` / `CANONICAL_EVALUATION` |
| **Alex Chen** | `USR-ENG-42` | `TENANT-NOVASTACK` | `engineer` | `Engineering` | Platform Engineer | `DEMO_FIXTURE` / `CANONICAL_EVALUATION` |
| **Alice Vance** | `USR-ACME-01` | `TENANT-ACME-EXTERNAL` | `auditor` | `Finance` | Foreign Tenant Auditor | `DEMO_FIXTURE` / `CANONICAL_EVALUATION` |
| **Sam Taylor** | `USR-INTERN-01` | `TENANT-NOVASTACK` | `intern` | `Engineering` | Engineering Intern | `DEMO_FIXTURE` / `CANONICAL_EVALUATION` |

**Audit Finding**:
The UI clearly presents both concepts. However, the modal must explicitly label `Auth Role: engineer` alongside `Display Title: Incident Commander` to prevent any misconception that the backend matches arbitrary title strings.

---

### Section C: JWT Cryptographic Token Audit

> [!CAUTION]
> **P0 / SECURITY ARCHITECTURE AUDIT FINDING**  
> In the initial prototype UI, the browser client JavaScript contained the signing secret:  
> `const secretStr = "atlas-demo-secret-key-32-bytes-long-2026";`  
> In symmetric HS256 authentication, placing the shared HMAC secret in browser client code allows any client to mint arbitrary tokens for any tenant, user, or role, violating zero-trust isolation.

#### Analysis of Local Demo Solutions
1. **Option 1: Backend-Issued Demo Tokens**: Requires a `/auth/token` endpoint. *Status*: Backend is frozen (`BACKEND = FREEZE`), so adding new endpoints without explicit approval is prohibited.
2. **Option 2: Pre-Generated Isolated Demo Tokens**: The UI embeds read-only pre-minted tokens for the 4 specific demo personas signed with a generous demo expiration (`exp: 2030`). The browser possesses **zero signing keys** and **zero token minting capability**.
3. **Option 3: Dedicated Demo Identity Endpoint in UI Launcher**: `scripts/serve_ui.py` generates demo tokens on local startup.
4. **Option 4: Asymmetric (RS256 / ES256) Architecture**: For future production releases where a separate Auth0/OIDC IdP signs tokens with private keys and ATLAS verifies with public keys.

#### Prescribed Fix:
Implement **Option 2** (Pre-generated isolated tokens) paired with **Option 3** in `serve_ui.py`.
- **Remove all raw signing secrets from browser JavaScript completely**.
- The browser contains only the immutable, pre-signed tokens for Zara, Alex, Alice, and Sam.
- Mask the token preview in the UI: `eyJhbGciOiJIUzI1NiIs... *********************** ...xyz` so full credentials are never exposed in UI presentations or screen recordings.

---

### Section D: Enterprise Search & Request Dispatch

| Request Element | Schema Definition | Value Origin | Verification |
|---|---|---|---|
| **Endpoint** | `POST http://127.0.0.1:8000/query` | `LIVE_BACKEND` | Exact route defined in `src/novastack/service/api.py`. |
| **Headers** | `Authorization: Bearer <jwt>`<br>`Content-Type: application/json` | `LIVE_BACKEND` | Validated by `JwtIdentityVerifier.verify_authorization_header`. |
| **Query String** | `QueryRequest.query` (str) | `UI_STATE` | Non-empty validation enforced. |
| **User Context** | `QueryRequest.user_context` (CallerContext) | `UI_STATE` matching verified JWT | Cross-checked against verified token claims via `assert_context_matches_identity`. |

**Audit Finding**:
The request dispatch complies with the backend contract. When the FastAPI service is active, real HTTP round-trips occur, passing cryptographic authentication and context validation.

---

### Section E: Pipeline Progress Visualization

**Audit Finding**:
- The existing UI featured an 8-pill animated tracker (`JWT Auth`, `Context ACL`, `Query Understanding`, `Hybrid Retrieval`, `Reranking`, `Evidence Gate`, `LLM Generation`, `C2 Validator`) driven by client-side `setTimeout()` calls.
- **Truth Assessment**: The backend `/query` endpoint is a synchronous REST API. While internal stage latencies are logged to Prometheus (`metrics.record_stage_latency`) and OpenTelemetry, the backend **does not stream real-time pipeline events** over HTTP.
- **Correction**: Client-side simulated animation must NOT be disguised as real execution telemetry. The UI must replace artificial stage timers with:
  1. An honest client-side execution spinner during query flight: *"Processing ATLAS Request Lifecycle..."*
  2. A truthful post-execution **Server Processing Summary**:
     - Total Latency (`latency_ms`) — `LIVE_BACKEND`
     - LLM Inference Time (`generation_latency_ms`) — `LIVE_BACKEND`
     - Retrieval, Reranking & Verification Time (`latency_ms - generation_latency_ms`) — `LIVE_BACKEND`
     - Canary Variant & Bucket (`canary_variant`, `canary_bucket`) — `LIVE_BACKEND`
     - Index Generation Leased (`index_generation_id`) — `LIVE_BACKEND`

---

### Section F: Answer & Citation Rendering

| Element | Field in QueryResponse | Source of Truth | Truth Status |
|---|---|---|---|
| **Answer Status** | `answer_status` (`answered` / `abstained`) | `LIVE_BACKEND` / `DEMO_FIXTURE` | Truthful backend response enum. |
| **Answer Text** | `answer_text` | `LIVE_BACKEND` / `DEMO_FIXTURE` | Truthful response from GroundedAnswerGenerator. |
| **Citations** | `citations` (list of citation objects) | `LIVE_BACKEND` / `DEMO_FIXTURE` | Structured citations verified by C2 validator. |
| **Abstention Reason** | `abstention_reason` | `LIVE_BACKEND` / `DEMO_FIXTURE` | Null on success; descriptive reason on abstention. |
| **Total Latency** | `latency_ms` | `LIVE_BACKEND` / `DEMO_FIXTURE` | Wall-clock execution time in milliseconds. |

**Audit Finding**:
When running live, answers, citations, and abstention reasons originate from the backend response. When in Demo Mode, data originates from `scripts/demo_scenario.py` and is clearly badged as `[DEMO MODE: CANONICAL REPLAY]`.

---

### Section G: Evidence Drawer

The Evidence Drawer provides auditability for retrieval and grounding decisions.

| Data Item | Public API Behavior (`/query`) | Demo Replay Behavior | Truth Classification |
|---|---|---|---|
| **Selected Evidence Citations** | Returned in `QueryResponse.citations` (`citation_id`, `document_id`, `chunk_id`, `raw_tag`, `status`, `confidence_score`). | Full `EvidenceItem` details with text snippet and authority level. | `LIVE_BACKEND` (when live) / `DEMO_FIXTURE` (in demo replay). |
| **Excluded / Filtered Items** | **Intentionally not returned** over public API for security & payload hygiene (prevents leaking quarantined text). | Full audit trail of excluded candidates (`DOC-ADV-PSN-0001`, `DOC-TENANT-ACME-SECRET`) with rejection reasons. | `DEMO_FIXTURE` (derived from `scripts/demo_scenario.py`). |

**Audit Finding**:
The UI must be transparent:
- In `LIVE` mode, the drawer displays citations returned by `/query`, with an explicit notice: *"Excluded candidates quarantined server-side; not transmitted over public API for security"*.
- In `DEMO MODE`, the drawer displays the full diagnostic evidence package from `scripts/demo_scenario.py`, clearly marked as `Source: DEMO FIXTURE`.

---

### Section H: Security & Invariants View

| Security Category | Backend Enforcement Source | UI Truth Classification |
|---|---|---|
| **7.1 Authentication Boundary** | `JwtIdentityVerifier` (`src/novastack/service/identity.py`) | `CERTIFIED` (Red-Team Suite: 28/28 PASS) |
| **7.2 Authorization & Context** | `assert_context_matches_identity` | `CERTIFIED` (Fail-Closed Suite: 16/16 PASS) |
| **7.3 Cross-Tenant Isolation** | Pre-Evidence Security Gate (`EvidenceResolver`) | `CERTIFIED` (Multi-Tenant Suite: 100% Isolation) |
| **7.4 Direct Prompt Injection** | System Instruction Demarcation Rule 4 | `CERTIFIED` (Adversarial Suite: 0 Escapes) |
| **7.5 Indirect Prompt Injection** | Untrusted `<evidence_data>` XML Wrapper | `CERTIFIED` (Adversarial Suite: 0 Injections) |
| **7.6 Retrieval Poisoning** | Metadata Authority Override | `CERTIFIED` (Poisoning Suite: 100% Quarantined) |
| **7.7 Citation Leakage** | C2 Deterministic Validator | `CERTIFIED` (C2 Suite: 100% Grounded) |
| **7.8 Secret Exfiltration** | Structured Logger Redaction Filter | `CERTIFIED` (Telemetry Suite: 0 Secrets Logged) |
| **7.9 Metadata Attacks** | Verified Token Claims Override Request Body | `CERTIFIED` (Identity Suite: Server Authority Wins) |

**Audit Finding**:
The 9 attack categories must **not** be presented as "currently executing live tests on every page load". They are `CERTIFIED SECURITY TEST RESULTS` verified during canonical evaluations. The UI must explicitly separate **Live Boundary Health** (e.g., Bearer auth active) from **Certified Security Proofs**.

---

### Section I: SEC-OPS Operational Boundaries

| Boundary Check | Authoritative Status | Rationale & Truth Boundary |
|---|---|---|
| **SEC-OPS-02 Host Exposure** | `VERIFIED` | FastAPI bound strictly to `127.0.0.1:8000`, container to `127.0.0.1:8001`, Ollama to `127.0.0.1:11434`. Wildcard `0.0.0.0` prohibited. |
| **SEC-OPS-03 Remote Ingress** | `UNVERIFIED` | Single-machine testing environment; no independent physical machine on LAN was available to perform external port ingress probes. Must remain recorded as `UNVERIFIED`. |
| **Production Canary Traffic** | `0.0% (Standby)` | Canary router is integrated and verified, but production traffic is safely held at 0.0% in accordance with release gate criteria. |

---

### Section J: System Health & Readiness

| Health Component | Live Probe Endpoint | Response Attribute | Truth Tier |
|---|---|---|---|
| **Process Liveness** | `GET /healthz` | `{"status": "ok"}` | `LIVE_HEALTH` |
| **Component Readiness** | `GET /ready` | `components.bm25`, `components.dense`, `components.reranker`, `components.generator`, `components.authentication` | `LIVE_HEALTH` |
| **Active Index Generation** | `GET /ready` | `active_generation_id` | `LIVE_HEALTH` |
| **Latency Percentiles** | `artifacts/canonical_evaluation_manifest.json` | p95 QU: 1.29ms, p95 Hybrid: 57.71ms, p95 Reranker: 0.38ms, Mean LLM: 13.36s | `CANONICAL_EVALUATION` |

**Audit Finding**:
The UI must explicitly divide System Health into two distinct panels:
1. **Live Subsystem Probes** (fed by `/healthz` and `/ready`).
2. **Certified Benchmark Performance Metrics** (fed by canonical benchmark evaluation data).

---

### Section K: History & Audit Trail

| Field | Source | Security & Privacy Check |
|---|---|---|
| Query Text | `UI_STATE` | Safe plain text. |
| Timestamp | Client Date | Standard timestamp. |
| Persona Name | `UI_STATE` | Display name only. |
| Tenant ID | `UI_STATE` | Non-sensitive tenant identifier. |
| Latency | `LIVE_BACKEND` / `DEMO_FIXTURE` | Execution duration in ms. |
| Citations Count | `LIVE_BACKEND` / `DEMO_FIXTURE` | Integer count. |
| Credentials / Tokens | **EXCLUDED** | **Audit Pass**: Zero secrets, tokens, or passwords stored in session history. |

---

## 3. Summary of Identified Defects & Required Fixes

1. **P0 (Security)**: Remove hardcoded `ATLAS_AUTH_HS256_SECRET` from client JavaScript. Use pre-generated isolated demo tokens for each persona with token masking in the UI preview.
2. **P1 (Truthfulness)**: Eliminate artificial `setTimeout` pipeline animations. Replace with honest request spinner during transit and truthful post-query server processing summary.
3. **P1 (Status Model)**: Refactor ambiguous status badges into explicit truth tiers: `● ATLAS LIVE`, `● DEMO MODE`, or `● BACKEND OFFLINE`.
4. **P1 (Data Separation)**: Clearly demarcate live subsystem probes from certified benchmark percentiles in the System Health view.
5. **P2 (UX & Clarity)**: In the Persona Modal, explicitly differentiate `Display Title` from `Authorization Role`.
6. **P2 (Accessibility)**: Ensure keyboard escape closes modals and drawers, ARIA labels are comprehensive, and contrast ratios meet enterprise WCAG standards.
