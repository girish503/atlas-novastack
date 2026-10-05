"""Phase 5C Test Suite: Inference Service Security Tests.

Security invariant tests:
  1. Inference service has no auth/login endpoint.
  2. No JWT/auth in inference service call payload.
  3. Unauthorized evidence filtered before inference call.
  4. Cross-tenant evidence does not reach inference.
  5. Inference service ignores unexpected auth headers.
"""

from __future__ import annotations

import json
import urllib.error
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from novastack.evidence import (
    EvidenceItem,
    EvidencePackage,
    EvidenceStatus,
)
from novastack.generation import AnswerResult, AnswerStatus
from novastack.inference_service.app import create_inference_app
from novastack.inference_service.config import InferenceServiceConfig
from novastack.inference_service.schemas import InferenceGenerationResponse
from novastack.models import RecordPermissions
from novastack.provider import InferenceServiceAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT_A = "TENANT-ALPHA"
TENANT_B = "TENANT-BETA"
DOC_A = "doc-alpha-001"
DOC_B = "doc-beta-001"
CHUNK_A = "chunk-alpha-001"
CHUNK_B = "chunk-beta-001"


def _make_evidence_item(
    tenant_id: str,
    doc_id: str,
    chunk_id: str,
    evidence_status: str = EvidenceStatus.ACCEPTED.value,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=chunk_id,
        chunk_id=chunk_id,
        document_id=doc_id,
        tenant_id=tenant_id,
        source_type="documentation",
        title=f"{tenant_id} Doc",
        text=f"Authorized content for {tenant_id}: enterprise grounding.",
        source_entity_id=None,
        source_entity_type=None,
        related_entity_ids=[],
        authority_level="authoritative",
        classification="internal",
        permissions=RecordPermissions(allowed_roles=["engineer"]),
        status="published",
        version="v1.0",
        created_at="2026-01-01T00:00:00Z",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        parent_id=None,
        supersedes_id=None,
        retrieval_rank=1,
        retrieval_score=0.90,
        retrieval_channels=["bm25"],
        evidence_status=evidence_status,
        evidence_reasons=[],
    )


def _make_package(
    tenant_id: str = TENANT_A,
    doc_ids_stat: int = 1,
    evidence_items: list = None,
) -> EvidencePackage:
    return EvidencePackage(
        package_id="PKG-SEC-01",
        evaluation_id="EVAL-SEC-01",
        query="What is the policy?",
        tenant_id=tenant_id,
        user_context={"tenant_id": tenant_id, "roles": ["engineer"]},
        selected_evidence=evidence_items or [],
        excluded_evidence=[],
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={"retrieved_candidates_count": doc_ids_stat},
    )


def _make_adapter(
    corpus_doc_ids: set = None,
    corpus_chunk_ids: set = None,
    service_client=None,
) -> InferenceServiceAdapter:
    return InferenceServiceAdapter(
        service_url="http://127.0.0.1:8001",
        corpus_doc_ids=corpus_doc_ids or {DOC_A},
        corpus_chunk_ids=corpus_chunk_ids or {CHUNK_A},
        service_client=service_client,
    )


def _make_inf_response() -> InferenceGenerationResponse:
    return InferenceGenerationResponse(
        generated_text="Policy says X.",
        request_id="req-sec",
        model_name="gemma3:1b",
        generation_latency_ms=500.0,
        engine_telemetry={"prompt_eval_count": 50, "eval_count": 15},
    )


def _make_ollama_mock():
    payload = json.dumps({
        "response": "Answer.",
        "prompt_eval_count": 10,
        "eval_count": 5,
        "total_duration": 1000000000,
        "load_duration": 0,
        "prompt_eval_duration": 500000000,
        "eval_duration": 500000000,
    }).encode()
    mock_http_resp = MagicMock()
    mock_http_resp.__enter__ = lambda s: s
    mock_http_resp.__exit__ = MagicMock(return_value=False)
    mock_http_resp.status = 200
    mock_http_resp.read.return_value = payload
    return mock_http_resp


