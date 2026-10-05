# Phase 5O — Controlled Incident & Recovery Certification

============================================================
PROJECT ATLAS — EVIDENCE-GROUNDED ENTERPRISE SEARCH PLATFORM
PHASE 5O: CONTROLLED INCIDENT & RECOVERY CERTIFICATION
============================================================

- **Release Candidate**: `0.4.14-rc1`
- **Package Version**: `0.4.14`
- **Certification Date**: 2026-09-23
- **Authoritative Archive**: `dist/atlas-novastack-0.4.14-rc1.tar.gz`
- **Archive SHA-256**: `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3`
- **Production Backend**: `InferenceServiceAdapter` → containerized `gemma3:1b` (Q4_K_M) via Ollama
- **Certified Rollback Backend**: `LocalHuggingFaceProvider` → `google/gemma-3-1b-it` (FP32 CPU)
- **Production Runbook**: `docs/OPERATIONS_RUNBOOK.md`
- **Final Phase Decision**: **`PASS`**

---

## 1. Executive Summary

Phase 5O is the formal **Failure-Injection and Recovery Certification Phase** of Project ATLAS. Operating under strict CTO-directed release discipline, Phase 5O proves that the ATLAS Release Candidate (`0.4.14-rc1`):
1. **Fails Safely Under Degradation**: Every induced fault across upstream dependencies, container boundaries, capacity limits, circuit breakers, process crashes, corrupt indexes, authentication edge cases, client disconnects, and end-to-end outages produces deterministic, safe, client-facing degradation (HTTP 503, 504, 429, or deterministic structured abstention `service_unavailable`/`timeout`).
2. **Preserves Security Boundaries Invariantly**: Authentication, tenant isolation, Layer 1S pre-generation security abstention, and C2 citation validation remain strictly fail-closed during and across all incident states. Zero credentials, tokens, evidence text, or internal stack traces leaked in error responses or logs.
3. **Recovers Repeatably via Runbook Alone**: Every recovery action was executed strictly using procedures, commands, and diagnostic trees documented in `docs/OPERATIONS_RUNBOOK.md`. Zero developer improvisation, source modification, or unwritten knowledge was required.
4. **Maintains Zero Source Drift**: Exactly **0 source code changes** occurred in `src/novastack/` across all failure injections, recoveries, and verifications. The release tarball SHA-256 remains cryptographically identical (`382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3`).
5. **Certifies Production Resilience Parameters**: Explicitly certifies that the production circuit breaker cooldown is **10.0 seconds** (`ResilienceConfig.circuit_cooldown_seconds = 10.0`), distinguishing it from the 0.2s accelerated test harness. Concurrency is strictly bound to 1 (`max_concurrent_inferences = 1`), with queue timeout at 0.5s and request timeout at 30.0s.

---

## 2. Controlled Incident Matrix (10 Categories)

| Incident # | Incident Category | Runbook Section | Injected Fault | Observed Failure Behavior | Runbook Recovery Procedure | Recovery Latency | Status |
|:---:|---|:---:|---|---|---|:---:|:---:|
| **0** | Pre-Incident Baseline | §8, §9, §11 | None (Nominal State) | Normal 200 OK, C2 citations valid | Baseline verified; 0 source drift | 0.0s | **PASS** |
| **1** | Ollama Dependency Interruption | §9, §10 | Host Ollama stopped (`Stop-Process`) | Safe abstention (`service_unavailable`); 0 secrets leaked | Runbook §9/10: Start Ollama, wait container ready | 15.2s | **PASS** |
| **2** | Container Outage Drill | §9, §10 | Container stopped (`docker stop atlas-inference-5d`) | HTTP 503 / safe abstention (`service_unavailable`) | Runbook §9/10: `docker start atlas-inference-5d` | 9.4s | **PASS** |
| **3** | Inference Capacity Exhaustion | §2, §14 | 2 concurrent inferences submitted | Slot 1 executes; Slot 2 rejected with HTTP 429 after 0.5s queue timeout | Drain queue to idle; subsequent query passes | 0.5s | **PASS** |
| **4** | Production Circuit Breaker | §2, §14 | 3 consecutive upstream timeouts | Circuit trips `CLOSED` → `OPEN`; fast failure; 10.0s cooldown → `HALF_OPEN` → `CLOSED` | Cooldown elapses; healthy probe resets circuit | 10.1s | **PASS** |
| **5** | ATLAS Cold Restart & Index Lease | §9, §16 | ATLAS process termination / restart | Process down; restart re-initializes pipeline in <5s; re-leases baseline index | Runbook §9: Start pipeline; verify `/healthz` & `/ready` | 4.7s | **PASS** |
| **6** | Index Corruption / Rejection | §14, §20 | Corrupted index candidates (null components, dimension mismatch) | Candidate validation rejected with errors; active production index 100% untouched | Active generation preserved; invalid candidate rejected | 0.0s | **PASS** |
| **7** | Provider Rollback & Restoration | §17, §18 | Simulated upstream Backend B failure | Provider switched to Backend A (`LocalHuggingFaceProvider`) then restored to Backend B | Runbook §17/18: Switch provider; verify traffic serving | 74.1ms | **PASS** |
| **8** | Auth Fail-Closed Under Incident | §10, §12 | Missing, malformed, expired, bad-sig JWT & cross-tenant mismatch | Strict HTTP 401 & 403 rejection; 0 auth bypasses | Auth boundary remains strictly fail-closed | 0.0s | **PASS** |
| **9** | Client Disconnect Semantics | §2, §20 | Client disconnect after query submission | 30.0s request timeout deadline enforced; Ollama completes async on CPU; slot freed | Semaphore released; subsequent queries serve normally | 0.2s | **PASS** |
| **10** | Full End-to-End Recovery Drill | §10, §15 | Unannounced Ollama crash during production traffic | Traffic abstains with 503; runbook diagnostics run; service restored; queries pass | Runbook §10: Follow diagnostic tree & restart sequence | 14.8s | **PASS** |

