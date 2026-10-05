"""Phase 4M: API Service Security and Functionality Tests.

Verifies:
1. /healthz returns 200 OK + status JSON
2. /ready returns 200 when initialized, 503 when unready
3. valid query returns 200 + valid response schema
4. empty query rejected with 400/422
5. missing tenant_id rejected with 400/422
6. role-restricted document denied to caller with no role
7. role-restricted document denied to caller with wrong role
8. role-restricted document granted to caller with correct role
9. department-restricted document denied to caller with no department
10. department-restricted document denied to caller with wrong department
11. department-restricted document granted to caller with correct department
12. user-restricted document denied to caller with no user_id
13. user-restricted document denied to caller with wrong user_id
14. user-restricted document granted to caller with correct user_id
15. cross-tenant document denied under all caller contexts
16. internal server error does not leak stack trace
"""
from dataclasses import asdict
from pathlib import Path
import sys
from typing import Any
from fastapi.testclient import TestClient
import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "src"))

from novastack.citation_validator import Citation
from novastack.evidence import EvidencePackage, EvidenceStatus
from novastack.evidence_resolution import (
    EvidenceResolver,
    EvidenceResolverConfig,
)
from novastack.generation import AnswerResult, AnswerStatus
from novastack.models import (
    RecordPermissions,
    SearchChunk,
    SearchDocument,
)
from novastack.service import AtlasServicePipeline, create_app
from novastack.service.schemas import (
    CallerContext,
    QueryRequest,
    QueryResponse,
)


# ---------------------------------------------------------------------
# Fixtures and Helpers
# ---------------------------------------------------------------------

class MockCandidate:
    def __init__(self, document_id: str, score: float = 0.9, rank: int = 1):
        self.chunk_id = f"{document_id}::CHUNK-0001"
        self.document_id = document_id
        self.final_score = score
        self.score = score
        self.rank = rank
        self.title = f"Title for {document_id}"


class MockGenerator:
    """Fast deterministic generator for API testing without heavy GPU/CPU overhead."""
    def __init__(self):
        self.model_name = "mock/test-generator"

    def generate_answer(self, package: EvidencePackage, **kwargs) -> AnswerResult:
        if not package.selected_evidence:
            return AnswerResult(
                answer_id=f"ANS-{package.package_id}",
                evaluation_id=package.evaluation_id,
                query=package.query,
                answer_text="Insufficient evidence to answer this question.",
                answer_status=AnswerStatus.ABSTAINED.value,
                citations=[],
                abstention_reason="empty_selected_evidence",
                generation_latency_ms=1.5,
            )

        top_ev = package.selected_evidence[0]
        cit = Citation(
            raw_tag="[EVD-001]",
            evidence_id=top_ev.evidence_id,
            document_id=top_ev.document_id,
            chunk_id=top_ev.chunk_id,
            title=top_ev.title,
            status="VALID",
        )
        return AnswerResult(
            answer_id=f"ANS-{package.package_id}",
            evaluation_id=package.evaluation_id,
            query=package.query,
            answer_text=f"Factual statement from {top_ev.document_id} [EVD-001].",
            answer_status=AnswerStatus.ANSWERED.value,
            citations=[cit],
            generation_latency_ms=5.2,
        )


from novastack.bm25 import RetrievalResult


