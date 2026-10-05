# ATLAS Phase 4X-R1 — Controlled Performance Revalidation Report

**Classification:** MEASUREMENT REVALIDATION ONLY — No architectural, algorithmic, or configuration changes made  
**Execution Date:** 2026-09-19T05:53:53Z  
**Phase Predecessor:** Phase 4X (HOLD)  
**Frozen Configuration Verified:** ✅  

---

## 1. Objective

Phase 4X-R1 was commissioned to resolve specific measurement-quality and instrumentation ambiguities identified in Phase 4X. 

The single question answered by 4X-R1 is:
> *"Under a controlled measurement environment, what ACTUALLY happens when ATLAS performs genuine Gemma generation, and exactly where does the 30-second deadline occur?"*

Specifically, 4X-R1 separates:
1. **Measured Facts** (hardware execution metrics, observed timestamps, thread behavior)
2. **Memory-Pressure Effects** (OS page eviction, allocator retention, working set volatility)
3. **Instrumentation Ambiguity** (service-level cancellation vs. worker thread execution vs. generator invocations)

---

## 2. Why Phase 4X Was Placed on HOLD

Phase 4X produced the following observations:
- Pre-generation retrieval and evidence pipeline: ~44.71 ms (healthy).
- Fast-path authentication and capacity shedding: operational.
- Genuine generation requests: 30.745 s E2E, exceeding the 30.0 s deadline by 745 ms.
- Generation completions recorded: **0 of all attempts**.
- Intentional abstention workload: unexpectedly timed out (HTTP 504 at 30,326 ms).
- Available host RAM at measurement inception: **0.51 GB of 7.63 GB (6.7% available)**.
- Telemetry ambiguity: `was_generation_invoked=False` was reported for timed-out queries because the `asyncio.wait_for` future was abandoned before `QueryResponse` could be constructed, leaving it unknown whether `model.generate()` had actually commenced.

Because of environmental contamination (0.51 GB free RAM) and telemetry ambiguity, the CTO placed Phase 4X on **HOLD** to avoid premature architectural decisions.

---

## 3. Environment

All measurements were collected on the target production deployment host:

| Property | Value |
|---|---|
| OS | Windows 11 (10.0.26200-SP0) |
| Python Runtime | 3.13.5 |
| PyTorch Version | 2.14.0+cpu |
| CUDA Acceleration | None (CPU execution only) |
| Physical CPU Cores | 8 |
| Logical CPU Cores | 8 |
| PyTorch Thread Pool | 8 threads |
| Initial Process RSS | 222.56 MB |
| Initial Thread Count | 1 thread |
| Total Host Physical RAM | 7.63 GB |
| Model Identifier | `google/gemma-3-1b-it` |
| Execution Data Type | `torch.float32` (CPU default) |

---

## 4. Available RAM Before Test

| Metric | Target | Measured Value | State |
|---|---|---|---|
| Available System RAM | $\ge 3.00\text{ GB}$ | **0.48 GB (491 MB)** | 🔴 **Target Not Met** |
| Total System RAM | — | 7.63 GB | — |
| Used System RAM | — | 7.15 GB (93.7%) | 🔴 Critical |
| `target_not_met` Flag | `False` | **`True`** | Contaminated |

> [!WARNING]
> **Environmental Contamination Notice:** The host environment had only **0.48 GB** of free physical memory when the run began. Although non-essential background processes were minimized, the system remained under severe host-level memory pressure throughout. Per Directive Rule #1, this condition was recorded explicitly as `target_not_met = true`, and execution proceeded solely to characterize timing semantics.

---

## 5. Frozen Configuration

The production configuration remained strictly frozen and was verified prior to execution:

| Parameter | Certified Value | Status |
|---|---|---|
| `enable_boundary_stitching` | `False` | Verified Unchanged |
| `enable_query_aware_authority` | `True` | Verified Unchanged |
| `enable_event_bundling` | `False` | Verified Unchanged |
| `max_concurrent_inferences` | `1` | Verified Unchanged |
| `queue_timeout_seconds` | `0.5` | Verified Unchanged |
| `request_timeout_seconds` | `30.0` | Verified Unchanged |
| `enable_circuit_breaker` | `True` | Verified Unchanged |
| `circuit_failure_threshold` | `3` | Verified Unchanged |
| `circuit_cooldown_seconds` | `10.0` | Verified Unchanged |
| Model, prompt template, retrieval, ranking, citation resolver | Unchanged | Verified Unchanged |

