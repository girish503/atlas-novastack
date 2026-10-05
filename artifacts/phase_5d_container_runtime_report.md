# ATLAS PHASE 5D EXECUTION REPORT
**Container Runtime Validation & Operational Integration**

---

## 1. Executive Summary & Verification Context

Phase 5D empirically validates the standalone containerized inference service (`novastack.inference_service`) as an operational OCI container within a live Docker runtime environment.

All 12 operational validation gates mandated by the ATLAS CTO Directive have been executed and evaluated against the running container, local Ollama daemon, and ATLAS Core service pipeline.

### Status Classification

```
================================================================================
PHASE 5D STATUS: PASS
================================================================================
```

- **VERIFIED**: Gates 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12
- **OBSERVED**: Gate 11 (Host-specific resource observations)
- **NOT VERIFIED**: None
- **BLOCKED**: None (Historical blocked attempt cleared with provenance preserved below)

---

## 2. Historical Provenance & Clearance of Blocked State

| Milestone Stage | Recorded Status | Date | Trigger & Diagnostic Detail |
|---|---|---|---|
| **Phase 5D Initial Evaluation** | **BLOCKED** | 2026-09-19 | Docker CLI and Docker Desktop daemon unavailable on host machine. Execution halted before runtime simulation per engineering rules. |
| **Host Runtime Provisioning** | **READY** | 2026-09-20 | Docker Desktop 29.8.0 installed and running with WSL2 Linux backend (`desktop-linux`, 3.64 GiB allocated). Ollama 0.34.2 serving on host. |
| **Phase 5D Operational Validation** | **PASS** | 2026-09-20 | All 12 runtime gates empirically executed and verified on live container. Zero simulation. |

---

## 3. The 12 Operational Runtime Gates

| Gate | Name | Classification | Result | Empirical Evidence & Verification Details |
|---|---|---|---|---|
| **1** | Docker Availability | **VERIFIED** | **PASS** | Docker CLI 29.8.0, Server 29.8.0, Context `desktop-linux`, 8 CPUs, 3.64 GiB RAM limit, WSL2 backend active. |
| **2** | OCI Image Build | **VERIFIED** | **PASS** | Built `atlas-inference:5d` via `Dockerfile.inference`. Image ID: `sha256:94042560c4df...`, size 9,595 MB, base `python:3.11-slim`, port `8001/tcp`, user `appuser`, healthcheck configured. |
| **3** | Non-Root Execution | **VERIFIED** | **PASS** | Running container `atlas-inference-5d` inspected via `docker exec id`: verified `uid=1000(appuser) gid=1000(appuser)`. Zero root execution. |
| **4** | Health & Readiness | **VERIFIED** | **PASS** | `GET /healthz` $\to$ HTTP 200 `{"status": "ok"}` (964.37 ms first connect). `GET /ready` $\to$ HTTP 200 `{"status": "ready", "backend_connected": true, "model_available": true}` (100.48 ms). |
| **5** | Container $\to$ Ollama Route | **VERIFIED** | **PASS** | Container in `172.17.0.x` bridge routed via `http://host.docker.internal:11434`. Curls `/api/tags` from inside container; confirms `gemma3:1b` (815 MB Q4_K_M GGUF). |
| **6** | Direct Container `/generate` | **VERIFIED** | **PASS** | `POST http://localhost:8001/generate` executes in 3,220 ms (24 output tokens, 14.1 tok/s eval rate). Invalid request schema correctly returns HTTP 422 Unprocessable Entity. Request ID preserved. |
| **7** | Full ATLAS Core E2E Query | **VERIFIED** | **PASS** | Real grounded request (`What was the root cause of incident INC-NS-0001?`) traversed: JWT $\to$ Auth $\to$ Retrieval $\to$ Evidence Resolution $\to$ Adapter $\to$ Container $\to$ Ollama $\to$ Gemma 3 1B $\to$ ATLAS C2 Validator $\to$ 200 OK. Returned 2 VALID C2 citations (`[EVD-001]`, `[EVD-002]`). |
| **8** | Security Perimeter | **VERIFIED** | **PASS** | 8/8 checks pass: Missing JWT $\to$ 401; Invalid JWT $\to$ 401; Tenant mismatch $\to$ 403; Quarantined/unauthorized evidence filtered upstream; Zero JWT, zero auth headers, zero tenant records egress to port 8001. |
| **9** | Failure Isolation | **VERIFIED** | **PASS** | Terminated host Ollama $\to$ container `/ready` returned 503 (`backend_connected=false`), `/generate` returned 503. Zero hung requests, zero ATLAS crashes. Restarted Ollama $\to$ both `/ready` and `/generate` recovered to 200 OK. |
| **10** | Container Restart Recovery | **VERIFIED** | **PASS** | `docker restart atlas-inference-5d` completed in 1,367 ms. Post-restart `/healthz` (200), `/ready` (200), `/generate` (200), and full ATLAS E2E query succeeded in 3,310 ms with 2 VALID C2 citations. |
| **11** | Resource Observations | **OBSERVED** | **OBSERVED** | Host measurements: Container RSS 34.09 MiB (0.91% of limit, 0.28% CPU). Ollama host RSS 932.75 MiB (`ollama.exe` 32.27 MB + `llama-server.exe` 900.48 MB). Combined footprint: ~966.8 MiB. Direct generation: 3,220 ms. |
| **12** | Repository Regression Suite | **VERIFIED** | **PASS** | Full suite execution: `752 passed, 115 deselected in 143.85s`. Zero failures, zero test modifications. |

