# ATLAS — Phase 4D-2 Reconciliation Audit Report

**Document Version**: 1.0  
**Date**: 2026-09-10  
**Phase**: Phase 4D-2 Reconciliation Audit  
**Status**: Complete & Verified  

---

## Executive Summary

This audit resolves potential metric and configuration ambiguities in the Phase 4D-2 Structured Entity & Relational Retrieval Experiment report before architectural sign-off.

### Key Audit Findings

1. **Baseline Metric Difference Explained**: The difference between Phase 4D-1 baseline metrics (Recall@10 = 0.5644, MRR = 0.3911) and Phase 4D-2 baseline metrics (Recall@10 = 0.4750, MRR = 0.3292) is **100% due to the evaluation denominator**: Phase 4D-1 averaged over the **101 positive evaluation cases** ($N = 101$), while Phase 4D-2 averaged over **all 120 evaluation cases** ($N = 120$). Scaling Phase 4D-1 metrics by $\frac{101}{120}$ reproduces the Phase 4D-2 baseline numbers **identically to four decimal places** ($0.564356 \times \frac{101}{120} = 0.4750$).
2. **EVAL-0014 Rank Consistency**: In `data/evaluation/novastack/phase_4d2_relational_retrieval.json` and Section 3 of the report table, BM25 rank was correctly recorded as **78**, exactly matching Phase 4D-0.1. A narrative prose inaccuracy in Sections 5.6 and 7 casually stated ">100 in both channels" because the target missed the top-50 candidate pool. This text is corrected.
3. **EVAL-0107 Verification**: Confirmed and directly reproducible: BM25 >100, Dense >100, Baseline RRF Pool >50, Structured Rank 6, Combined Pool Rank 29, Downstream Metadata Rank 20.
4. **Security Language**: Replaced "zero-trust verified" with **"tested security invariants held"** across all documentation. Invariants held: **0 forbidden leaks into top-10**, **0 cross-tenant leaks** across all 120 cases.
5. **Corpus & Baseline Immutability**: Verified 100% byte-for-byte SHA256 integrity across all 18 prior artifacts (Milestones 4A through Phase 4D-1). Zero mutations.
6. **Starvation Recovery Reality**: **0 of the 23 starvation cases** reached the top-10 downstream. Structured retrieval recovered **2 cases into the candidate pool and top-20** (`EVAL-0014` to downstream rank 15, `EVAL-0107` to downstream rank 20).
7. **Regression Claim Verification**: Zero cases regressed from the top-10 downstream (0 top-10 dropouts). However, 21 cases experienced internal top-10 reordering (13 improvements, 8 minor shifts within ranks 1–9).

---

## 1. Audit 1 — Baseline Metric Consistency

### A. Comparison Across Denominators

| Metric | Phase 4D-1 Baseline (Pos=101) | Phase 4D-2 Baseline (Pos=101) | Phase 4D-2 Combined (Pos=101) | Phase 4D-2 Baseline (All=120) | Phase 4D-2 Combined (All=120) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Recall@1** | 0.1947 | 0.1947 | **0.2096** (+1.49%) | 0.1639 | **0.1764** (+1.25%) |
| **Recall@3** | 0.3482 | 0.3482 | **0.4373** (+8.91%) | 0.2931 | **0.3681** (+7.50%) |
| **Recall@5** | 0.4464 | 0.4464 | **0.4909** (+4.45%) | 0.3757 | **0.4132** (+3.75%) |
| **Recall@10** | **0.5644** | **0.5644** | **0.5916** (+2.72%) | **0.4750** | **0.4979** (+2.29%) |
| **Recall@20** | 0.6122 | 0.6122 | **0.6815** (+6.93%) | 0.5153 | **0.5736** (+5.83%) |
| **MRR** | **0.3911** | **0.3911** | **0.4080** (+0.0169) | **0.3292** | **0.3434** (+0.0142) |
| **NDCG@10** | **0.3753** | **0.3753** | **0.4035** (+0.0282) | **0.3159** | **0.3396** (+0.0237) |

