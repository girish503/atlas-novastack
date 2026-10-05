# ATLAS 0.5 — Current-System Gap Analysis & Enterprise Use Case Evaluation

**Document ID**: `DOC-ATLAS-0.5-GAP-ANALYSIS`  
**Author**: CTO / Principal AI Systems Architect  
**Project**: ATLAS — Evidence-Grounded Enterprise Search Platform  
**Current Baseline**: `0.4.14-rc1` (`COMMISSIONED WITH DOCUMENTED LIMITATIONS`)  
**Date**: 2026-09-23  

---

## 1. Executive Summary

A critical responsibility of an engineering leader is distinguishing between **theoretical shortcomings** and **actual product bottlenecks**. Adding technology without empirical justification creates unmaintainable software.

In ATLAS 0.4.14-rc1, the security invariants, mechanical citation precision, and incident recovery boundaries are certified to a very high standard. However, our Phase 4I failure-budget reconciliation and Phase 5H benchmark data reveal clear functional ceilings:
- **Positive Answer Yield**: Only **62.4% (63/101)** of legitimate positive questions produce a cited answer in the promoted 0.4.14 backend; **37.6% (38/101)** result in false abstentions.
- **Root-Cause Distribution**: As established in Phase 4I, failures are not primarily model hallucinations. They stem from:
  1. *Generation Under-Confidence* (35.4%): 1B model abstains even when the target document is in the prompt.
  2. *Candidate Starvation* (22.9%): Target documents never enter the top-50 pool.
  3. *Evidence Selection & Suppression* (20.8%): Legitimate evidence is suppressed by rank cutoffs or rigid heuristics.
  4. *Context Pruning Cutoff* (10.4%): Target evidence is present in the package but discarded by the top-3 context window.
  5. *Relational Blind Spots*: Queries requiring $\ge 2$ hops (e.g. Service $\to$ Deployment $\to$ Incident $\to$ Customer) fail because relational traversal is artificially capped at depth $d=1$.

This document establishes the 18-dimension gap analysis and stress-tests ATLAS against realistic enterprise operational inquiries.

---

## 2. 18-Dimension Current-System Gap Analysis

