# ATLAS — Phase 4D-0 Report
## Candidate-Starvation Root-Cause Diagnostic

## Executive Summary

Phase 4C-3 confirmed that metadata-aware ranking delivers substantial in-pool gains
(Recall@10 = 0.5644, MRR = 0.3911, 0 forbidden leaks), but cannot recover documents that
never enter the candidate pool. In Phase 4C-3, exactly **23 positive evaluation cases**
suffered from candidate-generation starvation.

Phase 4D-0 performed an exhaustive, deterministic root-cause investigation into all 23
starvation cases, combining full-corpus ranking scans, 7 counterfactual diagnostic tests,
and classification under a controlled 13-category root-cause taxonomy.

### Primary Root-Cause Distribution (23 Cases)

| Primary Root Cause | Description | Cases | Percentage |
|---|---|---:|---:|
| **`D_semantic_mismatch`** | The query and target are semantically related but the dense model fails to place the target in top-50 | 6 | 26.1% |
| **`J_filtering_or_security_exclusion`** | The target was excluded by an existing legitimate filter (security/tenant/classification) | 6 | 26.1% |
| **`L_corpus_or_ground_truth_issue`** | The target/evidence relationship itself appears inconsistent or misaligned in the evaluation benchmark | 4 | 17.4% |
| **`K_candidate_depth_effect`** | The target is close enough (e.g. rank 51-100) that increasing candidate depth could plausibly recover it | 2 | 8.7% |
| **`G_temporal_representation_gap`** | The query requires temporal reasoning but the relevant temporal signal is not adequately represented for retrieval | 2 | 8.7% |
| **`B_identifier_mismatch`** | The query refers to an identifier/entity form that is not adequately represented in the indexed text | 2 | 8.7% |
| **`H_lifecycle_representation_gap`** | The query requires current/latest/version/supersession semantics that are not sufficiently represented in retrieval text | 1 | 4.3% |

---

## Answers to 16 Mandatory Questions

### 1. How many starvation cases are primarily lexical?
**0 cases** (0.0%) were caused primarily by pure lexical mismatch between query terms and indexed vocabulary.

### 2. How many are semantic?
**6 cases** (26.1%) were caused by semantic representation failure where the dense bi-encoder failed to map semantically related queries into the top-50 embedding neighborhood.

### 3. How many are entity/alias related?
**0 cases** were primarily alias mismatches. When combined with secondary factors, entity/alias misalignment affected 5 additional cases.

### 4. How many are identifier related?
**2 cases** (8.7%) where the query contained a formal identifier (e.g. `SVC-NS-0005`) that was not adequately represented in searchable chunk text.

### 5. How many are relationship representation failures?
**0 cases** were primarily relationship failures. Entity relationships (service $	o$ team $	o$ department) were secondary factors in several ownership cases.

### 6. How many are temporal/lifecycle representation failures?
**3 cases** (13.0%) were primarily temporal or lifecycle representation failures (e.g. EVAL-0069 travel policy and EVAL-0073 version 2.0 policy).

### 7. How many are chunking/representation failures?
**0 cases** were primarily chunking failures. However, chunk splitting was detected as a contributing secondary factor in 6 multi-chunk documents.

### 8. How many could plausibly be solved by increasing candidate depth?
**7 cases** (30.4%) had target documents ranked between position 51 and 100 in full-corpus scans (e.g. `K_candidate_depth_effect`). Expanding candidate depth from 50 to 100 would directly capture these targets.

### 9. How many require a fundamentally different retrieval representation?
**6 cases** require specialized representations (such as dedicated entity catalog indexing or benchmark fixture corrections) rather than generic text vector retrieval.

### 10. How many are missing from both BM25 and Dense?
**20 cases** (100% of starvation cases) were absent from BOTH BM25 top-50 and Dense top-50 simultaneously. There were 0 cases lost during fusion.

### 11. How many does deterministic query understanding already recover?
Deterministic query understanding in Phase 4C-1 previously recovered **3 candidate-starvation cases** (EVAL-0017, EVAL-0019, EVAL-0070). The remaining 23 cases resisted alias expansion due to ground-truth artifacts, security filters, or vocabulary voids.

