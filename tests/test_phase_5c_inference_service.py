"""Phase 5C Test Suite: Inference Service Application Tests.

Tests /healthz, /ready, /generate endpoints of the standalone Inference Service
using FastAPI TestClient (in-memory). No real TCP socket or Ollama process needed.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from novastack.inference_service.config import InferenceServiceConfig
from novastack.inference_service.app import create_inference_app


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _make_cfg(**overrides):
    base = dict(
        backend_url="http://127.0.0.1:11434",
        model_name="gemma3:1b",
        connect_timeout_seconds=2.0,
        read_timeout_seconds=25.0,
        service_port=8001,
    )
    base.update(overrides)
    return InferenceServiceConfig(**base)


@pytest.fixture()
def inference_app_client():
    """Return a TestClient backed by a fresh inference app with default config."""
    app = create_inference_app(_make_cfg())
    return TestClient(app, raise_server_exceptions=False)


def _make_ollama_mock(text="Answer text."):
    payload = json.dumps({
        "response": text,
        "prompt_eval_count": 100,
        "eval_count": 20,
        "total_duration": 2000000000,
        "load_duration": 100000000,
        "prompt_eval_duration": 500000000,
        "eval_duration": 1400000000,
    }).encode()
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.status = 200
    mock_resp.read.return_value = payload
    return mock_resp


# ---------------------------------------------------------------------------
# /healthz - liveness probe
# ---------------------------------------------------------------------------

class TestHealthz:
    def test_healthz_returns_200(self, inference_app_client):
        resp = inference_app_client.get("/healthz")
        assert resp.status_code == 200

    def test_healthz_body_has_status_ok(self, inference_app_client):
        resp = inference_app_client.get("/healthz")
        body = resp.json()
        assert body.get("status") == "ok"

    def test_healthz_body_has_service_field(self, inference_app_client):
        resp = inference_app_client.get("/healthz")
        body = resp.json()
        assert "service" in body

    def test_healthz_has_no_auth_fields(self, inference_app_client):
        resp = inference_app_client.get("/healthz")
        body = resp.json()
        for forbidden in ("jwt", "authorization", "credential", "secret"):
            assert forbidden not in str(body).lower(), (
                f"Forbidden field '{forbidden}' found in /healthz response"
            )


# ---------------------------------------------------------------------------
# /ready - readiness probe (requires Ollama connectivity)
# ---------------------------------------------------------------------------

class TestReady:
    def test_ready_when_ollama_responds_with_model(self):
        """Simulate Ollama returning model in /api/tags response."""
        app = create_inference_app(_make_cfg())
        client = TestClient(app, raise_server_exceptions=False)

        mock_resp_data = json.dumps({"models": [{"name": "gemma3:1b"}]}).encode()
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.status = 200
        mock_response.read.return_value = mock_resp_data

        with patch("urllib.request.urlopen", return_value=mock_response):
            resp = client.get("/ready")

        assert resp.status_code == 200
        body = resp.json()
        # status field should be "ready" when model found
        assert body.get("status") in ("ready", "not_ready")  # at minimum the field exists
        assert "service" in body

    def test_ready_status_ready_when_model_found(self):
        app = create_inference_app(_make_cfg())
        client = TestClient(app, raise_server_exceptions=False)

        mock_resp_data = json.dumps({"models": [{"name": "gemma3:1b"}]}).encode()
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.status = 200
        mock_response.read.return_value = mock_resp_data

        with patch("urllib.request.urlopen", return_value=mock_response):
            resp = client.get("/ready")

        assert resp.json().get("status") == "ready"

    def test_ready_when_ollama_unreachable(self):
        app = create_inference_app(_make_cfg())
        client = TestClient(app, raise_server_exceptions=False)

        err = urllib.error.URLError("connection refused")
        with patch("urllib.request.urlopen", side_effect=err):
            resp = client.get("/ready")

        # 503 or body.status=not_ready
        assert resp.status_code in (200, 503)
        if resp.status_code == 200:
            assert resp.json().get("status") == "not_ready"

    def test_ready_no_auth_fields_in_response(self):
        app = create_inference_app(_make_cfg())
        client = TestClient(app, raise_server_exceptions=False)

        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({"models": [{"name": "gemma3:1b"}]}).encode()

        with patch("urllib.request.urlopen", return_value=mock_response):
            resp = client.get("/ready")

        body_str = str(resp.json()).lower()
        for forbidden in ("jwt", "authorization", "credential"):
            assert forbidden not in body_str


# ---------------------------------------------------------------------------
# /generate - generation endpoint
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_generate_returns_200_with_generated_text(self):
        app = create_inference_app(_make_cfg())
        client = TestClient(app, raise_server_exceptions=False)

        with patch("urllib.request.urlopen", return_value=_make_ollama_mock("Test answer.")):
            resp = client.post("/generate", json={
                "prompt": "What is ATLAS?",
                "request_id": "req-001",
                "max_new_tokens": 128,
                "temperature": 0.0,
                "model_name": "gemma3:1b",
            })

        assert resp.status_code == 200
        body = resp.json()
        assert body["generated_text"] == "Test answer."

    def test_generate_returns_engine_telemetry(self):
        app = create_inference_app(_make_cfg())
        client = TestClient(app, raise_server_exceptions=False)

        with patch("urllib.request.urlopen", return_value=_make_ollama_mock()):
            resp = client.post("/generate", json={
                "prompt": "test prompt",
                "request_id": "req-002",
            })

        assert resp.status_code == 200
        body = resp.json()
        assert "engine_telemetry" in body
        assert "prompt_eval_count" in body["engine_telemetry"]

    def test_generate_rejects_empty_prompt(self):
        app = create_inference_app(_make_cfg())
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/generate", json={
            "prompt": "",
            "request_id": "req-003",
        })
        assert resp.status_code == 422

    def test_generate_rejects_missing_prompt(self):
        app = create_inference_app(_make_cfg())
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/generate", json={
            "request_id": "req-004",
        })
        assert resp.status_code == 422

    def test_generate_returns_503_when_ollama_unreachable(self):
        app = create_inference_app(_make_cfg())
        client = TestClient(app, raise_server_exceptions=False)

        err = urllib.error.URLError("connection refused")
        with patch("urllib.request.urlopen", side_effect=err):
            resp = client.post("/generate", json={
                "prompt": "test",
                "request_id": "req-005",
            })
        assert resp.status_code == 503

    def test_generate_response_has_no_jwt_fields(self):
        app = create_inference_app(_make_cfg())
        client = TestClient(app, raise_server_exceptions=False)

        with patch("urllib.request.urlopen", return_value=_make_ollama_mock()):
            resp = client.post("/generate", json={
                "prompt": "test",
                "request_id": "req-006",
            })

        body_str = str(resp.json()).lower()
        # Check for auth-related fields (NOT "token" since prompt_tokens/output_tokens are valid)
        for forbidden in ("jwt", "authorization", "bearer", "credential", "secret"):
            assert forbidden not in body_str, (
                f"Forbidden field '{forbidden}' found in /generate response"
            )

    def test_generate_max_tokens_validation(self):
        app = create_inference_app(_make_cfg())
        client = TestClient(app, raise_server_exceptions=False)

        # Exceeding max limit (2048) should fail validation
        resp = client.post("/generate", json={
            "prompt": "test",
            "request_id": "req-007",
            "max_new_tokens": 99999,
        })
        assert resp.status_code == 422
