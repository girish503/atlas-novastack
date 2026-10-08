# ATLAS — Evidence-Grounded Enterprise Search Platform: Final Project Report

**Enterprise**: NovaStack  
**Repository**: `girish503/atlas-novastack`  
**Certified Release**: 0.4.14-rc1  
**Candidate Evaluation**: H5.1 Entity Resolution Candidate (`0.4.14-rc1+h5.1`)  
**Commit SHA**: `d325e5a82681456ebaca57f2f27c1900f17bd415`  
**Date**: October 8, 2026  
**Audience**: Principal AI Engineers, Systems Architects, Security & Reliability Leads  

---

## 1. Executive Summary

ATLAS is an evidence-grounded enterprise search and Retrieval-Augmented Generation (RAG) platform developed for NovaStack. The system provides high-precision search across heterogeneous enterprise operational records (incident postmortems, architecture RFCs, pull requests, deployments, runbooks, customer communications) while enforcing strict cross-tenant data isolation, fine-grained access control lists (ACLs), cryptographic provenance, and zero-hallucination citation validation.

Through a disciplined engineering process (**Build $\to$ Measure $\to$ Find Failure $\to$ Hypothesis $\to$ Change $\to$ Measure $\to$ Keep/Reject**), the platform has been completed and verified across all eight core systems without architectural sprawl or unnecessary framework bloat.

### Certified Milestones & Outcomes
- **Retrieval Upgrade (H5.1 Candidate)**: Expected entity recall improved from **0.5182 $\to$ 0.7820** (+50.91%), positive Recall@10 improved from **0.6716 $\to$ 0.7277**, MRR improved from **0.4804 $\to$ 0.5525**, and missing entity cases dropped from **23 $\to$ 0**, with zero wrong entities and zero security regressions.
- **Answerability Calibration (M8 Milestone)**: Positive answer yield reached **73.27%** (74/101), negative safety rate held at **100.0%** (19/19 correct abstentions), and citation precision achieved **100.0%** (122/122 valid citations) on local Gemma 3 1B IT inference.
- **Security Invariant Verification**: All nine mandatory attack categories (authentication, authorization, cross-tenant isolation, direct injection, indirect injection, retrieval poisoning, citation leakage, secret exfiltration, metadata attacks) passed 100% in automated red-team testing.
- **Operational Boundary Governance**: Inference exposure was remediated to local loopback `127.0.0.1:8001` and `127.0.0.1:11434` (SEC-OPS-02). Remote LAN ingress (SEC-OPS-03) remains explicitly recorded as **UNVERIFIED** due to single-machine environment constraints; production canary traffic is strictly held at **0.0%**.

---

## 2. Problem Statement

Modern enterprise knowledge retrieval suffers from four critical production failure modes:
1. **Context Dilution & False Refusal**: Small language models (e.g. 1B parameter range) struggle with long, noisy, multi-chunk contexts, frequently triggering false abstentions on legitimate queries.
2. **Hallucination & Fabricated Sourcing**: Standard RAG pipelines lack verifiable provenance, allowing models to generate plausible-sounding answers with non-existent or misattributed citations.
3. **Cross-Tenant & ACL Leakage**: Multi-tenant systems that rely on vector similarity alone risk exposing sensitive documents from other tenants or restricted departments in retrieved contexts.
4. **Adversarial Vulnerabilities**: Prompt injection embedded in documents (indirect injection) and self-declared authority in untrusted records (retrieval poisoning) can hijack system behavior.

ATLAS solves these challenges through deterministic pre-evidence authorization, targeted sentence-level evidence extraction, untrusted XML encapsulation, and post-generation C2 citation verification.

---

## 3. High-Level Architecture

ATLAS is structured around eight discrete, independently verifiable engineering systems:

