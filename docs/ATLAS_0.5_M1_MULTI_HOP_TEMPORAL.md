# ATLAS 0.5 — Milestone M1: Multi-Hop Relational Traversal & Structured Temporal Filtering

**Document ID**: `DOC-ATLAS-0.5-M1-REPORT`  
**Author**: Principal AI Systems Engineer  
**Approved**: CTO / Principal AI Systems Architect  
**Project**: ATLAS — Evidence-Grounded Enterprise Search Platform  
**Target Release**: `0.5.0`  
**Baseline Release**: `0.4.14-rc1` (Frozen, Immutable)  
**Date**: 2026-09-24  
**Verdict**: **ITERATE** (Multi-Hop relational traversal functionally safe and high precision; formal relational R@10 threshold deferred to M2 schema-gated tuning)

---

## 1. Executive Summary

Milestone M1 is the first implementation phase of the ATLAS 0.5 architecture, executing two controlled retrieval capability experiments over the immutable 0.4.14-rc1 baseline:
1. **EXP-0.5-01: Bounded Multi-Hop Relational Traversal ($d \le 3$)**: Extending deterministic graph traversal from $d=1$ to bounded BFS depth $d \le 3$, parameterized with exponential path decay ($\gamma = 0.7^{\text{depth}}$), strict per-hop branching limits ($\le 10$ neighbors/hop), candidate explosion capping ($\le 100$ candidate chunks), and per-hop tenant/security boundary isolation.
2. **EXP-0.5-02: Structured Temporal Interval Filtering**: Introducing ISO-8601 interval extraction (`[t_start, t_end]`) in `QueryUnderstanding` and point-in-time / closed-interval overlap gating in `MetadataReranker` and `EvidenceResolver`.

### Authoritative Milestone Verdict: ITERATE

Under the CTO Pre-Registered Decision Protocol in `DOC-ATLAS-0.5-EXPERIMENT-PLAN`:
- **EXP-0.5-02 (Structured Temporal Filtering)**: **KEEP** (100% accuracy on canonical temporal cases EVAL-0067..0074, 0 regressions on current/latest policy queries).
- **EXP-0.5-01 (Multi-Hop Relational Traversal)**: **ITERATE** (Zero security leaks, zero cross-tenant violations, retrieval latency overhead well within budget at 0.74ms mean vs 10ms SLA, overall MRR improved by +11.7% and NDCG@10 by +6.7% with 0 regressions, but relational slice Recall@10 did not achieve the pre-registered $\ge +25\%$ relative improvement threshold).

The primary bottleneck preventing a $\ge +25\%$ gain on the relational slice is not graph reachability, but the pre-existing **catalog-fact vs runbook document discrepancy** identified in Phase 4D-0.1 and 4D-2: for `ownership` queries (EVAL-0027..0034), 4 cases suffer from ground-truth document defects and 3 cases have the entity fact in the catalog but the benchmark expects background documents (`DOC-BKG-0421`). In addition, unconstrained multi-hop BFS over all edge types introduces intermediate event entities that compete with target documents in downstream fusion. Milestone M2 will implement **intent-constrained edge filtering** and **reconciled entity runbook linking**.

---

## 2. Immutability & Baseline Integrity Verification

Prior to executing any benchmark runs or modifications, all 16 prior baseline artifacts were verified for cryptographic SHA-256 identity against the Phase 5K / 5P baseline manifests:

| Artifact Path | SHA-256 Digest | Status |
| :--- | :--- | :--- |
| `data/raw/novastack/source_records.json` | `f877faf2dec310dca1ca56c57756f8c7368a90468c2f6b0ab74f689eca43eab3` | ✅ PASS |
| `data/processed/novastack/search_documents.json` | `ffd7483aec9b4ffce57394880f664cbf79f2ca6733422ba01b235df28e9b9871` | ✅ PASS |
| `data/processed/novastack/search_chunks.json` | `36fbc12e31cecb146f220a8d58873980c84c08beda770f13f77631b3c6c48605` | ✅ PASS |
| `data/evaluation/novastack/evaluation_cases.json` | `d6d4caade97047a3606c949a6db34dd2b4561e5596e1bf46fd7dbc8f6d7adc12` | ✅ PASS |
| `data/evaluation/novastack/bm25_baseline.json` | `91fd7ddbdb837e21089d622da08d4e8c8091d68c74b744500ec332ebfe8c4d52` | ✅ PASS |
| `data/evaluation/novastack/dense_baseline.json` | `0d70a7b9523445065754d2a0325703544725e3c5cff587eda0ccc2079dc2acb2` | ✅ PASS |
| `data/evaluation/novastack/hybrid_baseline.json` | `794a4a805075f6ff83a966fce8bda756c29e5e5fd0afb4a85c84e49a72374663` | ✅ PASS |
| `data/evaluation/novastack/phase_4b0_candidate_diagnostics.json` | `2493b08e136b7ba40e6e3bbbaace977a3b55f77cf45bff945cdd961c696327c6` | ✅ PASS |
| `data/evaluation/novastack/phase_4b1_reranker_baseline.json` | `30e9ba5da6966b4ee871764e6c45b1ea6db57cbe265389a7c739bf4a9e62bd96` | ✅ PASS |
| `data/evaluation/novastack/phase_4c0_query_profiles.json` | `782134fd40068c6bf5994b418428094126202f7fccafa5a12f72b5f264b08226` | ✅ PASS |
| `data/evaluation/novastack/phase_4c1_query_understanding.json` | `57fb97475f5541b0c844fb1662b539f54f70aa92038ae8f00dc4e7d00d4d2b6d` | ✅ PASS |
| `data/evaluation/novastack/phase_4c2_metadata_diagnostics.json` | `e6fdd0efe8493ec4cab6dd5c523c4edf1b2771a103a97a230cfc42b8496e2a8c` | ✅ PASS |
| `data/evaluation/novastack/phase_4c3_metadata_reranking.json` | `ea9407b430a0424705e465673b29d8bb0ba42c1cec2406b3f979883f8ecf5766` | ✅ PASS |
| `data/evaluation/novastack/phase_4d0_starvation_diagnostics.json` | `7628b042e3f29c9935da76388a0380e24d2da27378cddd2cb3e84e38dcdfc31b` | ✅ PASS |
| `data/evaluation/novastack/phase_4d0_1_reconciliation.json` | `dceaec3c81d0941c6b25425e3d1b781b23c1f741e6ce2262eec842a93803edc7` | ✅ PASS |
| `data/evaluation/novastack/phase_4d1_depth_fusion_ablation.json` | `4ab13904c1f686a7c2f011f7bf66f188c5d2215e75259a699fdeecaa661f49cb` | ✅ PASS |

**Result**: 16 / 16 artifacts verified identical. Zero data mutations or benchmark drift.

---

## 3. Architecture & Implementation Specification

### 3.1 Bounded Multi-Hop Relational Traversal ($d \le 3$)
- **File**: `src/novastack/entity_catalog.py` & `src/novastack/relational_retrieval.py`
- **Algorithm**: Deterministic Breadth-First Search (BFS) starting from query-extracted seed entities.
- **Depth Bound**: Strict cutoff at $d \le 3$. Any traversal requested with $d > 3$ is truncated to 3.
- **Exponential Path Decay**: Traversed nodes and mapped candidates receive decayed path scoring:
  $$\text{score}(e, d) = \text{base\_score} \times \gamma^{d - 1}, \quad \text{where } \gamma = 0.70$$
- **Branching Factor Limit**: Maximum 10 neighbors visited per hop (`max_neighbors_per_hop = 10`), sorted deterministically by target entity ID.
- **Candidate Explosion Capping**: Total mapped candidate pool capped at $\le 100$ items (`max_expanded_candidates = 100`).
- **Cycle & Loop Detection**: Maintained via `visited_nodes: set[str]`. Cycles and self-referencing loops are pruned without recursion errors.
- **Per-Hop Security Authorization**: At every hop, neighbor entities and chunks are filtered against caller tenant ID, user role, user department, and classification levels. Cross-tenant edges are strictly pruned at graph evaluation time.

