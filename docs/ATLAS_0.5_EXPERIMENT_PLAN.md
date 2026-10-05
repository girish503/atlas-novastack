# ATLAS 0.5 — Controlled Experiment Plan & Evaluation Strategy

**Document ID**: `DOC-ATLAS-0.5-EXPERIMENT-PLAN`  
**Author**: CTO / Principal AI Systems Architect  
**Project**: ATLAS — Evidence-Grounded Enterprise Search Platform  
**Target Release**: `0.5.0`  
**Baseline**: `0.4.14-rc1` (Frozen, Immutable)  
**Date**: 2026-09-23  

---

## 1. Evaluation Philosophy

In accordance with our core engineering principle — **"Every major optimization requires an experiment"** — no architectural change will be accepted into the ATLAS 0.5 production default without satisfying a formal, pre-registered hypothesis test.

We reject:
1. **Benchmark Gaming**: Modifying test questions or ground-truth expectations to artificially inflate scores.
2. **Safeguard Dilution**: Weakening negative control cases or Layer 1S security gates to improve answer yield.
3. **Silent Drift**: Changing internal heuristics without measuring regression impact across all existing suites.

### Preservation of Canonical Baseline
The **120-case canonical evaluation dataset** (`data/evaluation/novastack/evaluation_cases.json`: 101 positive, 19 negative) is **immutable**. All 0.5 experiments must run the 120-case suite to certify **zero regression** before any capability can be considered for production promotion.

---

## 2. Controlled Experiment Specifications

---

### Experiment EXP-0.5-01: Multi-Hop Relational Traversal ($d \le 3$)

```
HYPOTHESIS:
Extending StructuredRetriever traversal from depth d=1 to bounded BFS depth d<=3
with exponential path-decay scoring (gamma = 0.7^d) will recover candidate starvation
for multi-entity queries (improving R@10 by >= 25% on relational cases) without
increasing candidate noise or violating cross-tenant boundaries.

BASELINE:
ATLAS 0.4.14-rc1 StructuredRetriever (strictly d=1, top-50 candidates).
Relational candidate recall R@10 on multi-entity evaluation cases: 41.2%.
Non-relational query latency: 14.4s mean.

CHANGE:
Implement bounded BFS graph traversal in StructuredRetriever supporting d=2 and d=3.
Enforce tenant_id filtering at every traversal step.
Score candidates: score = base_weight * (0.7 ^ depth).
Fuse candidates using existing RRF k=60.

DATASET:
Canonical 120-case evaluation suite PLUS 20 new multi-hop challenge queries
(tracing Incident -> Deployment -> PR -> Author and Service -> Team -> Dept).

METRICS:
1. Multi-hop Candidate Recall (R@10).
2. Overall Candidate Coverage on 101 positive cases.
3. Total Retrieval Stage Latency (ms).
4. Security Boundary Violations (cross-tenant candidate count).

SUCCESS THRESHOLD:
- Multi-hop R@10 >= 65.0% (relative improvement >= +25%).
- Zero cross-tenant candidates retrieved.
- Retrieval stage latency increase <= 10.0 ms.

REGRESSION THRESHOLD:
- 0 regressions on the canonical 101 positive cases.
- 0 false positive inclusions on the 19 negative cases.

SECURITY CONDITIONS:
Every traversed entity and candidate chunk must possess tenant_id matching
CallerContext.tenant_id. Graph edges spanning tenants must be pruned immediately.

STOP CONDITION:
If multi-hop traversal causes candidate retrieval latency to exceed 50.0 ms
or introduces any cross-tenant candidate leak, abort immediately.

DECISION PROTOCOL:
KEEP if all success and regression thresholds are met; REJECT if latency regresses
or security fails; ITERATE if recall improves by <25%.
```

---

### Experiment EXP-0.5-02: Structured Temporal Interval Filtering

