# PHASE 5C: INFERENCE RUNTIME SERVICE BOUNDARY & CONTAINERIZATION
**ATLAS Architecture & Technical Specification Document**

---

## 1. Executive Summary & Context

Phase 5C establishes a clean, decoupled, independently deployable HTTP microservice boundary for the experimental quantized local inference backend (`Ollama` $\to$ `llama.cpp` $\to$ `gemma3:1b Q4_K_M GGUF`).

Building directly upon Phase 5A (Provider Abstraction Boundary), Phase 5B (Quantized Inference Feasibility), and Phase 5B-R1 (Multi-Process & Concurrency Characterization), Phase 5C achieves:
1. **Network Boundary Isolation**: Complete separation of model execution from ATLAS search, indexing, authorization, and document storage.
2. **Zero-Trust Security Boundary**: The inference microservice receives zero authorization materials (0 JWTs, 0 Bearer tokens, 0 caller IDs, 0 tenant databases). Upstream ATLAS remains the sole authority for caller authentication and tenant isolation.
3. **C2 Citation Integrity**: Layer 4 C2 citation validation remains exclusively within the ATLAS provider boundary. The inference service is merely a text completion worker and possesses zero citation authority.
4. **Resilience & Fault Isolation**: Service unreachability and deadline timeouts map cleanly to controlled abstention (`AnswerStatus.ABSTAINED`) without taking down ATLAS.
5. **Container Specification**: A hardened, non-root OCI container specification (`Dockerfile.inference`) based on `python:3.11-slim` with built-in healthchecking.

> [!IMPORTANT]
> **Production Default Preservation Invariant:**
> The ATLAS production default runtime remains **strictly unchanged**:
> `AtlasServicePipeline.create_default()` $\longrightarrow$ `LocalHuggingFaceProvider` (`google/gemma-3-1b-it`, `torch.float32` on CPU).
> The standalone inference service and `InferenceServiceAdapter` are strictly experimental. No production promotion occurs in this phase.

---

## 2. Decoupled Service Architecture

```
====================================================================================================
CLIENT REQUEST (JWT, Tenant Context)
      │
      ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ATLAS ENTERPRISE CORE (Port 8000)                                                                │
│                                                                                                  │
│  [Gateway Auth & Tenant Isolation]  <-- Fail-Closed: Rejects 401 unauth / 403 tenant mismatch    │
│            │                                                                                     │
│  [BM25 + Dense Hybrid Retrieval]   <-- Tenant-scoped search across authorized corpus             │
│            │                                                                                     │
│  [Evidence Resolution Engine]       <-- Resolves conflicts, provenance, builds EvidencePackage    │
│            │                                                                                     │
│  [InferenceServiceAdapter (Provider Layer)]                                                      │
│      ├─ Layer 1: Pre-generation Gate (Abstains on empty evidence or unresolvable conflict)       │
│      ├─ Layer 2: Token Budgeting & Grounded Prompt Assembly                                      │
│      ├─ Layer 3: Dispatch via InferenceServiceClient (HTTP POST http://localhost:8001/generate)   │
│      └─ Layer 4: C2 Citation Validation & Anti-Hallucination Gate                                │
└────────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                 │ HTTP JSON Payload:
                                                 │ - prompt (rendered text)
                                                 │ - max_new_tokens (64-1024)
                                                 │ - temperature (0.0)
                                                 │ - request_id (correlation ID)
                                                 │ ZERO JWTs / ZERO Tenant IDs / ZERO Auth Headers
                                                 ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ATLAS STANDALONE INFERENCE SERVICE (Port 8001 / Container)                                       │
│                                                                                                  │
│  FastAPI Application (novastack.inference_service.app)                                           │
│  - GET  /healthz  --> Process liveness probe (200 OK)                                            │
│  - GET  /ready    --> Backend connectivity probe (checks Ollama /api/tags for target model)      │
│  - POST /generate --> Validates bounds, forwards raw prompt to Ollama daemon                     │
│                                                 │                                                │
│                                                 │ HTTP POST http://127.0.0.1:11434/api/generate  │
│                                                 ▼                                                │
│ ┌──────────────────────────────────────────────────────────────────────────────────────────────┐ │
│ │ OLLAMA DAEMON / LLAMA.CPP RUNTIME                                                            │ │
│ │ - Dedicated C++ Inference Worker (llama-server.exe)                                          │ │
│ │ - Model: gemma3:1b (Q4_K_M GGUF, 815 MB disk, ~856 MB RSS)                                  │ │
│ └──────────────────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Microservice Specifications

### 3.1 Components & Package Structure

The inference service is packaged inside `src/novastack/inference_service/`:
- `config.py`: Environment-driven `InferenceServiceConfig`.
  - `backend_url`: URL of Ollama backend (default `http://127.0.0.1:11434`).
  - `model_name`: Target quantized model (default `gemma3:1b`).
  - `connect_timeout_seconds`: Backend connect timeout (default 2.0s).
  - `read_timeout_seconds`: Backend generation read timeout (default 25.0s).
  - `service_port`: Service listening port (default 8001).
- `schemas.py`: Pydantic V2 request and response contracts:
  - `InferenceGenerationRequest`: Minimal prompt, tokens, temperature, correlation ID.
  - `InferenceGenerationResponse`: Generated text, token counts, engine timings.
  - `InferenceHealthResponse`: Liveness status (`{"status": "ok", "service": "atlas-inference-service"}`).
  - `InferenceReadyResponse`: Readiness status (`{"status": "ready", "backend_connected": true, ...}`).
  - `InferenceErrorResponse`: Sanitized error response with status code and error type.
