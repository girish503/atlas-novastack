"""Unit and integration tests for Phase 1C Milestone 4E-1: Evaluation Dataset Foundation.

Verifies:
- Evaluation dataset scale (120 cases) and ID format/uniqueness
- Complete coverage of all 20 query categories
- Difficulty distribution across easy, medium, hard tiers
- Referential integrity of all document IDs (required, acceptable, forbidden, citation)
- Referential integrity of all entity IDs (users, teams, services, customers, events)
- Linkage to security fixtures (22 cases) and adversarial fixtures (15 cases)
- Explicit abstention for missing information queries
- Structural expected answer facts for answer generation evaluation
- Validation function returns 0 errors
- 100% determinism with random seed
- Canonical ground-truth layer immutability
- Serialized JSON files integrity (evaluation_cases.json and evaluation_taxonomy.json)
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

# Ensure src/ is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novastack.adversarial_corpus import AdversarialCorpusGenerator
from novastack.config import DATASET_VERSION, RANDOM_SEED
from novastack.corpus_generator import generate_combined_corpus
from novastack.eval_generator import (
    EvaluationDatasetGenerator,
    build_evaluation_taxonomy,
    validate_evaluation_cases,
)
from novastack.generator import NovaStackGenerator
from novastack.models import (
    EVALUATION_BEHAVIORS,
    EVALUATION_DIFFICULTIES,
    QUERY_CATEGORIES,
    AdversarialFixture,
    EvaluationCase,
    SecurityFixture,
    SourceRecord,
)
from novastack.security_corpus import SecurityCorpusGenerator


@pytest.fixture(scope="module")
def generator() -> NovaStackGenerator:
    """Instantiate and run NovaStack organizational and ground-truth generator."""
    gen = NovaStackGenerator(seed=RANDOM_SEED)
    gen.generate()
    return gen


@pytest.fixture(scope="module")
def full_corpus(generator: NovaStackGenerator) -> list[SourceRecord]:
    """Generate full 1,393 source-record corpus."""
    return generate_combined_corpus(
        generator,
        seed=RANDOM_SEED,
        include_noise=True,
        include_security=True,
        include_adversarial=True,
    )


@pytest.fixture(scope="module")
def security_fixtures(generator: NovaStackGenerator) -> list[SecurityFixture]:
    """Generate security fixtures."""
    sec_gen = SecurityCorpusGenerator(generator, seed=RANDOM_SEED)
    return sec_gen.generate_fixtures()


@pytest.fixture(scope="module")
def adversarial_fixtures(generator: NovaStackGenerator) -> list[AdversarialFixture]:
    """Generate adversarial fixtures."""
    adv_gen = AdversarialCorpusGenerator(generator, seed=RANDOM_SEED)
    return adv_gen.generate_fixtures()


@pytest.fixture(scope="module")
def evaluation_cases(
    generator: NovaStackGenerator,
    full_corpus: list[SourceRecord],
    security_fixtures: list[SecurityFixture],
    adversarial_fixtures: list[AdversarialFixture],
) -> list[EvaluationCase]:
    """Generate the formal evaluation dataset."""
    eval_gen = EvaluationDatasetGenerator(
        generator=generator,
        source_records=full_corpus,
        security_fixtures=security_fixtures,
        adversarial_fixtures=adversarial_fixtures,
        seed=RANDOM_SEED,
    )
    return eval_gen.generate_dataset()


# ==============================================================================
# 1. Scale and ID Uniqueness Tests
# ==============================================================================


class TestEvaluationDatasetScale:
    """Tests for dataset size and identifier format."""

    def test_evaluation_cases_total_count(
        self, evaluation_cases: list[EvaluationCase]
    ) -> None:
        """Verify exactly 120 evaluation cases are generated."""
        assert len(evaluation_cases) == 120

    def test_evaluation_case_ids_unique_and_formatted(
        self, evaluation_cases: list[EvaluationCase]
    ) -> None:
        """Verify each evaluation ID starts with EVAL- and is globally unique."""
        ids = [c.evaluation_id for c in evaluation_cases]
        assert len(ids) == len(set(ids)), "Duplicate evaluation IDs found"
        for eval_id in ids:
            assert eval_id.startswith("EVAL-"), f"Invalid ID format: {eval_id}"


# ==============================================================================
# 2. Taxonomy and Category Coverage Tests
# ==============================================================================


class TestTaxonomyAndCoverage:
    """Tests for category coverage and difficulty distribution."""

    def test_all_20_query_categories_covered(
        self, evaluation_cases: list[EvaluationCase]
    ) -> None:
        """Verify every category in QUERY_CATEGORIES is represented."""
        observed_categories = {c.query_category for c in evaluation_cases}
        assert len(observed_categories) == 20
        assert observed_categories == set(QUERY_CATEGORIES)

    def test_category_case_counts(
        self, evaluation_cases: list[EvaluationCase]
    ) -> None:
        """Verify reasonable distribution across categories (4 to 10 cases each)."""
        counts = Counter(c.query_category for c in evaluation_cases)
        for cat, cnt in counts.items():
            assert 4 <= cnt <= 10, f"Category '{cat}' has {cnt} cases, expected 4-10"

    def test_difficulty_distribution(
        self, evaluation_cases: list[EvaluationCase]
    ) -> None:
        """Verify difficulty tiers are easy, medium, hard and properly distributed."""
        diff_counts = Counter(c.difficulty for c in evaluation_cases)
        for diff in diff_counts:
            assert diff in EVALUATION_DIFFICULTIES

        assert diff_counts["easy"] >= 20, f"Expected >= 20 easy, got {diff_counts['easy']}"
        assert diff_counts["medium"] >= 25, f"Expected >= 25 medium, got {diff_counts['medium']}"
        assert diff_counts["hard"] >= 50, f"Expected >= 50 hard, got {diff_counts['hard']}"

    def test_expected_behaviors_valid(
        self, evaluation_cases: list[EvaluationCase]
    ) -> None:
        """Verify all expected behaviors conform to controlled vocabulary."""
        for c in evaluation_cases:
            assert c.expected_behavior in EVALUATION_BEHAVIORS, (
                f"{c.evaluation_id}: invalid behavior '{c.expected_behavior}'"
            )

    def test_expected_access_valid(
        self, evaluation_cases: list[EvaluationCase]
    ) -> None:
        """Verify expected_access is either allow, deny, or abstain."""
        valid_access = {"allow", "deny", "abstain"}
        for c in evaluation_cases:
            assert c.expected_access in valid_access, (
                f"{c.evaluation_id}: invalid access '{c.expected_access}'"
            )


# ==============================================================================
# 3. Document and Entity Referential Integrity Tests
# ==============================================================================


class TestReferentialIntegrity:
    """Tests verifying all referenced documents and entities exist."""

    def test_required_document_ids_exist(
        self,
        evaluation_cases: list[EvaluationCase],
        full_corpus: list[SourceRecord],
    ) -> None:
        """Verify every required_document_id exists in the source corpus."""
        valid_docs = {r.document_id for r in full_corpus}
        for c in evaluation_cases:
            for doc_id in c.required_document_ids:
                assert doc_id in valid_docs, (
                    f"{c.evaluation_id}: required doc '{doc_id}' not in corpus"
                )

    def test_acceptable_document_ids_exist(
        self,
        evaluation_cases: list[EvaluationCase],
        full_corpus: list[SourceRecord],
    ) -> None:
        """Verify every acceptable_document_id exists in the source corpus."""
        valid_docs = {r.document_id for r in full_corpus}
        for c in evaluation_cases:
            for doc_id in c.acceptable_document_ids:
                assert doc_id in valid_docs, (
                    f"{c.evaluation_id}: acceptable doc '{doc_id}' not in corpus"
                )

    def test_forbidden_document_ids_exist(
        self,
        evaluation_cases: list[EvaluationCase],
        full_corpus: list[SourceRecord],
    ) -> None:
        """Verify every forbidden_document_id exists in the source corpus."""
        valid_docs = {r.document_id for r in full_corpus}
        for c in evaluation_cases:
            for doc_id in c.forbidden_document_ids:
                assert doc_id in valid_docs, (
                    f"{c.evaluation_id}: forbidden doc '{doc_id}' not in corpus"
                )

    def test_expected_citation_document_ids_exist(
        self,
        evaluation_cases: list[EvaluationCase],
        full_corpus: list[SourceRecord],
    ) -> None:
        """Verify every citation document ID exists in the source corpus."""
        valid_docs = {r.document_id for r in full_corpus}
        for c in evaluation_cases:
            for doc_id in c.expected_citation_document_ids:
                assert doc_id in valid_docs, (
                    f"{c.evaluation_id}: citation doc '{doc_id}' not in corpus"
                )

    def test_expected_entity_ids_exist(
        self,
        evaluation_cases: list[EvaluationCase],
        generator: NovaStackGenerator,
    ) -> None:
        """Verify every expected_entity_id exists in ground-truth entities."""
        valid_entities = set()
        valid_entities.update(u.user_id for u in generator.users)
        valid_entities.update(t.team_id for t in generator.teams)
        valid_entities.update(s.service_id for s in generator.services)
        valid_entities.update(c.customer_id for c in generator.customers)
        valid_entities.update(e.event_id for e in generator.events)
        valid_entities.update(i.incident_id for i in generator.incidents)
        valid_entities.update(d.deployment_id for d in generator.deployments)
        valid_entities.update(p.pull_request_id for p in generator.pull_requests)

        for c in evaluation_cases:
            for ent_id in c.expected_entity_ids:
                assert ent_id in valid_entities, (
                    f"{c.evaluation_id}: entity '{ent_id}' not in ground truth"
                )


# ==============================================================================
# 4. Fixture Linkage Tests
# ==============================================================================


class TestFixtureLinkages:
    """Tests verifying linkages to SecurityFixture and AdversarialFixture."""

    def test_security_fixture_linkages(
        self,
        evaluation_cases: list[EvaluationCase],
        security_fixtures: list[SecurityFixture],
    ) -> None:
        """Verify security-related evaluation cases link to valid security fixtures."""
        sec_ids = {f.fixture_id for f in security_fixtures}
        linked_cases = [c for c in evaluation_cases if c.security_fixture_id]
        assert len(linked_cases) == 22, f"Expected 22 linked security cases, got {len(linked_cases)}"
        for c in linked_cases:
            assert c.security_fixture_id in sec_ids, (
                f"{c.evaluation_id}: invalid security_fixture_id '{c.security_fixture_id}'"
            )

    def test_adversarial_fixture_linkages(
        self,
        evaluation_cases: list[EvaluationCase],
        adversarial_fixtures: list[AdversarialFixture],
    ) -> None:
        """Verify adversarial evaluation cases link to valid adversarial fixtures."""
        adv_ids = {f.fixture_id for f in adversarial_fixtures}
        linked_cases = [c for c in evaluation_cases if c.adversarial_fixture_id]
        assert len(linked_cases) == 15, f"Expected 15 linked adversarial cases, got {len(linked_cases)}"
        for c in linked_cases:
            assert c.adversarial_fixture_id in adv_ids, (
                f"{c.evaluation_id}: invalid adversarial_fixture_id '{c.adversarial_fixture_id}'"
            )

    def test_adversarial_cases_forbid_poisoned_docs(
        self, evaluation_cases: list[EvaluationCase]
    ) -> None:
        """Verify poisoning and citation manipulation cases forbid deceptive documents."""
        poison_cases = [
            c for c in evaluation_cases
            if c.query_category in {"retrieval_poisoning", "citation_manipulation"}
        ]
        assert len(poison_cases) == 10
        for c in poison_cases:
            assert len(c.forbidden_document_ids) > 0, (
                f"{c.evaluation_id}: poisoning/citation manipulation case must specify forbidden document"
            )


# ==============================================================================
# 5. Missing Information & Abstention Tests
# ==============================================================================


class TestMissingInformationAndAbstention:
    """Tests verifying behavior on unanswerable and missing information queries."""

    def test_missing_information_abstention(
        self, evaluation_cases: list[EvaluationCase]
    ) -> None:
        """Verify missing information cases abstain and do not require documents."""
        missing_cases = [c for c in evaluation_cases if c.query_category == "missing_information"]
        assert len(missing_cases) == 7
        for c in missing_cases:
            assert c.expected_behavior == "abstain_insufficient_evidence", (
                f"{c.evaluation_id}: expected abstain_insufficient_evidence, got {c.expected_behavior}"
            )
            assert len(c.required_document_ids) == 0, (
                f"{c.evaluation_id}: missing information query cannot require documents"
            )
            assert len(c.expected_citation_document_ids) == 0

    def test_answer_facts_always_present(
        self, evaluation_cases: list[EvaluationCase]
    ) -> None:
        """Verify every single evaluation case specifies structured expected facts."""
        for c in evaluation_cases:
            assert len(c.expected_answer_facts) > 0, (
                f"{c.evaluation_id}: expected_answer_facts is empty"
            )
            for fact in c.expected_answer_facts:
                assert isinstance(fact, str) and len(fact.strip()) > 0


# ==============================================================================
# 6. Validation Function, Determinism, and Immutability Tests
# ==============================================================================


class TestValidationDeterminismImmutability:
    """Tests for the validation suite, determinism, and ground-truth immutability."""

    def test_validate_evaluation_cases_returns_zero_errors(
        self,
        evaluation_cases: list[EvaluationCase],
        full_corpus: list[SourceRecord],
        generator: NovaStackGenerator,
        security_fixtures: list[SecurityFixture],
        adversarial_fixtures: list[AdversarialFixture],
    ) -> None:
        """Verify validate_evaluation_cases() finds 0 errors in generated cases."""
        errors = validate_evaluation_cases(
            evaluation_cases,
            full_corpus,
            generator,
            security_fixtures,
            adversarial_fixtures,
        )
        assert len(errors) == 0, f"Validation errors: {errors}"

    def test_dataset_generation_is_deterministic(
        self,
        generator: NovaStackGenerator,
        full_corpus: list[SourceRecord],
        security_fixtures: list[SecurityFixture],
        adversarial_fixtures: list[AdversarialFixture],
    ) -> None:
        """Verify running generator twice with the same seed produces identical results."""
        gen1 = EvaluationDatasetGenerator(
            generator, full_corpus, security_fixtures, adversarial_fixtures, seed=RANDOM_SEED
        )
        cases1 = gen1.generate_dataset()

        gen2 = EvaluationDatasetGenerator(
            generator, full_corpus, security_fixtures, adversarial_fixtures, seed=RANDOM_SEED
        )
        cases2 = gen2.generate_dataset()

        assert len(cases1) == len(cases2)
        for c1, c2 in zip(cases1, cases2):
            assert c1.evaluation_id == c2.evaluation_id
            assert c1.query == c2.query
            assert c1.query_category == c2.query_category
            assert c1.difficulty == c2.difficulty
            assert c1.expected_answer_facts == c2.expected_answer_facts
            assert c1.required_document_ids == c2.required_document_ids
            assert c1.forbidden_document_ids == c2.forbidden_document_ids

    def test_ground_truth_layer_immutability(
        self, generator: NovaStackGenerator
    ) -> None:
        """Verify canonical ground-truth event counts are completely unchanged."""
        assert len(generator.events) == 12
        assert len(generator.incidents) == 12
        assert len(generator.deployments) == 9
        assert len(generator.pull_requests) == 11
        assert len(generator.event_relationships) == 92
        assert len(generator.users) == 100
        assert len(generator.teams) == 15
        assert len(generator.customers) == 75
        assert len(generator.services) == 15


# ==============================================================================
# 7. Serialized Files Tests
# ==============================================================================


class TestSerializedEvaluationFiles:
    """Tests verifying written JSON files on disk."""

    def test_evaluation_cases_json_file(self) -> None:
        """Verify data/evaluation/novastack/evaluation_cases.json matches specification."""
        file_path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "evaluation"
            / "novastack"
            / "evaluation_cases.json"
        )
        assert file_path.exists(), f"File {file_path} does not exist"

        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["version"] == DATASET_VERSION
        assert data["seed"] == RANDOM_SEED
        assert data["count"] == 120
        assert len(data["evaluation_cases"]) == 120

        sample = data["evaluation_cases"][0]
        assert "evaluation_id" in sample
        assert "query" in sample
        assert "query_category" in sample
        assert "difficulty" in sample
        assert "expected_answer_facts" in sample
        assert "required_document_ids" in sample
        assert "acceptable_document_ids" in sample
        assert "forbidden_document_ids" in sample

    def test_evaluation_taxonomy_json_file(self) -> None:
        """Verify data/evaluation/novastack/evaluation_taxonomy.json contains full taxonomy."""
        file_path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "evaluation"
            / "novastack"
            / "evaluation_taxonomy.json"
        )
        assert file_path.exists(), f"File {file_path} does not exist"

        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["version"] == DATASET_VERSION
        assert len(data["query_categories"]) == 20
        assert "difficulty_tiers" in data
        assert "expected_behaviors" in data
        assert "evaluation_metrics" in data
        assert "retrieval" in data["evaluation_metrics"]
        assert "generation" in data["evaluation_metrics"]
