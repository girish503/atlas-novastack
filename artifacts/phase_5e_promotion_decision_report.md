# ATLAS Phase 5E — Production Promotion Decision Report

**Timestamp:** 2026-09-20T10:24:32.962050+00:00
**Duration:** 148.5 minutes

---

## Verdict

```
============================================================
REJECT — Failed gates: G3 (Negative Case Abstention)
============================================================
```

**Production default changed:** NO
**Version:** 0.4.14 (unchanged)

---

## Mandatory SLA Gates

| Gate | Name | Threshold | Backend A | Backend B | B Result |
|---|---|---|---|---|---|
| G1 | Citation Completeness | >= 90.0% | 91.38% | 91.94% | PASS |
| G2 | Mechanical Citation Precision | = 100.0% | 100.0% | 100.0% | PASS |
| G3 | Negative Case Abstention | 19/19 (100%) | 19/19 (100.0%) | 15/19 (78.95%) | **FAIL** |
| G4 | Security Invariants | 0 violations | 0 | 0 | PASS |
| G5 | Positive Successful Outcomes | >= 40/101 | 53/101 | 57/101 | PASS |
| G6 | Mean Latency | <= 30000 ms | 58534.42 ms | 14877.02 ms | PASS |
| G7 | p95 Latency | <= 60000 ms | 272427.84 ms | 22721.91 ms | PASS |
| G8 | Correctness Non-Regression vs A | >= 89.38% (A - 2%) | 91.38% | 91.94% | PASS |
| G9 | Regression Suite | 752/752 pass | 752/752 (pre-verified) | 752/752 (pre-verified) | PASS |

---

## Backend Comparison

| Metric | Backend A (LocalHuggingFace) | Backend B (InferenceServiceAdapter) |
|---|---|---|
| **Provider** | LocalHuggingFaceProvider | InferenceServiceAdapter |
| **Model** | google/gemma-3-1b-it (PyTorch CPU) | gemma3:1b (Q4_K_M Ollama container) |
| **Benchmark Duration** | 118.5 min | 29.8 min |
| **Answered** | 57 | 66 |
| **Partially Answered** | 1 | 0 |
| **Abstained** | 62 | 54 |
| **Positive Answered** | 58/101 | 62/101 |
| **Positive w/ Valid Citation** | 53/101 | 57/101 |
| **Citation Completeness** | 91.38% | 91.94% |
| **Citation Precision** | 100.0% | 100.0% |
| **Total Citations** | 86 | 93 |
| **Valid Citations** | 86 | 93 |
| **Negative Abstention** | 19/19 (100.0%) | 15/19 (78.95%) |
| **Security Violations** | 0 | 0 |
| **Mean Latency** | 58534.42 ms | 14877.02 ms |
| **p50 Latency** | 27680.97 ms | 15887.68 ms |
| **p90 Latency** | 147477.78 ms | 21291.27 ms |
| **p95 Latency** | 272427.84 ms | 22721.91 ms |

---

## Immutability Verification

- **Pre-benchmark:** All 20 baseline artifacts verified (SHA-256)
- **Post-benchmark:** All 20 baseline artifacts verified (SHA-256)

---

## Production Default Invariants

- `production_default_changed`: false
- `production_promotion`: false
- Default provider: `LocalHuggingFaceProvider`
- Default model: `google/gemma-3-1b-it`
- `pyproject.toml` version: `0.4.14`

---

*Report generated automatically by Phase 5E Certification Benchmark.*