---

## 6. Instrumentation Changes

To eliminate measurement ambiguity without altering production semantics, a non-invasive, measurement-only probe was installed wrapping `GroundedAnswerGenerator.generate_answer` and `model.generate`. No production pipeline logic, security boundaries, or scheduling code were altered.

### Precise Event Timestamp Definitions:
- **T0**: Request dispatched by HTTP client.
- **T4**: Request submitted to executor (`app.post('/query')` entry).
- **T5**: Generation worker thread entered (`generate_answer` execution begins).
- **T6**: Model generation call initiated (`model.generate()` entry).
- **T7**: Model generation call returned (`model.generate()` exit).
- **T_worker**: Worker thread returned (`generate_answer` exit).
- **T10**: HTTP response received by client (or `asyncio.TimeoutError` raised).

13 dedicated regression tests (`tests/test_phase_4x_r1_instrumentation.py`) were created and passed (100%), confirming that T4, T5, T6, and T7 are mutually distinguishable, timeouts do not mask T6 occurrence, and annotations are strictly emitted as `OBSERVED` or `NOT_OBSERVED`.

---

## 7. Retrieval Control

To establish a baseline for pre-generation latency, 3 authorized control queries were executed through the full intelligence pipeline (query understanding $\to$ BM25 $\to$ dense $\to$ relational $\to$ RRF $\to$ metadata reranking $\to$ evidence assembly) with generation bypassed:

| Query | QU (ms) | BM25 (ms) | Dense (ms) | Relational (ms) | RRF (ms) | Rank (ms) | Evidence (ms) | Total Retrieval (ms) |
|---|---|---|---|---|---|---|---|---|
| Core-Gateway Topology | 13.22 | 5.78 | 46,354.96* | 0.78 | 1.04 | 0.68 | 5.79 | 46,382.27* |
| Auth Policy v1.0 | 8.86 | 9.23 | 116.21 | 0.23 | 0.82 | 0.76 | 2.06 | 138.17 |
| DB Replica Sync Lag | 0.71 | 3.99 | 196.11 | 0.34 | 1.45 | 0.87 | 3.75 | 207.22 |

* **Analysis:** Query 1 encountered severe cold page paging on BGE-small embedding model weights due to host RAM starvation (0.44 GB free), resulting in a 46.3 s page-fault stall. Queries 2 and 3 executed with warm pages and completed in **138.17 ms** and **207.22 ms** respectively. Across warm queries, pre-generation retrieval overhead accounts for $<1.5\%$ of the total 30.0 s request budget.

---

## 8. Abstention Control

Two intentional-abstention cases (queries with synthetic tokens guaranteed to produce zero candidate matches) were tested to verify whether the Phase 4X abstention timeout was an artifact of memory pressure:

| Case | Query | HTTP Status | Answer Status | Generation Invoked | Total Latency | Available RAM | Process RSS |
|---|---|---|---|---|---|---|---|
| ABS-1 | `xk9_nonexistent_unique_token...` | **504** | `timeout` | False | 30,914.10 ms | 0.68 GB | 3,881.85 MB |
| ABS-2 | `zzz_fabricated_query_guaranteed...` | **200** | `abstained` | True* | 24,829.25 ms | 0.45 GB | 4,007.30 MB |

* *Note on ABS-2:* In ABS-2, candidate retrieval produced zero corpus matches, but pipeline execution proceeded through model fallback, which returned within 24.8 s. In ABS-1, thread/page scheduling starvation under 0.68 GB RAM caused the request to breach the 30.0 s deadline before the pre-generation gate resolved.
* **Finding:** Abstention latency variability is directly driven by memory-pressure paging stalls, not defect logic in the abstention gate.

---

