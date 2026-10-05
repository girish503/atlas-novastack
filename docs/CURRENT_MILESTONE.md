# Current Milestone

## ATLAS 0.5 — Milestone M8: Answerability Calibration & Targeted Evidence Extraction

**Global-Level Controlled Experiment Across 120 Benchmark Cases Under Frozen 0.4.14-rc1 Production Baseline**

### Objective

Experimentally determine whether the remaining ATLAS positive-answer failures are primarily caused by:
1. Evidence-density/context inefficiency (H1),
2. Answer-generation prompt calibration (H2),
3. Evidence sufficiency representation (H3),
4. Or an intrinsic limitation of the current Gemma 3 1B Q4_K_M answering backend (H4).

Primary success target: Positive Answer Yield $\ge 66.34\%$ (67/101 cases) while preserving ALL critical safety invariants.

### Status

✅ **All 10 Mandatory Gates PASSED** (Official Verdict: **`PASS`**; M8 Candidate Configuration Qualified as **`CANDIDATE FOR 0.5 PROMOTION REVIEW`**)

### M8 Gate Scorecard

| Gate | Metric | Target | M8 Measured | Status |
| :--- | :--- | :--- | :--- | :--- |
| **G1** | Positive Answer Yield | $\ge 66.34\%$ (67/101) | **73.27% (74/101)** | ✅ **PASS** |
| **G2** | Negative Abstention Safety | $= 100.0\%$ (19/19) | **100.0% (19/19)** | ✅ **PASS** |
| **G3** | Mechanical Citation Precision | $= 100.0\%$ | **100.0% (122/122)** | ✅ **PASS** |
| **G4** | Citation Completeness | $\ge 90.0\%$ | **93.24% (69/74)** | ✅ **PASS** |
| **G5** | Security Violations | $= 0$ | **0** | ✅ **PASS** |
| **G6** | Mean Positive Latency | $\le 15{,}000$ ms | **14,899.67 ms** | ✅ **PASS** |
| **G7** | Multi-Hop Focus Slice | $\ge 13/18$ | **14/18 (77.78%)** | ✅ **PASS** |
| **G8** | Timeouts | $= 0$ | **0** | ✅ **PASS** |
| **G9** | Fail-Closed Verification | True | **True** | ✅ **PASS** |
| **G10** | Protective Non-Regression | EVAL-0054 & EVAL-0058 abstained | **Both abstained** | ✅ **PASS** |

### Hypothesis Assessment

1. **H1: Evidence-density/context inefficiency** — **CONFIRMED**. Sentence-level targeted extraction compresses evidence by ~19.5% while preserving entity anchors and causal bridges, recovering 12 additional positive cases.
2. **H2: Answer-generation prompt calibration** — **CONFIRMED WITH SAFETY BOUNDARY**. Dynamic dispatch (`config_b_calibrated_safe`: B0 for protective, B3 for non-protective) resolves false abstention without weakening safety.
3. **H3: Evidence sufficiency representation** — **CONFIRMED**. DOC-DOC classification correction and intent-aware query planning recover incorrectly protective-classified queries.
4. **H4: Intrinsic backend limitation** — **REFUTED**. Gemma 3 1B outperforms Qwen 2.5 1.5B on the failure corpus under identical evidence.

### Comparative Milestone Matrix

| Dimension | Baseline (0.4.14-rc1) | M2 | M3 | M4 | M5 | M6 | M7 | **M8** | Target | Outcome |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Positive Answer Yield** | 62.38% (63/101) | 66.34% (67/101) | 57.43% (58/101) | 60.40% (61/101) | 65.35% (66/101) | 61.39% (62/101) | 61.39% (62/101) | **73.27% (74/101)** | $\ge 66.34\%$ | ✅ **PASS** |
| **Negative Case Abstention** | 100.0% (19/19) | 89.47% (17/19) | 100.0% (19/19) | 100.0% (19/19) | 100.0% (19/19) | 100.0% (19/19) | 100.0% (19/19) | **100.0% (19/19)** | $= 100.0\%$ | ✅ **PASS** |
| **EVAL-0054 (Satellite)** | `abstained` | `answered` ❌ | `abstained` ✅ | `abstained` ✅ | `abstained` ✅ | `abstained` ✅ | `abstained` ✅ | **`abstained` ✅** | `abstained` | ✅ **PASS** |
| **EVAL-0058 (Twilio SMS)** | `abstained` | `answered` ❌ | `abstained` ✅ | `abstained` ✅ | `abstained` ✅ | `abstained` ✅ | `abstained` ✅ | **`abstained` ✅** | `abstained` | ✅ **PASS** |
| **Citation Precision** | 100.0% (80/80) | 100.0% (111/111) | 100.0% (84/84) | 100.0% (88/88) | 100.0% (88/88) | 100.0% (85/85) | 100.0% (99/99) | **100.0% (122/122)** | $= 100.0\%$ | ✅ **PASS** |
| **Citation Completeness** | 93.65% | 92.54% | 89.66% | 88.52% | 89.39% | 93.55% | 91.94% | **93.24%** | $\ge 90.0\%$ | ✅ **PASS** |
| **Security Violations** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** | 0 | ✅ **PASS** |
| **Mean Positive Latency** | 14,414 ms | 11,632 ms | 15,564 ms | 15,541 ms | 13,620 ms | 14,102 ms | 15,810 ms | **14,900 ms** | $\le 15{,}000$ ms | ✅ **PASS** |
| **Multi-Hop Slice Yield** | — | — | — | 55.56% (10/18) | 66.67% (12/18) | 72.22% (13/18) | 77.78% (14/18) | **77.78% (14/18)** | $\ge 72.22\%$ | ✅ **PASS** |

### Milestone Verdict & Directives

- **Official Verdict**: **`PASS`**.
- M8 achieved the first all-gates-passing milestone since the 0.4.14-rc1 release candidate. Positive answer yield reached 73.27% (74/101), exceeding the pre-registered recovery target of $\ge 66.34\%$ by 7 cases and surpassing M2's previous high-water mark of 66.34% (67/101) by 7 additional cases.
- All safety invariants strictly preserved: 100% negative abstention, 100% citation precision, 93.24% citation completeness, 0 security violations, 0 cross-tenant exposures, 0 unauthorized evidence, 0 forbidden citations.
- Mean positive latency reduced to 14,899.67ms (within the $\le 15,000$ms SLA), down from M7's 15,810.17ms.
- M8 candidate configuration (`m8_targeted_extraction` + `config_b_calibrated_safe` + DOC-DOC role classification + intent-aware planning) qualified as **CANDIDATE FOR 0.5 PROMOTION REVIEW**.
- **No automatic promotion.** Production baseline `0.4.14-rc1` remains frozen, immutable, and unmodified.
