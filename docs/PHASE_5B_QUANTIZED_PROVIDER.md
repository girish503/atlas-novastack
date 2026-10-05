# ATLAS Phase 5B — Quantized Local Inference Engine

## 1. Overview & Architectural Role

Phase 5B introduces an experimental quantized local inference backend for the ATLAS Enterprise Grounded Search and Answering platform. Following the successful decoupling of the inference abstraction boundary in Phase 5A (`AnswerGeneratorProvider`), Phase 5B investigates whether quantized local inference can significantly reduce:

1. **Model memory footprint** (file and resident weight RAM)
2. **Generation latency** (time-to-first-token and token generation rate on CPU)
3. **Host memory pressure** (process RSS and OS virtual memory demand)

while strictly preserving all core ATLAS invariants:
- **Evidence grounding**: strictly bounding generated claims to retrieved facts.
- **Citation correctness**: executing deterministic C2 post-generation citation resolution and validation.
- **Abstention semantics**: preserving pre-generation gates (empty evidence, unresolved conflicts) and model-driven abstention triggers.
- **Security boundary**: maintaining upstream JWT validation, tenant isolation, and evidence filtering before provider invocation.
- **Contract compatibility**: returning standard `AnswerResult` schemas without modifying API endpoints or schemas.

> [!IMPORTANT]
> **Production Default Preservation Invariant:**
> The production default runtime remains `LocalHuggingFaceProvider` using `google/gemma-3-1b-it` (PyTorch `torch.float32` on CPU). `QuantizedLocalProvider` is strictly an experimental provider that can be injected at application startup or evaluation harness configuration.

---

## 2. Abstraction Boundary & Interface Conformance

The `QuantizedLocalProvider` class conforms to the `@runtime_checkable` `AnswerGeneratorProvider` protocol established in Phase 5A (`src/novastack/provider.py`):

```python
@runtime_checkable
class AnswerGeneratorProvider(Protocol):
    provider_name: str

    def generate_answer(
        self,
        package: EvidencePackage,
        **kwargs: Any,
    ) -> AnswerResult:
        ...

    def is_ready(self) -> bool:
        ...
```

### Protocol Attributes & Metadata

| Attribute | Value | Description |
| :--- | :--- | :--- |
| `provider_name` | `"quantized_local"` | Unique provider identifier string |
| `model_name` | `"gemma3:1b"` | Model architecture family and size |
| `quantization_format` | `"GGUF"` | Binary weight serialization format |
| `quantization_level` | `"Q4_K_M"` | 4-bit medium quantization level |
| `model_file_size_mb` | `815.0` | Weight file size (vs ~2500 MB float32 baseline) |
| `endpoint_url` | `"http://127.0.0.1:11434"` | Local Ollama daemon REST endpoint |

---

## 3. Selected Engine & Model Specifications

### Engine
- **Runtime:** Embedded `llama.cpp` executed via local Ollama background service (`ollama.exe`).
- **Execution Target:** Local CPU execution (AVX2 / FMA instruction set optimizations).
- **Communication Protocol:** Local HTTP REST API (`/api/generate` and `/api/tags`).
- **Process Isolation:** The inference engine runs in a separate process space from the Python API service, eliminating Python Global Interpreter Lock (GIL) contention and PyTorch tensor cache fragmentation.

### Model
- **Model Identifier:** `gemma3:1b` (instruction-tuned Gemma 3 1B parameter model).
- **Parameter Count:** 999.89M parameters (~1 Billion).
- **Quantization:** `Q4_K_M` (k-quant 4-bit medium block quantization).
- **Context Length:** 32,768 tokens.
- **Disk Footprint:** 815 MB (a 67.4% reduction compared to the 2.5 GB PyTorch float32 model).

---

## 4. Zero Duplication of ATLAS Domain Logic

`QuantizedLocalProvider` strictly avoids re-implementing or duplicating core ATLAS logic:
1. **Prompt Construction:** Uses ATLAS `build_prompt(query, package, prompt_strategy="config_a_calibrated")` with enterprise data wrapping (`<evidence_data id="...">`).
2. **Context Budgeting:** Delegates to `AdaptiveContextBudgeter` (`raw_prefix`, document diversity filtering, salience compression).
3. **Pre-generation Abstention Gates:**
   - Empty `selected_evidence` -> immediate `AnswerResult(answer_status="abstained", abstention_reason="no_usable_evidence")`.
   - Unresolved `conflicts` -> immediate `AnswerResult(answer_status="abstained", abstention_reason="unresolved_conflict")`.
