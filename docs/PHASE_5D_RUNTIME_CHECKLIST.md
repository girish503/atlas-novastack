# ATLAS PHASE 5D — REPRODUCIBLE RUNTIME EXECUTION CHECKLIST
**Operational Playbook for Container-Enabled Verification Environments**

---

## Overview

This checklist defines the strict 17-step operational execution sequence for completing **Phase 5D: Container Runtime Validation & Operational Integration** once a Docker-capable host environment (Linux or Windows with Docker Desktop / Docker Engine) is provisioned.

> [!IMPORTANT]
> **Production Default Preservation Invariant:**
> Throughout all 17 steps, `AtlasServicePipeline.create_default()` and `create_app()` MUST remain `LocalHuggingFaceProvider` (`google/gemma-3-1b-it`, `torch.float32` on CPU).
> The containerized inference service and `InferenceServiceAdapter` remain strictly experimental.

---

## The 17-Step Execution Sequence

### Step 1: Verify Docker CLI
Verify that the `docker` command-line executable is available in system `PATH`.
```bash
docker --version
```
- **Expected Exit Code**: `0`
- **Criteria**: Returns valid Docker CLI version string (e.g., `Docker version 24.x.x` or newer).
- **If Absent**: STOP. Status remains `BLOCKED (Docker runtime unavailable)`.

---

### Step 2: Verify Docker Engine / Daemon
Verify that the Docker daemon is running and responsive.
```bash
docker info
```
- **Expected Exit Code**: `0`
- **Criteria**: Outputs server engine details without daemon communication errors (`Cannot connect to the Docker daemon`).
- **If Daemon Stopped**: Start Docker daemon service (`sudo systemctl start docker` or launch Docker Desktop) and re-verify.

---

### Step 3: Build Real OCI Image (`atlas-inference:5d`)
Build the standalone inference service image from `Dockerfile.inference`.
```bash
docker build -f Dockerfile.inference -t atlas-inference:5d .
```
- **Expected Exit Code**: `0`
- **Criteria**: Clean build completing all layers (Python 3.11-slim base, curl install, appuser UID 1000 creation, pyproject install, uvicorn entrypoint).
- **Record**: Build duration, final image ID.

---

### Step 4: Inspect Image Configuration
Statically inspect the built container image metadata to ensure compliance with security invariants before execution.
```bash
docker inspect atlas-inference:5d --format '{{json .Config}}'
```
- **Criteria**:
  - `User`: `appuser` (or UID `1000`)
  - `ExposedPorts`: `8001/tcp` present (port 8000 must NOT be exposed)
  - `Healthcheck`: Configured to curl `http://localhost:8001/healthz`
  - `Cmd`: `["uvicorn", "novastack.inference_service.app:app", "--host", "0.0.0.0", "--port", "8001"]`

---

### Step 5: Run Container Non-Root

> [!WARNING]
> **Historical/reference checklist; SEC-OPS-02 supersedes its former broad host-port publication.** The approved operational command below publishes inference only to host loopback. Do not use `--network host`, `-p 8001:8001`, or `-p 0.0.0.0:8001:8001`.

Launch the container bound only to host loopback port 8001.

*Note on Network Topology*:
- Do not use `--network host` for the inference container.
- On Windows / macOS hosts with Docker Desktop: use `-e INFERENCE_BACKEND_URL=http://host.docker.internal:11434` and verify that Ollama remains private to the host.
- On Linux bridge deployments: use `--add-host=host.docker.internal:host-gateway` and verify host firewall containment for TCP/11434 before proceeding. This repository cannot verify that firewall universally; absence of verification is a readiness block.
```bash
docker run -d \
  --name atlas-inference-5d \
  -p 127.0.0.1:8001:8001 \
  --add-host=host.docker.internal:host-gateway \
  -e INFERENCE_BACKEND_URL="http://host.docker.internal:11434" \
  atlas-inference:5d
```
Verify process user inside the running container:
```bash
docker exec atlas-inference-5d id
```
- **Criteria**: Output must show `uid=1000(appuser) gid=1000(appuser)`. Must NOT run as root (`uid=0`).

---

### Step 6: Verify `/healthz` Liveness Probe
Issue a GET request against the running container's liveness probe.
```bash
curl -f -s http://localhost:8001/healthz
```
- **Expected HTTP Status**: `200 OK`
- **Criteria**: Response body matches `{"status": "ok", "service": "atlas-inference-service"}`.
- **Security Check**: Response contains zero authorization or credential fields.

---

### Step 7: Verify `/ready` Readiness Probe
Issue a GET request against the running container's readiness probe.
```bash
curl -f -s http://localhost:8001/ready
```
- **Expected HTTP Status**: `200 OK`
- **Criteria**: `{"status": "ready", "backend": "ollama", "backend_connected": true, "model_name": "gemma3:1b", "model_available": true}`.
- **If 503**: Backend connection failed; inspect Step 8.

---