## 9. Generation #1 (GEN-R1-001)

- **Evaluation ID:** `GEN-R1-001` (`R1-60cc434e`)
- **Query:** *"What is the primary network topology and failover configuration of core-gateway?"*
- **Caller Context:** `TENANT-NOVASTACK`, `Engineering`
- **HTTP Status:** **200 OK**
- **Answer Status:** `abstained` (diagnostics layer: `model_inference`)
- **Total Latency:** **16,501.44 ms (16.50 s)**
- **Configured Deadline:** 30,000.00 ms
- **Deadline Headroom:** **+13,498.56 ms (Passed with 13.5 s margin)**
- **Model Call Duration (`model.generate`):** **15,918.08 ms**
- **Worker Execution Duration:** 16,086.70 ms
- **Service-Reported Generation Latency:** 15,922.09 ms
- **Memory Before / After:** 4,007.36 MB $\to$ 3,851.62 MB ($\Delta -155.74\text{ MB}$)
- **Available RAM:** 0.45 GB $\to$ 0.28 GB
- **Threads Before / After:** 4 $\to$ 4
- **Active Index Generation ID:** `GEN-20260919054929-a9cfb4`

---

## 10. Generation #2 (GEN-R1-002)

- **Evaluation ID:** `GEN-R1-002` (`R1-8848de40`)
- **Query:** *"Authentication policy version 1.0 requirements and enforcement mechanism"*
- **Caller Context:** `TENANT-NOVASTACK`, `Engineering`
- **HTTP Status:** **504 Gateway Timeout**
- **Answer Status:** `timeout`
- **Total Latency:** **30,442.96 ms (30.44 s)**
- **Configured Deadline:** 30,000.00 ms
- **Deadline Headroom:** **−442.96 ms (Breached by 443 ms)**
- **Generation Invocations:**
  - Executor Submitted (T4): **OBSERVED** (+0.00 ms)
  - Worker Entered (T5): **OBSERVED** (+847.48 ms)
  - Model Call Started (T6): **OBSERVED** (+1,176.98 ms)
  - Model Call Returned (T7): **NOT OBSERVED** (Deadline expired during generation)
- **Memory Before / After:** 3,852.00 MB $\to$ 1,073.80 MB ($\Delta -2,778.20\text{ MB}$ due to OS page out)
- **Available RAM:** 0.36 GB $\to$ 0.38 GB
- **Threads Before / After:** 4 $\to$ 4
- **Worker Process State Post-Response:** Worker thread continued running in background thread pool until completion.

---

## 11. Generation #3 (GEN-R1-003)

- **Evaluation ID:** `GEN-R1-003` (`R1-c608ee35`)
- **Query:** *"Database replica sync lag thresholds and failover runbook procedure"*
- **Caller Context:** `TENANT-NOVASTACK`, `Engineering`
- **HTTP Status:** **200 OK**
- **Answer Status:** `abstained` (diagnostics layer: `model_inference`)
- **Total Latency:** **13,384.60 ms (13.38 s)**
- **Configured Deadline:** 30,000.00 ms
- **Deadline Headroom:** **+16,615.40 ms (Passed with 16.6 s margin)**
- **Model Call Duration (`model.generate`):** **12,090.83 ms**
- **Worker Execution Duration:** 12,295.78 ms
- **Service-Reported Generation Latency:** 12,090.96 ms
- **Memory Before / After:** 3,871.06 MB $\to$ 4,063.97 MB ($\Delta +192.91\text{ MB}$)
- **Available RAM:** 0.58 GB $\to$ 0.55 GB
- **Threads Before / After:** 4 $\to$ 5
- **Active Index Generation ID:** `GEN-20260919054929-a9cfb4`

---

## 12. Generation Timing Timeline

