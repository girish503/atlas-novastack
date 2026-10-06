# ATLAS GH-07D — CI Portability Remediation & Offline Test Hardening Report

## 1. Executive Summary

During **GH-07C-W**, remote execution on GitHub Actions (Run ID `37323393211`) confirmed that all three original root causes identified in **GH-07A** were completely resolved:
1. **Container Build**: 100% PASS (Docker build context and `COPY data/` verified).
2. **`psutil` dependency**: 100% PASS (Package available in isolated virtual environment).
3. **`tests` import path**: 100% PASS (Pytest collects `tests.mock_provider` cleanly).

However, execution on clean Ubuntu Linux runners surfaced three new failure classes stemming from environment divergence between the Windows development environment and the Linux CI runner:
- **Failure Class A**: Line-ending (CRLF vs LF) hash mismatches in raw binary SHA-256 assertions across 20 integrity tests.
- **Failure Class B**: Stale `pyproject.toml` hash in `artifacts/phase_5k_sha256_manifest.json` and `artifacts/phase_5m_sha256_manifest.json` predating the GH-07B packaging updates (`psutil` and `pythonpath`).
- **Failure Class C**: Offline environment assumptions violated by unit tests attempting unauthenticated outbound network calls to HuggingFace (`BAAI/bge-small-en-v1.5`) or attempting local socket connections to port 8001 / 11434 when no container daemon runs during CI test steps.

This phase, **GH-07D**, resolves all three failure classes completely and locally without modifying frozen production code or compromising safety boundaries.

### Authoritative Decision
```text
REMEDIATION_READY
```

### Safety & Guardrail Invariants
- **Remote Push**: NOT performed (`push_performed = false`).
- **Production Baseline**: 0 files modified in `src/novastack/` (`source_modified = false`).
- **Release Archives**: Unmodified and byte-exact (`release_archives_modified = false`).
  - 0.5.0-rc1: `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936` (VERIFIED)
  - 0.4.14: `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` (VERIFIED)
- **Canary / CS-01**: NOT executed (`cs01_executed = false`).
- **Docker Local Status**: `LOCAL_DOCKER_BUILD_UNVERIFIED` (retained under operator memory waiver).

---

## 2. Governing State & Git Lineage

| Parameter | Value |
| :--- | :--- |
| **Repository** | `girish503/atlas-novastack` |
| **Visibility** | `PRIVATE` |
| **Branch** | `main` |
| **Governing Remote HEAD** | `f257e8ccfc70bafdb85183f8ce5b7b60bb6c5b78` |
| **Parent Commit** | `f257e8ccfc70bafdb85183f8ce5b7b60bb6c5b78` |
| **GH-07C-W Run ID** | `37323393211` |
| **Proposed Remediation Commit Message** | `fix: make CI platform-independent and offline-safe` |
| **Production Source Drift** | 0 bytes / 0 lines modified in `src/novastack/` |
| **Test Suites Passing Locally** | **1043 passed**, 114 deselected, 0 failed in 209.67s |

---

## 3. Failure Class Diagnostics & Remediation Details

### 3.1 Failure Class A: Line-Ending Hash Mismatches (CRLF vs LF)
- **Diagnostic Finding**: Windows checkouts preserved CRLF (`\r\n`) in text files, while Linux runners checked out repository files with LF (`\n`). Several regression tests in `test_phase_5p`, `test_phase_5k`, `test_phase_5l`, `test_phase_5m`, and `test_depth_fusion_ablation` calculated raw binary SHA-256 hashes (`path.read_bytes()`) against stored hex digests. Because CRLF and LF produce different binary SHA-256 hashes, the tests passed on Windows but failed on Linux.
- **Architectural Solution**:
  1. Configured repository-wide `.gitattributes` to enforce canonical LF checkout across all environments (`* text=auto eol=lf`) while protecting binary archives and array dumps (`*.tar.gz binary`, `*.npz binary`).
  2. Implemented `verify_sha256_platform_independent(actual_path_or_bytes, expected_sha)` in `tests/conftest.py`. This helper computes the raw SHA-256, LF-normalized SHA-256, and CRLF-normalized SHA-256, verifying cryptographic match regardless of local OS checkout line endings while remaining completely fail-closed for any actual content modification.
  3. Integrated `verify_sha256_platform_independent` across all integrity and freeze verification tests.

### 3.2 Failure Class B: Stale `pyproject.toml` Manifest SHA-256
- **Diagnostic Finding**: During GH-07B, `pyproject.toml` was correctly updated to include `pythonpath = [".", "src"]` and `"psutil>=5.9.0,<7.0.0"`. However, `artifacts/phase_5k_sha256_manifest.json` and `artifacts/phase_5m_sha256_manifest.json` still contained the pre-remediation SHA-256 digest (`307d...`). Consequently, `test_sha256_manifest_immutability` failed in both fast tests and release packaging checks.
- **Architectural Solution**:
  1. Computed the canonical LF SHA-256 digest of `pyproject.toml`: `4b4022fa4478354838c5e6446f232137d8087e1d5ebfc6c590714811b20c6484`.
  2. Updated the entry in both `artifacts/phase_5k_sha256_manifest.json` and `artifacts/phase_5m_sha256_manifest.json`.

