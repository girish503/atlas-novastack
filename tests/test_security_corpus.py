"""Unit and integration tests for Phase 1C Milestone 4D-1: Security & Authorization Test Corpus.

Verifies:
- Security record scale (80–120 records) and unique IDs
- Valid tenant, role, department, and classification metadata
- Valid permission structures and allowed_user_ids
- Correctness of security ground-truth fixtures (expected_access allow/deny)
- Cross-tenant isolation boundaries
- Role-based and department-based access constraints
- Document-level permission overrides
- Version-specific permission escalations
- Duplicate document ACL differentiations
- Superseded restricted document access controls
- Determinism and ground-truth immutability
- Zero retrieval engine assumptions
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure src/ is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novastack.config import RANDOM_SEED
from novastack.corpus_generator import generate_combined_corpus
from novastack.generator import NovaStackGenerator
from novastack.models import (
    CLASSIFICATION_LEVELS,
    RECORD_STATUSES,
    SECURITY_ROLES,
    SECURITY_SCENARIOS,
    SOURCE_TYPES,
    SecurityFixture,
    SourceRecord,
)
from novastack.rendering import validate_source_records
from novastack.security_corpus import SecurityCorpusGenerator


@pytest.fixture(scope="module")
def generator() -> NovaStackGenerator:
    """Instantiate and run generator for testing."""
    gen = NovaStackGenerator(seed=RANDOM_SEED)
    gen.generate()
    return gen


@pytest.fixture(scope="module")
def security_generator(generator: NovaStackGenerator) -> SecurityCorpusGenerator:
    """Instantiate SecurityCorpusGenerator."""
    return SecurityCorpusGenerator(generator, seed=RANDOM_SEED)


@pytest.fixture(scope="module")
def security_records(security_generator: SecurityCorpusGenerator) -> list[SourceRecord]:
    """Generate security records."""
    return security_generator.generate_corpus()


@pytest.fixture(scope="module")
def security_fixtures(security_generator: SecurityCorpusGenerator) -> list[SecurityFixture]:
    """Generate security fixtures."""
    return security_generator.generate_fixtures()


# ---------------------------------------------------------------------------
# 1. Scale and Uniqueness Tests
# ---------------------------------------------------------------------------


class TestMilestone4DSecurityRecordScaleAndUniqueness:
    def test_security_record_scale(self, security_records: list[SourceRecord]) -> None:
        count = len(security_records)
        assert 80 <= count <= 120, (
            f"Expected security corpus size between 80 and 120 records, got {count}"
        )

    def test_all_security_document_ids_unique(
        self, security_records: list[SourceRecord]
    ) -> None:
        doc_ids = [r.document_id for r in security_records]
        assert len(doc_ids) == len(set(doc_ids)), "Duplicate document_id found in security records"

    def test_security_document_id_formatting(
        self, security_records: list[SourceRecord]
    ) -> None:
        for r in security_records:
            assert r.document_id.startswith("DOC-SEC-"), (
                f"Document ID {r.document_id} should start with 'DOC-SEC-'"
            )


# ---------------------------------------------------------------------------
# 2. Metadata, Taxonomies, and Permissions
# ---------------------------------------------------------------------------


class TestMilestone4DSecurityTaxonomiesAndPermissions:
    def test_valid_tenants(self, security_records: list[SourceRecord]) -> None:
        valid_tenants = {"TENANT-NOVASTACK", "TENANT-ORBITAL", "TENANT-PINECONE"}
        for r in security_records:
            assert r.tenant_id in valid_tenants, f"Invalid tenant {r.tenant_id} on {r.document_id}"

    def test_valid_source_types(self, security_records: list[SourceRecord]) -> None:
        for r in security_records:
            assert r.source_type in SOURCE_TYPES, f"Invalid source_type {r.source_type}"

    def test_valid_classifications(self, security_records: list[SourceRecord]) -> None:
        for r in security_records:
            assert r.classification in CLASSIFICATION_LEVELS, (
                f"Invalid classification {r.classification}"
            )

    def test_all_four_classifications_represented(
        self, security_records: list[SourceRecord]
    ) -> None:
        classes = {r.classification for r in security_records}
        assert classes == {"public", "internal", "confidential", "restricted"}

    def test_valid_record_statuses(self, security_records: list[SourceRecord]) -> None:
        for r in security_records:
            assert r.status in RECORD_STATUSES, f"Invalid status {r.status}"

    def test_valid_permissions_structure(
        self, security_records: list[SourceRecord]
    ) -> None:
        for r in security_records:
            assert hasattr(r.permissions, "allowed_roles")
            assert hasattr(r.permissions, "allowed_departments")
            assert hasattr(r.permissions, "allowed_teams")
            assert hasattr(r.permissions, "allowed_user_ids")
            assert isinstance(r.permissions.allowed_roles, list)
            assert isinstance(r.permissions.allowed_departments, list)
            assert isinstance(r.permissions.allowed_teams, list)
            assert isinstance(r.permissions.allowed_user_ids, list)

    def test_security_corpus_passes_validator(
        self, security_records: list[SourceRecord], generator: NovaStackGenerator
    ) -> None:
        all_entity_ids = set()
        all_entity_ids.update(u.user_id for u in generator.users)
        all_entity_ids.update(t.team_id for t in generator.teams)
        all_entity_ids.update(s.service_id for s in generator.services)
        all_entity_ids.update(c.customer_id for c in generator.customers)
        all_entity_ids.update(e.event_id for e in generator.events)
        all_entity_ids.update(i.incident_id for i in generator.incidents)
        all_entity_ids.update(d.deployment_id for d in generator.deployments)
        all_entity_ids.update(p.pull_request_id for p in generator.pull_requests)

        valid_tenants = {"TENANT-NOVASTACK", "TENANT-ORBITAL", "TENANT-PINECONE"}
        errors = validate_source_records(security_records, all_entity_ids, valid_tenants)
        assert errors == [], f"Validation errors in security corpus: {errors}"


# ---------------------------------------------------------------------------
# 3. Security Ground-Truth Fixtures Correctness
# ---------------------------------------------------------------------------


class TestMilestone4DSecurityFixtures:
    def test_fixture_count_and_uniqueness(
        self, security_fixtures: list[SecurityFixture]
    ) -> None:
        assert len(security_fixtures) >= 12, "Must generate at least 12 explicit fixtures"
        fixture_ids = [f.fixture_id for f in security_fixtures]
        assert len(fixture_ids) == len(set(fixture_ids)), "Duplicate fixture_id found"

    def test_fixture_scenarios_valid(
        self, security_fixtures: list[SecurityFixture]
    ) -> None:
        for f in security_fixtures:
            assert f.security_scenario in SECURITY_SCENARIOS, (
                f"Invalid scenario {f.security_scenario} on {f.fixture_id}"
            )
            assert f.expected_access in {"allow", "deny"}
            assert f.target_document_id.startswith("DOC-SEC-")

    def test_fixture_expected_access_balance(
        self, security_fixtures: list[SecurityFixture]
    ) -> None:
        allow_count = sum(1 for f in security_fixtures if f.expected_access == "allow")
        deny_count = sum(1 for f in security_fixtures if f.expected_access == "deny")
        assert allow_count > 0, "Fixtures must include ALLOW cases"
        assert deny_count > 0, "Fixtures must include DENY cases"


# ---------------------------------------------------------------------------
# 4. Scenario-Specific Security Boundaries
# ---------------------------------------------------------------------------


class TestMilestone4DScenarioBoundaries:
    def test_cross_tenant_isolation_fixtures(
        self, security_fixtures: list[SecurityFixture]
    ) -> None:
        tenant_fixtures = [f for f in security_fixtures if f.security_scenario == "cross_tenant"]
        assert len(tenant_fixtures) >= 3

        # Cross-tenant denials
        cross_denials = [f for f in tenant_fixtures if f.test_user_tenant != f.expected_tenant]
        for f in cross_denials:
            assert f.expected_access == "deny", (
                f"User from {f.test_user_tenant} must be DENIED access to {f.expected_tenant} document"
            )

        # Same-tenant allowances
        same_tenant_allows = [f for f in tenant_fixtures if f.test_user_tenant == f.expected_tenant]
        for f in same_tenant_allows:
            assert f.expected_access == "allow", (
                f"User from {f.test_user_tenant} should be ALLOWED access to own document"
            )

    def test_role_based_access_fixtures(
        self, security_fixtures: list[SecurityFixture]
    ) -> None:
        role_fixtures = [f for f in security_fixtures if f.security_scenario == "role_based"]
        assert len(role_fixtures) >= 2

        for f in role_fixtures:
            if f.required_roles and f.test_user_role in f.required_roles:
                assert f.expected_access == "allow"
            elif f.required_roles and f.test_user_role not in f.required_roles:
                assert f.expected_access == "deny"

    def test_department_restriction_fixtures(
        self, security_fixtures: list[SecurityFixture]
    ) -> None:
        dept_fixtures = [f for f in security_fixtures if f.security_scenario == "department"]
        assert len(dept_fixtures) >= 4

        for f in dept_fixtures:
            if f.required_department:
                if f.test_user_department == f.required_department:
                    assert f.expected_access == "allow"
                else:
                    assert f.expected_access == "deny"

    def test_document_level_user_specific_fixtures(
        self, security_fixtures: list[SecurityFixture]
    ) -> None:
        usr_fixtures = [
            f for f in security_fixtures
            if f.fixture_id.startswith("FIX-SEC-0011")
        ]
        assert len(usr_fixtures) == 2

        allow_fix = next(f for f in usr_fixtures if f.expected_access == "allow")
        deny_fix = next(f for f in usr_fixtures if f.expected_access == "deny")

        assert allow_fix.test_user_id in allow_fix.allowed_user_ids
        assert deny_fix.test_user_id not in deny_fix.allowed_user_ids

    def test_version_specific_permission_escalation(
        self,
        security_records: list[SourceRecord],
        security_fixtures: list[SecurityFixture],
    ) -> None:
        rec_map = {r.document_id: r for r in security_records}
        v1 = rec_map["DOC-SEC-VACL-01-V1"]
        v2 = rec_map["DOC-SEC-VACL-01-V2"]
        v3 = rec_map["DOC-SEC-VACL-01-V3"]

        assert v1.classification == "internal"
        assert v2.classification == "confidential"
        assert v3.classification == "restricted"
        assert v1.status == "superseded"
        assert v3.status == "published"

        v_fixtures = [f for f in security_fixtures if f.security_scenario == "version_specific"]
        assert len(v_fixtures) >= 2
        v1_allow = next(f for f in v_fixtures if f.target_document_id == "DOC-SEC-VACL-01-V1")
        v3_deny = next(f for f in v_fixtures if f.target_document_id == "DOC-SEC-VACL-01-V3")
        assert v1_allow.expected_access == "allow"
        assert v3_deny.expected_access == "deny"

    def test_duplicate_acl_differentiation(
        self,
        security_records: list[SourceRecord],
        security_fixtures: list[SecurityFixture],
    ) -> None:
        rec_map = {r.document_id: r for r in security_records}
        doc_a = rec_map["DOC-SEC-DUP-0001"]
        doc_b = rec_map["DOC-SEC-DUP-0002"]

        assert doc_a.classification == "internal"
        assert doc_b.classification == "restricted"
        assert doc_b.parent_id == doc_a.document_id

        dup_fixtures = [f for f in security_fixtures if f.security_scenario == "duplicate_acl"]
        assert len(dup_fixtures) >= 2
        allow_fix = next(f for f in dup_fixtures if f.target_document_id == doc_a.document_id)
        deny_fix = next(f for f in dup_fixtures if f.target_document_id == doc_b.document_id)
        assert allow_fix.expected_access == "allow"
        assert deny_fix.expected_access == "deny"

    def test_superseded_restricted_documents_remain_protected(
        self,
        security_records: list[SourceRecord],
        security_fixtures: list[SecurityFixture],
    ) -> None:
        rec_map = {r.document_id: r for r in security_records}
        sup_doc = rec_map["DOC-SEC-SUP-0001"]

        assert sup_doc.status == "superseded"
        assert sup_doc.classification == "restricted"
        assert "security_admin" in sup_doc.permissions.allowed_roles

        sup_fixtures = [f for f in security_fixtures if f.security_scenario == "superseded_restricted"]
        assert len(sup_fixtures) >= 2

        deny_fix = next(f for f in sup_fixtures if f.expected_access == "deny")
        allow_fix = next(f for f in sup_fixtures if f.expected_access == "allow")

        assert deny_fix.test_user_role != "security_admin"
        assert allow_fix.test_user_role == "security_admin"


# ---------------------------------------------------------------------------
# 5. Determinism and Immutability Tests
# ---------------------------------------------------------------------------


class TestMilestone4DDeterminismAndImmutability:
    def test_deterministic_generation(self, generator: NovaStackGenerator) -> None:
        gen1 = SecurityCorpusGenerator(generator, seed=RANDOM_SEED)
        gen2 = SecurityCorpusGenerator(generator, seed=RANDOM_SEED)

        recs1 = gen1.generate_corpus()
        recs2 = gen2.generate_corpus()

        assert len(recs1) == len(recs2)
        for r1, r2 in zip(recs1, recs2):
            assert r1.document_id == r2.document_id
            assert r1.tenant_id == r2.tenant_id
            assert r1.title == r2.title
            assert r1.classification == r2.classification
            assert r1.permissions == r2.permissions

        fix1 = gen1.generate_fixtures()
        fix2 = gen2.generate_fixtures()
        assert len(fix1) == len(fix2)
        for f1, f2 in zip(fix1, fix2):
            assert f1.fixture_id == f2.fixture_id
            assert f1.expected_access == f2.expected_access
            assert f1.test_user_id == f2.test_user_id

    def test_ground_truth_unmutated(self, generator: NovaStackGenerator) -> None:
        events_before = len(generator.events)
        incidents_before = len(generator.incidents)
        deployments_before = len(generator.deployments)
        prs_before = len(generator.pull_requests)
        rel_before = len(generator.event_relationships)

        sec_gen = SecurityCorpusGenerator(generator, seed=RANDOM_SEED)
        sec_gen.generate_corpus()

        assert len(generator.events) == events_before == 12
        assert len(generator.incidents) == incidents_before == 12
        assert len(generator.deployments) == deployments_before == 9
        assert len(generator.pull_requests) == prs_before == 11
        assert len(generator.event_relationships) == rel_before == 92

    def test_combined_corpus_with_security_enabled(
        self, generator: NovaStackGenerator
    ) -> None:
        full_corpus = generate_combined_corpus(
            generator, seed=RANDOM_SEED, include_noise=True, include_security=True
        )
        assert len(full_corpus) == 1303