| # | Dimension | Problem | Empirical Evidence | Current 0.4.14 Behavior | Impact | Proposed 0.5 Direction | Conf. | In 0.5? |
|:---|:---|:---|:---|:---|:---|:---|:---:|:---:|
| **1** | **Product Capability** | System is purely single-turn Q&A API with no conversational context or interactive search UI. | Single endpoint `POST /query`; no session management or web interface. | Stateless single-turn execution; returns JSON response only. | Operators and engineers must use curl or custom scripts; cannot drill down. | Add clean, standalone Operator Web Search & Inspection UI. | HIGH | **YES** |
| **2** | **Retrieval Quality** | Candidate starvation on vocabulary mismatches and specific entity questions. | Phase 4I Stage A: 11 cases (22.9% of failures) missed top-50 candidate pool entirely. | Static BM25 + BGE-small-en-v1.5 with static RRF fusion. | False abstentions on valid enterprise queries (e.g., `EVAL-0031`, `0069`). | Query expansion with learned entity-aware synonym routing. | HIGH | **YES** |
| **3** | **Evidence Quality** | Rigid additive metadata reranking suppresses valid evidence. | Phase 4I Stage B: 10 cases present in top-50 were excluded from top-10 package. | Fixed linear weights (+0.004/-0.004) regardless of query intent. | High-authority stale docs outrank recent operational tickets. | Intent-adaptive metadata weighting and confidence scoring. | MEDIUM| **YES** |
| **4** | **Security** | Symmetric JWT secret (HS256) limits enterprise federated identity. | `JwtIdentityVerifier` requires shared $\ge 32$B secret string in environment. | Validates HS256 HMAC tokens only; single issuer/audience. | Cannot integrate directly with enterprise IdPs (Okta, Azure AD, Keycloak). | Introduce OIDC / RS256 public key verification alongside HS256. | HIGH | **YES** |
| **5** | **Authorization** | Rigid role/department matching lacks attribute-based evaluation (ABAC). | Hardcoded checks: `allowed_roles`, `allowed_departments`, `allowed_teams`, `allowed_user_ids`. | Exact set intersection check in `EvidenceResolver`. | Cannot express fine-grained ABAC policies (e.g., on-call schedule active). | Defer dynamic ABAC; refine rule sets and auditability. | LOW | **DEFER** |
| **6** | **Data Freshness** | Zero support for real-time document updates or streaming ingestion. | Ingestion requires offline batch generation via `IndexManager`. | Monolithic index rebuild and full candidate generation validation. | New incidents, Slack discussions, and PRs are not searchable until batch indexing. | Add lightweight append-only delta index for hot updates. | MEDIUM| **YES** |
| **7** | **Versioning** | Temporal and version-aware queries suffer from date-parser limitations. | Queries with "before July 2025" or "FY24" rely on lexical markers rather than date intervals. | Regex token matching (`TEMPORAL_MARKERS`) with linear boost. | Fails on explicit date-range queries or historical point-in-time requests. | Formal ISO-8601 temporal interval extraction and filtering. | HIGH | **YES** |
| **8** | **Multi-Hop Reasoning** | Relational search strictly bounded to depth $d=1$. | `StructuredRetrieverConfig.max_traversal_depth = 1`; queries spanning 2+ hops fail. | Only direct entity match and immediate 1-hop neighbors retrieved. | Cannot answer questions connecting deployments $\to$ incidents $\to$ customer impact. | Multi-hop relational path traversal ($d \le 3$) with path budgeting. | HIGH | **YES** |
| **9** | **Search UX** | No explainability UI or visual evidence graph for operators. | Operators receive raw JSON payloads with bracketed citation IDs. | API returns `QueryResponse` with raw text and list of dicts. | High cognitive friction during incident triage; cannot visualize proof chain. | Interactive Evidence Graph & Provenance Viewer in Web UI. | HIGH | **YES** |
| **10**| **Evaluation** | 120-case evaluation suite is static and synthetic. | Fixed `evaluation_cases.json` created in Milestone 4E-1. | Evaluates only pre-defined 120 synthetic cases; no dynamic drift testing. | Risk of benchmark overfitting; does not capture production query variance. | Expand benchmark with 50 hard multi-hop & temporal challenge cases. | HIGH | **YES** |
| **11**| **Observability** | No telemetry on citation utility or operator query satisfaction. | Prometheus metrics capture latency, counter, circuit state; no user feedback loop. | Standard metrics emitted; no feedback endpoint. | Engineering cannot identify which answers were helpful or incorrect. | Add `/query/feedback` endpoint with correlation tracing. | MEDIUM| **YES** |
| **12**| **Reliability** | Production circuit breaker blocks entire service on single backend failure. | 3 consecutive failures trip breaker to `OPEN` for all subsequent callers. | Monolithic circuit breaker protecting single provider. | Rollback to Backend A requires operator intervention or factory restart. | Automatic fallback routing from Backend B to Backend A on circuit trip. | HIGH | **YES** |
| **13**| **Performance** | Cold prompt evaluation on CPU takes $\sim 22$s; warm latency is $\sim 5.1$s. | Measured in Phase 5B and 5O under Intel Core i3-N305 class CPU. | Synchronous inference call with full prompt evaluation per query. | Unacceptable latency during high-stress incident triage. | Implement KV prefix caching and prompt token optimization. | HIGH | **YES** |
| **14**| **Scalability** | Concurrency strictly bound to 1 (`max_concurrent_inferences=1`). | Any second concurrent request is rejected with HTTP 429 after 0.5s. | Hard semaphore lock in `InferenceConcurrencyLimiter`. | Single-user bottleneck; cannot support simultaneous incident responders. | Characterize bounded concurrency ($N=2$ to $4$) with CPU core affinity. | MEDIUM| **YES** |
| **15**| **Deployment** | Host Ollama daemon runs as unmanaged detached background process. | Runbook requires manual `powershell -Command "ollama serve"`. | Process lifecycle not supervised by Docker or systemd. | Host process crashes require manual operator intervention. | Fully compose application and inference daemons via standard orchestration. | MEDIUM| **YES** |
| **16**| **Developer Experience**| Local testing requires active Docker and running Ollama daemon. | Test suite requires mock overrides or active port 8001/11434 bindings. | Tests skip or fail if ports are unmapped. | New contributors face high barrier to running complete integration suite. | Hermetic mock test harness with zero external runtime requirements. | HIGH | **YES** |
| **17**| **Cost** | Local CPU inference is computationally expensive on host cores. | 100% CPU utilization across available cores during inference generation. | CPU threads saturated during generation. | Can starve co-located services on small virtual machines. | CPU thread capping (`OMP_NUM_THREADS` / `--threads 4`). | HIGH | **YES** |
| **18**| **Maintainability**| Monolithic `api.py` (965 lines) and duplicated provider logic. | `src/novastack/service/api.py` bundles routing, resilience, auth, pipeline execution. | High cyclomatic complexity in API coordinator. | Risk of unintended regressions during minor maintenance. | Refactor `api.py` into modular route handlers without changing interfaces. | MEDIUM| **DEFER** |

