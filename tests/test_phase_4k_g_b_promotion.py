"""Phase 4K-G: Production Promotion Verification Tests for Mechanism B.

Asserts:
- Production flag defaults: A == False, B == True, C == False.
- Canonical security invariants remain intact under default configuration.
- Query-Aware Authority preserves source-specific evidence by default without compromising safety.
"""
import inspect
from dataclasses import asdict
from pathlib import Path
import sys
import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "src"))

from novastack.event_evidence_bundler import EventBundlerConfig
from novastack.evidence import EvidenceStatus
from novastack.evidence_resolution import (
    EvidenceResolver,
    EvidenceResolverConfig,
)
from novastack.generation import GroundedAnswerGenerator
from novastack.models import (
    EvaluationCase,
    RecordPermissions,
    SearchChunk,
    SearchDocument,
)


def test_production_flag_defaults():
    """Assert exact production flags: A == False, B == True, C == False."""
    # Mechanism B: Query-Aware Authority Preservation (MUST BE TRUE)
    resolver_config = EvidenceResolverConfig()
    assert resolver_config.enable_query_aware_authority is True, (
        "Production default for enable_query_aware_authority must be True"
    )

    # Mechanism C: Event-Centric Evidence Bundling (MUST BE FALSE)
    assert resolver_config.enable_event_bundling is False, (
        "Production default for enable_event_bundling in EvidenceResolverConfig must be False"
    )
    bundler_config = EventBundlerConfig()
    assert bundler_config.enable_event_bundling is False, (
        "Production default for enable_event_bundling in EventBundlerConfig must be False"
    )

    # Mechanism A: Boundary Sentence Stitching (MUST BE FALSE)
    sig = inspect.signature(GroundedAnswerGenerator.generate_answer)
    assert "enable_boundary_stitching" in sig.parameters, (
        "enable_boundary_stitching parameter missing from generate_answer"
    )
    assert sig.parameters["enable_boundary_stitching"].default is False, (
        "Production default for enable_boundary_stitching must be False"
    )


def test_evidence_resolver_default_instance():
    """Verify EvidenceResolver initializes with production defaults when config is omitted."""
    resolver = EvidenceResolver()
    assert resolver.config.enable_query_aware_authority is True
    assert resolver.config.enable_event_bundling is False
    assert resolver.config.enforce_strict_authorization is True
    assert resolver.config.enforce_adversarial_quarantine is True
    assert resolver.config.enforce_version_supersession is True
    assert resolver.config.enforce_lifecycle_rules is True
    assert resolver.config.enforce_temporal_validity is True


