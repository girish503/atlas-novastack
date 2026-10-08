# ATLAS — Developer & Maintainer Repository Guide

**Audience**: Staff AI Engineers, Information Retrieval Engineers, Backend/Distributed Systems Engineers, and Security Reviewers joining or auditing the ATLAS codebase.
**Purpose**: An authoritative technical map answering: *"If I join this project today, where do I look, how does data flow, and why does each file exist?"*
**Repository Root**: . (relative workspace root)
**Certified HEAD Commit**: `6b8018b1e3d0864bcd2d2cae1319712bc13ae9d4`
**Certified Baseline**: `d325e5a82681456ebaca57f2f27c1900f17bd415`

---

## 1. High-Level Architecture & End-to-End Execution Flow

When a client submits an authenticated request to `POST /query`, execution flows synchronously through 14 distinct stages:

```
[Client / UI Application]
       │  POST /query (Bearer <JWT>)
       ▼
1.  api.py:query()                         ──► Extracts X-Request-ID; binds distributed correlation context.
       ▼
2.  identity.py:verify_authorization()     ──► Cryptographically verifies HS256 signature, issuer, audience, clock skew.
       ▼
3.  api.py:assert_context()                ──► Asserts caller JSON context matches verified JWT claims (fail-closed).
       ▼
4.  resilience.py:acquire()                ──► Evaluates concurrency limiter & circuit breaker state.
       ▼
5.  canary.py:route_request()              ──► Evaluates SHA-256 hash bucket routing (held at 0.0% standby).
       ▼
6.  H5_1QueryUnderstandingOverlay          ──► Expands operational shorthand, service aliases, and incident n-grams.
       ▼
7.  Multi-Channel Retrieval:
    ├── bm25.py:search()                   ──► In-memory Okapi BM25 lexical search (k1=1.5, b=0.75).
    ├── dense.py:search()                  ──► 384-dim MiniLM dense semantic search (cosine similarity).
    └── relational_retrieval.py            ──► Relational entity graph traversals (services, incidents, teams).
       ▼
8.  depth_fusion_ablation.py:fuse()        ──► Reciprocal Rank Fusion (fuse_rrf_sum with k=60).
       ▼
9.  metadata_reranker.py:rerank()          ──► Re-weights candidates by catalog authority tiers (Postmortem: 1.0, Draft: 0.3).
       ▼
10. evidence_resolution.py:resolve()       ──► PRE-EVIDENCE AUTHORIZATION BOUNDARY:
                                               Filters foreign tenants, enforces ACLs, quarantines poisoned records.
       ▼
11. context_budgeter.py:format()           ──► Prunes tokens and wraps evidence inside untrusted <evidence_data> tags.
       ▼
12. generation.py:generate_answer()        ──► Constrained generation via Gemma 3 1B IT (AnswerGeneratorProvider).
       ▼
13. citation_validator.py:validate()       ──► Server-side C2 engine verifies [EVD-xxx] tags and chunk provenance.
       ▼
14. api.py:serialize()                     ──► Emits QueryResponse with validated citations, latency, and telemetry headers.
```

---

## 2. Top-Level Directory Layout

```
ATLAS/
├── src/novastack/             # Core Python package (all intelligence, retrieval, and service logic)
│   ├── service/               # FastAPI service, JWT identity, resilience, schemas
│   ├── observability/         # Prometheus metrics, structured logging, correlation tracing
│   └── inference_service/     # Standalone HTTP container wrapper for LLM inference
├── data/
│   ├── raw/novastack/         # Entity catalogs, adversarial fixtures, security fixtures
│   ├── processed/novastack/   # search_documents.json, search_chunks.json, dense_embeddings.npz
│   └── evaluation/novastack/  # 120 canonical evaluation cases and difficulty taxonomy
├── ui/                        # Enterprise demonstration Single-Page Application (SPA)
│   ├── index.html             # Zero-secret frontend interface with live status and evidence drawer
│   └── demo_tokens.json       # Pre-signed isolated demo credentials for 4 synthetic personas
├── tests/                     # 94 automated pytest unit, integration, and security tests
│   └── security/              # Certified red-team attack harness (Categories 7.1 to 7.9)
├── scripts/                   # Evaluation runners, demo token generators, scenario validators
├── docs/                      # Architectural reports, benchmark manifests, security audits
├── Dockerfile                 # API service container definition (Python 3.11-slim)
├── Dockerfile.inference       # Inference service container definition
├── pyproject.toml             # Package metadata, dependencies, and pytest configuration
└── README.md                  # Public AI engineering showcase and primary project landing page
```

