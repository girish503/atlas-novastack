# ATLAS 0.5 — Comprehensive Architecture Review

**Document ID**: `DOC-ATLAS-0.5-ARCH-REVIEW`  
**Author**: CTO / Principal AI Systems Architect  
**Project**: ATLAS — Evidence-Grounded Enterprise Search Platform  
**Current Baseline**: `0.4.14-rc1` (`COMMISSIONED WITH DOCUMENTED LIMITATIONS`)  
**Date**: 2026-09-23  

---

## 1. Executive Summary

Project ATLAS has achieved production commissioning under Release Candidate `0.4.14-rc1`. Operating under strict release discipline, the system demonstrated:
- **100% fail-closed security invariants** with zero credential leaks and zero cross-tenant leakage.
- **100% mechanical citation precision** and **93.65% citation completeness** on Backend B (`InferenceServiceAdapter` $\to$ containerized `gemma3:1b` Q4_K_M via Ollama).
- **10/10 controlled incident recovery drills** validated via `docs/OPERATIONS_RUNBOOK.md`.
- **225/225 regression tests passing** across 13 test suites.

However, commissioning also codified an explicitly constrained **operating envelope**:
- Single-node, CPU-only execution (Intel Core i3-N305 class hardware, 8GB RAM).
- Concurrency strictly bound to 1 (`max_concurrent_inferences=1`) with 0.5s queue shedding (HTTP 429).
- Static pre-computed corpus (1,393 documents, 1,663 chunks) without real-time synchronization.
- Top-3 raw evidence context cutoff (`max_evidence_items=3`) driven by CPU prompt evaluation constraints.
- Shallow 1-hop relational retrieval bound to compiled regex vocabularies.

As we initiate the **ATLAS 0.5 development cycle**, this review provides the exhaustive architectural reconnaissance of the 0.4.14-rc1 baseline. In accordance with non-negotiable engineering principles, **0.4.14-rc1 remains 100% frozen and immutable**. 0.5 represents a disciplined, evidence-driven evolution designed to solve high-value enterprise search challenges without accumulating unearned architectural complexity.

---

## 2. End-to-End System Architecture Map

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               CLIENT INGRESS & SECURITY BOUNDARY                        │
│                                                                                        │
│  HTTP Client ──► Authorization: Bearer <JWT> ──► FastAPI POST /query (port 8000)       │
│                                                     │                                  │
│                                                     ▼                                  │
│                                         JwtIdentityVerifier (HS256)                    │
│                                         CallerContext Binding (Tenant, Role, Dept)     │
└─────────────────────────────────────────────┬──────────────────────────────────────────┘
                                              │ Verified Context + Request
                                              ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        ATLAS SERVICE PIPELINE (Stateless Engine)                       │
│                                                                                        │
│ 1. IndexManager Lease: Acquire Active IndexGeneration Lease (Lease Lock)               │
│                                                                                        │
│ 2. Query Understanding:                                                                │
│    EntityCatalog, Regex Pattern Matching, Alias Expansion ('checkout' -> 'SVC-0001')   │
│                                                                                        │
│ 3. Multi-Channel Candidate Retrieval (Pre-Scoring Partitioning by tenant_id):         │
│    ┌──────────────────────────┬──────────────────────────┬──────────────────────────┐  │
│    │ BM25 Inverted Index      │ Dense Vector Index       │ Relational Retriever     │  │
│    │ Lexical Matching         │ BGE-small-en-v1.5        │ 0-hop / 1-hop Entity Graph│ │
│    │ Top-50 Candidates        │ Top-50 Candidates        │ Top-50 Candidates        │  │
│    └────────────┬─────────────┴────────────┬─────────────┴────────────┬─────────────┘  │
│                 └──────────────────────────┼──────────────────────────┘                │
│                                            ▼                                           │
│ 4. Rank Fusion: Reciprocal Rank Fusion (RRF k=60) + Relational Fusion (w=1.0)          │
│                                            ▼                                           │
│ 5. Metadata-Aware Reranker:                                                            │
│    Additive Scoring: Authority (+0.004/-0.004) + Lifecycle (+0.002/-0.004) + Recency   │
│                                            ▼                                           │
│ 6. Evidence Resolution Engine (EvidenceResolver):                                      │
│    - Stage 1: Candidate Ingestion & Channel Provenance                                 │
│    - Stage 2: Strict Pre-Evidence Authorization Gate (Role, Dept, ACL)                 │
│    - Stage 3: Multi-Channel & Intra-Document Deduplication                             │
│    - Stage 4: Adversarial & Retrieval Poisoning Classification                         │
│    - Stage 5: Version & Lifecycle Supersession Resolution                              │
│    - Stage 6: Temporal Validity Window Check                                           │
│    - Stage 7: Authority Conflict Detection                                             │
│    - Stage 8: Query-Aware Authority Bonus & Assembly -> EvidencePackage (Top-10)       │
└─────────────────────────────────────────────┬──────────────────────────────────────────┘
                                              │ Filtered EvidencePackage
                                              ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                          INFERENCE & GENERATION BOUNDARY                               │
