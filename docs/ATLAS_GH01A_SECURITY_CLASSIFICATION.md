# ATLAS GH-01A — Security Finding Classification Report

**Author:** ATLAS Security & Release Engineering  
**Date:** 2026-10-05  
**Governing Reviews:** PRR-01 (`PROMOTION_READY`), CR-01 (`CANARY_READY`), ENV-01 (`FREE_ENVIRONMENT_FOUND`)  
**Official Decision:** **`BLOCKED`**  
**Publication Safe:** **`false`**  

---

## 1. Executive Summary & Verdict

Following the security preflight audit of **1,123 workspace files**, every identified secret (13 total) and personal data instance (815 total) was classified.

### Official Verdict: **`BLOCKED`**

The repository is currently **BLOCKED** from publication to GitHub. While **zero real production secrets** exist in the repository, publication is blocked by **116 instances of real personal / local machine data** (local Windows username and absolute machine filesystem paths) that must be sanitized or excluded prior to repository publication.

### Hard Invariants Maintained
- **No GitHub repository created**
- **No GitHub remote created**
- **No git commit performed**
- **No git push performed**
- **No workflows created or modified**
- **No ATLAS source code modified**
- **No files deleted**
- **No credentials rotated or replaced**
- **No CS-01 executed**
- **No M8 rerun**
- **No PRR-01 rerun**
- **No CR-01 rerun**
- **No production deployment modified**

---

## 2. Secret Findings Classification (13 Total)

All 13 findings were individually inspected. **Zero real production secrets** were found. All 13 findings represent synthetic test secrets and red-team test fixtures:

| # | File | Line | Detector / Type | Classification | Remediation Required? | Description / Context |
|:---:|---|:---:|---|:---:|:---:|---|
| 1 | `dist/atlas-novastack-0.4.14-rc1/tests/test_phase_5j_production_promotion.py` | 109 | HARDCODED_JWT_SECRET | `TEST_SECRET` | NO | Static HMAC test secret in frozen baseline test suite |
| 2 | `dist/atlas-novastack-0.4.14-rc1/tests/test_phase_5k_release_freeze.py` | 142 | HARDCODED_JWT_SECRET | `TEST_SECRET` | NO | Static HMAC test secret in frozen baseline test suite |
| 3 | `dist/clean_deployment_test/atlas-novastack-0.4.14-rc1/tests/test_phase_5j_production_promotion.py` | 109 | HARDCODED_JWT_SECRET | `TEST_SECRET` | NO | Test fixture copy in verification extract |
| 4 | `dist/clean_deployment_test/atlas-novastack-0.4.14-rc1/tests/test_phase_5k_release_freeze.py` | 142 | HARDCODED_JWT_SECRET | `TEST_SECRET` | NO | Test fixture copy in verification extract |
| 5 | `dist/runbook_extract_test/atlas-novastack-0.4.14-rc1/tests/test_phase_5j_production_promotion.py` | 109 | HARDCODED_JWT_SECRET | `TEST_SECRET` | NO | Test fixture copy in runbook extract |
| 6 | `dist/runbook_extract_test/atlas-novastack-0.4.14-rc1/tests/test_phase_5k_release_freeze.py` | 142 | HARDCODED_JWT_SECRET | `TEST_SECRET` | NO | Test fixture copy in runbook extract |
| 7 | `scripts/phase_5d_container_runtime.py` | 405 | HARDCODED_JWT_SECRET | `TEST_SECRET` | NO | Local test secret for synthetic JWT signing |
| 8 | `scripts/phase_5j_pre_promotion_runner.py` | 162 | HARDCODED_JWT_SECRET | `TEST_SECRET` | NO | Local verification script test secret |
| 9 | `scripts/phase_5j_production_promotion.py` | 231 | HARDCODED_JWT_SECRET | `TEST_SECRET` | NO | Controlled promotion drill test secret |
| 10 | `scripts/phase_5k_release_freeze.py` | 713 | HARDCODED_JWT_SECRET | `TEST_SECRET` | NO | Release freeze verification test secret |
| 11 | `tests/test_adversarial_corpus.py` | 511 | AWS_KEY | `TEST_SECRET` | NO | Synthetic fake AWS key (`AKIA...`) in adversarial test fixture verifying data leakage prevention |
| 12 | `tests/test_phase_5j_production_promotion.py` | 109 | HARDCODED_JWT_SECRET | `TEST_SECRET` | NO | Integration test JWT signing key |
| 13 | `tests/test_phase_5k_release_freeze.py` | 142 | HARDCODED_JWT_SECRET | `TEST_SECRET` | NO | Release freeze test JWT signing key |

