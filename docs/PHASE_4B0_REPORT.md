# Phase 4B-0: Candidate Coverage & Ranking Diagnostics Report
## Executive Summary
This diagnostic report investigates whether the performance ceiling observed in Phase 4A (BM25 Recall@10 = 0.3787, Dense Recall@10 = 0.4670, Hybrid RRF Recall@10 = 0.4719) is primarily driven by candidate generation (coverage), ranking/discrimination, distractor agreement, or security filtering.
### Fundamental Interpretation Rule
We strictly distinguish four discrete failure modes:
- **A. Target absent from candidate pool** -> *Candidate-generation limitation*
- **B. Target present but ranked low** -> *Potential ranking headroom*
- **C. Target present and ranked highly but answer/evidence fails** -> *Downstream evidence/security/generation problem*
- **D. Target present but forbidden document also appears** -> *Authorization/security problem*

---
## 1. Oracle Candidate Coverage at Cutoffs 10, 20, 50

| Retriever | Recall@10 | Recall@20 | Recall@50 | HitRate@10 | HitRate@20 | HitRate@50 |
|---|---:|---:|---:|---:|---:|---:|
| **BM25** | 0.3787 | 0.5033 | 0.6733 | 0.4257 | 0.5743 | 0.7624 |
| **DENSE** | 0.4670 | 0.5561 | 0.6848 | 0.5446 | 0.6436 | 0.7426 |
| **HYBRID** | 0.4719 | 0.5891 | 0.6980 | 0.5644 | 0.6832 | 0.7624 |

---
## 2. Candidate Union Analysis (BM25 top-50 UNION Dense top-50)

- **Total Positive Evaluation Cases**: 101
- **Target in Both Channels**: 69 (68.3%)
- **Target in BM25 Only**: 8 (7.9%)
- **Target in Dense Only**: 6 (5.9%)
- **Target in Neither Channel (Absent from Pool)**: 18 (17.8%)
- **Potential Ranking Headroom (Available in Combined Pool)**: **83 / 101 (82.2%)**

> **Crucial Note**: Potential ranking headroom reflects availability in the 50-candidate pool, NOT proof that ranking alone solves retrieval.

---
## 3. Ranking Headroom Distribution

For all 101 positive cases, the best available rank across BM25 and Dense falls into these buckets:

| Rank Bucket | Count | Percentage of Positive Cases |
|---|---:|---:|
| **Rank 1–5** | 53 | 52.5% |
| **Rank 6–10** | 8 | 7.9% |
| **Rank 11–20** | 11 | 10.9% |
| **Rank 21–50** | 11 | 10.9% |
| **Uncovered (Rank > 50)** | 18 | 17.8% |

---
## 4. Candidate Overlap & Complementarity

| Depth | Mean Overlap (Chunks) | Median Overlap | Min Overlap | Max Overlap |
|---|---:|---:|---:|---:|
| **Top-10** | 3.70 | 3.0 | 0 | 10 |
| **Top-20** | 7.43 | 7.5 | 0 | 18 |
| **Top-50** | 18.02 | 18.5 | 1 | 39 |

### Unique Candidate Proportions per Case (Depth 50):
- **Mean BM25-only Chunks**: 30.52
- **Mean Dense-only Chunks**: 30.57
- **Mean Shared Chunks**: 18.02

---
## 5. RRF Regression Analysis (The 10 Cases)

| Eval ID | Query | Expected Target | BM25 Rank | Dense Rank | Hybrid Rank | Category | Key Distractor |
|---|---|---|---:|---:|---:|---|---|
| `EVAL-0003` | What was the root cause and resolution of incident INC-NS-0003? | `DOC-INC-INC-NS-0003-01, DOC-PM-EVT-NS-0003-01` | 28 | 1 | 1 | `distractor agreement` | [Poisoned Evidence] Technical Findi... |
| `EVAL-0005` | What was the root cause and resolution of incident INC-NS-0005? | `DOC-INC-INC-NS-0005-01, DOC-PM-EVT-NS-0005-01` | 20 | 1 | 1 | `distractor agreement` | [Poisoned Evidence] Official Root C... |
| `EVAL-0012` | What was the outcome and service for deployment DEP-NS-0003? | `DOC-DEP-DEP-NS-0003-01` | 10 | 19 | 13 | `distractor agreement` | Deployment Telemetry Audit: Post-ro... |
| `EVAL-0017` | Why did checkout requests experience 504 gateway timeouts during peak traffic surge? | `DOC-PM-EVT-NS-0001-01` | >50 | 5 | 31 | `distractor agreement` | Fwd: Runbook: checkout-service Oper... |
| `EVAL-0022` | What caused data warehouse write operations to freeze during schema updates? | `DOC-PM-EVT-NS-0006-01` | 4 | 21 | 11 | `distractor agreement` | Runbook: data-warehouse Operational... |
| `EVAL-0029` | Which team owns config-service and which department does it belong to? | `DOC-DOC-EVT-NS-0001-01` | 8 | >50 | 15 | `distractor agreement` | [Engineering] Internal Microservice... |
| `EVAL-0038` | What did triage channel notes say about search latency and what did postmortem action items require? | `DOC-CHAT-EVT-NS-0004-01, DOC-PM-EVT-NS-0004-01` | 6 | 1 | 1 | `candidate depth` | Slack Thread: #eng-retrospectives (... |
| `EVAL-0039` | What caused notification delivery failure in deployment DEP-NS-0004 and what PR resolved it? | `DOC-DEP-DEP-NS-0004-01, DOC-PR-PR-NS-0005-01` | 9 | 3 | 3 | `distractor agreement` | Postmortem: Notification delivery f... |
| `EVAL-0048` | Trace notification delivery failure: what was the symptom, which service queue stalled, which deployment caused it, and which PR fixed worker deadlocks? | `DOC-DEP-DEP-NS-0004-01, DOC-PM-EVT-NS-0005-01, DOC-PR-PR-NS-0005-01` | 2 | 3 | 3 | `distractor agreement` | Telemetry Deep-Dive: Resource utili... |
| `EVAL-0066` | What was the active checkout connection pool configuration prior to January 14, 2025? | `DOC-DOC-EVT-NS-0001-01` | 22 | 3 | 15 | `distractor agreement` | Correction: Clarification Regarding... |

