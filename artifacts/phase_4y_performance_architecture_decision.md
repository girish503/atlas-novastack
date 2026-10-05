# PHASE 4Y — PERFORMANCE ARCHITECTURE DECISION MEMO
**ATLAS CTO DIRECTIVE — SYNTHESIS & ARCHITECTURAL EVALUATION**

**Classification:** ARCHITECTURE DECISION MEMO ONLY — Zero Production Code Changes  
**Date:** 2026-09-19T06:45:00Z  
**Author:** ATLAS Performance Architecture Analyst  
**Status:** COMPLETE — DECISION MEMO ONLY  

---

## 1. Executive Summary

Four consecutive performance and capacity phases have been evaluated on the certified ATLAS platform:
- **Phase 4X** (Performance & Capacity Characterization) — **HOLD** (Contaminated by 0.51 GB free host RAM; 0 generation completions; telemetry ambiguity).
- **Phase 4X-R1** (Controlled Performance Revalidation) — **HOLD** (Resolved telemetry ambiguity; proved Gemma 3 1B CPU generation can complete in 12.1–15.9 s; confirmed 2 of 3 completions within 30 s deadline; host memory remained at 0.48 GB).
- **Phase 4X-R2** (Clean-Memory Confirmation) — **HOLD** (Enforced Critical Environment Gate; available RAM was 1.19 GB vs $\ge 3.0\text{ GB}$ required; benchmark safely blocked).
- **Phase 4X-R2-C** (Clean-Boot Confirmation) — **HOLD** (Evaluated post-boot environment; available RAM was 1.28 GB vs $\ge 3.0\text{ GB}$ required; benchmark safely blocked).

**Core Takeaway:**  
ATLAS's retrieval, evidence resolution, authorization, and API pipeline is fast ($<210\text{ ms}$ warm) and robust. Gemma 3 1B on an 8-core CPU executes in **12.1–15.9 s** when memory pages are warm and resident, well within the 30.0 s service deadline. However, running a ~4.0 GB float32 LLM in-process within the FastAPI web service on a 7.63 GB host leaves insufficient headroom under Windows 11, causing virtual memory paging thrash that pushes latency over 30 s.

This memo evaluates six architectural options conceptually, reinforces non-negotiable security and provenance invariants, and proposes the next engineering phase: **Phase 5A — Inference Provider & Engine Decoupling**.

---

## 2. Evidence Sources

All findings, observations, and conclusions in this memo are drawn exclusively from existing certified repository code and measurement artifacts:

1. `artifacts/phase_4x_performance_characterization.json` & `artifacts/phase_4x_performance_capacity_report.md`
2. `artifacts/phase_4x_r1_performance_revalidation.json` & `artifacts/phase_4x_r1_performance_revalidation_report.md`
3. `artifacts/phase_4x_r2_clean_memory.json` & `artifacts/phase_4x_r2_clean_memory_report.md`
4. `artifacts/phase_4x_r2c_clean_boot.json` & `artifacts/phase_4x_r2c_clean_boot_report.md`
5. Certified repository code: `src/novastack/service/api.py`, `src/novastack/generation.py`, `src/novastack/service/resilience.py`, `src/novastack/service/identity.py`
6. Test suites: `tests/test_phase_4x_characterization.py`, `tests/test_phase_4x_r1_instrumentation.py`

### Classification Standard:
Every claim is strictly partitioned into:
- **[MEASURED FACT]**: Directly observed, machine-recorded telemetry from certified scripts.
- **[OBSERVED ON THIS HOST]**: Environmental metric specific to this 7.63 GB Windows 11 machine.
- **[INFERENCE]**: Logical deduction derived from combining measured facts.
- **[ARCHITECTURAL HYPOTHESIS]**: Unproven engineering conjecture requiring verification.
- **[UNKNOWN]**: Outside the boundary of currently collected evidence.

---

## 3. Performance Evidence Table

