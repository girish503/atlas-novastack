"""
Unit tests for Phase 5P — Final Production Commissioning & Release Sign-Off.
Validates release identity, source immutability, artifact integrity, model identity,
corpus integrity, evaluation dataset structure, security invariants, operational runbook,
incident recovery certification, regression evidence, configuration lock, and known limitations.
"""

import hashlib
import json
import pathlib
import tomllib
import pytest

WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = WORKSPACE_ROOT / "artifacts"
DIST_DIR = WORKSPACE_ROOT / "dist"
DOCS_DIR = WORKSPACE_ROOT / "docs"

EXPECTED_RC = "0.4.14-rc1"
EXPECTED_VERSION = "0.4.14"
EXPECTED_TARBALL = f"atlas-novastack-{EXPECTED_RC}.tar.gz"
EXPECTED_SHA256 = "382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3"
EXPECTED_MODEL_DIGEST = "8648f39daa8fbf5b18c7b4e6a8fb4990c692751d49917417b8842ca5758e7ffc"


class TestPhase5PReleaseIdentity:
    def test_pyproject_version(self):
        pyproj_path = WORKSPACE_ROOT / "pyproject.toml"
        assert pyproj_path.exists(), "pyproject.toml missing"
        data = tomllib.loads(pyproj_path.read_text(encoding="utf-8"))
        assert data["project"]["version"] == EXPECTED_VERSION

    def test_release_tarball_and_sha256(self):
        tarball_path = DIST_DIR / EXPECTED_TARBALL
        assert tarball_path.exists(), f"Release tarball missing at {tarball_path}"
        actual_sha = hashlib.sha256(tarball_path.read_bytes()).hexdigest()
        assert actual_sha == EXPECTED_SHA256, f"SHA-256 mismatch: {actual_sha} != {EXPECTED_SHA256}"

    def test_production_and_rollback_backend_identities(self):
        from novastack.provider import create_default_provider, AnswerGeneratorProvider, LocalHuggingFaceProvider
        from novastack.quantized_provider import InferenceServiceAdapter

        prod_provider = create_default_provider(provider_name="inference_service", lazy_load=True)
        assert isinstance(prod_provider, InferenceServiceAdapter)
        assert isinstance(prod_provider, AnswerGeneratorProvider)

        rollback_provider = create_default_provider(provider_name="local_huggingface", lazy_load=True)
        assert isinstance(rollback_provider, LocalHuggingFaceProvider)
        assert isinstance(rollback_provider, AnswerGeneratorProvider)


class TestPhase5PSourceImmutability:
    def test_zero_source_drift_in_novastack(self):
        manifest_path = ARTIFACTS_DIR / "phase_5k_sha256_manifest.json"
        assert manifest_path.exists(), "Phase 5K freeze manifest missing"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        from tests.conftest import verify_sha256_platform_independent
        import tarfile

        mismatches = []
        checked_count = 0
        tarball_path = DIST_DIR / EXPECTED_TARBALL
        for rel_path, exp_sha in manifest.items():
            if rel_path.startswith("src/novastack/"):
                checked_count += 1
                fp = WORKSPACE_ROOT / rel_path
                assert fp.exists(), f"Source file missing: {rel_path}"
                if verify_sha256_platform_independent(fp, exp_sha):
                    continue
                # If workspace progressed to 0.5.0, verify 0.4.14 release archive preserves freeze
                matched_in_archive = False
                if tarball_path.exists():
                    try:
                        with tarfile.open(tarball_path, "r:gz") as tar:
                            member = tar.getmember(f"atlas-novastack-{EXPECTED_RC}/{rel_path}")
                            if verify_sha256_platform_independent(tar.extractfile(member).read(), exp_sha):
                                matched_in_archive = True
                    except (KeyError, tarfile.TarError):
                        pass
                if not matched_in_archive:
                    mismatches.append((rel_path, exp_sha, hashlib.sha256(fp.read_bytes()).hexdigest()))

        assert checked_count == 16, f"Expected 16 source files, checked {checked_count}"
        assert len(mismatches) == 0, f"Source drift detected in {len(mismatches)} files: {mismatches}"


class TestPhase5PArtifactIntegrity:
    def test_manifest_alignment(self):
        m_manifest = ARTIFACTS_DIR / "phase_5m_sha256_manifest.json"
        assert m_manifest.exists()
        m_data = json.loads(m_manifest.read_text(encoding="utf-8"))
        assert m_data[EXPECTED_TARBALL] == EXPECTED_SHA256

        tarball_path = DIST_DIR / EXPECTED_TARBALL
        assert tarball_path.stat().st_size == 3475452, f"Tarball size mismatch: {tarball_path.stat().st_size}"


