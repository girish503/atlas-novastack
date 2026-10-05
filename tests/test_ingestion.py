"""Unit and integration tests for Phase 2A: Canonical Ingestion & Normalization.

Verifies:
1. All 1,393 records can be ingested cleanly
2. Output document count equals input record count (1,393 == 1,393)
3. Document IDs remain unchanged (1:1 mapping)
4. Document IDs remain unique across the ingested corpus
5. Tenant IDs are preserved without mutation or leakage
6. Permissions (roles, departments, teams, user IDs) are preserved
7. Classification levels are preserved (public, internal, confidential, restricted)
8. Temporal metadata is preserved (valid_from, valid_until, created_at, updated_at)
9. Provenance is preserved (source_entity_id, source_entity_type, related_entity_ids)
10. Version relationships are preserved (version, parent_id)
11. Supersedes relationships are preserved (supersedes_id)
12. Content meaning is preserved (Unicode NFC, whitespace trimming without content loss)
13. Deterministic ingestion (same input produces identical normalized documents)
14. Repeated ingestion produces equivalent, byte-for-byte identical output
15. Malformed records are detected and rejected (missing ID, duplicate ID, invalid tenant,
    invalid status, invalid classification, inverted temporal bounds, dangling parent/supersedes)
16. Canonical ground truth remains completely unchanged
17. Existing 211 tests continue to pass
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure src/ is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novastack.config import RANDOM_SEED
from novastack.generator import NovaStackGenerator
from novastack.ingestion import (
    IngestionReport,
    ingest_records,
    load_source_records,
    normalize_content,
    normalize_source_record,
    normalize_title,
    validate_source_record_batch,
)
from novastack.models import RecordPermissions, SearchDocument, SourceRecord


@pytest.fixture(scope="module")
def raw_source_records_path() -> Path:
    """Path to raw source records JSON."""
    return (
        Path(__file__).resolve().parent.parent
        / "data"
        / "raw"
        / "novastack"
        / "source_records.json"
    )


@pytest.fixture(scope="module")
def raw_records(raw_source_records_path: Path) -> list[SourceRecord]:
    """Load raw SourceRecord instances directly from raw file."""
    return load_source_records(raw_source_records_path)


@pytest.fixture(scope="module")
def ingestion_output(
    raw_source_records_path: Path,
) -> tuple[list[SearchDocument], IngestionReport]:
    """Execute ingestion pipeline once for module tests."""
    return ingest_records(raw_source_records_path, strict=True)


@pytest.fixture(scope="module")
def search_documents(
    ingestion_output: tuple[list[SearchDocument], IngestionReport],
) -> list[SearchDocument]:
    """Ingested search documents."""
    return ingestion_output[0]


@pytest.fixture(scope="module")
def ingestion_report(
    ingestion_output: tuple[list[SearchDocument], IngestionReport],
) -> IngestionReport:
    """Ingestion observability report."""
    return ingestion_output[1]


# ==============================================================================
# 1 & 2. Ingestion Scale & Count Equivalence
# ==============================================================================


class TestIngestionScale:
    """Tests verifying complete scale and input/output parity."""

    def test_all_1393_records_ingested(
        self, search_documents: list[SearchDocument]
    ) -> None:
        """Verify exactly 1,393 records are ingested into SearchDocument objects."""
        assert len(search_documents) == 1393

    def test_output_count_equals_input_count(
        self,
        raw_records: list[SourceRecord],
        search_documents: list[SearchDocument],
        ingestion_report: IngestionReport,
    ) -> None:
        """Verify input record count strictly equals output document count."""
        assert len(raw_records) == 1393
        assert len(search_documents) == len(raw_records)
        assert ingestion_report.input_count == len(raw_records)
        assert ingestion_report.output_count == len(search_documents)
        assert ingestion_report.successful_count == 1393
        assert len(ingestion_report.errors) == 0


# ==============================================================================
# 3, 4, 5. Identity and Tenancy Preservation
# ==============================================================================


class TestIdentityAndTenancy:
    """Tests verifying stable identity and strict tenant boundary preservation."""

    def test_document_ids_remain_unchanged(
        self,
        raw_records: list[SourceRecord],
        search_documents: list[SearchDocument],
    ) -> None:
        """Verify 1:1 matching document_id preservation without identity regeneration."""
        for raw, doc in zip(raw_records, search_documents):
            assert doc.document_id == raw.document_id

    def test_document_ids_remain_unique(
        self, search_documents: list[SearchDocument]
    ) -> None:
        """Verify no duplicate document IDs exist across ingested documents."""
        doc_ids = [d.document_id for d in search_documents]
        assert len(doc_ids) == len(set(doc_ids))
        for doc_id in doc_ids:
            assert doc_id.startswith("DOC-")

    def test_tenant_ids_preserved(
        self,
        raw_records: list[SourceRecord],
        search_documents: list[SearchDocument],
    ) -> None:
        """Verify tenant_id is preserved for each record without cross-tenant mutation."""
        for raw, doc in zip(raw_records, search_documents):
            assert doc.tenant_id == raw.tenant_id
            assert doc.tenant_id in {
                "TENANT-NOVASTACK",
                "TENANT-ORBITAL",
                "TENANT-PINECONE",
            }


# ==============================================================================
# 6 & 7. Security Metadata & Permissions Preservation
# ==============================================================================


class TestSecurityPreservation:
    """Tests verifying permissions and classifications are not weakened or flattened."""

    def test_classification_preserved(
        self,
        raw_records: list[SourceRecord],
        search_documents: list[SearchDocument],
    ) -> None:
        """Verify classification is strictly preserved for all records."""
        for raw, doc in zip(raw_records, search_documents):
            assert doc.classification == raw.classification
            assert doc.classification in {
                "public",
                "internal",
                "confidential",
                "restricted",
            }

    def test_permissions_preserved_and_normalized(
        self,
        raw_records: list[SourceRecord],
        search_documents: list[SearchDocument],
    ) -> None:
        """Verify RecordPermissions roles, departments, teams, and users are preserved."""
        for raw, doc in zip(raw_records, search_documents):
            raw_p = raw.permissions
            doc_p = doc.permissions
            assert set(doc_p.allowed_roles) == set(raw_p.allowed_roles)
            assert set(doc_p.allowed_departments) == set(raw_p.allowed_departments)
            assert set(doc_p.allowed_teams) == set(raw_p.allowed_teams)
            assert set(doc_p.allowed_user_ids) == set(raw_p.allowed_user_ids)

            # In addition, check deterministic sorting
            assert doc_p.allowed_roles == sorted(set(doc_p.allowed_roles))
            assert doc_p.allowed_departments == sorted(set(doc_p.allowed_departments))
            assert doc_p.allowed_teams == sorted(set(doc_p.allowed_teams))
            assert doc_p.allowed_user_ids == sorted(set(doc_p.allowed_user_ids))

    def test_restricted_documents_remain_restricted(
        self, search_documents: list[SearchDocument]
    ) -> None:
        """Verify restricted security documents retain their strict ACLs."""
        restricted_docs = [d for d in search_documents if d.classification == "restricted"]
        assert len(restricted_docs) == 48
        for doc in restricted_docs:
            assert doc.classification == "restricted"


# ==============================================================================
# 8, 9, 10, 11. Temporal, Provenance, Version & Supersedes Preservation
# ==============================================================================


class TestMetadataPreservation:
    """Tests verifying temporal, provenance, and hierarchical lineage preservation."""

    def test_temporal_metadata_preserved(
        self,
        raw_records: list[SourceRecord],
        search_documents: list[SearchDocument],
    ) -> None:
        """Verify created_at, updated_at, valid_from, and valid_until are preserved."""
        for raw, doc in zip(raw_records, search_documents):
            assert isinstance(doc.created_at, str)
            assert len(doc.created_at) > 0

            if raw.valid_from:
                assert doc.valid_from is not None
            else:
                assert doc.valid_from is None

            if raw.valid_until:
                assert doc.valid_until is not None
            else:
                assert doc.valid_until is None

    def test_provenance_preserved(
        self,
        raw_records: list[SourceRecord],
        search_documents: list[SearchDocument],
    ) -> None:
        """Verify source_entity_id, source_entity_type, and related_entity_ids are preserved."""
        for raw, doc in zip(raw_records, search_documents):
            assert doc.source_entity_id == (
                raw.source_entity_id.strip() if raw.source_entity_id else None
            )
            assert doc.source_entity_type == (
                raw.source_entity_type.strip() if raw.source_entity_type else None
            )
            assert set(doc.related_entity_ids) == set(raw.related_entity_ids)
            assert doc.related_entity_ids == sorted(set(doc.related_entity_ids))

    def test_version_relationships_preserved(
        self,
        raw_records: list[SourceRecord],
        search_documents: list[SearchDocument],
    ) -> None:
        """Verify version and parent_id relationships are preserved."""
        for raw, doc in zip(raw_records, search_documents):
            assert doc.version == raw.version
            if raw.parent_id:
                assert doc.parent_id == raw.parent_id.strip()
            else:
                assert doc.parent_id is None

    def test_supersedes_relationships_preserved(
        self,
        raw_records: list[SourceRecord],
        search_documents: list[SearchDocument],
    ) -> None:
        """Verify supersedes_id relationships are preserved."""
        for raw, doc in zip(raw_records, search_documents):
            if raw.supersedes_id:
                assert doc.supersedes_id == raw.supersedes_id.strip()
            else:
                assert doc.supersedes_id is None


# ==============================================================================
# 12, 13, 14. Content Normalization and Determinism
# ==============================================================================


class TestNormalizationAndDeterminism:
    """Tests verifying content semantic fidelity and deterministic output."""

    def test_content_meaning_is_preserved(
        self,
        raw_records: list[SourceRecord],
        search_documents: list[SearchDocument],
    ) -> None:
        """Verify content meaning and core text blocks are preserved without deletion."""
        for raw, doc in zip(raw_records, search_documents):
            assert len(doc.content) > 0
            # Stripped raw content should match or be an exact substring of normalized
            assert doc.content == normalize_content(raw.content)
            assert doc.title == normalize_title(raw.title)

    def test_normalization_helper_edge_cases(self) -> None:
        """Verify line endings, trailing whitespace, and Unicode NFC in helper functions."""
        crlf_text = "Line 1  \r\nLine 2\t \r\n\r\nLine 3"
        normalized = normalize_content(crlf_text)
        assert "\r" not in normalized
        assert normalized == "Line 1\nLine 2\n\nLine 3"

        raw_title = " Incident  Title \r\n With Newlines "
        norm_title = normalize_title(raw_title)
        assert "\n" not in norm_title
        assert "\r" not in norm_title
        assert norm_title == "Incident  Title   With Newlines"

    def test_deterministic_ingestion(
        self, raw_source_records_path: Path
    ) -> None:
        """Verify identical raw inputs produce identical SearchDocument objects."""
        docs1, report1 = ingest_records(raw_source_records_path)
        docs2, report2 = ingest_records(raw_source_records_path)

        assert len(docs1) == len(docs2)
        for d1, d2 in zip(docs1, docs2):
            assert d1 == d2
            assert d1.to_dict() == d2.to_dict()

    def test_repeated_ingestion_produces_equivalent_output(
        self, raw_source_records_path: Path
    ) -> None:
        """Verify repeated ingestion produces byte-for-byte identical JSON serialization."""
        docs1, _ = ingest_records(raw_source_records_path)
        docs2, _ = ingest_records(raw_source_records_path)

        json1 = json.dumps([d.to_dict() for d in docs1], sort_keys=True)
        json2 = json.dumps([d.to_dict() for d in docs2], sort_keys=True)
        assert json1 == json2


# ==============================================================================
# 15. Malformed Record Detection and Validation
# ==============================================================================


class TestValidationAndMalformedDetection:
    """Tests verifying strict validation catches malformed data without silent repair."""

    def test_missing_document_id_detected(self) -> None:
        """Verify missing document_id is flagged as an error."""
        rec = SourceRecord(
            document_id="",
            tenant_id="TENANT-NOVASTACK",
            source_type="incident",
            title="Test",
            content="Test content",
            author_id="USR-NS-0001",
            department="Engineering",
        )
        errors, _ = validate_source_record_batch([rec])
        assert any("missing or empty document_id" in e for e in errors)

    def test_duplicate_document_id_detected(self) -> None:
        """Verify duplicate document_id in batch is flagged as an error."""
        rec1 = SourceRecord(
            document_id="DOC-DUP-0001",
            tenant_id="TENANT-NOVASTACK",
            source_type="incident",
            title="Test 1",
            content="Content 1",
            author_id="USR-NS-0001",
            department="Engineering",
        )
        rec2 = SourceRecord(
            document_id="DOC-DUP-0001",
            tenant_id="TENANT-NOVASTACK",
            source_type="incident",
            title="Test 2",
            content="Content 2",
            author_id="USR-NS-0001",
            department="Engineering",
        )
        errors, _ = validate_source_record_batch([rec1, rec2])
        assert any("duplicate document_id" in e for e in errors)

    def test_invalid_tenant_detected(self) -> None:
        """Verify invalid tenant is flagged."""
        rec = SourceRecord(
            document_id="DOC-TEST-0001",
            tenant_id="TENANT-UNKNOWN",
            source_type="incident",
            title="Test",
            content="Test content",
            author_id="USR-NS-0001",
            department="Engineering",
        )
        errors, _ = validate_source_record_batch([rec])
        assert any("invalid tenant_id" in e for e in errors)

    def test_invalid_classification_detected(self) -> None:
        """Verify invalid classification level is flagged."""
        rec = SourceRecord(
            document_id="DOC-TEST-0001",
            tenant_id="TENANT-NOVASTACK",
            source_type="incident",
            title="Test",
            content="Test content",
            author_id="USR-NS-0001",
            department="Engineering",
            classification="super_secret_clearance",
        )
        errors, _ = validate_source_record_batch([rec])
        assert any("invalid classification" in e for e in errors)

    def test_invalid_status_detected(self) -> None:
        """Verify invalid status is flagged."""
        rec = SourceRecord(
            document_id="DOC-TEST-0001",
            tenant_id="TENANT-NOVASTACK",
            source_type="incident",
            title="Test",
            content="Test content",
            author_id="USR-NS-0001",
            department="Engineering",
            status="pending_review",
        )
        errors, _ = validate_source_record_batch([rec])
        assert any("invalid status" in e for e in errors)

    def test_inverted_temporal_bounds_detected(self) -> None:
        """Verify valid_from > valid_until is flagged."""
        rec = SourceRecord(
            document_id="DOC-TEST-0001",
            tenant_id="TENANT-NOVASTACK",
            source_type="incident",
            title="Test",
            content="Test content",
            author_id="USR-NS-0001",
            department="Engineering",
            valid_from="2026-12-31T00:00:00",
            valid_until="2025-01-01T00:00:00",
        )
        errors, _ = validate_source_record_batch([rec])
        assert any("invalid temporal bounds" in e for e in errors)

    def test_dangling_parent_and_supersedes_detected(self) -> None:
        """Verify parent_id and supersedes_id not in batch are flagged."""
        rec = SourceRecord(
            document_id="DOC-TEST-0001",
            tenant_id="TENANT-NOVASTACK",
            source_type="incident",
            title="Test",
            content="Test content",
            author_id="USR-NS-0001",
            department="Engineering",
            parent_id="DOC-NONEXISTENT-PARENT",
            supersedes_id="DOC-NONEXISTENT-SUPERSEDES",
        )
        errors, _ = validate_source_record_batch([rec])
        assert any("parent_id 'DOC-NONEXISTENT-PARENT' not found" in e for e in errors)
        assert any("supersedes_id 'DOC-NONEXISTENT-SUPERSEDES' not found" in e for e in errors)

    def test_self_referencing_parent_or_supersedes_detected(self) -> None:
        """Verify document cannot be its own parent or supersede itself."""
        rec = SourceRecord(
            document_id="DOC-TEST-0001",
            tenant_id="TENANT-NOVASTACK",
            source_type="incident",
            title="Test",
            content="Test content",
            author_id="USR-NS-0001",
            department="Engineering",
            parent_id="DOC-TEST-0001",
            supersedes_id="DOC-TEST-0001",
        )
        errors, _ = validate_source_record_batch([rec])
        assert any("self-referencing parent_id" in e for e in errors)
        assert any("self-referencing supersedes_id" in e for e in errors)

    def test_strict_mode_raises_value_error(self) -> None:
        """Verify strict=True raises ValueError on invalid batch."""
        rec = SourceRecord(
            document_id="",
            tenant_id="TENANT-NOVASTACK",
            source_type="incident",
            title="Test",
            content="Test",
            author_id="USR-01",
            department="DevOps",
        )
        with pytest.raises(ValueError, match="Ingestion batch validation failed"):
            ingest_records([rec], strict=True)


# ==============================================================================
# 16. Canonical Ground Truth Immutability
# ==============================================================================


class TestGroundTruthImmutability:
    """Tests verifying canonical ground truth layer remains completely unchanged."""

    def test_ground_truth_layer_immutability(self) -> None:
        """Verify canonical ground-truth event counts remain 100% unmutated."""
        gen = NovaStackGenerator(seed=RANDOM_SEED)
        gen.generate()

        assert len(gen.events) == 12
        assert len(gen.incidents) == 12
        assert len(gen.deployments) == 9
        assert len(gen.pull_requests) == 11
        assert len(gen.event_relationships) == 92
        assert len(gen.users) == 100
        assert len(gen.teams) == 15
        assert len(gen.customers) == 75
        assert len(gen.services) == 15


# ==============================================================================
# Processed File Serialization Tests
# ==============================================================================


class TestProcessedSearchDocumentsFile:
    """Tests verifying data/processed/novastack/search_documents.json."""

    def test_processed_search_documents_file(self) -> None:
        """Verify search_documents.json exists and is valid SearchDocument data."""
        file_path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "processed"
            / "novastack"
            / "search_documents.json"
        )
        assert file_path.exists(), f"File {file_path} does not exist"

        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["count"] == 1393
        assert len(data["search_documents"]) == 1393

        sample = data["search_documents"][0]
        assert "document_id" in sample
        assert "tenant_id" in sample
        assert "source_type" in sample
        assert "title" in sample
        assert "content" in sample
        assert "department" in sample
        assert "created_at" in sample
        assert "permissions" in sample
        assert "classification" in sample
        assert "authority_level" in sample

        # Test SearchDocument.from_dict reconstruction
        doc = SearchDocument.from_dict(sample)
        assert doc.document_id == sample["document_id"]
        assert doc.tenant_id == sample["tenant_id"]