- `app.py`: Factory `create_inference_app(config)` and default ASGI `app` instance.
- `client.py` (`src/novastack/inference_client.py`): Robust `InferenceServiceClient` providing synchronous HTTP transport across the boundary, connection pooling, and error mapping.
- `adapter.py` (`InferenceServiceAdapter` in `src/novastack/quantized_provider.py`): Concrete provider conforming to `AnswerGeneratorProvider`.

### 3.2 Endpoints Specification

| Method | Path | Description | Expected Status |
|---|---|---|---|
| `GET` | `/healthz` | Process liveness probe. Fast in-memory response. | 200 OK |
| `GET` | `/ready` | Readiness probe. Pings Ollama `/api/tags` to ensure `gemma3:1b` is available. | 200 OK / 503 Service Unavailable |
| `POST` | `/generate` | Executes prompt generation on quantized backend. | 200 OK / 422 Unprocessable / 503 Backend Down / 504 Backend Timeout |

---

## 4. Security Invariants & Isolation Verification

### 4.1 Boundary Security Proof

The inference service operates strictly behind the ATLAS security perimeter:
1. **Zero Auth Materials**: Inspection of requests sent to the inference service confirms zero Authorization headers, zero Bearer tokens, zero JWTs, and zero database credentials.
2. **Fail-Closed Gateway Enforcement**:
   - Request with missing `Authorization` header $\longrightarrow$ Rejected with **HTTP 401 Unauthorized** before retrieval or provider invocation.
   - Request with tenant token mismatch (`tenant_id="TENANT-OTHER"`) $\longrightarrow$ Rejected with **HTTP 403 Forbidden** before retrieval or provider invocation.
3. **Evidence Filtering Before Generation**:
   - Quarantined or unauthorized documents are filtered in upstream ATLAS search and evidence resolution. The inference service prompt never receives unauthorized texts.
4. **Citation Validation Isolation**:
   - The inference service output is strictly untrusted text. ATLAS Layer 4 validator parses citations (`[EVD-001]`), verifies chunk existence against the authorized corpus snapshot, validates phrase overlap, and marks citations as `VALID` or `INVALID`.

---

## 5. Container Specification (`Dockerfile.inference`)

A dedicated container file `Dockerfile.inference` is provided in the repository root:
- **Base Image**: `python:3.11-slim` (minimal attack surface, no unnecessary compiler tools).
- **Non-Root Execution**: Runs as dedicated `appuser` (explicit UID/GID 1000).
- **Hardening**: `rm -rf /var/lib/apt/lists/*`, byte-code write disabled, stdout unbuffered.
- **Port**: Strictly exposes port `8001` (avoiding collision with ATLAS core port `8000`).
- **Healthcheck**: Configured with Docker native `HEALTHCHECK`:
  ```dockerfile
  HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
      CMD curl -f http://localhost:8001/healthz || exit 1
  ```
- **Entrypoint**: Clean ASGI execution:
  ```dockerfile
  CMD ["uvicorn", "novastack.inference_service.app:app", "--host", "0.0.0.0", "--port", "8001"]
  ```

---

## 6. Empirical Verification Results

Measurements captured during Phase 5C end-to-end verification (`artifacts/phase_5c_inference_service_boundary.json`):

| Metric / Check | Observed Value | Status |
|---|---|---|
| `/healthz` Liveness Latency | **12.67 ms** | PASS |
| `/ready` Readiness Probe Latency | **764.81 ms** | PASS |
| Direct `/generate` Service Latency | **1855.15 ms** (21 tokens) | PASS |
| End-to-End Pipeline Latency (Auth $\to$ Retrieval $\to$ Service $\to$ C2) | **2285.50 ms** | PASS |
| End-to-End Answer Correctness | Grounded root cause answer + `[EVD-002]` citation | PASS |
| C2 Citation Validation Status | `status="VALID"`, verified against authorized evidence | PASS |
| Unauthenticated Gateway Rejection | HTTP 401 | PASS |
| Cross-Tenant Gateway Rejection | HTTP 403 | PASS |
| Intentional Abstention on Missing Evidence | `status="abstained"`, provider skipped | PASS |
| ATLAS Python Process RSS | **134.89 MB** | PASS |
| Dedicated llama-server.exe RSS | **856.76 MB** | PASS |
| Combined Host Footprint | **~991 MB** | PASS |
| Dockerfile Static Analysis Suite | 61/61 tests passed | PASS |
| Regression Test Suite (5A + 5B + Fast) | 40/40 + 718/718 passed | PASS |

---

## 7. Architectural Decisions & Production Invariants

1. **Production Default Remains Local PyTorch/HuggingFace**:
   - `AtlasServicePipeline.create_default()` instantiates `LocalHuggingFaceProvider`.
   - `create_app()` defaults to `LocalHuggingFaceProvider`.
2. **Inference Service is Opt-In**:
   - To use the inference service, callers explicitly inject `InferenceServiceAdapter(service_url=...)`.
3. **No Promotion to Production Default**:
   - Per CTO Directive, Phase 5C validates the service boundary architecture. Promotion to production default requires formal SLA certification and multi-day soak testing.
