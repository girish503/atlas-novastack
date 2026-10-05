"""Phase 5C: Standalone Inference Service Application.

Provides a decoupled HTTP microservice for quantized model execution:
- GET /healthz: Fast liveness check.
- GET /ready: Backend connectivity and model readiness check.
- POST /generate: Minimal generation execution against local Ollama/llama.cpp runtime.

Security Invariants:
- Zero authorization logic.
- Zero handling of JWTs, bearer tokens, user identities, or tenant contexts.
- Strict data privacy: Prompts, answers, and sensitive payloads are never logged.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from novastack.inference_service.config import InferenceServiceConfig
from novastack.inference_service.schemas import (
    InferenceErrorResponse,
    InferenceGenerationRequest,
    InferenceGenerationResponse,
    InferenceHealthResponse,
    InferenceReadyResponse,
)

logger = logging.getLogger("novastack.inference_service")


def create_inference_app(config: Optional[InferenceServiceConfig] = None) -> FastAPI:
    """Create and configure the standalone Inference Service FastAPI application."""
    cfg = config or InferenceServiceConfig.from_env()

    app = FastAPI(
        title="ATLAS Inference Runtime Service",
        version="5C.0.0",
        description="Isolated model execution service for ATLAS quantized local inference.",
    )
    app.state.config = cfg

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        error_resp = InferenceErrorResponse(
            detail=str(exc.detail),
            error_type="HTTPException",
            status_code=exc.status_code,
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_resp.model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled inference service error: %s", type(exc).__name__)
        error_resp = InferenceErrorResponse(
            detail="Internal inference service error",
            error_type="InternalServiceError",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_resp.model_dump(),
        )

    @app.get(
        "/healthz",
        response_model=InferenceHealthResponse,
        summary="Service liveness probe",
    )
    async def healthz():
        """Fast liveness probe reporting whether the inference service process is alive."""
        return InferenceHealthResponse(status="ok", service="atlas-inference-service")

    @app.get(
        "/ready",
        response_model=InferenceReadyResponse,
        summary="Service readiness probe",
    )
    async def ready(response: Response):
        """Readiness probe verifying connectivity to backend daemon and target model availability."""
        target_model = cfg.model_name
        backend_url = cfg.backend_url
        timeout = cfg.connect_timeout_seconds

        try:
            url = f"{backend_url}/api/tags"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
                    return InferenceReadyResponse(
                        status="not_ready",
                        service="atlas-inference-service",
                        backend="ollama",
                        backend_connected=False,
                        model_name=target_model,
                        model_available=False,
                        detail=f"Backend returned HTTP {resp.status}",
                    )
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", [])
                model_found = any(
                    target_model in m.get("name", "") or m.get("name", "").startswith(target_model)
                    for m in models
                )

                if not model_found and len(models) == 0:
                    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
                    return InferenceReadyResponse(
                        status="not_ready",
                        service="atlas-inference-service",
                        backend="ollama",
                        backend_connected=True,
                        model_name=target_model,
                        model_available=False,
                        detail=f"Model '{target_model}' not found in backend",
                    )

                return InferenceReadyResponse(
                    status="ready",
                    service="atlas-inference-service",
                    backend="ollama",
                    backend_connected=True,
                    model_name=target_model,
                    model_available=model_found,
                )
        except Exception as exc:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return InferenceReadyResponse(
                status="not_ready",
                service="atlas-inference-service",
                backend="ollama",
                backend_connected=False,
                model_name=target_model,
                model_available=False,
                detail=f"Cannot connect to backend: {type(exc).__name__}",
            )

    @app.post(
        "/generate",
        response_model=InferenceGenerationResponse,
        summary="Execute text generation on quantized model",
    )
    async def generate(req: InferenceGenerationRequest, request: Request):
        """Execute text generation against the configured backend inference runtime."""
        request.state.request_id = req.request_id
        target_model = req.model_name or cfg.model_name
        backend_url = cfg.backend_url
        read_timeout = cfg.read_timeout_seconds

        call_payload = {
            "model": target_model,
            "prompt": req.prompt,
            "stream": False,
            "options": {
                "temperature": req.temperature,
                "num_predict": req.max_new_tokens,
            },
        }

        call_req = urllib.request.Request(
            f"{backend_url}/api/generate",
            data=json.dumps(call_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        t_start = time.perf_counter()
        try:
            with urllib.request.urlopen(call_req, timeout=read_timeout) as resp:
                raw_bytes = resp.read()
                data = json.loads(raw_bytes.decode("utf-8"))
        except (TimeoutError, urllib.error.URLError) as net_err:
            is_timeout = (
                isinstance(net_err, TimeoutError)
                or (isinstance(net_err, urllib.error.URLError) and "timed out" in str(net_err.reason).lower())
            )
            if is_timeout:
                logger.warning("Inference backend timed out for request_id=%s", req.request_id)
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="Inference backend timed out",
                )
            logger.warning("Inference backend unavailable: %s", net_err)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Inference backend unavailable",
            )
        except Exception as exc:
            logger.error("Inference backend execution error: %s", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Inference backend execution error",
            )

        gen_latency_ms = (time.perf_counter() - t_start) * 1000.0
        generated_text = data.get("response", "").strip()

        engine_telemetry: Dict[str, Any] = {
            "total_duration_ms": round(data.get("total_duration", 0) / 1e6, 2),
            "load_duration_ms": round(data.get("load_duration", 0) / 1e6, 2),
            "prompt_eval_count": data.get("prompt_eval_count", 0),
            "prompt_eval_duration_ms": round(data.get("prompt_eval_duration", 0) / 1e6, 2),
            "eval_count": data.get("eval_count", 0),
            "eval_duration_ms": round(data.get("eval_duration", 0) / 1e6, 2),
        }

        logger.info(
            "Inference completed for request_id=%s in %.2f ms (tokens: prompt=%d, eval=%d)",
            req.request_id,
            gen_latency_ms,
            engine_telemetry["prompt_eval_count"],
            engine_telemetry["eval_count"],
        )

        return InferenceGenerationResponse(
            generated_text=generated_text,
            model_name=target_model,
            prompt_tokens=engine_telemetry["prompt_eval_count"],
            output_tokens=engine_telemetry["eval_count"],
            generation_latency_ms=round(gen_latency_ms, 2),
            engine_telemetry=engine_telemetry,
            request_id=req.request_id,
        )

    return app


# Default ASGI application instance for uvicorn
app = create_inference_app()
