# Project ATLAS — Release Specification (0.4.14-rc1)

**Release Candidate Tag**: `0.4.14-rc1`  
**Package Version**: `0.4.14`  
**Tarball Archive**: `dist/atlas-novastack-0.4.14-rc1.tar.gz`  
**Tarball Size**: `3,475,452 bytes`  
**Archive SHA-256**: `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3`  
**Release Decision**: `COMMISSIONED WITH DOCUMENTED LIMITATIONS`  
**Commissioning Date**: 2026-09-23  

---

## 1. Release Overview

Release Candidate `0.4.14-rc1` represents the frozen, validated, and operationally commissioned production release of Project ATLAS (Evidence-Grounded Enterprise Search Platform).

ATLAS bridges multi-channel enterprise search (BM25 lexical retrieval, dense neural vector retrieval, and relational structured entity retrieval) with grounded LLM generation, fail-closed authorization, tenant isolation, and strict citation validation.

---

## 2. Release Artifacts & Cryptographic Signatures

| File / Component | Location / Identifier | Cryptographic Digest (SHA-256) |
|:---|:---|:---|
| **Distribution Tarball** | `dist/atlas-novastack-0.4.14-rc1.tar.gz` | `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` |
| **Production Model** | `gemma3:1b` (Q4_K_M GGUF) | `8648f39daa8fbf5b18c7b4e6a8fb4990c692751d49917417b8842ca5758e7ffc` |
| **Inference Docker Image**| `atlas-inference:5d` | Container built from `Dockerfile.inference` |
| **Commissioning Manifest**| `artifacts/phase_5p_commissioning_manifest.json` | Generated during Phase 5P commissioning |
| **Commissioning Audit** | `artifacts/phase_5p_final_commissioning.json` | Machine-readable 18-gate audit |

---

## 3. Production Architecture & Configuration

### Primary Backend (Backend B)
- **Adapter**: `InferenceServiceAdapter` (`src/novastack/quantized_provider.py`)
- **Microservice**: Containerized FastAPI service running in Docker as non-root `appuser:1000` on port `8001`.
- **Inference Engine**: Ollama daemon running on host bound to `0.0.0.0:11434`.
- **Model**: `gemma3:1b` Q4_K_M quantized GGUF weights.
- **Warm Inference Latency**: $\sim 5.1$ seconds on Intel Core i3-N305 class CPU.

### Certified Rollback Backend (Backend A)
- **Provider**: `LocalHuggingFaceProvider` (`src/novastack/provider.py`)
- **Model**: `google/gemma-3-1b-it` (FP32 CPU) via HuggingFace Transformers pipeline.
- **Rollback Procedure**: Zero code change; set `provider_name="local_huggingface"` in pipeline factory.

### Production Resilience & Concurrency Guardrails
- `max_concurrent_inferences`: `1` (strictly enforced by `InferenceConcurrencyLimiter`).
- `queue_timeout_seconds`: `0.5` seconds (sheds overflow traffic with HTTP 429).
- `request_timeout_seconds`: `30.0` seconds (container read timeout `25.0s`).
- `circuit_failure_threshold`: `3` consecutive upstream failures.
- `circuit_cooldown_seconds`: **`10.0` seconds** in production.

---

## 4. Certified Operating Envelope

ATLAS Release Candidate `0.4.14-rc1` is certified strictly for deployment within the following operational envelope:

- **Single-Node Execution**: Deployment on bare metal or single virtual machine running Linux or Windows.
- **CPU Architecture**: Certified CPU-only execution (Intel Core i3-N305 or equivalent modern x86-64 processor).
- **RAM**: Minimum 8 GB dedicated system memory.
- **Security Boundaries**: Mandatory JWT HS256 secret ($\ge 32$ bytes), strict tenant isolation, and Layer 1S security gate.
- **Operations Runbook**: All operator commands, monitoring, restart, and rollback procedures must follow `docs/OPERATIONS_RUNBOOK.md`.

### Explicitly Excluded / Non-Certified Scenarios
- Multi-node Kubernetes clustering or pod autoscaling.
- Discrete GPU acceleration (NVIDIA / AMD); platform has not been certified on CUDA.
- Concurrent multi-query generation ($N > 1$).
- Distributed shared storage or networked index synchronization.

---

## 5. Verification & Testing Evidence

- **Phase 5O Incident Recovery Suite**: 225 passed / 0 failed across 13 test suites.
- **Phase 5P Final Commissioning Suite**: 13 passed / 0 failed in `tests/test_phase_5p_final_commissioning.py`.
- **Phase 5H Full Evaluation Suite**: 120 canonical cases evaluated (101 positive, 19 negative), G1–G9 SLA gates passed, 100% negative abstention.
- **Source Immutability**: Exactly 0 modified lines across all 16 files in `src/novastack/` since Phase 5K freeze.

---

## 6. Release Sign-Off Statement

Release Candidate `0.4.14-rc1` is hereby **COMMISSIONED WITH DOCUMENTED LIMITATIONS** for production deployment under Project ATLAS governance.
