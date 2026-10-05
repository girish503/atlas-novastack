# PHASE 5D: CONTAINER RUNTIME VALIDATION & OPERATIONAL INTEGRATION
**ATLAS Architecture & Technical Specification Document**

---

## 1. Executive Summary & Objective

Phase 5D empirically demonstrates that the Phase 5C standalone inference service (`novastack.inference_service`) functions as an operational OCI container component within a live Docker runtime environment, rather than relying exclusively on static Dockerfile inspection or in-memory mock harnesses.

With the successful provisioning and verification of Docker Desktop 29.8.0 and host Ollama 0.34.2 with `gemma3:1b`, all 12 operational gates mandated by the ATLAS CTO Directive have been executed and verified in real runtime conditions without mocking, faking, or modifying production defaults.

```
================================================================================
PHASE 5D STATUS: PASS
================================================================================
```

---

## 2. Classification of Gates

### 2.1 VERIFIED
- **Gate 1 (Docker Availability)**: Docker CLI 29.8.0, daemon responsive in `desktop-linux` context, WSL2 engine responsive.
- **Gate 2 (OCI Image Build)**: `atlas-inference:5d` built cleanly via `Dockerfile.inference` (`sha256:94042560c4df...`, 9,595 MB, `python:3.11-slim`, port 8001, `appuser` UID 1000, HEALTHCHECK enabled).
- **Gate 3 (Non-Root Execution)**: Live container `atlas-inference-5d` running as `uid=1000(appuser) gid=1000(appuser)`. Zero root execution.
- **Gate 4 (Health & Readiness)**: `/healthz` returns 200 OK (`{"status":"ok"}`); `/ready` returns 200 OK (`{"status":"ready","backend_connected":true,"model_available":true}`).
- **Gate 5 (Container $\to$ Ollama Network)**: Container in `172.17.0.x` bridge connects to `http://host.docker.internal:11434/api/tags`; confirms `gemma3:1b`.
- **Gate 6 (Direct /generate)**: `POST http://localhost:8001/generate` executes in 3,220 ms (24 tokens, 14.1 tok/s); invalid request bounds fail closed with HTTP 422.
- **Gate 7 (Full ATLAS Core E2E)**: Real grounded query (`What was the root cause of incident INC-NS-0001?`) traverses full pipeline and returns 200 OK with 2 VALID C2 citations (`[EVD-001]`, `[EVD-002]`).
- **Gate 8 (Security Perimeter)**: 8/8 checks pass: missing JWT $\to$ 401, invalid JWT $\to$ 401, tenant mismatch $\to$ 403, upstream quarantine intact, zero JWT/auth headers/tenant records egress to port 8001.
- **Gate 9 (Failure Isolation)**: Stopped host Ollama causes 503 on `/ready` & `/generate`; pipeline abstains cleanly without hung requests or crashes; restarting Ollama recovers both endpoints to 200 OK.
- **Gate 10 (Container Restart Recovery)**: `docker restart atlas-inference-5d` completes in 1,367 ms; `/healthz`, `/ready`, `/generate`, and ATLAS E2E query succeed post-restart.
- **Gate 12 (Regression Suite)**: `752 passed, 115 deselected in 143.85s`. Zero failures, zero test modifications.

### 2.2 OBSERVED
- **Gate 11 (Resource Observations)**: Host-specific measurements on Windows 11 Home (Intel Core i3-N305, 8GB RAM):
  - Container RSS: `34.09 MiB` (0.91% of 3.64 GiB Docker limit)
  - Container CPU: `0.28%`
  - Ollama host RSS: `932.75 MiB` (`ollama.exe` 32.27 MB + `llama-server.exe` 900.48 MB)
  - Combined inference stack footprint: `~966.8 MiB`
  - Direct generation latency: `3,220.2 ms` (~14.1 tokens/sec)

### 2.3 NOT VERIFIED
- None. All required gates were empirically executed and verified.

### 2.4 BLOCKED (Historical Provenance)
- **Initial Status (2026-09-19)**: BLOCKED due to Docker CLI and daemon being unavailable on the host machine.
- **Resolution (2026-09-20)**: Docker Desktop 29.8.0 provisioned with WSL2 backend. Blocked state cleared and all runtime gates successfully executed.

---

## 3. Environment Architecture & Network Topology

```
+-----------------------------------------------------------------------------------+
| Host Environment (Windows 11 Home 26200 x64, Intel Core i3-N305, 8GB RAM)          |
|                                                                                   |
|  +---------------------------+             +-----------------------------------+  |
|  | ATLAS Core Application    |             | Ollama Host Daemon (11434)        |  |
|  | (AtlasServicePipeline)    |             | - ollama.exe (32.3 MB RSS)        |  |
|  | - Port 8000 (JWT Boundary)|             | - llama-server.exe (900.5 MB RSS) |  |
|  | - InferenceServiceAdapter |             | - Model: gemma3:1b (GGUF Q4_K_M)  |  |
|  +-------------+-------------+             +-----------------+-----------------+  |
|                |                                             ^                    |
|       HTTP POST| localhost:8001                              | HTTP POST          |
|       (Payload | with NO auth)                               | host.docker.       |
|                v                                             | internal:11434     |
|  +-------------+---------------------------------------------+-----------------+  |
|  | Docker Engine (WSL2 desktop-linux 29.8.0, 3.64 GiB limit)                    |  |
|  | Container: atlas-inference-5d (UID 1000 appuser, 34.09 MB RSS)              |  |
|  | - Liveness:  GET /healthz                                                   |  |
|  | - Readiness: GET /ready                                                     |  |
|  | - Execution: POST /generate                                                 |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## 4. Production Default Preservation Invariants

Throughout Phase 5D:
- `AtlasServicePipeline.create_default()` strictly preserves `LocalHuggingFaceProvider` (`google/gemma-3-1b-it` on CPU with `torch.float32`).
- `create_app()` retains default wiring to `LocalHuggingFaceProvider`.
- The containerized standalone inference runtime remains decoupled and opt-in.
- `production_default_changed`: `false`
- `production_promotion`: `false`
- `pyproject.toml` version remains `0.4.14`.

---

## 5. Artifact Reference

- Operational Execution Script: `scripts/phase_5d_container_runtime.py`
- Structured Telemetry JSON: `artifacts/phase_5d_container_runtime.json`
- Comprehensive Execution Report: `artifacts/phase_5d_container_runtime_report.md`
- Operational Playbook & Checklist: `docs/PHASE_5D_RUNTIME_CHECKLIST.md`

---

## 6. Next Phase Boundary

**Phase 5E is NOT started.**
Per CTO Directive, execution halts following completion and certification of Phase 5D.
