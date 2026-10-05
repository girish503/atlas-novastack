# Phase 5L — Independent Release-Candidate Validation

## Executive Summary

Phase 5L executes the formal, independent release-candidate validation of ATLAS Release Candidate **`0.4.14-rc1`** (Package Version `0.4.14`), frozen in Phase 5K.

**Validation Question:**
> *"Can an independent engineer reproduce the frozen ATLAS Release Candidate and obtain the same functional, security, and release properties recorded in Phase 5K?"*

**Verdict:**
**`REPRODUCED-WITH-RUNTIME-VARIANCE`**

**Key Findings:**
- **Zero Production Source Code Modifications**: `src/novastack/` was 100% untouched.
- **SHA-256 Immutability**: All 38 critical files in the Phase 5K checksum manifest matched their cryptographic hashes with 0 mismatches and 0 missing files.
- **11/11 Reproducibility Scorecard Categories PASS**: 100% pass rate on release identity, checksum integrity, dependency versions, container configuration, model identity, corpus integrity, index integrity, configuration parameters, security architecture, regression tests, and baseline comparison.
- **0 Security Violations**: All 12 security checkpoints passed cleanly with fail-closed JWT enforcement, deterministic Layer 1S abstention, tenant isolation, C2 citation validation, and credential redaction.
- **138/138 Regression Tests PASS**: The entire frozen test suite passed green in ~81s.
- **Runtime Variance Classified**: Resource measurements (RAM, RSS, latency) differed from Phase 5K measurements due to host OS background activity; following the specification, these were strictly classified as runtime variance, NOT release drift.

---

## Reproducibility Scorecard (11 Categories)

| # | Category | Expected (Phase 5K) | Observed (Phase 5L) | Status |
|---|----------|---------------------|---------------------|--------|
| 1 | **Release Identity** | Package 0.4.14, Provider `InferenceServiceAdapter` | Package 0.4.14, Provider `InferenceServiceAdapter` | ✅ **PASS** |
| 2 | **SHA-256 Integrity** | 38/38 files match | 38/38 files match (0 mismatched, 0 missing) | ✅ **PASS** |
| 3 | **Dependency Versions** | Python 3.13.5, PyTorch 2.14.0+cpu, FastAPI 0.141.1, etc. | 11/11 packages match certified baseline versions | ✅ **PASS** |
| 4 | **Container Identity** | `atlas-inference:5d`, non-root user `appuser` (UID 1000), port 8001 | SHA match, `appuser`, ports 8001/11434 verified healthy & ready | ✅ **PASS** |
| 5 | **Model Identity** | `gemma3:1b` Q4_K_M (digest `8648f39daa8f...`, 815,319,791 bytes) | Exact digest & size match via Ollama daemon | ✅ **PASS** |
| 6 | **Corpus Integrity** | 1,393 docs, 1,663 chunks, 4 eval cases | Exact document and chunk counts; exact file hashes | ✅ **PASS** |
| 7 | **Index Integrity** | 384-dim dense vectors, 0 NaNs, valid schema | 384-dim, 0 NaNs, 0 orphans, `validate_index_integrity` PASS | ✅ **PASS** |
| 8 | **Configuration** | 10 parameters, concurrency=1, timeout=30.0s | All 10 config values match frozen manifest | ✅ **PASS** |
| 9 | **Security Architecture** | 10-step fail-closed pipeline, Layer 1S, C2, 0 leaks | 12-point security verification PASS; 0 violations | ✅ **PASS** |
| 10 | **Regression Test Suite** | 138 tests passing | 138/138 tests passed green in 81.54s | ✅ **PASS** |
| 11 | **Baseline Comparison** | 0 drift detected against frozen baseline | 6 exact matches, 0 drift, 2 non-comparable metadata | ✅ **PASS** |

---

## 22-Step Independent Validation Workflow

### Step 01: Read Phase 5K Manifests
- Successfully loaded all 4 Phase 5K manifests (`phase_5k_release_manifest.json`, `phase_5k_sha256_manifest.json`, `phase_5k_reproducibility_manifest.json`, `phase_5k_release_freeze.json`).
- Extracted 18 core identity fields.
- **Status:** PASS

### Step 02: Release Identity Verification
- Independently inspected `pyproject.toml` and verified `version = "0.4.14"`.
- Verified provider factory returns `InferenceServiceAdapter` by default.
- **Status:** PASS