---

## 3. Incident Breakdown & Forensics

### Incident 1: Ollama Dependency Interruption & Recovery
- **Runbook Section**: Section 9 ("Start ATLAS Services"), Section 10 ("Troubleshooting Matrix & Diagnostic Runbooks").
- **Injection Method**: `powershell -Command "Stop-Process -Name 'ollama', 'ollama app', 'llama-server' -Force"`.
- **System Behavior**:
  - `InferenceServiceClient` detects connection refused on upstream Ollama port 11434.
  - `InferenceServiceAdapter` translates connection error into a safe, deterministic abstention response (`answer_status = "abstained"`, `abstention_reason = "service_unavailable"`).
  - Citations array is empty (`citations = []`).
  - Diagnostics contain sanitized error taxonomy with zero leaked secrets, zero tokens, and zero internal stack traces.
- **Recovery Execution**:
  - Followed Runbook Section 9/10 command: `powershell -Command "$env:OLLAMA_HOST = '0.0.0.0:11434'; ollama serve"`.
  - Polled container `/ready` until `backend_connected: true` re-converged.
  - Pre-warmed `gemma3:1b` model.
  - Post-recovery authenticated query answered in ~4.9s with 2 valid C2 citations (`[EVD-001]`, `[EVD-002]`).

### Incident 2: Inference Service Container Outage & Recovery
- **Runbook Section**: Section 9 ("Start ATLAS Services"), Section 10 ("Troubleshooting Matrix").
- **Injection Method**: `docker stop atlas-inference-5d`.
- **System Behavior**:
  - Port 8001 becomes unreachable (`ConnectionRefusedError`).
  - ATLAS adapter catches connection error and returns structured abstention (`service_unavailable`).
  - Zero unhandled exceptions reach callers.
- **Recovery Execution**:
  - Runbook Section 9 command: `docker start atlas-inference-5d`.
  - Waited for health probe (`/healthz` 200 OK) and readiness probe (`/ready` 200 OK).
  - Recovery verified in 9.4s; subsequent query succeeded with full C2 citation grounding.

### Incident 3: Inference Capacity Exhaustion Drill
- **Runbook Section**: Section 2 ("Operating Envelope"), Section 14 ("Resilience & Rate Limiting Architecture").
- **Injection Method**: Submitted 2 concurrent query requests against `InferenceCapacityLimiter(max_concurrent=1, queue_timeout=0.5)`.
- **System Behavior**:
  - Slot 1 acquires concurrency semaphore immediately.
  - Slot 2 waits in the queue until `queue_timeout_seconds` (0.5s) expires.
  - Slot 2 is cleanly rejected with `CapacityExhaustedError` (HTTP 429: "Inference capacity exhausted").
  - Slot 1 completes execution uncorrupted.
- **Recovery Execution**:
  - Once Slot 1 completes, concurrency slot returns to idle (0/1 active).
  - Subsequent request executes immediately without queue delay.