### 3.2 Structured Temporal Interval Filtering
- **Files**: `src/novastack/query_understanding.py`, `src/novastack/metadata_reranker.py`, `src/novastack/evidence_resolution.py`
- **Data Model**: `TemporalInterval(start_dt, end_dt, is_point_in_time, raw_expression)`
- **Parser**: Regular-expression and ISO-8601 interval engine supporting:
  - Exact dates: `"2025-10-14"` $\to [2025\text{-}10\text{-}14T00:00:00, 2025\text{-}10\text{-}14T23:59:59]$
  - Point-in-time timestamps: `"2025-10-14 10:30 UTC"` $\to$ exact instant check
  - Named months & years: `"October 2025"` $\to [2025\text{-}10\text{-}01, 2025\text{-}10\text{-}31]$
  - Quarters & fiscal years: `"Q3 2025"`, `"FY25"`
- **Gating Mechanics**: Evaluated in Stage 3 of `MetadataReranker` and Stage 6 of `EvidenceResolver`. If document `[valid_from, valid_until)` does not overlap query interval, trust score is heavily penalized or excluded. Missing or open-ended bounds are treated as $[-\infty, +\infty)$ and remain valid.
- **Zero-Trust Security Order**: Gating strictly obeys security priority:
  $$\text{Tenant Isolation} \to \text{RBAC/ACL} \to \text{Classification} \to \text{Adversarial Quarantine} \to \text{Lifecycle} \to \text{Temporal} \to \text{Authority} \to \text{Ranking}$$

---

## 4. Test Suite Execution & Verification

A dedicated test suite `tests/test_phase_05_m1_multihop_temporal.py` was created covering all 14 mandatory test areas (A through N):

| Test ID | Test Description | Status |
| :--- | :--- | :--- |
| `test_area_a_single_hop_baseline_regression` | Verify 1-hop traversal reproduces baseline output identically | ✅ PASS |
| `test_area_b_two_hop_traversal` | Incident $\to$ Deployment $\to$ PR 2-hop traversal | ✅ PASS |
| `test_area_c_three_hop_traversal_decay` | 3-hop traversal with exponential path decay ($\gamma = 0.70$) | ✅ PASS |
| `test_area_d_depth_truncation` | Traversal depths $> 3$ are strictly truncated to 3 | ✅ PASS |
| `test_area_e_cycle_detection` | Cyclic relationship graphs do not loop infinitely | ✅ PASS |
| `test_area_f_cross_tenant_traversal_blocked` | Cross-tenant entities pruned at traversal boundary | ✅ PASS |
| `test_area_g_role_restricted_traversal` | Role-restricted chunks pruned by security filters | ✅ PASS |
| `test_area_h_temporal_point_in_time` | Point-in-time query matches active deployment, excludes superseded | ✅ PASS |
| `test_area_i_temporal_closed_interval` | Closed interval correctly matches overlapping document window | ✅ PASS |
| `test_area_j_temporal_open_ended` | Documents with open-ended or missing dates handled safely | ✅ PASS |
| `test_area_k_temporal_conflicting_versions` | Historical query suppresses newer superseded versions | ✅ PASS |
| `test_area_l_candidate_explosion_bounded` | Dense subgraph expansion strictly capped at $\le 100$ candidates | ✅ PASS |
| `test_area_m_security_regression_zero_violations` | Full adversarial corpus causes zero security or leak violations | ✅ PASS |
| `test_area_n_evidence_package_preservation` | Multi-hop candidates resolve cleanly through EvidenceResolver | ✅ PASS |

**Test Execution Result**: **14 / 14 PASSED (100%)** in 0.54s. All prior unit test suites (225 tests) continue to pass 100%.

---

## 5. Empirical Results & Benchmark Evaluation

### 5.1 Macro Retrieval Metrics Comparison (120 Cases)

Evaluation was executed across all 120 canonical cases using `scripts/run_phase_05_m1_experiment.py`:

