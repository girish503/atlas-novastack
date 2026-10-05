"""Phase 4M: Fail-Closed Authorization Unit Tests.

Verifies:
- Role-restricted documents denied to caller with no role (fail-closed)
- Role-restricted documents denied to caller with non-matching role
- Role-restricted documents granted to caller with matching role
- Department-restricted documents denied to caller with no department (fail-closed)
- Department-restricted documents denied to caller with non-matching department
- Department-restricted documents granted to caller with matching department
- User-restricted documents denied to caller with no user_id (fail-closed)
- User-restricted documents denied to caller with non-matching user_id
- User-restricted documents granted to caller with matching user_id
- Cross-tenant documents strictly denied under all caller contexts
- Combined multi-constraint enforcement (all constraints must be satisfied)
"""
from dataclasses import asdict
from pathlib import Path
import sys
import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "src"))

from novastack.evidence import EvidenceStatus
from novastack.evidence_resolution import (
    EvidenceResolver,
    EvidenceResolverConfig,
)
from novastack.models import (
    RecordPermissions,
    SearchChunk,
    SearchDocument,
)


class MockCandidate:
    """Minimal candidate structure matching resolver requirements."""
    def __init__(self, document_id: str, score: float = 0.95):
        self.chunk_id = f"{document_id}::CHUNK-0001"
        self.document_id = document_id
        self.final_score = score
        self.title = f"Title for {document_id}"


def make_test_resolver(docs: dict[str, SearchDocument]) -> EvidenceResolver:
    """Create a standalone EvidenceResolver with mock SearchDocuments and chunks."""
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
    return EvidenceResolver(
        documents_index=docs,
        chunks_index=chunks,
        config=EvidenceResolverConfig(),
    )


# ---------------------------------------------------------------------
# Role Restriction Tests (Fail-Closed)
# ---------------------------------------------------------------------

def test_role_restricted_doc_denied_to_caller_with_no_role():
    """Requirement 6: role-restricted document denied to caller with no role."""
    doc = SearchDocument(
        document_id="DOC-ROLE-TEST-01",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="restricted",
        permissions=RecordPermissions(allowed_roles=["engineer", "architect"]),
        status="published",
        source_type="documentation",
        title="Role Restricted Architecture",
        content="Secret architectural guidelines.",
        department="Engineering",
        author_id="USR-001",
        created_at="2026-01-01T00:00:00",
    )
    resolver = make_test_resolver({"DOC-ROLE-TEST-01": doc})

    # Caller has no role (user_role=None)
    pkg = resolver.resolve_package(
        query="architecture details",
        candidates=[MockCandidate("DOC-ROLE-TEST-01")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-ROLE-NONE",
            "user_role": None,
            "user_department": "Engineering",
            "user_id": "USR-001",
        },
    )

    assert len(pkg.selected_evidence) == 0
    assert len(pkg.excluded_evidence) == 1
    excl = pkg.excluded_evidence[0]
    assert excl.evidence_status == EvidenceStatus.UNAUTHORIZED.value
    assert any("role_unauthorized" in r for r in excl.evidence_reasons)
    assert any("missing_user_role" in r for r in excl.evidence_reasons)


def test_role_restricted_doc_denied_to_caller_with_wrong_role():
    """Requirement 7: role-restricted document denied to caller with wrong role."""
    doc = SearchDocument(
        document_id="DOC-ROLE-TEST-02",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="restricted",
        permissions=RecordPermissions(allowed_roles=["engineer"]),
        status="published",
        source_type="documentation",
        title="Engineering Design Document",
        content="Design specs for internal engine.",
        department="Engineering",
        author_id="USR-001",
        created_at="2026-01-01T00:00:00",
    )
    resolver = make_test_resolver({"DOC-ROLE-TEST-02": doc})

    # Caller has wrong role ("sales")
    pkg = resolver.resolve_package(
        query="design specs",
        candidates=[MockCandidate("DOC-ROLE-TEST-02")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-ROLE-WRONG",
            "user_role": "sales",
            "user_department": "Engineering",
            "user_id": "USR-001",
        },
    )

    assert len(pkg.selected_evidence) == 0
    assert len(pkg.excluded_evidence) == 1
    excl = pkg.excluded_evidence[0]
    assert excl.evidence_status == EvidenceStatus.UNAUTHORIZED.value
    assert any("role_unauthorized" in r for r in excl.evidence_reasons)
    assert any("user_role='sales'" in r for r in excl.evidence_reasons)