│                                                                                        │
│  InferenceConcurrencyLimiter (Max Concurrency = 1, Queue Timeout = 0.5s)              │
│                                     │                                                  │
│                                     ▼                                                  │
│  Layer 1a Gate: Empty Evidence Check -> Return ABSTAINED (0ms)                         │
│  Layer 1b Gate: Unresolved Conflict Check -> Return ABSTAINED (0ms)                    │
│  Layer 1S Gate: Security Policy Check -> Return ABSTAINED (0ms)                        │
│                                     │ (Passed)                                         │
│                                     ▼                                                  │
│  Context Budgeting: Prune to Top-3 Items (max_evidence_items=3)                        │
│  Prompt Assembly: <evidence_data id="..."> Encapsulation + Chat Template               │
│                                     │                                                  │
│                                     ▼                                                  │
│  InferenceServiceAdapter (HTTP Client, Read Timeout = 25.0s, Request Timeout = 30.0s) │
│  CircuitBreaker (Threshold = 3, Real Production Cooldown = 10.0s)                      │
│                                     │                                                  │
│                                     ▼                                                  │
│  Docker Container `atlas-inference-5d` (port 8001, non-root appuser:1000)              │
│  Host Ollama Daemon (port 11434) ──► `gemma3:1b` (Q4_K_M GGUF)                         │
└─────────────────────────────────────────────┬──────────────────────────────────────────┘
                                              │ Raw Generated Text
                                              ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        POST-GENERATION CITATION & RESPONSE                             │
│                                                                                        │
│  Tiered C2 Citation Resolver:                                                          │
│  1. Legacy Chunk-level Citation Check                                                  │
│  2. C1 Short-Exact Substring Matching                                                  │
│  3. C2 Sentence-Level N-Gram Grounding & Precision Filter                              │
│                                     │                                                  │
│                                     ▼                                                  │
│  Formatting & Observability:                                                           │
│  - Attach Verified Citations & Diagnostics                                             │
│  - Prometheus Latency, Inferences & Rejections Recording                               │
│  - Structured JSON Logging with Credential Redaction                                   │
│  - Return QueryResponse (HTTP 200)                                                     │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Subsystem Reconnaissance & Flow Audits

### Flow B: Runtime Request Flow
1. **HTTP Ingress**: `POST /query` receives JSON payload containing `query`, `user_context`, and optional `evaluation_id`.
2. **Authentication Middleware**: In `src/novastack/service/api.py`, `get_current_caller` validates the HTTP `Authorization: Bearer <token>` header using `JwtIdentityVerifier`.
3. **Context Binding**: The user-supplied `CallerContext` is checked against verified token claims via `assert_context_matches_identity()`. If there is any mismatch between token claims and payload headers (e.g. spoofed tenant or elevated role), execution immediately aborts with HTTP 403.
4. **Execution Lease**: `AtlasServicePipeline.execute_query()` leases the active `IndexGeneration` from `IndexManager`.
5. **Stage Latencies**: Timers wrap each pipeline stage, emitting duration metrics to `PrometheusMetrics`.

