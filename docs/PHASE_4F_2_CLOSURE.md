# ATLAS — Phase 4F-2 Final Closure Audit

**Audit Date:** 2026-09-11  
**Auditor:** ATLAS System Architecture & Verification Team  
**Review Status:** Final Reconciliation Audit for CTO Review  
**Milestone State:** Phase 4F-2 Concluded; Phase 4F-1 Not Closed; Phase 4G Not Started  
**Codebase & Retrieval State:** Immutable (BM25, Dense, Structured Relational Retrieval, RRF, MetadataReranker, Phase 4E Evidence Assembly/Resolution, and Ground Truth unmodified)  

---

## 1. Executive Summary & Context

This closure audit provides the definitive technical reconciliation of the **Phase 4F-2: Evidence Context Pruning & Abstention Calibration** experiment on the ATLAS enterprise RAG pipeline.

Phase 4F-2 evaluated whether parameterizing prompt evidence context depth ($N \in \{3, 5, 7\}$ vs. baseline $N=10$) under strict Config A conservative refusal instructions mitigates attention dilution on the 54 Stage-E false-abstention queries while maintaining 100% refusal fidelity on unanswerable negative queries.

All tests, hash audits, and case reconciliations documented below were executed on the final codebase state.

---

## 2. A0 / A1 / A2 / A3 Final Benchmark Comparison Matrix

Evaluation performed across all 120 canonical cases ($120 \times 4 = 480$ total evaluation passes) using local CPU inference on `google/gemma-3-1b-it` under greedy decoding (`do_sample=False`, `temperature=0.0`):

| Metric Dimension | A0 (Baseline: 10 items) | A1 (Top 3 items) | A2 (Top 5 items) | A3 (Top 7 items) | Architectural Target |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **Max Prompt Evidence Items ($N$)** | 10 | **3** | 5 | 7 | Controlled serialization |
| **Mean Input Tokens** | 1,954.6 | **772.9** (-60.5%) | 1,130.8 | 1,475.0 | Context compression |
| **Mean Output Tokens** | 4.8 | **13.5** | 6.0 | 5.3 | Succinct generation |
| **Complete Answers (`answered`)** | 17 (14.17%) | **32 (26.67%)** | 13 (10.83%) | 13 (10.83%) | Factual answer yield |
| **Partial Answers (`partially_answered`)** | 1 (0.83%) | **1 (0.83%)** | 1 (0.83%) | 0 (0.0%) | Semantic coverage |
| **Principled Abstentions (`abstained`)** | 102 (85.00%) | **87 (72.50%)** | 106 (88.33%) | 107 (89.17%) | Conservative refusal |
| **Total Successful Outcomes** | **18 (15.00%)** | **33 (27.50%)** | **14 (11.66%)** | **13 (10.83%)** | Answerable yield |
| **54 Stage E Cases Recovered** | 0 / 54 (0.0%) | **20 / 54 (37.04%)** | 4 / 54 (7.41%) | 3 / 54 (5.56%) | H1: Dilution mitigation |
| **19 Negative Cases Correct Abstention** | **19 / 19 (100.0%)** | **19 / 19 (100.0%)** | **19 / 19 (100.0%)** | **19 / 19 (100.0%)** | H2: Refusal fidelity |
| **Negative Cases False Answers (Hallucinations)**| **0 (0.0%)** | **0 (0.0%)** | **0 (0.0%)** | **0 (0.0%)** | Zero-tolerance safety gate |
| **Total Citations Emitted** | 58 | 53 | 32 | 38 | Mechanical count |
| **Mechanically Valid Citations** | 58 | 53 | 32 | 38 | Verified citations |
| **Mechanical Citation Precision** | **100.0%** (58/58) | **100.0%** (53/53) | **100.0%** (32/32) | **100.0%** (38/38) | Valid / Emitted (denom > 0) |
| **Answer-Level Citation Completeness** | **77.78%** (14/18) | **93.94%** (31/33) | **71.43%** (10/14) | **84.62%** (11/13) | Answered cases with ≥1 cit |
| **Tested Security Invariants** | **HELD (0 leaks)** | **HELD (0 leaks)** | **HELD (0 leaks)** | **HELD (0 leaks)** | Cross-tenant/unauth/forb/adv |
| **Mean Inference Latency** | 29,293 ms | **14,594 ms** (-50.2%) | 17,260 ms | 21,586 ms | Latency reduction |
| **P50 Inference Latency** | 28,456 ms | **12,603 ms** (-55.7%) | 16,303 ms | 20,950 ms | Median duration |
| **Byte-Identical Reproducibility** | **100% (5/5)** | **100% (5/5)** | **100% (5/5)** | **100% (5/5)** | Repeated identical runs |

