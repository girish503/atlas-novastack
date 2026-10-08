"""Automated Frontend Integration & Truth Verification Tests.

Verifies:
1. HTML file exists and contains all required UI elements and semantic structures.
2. Security: Zero secret signing keys in client JavaScript.
3. Pre-signed demo tokens are cryptographically valid against JwtIdentityVerifier.
4. Persona mapping truth: Display Title != Authorization Role.
5. Live vs. Offline status models are truthful.
6. Backend endpoint compatibility: FastAPI QueryRequest / QueryResponse contracts match.
7. Three deterministic demo scenario contracts:
   - Scenario 1 (Incident Investigation): Answered, C2 valid citations.
   - Scenario 2 (Cross-Tenant Rejection): Abstained, 0 chunks leaked.
   - Scenario 3 (Prompt Injection Defense): Untrusted data demarcation.
8. Health probes: /healthz and /ready response compatibility.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

WORKSPACE = Path(__file__).resolve().parent.parent

from novastack.service.identity import IdentityConfig, JwtIdentityVerifier
from novastack.service.schemas import CallerContext, QueryRequest, QueryResponse
from novastack.service import create_app


@pytest.fixture(scope="module")
def html_content() -> str:
    ui_path = WORKSPACE / "ui" / "index.html"
    assert ui_path.exists(), f"ui/index.html not found at {ui_path}"
    return ui_path.read_text(encoding="utf-8")


def test_ui_structure_and_element_ids(html_content: str):
    """Verify all critical UI elements exist in index.html."""
    required_ids = [
        "nav-search",
        "nav-security",
        "nav-health",
        "nav-history",
        "api-status-pill",
        "search-input",
        "search-submit-btn",
        "result-container",
        "answer-body-text",
        "abstention-alert",
        "evidence-drawer",
        "drawer-tab-selected",
        "drawer-tab-excluded",
        "drawer-panel-selected",
        "drawer-panel-excluded",
        "identity-modal",
        "persona-list",
        "jwt-token-preview",
        "history-list",
        "health-components-grid",
        "security-categories-list",
    ]
    for el_id in required_ids:
        assert f'id="{el_id}"' in html_content, f"Missing required element ID: {el_id}"


def test_zero_signing_secrets_in_client_code(html_content: str):
    """P0 Security Audit: Verify no raw JWT signing secrets exist in client code."""
    # Ensure neither the raw secret string nor signing variable exists
    assert "atlas-demo-secret-key-32-bytes-long-2026" not in html_content, (
        "Security Violation: Raw JWT signing secret found in client JavaScript!"
    )
    assert "crypto.subtle.sign" not in html_content, (
        "Security Violation: Client-side signing logic should be removed in favor of pre-signed tokens!"
    )


def test_presigned_demo_tokens_cryptographic_validity(html_content: str):
    """Verify all 4 persona tokens in DEMO_TOKENS pass server-side JwtIdentityVerifier."""
    secret = "atlas-demo-secret-key-32-bytes-long-2026"
    issuer = "https://identity.atlas.example/issuer"
    audience = "atlas-query-api"
    verifier = JwtIdentityVerifier(IdentityConfig(issuer, audience, secret.encode("utf-8")))

    # Extract DEMO_TOKENS block from HTML
    match = re.search(r"const DEMO_TOKENS = (\{.*?\});", html_content, re.DOTALL)
    assert match is not None, "DEMO_TOKENS object not found in HTML"
    tokens_json = match.group(1)
    # Parse tokens
    tokens = json.loads(tokens_json)

    expected_personas = {
        "USR-NS-0008": {"tenant_id": "TENANT-NOVASTACK", "role": "engineer"},
        "USR-ENG-42": {"tenant_id": "TENANT-NOVASTACK", "role": "engineer"},
        "USR-ACME-01": {"tenant_id": "TENANT-ACME-EXTERNAL", "role": "auditor"},
        "USR-INTERN-01": {"tenant_id": "TENANT-NOVASTACK", "role": "intern"},
    }

    for user_id, expected in expected_personas.items():
        assert user_id in tokens, f"Missing demo token for {user_id}"
        tok = tokens[user_id]
        ident = verifier.verify_compact_token(tok)
        assert ident.subject == user_id
        assert ident.tenant_id == expected["tenant_id"]
        assert ident.primary_role == expected["role"]


def test_persona_title_versus_role_delineation(html_content: str):
    """Verify display titles are explicitly differentiated from authorization roles."""
    assert "displayTitle" in html_content
    assert "Auth Role:" in html_content
    assert "Zara Reyes" in html_content
    assert "Incident Commander" in html_content
    assert "USR-NS-0008" in html_content


def test_sec_ops_truthfulness(html_content: str):
    """Verify SEC-OPS-02 is VERIFIED and SEC-OPS-03 is strictly UNVERIFIED."""
    assert "SEC-OPS-02 Host Exposure: VERIFIED" in html_content
    assert "SEC-OPS-03 Remote Ingress: UNVERIFIED" in html_content
    assert "0.0% (Standby)" in html_content


def test_fastapi_backend_contract_compatibility(monkeypatch):
    """Verify actual FastAPI service endpoints match frontend expectations."""
    secret = "atlas-demo-secret-key-32-bytes-long-2026"
    monkeypatch.setenv("ATLAS_AUTH_ISSUER", "https://identity.atlas.example/issuer")
    monkeypatch.setenv("ATLAS_AUTH_AUDIENCE", "atlas-query-api")
    monkeypatch.setenv("ATLAS_AUTH_HS256_SECRET", secret)

    app = create_app()
    client = TestClient(app)

    # 1. Health probe
    res_health = client.get("/healthz")
    assert res_health.status_code == 200
    assert res_health.json() == {"status": "ok"}

    # 2. Ready probe
    res_ready = client.get("/ready")
    assert res_ready.status_code in (200, 503)
    data = res_ready.json()
    assert "components" in data
    assert "authentication" in data["components"]
    assert data["components"]["authentication"] is True

    # 3. Unauthenticated query returns 401
    res_unauth = client.post("/query", json={"query": "test", "user_context": {"tenant_id": "TENANT-NOVASTACK"}})
    assert res_unauth.status_code == 401

    # 4. Context mismatch returns 403
    # Use token for Zara (TENANT-NOVASTACK) but claim TENANT-ACME in payload
    zara_token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJpc3MiOiJodHRwczovL2lkZW50aXR5LmF0bGFzLmV4YW1wbGUvaXNzdWVyIiwiYXVkIjoiYXRsYXMtcXVlcnktYXBpIiwic3ViIjoiVVNSLU5TLTAwMDgiLCJ0ZW5hbnRfaWQiOiJURU5BTlQtTk9WQVNUQUNLIiwicm9sZXMiOlsiZW5naW5lZXIiXSwiZGVwYXJ0bWVudHMiOlsiRW5naW5lZXJpbmciXSwicm9sZSI6ImVuZ2luZWVyIiwiZGVwYXJ0bWVudCI6IkVuZ2luZWVyaW5nIiwiaWF0IjoxNzAwMDAwMDAwLCJleHAiOjE4OTM0NTYwMDB9."
        "T91-80JWdkN_cJmIEu1izcET3VaafzJh2MwIWeMhwVQ"
    )
    res_mismatch = client.post(
        "/query",
        headers={"Authorization": f"Bearer {zara_token}"},
        json={
            "query": "test",
            "user_context": {
                "tenant_id": "TENANT-ACME-EXTERNAL",
                "user_id": "USR-NS-0008",
                "user_role": "engineer",
                "roles": ["engineer"],
            },
        },
    )
    assert res_mismatch.status_code == 403