def test_role_restricted_doc_granted_to_caller_with_correct_role():
    """Requirement 8: role-restricted document granted to caller with correct role."""
    doc = SearchDocument(
        document_id="DOC-ROLE-TEST-03",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="restricted",
        permissions=RecordPermissions(allowed_roles=["engineer", "sre"]),
        status="published",
        source_type="documentation",
        title="Engineering Runbook",
        content="Incident response procedure.",
        department="Engineering",
        author_id="USR-001",
        created_at="2026-01-01T00:00:00",
    )
    resolver = make_test_resolver({"DOC-ROLE-TEST-03": doc})

    # Caller has matching role ("sre")
    pkg = resolver.resolve_package(
        query="incident response runbook",
        candidates=[MockCandidate("DOC-ROLE-TEST-03")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-ROLE-MATCH",
            "user_role": "sre",
            "user_department": "Engineering",
            "user_id": "USR-001",
        },
    )

    assert len(pkg.selected_evidence) == 1
    assert pkg.selected_evidence[0].document_id == "DOC-ROLE-TEST-03"
    assert len(pkg.excluded_evidence) == 0


# ---------------------------------------------------------------------
# Department Restriction Tests (Fail-Closed)
# ---------------------------------------------------------------------

def test_dept_restricted_doc_denied_to_caller_with_no_department():
    """Requirement 9: department-restricted document denied to caller with no department."""
    doc = SearchDocument(
        document_id="DOC-DEPT-TEST-01",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="internal",
        permissions=RecordPermissions(allowed_departments=["Finance", "Legal"]),
        status="published",
        source_type="documentation",
        title="Audit Guidelines",
        content="Financial audit checklist.",
        department="Finance",
        author_id="USR-002",
        created_at="2026-01-01T00:00:00",
    )
    resolver = make_test_resolver({"DOC-DEPT-TEST-01": doc})

    # Caller has no department (user_department=None)
    pkg = resolver.resolve_package(
        query="audit checklist",
        candidates=[MockCandidate("DOC-DEPT-TEST-01")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-DEPT-NONE",
            "user_role": "auditor",
            "user_department": None,
            "user_id": "USR-002",
        },
    )

    assert len(pkg.selected_evidence) == 0
    assert len(pkg.excluded_evidence) == 1
    excl = pkg.excluded_evidence[0]
    assert excl.evidence_status == EvidenceStatus.UNAUTHORIZED.value
    assert any("department_unauthorized" in r for r in excl.evidence_reasons)
    assert any("missing_user_dept" in r for r in excl.evidence_reasons)


def test_dept_restricted_doc_denied_to_caller_with_wrong_department():
    """Requirement 10: department-restricted document denied to caller with wrong department."""
    doc = SearchDocument(
        document_id="DOC-DEPT-TEST-02",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="internal",
        permissions=RecordPermissions(allowed_departments=["Legal"]),
        status="published",
        source_type="documentation",
        title="Contract Terms",
        content="Standard vendor contract clauses.",
        department="Legal",
        author_id="USR-003",
        created_at="2026-01-01T00:00:00",
    )
    resolver = make_test_resolver({"DOC-DEPT-TEST-02": doc})

    # Caller has wrong department ("Marketing")
    pkg = resolver.resolve_package(
        query="contract clauses",
        candidates=[MockCandidate("DOC-DEPT-TEST-02")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-DEPT-WRONG",
            "user_role": "manager",
            "user_department": "Marketing",
            "user_id": "USR-003",
        },
    )

    assert len(pkg.selected_evidence) == 0
    assert len(pkg.excluded_evidence) == 1
    excl = pkg.excluded_evidence[0]
    assert excl.evidence_status == EvidenceStatus.UNAUTHORIZED.value
    assert any("department_unauthorized" in r for r in excl.evidence_reasons)
    assert any("user_dept='Marketing'" in r for r in excl.evidence_reasons)


def test_dept_restricted_doc_granted_to_caller_with_correct_department():
    """Requirement 11: department-restricted document granted to caller with correct department."""
    doc = SearchDocument(
        document_id="DOC-DEPT-TEST-03",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="internal",
        permissions=RecordPermissions(allowed_departments=["Legal", "Compliance"]),
        status="published",
        source_type="documentation",
        title="Compliance Standards",
        content="Regulatory compliance standard documentation.",
        department="Legal",
        author_id="USR-003",
        created_at="2026-01-01T00:00:00",
    )
    resolver = make_test_resolver({"DOC-DEPT-TEST-03": doc})

    # Caller has correct department ("Compliance")
    pkg = resolver.resolve_package(
        query="regulatory compliance standards",
        candidates=[MockCandidate("DOC-DEPT-TEST-03")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-DEPT-MATCH",
            "user_role": "officer",
            "user_department": "Compliance",
            "user_id": "USR-003",
        },
    )

    assert len(pkg.selected_evidence) == 1
    assert pkg.selected_evidence[0].document_id == "DOC-DEPT-TEST-03"
    assert len(pkg.excluded_evidence) == 0