---

## 3. Stage-E 54-Case Reconciliation

### 3.1 Exact Outcome Breakdown Across the 54 Target Cases

The 54 Stage-E false-abstention cases were established in Phase 4F reconciliation as queries where relevant evidence was successfully retrieved into the candidate pool and selected into the top-10 prompt context, but the small language model conservatively refused under multi-document dilution.

Tracking every one of these 54 cases between A0 ($N=10$) and A1 ($N=3$):

- **In A0 ($N=10$)**:
  - `abstained`: **54 / 54 (100.0%)** (all failed due to `insufficient_evidence`)
  - `answered`: **0**
  - `partially_answered`: **0**

- **In A1 ($N=3$)**:
  - **Became `answered`**: **20 cases (37.04%)**  
    `EVAL-0004`, `EVAL-0005`, `EVAL-0006`, `EVAL-0007`, `EVAL-0008`, `EVAL-0012`, `EVAL-0015`, `EVAL-0018`, `EVAL-0021`, `EVAL-0022`, `EVAL-0025`, `EVAL-0037`, `EVAL-0046`, `EVAL-0047`, `EVAL-0048`, `EVAL-0051`, `EVAL-0060`, `EVAL-0068`, `EVAL-0072`, `EVAL-0109`
  - **Became `partially_answered`**: **0 cases (0.0%)**
  - **Remained `abstained`**: **34 cases (62.96%)**  
    `EVAL-0001`, `EVAL-0002`, `EVAL-0003`, `EVAL-0010`, `EVAL-0011`, `EVAL-0014`, `EVAL-0024`, `EVAL-0026`, `EVAL-0027`, `EVAL-0038`, `EVAL-0039`, `EVAL-0040`, `EVAL-0041`, `EVAL-0042`, `EVAL-0043`, `EVAL-0050`, `EVAL-0062`, `EVAL-0063`, `EVAL-0064`, `EVAL-0066`, `EVAL-0070`, `EVAL-0075`, `EVAL-0077`, `EVAL-0081`, `EVAL-0082`, `EVAL-0083`, `EVAL-0089`, `EVAL-0091`, `EVAL-0098`, `EVAL-0106`, `EVAL-0107`, `EVAL-0108`, `EVAL-0110`, `EVAL-0116`
  - **Changed to another failure category**: **0 cases (0.0%)**
  - **Any other outcome**: **0 cases (0.0%)**
  - **Sum accounted for**: $20 + 0 + 34 + 0 + 0 = \mathbf{54 / 54 (100.0\%)}$.

### 3.2 Reconciliation: 20 Stage-E Recoveries vs. +15 Net Successful Outcomes

A mathematical discrepancy appears on the surface: A1 recovered **20** Stage-E cases, yet total successful outcomes only rose from **18** in A0 to **33** in A1 ($\Delta = +15$).

The audit reconciles this delta completely:

$$\text{Net Change } \Delta = \text{Cases Gained} - \text{Cases Lost} = 21 - 6 = \mathbf{+15}$$

#### A. Cases Gained in A1 (+21 total):
1. **20 cases** from the Stage-E 54 target set (listed above).
2. **1 additional case** from outside the Stage-E target set: `EVAL-0117`. In Phase 4F reconciliation, `EVAL-0117` had been classified as Stage-A candidate starvation. However, in Phase 4F-2 A0 ($N=10$), it received 10 background items and abstained (`insufficient_evidence`). In A1 ($N=3$), pruning background distractors allowed Gemma to successfully answer from the top-3 items.

#### B. Cases Lost in A1 (-6 total):
Six cases that succeeded in A0 ($N=10$) failed to answer in A1 ($N=3$), reverting to `abstained`:
- `EVAL-0013`
- `EVAL-0016`
- `EVAL-0019`
- `EVAL-0044`
- `EVAL-0049`
- `EVAL-0061`

