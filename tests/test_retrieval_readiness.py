"""Comprehensive test suite for Phase 2C — Retrieval Index Readiness & Baseline Audit.

Tests cover:
1. all 1,663 chunks load successfully
2. every chunk has a unique chunk_id
3. every chunk references an existing document_id
4. no empty chunk text
5. valid chunk indexes
6. total_chunks consistency
7. tenant preservation
8. classification preservation
9. permissions preservation
10. temporal metadata preservation
11. lifecycle/version preservation
12. provenance preservation
13. duplicate-text detection works
14. evaluation expected documents can be resolved (100% resolvability)
15. deterministic audit output
16. retrieval index contract validation
17. raw source_records.json unchanged
18. search_documents.json unchanged
19. search_chunks.json unchanged
20. canonical ground truth unchanged
21. suspicious chunk length thresholds (<100c, >800c)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

# Ensure src/ is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novastack.models import RecordPermissions, SearchChunk, SearchDocument
from novastack.retrieval_readiness import (
    RetrievalIndexContract,
    audit_retrieval_readiness,
    validate_retrieval_contract,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RAW_PATH = _PROJECT_ROOT / "data" / "raw" / "novastack" / "source_records.json"
_DOCS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_documents.json"
_CHUNKS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_chunks.json"
_EVAL_PATH = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
_GROUND_TRUTH_DIR = _PROJECT_ROOT / "data" / "ground_truth" / "novastack"


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def audit_report():
    return audit_retrieval_readiness(
        chunks=_CHUNKS_PATH,
        documents=_DOCS_PATH,
        evaluation_cases=_EVAL_PATH,
    )


@pytest.fixture(scope="module")
def chunks_data():
    with open(_CHUNKS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["search_chunks"]


@pytest.fixture(scope="module")
def docs_data():
    with open(_DOCS_PATH, "r", encoding="utf-8") as f:
        return {d["document_id"]: d for d in json.load(f)["search_documents"]}


# 1. All 1,663 chunks load successfully
def test_all_1663_chunks_load_successfully(audit_report, chunks_data):
    assert len(chunks_data) == 1663
    assert audit_report.total_chunks == 1663


# 2. Every chunk has a unique chunk_id
def test_every_chunk_has_unique_chunk_id(audit_report, chunks_data):
    assert audit_report.duplicate_chunk_id_count == 0
    ids = [c["chunk_id"] for c in chunks_data]
    assert len(ids) == len(set(ids))


# 3. Every chunk references an existing document_id
def test_every_chunk_references_existing_document_id(audit_report, chunks_data, docs_data):
    assert audit_report.orphaned_document_reference_count == 0
    for c in chunks_data:
        assert c["document_id"] in docs_data


# 4. No empty chunk text
def test_no_empty_chunk_text(audit_report, chunks_data):
    assert audit_report.empty_text_count == 0
    for c in chunks_data:
        assert c["text"] and c["text"].strip()
        assert c["char_count"] > 0


# 5. Valid chunk indexes
def test_valid_chunk_indexes(chunks_data):
    by_doc = {}
    for c in chunks_data:
        by_doc.setdefault(c["document_id"], []).append(c)

    for doc_id, c_list in by_doc.items():
        total = len(c_list)
        indexes = [c["chunk_index"] for c in c_list]
        assert sorted(indexes) == list(range(total))
        for c in c_list:
            assert c["total_chunks"] == total
            assert 0 <= c["chunk_index"] < total


# 6. Total chunks consistency
def test_total_chunks_consistency(audit_report, chunks_data):
    by_doc = {}
    for c in chunks_data:
        by_doc.setdefault(c["document_id"], []).append(c)

    for doc_id, c_list in by_doc.items():
        assert all(c["total_chunks"] == len(c_list) for c in c_list)


# 7. Tenant preservation
def test_tenant_preservation(chunks_data, docs_data):
    for c in chunks_data:
        parent = docs_data[c["document_id"]]
        assert c["tenant_id"] == parent["tenant_id"]


# 8. Classification preservation
def test_classification_preservation(chunks_data, docs_data):
    for c in chunks_data:
        parent = docs_data[c["document_id"]]
        assert c["classification"] == parent["classification"]


# 9. Permissions preservation
def test_permissions_preservation(chunks_data, docs_data):
    for c in chunks_data:
        p_c = c["permissions"]
        p_doc = docs_data[c["document_id"]]["permissions"]
        assert p_c["allowed_roles"] == p_doc["allowed_roles"]
        assert p_c["allowed_departments"] == p_doc["allowed_departments"]
        assert p_c["allowed_teams"] == p_doc["allowed_teams"]
        assert p_c["allowed_user_ids"] == p_doc["allowed_user_ids"]


# 10. Temporal metadata preservation
def test_temporal_metadata_preservation(chunks_data, docs_data):
    for c in chunks_data:
        parent = docs_data[c["document_id"]]
        assert c["created_at"] == parent["created_at"]
        assert c["updated_at"] == parent["updated_at"]
        assert c["valid_from"] == parent["valid_from"]
        assert c["valid_until"] == parent["valid_until"]


# 11. Lifecycle and version preservation
def test_lifecycle_and_version_preservation(chunks_data, docs_data):
    for c in chunks_data:
        parent = docs_data[c["document_id"]]
        assert c["status"] == parent["status"]
        assert c["version"] == parent["version"]
        assert c["authority_level"] == parent["authority_level"]
        assert c["source_type"] == parent["source_type"]


# 12. Provenance preservation
def test_provenance_preservation(chunks_data, docs_data):
    for c in chunks_data:
        parent = docs_data[c["document_id"]]
        assert c["source_entity_id"] == parent["source_entity_id"]
        assert c["source_entity_type"] == parent["source_entity_type"]
        assert c["related_entity_ids"] == parent["related_entity_ids"]
        assert c["parent_id"] == parent["parent_id"]
        assert c["supersedes_id"] == parent["supersedes_id"]


# 13. Duplicate text detection works
def test_duplicate_text_detection(audit_report):
    # Natural duplicates from Milestone 4C noise & versioning must be accurately tracked
    assert audit_report.duplicate_text_group_count > 0
    assert audit_report.duplicate_text_chunk_count > 0


# 14. Evaluation expected documents can be resolved
def test_evaluation_expected_documents_resolvability(audit_report):
    assert audit_report.evaluation_cases_total == 120
    assert audit_report.evaluation_expected_docs_total > 0
    assert audit_report.evaluation_expected_docs_resolved == audit_report.evaluation_expected_docs_total
    assert audit_report.evaluation_expected_docs_resolvability_rate == 1.0
    assert len(audit_report.evaluation_unresolvable_doc_ids) == 0


# 15. Deterministic audit output
def test_deterministic_audit_output():
    report_1 = audit_retrieval_readiness(
        chunks=_CHUNKS_PATH,
        documents=_DOCS_PATH,
        evaluation_cases=_EVAL_PATH,
    )
    report_2 = audit_retrieval_readiness(
        chunks=_CHUNKS_PATH,
        documents=_DOCS_PATH,
        evaluation_cases=_EVAL_PATH,
    )
    assert json.dumps(report_1.to_dict(), sort_keys=True) == json.dumps(report_2.to_dict(), sort_keys=True)


# 16. Retrieval index contract validation
def test_retrieval_contract_validation(chunks_data):
    # Test valid chunk from corpus
    errs = validate_retrieval_contract(chunks_data[0])
    assert len(errs) == 0

    # Test malformed chunk
    bad_chunk = dict(chunks_data[0])
    bad_chunk["title"] = ""
    bad_chunk["classification"] = "invalid_classification"
    bad_chunk["chunk_id"] = "CORRUPT-PATTERN"
    bad_errs = validate_retrieval_contract(bad_chunk)
    assert len(bad_errs) >= 3
    assert any("title" in e for e in bad_errs)
    assert any("classification" in e for e in bad_errs)
    assert any("pattern" in e for e in bad_errs)


# 17. Raw source_records.json unchanged
def test_raw_source_records_unchanged():
    hash_before = _hash_file(_RAW_PATH)
    _ = audit_retrieval_readiness(chunks=_CHUNKS_PATH, documents=_DOCS_PATH)
    assert hash_before == _hash_file(_RAW_PATH)


# 18. Search documents unchanged
def test_search_documents_unchanged():
    hash_before = _hash_file(_DOCS_PATH)
    _ = audit_retrieval_readiness(chunks=_CHUNKS_PATH, documents=_DOCS_PATH)
    assert hash_before == _hash_file(_DOCS_PATH)


# 19. Search chunks unchanged
def test_search_chunks_unchanged():
    hash_before = _hash_file(_CHUNKS_PATH)
    _ = audit_retrieval_readiness(chunks=_CHUNKS_PATH, documents=_DOCS_PATH)
    assert hash_before == _hash_file(_CHUNKS_PATH)


# 20. Canonical ground truth unchanged
def test_canonical_ground_truth_unchanged():
    for f in _GROUND_TRUTH_DIR.glob("*.json"):
        hash_before = _hash_file(f)
        _ = audit_retrieval_readiness(chunks=_CHUNKS_PATH, documents=_DOCS_PATH)
        assert hash_before == _hash_file(f)


# 21. Suspicious chunk length thresholds
def test_suspicious_chunk_length_thresholds(audit_report):
    # Chunks are expected to be between min_chunk_size=100 and max_chunk_size=800
    assert audit_report.suspiciously_short_chunk_count == 0
    assert audit_report.suspiciously_large_chunk_count == 0
    assert audit_report.chunk_length_min >= 100
    assert audit_report.chunk_length_max <= 800