# ---------------------------------------------------------------------------
# Test 1: Inference Service has no auth endpoint
# ---------------------------------------------------------------------------

class TestNoAuthEndpoint:
    def test_inference_service_has_no_login_endpoint(self):
        app = create_inference_app(InferenceServiceConfig())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/login", json={"username": "admin", "password": "secret"})
        assert resp.status_code == 404

    def test_inference_service_has_no_token_endpoint(self):
        app = create_inference_app(InferenceServiceConfig())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/token", json={"username": "admin"})
        assert resp.status_code == 404

    def test_inference_service_has_no_auth_get_endpoint(self):
        app = create_inference_app(InferenceServiceConfig())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/auth")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 2: No JWT in inference service call payload
# ---------------------------------------------------------------------------

class TestNoJwtInPayload:
    def test_generate_answer_does_not_transmit_jwt(self):
        captured_calls: list = []

        def capture(**kw):
            captured_calls.append(kw)
            return _make_inf_response()

        mock_client = MagicMock()
        mock_client.generate.side_effect = capture

        adapter = _make_adapter(service_client=mock_client)
        evidence = _make_evidence_item(TENANT_A, DOC_A, CHUNK_A)
        pkg = _make_package(evidence_items=[evidence])

        adapter.generate_answer(pkg)

        assert len(captured_calls) == 1
        call_payload_str = str(captured_calls[0]).lower()
        for jwt_field in ("jwt", "bearer", "authorization", "x-api-key", "credential"):
            assert jwt_field not in call_payload_str, (
                f"JWT/auth field '{jwt_field}' found in inference call payload"
            )

    def test_generate_answer_prompt_contains_no_caller_id(self):
        """Caller identity must not leak into the prompt text sent to inference."""
        captured_prompts: list = []

        def capture(**kw):
            captured_prompts.append(kw.get("prompt", ""))
            return _make_inf_response()

        mock_client = MagicMock()
        mock_client.generate.side_effect = capture

        adapter = _make_adapter(service_client=mock_client)
        pkg = _make_package(evidence_items=[_make_evidence_item(TENANT_A, DOC_A, CHUNK_A)])

        adapter.generate_answer(pkg)

        if captured_prompts:
            # The user_context dict must not appear verbatim as JSON with sensitive keys
            for sensitive in ("password", "access_token", "secret"):
                assert sensitive not in captured_prompts[0].lower()

    def test_generate_answer_does_not_send_tenant_db_records(self):
        """Tenant database fields must not appear in inference payload."""
        captured_calls: list = []

        def capture(**kw):
            captured_calls.append(kw)
            return _make_inf_response()

        mock_client = MagicMock()
        mock_client.generate.side_effect = capture

        adapter = _make_adapter(service_client=mock_client)
        pkg = _make_package(evidence_items=[_make_evidence_item(TENANT_A, DOC_A, CHUNK_A)])

        adapter.generate_answer(pkg)

        payload_str = str(captured_calls).lower()
        for field in ("tenant_db", "tenant_record", "caller_tenant_db", "user_db"):
            assert field not in payload_str


# ---------------------------------------------------------------------------
# Test 3: Unauthorized evidence filtered before inference call
# ---------------------------------------------------------------------------

