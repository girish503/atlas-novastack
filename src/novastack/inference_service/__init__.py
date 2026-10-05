"""Phase 5C: Inference Service Package.

Exposes schemas, configuration, and factory functions for the standalone
ATLAS Inference Service microservice.
"""

from novastack.inference_service.config import InferenceServiceConfig
from novastack.inference_service.schemas import (
    InferenceErrorResponse,
    InferenceGenerationRequest,
    InferenceGenerationResponse,
    InferenceHealthResponse,
    InferenceReadyResponse,
)
from novastack.inference_service.app import create_inference_app, app

__all__ = [
    "InferenceServiceConfig",
    "InferenceGenerationRequest",
    "InferenceGenerationResponse",
    "InferenceHealthResponse",
    "InferenceReadyResponse",
    "InferenceErrorResponse",
    "create_inference_app",
    "app",
]
