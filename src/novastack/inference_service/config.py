"""Phase 5C: Configuration for Inference Service.

Defines environment-driven configuration for the standalone inference service.
"""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass
class InferenceServiceConfig:
    """Configuration for the inference service microservice."""

    backend_url: str = "http://127.0.0.1:11434"
    model_name: str = "gemma3:1b"
    connect_timeout_seconds: float = 2.0
    read_timeout_seconds: float = 25.0
    service_host: str = "0.0.0.0"
    service_port: int = 8001

    @classmethod
    def from_env(cls) -> "InferenceServiceConfig":
        """Instantiate config from environment variables with sensible defaults."""
        return cls(
            backend_url=(
                os.environ.get("ATLAS_INFERENCE_BACKEND_URL")
                or os.environ.get("INFERENCE_BACKEND_URL")
                or "http://127.0.0.1:11434"
            ).rstrip("/"),
            model_name=(
                os.environ.get("ATLAS_INFERENCE_MODEL_NAME")
                or os.environ.get("INFERENCE_MODEL_NAME")
                or "gemma3:1b"
            ),
            connect_timeout_seconds=float(
                os.environ.get("ATLAS_INFERENCE_CONNECT_TIMEOUT_SECONDS")
                or os.environ.get("INFERENCE_CONNECT_TIMEOUT_SECONDS")
                or "2.0"
            ),
            read_timeout_seconds=float(
                os.environ.get("ATLAS_INFERENCE_READ_TIMEOUT_SECONDS")
                or os.environ.get("INFERENCE_READ_TIMEOUT_SECONDS")
                or "25.0"
            ),
            service_host=(
                os.environ.get("ATLAS_INFERENCE_SERVICE_HOST")
                or os.environ.get("INFERENCE_SERVICE_HOST")
                or "0.0.0.0"
            ),
            service_port=int(
                os.environ.get("ATLAS_INFERENCE_SERVICE_PORT")
                or os.environ.get("INFERENCE_SERVICE_PORT")
                or "8001"
            ),
        )
