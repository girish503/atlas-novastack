# ATLAS — Project Context

## What is ATLAS?

**ATLAS** (Evidence-Grounded Enterprise Search Platform) is a
research-grade portfolio project that models a realistic enterprise
search system with security and provenance awareness.

It is **not** a "chat with PDFs" application.

ATLAS is designed to explore the core research question:

> How can an enterprise search system retrieve the right evidence
> from noisy, changing, permission-controlled, multi-source
> organisational data while minimising stale information, irrelevant
> context, unauthorised access, unsupported answers, prompt injection,
> retrieval poisoning, latency, cost, and unnecessary complexity?

## What is NovaStack?

**NovaStack** is a fictional enterprise used as the primary data
source for ATLAS.  It models a realistic company with ten departments:

Engineering · Product · Customer Support · Sales · Finance · HR ·
Security · DevOps · Legal · Operations

NovaStack is joined by two smaller tenants — **Orbital** and
**Pinecone** — that exist to test multi-tenant isolation.

## Current Project Phase

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Product and architecture | ✅ Complete |
| 1A | Dataset research and benchmark audit | ✅ Complete |
| 1B | Schema, scale, relationships, generation blueprint | ✅ Complete |
| 1C | NovaStack v0.1 deterministic dataset generator | ✅ Complete |
| 2A | Canonical ingestion & normalization layer | ✅ Complete |
| 2B | Semantic-aware document chunking | ✅ Complete |
| 2C | Retrieval index readiness & baseline audit | ✅ Complete |
| 3A | BM25 lexical retrieval baseline | ✅ Complete |
| 3B | Dense retrieval baseline | ✅ Complete |
| 4A | Hybrid retrieval & Reciprocal Rank Fusion | ✅ Complete |
| 4B-0 | Candidate coverage & ranking diagnostics | ✅ Complete |
| 4B-1 | Cross-encoder reranking baseline | ✅ Complete |
| 4C-0 | Query intent & failure taxonomy diagnostic | ✅ Complete |
| 4C-1 | Deterministic query understanding & candidate-coverage experiment | ✅ Complete |
| 4C-2 | Authority, lifecycle & provenance-aware ranking diagnostic | ✅ Complete |
| 4C-3 | Controlled metadata-aware reranking experiment | ✅ Complete |
| 4D-0 | Candidate-starvation root-cause diagnostic | ✅ Complete |
| 4D-0.1 | Candidate-starvation diagnostic reconciliation | ✅ Complete |
| 4D-1 | Candidate depth & fusion ablation experiment | ✅ Complete |
| 4D-2 | Structured entity & relational retrieval experiment | ✅ Complete |\n| 4E | Evidence assembly & evidence resolution | ✅ Complete |
| 4F | Grounded LLM answer generation & abstention experiment | ✅ Complete |
| 4F-1 | Targeted remediation: Failure taxonomy, citation completeness, partial answers & false abstention experiment | ✅ Complete |
| 4F-2 | Evidence context pruning & abstention calibration (A0: 10 items, A1: 3 items, A2: 5 items, A3: 7 items) | ✅ Complete |
| 4G-1 | Context-salience budgeting & document-diverse evidence selection (G0: Control, G1: Doc diversity, G2: Salience compression, G3: Adaptive density) | ✅ Complete |
| 4G-2 | Controlled model-capacity experiment (Local 3B Qwen 2.5 vs 1B Gemma Control on CPU; BF16 emulation bottleneck identified; G0 certified) | ✅ Complete |
| 4H-1 | Prompt instruction calibration for low-confidence grounded answering (Config A-Calibrated; +75.8% answer yield, 19/19 safety; rejected on 79.31% completeness) | ✅ Complete |
| 4H-2 | Short-answer citation resolution benchmark (C1 short-exact-match resolver; 100% precision, 82.76% completeness; rejected on >=90% gate) | ✅ Complete |
| **4H-3** | **Sentence-level citation resolution benchmark & production integration (C2 tiered resolver: legacy chunk -> short-exact -> sentence-level; 91.38% completeness, 100% precision, 19/19 safety; ACCEPTED FOR PRODUCTION)** | **✅ Complete** |
| 5A | Inference provider abstraction (`AnswerGeneratorProvider` protocol, `LocalHuggingFaceProvider`) | ✅ Complete |
| 5B | Quantized local provider characterization (GGUF Q4_K_M via Ollama / llama.cpp runtime) | ✅ Complete |
| 5C | Microservice inference boundary (FastAPI standalone service, Dockerfile, non-root user) | ✅ Complete |
| 5D | Containerized inference runtime validation (`atlas-inference-5d` on port 8001) | ✅ Complete |
| 5E | Dual-backend 120-case certification benchmark (Backend B rejected on Gate G3) | ✅ Complete |
| 5F | Q4_K_M negative-case failure forensics (bounded to G: Quantization + F: Runtime) | ✅ Complete |
| 5G | Controlled Layer 1S security abstention gate (39-case validation set, 4/4 failures recovered) | ✅ Complete |
| 5H | Full Backend B re-certification (120 cases, G1-G9 ALL PASS, Candidate Eligible) | ✅ Complete |
| 5I | Production promotion readiness review (14/14 operational gates PASS, Promotion-Ready) | ✅ Complete |
| **5J** | **Controlled production promotion & rollback certification (Backend B promoted to default; Backend A rollback certified)** | **✅ Complete** |
| **5K** | **Release freeze & production baseline certification (Frozen 0.4.14-rc1 baseline, 23/23 phases PASS, RELEASE-CANDIDATE-READY)** | **✅ Complete** |
| **5L** | **Independent release-candidate validation (22/22 steps PASS, 11/11 scorecard PASS, REPRODUCED-WITH-RUNTIME-VARIANCE)** | **✅ Complete** |
| **5M** | **Release packaging & deployment reproduction (18/18 gates PASS, 179 regression tests PASS, dist/ bundle certified, PASS)** | **✅ Complete** |
| **5N** | **Operational runbook & deployment certification (22 sections, 21 questions, 12/12 gates PASS, 207 regression tests, PASS)** | **✅ Complete** |
| **5O** | **Controlled incident & recovery certification (10 incident categories, 10.0s CB cooldown, runbook recovery, 225 tests, PASS)** | **✅ Complete** |
| **5P** | **Final production commissioning & release sign-off (18/18 gates PASS, 225+13 tests PASS, RC 0.4.14-rc1 COMMISSIONED WITH DOCUMENTED LIMITATIONS)** | **✅ Complete** |
| **0.5-M1** | **Multi-hop relational traversal ($d \le 3$) & structured temporal filtering (EXP-0.5-01 & EXP-0.5-02, 14/14 tests PASS, 0 security leaks, 0 regressions, MRR +11.7%; Verdict: ITERATE)** | **✅ Complete (Iterate)** |

