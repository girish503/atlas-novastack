# Phase 5A: Inference Provider & Engine Abstraction Boundary

> **Architectural Status Notice**  
> *Phase 5A does not change the production inference engine. It changes only the architectural dependency boundary.*

---

## 1. Current Architecture Before Phase 5A

Prior to Phase 5A, the ATLAS service layer (`novastack.service.api`) was directly coupled to concrete machine learning inference code:
- **Direct Import Dependency:** `novastack/service/api.py` directly imported `GroundedAnswerGenerator` from `novastack.generation`.
- **Concrete Instantiation:** `AtlasServicePipeline.create_default()` directly instantiated `GroundedAnswerGenerator(model_name="google/gemma-3-1b-it", lazy_load=lazy_generator)`.
- **Heavy Framework Coupling:** While `api.py` avoided direct imports of `transformers` or `torch`, its direct import and instantiation of `GroundedAnswerGenerator` structurally coupled the HTTP service entrypoint to HuggingFace Transformers, PyTorch, model loading weights, and tokenizers.
- **Inability to Test in Decoupled Mode:** Testing API query routing, HTTP error formatting, status codes, resilience boundaries, or telemetry required either instantiating the heavy generator or monkey-patching pipeline properties after the fact.
- **Rigid Service Composition:** `create_app()` had no parameter to supply an alternative inference backend or mock provider without constructing a custom pipeline out-of-band.

```
+-------------------------------------------------------------+
|                  FastAPI HTTP Layer (api.py)                 |
+-------------------------------------------------------------+
                               |
                   direct import & instantiation
                               v
+-------------------------------------------------------------+
|        GroundedAnswerGenerator (novastack.generation)       |
|    - AutoModelForCausalLM ("google/gemma-3-1b-it")          |
|    - AutoTokenizer                                          |
|    - PyTorch CPU float32 inference                          |
+-------------------------------------------------------------+
```

---

## 2. New Provider Boundary

Phase 5A introduces a formal, minimal protocol abstraction boundary in `novastack.provider`:

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

### Key Architectural Characteristics:
1. **Zero Heavy ML Types:** The interface communicates exclusively through domain data transfer objects: `EvidencePackage` as input, `AnswerResult` as output. No `torch.Tensor`, `PreTrainedModel`, or `PreTrainedTokenizer` is leaked across this boundary.
2. **Re-use of Existing Contracts:** The interface preserves the battle-tested `AnswerResult` contract unchanged (retaining `answer_id`, `evaluation_id`, `query`, `answer_text`, `answer_status`, `citations`, `evidence_ids_used`, `unsupported_claims`, `citation_validation_status`, `abstention_reason`, `generation_latency_ms`, and `diagnostics`).
3. **Runtime Checkable:** Using Python standard library `@runtime_checkable` allows non-intrusive duck typing; any test mock or wrapper implementing `generate_answer` automatically satisfies the protocol.

---

## 3. Local Provider

The default production provider is implemented as `LocalHuggingFaceProvider` in `novastack.provider`:

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
```

- **Functional Parity:** Delegates 100% of generation execution to `GroundedAnswerGenerator.generate_answer()`.
- **Parameter Transparency:** Explicitly declares and forwards standard parameters (`max_evidence_items=3`, `prompt_strategy="config_a_calibrated"`, `citation_resolver="c2"`, `enable_boundary_stitching=False`, `timeout_seconds=None`, and `**kwargs`).
- **Backward Compatibility:** Exposes `.model`, `.tokenizer`, and `.generator` properties so existing measurement probes and inspection tools function seamlessly.

Additionally, `GroundedAnswerGenerator` itself was updated with `provider_name = "local_huggingface"` and `is_ready() -> bool: return True`, ensuring direct protocol conformance.

---

## 4. Dependency Injection

Dependency injection is wired across two entrypoints:

1. **`AtlasServicePipeline`:**
   ```python
   class AtlasServicePipeline:
       def __init__(
           self,
           ...,
           generator: Optional[AnswerGeneratorProvider] = None,
           ...,
       ):
           self.generator = generator

       @classmethod
       def create_default(
           cls,
           workspace_root: Optional[Path] = None,
           lazy_generator: bool = True,
           generator: Optional[AnswerGeneratorProvider] = None,
       ) -> "AtlasServicePipeline":
           if generator is None:
               generator = LocalHuggingFaceProvider(
                   model_name="google/gemma-3-1b-it",
                   lazy_load=lazy_generator,
               )
           ...
   ```

2. **FastAPI Application (`create_app`):**
   ```python
   def create_app(
       pipeline: Optional[AtlasServicePipeline] = None,
       resilience_config: Optional[ResilienceConfig] = None,
       identity_config: Optional[IdentityConfig] = None,
       inference_provider: Optional[AnswerGeneratorProvider] = None,
   ) -> FastAPI:
       ...
       if pipeline is not None and inference_provider is not None:
           pipeline.generator = inference_provider
       app.state.inference_provider = inference_provider
       ...
   ```

Callers can now supply custom or fake providers at application creation, pipeline factory creation, or directly on the pipeline instance.

---

## 5. Default Behavior

Default runtime behavior is 100% preserved:
- When `create_app()` is called without arguments, `lifespan` invokes `AtlasServicePipeline.create_default()`.
- `create_default()` instantiates `LocalHuggingFaceProvider(model_name="google/gemma-3-1b-it", lazy_load=True)`.
- The provider instantiates `GroundedAnswerGenerator` with the certified frozen weights `google/gemma-3-1b-it` on CPU with `torch.float32`.
- All operational defaults remain completely identical to Phase 4W/4X/4Y.

```
create_app(pipeline=None)
       |
       v
