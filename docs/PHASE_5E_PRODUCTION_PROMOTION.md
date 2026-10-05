# PHASE 5E: PRODUCTION PROMOTION DECISION
**ATLAS Architecture & Technical Specification Document**

---

## 1. Executive Summary

Phase 5E executes a formal 120-case dual-backend certification benchmark comparing:
- **Backend A** (Production Default): `LocalHuggingFaceProvider` — `google/gemma-3-1b-it`, PyTorch CPU, torch.float32
- **Backend B** (Container Candidate): `InferenceServiceAdapter` — `gemma3:1b` Q4_K_M via containerized Ollama on port 8001

Both backends were evaluated sequentially (never concurrently) using identical:
- Evaluation cases (120 from Phase 4E)
- Retrieval configuration
- Evidence assembly pipeline
- Prompt strategy (`config_a_calibrated`)
- Citation resolver (`c2`)
- Security configuration
- Max evidence items (3)
- Max new tokens (60)

```
============================================================
VERDICT: REJECT — Failed gates: G3 (Negative Case Abstention)
============================================================
```

---

## 2. Mandatory SLA Gate Results

| Gate | Name | Threshold | Backend A | Backend B | B Status |
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

**All gates passed:** NO

---

## 3. Benchmark Evidence

- Backend A duration: 118.5 minutes (120 cases)
- Backend B duration: 29.8 minutes (120 cases)
- Citation completeness: A = 91.38%, B = 91.94%
- Citation precision: A = 100.0%, B = 100.0%
- Security violations: A = 0, B = 0
- Negative safety: A = 19/19, B = 15/19

---

## 4. Production Default State

- `production_default_changed`: **false**
- `production_promotion`: **false**
- Default provider remains: `LocalHuggingFaceProvider`
- Default model remains: `google/gemma-3-1b-it`
- `pyproject.toml` version remains: `0.4.14`
- `PROJECT_CONTEXT.md`: NOT modified
- `DECISIONS.md`: NOT modified

---

## 5. Artifact Reference

- Structured JSON: `artifacts/phase_5e_promotion_decision.json`
- Comprehensive Report: `artifacts/phase_5e_promotion_decision_report.md`
- This Document: `docs/PHASE_5E_PRODUCTION_PROMOTION.md`

---

## 6. CTO Review

Backend B (InferenceServiceAdapter) has NOT met all mandatory SLA gates. The production default remains LocalHuggingFaceProvider. No promotion is recommended.
