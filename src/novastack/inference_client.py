"""Phase 5C: Inference Service Client.

Provides a robust HTTP client for communicating across the network boundary
to the standalone Inference Service. Encapsulates connection pooling, timeouts,
and structured error translation into standard ATLAS service exceptions.
"""

from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from novastack.inference_service.schemas import (
    InferenceGenerationRequest,
    InferenceGenerationResponse,
)
from novastack.service.resilience import AtlasTimeoutError, ModelUnavailableError

logger = logging.getLogger("novastack.inference_client")


class InferenceServiceClient:
    """HTTP client communicating with the standalone ATLAS Inference Service."""

    def __init__(
        self,
        service_url: str = "http://127.0.0.1:8001",
        connect_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 25.0,
        http_client: Optional[Any] = None,
    ):
        self.service_url = service_url.rstrip("/")
        self.connect_timeout_seconds = connect_timeout_seconds
        self.read_timeout_seconds = read_timeout_seconds
        self._http_client = http_client  # Optional httpx.Client or TestClient for testing

    def check_health(self) -> bool:
        """Ping /healthz to verify process liveness."""
        if self._http_client is not None:
            try:
                resp = self._http_client.get("/healthz")
                return resp.status_code == 200
            except Exception:
                return False

        try:
            url = f"{self.service_url}/healthz"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.connect_timeout_seconds) as resp:
                return resp.status == 200
        except Exception:
            return False

    def check_readiness(self) -> tuple[bool, Dict[str, Any]]:
        """Ping /ready to verify backend connectivity and model readiness."""
        if self._http_client is not None:
            try:
                resp = self._http_client.get("/ready")
                data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                return resp.status_code == 200 and data.get("status") == "ready", data
            except Exception as e:
                return False, {"error": str(e)}

        try:
            url = f"{self.service_url}/ready"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.connect_timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status == 200 and data.get("status") == "ready", data
        except urllib.error.HTTPError as http_err:
            try:
                err_data = json.loads(http_err.read().decode("utf-8"))
            except Exception:
                err_data = {"error": str(http_err)}
            return False, err_data
        except Exception as e:
            return False, {"error": str(e)}

    def generate(
        self,
        prompt: str,
        request_id: Optional[str] = None,
        max_new_tokens: int = 100,
        temperature: float = 0.0,
        model_name: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> InferenceGenerationResponse:
        """Dispatch a minimal generation request to the inference service."""
        req_obj = InferenceGenerationRequest(
            prompt=prompt,
            request_id=request_id,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            model_name=model_name,
        )
        payload_bytes = req_obj.model_dump_json().encode("utf-8")
        effective_timeout = timeout_seconds or self.read_timeout_seconds

        if self._http_client is not None:
            try:
                resp = self._http_client.post(
                    "/generate",
                    content=payload_bytes,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    return InferenceGenerationResponse.model_validate(resp.json())
                if resp.status_code == 504:
                    raise AtlasTimeoutError(f"Inference service timed out: {resp.text}")
                if resp.status_code in (502, 503):
                    raise ModelUnavailableError(f"Inference service unavailable: {resp.text}")
                if resp.status_code == 422:
                    raise ValueError(f"Inference service validation error: {resp.text}")
                raise RuntimeError(f"Inference service returned HTTP {resp.status_code}: {resp.text}")
            except (AtlasTimeoutError, ModelUnavailableError, ValueError, RuntimeError):
                raise
            except Exception as exc:
                if "timeout" in str(exc).lower():
                    raise AtlasTimeoutError(f"Inference service request timed out: {exc}") from exc
                raise ModelUnavailableError(f"Cannot communicate with inference service: {exc}") from exc

        url = f"{self.service_url}/generate"
        req = urllib.request.Request(
            url,
            data=payload_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return InferenceGenerationResponse.model_validate(data)
        except urllib.error.HTTPError as http_err:
            body = http_err.read().decode("utf-8", errors="replace")
            if http_err.code == 504:
                raise AtlasTimeoutError(f"Inference service timed out: {body}") from http_err
            if http_err.code in (502, 503):
                raise ModelUnavailableError(f"Inference service unavailable: {body}") from http_err
            if http_err.code == 422:
                raise ValueError(f"Inference service validation error: {body}") from http_err
            raise RuntimeError(f"Inference service returned HTTP {http_err.code}: {body}") from http_err
        except (socket.timeout, TimeoutError) as to_err:
            raise AtlasTimeoutError("Inference service request timed out") from to_err
        except urllib.error.URLError as url_err:
            if isinstance(url_err.reason, socket.timeout) or "timed out" in str(url_err.reason).lower():
                raise AtlasTimeoutError("Inference service request timed out") from url_err
            raise ModelUnavailableError(f"Inference service unreachable: {url_err.reason}") from url_err
        except json.JSONDecodeError as json_err:
            raise RuntimeError(f"Malformed response from inference service: {json_err}") from json_err
        except Exception as exc:
            if "timed out" in str(exc).lower():
                raise AtlasTimeoutError(f"Inference service request timed out: {exc}") from exc
            raise ModelUnavailableError(f"Cannot communicate with inference service: {exc}") from exc