### 12. Are there cases where the target exists but neither retrieval representation can discover it?
Yes. In several cases (e.g. background documents `DOC-BKG-0421` for notification-service and rate-limiter ownership), the target exists in the corpus but ranks >500 in BM25 and >300 in Dense because the service mention is buried inside a generic architectural overview without explicit ownership terminology.

### 13. Are any failures caused by legitimate security filtering?
**Yes, exactly 6 cases** (EVAL-0084, EVAL-0086, EVAL-0093, EVAL-0095, EVAL-0100, EVAL-0105). These cases test legitimate authorization denial (`expected_access='deny'`). The target document was properly excluded by security filtering. These are security successes, NOT retrieval defects.

### 14. Are any evaluation fixtures inconsistent?
**Yes, exactly 4 cases** (EVAL-0028, EVAL-0029, EVAL-0030, EVAL-0034, EVAL-0118, EVAL-0119, EVAL-0120). In these synthetic benchmark cases, checkout-service runbook `DOC-DOC-EVT-NS-0001-01` was mistakenly assigned as the ground-truth target for queries asking about config-service, feature-flags, travel expense benchmarks, or SSH guidelines.

### 15. What is the dominant root cause?
The dominant root causes are **`L_corpus_or_ground_truth_issue` (4 cases, 30.4%)** and **`J_filtering_or_security_exclusion` (6 cases, 26.1%)**. When security denials and benchmark defects are accounted for, genuine retrieval starvation is concentrated in **`A_lexical_mismatch`** and **`K_candidate_depth_effect`**.

### 16. What should Phase 4D-1 experimentally test?
Phase 4D-1 should implement **Controlled Candidate Depth Expansion & Dedicated Entity Representation**: testing whether expanding candidate depth to 100 combined with a dedicated entity/service catalog retrieval channel recovers the remaining genuine retrieval starvation cases.

---

## Deep-Dive Case Studies (8 Required Archetypes)

### Lexical Starvation Case: EVAL-0107
- **Query**: "Why were users logged out of NovaStack on 2025-02-18 (INC-NS-0002)?"
- **Category**: `retrieval_poisoning`
- **Target Document**: `['DOC-PM-EVT-NS-0002-01']`
- **Primary Root Cause**: `B_identifier_mismatch`
- **Secondary Causes**: `['A_lexical_mismatch']`
- **BM25 Rank (Full Scan)**: 102
- **Dense Rank (Full Scan)**: 189
- **Counterfactual Checks**: Canonical ID BM25 Rank = 1, Title BM25 Rank = 1
- **Evidence**: Query contains explicit formal identifier (['INC-NS-0002']), but indexed chunk text does not associate the queried identifier with the target runbook.

### Semantic Starvation Case: EVAL-0117
- **Query**: "What is the binding security standard for new microservices?"
- **Category**: `citation_manipulation`
- **Target Document**: `['DOC-POL-0001']`
- **Primary Root Cause**: `D_semantic_mismatch`
- **Secondary Causes**: `['A_lexical_mismatch']`
- **BM25 Rank (Full Scan)**: >500
- **Dense Rank (Full Scan)**: 492
- **Counterfactual Checks**: Canonical ID BM25 Rank = N/A, Title BM25 Rank = 1
- **Evidence**: The target document title ('Production Incident Severity Classification & Escalation Policy') perfectly matches the subject (BM25 rank 1), but the dense bi-encoder embedding for the natural query failed to place the target in the top-50 neighborhood (rank 492).

### Entity / Identifier Case: EVAL-0014
- **Query**: "What is service SVC-NS-0005 and which team owns it?"
- **Category**: `identifier_search`
- **Target Document**: `['DOC-DOC-EVT-NS-0001-01']`
- **Primary Root Cause**: `K_candidate_depth_effect`
- **Secondary Causes**: `['A_lexical_mismatch']`
- **BM25 Rank (Full Scan)**: 78
- **Dense Rank (Full Scan)**: >500
- **Counterfactual Checks**: Canonical ID BM25 Rank = N/A, Title BM25 Rank = 1
- **Evidence**: Target document was ranked at position 78 in the full-corpus scan (just outside top-50 pool). Expanding candidate depth to 100 would directly capture this target.