```
                          Client Request (JWT)
                                   │
                                   ▼
             ┌──────────────────────────────────────────┐
             │ 1. Public API & Identity Verification   │ (FastAPI, HS256 JWT)
             └─────────────────────┬────────────────────┘
                                   │
                                   ▼
             ┌──────────────────────────────────────────┐
             │ 2. Context Consistency & Rate Limiting  │ (Tenant / ACL Check)
             └─────────────────────┬────────────────────┘
                                   │
                                   ▼
             ┌──────────────────────────────────────────┐
             │ 3. Query Intelligence & Expansion       │ (H5.1 Entity Resolver)
             └─────────────────────┬────────────────────┘
                                   │
                                   ▼
             ┌──────────────────────────────────────────┐
             │ 4. Hybrid Search Engine                 │ (BM25 + Dense + RRF)
             └─────────────────────┬────────────────────┘
                                   │
                                   ▼
             ┌──────────────────────────────────────────┐
             │ 5. Metadata-Aware Reranker              │ (Authority & Lifecycle)
             └─────────────────────┬────────────────────┘
                                   │
                                   ▼
             ┌──────────────────────────────────────────┐
             │ 6. Strict Evidence Resolution Engine    │ (8-Stage Boundary Filter)
             └─────────────────────┬────────────────────┘
                                   │
                                   ▼
             ┌──────────────────────────────────────────┐
             │ 7. Grounded AI Layer (Calibrated Prompt)│ (Gemma 3 1B GGUF)
             └─────────────────────┬────────────────────┘
                                   │
                                   ▼
             ┌──────────────────────────────────────────┐
             │ 8. Deterministic Citation Validator (C2) │ (Corpus & Usability)
             └─────────────────────┬────────────────────┘
                                   │
                                   ▼
                       Sanitized JSON Telemetry
```

---

## 4. Data Model & Corpus Hierarchy

The NovaStack corpus consists of 1,393 search documents and 3,124 search chunks:
- **SearchDocument**: Encapsulates document-level metadata (`document_id`, `tenant_id`, `source_type`, `title`, `author_id`, `department`, `classification`, `authority_level`, `status`, `version`, `created_at`, `permissions`).
- **SearchChunk**: Encapsulates block-level text (`chunk_id`, `document_id`, `chunk_index`, `total_chunks`, `text`, `source_entity_id`, `related_entity_ids`).
- **RecordPermissions**: Fine-grained ACL specification (`allowed_roles`, `allowed_departments`, `allowed_user_ids`, `allowed_teams`).
- **EvidenceItem**: Verified evidence node binding retrieved text to its provenance lineage, trust score, and status.
- **EvidencePackage**: Immutable structured payload passed to the grounded generator containing selected evidence, excluded items, conflict logs, and timing diagnostics.

---

## 5. Retrieval System

ATLAS utilizes a multi-channel hybrid retrieval pipeline:
1. **Lexical BM25 (`BM25Index`)**: Okapi BM25 implementation tuned for enterprise identifiers and error codes ($k_1=1.5, b=0.75$, title weight $2.0$).
2. **Dense Semantic (`DenseIndex`)**: 384-dimensional cosine similarity index powered by `sentence-transformers/all-MiniLM-L6-v2`.
3. **Structured Entity Retrieval (`StructuredRetriever`)**: Direct graph-based expansion linking services, incidents, teams, and pull requests.
4. **Reciprocal Rank Fusion (RRF)**: Combines multi-channel candidate lists with rank constant $k=60$:
   $$RRF(d) = \sum_{c \in Channels} \frac{1}{k + rank_c(d)}$$
5. **Metadata Reranker (`MetadataReranker`)**: Applies deterministic authority boosts (`CANONICAL` $+0.0030$, `HIGH` $+0.0015$) and lifecycle penalties (`STALE` $-0.0020$).

---

## 6. Query Understanding & Intelligence