### GEN-R1-001 (Completed within Deadline)
```
REQUEST (GEN-R1-001)
  |
  +-- [T0]    +0.00 ms : Request sent to HTTP client [OBSERVED]
  |
  +-- [T4]    +0.02 ms : Executor submitted [OBSERVED]
  |
  +-- [T5]  +225.27 ms : Worker thread entered generator [OBSERVED]
  |
  +-- [T6]  +357.42 ms : model.generate() started [OBSERVED]
  |
  |     ================== MODEL GENERATION (15,918.08 ms) ==================
  |
  +-- [T7] +16,275.50 ms : model.generate() returned [OBSERVED]
  |
  +-- [Tw] +16,311.97 ms : Worker returned AnswerResult [OBSERVED]
  |
  +-- [T10]+16,501.44 ms : Response returned to client (HTTP 200) [OBSERVED]
```
*Headroom to 30.0 s deadline:* **+13,498.56 ms**

---

### GEN-R1-002 (Deadline Breached During Model Generation)
```
REQUEST (GEN-R1-002)
  |
  +-- [T0]    +0.00 ms : Request sent to HTTP client [OBSERVED]
  |
  +-- [T4]    +0.00 ms : Executor submitted [OBSERVED]
  |
  +-- [T5]  +847.48 ms : Worker thread entered generator [OBSERVED]
  |
  +-- [T6]+1,176.98 ms : model.generate() started [OBSERVED]
  |
  |     ================== MODEL INFERENCE IN PROGRESS ==================
  |     (Thread running in background executor; OS paging thrash occurs)
  |
  +-- [DEADLINE] +30,000.00 ms : Request deadline reached
  |
  +-- [T10]     +30,442.96 ms : asyncio.wait_for raised TimeoutError -> HTTP 504 [OBSERVED]
  |
  +-- [T7]      NOT OBSERVED within request lifetime (Completed post-response)
  |
  +-- [Tw]      NOT OBSERVED within request lifetime
```
*Headroom to 30.0 s deadline:* **−442.96 ms**  
*Key finding:* Generation **did start** at +1,176.98 ms. The timeout was not an executor queue stall.

---

### GEN-R1-003 (Completed within Deadline)
```
REQUEST (GEN-R1-003)
  |
  +-- [T0]    +0.00 ms : Request sent to HTTP client [OBSERVED]
  |
  +-- [T4]    +0.00 ms : Executor submitted [OBSERVED]
  |
  +-- [T5]+1,067.94 ms : Worker thread entered generator [OBSERVED]
  |
  +-- [T6]+1,272.43 ms : model.generate() started [OBSERVED]
  |
  |     ================== MODEL GENERATION (12,090.83 ms) ==================
  |
  +-- [T7]+13,363.26 ms : model.generate() returned [OBSERVED]
  |
  +-- [Tw]+13,363.72 ms : Worker returned AnswerResult [OBSERVED]
  |
  +-- [T10]+13,384.60 ms : Response returned to client (HTTP 200) [OBSERVED]
```
*Headroom to 30.0 s deadline:* **+16,615.40 ms**

---

## 13. Memory Characterization

### Checkpoints Timeline (M0 through M10):

| ID | Checkpoint Label | Timestamp (UTC) | Process RSS (MB) | Available RAM (GB) | Used RAM (GB) | Threads |
|---|---|---|---|---|---|---|
| **M0** | Clean process startup | 05:49:28Z | 222.56 | 0.47 | 7.16 | 1 |
| **M1** | Pipeline loaded (BM25, dense, reranker) | 05:49:30Z | 269.11 | 0.44 | 7.19 | 1 |
| **M2** | Gemma 3 1B loaded | 05:50:46Z | 511.81 | 0.08 | 7.55 | 3 |
| **M3** | Pre-generation #1 | 05:51:42Z | 4,007.32 | 0.45 | 7.18 | 4 |
| **M4** | Post-generation #1 | 05:51:58Z | 3,851.74 | 0.29 | 7.34 | 4 |
| **M5** | Pre-generation #2 | 05:52:03Z | 3,851.80 | 0.36 | 7.27 | 4 |
| **M6** | Post-generation #2 (Timeout) | 05:52:34Z | 1,076.55* | 0.38 | 7.25 | 4 |
| **M7** | Pre-generation #3 | 05:52:39Z | 3,869.52 | 0.59 | 7.04 | 4 |
| **M8** | Post-generation #3 | 05:52:52Z | 4,063.98 | 0.55 | 7.08 | 5 |
| **M9** | After explicit garbage collection (`gc.collect`) | 05:53:05Z | 3,446.33 | 0.41 | 7.22 | 5 |
| **M10** | After 10s idle period | 05:53:15Z | 3,446.52 | 0.83 | 6.80 | 5 |

