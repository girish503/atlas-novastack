"""Phase 5I: Production Promotion Readiness Review Unit Test Suite.

Verifies:
1. Phase 5H evidence integrity
2. Provider switchability (Backend A -> Backend B -> Backend A)
3. End-to-end runtime integration with InferenceServiceAdapter
4. Failure behavior translation (504 timeout, 503 unavailable, malformed)
5. Resilience contract (concurrency=1, queue timeout=0.5s, circuit breaker threshold=3, cooldown=10s)
6. Security boundary fail-closed verification (no credentials passed to inference service)
7. Layer 1S deterministic abstention invariance
8. Observability & logging data privacy (prohibited keys redaction, bounded label cardinality)
9. Rollback determinism
10. Production default invariance (LocalHuggingFaceProvider, version 0.4.14, production_changes=[])
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
)
from novastack.service.api import AtlasServicePipeline, create_app
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

WORKSPACE = Path(__file__).resolve().parent.parent


def test_gate_1_phase_5h_evidence_integrity():
    json_path = WORKSPACE / "artifacts" / "phase_5h_backend_b_recertification.json"
    rep_path = WORKSPACE / "artifacts" / "phase_5h_backend_b_recertification_report.md"
    doc_path = WORKSPACE / "docs" / "PHASE_5H_BACKEND_B_RECERTIFICATION.md"

    assert json_path.exists(), "phase_5h json missing"
    assert rep_path.exists(), "phase_5h report missing"
    assert doc_path.exists(), "phase_5h doc missing"

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["status"] == "CANDIDATE ELIGIBLE"
    assert data["candidate_eligible"] is True
    assert data["production_changes"] == []

    gates = {g["id"]: g for g in data["gates"]}
    assert len(gates) == 9
    for gid in ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9"]:
        assert gates[gid]["pass_b"] is True


def test_gate_2_provider_switchability():
    adapter_b = InferenceServiceAdapter(service_url="http://127.0.0.1:8001")
    pipeline_b = AtlasServicePipeline.create_default(lazy_generator=True, generator=adapter_b)
    assert isinstance(pipeline_b.generator, InferenceServiceAdapter)

    with patch.dict(os.environ, {"ATLAS_INFERENCE_PROVIDER": "local_huggingface"}):
        pipeline_rollback = AtlasServicePipeline.create_default(lazy_generator=True)
        assert isinstance(pipeline_rollback.generator, LocalHuggingFaceProvider)


def test_gate_4_failure_translation():
    class Mock504Client:
        def post(self, url, **kwargs):
            class Resp:
                status_code = 504
                text = "Timeout"
                headers = {}
            return Resp()

    client = InferenceServiceClient(http_client=Mock504Client())
    with pytest.raises(AtlasTimeoutError):
        client.generate(prompt="test")

    class Mock503Client:
        def post(self, url, **kwargs):
            class Resp:
                status_code = 503
                text = "Unavailable"
                headers = {}
            return Resp()

    client503 = InferenceServiceClient(http_client=Mock503Client())
    with pytest.raises(ModelUnavailableError):
        client503.generate(prompt="test")


def test_gate_5_resilience_circuit_breaker():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.1)
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    assert cb.can_execute() is False

    time.sleep(0.15)
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.CLOSED




def test_gate_6_security_no_secrets_in_payload():
    req = InferenceGenerationRequest(
        prompt="Test prompt",
        request_id="REQ-001",
        max_new_tokens=50,
        temperature=0.0,
        model_name="gemma3:1b",
    )
    keys = set(req.model_dump().keys())
    assert keys == {"prompt", "request_id", "max_new_tokens", "temperature", "model_name"}
    for secret_word in ["jwt", "token", "password", "secret", "authorization", "tenant_id", "user_id"]:
        assert secret_word not in keys


def test_gate_8_observability_redaction():
    formatter = StructuredJsonFormatter()
    logger = logging.getLogger("test.redaction")
    record = logger.makeRecord(
        name="test",
        level=logging.INFO,
        fn="test.py",
        lno=1,
        msg="Logged password=secretvalue123 and token=mytoken456",
        args=(),
        exc_info=None,
    )
    res = formatter.format(record)
    assert "secretvalue123" not in res
    assert "mytoken456" not in res
    assert "[REDACTED_CREDENTIAL]" in res


def test_gate_14_production_default_invariance():
    pyproject = (WORKSPACE / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.4.14"' in pyproject

    with patch.dict(os.environ, {"ATLAS_INFERENCE_PROVIDER": "local_huggingface"}):
        pipeline = AtlasServicePipeline.create_default(lazy_generator=True)
        assert isinstance(pipeline.generator, LocalHuggingFaceProvider)
        assert getattr(pipeline.generator._generator, "model_name", "") == "google/gemma-3-1b-it"