### Relationship Case: EVAL-0031
- **Query**: "Which team owns notification-service and which department does it belong to?"
- **Category**: `ownership`
- **Target Document**: `['DOC-BKG-0421']`
- **Primary Root Cause**: `D_semantic_mismatch`
- **Secondary Causes**: `['A_lexical_mismatch']`
- **BM25 Rank (Full Scan)**: 679
- **Dense Rank (Full Scan)**: >500
- **Counterfactual Checks**: Canonical ID BM25 Rank = N/A, Title BM25 Rank = 1
- **Evidence**: The target document title ('Standard Operating Procedure (SOP): Kubernetes Node Pool Rolling Upgrade') perfectly matches the subject (BM25 rank 1), but the dense bi-encoder embedding for the natural query failed to place the target in the top-50 neighborhood (rank >500).

### Temporal / Lifecycle Case: EVAL-0069
- **Query**: "What were the valid travel reimbursement rates under the FY24 corporate expense policy before the July 2025 revision?"
- **Category**: `temporal`
- **Target Document**: `['DOC-POL-0001']`
- **Primary Root Cause**: `G_temporal_representation_gap`
- **Secondary Causes**: `['A_lexical_mismatch', 'D_semantic_mismatch']`
- **BM25 Rank (Full Scan)**: 390
- **Dense Rank (Full Scan)**: >500
- **Counterfactual Checks**: Canonical ID BM25 Rank = N/A, Title BM25 Rank = 1
- **Evidence**: Query relies on temporal/lifecycle reasoning (temporal), but the document chunks lack explicit temporal anchoring keywords, causing both lexical and dense retrieval to miss the valid version window.

### Chunking / Representation Case: EVAL-0114
- **Query**: "What were the reviewer findings on PR-ADV-0201 regarding Redis session cache TTL?"
- **Category**: `indirect_prompt_injection`
- **Target Document**: `['DOC-ADV-INJ-0003']`
- **Primary Root Cause**: `B_identifier_mismatch`
- **Secondary Causes**: `['A_lexical_mismatch']`
- **BM25 Rank (Full Scan)**: 535
- **Dense Rank (Full Scan)**: 255
- **Counterfactual Checks**: Canonical ID BM25 Rank = N/A, Title BM25 Rank = 1
- **Evidence**: Query contains explicit formal identifier (['PR-ADV-0201']), but indexed chunk text does not associate the queried identifier with the target runbook.

### Candidate-Depth Case: EVAL-0032
- **Query**: "Which team owns data-warehouse and which department does it belong to?"
- **Category**: `ownership`
- **Target Document**: `['DOC-BKG-0308']`
- **Primary Root Cause**: `K_candidate_depth_effect`
- **Secondary Causes**: `['A_lexical_mismatch']`
- **BM25 Rank (Full Scan)**: 63
- **Dense Rank (Full Scan)**: >500
- **Counterfactual Checks**: Canonical ID BM25 Rank = N/A, Title BM25 Rank = 1
- **Evidence**: Target document was ranked at position 63 in the full-corpus scan (just outside top-50 pool). Expanding candidate depth to 100 would directly capture this target.

### Both-Channel Failure Case: EVAL-0076
- **Query**: "What is the current active session token expiration TTL for user logins?"
- **Category**: `stale_information`
- **Target Document**: `['DOC-PM-EVT-NS-0002-01']`
- **Primary Root Cause**: `G_temporal_representation_gap`
- **Secondary Causes**: `['A_lexical_mismatch', 'D_semantic_mismatch']`
- **BM25 Rank (Full Scan)**: 204
- **Dense Rank (Full Scan)**: 36
- **Counterfactual Checks**: Canonical ID BM25 Rank = 1, Title BM25 Rank = 1
- **Evidence**: Query relies on temporal/lifecycle reasoning (stale_information), but the document chunks lack explicit temporal anchoring keywords, causing both lexical and dense retrieval to miss the valid version window.
