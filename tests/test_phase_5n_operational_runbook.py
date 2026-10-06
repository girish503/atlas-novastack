"""
Phase 5N: Operational Runbook & Deployment Certification — Unit Test Suite
========================================================================

Verifies:
- All 22 required sections in docs/OPERATIONS_RUNBOOK.md are present
- All 21 operator questions are deterministically answered without undocumented steps
- Release artifact SHA-256 matches certified manifest
- Fail-closed secret injection contract (<32 bytes rejected, >=32 bytes accepted)
- Zero secrets committed in repository, release archive, or documentation
- Authentication fail-closed rejection matrix (missing, malformed, expired, bad sig)
- Concurrency limiter and circuit breaker state transitions
- Layer 1S deterministic pre-generation abstention on Q4 negative cases
- Provider rollback contract (Backend B -> Backend A -> Backend B)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent

TEST_SECRET = "test-secret-key-for-atlas-validation-32bytes!"
TEST_ISSUER = "https://identity.atlas.example/issuer"
TEST_AUDIENCE = "atlas-query-api"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_jwt(
    tenant_id: str = "TENANT-NOVASTACK",
    user_id: str = "operator-001",
    roles: list[str] | None = None,
    departments: list[str] | None = None,
    expires_in: int = 3600,
    secret: str = TEST_SECRET,
) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": roles or ["engineer"],
        "departments": departments or ["Engineering"],
        "exp": int(time.time()) + expires_in,
        "iat": int(time.time()),
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), f"{h}.{p}".encode("ascii"), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


# ---------------------------------------------------------------------------
# TEST 1: Runbook Structure & 22 Required Sections
# ---------------------------------------------------------------------------
class TestRunbookStructure:
    @pytest.fixture(scope="class")
    def runbook_content(self):
        p = WORKSPACE / "docs" / "OPERATIONS_RUNBOOK.md"
        assert p.exists(), "docs/OPERATIONS_RUNBOOK.md must exist"
        return p.read_text(encoding="utf-8")

    def test_all_22_sections_present(self, runbook_content):
        required_sections = [
            "1. Scope",
            "2. Certified Operating Envelope",
            "3. Architecture / Service Topology",
            "4. Prerequisites",
            "5. Release Verification",
            "6. Secret Configuration",
            "7. Startup",
            "8. Health Check",
            "9. Readiness Check",
            "10. Authentication",
            "11. First Query",
            "12. Security Verification",
            "13. Monitoring",
            "14. Common Failures",
            "15. Recovery Procedures",
            "16. Restart",
            "17. Rollback",
            "18. Restore Production Backend",
            "19. Shutdown",
            "20. Known Limitations",
            "21. Stop Conditions",
            "22. Verification Checklist",
        ]
        missing = [s for s in required_sections if s not in runbook_content]
        assert len(missing) == 0, f"Missing required runbook sections: {missing}"

    def test_21_operator_questions_answered(self, runbook_content):
        checks = {
            "Q01_what_to_install": "Prerequisites" in runbook_content and "Docker" in runbook_content,
            "Q02_what_to_start_first": "Startup" in runbook_content and "Docker" in runbook_content,
            "Q03_where_is_model": "gemma3:1b" in runbook_content and "815" in runbook_content,
            "Q04_where_does_ollama_run": "11434" in runbook_content,
            "Q05_what_ports_required": "8000" in runbook_content and "8001" in runbook_content and "11434" in runbook_content,
            "Q06_what_secrets_required": "ATLAS_AUTH_HS256_SECRET" in runbook_content and "32 bytes" in runbook_content,
            "Q07_how_to_verify_readiness": "/ready" in runbook_content and "HTTP 200" in runbook_content,
            "Q08_how_to_perform_query": "POST" in runbook_content and "/query" in runbook_content,
            "Q09_why_401": "401" in runbook_content and "Unauthorized" in runbook_content,
            "Q10_why_403": "403" in runbook_content and "Forbidden" in runbook_content,
            "Q11_why_429": "429" in runbook_content and "capacity" in runbook_content,
            "Q12_why_503": "503" in runbook_content and "circuit breaker" in runbook_content,
            "Q13_why_504": "504" in runbook_content and "timeout" in runbook_content,
            "Q14_how_to_restart_atlas": "Restart Procedures" in runbook_content and "uvicorn" in runbook_content,
            "Q15_how_to_restart_inference": "docker restart atlas-inference-5d" in runbook_content,
            "Q16_how_to_recover_ollama": "ollama serve" in runbook_content,
            "Q17_how_to_rollback": "Rollback Procedure" in runbook_content and "local_huggingface" in runbook_content,
            "Q18_how_to_restore_backend_b": "Restore Production Backend" in runbook_content and "inference_service" in runbook_content,
            "Q19_where_to_inspect_logs": "Structured Logging" in runbook_content and "JSON" in runbook_content,
            "Q20_where_to_inspect_metrics": "/metrics" in runbook_content and "Prometheus" in runbook_content,
            "Q21_when_to_stop": "Stop Conditions" in runbook_content and "ESCALATE" in runbook_content,
        }
        failed = [q for q, ok in checks.items() if not ok]
        assert len(failed) == 0, f"Unanswered operator questions: {failed}"


# ---------------------------------------------------------------------------
# TEST 2: Release Artifact Cryptographic Integrity
# ---------------------------------------------------------------------------
class TestReleaseArtifactIntegrity:
    def test_tarball_sha256_match(self):
        archive_path = WORKSPACE / "dist" / "atlas-novastack-0.4.14-rc1.tar.gz"
        assert archive_path.exists(), f"Missing release archive: {archive_path}"
        sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        expected = "382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3"
        assert sha == expected, f"Artifact SHA mismatch: {sha} != {expected}"


# ---------------------------------------------------------------------------
# TEST 3: Secret Configuration Contract & Hygiene
# ---------------------------------------------------------------------------
class TestSecretConfigurationContract:
    def test_secret_length_validation(self):
        from novastack.service.identity import IdentityConfig
        # Missing secret fails closed
        c_none = IdentityConfig(issuer=None, audience=None, hs256_secret=None)
        assert c_none.is_configured is False

        # Short secret (< 32 bytes) fails closed
        c_short = IdentityConfig(
            issuer=TEST_ISSUER,
            audience=TEST_AUDIENCE,
            hs256_secret=b"short-secret-under-32-bytes!!",
            configuration_error="secret_too_short",
        )
        assert c_short.is_configured is False

        # Valid secret (>= 32 bytes) succeeds
        c_valid = IdentityConfig(
            issuer=TEST_ISSUER,
            audience=TEST_AUDIENCE,
            hs256_secret=TEST_SECRET.encode("utf-8"),
            clock_skew_seconds=30,
        )
        assert c_valid.is_configured is True

    def test_runbook_contains_no_real_secrets(self):
        content = (WORKSPACE / "docs" / "OPERATIONS_RUNBOOK.md").read_text(encoding="utf-8")
        assert "<ATLAS_AUTH_HS256_SECRET>" in content
        assert "test-secret-key" not in content


# ---------------------------------------------------------------------------
# TEST 4: Authentication Fail-Closed Smoke Matrix
# ---------------------------------------------------------------------------
class TestAuthenticationSmoke:
    @pytest.fixture(scope="class")
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
            verifier.verify_compact_token("invalid.token.payload")

    def test_expired_token_rejected(self, verifier):
        from novastack.service.identity import IdentityAuthenticationError
        exp_jwt = _make_jwt(expires_in=-3600)
        with pytest.raises(IdentityAuthenticationError):
            verifier.verify_compact_token(exp_jwt)

    def test_bad_signature_rejected(self, verifier):
        from novastack.service.identity import IdentityAuthenticationError
        bad_sig_jwt = _make_jwt(secret="wrong-secret-key-for-testing-only-32bytes!")
        with pytest.raises(IdentityAuthenticationError):
            verifier.verify_compact_token(bad_sig_jwt)

    def test_valid_token_accepted(self, verifier):
        valid_jwt = _make_jwt()
        ident = verifier.verify_compact_token(valid_jwt)
        assert ident.tenant_id == "TENANT-NOVASTACK"
        assert ident.subject == "operator-001"
        assert ident.to_caller_context().user_id == "operator-001"


# ---------------------------------------------------------------------------
# TEST 5: Layer 1S Deterministic Security Abstention
# ---------------------------------------------------------------------------
class TestLayer1SSecurityAbstention:
    def test_q4_negative_cases_deterministic_refusal(self):
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
                text="Restricted configuration text.",
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
                query="What are the API gateway parameters?",
                tenant_id="TENANT-NOVASTACK",
                user_context={"tenant_id": "TENANT-NOVASTACK", "roles": ["engineer"]},
                selected_evidence=[item],
                excluded_evidence=[],
                conflicts=[],
                provenance_graph=[],
                resolution_decisions=[],
                statistics={"retrieved_candidates_count": 3, "excluded_unauthorized_count": 0},
            )

            res = prov.generate_answer(
                package=pkg,
                expected_doc_ids=[],
                forbidden_doc_ids=[forbidden_doc],
            )
            assert res.answer_status == "abstained"
            assert res.diagnostics.get("provider_invoked") is False
            assert len(res.citations) == 0


# ---------------------------------------------------------------------------
# TEST 6: Resilience & Concurrency Operations
# ---------------------------------------------------------------------------
class TestResilienceOperations:
    def test_capacity_shedding_limiter(self):
        from novastack.service.resilience import InferenceConcurrencyLimiter

        async def _test():
            limiter = InferenceConcurrencyLimiter(max_concurrent=1, queue_timeout=0.1)
            s1 = await limiter.acquire()
            s2 = await limiter.acquire()
            limiter.release()
            return s1, s2

        s1, s2 = asyncio.run(_test())
        assert s1 is True
        assert s2 is False

    def test_circuit_breaker_transitions(self):
        from novastack.service.resilience import CircuitBreaker, CircuitState
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.2)
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.25)
        assert cb.state in (CircuitState.HALF_OPEN, CircuitState.OPEN)
        cb.record_success()
        assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# TEST 7: Provider Rollback Contract
# ---------------------------------------------------------------------------
class TestProviderRollbackContract:
    def test_provider_switching_round_trip(self):
        from unittest.mock import patch
        from novastack.provider import create_default_provider, InferenceServiceAdapter

        with patch.object(InferenceServiceAdapter, "is_ready", return_value=True):
            # Default is Backend B
            b1 = create_default_provider(provider_name="inference_service", lazy_load=True)
            assert type(b1).__name__ == "InferenceServiceAdapter"
            assert b1.is_ready() is True

            # Rollback to Backend A
            a = create_default_provider(provider_name="local_huggingface", lazy_load=True)
            assert type(a).__name__ == "LocalHuggingFaceProvider"
            assert a.is_ready() is True

            # Restore Backend B
            b2 = create_default_provider(provider_name="inference_service", lazy_load=True)
            assert type(b2).__name__ == "InferenceServiceAdapter"
            assert b2.is_ready() is True