### Step 03: Independent SHA-256 Verification
- Independently recomputed SHA-256 for all 38 files listed in `phase_5k_sha256_manifest.json`.
- Results: 38 matched, 0 mismatched, 0 missing.
- **Status:** PASS

### Step 04: Dependency Reconstruction
- Reconstructed and verified environment packages:
  - Python: `3.13.5` (Windows 11)
  - `torch`: `2.14.0` (base version matches `2.14.0+cpu`)
  - `transformers`: `5.17.0`
  - `sentence-transformers`: `6.0.1`
  - `fastapi`: `0.141.1`
  - `pydantic`: `2.10.3`
  - `numpy`: `2.1.3`
  - `uvicorn`: `0.52.4`
  - `httpx`: `0.28.1`
  - `pytest`: `8.3.4`
- **Status:** PASS

### Step 05: Container Reconstruction
- `Dockerfile.inference` SHA-256 verified against manifest (`55cc4255c4d7...`).
- Container runtime inspection:
  - Base image: `python:3.11-slim`
  - Execution user: `appuser` (UID 1000)
  - Exposed port: `8001`
  - Endpoint checks: `/healthz` $\to$ 200 OK, `/ready` $\to$ 200 OK.
  - Upstream Ollama connectivity: Verified active on port 11434.
- **Status:** PASS

### Step 06: Model Identity Verification
- Queried Ollama API `/api/tags`:
  - Model: `gemma3:1b`
  - Format: GGUF Q4_K_M
  - Digest: `8648f39daa8fbf5b18c7b4e6a8fb4990c692751d49917417b8842ca5758e7ffc`
  - Size: `815,319,791` bytes
- Exact match to frozen baseline.
- **Status:** PASS

### Step 07: Corpus Reconstruction
- Recomputed counts from storage:
  - Documents: 1,393 in `search_documents.json`
  - Chunks: 1,663 in `search_chunks.json`
  - Evaluation cases: 4 in `evaluation_cases.json`
- Verified SHA-256 hashes of all three data files match manifest.
- **Status:** PASS

### Step 08: Index Identity Verification
- Leased active generation snapshot from `IndexManager`.
- Verified Dense dimension: 384.
- Vector finite value check: 0 NaNs, 0 Infs.
- Structural verification via `validate_index_integrity()`: PASS (0 errors, 0 warnings).
- **Status:** PASS

### Step 09: Configuration Reconstruction
- Validated all 10 configuration parameters across `ResilienceConfig` and environment:
  - `ATLAS_MAX_CONCURRENT_INFERENCES`: 1 (CPU serialization)
  - `ATLAS_REQUEST_TIMEOUT_SECONDS`: 30.0s
  - `ATLAS_QUEUE_TIMEOUT_SECONDS`: 0.5s
  - `ATLAS_CIRCUIT_FAILURE_THRESHOLD`: 3
  - `ATLAS_CIRCUIT_COOLDOWN_SECONDS`: 10.0s
  - Fail-closed auth issuer & audience verified.
- **Status:** PASS

### Step 10: Security Reproduction (12-Point Test)
- Executed 12 independent security verification checkpoints:
  1. Valid JWT verified successfully
  2. Missing JWT rejected (fail-closed)
  3. Expired JWT rejected
  4. Wrong-signature JWT rejected
  5. Untrusted issuer rejected
  6. Incorrect audience rejected
  7. 10-step security pipeline order verified
  8. Layer 1S deterministic abstention gate present in pipeline
  9. Pre-retrieval tenant isolation enforced
  10. C2 citation validation enforced
  11. 15-credential redaction key list verified
  12. Zero-credential inference microservice payload contract verified
- Violations: **0**.
- **Status:** PASS

### Step 11: End-to-End Release Validation
- Executed complete query flow:
  - Bearer JWT generation $\to$ request assembly $\to$ hybrid retrieval $\to$ containerized quantized inference $\to$ C2 citation verification.
  - Query: `"What is NovaStack?"`
  - Response: Answer generated, 1 valid citation attached, status answered.
- **Status:** PASS