### B. Mathematical Identity

$$\\text{Metric}_{120} = \\text{Metric}_{101} \\times \\frac{101}{120}$$

- **Recall@10**: $0.564356 \times \frac{101}{120} = 0.4749996 \approx \mathbf{0.4750}$
- **MRR**: $0.391144 \times \frac{101}{120} = 0.3292128 \approx \mathbf{0.3292}$
- **NDCG@10**: $0.375289 \times \frac{101}{120} = 0.3158682 \approx \mathbf{0.3159}$

### C. Pipeline Invariant Confirmation

Re-running the Phase 4D-1 baseline through the Phase 4D-2 evaluation pipeline confirms:
- **Zero code path discrepancy**: Both scripts execute identical BM25, Dense, RRF ($k=60$, $C=50$), and `MetadataReranker` pipelines.
- **Zero parameter deviation**: Identical weights ($w=1.0$), identical pre-scoring security filters, identical query expansion logic.
- **Root Cause**: Phase 4D-1 excluded the 19 negative/unanswerable test cases (`expected_document_ids == []`) where recall/MRR are defined as 0.0. Phase 4D-2 included them in macro averages. When evaluated on the same 101 positive cases, the baselines are 100% identical.

---

## 2. Audit 2 — EVAL-0014 Consistency

- **Query**: `"What is service SVC-NS-0005 and which team owns it?"`
- **Category**: `identifier_search`
- **Target Document**: `DOC-DOC-EVT-NS-0001-01`
- **Telemetry Verification**:

| Field | Phase 4D-0.1 Value | Phase 4D-2 Evaluation JSON | Phase 4D-2 Report Table | Status |
| :--- | :---: | :---: | :---: | :---: |
| **BM25 Rank** | 78 | 78 | 78 | ✅ Identical |
| **Dense Rank** | None (>100) | None (>100) | >100 | ✅ Identical |
| **Hybrid Pool (D50)**| None (>50) | None (>50) | >50 | ✅ Identical |
| **Structured Rank** | — | 1 | 1 | ✅ New Channel |
| **Combined Pool Rank**| — | 17 | 17 | ✅ Promoted |
| **Downstream Rank** | — | 15 | 15 | ✅ Promoted |

- **Cause of Discrepancy**: The evaluation telemetry and the Section 3 report table recorded `BM25 = 78` accurately. Narrative prose in Sections 5.6 and 7 loosely stated ">100 in both channels" because the target missed the top-50 candidate pool. This text has been corrected to reflect `BM25 = 78` and `Dense > 100`.

---

## 3. Audit 3 — EVAL-0107 Verification

- **Query**: `"Why were users logged out of NovaStack on 2025-02-18 (INC-NS-0002)?"`
- **Category**: `retrieval_poisoning`
- **Target Document**: `DOC-PM-EVT-NS-0002-01`
- **Telemetry Verification**:
  - BM25 Rank: **None (>100)** (full index rank: 102 in 4D-0.1)
  - Dense Rank: **None (>100)** (full index rank: 189 in 4D-0.1)
  - Baseline RRF Pool Rank (Depth 50): **None (>50)** (full index rank: 154 in 4D-0.1)
  - Structured Rank (in structured pool): **6**
  - Combined Pool Rank: **29**
  - Downstream Metadata Rank: **20**
- **Outcome**: Reproducible and verified.

---

## 4. Audit 4 — Security Language & Invariants

All security measurements across all 120 cases were re-verified:
- **Forbidden Document Leaks into Top-10**: **0** (Standard: exactly 0) — ✅ PASS.
- **Cross-Tenant Violations**: **0** (Standard: exactly 0) — ✅ PASS.
- **Poisoned Documents in Top-10**: **55** across 120 queries (expected, as retrieval poisoning defenses are not part of candidate generation).

