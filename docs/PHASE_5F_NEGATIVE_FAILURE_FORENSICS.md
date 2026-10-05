# PHASE 5F — Q4_K_M NEGATIVE-CASE FAILURE FORENSICS

**ATLAS Version:** 0.4.14  
**Document Type:** Technical Forensics Specification  
**Classification:** Engineering — Internal  
**Phase 5E Verdict (unchanged):** REJECT  
**Phase 5F Status:** PASS — HYPOTHESIS BOUNDED, NEXT EXPERIMENT DEFINED

---

## 1. Objective

Phase 5F is a **forensics-only measurement phase** executed after Phase 5E dual-backend certification produced a REJECT verdict. The sole purpose is to determine **WHY Backend B failed Gate G3** (Negative Case Abstention).

**No fixes were implemented. No production provider changes were made. The REJECT verdict from Phase 5E is unchanged.**

---

## 2. Phase 5E Failure Summary

| Backend | Provider | Model | Runtime | G3 Score | G3 Status |
|---------|----------|-------|---------|----------|-----------|
| A (production) | `LocalHuggingFaceProvider` | `google/gemma-3-1b-it` | PyTorch CPU float32 | 19/19 (100%) | PASS |
| B (candidate) | `InferenceServiceAdapter` | `gemma3:1b` | llama.cpp / Ollama / Q4_K_M GGUF | 15/19 (78.95%) | **FAIL** |

Gate G3 threshold: 19/19 (100% negative case abstention required).

---

## 3. Failing Cases Identified

Four cases where Backend A abstained but Backend B answered:

| Evaluation ID | Category | Tenant | Evidence Count | B Answer (truncated) |
|--------------|----------|--------|---------------|---------------------|
| EVAL-0088 | `cross_tenant` | TENANT-NOVASTACK | 10 (top-3 exposed) | API gateway routing config... |
| EVAL-0090 | `cross_tenant` | TENANT-ORBITAL | 7 (top-3 exposed) | API gateway config params... |
| EVAL-0092 | `cross_tenant` | TENANT-PINECONE | 7 (top-3 exposed) | TLS termination, rate-limiting... |
| EVAL-0096 | `role_restricted` | TENANT-NOVASTACK | 10 (top-3 exposed) | Session replay risk in legacy token... |

---

## 4. Input Parity Verification

All inputs were **identical** for both backends: `query`, `evidence_package`, `tenant_id`, `expected_document_ids`, `forbidden_document_ids`, `prompt_strategy` (config_a_calibrated), `max_new_tokens=60`, `max_evidence_items=3`, `citation_resolver=c2`.

Divergence: `model_runtime` (FP32 vs Q4_K_M), `generation_latency_ms` (expected).

---

## 5. Architecture — Where Divergence Occurs

```
Both backends receive identical inputs:
query + evidence_package + tenant_id + forbidden_doc_ids

Layer 1 (pre-gate): evidence_count == 0 → abstain (deterministic, shared logic)
                    evidence_count > 0 → proceed to Layer 3

Layer 3 (model inference):
  Backend A → torch.generate() [FP32, full precision]
  Backend B → llama.cpp via Ollama [Q4_K_M GGUF, 815 MB]

<< DIVERGENCE OCCURS HERE >>

Backend A output: "Insufficient evidence to answer this question."   (abstained)
Backend B output: [factual response citing EVD tags]                  (answered)
```

The Layer 1 pre-gate is **deterministic** and fires correctly for both backends when `evidence_count == 0` (confirmed by EVAL-0094, EVAL-0097 with 0 evidence → both abstained). The failure occurs exclusively at **Layer 3 model inference** when evidence IS present.

---

## 6. Root Cause Classification

| Category | Label | Assignment |
|----------|-------|-----------|
| **G** (Primary) | QUANTIZATION-INDUCED MODEL BEHAVIOR DIFFERENCE | ✅ All 4 cases |
| **F** (Secondary) | INFERENCE RUNTIME BEHAVIOR DIFFERENCE | ✅ All 4 cases |
| A — INPUT DIVERGENCE | | ❌ Ruled out (parity confirmed) |
| B — EVIDENCE/CONTEXT DIVERGENCE | | ❌ Ruled out (same evidence_package) |
| C — PROMPT SERIALIZATION DIVERGENCE | | ❌ Ruled out (same template) |
| D — GENERATION PARAMETER DIVERGENCE | | ❌ Ruled out (same max_new_tokens=60) |
| E — STOP/EOS/TOKENIZATION DIVERGENCE | | Possible co-factor; cannot confirm without tokenizer comparison |

