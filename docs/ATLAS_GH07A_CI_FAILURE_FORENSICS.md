# ATLAS GH-07A — GitHub CI Failure Forensics Report

## 1. Executive Summary

Following the initial publication of the private repository `girish503/atlas-novastack` at commit `ddcf96cb8f4d7598760bc8b98454db15d7a46fd7`, the automated GitHub Actions workflow **`ATLAS CI`** triggered automatically. All three jobs within the workflow failed:
- **Fast Tests**: FAILED (Exit Code 2 — pytest collection abort)
- **Security Regression Gate**: FAILED (Exit Code 1 — 10 failures in load validation suite)
- **Container Build**: FAILED (Exit Code 1 — Docker context missing `/data`)

This forensic investigation was conducted in strict **READ-ONLY** mode. No source files, workflows, Dockerfiles, or dependencies were altered, no Git commits or pushes were executed, no CI runs were triggered, and CS-01 canary testing was not executed.

### Decision
`DIAGNOSIS_COMPLETE`

### Overall Classification
`MULTIPLE_ROOT_CAUSES` (Publication Surface Incompleteness + Packaging Dependency Gap + Pytest Import Path Configuration)

### Release Impact
`RELEASE_BLOCKING` (Blocks automated GitHub Actions CI and downstream CS-01 canary execution on GitHub Actions until remediated; core source code and release candidate archive hashes remain 100% verified and intact).

---

## 2. Governing State & Workflow Execution