### Flow C: Retrieval Flow
1. **Query Understanding (`QueryUnderstandingExtractor`)**:
   - Matches entity tokens against `EntityCatalog` (canonical names, service aliases, IDs).
   - Produces expanded query strings for lexical channels (e.g., injecting service identifiers).
2. **Channel 1 — BM25 Lexical Retrieval**:
   - In-memory inverted index partitioned by `tenant_id`.
   - Computes Okapi BM25 scores across tokenized chunks; returns top-50 candidates.
3. **Channel 2 — Dense Vector Retrieval**:
   - Pre-computed 384-dimensional dense vectors using `BAAI/bge-small-en-v1.5`.
   - Cosine similarity computed against tenant-filtered passage embeddings; returns top-50 candidates.
4. **Channel 3 — Relational Structured Retrieval**:
   - Deterministic graph search traversing catalog relations (`owned_by`, `caused_by`, `affects`, `resolved_by`, `targets`).
   - Bounded strictly to traversal depth $d \le 1$. Reverse-maps entities to chunks; returns top-50 candidates.
5. **Candidate Fusion & Reranking**:
   - Dual-channel RRF ($k=60$) fuses BM25 and Dense pools.
   - Structured candidates fused additively ($w=1.0$).
   - `MetadataReranker` applies fixed linear additive weights: authority, lifecycle status, recency, and provenance entity match.

### Flow D: Evidence-Selection Flow
The `EvidenceResolver` operates downstream from retrieval:
1. **Pre-Evidence Authorization**: Re-verifies every candidate against `CallerContext`. Chunks violating role, department, classification, or ACL are dropped into `excluded_evidence`.
2. **Deduplication**: Collapses chunks from the same document, preserving the highest-scoring chunk.
3. **Adversarial Quarantine**: Matches against known adversarial targets (indirect injection, retrieval poisoning); quarantines untrusted or manipulated documents.
4. **Lifecycle & Version Resolution**: If query specifies "latest", superseded/deprecated documents are penalized. If query specifies historical versions (e.g. "v1.0"), historical items are preserved.
5. **Query-Aware Authority Bonus**: If query explicitly asks for notes, tickets, or triage Slack conversations, low-authority penalties are waived (`enable_query_aware_authority = True`).
6. **Package Assembly**: Produces an `EvidencePackage` containing up to 10 ranked `EvidenceItem` records.

### Flow E: Security & Authorization Flow
- **Tenant Isolation**: Non-bypassable constraint enforced in query filters (`filters={"tenant_id": caller.tenant_id}`) on all retrieval backends.
- **Fail-Closed Gateways**: Missing tokens $\to$ 401; token/context tenant mismatch $\to$ 403; corrupted secret $\to$ service startup abort.
- **Layer 1S Security Abstention**: When ground-truth indicates an unauthorized query (evaluated in test harnesses via forbidden doc sets), Layer 1S forces deterministic abstention (`provider_invoked = False`) with 0 citations in $<1$ ms.
- **Zero-Trust Error Payloads**: Exceptions caught at the API boundary are sanitized by `sanitize_error_detail()` to prevent leaking directory paths, environment variables, or database structures.

### Flow F: Inference & Generation Flow
1. **Resilience Limiter**: `InferenceConcurrencyLimiter` acquires an execution semaphore (`max_concurrent_inferences=1`). If busy, waits up to 0.5s before shedding traffic with HTTP 429.
2. **Context Budgeting**: `AdaptiveContextBudgeter` prunes `EvidencePackage` from 10 items down to **3 items** (`max_evidence_items=3`) to stay within CPU generation limits.
3. **Prompt Construction**: Evidence items are wrapped in `<evidence_data id="...">` tags. The system prompt directs the LLM to answer using only supplied data and cite bracketed tags.
4. **Transport**: `InferenceServiceAdapter` issues an HTTP POST to `http://127.0.0.1:8001/generate` (Docker container), which relays to host Ollama (`http://127.0.0.1:11434/api/generate`).
5. **Circuit Breaker**: Monitored by `CircuitBreaker`. 3 consecutive timeouts ($>30$s) or 503 errors transition the circuit to `OPEN`, failing fast for **10.0 seconds** before testing `HALF_OPEN`.

