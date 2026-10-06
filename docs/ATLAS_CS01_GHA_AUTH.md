# ATLAS CS-01-GHA-AUTH — Isolated GitHub Actions Canary Workflow Authorization & Security Preflight Report

**Review Date**: 2026-10-06  
**Role**: ATLAS Release Engineer, Senior SRE, Security Reviewer  
**Repository**: `girish503/atlas-novastack` (PRIVATE)  
**Current HEAD Commit**: `57138c7ae1a98ca4887c3cbef41ca6c7024cbae1`  
**CI Baseline State**: Run `#37428964549` (**CI_GREEN_BASELINE**, 100% PASS)  
**Phase Objective**: Infrastructure Authorization & Security Preflight (NON-EXECUTING)  
**Phase Decision**: **`WORKFLOW_READY`**  

---

## 1. Objective

The objective of **CS-01-GHA-AUTH** is to prepare and statically certify the manual-only infrastructure required for the future isolated execution of the **ATLAS 0.5.0-rc1 canary** on GitHub Actions (`ubuntu-latest`).

**CRITICAL MANDATE**:
- This phase is **AUTHORIZATION AND PREFLIGHT ONLY**.
- The canary workflow is prepared on the runway; **IT DOES NOT TAKE OFF**.
- **NO CANARY EXECUTION HAS OCCURRED.**
- **NO WORKFLOW DISPATCH HAS BEEN TRIGGERED.**

---

## 2. Authoritative Current State

| Dimension | Measured / Verified Value | Authority / Standard | Status |
| :--- | :--- | :--- | :--- |
| **Project** | ATLAS — Evidence-Grounded Enterprise Search Platform | Milestone Baseline | **CONFIRMED** |
| **Repository Visibility** | `girish503/atlas-novastack` (`PRIVATE`) | Security Parameter | **VERIFIED** |
| **Current Main Commit** | `57138c7ae1a98ca4887c3cbef41ca6c7024cbae1` | Synchronized with `origin/main` | **MATCH** |
| **Remote CI Baseline** | Run `#37428964549` | 3/3 jobs PASS (Fast, Security, Docker) | **GREEN BASELINE** |
| **Release Candidate** | `ATLAS 0.5.0-rc1` | `dist/atlas-novastack-0.5.0-rc1.tar.gz` | **INTACT** |
| **0.5.0-rc1 SHA-256** | `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936` | Bit-for-bit release hash | **VERIFIED** |
| **Production Baseline** | `ATLAS 0.4.14-rc1` (FROZEN & IMMUTABLE) | `dist/atlas-novastack-0.4.14-rc1.tar.gz` | **INTACT** |
| **0.4.14-rc1 SHA-256** | `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` | Phase 5M certified hash | **VERIFIED** |
| **M8 Certified Metrics** | 74/101 positive yield, 100% negative safety, 0 security violations | Pre-certified milestone | **IMMUTABLE** |
| **Workstation Capacity** | Windows 11 host (AMD64, 7.63 GB RAM, **0.94 GB free**) | $\ge 2.50\text{ GB}$ envelope required | **CONSTRAINED** |
| **Target Canary Runner** | GitHub Actions hosted standard Linux runner (`ubuntu-latest`, 7.0 GB RAM) | ENV-01 Free Tier Selection | **DESIGNED** |
| **Canary Status** | **NOT EXECUTED** | Hard Safety Boundary | **HALTED** |

---

## 3. Workflow Design

