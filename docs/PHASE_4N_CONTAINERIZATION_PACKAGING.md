# ATLAS Phase 4N: Containerization & Dependency Packaging

## Executive Summary

Pursuant to the ATLAS CTO Directive, **Phase 4N: Containerization & Dependency Packaging** has been executed and validated. The ATLAS enterprise search and grounded answering service has been packaged into a reproducible modern Python package, containerized under a hardened OCI Docker specification, configured with automated continuous integration (CI) workflows, and verified across all operational and regression test suites.

All core production configuration defaults remain strictly frozen:
- `enable_boundary_stitching = False` (Mechanism A: OFF)
- `enable_query_aware_authority = True` (Mechanism B: ON, certified production default)
- `enable_event_bundling = False` (Mechanism C: OFF)

Across the complete test suite (Phase 4N Packaging, Phase 4M API Service, Phase 4M Fail-Closed Authorization, Phase 4K-G Promotion, Phase 4K-F Red-Team, Phase 4K-E Validation, and Canonical Baseline Freeze), **53 out of 53 tests passed with 0 regressions**.

---

## 1. Deliverable 1: Python Package Configuration (`pyproject.toml`)

### 1.1 Specification & Standards Compliance
A modern, standard-compliant `pyproject.toml` (PEP 517, PEP 518, PEP 621) was created in the workspace root:
- **Build System**: `setuptools.build_meta` with `setuptools>=61.0`.
- **Package Name**: `atlas-novastack`
- **Package Version**: `0.4.14`
- **Python Compatibility**: `>=3.10`
- **Package Discovery**: `[tool.setuptools.packages.find]` configured with `where = ["src"]`.
- **Test Runner Configuration**: `[tool.pytest.ini_options]` with `pythonpath = ["src"]` and `testpaths = ["tests"]`.

### 1.2 Dependency Audit
AST-level import inspection across all source files identified the exact minimal runtime dependencies:

| Runtime Dependency | Version Constraint | Function in ATLAS |
|---|---|---|
| `fastapi` | `>=0.110.0,<1.0.0` | High-performance asynchronous HTTP service framework |
| `pydantic` | `>=2.7.0,<3.0.0` | Strict data validation, fail-closed caller schema modeling |
| `uvicorn` | `>=0.28.0,<1.0.0` | Production ASGI server runtime |
| `torch` | `>=2.0.0,<3.0.0` | Deep learning tensor engine for embedding and generation models |
| `transformers` | `>=4.40.0,<6.0.0` | Gemma 3 1B IT model loader and inference engine |
| `sentence-transformers` | `>=3.0.0,<7.0.0` | BAAI/bge-small-en-v1.5 dense index embedding loader |
| `numpy` | `>=1.24.0,<3.0.0` | Array calculations, dense cosine similarity, fusion calculations |
| `httpx` | `>=0.27.0,<1.0.0` | HTTP transport client / ASGI test client |

**Optional / Development Dependencies**:
- `dev` / `test`: `pytest>=7.4.0,<9.0.0`

### 1.3 Clean-Environment Installation Verification
- Standard and editable builds were verified via pip wheel creation (`atlas_novastack-0.4.14-py3-none-any.whl`, 238 KB).
- A clean, isolated Python virtual environment was constructed dynamically in a fresh temporary directory without shared site-packages. `atlas-novastack` installed cleanly and imported successfully with exit code 0.

---

## 2. Deliverable 2: Containerization (`Dockerfile`)

### 2.1 Base Image Rationale
The service container is built upon `python:3.11-slim`:
- **Stability & Security**: Official Debian Bookworm-based OCI image with minimal pre-installed packages, reducing vulnerability surface.
- **Binary Compatibility**: Provides full manylinux wheel compatibility for PyTorch, NumPy, and Transformers, completely eliminating the need for bulky C/C++ compilation toolchains in production.
- **Image Size**: Slim footprint (~150MB base) compared to full Python images (>1GB).

### 2.2 Security Hardening & Container Directives
1. **Environment Variables**:
   - `PYTHONDONTWRITEBYTECODE=1`: Suppresses `.pyc` creation on container runtime.
   - `PYTHONUNBUFFERED=1`: Ensures immediate log flushing to stdout/stderr.
   - `PIP_NO_CACHE_DIR=1`, `PIP_DISABLE_PIP_VERSION_CHECK=1`: Prevents layer bloat.
   - `PORT=8000`, `HOST=0.0.0.0`: Binds network interfaces predictably.
2. **User Isolation**:
   - Creates a dedicated non-privileged user and group: `atlas:atlas` (`groupadd -r atlas && useradd -r -g atlas -d /app -s /sbin/nologin atlas`).
   - Switches execution context to `USER atlas`.
3. **Port & Entrypoint**:
   - `EXPOSE 8000`: Formally exposes service port.
   - `CMD ["uvicorn", "novastack.service.api:app", "--host", "0.0.0.0", "--port", "8000"]`: Executes production ASGI server.
4. **Container Healthcheck**:
   - `HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD curl -f http://localhost:8000/healthz || exit 1`.

---

## 3. Deliverable 3: Build Context Optimization (`.dockerignore`)

The `.dockerignore` excludes non-runtime files to accelerate container build times and prevent secrets or artifacts from leaking into production layers:
- **Excludes**:
  - Version control files (`.git/`, `.gitignore`).
  - Python bytecode and build artifacts (`__pycache__/`, `*.py[cod]`, `build/`, `dist/`, `*.egg-info/`).
  - Virtual environments (`.venv/`, `venv/`, `env/`, `scratch/`).
  - Test suites and test caches (`tests/`, `.pytest_cache/`, `.coverage`).
  - Evaluation notebooks and documentation (`docs/`, `artifacts/`).
