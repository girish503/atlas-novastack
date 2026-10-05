# ATLAS 0.5 — Concrete Product Definition & Implementation Roadmap

**Document ID**: `DOC-ATLAS-0.5-ROADMAP`  
**Author**: CTO / Principal AI Systems Architect  
**Project**: ATLAS — Evidence-Grounded Enterprise Search Platform  
**Target Release**: `0.5.0`  
**Baseline**: `0.4.14-rc1` (Frozen, Immutable)  
**Date**: 2026-09-23  

---

## 1. ATLAS 0.5 Product Definition

### 1.1 Objective
ATLAS 0.5 elevates the platform from an **accurate, single-turn, headless search engine** into a **deeply capable, live-updated, visual enterprise intelligence platform**.

Without abandoning our certified low-TCO, CPU-first, single-node operating envelope, ATLAS 0.5 solves the primary causes of remaining false abstentions:
1. Multi-hop relational blind spots.
2. Temporal policy and historical version confusion.
3. Prompt evidence starvation caused by raw passage truncation.
4. Total batch ingestion latency for real-time incidents.
5. Headless operator friction during high-stress operational triage.

### 1.2 User Problems Being Solved
- **Incident Responders**: Can now trace root cause across multi-hop operational graphs (e.g. PR $\to$ Deployment $\to$ Incident $\to$ Customer) in a single query.
- **Compliance & HR Officers**: Can query exact point-in-time historical policies (e.g. "valid in FY24 before the FY25 update") without being misled by recency biases.
- **Engineers**: Can search live triage updates and Slack discussions ingested seconds ago into the delta buffer.
- **Operators**: Gain a visual single-page Web UI with clickable, highlighted sentence-level citations and visual provenance graphs.
- **SREs**: Enjoy 100% search uptime via autonomous circuit-tripped fallback to the certified rollback backend during primary backend maintenance.

### 1.3 Architecture Changes & New Components

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              ATLAS 0.5 NEW & EVOLVED COMPONENTS                        │
│                                                                                        │
│  [NEW] Operator Web Search UI          ──► Standalone responsive SPA at /ui            │
│  [NEW] TemporalIntervalFilter         ──► ISO-8601 interval parser & metadata gate   │
│  [NEW] DeltaIndexBuffer               ──► In-memory append-only streaming index       │
│  [NEW] AutonomousFallbackRouter       ──► Zero-downtime CircuitBreaker HA failover    │
│  [EVOLVED] MultiHopStructuredRetriever──► Bounded BFS graph traversal (d <= 3)        │
│  [EVOLVED] SalienceContextCompactor   ──► Sentence-level compaction (Top 3 -> Top 6-8)│
│                                                                                        │
│  [UNCHANGED] JwtIdentityVerifier      ──► Fail-closed HS256 authentication            │
│  [UNCHANGED] BM25 & Dense Index Base  ──► Pre-scoring tenant partitioning              │
│  [UNCHANGED] Layer 1S Security Gate   ──► Deterministic security abstention            │
│  [UNCHANGED] Tiered C2 CitationResolver─► 100% mechanical precision verification      │
│  [UNCHANGED] Containerized Ollama Q4  ──► Single-node low-cost CPU inference           │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.4 Components That Remain Strictly Unchanged
- **Core Security Boundary**: Tenant isolation pre-scoring filters and fail-closed authentication mechanisms remain identical.
- **Underlying Models**: `BAAI/bge-small-en-v1.5` (dense) and `gemma3:1b` Q4_K_M (generation) remain the validated production defaults.
- **Rollback Control**: `LocalHuggingFaceProvider` (`google/gemma-3-1b-it` FP32) remains the certified rollback control.
- **Citation Precision Standard**: 100% mechanical citation precision requirement remains non-negotiable.

### 1.5 Security, Evaluation & Observability Evolution
- **Security**: In-memory delta buffer and multi-hop traversal strictly inherit tenant isolation and document-level ACLs at every hop. Zero cross-tenant entity links.
- **Evaluation**: The 120-case canonical benchmark is preserved as an immutable regression baseline. 50 new challenge cases (multi-hop, temporal, delta freshness) are added in a separate evaluation suite.
- **Observability**: Prometheus metrics extended with `atlas_delta_buffer_documents`, `atlas_multihop_traversals_total`, and `atlas_autonomous_fallback_total`.

### 1.6 Non-Goals for 0.5
- **NO** external distributed vector databases (Milvus, Qdrant, Pinecone).
- **NO** multi-turn open-ended agentic chat loops.
- **NO** 70B parameter GPU model clusters.
- **NO** Kubernetes or cloud-specific orchestration lock-in.

---

## 2. Phased Implementation Roadmap

Development will proceed through **5 incremental, test-driven milestones**. Every milestone follows the strict engineering cycle:  
$$\text{BUILD} \longrightarrow \text{TEST} \longrightarrow \text{MEASURE} \longrightarrow \text{ANALYZE} \longrightarrow \text{DECIDE} \longrightarrow \text{PROMOTE/REJECT}$$

---