---

## 3. Detailed Component Map (File-by-File Technical Guide)

Every important file in the ATLAS repository is documented below with its exact path, technical purpose, inputs, outputs, and architectural justification.

### 3.1 HTTP Gateway, Security Boundary & Resilience

#### [`src/novastack/service/api.py`](../src/novastack/service/api.py)
* **PATH**: `src/novastack/service/api.py`
* **PURPOSE**: Main FastAPI application entry point and service pipeline orchestrator.
* **INPUT**: HTTP requests targeting `/query`, `/ready`, `/healthz`, `/metrics`.
* **OUTPUT**: Structured JSON responses (`QueryResponse`), Prometheus metrics, or sanitized error payloads.
* **WHY IT EXISTS**: Provides the RESTful HTTP API boundary, encapsulates request lifecycle orchestration, propagates correlation IDs, enforces stage deadlines, and ensures no internal stack traces leak to callers.

#### [`src/novastack/service/identity.py`](../src/novastack/service/identity.py)
* **PATH**: `src/novastack/service/identity.py`
* **PURPOSE**: Fail-closed cryptographic caller identity verifier.
* **INPUT**: HTTP `Authorization: Bearer <JWT>` header.
* **OUTPUT**: `VerifiedIdentity` dataclass containing verified `subject`, `tenant_id`, `roles`, and `departments`.
* **WHY IT EXISTS**: Completely eliminates reliance on untrusted client JSON for authentication. If a JWT signature, expiration, issuer, or audience is invalid, execution terminates immediately with HTTP 401.

#### [`src/novastack/service/resilience.py`](../src/novastack/service/resilience.py)
* **PATH**: `src/novastack/service/resilience.py`
* **PURPOSE**: Operational fault tolerance, concurrency gating, and deadline management.
* **INPUT**: Request execution signals and duration telemetry.
* **OUTPUT**: Concurrency leases, circuit breaker state transitions, and timeout deadline checks.
* **WHY IT EXISTS**: Protects downstream CPU and GPU inference providers from queue saturation, resource starvation, and cascading system outages.

#### [`src/novastack/service/schemas.py`](../src/novastack/service/schemas.py)
* **PATH**: `src/novastack/service/schemas.py`
* **PURPOSE**: Strongly typed Pydantic data contracts for external HTTP interfaces.
* **INPUT**: Incoming raw JSON request payloads.
* **OUTPUT**: Validated Pydantic models: `QueryRequest`, `QueryResponse`, `CitationPayload`, `TenantContext`.
* **WHY IT EXISTS**: Guarantees strict schema validation at the HTTP network boundary before any data reaches internal pipeline components.

---

### 3.2 Retrieval & Query Understanding

#### [`src/novastack/bm25.py`](../src/novastack/bm25.py)
* **PATH**: `src/novastack/bm25.py`
* **PURPOSE**: In-memory inverted lexical search engine.
* **INPUT**: Tokenized query string and optional metadata filters (`tenant_id`).
* **OUTPUT**: Ranked list of `SearchChunk` candidates with Okapi BM25 scores.
* **WHY IT EXISTS**: Guarantees deterministic, sub-millisecond exact-token matching for critical operational identifiers (`INC-NS-0001`, `v2.4.1`, error codes) that dense embeddings fail to capture.

#### [`src/novastack/dense.py`](../src/novastack/dense.py)
* **PATH**: `src/novastack/dense.py`
* **PURPOSE**: Semantic vector search engine.
* **INPUT**: Raw query string and optional metadata filters (`tenant_id`).
* **OUTPUT**: Ranked list of `SearchChunk` candidates with cosine similarity scores.
* **WHY IT EXISTS**: Provides semantic synonym matching and conceptual query understanding without requiring keyword overlap, executing in `< 45ms` on local CPU.

#### [`src/novastack/hybrid.py`](../src/novastack/hybrid.py)
* **PATH**: `src/novastack/hybrid.py`
* **PURPOSE**: Multi-channel retrieval orchestrator.
* **INPUT**: User query and retrieval execution configuration.
* **OUTPUT**: Multi-channel candidate lists from BM25, Dense, and Relational indexes.
* **WHY IT EXISTS**: Encapsulates parallel candidate retrieval across disparate index representations into a single unified Python interface.