---

## 4. Host-Specific Resource Observations (Gate 11)

> [!NOTE]
> These measurements represent empirical observations on this specific host environment:
> - **Host OS**: Windows 11 Home (Build 26200 x64)
> - **CPU**: Intel Core i3-N305 (8 logical cores)
> - **System RAM**: ~7.63 GB total
> - **Docker Resource Allocation**: 3.64 GiB max (WSL2 backend)

| Component | Observed Metric | Value | Context |
|---|---|---|---|
| **Inference Container (`atlas-inference-5d`)** | Resident Set Size (RSS) | `34.09 MiB` | Fast, lightweight ASGI microservice |
| **Inference Container (`atlas-inference-5d`)** | Docker Memory Utilization | `0.91%` | Out of 3.64 GiB container memory limit |
| **Inference Container (`atlas-inference-5d`)** | CPU Utilization (Idle/Active) | `0.28%` | Sub-1% baseline utilization |
| **Host Ollama Daemon (`ollama.exe`)** | Resident Set Size (RSS) | `32.27 MiB` | Local HTTP API server |
| **Host Inference Engine (`llama-server.exe`)** | Resident Set Size (RSS) | `900.48 MiB` | `gemma3:1b` Q4_K_M GGUF model in memory |
| **Combined Inference Stack Footprint** | Total Memory Footprint | `~966.84 MiB` | Well within host available capacity |
| **Direct Generation Latency** | Warm-Model Latency | `3,220.2 ms` | 22 prompt tokens, 24 generated tokens |
| **Generation Evaluation Speed** | Token Generation Rate | `~14.1 tokens/sec` | Efficient quantized CPU inference |

---

## 5. Production Default Preservation Invariants

Throughout Phase 5D:
1. `AtlasServicePipeline.create_default()` strictly preserves `LocalHuggingFaceProvider` (`google/gemma-3-1b-it` on CPU with `torch.float32`).
2. `create_app()` retains default wiring to `LocalHuggingFaceProvider`.
3. The standalone containerized inference runtime (`InferenceServiceAdapter`) remains strictly decoupled and opt-in.
4. No retrieval, reranking, evidence assembly, C2 citation validation, prompt templates, or evaluation datasets have been modified.
5. `pyproject.toml` version remains at `0.4.14`.
6. `production_default_changed`: `false`
7. `production_promotion`: `false`

---

## 6. Phase 5E Boundary

In accordance with CTO Directives:
- **Phase 5E is NOT started.**
- **The 120-case benchmark was NOT executed.**
- All Phase 5D prerequisites are now completed and verified with empirical runtime evidence.
