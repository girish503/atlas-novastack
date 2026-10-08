# ATLAS GH-07C-W — Controlled CI Waiver & Remote Revalidation Report

## 1. Executive Summary

Phase **GH-07C-W — Controlled CI Waiver & Remote Revalidation** was executed pursuant to explicit operator authorization waiving the local Docker build gate (due to local Docker daemon unavailability and host physical memory constraints: ~1.18 GB free RAM out of 8 GB). The authoritative container build verification was delegated to the clean, isolated GitHub Actions Linux runner.

The pre-push safety invariants were verified, and exactly one controlled remediation commit was pushed to GitHub:
- **Local HEAD**: [`f257e8ccfc70bafdb85183f8ce5b7b60bb6c5b78`](file:///c:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS) (`fix: make CI publication surface reproducible`)
- **Remote `origin/main`**: Advanced from `ddcf96cb8f4d7598760bc8b98454db15d7a46fd7` to `f257e8ccfc70bafdb85183f8ce5b7b60bb6c5b78` (0 divergence, 2 commits total).

The push naturally triggered workflow **`ATLAS CI`** ([Run #37323393211](https://github.com/girish503/atlas-novastack/actions/runs/37323393211)).

### Key Findings
1. **Container Build**: **PASSED (`success`)**. The previously failing `COPY data/ ./data/` instruction and Docker build smoke test succeeded completely on the clean Linux runner.
2. **GH-07A Failures Resolved**:
   - `ModuleNotFoundError: No module named 'tests'` in Fast Tests is **RESOLVED**.
   - `ModuleNotFoundError: No module named 'psutil'` in Fast Tests and Security Regression is **RESOLVED**.
   - `FileNotFoundError: data/processed/novastack/search_documents.json` in Security Regression is **RESOLVED**.
   - `Docker build smoke test: failed to calculate checksum ... "/data": not found` is **RESOLVED**.
3. **New Runner Environment Blockers Identified**:
   - **Fast Tests** executed 1043 test items (1002 passed, 26 failed, 15 skipped, 114 deselected). The 26 failures stem from: (a) binary SHA-256 comparisons on text files where git checked out with Linux LF (`\n`) vs hardcoded Windows CRLF (`\r\n`) hashes, and (b) `test_phase_5l` asserting the pre-remediation hash of `pyproject.toml`.
   - **Security Regression Gate** passed 113 tests (out of 120). The 7 failures in `test_phase_4r_load_validation.py` stem from `AtlasServicePipeline.create_default()` attempting an online download of `google/gemma-3-1b-it` from `https://huggingface.co` without credentials/cache on the GitHub runner, triggering `OSError: We couldn't connect to 'https://huggingface.co'`.

### Decision
`CI_REVALIDATION_FAILED`

---

## 2. Governing State & Workflow Execution

| Parameter | Value |
| :--- | :--- |
| **Repository** | `girish503/atlas-novastack` |
| **Visibility** | `PRIVATE` |
| **Branch** | `main` |
| **Operator Docker Waiver** | `true` (Local build waived; remote build delegated) |
| **Previous Remote HEAD** | `ddcf96cb8f4d7598760bc8b98454db15d7a46fd7` |
| **Published Remediation SHA** | `f257e8ccfc70bafdb85183f8ce5b7b60bb6c5b78` |
| **Local/Remote SHA Match** | `true` (Both at `f257e8c...`, 0 divergence) |
| **Workflow Run ID** | `37323393211` |
| **Run URL** | [Run #37323393211](https://github.com/girish503/atlas-novastack/actions/runs/37323393211) |
| **Trigger** | `push` |
| **Overall Conclusion** | `failure` |
| **Runner OS** | `Ubuntu 24.04.5 LTS` |
| **Python Version** | `3.11.16` |

---

## 3. Detailed CI Job Breakdown

### 3.1 Job: Container Build (Job ID: `111807884179`) — PASS
- **Status**: `completed`
- **Conclusion**: `success`
- **Steps**:
  1. Set up job: SUCCESS
  2. Checkout repository: SUCCESS
  3. Docker build smoke test: **SUCCESS**
  4. Post Checkout repository: SUCCESS
  5. Complete job: SUCCESS
- **Log Verification**:
  - `docker build -t atlas-service:ci-test .` executed cleanly.
  - `COPY data/ ./data/` succeeded with all 36 published synthetic files present in build context.
  - `docker image inspect atlas-service:ci-test` executed successfully.
  - **Verdict**: The container build failure from GH-07A is **100% RESOLVED**.

### 3.2 Job: Fast Tests (Job ID: `111807884013`) — FAILED
- **Status**: `completed`
- **Conclusion**: `failure`
- **Collection**:
  - In GH-07A, pytest collection **aborted completely** (0 tests ran) due to missing `tests` in pythonpath and missing `psutil`.
  - In GH-07C-W, collection **succeeded completely**.
  - **Passed**: 1002 tests
  - **Failed**: 26 tests
  - **Skipped**: 15 tests
  - **Deselected**: 114 tests
- **Root Cause of 26 Failures**:
  1. **CRLF vs LF Line-Ending Normalization (20 failures)**: Tests in `test_phase_5p_final_commissioning.py`, `test_phase_5k_release_freeze.py`, and `test_phase_5l_independent_validation.py` compute raw binary hashes (`hashlib.sha256(open(f, 'rb').read()).hexdigest()`) of source and data files. Because git checkout on Ubuntu defaults to LF (`\n`), text files committed from Windows (`\r\n`) produce different SHA-256 hashes than the hardcoded baseline strings.
  2. **Pyproject Hash Invariant (1 failure)**: `test_phase_5l_independent_validation.py::TestStep03SHA256::test_sha256_of_pyproject` asserts the pre-remediation hash of `pyproject.toml`. Because `pyproject.toml` was edited to add `psutil` and `pythonpath`, its hash changed.
  3. **Hugging Face Hub Online Call (5 failures)**: Tests attempting end-to-end inference without mocking try to connect to `https://huggingface.co` to load `google/gemma-3-1b-it`.

### 3.3 Job: Security Regression Gate (Job ID: `111807883815`) — FAILED
- **Status**: `completed`
- **Conclusion**: `failure`
- **Passed**: 113 tests
- **Failed**: 7 tests
- **Root Cause of 7 Failures**:
  - In GH-07A, 10 tests failed (9 on missing `data/processed/novastack/search_documents.json` and 1 on missing `psutil`).
  - Both missing data and missing `psutil` are **100% RESOLVED**.
  - The 7 remaining failures are all in `tests/test_phase_4r_load_validation.py`. When `AtlasServicePipeline.create_default()` instantiates `LocalHuggingFaceProvider`, it attempts to load model weights from `https://huggingface.co`. On the clean GitHub runner (which has no pre-populated HF cache and no HF API token), transformers raises:
    ```text
    OSError: We couldn't connect to 'https://huggingface.co' to load the files, and couldn't find them in the cached files.
    ```
  - This unhandled exception in the pipeline causes requests to return HTTP 500 instead of 200/504.

---

## 4. Security & Credential Leakage Audit

A full security scan across the downloaded raw logs of all three CI jobs was conducted:
- **Fast Tests Log**: 0 secrets, 0 authorization headers
- **Security Regression Log**: 0 secrets, 0 authorization headers
- **Container Build Log**: 0 secrets, 0 authorization headers
- **Security Incident Status**: `false` (Zero sensitive data exposed)

---

## 5. Release Archive & Source Immutability Record

| Asset | Expected SHA256 / State | Verified Actual SHA256 / State | Invariant Preserved? |
| :--- | :--- | :--- | :--- |
| **0.5.0-rc1 Tarball** | `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936` | `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936` | **YES** |
| **0.4.14-rc1 Tarball** | `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` | `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` | **YES** |
| **Core Source (`src/novastack/`)** | 0 files modified | 0 files modified (byte-identical) | **YES** |
| **Production Baseline** | Unmodified, frozen | Unmodified, frozen | **YES** |
| **Canary Suite CS-01** | NOT executed | NOT executed | **YES** |

---

## 6. Comparison: GH-07A vs GH-07C-W

| Dimension | GH-07A Initial Push (`ddcf96c...`) | GH-07C-W Remediation Push (`f257e8c...`) | Progress |
| :--- | :--- | :--- | :--- |
| **Container Build** | FAILED (`/data: not found`) | **PASSED** (`success`) | **FIXED** |
| **Fast Tests Collection** | FAILED (Aborted, 0 tests ran) | **PASSED** (1,140 items collected) | **FIXED** |
| **Fast Tests `tests` import** | `ModuleNotFoundError: No module named 'tests'` | **REMOVED** (Zero import errors) | **FIXED** |
| **Fast Tests `psutil` dep** | `ModuleNotFoundError: No module named 'psutil'` | **REMOVED** (psutil installed cleanly) | **FIXED** |
| **Fast Tests Passed Count** | `0` | **`1002`** | **+1002 tests passed** |
| **Security Gate Missing Data**| `FileNotFoundError: search_documents.json` | **REMOVED** (Data present on runner) | **FIXED** |
| **Security Gate Passed Count**| `110` | **`113`** | **+3 tests passed** |

---

## 7. Artifacts Created

- **JSON Artifact**: [`artifacts/phase_gh07c_w_ci_revalidation.json`](file:///c:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/artifacts/phase_gh07c_w_ci_revalidation.json)
- **Documentation Report**: [`docs/ATLAS_GH07C_W_CI_REVALIDATION.md`](file:///c:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/docs/ATLAS_GH07C_W_CI_REVALIDATION.md)

---

## 8. Remaining Blockers

While all three root causes diagnosed in GH-07A have been resolved (and Container Build is now 100% green), full CI pass requires addressing the secondary Linux runner environment factors:
1. **Line-ending CRLF/LF normalization** affecting binary hash assertions on Linux runners.
2. **`pyproject.toml` baseline hash expectation** in freeze tests.
3. **Offline mode / model mocking** in `test_phase_4r_load_validation.py` to prevent attempted online downloads from Hugging Face Hub during CI runs.

---

## HARD STOP

The remote CI revalidation results have been thoroughly collected and audited.
- **CS-01 canary NOT executed.**
- **No production deployment performed.**
- **No additional commits created.**
- Awaiting explicit operator review of GH-07C-W.