#### C. Root Cause Analysis of the 6 Lost Cases:
1. **Physical Pruning of Multi-Document Evidence ($N > 3$)**:
   - `EVAL-0049`: Trace the database write freeze. Ground truth requires `DOC-PM-EVT-NS-0006-01` and `DOC-DEP-DEP-NS-0005-01`. In the Phase 4E `selected_evidence`, the postmortem document was placed at **rank 4**. In A0 ($N=10$), rank 4 was present in the prompt, allowing the answer. In A1 ($N=3$), rank 4 was pruned out. Lacking the necessary postmortem, the model correctly and conservatively refused.
   - `EVAL-0044`: Trace the full checkout outage causal chain. Requires 3 distinct documents: `DOC-PM-EVT-NS-0001-01`, `DOC-DEP-DEP-NS-0001-01`, `DOC-PR-PR-NS-0001-01`. Only 1 expected document was ranked in the top 3; the remaining two were ranked at 4–10. In A0, all 3 were accessible. In A1, the missing links caused the model to recognize incomplete coverage and conservatively refuse.
2. **Context Framing & Corroboration Sensitivity**:
   - `EVAL-0013`, `EVAL-0016`, `EVAL-0019`, `EVAL-0061`: In these cases, the primary expected document was within the top 3 ranks, but supporting context documents (e.g. incident reports or duplicate runbooks) were located at ranks 4–10. Under strict Config A refusal instructions, `google/gemma-3-1b-it` refused when presented with isolated snippets, requiring the corroborating context present in the 10-item pool to cross its confidence threshold.

---

## 4. Partial-Answer Audit

### 4.1 Exact Distribution Across Configurations

| Configuration | Answered | Partially Answered | Abstained | Total Cases |
| :--- | :---: | :---: | :---: | :---: |
| **A0 ($N=10$)** | 17 | 1 | 102 | 120 |
| **A1 ($N=3$)**  | 32 | 1 | 87  | 120 |
| **A2 ($N=5$)**  | 13 | 1 | 106 | 120 |
| **A3 ($N=7$)**  | 13 | 0 | 107 | 120 |

### 4.2 Audit of the Single `partially_answered` Case (`EVAL-0113`)

In both A0 and A1, exactly one case was classified as `partially_answered`: **`EVAL-0113`**.

- **Query**: `"How do DevOps engineers resolve table lock contention in PostgreSQL?"`
- **Expected Document**: `DOC-ADV-IDC-0001` (Adversarial / unindexed query)
- **Model Output**: `"The provided evidence does not detail how DevOps engineers resolve table lock contention in PostgreSQL."`
- **Citations Emitted**: None (`[]`)
- **Classification Mechanism**:
  - The classification engine checks for refusal via exact substring matching against `["insufficient evidence to answer", "i am unable to answer", "the provided evidence does not contain sufficient information"]`.
  - The model's generation (`"The provided evidence does not detail..."`) escaped the strict refusal match.
  - The engine then checked `_PARTIAL_HEDGING_SIGNALS`, matching `"does not detail"`, which routed the outcome to `PARTIALLY_ANSWERED`.

### 4.3 Were Qualifying Partial-Answer Scenarios Actually Exercised?

**Conclusion: NO.**

The evaluation suite contains complex multi-part queries designed to test partial coverage (e.g., queries asking for a symptom, root cause service, deployment ID, and resolving PR). 

The audit confirms:
1. When presented with partial evidence under Config A's conservative refusal prompt, `google/gemma-3-1b-it` **never synthesizes a partial answer**. It uniformly emits the complete refusal: `"Insufficient evidence to answer this question."`
2. `EVAL-0113` was not a genuine partial answer with factual coverage; it was an unstandardized prose refusal matching a substring heuristic.
3. Therefore, qualifying partial-answer scenarios were **not actually exercised** by the 1.0B model under Config A rules.

---

## 5. Citation Metric Definitions & Audit

### 5.1 Exact Mathematical Formulas

The Phase 4F-2 benchmark evaluated four distinct citation metrics:

1. **Citation Presence**:
   $$\text{Citation Presence} = \sum_{c \in \text{Cases}} \mathbb{I}(\text{len}(c.\text{citations}) > 0)$$
   Measures whether at least one bracketed `[EVD-...]` citation tag was emitted in or attached to the response.

2. **Answer-Level Citation Completeness**:
   $$\text{Citation Completeness} = \frac{\sum_{c \in \text{Answered Cases}} \mathbb{I}(\exists \text{cit} \in c.\text{citations} : \text{cit}.\text{status} = \text{VALID})}{\text{Total Answered or Partially Answered Cases}} \times 100\%$$
   - **Exact Definition**: **Percentage of answered cases with $\ge 1$ valid citation**.
   - In A1: $\frac{31}{33} \times 100\% = \mathbf{93.94\%}$. (31 out of 33 answered/partial cases had $\ge 1$ valid citation; 2 cases omitted citations and were attributed to `unsupported_claim`).

3. **Mechanical Citation Precision**:
   $$\text{Citation Precision} = \begin{cases} \frac{\text{Count of VALID Citations}}{\text{Total Citations Emitted}} \times 100\%, & \text{if Total Citations Emitted} > 0 \\ \text{N/A (Undefined)}, & \text{if Total Citations Emitted} = 0 \end{cases}$$
   - Measures the fraction of emitted citations that pass all mechanical checks.
   - In A1: $\frac{53}{53} \times 100\% = \mathbf{100.0\%}$. Zero-denominator cases are explicitly designated as `N/A`, never 100%.

4. **Citation Validity Rate**:
   Identical to mechanical citation precision across the entire pool of emitted citations for the configuration.

### 5.2 Mechanical Validity vs. Semantic Correctness

> [!IMPORTANT]
> **Semantic Claim-Level Correctness Disclaimer**:
> Mechanical citation validity verifies strictly that:
> 1. The citation syntax matches `[EVD-XXX]` or `[DOC-XXX]`.
> 2. The cited evidence ID exists in the query's `selected_evidence`.
> 3. The underlying document and chunk exist in the canonical search corpus.
> 4. The user has valid tenant, role, and clearance authorization to view the document.
> 5. The document is not in `forbidden_document_ids` or quarantined as adversarial.
>
> **These mechanical checks do NOT establish semantic claim-level citation correctness.** They do not verify whether the text within the cited chunk semantically entails, supports, or proves the specific factual assertion in the generated sentence. Natural language inference (NLI) entailment scoring remains unproven and out of scope for Phase 4F-2.

---

## 6. Repository Test Verification

The complete repository test suite was executed post-implementation:

- **Command**: `$env:PYTHONPATH="src"; pytest`
- **Total Tests Collected**: **502**
- **Passed**: **502 (100.0%)**
- **Failed**: **0**
- **Errors**: **0**
- **Execution Time**: 232.93s (0:03:52)

Test coverage encompasses all system layers: adversarial fixtures, BM25, dense retrieval, candidate diagnostics, chunking, citation validation, depth fusion ablation, evaluation datasets, evidence assembly/resolution, grounded generation, hybrid fusion, ingestion, metadata diagnostics, metadata reranking, query profiling, query understanding, relational retrieval, cross-encoder reranking, retrieval readiness, security corpus boundaries, source records, and starvation diagnostics/reconciliation.

---

## 7. Baseline Immutability Audit (22 Artifacts)

All 22 baseline artifacts were verified via cryptographic SHA256 hashing before and after the closure audit:

| # | Relative Artifact Path | Expected SHA256 Hash | Post-Audit Hash | Status |
| :-: | :--- | :--- | :--- | :---: |
| 1 | `data/raw/novastack/source_records.json` | `f877faf2dec310dca1ca56c57756f8c7368a90468c2f6b0ab74f689eca43eab3` | `f877faf2...` | **MATCH** |
| 2 | `data/raw/novastack/adversarial_fixtures.json` | `1e11fb7d4dd81538281e10a2c2a251c206afb8e59d1a2dd8f9c9e418740a5fee` | `1e11fb7d...` | **MATCH** |
| 3 | `data/raw/novastack/security_fixtures.json` | `9c519bc725ce96463abc8ada2be7e2aa5792cf9dc8cb5c82eb5db7b365252f6f` | `9c519bc7...` | **MATCH** |
| 4 | `data/processed/novastack/search_documents.json` | `ffd7483aec9b4ffce57394880f664cbf79f2ca6733422ba01b235df28e9b9871` | `ffd7483a...` | **MATCH** |
| 5 | `data/processed/novastack/search_chunks.json` | `36fbc12e31cecb146f220a8d58873980c84c08beda770f13f77631b3c6c48605` | `36fbc12e...` | **MATCH** |
| 6 | `data/evaluation/novastack/evaluation_cases.json` | `d6d4caade97047a3606c949a6db34dd2b4561e5596e1bf46fd7dbc8f6d7adc12` | `d6d4caad...` | **MATCH** |
| 7 | `data/evaluation/novastack/bm25_baseline.json` | `91fd7ddbdb837e21089d622da08d4e8c8091d68c74b744500ec332ebfe8c4d52` | `91fd7ddb...` | **MATCH** |
| 8 | `data/evaluation/novastack/dense_baseline.json` | `0d70a7b9523445065754d2a0325703544725e3c5cff587eda0ccc2079dc2acb2` | `0d70a7b9...` | **MATCH** |
| 9 | `data/evaluation/novastack/hybrid_baseline.json` | `794a4a805075f6ff83a966fce8bda756c29e5e5fd0afb4a85c84e49a72374663` | `794a4a80...` | **MATCH** |
| 10 | `data/evaluation/novastack/phase_4b0_candidate_diagnostics.json` | `2493b08e136b7ba40e6e3bbbaace977a3b55f77cf45bff945cdd961c696327c6` | `2493b08e...` | **MATCH** |
| 11 | `data/evaluation/novastack/phase_4b1_reranker_baseline.json` | `30e9ba5da6966b4ee871764e6c45b1ea6db57cbe265389a7c739bf4a9e62bd96` | `30e9ba5d...` | **MATCH** |
| 12 | `data/evaluation/novastack/phase_4c0_query_profiles.json` | `782134fd40068c6bf5994b418428094126202f7fccafa5a12f72b5f264b08226` | `782134fd...` | **MATCH** |
| 13 | `data/evaluation/novastack/phase_4c1_query_understanding.json` | `57fb97475f5541b0c844fb1662b539f54f70aa92038ae8f00dc4e7d00d4d2b6d` | `57fb9747...` | **MATCH** |
| 14 | `data/evaluation/novastack/phase_4c2_metadata_diagnostics.json` | `e6fdd0efe8493ec4cab6dd5c523c4edf1b2771a103a97a230cfc42b8496e2a8c` | `e6fdd0ef...` | **MATCH** |
| 15 | `data/evaluation/novastack/phase_4c3_metadata_reranking.json` | `ea9407b430a0424705e465673b29d8bb0ba42c1cec2406b3f979883f8ecf5766` | `ea9407b4...` | **MATCH** |
| 16 | `data/evaluation/novastack/phase_4d0_starvation_diagnostics.json` | `7628b042e3f29c9935da76388a0380e24d2da27378cddd2cb3e84e38dcdfc31b` | `7628b042...` | **MATCH** |
| 17 | `data/evaluation/novastack/phase_4d0_1_reconciliation.json` | `dceaec3c81d0941c6b25425e3d1b781b23c1f741e6ce2262eec842a93803edc7` | `dceaec3c...` | **MATCH** |
| 18 | `data/evaluation/novastack/phase_4d1_depth_fusion_ablation.json` | `4ab13904c1f686a7c2f011f7bf66f188c5d2215e75259a699fdeecaa661f49cb` | `4ab13904...` | **MATCH** |
| 19 | `data/evaluation/novastack/phase_4d2_relational_retrieval.json` | `7d79d13b696b8bffed0a90751be7fb4e449c2235a716378d3a02f53482a1b00e` | `7d79d13b...` | **MATCH** |
| 20 | `data/evaluation/novastack/phase_4e_evidence_assembly.json` | `8cebe97b2112d70f4fada63b671fe62d90c166259c6560182f066515dcf74357` | `8cebe97b...` | **MATCH** |
| 21 | `data/evaluation/novastack/phase_4f_grounded_generation.json` | `f0e80b362eba1fa928ca2e681d1aec851e9bc7aee5bf010e85732f9d8c64de79` | `f0e80b36...` | **MATCH** |
| 22 | `data/evaluation/novastack/phase_4f1_remediation.json` | `29cb455404382df8dffda953895e41569fff6247c5cdbf05579f31b8430a3019` | `29cb4554...` | **MATCH** |

