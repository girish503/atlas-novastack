"""Unit and integration tests for Phase 1C Milestone 4D-2: Indirect Prompt Injection & Retrieval Poisoning Corpus.

Verifies:
- Adversarial record scale (60–100 records) and unique IDs
- Document ID formatting across all 8 attack categories
- Valid tenant, source type, classification, and permissions metadata
- Zero errors from source-record validator
- Correctness of all 8 attack categories
- Poisoned vs non-poisoned labeling counts (38 poisoned, 52 non-poisoned)
- Correctness and coverage of structured attack fixtures (20 fixtures)
- Target document and legitimate evidence ID resolution
- Behavior across injection variants (direct, indirect, poisoning, manipulation, confusion, citation, obfuscated, cross-tenant)
- Safety constraints: no real secrets, no malware, no executable attack code
- Determinism and canonical ground-truth immutability
- Full combined corpus scale (1,393 records)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure src/ is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novastack.adversarial_corpus import AdversarialCorpusGenerator
from novastack.config import RANDOM_SEED
from novastack.corpus_generator import generate_combined_corpus
from novastack.generator import NovaStackGenerator
from novastack.models import (
    ATTACK_CATEGORIES,
    CLASSIFICATION_LEVELS,
    RECORD_STATUSES,
    SOURCE_TYPES,
    AdversarialFixture,
    SourceRecord,
)
from novastack.rendering import validate_source_records


@pytest.fixture(scope="module")
def generator() -> NovaStackGenerator:
    """Instantiate and run NovaStack organizational and ground-truth generator."""
    gen = NovaStackGenerator(seed=RANDOM_SEED)
    gen.generate()
    return gen


@pytest.fixture(scope="module")
def adversarial_generator(generator: NovaStackGenerator) -> AdversarialCorpusGenerator:
    """Instantiate AdversarialCorpusGenerator."""
    return AdversarialCorpusGenerator(generator, seed=RANDOM_SEED)


@pytest.fixture(scope="module")
def adversarial_records(
    adversarial_generator: AdversarialCorpusGenerator,
) -> list[SourceRecord]:
    """Generate adversarial source records."""
    return adversarial_generator.generate_corpus()


@pytest.fixture(scope="module")
def adversarial_fixtures(
    adversarial_generator: AdversarialCorpusGenerator,
) -> list[AdversarialFixture]:
    """Generate adversarial attack fixtures."""
    return adversarial_generator.generate_fixtures()


# ---------------------------------------------------------------------------
# 1. Scale and Uniqueness Tests
# ---------------------------------------------------------------------------


class TestMilestone4DAdversarialRecordScaleAndUniqueness:
    def test_adversarial_record_scale(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        count = len(adversarial_records)
        assert 60 <= count <= 100, (
            f"Expected adversarial corpus size between 60 and 100 records, got {count}"
        )
        assert count == 90, f"Expected exactly 90 adversarial records, got {count}"

    def test_all_adversarial_document_ids_unique(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        doc_ids = [r.document_id for r in adversarial_records]
        assert len(doc_ids) == len(set(doc_ids)), (
            "Duplicate document_id found in adversarial records"
        )

    def test_adversarial_document_id_formatting(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        valid_prefixes = (
            "DOC-ADV-DIR-",
            "DOC-ADV-INJ-",
            "DOC-ADV-PSN-",
            "DOC-ADV-MAN-",
            "DOC-ADV-IDC-",
            "DOC-ADV-CIT-",
            "DOC-ADV-OBF-",
            "DOC-ADV-XTT-",
        )
        for r in adversarial_records:
            assert r.document_id.startswith(valid_prefixes), (
                f"Document ID {r.document_id} does not have a recognized adversarial prefix"
            )


# ---------------------------------------------------------------------------
# 2. Metadata, Taxonomies, and Validator Compliance
# ---------------------------------------------------------------------------


class TestMilestone4DAdversarialTaxonomies:
    def test_valid_tenants(self, adversarial_records: list[SourceRecord]) -> None:
        valid_tenants = {"TENANT-NOVASTACK", "TENANT-ORBITAL", "TENANT-PINECONE"}
        for r in adversarial_records:
            assert r.tenant_id in valid_tenants, (
                f"Invalid tenant {r.tenant_id} on {r.document_id}"
            )

    def test_valid_source_types(self, adversarial_records: list[SourceRecord]) -> None:
        for r in adversarial_records:
            assert r.source_type in SOURCE_TYPES, f"Invalid source_type {r.source_type}"

    def test_valid_classifications(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        for r in adversarial_records:
            assert r.classification in CLASSIFICATION_LEVELS, (
                f"Invalid classification {r.classification}"
            )

    def test_valid_record_statuses(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        for r in adversarial_records:
            assert r.status in RECORD_STATUSES, f"Invalid status {r.status}"

    def test_adversarial_corpus_passes_validator(
        self, adversarial_records: list[SourceRecord], generator: NovaStackGenerator
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
        errors = validate_source_records(
            adversarial_records, all_entity_ids, valid_tenants
        )
        assert errors == [], f"Validation errors in adversarial corpus: {errors}"


# ---------------------------------------------------------------------------
# 3. Attack Categories & Poisoned vs Non-Poisoned Distribution
# ---------------------------------------------------------------------------


class TestMilestone4DAttackCategoriesAndDistribution:
    def test_all_eight_attack_categories_represented(
        self, adversarial_generator: AdversarialCorpusGenerator
    ) -> None:
        assert adversarial_generator.direct_instruction_count == 10
        assert adversarial_generator.indirect_prompt_injection_count == 16
        assert adversarial_generator.retrieval_poisoning_count == 16
        assert adversarial_generator.evidence_manipulation_count == 12
        assert adversarial_generator.instruction_data_confusion_count == 12
        assert adversarial_generator.citation_manipulation_count == 10
        assert adversarial_generator.hidden_obfuscated_count == 10
        assert adversarial_generator.cross_tenant_adversarial_count == 4

        total = (
            adversarial_generator.direct_instruction_count
            + adversarial_generator.indirect_prompt_injection_count
            + adversarial_generator.retrieval_poisoning_count
            + adversarial_generator.evidence_manipulation_count
            + adversarial_generator.instruction_data_confusion_count
            + adversarial_generator.citation_manipulation_count
            + adversarial_generator.hidden_obfuscated_count
            + adversarial_generator.cross_tenant_adversarial_count
        )
        assert total == 90

    def test_poisoned_vs_non_poisoned_counts(
        self, adversarial_generator: AdversarialCorpusGenerator
    ) -> None:
        assert adversarial_generator.poisoned_count == 38
        assert adversarial_generator.non_poisoned_count == 52
        assert (
            adversarial_generator.poisoned_count
            + adversarial_generator.non_poisoned_count
            == 90
        )

    def test_attack_categories_vocabulary_compliance(self) -> None:
        expected = {
            "direct_instruction",
            "indirect_prompt_injection",
            "retrieval_poisoning",
            "evidence_manipulation",
            "instruction_data_confusion",
            "citation_manipulation",
            "hidden_obfuscated_variant",
            "cross_tenant_adversarial",
        }
        assert set(ATTACK_CATEGORIES) == expected


# ---------------------------------------------------------------------------
# 4. Adversarial Ground-Truth Fixtures Correctness
# ---------------------------------------------------------------------------


class TestMilestone4DAdversarialFixtures:
    def test_fixture_count_and_uniqueness(
        self, adversarial_fixtures: list[AdversarialFixture]
    ) -> None:
        assert len(adversarial_fixtures) >= 15, (
            "Must generate at least 15 explicit attack fixtures"
        )
        assert len(adversarial_fixtures) == 20, (
            f"Expected 20 fixtures, got {len(adversarial_fixtures)}"
        )
        fixture_ids = [f.fixture_id for f in adversarial_fixtures]
        assert len(fixture_ids) == len(set(fixture_ids)), "Duplicate fixture_id found"

    def test_fixture_categories_valid(
        self, adversarial_fixtures: list[AdversarialFixture]
    ) -> None:
        for f in adversarial_fixtures:
            assert f.attack_category in ATTACK_CATEGORIES, (
                f"Invalid attack category {f.attack_category} on {f.fixture_id}"
            )
            assert f.target_document_id.startswith("DOC-ADV-")
            assert f.query != ""
            assert f.expected_behavior != ""
            assert f.expected_safe_answer_behavior != ""
            assert f.expected_citation_behavior != ""

    def test_target_document_ids_exist(
        self,
        adversarial_fixtures: list[AdversarialFixture],
        adversarial_records: list[SourceRecord],
    ) -> None:
        record_ids = {r.document_id for r in adversarial_records}
        for f in adversarial_fixtures:
            assert f.target_document_id in record_ids, (
                f"Fixture {f.fixture_id} references target {f.target_document_id} not in adversarial records"
            )

    def test_fixture_poisoned_boolean_consistency(
        self, adversarial_fixtures: list[AdversarialFixture]
    ) -> None:
        poisoned_categories = {
            "retrieval_poisoning",
            "evidence_manipulation",
            "citation_manipulation",
        }
        for f in adversarial_fixtures:
            if f.attack_category in poisoned_categories:
                assert f.is_poisoned is True, (
                    f"Fixture {f.fixture_id} ({f.attack_category}) should have is_poisoned=True"
                )
            else:
                assert f.is_poisoned is False, (
                    f"Fixture {f.fixture_id} ({f.attack_category}) should have is_poisoned=False"
                )

    def test_fixture_instructional_boolean_consistency(
        self, adversarial_fixtures: list[AdversarialFixture]
    ) -> None:
        instructional_categories = {
            "direct_instruction",
            "indirect_prompt_injection",
            "instruction_data_confusion",
            "hidden_obfuscated_variant",
            "cross_tenant_adversarial",
        }
        for f in adversarial_fixtures:
            if f.attack_category in instructional_categories:
                assert f.is_instructional is True, (
                    f"Fixture {f.fixture_id} ({f.attack_category}) should have is_instructional=True"
                )
            else:
                assert f.is_instructional is False, (
                    f"Fixture {f.fixture_id} ({f.attack_category}) should have is_instructional=False"
                )

    def test_fixture_to_dict_serialization(
        self, adversarial_fixtures: list[AdversarialFixture]
    ) -> None:
        for f in adversarial_fixtures:
            d = f.to_dict()
            assert isinstance(d, dict)
            assert d["fixture_id"] == f.fixture_id
            assert d["attack_category"] == f.attack_category
            assert d["target_document_id"] == f.target_document_id
            assert "is_poisoned" in d
            assert "is_instructional" in d


# ---------------------------------------------------------------------------
# 5. Attack Category Behaviors and Content Assertions
# ---------------------------------------------------------------------------


class TestMilestone4DAttackCategoryContentBehaviors:
    def test_direct_instruction_payloads(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        direct_records = [
            r for r in adversarial_records if r.document_id.startswith("DOC-ADV-DIR-")
        ]
        assert len(direct_records) == 10
        for r in direct_records:
            text = (r.title + " " + r.content).lower()
            has_instruction = any(
                kw in text
                for kw in [
                    "system override",
                    "directive for ai",
                    "command for search",
                    "prompt injection",
                    "system instruction",
                    "debugmode",
                    "ignore earlier",
                    "override earlier",
                    "disregard all",
                ]
            )
            assert has_instruction, (
                f"Direct instruction record {r.document_id} missing override pattern"
            )

    def test_indirect_prompt_injection_payloads(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        inj_records = [
            r for r in adversarial_records if r.document_id.startswith("DOC-ADV-INJ-")
        ]
        assert len(inj_records) == 16
        for r in inj_records:
            text = r.content.lower()
            has_injection = any(
                term in text
                for term in [
                    "ai assistant",
                    "ai model",
                    "ai reasoning",
                    "ai code reviewer",
                    "automated assistant",
                    "automated summarization",
                    "automated triage",
                    "assistant context",
                    "llm summarizer",
                    "special agent notice",
                ]
            )
            assert has_injection, (
                f"Indirect injection record {r.document_id} missing embedded assistant pattern"
            )

    def test_retrieval_poisoning_payloads(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        psn_records = [
            r for r in adversarial_records if r.document_id.startswith("DOC-ADV-PSN-")
        ]
        assert len(psn_records) == 16
        canonical_event_references = [
            r.source_entity_id for r in psn_records if r.source_entity_id
        ]
        assert len(canonical_event_references) > 0
        for r in psn_records:
            assert any(
                term in r.content.lower()
                for term in ["root cause", "post-incident", "findings", "concluded", "identified as", "incident investigation"]
            )

    def test_evidence_manipulation_payloads(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        man_records = [
            r for r in adversarial_records if r.document_id.startswith("DOC-ADV-MAN-")
        ]
        assert len(man_records) == 12
        for r in man_records:
            text = (r.title + " " + r.content).lower()
            assert any(
                term in text
                for term in [
                    "timeline",
                    "sla",
                    "approval",
                    "deployment",
                    "audit",
                    "compliance",
                    "outage",
                    "dropped packets",
                    "webhook",
                ]
            )

    def test_instruction_data_confusion_payloads(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        idc_records = [
            r for r in adversarial_records if r.document_id.startswith("DOC-ADV-IDC-")
        ]
        assert len(idc_records) == 12
        for r in idc_records:
            text = (r.title + " " + r.content).lower()
            assert any(
                kw in text
                for kw in [
                    "runbook",
                    "reproduction",
                    "procedure",
                    "workflow",
                    "psql",
                    "curl",
                    "notice for ai",
                    "human operator",
                    "untrusted reference code",
                    "steps",
                ]
            )

    def test_citation_manipulation_payloads(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        cit_records = [
            r for r in adversarial_records if r.document_id.startswith("DOC-ADV-CIT-")
        ]
        assert len(cit_records) == 10
        for r in cit_records:
            text = (r.title + " " + r.content).lower()
            assert any(
                phrase in text
                for phrase in [
                    "canonical",
                    "supersedes",
                    "authoritative",
                    "directive",
                    "exclusive source",
                    "cite this",
                    "definitive legal proof",
                    "binding standard",
                ]
            )

    def test_hidden_obfuscated_variants(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        obf_records = [
            r for r in adversarial_records if r.document_id.startswith("DOC-ADV-OBF-")
        ]
        assert len(obf_records) == 10
        for r in obf_records:
            has_hidden = (
                "<!--" in r.content
                or "<details>" in r.content
                or "[//]: #" in r.content
                or "> [!NOTE]" in r.content
            )
            assert has_hidden, (
                f"Obfuscated record {r.document_id} missing hidden markup comment or detail block"
            )

    def test_cross_tenant_adversarial_records(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        xtt_records = [
            r for r in adversarial_records if r.document_id.startswith("DOC-ADV-XTT-")
        ]
        assert len(xtt_records) == 4
        tenants = {r.tenant_id for r in xtt_records}
        assert tenants == {"TENANT-ORBITAL", "TENANT-PINECONE"}
        for r in xtt_records:
            assert "NovaStack" in r.content or "TENANT-NOVASTACK" in r.content


# ---------------------------------------------------------------------------
# 6. Safety Boundaries, Determinism & Immutability Tests
# ---------------------------------------------------------------------------


class TestMilestone4DSafetyDeterminismAndImmutability:
    def test_safety_no_live_malware_or_real_credentials(
        self, adversarial_records: list[SourceRecord]
    ) -> None:
        forbidden_patterns = [
            "curl -s http://evil",
            "rm -rf /",
            "format c:",
            "powershell -encodedcommand",
            "eval(base64",
            "BEGIN RSA PRIVATE KEY",
            "AKIAIOSFODNN7EXAMPLE",
        ]
        for r in adversarial_records:
            for pat in forbidden_patterns:
                assert pat not in r.content, (
                    f"Record {r.document_id} contains forbidden dangerous pattern: {pat}"
                )

    def test_deterministic_generation(self, generator: NovaStackGenerator) -> None:
        gen1 = AdversarialCorpusGenerator(generator, seed=RANDOM_SEED)
        gen2 = AdversarialCorpusGenerator(generator, seed=RANDOM_SEED)

        recs1 = gen1.generate_corpus()
        recs2 = gen2.generate_corpus()

        assert len(recs1) == len(recs2) == 90
        for r1, r2 in zip(recs1, recs2):
            assert r1.document_id == r2.document_id
            assert r1.tenant_id == r2.tenant_id
            assert r1.title == r2.title
            assert r1.content == r2.content
            assert r1.classification == r2.classification

        fix1 = gen1.generate_fixtures()
        fix2 = gen2.generate_fixtures()
        assert len(fix1) == len(fix2) == 20
        for f1, f2 in zip(fix1, fix2):
            assert f1.fixture_id == f2.fixture_id
            assert f1.attack_category == f2.attack_category
            assert f1.target_document_id == f2.target_document_id
            assert f1.query == f2.query

    def test_canonical_ground_truth_unmutated(
        self, generator: NovaStackGenerator
    ) -> None:
        events_before = len(generator.events)
        incidents_before = len(generator.incidents)
        deployments_before = len(generator.deployments)
        prs_before = len(generator.pull_requests)
        rel_before = len(generator.event_relationships)

        adv_gen = AdversarialCorpusGenerator(generator, seed=RANDOM_SEED)
        adv_gen.generate_corpus()

        assert len(generator.events) == events_before == 12
        assert len(generator.incidents) == incidents_before == 12
        assert len(generator.deployments) == deployments_before == 9
        assert len(generator.pull_requests) == prs_before == 11
        assert len(generator.event_relationships) == rel_before == 92

    def test_combined_corpus_with_adversarial_enabled(
        self, generator: NovaStackGenerator
    ) -> None:
        full_corpus = generate_combined_corpus(
            generator,
            seed=RANDOM_SEED,
            include_noise=True,
            include_security=True,
            include_adversarial=True,
        )
        assert len(full_corpus) == 1393