---

## 3. Real Enterprise Use Case Breakdown

To evaluate where ATLAS 0.4.14-rc1 breaks down in real enterprise operations, we analyze 10 canonical enterprise scenarios:

### Scenario 1: Multi-Hop Incident Root-Cause & Attribution
- **Enterprise Query**: *"Which customer accounts were impacted by the deployment that triggered incident INC-NS-0001, and who approved the pull request?"*
- **Required Path**: `INC-NS-0001` $\xrightarrow{\text{triggered_by}}$ `DEP-NS-0001` $\xrightarrow{\text{implements}}$ `PR-NS-0001` $\xrightarrow{\text{reviewed_by}}$ `USR-0042` **AND** `INC-NS-0001` $\xrightarrow{\text{affects}}$ `SVC-0001` $\xrightarrow{\text{impacts}}$ `CUST-0012`.
- **0.4.14 Behavior**: **FAILS**. `StructuredRetriever` is capped at $d=1$. It retrieves entities directly connected to `INC-NS-0001` (the deployment and the service), but cannot reach the PR reviewer ($d=2$) or the customer ($d=2$). The model receives incomplete evidence and safely abstains.
- **0.5 Solution**: Dynamic Multi-Hop Graph Traversal ($d \le 3$) with path-guided evidence expansion.

### Scenario 2: Temporal Point-in-Time Policy Lookup
- **Enterprise Query**: *"What was NovaStack's corporate travel reimbursement per diem in late 2024 before the FY25 policy revision took effect?"*
- **Required Path**: Match `DOC-POL-0001`, inspect `valid_from` (`2024-01-01`) and `valid_until` (`2024-12-31`), ignore newer `DOC-POL-0002` (`valid_from`: `2025-01-01`).
- **0.4.14 Behavior**: **FAILS** (e.g. `EVAL-0069`). The retrieval engine relies on regex token matching for "before", but the `MetadataReranker` applies a hardcoded recency and active boost that actively penalizes the 2024 document in favor of the 2025 document.
- **0.5 Solution**: Structured Temporal Filter parsing that evaluates date bounds before rank scoring.

### Scenario 3: Real-Time Incident Triage & Live Evidence
- **Enterprise Query**: *"What is the latest status of the active database connection pool outage reported in Slack 10 minutes ago?"*
- **Required Path**: Ingest real-time chat/ticket snippet, index into active search space, query immediately.
- **0.4.14 Behavior**: **FAILS**. ATLAS 0.4.14 is an offline batch-indexed system. Ingesting new records requires running a complete dataset generation and atomic index swap via `IndexManager`.
- **0.5 Solution**: Append-Only Delta Index Generation that allows memory-buffered live updates without rebuilding the baseline index.

### Scenario 4: Engineering Service Ownership & On-Call Lookup
- **Enterprise Query**: *"Who owns `notification-service`, what department is it in, and who is currently on-call?"*
- **Required Path**: `SVC-0005` $\xrightarrow{\text{owned_by}}$ `TEAM-0003` $\xrightarrow{\text{part_of}}$ `DEPT-0001`.
- **0.4.14 Behavior**: **PARTIALLY PASSES**. Directly queries the entity catalog for service ownership ($d=1$). However, if the ownership information was updated in an informal runbook note rather than the canonical catalog, candidate starvation occurs (e.g. `EVAL-0031`).
- **0.5 Solution**: Entity-Alias Synonyms and Hybrid Lexical Expansion.

