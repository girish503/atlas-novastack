# ATLAS 0.5.0-rc1 — Release Candidate Specification

**Release Version**: `0.5.0-rc1`  
**Base Production Release**: `0.4.14`  
**Governing Promotion Review**: PRR-01  
**Status**: Release Candidate (Candidate for Staged Promotion)  
**Date**: 2026-10-04  

---

## 1. Release Overview

ATLAS `0.5.0-rc1` represents the culmination of the Phase 0.5 engineering program, advancing ATLAS from retrieval-focused heuristics to an end-to-end evidence-calibrated enterprise search and answering engine.

Across Milestones M1 through M8, ATLAS 0.5 introduced eight structural architectural capabilities:
1. **M1 — Multi-Hop & Temporal Reasoning**: Lineage tracking and valid-time temporal filtering.
2. **M2 — Compaction & Delta Index**: Dynamic update compaction with zero data loss.
3. **M3 — Entity Grounding Gate**: Deterministic catalog disambiguation and entity linking.
4. **M4 — Hierarchical Budgeting**: Tiered token budgeting under strict context ceilings.
5. **M5 — Minimum Sufficient Evidence**: Set-cover optimization for structural completeness.
6. **M6 — Entity-Anchored Retrieval**: Graph-aware traversal across related operational entities.
7. **M7 — Query-Adaptive Depth**: Dynamic budget scaling (simple: 3 docs, multi-hop: 4 docs).
8. **M8 — Answerability Calibration & Targeted Extraction**: Sentence-level evidence extraction with calibrated safe prompt dispatch.

---

## 2. Certified Performance Benchmarks

Measured across the 120 canonical benchmark cases (101 positive, 19 negative) on the frozen single-node operating envelope:

| Dimension | 0.4.14 Baseline | 0.5.0-rc1 Candidate | Delta |
| :--- | :--- | :--- | :--- |
| **Positive Answer Yield** | 62.38% (63/101) | **73.27% (74/101)** | **+10.89 pp (+11 cases)** |
| **Negative Safety** | 100.0% (19/19) | **100.0% (19/19)** | **Invariance Preserved** |
| **Citation Precision** | 100.0% (80/80) | **100.0% (122/122)** | **Invariance Preserved** |
| **Citation Completeness** | 93.65% | **93.24% (69/74)** | **Maintained ($\ge 90\%$)** |
| **Multi-Hop Slice Yield** | — | **77.78% (14/18)** | **Record High** |
| **Mean Positive Latency** | 14,414 ms | **14,899 ms** | **Within SLA ($\le 15{,}000$ ms)** |
| **Security Violations** | 0 | **0** | **Zero Vulnerabilities** |
| **Execution Timeouts** | 0 | **0** | **Zero Timeouts** |

---

## 3. Candidate Runtime Configuration

```python
from novastack.evidence_selector import SelectorConfig, MinimumSufficientEvidenceSelector

selector_config = SelectorConfig(
    enable_adaptive_depth=True,
    adaptive_budget_simple=3,
    adaptive_budget_multihop=4,
    require_entity_overlap_for_fill=True,
    enable_alias_context_notes=True,
    enable_contrastive_disambiguation=True,
    enable_targeted_missing_role_recovery=True,
    enable_targeted_evidence_extraction=True,
    max_extracted_sentences_per_chunk=3,
    treat_ungrounded_as_protective=False,
)
```

- **Context Strategy**: `m8_targeted_extraction`
- **Prompt Strategy**: `config_b_calibrated_safe` (dynamic dispatch: B0 strict absent-fact clause on protective queries; B3 calibrated on positive queries)
- **Citation Resolver**: `c2` (sentence/chunk mechanical verification)
- **Primary Backend (Backend B)**: `InferenceServiceAdapter` pointing to containerized `gemma3:1b` (Q4_K_M GGUF, 815 MB) on port 8001
- **Rollback Backend (Backend A)**: `LocalHuggingFaceProvider` (`google/gemma-3-1b-it` FP32 on CPU) activatable via `ATLAS_INFERENCE_PROVIDER=local_huggingface`

---

## 4. Security & Safety Guarantees

1. **Deterministic Abstention**: All out-of-scope (`EVAL-0054`), secret-seeking (`EVAL-0058`), and Layer 1S queries (`EVAL-0088`, `EVAL-0090`, `EVAL-0092`, `EVAL-0096`) abstain deterministically.
2. **Zero Unauthorized Evidence**: Pre-selection filter strictly discards unauthorized, deprecated, or cross-tenant candidates.
3. **Fail-Closed Execution**: Any infrastructure or classification anomaly defaults to secure abstention.
4. **Data Isolation**: Multi-tenant boundaries enforced prior to embedding retrieval and prompt generation.

---

## 5. Deployment & Rollback Instructions

### Deployment Prerequisites
- x86_64 CPU host (8GB RAM recommended)
- Docker installed and running
- Model container `atlas-inference-5d` active on port 8001

### Promotion Verification Command
```bash
# Verify all 236 regression tests
python -m pytest tests/test_phase_05_m*.py tests/test_phase_5n*.py tests/test_phase_5o*.py tests/test_phase_5c*.py -v
```

### Emergency Rollback
If an operational anomaly occurs post-deployment:
```bash
# Switch to certified 0.4.14 rollback backend
export ATLAS_INFERENCE_PROVIDER=local_huggingface
# Restart API service
```

---

## 6. Review Sign-Off

- **PRR-01 Review Status**: `PROMOTION_READY`
- **G1–G20 Gates**: 20/20 Passed
- **Production Baseline Status**: 0.4.14-rc1 untouched, frozen, immutable
- **Next Administrative Action**: Awaiting manual executive approval for staged canary deployment.
