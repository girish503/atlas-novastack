# ATLAS 0.5 — Architecture Options & Prioritization Analysis

**Document ID**: `DOC-ATLAS-0.5-ARCH-OPTIONS`  
**Author**: CTO / Principal AI Systems Architect  
**Project**: ATLAS — Evidence-Grounded Enterprise Search Platform  
**Current Baseline**: `0.4.14-rc1` (`COMMISSIONED WITH DOCUMENTED LIMITATIONS`)  
**Date**: 2026-09-23  

---

## 1. Executive Architecture Strategy

In evaluating technical architecture for ATLAS 0.5, our guiding engineering principle is:  
**"Do not add technologies to solve problems we do not have, or before we have extracted full value from the technologies we already possess."**

Too many enterprise search and AI platforms collapse under their own weight by adopting distributed vector databases, multi-agent frameworks, complex Kubernetes microservices, and massive GPU clusters before their core retrieval, evidence selection, and grounding pipelines are sound.

ATLAS 0.4.14-rc1 proves that a single-node, CPU-efficient, deterministic retrieval pipeline with containerized quantized inference can achieve 100% mechanical citation precision, zero security leaks, and deterministic recovery.

The goal of ATLAS 0.5 is to **remove the specific, measured bottlenecks that limit enterprise utility** while preserving the simplicity, predictability, and low total-cost-of-ownership (TCO) that makes ATLAS exceptional.

---

## 2. Evaluation of Candidate Architectural Capabilities

We examine 12 candidate capabilities, rigorously dissecting the architectural trade-offs of each:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        ATLAS 0.5 ARCHITECTURE CAPABILITY OPTIONS                       │
│                                                                                        │
│  [1] Multi-Hop Relational Traversal (d <= 3)     │ [7] Bounded Multi-Worker Concurrency │
│  [2] Structured Temporal Interval Filtering      │ [8] OIDC / RS256 Federated Identity │
│  [3] Salience-Budgeted Evidence Compaction       │ [9] Semantic Query Decomposition    │
│  [4] Append-Only Delta Index Buffer              │ [10] Multi-Turn Agentic Chat Loops  │
│  [5] Operator Web Search & Evidence Graph UI     │ [11] External Distributed Vector DB │
│  [6] Autonomous Circuit-Breaker Backend Fallback │ [12] GPU 70B Multi-Node Deployment  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

### Option 1: Multi-Hop Relational Graph Traversal ($d \le 3$)
- **Problem Solved**: Current relational retrieval is strictly limited to depth $d=1$. Queries connecting incidents to deployments, PR reviewers, and customer accounts fail because relationships are separated by 2 or 3 graph edges.
- **Architectural Change**: Extend `StructuredRetriever` to perform bounded breadth-first search (BFS) traversal up to depth $d=3$ with path-weight decay ($\gamma = 0.7^d$) and per-hop tenant/authorization filtering.
- **Benefits**: Unlocks multi-entity enterprise queries (incident $\to$ deployment $\to$ PR $\to$ author; service $\to$ team $\to$ department $\to$ director) without adding an external graph database.
- **Risks**: Potential combinatorial explosion of candidate chunks if node degree is high.
- **Complexity**: **MEDIUM**. Pure in-memory algorithm over existing `EntityCatalog` dataclasses.
- **Success Criteria**: R@10 on multi-hop questions increases by $\ge 25\%$; zero latency increase on non-relational queries.
- **Dependencies**: Existing `EntityCatalog` and `TypedRelationship`.
- **Verdict**: **MUST-HAVE FOR 0.5**.

---