Implemented in `src/novastack/query_understanding.py` and upgraded in `H5.1`:
- **Exact Identifier Recognition**: Matches formal identifiers (`INC-NS-XXXX`, `SVC-NS-XXXX`, `PR-NS-XXXX`).
- **Catalog Phrase Matching**: Case-insensitive matching across canonical service names, incident sequences, and deployment titles.
- **H5.1 Coverage Expansion**: Resolves synchronized service aliases, derived catalog phrases, and canonical token sequences.
- **Temporal & Lifecycle Constraints**: Parses ISO dates, relative windows (`during Q1 2025`, `late 2024`), and version modifiers (`latest`, `deprecated`).

---

## 7. Evidence Grounding & Context Building

Implemented in `src/novastack/evidence_resolution.py` and `evidence_extractor.py`:
- **8-Stage Resolution Pipeline**:
  - Stage 1: Candidate Ingestion & Lineage Binding
  - Stage 2: Strict Pre-Evidence Authorization Gate
  - Stage 3: Multi-Channel & Intra-Document Deduplication
  - Stage 4: Adversarial & Retrieval Poisoning Classification
  - Stage 5: Version & Lifecycle Resolution
  - Stage 6: Temporal Validity Window Check
  - Stage 7: Authority Resolution & Conflict Detection
  - Stage 8: Evidence Selection, Trust Scoring & Package Assembly
- **Targeted Sentence-Level Extraction**: Extracts key evidentiary sentences containing entity anchors, causal bridges, and negations, compressing context tokens by **19.46%** while preserving grounding facts.

---

## 8. Citation System & Validation

Implemented in `src/novastack/citation_validator.py` (C2 Validator):
- **Tag Extraction**: Extracts `[EVD-XXX]` and `[DOC-XXX]` tags from model outputs using regex.
- **Deterministic Validation Invariants**:
  1. Cited document must exist in processed corpus.
  2. Cited chunk must exist in processed corpus.
  3. Cited item must be present in the authorized `EvidencePackage`.
  4. Item status must be `ACCEPTED` or `ACCEPTED_WITH_CAVEAT`.
  5. If citation references excluded or unauthorized evidence, status is flagged as `UNAUTHORIZED` or `EXCLUDED`.
- **Zero Fabricated Citations**: Phantom tags (e.g. `[EVD-999]`) are mapped to `UNKNOWN` and cause answer rejection.

---

## 9. Authorization & Access Control

- **Pre-Evidence Filtering**: Authorization checks occur *before* evidence enters the LLM prompt. Unauthorized documents are physically withheld from prompt construction.
- **Fail-Closed ACL Checks**:
  - Missing role $\to$ Denied if document requires roles.
  - Role mismatch $\to$ Denied.
  - Department mismatch $\to$ Denied.
  - User ID mismatch $\to$ Denied.
- **Security Fixtures**: Explicit negative test fixtures and quarantined documents are unconditionally dropped.

---

## 10. Multi-Tenancy & Cross-Tenant Isolation

- **Tenant Scoping**: All documents, chunks, and entity catalog records are partitioned by `tenant_id`.
- **Identity Invariant**: The caller's tenant is established exclusively via cryptographically verified JWT claims (`tenant_id`). Client-provided body claims cannot override verified token claims.
- **Zero Leakage**: In both benchmark evaluation (120 queries) and red-team testing, cross-tenant candidate count in selected evidence was **0**.

---

## 11. Security Architecture & Threat Mitigations

- **Direct Prompt Injection**: Treated as standard user search string; model instructed via system prompt to treat user input as plain text.
- **Indirect Prompt Injection**: Document contents wrapped in `<evidence_data>` tags. System instructions command model to treat data as untrusted information.
- **Retrieval Poisoning**: Metadata authority (`CANONICAL`, `HIGH`, `DRAFT`) is stored in trusted index schema. Self-declared assertions in document body cannot elevate authority.
- **Exfiltration Resistance**: Prohibited keys (`token`, `secret`, `password`, `prompt`, `raw_query`) are stripped from logs; assignments matching credentials are redacted.

---

## 12. Evaluation Methodology