- **Preserves Essential Data Assets**:
  - `data/raw/novastack/` (fixtures, catalogs, schemas).
  - `data/processed/novastack/` (search documents, chunks, dense index metadata, embeddings).
  - `src/` (production source code).

---

## 4. Deliverable 4: Continuous Integration Workflow (`.github/workflows/ci.yml`)

A declarative GitHub Actions workflow was created at `.github/workflows/ci.yml`:
1. **Trigger Matrix**: `push` to main/master, `pull_request` to main/master, manual `workflow_dispatch`.
2. **Step Sequence**:
   - `actions/checkout@v4`: Clean repository checkout.
   - `actions/setup-python@v5`: Provisions Python 3.11 with pip caching.
   - Dependency Installation: Upgrades pip and installs `.[test]`.
   - Test Suite Execution: Executes `pytest tests/test_phase_4n_packaging.py tests/test_phase_4m_api_service.py tests/test_phase_4m_auth_fail_closed.py tests/test_phase_4k_g_b_promotion.py -v`.
   - Container Smoke Test: Executes `docker build -t atlas-service:ci-test .` and inspects image metadata.

---

## 5. Deliverable 5: Service Startup & Operational Verification

The service was verified end-to-end via the ASGI lifespan protocol:
- **`GET /healthz` (Liveness)**: Returned HTTP `200 OK` with payload `{"status": "ok"}`.
- **`GET /ready` (Readiness)**: Returned HTTP `200 OK` with payload:
  ```json
  {
    "status": "ready",
    "components": {
      "bm25": true,
      "dense": true,
      "reranker": true,
      "generator": true
    }
  }
  ```
- **`POST /query` (End-to-End Grounded Query)**:
  - Invoked with valid `QueryRequest` containing caller context (`tenant_id="customer-tenant-prod"`, `roles=["admin", "engineer"]`).
  - Pipeline executed retrieval (BM25 + Dense BGE + RRF + Metadata Reranker + Relational), Evidence Assembly (Query-Aware Authority B=True), and Grounded Generation.
  - Returned HTTP `200 OK` with structured `QueryResponse`.

---

## 6. Deliverable 6: Packaging Verification Tests (`tests/test_phase_4n_packaging.py`)

A dedicated pytest test suite was implemented verifying all packaging contracts:
1. `test_pyproject_toml_exists_and_valid`: Validates TOML syntax, build backend, and package metadata.
2. `test_runtime_and_test_dependencies_declared`: Verifies all 8 core runtime dependencies and pytest are declared.
3. `test_dockerfile_specification`: Confirms base image, `WORKDIR`, `COPY`, `USER atlas`, `EXPOSE 8000`, `HEALTHCHECK`, and `uvicorn` entrypoint.
4. `test_dockerignore_specification`: Verifies cache/venv exclusion and runtime data preservation.
5. `test_ci_workflow_specification`: Verifies CI workflow existence and action step definitions.
6. `test_service_entrypoint_and_routes`: Confirms `novastack.service.api:app` is importable and registers `/healthz`, `/ready`, `/query`.
7. `test_frozen_production_configuration`: Asserts `enable_boundary_stitching == False`, `enable_query_aware_authority == True`, `enable_event_bundling == False`.
8. `test_service_startup_and_healthz`: Executes ASGI lifespan initialization and verifies `/healthz` and `/ready`.

**Results**: 8 passed, 0 failed in 3.09s.

---

## 7. Full Regression Test Matrix

All test suites across Phase 4K, 4M, and 4N were executed collectively:

| Test Suite | Scope | Result | Status |
|---|---|:---:|:---:|
| `tests/test_phase_4n_packaging.py` | Package, Docker, CI, and Startup Checks | 8 / 8 | ✅ PASSED |
| `tests/test_phase_4m_api_service.py` | HTTP Endpoints, Validation, Error Handling | 16 / 16 | ✅ PASSED |
| `tests/test_phase_4m_auth_fail_closed.py` | Role, Dept, User, Tenant Fail-Closed Auth | 11 / 11 | ✅ PASSED |
| `tests/test_phase_4k_g_b_promotion.py` | Mechanism B Certified Defaults (A=F, B=T, C=F) | 3 / 3 | ✅ PASSED |
| `tests/test_phase_4k_f_b_security_redteam.py` | Mechanism B Red-Team Security Gates | 5 / 5 | ✅ PASSED |
| `tests/test_phase_4k_e_b_only.py` | Mechanism B Benchmark Telemetry & Outcomes | 5 / 5 | ✅ PASSED |
| `tests/test_canonical_baseline.py` | Canonical 120-Case Baseline Invariants | 5 / 5 | ✅ PASSED |
| **Total** | **Comprehensive Full System Verification** | **53 / 53** | ✅ **PASSED (0 Regressions)** |

---

## 8. Final CTO Directive Status & Compliance Check

- **Production Configuration**: Frozen as `A=False, B=True, C=False`.
- **Retrieval & Generation Invariants**: No alterations to BM25, Dense, RRF, Metadata Reranking, Evidence Resolution, Gemma Generation, or C2 Citation Resolution.
- **Architectural Scope**: No speculative technologies (Redis, vector DBs, Kafka, Kubernetes, microservices) introduced.
- **Phase 4N Status**: **PASSED AND COMPLETE**.
- **STOP Condition**: Standing down. Do NOT begin Phase 4O.