### Step 8: Establish Container $\to$ Ollama Network Topology
Formally establish and document the actual network path between container and Ollama daemon.
```bash
docker exec atlas-inference-5d curl -s -f http://host.docker.internal:11434/api/tags
```
- **Documented Route**:
  $$\text{Container (172.17.x.x)} \longrightarrow \text{Host Gateway / Bridge} \longrightarrow \text{Ollama Daemon (11434)} \longrightarrow \text{llama-server.exe} \longrightarrow \text{gemma3:1b}$$
- **Verification**: Container must successfully receive the models JSON payload from Ollama containing `gemma3:1b`.

---

### Step 9: Call `POST /generate` on Running Container
Execute a direct bounded generation request across port 8001.
```bash
curl -s -X POST http://localhost:8001/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Answer in one sentence: What is an evidence-grounded search platform?",
    "request_id": "req-5d-test-01",
    "max_new_tokens": 64,
    "temperature": 0.0,
    "model_name": "gemma3:1b"
  }'
```
- **Criteria**:
  - HTTP `200 OK`
  - Valid `InferenceGenerationResponse` schema (`generated_text`, `prompt_tokens`, `output_tokens`, `engine_telemetry`)
  - Valid bounds enforcement: empty prompt rejected with `422`; oversized tokens rejected with `422`.

---

### Step 10: Run End-to-End ATLAS $\to$ Container $\to$ Ollama $\to$ C2 Pipeline
Run an authentic query through ATLAS Core with `InferenceServiceAdapter` pointed at the container:
```python
adapter = InferenceServiceAdapter(service_url="http://localhost:8001")
pipeline = AtlasServicePipeline(..., generator=adapter)
```
Query: `"What was the root cause of incident INC-NS-0001?"` with valid JWT.
- **Criteria**:
  - HTTP `200 OK`
  - `answer_status="answered"`
  - Factually accurate answer containing `"connection pool leak"`
  - Structured C2 citation attached: `status="VALID"`, reason `verified_selected_authorized_evidence`

---

### Step 11: Security & Identity Boundary Invariant Tests
Execute automated security gate tests against the containerized pipeline:
1. **Unauthenticated Query**: Send query without `Authorization` header $\to$ Rejection with HTTP `401 Unauthorized` before reaching provider.
2. **Invalid JWT**: Send query with tampered JWT $\to$ Rejection with HTTP `401 Unauthorized`.
3. **Cross-Tenant Request**: Send query with tenant mismatch $\to$ Rejection with HTTP `403 Forbidden`.
4. **Zero-Token Egress Verification**: Inspect payload egress to container port 8001 $\to$ Zero JWTs, zero bearer tokens, zero user credentials.

---

### Step 12: Failure Isolation — Stop Ollama Backend
Test bounded failure behavior when the model daemon is stopped.
1. Terminate or pause Ollama (`taskkill /F /IM ollama.exe` or `systemctl stop ollama`).
2. Query container `/ready` $\to$ Returns HTTP `503 Service Unavailable`.
3. Query container `/generate` $\to$ Returns HTTP `503 Service Unavailable`.
4. Query ATLAS pipeline $\to$ Cleanly returns `answer_status="abstained"`, `abstention_reason="service_unavailable"`. No hung threads, no uncaught exceptions.

---

### Step 13: Restore Ollama Backend
1. Restart Ollama daemon (`ollama serve`).
2. Verify Ollama reaches readiness on port 11434.
3. Query container `/ready` $\to$ Recovers cleanly to HTTP `200 OK` (`backend_connected=true`).

---

### Step 14: Container Lifecycle — Restart Inference Container
Test independent container restart behavior while ATLAS remains running.
```bash
docker restart atlas-inference-5d
```
- **Criteria**: Container restarts cleanly without error, healthcheck transitions to healthy, port 8001 rebinds.

---

### Step 15: Post-Restart Recovery Verification
Re-test full pipeline query after container restart:
1. Probe `/healthz` $\to$ HTTP `200 OK`.
2. Probe `/ready` $\to$ HTTP `200 OK`.
3. Send ATLAS end-to-end query $\to$ Returns HTTP `200 OK`, `answer_status="answered"`.
- **Criteria**: Zero state corruption, zero manual cache clears required.

---

### Step 16: Full Regression Suite Execution
Execute the entire repository regression suite against the updated environment:
```bash
pytest tests/ -v
```
- **Criteria**: 100% pass across all test suites (Phase 5C, 5B, 5A, Fast Regression).

---

### Step 17: Collect Runtime Evidence & Generate Phase 5D Artifacts
1. Capture runtime metrics:
   - Container RSS (`docker stats --no-stream atlas-inference-5d`)
   - Host Ollama process RSS (`llama-server.exe`)
   - Generation latency and token throughput
2. Run operational harness to emit final JSON and Markdown artifacts:
   ```bash
   python scripts/phase_5d_container_runtime.py
   ```
3. Update `artifacts/phase_5d_container_runtime_report.md` and `artifacts/phase_5d_container_runtime.json` with live operational metrics.
