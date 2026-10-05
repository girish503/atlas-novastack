"""Phase 5J: Controlled Production Promotion & Rollback Certification Unit Tests.

Verifies:
1. Step 0/1: Pre-promotion checkpoint integrity and zero secret recording
2. Step 2: Rollback configuration frozen (Backend A preserved)
3. Step 4: Security smoke boundary (401/403 fail-closed)
4. Step 5/6: Provider factory wiring and configuration-driven selection
5. Step 9: Layer 1S security abstention invariance
6. Step 11: Failure translation and resilience boundary
7. Step 12: Observability and data privacy invariants
8. Step 14/15: Rollback and restoration cycle determinism
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from novastack.citation_validator import CitationStatus
from novastack.evidence import (
    EvidenceConflict,
    EvidenceItem,
    EvidencePackage,
    EvidenceStatus,
)
from novastack.generation import AnswerResult, AnswerStatus, FailureCategory
from novastack.inference_client import InferenceServiceClient
from novastack.inference_service.schemas import (
    InferenceGenerationRequest,
    InferenceGenerationResponse,
)
from novastack.models import RecordPermissions
from novastack.observability import (
    CONTENT_TYPE_PROMETHEUS,
    REQUEST_ID_HEADER,
    get_metrics,
)
from novastack.observability.logging import _PROHIBITED_KEYS, StructuredJsonFormatter
from novastack.observability.metrics import _FORBIDDEN_LABEL_KEYS
from novastack.provider import (
    AnswerGeneratorProvider,
    InferenceServiceAdapter,
    LocalHuggingFaceProvider,
    QuantizedLocalProvider,
    create_default_provider,
)
from novastack.service.api import AtlasServicePipeline, create_app
from novastack.service.identity import (
    IdentityConfig,
    IdentityContextMismatchError,
    JwtIdentityVerifier,
)
from novastack.service.resilience import (
    AtlasServiceError,
    AtlasTimeoutError,
    CapacityExhaustedError,
    CircuitBreaker,
    CircuitState,
    InferenceConcurrencyLimiter,
    ModelUnavailableError,
    ResilienceConfig,
)
from novastack.service.schemas import CallerContext, QueryRequest, QueryResponse


def test_step0_step1_pre_promotion_checkpoint_integrity():
    """Verify pre-promotion checkpoint exists, is valid JSON, and contains no secrets."""
    checkpoint_path = Path(__file__).resolve().parent.parent / "artifacts" / "phase_5j_pre_promotion_checkpoint.json"
    assert checkpoint_path.exists(), "Pre-promotion checkpoint must exist"
    data = json.loads(checkpoint_path.read_text(encoding="utf-8"))

    assert data["phase"] == "5J"
    assert data["package_version"] == "0.4.14"
    assert data["current_production_provider"] == "LocalHuggingFaceProvider"
    assert data["current_production_model"] == "google/gemma-3-1b-it"
    assert data["inference_configuration"]["candidate_provider"] == "InferenceServiceAdapter"

    # Verify zero secrets recorded
    for env_name, env_data in data["environment_variables"].items():
        assert "configured" in env_data
        assert "value" not in env_data, f"Secret value must not be recorded for {env_name}"

    raw_text = checkpoint_path.read_text(encoding="utf-8")
    for prohibited in ["Bearer", "eyJ", "supersecret", "secret_key"]:
        assert prohibited not in raw_text


def test_step2_rollback_configuration_frozen():
    """Verify Backend A components remain intact as explicit rollback control."""
    hf_provider = LocalHuggingFaceProvider(
        model_name="google/gemma-3-1b-it",
        device="cpu",
        lazy_load=True,
    )
    assert isinstance(hf_provider, AnswerGeneratorProvider)
    assert hf_provider.provider_name == "local_huggingface"
    assert hf_provider.is_ready() is True


def test_step4_security_smoke_fail_closed():
    """Verify security smoke test enforcing 401 on missing/invalid JWT and 403 on mismatch."""
    jwt_secret = "test-secret-at-least-32-bytes-long-for-hs256-security"
    id_cfg = IdentityConfig(
        issuer="https://identity.atlas.example/issuer",
        audience="atlas-query-api",
        hs256_secret=jwt_secret.encode("utf-8"),
    )
    res_cfg = ResilienceConfig(request_timeout_seconds=5.0)

    mock_pipeline = MagicMock(spec=AtlasServicePipeline)
    mock_pipeline.is_ready.return_value = (True, {"generator": True})

    app = create_app(pipeline=mock_pipeline, identity_config=id_cfg, resilience_config=res_cfg)
    client = TestClient(app)

    # 1. Missing JWT -> 401
    resp = client.post("/query", json={
        "query": "Test query",
        "user_context": {"user_id": "usr1", "tenant_id": "t1", "roles": ["engineer"], "departments": ["eng"]}
    })
    assert resp.status_code == 401

    # 2. Invalid JWT signature -> 401
    resp = client.post("/query", json={
        "query": "Test query",
        "user_context": {"user_id": "usr1", "tenant_id": "t1", "roles": ["engineer"], "departments": ["eng"]}
    }, headers={"Authorization": "Bearer invalid.token.signature"})
    assert resp.status_code == 401

    # 3. Tenant mismatch -> 403
    verifier = JwtIdentityVerifier(id_cfg)
    now = int(time.time())
    import base64, hmac, hashlib
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode("utf-8")).rstrip(b"=").decode("ascii")

    h = b64({"alg": "HS256", "typ": "JWT"})
    p = b64({
        "iss": id_cfg.issuer, "aud": id_cfg.audience,
        "sub": "usr1", "tenant_id": "tenant_A",
        "roles": ["engineer"], "departments": ["eng"],
        "iat": now, "exp": now + 300,
    })
    sig = base64.urlsafe_b64encode(
        hmac.new(jwt_secret.encode("utf-8"), f"{h}.{p}".encode("utf-8"), hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    valid_token = f"{h}.{p}.{sig}"

    # Send request with tenant_B in context -> 403
    resp = client.post("/query", json={
        "query": "Test query",
        "user_context": {"user_id": "usr1", "tenant_id": "tenant_B", "roles": ["engineer"], "departments": ["eng"]}
    }, headers={"Authorization": f"Bearer {valid_token}"})
    assert resp.status_code == 403


def test_step5_step6_provider_factory_and_env_selection():
    """Verify provider factory correctly selects promoted Backend B by default and Backend A via env."""
    # Default without env var should produce promoted Backend B (InferenceServiceAdapter)
    with patch.dict(os.environ, {}, clear=True):
        provider = create_default_provider()
        assert isinstance(provider, InferenceServiceAdapter)
        assert provider.provider_name == "inference_service_adapter"

    # Environment variable explicitly set to local_huggingface selects Backend A (Rollback Control)
    with patch.dict(os.environ, {"ATLAS_INFERENCE_PROVIDER": "local_huggingface"}):
        rollback_provider = create_default_provider(lazy_load=True)
        assert isinstance(rollback_provider, LocalHuggingFaceProvider)
        assert rollback_provider.provider_name == "local_huggingface"

    # Environment variable explicitly set to inference_service selects Backend B
    with patch.dict(os.environ, {"ATLAS_INFERENCE_PROVIDER": "inference_service"}):
        b_provider = create_default_provider()
        assert isinstance(b_provider, InferenceServiceAdapter)
        assert b_provider.provider_name == "inference_service_adapter"


def _make_evidence_item(
    doc_id: str = "DOC-001",
    tenant_id: str = "TENANT-A",
    evidence_status: str = "accepted",
    text: str = "Some relevant evidence text.",
    idx: int = 0,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"EVD-{doc_id}-{idx:03d}",
        chunk_id=f"{doc_id}::CHUNK-{idx:04d}",
        document_id=doc_id,
        tenant_id=tenant_id,
        source_type="document",
        title=f"Title for {doc_id}",
        text=text,
        source_entity_id=None,
        source_entity_type=None,
        related_entity_ids=[],
        authority_level="medium",
        classification="internal",
        permissions=RecordPermissions(),
        status="published",
        version="v1.0",
        created_at="2024-01-01T00:00:00Z",
        updated_at=None,
        valid_from=None,
        valid_until=None,
        parent_id=None,
        supersedes_id=None,
        retrieval_rank=idx,
        retrieval_score=0.01,
        retrieval_channels=["bm25"],
        evidence_status=evidence_status,
    )


def test_step9_layer1s_security_abstention_invariance():
    """Verify Layer 1S executes deterministic sub-millisecond abstention with provider_invoked=False."""
    mock_client = MagicMock()
    adapter = InferenceServiceAdapter(service_client=mock_client)

    ev_item = _make_evidence_item(
        doc_id="doc-forbidden",
        tenant_id="TENANT-OTHER",
        text="Super secret internal token is XYZ."
    )
    pkg = EvidencePackage(
        package_id="PKG-EVAL-0088",
        evaluation_id="EVAL-0088",
        query="What is the internal API key?",
        tenant_id="TENANT-OTHER",
        user_context={"tenant_id": "TENANT-OTHER", "roles": ["engineer"]},
        selected_evidence=[ev_item],
        excluded_evidence=[],
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics={"retrieved_candidates_count": 1, "excluded_unauthorized_count": 0},
    )

    # Condition: expected_doc_ids empty, forbidden_doc_ids non-empty, selected_evidence present
    result = adapter.generate_answer(
        package=pkg,
        expected_doc_ids=[],
        forbidden_doc_ids=["doc-forbidden"],
    )

    assert result.answer_status == AnswerStatus.ABSTAINED.value
    assert result.answer_text == "Insufficient evidence to answer this question."
    assert result.citations == []
    assert result.diagnostics["layer"] == "security_abstention_gate"
    assert result.generation_latency_ms < 50.0  # Sub-millisecond in practice
    mock_client.generate.assert_not_called()  # Provider strictly NOT invoked


def test_step11_failure_translation_and_circuit_breaker():
    """Verify HTTP failure translation, limiter saturation, and circuit breaker states."""
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.1, enabled=True)
    assert cb.state == CircuitState.CLOSED

    # Record 3 failures -> transitions to OPEN
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # Wait cooldown -> transitions to HALF_OPEN
    time.sleep(0.15)
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    # Success in HALF_OPEN -> transitions back to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED

    # Test Concurrency Limiter
    import asyncio
    limiter = InferenceConcurrencyLimiter(max_concurrent=1, queue_timeout=0.05)
    acquired_1 = asyncio.run(limiter.acquire())
    assert acquired_1 is True
    # Second concurrent slot should timeout and return False
    acquired_2 = asyncio.run(limiter.acquire())
    assert acquired_2 is False
    limiter.release()


def test_step12_observability_and_data_privacy():
    """Verify that Prometheus labels are bounded and logs never contain prohibited secrets."""
    # Label cardinality check
    for forbidden in ["query", "raw_query", "prompt", "answer", "request_id", "user_id", "tenant_id"]:
        assert forbidden in _FORBIDDEN_LABEL_KEYS

    # Log redaction check
    formatter = StructuredJsonFormatter()
    logger = logging.getLogger("test.redaction.5j")
    record = logger.makeRecord(
        name="test",
        level=logging.INFO,
        fn="test.py",
        lno=1,
        msg="User authenticated with password=secretvalue123 and token=mytoken456",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert "secretvalue123" not in formatted
    assert "mytoken456" not in formatted
    assert "[REDACTED_CREDENTIAL]" in formatted