AtlasServicePipeline.create_default()
       |
       v
LocalHuggingFaceProvider("google/gemma-3-1b-it")
       |
       v
GroundedAnswerGenerator (CPU, float32, Gemma 3 1B)
```

---

## 6. Security Boundary

Security remains strictly upstream and fail-closed:
1. **JWT Verification:** `JwtIdentityVerifier` validates signatures, issuer, audience, and exp before any query processing.
2. **Caller Context Binding:** `assert_context_matches_identity` ensures caller context cannot impersonate different tenants or roles.
3. **Retrieval Filtering:** BM25 and Dense retrieval enforce tenant boundary filters (`filters={"tenant_id": tenant_id}`).
4. **Evidence Resolution:** `EvidenceResolver` applies RBAC, department constraints, user restrictions, and adversarial quarantine.
5. **Provider Isolation:** The inference provider **never** receives unauthorized documents or untrusted caller credentials. It is invoked purely on the filtered `EvidencePackage`. The provider performs no authorization of its own.

---

## 7. Resilience Boundary

Resilience mechanics encompass provider execution without alteration:
- **Concurrency Limiting:** `InferenceConcurrencyLimiter` bounds concurrent calls to `max_concurrent_inferences = 1`.
- **Queue Timeout:** Queue deadline enforcement (`0.5s`) triggers HTTP 429 when capacity is exhausted.
- **Request Deadline:** Execution deadline (`30.0s`) is propagated into `timeout_seconds` kwarg to the provider. If the deadline expires, HTTP 504 Gateway Timeout is returned without partial answers or leaked stack traces.
- **Circuit Breaker:** Consecutive unhandled errors from the provider increment the failure count (`threshold = 3`), transitioning state to OPEN and rejecting subsequent calls with HTTP 503 (`cooldown = 10.0s`).

---

## 8. Observability Boundary

Telemetry instrumentation remains fully intact:
- **Metrics Format:** GET `/metrics` exposes Prometheus exposition format with thread-safe counters, gauges, and histograms.
- **Bounded Labels:** Strictly forbidden unbounded labels (`query`, `raw_query`, `user_id`, `tenant_id`, `prompt`, `answer`) remain prohibited.
- **Stage Durations:** `atlas_stage_latency_seconds{stage="generation"}` records precise generation latency for every provider invocation.
- **Structured Logging:** `log_event` logs `was_generation_invoked`, `generation_latency_ms`, and `provider` name while redacting sensitive payloads.

---

## 9. Testing Strategy

The test suite validates both ends of the abstraction boundary:
1. **Static AST Analysis:** Verifies `src/novastack/service/api.py` contains 0 imports of `transformers`, `torch`, `AutoModelForCausalLM`, `AutoTokenizer`, or `GroundedAnswerGenerator`.
2. **Protocol Conformance:** Validates `@runtime_checkable` conformance for `LocalHuggingFaceProvider`, `GroundedAnswerGenerator`, and `FakeAnswerGeneratorProvider`.
3. **Deterministic Decoupling Verification:** Uses `FakeAnswerGeneratorProvider` to test the full HTTP `/query` lifecycle without instantiating PyTorch or HuggingFace.
4. **Backward Compatibility & Regression:** Re-runs the full 675-test regression suite, verifying that no existing citation, resilience, security, or index management tests are broken.

---

## 10. Future Provider Possibilities

The `AnswerGeneratorProvider` boundary enables straightforward integration of future backends once approved:
- **Quantized Engine Provider:** Local GGUF/llama.cpp or AWQ/GPTQ backends on CPU or edge hardware.
- **High-Throughput Local Server Provider:** vLLM or Triton inference server over IPC or local HTTP.
- **Dedicated Remote Inference Provider:** Secure private cloud endpoint hosting larger models.
- **Optimized ONNX / OpenVINO Provider:** Accelerated CPU inference pipelines.

---

## 11. Explicitly Deferred Work

The following items are intentionally **out of scope** for Phase 5A:
- **No Alternative Providers Implemented:** No OpenAI, Anthropic, vLLM, Triton, or ONNX providers were created. Only `LocalHuggingFaceProvider` and test fakes exist.
- **No Performance Changes:** No quantization (int8/int4), model weight substitution, or prompt modifications were introduced.
- **No Worker Thread Termination:** Solving hard thread preemption during long PyTorch C++ loops remains deferred to future process-level worker isolation.
- **Frozen Configuration:** Production flags remain `enable_boundary_stitching=False`, `enable_query_aware_authority=True`, `enable_event_bundling=False`.
