# Agent 2 — Retrieval Review Report

**System**: ATLAS Canonical Retrieval Benchmark  
**Evaluation Scope**: OFFLINE_RETRIEVAL_ONLY (120 evaluation cases: 101 positive, 19 negative)  
**Baseline**: B4 + H1(H5) + H3 (Version 0.4.14-rc1)  
**Candidate**: B4 + H1(H5.1) + H3 (Version 0.4.14-rc1+h5.1)  
**Evaluator**: Principal Retrieval Systems Engineer  
**Status**: BENCHMARK PASSED — PROMOTION READY FOR CONTROLLED CANARY  

---

## 1. Executive Summary & Side-by-Side Comparison

The canonical retrieval benchmark was executed deterministically across all 120 evaluation queries comparing the frozen baseline (`0.4.14-rc1`) against the candidate H5.1 resolver.

### Metric Comparison Table

| Metric Category | Metric | Baseline (H5) | Candidate (H5.1) | Absolute Delta | Relative Gain | Gate Status |
|---|---|---|---|---|---|---|
| **Entity Resolution** | Expected Entity Recall | 0.5182 | **0.7820** | +0.2638 | +50.91% | Target $\ge 0.70$ ✅ PASS |
| | Wrong Entities | 0 | **0** | 0 | 0.0% | Max $\le 1$ ✅ PASS |
| | Missing Entity Cases | 23 | **0** | -23 | -100.0% | Max $\le 1$ ✅ PASS |
| **Information Retrieval** | Recall@1 | 0.2748 | **0.3259** | +0.0511 | +18.60% | Informational |
| | Recall@3 | 0.5083 | **0.5792** | +0.0709 | +13.95% | Informational |
| | Recall@5 | 0.5652 | **0.6337** | +0.0685 | +12.12% | Informational |
| | **Positive Recall@10** | 0.6716 | **0.7277** | +0.0561 | +8.35% | Target $\ge 0.66$ ✅ PASS |
| | Precision@10 | 0.0980 | **0.1069** | +0.0089 | +9.08% | Informational |
| | Hit@10 | 0.7030 | **0.7525** | +0.0495 | +7.04% | Informational |
| | **MRR** | 0.4804 | **0.5525** | +0.0721 | +15.01% | Informational |
| | **NDCG@10** | 0.4834 | **0.5499** | +0.0665 | +13.76% | Informational |
| | Multi-Aspect Recall@10 | 0.8167 | **0.9000** | +0.0833 | +10.20% | Target $\ge 0.72$ ✅ PASS |
| | H2 Slice Recall@10 | 0.6389 | **0.9167** | +0.2778 | +43.48% | Target $\ge 0.50$ ✅ PASS |
| **Security & Isolation** | Cross-Tenant Top-10 Leaks | 0 | **0** | 0 | 0.0% | Invariant 0 ✅ PASS |
| | Unauthorized Top-10 Leaks | 0 | **0** | 0 | 0.0% | Invariant 0 ✅ PASS |
| | Negative Case Leaks | 0 | **0** | 0 | 0.0% | Invariant 0 ✅ PASS |

---

## 2. Gate Verification Summary

- **G1 (Security Zero Violations)**: 0 forbidden documents in top 10 candidates ✅ PASS
- **G2 (Tenant Zero Violations)**: 0 cross-tenant chunks in top 10 candidates ✅ PASS
- **G3 (Wrong Entity Bound)**: 0 wrong entities resolved (Target $\le 1$) ✅ PASS
- **G4 (Missing Entity Bound)**: 0 missing entity cases (Target $\le 1$) ✅ PASS
- **G5 (EVAL-0036 Recall@10)**: Baseline 0.0000 $\to$ Candidate 1.0000 (Target $\ge 0.50$) ✅ PASS
- **G6 (Positive Recall@10)**: 0.7277 (Target $\ge 0.66$) ✅ PASS
- **G7 (Multi-Aspect Recall@10)**: 0.9000 (Target $\ge 0.72$) ✅ PASS

---

## 3. Engineering Analysis

1. **Resolution Mechanism**:
   - H5.1 expands catalog coverage by adding synchronized service aliases, derived catalog phrases, and canonical multi-token incident/deployment sequences.
   - It maintains strict tenant isolation by checking `entity.tenant_id == tenant_id` before any alias is admitted to the candidate list.
2. **Precision Preservation**:
   - Zero wrong entities were introduced across the entire 120-query evaluation set.
   - Missing entity cases dropped from 23 to 0, completely resolving the vocabulary mismatch on incident titles and service descriptions.
3. **Retrieval Authority Status**:
   - Baseline `0.4.14-rc1` remains the production default (`ATLAS_CANARY_ENABLED=false`, traffic 0.0%).
   - The H5.1 candidate is verified for staged canary promotion, pending external security verification.

---

## 4. Final Verdict

**Gate B (Retrieval Quality): PASS**  
The retrieval metrics strictly exceed all defined thresholds with zero security regressions.