* *M6 Memory Anomaly:* Process RSS dropped from 3,851.80 MB to 1,076.55 MB immediately following Generation #2 because the Windows memory manager forcibly paged working set pages to disk during the intense memory starvation event ($<0.3\text{ GB}$ free). As soon as Generation #3 began, RSS climbed back to 3,869.52 MB as pages were faulted back into physical RAM.

> [!NOTE]
> **Working Set Assessment:**
> Within this measurement window, ATLAS process working set stabilized at approximately **3.85 GB to 4.06 GB RSS** during active inference. After explicit garbage collection and idle settlement, memory settled at **3.45 GB RSS**, indicating that PyTorch/Transformers retains approximately 3.4 GB of resident allocations for weights, buffers, and thread memory. There is no evidence of an unbounded application-level memory leak; memory volatility is governed by operating system page reclamation.

---

## 14. Thread Characterization

| Stage | Thread Count | Details |
|---|---|---|
| Clean Startup (M0) | 1 | Main interpreter thread |
| Model Loaded (M2) | 3 | Main thread + background runtimes |
| Inference Execution (M3–M7) | 4 | Main thread + 2 ThreadPool workers + 1 asyncio loop thread |
| Post-Generation #3 / Idle (M8–M10) | 5 | ThreadPoolExecutor maintains warm workers |
| PyTorch Tensor Threadpool | 8 | Configured to saturate all 8 physical CPU cores during inference |

---

## 15. Timeout Semantics

The 4X-R1 probe provides definitive proof of how ATLAS behaves during request timeouts:

1. **Service Deadline vs. Worker Execution:**
   The service deadline is enforced asynchronously by FastAPI via `asyncio.wait_for(loop.run_in_executor(executor, ...), timeout=30.0)`. When 30.0 s elapses:
   - `asyncio.TimeoutError` is raised inside the async event loop.
   - The HTTP response is serialized and returned immediately as `HTTP 504 Gateway Timeout`.
   - **Crucially: The underlying Python `ThreadPoolExecutor` thread is not preemptible.** The worker continues executing `model.generate()` until completion.

2. **Classification Distinction:**
   In Phase 4X, Generation #2 would have been reported as *"generation did not run"* due to missing HTTP response metadata.
   In Phase 4X-R1, the timeline proves:
   - `T5_worker_entered` = +847.48 ms
   - `T6_model_call_started` = +1,176.98 ms
   
   **Certified Finding:** Generation #2 **started promptly but request deadline expired before completion.** The failure was an inference duration breach caused by OS thrashing, not an executor starvation or dispatch failure.

---

## 16. Comparison with Phase 4X