| Parameter | Value |
| :--- | :--- |
| **Repository** | `girish503/atlas-novastack` |
| **Visibility** | `PRIVATE` |
| **Branch** | `main` |
| **Commit SHA** | `ddcf96cb8f4d7598760bc8b98454db15d7a46fd7` |
| **Commit Message** | `chore: initialize ATLAS repository` |
| **Workflow Name** | `ATLAS CI` (`.github/workflows/ci.yml`) |
| **Workflow Run ID** | `37294593822` |
| **Run URL** | [Run #37294593822](https://github.com/girish503/atlas-novastack/actions/runs/37294593822) |
| **Trigger Event** | `push` |
| **Run Started** | `2026-10-05T10:08:06Z` |
| **Run Completed** | `2026-10-05T10:10:24Z` (Total Duration: 2m 18s) |
| **Overall Status** | `completed` |
| **Conclusion** | `failure` |
| **Runner OS** | `Ubuntu 24.04.5 LTS (Noble Numbat)` |
| **Runner Image** | `ubuntu-24.04`, Version `20260927.320.1` |
| **Runner Python** | `3.11.16` (`/opt/hostedtoolcache/Python/3.11.16/x64/bin/python`) |

---

## 3. Detailed Job Forensics

### 3.1 Job 1: Fast Tests (Job ID: `111712719451`)

- **Duration**: 2m 14s (Started `10:08:09Z`, Completed `10:10:23Z`)
- **Step Hierarchy**:
  1. Set up job: SUCCESS
  2. Checkout repository: SUCCESS
  3. Set up Python 3.11: SUCCESS
  4. Install dependencies (`pip install .[test]`): SUCCESS (Duration: ~2m 02s)
  5. Run fast test suite (`pytest tests/ -m "not slow" -v --tb=short`): **FAILURE** (Duration: 5s)
- **Exit Code**: `2` (pytest collection error)
- **Test Metrics**:
  - Collected: 1,140 test items before collection halted
  - Deselected: 114 test items (by `-m "not slow"`)
  - Selected: 1,026 test items
  - Executed: **0** (aborted during collection)
  - Passed: 0
  - Failed: 0
  - Collection Errors: **2**

#### Exact First Causal Failure:
```text
___________ ERROR collecting tests/test_phase_4x_characterization.py ___________
ImportError while importing test module '/home/runner/work/atlas-novastack/atlas-novastack/tests/test_phase_4x_characterization.py'.
Traceback:
/opt/hostedtoolcache/Python/3.11.16/x64/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests/test_phase_4x_characterization.py:31: in <module>
    from tests.test_phase_4o_resilience import build_test_pipeline
E   ModuleNotFoundError: No module named 'tests'
```
- **Causal Analysis**: `pyproject.toml` defines `[tool.pytest.ini_options]` with `pythonpath = ["src"]`. In a clean GitHub Actions environment without `PYTHONPATH=.` set, Python does not include the workspace root in `sys.path`. When `test_phase_4x_characterization.py` executes `from tests.test_phase_4o_resilience import ...`, the `tests` top-level package cannot be found.

#### Second Causal Failure:
```text
____________ ERROR collecting tests/test_phase_5k_release_freeze.py ____________
ImportError while importing test module '/home/runner/work/atlas-novastack/atlas-novastack/tests/test_phase_5k_release_freeze.py'.
Traceback:
/opt/hostedtoolcache/Python/3.11.16/x64/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests/test_phase_5k_release_freeze.py:81: in <module>
    from scripts.phase_5k_release_freeze import BASELINE_HASHES, sha256_file
scripts/phase_5k_release_freeze.py:28: in <module>
    import psutil
E   ModuleNotFoundError: No module named 'psutil'
```
- **Causal Analysis**: `scripts/phase_5k_release_freeze.py` imports `psutil`, which is not declared in `pyproject.toml` (`dependencies` or `optional-dependencies.test`).

---

### 3.2 Job 2: Security Regression Gate (Job ID: `111712719840`)

- **Duration**: 2m 03s (Started `10:08:08Z`, Completed `10:10:11Z`)
- **Step Hierarchy**:
  1. Set up job: SUCCESS
  2. Checkout repository: SUCCESS
  3. Set up Python 3.11: SUCCESS
  4. Install dependencies (`pip install .[test]`): SUCCESS (Duration: ~1m 52s)
  5. Security regression invariants: **FAILURE** (Duration: 4s)
- **Command Executed**:
  ```bash
  pytest tests/test_phase_4m_auth_fail_closed.py \
        tests/test_phase_4t_identity_boundary.py \
        tests/test_phase_4k_f_b_security_redteam.py \
        tests/test_phase_4s_live_index_hotswap.py \
        tests/test_phase_4p_observability.py \
        tests/test_phase_4o_resilience.py \
        tests/test_phase_4q_ingestion_reliability.py \
        tests/test_phase_4r_load_validation.py \
        tests/test_phase_4w_clean_runtime.py \
        -v --tb=short
  ```
- **Exit Code**: `1`
- **Test Metrics**:
  - Total Tests Executed: **120**
  - Passed: **110** (91.7%)
  - Failed: **10** (8.3%)

#### Breakdown by Test File:
| Test Suite | Tests | Result | Status |
| :--- | :---: | :---: | :---: |
| `tests/test_phase_4m_auth_fail_closed.py` | 13 | 13/13 PASS | ✅ PASSED |
| `tests/test_phase_4t_identity_boundary.py` | 14 | 14/14 PASS | ✅ PASSED |
| `tests/test_phase_4k_f_b_security_redteam.py` | 15 | 15/15 PASS | ✅ PASSED |
| `tests/test_phase_4s_live_index_hotswap.py` | 12 | 12/12 PASS | ✅ PASSED |
| `tests/test_phase_4p_observability.py` | 12 | 12/12 PASS | ✅ PASSED |
| `tests/test_phase_4o_resilience.py` | 15 | 15/15 PASS | ✅ PASSED |
| `tests/test_phase_4q_ingestion_reliability.py` | 15 | 15/15 PASS | ✅ PASSED |
| `tests/test_phase_4w_clean_runtime.py` | 14 | 14/14 PASS | ✅ PASSED |
| `tests/test_phase_4r_load_validation.py` | 10 | 0/10 PASS | ❌ **10 FAILED** |

#### Security Regression Assessment:
**NO SECURITY REGRESSION.** 
All 8 dedicated security suites passed 100%. The 10 failures in `test_phase_4r_load_validation.py` were caused strictly by environment / missing file assets:
1. **9 Tests Failed with FileNotFoundError**:
   ```text
   FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/atlas-novastack/atlas-novastack/data/processed/novastack/search_documents.json'
   ```
   Location: `src/novastack/service/api.py:453` invoked via `AtlasServicePipeline.create_default(lazy_generator=True)`.
   Root cause: `data/processed/` was excluded from Git publication by `.gitignore`.
2. **1 Test Failed with ModuleNotFoundError**:
   ```text
   FAILED tests/test_phase_4r_load_validation.py::test_memory_and_thread_stability_under_burst - ModuleNotFoundError: No module named 'psutil'
   ```
   Root cause: `psutil` missing from `pyproject.toml` dependencies.

---

### 3.3 Job 3: Container Build (Job ID: `111712719857`)

- **Duration**: 7s (Started `10:08:09Z`, Completed `10:08:16Z`)
- **Step Hierarchy**:
  1. Set up job: SUCCESS
  2. Checkout repository: SUCCESS
  3. Docker build smoke test: **FAILURE** (Duration: 3s)
- **Command Executed**:
  ```bash
  docker build -t atlas-service:ci-test .
  docker image inspect atlas-service:ci-test
  ```
- **Exit Code**: `1`
- **Dockerfile**: `Dockerfile`
- **Base Image**: `python:3.11-slim`
- **Failing Instruction**: `COPY data/ ./data/` (Dockerfile Line 29)

#### Exact Error Log:
```text
#11 [7/9] COPY data/ ./data/
#11 ERROR: failed to calculate checksum of ref 96bc9d58-7015-4b4a-8357-8e592f0b19b5::ux7ssjagqt4ve2le4bvkjgzig: "/data": not found
...
ERROR: failed to build: failed to solve: failed to compute cache key: failed to calculate checksum of ref ...: "/data": not found
```
- **Causal Analysis**: The build context is the repository root `.`. `.gitignore` contains rules ignoring `data/raw/`, `data/processed/`, and `data/evaluation/`. Consequently, no `data/` directory was tracked or committed to Git. When Docker BuildKit attempted to copy `data/`, the directory did not exist in the build context.

---

## 4. Cross-Job Root-Cause Correlation Matrix

| Job | First Causal Failure | Underlying Root Cause | Shared with Other Jobs? |
| :--- | :--- | :--- | :--- |
| **Fast Tests** | `ModuleNotFoundError: No module named 'tests'` during test collection | `pyproject.toml` `pythonpath` lacks `.` | Independent to Fast Tests collection |
| | `ModuleNotFoundError: No module named 'psutil'` during test collection | Undeclared dependency `psutil` in `pyproject.toml` | **YES** — Shared with `Security Regression Gate` |
| **Security Gate** | `FileNotFoundError: .../data/processed/novastack/search_documents.json` | `data/` directory excluded from Git by `.gitignore` | **YES** — Shared with `Container Build` |
| | `ModuleNotFoundError: No module named 'psutil'` in burst memory test | Undeclared dependency `psutil` in `pyproject.toml` | **YES** — Shared with `Fast Tests` |
| **Container Build** | `ERROR: failed to calculate checksum ... "/data": not found` | `data/` directory excluded from Git by `.gitignore` | **YES** — Shared with `Security Regression Gate` |

---

## 5. Comparison Against Certified Local Environment

| Component | Certified Local State | Clean GitHub Runner State | Discrepancy Cause |
| :--- | :--- | :--- | :--- |
| `data/processed/novastack/` | Present on disk from earlier phases | **Missing** | `.gitignore` excluded `data/` from Git repository |
| `data/evaluation/novastack/` | Present on disk | **Missing** | `.gitignore` excluded `data/` from Git repository |
| Release Tarball (`0.5.0-rc1`) | **Contains** `data/processed` & `data/evaluation` | Tarball contains data, but Git workspace did not unpack or commit it | Tarball is self-contained, but Git checkout is not |
| Python Environment | Anaconda base (`psutil` installed globally) | Fresh Python 3.11 (`pip install .[test]`) | `psutil` undeclared in `pyproject.toml` |
| Pytest `sys.path` | Working dir on `sys.path` in local PowerShell | Subprocess pytest on Linux respects only `pythonpath` config | `pyproject.toml` sets `pythonpath = ["src"]`, omitting `.` |
| Container Build | Validated via `Dockerfile.inference` (`phase_5d`) | Root `Dockerfile` tested in CI | `Dockerfile` has `COPY data/ ./data/` requiring repo `data/` |

---

## 6. Security & Credential Leakage Audit

A comprehensive scan across all three raw GitHub Actions logs (`job_111712719451_fast_tests.log`, `job_111712719840_security_regression_gate.log`, `job_111712719857_container_build.log`) was performed.
- Pattern checks: GitHub PATs, fine-grained tokens, Actions tokens, Bearer tokens, private keys, passwords, AWS keys.
- **Results**:
  - Masked values: `token: ***` and `AUTHORIZATION: basic ***` were properly masked by GitHub Actions runner secret masking.
  - Raw exposed secrets: **0**
  - Personal data exposure: **0**
  - Security incident status: **`FALSE`** (No security incident or credential exposure detected).

---

## 7. Release & Security Baseline Verification

- **ATLAS Core Source (`src/novastack/`)**: 100% byte-identical, 0 modifications.
- **ATLAS 0.5.0-rc1 Tarball SHA256**:
  `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936` (Unchanged, verified).
- **ATLAS 0.4.14-rc1 Baseline Tarball SHA256**:
  `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` (Unchanged, verified).
- **Production Baseline**: Untouched, frozen.
- **CS-01 Canary**: NOT executed.

---

## 8. Minimum Safe Remediation (Proposals Only — Unexecuted)

To achieve green CI in GitHub Actions without modifying core source code:

1. **Repository Publication Surface (`.gitignore` & `data/` tracking)**:
   - Amend `.gitignore` to allow runtime index and evaluation test fixtures:
     - Allow `!data/processed/novastack/`
     - Allow `!data/evaluation/novastack/`
     - Allow `!data/raw/novastack/` (synthetic test fixtures)
   - Commit the synthetic dataset files to `main`. This resolves both the `Container Build` failure (`COPY data/ ./data/`) and the 9 `test_phase_4r_load_validation.py` failures.

2. **Package Test Dependencies (`pyproject.toml`)**:
   - Add `"psutil>=5.9.0,<7.0.0"` to `[project.optional-dependencies] test = [...]`. This resolves the `psutil` missing module error in both `Fast Tests` and `Security Regression Gate`.

3. **Pytest Import Path Configuration (`pyproject.toml`)**:
   - Update `[tool.pytest.ini_options]` in `pyproject.toml`:
     ```toml
     pythonpath = [".", "src"]
     ```
     This resolves `ModuleNotFoundError: No module named 'tests'` in `tests/test_phase_4x_characterization.py`.

*Note: Per strict instructions for GH-07A, none of these remediations have been implemented. They will await explicit authorization in the subsequent phase.*
