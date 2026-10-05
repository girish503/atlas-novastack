# ATLAS 0.5 — Milestone M5: Minimum Sufficient Evidence Selection & Bounded Multi-Hop Answer Planning

## Executive Summary

| Attribute | Value |
|---|---|
| **Milestone** | ATLAS 0.5 — M5 |
| **Official Verdict** | **ITERATE** |
| **Date** | 2026-09-26 |
| **Author** | Principal AI Systems Engineer |
| **Production Baseline Release** | `0.4.14-rc1` (Frozen & Immutable) |
| **Immutability Verification** | 16/16 baseline artifacts verified intact (SHA-256 matched) |
| **Positive Answer Yield** | **66/101 (65.35%)** (Target $\ge 66.34\%$) |
| **Negative Case Abstention** | **19/19 (100.0%)** (Target 100.0%) |
| **Mechanical Citation Precision** | **100.0%** (Target 100.0%) |
| **Citation Completeness** | **89.39%** (Target $\ge 90.0\%$) |
| **Security Violations** | **0** (Target 0) |
| **Mean Positive Latency** | **13619.82 ms** |
| **Multi-Hop Focus Slice Yield** | **12/18 (66.67%)** (Timeouts: 1) |

---

## 1. Problem Statement & Milestone Objectives

In Milestone M4, Hierarchical Evidence Budgeting restored positive yield to 60.40% (61/101), but fell short of the M2 recovery target ($\ge 66.34\%$). The root cause was identified as the **multi-document evidence vs. CPU inference context cost bottleneck**:
- When complex queries required 3+ documents (such as postmortem, deployment, and PR causal chains), increasing context depth caused CPU token-processing latency to exceed timeout boundaries.
- Indiscriminate top-k retrieval often selected redundant documents from the same role while missing crucial cross-domain links.

Milestone M5 investigated **Minimum Sufficient Evidence Selection**:
1. Formulating a deterministic, bounded structural evidence plan specifying required enterprise roles and relationship chains.
2. Formulating a bounded set cover selection algorithm that identifies the minimal sufficient set of authorized documents covering all required roles.
3. Combining minimum sufficient selection with M4 hierarchical soft compaction (~100 tokens/document) under a 350-420 token ceiling.

---

## 2. Architecture & Implementation

### 2.1 Formal Enterprise Evidence Roles
ATLAS M5 introduces 12 formal deterministic evidence roles:
- `INCIDENT_RECORD`
- `POSTMORTEM_RECORD`
- `DEPLOYMENT_RECORD`
- `PULL_REQUEST_RECORD`
- `SERVICE_SPECIFICATION`
- `TEAM_OWNERSHIP`
- `OPERATIONAL_RUNBOOK`
- `POLICY_DOCUMENT`
- `CUSTOMER_TICKET`
- `CHAT_RECORD`
- `PROTECTIVE_BOUNDARY`
- `SUPPORTING_CONTEXT`

### 2.2 Bounded Set Cover Selection
Given an evidence package and an `EvidencePlan`, the `MinimumSufficientEvidenceSelector` algorithm:
1. Rejects unauthorized or cross-tenant candidates.
2. Gating on protective boundary for ungrounded or secret-seeking queries (Tier 0).
3. Classifies candidates into roles and ranks candidates by entity overlap, authority, and retrieval score.
4. Solves greedy set cover across required roles.
5. Fills remaining budget with highest-scoring unique documents.
6. Evaluates structural completeness: $C = (\text{missing roles} == \emptyset)$.

---

## 3. SLA Gate Evaluation

| Gate | Description | Target | Measured | Result |
|---|---|---|---|---|
| **G1** | Positive Answer Yield | $\ge 66.34\%$ | **65.35%** (66/101) | **FAIL** |
| **G2** | Negative Abstention Safety | $100.0\%$ (19/19) | **100.0%** (19/19) | **PASS** |
| **G3** | Citation Precision | $100.0\%$ | **100.0%** | **PASS** |
| **G4** | Citation Completeness | $\ge 90.0\%$ | **89.39%** | **FAIL** |
| **G5** | Security Invariance | 0 violations | **0** | **PASS** |
| **G6** | Mean Positive Latency | $\le 15,000$ ms | **13619.82 ms** | **PASS** |

---

## 4. Multi-Hop Focus Slice Analysis (18 Cases)

The 18 multi-hop cases (`EVAL-0035` through `EVAL-0052`) evaluated causal chain queries spanning incidents, deployments, and PRs:
- **Answered**: 12/18 (66.67%)
- **Timeouts**: 1
- **Mean Latency**: 16436.87 ms

---

## 5. Comparative Trajectory Across Milestones

| Milestone | Strategy | Positive Yield | Negative Abstention | Citation Precision | Completeness | Mean Latency | Verdict |
|---|---|---|---|---|---|---|---|
| **Phase 4E / Baseline** | Direct Top-K Retrieval | 62.38% (63/101) | 100.0% (19/19) | 100.0% | 93.65% | 4,967 ms | BASELINE |
| **M1** | Multi-Hop Relational Traversal | 62.38% (63/101) | 100.0% (19/19) | 100.0% | 93.65% | 5,012 ms | ITERATE |
| **M2** | Salience Compaction + Delta Index | 66.34% (67/101) | 89.47% (17/19) | 100.0% | 94.03% | 5,210 ms | ITERATE |
| **M3** | Entity Grounding Gate | 57.43% (58/101) | 100.0% (19/19) | 100.0% | 89.66% | 13,910 ms | ITERATE |
| **M4** | Hierarchical Evidence Budgeting | 60.40% (61/101) | 100.0% (19/19) | 100.0% | 88.52% | 15,541 ms | ITERATE |
| **M5** | **Minimum Sufficient Evidence Selection** | **65.35%** (66/101) | **100.0%** (19/19) | **100.0%** | **89.39%** | **13619.82 ms** | **ITERATE** |

---

## 6. Official Verdict & Decision

**OFFICIAL VERDICT: ITERATE**

Milestone M5 demonstrated robust safety invariance (100.0% negative abstention, 100.0% citation precision, 0 security leaks) and validated the deterministic role selection model, but positive answer yield reached 65.35% (target >= 66.34%). Minimum Sufficient Evidence Selection is retained as an EXPERIMENTAL candidate for further refinement.

Production baseline `0.4.14-rc1` remains frozen and immutable.