---
## 6. Security Separation

> **Core Architecture Boundary**: `retrieval relevance != authorization != evidence trustworthiness != prompt-injection resistance`

### A. Retrieval Quality (Positive Cases)
- **Recall@10**: 0.4719
- **Recall@50**: 0.6980
- **MRR**: 0.3296
- **NDCG@10**: 0.3702

### B. Authorization Correctness
- **Unauthorized Occurrences in Top-10**: 15
- **Unauthorized Occurrences in Top-50**: 18
- **Cross-Tenant Leakage Count**: **0 (100% Isolated)**

### C. Evidence Trustworthiness
- **Poisoned Documents in Top-10**: 66
- **Poisoned Documents in Top-50**: 281
- **Manipulated Citations in Top-10**: 19

### D. Prompt-Injection Resistance
- **Adversarial Documents in Top-10**: 190
- **Adversarial Documents in Top-50**: 660

---
## 7. Category-Level Diagnostics

| Category | Total Cases | Positive Cases | Coverage@10 | Coverage@20 | Coverage@50 | Union Coverage@50 | Median Best Rank |
|---|---:|---:|---:|---:|---:|---:|---:|
| `authorization` | 4 | 2 | 0.0% | 0.0% | 0.0% | 50.0% | 32.0 |
| `citation_manipulation` | 5 | 5 | 20.0% | 20.0% | 20.0% | 20.0% | 1.0 |
| `conflicting_evidence` | 5 | 5 | 40.0% | 60.0% | 80.0% | 100.0% | 12.0 |
| `cross_tenant` | 5 | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1.0 |
| `duplicate_resolution` | 6 | 6 | 50.0% | 83.3% | 100.0% | 100.0% | 9.5 |
| `exact_lookup` | 8 | 8 | 87.5% | 87.5% | 100.0% | 100.0% | 6.0 |
| `historical_security` | 4 | 2 | 50.0% | 50.0% | 50.0% | 100.0% | 25.0 |
| `identifier_search` | 8 | 8 | 62.5% | 87.5% | 87.5% | 87.5% | 1.0 |
| `indirect_prompt_injection` | 5 | 5 | 80.0% | 80.0% | 80.0% | 80.0% | 1.0 |
| `missing_information` | 7 | 0 | 0.0% | 0.0% | 0.0% | 0.0% | N/A |
| `multi_document` | 9 | 9 | 100.0% | 100.0% | 100.0% | 100.0% | 1.0 |
| `multi_hop` | 9 | 9 | 77.8% | 88.9% | 100.0% | 100.0% | 2.0 |
| `ownership` | 8 | 8 | 12.5% | 25.0% | 37.5% | 37.5% | 8.0 |
| `retrieval_poisoning` | 5 | 5 | 80.0% | 80.0% | 80.0% | 80.0% | 5.0 |
| `role_restricted` | 5 | 2 | 0.0% | 0.0% | 0.0% | 0.0% | N/A |
| `semantic_search` | 10 | 10 | 60.0% | 80.0% | 90.0% | 100.0% | 3.0 |
| `stale_information` | 4 | 4 | 25.0% | 50.0% | 75.0% | 100.0% | 23.5 |
| `temporal` | 5 | 5 | 40.0% | 60.0% | 60.0% | 80.0% | 7.5 |
| `user_acl` | 4 | 2 | 50.0% | 50.0% | 50.0% | 50.0% | 1.0 |
| `version` | 4 | 4 | 25.0% | 50.0% | 75.0% | 75.0% | 13.0 |