def test_security_invariants_preserved_with_production_defaults():
    """Verify that all security barriers execute strictly before Mechanism B in default production."""
    class SimpleCandidate:
        def __init__(self, chunk_id: str, document_id: str, score: float = 1.0):
            self.chunk_id = chunk_id
            self.document_id = document_id
            self.final_score = score

    # Setup documents spanning various security violations
    docs = {
        # Valid authoritative document
        "DOC-AUTH-001": SearchDocument(
            document_id="DOC-AUTH-001",
            tenant_id="TENANT-NOVASTACK",
            authority_level="canonical",
            classification="internal",
            status="published",
            source_type="postmortem",
            title="Authoritative Postmortem",
            content="Authoritative incident postmortem data.",
            department="Engineering",
            author_id="USR-001",
            created_at="2026-01-01T00:00:00",
            source_entity_id="EVT-001",
            source_entity_type="event",
        ),
        # Valid low-authority document matching query source type
        "DOC-CHAT-001": SearchDocument(
            document_id="DOC-CHAT-001",
            tenant_id="TENANT-NOVASTACK",
            authority_level="low",
            classification="internal",
            status="published",
            source_type="conversation",
            title="Triage Chat Notes",
            content="Triage notes observation.",
            department="Engineering",
            author_id="USR-001",
            created_at="2026-01-01T00:00:00",
            source_entity_id="EVT-001",
            source_entity_type="event",
        ),
        # Cross-tenant document matching query source type
        "DOC-SEC-TENT-001": SearchDocument(
            document_id="DOC-SEC-TENT-001",
            tenant_id="TENANT-ORBITAL",
            authority_level="high",
            classification="internal",
            status="published",
            source_type="conversation",
            title="Foreign Tenant Chat",
            content="Cross tenant data.",
            department="Engineering",
            author_id="USR-001",
            created_at="2026-01-01T00:00:00",
        ),
        # RBAC restricted document matching query source type
        "DOC-SEC-ROLE-001": SearchDocument(
            document_id="DOC-SEC-ROLE-001",
            tenant_id="TENANT-NOVASTACK",
            authority_level="high",
            classification="restricted",
            permissions=RecordPermissions(allowed_roles=["executive"]),
            status="published",
            source_type="conversation",
            title="Restricted Executive Chat",
            content="Confidential executive chat.",
            department="Engineering",
            author_id="USR-001",
            created_at="2026-01-01T00:00:00",
        ),
        # Adversarial poisoned document matching query source type
        "DOC-ADV-PSN-001": SearchDocument(
            document_id="DOC-ADV-PSN-001",
            tenant_id="TENANT-NOVASTACK",
            authority_level="low",
            classification="internal",
            status="published",
            source_type="conversation",
            title="Poisoned Chat Notes [Poisoned Evidence]",
            content="Poisoned injection payload.",
            department="Engineering",
            author_id="USR-001",
            created_at="2026-01-01T00:00:00",
        ),
        # Superseded document matching query source type
        "DOC-NOISE-STALE-001": SearchDocument(
            document_id="DOC-NOISE-STALE-001",
            tenant_id="TENANT-NOVASTACK",
            authority_level="low",
            classification="internal",
            status="superseded",
            source_type="conversation",
            title="Superseded Chat Notes",
            content="Superseded chat notes.",
            department="Engineering",
            author_id="USR-001",
            created_at="2026-01-01T00:00:00",
        ),
    }

    chunks = {
        f"{did}::CHUNK-0001": SearchChunk.from_dict({
            "chunk_id": f"{did}::CHUNK-0001",
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
            "version": "1.0",
            "permissions": asdict(d.permissions) if isinstance(d.permissions, RecordPermissions) else {},
        })
        for did, d in docs.items()
    }

    # Adversarial fixture quarantine
    adv_fixtures = [{"document_id": "DOC-ADV-PSN-001", "type": "poisoning"}]

    # Production resolver instance (default config: B=True, C=False)
    resolver = EvidenceResolver(
        documents_index=docs,
        chunks_index=chunks,
        adversarial_fixtures=adv_fixtures,
    )

    # Query explicitly requesting triage channel notes
    case = EvaluationCase(
        evaluation_id="TEST-PROD-001",
        query="What did the triage channel notes report about the event?",
        query_category="multi_document",
        difficulty="medium",
        tenant_id="TENANT-NOVASTACK",
        user_role="engineer",
        user_department="Engineering",
        user_id="USR-001",
    )

    candidates = [SimpleCandidate(f"{d}::CHUNK-0001", d, 1.0 - i * 0.1) for i, d in enumerate(docs.keys())]

    package = resolver.resolve_package(
        query=case.query,
        candidates=candidates,
        eval_case=case,
    )

    selected_doc_ids = [e.document_id for e in package.selected_evidence]

    # 1. Foreign tenant must NOT be exposed
    assert "DOC-SEC-TENT-001" not in selected_doc_ids, "Cross-tenant document leaked!"
    assert any(e.document_id == "DOC-SEC-TENT-001" for e in package.excluded_evidence)

    # 2. RBAC restricted document must NOT be exposed
    assert "DOC-SEC-ROLE-001" not in selected_doc_ids, "RBAC restricted document leaked!"
    assert any(e.document_id == "DOC-SEC-ROLE-001" for e in package.excluded_evidence)

    # 3. Adversarial poisoned document must NOT be exposed
    assert "DOC-ADV-PSN-001" not in selected_doc_ids, "Adversarial poisoned document leaked!"
    assert any(e.document_id == "DOC-ADV-PSN-001" for e in package.excluded_evidence)

    # 4. Superseded document must NOT be exposed
    assert "DOC-NOISE-STALE-001" not in selected_doc_ids, "Superseded document resurrected!"
    assert any(e.document_id == "DOC-NOISE-STALE-001" for e in package.excluded_evidence)

    # 5. Legitimate low-authority source matching query intent MUST be preserved under caveat
    assert "DOC-CHAT-001" in selected_doc_ids, "Legitimate query-aware source was improperly excluded!"
    chat_ev = next(e for e in package.selected_evidence if e.document_id == "DOC-CHAT-001")
    assert chat_ev.evidence_status == EvidenceStatus.ACCEPTED_WITH_CAVEAT.value

    # 6. Authoritative document is also preserved
    assert "DOC-AUTH-001" in selected_doc_ids
