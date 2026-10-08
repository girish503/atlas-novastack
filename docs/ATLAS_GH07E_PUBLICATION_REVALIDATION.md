# ATLAS GH-07E — Controlled Publication & Remote CI Revalidation Report

## 1. Executive Summary

As **ATLAS Release Engineer & Senior CI/CD Reviewer**, execution of **GH-07E — Controlled Publication & Remote CI Revalidation** is complete.

The remediation commit `bce7465060b6c76cda9629fdeab19c9447a87d3a` (`fix: make CI platform-independent and offline-safe`) was published to `origin/main` following strict read-only safety preconditions. Remote revalidation was observed on GitHub Actions (Workflow Run ID `37421957467`).

### CI Execution Outcomes:
1. **Container Build**: **PASS** (Job ID `112133049440`) — Verified isolated Linux container build.
2. **Security Regression Gate**: **PASS** (Job ID `112133049737`) — **120/120 passed (100%)**. All 7 previous failures from GH-07C-W (offline load validation and HuggingFace connection timeouts) are completely resolved.
3. **Fast Tests**: **FAILED** (Job ID `112133049727`) — **1026 passed, 2 failed, 15 skipped, 114 deselected** in 38.09s. **24 out of the 26 failures in GH-07C-W were successfully resolved.** Only 2 isolated test failures remain.

In strict adherence to the **HARD SAFETY RULES** of GH-07E:
- **NO automatic remediation commits were created.**
- **NO files were modified.**
- **NO CS-01 canary testing was executed.**
- Execution halted immediately for thorough, forensic diagnostic reporting.

### Authoritative Decision
```text
CI_REVALIDATION_FAILED
```

---

## 2. Governing State & Publication Evidence

| Parameter | Value |
| :--- | :--- |
| **Repository** | `girish503/atlas-novastack` (PRIVATE) |
| **Branch** | `main` |
| **Previous Remote HEAD** | `f257e8ccfc70bafdb85183f8ce5b7b60bb6c5b78` |
| **Published Remediation HEAD** | `bce7465060b6c76cda9629fdeab19c9447a87d3a` |
| **Push Result** | `SUCCESS` (`f257e8c..bce7465  main -> main`) |
| **Workflow Run ID** | `37421957467` |
| **Workflow URL** | https://github.com/girish503/atlas-novastack/actions/runs/37421957467 |
| **Runner Environment** | `ubuntu-latest` (GitHub-hosted Linux runner) |
| **Release Candidate Archives** | Verified byte-exact and unmodified |
| **Production Source Drift (`src/novastack/`)** | **0 files / 0 lines / 0 bytes modified** |
| **CS-01 Status** | **NOT EXECUTED** |

---

## 3. Pre-Publication Safety Verification (Steps 1–3)

### 3.1 Step 1: Local Publication Preconditions
- Branch verified: `main`
- HEAD verified: `bce7465060b6c76cda9629fdeab19c9447a87d3a`
- Origin verified: `f257e8ccfc70bafdb85183f8ce5b7b60bb6c5b78`
- Commit count: Exactly 1 commit ahead of remote.
- Staged / working tree state: Clean.

### 3.2 Step 2: Release Artifact Integrity
Both certified release candidate archives were verified byte-exact:
- **0.5.0-rc1**: `dist/atlas-novastack-0.5.0-rc1.tar.gz`
  - Expected: `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936`
  - Actual:   `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936` (MATCH: True)
- **0.4.14-rc1**: `dist/atlas-novastack-0.4.14-rc1.tar.gz`
  - Expected: `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3`
  - Actual:   `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` (MATCH: True)

### 3.3 Step 3: Publication Diff Review
- `git diff origin/main..HEAD --stat` verified 17 files changed, all confined to tests, manifests, documentation, and `.gitattributes`.
- 0 changes in `src/novastack/`.
- Automated security scan confirmed 0 secrets, 0 credentials, and 0 personal machine paths.

---

## 4. GitHub Actions Remote Revalidation Results (Step 5)

### Job Status Matrix

| Job Name | Job ID | Status | Conclusion | Duration |
| :--- | :--- | :--- | :--- | :--- |
| **Container Build** | `112133049440` | `completed` | **`success`** | 1m 32s |
| **Security Regression Gate** | `112133049737` | `completed` | **`success`** | 23s |
| **Fast Tests** | `112133049727` | `completed` | **`failure`** | 44s |

---

## 5. Forensic Diagnosis of Remaining Failures

Only **2 tests** failed out of 1028 fast tests executed (1026 passed). Both failures are independent, non-cascading primary test design / fixture issues:

### Failure 1: Unmocked HTTP IPC in `test_phase_5g_abstention_safety.py`

- **Test**: `tests/test_phase_5g_abstention_safety.py::TestSecurityInvariants::test_layer1s_does_not_use_query_keywords`
- **Location**: Line 547
- **Exception**:
  ```text
  RuntimeError: Quantized local inference engine error: <urlopen error [Errno 111] Connection refused>
  ```
- **Traceback**:
  ```text
  tests/test_phase_5g_abstention_safety.py:547: in test_layer1s_does_not_use_query_keywords
      result2 = provider.generate_answer(
          package=pkg1,
          expected_doc_ids=["DOC-EXPECTED"],
          forbidden_doc_ids=["DOC-FORBIDDEN"],
      )
  src/novastack/quantized_provider.py:436: in generate_answer
      with urllib.request.urlopen(call_req, timeout=remaining_timeout) as resp:
  ConnectionRefusedError: [Errno 111] Connection refused
  ```
- **Diagnostic Finding**:
  In `test_layer1s_does_not_use_query_keywords`, the test checks two scenarios:
  1. Negative case (`expected_doc_ids=[]`): Layer 1S security gate fires deterministically without calling Ollama.
  2. Positive case (`expected_doc_ids=["DOC-EXPECTED"]`): The security gate does *not* fire, and execution proceeds to query generation.
  `QuantizedLocalProvider.generate_answer()` makes a direct call to `urllib.request.urlopen(call_req)` on port 11434. In GH-07D, `provider._call_ollama` was mocked, but `QuantizedLocalProvider.generate_answer` invokes `urllib.request.urlopen` directly without delegating to `_call_ollama`.
  On the clean Linux CI runner with no Ollama daemon running, port 11434 is closed, raising `[Errno 111] Connection refused`.
- **Classification**: **Test Design / Missing Mock**.

---

### Failure 2: Mixed-Ending Baseline SHA in `test_phase_5l_independent_validation.py`

- **Test**: `tests/test_phase_5l_independent_validation.py::TestStep03SHA256::test_sha256_of_dockerfile_inference`
- **Location**: Line 149
- **Exception**:
  ```text
  assert verify_sha256_platform_independent(actual, expected)
  assert False
  ```
- **Diagnostic Finding**:
  - Expected hash in test assertion:
    `55cc4255c4d78eafedd4006cfb9188a6aaa9002c787cbf97d9f598d29b2da546`
  - Forensic line-ending inspection of `Dockerfile.inference` on Windows revealed:
    - CRLF count: **1**
    - LF count: **34**
    The expected hash `55cc42...` was computed against this hybrid file possessing a single CRLF line terminator and 34 LF terminators.
  - On the Linux runner (and under git checkout with `.gitattributes`), Git normalized the single CRLF line terminator to LF, making all 35 lines LF.
  - The canonical LF hash of `Dockerfile.inference` is:
    `3fa0566d1ea41d3d2df2a09027d7190a8cbdd70512e38bed0406793b613df9d8`
  - Because `verify_sha256_platform_independent` tests pure-LF (`\n`) and pure-CRLF (`\r\n`), it rejected the hybrid mixed-line-ending expected hash.
- **Classification**: **Test Fixture / Stale Mixed-Ending Hash**.

---

## 6. Comparison Across CI Phases

| Phase | Container Build | Security Regression Gate | Fast Tests Result | Total Failures |
| :--- | :--- | :--- | :--- | :--- |
| **GH-07A** | `FAILED` (COPY data/ missing) | `FAILED` (psutil missing) | `FAILED` (tests import missing) | 3 jobs failed (collection abort) |
| **GH-07C-W** | `PASS` (100%) | `FAILED` (7 failures) | `FAILED` (26 failures) | 33 test failures |
| **GH-07E (Current)** | `PASS` (100%) | **`PASS` (120/120, 100%)** | `FAILED` (**1026 passed, only 2 failed**) | **2 test failures** |

**Net Improvement in GH-07E**: 31 out of 33 failures resolved (94% failure reduction, 2 of 3 jobs fully GREEN).

---

## 7. Hard Safety Rule Compliance & Next Actions

1. **Production Code Unmodified**: `src/novastack/` has 0 changes.
2. **Release Archives Frozen**: 0.5.0-rc1 and 0.4.14-rc1 remain byte-identical.
3. **No Automatic Remediation**: In strict adherence to GH-07E, no subsequent commit was created and no files were patched.
4. **CS-01 Canary Gate**: **NOT EXECUTED**.

Awaiting operator instruction for GH-07F remediation of the two diagnosed test fixture issues.
