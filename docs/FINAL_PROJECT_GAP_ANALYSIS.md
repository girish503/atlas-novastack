# ATLAS — Final Project Gap Analysis & Engineering Inventory

**Document ID**: `DOC-ATLAS-FINAL-GAP-ANALYSIS`  
**Author**: Principal AI Engineer / Staff Engineer  
**Project**: ATLAS — Evidence-Grounded Enterprise Search Platform  
**Enterprise Target**: NovaStack  
**Baseline Authority**: `0.4.14-rc1` (Frozen Baseline: `B4 + H1(H5) + H3`)  
**Candidate Variant**: `0.4.14-rc1+h5.1` (H5.1 Query Understanding & Entity Resolution Overlay)  
**Date**: 2026-10-08  
**Repository**: `girish503/atlas-novastack`  
**Git Commit SHA**: `d325e5a82681456ebaca57f2f27c1900f17bd415`

---

## 1. Executive Summary & Inventory Overview

This document presents the authoritative, evidence-backed inventory of the ATLAS enterprise search engine. It audits what is concrete and executing in code versus what is offline, simulated, unverified, or broken.

### Authoritative Architecture Summary (8 Core Systems)

1. **Enterprise Search Engine**: Hybrid multi-channel lexical (BM25 with token-weighting) + dense semantic (BGE-small-en-v1.5 embeddings) + structured relationship retrieval (`StructuredRetriever`), fused with Reciprocal Rank Fusion (RRF $k=60$) and metadata-boosted reranking (`MetadataReranker`).
2. **Query Intelligence**: Deterministic entity catalog (`EntityCatalog`), exact identifier recognition, alias mapping, phrase normalization, and canonical coverage expansion (`H5.1EntityResolver`).
3. **Evidence Engine**: Grounded evidence extraction (`EvidenceExtractor`), multi-stage boundary enforcement (`EvidenceResolver`), role planning, sentence-level anchor preservation, and deterministic citation binding (`CitationValidator`).
4. **Authorization & Security Layer**: JWT cryptographic verification (`JwtIdentityVerifier`), context mismatch detection, tenant-isolated candidate scoping, and fail-closed security boundary.
5. **Grounded AI Layer**: Containerized Ollama backend (`gemma3:1b` 815MB Q4_K_M GGUF), calibrated prompt dispatching (`config_b_calibrated_safe`), and strict refusal on absent or protective evidence.
6. **Evaluation & Verification Harness**: 120 canonical multi-intent test cases (`evaluation_cases.json`) covering direct lookups, multi-hop incidents, security boundaries, and adversarial injection scenarios.
7. **Security & Red-Team Verification Layer**: Comprehensive suites for direct prompt injection, indirect prompt injection, retrieval poisoning, cross-tenant leaks, and credential sanitization.
8. **Production & Reliability Layer**: Standalone inference microservice (`:8001`), live Canary Router (`CanaryRouter`), concurrency limiter, circuit breaker, structured JSON logging, and Prometheus exposition.

---

## 2. Comprehensive Gap Analysis Table

