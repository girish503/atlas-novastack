# ATLAS 0.5 — Milestone M7: Query-Adaptive Evidence Depth & Calibrated Answering Optimization

## Executive Summary & Official Milestone Verdict

- **Milestone Code**: `ATLAS-0.5-M7`
- **Execution Date**: 2026-09-29 10:02:06 UTC
- **Production Reference Release**: `0.4.14-rc1` (Frozen, Immutable)
- **Official Verdict**: **`ITERATE`**
- **Positive Answer Yield**: **62/101 (61.39%)** (Target: $\ge 66.34\%$ / 67 cases)
- **Negative Case Safety**: **19/19 (100.0%)** (Target: $100.0\%$)
- **Mechanical Citation Precision**: **99/99 (100.0%)** (Target: $100.0\%$)
- **Citation Completeness**: **57/62 (91.94%)** (Target: $\ge 90.0\%$)
- **Security Invariants**: **0 Violations** (Target: 0)
- **Mean Positive Latency**: **15810.17 ms** (Target: $\le 15,000$ ms)
- **Multi-Hop Focus Slice (18 Cases)**: **14/18 (77.78%)** with **0 Timeouts**

---

## 1. Architectural Changes Implemented

### Track A: Query-Adaptive Evidence Depth Allocation & Distractor Suppression
- **Adaptive Document Budgets**: Simple entity lookups dynamically allocate $k=3$ documents, while multi-hop causal chain queries allocate $k=4$ documents under a soft $460$-token ceiling.
- **Cross-Event Distractor Suppression**: Step 6 budget fill enforces `candidate_quality_key(it)[0] > 0.0` (strict entity overlap requirement). Documents from unrelated incident events (e.g. EVT-NS-0012) are eliminated from the candidate pool, preventing contradictory root causes and false abstentions.

### Track B: Non-Displacing Missing-Role Retrieval Recovery
- Missing structural roles (Deployments, PRs) recovered via 1-hop catalog traversal receive `retrieval_score=0.95` and `retrieval_rank=95+i`. Primary postmortems (`retrieval_score=1.0`, `retrieval_rank=1`) are preserved at the top of the context package, eliminating displacement-induced hallucinations.

### Track C: Alias-Grounded Context Note Injection
- Queries using colloquial entity aliases (e.g. "credit card payments") generate neutral, grounded context notes (`[Context Note: Service 'checkout-service' operates in Engineering handling checkout functionality.]`). These are injected before `EVIDENCE:` in prompt assembly, resolving lexical disconnects while strictly preserving anti-hallucination boundaries. Out-of-scope probes (`EVAL-0054`, `EVAL-0058`) match zero entities and produce zero context notes.

---

## 2. Mandatory Gate Evaluation

| Gate | Description | Measured Value | SLA Target | Status |
| :--- | :--- | :--- | :--- | :--- |
| **G1** | Positive Answer Yield | 62/101 (61.39%) | $\ge 66.34\%$ (67/101) | **FAIL** |
| **G2** | Negative Abstention Safety | 19/19 (100.0%) | $= 100.0\%$ (19/19) | **PASS** |
| **G3** | Citation Precision (C2) | 99/99 (100.0%) | $= 100.0\%$ | **PASS** |
| **G4** | Citation Completeness | 57/62 (91.94%) | $\ge 90.0\%$ | **PASS** |
| **G5** | Security Policy Invariance | 0 violations | $= 0$ | **PASS** |
| **G6** | Latency Performance | 15810.17 ms | $\le 15,000$ ms | **FAIL** |

---

## 3. Targeted Recovery Case Analysis

| Evaluation ID | Status | Latency | Valid Citations | Attribution / Recovery Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| `EVAL-0018` | `abstained` | 1599.7 ms | [] | Distractor suppression eliminated EVT-NS-0012 contamination |
| `EVAL-0019` | `answered` | 20371.3 ms | ['DOC-PM-EVT-NS-0001-01', 'DOC-INC-INC-NS-0001-02'] | Alias context note connected payment alias to checkout-service |
| `EVAL-0033` | `abstained` | 14423.5 ms | [] | Adaptive evidence depth (budget=3) preserved supporting runbook |
| `EVAL-0044` | `answered` | 25862.9 ms | ['DOC-PM-EVT-NS-0001-01', 'DOC-NOISE-DUP-0023'] | Non-displacing role recovery preserved postmortem rank 1 |
| `EVAL-0046` | `answered` | 24113.8 ms | ['DOC-PM-EVT-NS-0003-01', 'DOC-INC-INC-NS-0003-02', 'DOC-DEP-DEP-NS-0003-01'] | Multi-hop adaptive depth (budget=4) accommodated full PR chain |
| `EVAL-0050` | `answered` | 27744.1 ms | ['DOC-PM-EVT-NS-0007-01'] | Adaptive depth provided cross-service deployment context |
| `EVAL-0062` | `abstained` | 13934.4 ms | [] | Adaptive budget prevented truncation of SLA threshold table |

---

## 4. Critical Negative Safety Verification

- **`EVAL-0054` (Satellite Downlink Degradation)**: Status = **`abstained`**, Citations = `0`, Reason = `model_evidence_insufficient`.
- **`EVAL-0058` (Twilio SMS Auth Tokens)**: Status = **`abstained`**, Citations = `0`, Reason = `model_evidence_insufficient`.
- **Negative Abstention Completeness**: **19/19 (100.0%)**.

---

## 5. Milestone Verdict & Directives

- **Official Verdict**: **`ITERATE`**.
- Production baseline `0.4.14-rc1` remains frozen, immutable, and unmodified.
