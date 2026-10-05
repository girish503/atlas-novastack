# ATLAS GH-03 — Git Publication Preflight Audit Report

**Author:** ATLAS Release Engineer & Security Reviewer  
**Date:** 2026-10-05  
**Governing Reviews:** PRR-01 (`PROMOTION_READY`), CR-01 (`CANARY_READY`), ENV-01 (`FREE_ENVIRONMENT_FOUND`), GH-02 (`READY_FOR_PUBLICATION_PREFLIGHT`)  
**Official Decision:** **`BLOCKED`**  
**Repository Visibility:** **`PRIVATE`**  

---

## 1. Executive Summary & Verdict

A rigorous read-only preflight audit was executed over the full workspace to determine if it is 100% clean and ready to be committed and published as a private GitHub repository for CI execution on GitHub Actions.

### Official Verdict: **`BLOCKED`**

While all **44 primary documentation path blockers** identified in GH-01A have been completely sanitized and all local execution artifacts in `scratch/` are excluded via `.gitignore`, this preflight audit detected **10 remaining machine-specific absolute path occurrences** (`c:/Users/<LOCAL_USER>/...`) in publishable script, test, and remediation report files.

Per GH-03 Section 4 protocol:
> *"Expected result: 0 publication-blocking local-machine findings. If anything remains, classify it and STOP with BLOCKED."*

Therefore, publication remains **`BLOCKED`** until these 10 references are either sanitized or excluded.

---

## 2. Hard Safety Rules Confirmation

- **No GitHub repository was created.**
- **No Git remote was created.**
- **No Git commit was performed.**
- **No Git push was performed.**
- **No GitHub authentication occurred.**
- **No GitHub Actions workflows were created or triggered.**
- **No CS-01 canary was executed.**
- **No M8, PRR-01, or CR-01 benchmarks were rerun.**
- **Production 0.4.14 baseline remains untouched and frozen.**
- **Release candidate (0.5.0-rc1) remains byte-identical and verified.**
- **ATLAS source code was unmodified.**
- **`.gitignore` was unmodified.**
- **Zero files were deleted.**

---

## 3. Section 1 — Final Git State

| Property | Value | Notes |
|---|---|---|
| **Git Repository Initialized** | `False` | `.git` does not exist in workspace |
| **Current Branch** | None | No active Git head |
| **Repository Root** | `$WORKSPACE_ROOT` | Local workspace root |
| **Tracked Files** | `0` | No files currently tracked |
| **Total Files in Workspace** | `1,127` | Complete physical file count |
| **Publishable Files** | `596` | Files outside `.gitignore` rules |
| **Ignored Files** | `531` | Excluded by `.gitignore` rules |
| **Modified / Deleted / Renamed** | `0` | Clean workspace |
| **Existing Remotes** | `0` (None) | Zero remote origins configured |

---

## 4. Section 2 — Publication Surface Classification

If the workspace were committed under the current `.gitignore`, the 596 publishable files categorize as follows:

| Category | File Count | Description |
|---|---:|---|
| **A. REQUIRED ATLAS SOURCE** | 63 | `src/novastack/...`, `pyproject.toml`, `requirements.txt`, `Dockerfile`, `.github/workflows/ci.yml`, `scripts/...` |
| **B. TESTS** | 54 | `tests/test_*.py` |
| **C. DOCUMENTATION** | 38 | `docs/*.md`, `README.md` |
| **D. CERTIFIED ARTIFACTS** | 36 | `artifacts/phase_*.json`, certified reports |
| **E. RELEASE ARTIFACTS** | 3 | `dist/atlas-novastack-0.5.0-rc1.tar.gz`, `dist/atlas-novastack-0.4.14-rc1.tar.gz`, manifests |
| **F. SECURITY / EVALUATION CORPUS** | 0 tracked (402 ignored) | `data/raw/`, `data/processed/`, `data/evaluation/` (excluded via `.gitignore`) |
| **G. TEMPORARY / LOCAL DATA** | 0 tracked (129 ignored) | `scratch/`, `build/`, `.pytest_cache/`, `*.stdout.log`, `*.pyc` (excluded via `.gitignore`) |
| **H. IDE / OS DATA** | 0 | None present |
| **I. UNKNOWN** | 0 | None |
| **Total Publishable Surface** | **596** | **Files entering Git tracking** |

---

## 5. Section 3 — Secret Rescan

A publication-oriented scan of the 596 publishable files confirmed:

- **Real Production Secrets:** **`0`**
- **Test Secrets in Publishable Surface:** **`9`** (12 previously found; 3 copies were inside `dist/*_test/` folders now excluded by `.gitignore`).
- **Synthetic AWS Adversarial Fixture:** Intact in `tests/test_adversarial_corpus.py` (line 511), verified as `TEST_SECRET`.
- **New Secrets Introduced During GH-02:** **`0`**.

---

## 6. Section 4 — Local-Machine Data Rescan (Exact Blockers)

A full rescan of the 596 publishable files revealed **10 remaining occurrences** of local machine absolute paths:

| # | File | Line | Type | Content Snippet |
|:---:|---|:---:|---|---|
| 1 | `docs/ATLAS_GH02_PUBLICATION_REMEDIATION.md` | 43 | Documentation | Self-referential quote in remediation report table: `file:///c:/Users/<LOCAL_USER>/...` |
| 2 | `scripts/phase_4r_load_test.py` | 20 | Historical Script | `WORKSPACE = Path("c:/Users/<LOCAL_USER>/.../ATLAS").resolve()` |
| 3 | `scripts/phase_4r_r2_characterization.py` | 29 | Historical Script | `WORKSPACE = Path("c:/Users/<LOCAL_USER>/.../ATLAS").resolve()` |
| 4 | `scripts/phase_5f_build_artifacts.py` | 6 | Historical Script | `WORKSPACE = Path(r"c:\Users\<LOCAL_USER>\...\ATLAS")` |
| 5 | `scripts/phase_5f_forensics_analysis.py` | 17 | Historical Script | `WORKSPACE = Path(r"c:\Users\<LOCAL_USER>\...\ATLAS")` |
| 6 | `scripts/phase_5f_forensics_analysis.py` | 394 | Historical Script | `Path(r"c:\Users\<LOCAL_USER>\...\ATLAS\artifacts").mkdir(exist_ok=True)` |
| 7 | `scripts/phase_5f_root_cause_classification.py` | 7 | Historical Script | `WORKSPACE = Path(r"c:\Users\<LOCAL_USER>\...\ATLAS")` |
| 8 | `tests/test_event_evidence_bundler.py` | 15 | Test Suite | `WORKSPACE = Path("c:/Users/<LOCAL_USER>/.../ATLAS").resolve()` |
| 9 | `tests/test_phase_4k_d_regression_attribution.py` | 5 | Test Suite | `WORKSPACE = Path("c:/Users/<LOCAL_USER>/.../ATLAS").resolve()` |
| 10 | `tests/test_sentence_level_citation.py` | 314 | Test Suite | `WORKSPACE_ROOT = Path("c:/Users/<LOCAL_USER>/.../ATLAS").resolve()` |

**Analysis:**
- In GH-02, only documentation paths were authorized for sanitization because modifying source or tests was restricted.
- The 10 remaining occurrences are localized fallback paths in 6 scripts, 3 tests, and 1 report quote.
- They must be sanitized (e.g. to `Path(__file__).resolve().parent.parent`) before committing to prevent leaking local usernames into git history.

---

## 7. Section 5 — Personal Data Verification

- **Real Personal Email Addresses:** **`0`**.
- **Real Personal Names / Phones / Addresses:** **`0`** (outside the 10 machine path lines above).
- **Synthetic Test Identities Preserved:** **`665`** (`@novastack.example`, `@orbital.example`, `@acme.example`, `@db.internal`).
- **Regex False Positives Preserved:** **`34`**.

---

## 8. Section 6 — Release Artifact Verification

Cryptographic verification confirmed byte-identical integrity:

- **0.5.0-rc1 Release Candidate:**
  - `dist/atlas-novastack-0.5.0-rc1.tar.gz`
  - Expected SHA256: `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936`
  - Computed SHA256: `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936`
  - Status: **`PASS (VERIFIED)`**

- **0.4.14-rc1 Production Baseline:**
  - `dist/atlas-novastack-0.4.14-rc1.tar.gz`
  - Expected SHA256: `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3`
  - Computed SHA256: `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3`
  - Status: **`PASS (FROZEN & IMMUTABLE)`**

---

## 9. Section 7 & 8 — Large Files & Gitignore Review

- **Files > 50 MB in publishable surface:** **`0`**.
- **Model weights:** Excluded (reside in Ollama system directories).
- **Virtual environments / caches:** Correctly excluded via `.gitignore`.
- **`.gitignore` Coverage:** Sound and robust. Protects `scratch/`, `build/`, `*.stdout.log`, `*.log`, and generated corpus datasets without masking core `src/`, `tests/`, `docs/`, or `artifacts/`.

---

## 10. Section 9 & 10 — Release Reproducibility & Security Boundaries

- **Reproducibility:** A clean Linux runner (`ubuntu-latest`) can unpack `dist/atlas-novastack-0.5.0-rc1.tar.gz`, install python dependencies, pull `gemma3:1b` via Ollama, and execute CS-01 without any reliance on local laptop state.
- **Security Boundary:** The repository has **zero production dependencies**. It requires no production credentials, no AWS keys, no internal VPN access, and no live traffic connectivity.

---

## 11. Final Decision & Exact Next Steps

### Decision: **`BLOCKED`**

**Reason:** 10 machine-specific path occurrences remain across 6 historical scripts, 3 test files, and 1 report file.

### Recommended Next Action:
1. Conduct a narrow remediation pass on the 10 identified lines to replace hardcoded local Windows paths with dynamic `Path(__file__).resolve().parent.parent` workspace resolution.
2. Re-verify the publication surface.
3. Upon achieving 0 remaining findings, proceed with private GitHub repository publication.

---

**STOP.** Preflight audit complete. Zero Git actions, zero remotes, zero pushes. Awaiting human instructions.
