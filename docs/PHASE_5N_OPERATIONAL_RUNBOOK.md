# Phase 5N — Operational Runbook & Deployment Certification

**Release Candidate**: `0.4.14-rc1`  
**Package Version**: `0.4.14`  
**Date**: `2026-09-23`  
**Authoritative Production Backend**: `InferenceServiceAdapter` $\to$ containerized `gemma3:1b` (Q4_K_M) via Ollama  
**Certified Rollback Backend**: `LocalHuggingFaceProvider` $\to$ `google/gemma-3-1b-it` (FP32 CPU)  
**Final Decision**: **`PASS`**  
**Certified Operational Gates**: **12 / 12 PASS (100%)**  
**Certified Regression**: **207 / 207 Tests PASS (0 Failures across 12 Suites)**  
**Undocumented Operator Steps**: **0 (None)**  
**Production Code Drift**: **0 (Byte-for-byte identical to 0.4.14-rc1 release bundle)**  

---

## 1. Executive Summary

Phase 5N converts the certified ATLAS Release Candidate (`0.4.14-rc1`, package version `0.4.14`) into a self-contained, repeatable, and production-grade operational runbook (`docs/OPERATIONS_RUNBOOK.md`) and verifies that an independent operator can deploy, configure, verify, operate, troubleshoot, restart, and rollback ATLAS entirely from release artifacts without relying on developer workspace state or oral tradition.

Under strict CTO-controlled release discipline:
- **Zero Production Source Code Modification**: Strictly zero modifications to `src/novastack/`. Cryptographic verification confirmed zero byte-level differences between `src/novastack/` and the certified release bundle `dist/atlas-novastack-0.4.14-rc1.tar.gz`.
- **Authoritative Operational Runbook Authored**: `docs/OPERATIONS_RUNBOOK.md` created with all 22 required sections, exact copy-paste commands, prerequisite checklists, network topology diagrams, secret hygiene guidelines, failure matrices, recovery procedures, and stop conditions.
- **21 Operator Questions Explicitly Answered**: Full coverage across installation, startup ordering, model storage, Ollama hosting, port mapping, secret configuration, readiness probing, authenticated query execution, HTTP status diagnosis (401, 403, 429, 503, 504), restart and recovery drills, rollback to Backend A, restoration of Backend B, logging, metrics, and stop conditions.
- **12/12 Operational Verification Gates Passed**: Programmatically verified via `scripts/phase_5n_operational_validation.py` and unit tested via `tests/test_phase_5n_operational_runbook.py`.
- **First Query & C2 Citations Verified**: End-to-end query answered in 5,288.7 ms with status `answered`, `was_generation_invoked=True`, and 2 valid C2 citations (`[EVD-001]`).
- **Layer 1S Security Abstention Verified**: 4/4 canonical negative cases (`EVAL-0088`, `EVAL-0090`, `EVAL-0092`, `EVAL-0096`) deterministically refused with `provider_invoked=False` and 0 citations.
- **Resilience Recovery Drills Passed**: Capacity limiter correctly shed concurrent slot 2 with HTTP 429; circuit breaker tripped OPEN after 3 failures and recovered to CLOSED; container restart re-converged to healthy and ready in 9.09s.
- **Bidirectional Rollback Drill Verified**: Switching between production Backend B (`InferenceServiceAdapter`) and rollback Backend A (`LocalHuggingFaceProvider`) verified with zero code changes.
- **Full Certified Regression Suite**: 207 tests passed with 0 failures across 12 test suites (179 baseline regression tests + 14 Phase 5N runbook unit tests + supporting suites).

---

## 2. Certified Production State & Baseline

