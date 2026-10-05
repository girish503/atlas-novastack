"""Phase 5A: Inference Provider & Engine Abstraction Boundary.

Defines the decoupling contract between the ATLAS service/application layer
and concrete downstream inference engines. Decouples request orchestration,
security boundaries, and evidence assembly from concrete model runtimes.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from novastack.evidence import EvidencePackage
from novastack.generation import AnswerResult, GroundedAnswerGenerator

__all__ = [
    "AnswerGeneratorProvider",
    "LocalHuggingFaceProvider",
    "QuantizedLocalProvider",
    "InferenceServiceAdapter",
    "InferenceServiceClient",
    "create_default_provider",
]


@runtime_checkable
class AnswerGeneratorProvider(Protocol):
    """Abstract protocol for ATLAS inference providers.

    All downstream answer generation engines (local HuggingFace, remote inference
    services, quantized runtimes, or test fakes) must conform to this interface.
    """

    provider_name: str

    def generate_answer(
        self,
        package: EvidencePackage,
        **kwargs: Any,
    ) -> AnswerResult:
        """Generate a grounded answer or principled abstention from an EvidencePackage."""
        ...

    def is_ready(self) -> bool:
        """Check if the inference provider is initialized and ready to serve requests."""
        ...


class LocalHuggingFaceProvider:
    """Concrete provider wrapping GroundedAnswerGenerator for local Gemma execution.

    Implements AnswerGeneratorProvider while encapsulating all HuggingFace / PyTorch
    model loading, prompt formatting, token generation, and citation extraction.
    """

    provider_name: str = "local_huggingface"

    def __init__(
        self,
        model_name: str = "google/gemma-3-1b-it",
        device: str = "cpu",
        lazy_load: bool = True,
        corpus_doc_ids: Optional[set[str]] = None,
        corpus_chunk_ids: Optional[set[str]] = None,
        torch_dtype: Optional[Any] = None,
        local_files_only: bool = True,
        generator: Optional[GroundedAnswerGenerator] = None,
    ):
        if generator is not None:
            self._generator = generator
        else:
            self._generator = GroundedAnswerGenerator(
                model_name=model_name,
                device=device,
                lazy_load=lazy_load,
                corpus_doc_ids=corpus_doc_ids,
                corpus_chunk_ids=corpus_chunk_ids,
                torch_dtype=torch_dtype,
                local_files_only=local_files_only,
            )

    @property
    def generator(self) -> GroundedAnswerGenerator:
        """Access underlying generator for compatibility."""
        return self._generator

    @property
    def model(self) -> Any:
        """Expose model reference for compatibility and lifecycle inspection."""
        return getattr(self._generator, "model", None)

    @model.setter
    def model(self, value: Any) -> None:
        self._generator.model = value

    @property
    def tokenizer(self) -> Any:
        """Expose tokenizer reference for compatibility."""
        return getattr(self._generator, "tokenizer", None)

    def is_ready(self) -> bool:
        """Check if the underlying generator is initialized."""
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
        """Delegate grounded answer generation to the underlying generator."""
        return self._generator.generate_answer(
            package,
            max_evidence_items=max_evidence_items,
            prompt_strategy=prompt_strategy,
            citation_resolver=citation_resolver,
            enable_boundary_stitching=enable_boundary_stitching,
            timeout_seconds=timeout_seconds,
            **kwargs,
        )


def create_default_provider(
    provider_name: Optional[str] = None,
    service_url: Optional[str] = None,
    lazy_load: bool = True,
    corpus_doc_ids: Optional[set[str]] = None,
    corpus_chunk_ids: Optional[set[str]] = None,
) -> AnswerGeneratorProvider:
    """Factory to instantiate the default or configured ATLAS answer generator provider.

    Phase 5J Controlled Production Promotion:
    The certified production default is Backend B (InferenceServiceAdapter).
    Rollback control is Backend A (LocalHuggingFaceProvider), selectable via:
      - environment variable: ATLAS_INFERENCE_PROVIDER=local_huggingface
      - explicit argument: provider_name='local_huggingface'
    """
    import os
    from novastack.quantized_provider import InferenceServiceAdapter, QuantizedLocalProvider

    name = (provider_name or os.environ.get("ATLAS_INFERENCE_PROVIDER", "inference_service")).lower()
    if name in ("inference_service", "inference_service_adapter", "backend_b"):
        url = service_url or os.environ.get("ATLAS_INFERENCE_SERVICE_URL", "http://127.0.0.1:8001")
        return InferenceServiceAdapter(
            service_url=url,
            corpus_doc_ids=corpus_doc_ids,
            corpus_chunk_ids=corpus_chunk_ids,
        )
    elif name in ("local_huggingface", "backend_a"):
        return LocalHuggingFaceProvider(
            model_name="google/gemma-3-1b-it",
            device="cpu",
            lazy_load=lazy_load,
            corpus_doc_ids=corpus_doc_ids,
            corpus_chunk_ids=corpus_chunk_ids,
        )
    elif name in ("quantized_local",):
        return QuantizedLocalProvider(
            corpus_doc_ids=corpus_doc_ids,
            corpus_chunk_ids=corpus_chunk_ids,
        )
    else:
        raise ValueError(
            f"Unknown ATLAS inference provider '{name}'. "
            f"Valid providers: 'inference_service' (Backend B), 'local_huggingface' (Backend A rollback), 'quantized_local'."
        )


def __getattr__(name: str) -> Any:
    if name == "QuantizedLocalProvider":
        from novastack.quantized_provider import QuantizedLocalProvider
        return QuantizedLocalProvider
    if name == "InferenceServiceAdapter":
        from novastack.quantized_provider import InferenceServiceAdapter
        return InferenceServiceAdapter
    if name == "InferenceServiceClient":
        from novastack.inference_client import InferenceServiceClient
        return InferenceServiceClient
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
