"""Test-only authenticated-client support for Phase 4T.

The production API has no identity bypass: it refuses queries until a trusted
JWT verifier is configured and receives a valid bearer token.  Historical API
tests predate that boundary and exercise request payloads directly, so this
fixture models the trusted edge by supplying a signed test credential that
matches the caller context already declared by each test.  Explicit
``Authorization`` headers are never changed, allowing negative authentication
tests to reach the real verifier unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
import httpx


_TEST_ISSUER = "https://tests.atlas.example/issuer"
_TEST_AUDIENCE = "atlas-api-tests"
_TEST_SECRET = "atlas-phase-4t-test-secret-at-least-32-bytes-long"


def _b64url_json(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")


def _test_bearer_token(context: dict[str, Any]) -> str:
    now = int(time.time())
    roles = context.get("roles") or []
    departments = context.get("departments") or []
    payload: dict[str, Any] = {
        "iss": _TEST_ISSUER,
        "aud": _TEST_AUDIENCE,
        "sub": context.get("user_id") or "USR-PHASE-4T-TEST",
        "tenant_id": context["tenant_id"],
        "roles": roles,
        "departments": departments,
        "exp": now + 300,
        "iat": now,
    }
    if context.get("user_role") is not None:
        payload["role"] = context["user_role"]
    if context.get("user_department") is not None:
        payload["department"] = context["user_department"]
    signed = f"{_b64url_json({'alg': 'HS256', 'typ': 'JWT'})}.{_b64url_json(payload)}"
    signature = hmac.new(
        _TEST_SECRET.encode("utf-8"), signed.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signed}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


@pytest.fixture(autouse=True)
def configure_phase_4t_test_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure a verifier for each test without weakening production defaults."""
    monkeypatch.setenv("ATLAS_AUTH_ISSUER", _TEST_ISSUER)
    monkeypatch.setenv("ATLAS_AUTH_AUDIENCE", _TEST_AUDIENCE)
    monkeypatch.setenv("ATLAS_AUTH_HS256_SECRET", _TEST_SECRET)
    monkeypatch.setenv("ATLAS_AUTH_CLOCK_SKEW_SECONDS", "0")


@pytest.fixture(autouse=True)
def attach_test_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authenticate legacy ``/query`` tests unless they opt out explicitly."""
    original_request = TestClient.request
    original_async_request = httpx.AsyncClient.request

    def add_test_bearer_token(method: str, url: Any, kwargs: dict[str, Any]) -> None:
        """Mutate one test request only when it has no explicit credential."""
        is_query = str(method).upper() == "POST" and str(url).split("?", 1)[0].endswith("/query")
        headers = kwargs.get("headers")
        has_authorization = bool(headers) and any(
            str(key).lower() == "authorization" for key in headers
        )
        body = kwargs.get("json")
        context = body.get("user_context") if isinstance(body, dict) else None
        if is_query and not has_authorization and isinstance(context, dict) and context.get("tenant_id"):
            updated_headers = dict(headers or {})
            updated_headers["Authorization"] = f"Bearer {_test_bearer_token(context)}"
            kwargs["headers"] = updated_headers

    def authenticated_request(
        client: TestClient,
        method: str,
        url: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        add_test_bearer_token(method, url, kwargs)
        return original_request(client, method, url, *args, **kwargs)

    async def authenticated_async_request(
        client: httpx.AsyncClient,
        method: str,
        url: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        add_test_bearer_token(method, url, kwargs)
        return await original_async_request(client, method, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "request", authenticated_request)
    monkeypatch.setattr(httpx.AsyncClient, "request", authenticated_async_request)


def verify_sha256_platform_independent(actual: bytes | str | Path, expected_sha: str) -> bool:
    """Verify SHA-256 against expected hash with platform line-ending tolerance (raw, LF, or CRLF)."""
    if isinstance(actual, Path):
        data = actual.read_bytes()
    elif isinstance(actual, str):
        data = actual.encode("utf-8")
    else:
        data = actual

    # 1. Exact raw match
    if hashlib.sha256(data).hexdigest() == expected_sha:
        return True
    # 2. Canonical LF-normalized match
    lf_data = data.replace(b"\r\n", b"\n")
    if hashlib.sha256(lf_data).hexdigest() == expected_sha:
        return True
    # 3. Canonical CRLF-normalized match
    crlf_data = lf_data.replace(b"\n", b"\r\n")
    if hashlib.sha256(crlf_data).hexdigest() == expected_sha:
        return True
    return False