def build_test_pipeline(docs: dict[str, SearchDocument]) -> AtlasServicePipeline:
    """Build a lightweight AtlasServicePipeline with real EvidenceResolver and mock generator."""
    chunks = {}
    for did, d in docs.items():
        cid = f"{did}::CHUNK-0001"
        chunks[cid] = SearchChunk.from_dict({
            "chunk_id": cid,
            "document_id": did,
            "chunk_index": 0,
            "total_chunks": 1,
            "text": d.content,
            "char_count": len(d.content),
            "word_count": len(d.content.split()),
            "tenant_id": d.tenant_id,
            "source_type": d.source_type,
            "authority_level": d.authority_level,
            "source_entity_id": d.source_entity_id,
            "title": d.title,
            "department": d.department,
            "author_id": d.author_id,
            "created_at": d.created_at,
            "classification": d.classification,
            "status": d.status,
            "version": d.version,
            "permissions": asdict(d.permissions) if isinstance(d.permissions, RecordPermissions) else {},
        })

    resolver = EvidenceResolver(
        documents_index=docs,
        chunks_index=chunks,
        config=EvidenceResolverConfig(),
    )

    class MockBM25:
        def search(self, query: str, top_k: int = 50, filters: dict = None):
            tenant = filters.get("tenant_id") if filters else None
            matching = [d for d in docs.values() if tenant is None or d.tenant_id == tenant]
            return [
                RetrievalResult(
                    chunk_id=f"{d.document_id}::CHUNK-0001",
                    document_id=d.document_id,
                    score=0.9 - i * 0.05,
                    rank=i + 1,
                    title=d.title,
                    text_preview=d.content[:200],
                    tenant_id=d.tenant_id,
                    source_type=d.source_type,
                    department=d.department,
                    classification=d.classification,
                    authority_level=d.authority_level,
                    status=d.status,
                    version=d.version,
                    created_at=d.created_at,
                    source_entity_id=d.source_entity_id,
                    related_entity_ids=d.related_entity_ids,
                )
                for i, d in enumerate(matching)
            ]

    class MockDense:
        def search(self, query: str, top_k: int = 50, filters: dict = None):
            tenant = filters.get("tenant_id") if filters else None
            matching = [d for d in docs.values() if tenant is None or d.tenant_id == tenant]
            return [
                RetrievalResult(
                    chunk_id=f"{d.document_id}::CHUNK-0001",
                    document_id=d.document_id,
                    score=0.9 - i * 0.05,
                    rank=i + 1,
                    title=d.title,
                    text_preview=d.content[:200],
                    tenant_id=d.tenant_id,
                    source_type=d.source_type,
                    department=d.department,
                    classification=d.classification,
                    authority_level=d.authority_level,
                    status=d.status,
                    version=d.version,
                    created_at=d.created_at,
                    source_entity_id=d.source_entity_id,
                    related_entity_ids=d.related_entity_ids,
                )
                for i, d in enumerate(matching)
            ]

    class MockReranker:
        def rerank(self, candidates, qu=None, metadata_index=None, forbidden_doc_ids=None):
            return candidates

    return AtlasServicePipeline(
        bm25_index=MockBM25(),
        dense_index=MockDense(),
        reranker=MockReranker(),
        generator=MockGenerator(),
        resolver=resolver,
    )


# ---------------------------------------------------------------------
# Test 1: Liveness Endpoint /healthz
# ---------------------------------------------------------------------