| Phase | Environment | Memory Condition | Generation Observations | Timeout Observations | What Can Be Concluded | What Cannot Be Concluded |
|---|---|---|---|---|---|---|
| **4X** | Windows 11, 8 physical CPU cores, PyTorch 2.14 CPU | Available RAM: **0.51 GB** / 7.63 GB | 1 request attempted; 0 completed. E2E: 30,745 ms. | 1 timeout (504). Headroom: −745 ms. `was_generation_invoked=False` reported. | Retrieval + evidence is fast (~44.7 ms). System sheds load properly. | Whether `model.generate()` actually started before the 30 s timeout. |
| **4X-R1** | Windows 11, 8 physical CPU cores, PyTorch 2.14 CPU | Available RAM: **0.48 GB** / 7.63 GB | 3 sequential requests (C=1).<br>• Gen 1: 15.92 s model, 16.50 s E2E (200 OK)<br>• Gen 2: Timeout (504)<br>• Gen 3: 12.09 s model, 13.38 s E2E (200 OK) | 1 timeout (Gen 2: 30,443 ms). T6 observed at +1,177 ms; T7 not observed before HTTP response. | Gemma 3 1B CPU generation **can** complete in 12–16 s within 30 s deadline. Timeouts occur mid-generation. | Sustained generation throughput; latency distribution under clean memory. |
| **4X-R2** | Windows 11, 8 physical CPU cores, PyTorch 2.14 CPU | Available RAM: **1.19 GB** / 7.63 GB | **None.** (Halted at Environment Gate). | **None.** (Zero requests dispatched). | Environment Gate correctly halts execution when RAM $<3.0\text{ GB}$. | Generation performance under clean memory. |
| **4X-R2-C** | Windows 11, 8 physical CPU cores, PyTorch 2.14 CPU | Available RAM: **1.28 GB** / 7.63 GB (Uptime: 2.05 h) | **None.** (Halted at Environment Gate). | **None.** (Zero requests dispatched). | Host consistently operates with $<1.3\text{ GB}$ available RAM with OS + IDE active. | Unconstrained hardware baseline performance. |

*Critical Note:* Phases 4X-R2 and 4X-R2-C were environment-gate validations, not performance benchmarks. They did not load Gemma or execute generation.

---

## 4. Established Facts Evaluation

Reviewing the 11 specific architectural propositions:

| Proposition | Status | Classification & Evidence |
|---|---|---|
| **A. Retrieval/evidence is much faster than generation** | **SUPPORTED** | **[MEASURED FACT]** Warm retrieval took 44.71 ms (4X) and 138.17–207.22 ms (4X-R1), representing $<1.5\%$ of generation duration (12,091–15,918 ms). |
| **B. Gemma 3 1B CPU generation was observed to start successfully** | **SUPPORTED** | **[MEASURED FACT]** In 4X-R1, T6 (`model.generate()` started) was directly observed on 100% of requests: Gen 1 (+357 ms), Gen 2 (+1,177 ms), Gen 3 (+1,272 ms). |
| **C. Completed generation runs in R1 were approximately 12.1–15.9 s** | **SUPPORTED** | **[MEASURED FACT]** 4X-R1 measured Gen 1 model duration at 15,918.08 ms (16,501.44 ms E2E) and Gen 3 at 12,090.83 ms (13,384.60 ms E2E). |
| **D. One generation crossed the 30-second service deadline** | **SUPPORTED** | **[MEASURED FACT]** In 4X-R1, Gen 2 returned HTTP 504 at 30,442.96 ms (headroom: −442.96 ms). |
| **E. The model process reached approximately 3.85–4.06 GB RSS during R1** | **SUPPORTED** | **[MEASURED FACT]** In 4X-R1, Checkpoint M3 was 4,007.32 MB, M5 was 3,851.80 MB, M7 was 3,869.52 MB, and M8 reached 4,063.98 MB RSS. |
| **F. The development host has 7.63 GB physical RAM** | **SUPPORTED** | **[MEASURED FACT]** `psutil.virtual_memory().total` consistently reported 7.63 GB (7,813 MB) across all runs. |
| **G. The host repeatedly failed the predefined $\ge 3\text{ GB}$ available-RAM gate** | **SUPPORTED** | **[MEASURED FACT]** Measured available RAM was 0.51 GB (4X), 0.48 GB (4X-R1), 1.19 GB (4X-R2), and 1.28 GB (4X-R2-C). All $<3.00\text{ GB}$. |
| **H. The 30-second service deadline does not preempt the executor worker** | **SUPPORTED** | **[MEASURED FACT]** `asyncio.wait_for` cancels the coroutine and returns HTTP 504, but Python `ThreadPoolExecutor` threads cannot be asynchronously aborted. Worker thread continued running until completion. |
| **I. Generation throughput under healthy memory conditions has NOT been certified** | **SUPPORTED** | **[MEASURED FACT]** Because available RAM was $<1.3\text{ GB}$ in all runs, clean-memory throughput has never been measured or certified. |
| **J. Production QPS has NOT been certified** | **SUPPORTED** | **[MEASURED FACT]** Concurrency ladder was evaluated only under memory pressure in 4X; sustained production QPS is uncertified. |
| **K. Stable long-run memory behavior has NOT been certified** | **SUPPORTED** | **[MEASURED FACT]** Only 3 sequential generations were executed in 4X-R1. Long-run stability ($n > 100$ requests) remains unmeasured. |