- **Total Baseline Artifacts Checked**: **22**
- **Unchanged**: **22 (100.0%)**
- **Changed**: **0**

---

## 8. Security Results & Audit Language

### 8.1 Measured Security Telemetry Across All Runs (480 Passes)

- **Cross-Tenant Citations**: **0**
- **Unauthorized Citations**: **0**
- **Forbidden Document Citations**: **0**
- **Adversarial / Poisoned Citations**: **0**
- **Negative Queries False Answers**: **0** (100.0% correct abstention across all 19 queries in A0, A1, A2, A3)

### 8.2 Certified Phrasing

> **Tested security invariants held.**

*(In accordance with CTO policy, universal security, complete prompt-injection immunity, or formal zero-trust proofs are explicitly disclaimed. The findings confirm that all pre-generation authorization gates, tenant boundaries, XML data encapsulation fences, and citation validation checks operated without error across the 120-case test suite).*

---

## 9. Limitations

1. **Context Truncation Information Loss**:
   Truncating evidence to $N=3$ inherently sacrifices multi-hop or distributed queries where answers require $\ge 4$ independent documents. In A1, this caused 6 previously answerable queries to fail.
2. **Refusal Rigidity on Complex Multi-Part Queries**:
   Under Config A refusal instructions, `google/gemma-3-1b-it` is incapable of nuanced partial answering. If any sub-part of a query is missing from evidence, the model uniformly abstains on the entire query.
3. **Absence of Semantic Claim Verification**:
   The current pipeline enforces mechanical citation validity, but lacks runtime NLI verification to detect subtle hallucinated extrapolations within an otherwise valid document reference.
4. **Hardware-Specific Decoding Determinism**:
   While repeated greedy runs (`temperature=0.0`, `do_sample=False`) produced 100% byte-identical outputs on this environment, deterministic reproducibility across heterogeneous BLAS libraries or multi-threaded CPU architectures is not mathematically guaranteed.

---

## 10. What Phase 4F-2 Proved vs. What It Did NOT Prove

### What Phase 4F-2 PROVED:
1. **Context Dilution is a Primary Driver of 1.0B Model False Abstention**:
   Reducing prompt context from 10 items to top-3 items recovered **37.04% of Stage-E false abstentions** (20 cases) with an **83.3% relative increase in total successful outcomes** (from 18 in A0 to 33 in A1: 32 complete answers + 1 partial answer).
2. **Top 3 items was the best-performing configuration among the tested N ∈ {3, 5, 7, 10} settings**:
   A1 ($N=3$) achieved the highest answer rate (27.5%), highest citation completeness (93.94%), lowest prompt tokens (772.9), and lowest latency (14.6s).
3. **Refusal Instructions Protect Against Hallucination Without Sacrificing Extraction Gains**:
   Unlike prompt relaxation (Configs B/C in Phase 4F-1), which caused 68–84% hallucinations on unanswerable queries, Config A with context pruning maintained **0.0% false answers (100% correct abstention)** across all 19 negative queries.
4. **Tested Security Invariants Held**:
   Zero cross-tenant leaks, zero unauthorized document exposure, and zero penetration of adversarial fixtures.

### What Phase 4F-2 Did NOT Prove:
1. **Did NOT prove universal optimality of $N=3$**:
   The optimal context window is intimately tied to model parameter capacity (1.0B parameters) and attention head count. Larger models (e.g. 8B, 70B) may exhibit vastly different dilution profiles.
2. **Did NOT prove that retrieval candidate starvation is resolved**:
   34 Stage-E cases remained abstained in A1, and queries requiring documents at ranks 4–10 suffered from truncation.
3. **Did NOT prove semantic claim-level citation correctness**:
   Only mechanical validity was evaluated.
4. **Did NOT prove that partial-answer synthesis is operational**:
   No true partial answer was generated by the model under conservative refusal rules.
