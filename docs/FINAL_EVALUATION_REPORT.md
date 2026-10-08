# ATLAS — Final Unified Evaluation Benchmark Report

**Enterprise**: NovaStack  
**System**: ATLAS Evidence-Grounded Enterprise Search Platform  
**Baseline Architecture**: 0.4.14-rc1 (B4 + H1(H5) + H3)  
**Candidate Architecture**: 0.4.14-rc1+h5.1 (B4 + H1(H5.1) + H3)  
**Evaluation Dataset**: `data/evaluation/novastack/evaluation_cases.json` (120 queries)  
**Harness Manifest**: `artifacts/canonical_evaluation_manifest.json`  
**Commit SHA**: `d325e5a82681456ebaca57f2f27c1900f17bd415`  
**Execution Timestamp**: 2026-10-08T11:44:57Z  
**Total Evaluation Duration**: 66.07 seconds  

---

## 1. Executive Summary

This report aggregates all deterministic metrics produced by the unified evaluation suite across retrieval, generation, security, performance, and reliability. Baseline and candidate systems were evaluated under identical conditions using the 120-query canonical benchmark.

---

## 2. Retrieval Evaluation: Baseline (H5) vs Candidate (H5.1)

### Information Retrieval & Ranking Metrics

| Metric | Baseline (H5) | Candidate (H5.1) | Delta (Absolute) | Delta (Relative) | Benchmark Target | Verdict |
|---|---|---|---|---|---|---|
| **Recall@1** | 0.2748 | **0.3259** | +0.0511 | +18.60% | Informational | — |
| **Recall@3** | 0.5083 | **0.5792** | +0.0709 | +13.95% | Informational | — |
| **Recall@5** | 0.5652 | **0.6337** | +0.0685 | +12.12% | Informational | — |
| **Positive Recall@10** | 0.6716 | **0.7277** | +0.0561 | +8.35% | $\ge 0.6600$ | ✅ PASS |
| **Precision@10** | 0.0980 | **0.1069** | +0.0089 | +9.08% | Informational | — |
| **Hit@10** | 0.7030 | **0.7525** | +0.0495 | +7.04% | Informational | — |
| **MRR (Mean Reciprocal Rank)** | 0.4804 | **0.5525** | +0.0721 | +15.01% | Informational | — |
| **NDCG@10** | 0.4834 | **0.5499** | +0.0665 | +13.76% | Informational | — |
| **Multi-Aspect Recall@10** | 0.8167 | **0.9000** | +0.0833 | +10.20% | $\ge 0.7200$ | ✅ PASS |
| **H2 Slice Recall@10** | 0.6389 | **0.9167** | +0.2778 | +43.48% | $\ge 0.5000$ | ✅ PASS |

### Entity Resolution Metrics

| Metric | Baseline (H5) | Candidate (H5.1) | Target Bound | Verdict |
|---|---|---|---|---|
| **Expected Entity Recall** | 0.5182 | **0.7820** | $\ge 0.7000$ | ✅ PASS |
| **Wrong Entities Resolved** | 0 | **0** | $\le 1$ | ✅ PASS |
| **Missing Entity Cases** | 23 | **0** | $\le 1$ | ✅ PASS |

### Retrieval Security & Leakage Bounds

| Security Metric | Baseline (H5) | Candidate (H5.1) | Invariant Bound | Verdict |
|---|---|---|---|---|
| **Cross-Tenant Top-10 Leaks** | 0 | **0** | 0 | ✅ PASS |
| **Forbidden Top-10 Leaks** | 0 | **0** | 0 | ✅ PASS |
| **Negative Case Document Leaks** | 0 | **0** | 0 | ✅ PASS |

---

## 3. Grounded Generation Evaluation (Certified M8 Benchmark)

- **Underlying Model**: Google Gemma 3 1B IT (Q4_K_M GGUF, 815MB)
- **Prompt Strategy**: `config_b_calibrated_safe` (Dynamic B0/B3 dispatch)
- **Context Budgeting**: `m8_targeted_extraction` (Sentence-level extraction)

| Metric | Target Threshold | Measured Score | Status |
|---|---|---|---|
| **Positive Answer Yield** | $\ge 70.0\%$ (71/101) | **73.27%** (74/101) | ✅ PASS |
| **Negative Safety Rate** | $100.0\%$ (19/19) | **100.0%** (19/19) | ✅ PASS |
| **Citation Precision** | $100.0\%$ | **100.0%** (122/122 valid) | ✅ PASS |
| **Citation Completeness** | $\ge 90.0\%$ | **93.24%** (69/74) | ✅ PASS |
| **Unauthorized Citations** | 0 | **0** | ✅ PASS |
| **Fabricated Citations** | 0 | **0** | ✅ PASS |
| **Multi-Hop Query Recovery** | $\ge 13/18$ | **77.78%** (14/18) | ✅ PASS |
| **Protective Query Safety** | EVAL-0054 & EVAL-0058 Abstained | **100.0%** (2/2 abstained) | ✅ PASS |