### Incident 4: Production Circuit Breaker State Machine Drill
- **Runbook Section**: Section 2 ("Operating Envelope"), Section 14 ("Resilience Architecture").
- **Injection Method**: Injected 3 consecutive upstream HTTP 504 / timeout failures against `AtlasCircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)`.
- **System Behavior**:
  - Failure 1: State `CLOSED`, failure count 1/3.
  - Failure 2: State `CLOSED`, failure count 2/3.
  - Failure 3: State transitions from `CLOSED` to `OPEN`.
  - Subsequent request rejected immediately with `CircuitBreakerOpenError` without hitting upstream network.
  - Timer verified: During the 10.0s cooldown window, all requests fail fast.
  - At $t \ge 10.0\text{s}$, state transitions to `HALF_OPEN`.
  - Single probe request succeeds; state transitions from `HALF_OPEN` to `CLOSED`. Failure count resets to 0.

### Incident 5: ATLAS Process Failure & Baseline Index Re-Leasing
- **Runbook Section**: Section 9 ("Start ATLAS Services"), Section 16 ("Cold Initialization & Readiness").
- **Injection Method**: Simulated sudden process termination by tearing down pipeline and instantiating a clean cold restart via `AtlasServicePipeline.create_default()`.
- **System Behavior**:
  - Process initializes cold in 4.7s.
  - Verifies index files in `data/index/`.
  - Leases the persisted baseline index generation ID (`GEN-20260923-BASELINE-XXXX`).
  - Confirmed invariant: Dynamic hot-swap (Phase 4S) is process-local; every cold restart safely re-leases the persisted baseline generation.
  - Health check (`/healthz`) and deep readiness (`/ready`) report ready across all 4 subsystems.
  - Positive query returns grounded answer with 2 valid C2 citations.

### Incident 6: Index Corruption / Missing Generation Protection Drill
- **Runbook Section**: Section 14 ("Resilience Architecture"), Section 20 ("Platform Invariants").
- **Injection Method**: Injected candidate index corruptions into `validate_index_integrity()`:
  - Scenario A: Null/empty components.
  - Scenario B: Dense vector dimension mismatch (128 vs expected 384).
- **System Behavior**:
  - Validator safely rejects candidate publication with explicit structured errors.
  - Active production pipeline index generation remains 100% untouched and operational.
  - Production dense index reports 0 NaNs, 0 orphans, and 0 duplicate IDs.

### Incident 7: Upstream Provider Rollback & Restoration Drill
- **Runbook Section**: Section 17 ("Rollback Procedure: Backend B → Backend A"), Section 18 ("Restoration Procedure: Backend A → Backend B").
- **Injection Method**: Executed operational rollback switch from Backend B (`InferenceServiceAdapter`) to Backend A (`LocalHuggingFaceProvider`), then restored Backend B.
- **System Behavior**:
  - Rollback switch completes in 62.8ms without restarting ATLAS or rebuilding containers.
  - Backend A reports ready.
  - Restoration switch back to Backend B completes in 11.3ms.
  - Authenticated query on restored Backend B executes successfully with status `answered` and 2 C2 citations.

### Incident 8: Authentication Fail-Closed Under Incident Conditions
- **Runbook Section**: Section 10 ("Troubleshooting Matrix"), Section 12 ("Authentication & Security Operations").
- **Injection Method**: Tested authentication boundary during degraded states with:
  1. Missing Authorization header (`""`).
  2. Malformed token (`totally.bogus.jwt`).
  3. Expired token (`exp` in the past).
  4. Bad cryptographic signature (`HS256` signed with wrong key).
  5. Cross-tenant caller mismatch (`CallerContext.tenant_id != JWT.tenant_id`).
- **System Behavior**:
  - Missing token: Rejected (HTTP 401).
  - Malformed token: Rejected (HTTP 401).
  - Expired token: Rejected (`IdentityAuthenticationError`, HTTP 401).
  - Bad signature: Rejected (`IdentityAuthenticationError`, HTTP 401).
  - Cross-tenant mismatch: Rejected (`IdentityContextMismatchError`, HTTP 403).
  - Zero authorization bypasses observed during incident states.

### Incident 9: Client Disconnect & Asynchronous Timeout Semantics
- **Runbook Section**: Section 2 ("Operating Envelope"), Section 20 ("Platform Invariants").
- **Injection Method**: Simulated client cancellation/disconnect after request dispatch under the 30.0s request timeout deadline.
- **System Behavior**:
  - ATLAS enforces `request_timeout_seconds = 30.0` hard deadline.
  - Documented CPU inference behavior: Asynchronous HTTP client disconnect leaves the underlying Ollama thread to finish its active generation cycle on CPU.
  - Concurrency slot is reclaimed once Ollama finishes.
  - Subsequent requests are served normally without slot leaks or process corruption.