### Milestone Progress

- **Milestone 1**: Organisational entity generation (Users, Teams, Customers, Services) — ✅ Complete
- **Milestone 2**: Ground-truth event layer (Events, Incidents, Deployments, PRs, Relationships) — ✅ Complete
- **Milestone 3**: Observational source record foundation (Models, rendering architecture, provenance) — ✅ Complete
- **Milestone 4A**: Core observational source corpus (433 event-related records) — ✅ Complete
- **Milestone 4B**: Background enterprise corpus (470 background records; 903 combined) — ✅ Complete
- **Milestone 4C**: Temporal noise, duplicates, versions & conflicting evidence (290 noise records; 1,193 combined) — ✅ Complete
- **Milestone 4D-1**: Security metadata & authorization test corpus (110 security records, 29 security fixtures; 1,303 combined) — ✅ Complete
- **Milestone 4D-2**: Indirect prompt injection & retrieval poisoning corpus (90 adversarial records, 20 attack fixtures; 1,393 combined) — ✅ Complete
- **Milestone 4E-1**: Evaluation dataset foundation (120 structured evaluation cases, taxonomy benchmark) — ✅ Complete
- **Phase 2A**: Canonical ingestion & normalization (1,393 SearchDocuments, deterministic pipeline) — ✅ Complete
- **Phase 2B**: Semantic-aware document chunking (1,663 SearchChunks, structural preservation, zero-join security) — ✅ Complete
- **Phase 2C**: Retrieval index readiness & baseline audit (formal contract, 100% eval resolvability, zero-defect baseline) — ✅ Complete
- **Phase 3A**: BM25 lexical retrieval baseline (in-memory inverted index, pre-scoring filtering, 120-case evaluation) — ✅ Complete
- **Phase 3B**: Dense retrieval baseline (BGE-small-en-v1.5, pre-scoring filtering, failure-overlap benchmark) — ✅ Complete
- **Phase 4A**: Hybrid retrieval & Reciprocal Rank Fusion (dual-channel RRF k=60, pre-scoring filtering, 3-way evaluation) — ✅ Complete
- **Phase 4B-0**: Candidate coverage & ranking diagnostics (82.2% union headroom, 10 RRF regressions diagnosed, security separation) — ✅ Complete
- **Phase 4B-1**: Cross-encoder reranking baseline (ms-marco-MiniLM-L-6-v2, 6-way ablation, H1 rejected due to distractor/poisoning sensitivity) — ✅ Complete
- **Phase 4C-0**: Query intent & failure taxonomy diagnostic (15 controlled intent dimensions, multi-system cross-tabulation, failure attribution) — ✅ Complete
- **Phase 4C-1**: Deterministic query understanding & candidate-coverage experiment (Entity catalog, alias expansion into BM25, H1 confirmed: R@5 +11%, R@10 +3.2%, MRR +1.6%, 3 candidate failures recovered) — ✅ Complete
- **Phase 4C-2**: Authority, lifecycle & provenance-aware ranking diagnostic (Metadata snapshot index, 7 experiments, Failure Taxonomy A–F, Metadata Oracle: R@5 +14.6%, R@10 +9.3%, MRR +5.6%, H2 confirmed for in-pool candidates) — ✅ Complete
- **Phase 4C-3**: Controlled metadata-aware reranking experiment (7-ablation study A-G, fixed weights, zero LLM, R@10: 0.4868 -> 0.5644 (+15.9%), MRR: 0.3469 -> 0.3911 (+12.7%), 0 forbidden leaks) — ✅ Complete
- **Phase 4D-0**: Candidate-starvation root-cause diagnostic (13-label taxonomy, 23 starvation cases, 7 counterfactual checks, depth scan: 30.4% recoverable at depth 100, 26.1% security exclusions, 17.4% benchmark defects) — ✅ Complete
- **Phase 4D-0.1**: Candidate-starvation diagnostic reconciliation (Resolved depth discrepancy: 7 channel vs 6 hybrid recoverable; EVAL-0076 fusion suppression diagnosed; 4-way partition: 0 deny exclusions, 4 GT defects, 1-3 fusion clipping, 12-18 genuine failures) — ✅ Complete
- **Phase 4D-1**: Candidate depth & fusion ablation experiment (D50/75/100, RRF/CombMAX/Interleaving, rank dilution demonstrated, RRF k=60 at D50 retained) — ✅ Complete
- **Phase 4D-2**: Structured entity & relational retrieval experiment (249 entities, 1,376 edges, 3-channel RRF k=60, Downstream R@3 +25.6%, R@5 +3.75%, R@10 +2.29%, 0 regressions, 0 security leaks) — ✅ Complete
- **Phase 4E**: Evidence assembly & evidence resolution (8-stage deterministic resolution engine, authorization gate, multi-channel dedup, adversarial quarantine, lifecycle/temporal validity, conflict resolution, EvidencePackage contract) — ✅ Complete
- **Phase 4F**: Grounded LLM answer generation & abstention experiment (google/gemma-3-1b-it, dual-layer abstention, greedy decoding reproducibility, citation validator, 0 security leaks, 12-category taxonomy) — ✅ Complete
- **Phase 4F-1**: Targeted remediation (Statistics key alignment, deterministic citation attachment: 58 citations / 100% precision / 77.8% completeness, semantic partial-answer classification, 3-config prompt ablation across 54 false abstentions) — ✅ Complete
- **Phase 4F-2**: Evidence context pruning & abstention calibration (A0: 10 items, A1: 3 items, A2: 5 items, A3: 7 items; A1 recovers 37.04% of Stage E false abstentions, answer rate 27.5%, 93.94% citation completeness, 100% safety on all 19 negative queries, 0 regressions) — ✅ Complete
- **Phase 4G-1**: Context-salience budgeting & document-diverse evidence selection (G0: Control, G1: Doc diversity, G2: Salience compression, G3: Adaptive density; G2 achieved 41 complete answers but triggered Lexical Salience Trap on EVAL-0054; G2/G3 rejected for production; G0/A1 retained) — ✅ Complete
- **Phase 4G-2**: Controlled model-capacity experiment (Qwen 2.5 3B vs Gemma 1B Control; evaluated gated access, CPU bfloat16 emulation bottleneck of 10.2s/tok, and RAM limits; 3B rejected for CPU production; G0 certified for production) — ✅ Complete
- **Phase 4H-1**: Prompt instruction calibration (Config A-Calibrated prompt ablation; 58 successful outcomes, 17/34 Stage-E recovery, 100% precision, 19/19 negative safety, zero regressions; citation completeness 79.31% diagnosed due to ultra-concise answers) — ✅ Complete
- **Phase 4H-2**: Short-answer citation resolution benchmark (C1 short-exact-match resolver; 2 short answers cited with 100% precision, 2 collisions safely refused, completeness 82.76%; rejected against >=90% gate, chunk denominator problem diagnosed) — ✅ Complete
- **Phase 4H-3**: Sentence-level citation resolution benchmark & production integration (C2 tiered citation resolver: Tier 1 legacy chunk-level -> Tier 2 C1 short-exact-match -> Tier 3 C2 sentence-level fallback; 91.38% citation completeness, 100% precision, 19/19 negative safety, 0 security leaks, 53 positive successes; certified as production default) — ✅ Complete
- **Phase 5A**: Inference provider abstraction (`AnswerGeneratorProvider` protocol, `LocalHuggingFaceProvider`) — ✅ Complete
- **Phase 5B**: Quantized local provider characterization (GGUF Q4_K_M via Ollama / llama.cpp runtime) — ✅ Complete
- **Phase 5C**: Microservice inference boundary (FastAPI standalone service, Dockerfile, non-root user) — ✅ Complete
- **Phase 5D**: Containerized inference runtime validation (`atlas-inference-5d` on port 8001) — ✅ Complete
- **Phase 5E**: Dual-backend 120-case certification benchmark (Backend B rejected on Gate G3) — ✅ Complete
- **Phase 5F**: Q4_K_M negative-case failure forensics (bounded to G: Quantization + F: Runtime) — ✅ Complete
- **Phase 5G**: Controlled Layer 1S security abstention gate (39-case validation set, 4/4 failures recovered) — ✅ Complete
- **Phase 5H**: Full Backend B re-certification (120 cases, G1-G9 ALL PASS, Candidate Eligible) — ✅ Complete
- **Phase 5I**: Production promotion readiness review (14/14 operational gates PASS, Promotion-Ready) — ✅ Complete
- **Phase 5J**: Controlled production promotion & rollback certification (Backend B promoted to default; Backend A rollback certified) — ✅ Complete
- **Phase 5K**: Release freeze & production baseline certification (0.4.14-rc1 frozen baseline, 23/23 phases PASS, RELEASE-CANDIDATE-READY) — ✅ Complete
- **Phase 5L**: Independent release-candidate validation (0.4.14-rc1 reproduced across 22/22 steps, 11/11 scorecard, REPRODUCED-WITH-RUNTIME-VARIANCE) — ✅ Complete
- **Phase 5M**: Release packaging & deployment reproduction (0.4.14-rc1 standalone bundle, 18/18 gates PASS, 179 regression tests PASS, PASS) — ✅ Complete
- **Phase 5N**: Operational runbook & deployment certification (0.4.14-rc1 standalone runbook docs/OPERATIONS_RUNBOOK.md, 12/12 gates PASS, 21 operator questions answered, 207 regression tests PASS, PASS) — ✅ Complete
- **Phase 5O**: Controlled incident & recovery certification (10 incident categories, 10.0s CB cooldown, safe abstention, runbook recovery, 225/225 regression tests, zero drift, PASS) — ✅ Complete