```
HYPOTHESIS:
Parsing natural-language temporal expressions into explicit ISO-8601 date intervals
[t_start, t_end] in QueryUnderstanding and applying hard interval gating in
MetadataReranker will resolve version/policy confusion (achieving 100% accuracy on
temporal cases EVAL-0067..0074) without penalizing current/active policy queries.

BASELINE:
ATLAS 0.4.14-rc1 regex token matching (TEMPORAL_MARKERS) with additive recency boost.
Point-in-time accuracy on historical policy cases: 0.0% (EVAL-0067, 0069, 0071 fail
due to recency bias outranking valid historical documents).

CHANGE:
Introduce TemporalIntervalFilter extracting [t_start, t_end].
Match against document metadata valid_from and valid_until.
If a query explicitly targets a historical window, suppress documents outside the window.
If query seeks 'latest' or 'current', preserve existing recency boost.

DATASET:
Canonical 120-case evaluation suite PLUS 15 new temporal interval test cases.

METRICS:
1. Temporal Case Answer Accuracy (% of temporal cases answered with valid citations).
2. Positive Answer Yield on non-temporal cases.
3. Reranker Stage Latency (ms).

SUCCESS THRESHOLD:
- 100% answer accuracy on canonical temporal cases (EVAL-0067..0074).
- 0 regressions on current/latest policy queries (e.g. EVAL-0078).

REGRESSION THRESHOLD:
- Zero score reduction on non-temporal queries.
- Zero increase in false abstentions on active document queries.

SECURITY CONDITIONS:
Historical and superseded documents must strictly preserve document-level ACLs.
Expired documents do not become public.

STOP CONDITION:
If temporal filtering incorrectly filters out valid non-temporal documents, abort.

DECISION PROTOCOL:
KEEP if historical cases achieve >= 90% and non-temporal has 0 regressions;
REJECT if active queries are penalized; ITERATE if parser fails on complex dates.
```

---

### Experiment EXP-0.5-03: Salience-Budgeted Evidence Compaction

```
HYPOTHESIS:
Extracting top 2-3 salient sentences per evidence chunk rather than serializing
full raw 400-word passages will allow expanding context from Top-3 to Top 6-8 distinct
documents within the same 800-token prompt budget, increasing positive answer yield
from 62.4% to >= 75.0% while keeping mechanical citation precision at 100%.

BASELINE:
ATLAS 0.4.14-rc1 Top-3 raw evidence serialization (max_evidence_items=3).
Positive answer yield: 62.4% (63/101).
Mechanical citation precision: 100.0%.
Mean prompt token count: ~680 tokens.
Mean CPU generation latency: ~14.4s.

CHANGE:
Enable AdaptiveContextBudgeter salience compaction in GroundedAnswerGenerator.
For each chunk in EvidencePackage (ranks 1 to 8), extract top 2 sentences by query overlap.
Construct compact prompt with 6 to 8 evidence items, capping total tokens at 800.
Evaluate with Gemma 3 1B IT Q4_K_M via InferenceServiceAdapter.

DATASET:
Complete 120-case canonical evaluation suite.

METRICS:
1. Positive Answer Yield (% of 101 positive cases answered or partially answered).
2. Mechanical Citation Precision (% of citations exactly matching ground truth).
3. Citation Completeness (%).
4. Prompt Token Count (tokens).
5. Generation Latency (ms).

SUCCESS THRESHOLD:
- Positive Answer Yield >= 75.0% (>= 76 / 101 cases).
- Mechanical Citation Precision = 100.0% (Zero tolerance for hallucinated citations).
- Generation Latency <= 15.0s on host CPU.

REGRESSION THRESHOLD:
- Citation Precision < 100.0%.
- Negative case abstention < 100.0% (Must remain 19/19 abstained).

SECURITY CONDITIONS:
Sentence-level evidence items must retain original document and chunk IDs.
Citation validation must verify exact substring alignment in the source chunk.

STOP CONDITION:
If mechanical citation precision drops below 100.0% or CPU latency exceeds 20.0s,
abort immediately.

DECISION PROTOCOL:
KEEP if yield >= 75% and precision = 100%; REJECT if precision drops or latency >20s;
ITERATE on sentence-selection heuristic if yield is between 68% and 75%.
```

---

### Experiment EXP-0.5-04: Append-Only Delta Index Buffer & Freshness

