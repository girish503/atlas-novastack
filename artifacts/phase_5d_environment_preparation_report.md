# ATLAS PHASE 5D — ENVIRONMENT PREPARATION REPORT
## Principal Engineer Operational Assessment & Repository Readiness Report

---

### Executive Summary

In accordance with the ATLAS CTO Directive, the repository has been audited and prepared so that **Phase 5D: Container Runtime Validation & Operational Integration** can be executed immediately when a Docker-capable host environment is provisioned.

The previously established and verified status of Phase 5D:
```
PHASE 5D STATUS: BLOCKED
REASON: Docker runtime unavailable
```
remains **fully preserved as historical empirical evidence** in:
- `artifacts/phase_5d_container_runtime_report.md`
- `artifacts/phase_5d_container_runtime.json`
- `docs/PHASE_5D_CONTAINER_RUNTIME.md`
- `scripts/phase_5d_container_runtime.py`

No environment modifications have been made to simulate Docker, manufacture a fake pass, or replace Docker with an unapproved runtime. The production default runtime remains strictly `LocalHuggingFaceProvider` with `google/gemma-3-1b-it` (CPU `torch.float32`).

---

### 1. Repository Inspection & Readiness Audit

An end-to-end audit of all Phase 5C/5D assets was completed:

1. **`Dockerfile.inference`**:
   - Base image: `python:3.11-slim`
   - Hardening: Dedicated non-root user `appuser` (explicit UID/GID 1000)
   - Port exposure: Port 8001 strictly exposed (ATLAS core port 8000 is isolated)
   - Healthcheck: Native OCI `HEALTHCHECK` curling `http://localhost:8001/healthz`
   - Entrypoint: `CMD ["uvicorn", "novastack.inference_service.app:app", "--host", "0.0.0.0", "--port", "8001"]`
   - Multi-line `ENV` instruction formatting verified with proper line continuations.

2. **`src/novastack/inference_service/config.py`**:
   - Updated `InferenceServiceConfig.from_env()` to recognize both `ATLAS_INFERENCE_*` and standard container `INFERENCE_*` environment variables (`INFERENCE_BACKEND_URL`, `INFERENCE_MODEL_NAME`, `INFERENCE_SERVICE_PORT`).
   - Ensures any host network bridge or proxy address (e.g. `http://host.docker.internal:11434` or `--network host`) can be injected dynamically without changing container code.

3. **`src/novastack/inference_client.py` & `src/novastack/provider.py`**:
   - `InferenceServiceClient` and `InferenceServiceAdapter` confirmed ready to connect to any reachable HTTP URL across the container boundary.

4. **`scripts/phase_5d_container_runtime.py`**:
   - Upgraded to be fully environment-aware. It explicitly probes conditions A through F:
     * Condition A: Docker CLI missing (`DOCKER_CLI_MISSING`)
     * Condition B: Docker daemon unavailable (`DOCKER_DAEMON_UNAVAILABLE`)
     * Condition C: Docker available (`DOCKER_AVAILABLE`)
     * Condition D: Ollama unavailable (`OLLAMA_UNAVAILABLE`)
     * Condition E: Ollama reachable (`OLLAMA_REACHABLE`)
     * Condition F: Target model status (`REQUIRED_MODEL_UNAVAILABLE` / `gemma3:1b` ready)
   - Enforces strict non-bypass gating: automatically halts with `BLOCKED` when Docker is unavailable, and with `HOLD` when Ollama is unreachable.

---

### 2. Container $\to$ Ollama Network Topology Specification

A major operational hazard in containerized LLM deployments is assuming `127.0.0.1:11434` inside a container accesses the host loopback.

In Phase 5D runtime testing, the network path must be established explicitly:
```
┌─────────────────────────────────────────────────────────────────┐
│ DOCKER CONTAINER (atlas-inference-5d, port 8001)                 │
│ - novastack.inference_service.app                               │
│ - Running as appuser (UID 1000)                                 │
└───────────────────────────────┬─────────────────────────────────┘
                                │ Configured via INFERENCE_BACKEND_URL
                                ▼
         [Actual Host Network Path / Bridge Gateway]
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ HOST SYSTEM (Ollama Daemon, port 11434)                         │
│ - ollama.exe / llama-server.exe                                 │
│ - Serving model: gemma3:1b (Q4_K_M GGUF)                        │
└─────────────────────────────────────────────────────────────────┘
```