## Engineering Philosophy

- BUILD → MEASURE → FIND FAILURE → FORM HYPOTHESIS → CHANGE SYSTEM → MEASURE AGAIN → KEEP OR REJECT
- Every component must have a reason
- Every optimisation must have a measurable metric
- Every security mechanism must have an attack test
- Prefer simple, maintainable Python
- Must remain runnable for ₹0 using local/open-source components
- Do not optimise for producing lots of code — optimise for correctness, reproducibility, clarity, testability, maintainability

## Separation of Ground Truth from Source Records

A core architectural principle in ATLAS is the strict separation between:

1. **Ground Truth** (The objective reality): Exactly what occurred, what caused what, which component failed, which deployment triggered the failure, and how it was fixed.
2. **Source Records** (The observational evidence): Tickets, incident logs, chat messages, postmortems, documentation, and PR comments written by human engineers or automated systems.

In real enterprises, source records are imperfect: an initial Slack conversation may blame the wrong service; early support tickets might describe symptoms incorrectly; a postmortem might reflect consensus after hours of debugging. By establishing the ground truth first, ATLAS provides an indisputable benchmark against which retrieval, evidence assembly, and provenance reasoning can be rigorously evaluated.

## What Has Already Been Decided

- Deterministic generation with `RANDOM_SEED = 20260909`
- Lightweight Python dataclasses (no ORM / framework)
- Multi-tenancy from day one (three tenants)
- Incremental milestone-based implementation
- Configurable per-tenant entity distribution
- Separate JSON output files per entity type
- Authoritative ground-truth layer built prior to text/document generation

## What Is NOT Being Built Yet

- Natural-language documents and ticket text
- Slack conversations and meeting notes
- Postmortems and markdown runbooks
- Document chunking and embedding models
- Vector databases, BM25, hybrid search
- RAG pipelines, reranking, and LLM answer generation
- Agents, evaluation harness, observability
- Security attack cases and prompt injection tests
- Deployment infrastructure