#### [`src/novastack/relational_retrieval.py`](../src/novastack/relational_retrieval.py)
* **PATH**: `src/novastack/relational_retrieval.py`
* **PURPOSE**: Structured entity graph retriever.
* **INPUT**: Resolved entity identifiers (e.g., `checkout-service`).
* **OUTPUT**: Related incident postmortems, team ownership records, and deployment histories.
* **WHY IT EXISTS**: Enables structural graph traversals across relational organizational data that unstructured text search cannot navigate.

#### [`scripts/ret_eval_08_h5_1_experiment.py`](../scripts/ret_eval_08_h5_1_experiment.py)
* **PATH**: `scripts/ret_eval_08_h5_1_experiment.py`
* **PURPOSE**: H5.1 deterministic Query Understanding Overlay and Entity Resolver.
* **INPUT**: Raw user query string.
* **OUTPUT**: `QueryUnderstandingResult` with canonical entity mappings and expanded search query.
* **WHY IT EXISTS**: Bridges informal engineer shorthand (e.g., *"checkout timeout"*) to canonical system entities (`checkout-service`, `INC-NS-0001`), boosting Expected Entity Recall from 0.5182 to 0.7820 (+50.9%).

#### [`src/novastack/depth_fusion_ablation.py`](../src/novastack/depth_fusion_ablation.py)
* **PATH**: `src/novastack/depth_fusion_ablation.py`
* **PURPOSE**: Score fusion engine implementing Reciprocal Rank Fusion (`fuse_rrf_sum`).
* **INPUT**: Ranked candidate lists from BM25 and Dense search channels, smoothing constant $k=60$.
* **OUTPUT**: Single unified candidate list ordered by composite RRF scores.
* **WHY IT EXISTS**: Merges incompatible score distributions (unbounded BM25 floats vs. bounded cosine similarities $[-1, 1]$) based purely on ordinal rank positions.

#### [`src/novastack/metadata_reranker.py`](../src/novastack/metadata_reranker.py)
* **PATH**: `src/novastack/metadata_reranker.py`
* **PURPOSE**: Authority-weighted candidate reranking engine.
* **INPUT**: Fused candidate list and document metadata records.
* **OUTPUT**: Reranked candidates ordered by authority-multiplied relevance scores.
* **WHY IT EXISTS**: Prevents unapproved drafts or deceptive documents from displacing canonical postmortems by multiplying scores with verified authority tiers (Postmortem: 1.0, Spec: 0.9, Active Ticket: 0.7, Draft: 0.3).

---

### 3.3 Authorization, Evidence Resolution & Context Construction

#### [`src/novastack/evidence_resolution.py`](../src/novastack/evidence_resolution.py)
* **PATH**: `src/novastack/evidence_resolution.py`
* **PURPOSE**: Pre-Evidence Security Boundary and Evidence Assembly Engine.
* **INPUT**: Reranked candidates and verified caller identity context (`VerifiedIdentity`).
* **OUTPUT**: `EvidencePackage` containing strictly authorized, vetted evidence items.
* **WHY IT EXISTS**: Enforces the foundational thesis **RETRIEVAL ≠ AUTHORIZATION**. Purges foreign tenant data, checks role ACLs, and quarantines poisoned records *before* evidence enters the LLM prompt.

#### [`src/novastack/evidence.py`](../src/novastack/evidence.py)
* **PATH**: `src/novastack/evidence.py`
* **PURPOSE**: Typed dataclass definitions for evidence domain objects.
* **INPUT**: Raw document and candidate metadata.
* **OUTPUT**: `EvidenceItem`, `EvidencePackage`, and candidate lifecycle states.
* **WHY IT EXISTS**: Provides immutable, typed domain structures representing vetted evidence packages with deterministic identifiers (`[EVD-001]`).

#### [`src/novastack/context_budgeter.py`](../src/novastack/context_budgeter.py)
* **PATH**: `src/novastack/context_budgeter.py`
* **PURPOSE**: Token budget pruner and prompt context builder.
* **INPUT**: `EvidencePackage` and target token limits.
* **OUTPUT**: Formatted prompt context string wrapped inside `<evidence_data>` tags.
* **WHY IT EXISTS**: Prevents context window saturation and enforces Rule 4 untrusted data demarcation to neutralize direct and indirect prompt injection attacks.