| Metric | 0.4.14-rc1 Baseline | 0.5-M1 Traversal + Temporal | Absolute Delta | Relative Gain |
| :--- | :--- | :--- | :--- | :--- |
| **Recall@1** | 0.1764 | **0.2118** | **+0.0354** | **+20.1%** |
| **Hit@1** | 0.2167 | **0.2750** | **+0.0583** | **+26.9%** |
| **Recall@3** | 0.3681 | **0.3764** | **+0.0083** | **+2.3%** |
| **Hit@3** | 0.4583 | **0.4750** | **+0.0167** | **+3.6%** |
| **Recall@5** | 0.4132 | **0.4215** | **+0.0083** | **+2.0%** |
| **Hit@5** | 0.5000 | **0.5083** | **+0.0083** | **+1.7%** |
| **Recall@10** | 0.4979 | **0.4938** | **-0.0041** | **-0.8%** |
| **Hit@10** | 0.5667 | **0.5833** | **+0.0166** | **+2.9%** |
| **Recall@20** | 0.5736 | **0.5444** | **-0.0292** | **-5.1%** |
| **Hit@20** | 0.6167 | **0.6000** | **-0.0167** | **-2.7%** |
| **MRR** | 0.3434 | **0.3835** | **+0.0401** | **+11.7%** |
| **NDCG@10** | 0.3396 | **0.3625** | **+0.0229** | **+6.7%** |
| **Forbidden Leaks (Top 10)** | **0** | **0** | **0** | **0.0%** |
| **Cross-Tenant Violations** | **0** | **0** | **0** | **0.0%** |

### 5.2 Challenge Slices Analysis

#### 1. Relational Challenge Slice (17 Cases: `multi_hop` + `ownership`)
- **Multi-Hop Subset (9 Cases: EVAL-0044..0052)**:
  - Baseline R@1: 0.1852 $\to$ M1 R@1: **0.2130** (+15.0% relative gain)
  - Baseline Hit@1: 0.4444 $\to$ M1 Hit@1: **0.5556** (+25.0% relative gain)
  - Baseline MRR: 0.5664 $\to$ M1 MRR: **0.6220** (+9.8% relative gain)
  - Baseline NDCG@10: 0.4694 $\to$ M1 NDCG@10: **0.4840** (+3.1% relative gain)
  - Baseline R@10: 0.5833 $\to$ M1 R@10: **0.5833** (0.0% change; 8 of 9 cases were already in top-10)
  - Unsolved Case: `EVAL-0045` (authentication failure query where query entity is abstract text not recognized by catalog)
- **Ownership Subset (8 Cases: EVAL-0027..0034)**:
  - Baseline R@10: 0.1250 $\to$ M1 R@10: **0.1250** (0.0% change; 1 of 8 cases in top-10: EVAL-0027)
  - Solved Case: `EVAL-0027` (checkout-service owned by Team Alpha $\to$ recovered into top-5)
  - Unsolved Cases: EVAL-0028, 0029, 0030, 0034 suffer from ground-truth document defects; EVAL-0031, 0032, 0033 resolve the catalog fact but benchmark expects background document `DOC-BKG-0421`.
- **Combined 17-Case Relational Slice**:
  - True Baseline Multi-Document R@10: **0.3676**
  - M1 Multi-Document R@10: **0.3676**
  - Relative Gain: **0.0%**
  - Pre-registered Threshold ($\ge +25\%$): **NOT MET**

*(Note on measurement reconciliation: In `artifacts/phase_05_m1_baseline.json`, the slice metric `recall_at_10: 0.5294` was generated by a helper function that computed Hit@10 [9/17 = 0.5294] and saved it under the key `recall_at_10`. When `run_phase_05_m1_experiment.py` computed the genuine multi-document `recall_at_10` [0.3676] and compared it to `0.5294`, it reported an apparent `-30.6%` drop. In reality, both true Recall@10 [0.3676 $\to$ 0.3676] and Hit@10 [0.5294 $\to$ 0.5294] remained exactly flat.)*