### Option 2: Structured Temporal Interval Filtering
- **Problem Solved**: Temporal queries (e.g. "valid in late 2024 before the FY25 revision") currently rely on brittle regex matching for words like "before", while the reranker's recency boost actively suppresses older, correct policy versions.
- **Architectural Change**: Introduce a deterministic temporal parser in `QueryUnderstanding` that extracts formal ISO-8601 date bounds (`[t_start, t_end]`) and applies a pre-scoring filter or hard reranking gate against document `valid_from` and `valid_until` metadata.
- **Benefits**: Guarantees point-in-time accuracy for corporate policies, historical incident postmortems, and deprecated API guides.
- **Risks**: Ambiguous natural language date expressions may fail to parse (requires fallback to unconstrained search).
- **Complexity**: **LOW**. Pure Python date parsing and comparison logic.
- **Success Criteria**: 100% accuracy on canonical temporal test cases (`EVAL-0067..0074`); 0 regressions on non-temporal queries.
- **Dependencies**: `QueryUnderstandingExtractor` and `MetadataReranker`.
- **Verdict**: **MUST-HAVE FOR 0.5**.

---

### Option 3: Salience-Budgeted Context Compaction (Top 3 $\to$ Top 6–8 Documents)
- **Problem Solved**: `AdaptiveContextBudgeter` strictly truncates evidence to **Top-3 items** to keep prompt tokens $<800$ on CPU. Complex queries requiring synthesis across 4–6 documents suffer from false abstentions.
- **Architectural Change**: Implement extractive sentence-level salience filtering. Instead of serializing entire 400-word chunks, extract the top 2–3 salient sentences per chunk based on query BM25/cosine overlap. Pack 6–8 distinct evidence sources into the same 800-token prompt budget.
- **Benefits**: Doubles the effective document diversity in the prompt without increasing prompt tokens or CPU inference latency.
- **Risks**: Potential loss of surrounding narrative context; sentence-level citation verification must remain exact.
- **Complexity**: **MEDIUM**. Builds directly on `novastack.context_budgeter.compress_evidence_item`.
- **Success Criteria**: Increases positive answer yield by $\ge 15\%$ on multi-document cases; keeps CPU prompt evaluation $<6.0$s.
- **Dependencies**: `GroundedAnswerGenerator` and `CitationValidator`.
- **Verdict**: **MUST-HAVE FOR 0.5**.

---

### Option 4: Append-Only Delta Index Buffer (Live Ingestion)
- **Problem Solved**: ATLAS 0.4.14 is an offline batch-indexed system. New operational records (incident triage notes, customer support updates) cannot be searched without a full batch rebuild.
- **Architectural Change**: Introduce an in-memory `DeltaIndexBuffer` alongside the base `IndexGeneration`. New documents are hashed and appended to a lightweight BM25 and mini-vector buffer in memory. Queries search Base Index + Delta Buffer and merge candidates via RRF.
- **Benefits**: Sub-second data freshness for live enterprise incidents without mutating the sealed base index generation.
- **Risks**: Memory footprint grows if delta buffer is never consolidated; requires background consolidation compaction.
- **Complexity**: **MEDIUM**. Reuses `BM25Index` and `IndexManager` primitives.
- **Success Criteria**: New records searchable within $<500$ms of ingestion; zero read disruption to base generation.
- **Dependencies**: `IndexManager`.
- **Verdict**: **MUST-HAVE FOR 0.5**.

---

### Option 5: Operator Web Search & Evidence Inspection UI
- **Problem Solved**: ATLAS is currently a headless JSON API. Incident responders and operators must use terminal commands (`curl`) or custom scripts, creating friction during high-stress triage and making citation verification tedious.
- **Architectural Change**: Build a lightweight, self-contained single-page web UI (HTML5/Tailwind/Vanilla JS or React) served directly by FastAPI at `/` or `/ui`. Features: Query bar, caller context selector, grounded answer box with clickable inline citations, and an interactive **Evidence Provenance Drawer** showing exact chunks and metadata.
- **Benefits**: Transforms ATLAS from an internal engine into an accessible enterprise product; drastically improves operational usability.
- **Risks**: Potential UI bloat or framework dependencies if not kept minimal.
- **Complexity**: **MEDIUM**. Single static bundle served by FastAPI static files.
- **Success Criteria**: Standalone web UI loads in $<200$ms; operators can execute queries and visually inspect citations in 1 click.
- **Dependencies**: FastAPI static mounts.
- **Verdict**: **MUST-HAVE FOR 0.5**.