def test_healthz_endpoint_returns_200():
    """Requirement 1: /healthz returns 200 OK + minimal status JSON."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------
# Test 2: Readiness Endpoint /ready
# ---------------------------------------------------------------------

def test_ready_endpoint_ready_and_unready():
    """Requirement 2: /ready returns 200 when initialized, 503 when unready."""
    # Case A: Unready pipeline (None)
    app_unready = create_app(pipeline=None)
    client_unready = TestClient(app_unready)
    resp_unready = client_unready.get("/ready")
    assert resp_unready.status_code == 503
    assert resp_unready.json()["status"] == "unready"
    assert resp_unready.json()["components"]["bm25"] is False

    # Case B: Partially unready (missing generator)
    partial_pipe = AtlasServicePipeline(bm25_index="ok", dense_index="ok", reranker="ok", generator=None)
    app_partial = create_app(pipeline=partial_pipe)
    client_partial = TestClient(app_partial)
    resp_partial = client_partial.get("/ready")
    assert resp_partial.status_code == 503
    assert resp_partial.json()["components"]["generator"] is False

    # Case C: Fully initialized pipeline
    ready_pipe = AtlasServicePipeline(
        bm25_index="ok", dense_index="ok", reranker="ok", generator="ok"
    )
    app_ready = create_app(pipeline=ready_pipe)
    client_ready = TestClient(app_ready)
    resp_ready = client_ready.get("/ready")
    assert resp_ready.status_code == 200
    assert resp_ready.json()["status"] == "ready"
    assert all(resp_ready.json()["components"].values())


# ---------------------------------------------------------------------
# Test 3: Valid Query Schema & Execution
# ---------------------------------------------------------------------

def test_query_valid_request_returns_200_and_schema():
    """Requirement 3: valid query returns 200 + valid response schema."""
    doc = SearchDocument(
        document_id="DOC-PUBLIC-01",
        tenant_id="TENANT-NOVASTACK",
        authority_level="canonical",
        classification="internal",
        status="published",
        source_type="documentation",
        title="Public Guidelines",
        content="Public infrastructure guidelines.",
        department="Engineering",
        author_id="USR-001",
        created_at="2026-01-01T00:00:00",
    )
    pipeline = build_test_pipeline({"DOC-PUBLIC-01": doc})
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    payload = {
        "query": "What are the infrastructure guidelines?",
        "user_context": {
            "tenant_id": "TENANT-NOVASTACK",
            "user_id": "USR-001",
            "user_role": "engineer",
            "user_department": "Engineering",
        },
        "evaluation_id": "EVAL-TEST-001",
    }
    resp = client.post("/query", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    # Validate against QueryResponse schema
    validated = QueryResponse(**data)
    assert validated.query == "What are the infrastructure guidelines?"
    assert validated.answer_status == "answered"
    assert len(validated.citations) > 0
    assert validated.latency_ms > 0.0


# ---------------------------------------------------------------------
# Test 4: Empty Query Rejected (400/422)
# ---------------------------------------------------------------------

def test_query_empty_query_rejected():
    """Requirement 4: empty or whitespace-only query rejected with 400/422."""
    app = create_app(pipeline=AtlasServicePipeline(bm25_index="ok", dense_index="ok", reranker="ok", generator="ok"))
    client = TestClient(app)

    # Empty string
    resp1 = client.post("/query", json={"query": "", "user_context": {"tenant_id": "TENANT-NOVASTACK"}})
    assert resp1.status_code in (400, 422)

    # Whitespace only
    resp2 = client.post("/query", json={"query": "   \t\n  ", "user_context": {"tenant_id": "TENANT-NOVASTACK"}})
    assert resp2.status_code in (400, 422)

    # Missing query field entirely
    resp3 = client.post("/query", json={"user_context": {"tenant_id": "TENANT-NOVASTACK"}})
    assert resp3.status_code in (400, 422)


# ---------------------------------------------------------------------
# Test 5: Missing tenant_id Rejected (400/422)
# ---------------------------------------------------------------------

def test_query_missing_tenant_id_rejected():
    """Requirement 5: missing tenant_id rejected with 400/422 (no silent default)."""
    app = create_app(pipeline=AtlasServicePipeline(bm25_index="ok", dense_index="ok", reranker="ok", generator="ok"))
    client = TestClient(app)

    # Missing tenant_id inside user_context
    resp1 = client.post("/query", json={"query": "valid query", "user_context": {"user_role": "engineer"}})
    assert resp1.status_code in (400, 422)

    # Empty tenant_id string
    resp2 = client.post("/query", json={"query": "valid query", "user_context": {"tenant_id": ""}})
    assert resp2.status_code in (400, 422)

    # Whitespace only tenant_id
    resp3 = client.post("/query", json={"query": "valid query", "user_context": {"tenant_id": "   "}})
    assert resp3.status_code in (400, 422)

    # Missing user_context entirely
    resp4 = client.post("/query", json={"query": "valid query"})
    assert resp4.status_code in (400, 422)


# ---------------------------------------------------------------------
# Tests 6, 7, 8: Role Restrictions at API Boundary
# ---------------------------------------------------------------------

def test_api_role_restricted_doc_denied_to_caller_with_no_role():
    """Requirement 6: role-restricted document denied to caller with no role."""
    doc = SearchDocument(
        document_id="DOC-ROLE-SEC-01",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="restricted",
        permissions=RecordPermissions(allowed_roles=["engineer"]),
        status="published",
        source_type="documentation",
        title="Restricted Architecture",
        content="Confidential engine design.",
        department="Engineering",
        author_id="USR-001",
        created_at="2026-01-01T00:00:00",
    )
    pipeline = build_test_pipeline({"DOC-ROLE-SEC-01": doc})
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    resp = client.post("/query", json={
        "query": "Confidential engine design",
        "user_context": {"tenant_id": "TENANT-NOVASTACK", "user_role": None},
    })
    assert resp.status_code == 200
    assert resp.json()["answer_status"] == "abstained"
    assert resp.json()["citations"] == []


def test_api_role_restricted_doc_denied_to_caller_with_wrong_role():
    """Requirement 7: role-restricted document denied to caller with wrong role."""
    doc = SearchDocument(
        document_id="DOC-ROLE-SEC-02",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="restricted",
        permissions=RecordPermissions(allowed_roles=["engineer"]),
        status="published",
        source_type="documentation",
        title="Restricted Architecture",
        content="Confidential engine design.",
        department="Engineering",
        author_id="USR-001",
        created_at="2026-01-01T00:00:00",
    )
    pipeline = build_test_pipeline({"DOC-ROLE-SEC-02": doc})
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    resp = client.post("/query", json={
        "query": "Confidential engine design",
        "user_context": {"tenant_id": "TENANT-NOVASTACK", "user_role": "sales"},
    })
    assert resp.status_code == 200
    assert resp.json()["answer_status"] == "abstained"
    assert resp.json()["citations"] == []


def test_api_role_restricted_doc_granted_to_caller_with_correct_role():
    """Requirement 8: role-restricted document granted to caller with correct role."""
    doc = SearchDocument(
        document_id="DOC-ROLE-SEC-03",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="restricted",
        permissions=RecordPermissions(allowed_roles=["engineer"]),
        status="published",
        source_type="documentation",
        title="Restricted Architecture",
        content="Confidential engine design.",
        department="Engineering",
        author_id="USR-001",
        created_at="2026-01-01T00:00:00",
    )
    pipeline = build_test_pipeline({"DOC-ROLE-SEC-03": doc})
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    resp = client.post("/query", json={
        "query": "Confidential engine design",
        "user_context": {"tenant_id": "TENANT-NOVASTACK", "user_role": "engineer"},
    })
    assert resp.status_code == 200
    assert resp.json()["answer_status"] == "answered"
    assert len(resp.json()["citations"]) > 0
    assert resp.json()["citations"][0]["document_id"] == "DOC-ROLE-SEC-03"


# ---------------------------------------------------------------------
# Tests 9, 10, 11: Department Restrictions at API Boundary
# ---------------------------------------------------------------------

def test_api_dept_restricted_doc_denied_to_caller_with_no_department():
    """Requirement 9: department-restricted document denied to caller with no department."""
    doc = SearchDocument(
        document_id="DOC-DEPT-SEC-01",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="internal",
        permissions=RecordPermissions(allowed_departments=["Finance"]),
        status="published",
        source_type="documentation",
        title="Financial Ledger",
        content="Private financial ledger data.",
        department="Finance",
        author_id="USR-FIN-01",
        created_at="2026-01-01T00:00:00",
    )
    pipeline = build_test_pipeline({"DOC-DEPT-SEC-01": doc})
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    resp = client.post("/query", json={
        "query": "financial ledger",
        "user_context": {"tenant_id": "TENANT-NOVASTACK", "user_department": None},
    })
    assert resp.status_code == 200
    assert resp.json()["answer_status"] == "abstained"


def test_api_dept_restricted_doc_denied_to_caller_with_wrong_department():
    """Requirement 10: department-restricted document denied to caller with wrong department."""
    doc = SearchDocument(
        document_id="DOC-DEPT-SEC-02",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="internal",
        permissions=RecordPermissions(allowed_departments=["Finance"]),
        status="published",
        source_type="documentation",
        title="Financial Ledger",
        content="Private financial ledger data.",
        department="Finance",
        author_id="USR-FIN-01",
        created_at="2026-01-01T00:00:00",
    )
    pipeline = build_test_pipeline({"DOC-DEPT-SEC-02": doc})
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    resp = client.post("/query", json={
        "query": "financial ledger",
        "user_context": {"tenant_id": "TENANT-NOVASTACK", "user_department": "Engineering"},
    })
    assert resp.status_code == 200
    assert resp.json()["answer_status"] == "abstained"


def test_api_dept_restricted_doc_granted_to_caller_with_correct_department():
    """Requirement 11: department-restricted document granted to caller with correct department."""
    doc = SearchDocument(
        document_id="DOC-DEPT-SEC-03",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="internal",
        permissions=RecordPermissions(allowed_departments=["Finance"]),
        status="published",
        source_type="documentation",
        title="Financial Ledger",
        content="Private financial ledger data.",
        department="Finance",
        author_id="USR-FIN-01",
        created_at="2026-01-01T00:00:00",
    )
    pipeline = build_test_pipeline({"DOC-DEPT-SEC-03": doc})
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    resp = client.post("/query", json={
        "query": "financial ledger",
        "user_context": {"tenant_id": "TENANT-NOVASTACK", "user_department": "Finance"},
    })
    assert resp.status_code == 200
    assert resp.json()["answer_status"] == "answered"
    assert resp.json()["citations"][0]["document_id"] == "DOC-DEPT-SEC-03"


# ---------------------------------------------------------------------
# Tests 12, 13, 14: User Restrictions at API Boundary
# ---------------------------------------------------------------------

def test_api_user_restricted_doc_denied_to_caller_with_no_user_id():
    """Requirement 12: user-restricted document denied to caller with no user_id."""
    doc = SearchDocument(
        document_id="DOC-USER-SEC-01",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="confidential",
        permissions=RecordPermissions(allowed_user_ids=["USR-CTO-001"]),
        status="published",
        source_type="documentation",
        title="CTO Notes",
        content="CTO confidential strategy.",
        department="Executive",
        author_id="USR-CTO-001",
        created_at="2026-01-01T00:00:00",
    )
    pipeline = build_test_pipeline({"DOC-USER-SEC-01": doc})
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    resp = client.post("/query", json={
        "query": "CTO confidential strategy",
        "user_context": {"tenant_id": "TENANT-NOVASTACK", "user_id": None},
    })
    assert resp.status_code == 200
    assert resp.json()["answer_status"] == "abstained"


def test_api_user_restricted_doc_denied_to_caller_with_wrong_user_id():
    """Requirement 13: user-restricted document denied to caller with wrong user_id."""
    doc = SearchDocument(
        document_id="DOC-USER-SEC-02",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="confidential",
        permissions=RecordPermissions(allowed_user_ids=["USR-CTO-001"]),
        status="published",
        source_type="documentation",
        title="CTO Notes",
        content="CTO confidential strategy.",
        department="Executive",
        author_id="USR-CTO-001",
        created_at="2026-01-01T00:00:00",
    )
    pipeline = build_test_pipeline({"DOC-USER-SEC-02": doc})
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    resp = client.post("/query", json={
        "query": "CTO confidential strategy",
        "user_context": {"tenant_id": "TENANT-NOVASTACK", "user_id": "USR-DEV-002"},
    })
    assert resp.status_code == 200
    assert resp.json()["answer_status"] == "abstained"


def test_api_user_restricted_doc_granted_to_caller_with_correct_user_id():
    """Requirement 14: user-restricted document granted to caller with correct user_id."""
    doc = SearchDocument(
        document_id="DOC-USER-SEC-03",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="confidential",
        permissions=RecordPermissions(allowed_user_ids=["USR-CTO-001"]),
        status="published",
        source_type="documentation",
        title="CTO Notes",
        content="CTO confidential strategy.",
        department="Executive",
        author_id="USR-CTO-001",
        created_at="2026-01-01T00:00:00",
    )
    pipeline = build_test_pipeline({"DOC-USER-SEC-03": doc})
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    resp = client.post("/query", json={
        "query": "CTO confidential strategy",
        "user_context": {"tenant_id": "TENANT-NOVASTACK", "user_id": "USR-CTO-001"},
    })
    assert resp.status_code == 200
    assert resp.json()["answer_status"] == "answered"
    assert resp.json()["citations"][0]["document_id"] == "DOC-USER-SEC-03"


# ---------------------------------------------------------------------
# Test 15: Cross-Tenant Isolation at API Boundary
# ---------------------------------------------------------------------

def test_api_cross_tenant_document_denied_under_all_contexts():
    """Requirement 15: cross-tenant document denied under all caller contexts."""
    orbital_doc = SearchDocument(
        document_id="DOC-ORBITAL-SENSITIVE-01",
        tenant_id="TENANT-ORBITAL",
        authority_level="canonical",
        classification="internal",
        permissions=RecordPermissions(allowed_roles=["engineer"]),
        status="published",
        source_type="documentation",
        title="Orbital Internal Specs",
        content="Proprietary satellite telemetry protocols.",
        department="Engineering",
        author_id="USR-ORBITAL-01",
        created_at="2026-01-01T00:00:00",
    )
    pipeline = build_test_pipeline({"DOC-ORBITAL-SENSITIVE-01": orbital_doc})
    app = create_app(pipeline=pipeline)
    client = TestClient(app)

    # Caller belongs to TENANT-NOVASTACK
    resp = client.post("/query", json={
        "query": "satellite telemetry protocols",
        "user_context": {
            "tenant_id": "TENANT-NOVASTACK",
            "user_role": "engineer",
            "user_department": "Engineering",
            "user_id": "USR-ORBITAL-01",
        },
    })
    assert resp.status_code == 200
    assert resp.json()["answer_status"] == "abstained"
    assert resp.json()["citations"] == []


# ---------------------------------------------------------------------
# Test 16: Internal Server Error Does Not Leak Stack Trace
# ---------------------------------------------------------------------

def test_api_internal_server_error_no_stack_trace_leak():
    """Requirement 16: internal server error does not leak stack trace."""
    class FailingPipeline(AtlasServicePipeline):
        def is_ready(self):
            return True, {"bm25": True, "dense": True, "reranker": True, "generator": True}

        def execute_query(self, request: QueryRequest) -> QueryResponse:
            # Simulate an unhandled internal crash with sensitive stack information
            raise RuntimeError(
                "CRITICAL_INTERNAL_DB_FAILURE: Connection refused at postgresql://atlas_admin:p@ssw0rd123@db.internal:5432/secrets"
            )

    app = create_app(pipeline=FailingPipeline())
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/query", json={
        "query": "trigger failure",
        "user_context": {"tenant_id": "TENANT-NOVASTACK"},
    })

    assert resp.status_code == 500
    data = resp.json()
    assert data["answer_status"] == "error"
    assert data["detail"] == "Internal server processing failure"
    assert data["error_type"] == "RuntimeError"

    # Strict invariant: NO stack traces or sensitive strings leaked
    body_text = resp.text
    assert "Traceback (most recent call last)" not in body_text
    assert "CRITICAL_INTERNAL_DB_FAILURE" not in body_text
    assert "p@ssw0rd123" not in body_text
    assert "postgresql://" not in body_text
    assert "File \"" not in body_text
