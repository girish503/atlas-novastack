"""
Phase 5M: Release Packaging & Deployment Reproduction — Unit Test Suite
========================================================================

Verifies that the frozen ATLAS Release Candidate (0.4.14-rc1) release artifact:
- Bundles complete source, configuration, and data assets without secrets
- Can be independently hashed and verified via cryptographic checksums
- Enforces non-root container deployment contracts
- Maintains fail-closed authentication and Layer 1S security abstention
- Preserves full C2 citation and query answering behavior
- Correctly documents the 120-case canonical evaluation dataset audit
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import tarfile
import time
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "src"))
sys.path.insert(0, str(WORKSPACE))

# ---------------------------------------------------------------------------
# FIXTURES & HELPERS
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def p5k_release():
    p = WORKSPACE / "artifacts" / "phase_5k_release_manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))

@pytest.fixture(scope="session")
def p5k_sha256():
    p = WORKSPACE / "artifacts" / "phase_5k_sha256_manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _make_jwt(secret: str = "test-secret-key-for-atlas-validation-32bytes!", exp_offset: int = 3600):
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": "https://identity.atlas.example/issuer",
        "aud": "atlas-query-api",
        "sub": "unit-test-user",
        "tenant_id": "TENANT-NOVASTACK",
        "roles": ["engineer"],
        "departments": ["engineering"],
        "exp": int(time.time()) + exp_offset,
        "iat": int(time.time()),
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), f"{h}.{p}".encode("ascii"), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


# ---------------------------------------------------------------------------
# GATE 1: Release Identity
# ---------------------------------------------------------------------------
class TestGate01ReleaseIdentity:
    def test_pyproject_version_matches_manifest(self, p5k_release):
        content = (WORKSPACE / "pyproject.toml").read_text(encoding="utf-8")
        version = None
        for line in content.splitlines():
            if line.strip().startswith("version"):
                version = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
        assert version == p5k_release["package_version"]
        assert version == "0.4.14"

    def test_provider_factory_defaults(self, p5k_release):
        from novastack.provider import create_default_provider
        prov = create_default_provider(provider_name="inference_service", lazy_load=True)
        assert type(prov).__name__ == "InferenceServiceAdapter"

        rollback = create_default_provider(provider_name="local_huggingface", lazy_load=True)
        assert type(rollback).__name__ == "LocalHuggingFaceProvider"

    def test_sha256_manifest_immutability(self, p5k_sha256):
        mismatches = []
        for rel_path, expected_sha in p5k_sha256.items():
            f = WORKSPACE / rel_path
            if not f.exists():
                mismatches.append(f"MISSING: {rel_path}")
                continue
            actual_sha = hashlib.sha256(f.read_bytes()).hexdigest()
            if actual_sha != expected_sha:
                mismatches.append(f"MISMATCH: {rel_path}")
        assert len(mismatches) == 0, f"SHA mismatches found: {mismatches}"


# ---------------------------------------------------------------------------
# GATE 2 & 3: Release Artifact Packaging & Hashing
# ---------------------------------------------------------------------------
class TestGate02And03ReleaseArtifact:
    def test_release_artifact_bundle_structure(self):
        archive_path = WORKSPACE / "dist" / "atlas-novastack-0.4.14-rc1.tar.gz"
        if not archive_path.exists():
            pytest.skip("Release archive not yet built; run packaging script first")

        assert archive_path.stat().st_size > 100_000, "Archive size suspiciously small"
        with tarfile.open(archive_path, "r:gz") as tar:
            names = tar.getnames()
            # Must contain essential components
            assert any("pyproject.toml" in n for n in names)
            assert any("src/novastack" in n for n in names)
            assert any("Dockerfile.inference" in n for n in names)
            assert any("search_documents.json" in n for n in names)
            assert any("evaluation_cases.json" in n for n in names)
            assert any("phase_5k_release_manifest.json" in n for n in names)
            assert any("deploy/env.template" in n for n in names)

            # Must NOT contain forbidden development state
            assert not any(".venv" in n for n in names)
            assert not any("__pycache__" in n for n in names)
            assert not any(".git/" in n for n in names)

    def test_artifact_manifest_validity(self):
        manifest_path = WORKSPACE / "artifacts" / "phase_5m_release_artifact_manifest.json"
        if not manifest_path.exists():
            pytest.skip("Artifact manifest not yet generated")

        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert m["release_candidate"] == "0.4.14-rc1"
        assert m["package_version"] == "0.4.14"
        assert "archive_sha256" in m
        assert m["archive_size_bytes"] > 0
        assert m["included_file_count"] > 10


# ---------------------------------------------------------------------------
# GATE 4 & 5: Clean Deployment & Secret Injection
# ---------------------------------------------------------------------------
class TestGate04And05DeploymentAndSecurityContract:
    def test_dockerfile_inference_hardening(self):
        df = (WORKSPACE / "Dockerfile.inference").read_text(encoding="utf-8")
        assert "USER appuser" in df
        assert "EXPOSE 8001" in df
        assert "FROM python:3.11-slim" in df

    def test_secret_injection_fail_closed(self):
        from novastack.service.identity import IdentityConfig
        unconfigured = IdentityConfig(issuer=None, audience=None, hs256_secret=None)
        assert unconfigured.is_configured is False

        short_secret = IdentityConfig(
            issuer="https://identity.atlas.example/issuer",
            audience="atlas-query-api",
            hs256_secret=b"short-secret!",
            configuration_error="secret_too_short",
        )
        assert short_secret.is_configured is False

        valid_cfg = IdentityConfig(
            issuer="https://identity.atlas.example/issuer",
            audience="atlas-query-api",
            hs256_secret=b"test-secret-key-for-atlas-validation-32bytes!",
            clock_skew_seconds=30,
        )
        assert valid_cfg.is_configured is True


# ---------------------------------------------------------------------------
# GATE 8: End-to-End Query
# ---------------------------------------------------------------------------
class TestGate08EndToEndQuery:
    def test_pipeline_readiness(self):
        from novastack.service.api import AtlasServicePipeline
        pipe = AtlasServicePipeline.create_default()
        all_ready, components = pipe.is_ready()
        assert all_ready is True
        assert components.get("bm25") is True
        assert components.get("dense") is True
        assert components.get("generator") is True

    def test_e2e_query_execution(self):
        from novastack.service.api import AtlasServicePipeline
        from novastack.service.schemas import CallerContext, QueryRequest

        caller = CallerContext(
            tenant_id="TENANT-NOVASTACK",
            user_id="test-user",
            user_role="engineer",
            user_department="Engineering",
        )
        req = QueryRequest(
            query="What was the root cause and resolution of incident INC-NS-0001?",
            user_context=caller,
            evaluation_id="EVAL-0001",
        )

        pipe = AtlasServicePipeline.create_default()
        if hasattr(pipe, "dense_index") and pipe.dense_index and hasattr(pipe.dense_index, "encoder"):
            pipe.dense_index.encoder.get_model()

        resp = pipe.execute_query(req, timeout_seconds=60.0)
        assert resp.answer_status in ("answered", "partially_answered")
        assert len(resp.answer_text) > 0
        assert len(resp.citations) > 0


# ---------------------------------------------------------------------------
# GATE 9: Layer 1S Security Abstention
# ---------------------------------------------------------------------------
class TestGate09Layer1SSecurityAbstention:
    def test_q4_negative_cases_deterministic_abstention(self):
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
                title=f"Title {forbidden_doc}",
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
            assert res.diagnostics.get("gate") == "security_policy_no_expected_docs_with_forbidden"
            assert len(res.citations) == 0


# ---------------------------------------------------------------------------
# GATE 10: Security Smoke Matrix
# ---------------------------------------------------------------------------
class TestGate10SecuritySmoke:
    def test_jwt_verification_and_rejections(self):
        from novastack.service.identity import IdentityConfig, JwtIdentityVerifier, IdentityAuthenticationError
        cfg = IdentityConfig(
            issuer="https://identity.atlas.example/issuer",
            audience="atlas-query-api",
            hs256_secret=b"test-secret-key-for-atlas-validation-32bytes!",
            clock_skew_seconds=30,
        )
        verifier = JwtIdentityVerifier(cfg)

        # Valid token accepts
        valid_token = _make_jwt()
        identity = verifier.verify_compact_token(valid_token)
        assert identity.tenant_id == "TENANT-NOVASTACK"

        # Missing token rejects
        with pytest.raises(Exception):
            verifier.verify_compact_token("")

        # Expired token rejects
        exp_token = _make_jwt(exp_offset=-3600)
        with pytest.raises(IdentityAuthenticationError):
            verifier.verify_compact_token(exp_token)

        # Bad signature rejects
        bad_sig = _make_jwt(secret="wrong-secret-key-for-testing-only-32bytes!")
        with pytest.raises(IdentityAuthenticationError):
            verifier.verify_compact_token(bad_sig)


# ---------------------------------------------------------------------------
# GATE 12: Resilience & Concurrency
# ---------------------------------------------------------------------------
class TestGate12Resilience:
    def test_concurrency_limiter_sheds_capacity(self):
        import asyncio
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

    def test_circuit_breaker_state_transitions(self):
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
# GATE 18: Documentation Discrepancy Audit
# ---------------------------------------------------------------------------
class TestGate18DocumentationDiscrepancy:
    def test_canonical_evaluation_dataset_120_cases(self):
        eval_path = WORKSPACE / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
        data = json.loads(eval_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), "evaluation_cases.json must be a top-level JSON dict"
        assert set(data.keys()) == {"version", "seed", "count", "evaluation_cases"}
        assert len(data) == 4, "Top-level dictionary has 4 metadata keys"
        assert len(data["evaluation_cases"]) == 120, "Canonical evaluation dataset has exactly 120 cases"
        assert data["count"] == 120

        positive = sum(1 for c in data["evaluation_cases"] if c.get("expected_access") == "allow")
        negative = sum(1 for c in data["evaluation_cases"] if c.get("expected_access") != "allow")
        assert positive == 101, f"Expected 101 positive cases, got {positive}"
        assert negative == 19, f"Expected 19 negative cases, got {negative}"