---

### Option 6: Autonomous Circuit-Breaker Fallback to Rollback Backend
- **Problem Solved**: When the production Ollama daemon or container encounters severe latency ($>30$s) or crashes, the 0.4.14 circuit breaker trips to `OPEN` and fast-fails all callers with HTTP 503 for 10 seconds. Recovery requires human runbook execution.
- **Architectural Change**: When `CircuitBreaker` trips to `OPEN`, `InferenceServiceAdapter` automatically and transparently reroutes incoming requests to the certified rollback backend (`LocalHuggingFaceProvider` on CPU). When the probe detects primary backend recovery, it seamlessly transitions back to primary Backend B.
- **Benefits**: High availability (HA) with zero operator intervention; eliminates 503 downtime during upstream restarts.
- **Risks**: Temporary latency increase during fallback execution on FP32 CPU.
- **Complexity**: **LOW**. Simple provider delegation within `InferenceServiceAdapter`.
- **Success Criteria**: 0% downtime during container failure drills; automatic recovery upon primary container restart.
- **Dependencies**: `LocalHuggingFaceProvider` and `CircuitBreaker`.
- **Verdict**: **MUST-HAVE FOR 0.5**.

---

### Option 7: Bounded Multi-Worker Concurrency ($N=2$ to $4$)
- **Problem Solved**: Strict concurrency of 1 (`max_concurrent_inferences=1`) sheds simultaneous incident queries with HTTP 429 after 0.5s.
- **Architectural Change**: Expand `InferenceConcurrencyLimiter` from a binary lock to a semaphore with $N=2$ (or $N=4$ on multi-core hosts), combined with CPU core affinity (`OMP_NUM_THREADS=2` per worker).
- **Benefits**: Supports multiple simultaneous incident responders without queuing drop-offs.
- **Risks**: CPU thrashing and latency degradation if multiple Ollama instances contend for the same memory bus.
- **Complexity**: **MEDIUM**. Requires careful thread pinning and empirical latency benchmarking.
- **Success Criteria**: Serves 2 concurrent queries with latency degradation $<40\%$; zero thread-deadlocks.
- **Dependencies**: `InferenceConcurrencyLimiter` and Ollama concurrency settings.
- **Verdict**: **VALUABLE IF CAPACITY ALLOWS (Phase 0.5-M3)**.

---

### Option 8: OIDC / RS256 Federated Identity Verification
- **Problem Solved**: Current `JwtIdentityVerifier` only validates symmetric HS256 tokens using a shared secret. Enterprise environments use asymmetric keys (RS256) distributed via OpenID Connect (OIDC) discovery endpoints.
- **Architectural Change**: Extend `JwtIdentityVerifier` to support RS256 public key certificates and JWKS (JSON Web Key Set) URLs with local key caching.
- **Benefits**: Direct integration with Okta, Google Workspace, Azure AD, Keycloak without sharing master HMAC secrets.
- **Risks**: Network dependency on IdP JWKS endpoint during key rotation.
- **Complexity**: **MEDIUM**. Uses standard `pyjwt[crypto]` libraries.
- **Success Criteria**: Successfully validates RS256 signed JWTs from simulated IdP; falls back cleanly to local cached keys.
- **Dependencies**: `novastack.service.identity`.
- **Verdict**: **VALUABLE IF CAPACITY ALLOWS (Phase 0.5-M4)**.

---

### Option 9: Semantic Query Decomposition & Sub-Query Planning
- **Problem Solved**: Complex questions containing multiple independent clauses (e.g. "What is service A, who owns it, and what was its last incident?") require distinct retrieval plans.
- **Architectural Change**: LLM-driven query planner decomposing queries into structured sub-queries, executing each, and synthesizing.
- **Benefits**: Higher coverage on multi-intent questions.
- **Risks**: Adds 1–2 sequential LLM calls, blowing up CPU inference latency from 5s to 15–20s; high risk of hallucinated sub-queries.
- **Complexity**: **HIGH**.
- **Verdict**: **DEFER TO 0.6**. Too heavy for current CPU budget. Multi-hop graph traversal (Option 1) solves the entity portion deterministically at 0ms.