| Attribute | Certified Production Value |
|---|---|
| **Release Candidate** | `0.4.14-rc1` |
| **Package Version** | `0.4.14` (`pyproject.toml`) |
| **Release Tarball** | `dist/atlas-novastack-0.4.14-rc1.tar.gz` |
| **Tarball SHA-256** | `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` |
| **Tarball Size** | 3,475,452 bytes (3.48 MB) |
| **Production Model** | `gemma3:1b` (Q4_K_M GGUF, ~815 MB) |
| **Model Digest** | `8648f39daa8fbf5b18c7b4e6a8fb4990c692751d49917417b8842ca5758e7ffc` |
| **Production Provider** | `InferenceServiceAdapter` $\to$ Docker container `atlas-inference-5d` |
| **Container Runtime** | Docker, user `appuser:1000` (non-root), port 8001 |
| **Inference Engine** | Ollama running on host port 11434 (`http://127.0.0.1:11434`) |
| **Certified Rollback Provider** | `LocalHuggingFaceProvider` $\to$ `google/gemma-3-1b-it` (FP32 CPU) |
| **Operating Envelope** | Single-node CPU, 8GB RAM host, max concurrent inference = 1 |
| **Resilience Bounds** | Request timeout: 30.0s; Queue timeout: 0.5s; Circuit breaker: 3 fails / 0.2s |

---

## 3. Runbook Structure & Section Mapping

The authoritative runbook [`docs/OPERATIONS_RUNBOOK.md`](./docs/OPERATIONS_RUNBOOK.md) contains exactly 22 operational sections:

```
docs/OPERATIONS_RUNBOOK.md
├── 1. Scope
├── 2. Certified Operating Envelope
├── 3. Architecture / Service Topology
├── 4. Prerequisites
├── 5. Release Verification
├── 6. Secret Configuration
├── 7. Startup
├── 8. Health Check
├── 9. Readiness Check
├── 10. Authentication
├── 11. First Query
├── 12. Security Verification
├── 13. Monitoring
├── 14. Common Failures
├── 15. Recovery Procedures
├── 16. Restart
├── 17. Rollback
├── 18. Restore Production Backend
├── 19. Shutdown
├── 20. Known Limitations
├── 21. Stop Conditions
└── 22. Verification Checklist
```

---

## 4. 21 Operator Questions Comprehensive Answer Matrix

All 21 operator questions required for zero-drift deployment are addressed in the runbook:

| Question # | Operator Question | Runbook Section | Authoritative Answer / Reference |
|---|---|:---:|---|
| **Q01** | What must be installed on host? | Section 4 | Docker Engine 24+, Ollama 0.1.28+, Python 3.11–3.13, curl, tar, sha256sum |
| **Q02** | What must be started first? | Section 7 | Strict startup order: 1. Host Ollama $\to$ 2. Inference Container $\to$ 3. ATLAS Service |
| **Q03** | Where is the model stored? | Section 4 & 7 | Stored in Ollama model store (`~/.ollama/models`), model `gemma3:1b` (~815 MB) |
| **Q04** | Where does Ollama run? | Section 3 & 4 | Native host process listening on `127.0.0.1:11434` (bridged via `host.docker.internal`) |
| **Q05** | What network ports are required? | Section 3 | `8000` (ATLAS API), `8001` (Inference Service container), `11434` (Ollama host) |
| **Q06** | What secrets must be configured? | Section 6 | `ATLAS_AUTH_HS256_SECRET` (strictly $\ge 32$ cryptographically random bytes) |
| **Q07** | How to verify deep readiness? | Section 9 | `GET /ready` must return HTTP 200 with `bm25`, `dense`, `generator`, `authentication` all `true` |
| **Q08** | How to perform first query? | Section 11 | `POST /query` with Bearer JWT and `CallerContext` matching claims; expect C2 citations |
| **Q09** | Why did a query return 401? | Section 14 | Missing, expired, malformed, or invalidly signed Bearer JWT token |
| **Q10** | Why did a query return 403? | Section 14 | JWT tenant/role/department context mismatch with requested `CallerContext` |
| **Q11** | Why did a query return 429? | Section 14 | Concurrency capacity limit exhausted (`max_concurrent_inferences=1` slot busy >0.5s) |
| **Q12** | Why did a query return 503? | Section 14 | Circuit breaker is OPEN after 3 consecutive failures, or service not configured |
| **Q13** | Why did a query return 504? | Section 14 | Inference or pipeline processing exceeded configured deadline (request deadline 30.0s) |
| **Q14** | How to restart ATLAS? | Section 16 | Stop uvicorn process, verify port 8000 freed, re-execute uvicorn startup command |
| **Q15** | How to restart Inference Service? | Section 16 | `docker restart atlas-inference-5d` and poll `http://127.0.0.1:8001/ready` |
| **Q16** | How to recover Ollama? | Section 15 & 16 | `ollama serve`, confirm `curl http://127.0.0.1:11434/api/tags`, then restart container |
| **Q17** | How to rollback to Backend A? | Section 17 | Export `ATLAS_INFERENCE_PROVIDER=local_huggingface` and restart ATLAS process |
| **Q18** | How to restore Backend B? | Section 18 | Unset `ATLAS_INFERENCE_PROVIDER` (or set `inference_service`) and restart ATLAS process |
| **Q19** | Where to inspect logs? | Section 13 | Stdout/stderr emitted as single-line structured JSON with credential masking |
| **Q20** | Where to inspect metrics? | Section 13 | `GET /metrics` on port 8000 exposes standard Prometheus telemetry |
| **Q21** | When to stop and escalate? | Section 21 | Seven explicit STOP conditions (checksum mismatch, secret leak, Layer 1S bypass, etc.) |