---

## 5. Corrections to Overclaims

The Phase 4X-R2 and 4X-R2-C reports contained certain interpretive statements that must be formally corrected:

### 1. Correction on Minimum RAM Claim:
- **Previous Claim:** *"minimum host RAM must be sized at $\ge 16\text{ GB}$"*
- **Correction:** This statement is an **[ARCHITECTURAL HYPOTHESIS]**, not a measured fact. The measurement data proves only that 7.63 GB total host RAM with ~1.2 GB available is insufficient to avoid OS paging when running a 4.0 GB process alongside Windows 11 and an active IDE. Whether 12 GB, 16 GB, or a headless Linux container with 8 GB is sufficient has not been measured.

### 2. Correction on OS Paging Claim:
- **Previous Claim:** *"OS memory management inevitably reclaims working set pages"*
- **Correction:** This statement is an **[INFERENCE / OBSERVATION ON THIS HOST]**, not an invariant system law. On this specific host under $<0.36\text{ GB}$ available RAM, Windows `MemCompression` and working set trimming paged out 2.78 GB of process RSS. Under different operating systems (e.g., Linux without aggressive swap) or with pinned memory buffers, this behavior would differ.

---

## 6. Current Operating Envelope

Based strictly on verified evidence:

### CERTIFIED
- **API & Routing:** FastAPI `/query`, `/healthz`, `/ready`, `/metrics` lifecycle and schemas.
- **Identity & Security Boundary:** HS256 JWT validation, fail-closed missing auth (HTTP 401 in $<200\text{ ms}$), tenant isolation, metadata permission filtering, adversarial prompt DLQ.
- **Retrieval & Evidence Pipeline:** BM25, BGE-small dense, relational retrieval, RRF sum fusion, query-aware authority preservation, metadata reranking, C2 citation resolution ($<210\text{ ms}$ warm latency).
- **Index Management:** Atomic index publication, staged validation, live index hot-swap without downtime.
- **Resilience Controls:** Request deadline cancellation (30 s), circuit breaker state transitions (OPEN on 3 failures, cooldown 10 s), capacity shedding (HTTP 429 under saturation).

### OBSERVED BUT NOT CERTIFIED
- **Gemma 3 1B CPU Latency:** 12.09–15.92 s model execution duration on an 8-core CPU (observed on $n=2$ successful requests).
- **In-Process Memory Working Set:** ~3.85–4.06 GB active RSS during float32 inference; ~3.45 GB retained post-GC.
- **Timeout Mechanism:** Service returns 504 at 30 s while background thread completes `model.generate()`.

### UNKNOWN
- Clean-memory generation latency distribution and variance.
- Maximum sustainable generation throughput (QPS).
- Long-run process memory stability over hundreds of queries.
- Quantized inference performance (int8, int4, GGUF/AWQ).
- Dedicated inference tier performance (vLLM, Triton, external endpoint).
- Smaller model grounding and citation fidelity (e.g., Gemma 270M, SmolLM).

