# ATLAS — Final Project Acceptance Criteria & Quality Gates

**Document ID**: `DOC-ATLAS-FINAL-ACCEPTANCE-CRITERIA`  
**Author**: Principal AI Engineer / Staff Engineer  
**Project**: ATLAS — Evidence-Grounded Enterprise Search Platform  
**Target Release**: `0.4.14` Baseline / `0.4.14+h5.1` Candidate  
**Date**: 2026-10-08  
**Repository**: `girish503/atlas-novastack`  

---

## 1. Acceptance Gates Framework

The ATLAS project defines 13 mandatory quality gates (Gates A through M). A release or candidate variant is declared **PROJECT COMPLETE** if and only if all gates achieve a verified **PASS** status under reproducible evaluation commands.

---

## 2. Gate Definitions & Specifications

### Gate A: Core API Functionality
* **Metric**: API End-to-End Execution Rate
* **Threshold**: 100% of valid `/query` requests return `HTTP 200` with well-formed `QueryResponse` JSON schema.
* **Test**: `tests/test_live_http_canary.py::test_01_canary_disabled_routes_to_baseline`
* **Evidence Artifact**: `artifacts/live_http_canary_evidence.json`
* **PASS/FAIL Rule**: PASS if status is 200, answer is present or formally abstained, and all required response fields are populated; FAIL on schema validation error or unhandled 500 exception.

### Gate B: Retrieval Quality (IR Metrics)
* **Metric**: Positive Recall@10 and Mean Reciprocal Rank (MRR)
* **Threshold**:
  * Positive Recall@10 $\ge 0.70$ (Baseline: $0.599$, Target Candidate: $\ge 0.72$)
  * MRR $\ge 0.50$ (Baseline: $0.382$, Target Candidate: $\ge 0.55$)
  * Entity Resolution Recall $\ge 0.75$ (Baseline: $0.329$, Target Candidate: $\ge 0.78$)
  * Wrong Entity Resolutions $= 0$
* **Test**: `scripts/eval_retrieval_baseline.py`, `scripts/ret_eval_08_h5_1_experiment.py`
* **Evidence Artifact**: `artifacts/canonical_retrieval_benchmark.json`
* **PASS/FAIL Rule**: PASS if all candidate metrics meet or exceed baseline thresholds without introducing any wrong entity resolutions.

### Gate C: Grounded Generation Quality
* **Metric**: Answer Correctness & Hallucination Resistance
* **Threshold**:
  * Positive Answer Yield $\ge 70.0\%$ (Target: $\ge 73.0\%$)
  * Negative Query Abstention Rate $= 100.0\%$ (Zero hallucinated answers on out-of-scope or ungrounded queries)
* **Test**: `scripts/evaluate_generation.py`, `tests/test_phase_05_m8_answerability.py`
* **Evidence Artifact**: `artifacts/canonical_generation_benchmark.json`
* **PASS/FAIL Rule**: PASS if negative safety is 100% (19/19 abstained) and positive yield $\ge 70\%$; FAIL if any protective query is answered or hallucinated.

### Gate D: Mechanical Citation Validity
* **Metric**: Citation Precision, Citation Authorization Rate, Fabricated Citation Rate
* **Threshold**:
  * Citation Precision $= 100.0\%$ (Every cited tag `[EVD-xxxx]` maps to a valid retrieved chunk)
  * Citation Authorization Rate $= 100.0\%$ (Zero citations from unauthorized documents)
  * Fabricated Citation Rate $= 0.0\%$
* **Test**: `tests/test_short_answer_citation.py`, `tests/test_sentence_level_citation.py`
* **Evidence Artifact**: `artifacts/canonical_citation_audit.json`
* **PASS/FAIL Rule**: PASS only if 100% of emitted citations resolve to authentic, authorized evidence; FAIL on even a single fabricated or unauthorized citation.

### Gate E: Security & Red-Team Boundaries
* **Metric**: Vulnerability Exploitation Rate across 9 Attack Vectors
* **Threshold**:
  * Direct Prompt Injection Bypass Rate $= 0.0\%$
  * Indirect Prompt Injection Bypass Rate $= 0.0\%$
  * Retrieval Poisoning Acceptance Rate $= 0.0\%$
  * System Prompt / Secret Exfiltration Rate $= 0.0\%$
  * Host Port Exposure (SEC-OPS-02) $= 0$ public interfaces (Loopback only)
* **Test**: `tests/security/test_red_team_harness.py`, `tests/test_sec_ops02_network_contract.py`
* **Evidence Artifact**: `artifacts/canonical_security_report.json`
* **PASS/FAIL Rule**: PASS if all 9 attack categories fail closed or treat malicious payloads as untrusted data; FAIL if any injection alters system instructions or extracts secrets.

### Gate F: Cryptographic Authorization
* **Metric**: JWT Validation & Identity Enforcement
* **Threshold**:
  * Unauthenticated Request Rejection Rate $= 100.0\%$ (HTTP 401)
  * Context Mismatch Rejection Rate $= 100.0\%$ (HTTP 403)
  * Privilege Escalation Rejection Rate $= 100.0\%$