---

### 3.4 Grounded Generation & Citation Verification

#### [`src/novastack/generation.py`](../src/novastack/generation.py)
* **PATH**: `src/novastack/generation.py`
* **PURPOSE**: Grounded answer generator and prompt protocol enforcement.
* **INPUT**: `EvidencePackage`, query string, and generation configuration.
* **OUTPUT**: `AnswerResult` containing generated answer text, citation tags, or explicit abstention.
* **WHY IT EXISTS**: Drives instruction-tuned Gemma 3 1B with strict prompt calibration to synthesize evidence-grounded answers, or abstains with `"Insufficient evidence..."` when factual support is lacking.

#### [`src/novastack/provider.py`](../src/novastack/provider.py)
* **PATH**: `src/novastack/provider.py`
* **PURPOSE**: `AnswerGeneratorProvider` protocol abstraction.
* **INPUT**: Generation invocation requests.
* **OUTPUT**: `AnswerResult` instances.
* **WHY IT EXISTS**: Decouples the application from a specific runtime, enabling transparent switching between in-process PyTorch CPU execution (Backend A), HTTP containerized inference (Backend B), and test deterministic providers.

#### [`src/novastack/citation_validator.py`](../src/novastack/citation_validator.py)
* **PATH**: `src/novastack/citation_validator.py`
* **PURPOSE**: Server-side C2 citation verification engine.
* **INPUT**: Emitted answer text and the authorized `EvidencePackage`.
* **OUTPUT**: Validated `Citation` objects with verification states (`VALID`, `HALLUCINATED`, `UNAUTHORIZED`).
* **WHY IT EXISTS**: Mathematically proves that every factual statement in the generated answer points back to a legitimate, authorized source chunk delivered in the caller's evidence package.

---

### 3.5 Canary Routing & Operational Observability

#### [`src/novastack/canary.py`](../src/novastack/canary.py)
* **PATH**: `src/novastack/canary.py`
* **PURPOSE**: Deterministic hash bucket canary router.
* **INPUT**: `tenant_id`, routing key, and canary traffic configuration.
* **OUTPUT**: Routing decision (`baseline` vs. `h5.1_candidate`).
* **WHY IT EXISTS**: Enables controlled, reproducible routing evaluation in the live HTTP path using pure SHA-256 hash bucket assignment (held at 0.0% traffic).

#### [`src/novastack/observability/metrics.py`](../src/novastack/observability/metrics.py)
* **PATH**: `src/novastack/observability/metrics.py`
* **PURPOSE**: Prometheus metric definitions and telemetry collectors.
* **INPUT**: Stage latency events, query counts, and security violation counters.
* **OUTPUT**: Prometheus exposition text via `/metrics`.
* **WHY IT EXISTS**: Provides real-time visibility into retrieval latencies, error distributions, and security boundary rejections.

#### [`src/novastack/observability/logging.py`](../src/novastack/observability/logging.py)
* **PATH**: `src/novastack/observability/logging.py`
* **PURPOSE**: Structured JSON logging and sensitive credential scrubber.
* **INPUT**: Application log events and query contexts.
* **OUTPUT**: Structured JSON log lines written to stdout.
* **WHY IT EXISTS**: Ensures auditability while scrubbing JWT signatures, authorization tokens, and confidential fields before writing to logs.

---

### 3.6 Automated Testing, Security & Live Scenarios

#### [`tests/security/test_red_team_harness.py`](../tests/security/test_red_team_harness.py)
* **PATH**: `tests/security/test_red_team_harness.py`
* **PURPOSE**: Certified red-team attack harness covering 9 attack categories (7.1 – 7.9).
* **INPUT**: Adversarial JWTs, prompt injection payloads, cross-tenant queries, and poisoned records.
* **OUTPUT**: 13 automated test assertions validating fail-closed defense.
* **WHY IT EXISTS**: Mathematically proves system defenses against token forgery, tenant spoofing, injection, and retrieval poisoning.

#### [`scripts/verify_live_scenarios.py`](../scripts/verify_live_scenarios.py)
* **PATH**: `scripts/verify_live_scenarios.py`
* **PURPOSE**: Live end-to-end FastAPI scenario verifier.
* **INPUT**: The 3 canonical demonstration scenarios (Incident, Cross-Tenant, Prompt Injection).
* **OUTPUT**: HTTP 200 execution verification and assertion output.
* **WHY IT EXISTS**: Proves that the actual FastAPI pipeline correctly answers, abstains, and neutralizes attacks against live authenticated HTTP requests.