| Area | Status | Evidence | Missing | Priority |
| :--- | :--- | :--- | :--- | :---: |
| **1. Lexical Search (BM25)** | **VERIFIED** | `src/novastack/bm25.py`, `tests/test_ret_eval_01_baseline.py` | None | P4 |
| **2. Dense Retrieval (Embeddings)** | **VERIFIED** | `src/novastack/dense.py`, `data/processed/novastack/dense_embeddings.npz` | Automated warm-up daemon on boot | P3 |
| **3. Structured / Relational Retrieval** | **VERIFIED** | `src/novastack/relational_retrieval.py`, `tests/test_relational_retrieval.py` | Multi-hop beyond depth $d=2$ (deferred to 0.5) | P3 |
| **4. Reciprocal Rank Fusion (RRF)** | **VERIFIED** | `src/novastack/depth_fusion_ablation.py` ($k=60$) | None | P4 |
| **5. Metadata Reranking** | **VERIFIED** | `src/novastack/metadata_reranker.py`, `tests/test_reranker.py` | None | P4 |
| **6. Query Understanding (Baseline H5)** | **VERIFIED** | `src/novastack/query_understanding.py`, `tests/test_query_understanding.py` | Coverage for complex multi-token phrases | P2 |
| **7. Query Understanding (H5.1 Candidate)** | **VERIFIED** | `scripts/ret_eval_08_h5_1_experiment.py`, `tests/test_ret_eval_08_h5_1_experiment.py` | Real production traffic authorization (holds 0%) | P2 |
| **8. Evidence Resolution Boundary** | **VERIFIED** | `src/novastack/evidence_resolution.py` (8-stage filter) | None | P0 |
| **9. Grounded Answer Generation** | **VERIFIED** | `src/novastack/generation.py`, `tests/test_phase_05_m8_answerability.py` | GPU acceleration (currently CPU-bound) | P3 |
| **10. Citation Verification (C2)** | **VERIFIED** | `src/novastack/citation_validator.py`, `tests/test_short_answer_citation.py` | None | P0 |
| **11. JWT Authentication Boundary** | **VERIFIED** | `src/novastack/service/identity.py`, `tests/test_phase_4t_identity_boundary.py` | RS256 enterprise IdP support (HS256 only) | P3 |
| **12. Tenant Isolation** | **VERIFIED** | `tests/test_phase_4t_identity_boundary.py`, `tests/test_live_http_canary.py` | None | P0 |
| **13. Document ACL & RBAC** | **VERIFIED** | `src/novastack/models.py`, `tests/test_security_corpus.py` | ABAC dynamic attribute policies | P4 |
| **14. Prompt Injection Defense** | **VERIFIED** | `src/novastack/evidence_resolution.py`, `tests/test_security_corpus.py` | Formal continuous red-team harness script | P1 |
| **15. Indirect Injection Defense** | **VERIFIED** | Untrusted document content demarcation in generation prompt | Automated adversarial test suite | P1 |
| **16. Retrieval Poisoning Defense** | **VERIFIED** | Metadata authority validation; self-declared authority rejected | Automated adversarial test suite | P1 |
| **17. Cross-Tenant Leak Defense** | **VERIFIED** | Strict filtering in BM25, Dense, and Structured retrieval layers | Unified security test runner | P1 |
| **18. Telemetry & Log Sanitization** | **VERIFIED** | `src/novastack/observability/`, `tests/test_live_http_canary.py` | None | P0 |
| **19. Canary Routing Engine** | **VERIFIED** | `src/novastack/canary.py`, `tests/test_canary_routing.py` | None | P1 |
| **20. Real HTTP Canary Integration** | **VERIFIED** | `src/novastack/service/api.py`, `tests/test_live_http_canary.py` | Real organic user traffic (holds 0%) | P2 |
| **21. Zero-Downtime Kill-Switch** | **VERIFIED** | `CanaryRouter.route_request` live env override check | None | P0 |
| **22. Host Port Exposure (SEC-OPS-02)** | **VERIFIED** | Ports 8001 & 11434 bound exclusively to `127.0.0.1` | None | P0 |
| **23. Remote Ingress Probe (SEC-OPS-03)** | **UNVERIFIED** | Host inspection confirmed loopback; secondary machine absent | Physical external machine validation | P1 |
| **24. Canonical Eval Harness** | **PARTIALLY VERIFIED** | Dispersed across 6 distinct evaluation scripts in `scripts/` | Unified single-entrypoint test harness | P1 |
| **25. Standalone Deterministic Demo** | **MISSING** | Fragmented across test scripts and manual instructions | End-to-end runnable showcase script | P1 |
| **26. Release SHA256 Integrity Manifest** | **BROKEN** | `test_sha256_matches_for_critical_source_files` failing on `api.py` | Updated SHA-256 manifest post-canary integration | P1 |
| **27. Source Tree Git Cleanliness Gate** | **BROKEN** | `test_git_status_clean_on_src` failing due to untracked `canary.py` | Commit or bundle changes cleanly | P1 |

---

## 3. Detailed Forensic Findings (A through J)

### A. What is Actually Implemented?
- Complete hybrid search engine: BM25, Dense vectors (BGE-small-en-v1.5), Relational graph retrieval, and RRF fusion ($k=60$).
- Grounded generation pipeline using Ollama `gemma3:1b` with fail-closed prompt boundaries and sentence-level C2 citation validation.
- FastAPI query service with JWT HS256 authentication, caller context consistency checks, concurrency limits, and circuit breaker.
- Explicit `CanaryRouter` integrated into `AtlasServicePipeline` with live environment-variable kill-switch.

