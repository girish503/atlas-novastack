# ATLAS — Final Git Repository Audit & State Report

**Document Status**: AUTHORITATIVE / AUDIT VERIFIED  
**Date**: 2026-10-08  
**Author**: Principal Engineer, Release Engineer & Technical Auditor  
**Repository**: `C:\Users\Adusumalli Girish\.gemini\antigravity\scratch\ATLAS`  
**GitHub Remote**: `girish503/atlas-novastack` (branch: `main`)  

---

## 1. Repository Identity & Commit Hashes

| Property | Value | Audit Verification |
| :--- | :--- | :--- |
| **Certified Baseline SHA** | `d325e5a82681456ebaca57f2f27c1900f17bd415` | Confirmed via Git history |
| **Demonstration Suite SHA** | `d213288990f65e36d7993b3300eeecf7355bee02` | `release: finalize ATLAS production-grade demonstration suite` |
| **Active Branch** | `main` | `git branch` |
| **Candidate Version** | `0.4.14-rc1+h5.1` | Aligned with architecture spec |
| **Certified Baseline Version** | `0.4.14-rc1` | Frozen immutable baseline |

---

## 2. Working Tree Status

```text
## Active Branch: main
## Base Release Commit: d213288990f65e36d7993b3300eeecf7355bee02
```

### 2.1 Tracked Modified Files (9 files)
All modified files represent approved release engineering, canary router injection (RET-EVAL-09), and schema updates:

1. `src/novastack/service/api.py` (+84, -1 lines):
   - Integrated `CanaryRouter` into the real FastAPI `/query` request path.
   - Preserved strict execution order: Request ID → JWT Authentication → Tenant/Context Validation → Concurrency/Deadline Check → Canary Routing.
   - Added response headers: `x-canary-variant`, `x-canary-bucket`.
2. `src/novastack/service/schemas.py` (+8 lines):
   - Added `canary_variant` (Optional[str]) and `canary_bucket` (Optional[int]) fields to `QueryResponse`.
3. `src/novastack/evidence_resolution.py` (+27, -18 lines):
   - Strict bounds checking and metadata authority handling.
4. `artifacts/phase_5k_sha256_manifest.json`:
   - Updated manifest hashes for release freeze tracking.
5. `scripts/phase_5k_release_freeze.py`:
   - Verified immutability hash checks.
6. `scripts/phase_5o_incident_recovery.py`:
   - Incident recovery harness contract updates.
7. `scripts/phase_5p_final_commissioning.py`:
   - Commissioning harness updates.
8. `docs/OPERATIONS_RUNBOOK.md`:
   - Operator instructions for canary management.
9. `docs/PHASE_5D_RUNTIME_CHECKLIST.md`:
   - Runtime checklist updates.

### 2.2 Untracked Files (New Release & Demonstration Components)
The following files were created during the demonstration frontend build, canary evaluation, and release certification phases:

#### Frontend & UI
- `ui/index.html`: Complete enterprise single-page application. Zero client-side signing secrets; truth-calibrated status indicators; masked token display; evidence drawer; certified security matrix.
- `ui/demo_tokens.json`: Pre-signed isolated demo tokens for the 4 synthetic personas (`USR-NS-0008`, `USR-ENG-42`, `USR-ACME-01`, `USR-INTERN-01`), expiring Jan 2030, scoped to `atlas-query-api`.
- `scripts/generate_demo_tokens.py`: Standalone offline token generator for demo identities.
- `scripts/serve_ui.py`: Local static HTTP file server for `ui/index.html`.

#### Live Testing & Integration
- `scripts/verify_live_scenarios.py`: Automated end-to-end FastAPI test runner for all 3 demo scenarios.
- `tests/test_frontend_integration.py`: Pytest suite (6/6 tests passing) verifying zero browser secrets, cryptographic token validity, role vs. title separation, and backend contract.

#### Canary Evaluation (RET-EVAL-09 / RET-EVAL-10)
- `src/novastack/canary.py`: Pure hashing canary router (`hashlib.sha256`), default baseline, disabled by default.
- `tests/test_canary_routing.py`: 12/12 unit tests for hash stability, bucket bounds, fail-closed fallback.
- `tests/test_live_http_canary.py`: 13/13 live HTTP integration tests against FastAPI.
- `scripts/run_5pct_canary_evaluation.py`: Controlled 5% canary evaluation harness.
- `artifacts/canary_5pct_evaluation_report.json`, `artifacts/canary_5pct_telemetry.json`, `artifacts/canary_rollback_evidence.json`.

#### Canonical Release Artifacts & Specifications
- `artifacts/canonical_evaluation_manifest.json`
- `artifacts/canonical_citation_audit.json`
- `artifacts/canonical_generation_benchmark.json`
- `artifacts/canonical_performance_report.json`
- `artifacts/canonical_retrieval_benchmark.json`
- `artifacts/canonical_security_report.json`
- `docs/ATLAS_FRONTEND_FINAL_SPEC.md`
- `docs/FRONTEND_BACKEND_TRUTH_AUDIT.md`
- `docs/ATLAS_FINAL_DEMO_RUNBOOK.md`
- `docs/FRONTEND_FINAL_VALIDATION_REPORT.md`
- `docs/FINAL_TIMEOUT_ANALYSIS.md`
- `docs/FINAL_REPOSITORY_STATE.md`
- `docs/FINAL_RELEASE_CERTIFICATION.md`

---

## 3. Git Invariant Verification

1. **Zero Signing Secrets Tracked**: All secret keys (`ATLAS_AUTH_HS256_SECRET`) remain exclusively in backend environment configuration.
2. **Zero Production Credentials in Frontend**: `ui/index.html` contains no signing keys, API keys, passwords, or production endpoints.
3. **Immutability of Corpus & Evaluation Baselines**:
   - `data/processed/novastack/search_chunks.json`: Unmodified.
   - `data/processed/novastack/search_documents.json`: Unmodified.
   - `data/processed/novastack/dense_embeddings.npz`: Unmodified.
   - `data/evaluation/novastack/evaluation_cases.json`: Unmodified.
4. **Frozen Production Baseline**: Version `0.4.14-rc1` remains frozen. `0.4.14-rc1+h5.1` remains an evaluated release candidate.
