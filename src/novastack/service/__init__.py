"""Phase 4M / 4O / 4T: ATLAS API Service Package."""

from typing import Any

from novastack.provider import (
    AnswerGeneratorProvider,
    LocalHuggingFaceProvider,
)
from novastack.service.api import AtlasServicePipeline, app, create_app
from novastack.service.resilience import (
    AtlasServiceError,
    AtlasTimeoutError,
    CapacityExhaustedError,
    CircuitBreaker,
    CircuitState,
    InferenceConcurrencyLimiter,
    InternalServerError,
    ModelUnavailableError,
    ResilienceConfig,
    sanitize_error_detail,
)
from novastack.service.identity import (
    IdentityAuthenticationError,
    IdentityConfig,
    IdentityConfigurationError,
    IdentityContextMismatchError,
    JwtIdentityVerifier,
    VerifiedIdentity,
)
from novastack.service.schemas import (
    CallerContext,
    CitationItem,
    ErrorResponse,
    HealthResponse,
    QueryRequest,
    QueryResponse,
    ReadyResponse,
)

__all__ = [
    "AnswerGeneratorProvider",
    "AtlasServiceError",
    "AtlasServicePipeline",
    "AtlasTimeoutError",
    "CallerContext",
    "CapacityExhaustedError",
    "CircuitBreaker",
    "CircuitState",
    "CitationItem",
    "ErrorResponse",
    "HealthResponse",
    "InferenceConcurrencyLimiter",
    "IdentityAuthenticationError",
    "IdentityConfig",
    "IdentityConfigurationError",
    "IdentityContextMismatchError",
    "InferenceServiceAdapter",
    "InferenceServiceClient",
    "InternalServerError",
    "JwtIdentityVerifier",
    "LocalHuggingFaceProvider",
    "ModelUnavailableError",
    "QuantizedLocalProvider",
    "QueryRequest",
    "QueryResponse",
    "ReadyResponse",
    "ResilienceConfig",
    "VerifiedIdentity",
    "app",
    "create_app",
    "sanitize_error_detail",
]


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