---

## 7. Bounded Hypothesis

> **Q4_K_M quantization reduces the probability mass for the abstention phrase when semantically relevant evidence is present in the context window. When the competing signal (on-topic factual evidence) is strong, the Q4_K_M model generates factual answers rather than the calibrated abstention phrase.**

The abstention instruction (`"If insufficient evidence exists, respond with exactly: 'Insufficient evidence to answer this question.'"`) is a **semantic language-level constraint**. It is not a hard-coded computation in inference logic. The probability of generating this exact phrase at Layer 3 depends on the model's weight distribution. Quantization to Q4_K_M compresses weights from 16-bit to ~4-bit, which can shift probability distributions at boundary conditions.

**Evidence supporting hypothesis:**
- EVAL-0094, EVAL-0097: `role_restricted`, 0 evidence → both A and B abstained correctly (Layer 1 deterministic pre-gate, model not invoked)
- 7 `missing_information` cases: both A and B abstain (evidence present but not semantically relevant to query)
- 4 failures: evidence is semantically relevant, on-topic, `evidence_status=accepted` → Q4_K_M generates factual answers

**Evidence creating uncertainty:**
- 2 `authorization` + 2 `user_acl` cases: evidence present, B correctly abstained → abstention not uniformly broken under quantization
- No logit-level probability inspection available from Ollama API

---

## 8. Security Boundary

**Security check: PASS for all 4 failing cases.**

Backend B's abstention failures are behavioral policy failures, NOT security violations:
- No forbidden documents were cited
- No cross-tenant data leakage occurred
- No authorization bypass occurred
- Citations reference `evidence_status=accepted` items validated by the C2 citation resolver

---

## 9. G6/G7 Backend A Label Clarification

The Phase 5E gate harness evaluates G6 (mean latency ≤ 30,000 ms) and G7 (P95 latency ≤ 60,000 ms) **only for Backend B**. Backend A latency is recorded for reference:

- Backend A mean: 58,534 ms (CPU-only FP32, expected to be slow)
- Backend A P95: 272,428 ms
- Backend B mean: 14,877 ms (**PASS**)
- Backend B P95: 22,722 ms (**PASS**)

Backend A is the **production reference**, not the candidate. These thresholds measure the candidate's (Backend B's) suitability for production. The gate `pass_b` field correctly represents Backend B's status. The phase_5e_promotion_decision_report.md G6/G7 Backend A column should be annotated as informational only.

---

## 10. Next Experiment (If Required by CTO)

To **confirm or refute** the quantization hypothesis before any remediation attempt:

**Experiment 1 (Isolation — evidence removal):**  
Run the 4 failing cases against Backend B with `evidence_package.selected_evidence = []`. If Backend B abstains → confirms that the model inference with evidence present is the divergence point.

**Experiment 2 (Quantization isolation):**  
Run the 4 failing cases against a 32-bit or 16-bit GGUF variant of gemma3:1b via Ollama. If abstention is restored → confirms quantization (not llama.cpp runtime) is the root cause.

**Note:** These experiments are NOT authorized during Phase 5F. They require explicit CTO approval as a new measurement phase.

---

## 11. Phase 5F Invariants Confirmed

| Invariant | Status |
|-----------|--------|
| Production provider: `LocalHuggingFaceProvider` | ✅ Unchanged |
| Production model: `google/gemma-3-1b-it` | ✅ Unchanged |
| Phase 5E verdict: REJECT | ✅ Unchanged |
| Source code changes: none | ✅ Confirmed |
| `production_changes: []` | ✅ Confirmed |
| `pyproject.toml` version: 0.4.14 | ✅ Unchanged |
| `PROJECT_CONTEXT.md` | ✅ Unchanged |
| `DECISIONS.md` | ✅ Unchanged |

---

## PHASE 5F STATUS: PASS — HYPOTHESIS BOUNDED, NEXT EXPERIMENT DEFINED