---

### Option 10: Multi-Turn Agentic Chat Loops
- **Problem Solved**: Users cannot ask follow-up questions referencing previous answers.
- **Architectural Change**: Introduce conversational state, chat history memory, and multi-turn agentic tool calling loops.
- **Benefits**: Familiar ChatGPT-style conversational UX.
- **Risks**: Drastically complicates authorization (conversation state caching can leak unauthorized evidence across turns); opens broad attack surface for indirect prompt injection; stateful server violates 12-factor architecture.
- **Complexity**: **HIGH**.
- **Verdict**: **EXPLICITLY REJECT AS PREMATURE FOR 0.5**. Enterprise search is fundamentally about deterministic, evidence-grounded truth retrieval, not open-ended conversation.

---

### Option 11: External Distributed Vector Database (Milvus / Qdrant / Pinecone)
- **Problem Solved**: Scaling vector storage beyond 1 million vectors.
- **Architectural Change**: Replace in-memory `DenseIndex` with remote vector database cluster via gRPC.
- **Benefits**: Supports billion-scale vector indexes and horizontal clustering.
- **Risks**: Introduces major network hop, external failure modes, complex distributed deployments, and substantial operational overhead for a 1,663-chunk corpus.
- **Complexity**: **VERY HIGH**.
- **Verdict**: **EXPLICITLY REJECT AS PREMATURE**. Current corpus has 1,663 chunks. NumPy cosine similarity computes in $<2$ ms. Introducing an external distributed vector database is classic architectural cargo-culting.

---

### Option 12: GPU-Accelerated 70B Parameter Model Deployment
- **Problem Solved**: 1B model under-confidence and false abstentions on complex reasoning.
- **Architectural Change**: Replace quantized `gemma3:1b` with 70B model requiring multiple high-end enterprise GPUs (NVIDIA A100/H100).
- **Benefits**: Superior raw reasoning capacity.
- **Risks**: Destroys low-TCO proposition; requires specialized cloud hardware; eliminates local edge deployment capability; extreme power and cost overhead.
- **Complexity**: **HIGH**.
- **Verdict**: **EXPLICITLY REJECT AS PREMATURE**. 80% of current false abstentions can be solved by better evidence selection and salience compaction on our efficient 1B model.

---

## 3. Prioritization Matrix

Every candidate is evaluated against 10 strict engineering criteria:

| Capability Candidate | User Impact | Business Impact | Security Impact | Reliability Impact | Evidence from Baseline | Impl. Complex. | Ops Complex. | Measurable Improv. | Dep. Risk | Reversibility | Overall Priority |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **1. Multi-Hop Graph Traversal ($d \le 3$)** | **HIGH** | **HIGH** | LOW | **HIGH** | **HIGH** (Stage A/B losses) | MEDIUM | LOW | **HIGH** (+25% R@10) | LOW | **HIGH** | **MUST-HAVE (0.5-M1)** |
| **2. Temporal Interval Filtering** | **HIGH** | **HIGH** | LOW | **HIGH** | **HIGH** (EVAL-0067..74) | LOW | LOW | **HIGH** (100% on date cases)| LOW | **HIGH** | **MUST-HAVE (0.5-M1)** |
| **3. Salience Evidence Compaction** | **HIGH** | **HIGH** | LOW | **HIGH** | **HIGH** (Stage C cutoffs) | MEDIUM | LOW | **HIGH** (2x doc diversity) | LOW | **HIGH** | **MUST-HAVE (0.5-M2)** |
| **4. Append-Only Delta Index Buffer** | **HIGH** | **HIGH** | LOW | **HIGH** | **HIGH** (Zero freshness) | MEDIUM | LOW | **HIGH** (<500ms live indexing)| LOW | **HIGH** | **MUST-HAVE (0.5-M2)** |
| **5. Operator Web Search & Evidence UI** | **HIGH** | **HIGH** | LOW | MEDIUM | **HIGH** (Operator friction) | MEDIUM | LOW | **HIGH** (1-click proof visual) | LOW | **HIGH** | **MUST-HAVE (0.5-M3)** |
| **6. Autonomous Fallback to Backend A** | MEDIUM | **HIGH** | LOW | **HIGH** | **HIGH** (Incident 04 drills) | LOW | LOW | **HIGH** (0% downtime on trip) | LOW | **HIGH** | **MUST-HAVE (0.5-M3)** |
| **7. Bounded Multi-Worker Concurrency** | **HIGH** | MEDIUM | LOW | MEDIUM | **HIGH** (HTTP 429 on $N>1$) | MEDIUM | MEDIUM | **HIGH** (Supports 2 responders)| MEDIUM| **HIGH** | **VALUABLE (0.5-M4)** |
| **8. OIDC / RS256 Federated Auth** | MEDIUM | **HIGH** | **HIGH** | LOW | MEDIUM (Enterprise IdP) | MEDIUM | LOW | **HIGH** (Direct Okta/Azure AD) | LOW | **HIGH** | **VALUABLE (0.5-M4)** |
| **9. Semantic Query Decomposition** | MEDIUM | MEDIUM | LOW | LOW | LOW (Too slow on CPU) | HIGH | MEDIUM | MEDIUM | HIGH | MEDIUM | **DEFER (0.6)** |
| **10. Multi-Turn Agentic Chat Loops** | MEDIUM | LOW | **HIGH** | **HIGH** | LOW (High injection risk)| HIGH | HIGH | LOW (Adds drift/leakage) | HIGH | LOW | **REJECT (Premature)** |
| **11. Distributed Vector DB (Milvus)** | LOW | LOW | LOW | **HIGH** | NONE (1663 chunks = 2ms) | HIGH | HIGH | LOW (Adds network latency) | HIGH | LOW | **REJECT (Premature)** |
| **12. GPU 70B Multi-Node Deployment** | MEDIUM | LOW | LOW | LOW | LOW (Violates CPU envelope)| HIGH | HIGH | LOW (Extreme cost/power) | HIGH | LOW | **REJECT (Premature)** |

---

## 4. Final Classification Summary for ATLAS 0.5

### Group A: Must-Have for 0.5 (Core Scope)
1. **Multi-Hop Relational Traversal ($d \le 3$)**: Solves relational blind spots deterministically.
2. **Structured Temporal Interval Filtering**: Solves point-in-time policy and historical guidance queries.
3. **Salience-Budgeted Evidence Compaction**: Doubles document diversity in the prompt without increasing CPU latency.
4. **Append-Only Delta Index Buffer**: Enables live operational data freshness without breaking base index immutability.
5. **Operator Web Search & Evidence Inspection UI**: Transforms headless API into an intuitive enterprise product.
6. **Autonomous Circuit-Breaker Fallback**: Eliminates 503 outages during primary backend interruptions.

### Group B: Valuable if Capacity Allows (Stretch Scope)
7. **Bounded Multi-Worker Concurrency ($N=2$)**: Evaluated under controlled CPU thread pinning.
8. **OIDC / RS256 Federated Auth**: Adds public key token verification for standard enterprise IdPs.

### Group C: Defer to 0.6
9. **Semantic Query Decomposition**: Requires higher-throughput inference hardware before multi-LLM planning is viable.

### Group D: Explicitly Rejected as Premature
10. **Multi-Turn Agentic Chat Loops**: High stateful security risk; tangential to grounded enterprise search.
11. **External Distributed Vector Databases**: Unnecessary operational overhead for current index scale.
12. **70B GPU Deployment**: Violates single-node, low-TCO, CPU-first deployment principles.
