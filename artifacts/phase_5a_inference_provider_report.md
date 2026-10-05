# Phase 5A: Inference Provider & Engine Decoupling Report
**ATLAS Principal Engineer Implementation Report**  
**Evaluation Date:** September 19, 2026  
**Phase Status:** PASS

---

## 1. Repository Inspection Findings

Prior to making any code modifications, an exhaustive inspection of the ATLAS codebase was conducted to trace all dependencies on inference models, concrete generators, and downstream contracts:

1. **Where the concrete model is instantiated:**
   - Concrete HuggingFace/PyTorch initialization occurred inside `GroundedAnswerGenerator.__init__()` in `src/novastack/generation.py`.
   - `AutoModelForCausalLM.from_pretrained()` and `AutoTokenizer.from_pretrained()` were invoked in `_load_model_and_tokenizer()`.
   - In the service layer, `AtlasServicePipeline.create_default()` in `src/novastack/service/api.py` directly instantiated `GroundedAnswerGenerator(model_name="google/gemma-3-1b-it", lazy_load=lazy_generator)`.
2. **Where generation is invoked:**
   - Generation was invoked in `AtlasServicePipeline._execute_query_bound()` via `ans_res = self.generator.generate_answer(pkg, **gen_kwargs)`.
3. **What inputs generation receives:**
   - Input is strictly domain-typed: `package: EvidencePackage`, along with operational configuration keywords: `max_evidence_items=3`, `prompt_strategy="config_a_calibrated"`, `citation_resolver="c2"`, `enable_boundary_stitching=False`, and `timeout_seconds: Optional[float]`.
4. **What outputs generation returns:**
   - Output is strictly domain-typed: `AnswerResult` dataclass containing `answer_id`, `evaluation_id`, `query`, `answer_text`, `answer_status`, `citations`, `evidence_ids_used`, `unsupported_claims`, `citation_validation_status`, `abstention_reason`, `generation_latency_ms`, and `diagnostics`.
5. **Which diagnostics/timing fields are exposed:**
   - `diagnostics`: Dictionary exposing `layer` ("model_inference" or "pre_generation_gate"), `inference_duration_ms`, and `provider`.
   - Latency metadata: `generation_latency_ms`.
   - HTTP Headers: `x-generation-invoked` ("true"/"false"), `x-generation-latency-ms`.
6. **Which code depended directly on GroundedAnswerGenerator:**
   - `src/novastack/service/api.py`: Imported `GroundedAnswerGenerator` directly and typed `generator: Optional[GroundedAnswerGenerator]`.
   - Multiple unit tests (`test_phase_4o_resilience.py`, `test_phase_4m_api_service.py`, `test_phase_4r_load_validation.py`) already used custom mock classes (`MockGenerator`, `MockDelayedGenerator`) matching the duck-typed `generate_answer` signature.
7. **Which tests depended on concrete implementation details:**
   - `test_generation.py`: Integration test suite for real model weights (marked `@pytest.mark.slow`).
   - `test_phase_4q_ingestion_reliability.py` & `test_phase_4o_resilience.py`: Frozen flag verification inspecting parameter default `enable_boundary_stitching=False`.

---

## 2. Before Architecture

```
+-------------------------------------------------------------------------+
|                         FastAPI Layer (api.py)                          |
|   - Depends directly on GroundedAnswerGenerator                         |
|   - Hardcoded instantiation of Gemma 3 1B                               |
+-------------------------------------------------------------------------+
                                     |
                         Direct Class Coupling
                                     v
+-------------------------------------------------------------------------+
|            GroundedAnswerGenerator (novastack.generation)               |
|   - Imports HuggingFace Transformers (AutoModelForCausalLM)             |
|   - Imports PyTorch (torch)                                             |
|   - Executes CPU float32 generation on google/gemma-3-1b-it             |
+-------------------------------------------------------------------------+
```

### Defects of Before Architecture:
- Service layer `api.py` could not run or be tested in isolation without coupling to heavy ML frameworks.
- No dependency injection mechanism existed in `create_app()` or `AtlasServicePipeline.create_default()`.
- Future inference engines (e.g. vLLM, Triton, GGUF/llama.cpp, ONNX) could not be introduced without modifying the core API service module.

---

## 3. After Architecture

