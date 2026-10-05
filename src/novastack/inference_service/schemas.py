"""Phase 5C: Pydantic Schemas for Inference Service.

Defines the strictly minimal generation contract for the standalone inference service.
Enforces the Phase 5C security principle:
- Zero authorization metadata.
- Zero JWTs, bearer tokens, or caller credentials.
- Zero search documents, chunks, or tenant database records.
- Accepts strictly rendered prompt, token limits, and correlation ID.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator


class InferenceGenerationRequest(BaseModel):
    """Minimal generation request payload for the inference service."""

    prompt: str = Field(..., description="Rendered, evidence-grounded prompt text (non-empty)")
    request_id: Optional[str] = Field(None, description="Optional correlation/trace ID for observability")
    max_new_tokens: int = Field(default=100, ge=1, le=2048, description="Maximum tokens to generate")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, description="Sampling temperature (0.0=deterministic)")
    model_name: Optional[str] = Field(None, description="Optional model identifier override")

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("prompt is required and cannot be empty or whitespace only")
        return v


class InferenceGenerationResponse(BaseModel):
    """Structured generation response returned by the inference service."""

    generated_text: str = Field(..., description="Raw generated text from the inference model")
    model_name: str = Field(..., description="Model identifier that executed the generation")
    prompt_tokens: int = Field(default=0, description="Tokens evaluated in prompt")
    output_tokens: int = Field(default=0, description="Tokens generated in output")
    generation_latency_ms: float = Field(default=0.0, description="Inference engine latency in milliseconds")
    engine_telemetry: Dict[str, Any] = Field(
        default_factory=dict,
        description="Detailed engine metrics (load duration, prompt eval duration, etc.)",
    )
    request_id: Optional[str] = Field(None, description="Correlation/trace ID echoed back")


class InferenceHealthResponse(BaseModel):
    """Liveness probe response."""

    status: str = Field(default="ok", description="Service process status")
    service: str = Field(default="atlas-inference-service", description="Service name")


class InferenceReadyResponse(BaseModel):
    """Readiness probe response verifying backend connectivity and model readiness."""

    status: str = Field(..., description="Readiness status ('ready' or 'not_ready')")
    service: str = Field(default="atlas-inference-service", description="Service name")
    backend: str = Field(default="ollama", description="Backend execution runtime")
    backend_connected: bool = Field(..., description="Whether backend daemon is reachable")
    model_name: str = Field(..., description="Configured target model identifier")
    model_available: bool = Field(..., description="Whether target model is loaded or available in backend")
    detail: Optional[str] = Field(None, description="Optional diagnostic details on failure")


class InferenceErrorResponse(BaseModel):
    """Sanitized structured error response returned by the inference service."""

    detail: str = Field(..., description="Sanitized, human-readable error description")
    error_type: str = Field(..., description="Classified error category")
    status_code: int = Field(..., description="HTTP status code")
    request_id: Optional[str] = Field(None, description="Correlation/trace ID if provided")