#### [`ui/index.html`](../ui/index.html)
* **PATH**: `ui/index.html`
* **PURPOSE**: Zero-secret enterprise demonstration Single-Page Application.
* **INPUT**: User interactions and queries.
* **OUTPUT**: Rendered search results, evidence drawer, citations, and live health status.
* **WHY IT EXISTS**: Provides a clean, professional, truth-calibrated user interface for demonstrations without storing any signing secrets in client code.

#### [`ui/demo_tokens.json`](../ui/demo_tokens.json)
* **PATH**: `ui/demo_tokens.json`
* **PURPOSE**: Pre-signed isolated demonstration identities for 4 test personas.
* **INPUT**: Read by the UI to populate the persona switcher.
* **OUTPUT**: Valid JWT bearer tokens expiring January 2030 scoped strictly to `atlas-query-api`.
* **WHY IT EXISTS**: Eliminates browser-side token generation and secret leakage while enabling immediate local scenario demonstration.

#### [`.env.example`](../.env.example)
* **PATH**: `.env.example`
* **PURPOSE**: Environment configuration template.
* **INPUT**: Copied to `.env` during local setup.
* **OUTPUT**: Configured environment variables for identity, resilience, and providers.
* **WHY IT EXISTS**: Provides a clean, documented template matching pre-signed demo tokens without exposing production secrets.

---

## 4. Test Suite Organization

| Test Directory | Focus Area | Command |
| :--- | :--- | :--- |
| [`tests/test_frontend_integration.py`](../tests/test_frontend_integration.py) | Frontend security, zero-secret audit, token validity | `pytest tests/test_frontend_integration.py` |
| [`tests/test_phase_4t_identity_boundary.py`](../tests/test_phase_4t_identity_boundary.py) | JWT signature, clock skew, claim verification | `pytest tests/test_phase_4t_identity_boundary.py` |
| [`tests/test_canary_routing.py`](../tests/test_canary_routing.py) | Canary hashing, bucket boundaries, fail-closed | `pytest tests/test_canary_routing.py` |
| [`tests/test_live_http_canary.py`](../tests/test_live_http_canary.py) | Live HTTP canary routing and kill-switch | `pytest tests/test_live_http_canary.py` |
| [`tests/test_phase_4m_api_service.py`](../tests/test_phase_4m_api_service.py) | API contracts, status codes, error sanitization | `pytest tests/test_phase_4m_api_service.py` |
| [`tests/test_phase_4m_auth_fail_closed.py`](../tests/test_phase_4m_auth_fail_closed.py) | Tenant isolation and fail-closed ACL enforcement | `pytest tests/test_phase_4m_auth_fail_closed.py` |
| [`tests/test_citation_validator.py`](../tests/test_citation_validator.py) | C2 grounder, tag verification, tenant matching | `pytest tests/test_citation_validator.py` |
| [`tests/test_sec_ops02_network_contract.py`](../tests/test_sec_ops02_network_contract.py) | Loopback network binding (`127.0.0.1`) verification | `pytest tests/test_sec_ops02_network_contract.py` |
| [`tests/security/test_red_team_harness.py`](../tests/security/test_red_team_harness.py) | 9 certified red-team attack categories (7.1 – 7.9)| `pytest tests/security/test_red_team_harness.py` |
| [`scripts/verify_live_scenarios.py`](../scripts/verify_live_scenarios.py) | Live end-to-end FastAPI scenario execution | `python scripts/verify_live_scenarios.py` |

---

## 5. Architectural Invariants for Developers & Maintainers

When extending or maintaining ATLAS:
1. **Never bypass `_enforce_pre_evidence_security_boundary`**: Candidates must always be filtered server-side before touching model context.
2. **Never store signing secrets in client code**: The UI must remain zero-secret; use pre-signed demo tokens for demonstration.
3. **Preserve fail-closed semantics**: Missing configuration, expired tokens, or deadline violations must return HTTP 401, 403, or 504—never permissive access.
4. **Keep Ollama port 11434 private**: Inference endpoints must bind strictly to `127.0.0.1` and never be exposed to public networks.
5. **Never fabricate operational evidence**: If a test cannot be executed (e.g., SEC-OPS-03 remote knocking), document it honestly as `UNVERIFIED`.
