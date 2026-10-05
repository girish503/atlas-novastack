"""Comprehensive test suite for Phase 2B — Semantic-Aware Document Chunking.

Tests cover:
1. all 1,393 documents can be chunked
2. every document produces at least one chunk
3. every chunk references an existing document
4. chunk IDs are globally unique
5. chunk IDs are deterministic
6. chunk indexes are valid
7. tenant metadata is preserved
8. classification is preserved
9. permissions are preserved
10. source type is preserved
11. authority is preserved
12. lifecycle status is preserved
13. version is preserved
14. temporal metadata is preserved
15. supersedes relationship is preserved
16. provenance is preserved
17. related entities are preserved
18. no empty chunks
19. Markdown structure is not unnecessarily destroyed
20. code blocks are handled safely
21. short documents work
22. long documents work
23. repeated chunking produces byte-identical output
24. adversarial records preserve their metadata
25. security records preserve their authorization metadata
26. raw corpus remains unchanged
27. SearchDocument artifact remains unchanged
28. explicit security and tenant isolation invariant
29. validation engine detects malformed chunks
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

# Ensure src/ is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novastack.chunking import (
    ChunkingConfig,
    chunk_document,
    chunk_documents,
    load_search_documents,
    split_into_blocks,
    validate_chunks,
)
from novastack.models import (
    RecordPermissions,
    SearchChunk,
    SearchDocument,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DOCS_PATH = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_documents.json"
_RAW_PATH = _PROJECT_ROOT / "data" / "raw" / "novastack" / "source_records.json"


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def search_documents() -> list[SearchDocument]:
    assert _DOCS_PATH.exists(), f"Search documents not found at {_DOCS_PATH}"
    return load_search_documents(_DOCS_PATH)


@pytest.fixture(scope="module")
def chunked_corpus(search_documents: list[SearchDocument]):
    config = ChunkingConfig(
        target_chunk_size=500,
        max_chunk_size=800,
        min_chunk_size=100,
        overlap_size=100,
    )
    chunks, report = chunk_documents(search_documents, config=config)
    return chunks, report, config


# 1. All 1,393 documents can be chunked
def test_all_documents_chunked(search_documents: list[SearchDocument], chunked_corpus):
    chunks, report, _ = chunked_corpus
    assert len(search_documents) == 1393
    assert report.input_document_count == 1393
    assert len(chunks) > 0
    assert report.output_chunk_count == len(chunks)


# 2. Every document produces at least one chunk
def test_every_document_produces_at_least_one_chunk(search_documents: list[SearchDocument], chunked_corpus):
    chunks, report, _ = chunked_corpus
    assert report.documents_zero_chunks == 0
    chunked_doc_ids = {c.document_id for c in chunks}
    for doc in search_documents:
        assert doc.document_id in chunked_doc_ids


# 3. Every chunk references an existing document
def test_every_chunk_references_existing_document(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    valid_doc_ids = {doc.document_id for doc in search_documents}
    for c in chunks:
        assert c.document_id in valid_doc_ids


# 4. Chunk IDs are globally unique
def test_chunk_ids_globally_unique(chunked_corpus):
    chunks, _, _ = chunked_corpus
    chunk_ids = [c.chunk_id for c in chunks]
    assert len(chunk_ids) == len(set(chunk_ids))


# 5. Chunk IDs are deterministic
def test_chunk_ids_deterministic(search_documents: list[SearchDocument]):
    config = ChunkingConfig()
    chunks_1, _ = chunk_documents(search_documents[:50], config=config)
    chunks_2, _ = chunk_documents(search_documents[:50], config=config)
    assert [c.chunk_id for c in chunks_1] == [c.chunk_id for c in chunks_2]


# 6. Chunk indexes are valid
def test_chunk_indexes_valid(chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_to_chunks = {}
    for c in chunks:
        docs_to_chunks.setdefault(c.document_id, []).append(c)

    for doc_id, doc_chunks in docs_to_chunks.items():
        total = len(doc_chunks)
        indexes = [c.chunk_index for c in doc_chunks]
        assert sorted(indexes) == list(range(total))
        for c in doc_chunks:
            assert c.total_chunks == total
            assert 0 <= c.chunk_index < c.total_chunks
            assert c.chunk_id == f"{doc_id}::CHUNK-{c.chunk_index + 1:04d}"


# 7. Tenant metadata is preserved
def test_tenant_metadata_preserved(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    for c in chunks:
        parent = docs_map[c.document_id]
        assert c.tenant_id == parent.tenant_id


# 8. Classification is preserved
def test_classification_preserved(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    for c in chunks:
        parent = docs_map[c.document_id]
        assert c.classification == parent.classification


# 9. Permissions are preserved
def test_permissions_preserved(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    for c in chunks:
        parent = docs_map[c.document_id]
        assert c.permissions.allowed_roles == parent.permissions.allowed_roles
        assert c.permissions.allowed_departments == parent.permissions.allowed_departments
        assert c.permissions.allowed_teams == parent.permissions.allowed_teams
        assert c.permissions.allowed_user_ids == parent.permissions.allowed_user_ids


# 10. Source type is preserved
def test_source_type_preserved(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    for c in chunks:
        assert c.source_type == docs_map[c.document_id].source_type


# 11. Authority is preserved
def test_authority_preserved(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    for c in chunks:
        assert c.authority_level == docs_map[c.document_id].authority_level


# 12. Lifecycle status is preserved
def test_lifecycle_status_preserved(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    for c in chunks:
        assert c.status == docs_map[c.document_id].status


# 13. Version is preserved
def test_version_preserved(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    for c in chunks:
        assert c.version == docs_map[c.document_id].version


# 14. Temporal metadata is preserved
def test_temporal_metadata_preserved(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    for c in chunks:
        parent = docs_map[c.document_id]
        assert c.created_at == parent.created_at
        assert c.updated_at == parent.updated_at
        assert c.valid_from == parent.valid_from
        assert c.valid_until == parent.valid_until


# 15. Supersedes relationship is preserved
def test_supersedes_relationship_preserved(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    for c in chunks:
        parent = docs_map[c.document_id]
        assert c.supersedes_id == parent.supersedes_id
        assert c.parent_id == parent.parent_id


# 16. Provenance is preserved
def test_provenance_preserved(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    for c in chunks:
        parent = docs_map[c.document_id]
        assert c.source_entity_id == parent.source_entity_id
        assert c.source_entity_type == parent.source_entity_type


# 17. Related entities are preserved
def test_related_entities_preserved(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    for c in chunks:
        parent = docs_map[c.document_id]
        assert c.related_entity_ids == parent.related_entity_ids


# 18. No empty chunks
def test_no_empty_chunks(chunked_corpus):
    chunks, _, _ = chunked_corpus
    for c in chunks:
        assert c.text and c.text.strip(), f"Empty chunk detected: {c.chunk_id}"
        assert c.char_count > 0
        assert c.word_count > 0


# 19. Markdown structure is not unnecessarily destroyed (heading lookahead)
def test_markdown_structure_preserved():
    doc = SearchDocument(
        document_id="DOC-TEST-STRUCT-01",
        tenant_id="TENANT-NOVASTACK",
        source_type="documentation",
        title="Test Heading Lookahead",
        content=(
            "Paragraph one introducing the section with enough text to reach close to boundary.\n\n"
            "Paragraph two extending the length of the document so we are near limit.\n\n"
            "## Secondary Section Heading\n\n"
            "Content under the secondary section that should never be split from its heading."
        ),
        department="Engineering",
        author_id="USR-NS-0001",
        created_at="2025-01-01T00:00:00",
    )
    chunks = chunk_document(doc, ChunkingConfig(target_chunk_size=150, max_chunk_size=250))
    # Verify no chunk ends with a standalone heading
    for c in chunks:
        lines = [l.strip() for l in c.text.split("\n") if l.strip()]
        assert not lines[-1].startswith("#"), f"Orphaned heading found at end of chunk: {c.text}"


# 20. Code blocks are handled safely
def test_code_blocks_handled_safely():
    code_block = (
        "```python\n"
        "def authenticate_service(token: str) -> bool:\n"
        "    if not token:\n"
        "        return False\n"
        "    return token.startswith('bearer_')\n"
        "```"
    )
    doc = SearchDocument(
        document_id="DOC-TEST-CODE-01",
        tenant_id="TENANT-NOVASTACK",
        source_type="engineering_note",
        title="Code Block Test",
        content=f"Here is the service authentication function:\n\n{code_block}\n\nCall this on every request.",
        department="Engineering",
        author_id="USR-NS-0001",
        created_at="2025-01-01T00:00:00",
    )
    chunks = chunk_document(doc, ChunkingConfig(target_chunk_size=200, max_chunk_size=400))
    # Verify code block was kept intact in one chunk
    found_intact = any("def authenticate_service" in c.text and "return token.startswith" in c.text for c in chunks)
    assert found_intact, "Code block was fragmented"


# 21. Short documents work
def test_short_documents_work():
    doc = SearchDocument(
        document_id="DOC-TEST-SHORT-01",
        tenant_id="TENANT-NOVASTACK",
        source_type="conversation",
        title="Short Message",
        content="Quick question on the checkout configuration.",
        department="Engineering",
        author_id="USR-NS-0001",
        created_at="2025-01-01T00:00:00",
    )
    chunks = chunk_document(doc, ChunkingConfig(target_chunk_size=500, max_chunk_size=800))
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].total_chunks == 1
    assert chunks[0].text == "Quick question on the checkout configuration."
    assert chunks[0].chunk_id == "DOC-TEST-SHORT-01::CHUNK-0001"


# 22. Long documents work
def test_long_documents_work():
    paragraphs = [f"Paragraph {i}: " + ("This is extended content for testing long document chunking. " * 5) for i in range(10)]
    doc = SearchDocument(
        document_id="DOC-TEST-LONG-01",
        tenant_id="TENANT-NOVASTACK",
        source_type="postmortem",
        title="Long Postmortem Document",
        content="\n\n".join(paragraphs),
        department="Engineering",
        author_id="USR-NS-0001",
        created_at="2025-01-01T00:00:00",
    )
    chunks = chunk_document(doc, ChunkingConfig(target_chunk_size=400, max_chunk_size=600))
    assert len(chunks) > 1
    assert all(c.total_chunks == len(chunks) for c in chunks)
    for i, c in enumerate(chunks):
        assert c.chunk_index == i
        assert c.chunk_id == f"DOC-TEST-LONG-01::CHUNK-{i+1:04d}"


# 23. Repeated chunking produces byte-identical output
def test_repeated_chunking_byte_identical(search_documents: list[SearchDocument]):
    config = ChunkingConfig(target_chunk_size=500, max_chunk_size=800)
    chunks_a, _ = chunk_documents(search_documents[:100], config=config)
    chunks_b, _ = chunk_documents(search_documents[:100], config=config)

    json_a = json.dumps([c.to_dict() for c in chunks_a], sort_keys=True)
    json_b = json.dumps([c.to_dict() for c in chunks_b], sort_keys=True)
    assert json_a == json_b


# 24. Adversarial records preserve their metadata
def test_adversarial_records_preserve_metadata(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    adv_chunks = [c for c in chunks if "ADV" in c.document_id or "injection" in c.related_entity_ids]
    assert len(adv_chunks) > 0
    for c in adv_chunks:
        parent = docs_map[c.document_id]
        assert c.authority_level == parent.authority_level
        assert c.classification == parent.classification
        assert c.tenant_id == parent.tenant_id
        assert c.related_entity_ids == parent.related_entity_ids


# 25. Security records preserve their authorization metadata
def test_security_records_preserve_authorization_metadata(search_documents: list[SearchDocument], chunked_corpus):
    chunks, _, _ = chunked_corpus
    docs_map = {d.document_id: d for d in search_documents}
    sec_chunks = [c for c in chunks if "SEC" in c.document_id]
    assert len(sec_chunks) > 0
    for c in sec_chunks:
        parent = docs_map[c.document_id]
        assert c.classification == parent.classification
        assert c.permissions.allowed_roles == parent.permissions.allowed_roles
        assert c.permissions.allowed_departments == parent.permissions.allowed_departments
        assert c.permissions.allowed_teams == parent.permissions.allowed_teams
        assert c.permissions.allowed_user_ids == parent.permissions.allowed_user_ids


# 26. Raw corpus remains unchanged
def test_raw_corpus_remains_unchanged():
    hash_before = _hash_file(_RAW_PATH)
    # Perform a chunking operation
    docs = load_search_documents(_DOCS_PATH)
    _ = chunk_documents(docs[:20])
    hash_after = _hash_file(_RAW_PATH)
    assert hash_before == hash_after


# 27. SearchDocument artifact remains unchanged
def test_search_documents_remain_unchanged():
    hash_before = _hash_file(_DOCS_PATH)
    # Perform chunking operation
    docs = load_search_documents(_DOCS_PATH)
    _ = chunk_documents(docs[:20])
    hash_after = _hash_file(_DOCS_PATH)
    assert hash_before == hash_after


# 28. Explicit security and tenant isolation invariant
def test_explicit_security_and_tenant_isolation():
    # Restricted doc must produce strictly restricted chunks with identical permissions
    restricted_doc = SearchDocument(
        document_id="DOC-TEST-RESTRICTED-01",
        tenant_id="TENANT-ORBITAL",
        source_type="policy",
        title="Orbital Restricted Security Policy",
        content="This document contains classified orbital satellite telemetry access control guidelines.",
        department="Security",
        author_id="USR-ORB-0001",
        created_at="2025-05-01T12:00:00",
        classification="restricted",
        permissions=RecordPermissions(
            allowed_roles=["Security-Officer", "CISO"],
            allowed_departments=["Security"],
            allowed_teams=["Orbital-SecOps"],
            allowed_user_ids=["USR-ORB-0001", "USR-ORB-0005"],
        ),
    )
    chunks = chunk_document(restricted_doc)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.classification == "restricted"
    assert chunk.tenant_id == "TENANT-ORBITAL"
    assert chunk.permissions.allowed_roles == ["Security-Officer", "CISO"]
    assert chunk.permissions.allowed_departments == ["Security"]
    assert chunk.permissions.allowed_teams == ["Orbital-SecOps"]
    assert chunk.permissions.allowed_user_ids == ["USR-ORB-0001", "USR-ORB-0005"]


# 29. Validation engine detects malformed chunks
def test_malformed_chunk_validation():
    # Construct an invalid chunk: empty text, wrong index, invalid format
    bad_chunk = SearchChunk(
        chunk_id="BAD-ID-PATTERN",
        document_id="DOC-PARENT-01",
        tenant_id="TENANT-NOVASTACK",
        chunk_index=5,
        total_chunks=1,
        title="Bad Chunk",
        text="",
        char_count=0,
        word_count=0,
        source_type="documentation",
        department="Engineering",
        author_id="USR-NS-0001",
        classification="internal",
        permissions=RecordPermissions(),
        authority_level="medium",
        status="published",
        version="1.0",
        created_at="2025-01-01T00:00:00",
    )
    errors, _ = validate_chunks([bad_chunk])
    assert len(errors) > 0
    assert any("empty text" in e for e in errors)
    assert any("expected deterministic pattern" in e for e in errors)
    assert any("chunk_index 5 >= total_chunks 1" in e for e in errors)