**Terminology Update**: Per instruction, replaced all occurrences of `"zero-trust verified"` with **`"tested security invariants held"`**.

---

## 5. Audit 5 — Immutability Verification

All 18 historical baseline artifacts were checked via SHA256 checksums:

| Artifact Path | SHA256 (First 16 chars) | Status |
| :--- | :---: | :---: |
| `data/raw/novastack/source_records.json` | `f877faf2dec310dc` | ✅ Verified Unchanged |
| `data/raw/novastack/adversarial_fixtures.json` | `1e11fb7d4dd81538` | ✅ Verified Unchanged |
| `data/raw/novastack/security_fixtures.json` | `9c519bc725ce9646` | ✅ Verified Unchanged |
| `data/processed/novastack/search_documents.json` | `ffd7483aec9b4ffc` | ✅ Verified Unchanged |
| `data/processed/novastack/search_chunks.json` | `36fbc12e31cecb14` | ✅ Verified Unchanged |
| `data/evaluation/novastack/evaluation_cases.json` | `d6d4caade97047a3` | ✅ Verified Unchanged |
| `data/evaluation/novastack/bm25_baseline.json` | `91fd7ddbdb837e21` | ✅ Verified Unchanged |
| `data/evaluation/novastack/dense_baseline.json` | `0d70a7b952344506` | ✅ Verified Unchanged |
| `data/evaluation/novastack/hybrid_baseline.json` | `794a4a805075f6ff` | ✅ Verified Unchanged |
| `data/evaluation/novastack/phase_4b0_candidate_diagnostics.json` | `2493b08e136b7ba4` | ✅ Verified Unchanged |
| `data/evaluation/novastack/phase_4b1_reranker_baseline.json` | `30e9ba5da6966b4e` | ✅ Verified Unchanged |
| `data/evaluation/novastack/phase_4c0_query_profiles.json` | `782134fd40068c6b` | ✅ Verified Unchanged |
| `data/evaluation/novastack/phase_4c1_query_understanding.json` | `57fb97475f5541b0` | ✅ Verified Unchanged |
| `data/evaluation/novastack/phase_4c2_metadata_diagnostics.json` | `e6fdd0efe8493ec4` | ✅ Verified Unchanged |
| `data/evaluation/novastack/phase_4c3_metadata_reranking.json` | `ea9407b430a04247` | ✅ Verified Unchanged |
| `data/evaluation/novastack/phase_4d0_starvation_diagnostics.json` | `7628b042e3f29c99` | ✅ Verified Unchanged |
| `data/evaluation/novastack/phase_4d0_1_reconciliation.json` | `dceaec3c81d0941c` | ✅ Verified Unchanged |
| `data/evaluation/novastack/phase_4d1_depth_fusion_ablation.json` | `4ab13904c1f686a7` | ✅ Verified Unchanged |

---

## 6. Audit 6 — Structured Retrieval Starvation Claims

Rigorous breakdown of all 23 candidate-starvation cases:

1. **Genuine Starvation Recoveries into Top-10 Downstream**: **0 cases** (0.0%).
   - Structured retrieval did not elevate any starvation target into the final top-10.
2. **Genuine Starvation Promoted into Candidate Pool / Top-20 Downstream**: **2 cases** (8.7%).
   - `EVAL-0014`: Promoted to Pool Rank 17, Downstream Rank 15.
   - `EVAL-0107`: Promoted to Pool Rank 29, Downstream Rank 20.
3. **Evaluation Ground Truth Defects**: **4 cases** (17.4%).
   - `EVAL-0028`, `EVAL-0029`, `EVAL-0030`, `EVAL-0034`: Corrupted or non-existent target documents. Correctly unretrievable.