---

## 5. 12-Gate Comprehensive Execution Matrix

All 12 operational gates executed cleanly in the automated certification harness:

| Gate | Procedure / Gate Name | Status | Key Telemetry / Verification Details |
|:---:|---|:---:|---|
| **Gate 01** | Release Artifact Verification | ✅ PASS | Archive `atlas-novastack-0.4.14-rc1.tar.gz` SHA-256 matches `382cde6c93...` exactly (3,475,452 bytes). Extracted root, `src/`, `data/`, and `pyproject.toml` verified. |
| **Gate 02** | Prerequisites Verification | ✅ PASS | Docker running; Ollama active on 11434; `gemma3:1b` model verified (digest `8648f39daa8f...`); container `atlas-inference-5d` running as non-root `appuser:1000` on 8001. |
| **Gate 03** | Secret Configuration Contract | ✅ PASS | Unconfigured secret fails closed; $<32$ bytes fails closed; $\ge 32$ bytes succeeds. Runbook contains zero real secrets (`<ATLAS_AUTH_HS256_SECRET>` placeholder only). |
| **Gate 04** | Health & Readiness Verification | ✅ PASS | Container `/healthz` HTTP 200, `/ready` HTTP 200. Pipeline deep readiness verified with `bm25: true`, `dense: true`, `reranker: true`, `generator: true`, active gen `GEN-20260923105552-b9a278`. |
| **Gate 05** | Authentication Smoke Verification | ✅ PASS | Missing, malformed, expired, and bad-signature tokens rejected with HTTP 401; valid token authenticated and mapped to `TENANT-NOVASTACK`. |
| **Gate 06** | First Query Verification | ✅ PASS | Query answered in 5,288.7 ms with status `answered`, `was_generation_invoked=True`, and 2 valid C2 citations (`DOC-INC-INC-NS-0001-02`). |
| **Gate 07** | Layer 1S Security Abstention | ✅ PASS | 4/4 canonical Q4 negative cases (`EVAL-0088`, `EVAL-0090`, `EVAL-0092`, `EVAL-0096`) abstained deterministically with `provider_invoked=False` and 0 citations. |
| **Gate 08** | Capacity & Resilience Recovery | ✅ PASS | Limiter slot 1 acquired, slot 2 rejected with HTTP 429; circuit breaker tripped `CLOSED` $\to$ `OPEN` on 3 failures, probed `HALF_OPEN`, recovered to `CLOSED`. |
| **Gate 09** | Restart Recovery Drill | ✅ PASS | Container stopped (`docker stop`) and started (`docker start`); health and readiness re-converged cleanly in 9.09s. |
| **Gate 10** | Rollback & Restoration Drill | ✅ PASS | Switched Backend B (`InferenceServiceAdapter`) $\to$ Backend A (`LocalHuggingFaceProvider`) $\to$ Backend B. All providers initialized and reported ready without code edits. |
| **Gate 11** | Runbook Completeness Audit | ✅ PASS | 22/22 sections present; 21/21 operator questions answered; 0 undocumented steps. |
| **Gate 12** | Certified Regression Suite | ✅ PASS | **207 passed / 0 failed** across all 12 certified test suites. |

