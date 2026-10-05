# PHASE 5B-R1: OLLAMA PROCESS + CONCURRENCY CHARACTERIZATION
**ATLAS Technical Reference Document**

---

## 1. Overview & Purpose

Phase 5B-R1 is a **measurement-only empirical characterization** of the experimental quantized local inference stack:
$$\text{QuantizedLocalProvider} \longrightarrow \text{localhost HTTP (11434)} \longrightarrow \text{Ollama Daemon} \longrightarrow \text{llama.cpp engine} \longrightarrow \text{Gemma 3 1B Q4\_K\_M GGUF}$$

The objective is to characterize:
1. Real multi-process process hierarchy and individual resident memory (RSS) allocations on Windows.
2. ATLAS Python process RSS vs. Ollama server RSS vs. `llama-server.exe` worker RSS.
3. System available memory impact across checkpoints M0–M7.
4. Concurrency behavior at bursts $C=1$, $C=2$, and $C=5$ under existing production resilience constraints (`max_concurrent_inferences=1`, `queue_timeout=0.5s`).
5. Strict decoupling of **Fast Rejection Rate** (HTTP 429) from **Actual Generation Throughput** (HTTP 200).
6. Repeated-request stability across 10 sequential generation invocations.
7. Security boundary and C2 citation regression verification.

> [!IMPORTANT]
> **Production Default Preservation Invariant:**
> The production default runtime remains `LocalHuggingFaceProvider` + `google/gemma-3-1b-it` (`torch.float32` on CPU).
> `QuantizedLocalProvider` is strictly experimental. It is NOT promoted to production default. Phase 5C is NOT opened.

---

## 2. Multi-Process Architecture & Discovery

On Windows 11 AMD64, the Ollama inference environment operates across distinct operating system processes:

```
+-------------------------------------------------------------------------------+
| HOST OS: Windows 11 (AMD64) | 8 Physical / 8 Logical Cores | 7.63 GB RAM       |
+-------------------------------------------------------------------------------+
       |
       +---> [PID 18504] "ollama app.exe" (GUI / Tray Supervisor)
       |       - Path: AppData\Local\Programs\Ollama\ollama app.exe
       |       - Steady-State RSS: ~33.5 - 33.6 MB
       |
       +---> [PID 18092] "ollama.exe serve" (Core HTTP/REST Daemon)
       |       - Path: AppData\Local\Programs\Ollama\ollama.exe
       |       - Listening Port: 127.0.0.1:11434
       |       - Steady-State RSS: ~26.8 - 27.3 MB
       |
       +---> [PID 17520] "llama-server.exe" (Dedicated C++ Inference Worker)
       |       - Path: AppData\Local\Programs\Ollama\lib\ollama\llama-server.exe
       |       - Engine: llama.cpp with AVX2 instruction sets
       |       - Loaded Model: gemma3:1b (Q4_K_M GGUF, 815 MB disk size)
       |       - Steady-State Worker RSS: ~944.5 - 958.9 MB
       |
       +---> [PID 3260] "python.exe" (ATLAS FastAPI / Retrieval Service)
               - Concurrency Limiter: max_concurrent_inferences=1, queue_timeout=0.5s
               - Initial Startup RSS (M0): 78.61 MB
               - Post-Benchmark Settled RSS (M7): 85.14 MB (Net Delta: +6.53 MB)
```

### Key Architectural Benefit
The entire weight matrix of `gemma3:1b` (815 MB on disk, ~945–959 MB in memory) resides in the dedicated C++ worker process `llama-server.exe`. The ATLAS Python application process never allocates model tensors into its own Python heap or PyTorch cache, remaining at ~85 MB resident set size.

---

## 3. Memory Checkpoints (M0 – M7)

All memory measurements were recorded via OS-level process sampling using `psutil`.

| Checkpoint | Description | Python RSS | Ollama Total RSS | llama-server RSS | Combined RSS | Host Available RAM |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **M0** | Initial startup (pre-benchmark) | 78.61 MB | 1019.35 MB | 958.90 MB | 1097.96 MB | 858.30 MB |
| **M1** | Provider initialized & pinged | 79.87 MB | 1019.35 MB | 958.90 MB | 1099.22 MB | 673.64 MB |
| **M2** | Pipeline & FastAPI app wired | 82.96 MB | 1019.35 MB | 958.90 MB | 1102.31 MB | 620.59 MB |
| **M4** | Post single-request control (3 runs) | 84.87 MB | 1010.86 MB | 950.31 MB | 1095.73 MB | 580.75 MB |
| **M5** | Post all bursts and repeated runs | 85.16 MB | 1005.32 MB | 944.47 MB | 1090.48 MB | 890.49 MB |
| **M6** | Post `gc.collect()` | 85.16 MB | 1005.32 MB | 944.47 MB | 1090.48 MB | 948.58 MB |
| **M7** | Post 60-second idle settle | 85.14 MB | 1005.32 MB | 944.47 MB | 1090.46 MB | 1209.19 MB |