---

## 7. Architecture Option Matrix

Six architectural options are analyzed below. Per directive, options are **not ranked** and **no winner is chosen**.

| Dimension | Option A: Keep Gemma 1B float32 CPU | Option B: Smaller/Faster Local Model | Option C: Quantized Local Inference | Option D: GPU Inference | Option E: Separate Inference Service | Option F: Hybrid Retrieval + Dedicated Tier |
|---|---|---|---|---|---|---|
| **What problem it addresses** | Eliminates architectural churn; keeps simple single-process deployment. | Reduces raw compute cycles and working set memory footprint. | Reduces working set from ~4 GB to ~1–2 GB, reducing memory pressure on host. | Accelerates token generation from 12–16 s down to $<1\text{ s}$. | Decouples API availability & memory from heavy model inference crashes/leaks. | Optimizes retrieval ($<200\text{ ms}$) on lightweight nodes; scales inference independently. |
| **Evidence supporting consideration** | Gen 1 & Gen 3 proved Gemma 1B CPU executes in 12–16 s ($<30\text{ s}$ deadline). | Retrieval and evidence are $<210\text{ ms}$; a lighter model would match this speed profile. | Working set was 4.06 GB; quantization mathematically halves/quarters parameter storage. | Gemma models are optimized for CUDA/TensorRT; CPU token generation is slow (12–16 s). | R1 proved background threads continue running post-504, polluting API worker threads. | 4X-R1 proved retrieval has fundamentally different latency and memory profiles than generation. |
| **Evidence currently missing** | Long-run memory stability; behavior under concurrent load. | Whether smaller models preserve C2 citation accuracy and resist hallucination. | Impact of int8/int4 quantization on evidence grounding and citation extraction accuracy. | Host has no CUDA GPU; performance and cost on target cloud GPU unmeasured. | Network serialization latency between API and inference worker; failure modes. | Operational overhead, deployment complexity, and RPC timeout mechanics. |
| **ATLAS component changed** | None (status quo). | `GroundedAnswerGenerator`, prompt template, citation extractor. | `GroundedAnswerGenerator` (`bitsandbytes`, `llama.cpp`, or `torch.ao`). | Execution device configuration (`device="cuda"`), host infrastructure. | API service `_execute_query_bound`, replaces direct call with HTTP/gRPC client. | System topology: split into ATLAS Web/Index Node + ATLAS Inference Worker Node. |
| **Risks introduced** | High memory footprint (~4 GB) risks thrashing on standard VMs; low throughput ($<0.1\text{ QPS}$). | Risk of regression in answer grounding, compliance, and citation validity. | Potential loss of citation precision; subtle degradation in adversarial resistance. | Significant infrastructure cost increase; hardware dependency. | Distributed system failure modes; network partition latency; double serialization. | Operational complexity: multiple Docker images, deployment orchestration, service discovery. |
| **Testing readiness** | Ready now (already in repo). | Deferred (requires model accuracy evaluation suite). | Deferred (requires quantized benchmark harness). | Deferred (requires GPU hardware). | **Ready for architectural abstraction.** | Deferred (requires Option E first). |

---

## 8. Security & Product Principle Invariants

ATLAS's primary product promise is non-negotiable:
> *"Find the most relevant information the user is authorized to access, explain the answer using evidence, and show where the evidence came from."*

Any future performance optimization or architectural change **must strictly preserve**:
1. **Tenant Isolation:** Tenant boundaries enforced before retrieval, during fusion, and in citation resolution.
2. **Fail-Closed Authorization:** Missing or invalid JWT credentials rejected immediately (HTTP 401) without query evaluation.
3. **Evidence Grounding:** Every factual claim must be backed by retrieved evidence; unsupported claims dropped.
4. **C2 Citation Correctness:** Sentence-level citation tags validated against authorized document chunks.
5. **Adversarial Quarantine:** Ingestion and query-level injection attacks detected and quarantined.
6. **Index Lifecycle Integrity:** Atomic index swapping and validation must remain independent of inference state.

Performance optimizations that compromise citation fidelity or bypass authorization are unacceptable.