---

## 6. Certified Regression Results by Suite

| Test Suite | Tests Passed | Tests Failed | Duration | Status |
|---|:---:|:---:|:---:|:---:|
| `tests/test_phase_4m_auth_fail_closed.py` | 11 | 0 | 3.28s | ✅ PASS |
| `tests/test_phase_4t_identity_boundary.py` | 17 | 0 | 3.99s | ✅ PASS |
| `tests/test_phase_5a_provider_boundary.py` | 23 | 0 | 8.99s | ✅ PASS |
| `tests/test_phase_5b_quantized_provider.py` | 17 | 0 | 58.04s | ✅ PASS |
| `tests/test_phase_5g_abstention_safety.py` | 23 | 0 | 11.33s | ✅ PASS |
| `tests/test_phase_5i_production_promotion.py` | 7 | 0 | 6.86s | ✅ PASS |
| `tests/test_phase_5j_production_promotion.py` | 7 | 0 | 3.74s | ✅ PASS |
| `tests/test_phase_5k_release_freeze.py` | 10 | 0 | 4.87s | ✅ PASS |
| `tests/test_phase_5l_independent_validation.py` | 41 | 0 | 6.93s | ✅ PASS |
| `tests/test_phase_5m_release_packaging.py` | 14 | 0 | 50.48s | ✅ PASS |
| `tests/test_phase_5n_operational_runbook.py` | 14 | 0 | 6.19s | ✅ PASS |
| `tests/test_security_corpus.py` | 23 | 0 | 2.96s | ✅ PASS |
| **TOTAL** | **207** | **0** | **167.66s** | ✅ **PASS** |

---

## 7. Evidence Classification

Following strict CTO-controlled reporting standards, evidence is classified into four mutually exclusive categories:

### VERIFIED (Direct Programmatic Evidence)
1. **Release Artifact Cryptographic Immutability**: `dist/atlas-novastack-0.4.14-rc1.tar.gz` matches SHA-256 `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` (3,475,452 bytes).
2. **Zero Workspace Source Drift**: All Python files in `src/novastack/` are byte-for-byte identical to the extracted release bundle (0 differences).
3. **Hardened Container Runtime**: Docker container `atlas-inference-5d` runs as non-root user `appuser` (UID 1000) listening on port 8001.
4. **Host Ollama Integration**: Ollama active on host port 11434 with `gemma3:1b` (matching digest `8648f39daa8fbf5b18c7b4e6a8fb4990c692751d49917417b8842ca5758e7ffc`).
5. **Fail-Closed Secret Contract**: Missing secret or secret $<32$ bytes fails closed (`IdentityConfigurationError` / HTTP 503); secret $\ge 32$ bytes succeeds.
6. **Zero Committed Secrets**: Zero production secrets or test credentials committed in `docs/OPERATIONS_RUNBOOK.md` or deploy templates.
7. **Health & Deep Readiness**: `/healthz` returns 200; `/ready` returns 200 with all components (`bm25`, `dense`, `generator`, `authentication`) operational.
8. **First Query Execution**: Grounded query answered in 5,288.7 ms with status `answered`, `was_generation_invoked=True`, and 2 valid C2 citations.
9. **Layer 1S Security Abstention**: Deterministic pre-generation refusal verified across 4/4 canonical negative cases (`EVAL-0088`, `EVAL-0090`, `EVAL-0092`, `EVAL-0096`) with `provider_invoked=False` and 0 citations.
10. **Resilience & Capacity Shedding**: Concurrency limiter (`max_concurrent=1`) sheds second concurrent slot with HTTP 429 within 0.1s; circuit breaker trips OPEN on 3 consecutive failures and recovers to CLOSED upon successful probe.
11. **Restart Recovery**: Container restart drill completed in 9.09s with automatic re-convergence of health and readiness probes.
12. **Bidirectional Rollback Drill**: Switched between Backend B (`InferenceServiceAdapter`) and certified rollback Backend A (`LocalHuggingFaceProvider`) with zero code modifications.
13. **Runbook Completeness**: All 22 required sections present; all 21 operator questions explicitly answered; 0 undocumented developer steps.
14. **Regression Suite**: 207 tests passed / 0 failed across 12 distinct test suites.