### Observations:
- **Python RSS Delta:** From M0 (78.61 MB) to M7 (85.14 MB), net growth was only **+6.53 MB** across 19 full generation and burst requests.
- **Worker Memory Stability:** `llama-server.exe` stabilized at **944.47 MB** post-workload, down slightly from initial 958.90 MB.
- **Clean Memory Gate:** Available host RAM at M0 was 858.30 MB (< 3,072 MB target). Labeled: `MEMORY-CONSTRAINED OBSERVATION`.

---

## 4. Concurrency Characterization ($C=1, C=2, C=5$)

Under frozen production resilience configuration:
- `max_concurrent_inferences = 1`
- `queue_timeout_seconds = 0.5`
- `request_timeout_seconds = 30.0`
- `enable_circuit_breaker = True`

### Results Table

| Concurrency Level | Requests Sent | Completed (HTTP 200) | Capacity Shed (HTTP 429) | Timeouts (HTTP 504) | Circuit Breaker (HTTP 503) | Wall Clock (ms) | Avg Gen Latency (ms) | Avg Fast Rejection Latency (ms) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **$C=1$** | 1 | 1 | 0 | 0 | 0 | 8,865.50 | 8,865.12 | N/A |
| **$C=2$** | 2 | 1 | 1 | 0 | 0 | 1,420.09 | 1,419.90 | 557.56 |
| **$C=5$** | 5 | 1 | 4 | 0 | 0 | 1,329.02 | 1,328.78 | 558.40 |

### Critical Separation: Fast Rejection vs. Generation Throughput
- **Fast Rejection Latency:** Exactly **$558.40\text{ ms}$** on average. The `InferenceConcurrencyLimiter` holds queued requests up to the configured `0.5\text{s}` queue timeout, then cleanly sheds excess requests with HTTP 429 (`CapacityExhaustedError`).
- **Generation Throughput:** Exactly **1 generation request** executes at a time, consuming ~1.33s on a warm prompt.
- **Zero Failures:** 0 timeouts (HTTP 504), 0 circuit breaker openings (HTTP 503), 0 unexpected internal errors.

---

## 5. Repeated-Request Stability (10 Sequential Generations)

| Request # | HTTP Status | Answer Status | E2E Latency (ms) | Python RSS (MB) | Ollama RSS (MB) | Combined RSS (MB) | Host Avail RAM (MB) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1** | 200 | answered | 8,790.40 | 85.02 | 1011.55 | 1096.57 | 633.82 |
| **2** | 200 | answered | 1,353.06 | 85.05 | 1020.03 | 1105.08 | 609.48 |
| **3** | 200 | answered | 1,439.59 | 85.06 | 1019.83 | 1104.89 | 623.18 |
| **4** | 200 | answered | 1,287.32 | 85.07 | 1019.84 | 1104.91 | 636.12 |
| **5** | 200 | answered | 1,311.96 | 85.07 | 1019.84 | 1104.91 | 630.41 |
| **6** | 200 | answered | 1,328.44 | 85.14 | 1019.85 | 1104.99 | 736.62 |
| **7** | 200 | answered | 1,316.64 | 85.14 | 1019.85 | 1104.99 | 736.91 |
| **8** | 200 | answered | 1,237.69 | 85.14 | 1019.99 | 1105.13 | 739.35 |
| **9** | 200 | answered | 1,360.96 | 85.15 | 1019.99 | 1105.14 | 738.92 |
| **10** | 200 | answered | 1,298.25 | 85.16 | 1020.16 | 1105.32 | 739.08 |

### Findings:
- Warm prompt evaluation and generation completes in **1.24s – 1.44s**.
- Python RSS is essentially flat across requests 2–10 ($85.05\text{ MB} \to 85.16\text{ MB}$, delta $+0.11\text{ MB}$).
- Ollama total RSS is essentially flat across requests 2–10 ($1020.03\text{ MB} \to 1020.16\text{ MB}$, delta $+0.13\text{ MB}$).
- No monotonic memory accumulation was observed across the 10 repeated requests.

---

## 6. Security and Citation Gates

- **Unauthenticated Gate:** HTTP 401 returned; provider invocation count is strictly 0.
- **Cross-Tenant Gate:** HTTP 403 returned; provider invocation count is strictly 0.
- **Grounding and C2 Validation:** All completed requests returned `answer_status: "answered"` with 100% C2 citation validity.