```
+-------------------------------------------------------------------------+
|                         FastAPI Layer (api.py)                          |
|   - Zero Transformers / Torch imports                                   |
|   - Depends ONLY on AnswerGeneratorProvider Protocol                    |
|   - Supports dependency injection via create_app(inference_provider=...) |
+-------------------------------------------------------------------------+
                                     |
                           Protocol Interface
                                     v
+-------------------------------------------------------------------------+
|               <<Protocol>> AnswerGeneratorProvider                      |
|   - provider_name: str                                                  |
|   - generate_answer(package: EvidencePackage, **kwargs) -> AnswerResult |
|   - is_ready() -> bool                                                  |
+-------------------------------------------------------------------------+
             ^                                           ^
             | implements                                | implements
+-----------------------------+             +-----------------------------+
|  LocalHuggingFaceProvider   |             | FakeAnswerGeneratorProvider |
|  (Default Production Provider|             | (Test Decoupling Mock)      |
|  in novastack.provider)     |             +-----------------------------+
+-----------------------------+
             | wraps
             v
+-----------------------------+
|   GroundedAnswerGenerator   |
|   - Gemma 3 1B (CPU)        |
+-----------------------------+
```

---

## 4. Provider Contract

The interface is formally defined in `src/novastack/provider.py`:

```python
@runtime_checkable
class AnswerGeneratorProvider(Protocol):
    """Minimal abstraction boundary for ATLAS answer generation backends."""

    provider_name: str

    def generate_answer(
        self,
        package: EvidencePackage,
        **kwargs: Any,
    ) -> AnswerResult:
        """Generate a grounded answer result from an EvidencePackage."""
        ...

    def is_ready(self) -> bool:
        """Return readiness status for health and readiness checks."""
        ...
```

- **Clean Types:** Only domain package types (`EvidencePackage`, `AnswerResult`) cross the boundary.
- **Contract Preservation:** Reuses `AnswerResult` without any schema mutations.
- **Duck-Typing:** Decorated with `@runtime_checkable` from standard library `typing`.

---

## 5. Local Provider

The default production provider is implemented as `LocalHuggingFaceProvider` in `src/novastack/provider.py`:

```python
class LocalHuggingFaceProvider:
    """Concrete provider wrapping the existing local HuggingFace/PyTorch generator."""

    provider_name: str = "local_huggingface"

    def __init__(
        self,
        generator: Optional[GroundedAnswerGenerator] = None,
        model_name: str = "google/gemma-3-1b-it",
        lazy_load: bool = True,
        device: str = "cpu",
    ) -> None:
        if generator is not None:
            self._generator = generator
        else:
            self._generator = GroundedAnswerGenerator(
                model_name=model_name,
                lazy_load=lazy_load,
                device=device,
            )

    @property
    def generator(self) -> GroundedAnswerGenerator:
        return self._generator

    @property
    def model(self) -> Any:
        return getattr(self._generator, "model", None)

    @model.setter
    def model(self, value: Any) -> None:
        self._generator.model = value

    @property
    def tokenizer(self) -> Any:
        return getattr(self._generator, "tokenizer", None)

    def is_ready(self) -> bool:
        return self._generator is not None

    def generate_answer(
        self,
        package: EvidencePackage,
        max_evidence_items: int = 3,
        prompt_strategy: str = "config_a_calibrated",
        citation_resolver: str = "c2",
        enable_boundary_stitching: bool = False,
        timeout_seconds: Optional[float] = None,
        **kwargs: Any,
    ) -> AnswerResult:
        return self._generator.generate_answer(
            package,
            max_evidence_items=max_evidence_items,
            prompt_strategy=prompt_strategy,
            citation_resolver=citation_resolver,
            enable_boundary_stitching=enable_boundary_stitching,
            timeout_seconds=timeout_seconds,
            **kwargs,
        )
```

- Functionally identical to `GroundedAnswerGenerator`.
- Explicitly exposes `.generator`, `.model`, and `.tokenizer` for 100% backward compatibility with existing measurement probes (such as Phase 4X-R1 timing probes).
- `GroundedAnswerGenerator` in `generation.py` was also augmented with `provider_name = "local_huggingface"` and `is_ready()` to satisfy the protocol directly.

---

## 6. Dependency Injection

Dependency injection was introduced across the application initialization layers:

1. **`AtlasServicePipeline`:**
   - Constructor parameter `generator: Optional[AnswerGeneratorProvider] = None`.
   - Factory method `create_default(..., generator: Optional[AnswerGeneratorProvider] = None)`. If omitted, defaults to `LocalHuggingFaceProvider(model_name="google/gemma-3-1b-it", lazy_load=lazy_generator)`.
2. **`create_app()`:**
   - Added parameter `inference_provider: Optional[AnswerGeneratorProvider] = None`.
   - When provided, injects `inference_provider` into `pipeline.generator` and stores on `app.state.inference_provider`.
   - In lifespan initialization, passes `inference_provider` to `AtlasServicePipeline.create_default()`.
   - Backward compatibility: existing callers calling `create_app()` without parameters receive the default local HuggingFace provider running Gemma 3 1B on CPU.