# ---------------------------------------------------------------------
# User ACL Restriction Tests (Fail-Closed)
# ---------------------------------------------------------------------

def test_user_restricted_doc_denied_to_caller_with_no_user_id():
    """Requirement 12: user-restricted document denied to caller with no user_id."""
    doc = SearchDocument(
        document_id="DOC-USER-TEST-01",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="confidential",
        permissions=RecordPermissions(allowed_user_ids=["USR-LEAD-001", "USR-CTO-001"]),
        status="published",
        source_type="documentation",
        title="Executive Strategy",
        content="Confidential corporate roadmap.",
        department="Executive",
        author_id="USR-CTO-001",
        created_at="2026-01-01T00:00:00",
    )
    resolver = make_test_resolver({"DOC-USER-TEST-01": doc})

    # Caller has no user_id (user_id=None)
    pkg = resolver.resolve_package(
        query="corporate roadmap",
        candidates=[MockCandidate("DOC-USER-TEST-01")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-USER-NONE",
            "user_role": "executive",
            "user_department": "Executive",
            "user_id": None,
        },
    )

    assert len(pkg.selected_evidence) == 0
    assert len(pkg.excluded_evidence) == 1
    excl = pkg.excluded_evidence[0]
    assert excl.evidence_status == EvidenceStatus.UNAUTHORIZED.value
    assert any("user_acl_unauthorized" in r for r in excl.evidence_reasons)
    assert any("missing_user_id" in r for r in excl.evidence_reasons)


def test_user_restricted_doc_denied_to_caller_with_wrong_user_id():
    """Requirement 13: user-restricted document denied to caller with wrong user_id."""
    doc = SearchDocument(
        document_id="DOC-USER-TEST-02",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="confidential",
        permissions=RecordPermissions(allowed_user_ids=["USR-CTO-001"]),
        status="published",
        source_type="documentation",
        title="Private Performance Review",
        content="Confidential personnel feedback.",
        department="Executive",
        author_id="USR-CTO-001",
        created_at="2026-01-01T00:00:00",
    )
    resolver = make_test_resolver({"DOC-USER-TEST-02": doc})

    # Caller has wrong user_id ("USR-INTERN-099")
    pkg = resolver.resolve_package(
        query="personnel feedback",
        candidates=[MockCandidate("DOC-USER-TEST-02")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-USER-WRONG",
            "user_role": "executive",
            "user_department": "Executive",
            "user_id": "USR-INTERN-099",
        },
    )

    assert len(pkg.selected_evidence) == 0
    assert len(pkg.excluded_evidence) == 1
    excl = pkg.excluded_evidence[0]
    assert excl.evidence_status == EvidenceStatus.UNAUTHORIZED.value
    assert any("user_acl_unauthorized" in r for r in excl.evidence_reasons)
    assert any("user_id='USR-INTERN-099'" in r for r in excl.evidence_reasons)


def test_user_restricted_doc_granted_to_caller_with_correct_user_id():
    """Requirement 14: user-restricted document granted to caller with correct user_id."""
    doc = SearchDocument(
        document_id="DOC-USER-TEST-03",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="confidential",
        permissions=RecordPermissions(allowed_user_ids=["USR-CTO-001"]),
        status="published",
        source_type="documentation",
        title="Board Meeting Minutes",
        content="Discussion notes from Q4 board meeting.",
        department="Executive",
        author_id="USR-CTO-001",
        created_at="2026-01-01T00:00:00",
    )
    resolver = make_test_resolver({"DOC-USER-TEST-03": doc})

    # Caller has matching user_id ("USR-CTO-001")
    pkg = resolver.resolve_package(
        query="board meeting minutes",
        candidates=[MockCandidate("DOC-USER-TEST-03")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-USER-MATCH",
            "user_role": "executive",
            "user_department": "Executive",
            "user_id": "USR-CTO-001",
        },
    )

    assert len(pkg.selected_evidence) == 1
    assert pkg.selected_evidence[0].document_id == "DOC-USER-TEST-03"
    assert len(pkg.excluded_evidence) == 0


# ---------------------------------------------------------------------
# Cross-Tenant Isolation Tests (Requirement 15)
# ---------------------------------------------------------------------