### B. What is Actually Tested?
- 1,183 unit and integration tests passing out of 1,185 total.
- Fast tests covering models, chunking, indexes, rerankers, evidence resolution, and API security.
- Live HTTP tests (`tests/test_live_http_canary.py`) verifying 11 end-to-end FastAPI `/query` scenarios under signed JWTs.

### C. What is Only Documented?
- Remote external ingress verification (`SEC-OPS-03`): Documented as required, but empirically `UNVERIFIED` due to lack of a secondary physical machine on the network.
- 5% production traffic canary: Was previously claimed in an offline harness; now correctly clarified and held at 0%.

### D. What is Simulated?
- Fast generator fixtures (`FastDeterministicTestGenerator`, `MockGenerator`) in unit tests simulate LLM inference to maintain sub-minute test runtimes.
- Real model inference is validated separately via standalone integration tests (`tests/test_phase_5c_inference_service.py`).

### E. What is Production-Path Verified?
- The full request path: `POST /query` $\to$ JWT auth $\to$ context check $\to$ concurrency limiter $\to$ `AtlasServicePipeline` $\to$ `CanaryRouter` $\to$ query extractor $\to$ hybrid retrieval $\to$ reranker $\to$ evidence boundary $\to$ generation $\to$ citation validator $\to$ telemetry logging.

### F. What is Offline-Only?
- Retrieval benchmark suites: `scripts/eval_retrieval_baseline.py` and `scripts/ret_eval_08_h5_1_experiment.py` operate on in-memory pipelines without HTTP overhead.

### G. What is Missing?
1. **Unified Evaluation Harness**: There is currently no single script that executes retrieval, generation, security red-teaming, and latency benchmarking under one command.
2. **Canonical Security Red-Team Test Suite**: Direct prompt injection, indirect prompt injection, retrieval poisoning, and cross-tenant exfiltration are scattered across datasets rather than run as an explicit automated red-team gate.
3. **Deterministic End-to-End Demo**: A clean, single-command operator/executive demo showing normal answering, citation verification, security refusal, and injection neutralization.

### H. What is Broken?
1. `tests/test_phase_5l_independent_validation.py::TestStep03SHA256::test_sha256_matches_for_critical_source_files`: Fails because `src/novastack/service/api.py` was legitimately modified during RET-EVAL-09 canary integration and differs from the frozen Phase 5K hash.
2. `tests/test_ret_eval_03_h1_experiment.py::TestGate06SourceImmutability::test_git_status_clean_on_src`: Fails because `src/novastack/service/api.py` and `src/novastack/service/schemas.py` have working-tree modifications.

### I. What is Duplicated?
- Multiple evaluation scripts in `scripts/` duplicate dataset loading, query extraction, and metric calculation routines.

### J. What Can Be Removed / Simplified?
- Ad-hoc probe scripts in `scripts/` (e.g. `test_pipeline_0044.py`, `inspect_eval44.py`) can be archived into test fixtures or superseded by the unified evaluation runner.

---

## 4. Prioritized Engineering Action Plan

1. **[P0/P1] Build Unified Evaluation & Security Harness**:
   Create `scripts/eval/runner.py` and `scripts/run_canonical_eval.py` integrating:
   - Retrieval metrics (Recall@K, MRR, NDCG@10, entity recall)
   - Generation metrics (answer yield, abstention rate, citation precision)
   - Security red-team attacks (auth fail-closed, cross-tenant isolation, prompt injection, retrieval poisoning)
   - System latency (p50, p95, p99)
2. **[P1] Build Dedicated Security Red-Team Test Suite**:
   Create `tests/security/test_red_team_harness.py` covering all 9 attack vectors from Section 7.
3. **[P1] Build Canonical End-to-End Demo**:
   Create `scripts/demo_scenario.py` demonstrating full lifecycle query processing, security denial, and prompt injection defense.
4. **[P1] Reconcile Source Immutability & SHA256 Integrity**:
   Address `test_sha256_matches_for_critical_source_files` and `test_git_status_clean_on_src` so that the entire test suite passes 100%.
5. **[P1] Generate Final Project Deliverables**:
   Produce `docs/FINAL_ACCEPTANCE_CRITERIA.md`, `docs/FINAL_PROJECT_REPORT.md`, `docs/FINAL_SECURITY_REPORT.md`, and `docs/FINAL_EVALUATION_REPORT.md`.