```
HYPOTHESIS:
Maintaining an in-memory append-only DeltaIndexBuffer for newly ingested documents
allows sub-second search availability (<500ms from ingest to query) with zero
read degradation on the base index generation.

BASELINE:
ATLAS 0.4.14-rc1 batch-only IndexManager. Freshness latency: Infinite (requires
full offline batch rebuild of 1,393 documents).

CHANGE:
Introduce DeltaIndexBuffer storing newly ingested SourceRecords in memory.
BM25 inverted list updated incrementally; passage vectors encoded asynchronously.
Retrieval pipeline queries Base Index + Delta Buffer and fuses via RRF.

DATASET:
Baseline corpus (1,393 docs) PLUS a live stream of 50 new incident triage records.

METRICS:
1. Ingest-to-Search Latency (ms).
2. Base Query Latency Degradation (%).
3. Retrieval Accuracy on New Documents (R@5).
4. Memory Consumption of Delta Buffer (MB).

SUCCESS THRESHOLD:
- Ingest-to-Search latency <= 500 ms.
- Base query latency increase <= 5.0%.
- New document R@5 >= 90.0%.
- Delta buffer memory footprint <= 50 MB for 500 records.

REGRESSION THRESHOLD:
- Any corruption or mutation of active base IndexGeneration.
- Any memory leak or uncontrolled buffer growth.

SECURITY CONDITIONS:
Delta documents must validate tenant_id and RecordPermissions before acceptance.
Unauthenticated or malformed delta payloads must be rejected immediately.

STOP CONDITION:
If delta querying slows base search latency by >10% or causes thread contention,
abort immediately.

DECISION PROTOCOL:
KEEP if ingest latency <= 500ms and base degradation <= 5%; REJECT if base query
degrades >10%; ITERATE if vector encoding causes CPU bottleneck.
```

---

### Experiment EXP-0.5-05: Autonomous Circuit-Breaker Fallback Routing

```
HYPOTHESIS:
Transparently rerouting inference requests to certified Rollback Backend A
(LocalHuggingFaceProvider) when the primary Backend B circuit breaker trips to OPEN
will eliminate 503 service downtime during primary container outages while preserving
100% security and grounded answering semantics.

BASELINE:
ATLAS 0.4.14-rc1 CircuitBreaker trips to OPEN on 3 failures; rejects all queries
with HTTP 503 for 10.0s cooldown. Availability during outage: 0.0%.

CHANGE:
Modify InferenceServiceAdapter to catch CircuitBreakerOpen exception and delegate
generate_answer() to LocalHuggingFaceProvider.
When probe succeeds, seamlessly revert to primary Backend B.

DATASET:
Phase 5O Incident Category 02 drill (stopping Docker container atlas-inference-5d)
under active query stream.

METRICS:
1. Query Availability (% of queries receiving HTTP 200 during container outage).
2. Answer Status and Citation Precision under fallback.
3. Fallback Transition Latency (ms).
4. Re-convergence Time upon container recovery (s).

SUCCESS THRESHOLD:
- Query Availability during outage = 100.0% (Zero HTTP 503 errors).
- Citation Precision under fallback = 100.0%.
- Zero security or tenant isolation leaks during fallback.

REGRESSION THRESHOLD:
- Any unhandled exception or crash during provider delegation.
- Failure to transition back to Backend B once container is restored.

SECURITY CONDITIONS:
Fallback backend must execute within identical CallerContext and Layer 1S gates.

STOP CONDITION:
If memory footprint exceeds 6.0 GB during dual-provider instantiation, abort.

DECISION PROTOCOL:
KEEP if availability = 100% and precision = 100%; REJECT if memory exceeds limits;
ITERATE on delegation hand-off if transition latency >2.0s.
```

---

## 3. Evaluation Dataset Expansion Specification

To evaluate ATLAS 0.5 without compromising the canonical 0.4.14 baseline, we specify the **ATLAS 0.5 Challenge Evaluation Suite** (`data/evaluation/novastack/evaluation_cases_v05.json`):

| Category | Cases Count | Purpose / Challenge | Expected Behavior |
|:---|:---:|:---|:---|
| **Multi-Hop Relational** | 20 | Traverses 2–3 graph hops (e.g. Incident $\to$ Deployment $\to$ PR $\to$ Author) | Answer with citations spanning multiple relational entities |
| **Temporal Interval** | 15 | Explicit ISO date windows ("between 2024-01-01 and 2024-06-30") | Strict point-in-time policy retrieval; suppress newer versions |
| **Live Delta Freshness** | 10 | Queries targeting documents ingested into DeltaIndexBuffer $<1$ min ago | Successful cited answer from real-time buffered evidence |
| **Cross-Document Synthesis**| 10 | Answers requiring synthesis across 5–7 distinct documents | Compact evidence packaging; synthesize across $\ge 4$ sources |
| **Negative Security Controls** | 10 | Complex cross-tenant probes and role-restricted multi-hop paths | 100% deterministic abstention; 0 tokens or evidence leaked |
| **Total New Challenge Cases** | **65** | **Augments canonical 120-case suite (Total 0.5 Benchmark: 185 Cases)** | **Evaluated in isolated 0.5 test harness** |