### Secret Findings Summary
- `REAL_SECRET`: **0**
- `TEST_SECRET`: **13**
- `PLACEHOLDER`: **0**
- `FALSE_POSITIVE`: **0**
- `UNKNOWN`: **0**
- **Total:** **13**

---

## 3. Personal Data Findings Classification (815 Total)

All 815 findings were categorized into distinct groups:

```
┌────────────────────────────────────────────────────────────────────────┐
│                    PERSONAL DATA CLASSIFICATION MATRIX                 │
├─────────────────────────┬───────┬──────────────────────────────────────┤
│ Category                │ Count │ Description                          │
├─────────────────────────┼───────┼──────────────────────────────────────┤
│ REAL_PERSONAL_DATA      │   116 │ Local username & absolute user paths │
│ SYNTHETIC_TEST_DATA     │   665 │ NovaStack / Orbital mock identities  │
│ FALSE_POSITIVE          │    34 │ Regex noise from hashes/base64 strings│
│ PUBLIC_REFERENCE_DATA   │     0 │ None                                 │
│ UNKNOWN                 │     0 │ None                                 │
├─────────────────────────┼───────┼──────────────────────────────────────┤
│ TOTAL                   │   815 │ Matches detector output exactly      │
└─────────────────────────┴───────┴──────────────────────────────────────┘
```

### Detailed Breakdown

1. **`REAL_PERSONAL_DATA` (116 findings — Remediation Required: YES)**
   - **Nature:** Occurrences of the developer's local Windows username (`<LOCAL_USER>`) embedded within local absolute paths (`$REPO_ROOT/...` and `$REPO_ROOT/...`).
   - **Distribution:**
     * `docs/` (milestone logs & reports): 44 instances (e.g. `PHASE_4L_PRODUCTION_READINESS_AUDIT.md`, `PHASE_4Q_INGESTION_INDEX_RELIABILITY.md`, `PHASE_4K_G_B_PROMOTION.md`, `PHASE_4R_LOAD_VALIDATION.md`).
     * `scratch/` (temporary test scripts & stdout logs): 63 instances (e.g. `pytest-full-phase-4t.stdout.log`, scratch scripts).
     * `scripts/`: 5 instances.
     * `tests/`: 3 instances.
     * `artifacts/`: 1 instance.
   - **Impact:** Publishing these without remediation would expose the author's real identity and local directory hierarchy in public/git history.

2. **`SYNTHETIC_TEST_DATA` (665 findings — Remediation Required: NO)**
   - **Nature:** Synthetic email addresses belonging to simulated enterprise users in NovaStack and Orbital test corpora (e.g. `carmen.huang@novastack.example`, `raj.fischer@orbital.example`, `william.varma@novastack.example`, `ap@acme.example`, `*@db.internal`).
   - **Impact:** These are intentional test fixtures generated by the synthetic corpus generator (`background_corpus.py`, `phase_4e_evidence_assembly.json`). They do not represent real persons.

3. **`FALSE_POSITIVE` (34 findings — Remediation Required: NO)**
   - **Nature:** Non-email strings matching the email pattern due to punctuation and base64 formatting (e.g. `-@Ynt5.FF`, `s@Bv7.bP`, `9kJ@R.xi`).

---

## 4. Exact Publication Blockers

Before publishing to GitHub, the following two blockers must be resolved:

1. **Blocker 1: Local Username & Path Exposure in Documentation (44 instances)**
   - Milestone report files in `docs/` reference absolute paths containing `$REPO_ROOT/...`.
   - *Required Action:* Sanitize paths to relative references (e.g., `$REPO_ROOT` or `./`) before committing.

2. **Blocker 2: Temporary & Scratch Files Tracked by Workspace (63 instances)**
   - Files in `scratch/` and `.stdout.log` contain local environment logs.
   - *Required Action:* Ensure `.gitignore` explicitly excludes `scratch/`, `dist/clean_deployment_test/`, `dist/runbook_extract_test/`, and `*.log`.

---

## 5. Security & Visibility Recommendation

Even after sanitization, the repository must be published with:

### **`PRIVATE_REPOSITORY`**

**Rationale:**
1. ATLAS contains enterprise search and authorization fixtures that model internal security boundaries.
2. An isolated private GitHub repository fully supports GitHub Actions with 2,000 free runner minutes per month at **zero cost**, while strictly preventing unintentional public disclosure.

---

**STOP.** Classification complete. Awaiting human review before remediation.