4. **Relationship Resolved but Supporting Document Absent**: **3 cases** (13.0%).
   - `EVAL-0031`, `EVAL-0032`, `EVAL-0033`: Graph traversal correctly resolved entity relationships (`inventory-service -[owned_by]-> TEAM-NS-0007`), but the target document is an unindexed background SOP that mentions neither the service nor the team.
5. **Entity Not Recognized in Query**: **14 cases** (60.9%).
   - `EVAL-0069`, `0073`, `0076`, `0084`, `0086`, `0093`, `0095`, `0100`, `0105`, `0114`, `0117`, `0118`, `0119`, `0120`. Non-entity policy queries that correctly bypassed structured retrieval.
6. **Authorization Denial**: **0 cases**.

*Clarification on Whole-Corpus Top-10 Wins*: Across all 120 queries, structured retrieval achieved **2 top-10 wins** (non-starvation cases elevated into top-10):
- `EVAL-0001`: Baseline Rank 18 $\to$ Combined Rank 4
- `EVAL-0064`: Baseline Rank 24 $\to$ Combined Rank 10

---

## 7. Audit 7 — Regression Claim Verification

Using the exact case-level comparison method from Phase 4D-1 across all 120 cases:

1. **Top-10 Presence Wins** (entered top-10 downstream): **2 cases** (`EVAL-0001`, `EVAL-0064`).
2. **Top-10 Presence Regressions** (fell out of top-10 downstream): **0 cases**.
3. **Neutral Cases** (top-10 presence unchanged): **118 cases**.
4. **Changed Top-10 Ordering but Unchanged Top-10 Presence**: **21 cases**:
   - 13 cases improved rank within top-10 (e.g. `EVAL-0010`: 7 $\to$ 2; `EVAL-0019`: 6 $\to$ 1; `EVAL-0022`: 6 $\to$ 2; `EVAL-0027`: 9 $\to$ 1).
   - 8 cases shifted down slightly within top-10 (e.g. `EVAL-0002`: 1 $\to$ 3; `EVAL-0005`: 1 $\to$ 2; `EVAL-0006`: 1 $\to$ 2; `EVAL-0007`: 1 $\to$ 3; `EVAL-0008`: 1 $\to$ 3; `EVAL-0077`: 5 $\to$ 6; `EVAL-0082`: 2 $\to$ 3; `EVAL-0083`: 2 $\to$ 3; `EVAL-0106`: 4 $\to$ 9). In none of these cases was the target displaced from the top-10.
5. **Cases where structured candidates entered the candidate pool but did not enter top-10**: **15 cases**.
6. **Case-Level MRR Movement**:
   - 18 cases had MRR improvements.
   - 13 cases had MRR regressions (due to targets moving from rank 1 to rank 2 or 3 within top-10).
   - Net MRR across all 120 cases improved from 0.3292 to 0.3434 (+0.0142).

---

## 8. Architectural Sign-Off Recommendation

### Hypothesis 1 (H1) Status: **Partially Supported (with Strict Boundary Qualifications)**

1. **Supported Elements**:
   - Generates substantial macro precision gains across the entire corpus (Recall@3 +25.6%, NDCG@10 +7.5%, MRR +4.3%).
   - Demonstrates zero top-10 candidate dropouts (0 regressions), avoiding the rank dilution observed in Phase 4D-1's candidate depth expansion.
   - Rescues candidate starvation for entity-linked documents into the candidate pool and top-20 (`EVAL-0014`, `EVAL-0107`).
2. **Qualified Elements**:
   - Cannot rescue candidate starvation into the top-10 without stronger fusion weighting or downstream reranking adjustments.
   - Cannot rescue queries where the target document lacks entity mentions (`EVAL-0031`–`0033`).

### Approval Decision: **APPROVED WITH ARCHITECTURAL QUALIFICATIONS**
Adopt the 3-Channel Structured Relational Retrieval pipeline as part of the ATLAS candidate generation framework, with the clear understanding that candidate starvation originating from unindexed background documentation requires evidence-linking solutions rather than deeper graph traversal.
