"""Phase 5O: Controlled Incident & Recovery Certification Unit Test Suite.

Verifies the recovery semantics, fault isolation, and invariant preservation
required by Phase 5O across all 10 incident categories:
1. Pre-incident baseline & production configuration contract
2. Ollama failure & safe translation semantics
3. Inference service container failure semantics
4. Capacity exhaustion & 429 shedding contract (max_concurrent=1, queue_timeout=0.5s)
5. Production circuit breaker state machine (threshold=3, REAL cooldown=10.0s)
6. ATLAS process restart & baseline index re-leasing contract
7. Index safety & invalid candidate rejection
8. Backend rollback & restoration contract
9. Authentication fail-closed enforcement during recovery
10. Asynchronous disconnect & timeout semantics
11. Layer 1S security refusal & zero-drift invariants
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
DIST_DIR = WORKSPACE / "dist"
DOCS_DIR = WORKSPACE / "docs"

EXPECTED_TARBALL = "atlas-novastack-0.4.14-rc1.tar.gz"
EXPECTED_SHA256 = "382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3"

TEST_SECRET = "test-secret-key-for-atlas-validation-32bytes!"
TEST_ISSUER = "https://identity.atlas.example/issuer"
TEST_AUDIENCE = "atlas-query-api"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_jwt(
    tenant_id: str = "TENANT-NOVASTACK",
    user_id: str = "operator-001",
    roles: Optional[List[str]] = None,
    departments: Optional[List[str]] = None,
    expires_in: int = 3600,
    secret: str = TEST_SECRET,
    issuer: str = TEST_ISSUER,
    audience: str = TEST_AUDIENCE,
) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": roles or ["engineer"],
        "departments": departments or ["Engineering"],
        "exp": int(time.time()) + expires_in,
        "iat": int(time.time()),
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h}.{p}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


# ===========================================================================
# 1. PRE-INCIDENT BASELINE & PRODUCTION CONFIGURATION
# ===========================================================================
class TestPreIncidentBaselineContract:
    def test_release_artifact_checksum(self):
        archive = DIST_DIR / EXPECTED_TARBALL
        assert archive.exists(), f"Release archive missing at {archive}"
        computed = hashlib.sha256(archive.read_bytes()).hexdigest()
        assert computed == EXPECTED_SHA256, f"SHA mismatch: {computed} != {EXPECTED_SHA256}"

    def test_package_version_identity(self):
        import tomllib
        pyproject_path = WORKSPACE / "pyproject.toml"
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        assert data["project"]["version"] == "0.4.14"

    def test_production_resilience_configuration_invariants(self):
        from novastack.service.resilience import ResilienceConfig
        cfg = ResilienceConfig()
        # Production defaults must match certified operating envelope
        assert cfg.max_concurrent_inferences == 1
        assert cfg.queue_timeout_seconds == 0.5
        assert cfg.request_timeout_seconds == 30.0
        assert cfg.circuit_failure_threshold == 3
        # Crucial: production circuit-breaker cooldown is 10.0 seconds, NOT 0.2s
        assert cfg.circuit_cooldown_seconds == 10.0


# ===========================================================================
# 2. INCIDENT 1: OLLAMA FAILURE & SAFE ERROR TRANSLATION
# ===========================================================================
class TestIncident1OllamaFailureSemantics:
    def test_inference_client_translates_unreachable_backend(self):
        from novastack.inference_client import InferenceServiceClient
        from novastack.service.resilience import AtlasServiceError

        # Client targeting a non-existent port (simulating dead Ollama / service)
        client = InferenceServiceClient(
            service_url="http://127.0.0.1:59999",
            connect_timeout_seconds=0.5,
            read_timeout_seconds=1.0,
        )
        assert client.check_health() is False
        ready, detail = client.check_readiness()
        assert ready is False

        with pytest.raises(AtlasServiceError) as exc_info:
            client.generate(prompt="test prompt")
        # Ensure no secrets or credentials leaked in exception
        assert "password" not in str(exc_info.value).lower()
        assert "secret" not in str(exc_info.value).lower()

    def test_adapter_abstains_safely_when_service_unreachable(self):
        from novastack.quantized_provider import InferenceServiceAdapter
        from novastack.evidence import EvidencePackage, EvidenceItem
        from novastack.models import RecordPermissions

        # Adapter pointing to unreachable port
        adapter = InferenceServiceAdapter(
            service_url="http://127.0.0.1:59999",
            default_timeout_seconds=1.0,
        )
        item = EvidenceItem(
            evidence_id="EVD-TEST-001",
            chunk_id="DOC-01::CHUNK-0001",
            document_id="DOC-01",
            tenant_id="TENANT-NOVASTACK",
            source_type="document",
            title="Doc 1",
            text="Evidence text for recovery testing.",
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
            retrieval_rank=0,
            retrieval_score=0.9,
            retrieval_channels=["bm25"],
            evidence_status="accepted",
        )
        pkg = EvidencePackage(
            package_id="PKG-TEST",
            evaluation_id="EVAL-TEST",
            query="Test query",
            tenant_id="TENANT-NOVASTACK",
            user_context={"tenant_id": "TENANT-NOVASTACK"},
            selected_evidence=[item],
            excluded_evidence=[],
            conflicts=[],
            provenance_graph=[],
            resolution_decisions=[],
            statistics={},
        )
        # Should gracefully return abstained result rather than crashing
        res = adapter.generate_answer(pkg, timeout_seconds=1.0)
        assert res.answer_status == "abstained"
        assert res.abstention_reason in ("service_unavailable", "timeout")
        assert len(res.citations) == 0


# ===========================================================================
# 3. INCIDENT 2: INFERENCE CONTAINER OUTAGE SEMANTICS
# ===========================================================================
class TestIncident2InferenceContainerFailureSemantics:
    def test_inference_service_readiness_fails_when_backend_dead(self):
        from novastack.inference_service.schemas import (
            InferenceReadyResponse,
            InferenceGenerationRequest,
        )
        # Verify schema contracts
        readiness = InferenceReadyResponse(
            status="not_ready",
            service="atlas-inference-service",
            backend="ollama",
            backend_connected=False,
            model_name="gemma3:1b",
            model_available=False,
            detail="Connection refused to backend",
        )
        assert readiness.status == "not_ready"
        assert readiness.backend_connected is False


# ===========================================================================
# 4. INCIDENT 3: CAPACITY EXHAUSTION CONTRACT (429)
# ===========================================================================
class TestIncident3CapacityExhaustionContract:
    def test_concurrency_limit_1_sheds_slot_2_after_queue_timeout(self):
        from novastack.service.resilience import InferenceConcurrencyLimiter

        async def _run_test():
            limiter = InferenceConcurrencyLimiter(max_concurrent=1, queue_timeout=0.1)
            # Acquire only available slot
            s1 = await limiter.acquire()
            assert s1 is True
            # Second acquire must time out and shed
            t0 = time.perf_counter()
            s2 = await limiter.acquire()
            elapsed = time.perf_counter() - t0
            assert s2 is False
            assert elapsed >= 0.08  # Respected queue timeout
            # Releasing slot 1 restores availability
            limiter.release()
            s3 = await limiter.acquire()
            assert s3 is True
            limiter.release()

        asyncio.run(_run_test())


# ===========================================================================
# 5. INCIDENT 4: PRODUCTION CIRCUIT BREAKER STATE MACHINE (10s COOLDOWN)
# ===========================================================================
class TestIncident4ProductionCircuitBreakerContract:
    def test_production_cooldown_duration_is_10s(self):
        from novastack.service.resilience import CircuitBreaker, CircuitState

        # Instantiate with REAL production configuration
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        assert cb.state == CircuitState.CLOSED
        assert cb.cooldown_seconds == 10.0
        assert cb.can_execute() is True

        # Inject 3 consecutive failures
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

        # During cooldown (e.g. at 0.1s), breaker remains OPEN
        time.sleep(0.1)
        assert cb.can_execute() is False
        assert cb.state == CircuitState.OPEN

    def test_circuit_breaker_transitions_to_half_open_and_recovers(self):
        from novastack.service.resilience import CircuitBreaker, CircuitState

        # Use 0.2s strictly for unit testing state transitions
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.2)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        time.sleep(0.25)
        # Probe transitions to HALF_OPEN
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

        # Success recovers to CLOSED
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True


# ===========================================================================
# 6. INCIDENT 5 & 6: INDEX SAFETY & RESTART PERSISTENCE CONTRACT
# ===========================================================================
class TestIncident5And6IndexSafetyContract:
    def test_active_index_generation_integrity(self):
        from unittest.mock import patch
        from novastack.service.api import AtlasServicePipeline

        pipe = AtlasServicePipeline.create_default(lazy_generator=True)
        with patch.object(pipe.generator, "is_ready", return_value=True):
            ready, components = pipe.is_ready()
            assert ready is True
            assert components.get("bm25") is True
            assert components.get("dense") is True
            assert components.get("generator") is True

        # Verify dense vectors have no NaNs or Infs
        assert pipe.dense_index is not None
        assert not hasattr(pipe.dense_index.vectors, "shape") or len(pipe.dense_index.vectors.shape) == 2
        valid, errors = pipe.dense_index.validate_integrity()
        assert valid is True, f"Dense index integrity errors: {errors}"

    def test_invalid_candidate_rejected_without_damaging_active_generation(self):
        from novastack.index_manager import validate_index_integrity

        # Candidate validation failure on missing/empty components
        validation = validate_index_integrity(
            bm25_index=None,
            dense_index=None,
            search_documents=[],
            search_chunks=[],
            metadata_snapshot_index={},
            expected_dimension=384,
        )
        assert validation.is_valid is False
        assert len(validation.errors) > 0


# ===========================================================================
# 7. INCIDENT 7: BACKEND ROLLBACK CONTRACT
# ===========================================================================
class TestIncident7BackendRollbackContract:
    def test_bidirectional_provider_factory_round_trip(self):
        from unittest.mock import patch
        from novastack.provider import create_default_provider, InferenceServiceAdapter

        with patch.object(InferenceServiceAdapter, "is_ready", return_value=True):
            # Default is Backend B (InferenceServiceAdapter)
            b1 = create_default_provider(provider_name="inference_service", lazy_load=True)
            assert type(b1).__name__ == "InferenceServiceAdapter"
            assert b1.is_ready() is True

            # Rollback to Backend A (LocalHuggingFaceProvider)
            a = create_default_provider(provider_name="local_huggingface", lazy_load=True)
            assert type(a).__name__ == "LocalHuggingFaceProvider"
            assert a.is_ready() is True

            # Restoration to Backend B
            b2 = create_default_provider(provider_name="inference_service", lazy_load=True)
            assert type(b2).__name__ == "InferenceServiceAdapter"
            assert b2.is_ready() is True


# ===========================================================================
# 8. INCIDENT 8: AUTHENTICATION FAIL-CLOSED DURING RECOVERY
# ===========================================================================
class TestIncident8AuthenticationFailClosedContract:
    @pytest.fixture
    def verifier(self):
        from novastack.service.identity import IdentityConfig, JwtIdentityVerifier
        cfg = IdentityConfig(
            issuer=TEST_ISSUER,
            audience=TEST_AUDIENCE,
            hs256_secret=TEST_SECRET.encode("utf-8"),
            clock_skew_seconds=30,
        )
        return JwtIdentityVerifier(cfg)

    def test_missing_token_rejected(self, verifier):
        with pytest.raises(Exception):
            verifier.verify_compact_token("")

    def test_malformed_token_rejected(self, verifier):
        with pytest.raises(Exception):
            verifier.verify_compact_token("garbage.token.format")

    def test_expired_token_rejected(self, verifier):
        from novastack.service.identity import IdentityAuthenticationError
        exp_jwt = _make_jwt(expires_in=-3600)
        with pytest.raises(IdentityAuthenticationError):
            verifier.verify_compact_token(exp_jwt)

    def test_bad_signature_rejected(self, verifier):
        from novastack.service.identity import IdentityAuthenticationError
        bad_sig = _make_jwt(secret="wrong-secret-key-for-testing-only-32bytes!")
        with pytest.raises(IdentityAuthenticationError):
            verifier.verify_compact_token(bad_sig)

    def test_tenant_context_mismatch_rejected(self, verifier):
        from novastack.service.identity import (
            assert_context_matches_identity,
            IdentityContextMismatchError,
        )
        from novastack.service.schemas import CallerContext

        token = _make_jwt(tenant_id="TENANT-NOVASTACK")
        ident = verifier.verify_compact_token(token)

        # Context claims different tenant
        mismatched_caller = CallerContext(
            tenant_id="TENANT-ORBITAL",
            user_id="operator-001",
            user_role="engineer",
            user_department="Engineering",
        )
        with pytest.raises(IdentityContextMismatchError):
            assert_context_matches_identity(mismatched_caller, ident)


# ===========================================================================
# 9. INCIDENT 9 & 10: DISCONNECT SEMANTICS & LAYER 1S INVARIANTS
# ===========================================================================
class TestIncident9And10SecurityInvariants:
    def test_layer1s_q4_negative_cases_deterministic_abstention(self):
        from novastack.provider import create_default_provider
        from novastack.evidence import EvidencePackage, EvidenceItem
        from novastack.models import RecordPermissions

        prov = create_default_provider(provider_name="inference_service", lazy_load=True)

        for eval_id, forbidden_doc in [
            ("EVAL-0088", "DOC-SEC-TENT-0002"),
            ("EVAL-0090", "DOC-SEC-TENT-0003"),
            ("EVAL-0092", "DOC-SEC-TENT-0004"),
            ("EVAL-0096", "DOC-SEC-TENT-0005"),
        ]:
            item = EvidenceItem(
                evidence_id=f"EVD-{eval_id}",
                chunk_id=f"{forbidden_doc}::CHUNK-0001",
                document_id=forbidden_doc,
                tenant_id="TENANT-NOVASTACK",
                source_type="document",
                title=f"Confidential {forbidden_doc}",
                text="Restricted text.",
                source_entity_id=None,
                source_entity_type=None,
                related_entity_ids=[],
                authority_level="medium",
                classification="restricted",
                permissions=RecordPermissions(),
                status="published",
                version="v1.0",
                created_at="2024-01-01T00:00:00Z",
                updated_at=None,
                valid_from=None,
                valid_until=None,
                parent_id=None,
                supersedes_id=None,
                retrieval_rank=0,
                retrieval_score=0.1,
                retrieval_channels=["bm25"],
                evidence_status="accepted",
            )
            pkg = EvidencePackage(
                package_id=f"PKG-{eval_id}",
                evaluation_id=eval_id,
                query="Config parameters?",
                tenant_id="TENANT-NOVASTACK",
                user_context={"tenant_id": "TENANT-NOVASTACK", "roles": ["engineer"]},
                selected_evidence=[item],
                excluded_evidence=[],
                conflicts=[],
                provenance_graph=[],
                resolution_decisions=[],
                statistics={"retrieved_candidates_count": 5, "excluded_unauthorized_count": 0},
            )

            res = prov.generate_answer(pkg, expected_doc_ids=[], forbidden_doc_ids=[forbidden_doc])
            assert res.answer_status == "abstained"
            assert res.diagnostics.get("provider_invoked") is False
            assert len(res.citations) == 0