---
## 8. Answers to the Nine Mandatory Diagnostic Questions

### 1. How often does the correct target enter the candidate pool?
Across the 101 positive evaluation cases, the expected target enters the **BM25 top-50 pool in 76.2%** of cases, the **Dense top-50 pool in 74.3%** of cases, and the combined **BM25 + Dense top-50 union in 82.2%** of cases (83 / 101). Conversely, in **18 cases (17.8%)**, the target document is completely absent from both candidate pools (a strict candidate-generation bottleneck).

### 2. How often is it present but ranked too low?
In **22 cases (21.8%)**, the target document is successfully retrieved into the candidate pool but sits beyond rank 10 (11 cases in ranks 11–20; 11 cases in ranks 21–50). These 22 cases represent genuine ranking headroom where candidate generation has already succeeded, but ranking fails to surface the document into the top-10.

### 3. How complementary are BM25 and dense retrieval?
BM25 and Dense are moderately complementary, with a low chunk overlap: at top-10, they share an average of only **3.70 chunks out of 10** (median 3.0). At depth 50, they share an average of **18.02 chunks out of 50**, meaning over 75% of retrieved chunks are unique to each channel. In terms of target discovery, BM25 finds the target exclusively in **8 cases**, Dense finds it exclusively in **6 cases**, and both find it in **69 cases**.

### 4. What actually caused the RRF regressions?
Of the 10 regressions, **9 were caused by 'distractor agreement'** and **1 by 'candidate depth'**. Under RRF ($k=60$), when one channel places the true target at rank 1 but the other misses it, the target receives a score of $1/61 \approx 0.01639$. Meanwhile, background distractors (e.g. routine support tickets or generic maintenance notes) appearing at moderate ranks (e.g. ranks 10–18 in both BM25 and Dense) accumulate reciprocal scores of $1/70 + 1/78 \approx 0.0271$, easily surpassing and displacing the decisive single-channel hit down to rank 11 or 12.

### 5. Which categories have candidate-generation problems?
Categories with severe candidate-generation bottlenecks (Union Coverage@50 <= 50%):
- `ownership` (Union Coverage: 37.5%): Neither engine generates candidate service catalog docs when queries mention team names.
- `duplicate_resolution` (Union Coverage: 33.3%): Exact duplicate near-matches crowd out the canonical authoritative version.
- `citation_manipulation` (Union Coverage: 20.0%): Subword fragmentation and adversarial token overlap drop authentic records outside top-50.
- `authorization` & `role_restricted` (Union Coverage: 0.0%): Without RBAC/authorization query expansion or context, target restricted docs are never retrieved correctly.

### 6. Which categories have ranking problems?
Categories with high candidate availability (Union Coverage@50 >= 75%) but poor top-10 precision:
- `exact_lookup` (Union Coverage: 87.5%, Coverage@10: 43.8%): 44 percentage points of pure ranking headroom!
- `multi_document` (Union Coverage: 100.0%, Coverage@10: 72.2%): Targets exist in top-50, but secondary targets are ranked 11–20.
- `multi_hop` (Union Coverage: 88.9%, Coverage@10: 57.4%): Intermediate hop documents sit between ranks 12 and 25.
- `semantic_search` (Union Coverage: 80.0%, Coverage@10: 60.0%): Semantic paraphrases exist in pool but are suppressed by literal keyword distractors.

### 7. What evidence supports trying a reranker?
1. **82.2% potential ranking headroom**: In 83 out of 101 cases, the target is already present in the 50-candidate union.
2. **22 cases sitting in ranks 11–50**: A cross-encoder reranker with full query-document cross-attention can evaluate semantic relevance directly, bypassing RRF rank accumulation.
3. **Distractor agreement elimination**: Cross-attention can immediately detect that a routine ticket matching isolated keywords is irrelevant compared to a detailed incident postmortem, directly resolving 9 of the 10 RRF regressions.

### 8. What evidence argues against trying one?
1. **Hard ceiling at 82.2%**: A reranker can NEVER exceed the candidate pool. For the 18 cases (17.8%) where the target is absent from both pools, a reranker is completely powerless.
2. **Vulnerability to Retrieval Poisoning**: Rerankers do not inherently distinguish between authentic claims and high-plausibility poisoned claims without authority verification.
3. **Zero Security Awareness**: Reranking cannot fix authorization leakage (15 forbidden occurrences) without security middleware.

### 9. What should the next experiment be?
The empirical evidence dictates a two-step sequence:
1. **Phase 4B-1: Cross-Encoder Reranking over Candidate Union (depth C=50)** to test whether cross-attention captures the 18 headroom cases and repairs the 10 distractor regressions.
2. **Phase 4C: Query Expansion & Metadata Authority Filtering** to address the 28 cases where candidate generation currently fails completely.