### Milestone 0.5-M1: Multi-Hop Relational Traversal & Structured Temporal Filtering
- **Objective**: Eliminate Stage A & Stage B candidate starvation for multi-hop entity queries and point-in-time policy questions.
- **Scope**:
  1. Extend `StructuredRetriever` to support bounded BFS traversal up to depth $d=3$ with path scoring ($\gamma = 0.7^d$).
  2. Implement `TemporalIntervalFilter` in `QueryUnderstanding` to parse ISO-8601 date ranges and apply hard filtering against `valid_from` / `valid_until`.
- **Components Affected**: `src/novastack/relational_retrieval.py`, `src/novastack/query_understanding.py`, `src/novastack/metadata_reranker.py`.
- **Target Tests**: Unit test suite `tests/test_phase_05_m1_multihop_temporal.py`.
- **Evaluation**: Canonical 120 cases + 20 multi-hop/temporal challenge cases.
- **Acceptance Criteria**:
  - Multi-hop candidate coverage (R@10) increases $\ge 25\%$ on multi-entity queries.
  - 100% accuracy on canonical temporal test cases (`EVAL-0067..0074`).
  - 0 regressions on the 120-case baseline.
  - Zero latency impact on non-relational queries ($<1$ ms added).

---

### Milestone 0.5-M2: Salience Context Compaction & Append-Only Delta Index
- **Objective**: Double effective document diversity in the generation prompt and provide sub-second freshness for live records.
- **Scope**:
  1. Integrate `AdaptiveContextBudgeter` extractive sentence salience compaction into `GroundedAnswerGenerator` to fit 6–8 documents in $<800$ tokens.
  2. Implement `DeltaIndexBuffer` in `IndexManager` to accept append-only streaming document updates in memory.
- **Components Affected**: `src/novastack/context_budgeter.py`, `src/novastack/generation.py`, `src/novastack/index_manager.py`.
- **Target Tests**: Unit test suite `tests/test_phase_05_m2_compaction_delta.py`.
- **Evaluation**: Benchmark positive answer yield across all 101 positive cases.
- **Acceptance Criteria**:
  - Positive answer yield increases from 62.4% to $\ge 75\%$.
  - Mechanical citation precision remains strictly 100%.
  - Newly appended documents in `DeltaIndexBuffer` become searchable within $<500$ ms.
  - CPU prompt evaluation latency remains $<6.0$ seconds.

---

### Milestone 0.5-M3: Operator Web Search UI & Autonomous Circuit-Breaker Fallback
- **Objective**: Transform ATLAS into an intuitive visual product and guarantee high availability during upstream inference interruptions.
- **Scope**:
  1. Author a clean, self-contained single-page Operator Web UI served directly by FastAPI at `/ui`. Includes query input, caller role switch, highlighted citations, and visual provenance drawer.
  2. Implement `AutonomousFallbackRouter` in `InferenceServiceAdapter` that transparently reroutes requests to `LocalHuggingFaceProvider` when primary circuit breaker trips to `OPEN`.
- **Components Affected**: `src/novastack/service/api.py`, `src/novastack/quantized_provider.py`, new static assets directory `src/novastack/ui/`.
- **Target Tests**: UI endpoint smoke tests and circuit-breaker failure injection tests in `tests/test_phase_05_m3_ui_resilience.py`.
- **Evaluation**: Runbook drill simulating primary container outage during live web query.
- **Acceptance Criteria**:
  - Web UI renders completely in $<200$ ms; zero external npm/CDN dependencies at runtime.
  - 0% query drop (zero 503 errors) during intentional primary backend outage.
  - Seamless automatic re-convergence to primary Backend B when container recovers.

---

### Milestone 0.5-M4: Bounded Concurrency Expansion ($N=2$) & RS256 / OIDC Auth
- **Objective (Stretch Scope)**: Support simultaneous incident responders and enable federated enterprise identity.
- **Scope**:
  1. Bounded concurrency expansion to $N=2$ with CPU thread pinning (`OMP_NUM_THREADS=2`).
  2. Asymmetric token verification (RS256) with JWKS caching in `JwtIdentityVerifier`.
- **Components Affected**: `src/novastack/service/resilience.py`, `src/novastack/service/identity.py`.
- **Target Tests**: Concurrency stress harness and RS256 token verification suite.
- **Evaluation**: Multi-threaded load test measuring throughput vs latency degradation.
- **Acceptance Criteria**:
  - Concurrent query capacity increases to 2 without deadlock or thread thrashing.
  - Latency degradation under concurrency $\le 40\%$.
  - 100% fail-closed validation on RS256 signatures.

---

### Milestone 0.5-M5: 0.5 Release Baseline Certification & Commissioning Freeze
- **Objective**: Full end-to-end regression audit, documentation update, release packaging, and formal commissioning of ATLAS 0.5.0.
- **Scope**:
  1. Execute complete regression suite across all historical and new test cases ($>280$ tests).
  2. Update `docs/OPERATIONS_RUNBOOK.md` with UI operations and delta buffer maintenance.
  3. Package standalone distribution tarball `atlas-novastack-0.5.0.tar.gz`.
  4. Generate authoritative Phase 0.5 release manifests and cryptographic checksums.
- **Components Affected**: All documentation, release artifacts, and build scripts.
- **Target Tests**: Complete end-to-end test suite (`pytest -v`).
- **Acceptance Criteria**:
  - 100% green test suite (0 failures).
  - Positive answer yield $\ge 75\%$ with 100% citation precision and 0 security violations.
  - Formal CTO commissioning review and release sign-off.
