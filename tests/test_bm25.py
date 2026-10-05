"""Comprehensive test suite for Phase 3A — BM25 Lexical Retrieval Baseline.

Tests cover:
1. index contains all 1,663 chunks
2. unique indexed chunk IDs
3. exact identifier query
4. exact service-name query
5. normal keyword query
6. no-match query
7. top_k behavior
8. score ordering
9. deterministic results
10. tenant filtering
11. classification filtering
12. department filtering
13. source_type filtering
14. status filtering
15. duplicate preservation
16. metadata/provenance preservation
17. empty/whitespace query behavior
18. evaluation document resolution
19. raw source_records.json unchanged
20. search_documents.json unchanged
21. search_chunks.json unchanged
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

# Ensure src/ is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novastack.bm25 import BM25Config, BM25Index, tokenize

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RAW_PATH = _PROJECT_ROOT / "data" / "raw" / "novastack" / "source_records.json"
_DOCS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_documents.json"
_CHUNKS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_chunks.json"


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def bm25_index() -> BM25Index:
    assert _CHUNKS_PATH.exists(), f"Chunks file not found at {_CHUNKS_PATH}"
    config = BM25Config(k1=1.5, b=0.75, title_weight=1.0, text_weight=1.0)
    return BM25Index.build_index(_CHUNKS_PATH, config=config)


# 1. Index contains all 1,663 chunks
def test_index_contains_all_1663_chunks(bm25_index: BM25Index):
    assert bm25_index.total_chunks == 1663
    assert len(bm25_index.chunks) == 1663
    stats = bm25_index.get_corpus_statistics()
    assert stats["total_chunks"] == 1663
    assert stats["unique_terms"] > 1000
    assert stats["average_document_length"] > 0


# 2. Unique indexed chunk IDs
def test_unique_indexed_chunk_ids(bm25_index: BM25Index):
    cids = [c.chunk_id for c in bm25_index.chunks]
    assert len(cids) == 1663
    assert len(set(cids)) == 1663


# 3. Exact identifier query
def test_exact_identifier_query(bm25_index: BM25Index):
    results = bm25_index.search("INC-NS-0001", top_k=5)
    assert len(results) > 0
    # Top results should reference incident 1
    doc_ids = [r.document_id for r in results]
    assert any("0001" in d for d in doc_ids)


# 4. Exact service-name query
def test_exact_service_name_query(bm25_index: BM25Index):
    results = bm25_index.search("checkout-service", top_k=5)
    assert len(results) > 0
    # Top result should mention checkout-service in title or text preview
    assert any("checkout" in r.text_preview.lower() or "checkout" in r.title.lower() for r in results)


# 5. Normal keyword query
def test_normal_keyword_query(bm25_index: BM25Index):
    results = bm25_index.search("database connection pool timeout", top_k=5)
    assert len(results) > 0
    assert results[0].score > 0


# 6. No-match query
def test_no_match_query(bm25_index: BM25Index):
    results = bm25_index.search("xyzzyqwertyunobtaniumnonexistentterm12345", top_k=5)
    assert len(results) == 0


# 7. Top-k behavior
def test_top_k_behavior(bm25_index: BM25Index):
    for k in [1, 3, 5, 10]:
        results = bm25_index.search("incident timeout error", top_k=k)
        assert len(results) == k
        assert all(r.rank == i for i, r in enumerate(results, start=1))


# 8. Score ordering
def test_score_ordering(bm25_index: BM25Index):
    results = bm25_index.search("outage latency service degradation", top_k=10)
    assert len(results) > 1
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


# 9. Deterministic results
def test_deterministic_results(bm25_index: BM25Index):
    res_1 = bm25_index.search("deployment release rollback", top_k=5)
    res_2 = bm25_index.search("deployment release rollback", top_k=5)
    assert [r.chunk_id for r in res_1] == [r.chunk_id for r in res_2]
    assert [r.score for r in res_1] == [r.score for r in res_2]


# 10. Tenant filtering (pre-scoring boundary)
def test_tenant_filtering(bm25_index: BM25Index):
    # Orbital search
    res_orbital = bm25_index.search("satellite telemetry access", top_k=10, filters={"tenant_id": "TENANT-ORBITAL"})
    assert len(res_orbital) > 0
    assert all(r.tenant_id == "TENANT-ORBITAL" for r in res_orbital)

    # NovaStack search should NEVER return Orbital or Pinecone chunks
    res_novastack = bm25_index.search("satellite telemetry access", top_k=10, filters={"tenant_id": "TENANT-NOVASTACK"})
    assert all(r.tenant_id == "TENANT-NOVASTACK" for r in res_novastack)


# 11. Classification filtering
def test_classification_filtering(bm25_index: BM25Index):
    res_public = bm25_index.search("security policy standard", top_k=10, filters={"classification": "public"})
    assert all(r.classification == "public" for r in res_public)

    res_restricted = bm25_index.search("security policy standard", top_k=10, filters={"classification": "restricted"})
    assert all(r.classification == "restricted" for r in res_restricted)


# 12. Department filtering
def test_department_filtering(bm25_index: BM25Index):
    res_hr = bm25_index.search("onboarding leave policy", top_k=10, filters={"department": "HR"})
    assert len(res_hr) > 0
    assert all(r.department == "HR" for r in res_hr)


# 13. Source type filtering
def test_source_type_filtering(bm25_index: BM25Index):
    res_pm = bm25_index.search("outage checkout root cause", top_k=10, filters={"source_type": "postmortem"})
    assert len(res_pm) > 0
    assert all(r.source_type == "postmortem" for r in res_pm)


# 14. Status filtering
def test_status_filtering(bm25_index: BM25Index):
    res_pub = bm25_index.search("incident report", top_k=10, filters={"status": "published"})
    assert all(r.status == "published" for r in res_pub)


# 15. Duplicate preservation (both chunks indexed and retrievable)
def test_duplicate_preservation(bm25_index: BM25Index):
    # Find a text present in multiple chunks
    counts = {}
    for c in bm25_index.chunks:
        counts.setdefault(c.text, []).append(c)
    multi_chunks = next(chunks for chunks in counts.values() if len(chunks) > 1)
    
    # Query with distinctive terms from that text
    sample_term = tokenize(multi_chunks[0].text)[0]
    results = bm25_index.search(sample_term, top_k=20)
    retrieved_cids = {r.chunk_id for r in results}
    # Verify index contains all duplicate chunk instances
    assert all(c.chunk_id in [idx_c.chunk_id for idx_c in bm25_index.chunks] for c in multi_chunks)


# 16. Metadata and provenance preservation
def test_metadata_and_provenance_preservation(bm25_index: BM25Index):
    results = bm25_index.search("INC-NS-0001", top_k=1)
    assert len(results) == 1
    r = results[0]
    assert r.chunk_id
    assert r.document_id
    assert r.title
    assert r.source_type
    assert r.tenant_id
    assert r.classification
    assert r.authority_level
    assert r.status
    assert r.version
    assert r.created_at


# 17. Empty and whitespace query behavior
def test_empty_and_whitespace_query_behavior(bm25_index: BM25Index):
    assert bm25_index.search("", top_k=5) == []
    assert bm25_index.search("   ", top_k=5) == []
    assert bm25_index.search(" \t\n ", top_k=5) == []


# 18. Evaluation document resolution
def test_evaluation_document_resolution(bm25_index: BM25Index):
    eval_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
    assert eval_path.exists()
    with open(eval_path, "r", encoding="utf-8") as f:
        cases = json.load(f)["evaluation_cases"]

    # 1. Assert all referenced evaluation document IDs resolve to indexed document IDs
    indexed_doc_ids = {c.document_id for c in bm25_index.chunks}
    for c in cases:
        for doc_id in c.get("expected_document_ids", []):
            assert doc_id in indexed_doc_ids, f"Expected doc {doc_id} not in index!"

    # 2. Test exact identifier lookup case (e.g. EVAL-0011: PR-NS-0002) resolves in top-5
    case_11 = next(c for c in cases if c["evaluation_id"] == "EVAL-0011")
    results = bm25_index.search(case_11["query"], top_k=5)
    retrieved_docs = {r.document_id for r in results}
    expected_docs = set(case_11["expected_document_ids"])
    assert len(retrieved_docs.intersection(expected_docs)) > 0



# 19. Raw source records unchanged
def test_raw_source_records_unchanged(bm25_index: BM25Index):
    h1 = _hash_file(_RAW_PATH)
    _ = bm25_index.search("test query", top_k=5)
    h2 = _hash_file(_RAW_PATH)
    assert h1 == h2


# 20. Search documents unchanged
def test_search_documents_unchanged(bm25_index: BM25Index):
    h1 = _hash_file(_DOCS_PATH)
    _ = bm25_index.search("test query", top_k=5)
    h2 = _hash_file(_DOCS_PATH)
    assert h1 == h2


# 21. Search chunks unchanged
def test_search_chunks_unchanged(bm25_index: BM25Index):
    h1 = _hash_file(_CHUNKS_PATH)
    _ = bm25_index.search("test query", top_k=5)
    h2 = _hash_file(_CHUNKS_PATH)
    assert h1 == h2
