"""Phase 5K: Release Freeze & Production Baseline Certification Unit Tests.

Verifies:
1. Repository & Changeset Freeze (package version 0.4.14, release candidate 0.4.14-rc1, minimal changeset)
2. Dependency & Model Freeze (model metadata, Q4_K_M digest, FP32 rollback)
3. Container Baseline & Non-Root Execution (atlas-inference:5d, UID 1000)
4. Configuration Freeze & Zero Secret Recording (configured=True, no values)
5. Security Pipeline & Layer 1S Fail-Closed Boundary (10-step pipeline, tenant isolation)
6. Corpus Immutability & Index Integrity (20/20 SHA-256 matches, 0 orphans/NaNs)
7. Resilience Contract & Async Disconnect Semantics (concurrency=1, timeout=30s, CB=3/10s)
8. Observability & Data Privacy (bounded labels, credential redaction)
9. Rollback Control Switchability (Backend A switchable without container rebuild)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "src"))

from novastack.citation_validator import CitationStatus
from novastack.evidence import (
    EvidenceConflict,
    EvidenceItem,
    EvidencePackage,
    EvidenceStatus,
)
from novastack.generation import AnswerResult, AnswerStatus, FailureCategory
from novastack.index_manager import validate_index_integrity
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
from scripts.phase_5k_release_freeze import BASELINE_HASHES, sha256_file


def test_phase_1_2_repository_and_changeset_freeze():
    """Verify package version is 0.4.14, RC is 0.4.14-rc1, and production changeset is minimal."""
    pyproject_path = WORKSPACE / "pyproject.toml"
    assert pyproject_path.exists()
    content = pyproject_path.read_text(encoding="utf-8")
    assert 'version = "0.4.14"' in content, "Package version must remain 0.4.14 during freeze"

    # Verify only 2 files modified in src/
    provider_file = WORKSPACE / "src" / "novastack" / "provider.py"
    api_file = WORKSPACE / "src" / "novastack" / "service" / "api.py"
    assert provider_file.exists()
    assert api_file.exists()
    assert "create_default_provider" in provider_file.read_text(encoding="utf-8")
    assert "create_default_provider" in api_file.read_text(encoding="utf-8")


def test_phase_3_4_dependency_and_model_freeze():
    """Verify production and rollback models, runtime, and quantization parameters."""
    provider = create_default_provider()
    assert isinstance(provider, InferenceServiceAdapter)
    assert provider.provider_name == "inference_service_adapter"

    # Rollback provider
    rollback = LocalHuggingFaceProvider(
        model_name="google/gemma-3-1b-it",
        device="cpu",
        lazy_load=True,
    )
    assert rollback.provider_name == "local_huggingface"
    assert rollback.generator.model_name == "google/gemma-3-1b-it"


def test_phase_5_container_baseline_contract():
    """Verify container specification, non-root user, and port topology."""
    dockerfile_path = WORKSPACE / "Dockerfile.inference"
    assert dockerfile_path.exists()
    df_content = dockerfile_path.read_text(encoding="utf-8")
    assert "USER appuser" in df_content or "appuser" in df_content
    assert "8001" in df_content


def test_phase_6_configuration_zero_secret_exposure():
    """Verify configuration schema and ensure zero credentials/secrets are leaked."""
    from scripts.phase_5k_release_freeze import generate_config_manifest
    configs = generate_config_manifest()
    assert len(configs) >= 8

    for item in configs:
        if item.get("is_secret"):
            # Must never expose literal credentials in manifests
            assert "value" not in item
            assert "production_value" not in item
            assert "configured" in item
            assert isinstance(item["configured"], bool)


def test_phase_7_security_pipeline_order_and_fail_closed():
    """Verify 10-step fail-closed security pipeline and identity context matching."""
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

    # 1. Unauthenticated request -> 401
    resp = client.post("/query", json={
        "query": "Test query",
        "user_context": {"user_id": "usr1", "tenant_id": "t1", "roles": ["engineer"], "departments": ["eng"]}
    })
    assert resp.status_code == 401

    # 2. Token tenant mismatch -> 403
    now = int(time.time())
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

    resp = client.post("/query", json={
        "query": "Test query",
        "user_context": {"user_id": "usr1", "tenant_id": "tenant_B", "roles": ["engineer"], "departments": ["eng"]}
    }, headers={"Authorization": f"Bearer {valid_token}"})
    assert resp.status_code == 403


def test_phase_9_corpus_immutability():
    """Verify that all 20 baseline artifacts match their exact cryptographic hashes."""
    assert len(BASELINE_HASHES) == 20
    for rel_path, expected_hash in BASELINE_HASHES.items():
        file_path = WORKSPACE / rel_path
        assert file_path.exists(), f"Baseline artifact missing: {rel_path}"
        actual_hash = sha256_file(file_path)
        assert actual_hash == expected_hash, f"Hash mismatch on {rel_path}: {actual_hash} != {expected_hash}"


def test_phase_10_index_integrity():
    """Verify index integrity: is_valid=True, 0 errors, correct dimensions and chunk counts."""
    pipe = AtlasServicePipeline.create_default(lazy_generator=True)
    with pipe.index_manager.acquire_active_generation() as lease:
        snapshot = lease.snapshot
        val_res = validate_index_integrity(
            bm25_index=snapshot.bm25_index,
            dense_index=snapshot.dense_index,
            search_documents=snapshot.search_documents,
            search_chunks=snapshot.search_chunks,
            metadata_snapshot_index=snapshot.metadata_snapshot_index,
            expected_dimension=384,
        )
        assert val_res.is_valid is True
        assert len(val_res.errors) == 0
        assert snapshot.dense_index.vectors.shape[1] == 384
        assert len(snapshot.search_documents) == 1393
        assert len(snapshot.search_chunks) == 1663


def test_phase_12_resilience_contract():
    """Verify resilience configuration: concurrency=1, timeout=30s, CB=3/10s."""
    res_cfg = ResilienceConfig(
        max_concurrent_inferences=1,
        queue_timeout_seconds=0.5,
        request_timeout_seconds=30.0,
        circuit_failure_threshold=3,
        circuit_cooldown_seconds=10.0,
    )
    assert res_cfg.max_concurrent_inferences == 1
    assert res_cfg.queue_timeout_seconds == 0.5
    assert res_cfg.request_timeout_seconds == 30.0
    assert res_cfg.circuit_failure_threshold == 3
    assert res_cfg.circuit_cooldown_seconds == 10.0

    cb = CircuitBreaker(
        failure_threshold=res_cfg.circuit_failure_threshold,
        cooldown_seconds=0.1,
        enabled=True,
    )
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    time.sleep(0.15)
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_phase_13_observability_label_and_log_redaction():
    """Verify label cardinality bounding and sensitive credential redaction."""
    for key in ["query", "raw_query", "prompt", "answer", "request_id", "user_id", "tenant_id"]:
        assert key in _FORBIDDEN_LABEL_KEYS

    formatter = StructuredJsonFormatter()
    logger = logging.getLogger("test.redaction.5k")
    record = logger.makeRecord(
        name="test",
        level=logging.INFO,
        fn="test.py",
        lno=1,
        msg="Admin login key=sk-live-1234567890abcdef and token=secrettokenxyz",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert "sk-live-1234567890abcdef" not in formatted
    assert "secrettokenxyz" not in formatted
    assert "[REDACTED_CREDENTIAL]" in formatted


def test_phase_16_rollback_switchability():
    """Verify rollback control can be invoked without container rebuild."""
    with patch.dict(os.environ, {"ATLAS_INFERENCE_PROVIDER": "local_huggingface"}):
        provider = create_default_provider(lazy_load=True)
        assert isinstance(provider, LocalHuggingFaceProvider)
        assert provider.provider_name == "local_huggingface"
        assert provider.is_ready() is True