### Flow G: Index Lifecycle
- Controlled by `IndexManager`.
- Each corpus state is an immutable `IndexGeneration` snapshot identified by `generation_id` and SHA-256 checksum.
- Content hashing (`compute_document_content_hash`) detects `NEW`, `UPDATED`, `DELETED`, and `UNCHANGED` documents.
- Candidate index builds are validated out-of-band (checking vector dimensions, orphan chunks, and BM25 alignment). Only valid builds can transition to `ACTIVE`.
- Read queries hold a lightweight generation lease lock, ensuring zero read disruption during generation publishing.

### Flow H: Evaluation Framework
- **Dataset**: `data/evaluation/novastack/evaluation_cases.json` containing 120 canonical cases (101 positive, 19 negative).
- **Harness**: `scripts/eval_generator.py` executes pipelines against test cases.
- **Metrics Computed**:
  - Mechanical Citation Precision: % of citations matching ground-truth chunk/document IDs.
  - Citation Completeness: % of answerable positive cases having valid citations.
  - Negative Case Abstention: % of unauthorized / unanswerable cases resulting in principled abstention.
  - Latency: Mean, P50, P90, P95, and Max execution times.

### Flow I: Observability Architecture
- **Metrics**: `novastack.observability.metrics.PrometheusMetrics` exports Prometheus exposition text at `GET /metrics`. Tracks stage latencies, active inferences, circuit breaker states, and citation counts.
- **Tracing**: Lightweight internal span tracer (`novastack.observability.tracing`) generating structured spans for each pipeline stage.
- **Logging**: JSON structured logging (`novastack.observability.logging`) emitting ISO-8601 timestamps, Request-IDs, and tenant contexts, with automated redaction of sensitive credentials.

### Flow J: Deployment Architecture
- **Host**: Single bare-metal or VM node (Linux / Windows).
- **Inference Container**: `atlas-inference:5d` running standalone FastAPI service under Uvicorn, executing as non-root `appuser:1000` on port 8001.
- **Inference Runtime Engine**: Native Ollama daemon bound to host port 11434 (`0.0.0.0:11434`), serving quantized GGUF weights (`gemma3:1b` Q4_K_M).
- **Application Server**: ATLAS FastAPI API server bound to port 8000.

### Flow K: Configuration System
- Configured via environment variables with strictly typed pydantic/dataclass bindings:
  - `ATLAS_JWT_SECRET` ($\ge 32$ bytes)
  - `ATLAS_INFERENCE_SERVICE_URL` (default: `http://127.0.0.1:8001`)
  - `ATLAS_MAX_CONCURRENT_INFERENCES` (default: `1`)
  - `ATLAS_REQUEST_TIMEOUT_SECONDS` (default: `30.0`)
  - `ATLAS_QUEUE_TIMEOUT_SECONDS` (default: `0.5`)
  - `ATLAS_CIRCUIT_COOLDOWN_SECONDS` (default: `10.0`)
  - `DEFAULT_PROVIDER` (default: `inference_service`, rollback: `local_huggingface`)

### Flow L: Test Structure
- 14 test suites in `tests/`:
  - `test_phase_4m_api_service.py`: Endpoint contracts, HTTP status translation.
  - `test_phase_4m_auth_fail_closed.py`: JWT token edge cases, missing secrets.
  - `test_phase_4t_identity_boundary.py`: Cross-tenant boundary verification.
  - `test_phase_5a_provider_boundary.py`: Provider abstraction contracts.
  - `test_phase_5b_quantized_provider.py`: GGUF/Ollama provider integration.
  - `test_phase_5g_abstention_safety.py`: Layer 1S security gate verification.
  - `test_phase_5i_production_promotion.py`: Readiness review invariants.
  - `test_phase_5j_production_promotion.py`: Production promotion defaults.
  - `test_phase_5k_release_freeze.py`: Release freeze checks.
  - `test_phase_5l_independent_validation.py`: Verification reproducibility.
  - `test_phase_5m_packaging.py`: Package build and tarball audits.
  - `test_phase_5n_operational_runbook.py`: Operations runbook verification.
  - `test_phase_5o_incident_recovery.py`: 10 failure injection drills.
  - `test_phase_5p_final_commissioning.py`: 18 final commissioning gates.