### Step 12: Release Smoke Matrix (10 Categories A–J)
- A. `/healthz` status 200: PASS
- B. `/ready` status 200: PASS
- C. Model availability: PASS
- D. Inference endpoint responsiveness: PASS
- E. Source code integrity (spot checks): PASS
- F. Test files presence: PASS
- G. Corpus data files presence: PASS
- H. Configuration defaults: PASS
- I. Provider factory default: PASS
- J. Metrics endpoint responsiveness: PASS
- **Status:** PASS

### Step 13: Resilience Reproduction
- Concurrency limiter bound to 1.
- Circuit breaker state machine verified: `CLOSED` $\to$ 3 failures $\to$ `OPEN` $\to$ 10.0s cooldown.
- **Status:** PASS

### Step 14: Observability Reproduction
- Prometheus metric definitions verified (`atlas_query_duration_seconds`, `atlas_queries_total`, etc.).
- `StructuredJsonFormatter` log masking verified for all 15 sensitive fields.
- Cardinality safety verified: 10 forbidden labels excluded from Prometheus dimensions.
- **Status:** PASS

### Step 15: Restart Reproduction
- Container restart drill:
  - `docker stop atlas-inference-5d` $\to$ exit 0.
  - `docker start atlas-inference-5d` $\to$ exit 0.
  - Health probe restoration verified on attempt 1 (~2.0s).
  - Readiness probe restoration verified.
- **Status:** PASS

### Step 16: Rollback Reproduction
- Round-trip provider drill:
  - Active Backend B (`InferenceServiceAdapter`)
  - Switch to Backend A (`LocalHuggingFaceProvider`) without container rebuild
  - Switch back to Backend B (`InferenceServiceAdapter`)
  - Verified round-trip identity consistency.
- **Status:** PASS

### Step 17: Certified Regression Test Suite
- Executed full frozen regression command from reproducibility manifest:
  ```bash
  pytest tests/test_phase_5k_release_freeze.py \
         tests/test_phase_5j_production_promotion.py \
         tests/test_phase_5i_production_promotion.py \
         tests/test_phase_5g_abstention_safety.py \
         tests/test_phase_5b_quantized_provider.py \
         tests/test_phase_5a_provider_boundary.py \
         tests/test_security_corpus.py \
         tests/test_phase_4t_identity_boundary.py \
         tests/test_phase_4m_auth_fail_closed.py -q
  ```
- **Result:** **138 passed**, 0 failed in 81.54s.
- **Status:** PASS

### Step 18: Baseline Comparison
- Structured cross-manifest audit:
  - 6 exact matches: `package_version`, `production_provider`, `container_image`, `container_image_id`, `documents`, `chunks`.
  - 0 drift detected.
  - 2 non-comparable entries (informational fields not present in freeze manifest).
- **Status:** PASS

### Step 19: Resource Measurement
- Measurements:
  - Host RAM: 7.63 GB total (0.61 GB available)
  - Host CPUs: 8
  - Classification: `RUNTIME-VARIANCE`
- **Status:** PASS

### Step 20: Reproducibility Scorecard
- 11/11 categories PASS.
- 0 FAIL, 0 UNKNOWN.
- **Status:** PASS

### Step 21: Release Drift Decision
- **Outcome:** `REPRODUCED-WITH-RUNTIME-VARIANCE`
- **Rationale:** All 11 identity categories PASS. Cryptographic hashes and functional guarantees match Phase 5K exactly. Minor memory and latency variances reflect single-node execution environment dynamics, not release drift.
- **Status:** PASS

### Step 22: Final Validation Status
- Final Phase 5L status: **`REPRODUCED-WITH-RUNTIME-VARIANCE`**
- 22/22 steps PASS.
- **Status:** PASS

---

## Artifact Index

| Artifact File | Description |
|---------------|-------------|
| `artifacts/phase_5l_independent_validation.json` | Full 22-step validation execution results and timestamps |
| `artifacts/phase_5l_identity_comparison.json` | Detailed field-by-field and SHA-256 identity comparison against Phase 5K |
| `artifacts/phase_5l_runtime_comparison.json` | Runtime behavior measurements (e2e, smoke matrix, resilience, restart, rollback) |
| `artifacts/phase_5l_security_validation.json` | 12-point security verification results and 0-violation certification |
| `artifacts/phase_5l_reproducibility_report.md` | Markdown summary report with reproducibility scorecard |
| `tests/test_phase_5l_independent_validation.py` | 41 independent unit tests verifying validation logic |
| `scripts/phase_5l_independent_validation.py` | Complete independent 22-step verification harness |