### 3.3 Failure Class C: Offline & Unauthenticated Environment Hardening
- **Diagnostic Finding**:
  1. `DenseEncoder.get_model()` in `novastack/retrieval.py` downloads `BAAI/bge-small-en-v1.5` if no local cache exists. In Windows development, the weights were pre-cached in `~/.cache/huggingface/hub/`. On a clean GitHub Actions Ubuntu runner with no cache and unauthenticated network access, calls to `DenseEncoder.get_model()` in `test_phase_4r_load_validation.py` triggered HuggingFace connection timeouts.
  2. `InferenceServiceAdapter.is_ready()` performs an HTTP GET request to `http://127.0.0.1:8001/healthz` or `http://localhost:11434/api/tags`. In CI test steps, no inference container is running, causing socket connection errors or failed readiness assertions in operational runbook tests (`test_phase_5n`, `test_phase_5o`, `test_phase_4n`, `test_phase_5a`).
- **Architectural Solution**:
  1. In `test_phase_4r_load_validation.py`, wired `DeterministicEncoder` directly into `pipe.dense_index.encoder` within `build_test_pipeline()`, eliminating network model downloads while thoroughly exercising query pipeline execution, thread concurrency, and burst memory validation.
  2. In `test_phase_4n_packaging.py`, `test_phase_5a_provider_boundary.py`, `test_phase_5g_abstention_safety.py`, `test_phase_5m_release_packaging.py`, `test_phase_5n_operational_runbook.py`, and `test_phase_5o_incident_recovery.py`, injected deterministic mocks for generator/adapter network calls (`is_ready`, `_call_ollama`), enabling fast, fully hermetic, offline-safe execution.

---

## 4. Phase 5 CI Equivalence Review

### 4.1 Equivalence Review: Failure Class A (Line-Ending Hash Invariance)

1. **OLD FAILURE**:
   - `test_phase_5p_final_commissioning.py::test_zero_source_drift_in_novastack` FAILED.
   - `test_phase_5k_release_freeze.py::test_phase_9_corpus_immutability` FAILED.
   - `test_phase_5l_independent_validation.py::TestStep01SourceIntegrity::test_critical_source_files_sha256` FAILED.
   - `test_depth_fusion_ablation.py::test_prior_15_artifacts_sha256_immutability` FAILED.
2. **ROOT CAUSE**:
   - Files checked out on Windows had CRLF line terminators; GitHub Actions Ubuntu runners check out files with LF terminators. Tests calculated raw byte hashes without normalizing line-endings.
3. **REMEDIATION**:
   - Created `.gitattributes` (`* text=auto eol=lf`).
   - Implemented `verify_sha256_platform_independent()` in `tests/conftest.py` supporting byte, LF, and CRLF validation.
   - Updated test assertions to utilize platform-independent verification.
4. **WHY THIS IS CORRECT**:
   - Source code and text data semantics are identical regardless of newline representation. Cryptographic integrity must test semantic content rather than transient OS checkout conventions, while binary assets (`tar.gz`, `npz`) remain strictly byte-exact.
5. **LOCAL TEST RESULT**:
   - All 20 previously failing tests PASS locally on Windows (90/90 integrity suite pass).
6. **CI EXPECTED RESULT**:
   - All 20 tests will PASS on Ubuntu runners, matching either raw LF bytes or LF-normalized content.
7. **SECURITY IMPACT**:
   - None. The normalization only strips `\r` from `\r\n` line endings before hashing text; any modification to characters, tokens, logic, or structure produces an immediate hash mismatch. Fail-closed posture is 100% maintained.
8. **RELEASE IMPACT**:
   - Ensures the codebase is reproducible and verifiable across developers and CI systems on Linux, macOS, and Windows.

---

### 4.2 Equivalence Review: Failure Class B (Stale `pyproject.toml` Manifest Hash)

1. **OLD FAILURE**:
   - `test_phase_5m_release_packaging.py::TestGate02ManifestImmutability::test_sha256_manifest_immutability` FAILED.
   - `test_phase_5l_independent_validation.py::TestStep01SourceIntegrity::test_pyproject_toml_sha256` FAILED.
2. **ROOT CAUSE**:
   - `pyproject.toml` was legitimately modified in GH-07B to add test dependencies (`psutil`, `pythonpath`), altering its content. The freeze manifests (`phase_5k` and `phase_5m`) held the obsolete pre-remediation hash.
3. **REMEDIATION**:
   - Updated `pyproject.toml` hash in `artifacts/phase_5k_sha256_manifest.json` and `artifacts/phase_5m_sha256_manifest.json` to canonical LF hash `4b4022fa4478354838c5e6446f232137d8087e1d5ebfc6c590714811b20c6484`.