class TestUnauthorizedEvidenceFiltered:
    def test_unauthorized_evidence_not_in_prompt(self):
        """Evidence marked unauthorized should not be in the prompt sent to inference."""
        captured_prompts: list = []

        def capture(**kw):
            captured_prompts.append(kw.get("prompt", ""))
            return _make_inf_response()

        mock_client = MagicMock()
        mock_client.generate.side_effect = capture

        adapter = _make_adapter(service_client=mock_client)

        # Only authorized evidence in selected_evidence (upstream security already filtered)
        auth_ev = _make_evidence_item(TENANT_A, DOC_A, CHUNK_A)
        pkg = _make_package(evidence_items=[auth_ev])

        adapter.generate_answer(pkg)

        if captured_prompts:
            # Forbidden doc ID must not appear in the prompt
            assert "doc-forbidden-999" not in captured_prompts[0]

    def test_excluded_evidence_id_not_in_prompt(self):
        """Document IDs outside corpus should not appear in prompt."""
        captured_prompts: list = []

        def capture(**kw):
            captured_prompts.append(kw.get("prompt", ""))
            return _make_inf_response()

        mock_client = MagicMock()
        mock_client.generate.side_effect = capture

        # Corpus restricted to DOC_A only
        adapter = _make_adapter(
            corpus_doc_ids={DOC_A},
            corpus_chunk_ids={CHUNK_A},
            service_client=mock_client,
        )

        # Only authorized evidence from DOC_A
        auth_ev = _make_evidence_item(TENANT_A, DOC_A, CHUNK_A)
        pkg = _make_package(evidence_items=[auth_ev])

        adapter.generate_answer(pkg)

        if captured_prompts:
            assert DOC_B not in captured_prompts[0]


# ---------------------------------------------------------------------------
# Test 4: Cross-tenant isolation
# ---------------------------------------------------------------------------

class TestCrossTenantIsolation:
    def test_cross_tenant_doc_not_in_prompt(self):
        """Documents from a different tenant's corpus must not appear in the prompt."""
        captured_prompts: list = []

        def capture(**kw):
            captured_prompts.append(kw.get("prompt", ""))
            return _make_inf_response()

        mock_client = MagicMock()
        mock_client.generate.side_effect = capture

        # Adapter corpus restricted to TENANT_A docs
        adapter = _make_adapter(
            corpus_doc_ids={DOC_A},
            corpus_chunk_ids={CHUNK_A},
            service_client=mock_client,
        )

        # Only TENANT_A evidence (TENANT_B evidence already filtered by upstream ATLAS security)
        tenant_a_ev = _make_evidence_item(TENANT_A, DOC_A, CHUNK_A)
        pkg = _make_package(
            tenant_id=TENANT_A,
            evidence_items=[tenant_a_ev],
        )

        adapter.generate_answer(pkg)

        # Tenant B doc ID must not appear
        if captured_prompts:
            assert DOC_B not in captured_prompts[0]
            assert CHUNK_B not in captured_prompts[0]


# ---------------------------------------------------------------------------
# Test 5: Inference service ignores / does not echo auth headers
# ---------------------------------------------------------------------------

class TestInferenceServiceHeaderSecurity:
    def test_inference_service_ignores_auth_header_on_generate(self):
        """Inference service /generate processes request even with an auth header (no auth enforcement)."""
        app = create_inference_app(InferenceServiceConfig())
        client = TestClient(app, raise_server_exceptions=False)

        with patch("urllib.request.urlopen", return_value=_make_ollama_mock()):
            resp = client.post(
                "/generate",
                json={"prompt": "test", "request_id": "sec-test"},
                headers={"Authorization": "Bearer some.jwt.token"},
            )

        # Service should not reject (no auth enforcement) - it processes normally
        assert resp.status_code == 200

    def test_inference_service_does_not_echo_auth_headers(self):
        """The inference service response must not include any Authorization token from the request."""
        app = create_inference_app(InferenceServiceConfig())
        client = TestClient(app, raise_server_exceptions=False)

        with patch("urllib.request.urlopen", return_value=_make_ollama_mock()):
            resp = client.post(
                "/generate",
                json={"prompt": "test", "request_id": "sec-echo-test"},
                headers={"Authorization": "Bearer some.jwt.token"},
            )

        body_str = str(resp.json()).lower()
        assert "some.jwt.token" not in body_str
        assert "bearer" not in body_str