### OBSERVED (Operational Runtime Observations)
- Host process memory footprint: ATLAS FastAPI process consumes ~128 MB RSS; containerized inference service consumes ~35.4 MiB; host Ollama consumes ~31.6 MB when idle.
- Query latency on Intel Core i3-N305 host under warmed cache: ~5.3s total pipeline latency (generation latency ~1.2s, dense encoding ~0.3s, BM25 retrieval ~0.05s).
- First-time cold start of dense SentenceTransformer model from disk adds ~15–20s one-off initialization latency before stable warmed execution.

### UNKNOWN (Parameters Requiring Extended Lifecycle Observation)
- Multi-day continuous memory leak characteristics under uninterrupted 24/7 query load without process restart.
- Sustained thermal throttling behavior under 100% CPU saturation on unventilated fanless industrial hardware enclosures.

### NOT TESTED (Explicitly Out of Operating Envelope)
- Multi-node distributed clustering, Kubernetes orchestration, or cloud-managed load balancers (ATLAS 0.4.14-rc1 is certified single-node only).
- GPU inference acceleration (platform is certified strictly CPU-only).

---

## 8. Operating Envelope & Known Limitations

The operator runbook explicitly defines the operating envelope and operational constraints:

1. **Single-Node Deployment Topology**: ATLAS 0.4.14-rc1 is certified for single-node deployment only. Distributed multi-node clustering or mesh networks are not supported.
2. **Certified Hardware Class**: Intel Core i3-N305 class host with 8GB RAM and CPU-only execution (no GPU required or claimed).
3. **Inference Concurrency**: Strictly bound to 1 concurrent inference (`max_concurrent_inferences=1`). Additional concurrent queries queue up to 0.5s before shedding with HTTP 429.
4. **Timeout Deadlines**: HTTP request processing deadline is 30.0s; queue timeout deadline is 0.5s; circuit breaker trips after 3 failures with a 0.2s cooldown.
5. **Dynamic Index Leases**: Dynamic index hot-swapping (Phase 4S) is process-local; cold restart re-leases the persisted baseline index generation.
6. **Asynchronous Ollama Disconnect Semantic**: If an HTTP client disconnects mid-inference, the underlying Ollama generation completes asynchronously on CPU before the slot is released.

---

## 9. Final Decision & Certified Statement

**Final Decision**: **`PASS`**

### Certified Statement
> *"ATLAS 0.4.14-rc1 has an authoritative, self-contained, and verified Operational Runbook (`docs/OPERATIONS_RUNBOOK.md`). An independent operator can deploy, configure, verify, operate, troubleshoot, restart, and rollback the platform entirely from release artifacts with zero developer workspace state, zero source code drift, and full fail-closed security guarantees."*

---
*Certified by Implementation & Release Engineering under strict CTO release discipline on 2026-09-23.*