ATLAS uses a canonical evaluation harness (`scripts/eval/`):
- **Dataset**: `data/evaluation/novastack/evaluation_cases.json` (120 cases: 101 positive, 19 negative across 20 query categories).
- **Harness Architecture**: Unified runner (`scripts/run_canonical_eval.py`) coordinating 4 distinct tracks:
  1. Retrieval Benchmark (side-by-side Baseline vs Candidate)
  2. Grounded Generation Benchmark (certified M8 artifact analysis)
  3. Security Red-Team Evaluation (9 attack vectors)
  4. Performance & Latency Benchmark (p50, p95, p99 across stages)
- **Manifest Output**: Produces `artifacts/canonical_evaluation_manifest.json` with commit SHA and timestamps.

---

## 13. Retrieval Results (Baseline vs Candidate)

| Metric | Baseline (H5) | Candidate (H5.1) | Delta | Gate Status |
|---|---|---|---|---|
| Expected Entity Recall | 0.5182 | **0.7820** | +0.2638 | ✅ PASS |
| Wrong Entities | 0 | **0** | 0 | ✅ PASS |
| Missing Entity Cases | 23 | **0** | -23 | ✅ PASS |
| Recall@1 | 0.2748 | **0.3259** | +0.0511 | Informational |
| Recall@3 | 0.5083 | **0.5792** | +0.0709 | Informational |
| Recall@5 | 0.5652 | **0.6337** | +0.0685 | Informational |
| **Positive Recall@10** | 0.6716 | **0.7277** | +0.0561 | ✅ PASS |
| Precision@10 | 0.0980 | **0.1069** | +0.0089 | Informational |
| Hit@10 | 0.7030 | **0.7525** | +0.0495 | Informational |
| **MRR** | 0.4804 | **0.5525** | +0.0721 | Informational |
| **NDCG@10** | 0.4834 | **0.5499** | +0.0665 | Informational |
| Multi-Aspect Recall@10 | 0.8167 | **0.9000** | +0.0833 | ✅ PASS |
| H2 Slice Recall@10 | 0.6389 | **0.9167** | +0.2778 | ✅ PASS |
| Cross-Tenant Leaks | 0 | **0** | 0 | ✅ PASS |
| Unauthorized Leaks | 0 | **0** | 0 | ✅ PASS |

---

## 14. Grounded Generation Results

| Metric | Measured Result | Benchmark Gate |
|---|---|---|
| Positive Answer Yield | **73.27%** (74/101) | Target $\ge 70.0\%$ ✅ PASS |
| Negative Safety Rate | **100.0%** (19/19) | Target 100.0% ✅ PASS |
| Citation Precision | **100.0%** (122/122) | Target 100.0% ✅ PASS |
| Citation Completeness | **93.24%** (69/74) | Target $\ge 90.0\%$ ✅ PASS |
| Multi-Hop Recovery Slice | **77.78%** (14/18) | Target $\ge 13/18$ ✅ PASS |
| Protective Non-Regression | **100.0%** (EVAL-0054 & EVAL-0058 abstained) | Target 100.0% ✅ PASS |
| Unauthorized Citations | **0** | Target 0 ✅ PASS |
| Fabricated Citations | **0** | Target 0 ✅ PASS |

---

## 15. Security Red-Team Results

All 9 attack categories evaluated in `tests/security/test_red_team_harness.py` passed cleanly:
- 7.1 Authentication: 4/4 PASS
- 7.2 Authorization: 2/2 PASS
- 7.3 Cross-Tenant Isolation: 2/2 PASS
- 7.4 Direct Prompt Injection: 1/1 PASS
- 7.5 Indirect Prompt Injection: 1/1 PASS
- 7.6 Retrieval Poisoning: 1/1 PASS
- 7.7 Citation Leakage: 2/2 PASS
- 7.8 Secret Exfiltration: 2/2 PASS
- 7.9 Metadata Attacks: 1/1 PASS

---

## 16. Performance Results