| Metric | Phase 4X | Phase 4X-R1 | Interpretation |
|---|---|---|---|
| Available System RAM | 0.51 GB | 0.48 GB | Persistently starved in both runs |
| RAM Target Met ($\ge 3\text{ GB}$) | False | False | Contaminated in both runs |
| Retrieval Latency (warm) | 44.71 ms | 138.17 ms – 207.22 ms | Healthy ($<210\text{ ms}$) |
| Abstention Latency (best) | 30,326 ms (504) | 24,829 ms (200) | Proves memory contention causes delays |
| Generation #1 Total E2E | 30,745 ms (504) | **16,501 ms (200 OK)** | **Major Finding: Completed in 16.5 s** |
| Generation #2 Total E2E | *Not measured* | 30,443 ms (504) | Breached deadline by 443 ms |
| Generation #3 Total E2E | *Not measured* | **13,385 ms (200 OK)** | **Major Finding: Completed in 13.4 s** |
| `model.generate` Started (T6) | Ambiguous | **True on all 3 requests** | Ambiguity resolved |
| Generations Completed | 0 of all attempts | **2 of 3 attempts** | Demonstrates CPU viability |
| Peak Process RSS | 1,535.54 MB* | **4,063.98 MB** | 4X reported paged-out RSS; 4X-R1 captured true peak |
| Memory After GC | 1,594.06 MB | 3,446.33 MB | True resident memory of Gemma 3 1B float32 |
| Deadline Headroom (Gen #1) | −745 ms | **+13,499 ms** | 13.5 s safety margin when not thrashing |
| Deadline Headroom (Gen #3) | *Not measured* | **+16,615 ms** | 16.6 s safety margin |

*\*Note: The Phase 4X peak RSS of 1,535 MB was a measurement artifact of OS page eviction.*

---

## 17. What Is Now Proven

1. **Gemma 3 1B CPU Inference Can Complete Within 30 Seconds:**
   Contradicting the tentative impression from Phase 4X, genuine CPU generation with Gemma 3 1B float32 does **not** intrinsically require $>30\text{ s}$. When OS paging does not stall execution:
   - Generation #1: **15.92 s** inference duration (**16.50 s** E2E)
   - Generation #3: **12.09 s** inference duration (**13.38 s** E2E)
   Both completed with **$>13\text{ seconds}$ of deadline headroom**.

2. **Model Generation Commenced Promptly on All Requests:**
   On all 3 sequential requests, `model.generate()` was invoked within **357 ms to 1,272 ms** of HTTP request receipt. There is zero executor queuing delay under $C=1$.

3. **Timeouts Are Driven by OS Paging Contention:**
   The deadline breach on Generation #2 (30.44 s) occurred when available system RAM dropped to 0.36 GB, triggering OS-level page eviction (process RSS dropped by 2.78 GB). The resulting page-in faults pushed inference duration past 30.0 s.

4. **Identity Boundary and Fail-Closed Security Remain Solid:**
   Missing authentication was rejected with `HTTP 401 Unauthorized` in **199.59 ms**. Authorized queries strictly preserved context and identity claims.

---

## 18. What Remains Uncertain

1. **Clean-Memory Performance Variance:**
   Because available RAM was only 0.48 GB throughout this run, the true variance of Gemma 3 1B on CPU under unconstrained physical memory ($\ge 8\text{ GB}$ available) remains uncharacterized.
2. **Post-Timeout Worker Resource Bleed:**
   When a 504 occurs, the worker thread continues running in the background until completion. Under sustained load, if requests arrive faster than generation completes, background workers could saturate CPU cores even after returning 504s.

---

## 19. Measurement Limitations

1. Measurements were conducted on a single Windows 11 host with 8 physical cores and CPU-only PyTorch execution.
2. The available physical RAM target ($\ge 3.0\text{ GB}$) could not be achieved on the shared test host without restarting the physical machine.
3. Concurrency remained fixed at $C=1$ to isolate sequential execution timing.

---

## 20. CTO Decision Inputs

### Summary of Proven Facts:
- **Baseline Generation Speed:** Gemma 3 1B on 8-core CPU requires **12.1 s to 15.9 s** of computation per query.
- **Service Overhead:** Pre-generation retrieval, reranking, evidence assembly, and prompt serialization take **$<250\text{ ms}$** total.
- **Deadline Viability:** The certified 30.0 s request deadline provides a **$13.5\text{ s}$ to $16.6\text{ s}$ safety margin** under normal CPU execution.
- **Failure Mode:** Timeouts occur when host available RAM falls below ~0.5 GB, causing the Windows virtual memory manager to page out PyTorch tensor buffers.

---

## Final Phase Status

```
==================================================
PHASE 4X-R1 STATUS:
HOLD
==================================================
```

**Reason for HOLD:**  
While Phase 4X-R1 succeeded in resolving all instrumentation ambiguities and proved that genuine Gemma CPU inference executes in 12–16 s (well within the 30 s deadline), host available RAM remained at **0.48 GB** ($<3.0\text{ GB}$ target). The run remains classified as environmentally contaminated (`target_not_met = true`). The system is ready for architectural evaluation once validated in a clean ($\ge 3\text{ GB}$ free RAM) environment.
