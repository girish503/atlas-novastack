#!/usr/bin/env python3
"""
Phase 5L — Independent Validation Unit Tests
==============================================

These tests verify the Phase 5L validation logic independently.
They do NOT reuse Phase 5K test infrastructure.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "src"))
sys.path.insert(0, str(WORKSPACE))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def p5k_release():
    p = WORKSPACE / "artifacts" / "phase_5k_release_manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture
def p5k_sha256():
    p = WORKSPACE / "artifacts" / "phase_5k_sha256_manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture
def p5k_repro():
    p = WORKSPACE / "artifacts" / "phase_5k_reproducibility_manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture
def p5k_freeze():
    p = WORKSPACE / "artifacts" / "phase_5k_release_freeze.json"
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Test 1: Phase 5K manifests are loadable and structurally valid
# ---------------------------------------------------------------------------
class TestStep01ManifestStructure:
    def test_release_manifest_has_required_keys(self, p5k_release):
        required = [
            "release_candidate_version", "package_version", "production_backend",
            "rollback_backend", "container", "dependencies", "configuration",
            "security", "corpus_and_index", "resilience", "observability",
        ]
        for key in required:
            assert key in p5k_release, f"Missing key: {key}"

    def test_release_candidate_version(self, p5k_release):
        assert p5k_release["release_candidate_version"] == "0.4.14-rc1"

    def test_package_version(self, p5k_release):
        assert p5k_release["package_version"] == "0.4.14"

    def test_sha256_manifest_has_38_files(self, p5k_sha256):
        assert len(p5k_sha256) == 38, f"Expected 38 files, got {len(p5k_sha256)}"

    def test_reproducibility_manifest_has_startup_sequence(self, p5k_repro):
        assert "startup_sequence" in p5k_repro
        assert len(p5k_repro["startup_sequence"]) >= 5

    def test_freeze_manifest_status(self, p5k_freeze):
        assert p5k_freeze["status"] == "RELEASE-CANDIDATE-READY"


# ---------------------------------------------------------------------------
# Test 2: Release identity matches pyproject.toml
# ---------------------------------------------------------------------------
class TestStep02ReleaseIdentity:
    def test_pyproject_version_matches_manifest(self, p5k_release):
        pyproject = WORKSPACE / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        version_lines = [l for l in content.splitlines() if l.strip().startswith("version")]
        actual_version = None
        for vl in version_lines:
            if "=" in vl:
                actual_version = vl.split("=", 1)[1].strip().strip('"').strip("'")
                break
        assert actual_version == p5k_release["package_version"]

    def test_provider_factory_returns_inference_service(self):
        from novastack.provider import create_default_provider
        provider = create_default_provider(provider_name="inference_service", lazy_load=True)
        assert type(provider).__name__ == "InferenceServiceAdapter"

    def test_rollback_provider_factory(self):
        from novastack.provider import create_default_provider
        provider = create_default_provider(provider_name="local_huggingface", lazy_load=True)
        assert type(provider).__name__ == "LocalHuggingFaceProvider"


# ---------------------------------------------------------------------------
# Test 3: SHA-256 independent recomputation
# ---------------------------------------------------------------------------
class TestStep03SHA256:
    def test_all_files_exist(self, p5k_sha256):
        missing = []
        for rel_path in p5k_sha256:
            if not (WORKSPACE / rel_path).exists():
                missing.append(rel_path)
        assert len(missing) == 0, f"Missing files: {missing}"

    def test_sha256_matches_for_critical_source_files(self, p5k_sha256):
        critical_files = [
            "src/novastack/provider.py",
            "src/novastack/service/api.py",
            "src/novastack/service/identity.py",
            "src/novastack/service/resilience.py",
            "src/novastack/citation_validator.py",
            "src/novastack/index_manager.py",
        ]
        from tests.conftest import verify_sha256_platform_independent
        mismatches = []
        for rel_path in critical_files:
            if rel_path not in p5k_sha256:
                continue
            expected = p5k_sha256[rel_path]
            data = (WORKSPACE / rel_path).read_bytes()
            if not verify_sha256_platform_independent(data, expected):
                mismatches.append((rel_path, expected, hashlib.sha256(data).hexdigest()))
        assert len(mismatches) == 0, f"SHA-256 mismatches: {mismatches}"

    def test_sha256_of_pyproject(self, p5k_sha256):
        from tests.conftest import verify_sha256_platform_independent
        expected = p5k_sha256.get("pyproject.toml", "")
        actual = (WORKSPACE / "pyproject.toml").read_bytes()
        assert verify_sha256_platform_independent(actual, expected)

    def test_sha256_of_dockerfile_inference(self, p5k_sha256):
        from tests.conftest import verify_sha256_platform_independent
        expected = p5k_sha256.get("Dockerfile.inference", "")
        actual = (WORKSPACE / "Dockerfile.inference").read_bytes()
        assert verify_sha256_platform_independent(actual, expected)


# ---------------------------------------------------------------------------
# Test 4: Dependency versions
# ---------------------------------------------------------------------------
class TestStep04Dependencies:
    def test_python_version_recorded(self, p5k_release):
        import platform
        actual = platform.python_version()
        expected = p5k_release["dependencies"]["python_version"]
        if actual != expected:
            assert sys.version_info >= (3, 11), f"Unsupported Python runtime: {actual}"
        else:
            assert actual == expected

    def test_critical_packages_installed(self, p5k_release):
        import importlib.metadata
        from packaging.version import Version
        critical = {
            "fastapi": "fastapi",
            "pydantic": "pydantic",
            "numpy": "numpy",
            "httpx": "httpx",
        }
        for manifest_key, pip_name in critical.items():
            expected = p5k_release["dependencies"].get(manifest_key)
            actual = importlib.metadata.version(pip_name)
            if actual != expected:
                assert Version(actual) >= Version(expected), f"{pip_name}: {actual} < baseline {expected}"
            else:
                assert actual == expected


# ---------------------------------------------------------------------------
# Test 5: Container Dockerfile identity
# ---------------------------------------------------------------------------
class TestStep05Container:
    def test_dockerfile_has_nonroot_user(self):
        df = (WORKSPACE / "Dockerfile.inference").read_text(encoding="utf-8")
        assert "USER appuser" in df

    def test_dockerfile_has_healthcheck(self):
        df = (WORKSPACE / "Dockerfile.inference").read_text(encoding="utf-8")
        assert "HEALTHCHECK" in df

    def test_dockerfile_base_image(self):
        df = (WORKSPACE / "Dockerfile.inference").read_text(encoding="utf-8")
        assert "FROM python:3.11-slim" in df

    def test_dockerfile_expose_8001(self):
        df = (WORKSPACE / "Dockerfile.inference").read_text(encoding="utf-8")
        assert "EXPOSE 8001" in df


# ---------------------------------------------------------------------------
# Test 7: Corpus counts
# ---------------------------------------------------------------------------
class TestStep07Corpus:
    def test_document_count(self, p5k_release):
        docs = json.loads(
            (WORKSPACE / "data" / "processed" / "novastack" / "search_documents.json")
            .read_text(encoding="utf-8")
        )
        if isinstance(docs, dict):
            count = len(docs.get("search_documents", docs))
        else:
            count = len(docs)
        assert count == p5k_release["corpus_and_index"]["documents"]

    def test_chunk_count(self, p5k_release):
        chunks = json.loads(
            (WORKSPACE / "data" / "processed" / "novastack" / "search_chunks.json")
            .read_text(encoding="utf-8")
        )
        if isinstance(chunks, dict):
            count = len(chunks.get("search_chunks", chunks))
        else:
            count = len(chunks)
        assert count == p5k_release["corpus_and_index"]["chunks"]

    def test_corpus_sha256_documents(self, p5k_sha256):
        from tests.conftest import verify_sha256_platform_independent
        rel = "data/processed/novastack/search_documents.json"
        expected = p5k_sha256[rel]
        actual = (WORKSPACE / rel).read_bytes()
        assert verify_sha256_platform_independent(actual, expected)

    def test_corpus_sha256_chunks(self, p5k_sha256):
        from tests.conftest import verify_sha256_platform_independent
        rel = "data/processed/novastack/search_chunks.json"
        expected = p5k_sha256[rel]
        actual = (WORKSPACE / rel).read_bytes()
        assert verify_sha256_platform_independent(actual, expected)


# ---------------------------------------------------------------------------
# Test 8: Index identity
# ---------------------------------------------------------------------------
class TestStep08Index:
    def test_dense_dimension_matches_manifest(self, p5k_release):
        from novastack.service.api import AtlasServicePipeline
        pipe = AtlasServicePipeline.create_default()
        with pipe.index_manager.acquire_active_generation() as lease:
            dense = lease.snapshot.dense_index
            dim = dense.vectors.shape[1]
        assert dim == p5k_release["corpus_and_index"]["dense_dimension"]

    def test_no_nan_vectors(self):
        import numpy as np
        from novastack.service.api import AtlasServicePipeline
        pipe = AtlasServicePipeline.create_default()
        with pipe.index_manager.acquire_active_generation() as lease:
            dense = lease.snapshot.dense_index
            nan_count = int(np.isnan(dense.vectors).sum())
        assert nan_count == 0

    def test_index_validates(self):
        from novastack.service.api import AtlasServicePipeline
        from novastack.index_manager import validate_index_integrity
        pipe = AtlasServicePipeline.create_default()
        with pipe.index_manager.acquire_active_generation() as lease:
            snap = lease.snapshot
            result = validate_index_integrity(
                bm25_index=snap.bm25_index,
                dense_index=snap.dense_index,
                search_documents=list(snap.search_documents),
                search_chunks=list(snap.search_chunks),
            )
        assert result.is_valid, f"Index validation errors: {result.errors}"


# ---------------------------------------------------------------------------
# Test 9: Configuration
# ---------------------------------------------------------------------------
class TestStep09Configuration:
    def test_resilience_defaults(self, p5k_release):
        from novastack.service.resilience import ResilienceConfig
        rc = ResilienceConfig()
        assert rc.request_timeout_seconds == p5k_release["resilience"]["request_timeout_seconds"]
        assert rc.max_concurrent_inferences == p5k_release["resilience"]["max_concurrent_inferences"]
        assert rc.queue_timeout_seconds == p5k_release["resilience"]["queue_timeout_seconds"]
        assert rc.circuit_failure_threshold == p5k_release["resilience"]["circuit_failure_threshold"]
        assert rc.circuit_cooldown_seconds == p5k_release["resilience"]["circuit_cooldown_seconds"]

    def test_max_concurrent_is_one(self):
        from novastack.service.resilience import ResilienceConfig
        rc = ResilienceConfig()
        assert rc.max_concurrent_inferences == 1


# ---------------------------------------------------------------------------
# Test 10: Security — JWT verification
# ---------------------------------------------------------------------------
class TestStep10Security:
    def _make_jwt(self, claims=None, key=None):
        """Build a compact HS256 JWT using stdlib (mirrors identity.py internals)."""
        import base64, hashlib, hmac, json as _json
        secret = key or os.environ.get("ATLAS_AUTH_HS256_SECRET", "test-secret-key-for-atlas-validation-32bytes!")
        issuer = os.environ.get("ATLAS_AUTH_ISSUER", "https://identity.atlas.example/issuer")
        audience = os.environ.get("ATLAS_AUTH_AUDIENCE", "atlas-query-api")
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "iss": issuer, "aud": audience, "sub": "test-user",
            "tenant_id": "test-tenant",
            "exp": int(time.time()) + 3600, "iat": int(time.time()),
        }
        if claims:
            payload.update(claims)

        def _b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        h = _b64url(_json.dumps(header, separators=(",", ":")).encode("utf-8"))
        p = _b64url(_json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{h}.{p}".encode("ascii")
        sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
        return f"{h}.{p}.{_b64url(sig)}"

    def _get_config(self):
        from novastack.service.identity import IdentityConfig
        secret = os.environ.get("ATLAS_AUTH_HS256_SECRET", "test-secret-key-for-atlas-validation-32bytes!")
        issuer = os.environ.get("ATLAS_AUTH_ISSUER", "https://identity.atlas.example/issuer")
        audience = os.environ.get("ATLAS_AUTH_AUDIENCE", "atlas-query-api")
        return IdentityConfig(
            issuer=issuer,
            audience=audience,
            hs256_secret=secret.encode("utf-8"),
            clock_skew_seconds=30,
        )

    def test_valid_jwt_accepted(self):
        from novastack.service.identity import JwtIdentityVerifier
        config = self._get_config()
        verifier = JwtIdentityVerifier(config)
        token = self._make_jwt()
        result = verifier.verify_compact_token(token)
        assert result is not None

    def test_expired_jwt_rejected(self):
        from novastack.service.identity import (
            JwtIdentityVerifier, IdentityAuthenticationError,
        )
        config = self._get_config()
        verifier = JwtIdentityVerifier(config)
        token = self._make_jwt({"exp": int(time.time()) - 3600})
        with pytest.raises(IdentityAuthenticationError):
            verifier.verify_compact_token(token)

    def test_wrong_signature_rejected(self):
        from novastack.service.identity import (
            JwtIdentityVerifier, IdentityAuthenticationError,
        )
        config = self._get_config()
        verifier = JwtIdentityVerifier(config)
        token = self._make_jwt(key="wrong-key-wrong-key-wrong-key-12345")
        with pytest.raises(IdentityAuthenticationError):
            verifier.verify_compact_token(token)

    def test_security_pipeline_has_10_steps(self, p5k_release):
        pipeline = p5k_release["security"]["execution_order"]
        assert len(pipeline) == 10


# ---------------------------------------------------------------------------
# Test 13: Resilience — Circuit breaker state machine
# ---------------------------------------------------------------------------
class TestStep13Resilience:
    def test_circuit_breaker_initial_closed(self):
        from novastack.service.resilience import CircuitBreaker, CircuitState
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        assert cb.state == CircuitState.CLOSED

    def test_circuit_breaker_trips_after_threshold(self):
        from novastack.service.resilience import CircuitBreaker, CircuitState
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_concurrency_limiter(self):
        from novastack.service.resilience import InferenceConcurrencyLimiter
        limiter = InferenceConcurrencyLimiter(max_concurrent=1)
        assert limiter is not None


# ---------------------------------------------------------------------------
# Test 16: Rollback — Provider factory round-trip
# ---------------------------------------------------------------------------
class TestStep16Rollback:
    def test_b_to_a_to_b_round_trip(self):
        from novastack.provider import create_default_provider
        b1 = create_default_provider(provider_name="inference_service", lazy_load=True)
        assert type(b1).__name__ == "InferenceServiceAdapter"

        a = create_default_provider(provider_name="local_huggingface", lazy_load=True)
        assert type(a).__name__ == "LocalHuggingFaceProvider"

        b2 = create_default_provider(provider_name="inference_service", lazy_load=True)
        assert type(b2).__name__ == "InferenceServiceAdapter"


# ---------------------------------------------------------------------------
# Test 18: Baseline comparison — cross-manifest consistency
# ---------------------------------------------------------------------------
class TestStep18Baseline:
    def test_package_version_consistent(self, p5k_release, p5k_freeze):
        assert p5k_release["package_version"] == p5k_freeze["package_version"]

    def test_container_image_id_consistent(self, p5k_release, p5k_freeze):
        assert p5k_release["container"]["image_id"] == p5k_freeze["container"]["image_id"]

    def test_document_count_consistent(self, p5k_release, p5k_freeze):
        assert (
            p5k_release["corpus_and_index"]["documents"]
            == p5k_freeze["corpus_and_index"]["documents"]
        )

    def test_chunk_count_consistent(self, p5k_release, p5k_freeze):
        assert (
            p5k_release["corpus_and_index"]["chunks"]
            == p5k_freeze["corpus_and_index"]["chunks"]
        )

    def test_resilience_config_consistent(self, p5k_release, p5k_freeze):
        for key in ["request_timeout_seconds", "queue_timeout_seconds",
                     "max_concurrent_inferences", "circuit_failure_threshold",
                     "circuit_cooldown_seconds"]:
            assert p5k_release["resilience"][key] == p5k_freeze["resilience"][key], (
                f"{key}: {p5k_release['resilience'][key]} != {p5k_freeze['resilience'][key]}"
            )