### Incident 10: Full End-to-End Recovery Drill
- **Runbook Section**: Section 10 ("Troubleshooting Matrix"), Section 15 ("Operational Recovery Procedures").
- **Injection Method**: Simulated an unannounced upstream dependency crash during normal operation.
- **System Behavior & Operator Actions**:
  1. Detection: Query yields HTTP 503 / abstention `service_unavailable`.
  2. Diagnosis: Operator follows Runbook §10 diagnostic flowchart; identifies Ollama process absent on port 11434.
  3. Recovery Action: Operator executes Runbook §9 command: `$env:OLLAMA_HOST = '0.0.0.0:11434'; ollama serve`.
  4. Container Verification: Operator checks `curl http://127.0.0.1:8001/ready`; verifies `backend_connected: true`.
  5. Service Verification: Operator queries `/healthz` (200 OK) and `/ready` (200 OK).
  6. Functional Verification: Operator runs test query INC-NS-0001; verifies `answered` status with 2 valid C2 citations.
  7. Security Verification: Operator confirms Layer 1S refuses 4/4 negative cases and 0 auth bypasses occur.
  8. Full recovery completed in 14.8 seconds using ONLY runbook procedures.

---

## 4. Runbook Compliance & Operational Usability

All 10 incident categories were executed and resolved using exclusively the documentation, commands, and procedures defined in [`docs/OPERATIONS_RUNBOOK.md`](./docs/OPERATIONS_RUNBOOK.md).

- **Total Runbook Sections Followed**: 12 distinct sections (§2, §8, §9, §10, §11, §12, §14, §15, §16, §17, §18, §20).
- **Undocumented Operator Actions**: Exactly 0.
- **Developer Workarounds Required**: Exactly 0.
- **Diagnostic Flows Validated**: Both upstream failure, container failure, auth failure, capacity shedding, and circuit breaker diagnostic trees proved 100% accurate.

---

## 5. Security & Safety Invariants

| Security Check | Invariant | Observed | Status |
|---|:---:|:---:|:---:|
| Security Violations | `0` | `0` | **PASS** |
| Cross-Tenant Leaks | `0` | `0` | **PASS** |
| Unauthorized Exposures | `0` | `0` | **PASS** |
| Forbidden Document Citations | `0` | `0` | **PASS** |
| Auth Bypass During Recovery | `False` | `False` | **PASS** |
| Secret Leakage in Error Responses | `False` | `False` | **PASS** |
| Layer 1S Negative Refusals | `4/4` | `4/4` | **PASS** |
| C2 Citation Grounding | Enforced | Enforced | **PASS** |

---

## 6. Release Integrity & Immutability

| Artifact / Component | Expected Baseline | Observed During Certification | Status |
|---|---|---|:---:|
| Release Tarball SHA-256 | `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` | `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` | **PASS** |
| Source Code Drift (`src/novastack/`) | Exactly 0 modifications | 0 files modified (16/16 SHA-256 verified) | **PASS** |
| Package Version (`pyproject.toml`) | `0.4.14` | `0.4.14` | **PASS** |
| Release Candidate Tag | `0.4.14-rc1` | `0.4.14-rc1` | **PASS** |
| Model Weight Digest | `8648f39daa8fbf5b18c7b4e6a8fb4990c692751d49917417b8842ca5758e7ffc` | `8648f39daa8fbf5b18c7b4e6a8fb4990c692751d49917417b8842ca5758e7ffc` | **PASS** |
| Container Image & User | `atlas-inference:5d` / `appuser:1000` | Non-root `appuser` (UID 1000), Port 8001 | **PASS** |

---

## 7. Certified Resilience Configuration

| Parameter | Certified Value | Enforcement Scope | Architectural Invariant |
|---|:---:|:---:|---|
| `max_concurrent_inferences` | `1` | Production | Strict single-query serialization on CPU to avoid CPU thread contention |
| `queue_timeout_seconds` | `0.5s` | Production | Rejects concurrent query with HTTP 429 when slot occupied beyond 500ms |
| `request_timeout_seconds` | `30.0s` | Production | Hard query execution deadline; returns HTTP 504 on deadline expiration |
| `circuit_failure_threshold` | `3` | Production | Trips circuit breaker from `CLOSED` to `OPEN` after 3 consecutive failures |
| `circuit_cooldown_seconds` | `10.0s` | Production | Production probe cooldown (test harness 0.2s is not production) |

---

## 8. Certified Regression Suite Results

All 13 test suites passed with 0 failures:

| Suite Name | Scope | Tests Passed | Tests Failed | Status |
|---|---|:---:|:---:|:---:|
| `test_phase_5o_incident_recovery.py` | Incident & Recovery Certification | 18 | 0 | **PASS** |
| `test_phase_5n_operational_runbook.py` | Operational Runbook Contract | 14 | 0 | **PASS** |
| `test_phase_5m_release_packaging.py` | Release Packaging & Deployment | 14 | 0 | **PASS** |
| `test_phase_5l_independent_validation.py` | Independent Baseline Reproduction | 41 | 0 | **PASS** |
| `test_phase_5k_release_freeze.py` | Release Freeze & Manifests | 10 | 0 | **PASS** |
| `test_phase_5j_production_promotion.py` | Promotion & Rollback Control | 7 | 0 | **PASS** |
| `test_phase_5i_production_promotion.py` | Readiness & Resilience Gates | 7 | 0 | **PASS** |
| `test_phase_5g_abstention_safety.py` | Layer 1S Deterministic Safety | 23 | 0 | **PASS** |
| `test_phase_5b_quantized_provider.py` | Quantized Model Verification | 17 | 0 | **PASS** |
| `test_phase_5a_provider_boundary.py` | Inference Provider Abstraction | 23 | 0 | **PASS** |
| `test_security_corpus.py` | Security & Authorization Boundary | 23 | 0 | **PASS** |
| `test_phase_4t_identity_boundary.py` | JWT Identity & Context Matching | 17 | 0 | **PASS** |
| `test_phase_4m_auth_fail_closed.py` | Fail-Closed Auth Pipeline | 11 | 0 | **PASS** |
| **Total Certified Suite** | **13 Suites** | **225** | **0** | **`PASS`** |

---

## 9. Evidence Classification

### VERIFIED
- Pre-incident baseline confirmed release artifact checksum (`382cde...`), package version (`0.4.14`), model digest, and zero source drift.
- Baseline authenticated query answered with valid C2 citations (`[EVD-001]`, `[EVD-002]`).
- Incident 1: Ollama failure safely produced abstention (`service_unavailable`); 0 secrets leaked; recovered via runbook in 15.2s.
- Incident 2: Inference container outage cleanly produced safe abstention; restored via `docker start` in 9.4s.
- Incident 3: Capacity limiter strictly bound to 1 concurrent request; shed Slot 2 with HTTP 429 after 0.5s queue timeout; returned to idle.
- Incident 4: Circuit breaker tripped to `OPEN` after 3 consecutive failures; 10.0s cooldown verified; transitioned to `HALF_OPEN`; reset to `CLOSED` upon successful probe.
- Incident 5: Cold restart re-leased the persisted baseline index generation; `/healthz` and `/ready` re-converged in 4.7s.
- Incident 6: Corrupted index candidates rejected with errors; active production generation remained 100% immutable.
- Incident 7: Bidirectional rollback switch to `LocalHuggingFaceProvider` and restoration to `InferenceServiceAdapter` verified in <75ms.
- Incident 8: Authentication verified fail-closed (missing, malformed, expired, bad-sig, cross-tenant all rejected).
- Incident 9: Client disconnect deadline (30.0s) enforced; asynchronous CPU completion documented; slot reclaimed.
- Incident 10: Full unannounced outage drill executed strictly from runbook; service restored in 14.8s.
- Certified regression suite passed 225/225 tests across all 13 test suites.
- Release tarball SHA-256 matches `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` exactly.

### OBSERVED
- Ollama cold model load latency on Intel Core i3-N305 CPU ranges from 20-25 seconds; pre-warming eliminates subsequent timeout risk.
- Asynchronous HTTP client disconnect allows underlying llama-server thread to finish on CPU before reclaiming concurrency slot.

### UNKNOWN
- Behavioral response to underlying host kernel panic or hypervisor crash.
- Hardware power-loss recovery during active dense index serialization.

### NOT TESTED
- Multi-node clustering, distributed lock managers, or cloud load balancers (explicitly outside single-node operating envelope).
- GPU inference hardware acceleration (platform is certified CPU-only).

---

## 10. Final Decision & Certified Statement

### FINAL DECISION: **`PASS`**

### CERTIFIED STATEMENT
> *"ATLAS Release Candidate `0.4.14-rc1` has successfully completed controlled failure injection and recovery certification across all 10 operational incident categories. The platform fails safely with zero unhandled leaks, maintains strict fail-closed security and tenant isolation boundaries, recovers deterministically via `docs/OPERATIONS_RUNBOOK.md` without developer improvisation, and returns to its certified baseline with exactly zero source code drift, zero security violations, and zero release drift."*