### Scenario 5: Conflicting Evidence & Incident Postmortem Rebuttal
- **Enterprise Query**: *"Did the checkout outage originate from third-party Cloudflare CDN routing or internal database pool exhaustion?"*
- **Required Path**: Triage notes claim Cloudflare (low authority); official postmortem `DOC-PM-EVT-NS-0001` proves pool exhaustion (authoritative).
- **0.4.14 Behavior**: **PASSES**. `EvidenceResolver` successfully detects the authority conflict, elevates the postmortem, and penalizes the speculative Slack note. Gemma 3 1B accurately answers with citations to the postmortem.
- **Current Strength**: Authority-weighted conflict resolution is an established, validated capability of ATLAS.

### Scenario 6: Cross-Tenant Information Boundary Probe
- **Enterprise Query**: *"Show me the database encryption keys and tenant configuration for Orbital."* (Invoked by caller from `TENANT-NOVASTACK`).
- **Required Path**: Reject at query filter level or abstain.
- **0.4.14 Behavior**: **PASSES WITH 100% SECURITY**. Pre-scoring tenant partitioning drops Orbital candidates; Layer 1S forces immediate abstention; 0 tokens leaked.
- **Current Strength**: Multi-tenant isolation is battle-tested and non-bypassable.

### Scenario 7: Broad Multi-Document Architecture Synthesis
- **Enterprise Query**: *"Summarize the complete security architecture across our identity provider, data encryption standards, network firewall rules, and container runtime policies."*
- **Required Path**: Synthesize facts across 4 distinct documents (`DOC-SEC-001`, `DOC-SEC-002`, `DOC-SEC-003`, `DOC-SEC-004`).
- **0.4.14 Behavior**: **FAILS**. `AdaptiveContextBudgeter` strictly caps the prompt at **Top-3 evidence items** (`max_evidence_items=3`). The 4th document is dropped before reaching the LLM, producing an incomplete answer or false abstention.
- **0.5 Solution**: Salience-Budgeted Evidence Compaction, extracting relevant sentences rather than full raw passages to fit 5–7 documents into the same token budget.

### Scenario 8: High-Stress Simultaneous Incident Response
- **Enterprise Query**: 3 engineers simultaneously query ATLAS for incident runbooks and diagnostic commands during a Sev-1 outage.
- **Required Path**: Serve all 3 queries concurrently within $<10$ seconds.
- **0.4.14 Behavior**: **FAILS CRITICALLY**. Query 1 acquires the semaphore. Query 2 waits 0.5s and is shed with HTTP 429. Query 3 is immediately shed with HTTP 429. Only 1 engineer receives an answer; the others get rate-limited.
- **0.5 Solution**: Bounded Multi-Worker Inference ($N=2$ to $4$) or Read-Through Response Caching for identical incident queries.

### Scenario 9: Operator Evidence Verification & Proof Inspection
- **Enterprise Query**: Operator receives an answer claiming a specific root cause and wants to inspect the exact sentences and timestamps supporting the claim.
- **Required Path**: Click through from the generated answer to highlighted passage spans with verified provenance metadata.
- **0.4.14 Behavior**: **FAILS UX**. ATLAS returns JSON with citation tags (`[EVD-001]`). The operator must manually parse chunk IDs and look up raw files on disk.
- **0.5 Solution**: Web-based Operator Search UI with inline provenance inspection and highlighted evidence snippets.

### Scenario 10: Automatic Failure Resiliency Under Model Degradation
- **Enterprise Query**: Host Ollama daemon encounters memory pressure and begins timing out ($>30$s).
- **Required Path**: Circuit breaker trips and automatically degrades gracefully to rollback Backend A (`LocalHuggingFaceProvider`) to maintain search availability.
- **0.4.14 Behavior**: **FAILS AUTOMATIC RESILIENCY**. The circuit breaker trips to `OPEN` and fast-fails all incoming queries with HTTP 503 for 10 seconds. It does not automatically reroute traffic to the rollback backend.
- **0.5 Solution**: Autonomous Circuit-Tripped Fallback to Certified Rollback Backend.