Supported deployment topographies:
- **Linux with host networking**: Run with `--network host`. Backend URL: `http://127.0.0.1:11434`.
- **Windows / macOS with Docker Desktop**: Run with `-p 8001:8001`. Backend URL: `http://host.docker.internal:11434`.
- **Linux standard bridge**: Run with `-p 8001:8001`. Backend URL: `http://172.17.0.1:11434` (or host gateway IP).

---

### 3. Separation of Verification Statuses

Per Section 9 of the Directive, all findings are categorized into the mandated sections:

#### A. VERIFIED
- **Repository / Harness Readiness**: `scripts/phase_5d_container_runtime.py` is fully prepared with environment detection A-F and configurable Ollama endpoints.
- **Static Contract Conformance (819/819 Tests Passed)**:
  - `tests/test_phase_5c_inference_container.py`: **20/20 PASS**
  - `tests/test_phase_5c_inference_service.py`: **15/15 PASS**
  - `tests/test_phase_5c_inference_contract.py`: **15/15 PASS**
  - `tests/test_phase_5c_inference_security.py`: **11/11 PASS**
  - `tests/test_phase_5b_quantized_provider.py`: **17/17 PASS**
  - `tests/test_phase_5a_provider_boundary.py`: **23/23 PASS**
  - Fast Unit Regression: **718/718 PASS**
- **Zero-Trust Security Boundary**: Verified that zero JWTs, bearer tokens, or user credentials leak across the inference boundary; upstream fail-closed 401/403 gateway rejection remains intact.
- **C2 Citation Authority**: Layer 4 C2 citation validation remains strictly internal to ATLAS.
- **Production Default Preservation**: `AtlasServicePipeline.create_default()` and `create_app()` remain `LocalHuggingFaceProvider` (`google/gemma-3-1b-it`, CPU `torch.float32`).
- **No Production Promotion**: The quantized provider and inference service remain experimental.

#### B. BLOCKED
- **Docker Runtime Execution**: Docker CLI and Docker daemon are unavailable on the current Windows host environment.

#### C. NOT VERIFIED (Halted at Gate 1 Prerequisite)
- Actual OCI container build (`docker build -f Dockerfile.inference -t atlas-inference:5d .`)
- Actual container startup and daemon lifecycle
- Container $\to$ Ollama bridge network reachability
- Real HTTP `POST /generate` against a live container port
- End-to-end containerized ATLAS $\to$ Container $\to$ Ollama $\to$ C2 query
- Runtime failure isolation (terminating Ollama while container runs)
- Runtime container restart recovery sequence

---

### 4. Reproducible Execution Checklist

A dedicated operational playbook has been generated in [docs/PHASE_5D_RUNTIME_CHECKLIST.md](./docs/PHASE_5D_RUNTIME_CHECKLIST.md).

It contains the exact commands and criteria for the 17-step runtime sequence:
1. `docker --version`
2. `docker info`
3. `docker build -f Dockerfile.inference -t atlas-inference:5d .`
4. `docker inspect atlas-inference:5d`
5. `docker run -d --name atlas-inference-5d ...` & verify `id` (non-root UID 1000)
6. `GET /healthz` (liveness)
7. `GET /ready` (readiness)
8. Establish container $\to$ Ollama connectivity
9. `POST /generate` (bounded generation)
10. End-to-end ATLAS $\to$ container $\to$ Ollama $\to$ C2 query
11. Authentication & security boundary tests (401 / 403 / zero JWT egress)
12. Failure isolation: Stop Ollama and verify bounded 503 / structured abstention
13. Restore Ollama and verify container recovers to 200 OK
14. Restart inference container independently
15. Verify post-restart recovery without ATLAS code changes
16. Run full regression suite (819 tests)
17. Collect runtime evidence and update Phase 5D artifacts

---

### 5. Final Status Block

```
PHASE 5D ENVIRONMENT PREPARATION: READY
```

STOP.