- **Query Understanding Latency (H5.1)**: p50: **1.14 ms**, p95: **1.29 ms**, p99: **3.20 ms**.
- **Hybrid Retrieval Latency**: p50: **41.36 ms**, p95: **57.71 ms**, p99: **123.23 ms**.
- **Metadata Reranking Latency**: p50: **0.32 ms**, p95: **0.38 ms**, p99: **0.41 ms**.
- **Evidence Assembly Latency**: p50: **2.85 ms**, p95: **4.60 ms**, p99: **6.20 ms**.
- **Total In-Process Warm Latency (Pre-LLM)**: p50: **45.67 ms**, p95: **63.98 ms**.
- **Gemma 3 1B LLM Generation Latency (Local CPU)**: Mean: **13.36 s**, p50: **13.92 s**, p95: **21.84 s**.
- **Cold-Start Latency**: Dense embedding model page-in: **~35.27 s**.

---

## 17. Reliability & Resilience Results

- **Fail-Closed Security**: 100% of invalid authentication and context mismatch requests reject before execution.
- **Request Deadlines**: Enforced via `ATLAS_QUERY_DEADLINE_MS=45000` with zero benchmark timeouts.
- **Concurrency Bounds**: Protected via `BoundedConcurrencyLimiter` preventing memory exhaustion.
- **Circuit Breaking**: HTTP 503 fast fail-over on downstream container disconnection.

---

## 18. Observability & Telemetry

- **Structured JSON Logs**: Formatted via `StructuredJsonFormatter` producing single-line JSON records.
- **Tracing & Correlation**: Propagates `request_id` across API, pipeline, and container boundaries.
- **Data Privacy**: Strips sensitive keys (`password`, `secret`, `token`, `prompt`, `raw_query`) and redacts credential assignments (`key=...`, `bearer: ...`).

---

## 19. Canary Architecture & Rollback Controls

Implemented in `src/novastack/canary.py` and integrated into `api.py`:
- **Deterministic Hashing**: Routes traffic via SHA-256 hash of `tenant_id + request_id` modulo 100.
- **Dynamic Kill-Switch**: Atomic boolean flag reverts all requests to baseline authority instantly.
- **Pre-Routing Security**: Authentication, tenant validation, and rate limiting precede canary dispatch.
- **Current Authority**: Baseline `0.4.14-rc1` holds 100% authority. Canary traffic is set to **0.0%**.

---

## 20. Known Limitations

1. **CPU Inference Latency**: Gemma 3 1B IT generation takes ~12–16 seconds per query on CPU. Production scaling requires GPU acceleration.
2. **Dense Index Cold Start**: Loading sentence-transformers and numpy arrays into RAM requires ~35 seconds on cold start.
3. **Local Loopback Exposure**: In the development environment, local non-root users on `127.0.0.1` can access port 8001.

---

## 21. Explicitly Unverified Claims

- **SEC-OPS-03 (Remote LAN Ingress Verification)**: **UNVERIFIED**. Due to the absence of a secondary physical machine on the local network, remote port knocking against ports 8001 and 11434 could not be externally executed.
- **Live HTTP Production Traffic**: **UNVERIFIED**. The CanaryRouter infrastructure is verified via test clients; real production traffic has not been routed to H5.1 (traffic remains 0.0%).

---

## 22. Final Release Decision

**STATUS: PRODUCTION COMPLETE — CANARY READY**  
1. Baseline release `0.4.14-rc1` is certified as feature-complete, secure, and production-ready.
2. Candidate `H5.1` is certified as an offline-validated improvement ready for staged 5% canary promotion once an independent physical machine validates SEC-OPS-03 or an explicit security-owner waiver is granted.

---

## 23. Future Work

1. GPU acceleration (vLLM / TensorRT-LLM) for sub-second generation latency.
2. Multi-region deployment of the inference container runtime.
3. Automated continuous benchmark regression gates in CI/CD pipeline.