4. **WHY THIS IS CORRECT**:
   - The manifests document frozen publication state. Once a formal, reviewed remediation modifies `pyproject.toml`, the corresponding manifest entries must reflect the updated canonical state.
5. **LOCAL TEST RESULT**:
   - `test_sha256_manifest_immutability` and `test_pyproject_toml_sha256` PASS locally (100%).
6. **CI EXPECTED RESULT**:
   - PASS on remote CI runner.
7. **SECURITY IMPACT**:
   - Positive. Resolves false alarms in build integrity while guaranteeing that any unauthorized changes to `pyproject.toml` are immediately detected.
8. **RELEASE IMPACT**:
   - Packaging manifests correctly represent the build configuration.

---

### 4.3 Equivalence Review: Failure Class C (Offline Test Hardening)

1. **OLD FAILURE**:
   - `test_phase_4r_load_validation.py::test_end_to_end_query_pipeline_performance` FAILED with network connection timeout / urllib error attempting to contact HuggingFace.
   - `test_phase_5n_operational_runbook.py::test_provider_switching_round_trip` and `test_phase_5o_incident_recovery.py` FAILED on socket connection errors to port 8001 / 11434.
2. **ROOT CAUSE**:
   - Tests inadvertently assumed local developer environment state (pre-cached model weights in `~/.cache/` and local mock/container daemon listening on port 8001). Clean CI runners possess neither.
3. **REMEDIATION**:
   - Injected `DeterministicEncoder` into pipeline fixtures for load tests.
   - Mocked generator readiness and Ollama bridge responses for unit-level runbook and provider boundary tests.
4. **WHY THIS IS CORRECT**:
   - Unit and load regression tests in CI must be hermetic and offline-safe. End-to-end container integration is validated in the dedicated container build and smoke-test job. Unit tests must not depend on external Internet connectivity or running background daemons.
5. **LOCAL TEST RESULT**:
   - All load validation (12/12) and runbook tests (100%) PASS locally without requiring network access.
6. **CI EXPECTED RESULT**:
   - All tests execute hermetically and PASS on remote CI runners without timeout or network flakiness.
7. **SECURITY IMPACT**:
   - Positive. Tests no longer attempt outbound network egress during automated test execution, enforcing offline isolation principles.
8. **RELEASE IMPACT**:
   - Test suite execution is deterministic, repeatable, and resilient to third-party network outages.

---

## 5. Verification & Test Evidence

### 5.1 Targeted Integrity Suites
Command:
```bash
pytest tests/test_depth_fusion_ablation.py tests/test_phase_5k_release_freeze.py tests/test_phase_5l_independent_validation.py tests/test_phase_5m_release_packaging.py tests/test_phase_5p_final_commissioning.py -q
```
**Result**: `90 passed, 2 warnings in 4.30s` (100% PASS)

### 5.2 Security Regression Gate (9 Certified Suites)
Command:
```bash
pytest tests/test_phase_4m_api_service.py tests/test_phase_4m_auth_fail_closed.py tests/test_phase_4t_security_gate.py tests/test_phase_4k_threat_model.py tests/test_phase_4s_audit_readiness.py tests/test_phase_4p_observability.py tests/test_phase_4o_resilience.py tests/test_phase_4q_ingestion_reliability.py tests/test_phase_4r_load_validation.py tests/test_phase_4w_soak_characterization.py -q
```
**Result**: `120 passed, 4 warnings in 20.35s` (100% PASS)

### 5.3 Complete Fast Test Suite
Command:
```bash
pytest tests/ -m "not slow" -q
```
**Result**: `1043 passed, 114 deselected, 6 warnings in 209.67s` (100% PASS, 0 FAILURES)

### 5.4 Release Archive Immutability
- `release_050_sha`: `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936` (MATCH = True)
- `release_0414_sha`: `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` (MATCH = True)

### 5.5 Production Source Invariance
- `src/novastack/` modifications: 0 files, 0 lines, 0 bytes.

---

## 6. Commit Staging Manifest

The following exact files are staged for the atomic remediation commit:
```text
.gitattributes
artifacts/phase_5k_sha256_manifest.json
artifacts/phase_5m_sha256_manifest.json
artifacts/phase_gh07d_ci_portability_remediation.json
docs/ATLAS_GH07D_CI_PORTABILITY_REMEDIATION.md
tests/conftest.py
tests/test_depth_fusion_ablation.py
tests/test_phase_4n_packaging.py
tests/test_phase_4r_load_validation.py
tests/test_phase_5a_provider_boundary.py
tests/test_phase_5g_abstention_safety.py
tests/test_phase_5k_release_freeze.py
tests/test_phase_5l_independent_validation.py
tests/test_phase_5m_release_packaging.py
tests/test_phase_5n_operational_runbook.py
tests/test_phase_5o_incident_recovery.py
tests/test_phase_5p_final_commissioning.py
```

Commit message:
```text
fix: make CI platform-independent and offline-safe
```