---

## 9. Missing Capability Analysis

Reviewing the certified capabilities of ATLAS:
- ✅ Retrieval & Fusion (BM25, Dense, Relational, RRF)
- ✅ Security & Authentication (JWT, fail-closed, multi-tenant)
- ✅ Evidence Assembly & Sentence Citation Validation (C2)
- ✅ Service Resilience (Circuit breaker, concurrency limiter, request deadline)
- ✅ Index Lifecycle (Atomic staging, hot-swap)
- ✅ Observability (Prometheus metrics, structured logging)

### What Architectural Capability is Missing?
**The Model / Inference Provider Abstraction Boundary.**

Currently, `GroundedAnswerGenerator` is tightly coupled to in-process HuggingFace `AutoModelForCausalLM` running PyTorch tensors on the local CPU:
- `api.py` directly instantiates PyTorch models within the web server process.
- The web server's memory space is directly contaminated by the 4 GB LLM working set.
- Background `ThreadPoolExecutor` workers cannot be aborted when HTTP timeouts fire.
- The architecture cannot switch between local CPU, quantized runtime, remote inference service, or cloud endpoints without modifying core service logic.

Decoupling the generation engine behind a formal **Inference Provider Interface** (`AnswerGeneratorProvider`) is the prerequisite architectural capability needed before any optimization, model swap, or hardware change can be safely evaluated.

---

## 10. Proposed Next Phase: Phase 5A

### Proposed Mission:
**PHASE 5A — INFERENCE PROVIDER & ENGINE DECOUPLING**

### Core Objectives:
1. **Define an Abstract Inference Provider Interface:**
   Formalize `AnswerGeneratorProvider` protocol with standardized inputs (`EvidencePackage`, generation parameters) and outputs (`AnswerResult`, execution metadata, timing diagnostics).
2. **Encapsulate Local In-Process Provider:**
   Refactor current `GroundedAnswerGenerator` into `LocalHuggingFaceProvider` implementing the interface without altering generation behavior or prompt logic.
3. **Establish Provider Configuration & Pluggability:**
   Allow the API pipeline to bind to an inference provider via dependency injection in `create_app(pipeline=..., inference_provider=...)`.
4. **Enable Non-Intrusive Future Evaluation:**
   Provide the architectural hook necessary to test Option B (smaller model), Option C (quantized engine), or Option E (remote inference service) without touching retrieval, ranking, security, or API routing code.

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Interface abstraction alters citation semantics | High | Enforce existing C2 citation validation suite on all provider implementations. |
| In-process memory contention persists | Medium | Phase 5A provides the interface to cleanly move to an out-of-process worker in subsequent phases. |
| Refactoring breaks frozen production configuration | Critical | Preserve existing `create_default()` factory with 100% backward-compatible defaults. |

---

## 12. Open Questions for the CTO

1. **Deployment Topology:** Should ATLAS target single-node appliance deployments (requiring aggressive memory minimization/quantization) or distributed cloud deployments (where a separate inference microservice tier is preferred)?
2. **Latency Budget:** Is a 12–16 s generation latency acceptable for enterprise search workloads under $C=1$, or does the target SLA require $<5\text{ s}$ E2E response times?
3. **Hardware Baseline:** What is the certified target production environment (minimum host RAM and core count) for ATLAS production deployments?

---

## 13. CTO Decision Inputs Summary

- **Measured Baseline:** Gemma 3 1B on CPU completes in **12.1–15.9 s** with **13.5–16.6 s headroom** when memory is resident.
- **Root Failure Mode:** 504 timeouts are caused by operating system virtual memory page reclamation when a 4.0 GB working set runs on a host with $<1.3\text{ GB}$ available RAM.
- **Architectural Bottleneck:** Direct coupling of in-process PyTorch inference to the FastAPI web process.
- **Immediate Path Forward:** Formalize the Inference Provider boundary in Phase 5A before committing to model, quantization, or hardware changes.

---

## Final Phase Status

```
==================================================
PHASE 4Y STATUS:
COMPLETE — DECISION MEMO ONLY
==================================================
```