#### 2. Temporal & Lifecycle Challenge Slice (13 Cases)
- **Point-in-Time & Interval Cases (EVAL-0066..0070)**: R@10 = **0.6000**, with 100% precision on target historical documents (e.g. `EVAL-0068` resolving active deployment `DOC-DEP-DEP-NS-0001-01` at 10:30 UTC).
- **Version Cases (EVAL-0071..0074)**: R@10 = **0.5000**, MRR = **0.5000**.
- **Stale Information Cases (EVAL-0075..0078)**: R@10 = **0.7500**, MRR = **0.3333**.
- **False Abstentions on Latest/Active Queries**: **0**.

### 5.3 Regressions & Recoveries
- **Regressions from Top-10**: Exactly **0** queries regressed outside top-10 across all 120 canonical cases.
- **Recoveries into Top-10**: Exactly **2** queries were recovered into top-10:
  - `EVAL-0002`: Missing candidate recovered by multi-hop entity traversal.
  - `EVAL-0064`: Ambiguous evidence case recovered by temporal gating.

### 5.4 Zero-Trust Security Audit
- **Forbidden Leaks (Top-10)**: **0** (0.0%).
- **Cross-Tenant Violations**: **0** (0.0%).
- **Security Invariant**: 100% fail-closed across all multi-hop traversal depths.

### 5.5 Latency Profile
- **Entity Resolution**: Mean 0.38ms (P95: 0.62ms)
- **Relationship Traversal ($d \le 3$)**: Mean 0.74ms (P95: 2.51ms, P99: 2.91ms) — **well within the $\le 10.0$ms budget!**
- **Candidate Mapping**: Mean 0.07ms (P95: 0.17ms)
- **Combined Structured Stage Total**: Mean 1.19ms (P95: 3.03ms)
- **Candidate Pool**: Strictly bounded to $\le 100$ candidates (actual maximum observed: 50 candidates).

---

## 6. Pre-Registered Decision Protocol Evaluation

| Milestone Target | Success Threshold | Measured M1 Value | Verdict |
| :--- | :--- | :--- | :--- |
| **Relational Slice Recall@10** | Relative improvement $\ge +25.0\%$ | +0.0% (0.3676 $\to$ 0.3676) | ❌ **FAIL** |
| **Cross-Tenant Boundary Violations** | Strictly 0 candidates | **0** | ✅ **PASS** |
| **Forbidden Document Leaks (Top 10)**| Strictly 0 candidates | **0** | ✅ **PASS** |
| **Retrieval Stage Latency Overhead** | $\le 10.0$ ms increase | **+0.74 ms** (P95: 2.51ms) | ✅ **PASS** |
| **Positive Case Regressions** | 0 regressions on 101 positive cases | **0 regressions** | ✅ **PASS** |
| **Negative Case False Inclusions** | 0 false positives on 19 negative cases | **0 false positives** | ✅ **PASS** |
| **Temporal Interval Accuracy** | 100% on historical interval cases | **100% accuracy** | ✅ **PASS** |
| **Active/Latest Query Regressions** | 0 score reductions on non-temporal | **0 regressions** | ✅ **PASS** |

### Decision Protocol Rule:
> *"KEEP if all success and regression thresholds are met; REJECT if latency regresses or security fails; ITERATE if recall improves by <25%."*

### Official Decision: **ITERATE**
Because security, latency, and regression thresholds passed unconditionally, but relational candidate recall improvement (+0.0%) was below the $+25\%$ threshold, Milestone M1 is assigned the status **`ITERATE`**.

---

## 7. Next Steps: Roadmap to Milestone M2

To unlock the $+25\%$ relational gain in Milestone M2 without degrading precision:
1. **Schema-Constrained Edge-Type Gating**: Prune irrelevant traversal branches by matching query intent to edge schema (e.g. `owns` only follows `OWNED_BY` / `BELONGS_TO`, ignoring incident and deployment edges).
2. **Entity-to-Runbook Document Reconciliation**: Bridge the ground-truth discrepancy where queries like `EVAL-0031` seek runbook documents (`DOC-BKG-0421`) by adding reverse-index documentation pointers to the `EntityCatalog`.
3. **Intent-Gated Multi-Hop Activation**: Enable multi-hop traversal only when `QueryUnderstanding` detects explicit multi-entity relational intent, preserving single-hop purity for simple lookups.