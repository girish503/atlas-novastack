"""Tests for Observational Source Record Foundation — Milestone 3.

Covers:
    1. Source-record model validity
    2. Required fields
    3. Optional temporal fields
    4. Valid source types (controlled vocabulary)
    5. Valid authority levels & defaults
    6. Provenance linkage to existing ground-truth entities
    7. Tenant consistency
    8. Permission metadata structure
    9. Deterministic rendering
    10. Different source types render distinct content from identical ground truth
    11. Full coverage of all 10 registered renderers
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Ensure src/ is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novastack.config import RANDOM_SEED
from novastack.generator import NovaStackGenerator
from novastack.models import (
    AUTHORITY_LEVELS,
    CLASSIFICATION_LEVELS,
    DEFAULT_AUTHORITY_BY_SOURCE_TYPE,
    RECORD_STATUSES,
    SOURCE_TYPES,
    RecordPermissions,
    SourceRecord,
)
from novastack.background_corpus import BackgroundCorpusGenerator
from novastack.corpus_generator import EventSourceCorpusGenerator, generate_combined_corpus
from novastack.noise_generator import TemporalNoiseGenerator
from novastack.rendering import (
    RENDERERS,
    RenderContext,
    render_demonstration_fixture,
    render_source_record,
    validate_source_records,
)


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture(scope="module")
def generator() -> NovaStackGenerator:
    """Generate organizational and ground-truth entities."""
    gen = NovaStackGenerator(seed=RANDOM_SEED)
    gen.generate()
    return gen


@pytest.fixture(scope="module")
def demo_records(generator: NovaStackGenerator) -> list[SourceRecord]:
    """Generate demonstration source records from Event 1."""
    return render_demonstration_fixture(generator)


@pytest.fixture(scope="module")
def valid_entity_ids(generator: NovaStackGenerator) -> set[str]:
    """Collect all valid entity IDs in the enterprise."""
    ids: set[str] = set()
    ids.update(u.user_id for u in generator.users)
    ids.update(t.team_id for t in generator.teams)
    ids.update(s.service_id for s in generator.services)
    ids.update(c.customer_id for c in generator.customers)
    ids.update(e.event_id for e in generator.events)
    ids.update(i.incident_id for i in generator.incidents)
    ids.update(d.deployment_id for d in generator.deployments)
    ids.update(p.pull_request_id for p in generator.pull_requests)
    return ids


# ---------------------------------------------------------------
# 1. Source-Record Model Validity
# ---------------------------------------------------------------

class TestSourceRecordModelValidity:
    def test_basic_instantiation(self) -> None:
        rec = SourceRecord(
            document_id="DOC-TEST-001",
            tenant_id="TENANT-NOVASTACK",
            source_type="engineering_note",
            title="Test Note",
            content="Testing source record creation.",
            author_id="USR-NS-0001",
            department="Engineering",
        )
        assert rec.document_id == "DOC-TEST-001"
        assert rec.tenant_id == "TENANT-NOVASTACK"
        assert rec.source_type == "engineering_note"
        assert rec.status == "published"
        assert rec.classification == "internal"
        assert rec.authority_level == "medium"
        assert rec.version == "1.0"
        assert isinstance(rec.permissions, RecordPermissions)
        assert isinstance(rec.created_at, datetime)

    def test_record_permissions_defaults(self) -> None:
        perms = RecordPermissions()
        assert perms.allowed_roles == []
        assert perms.allowed_departments == []
        assert perms.allowed_teams == []


# ---------------------------------------------------------------
# 2. Required Fields
# ---------------------------------------------------------------

class TestRequiredFields:
    def test_all_demo_records_have_required_fields(
        self, demo_records: list[SourceRecord]
    ) -> None:
        for r in demo_records:
            assert r.document_id, "Missing document_id"
            assert r.tenant_id, "Missing tenant_id"
            assert r.source_type, "Missing source_type"
            assert r.title, "Missing title"
            assert len(r.content) > 20, "Content too short or empty"
            assert r.author_id, "Missing author_id"
            assert r.department, "Missing department"
            assert r.created_at is not None, "Missing created_at"


# ---------------------------------------------------------------
# 3. Optional Temporal Fields
# ---------------------------------------------------------------

class TestTemporalFields:
    def test_optional_temporal_fields_nullable(self) -> None:
        rec = SourceRecord(
            document_id="DOC-TIME-001",
            tenant_id="TENANT-NOVASTACK",
            source_type="policy",
            title="Policy Without Explicit Valid Dates",
            content="Policy body text.",
            author_id="USR-NS-0001",
            department="Security",
            valid_from=None,
            valid_until=None,
            updated_at=None,
        )
        assert rec.valid_from is None
        assert rec.valid_until is None
        assert rec.updated_at is None

    def test_temporal_validity_check(
        self, valid_entity_ids: set[str]
    ) -> None:
        # valid_from > valid_until is an error
        bad_rec = SourceRecord(
            document_id="DOC-TIME-BAD",
            tenant_id="TENANT-NOVASTACK",
            source_type="policy",
            title="Bad Time Policy",
            content="Content",
            author_id="USR-NS-0001",
            department="Security",
            valid_from=datetime(2026, 1, 1),
            valid_until=datetime(2025, 1, 1),
        )
        errors = validate_source_records(
            [bad_rec], valid_entity_ids, {"TENANT-NOVASTACK"}
        )
        assert any("valid_from > valid_until" in e for e in errors)


# ---------------------------------------------------------------
# 4. Valid Source Types
# ---------------------------------------------------------------

class TestValidSourceTypes:
    def test_controlled_source_types_set(self) -> None:
        expected = {
            "documentation",
            "incident",
            "postmortem",
            "support_ticket",
            "engineering_note",
            "conversation",
            "meeting",
            "policy",
            "deployment_note",
            "pull_request_note",
        }
        assert SOURCE_TYPES == expected

    def test_invalid_source_type_rejected_by_validator(
        self, valid_entity_ids: set[str]
    ) -> None:
        bad_rec = SourceRecord(
            document_id="DOC-BAD-TYPE",
            tenant_id="TENANT-NOVASTACK",
            source_type="uncontrolled_blog_post",
            title="Bad Type",
            content="Content",
            author_id="USR-NS-0001",
            department="Engineering",
        )
        errors = validate_source_records(
            [bad_rec], valid_entity_ids, {"TENANT-NOVASTACK"}
        )
        assert any("invalid source_type" in e for e in errors)

    def test_render_unknown_source_type_raises_value_error(
        self, generator: NovaStackGenerator
    ) -> None:
        ctx = RenderContext(event=generator.events[0])
        with pytest.raises(ValueError, match="Unknown source type"):
            render_source_record("nonexistent_source_type", ctx)


# ---------------------------------------------------------------
# 5. Valid Authority Levels & Defaults
# ---------------------------------------------------------------

class TestAuthorityLevels:
    def test_controlled_authority_levels_set(self) -> None:
        assert AUTHORITY_LEVELS == {
            "authoritative",
            "high",
            "medium",
            "low",
            "draft",
        }

    def test_source_type_defaults(self) -> None:
        assert DEFAULT_AUTHORITY_BY_SOURCE_TYPE["policy"] == "authoritative"
        assert DEFAULT_AUTHORITY_BY_SOURCE_TYPE["postmortem"] == "high"
        assert DEFAULT_AUTHORITY_BY_SOURCE_TYPE["incident"] == "high"
        assert DEFAULT_AUTHORITY_BY_SOURCE_TYPE["conversation"] == "low"
        assert DEFAULT_AUTHORITY_BY_SOURCE_TYPE["support_ticket"] == "low"

    def test_authority_override(self, generator: NovaStackGenerator) -> None:
        ctx = RenderContext(
            event=generator.events[0],
            incident=generator.incidents[0],
            authority_level="draft",
        )
        rec = render_source_record("incident", ctx)
        assert rec.authority_level == "draft"

    def test_invalid_authority_rejected_by_validator(
        self, valid_entity_ids: set[str]
    ) -> None:
        bad_rec = SourceRecord(
            document_id="DOC-BAD-AUTH",
            tenant_id="TENANT-NOVASTACK",
            source_type="incident",
            title="Bad Auth",
            content="Content",
            author_id="USR-NS-0001",
            department="Engineering",
            authority_level="maximum_truth",  # Invalid
        )
        errors = validate_source_records(
            [bad_rec], valid_entity_ids, {"TENANT-NOVASTACK"}
        )
        assert any("invalid authority_level" in e for e in errors)


# ---------------------------------------------------------------
# 6. Provenance Linkage
# ---------------------------------------------------------------

class TestProvenanceLinkage:
    def test_provenance_points_to_existing_entities(
        self, demo_records: list[SourceRecord], valid_entity_ids: set[str]
    ) -> None:
        for r in demo_records:
            if r.source_entity_id:
                assert r.source_entity_id in valid_entity_ids, (
                    f"Record {r.document_id} source_entity_id "
                    f"'{r.source_entity_id}' not in ground-truth entities"
                )
            for rel_id in r.related_entity_ids:
                assert rel_id in valid_entity_ids, (
                    f"Record {r.document_id} related entity '{rel_id}' "
                    f"not in ground-truth entities"
                )

    def test_validator_detects_broken_provenance(
        self, valid_entity_ids: set[str]
    ) -> None:
        bad_rec = SourceRecord(
            document_id="DOC-BROKEN-PROV",
            tenant_id="TENANT-NOVASTACK",
            source_type="postmortem",
            title="Broken Prov",
            content="Content",
            author_id="USR-NS-0001",
            department="Engineering",
            source_entity_id="EVT-GHOST-9999",  # Nonexistent
        )
        errors = validate_source_records(
            [bad_rec], valid_entity_ids, {"TENANT-NOVASTACK"}
        )
        assert any("source_entity_id 'EVT-GHOST-9999' does not exist" in e for e in errors)


# ---------------------------------------------------------------
# 7. Tenant Consistency
# ---------------------------------------------------------------

class TestTenantConsistency:
    def test_all_demo_records_same_tenant(
        self, demo_records: list[SourceRecord]
    ) -> None:
        for r in demo_records:
            assert r.tenant_id == "TENANT-NOVASTACK"

    def test_validator_rejects_unknown_tenant(
        self, valid_entity_ids: set[str]
    ) -> None:
        bad_rec = SourceRecord(
            document_id="DOC-FOREIGN-TENANT",
            tenant_id="TENANT-UNKNOWN",
            source_type="policy",
            title="Foreign Policy",
            content="Content",
            author_id="USR-NS-0001",
            department="Security",
        )
        errors = validate_source_records(
            [bad_rec], valid_entity_ids, {"TENANT-NOVASTACK"}
        )
        assert any("invalid tenant_id" in e for e in errors)


# ---------------------------------------------------------------
# 8. Permission Metadata Structure
# ---------------------------------------------------------------

class TestPermissionMetadata:
    def test_permissions_structure(
        self, demo_records: list[SourceRecord]
    ) -> None:
        for r in demo_records:
            assert isinstance(r.permissions, RecordPermissions)
            assert isinstance(r.permissions.allowed_roles, list)
            assert isinstance(r.permissions.allowed_departments, list)
            assert isinstance(r.permissions.allowed_teams, list)

    def test_classification_controlled(
        self, demo_records: list[SourceRecord]
    ) -> None:
        for r in demo_records:
            assert r.classification in CLASSIFICATION_LEVELS


# ---------------------------------------------------------------
# 9. Deterministic Rendering
# ---------------------------------------------------------------

class TestDeterministicRendering:
    def test_identical_context_produces_identical_record(
        self, generator: NovaStackGenerator
    ) -> None:
        ctx1 = RenderContext(
            event=generator.events[0],
            incident=generator.incidents[0],
            primary_service=generator.services[0],
        )
        ctx2 = RenderContext(
            event=generator.events[0],
            incident=generator.incidents[0],
            primary_service=generator.services[0],
        )
        r1 = render_source_record("incident", ctx1)
        r2 = render_source_record("incident", ctx2)
        assert r1 == r2

    def test_demo_fixture_deterministic_across_generator_instances(
        self,
    ) -> None:
        g1 = NovaStackGenerator(seed=RANDOM_SEED)
        g1.generate()
        recs1 = render_demonstration_fixture(g1)

        g2 = NovaStackGenerator(seed=RANDOM_SEED)
        g2.generate()
        recs2 = render_demonstration_fixture(g2)

        assert len(recs1) == len(recs2)
        for a, b in zip(recs1, recs2):
            assert a == b


# ---------------------------------------------------------------
# 10. Multi-Perspective Rendering from Identical Truth
# ---------------------------------------------------------------

class TestMultiPerspectiveRendering:
    def test_different_source_types_produce_distinct_content(
        self, demo_records: list[SourceRecord]
    ) -> None:
        # All records in demo_records derive from Event 1
        contents = [r.content for r in demo_records]
        titles = [r.title for r in demo_records]
        source_types = [r.source_type for r in demo_records]

        # Each record has unique content and title
        assert len(set(contents)) == len(demo_records)
        assert len(set(titles)) == len(demo_records)
        assert len(set(source_types)) == len(demo_records)

    def test_different_authority_levels_in_demo(
        self, demo_records: list[SourceRecord]
    ) -> None:
        auth_levels = {r.authority_level for r in demo_records}
        # Expect a mix of high, medium, and low in the demo records
        assert "high" in auth_levels
        assert "medium" in auth_levels
        assert "low" in auth_levels


# ---------------------------------------------------------------
# 11. Full Coverage of All 10 Registered Renderers
# ---------------------------------------------------------------

class TestAllRegisteredRenderers:
    def test_all_ten_renderers_produce_valid_records(
        self, generator: NovaStackGenerator, valid_entity_ids: set[str]
    ) -> None:
        event = generator.events[0]
        incident = generator.incidents[0]
        dep = generator.deployments[0]
        pr = generator.pull_requests[0]
        svc = generator.services[0]
        team = generator.teams[0]
        author = generator.users[0]
        cust = generator.customers[0]

        ctx = RenderContext(
            event=event,
            incident=incident,
            triggering_deployment=dep,
            fixing_pull_request=pr,
            primary_service=svc,
            responsible_team=team,
            author=author,
            customer=cust,
        )

        rendered_records: list[SourceRecord] = []
        for st in sorted(RENDERERS.keys()):
            rec = render_source_record(st, ctx)
            assert rec.source_type == st
            assert len(rec.content) > 0
            rendered_records.append(rec)

        assert len(rendered_records) == 10

        errors = validate_source_records(
            rendered_records, valid_entity_ids, {"TENANT-NOVASTACK"}
        )
        assert errors == [], f"Validation errors on registered renderers: {errors}"


# ===============================================================
# Milestone 4A — Core Observational Source Corpus Tests
# ===============================================================

@pytest.fixture(scope="module")
def full_corpus(generator: NovaStackGenerator) -> list[SourceRecord]:
    """Generate the full event-related observational source corpus."""
    corpus_gen = EventSourceCorpusGenerator(generator, seed=RANDOM_SEED)
    return corpus_gen.generate_corpus()


class TestMilestone4ACorpusScaleAndUniqueness:
    def test_corpus_target_scale(self, full_corpus: list[SourceRecord]) -> None:
        count = len(full_corpus)
        assert 400 <= count <= 600, (
            f"Expected corpus size between 400 and 600 records, got {count}"
        )

    def test_all_document_ids_unique(self, full_corpus: list[SourceRecord]) -> None:
        doc_ids = [r.document_id for r in full_corpus]
        assert len(doc_ids) == len(set(doc_ids)), "Duplicate document_id found in corpus"

    def test_source_type_distribution(self, full_corpus: list[SourceRecord]) -> None:
        types_present = {r.source_type for r in full_corpus}
        # All 10 controlled source types must be present across the corpus
        assert types_present == SOURCE_TYPES


class TestMilestone4ACorpusValidity:
    def test_all_records_pass_validation(
        self, full_corpus: list[SourceRecord], valid_entity_ids: set[str]
    ) -> None:
        errors = validate_source_records(
            full_corpus, valid_entity_ids, {"TENANT-NOVASTACK"}
        )
        assert errors == [], f"Validation errors found in corpus: {errors[:5]}"

    def test_all_records_belong_to_novastack(
        self, full_corpus: list[SourceRecord]
    ) -> None:
        for r in full_corpus:
            assert r.tenant_id == "TENANT-NOVASTACK"

    def test_all_records_have_valid_authority(
        self, full_corpus: list[SourceRecord]
    ) -> None:
        for r in full_corpus:
            assert r.authority_level in AUTHORITY_LEVELS

    def test_all_records_have_valid_classification(
        self, full_corpus: list[SourceRecord]
    ) -> None:
        for r in full_corpus:
            assert r.classification in CLASSIFICATION_LEVELS


class TestMilestone4ACorpusProvenance:
    def test_provenance_linkage_to_ground_truth(
        self, full_corpus: list[SourceRecord], valid_entity_ids: set[str]
    ) -> None:
        for r in full_corpus:
            # Policy records are enterprise-wide, all others must have valid provenance
            if r.source_type != "policy":
                assert r.source_entity_id, f"Record {r.document_id} missing source_entity_id"
                assert r.source_entity_id in valid_entity_ids, (
                    f"Record {r.document_id} source_entity_id {r.source_entity_id} does not exist"
                )
            for rel_id in r.related_entity_ids:
                assert rel_id in valid_entity_ids, (
                    f"Record {r.document_id} related_entity_id {rel_id} does not exist"
                )

    def test_no_cross_tenant_references_in_corpus(
        self, full_corpus: list[SourceRecord], generator: NovaStackGenerator
    ) -> None:
        # Build tenant map for all entities
        tenant_map: dict[str, str] = {}
        for u in generator.users:
            tenant_map[u.user_id] = u.tenant_id
        for t in generator.teams:
            tenant_map[t.team_id] = t.tenant_id
        for s in generator.services:
            tenant_map[s.service_id] = s.tenant_id
        for c in generator.customers:
            tenant_map[c.customer_id] = c.tenant_id
        for e in generator.events:
            tenant_map[e.event_id] = e.tenant_id
        for i in generator.incidents:
            tenant_map[i.incident_id] = i.tenant_id
        for d in generator.deployments:
            tenant_map[d.deployment_id] = d.tenant_id
        for p in generator.pull_requests:
            tenant_map[p.pull_request_id] = p.tenant_id

        for r in full_corpus:
            if r.source_entity_id and r.source_entity_id in tenant_map:
                assert tenant_map[r.source_entity_id] == r.tenant_id, (
                    f"Record {r.document_id} source entity in different tenant"
                )
            for rel_id in r.related_entity_ids:
                if rel_id in tenant_map:
                    assert tenant_map[rel_id] == r.tenant_id, (
                        f"Record {r.document_id} related entity {rel_id} in different tenant"
                    )


class TestMilestone4ACorpusTimelines:
    def test_event_records_timeline_coherence(
        self, full_corpus: list[SourceRecord], generator: NovaStackGenerator
    ) -> None:
        event_map = {e.event_id: e for e in generator.events}

        for r in full_corpus:
            # Check event-linked records
            ev_id = None
            if r.source_entity_id and r.source_entity_id.startswith("EVT-"):
                ev_id = r.source_entity_id
            else:
                for rel in r.related_entity_ids:
                    if rel.startswith("EVT-"):
                        ev_id = rel
                        break

            if ev_id and ev_id in event_map:
                ev = event_map[ev_id]
                # Incident/conversation/note records should not precede the event by weeks or succeed it by months
                if r.source_type in ("incident", "conversation", "engineering_note", "support_ticket"):
                    # Must be around the event start/end window (-1 day to +7 days)
                    assert ev.start_time - timedelta(days=2) <= r.created_at <= (ev.end_time or ev.start_time) + timedelta(days=7), (
                        f"Record {r.document_id} timestamp {r.created_at} outside event window {ev.start_time}"
                    )
                elif r.source_type == "postmortem":
                    # Postmortems must be written after event resolution
                    assert r.created_at >= (ev.end_time or ev.start_time), (
                        f"Postmortem {r.document_id} created before event ended"
                    )


class TestMilestone4ACorpusDeterminismAndIntegrity:
    def test_corpus_deterministic_generation(
        self, generator: NovaStackGenerator
    ) -> None:
        c1 = EventSourceCorpusGenerator(generator, seed=RANDOM_SEED).generate_corpus()
        c2 = EventSourceCorpusGenerator(generator, seed=RANDOM_SEED).generate_corpus()
        assert len(c1) == len(c2)
        for r1, r2 in zip(c1, c2):
            assert r1 == r2, f"Discrepancy between runs: {r1.document_id} vs {r2.document_id}"

    def test_ground_truth_entities_not_mutated(
        self, generator: NovaStackGenerator
    ) -> None:
        # Verify counts of ground-truth entities remain invariant
        assert len(generator.events) == 12
        assert len(generator.incidents) == 12
        assert len(generator.deployments) == 9
        assert len(generator.pull_requests) == 11
        assert len(generator.event_relationships) == 92
        assert len(generator.services) == 15
        assert len(generator.teams) == 15
        assert len(generator.users) == 100
        assert len(generator.customers) == 75


class TestMilestone4ACorpusStructuralVariation:
    def test_zero_customer_impact_events_have_no_tickets(
        self, full_corpus: list[SourceRecord], generator: NovaStackGenerator
    ) -> None:
        # Events 6 and 10 have zero customer impact
        zero_impact_events = {e.event_id for e in generator.events if len(e.impacted_customer_ids) == 0}
        assert len(zero_impact_events) >= 2, "Expected at least 2 events with zero customer impact"

        ticket_events = set()
        for r in full_corpus:
            if r.source_type == "support_ticket":
                for rel in r.related_entity_ids:
                    if rel.startswith("EVT-"):
                        ticket_events.add(rel)

        for ev_id in zero_impact_events:
            assert ev_id not in ticket_events, (
                f"Event {ev_id} has zero customer impact but generated support tickets"
            )

    def test_events_without_pr_have_no_pr_notes(
        self, full_corpus: list[SourceRecord], generator: NovaStackGenerator
    ) -> None:
        # Event 6 has no fixing PR
        no_pr_events = {e.event_id for e in generator.events if e.fixing_pull_request_id is None}
        assert len(no_pr_events) >= 1, "Expected at least 1 event without fixing PR"

        pr_note_events = set()
        for r in full_corpus:
            if r.source_type == "pull_request_note":
                for rel in r.related_entity_ids:
                    if rel.startswith("EVT-"):
                        pr_note_events.add(rel)

        for ev_id in no_pr_events:
            assert ev_id not in pr_note_events, (
                f"Event {ev_id} has no fixing PR but generated pull request notes"
            )

    def test_event_9_has_rollback_deployment_notes(
        self, full_corpus: list[SourceRecord], generator: NovaStackGenerator
    ) -> None:
        rollback_event = next(e for e in generator.events if e.event_type == "rollback")
        rollback_notes = [
            r for r in full_corpus
            if r.source_type == "deployment_note" and "ROLLBACK" in r.document_id
        ]
        assert len(rollback_notes) == 1, "Expected exactly 1 rollback deployment note"
        assert rollback_event.event_id in rollback_notes[0].related_entity_ids


# ===============================================================
# Milestone 4B — Background Enterprise Corpus Tests
# ===============================================================

@pytest.fixture(scope="module")
def background_corpus(generator: NovaStackGenerator) -> list[SourceRecord]:
    """Generate the background enterprise source corpus."""
    bkg_gen = BackgroundCorpusGenerator(generator, seed=RANDOM_SEED)
    return bkg_gen.generate_corpus()


@pytest.fixture(scope="module")
def combined_corpus(generator: NovaStackGenerator) -> list[SourceRecord]:
    """Generate the combined observational corpus (Milestones 4A + 4B)."""
    return generate_combined_corpus(generator, seed=RANDOM_SEED, include_noise=False)


class TestMilestone4BBackgroundCorpusScaleAndUniqueness:
    def test_background_corpus_scale(
        self, background_corpus: list[SourceRecord]
    ) -> None:
        count = len(background_corpus)
        assert 400 <= count <= 500, (
            f"Expected background corpus size between 400 and 500 records, got {count}"
        )

    def test_combined_corpus_scale(
        self, combined_corpus: list[SourceRecord]
    ) -> None:
        count = len(combined_corpus)
        assert 800 <= count <= 950, (
            f"Expected combined corpus size between 800 and 950 records, got {count}"
        )

    def test_all_document_ids_unique_in_combined_corpus(
        self, combined_corpus: list[SourceRecord]
    ) -> None:
        doc_ids = [r.document_id for r in combined_corpus]
        assert len(doc_ids) == len(set(doc_ids)), "Duplicate document_id found in combined corpus"


class TestMilestone4BDepartmentAndSourceTypeBreadth:
    def test_background_records_span_all_ten_departments(
        self, background_corpus: list[SourceRecord]
    ) -> None:
        departments = {r.department for r in background_corpus}
        expected_depts = {
            "Engineering",
            "Product",
            "Customer Support",
            "Sales",
            "Finance",
            "HR",
            "Security",
            "Operations",
            "Legal",
            "DevOps",
        }
        assert expected_depts.issubset(departments), (
            f"Missing departments in background corpus: {expected_depts - departments}"
        )

    def test_background_records_span_multiple_source_types(
        self, background_corpus: list[SourceRecord]
    ) -> None:
        source_types = {r.source_type for r in background_corpus}
        # Background corpus must naturally use at least 6 distinct source types
        assert len(source_types) >= 6, f"Too few source types in background corpus: {source_types}"
        assert source_types.issubset(SOURCE_TYPES)


class TestMilestone4BValidityAndTaxonomies:
    def test_combined_corpus_passes_validation(
        self, combined_corpus: list[SourceRecord], valid_entity_ids: set[str]
    ) -> None:
        errors = validate_source_records(
            combined_corpus, valid_entity_ids, {"TENANT-NOVASTACK"}
        )
        assert errors == [], f"Validation errors found in combined corpus: {errors[:5]}"

    def test_all_records_belong_to_novastack(
        self, combined_corpus: list[SourceRecord]
    ) -> None:
        for r in combined_corpus:
            assert r.tenant_id == "TENANT-NOVASTACK"

    def test_all_records_have_valid_authority(
        self, combined_corpus: list[SourceRecord]
    ) -> None:
        for r in combined_corpus:
            assert r.authority_level in AUTHORITY_LEVELS

    def test_all_records_have_valid_classification(
        self, combined_corpus: list[SourceRecord]
    ) -> None:
        for r in combined_corpus:
            assert r.classification in CLASSIFICATION_LEVELS

    def test_all_records_have_valid_permissions(
        self, combined_corpus: list[SourceRecord]
    ) -> None:
        for r in combined_corpus:
            assert isinstance(r.permissions, RecordPermissions)
            assert isinstance(r.permissions.allowed_roles, list)
            assert isinstance(r.permissions.allowed_departments, list)


class TestMilestone4BProvenanceAndReferences:
    def test_background_records_have_no_event_provenance(
        self, background_corpus: list[SourceRecord]
    ) -> None:
        for r in background_corpus:
            # Background records must NOT claim source entity provenance from events or incidents
            assert r.source_entity_type is None, (
                f"Record {r.document_id} has unexpected source_entity_type {r.source_entity_type}"
            )
            assert r.source_entity_id is None, (
                f"Record {r.document_id} has unexpected source_entity_id {r.source_entity_id}"
            )
            # Must not reference canonical EVT-* or INC-* entities
            for rel in r.related_entity_ids:
                assert not rel.startswith("EVT-"), (
                    f"Background record {r.document_id} fabricated reference to event {rel}"
                )
                assert not rel.startswith("INC-"), (
                    f"Background record {r.document_id} fabricated reference to incident {rel}"
                )

    def test_background_record_references_resolve_to_existing_entities(
        self, background_corpus: list[SourceRecord], valid_entity_ids: set[str]
    ) -> None:
        for r in background_corpus:
            assert r.author_id in valid_entity_ids, (
                f"Record {r.document_id} author {r.author_id} not found"
            )
            for rel_id in r.related_entity_ids:
                assert rel_id in valid_entity_ids, (
                    f"Record {r.document_id} related entity {rel_id} not found"
                )

    def test_no_cross_tenant_references_in_background_records(
        self, background_corpus: list[SourceRecord], generator: NovaStackGenerator
    ) -> None:
        tenant_map = {u.user_id: u.tenant_id for u in generator.users}
        tenant_map.update({s.service_id: s.tenant_id for s in generator.services})
        tenant_map.update({c.customer_id: c.tenant_id for c in generator.customers})

        for r in background_corpus:
            if r.author_id in tenant_map:
                assert tenant_map[r.author_id] == r.tenant_id
            for rel_id in r.related_entity_ids:
                if rel_id in tenant_map:
                    assert tenant_map[rel_id] == r.tenant_id


class TestMilestone4BDeterminismAndIntegrity:
    def test_background_corpus_deterministic_generation(
        self, generator: NovaStackGenerator
    ) -> None:
        b1 = BackgroundCorpusGenerator(generator, seed=RANDOM_SEED).generate_corpus()
        b2 = BackgroundCorpusGenerator(generator, seed=RANDOM_SEED).generate_corpus()
        assert len(b1) == len(b2)
        for r1, r2 in zip(b1, b2):
            assert r1 == r2, f"Discrepancy between runs: {r1.document_id} vs {r2.document_id}"

    def test_combined_corpus_deterministic_generation(
        self, generator: NovaStackGenerator
    ) -> None:
        c1 = generate_combined_corpus(generator, seed=RANDOM_SEED)
        c2 = generate_combined_corpus(generator, seed=RANDOM_SEED)
        assert len(c1) == len(c2)
        for r1, r2 in zip(c1, c2):
            assert r1 == r2

    def test_ground_truth_entities_not_mutated_after_background_generation(
        self, generator: NovaStackGenerator
    ) -> None:
        assert len(generator.events) == 12
        assert len(generator.incidents) == 12
        assert len(generator.deployments) == 9
        assert len(generator.pull_requests) == 11
        assert len(generator.event_relationships) == 92
        assert len(generator.services) == 15
        assert len(generator.teams) == 15
        assert len(generator.users) == 100
        assert len(generator.customers) == 75


class TestMilestone4BSemanticDistraction:
    def test_background_corpus_contains_distractor_keywords(
        self, background_corpus: list[SourceRecord]
    ) -> None:
        combined_text = " ".join(r.content.lower() + " " + r.title.lower() for r in background_corpus)
        # Background corpus must naturally contain distractor keywords in routine contexts
        keywords = ["checkout", "payment", "latency", "timeout", "api", "database", "customer"]
        for kw in keywords:
            assert kw in combined_text, f"Expected distractor keyword '{kw}' in background corpus"


# ===============================================================
# Milestone 4C — Temporal Noise, Duplicates, Versions & Conflicts
# ===============================================================

@pytest.fixture(scope="module")
def noise_corpus(
    generator: NovaStackGenerator, combined_corpus: list[SourceRecord]
) -> list[SourceRecord]:
    """Generate controlled noise records (Milestone 4C)."""
    noise_gen = TemporalNoiseGenerator(generator, combined_corpus, seed=RANDOM_SEED)
    return noise_gen.generate_noise_records()


@pytest.fixture(scope="module")
def full_noisy_corpus(generator: NovaStackGenerator) -> list[SourceRecord]:
    """Generate the complete combined corpus with noise (Milestones 4A + 4B + 4C)."""
    return generate_combined_corpus(generator, seed=RANDOM_SEED, include_noise=True)


class TestMilestone4CScaleAndUniqueness:
    def test_noise_corpus_scale(self, noise_corpus: list[SourceRecord]) -> None:
        count = len(noise_corpus)
        assert 200 <= count <= 400, (
            f"Expected noise corpus size between 200 and 400 records, got {count}"
        )

    def test_full_corpus_scale(self, full_noisy_corpus: list[SourceRecord]) -> None:
        count = len(full_noisy_corpus)
        assert 1100 <= count <= 1300, (
            f"Expected full noisy corpus size between 1100 and 1300 records, got {count}"
        )

    def test_all_document_ids_unique_in_noisy_corpus(
        self, full_noisy_corpus: list[SourceRecord]
    ) -> None:
        doc_ids = [r.document_id for r in full_noisy_corpus]
        assert len(doc_ids) == len(set(doc_ids)), "Duplicate document_id found in full noisy corpus"


class TestMilestone4CNearDuplicates:
    def test_near_duplicates_representation(
        self, noise_corpus: list[SourceRecord], full_noisy_corpus: list[SourceRecord]
    ) -> None:
        dup_records = [r for r in noise_corpus if "DOC-NOISE-DUP-" in r.document_id]
        assert len(dup_records) >= 50, f"Expected at least 50 near duplicates, got {len(dup_records)}"

        doc_map = {r.document_id: r for r in full_noisy_corpus}
        for dup in dup_records:
            # Near duplicate must have distinct ID from its parent
            assert dup.parent_id is not None
            assert dup.parent_id in doc_map, f"Duplicate parent {dup.parent_id} does not exist"
            assert dup.parent_id != dup.document_id
            # Duplicate and parent share department and tenant
            parent = doc_map[dup.parent_id]
            assert dup.tenant_id == parent.tenant_id
            assert dup.department == parent.department

    def test_near_duplicates_differ_from_originals(
        self, noise_corpus: list[SourceRecord], full_noisy_corpus: list[SourceRecord]
    ) -> None:
        dup_records = [r for r in noise_corpus if "DOC-NOISE-DUP-" in r.document_id]
        doc_map = {r.document_id: r for r in full_noisy_corpus}
        for dup in dup_records:
            parent = doc_map[dup.parent_id]
            # Semantic variation: title or content is not 100% byte-identical
            assert dup.title != parent.title or dup.content != parent.content


class TestMilestone4CStaleRecords:
    def test_stale_records_valid_window(
        self, noise_corpus: list[SourceRecord]
    ) -> None:
        stale_records = [r for r in noise_corpus if "DOC-NOISE-STALE-" in r.document_id]
        assert len(stale_records) >= 35, f"Expected at least 35 stale records, got {len(stale_records)}"

        for r in stale_records:
            assert r.valid_from is not None and r.valid_until is not None
            assert r.valid_from <= r.valid_until, f"Stale record {r.document_id} valid_from > valid_until"
            assert r.status in ("deprecated", "superseded", "archived")

    def test_stale_records_historical_validity(
        self, noise_corpus: list[SourceRecord]
    ) -> None:
        stale_records = [r for r in noise_corpus if "DOC-NOISE-STALE-" in r.document_id]
        for r in stale_records:
            # All stale records must have expired by mid-2025 / early 2026
            assert r.valid_until <= datetime(2026, 6, 1), (
                f"Stale record {r.document_id} valid_until ({r.valid_until}) not in historical past"
            )


class TestMilestone4CVersionChains:
    def test_version_chains_continuity(
        self, noise_corpus: list[SourceRecord], full_noisy_corpus: list[SourceRecord]
    ) -> None:
        chain_records = [r for r in noise_corpus if "DOC-NOISE-VER-" in r.document_id]
        assert len(chain_records) >= 40, f"Expected at least 40 version chain records, got {len(chain_records)}"

        doc_map = {r.document_id: r for r in full_noisy_corpus}
        for r in chain_records:
            if r.supersedes_id:
                assert r.supersedes_id in doc_map, (
                    f"Record {r.document_id} supersedes_id '{r.supersedes_id}' does not exist"
                )
                assert r.supersedes_id != r.document_id

    def test_version_chains_temporal_coherence(
        self, noise_corpus: list[SourceRecord], full_noisy_corpus: list[SourceRecord]
    ) -> None:
        chain_records = [r for r in noise_corpus if "DOC-NOISE-VER-" in r.document_id]
        doc_map = {r.document_id: r for r in full_noisy_corpus}
        for r in chain_records:
            if r.supersedes_id:
                older = doc_map[r.supersedes_id]
                assert r.created_at >= older.created_at, (
                    f"Record {r.document_id} created_at {r.created_at} precedes older {older.created_at}"
                )

    def test_version_chains_version_increment(
        self, noise_corpus: list[SourceRecord], full_noisy_corpus: list[SourceRecord]
    ) -> None:
        v3_records = [r for r in noise_corpus if r.document_id.endswith("-V3")]
        assert len(v3_records) >= 15, "Expected at least 15 v3 terminal chain records"
        doc_map = {r.document_id: r for r in full_noisy_corpus}

        for v3 in v3_records:
            assert v3.version == "3.0"
            assert v3.status == "published"
            v2 = doc_map[v3.supersedes_id]
            assert v2.version == "2.0"
            assert v2.status == "superseded"
            v1 = doc_map[v2.supersedes_id]
            assert v1.version == "1.0"
            assert v1.status == "superseded"

    def test_parent_id_resolution(
        self, noise_corpus: list[SourceRecord], full_noisy_corpus: list[SourceRecord]
    ) -> None:
        doc_map = {r.document_id: r for r in full_noisy_corpus}
        for r in noise_corpus:
            if r.parent_id:
                assert r.parent_id in doc_map, f"Record {r.document_id} parent_id {r.parent_id} does not exist"


class TestMilestone4CDrafts:
    def test_drafts_lifecycle_status(
        self, noise_corpus: list[SourceRecord]
    ) -> None:
        draft_records = [r for r in noise_corpus if "DOC-NOISE-DFT-" in r.document_id]
        assert len(draft_records) >= 30, f"Expected at least 30 draft records, got {len(draft_records)}"
        for d in draft_records:
            assert d.status == "draft", f"Draft {d.document_id} has invalid status {d.status}"
            assert d.authority_level in ("draft", "low", "medium")


class TestMilestone4CConflictingObservations:
    def test_conflicting_observations_provenance(
        self, noise_corpus: list[SourceRecord], generator: NovaStackGenerator
    ) -> None:
        conflict_records = [r for r in noise_corpus if "DOC-NOISE-CONF-" in r.document_id]
        assert len(conflict_records) >= 30, f"Expected at least 30 conflicting records, got {len(conflict_records)}"

        event_ids = {e.event_id for e in generator.events}
        service_ids = {s.service_id for s in generator.services}

        for c in conflict_records:
            # Conflicting observation must preserve provenance by referencing event or service
            assert any(rel in event_ids or rel in service_ids for rel in c.related_entity_ids), (
                f"Conflicting record {c.document_id} lacks provenance link to event or service"
            )


class TestMilestone4CCorrectedObservations:
    def test_corrections_provenance_and_timing(
        self, noise_corpus: list[SourceRecord], generator: NovaStackGenerator
    ) -> None:
        corr_records = [r for r in noise_corpus if "DOC-NOISE-CORR-" in r.document_id]
        assert len(corr_records) >= 25, f"Expected at least 25 correction records, got {len(corr_records)}"

        event_map = {e.event_id: e for e in generator.events}
        for c in corr_records:
            ev_id = next((rel for rel in c.related_entity_ids if rel in event_map), None)
            assert ev_id is not None, f"Correction {c.document_id} does not link to valid event"
            ev = event_map[ev_id]
            # Correction must be created after event start
            assert c.created_at >= ev.start_time, (
                f"Correction {c.document_id} created before event start time {ev.start_time}"
            )
            # Must explicitly identify as correction
            assert "Correction" in c.title or "Correction" in c.content


class TestMilestone4CValidationAndTaxonomies:
    def test_full_noisy_corpus_passes_validator(
        self, full_noisy_corpus: list[SourceRecord], valid_entity_ids: set[str]
    ) -> None:
        errors = validate_source_records(
            full_noisy_corpus, valid_entity_ids, {"TENANT-NOVASTACK"}
        )
        assert errors == [], f"Validation errors found in full noisy corpus: {errors[:5]}"

    def test_all_statuses_valid(
        self, full_noisy_corpus: list[SourceRecord]
    ) -> None:
        for r in full_noisy_corpus:
            assert r.status in RECORD_STATUSES, f"Record {r.document_id} has invalid status {r.status}"

    def test_no_cross_tenant_references_in_noise(
        self, noise_corpus: list[SourceRecord]
    ) -> None:
        for r in noise_corpus:
            assert r.tenant_id == "TENANT-NOVASTACK"


class TestMilestone4CDeterminismAndIntegrity:
    def test_noise_deterministic_generation(
        self, generator: NovaStackGenerator, combined_corpus: list[SourceRecord]
    ) -> None:
        n1 = TemporalNoiseGenerator(generator, combined_corpus, seed=RANDOM_SEED).generate_noise_records()
        n2 = TemporalNoiseGenerator(generator, combined_corpus, seed=RANDOM_SEED).generate_noise_records()
        assert len(n1) == len(n2)
        for r1, r2 in zip(n1, n2):
            assert r1 == r2

    def test_ground_truth_unmutated_after_noise(
        self, generator: NovaStackGenerator
    ) -> None:
        assert len(generator.events) == 12
        assert len(generator.incidents) == 12
        assert len(generator.deployments) == 9
        assert len(generator.pull_requests) == 11
        assert len(generator.event_relationships) == 92
        assert len(generator.services) == 15
        assert len(generator.teams) == 15
        assert len(generator.users) == 100
        assert len(generator.customers) == 75