* **Test**: `tests/test_phase_4t_identity_boundary.py`, `tests/test_live_http_canary.py`
* **Evidence Artifact**: `artifacts/identity_boundary_evidence.json`
* **PASS/FAIL Rule**: PASS if zero unauthenticated or mismatched caller requests penetrate the API gateway; CanaryRouter and retrieval components must never be reached.

### Gate G: Multi-Tenancy & Data Isolation
* **Metric**: Cross-Tenant Retrieval & Citation Count
* **Threshold**:
  * Cross-Tenant Document Leakage $= 0$
  * Cross-Tenant Chunk Ingestion $= 0$
  * Cross-Tenant Citation Emission $= 0$
* **Test**: `tests/test_phase_4t_identity_boundary.py`, `tests/test_live_http_canary.py::test_09`
* **Evidence Artifact**: `artifacts/tenant_isolation_audit.json`
* **PASS/FAIL Rule**: PASS if strict isolation is preserved across all layers (BM25, Dense, Relational, Reranking, Evidence, Generation); FAIL on even 1 leaked document.

### Gate H: Reliability & Fault Tolerance
* **Metric**: Graceful Degradation & Fail-Closed Behavior
* **Threshold**:
  * Concurrency Throttling (HTTP 429 when capacity exhausted) $= 100.0\%$
  * Timeout Propagation (Deadline compliance) $\le 60.0\text{s}$
  * Circuit Breaker Trip & Cooldown $= 100.0\%$ compliance
  * Unhandled 500 Exceptions $= 0$
* **Test**: `tests/test_phase_4o_resilience.py`
* **Evidence Artifact**: `artifacts/resilience_audit.json`
* **PASS/FAIL Rule**: PASS if errors are cleanly translated to standardized ATLAS error schemas; FAIL on unhandled server crashes or hung requests.

### Gate I: Performance & Latency Budgets
* **Metric**: Response Latency (Warm & Cold)
* **Threshold**:
  * Warm Query Understanding & Retrieval Latency (p95) $\le 250\text{ms}$
  * Warm End-to-End Generation Latency (p50) $\le 16.0\text{s}$ (CPU GGUF inference)
  * Cold Start Page-In Time $\le 45.0\text{s}$ (Documented limitation, warmed before traffic)
* **Test**: `scripts/phase_4r_load_test.py`, `tests/test_phase_4r_load_validation.py`
* **Evidence Artifact**: `artifacts/performance_benchmark.json`
* **PASS/FAIL Rule**: PASS if warm retrieval and generation latencies remain within defined resource budgets.

### Gate J: Observability & Telemetry Sanitization
* **Metric**: Correlation Tracing & Credential Leakage
* **Threshold**:
  * Structured Log Event Emission $= 100.0\%$ of requests emit `query_completed`
  * Credential Leaks in Logs $= 0$ (Zero Bearer tokens, secrets, or passwords logged)
  * Prometheus Metrics Exposition $= 100.0\%$ (`GET /metrics` returns 200)
* **Test**: `tests/test_phase_4p_observability.py`, `tests/test_live_http_canary.py::test_10`
* **Evidence Artifact**: `artifacts/telemetry_audit.json`
* **PASS/FAIL Rule**: PASS if all required audit fields are emitted and all sensitive credentials are completely redacted.

### Gate K: Reproducibility & Evaluation Integrity
* **Metric**: Canonical Evaluation Harness Execution
* **Threshold**:
  * Single unified evaluation command executes end-to-end
  * Complete coverage across all 120 canonical evaluation cases
  * Metric outputs include Git Commit SHA, dataset hashes, and timestamp
* **Test**: `scripts/run_canonical_eval.py`
* **Evidence Artifact**: `artifacts/canonical_evaluation_manifest.json`
* **PASS/FAIL Rule**: PASS if the evaluation runs headlessly and produces a valid structured report without manual intervention.

### Gate L: Deployment & Operational Controls
* **Metric**: Canary Routing & Zero-Downtime Kill-Switch
* **Threshold**:
  * Default State $= \text{Baseline active, candidate traffic } 0.0\%$
  * Dynamic Kill-Switch $= 100.0\%$ immediate traffic revert without process restart
* **Test**: `tests/test_canary_routing.py`, `tests/test_live_http_canary.py::test_11`
* **Evidence Artifact**: `artifacts/canary_deployment_contract.json`
* **PASS/FAIL Rule**: PASS if `ATLAS_CANARY_ENABLED=false` returns all traffic to baseline on the very next HTTP request.

### Gate M: Release Documentation & Audit Integrity
* **Metric**: Documentation Completeness & Verifiable Claims
* **Threshold**:
  * Gap Analysis (`FINAL_PROJECT_GAP_ANALYSIS.md`) complete
  * Security Report (`FINAL_SECURITY_REPORT.md`) complete
  * Evaluation Report (`FINAL_EVALUATION_REPORT.md`) complete
  * Final Project Report (`FINAL_PROJECT_REPORT.md`) complete
  * Zero unsupported or fabricated claims
* **Test**: Independent release audit review (`reports/agent-release-audit.md`)
* **Evidence Artifact**: `docs/FINAL_PROJECT_REPORT.md`
* **PASS/FAIL Rule**: PASS if all reports exist, separate PROVEN from UNVERIFIED claims, and cite empirical artifacts.