class TestPhase5PCorpusAndEvaluationIntegrity:
    def test_search_documents_count(self):
        docs_path = WORKSPACE_ROOT / "data" / "processed" / "novastack" / "search_documents.json"
        assert docs_path.exists()
        d = json.loads(docs_path.read_text(encoding="utf-8"))
        assert d.get("count") == 1393
        assert len(d.get("search_documents", [])) == 1393

    def test_search_chunks_count(self):
        chunks_path = WORKSPACE_ROOT / "data" / "processed" / "novastack" / "search_chunks.json"
        assert chunks_path.exists()
        d = json.loads(chunks_path.read_text(encoding="utf-8"))
        assert d.get("count") == 1663
        assert len(d.get("search_chunks", [])) == 1663

    def test_evaluation_cases_distribution(self):
        eval_path = WORKSPACE_ROOT / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
        assert eval_path.exists()
        d = json.loads(eval_path.read_text(encoding="utf-8"))
        cases = d.get("evaluation_cases", [])
        assert len(cases) == 120, f"Expected 120 canonical cases, got {len(cases)}"

        allow_count = sum(1 for c in cases if c.get("expected_access") == "allow")
        deny_count = sum(1 for c in cases if c.get("expected_access") == "deny")
        abstain_count = sum(1 for c in cases if c.get("expected_access") == "abstain")

        assert allow_count == 101, f"Expected 101 positive cases, got {allow_count}"
        assert deny_count == 12, f"Expected 12 deny cases, got {deny_count}"
        assert abstain_count == 7, f"Expected 7 abstain cases, got {abstain_count}"
        assert deny_count + abstain_count == 19, "Expected 19 total negative cases"


class TestPhase5PSecurityAndNegativeCases:
    def test_canonical_q4_negative_cases(self):
        from novastack.provider import create_default_provider
        from novastack.evidence import EvidencePackage, EvidenceItem
        from novastack.models import RecordPermissions

        prov = create_default_provider(provider_name="inference_service", lazy_load=True)

        for eval_id, forbidden_doc in [
            ("EVAL-0088", "DOC-SEC-TENT-0002"),
            ("EVAL-0090", "DOC-SEC-TENT-0002"),
            ("EVAL-0092", "DOC-SEC-TENT-0004"),
            ("EVAL-0096", "DOC-SEC-TENT-0004"),
        ]:
            item = EvidenceItem(
                evidence_id=f"EVD-{eval_id}-001",
                chunk_id=f"CHK-{eval_id}",
                document_id="DOC-PUBLIC-001",
                tenant_id="TENANT-NOVASTACK",
                source_type="document",
                title=f"Public Doc for {eval_id}",
                text="Public document content.",
                source_entity_id=None,
                source_entity_type=None,
                related_entity_ids=[],
                authority_level="medium",
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
                retrieval_rank=0,
                retrieval_score=0.95,
                retrieval_channels=["bm25"],
            )
            pkg = EvidencePackage(
                package_id=f"PKG-{eval_id}",
                evaluation_id=eval_id,
                query="Unauthorized cross-tenant query",
                tenant_id="TENANT-NOVASTACK",
                user_context={"tenant_id": "TENANT-NOVASTACK", "roles": ["engineer"]},
                selected_evidence=[item],
                excluded_evidence=[],
                conflicts=[],
                provenance_graph=[],
                resolution_decisions=[],
                statistics={"retrieved_candidates_count": 1, "excluded_unauthorized_count": 0},
            )
            ans = prov.generate_answer(
                package=pkg,
                expected_doc_ids=[],
                forbidden_doc_ids=[forbidden_doc],
            )
            assert ans.answer_status == "abstained"
            assert ans.diagnostics.get("provider_invoked") is False
            assert len(ans.citations) == 0


class TestPhase5POperationsRunbookAndIncidentRecovery:
    def test_runbook_sections_and_circuit_breaker(self):
        rb_path = DOCS_DIR / "OPERATIONS_RUNBOOK.md"
        assert rb_path.exists(), "OPERATIONS_RUNBOOK.md missing"
        text = rb_path.read_text(encoding="utf-8")

        for sec_num in range(1, 23):
            assert f"## {sec_num}." in text or f"## {sec_num} " in text or f"Section {sec_num}" in text

        assert "10.0" in text or "10 seconds" in text or "10s" in text
        assert "circuit" in text.lower()

    def test_phase_5o_incident_recovery_certification_evidence(self):
        cert_path = ARTIFACTS_DIR / "phase_5o_incident_recovery_certification.json"
        assert cert_path.exists(), "Phase 5O certification artifact missing"
        cert = json.loads(cert_path.read_text(encoding="utf-8"))
        assert cert.get("final_decision") == "PASS"
        assert cert.get("release_candidate") == EXPECTED_RC
        assert cert.get("package_version") == EXPECTED_VERSION

        incidents = cert.get("incidents", {})
        assert len(incidents) >= 11
        for inc_id, inc_data in incidents.items():
            assert inc_data.get("status") == "PASS", f"Incident {inc_id} did not pass: {inc_data}"

    def test_phase_5o_regression_evidence(self):
        cert_path = ARTIFACTS_DIR / "phase_5o_incident_recovery_certification.json"
        assert cert_path.exists()
        cert = json.loads(cert_path.read_text(encoding="utf-8"))
        reg = cert.get("regression", {})
        assert reg.get("passed") == 225
        assert reg.get("failed") == 0


class TestPhase5PConfigurationLock:
    def test_production_resilience_configuration(self):
        cert_path = ARTIFACTS_DIR / "phase_5o_incident_recovery_certification.json"
        cert = json.loads(cert_path.read_text(encoding="utf-8"))
        res = cert.get("resilience_config", {})
        assert res.get("max_concurrent_inferences") == 1
        assert res.get("queue_timeout_seconds") == 0.5
        assert res.get("request_timeout_seconds") == 30.0
        assert res.get("circuit_failure_threshold") == 3
        assert res.get("circuit_cooldown_seconds") == 10.0