---

## 7. Files Changed

| File | Change Type | Description |
|---|---|---|
| `src/novastack/provider.py` | **NEW** | Implements `AnswerGeneratorProvider` protocol and `LocalHuggingFaceProvider` wrapper. |
| `src/novastack/generation.py` | **MODIFIED** | Added `provider_name` attribute and `is_ready()` method to `GroundedAnswerGenerator`. |
| `src/novastack/service/api.py` | **MODIFIED** | Replaced `GroundedAnswerGenerator` import with `AnswerGeneratorProvider` and `LocalHuggingFaceProvider`. Added `generator` DI parameter to `create_default()`, added `inference_provider` DI parameter to `create_app()`, and updated timeout kwarg forwarding. |
| `src/novastack/service/__init__.py` | **MODIFIED** | Exported `AnswerGeneratorProvider` and `LocalHuggingFaceProvider`. |
| `tests/test_phase_5a_provider_boundary.py` | **NEW** | Comprehensive 23-test suite verifying protocol conformance, decoupling, error propagation, citations, observability, and security. |
| `docs/PHASE_5A_INFERENCE_PROVIDER_BOUNDARY.md` | **NEW** | Complete architectural documentation for the provider boundary. |

---

## 8. Tests Added

A dedicated test suite was implemented in `tests/test_phase_5a_provider_boundary.py`, comprising 23 tests across 13 requirement categories:

1. **Category A: Provider Contract (Protocol Conformance)**
   - `test_local_provider_conforms_to_protocol`: PASS
   - `test_fake_provider_conforms_to_protocol`: PASS
   - `test_grounded_generator_conforms_to_protocol`: PASS
2. **Category B: LocalHuggingFaceProvider Construction & Compatibility**
   - `test_construction_with_defaults`: PASS
   - `test_construction_wrapping_existing_generator`: PASS
   - `test_model_property_setter`: PASS
3. **Category C: Default Application Wiring**
   - `test_pipeline_default_uses_local_provider`: PASS
   - `test_pipeline_is_ready_checks_generator`: PASS
4. **Category D: Explicit Provider Injection**
   - `test_inject_provider_via_create_default`: PASS
   - `test_inject_provider_via_create_app`: PASS
5. **Category E: API Query Execution Using Injected Provider**
   - `test_end_to_end_query_with_fake_provider`: PASS
   - `test_fake_provider_receives_timeout_kwarg`: PASS
6. **Category F: AnswerResult Schema Compatibility**
   - `test_abstention_result_propagates_correctly`: PASS
7. **Category G: Error Propagation**
   - `test_provider_exception_sanitized_to_500`: PASS
   - `test_provider_timeout_abstention_converts_to_504`: PASS
8. **Category H: Resilience & Concurrency Limiter Compatibility**
   - `test_circuit_breaker_triggers_on_repeated_provider_failures`: PASS
9. **Category I: Structured Citation Compatibility**
   - `test_citations_formatted_as_citation_items`: PASS
10. **Category J: Observability Compatibility**
    - `test_metrics_endpoint_records_generation`: PASS
11. **Category K: Security & Auth Compatibility (Upstream Boundary Protection)**
    - `test_missing_auth_rejected_before_provider_invocation`: PASS
    - `test_tenant_context_mismatch_rejected_before_provider_invocation`: PASS
12. **Category L: Index Generation ID Compatibility**
    - `test_index_generation_id_preserved_with_injected_provider`: PASS
13. **Category M: Architectural Decoupling (Static Code Analysis)**
    - `test_api_module_does_not_import_transformers`: PASS
    - `test_api_module_does_not_import_grounded_answer_generator`: PASS

---

## 9. Regression Results

All regression suites were executed against the codebase:

| Suite | Test Target Files | Tests Run | Passed | Failed | Duration |
|---|---|---|---|---|---|
| **Phase 5A Provider Boundary** | `tests/test_phase_5a_provider_boundary.py` | 23 | 23 | 0 | 7.60s |
| **Citation & Evidence** | `tests/test_citation_validator.py`, `tests/test_evidence.py`, `tests/test_sentence_level_citation.py`, `tests/test_short_answer_citation.py` | 58 | 58 | 0 | 1.17s |
| **Service, Security & Identity** | `tests/test_phase_4m_api_service.py`, `tests/test_phase_4m_auth_fail_closed.py`, `tests/test_phase_4o_resilience.py`, `tests/test_phase_4p_observability.py`, `tests/test_phase_4s_live_index_hotswap.py`, `tests/test_phase_4t_identity_boundary.py` | 83 | 83 | 0 | 5.08s |
| **Load & Concurrency Validation** | `tests/test_phase_4r_load_validation.py` | 13 | 13 | 0 | 51.53s |
| **Full Fast Regression Suite** | `tests/ -m "not slow"` | 675 | 675 | 0 | 129.57s |