def test_cross_tenant_doc_denied_under_all_caller_contexts():
    """Requirement 15: cross-tenant document denied under all caller contexts."""
    doc = SearchDocument(
        document_id="DOC-CROSS-TENANT-01",
        tenant_id="TENANT-ORBITAL",
        authority_level="canonical",
        classification="internal",
        permissions=RecordPermissions(
            allowed_roles=["engineer", "admin"],
            allowed_departments=["Engineering"],
            allowed_user_ids=["USR-MATCH-001"],
        ),
        status="published",
        source_type="documentation",
        title="Orbital Internal Architecture",
        content="Sensitive Orbital proprietary architecture.",
        department="Engineering",
        author_id="USR-MATCH-001",
        created_at="2026-01-01T00:00:00",
    )
    resolver = make_test_resolver({"DOC-CROSS-TENANT-01": doc})

    # Even if caller has all matching credentials for the document,
    # the tenant boundary MUST deny access strictly.
    contexts = [
        # Full credential match on role, dept, user_id, but tenant is TENANT-NOVASTACK
        {
            "tenant_id": "TENANT-NOVASTACK",
            "user_role": "admin",
            "user_department": "Engineering",
            "user_id": "USR-MATCH-001",
        },
        # No credentials, tenant is TENANT-NOVASTACK
        {
            "tenant_id": "TENANT-NOVASTACK",
            "user_role": None,
            "user_department": None,
            "user_id": None,
        },
        # Unrelated third tenant
        {
            "tenant_id": "TENANT-ACME",
            "user_role": "engineer",
            "user_department": "Engineering",
            "user_id": "USR-MATCH-001",
        },
    ]

    for idx, ctx in enumerate(contexts):
        pkg = resolver.resolve_package(
            query="Orbital proprietary architecture",
            candidates=[MockCandidate("DOC-CROSS-TENANT-01")],
            eval_case={"evaluation_id": f"TEST-CROSS-{idx}", **ctx},
        )
        assert len(pkg.selected_evidence) == 0, f"Context {idx} unexpectedly bypassed tenant boundary"
        assert len(pkg.excluded_evidence) == 1
        excl = pkg.excluded_evidence[0]
        assert excl.evidence_status == EvidenceStatus.UNAUTHORIZED.value
        assert any("cross_tenant_violation" in r for r in excl.evidence_reasons)


# ---------------------------------------------------------------------
# Multi-Constraint Combined Enforcement
# ---------------------------------------------------------------------

def test_multi_constraint_all_required_to_match():
    """Verify that if role, dept, and user constraints are present, all must match."""
    doc = SearchDocument(
        document_id="DOC-MULTI-01",
        tenant_id="TENANT-NOVASTACK",
        authority_level="high",
        classification="restricted",
        permissions=RecordPermissions(
            allowed_roles=["engineer"],
            allowed_departments=["Security"],
            allowed_user_ids=["USR-SEC-01"],
        ),
        status="published",
        source_type="documentation",
        title="Security Master Keys",
        content="Key ceremony procedure.",
        department="Security",
        author_id="USR-SEC-01",
        created_at="2026-01-01T00:00:00",
    )
    resolver = make_test_resolver({"DOC-MULTI-01": doc})

    # Test missing role -> denied
    pkg = resolver.resolve_package(
        query="key ceremony",
        candidates=[MockCandidate("DOC-MULTI-01")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-M1",
            "user_role": None,
            "user_department": "Security",
            "user_id": "USR-SEC-01",
        },
    )
    assert len(pkg.selected_evidence) == 0
    assert any("role_unauthorized" in r for r in pkg.excluded_evidence[0].evidence_reasons)

    # Test missing dept -> denied
    pkg = resolver.resolve_package(
        query="key ceremony",
        candidates=[MockCandidate("DOC-MULTI-01")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-M2",
            "user_role": "engineer",
            "user_department": None,
            "user_id": "USR-SEC-01",
        },
    )
    assert len(pkg.selected_evidence) == 0
    assert any("department_unauthorized" in r for r in pkg.excluded_evidence[0].evidence_reasons)

    # Test missing user_id -> denied
    pkg = resolver.resolve_package(
        query="key ceremony",
        candidates=[MockCandidate("DOC-MULTI-01")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-M3",
            "user_role": "engineer",
            "user_department": "Security",
            "user_id": None,
        },
    )
    assert len(pkg.selected_evidence) == 0
    assert any("user_acl_unauthorized" in r for r in pkg.excluded_evidence[0].evidence_reasons)

    # Test all match -> granted
    pkg = resolver.resolve_package(
        query="key ceremony",
        candidates=[MockCandidate("DOC-MULTI-01")],
        eval_case={
            "tenant_id": "TENANT-NOVASTACK",
            "evaluation_id": "TEST-M4",
            "user_role": "engineer",
            "user_department": "Security",
            "user_id": "USR-SEC-01",
        },
    )
    assert len(pkg.selected_evidence) == 1
    assert pkg.selected_evidence[0].document_id == "DOC-MULTI-01"