4. **Citation Resolution (C2):**
   - Step 1: Deterministic >=3-token exact resolver (`_attach_deterministic_citations`).
   - Step 2: Fallback short exact match resolver (`_resolve_short_exact_match`).
   - Step 3: Sentence-level semantic/overlap resolver (`_resolve_sentence_level_match`).
5. **Citation Validation:** Validates tagged evidence against corpus IDs using `CitationValidator`, populating `CitationStatus.VALID`, `UNAUTHORIZED`, `INVALID`, etc.
6. **Failure Taxonomy:** Maps failure modes to the certified 12-category failure taxonomy (`FailureCategory`).

---

## 5. Security & Architectural Invariants

The security model remains entirely upstream:
- **Authentication:** Inbound HTTP requests require valid RS256/HS256 JWT tokens. Missing or invalid headers yield HTTP 401 before any provider is invoked.
- **Tenant Isolation:** Cross-tenant mismatches (JWT tenant claim != request body tenant) yield HTTP 403 before any provider is invoked.
- **Evidence Sanitization:** Upstream evidence resolution filters unauthorized or quarantined documents before assembling the `EvidencePackage`. The provider never sees raw credentials, tokens, or unauthorized documents.

---

## 6. Empirical Performance & Memory Profile

Measurements conducted via `scripts/phase_5b_quantized_benchmark.py`:

### Memory Footprint (M0 - M6 Checkpoints)
| Checkpoint | Process RSS | System Avail RAM | Description |
| :--- | :--- | :--- | :--- |
| **M0** | 64.23 MB | 873.11 MB | Python process startup |
| **M1** | 68.77 MB | 873.56 MB | `QuantizedLocalProvider` initialized |
| **M2** | 71.87 MB | 915.08 MB | Engine ready ping confirmed |
| **M4** | 85.98 MB | 971.73 MB | Post 3 sequential generations |
| **M5** | 85.99 MB | 971.90 MB | Post explicit garbage collection (`gc.collect()`) |
| **M6** | 85.99 MB | 969.98 MB | Idle after 1 second settle |

**Net Process RSS Delta (M6 - M0):** `+21.76 MB` (in stark contrast to ~2.5 GB PyTorch in-process model loading).

### Latency Breakdown (T0 - T10)
| Generation Run | Upstream Retrieval (T1) | Prompt Eval (T6) | Token Eval (T7) | Engine Total (T4-T9) | Total E2E (T10) | Tokens/sec |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Gen #1 (Cold)** | 23.82 ms | 5434.36 ms (284 tok) | 2003.87 ms (29 tok) | 7497.24 ms | 7522.10 ms | 14.5 tok/s |
| **Gen #2 (Warm)** | 0.38 ms | 5197.48 ms (269 tok) | 1677.62 ms (25 tok) | 6921.08 ms | 6922.12 ms | 14.9 tok/s |
| **Gen #3 (Warm)** | 0.22 ms | 5248.38 ms (270 tok) | 1750.57 ms (25 tok) | 7023.87 ms | 7024.46 ms | 14.3 tok/s |

### Key Observations
- **Token Generation Latency:** ~1.67s – 2.00s (14.3 – 14.9 tokens/second) on standard CPU.
- **Total Request Latency:** ~6.92s – 7.52s, comfortably within the 30.0-second request timeout deadline (compared to the baseline PyTorch model which experienced timeouts at 30.4s and took 12.1s – 15.9s when completing).
- **Completion Rate:** 3 / 3 completions (100% completion rate, 0 HTTP timeouts, 0 504 errors).

---

## 7. How to Use & Inject Quantized Provider

### Python API Injection
```python
from novastack.provider import QuantizedLocalProvider
from novastack.service import create_app, AtlasServicePipeline

# 1. Instantiate the experimental quantized provider
quant_prov = QuantizedLocalProvider(
    endpoint_url="http://127.0.0.1:11434",
    model_name="gemma3:1b",
)

# 2. Inject into ATLAS service application
app = create_app(inference_provider=quant_prov)
```

### Standalone Answering
```python
from novastack.provider import QuantizedLocalProvider

prov = QuantizedLocalProvider()
if prov.is_ready():
    result = prov.generate_answer(evidence_package)
    print(result.answer_text)
    for cit in result.citations:
        print(f"Citation: [{cit.raw_tag}] -> {cit.document_id}")
```

---

## 8. Status & Recommendations

- **Correctness & Security:** PASS (17/17 focused tests pass, 0 security/citation regressions).
- **Environment Gate:** Available host RAM was 873 MB (< 3072 MB threshold required for certified production performance characterization).
- **Overall Status:** `HOLD` (per Section 21 acceptance gates).
- **Recommendation:** `KEEP FOR FURTHER EVALUATION` for Phase 5C.