**Total Regression Tests Evaluated:** 675 passed, 0 failed.

---

## 10. Security Validation

Security invariants remain completely fail-closed and upstream:
- **Authentication:** Unauthenticated requests receive HTTP 401 Unauthorized before provider invocation.
- **Tenant Context Mismatch:** Cross-tenant payload attempts receive HTTP 403 Forbidden before provider invocation.
- **Zero Token Leakage:** Bearer JWT credentials, secrets, and raw keys are never passed to the inference provider.
- **Zero Evidence Contamination:** The inference provider receives only the pre-filtered, tenant-isolated, RBAC-authorized, adversarial-quarantined `EvidencePackage`.

---

## 11. Behavioral Parity

Behavioral parity between `GroundedAnswerGenerator` and `LocalHuggingFaceProvider` is 100%:
- Both implement the exact same `generate_answer()` signature.
- Both use the certified frozen prompt strategies (`config_a_calibrated`).
- Both use the C2 sentence-level and short-answer citation resolvers.
- Both preserve identical `AnswerStatus` semantics (`answered`, `abstained`, `error`).
- In addition, `FakeAnswerGeneratorProvider` demonstrated that the full HTTP query pipeline executes to completion with verified C2 citations and status codes without importing or instantiating PyTorch or Transformers.

---

## 12. Performance Neutrality

As required by the directive, Phase 5A makes **zero** performance claims:
- No quantization was implemented (model remains `torch.float32`).
- No GPU acceleration was introduced (execution remains CPU).
- No prompt modifications or token budget changes were made.
- The default production provider continues to execute the exact same PyTorch inference loop on CPU.
- Performance characteristics remain strictly identical to those characterized in Phase 4X, 4X-R1, and 4Y.

---

## 13. Remaining Limitations

The architectural refactor purposefully leaves certain known characteristics unchanged:
1. **CPU Execution Speed:** Gemma 3 1B execution on CPU still requires ~12–16 seconds per generation request under unconstrained host RAM, and risks 504 timeouts under severe RAM pressure (<3 GB available).
2. **In-Process GIL Contention:** Running PyTorch C++ kernels inside the FastAPI process thread pool remains subject to thread preemption limitations.
3. **Memory Footprint:** The local provider still requires ~2.5 GB RSS for weights and KV cache.

These limitations are slated to be addressed in subsequent engine implementation phases enabled by the 5A boundary.

---

## 14. Future Provider Options

With the `AnswerGeneratorProvider` boundary in place, ATLAS can now introduce alternative inference backends in subsequent phases without modifying `api.py`:
1. **Phase 5B — Optimized Quantized Engine:** A provider wrapping llama.cpp/GGUF (Q4_K_M or Q8_0) for 3–4x lower latency and ~70% lower memory footprint on CPU.
2. **Phase 5C — IPC / Subprocess Engine:** A provider communicating with an isolated worker process via domain socket / shared memory, enabling hard deadline preemption and zero GIL contention.
3. **Phase 5D — Remote / Microservice Engine:** A provider interfacing with a dedicated vLLM or Triton cluster.

---

## 15. CTO Recommendation for Next Phase

Based on the clean establishment of the `AnswerGeneratorProvider` protocol and 100% green regression results across all 675 tests:

1. **Mark Phase 5A as PASS.**
2. **Proceed to Phase 5B (Engine Implementation):** Develop an optimized local quantized provider (such as GGUF / llama-cpp-python) conforming to `AnswerGeneratorProvider`, to solve the CPU latency and memory pressure bottlenecks documented in Phase 4Y.

---

PHASE 5A STATUS: PASS

Production code modified:
YES

Inference behavior intentionally changed:
NO

Provider abstraction established:
YES

Default Gemma provider preserved:
YES

Security regression:
PASS

Citation regression:
PASS

Resilience regression:
PASS

Index lifecycle regression:
PASS

Next phase recommendation:
Proceed to Phase 5B to implement an optimized local quantized engine (GGUF / llama.cpp) behind the newly established AnswerGeneratorProvider boundary, resolving CPU generation latency and memory pressure while leaving API service orchestration untouched.