### Flow M: Documentation & Release Structure
- `docs/`: 63 detailed architectural reports, operational runbooks (`OPERATIONS_RUNBOOK.md`), decision logs (`DECISIONS.md`), and release specifications.
- `artifacts/`: Certified machine-readable manifests, benchmark records, and test outputs.
- `dist/`: Sealed release tarball `atlas-novastack-0.4.14-rc1.tar.gz` (SHA-256: `382cde6c...`).

---

## 4. Component Audit Matrix

| Component | Primary Responsibility | Inputs | Outputs | Key Dependencies | Current Guarantees | Known Limitations |
|:---|:---|:---|:---|:---|:---|:---|
| **`JwtIdentityVerifier`** | Cryptographic caller authentication | JWT Bearer token | Verified `CallerContext` | `pyjwt`, `cryptography` | Fail-closed, HS256 $\ge 32$B, rejects forged/expired tokens | Only supports HS256 symmetric keys; no RS256/OIDC discovery |
| **`BM25Index`** | Lexical passage search | Query text, tenant filter | Top-50 candidates with BM25 scores | Tokenizer | In-memory, strict tenant partitioning | No phrase proximity, no synonym expansion beyond aliases |
| **`DenseIndex`** | Semantic vector search | 384d Query embedding | Top-50 candidates with cosine scores | `BGE-small-en-v1.5`, PyTorch/numpy | Pre-scoring tenant filtering, normalized dot-product | Monolithic in-memory matrix, CPU embedding computation is slow |
| **`StructuredRetriever`** | Graph-aware relational retrieval | Extracted entities, intent | Top-50 candidates with traversal paths | `EntityCatalog` | Deterministic typed graph traversal, $d \le 1$ | Max traversal depth = 1; relies on brittle regex intent matching |
| **`MetadataReranker`** | Additive metadata scoring | Candidates, `QueryUnderstanding` | Reranked candidates with attribution | `DocumentMetadataSnapshot` | Additive explainable scoring; authority/lifecycle boosts | Fixed linear weights; cannot learn non-linear feature interactions |
| **`EvidenceResolver`** | Provenance, authorization, and assembly | Top candidates, caller context | Bounded `EvidencePackage` (Top-10) | `SearchDocument`, `SearchChunk` | Fail-closed authorization, conflict detection, deduplication | Monolithic rules; drops valid evidence if ranking suppressed |
| **`AdaptiveContextBudgeter`**| Context pruning for LLM prompt | `EvidencePackage` | Top-3 serialized evidence chunks | Token counter | Restricts prompt token count to $<800$ tokens | Drops ranks 4–10, preventing multi-hop cross-document reasoning |
| **`InferenceServiceAdapter`**| LLM invocation & resilience boundary | Serialized prompt, package | `AnswerResult` with raw text | Docker service, Ollama, httpx | 30s timeout, concurrency=1 limiter, 10s circuit breaker | Zero streaming; single concurrency creates queue bottleneck |
| **`Tiered C2 CitationResolver`**| Sentence-level citation validation | Answer text, evidence items | Validated `Citation` list | Text regex, n-gram matcher | 100% precision; strips ungrounded or fabricated citations | Conservative; false abstention on valid paraphrasing |
| **`IndexManager`** | Atomic generation leasing & rollback | Source documents | Active `IndexGeneration` lease | Local filesystem | Atomic hot-swapping, candidate validation, rollback | Monolithic rebuild; no live streaming ingestion or CDC |