A dedicated, isolated workflow has been authored at:
[`.github/workflows/canary.yml`](file:///c:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/.github/workflows/canary.yml)

### Architectural Structure
The workflow encapsulates a complete, hermetic canary execution sequence designed for standard `ubuntu-latest` GitHub-hosted runners:
1. **Commit & Workspace Verification**: Asserts that `HEAD` matches expected commit and `src/novastack/` has zero uncommitted modifications.
2. **Cryptographic Release Verification**: Computes `sha256sum` on both candidate `0.5.0-rc1` and baseline `0.4.14-rc1` archives before runtime provisioning.
3. **Runtime Setup**: Installs Python 3.11 with pip caching and project dependencies (`pip install .[test]`).
4. **Ollama Daemon Isolation**: Installs Ollama via official installer, starts daemon on loopback `127.0.0.1:11434` with `OLLAMA_NUM_PARALLEL=1` and `OLLAMA_MAX_LOADED_MODELS=1`, and polls readiness.
5. **Model Identity Enforcement**: Pulls `gemma3:1b` (Q4_K_M) and verifies model family, parameters, and format via API inspection.
6. **Isolated Synthetic Canary Harness**: Instantiates the certified M8 pipeline configuration in-memory and runs the 5 designated synthetic probes.
7. **Clean Teardown**: Guarantees termination of Ollama and background tasks (`if: always()`).
8. **Safe Artifact Capture**: Packages structured execution telemetry (`canary_gha_results.json`, `canary_gha_summary.md`, `ollama.log`) via `actions/upload-artifact@v4`.

---

## 4. Trigger Analysis

The workflow trigger is strictly constrained to prevent accidental or automated execution:
```yaml
on:
  workflow_dispatch:
```

### Static Trigger Audit
- `push`: **DISABLED** (Workflow does not run on commit pushes).
- `pull_request`: **DISABLED** (Workflow does not run on PR creation or updates).
- `schedule`: **DISABLED** (No cron or periodic execution).
- `workflow_run`: **DISABLED** (Does not trigger following CI completion).
- `workflow_call`: **DISABLED** (Cannot be invoked as a reusable workflow).

Pushing `.github/workflows/canary.yml` to the remote repository **will NOT trigger execution**. Execution requires explicit human intervention through the GitHub Web UI or explicit authorized CLI invocation.

---

## 5. Permissions Analysis

The workflow implements strict least-privilege security permissions:
```yaml
permissions:
  contents: read
```

### Privileges Audit
- `contents: read`: Permitted (required for checkout and artifact inspection).
- `contents: write`: **FORBIDDEN** (No branch commits or release creation).
- `actions: write`: **FORBIDDEN** (Cannot cancel or modify workflow runs).
- `packages: write`: **FORBIDDEN** (Cannot publish container images).
- `administration: write`: **FORBIDDEN** (Cannot alter repository settings).
- `issues: write` / `pull-requests: write`: **FORBIDDEN** (No automated commenting).

`actions/upload-artifact@v4` operates securely within the default runner artifact exchange token and requires zero elevated permissions.

---

## 6. Secrets Analysis

- **Production Secrets Referenced**: `0`
- **Secrets Injected**: `0`
- **Environment Tokens Required**: `0`

The workflow requires **zero production secrets**. No database credentials, API keys, JWT signing keys, or external service tokens are configured or referenced in the workflow definition. Secret leakage via stdout/stderr is structurally eliminated.

---

## 7. Network Isolation

External network access during future canary runs is classified into three strict categories:

| Category | Endpoints / Resources | Justification / Security Boundary |
| :--- | :--- | :--- |
| **REQUIRED** | Ubuntu package mirrors (apt)<br>PyPI (`pypi.org`, `pythonhosted.org`)<br>`ollama.com/install.sh`<br>`library.ollama.ai` / `registry.ollama.ai` | Standard runner runtime setup, Python test wheels, Ollama binary, and public quantized model weights (`gemma3:1b`). |
| **OPTIONAL** | None | No optional network paths permitted. |
| **PROHIBITED** | • ATLAS production ingress / API endpoints<br>• Production databases & Redis caches<br>• Commercial cloud LLMs (OpenAI, Anthropic, GCP)<br>• External telemetry sinks & analytics | **Strictly prohibited**. The runner operates in complete isolation from production systems. |

---

## 8. Data Isolation

The future canary operates exclusively on:
1. Repository-contained synthetic records (`data/raw/novastack/source_records.json`).
2. Certified processed search corpora (`data/processed/novastack/search_documents.json`, `search_chunks.json`).
3. Certified synthetic evaluation test cases (`data/evaluation/novastack/phase_4e_evidence_assembly.json`).
4. Ephemeral synthetic tenant identifiers (`TENANT-NOVASTACK`, `TENANT-ORBITAL`).

**Zero customer data, zero production database records, and zero private telemetry will ever enter the runner.**

---

## 9. Model Identity Lock

The workflow mandates the exact certified M8 model architecture:
- **Model Name**: `gemma3:1b`
- **Family**: Gemma 3 (1.0B parameters)
- **Quantization Level**: `Q4_K_M`
- **Container Format**: GGUF
- **Required Model Digest**: `8648f39daa8fbf5b18c7b4e6a8fb4990c692751d49917417b8842ca5758e7ffc`

**Prohibited Substitutions**:
No substitutions to Qwen, alternative parameter sizes (e.g. 4B, 12B, 27B), unquantized FP16 weights, or external cloud endpoints are permitted.

---

## 10. Billing Safety Evaluation

The operator has completed authoritative inspection of GitHub account billing:
- **Billing Setting**:
  ```text
  BILLING_STATUS = OPERATOR_VERIFIED_ZERO_BUDGET_STOP
  ```
- **Actions Budget**: `$0`
- **Stop Usage When Limit Reached**: `YES`
- **Recent Incurred Charges**: `$0`
- **Runner Allocation**: GitHub standard hosted runner (`ubuntu-latest`), covered under private repository monthly free tier allocations (2,000 minutes/month, of which repository has consumed ~17 minutes).
- **Spending Protection**: Hard stop at $0 budget guarantees that runaway or billable executions cannot incur unexpected financial liability.

---

## 11. Artifact Safety

Artifact retention in the future workflow is bounded:
- Target files:
  - `artifacts/canary_gha_results.json` (Structured test results and probe latencies)
  - `artifacts/canary_gha_summary.md` (Markdown summary scorecard)
  - `ollama.log` (Inference daemon stdout/stderr for operational diagnostics)
- Prohibited from upload: Process environment variables (`env`), system credential stores, `.git/config` authentication headers, or private keys.
- **Retention Period**: 7 days (ephemeral diagnostic retention).

---

## 12. Timeout & Resource Controls

The future canary is bounded by strict resource guardrails:
- **Job Timeout**: `timeout-minutes: 25` (Workflow automatically aborts if runtime exceeds 25 minutes).
- **Daemon Concurrency**: `OLLAMA_NUM_PARALLEL=1` and `OLLAMA_MAX_LOADED_MODELS=1` to enforce strict sequential single-inference isolation.
- **Query Timeout**: Individual query timeout enforced at `45.0s`.
- **Readiness Polling**: Ollama daemon readiness bound to 30 attempts (30s max wait).

---

## 13. Static Security Findings

| Check | Inspection Target | Result | Status |
| :--- | :--- | :--- | :--- |
| **SEC-01** | Trigger mechanism | `workflow_dispatch` only | ✅ **PASS** |
| **SEC-02** | Automatic push trigger | Completely absent from YAML | ✅ **PASS** |
| **SEC-03** | Token privileges | `permissions: contents: read` | ✅ **PASS** |
| **SEC-04** | Secret exposure | 0 secret interpolations in script | ✅ **PASS** |
| **SEC-05** | Production isolation | 0 production URLs / IP references | ✅ **PASS** |
| **SEC-06** | Model reproducibility | Gemma 3 1B Q4_K_M locked | ✅ **PASS** |
| **SEC-07** | Pipeline security order | All 8 stages preserved in harness | ✅ **PASS** |
| **SEC-08** | Teardown guarantee | `if: always()` step terminates daemon | ✅ **PASS** |

---

## 14. Files Created / Modified

1. **Created Workflow**:
   - [`.github/workflows/canary.yml`](file:///c:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/.github/workflows/canary.yml)
2. **Created Artifact Manifest**:
   - [`artifacts/phase_cs01_gha_auth.json`](file:///c:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/artifacts/phase_cs01_gha_auth.json)
3. **Created Security Report**:
   - [`docs/ATLAS_CS01_GHA_AUTH.md`](file:///c:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/docs/ATLAS_CS01_GHA_AUTH.md)
4. **Untouched Files**:
   - [`.github/workflows/ci.yml`](file:///c:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/.github/workflows/ci.yml) (Unmodified, bit-for-bit intact)
   - `src/novastack/` (Unmodified, 0 files drifted)
   - `dist/atlas-novastack-0.5.0-rc1.tar.gz` (Unmodified, SHA verified)
   - `dist/atlas-novastack-0.4.14-rc1.tar.gz` (Unmodified, SHA verified)

---

## 15. Verification Results

```text
================================================================================
ATLAS CS-01-GHA-AUTH VERIFICATION SUMMARY
================================================================================
YAML Syntax Validity:             VALID (PyYAML verified)
Trigger Audit:                    MANUAL-ONLY (workflow_dispatch only)
Permissions Audit:                LEAST-PRIVILEGE (contents: read)
Production Source Drift:          0 files, 0 lines modified in src/novastack/
CI Workflow Integrity:            .github/workflows/ci.yml UNTOUCHED
0.5.0-rc1 SHA-256:                f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936 (MATCH)
0.4.14-rc1 SHA-256:               382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3 (MATCH)
Billing Status:                   OPERATOR_VERIFIED_ZERO_BUDGET_STOP ($0 Budget)
Canary Execution Status:          NOT EXECUTED
================================================================================
```

---

## 16. Explicit Non-Execution Statement

> [!IMPORTANT]
> **EXPLICIT NON-EXECUTION STATEMENT**:
> The ATLAS CS-01-GHA canary was **NOT EXECUTED** in this phase.
> - `gh workflow run` was NOT called.
> - The GitHub Actions UI `Run workflow` button was NOT pressed.
> - Ollama was NOT started on any remote runner.
> - Gemma 3 1B model was NOT pulled.
> - No canary probes were run.
> - Zero live inferences were performed.
>
> This phase successfully prepared the runway without initiating flight.

---

## 17. Next Authorized Action

The workflow [`.github/workflows/canary.yml`](file:///c:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/.github/workflows/canary.yml) is ready to be committed and published to `girish503/atlas-novastack` (branch `main`).

Once published, the operator may authorize a dedicated execution phase (`CS-01-GHA-EXEC`) to manually trigger the workflow and observe the 5 canary probes.