---

## 4. Security & Red-Team Evaluation (9 Categories)

| Attack Category | Sub-Tests | Passed | Failed | Verdict |
|---|---|---|---|---|
| 7.1 Authentication Boundary | Missing, forged, expired, malformed tokens | 4 | 0 | ✅ PASS |
| 7.2 Authorization & Context | Tenant spoofing, role escalation | 2 | 0 | ✅ PASS |
| 7.3 Cross-Tenant Isolation | Indexed foreign candidate, unknown lineage | 2 | 0 | ✅ PASS |
| 7.4 Direct Prompt Injection | System prompt reveal, rule bypass strings | 1 | 0 | ✅ PASS |
| 7.5 Indirect Prompt Injection | Adversarial document instructions | 1 | 0 | ✅ PASS |
| 7.6 Retrieval Poisoning | Self-declared canonical authority in body | 1 | 0 | ✅ PASS |
| 7.7 Citation Leakage | Phantom tags `[EVD-999]`, unauthorized doc | 2 | 0 | ✅ PASS |
| 7.8 Secret Exfiltration | Telemetry assignments, chunk corpus scan | 2 | 0 | ✅ PASS |
| 7.9 Metadata Attacks | Tampered client-side caller context | 1 | 0 | ✅ PASS |
| **Total Security Tests** | | **16** | **0** | ✅ **PASS (100%)** |

---

## 5. Performance & Latency Benchmark

### In-Process Warm Query Latencies (Sample Size = 20)

| Pipeline Stage | Mean | Median (p50) | p95 | p99 | Max | Threshold | Verdict |
|---|---|---|---|---|---|---|---|
| **Query Understanding** | 1.25 ms | 1.14 ms | 1.29 ms | 3.20 ms | 3.20 ms | $\le 250$ ms | ✅ PASS |
| **Hybrid Retrieval** | 47.34 ms | 41.36 ms | 57.71 ms | 123.23 ms | 123.23 ms | $\le 250$ ms | ✅ PASS |
| **Metadata Reranking** | 0.33 ms | 0.32 ms | 0.38 ms | 0.41 ms | 0.41 ms | $\le 100$ ms | ✅ PASS |
| **Total Pre-LLM Time** | **48.92 ms** | **42.82 ms** | **59.38 ms** | **126.84 ms** | **126.84 ms** | $\le 500$ ms | ✅ PASS |

### Generation Latency (Gemma 3 1B CPU)

| Slice | Sample Size | Mean | Median (p50) | p95 | p99 |
|---|---|---|---|---|---|
| **All Evaluation Cases** | 120 | 13,361.67 ms (13.36s) | 13,919.52 ms (13.92s) | 21,840.57 ms (21.84s) | 31,637.03 ms (31.64s) |
| **Positive Answered** | 74 | 14,899.67 ms (14.90s) | 14,350.20 ms (14.35s) | 22,650.00 ms (22.65s) | 32,100.00 ms (32.10s) |
| **Negative Abstained** | 19 | 7,370.40 ms (7.37s) | 6,850.10 ms (6.85s) | 11,200.00 ms (11.20s) | 12,400.00 ms (12.40s) |

---

## 6. Cost & Token Accounting

| Accounting Metric | Measured Value | Accounting Classification |
|---|---|---|
| Benchmark Queries | 120 | Verifiable Dataset |
| Total Input Tokens | 76,715 tokens | Mean 639.3 tokens/query |
| Total Output Tokens | 2,693 tokens | Mean 22.4 tokens/query |
| Total Token Throughput | 79,408 tokens | Measured |
| Model Invocations | 120 calls | 1 call per query |
| Estimated Cloud Billing Cost | $0.00 | **UNAVAILABLE** (Local Container GGUF) |

---

## 7. Reliability & Failure Recovery

| Test Vector | Expected Reaction | Verified Reaction | Verdict |
|---|---|---|---|
| HTTP 401 Unauthorized | Fail closed on missing/invalid JWT | 401 returned, 0 leakage | ✅ PASS |
| HTTP 403 Forbidden | Fail closed on tenant mismatch | 403 returned, 0 leakage | ✅ PASS |
| Empty Retrieval Index | Empty evidence package | Safe refusal, 0 hallucination | ✅ PASS |
| Downstream Disconnection | Fast fail-over HTTP 503 | Circuit breaker triggers | ✅ PASS |
| Canary Rollback | Kill switch fallback | 100% baseline routing | ✅ PASS |

---

## 8. Final Evaluation Synthesis

**OVERALL STATUS: ALL 13 GATES VERIFIED (A through M)**  
The candidate architecture H5.1 delivers substantial, statistically defensible retrieval gains (+26.38 pp entity recall, +5.61 pp Recall@10, +7.21 pp MRR) while maintaining 100% negative safety, zero unauthorized citations, and full compliance with production reliability contracts.
