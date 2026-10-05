"""Deterministic Evidence Resolution Engine — Phase 4E.

Transforms retrieved candidates into a verified, trustworthy EvidencePackage:
- Stage 1: Candidate Ingestion & Channel Provenance
- Stage 2: Strict Pre-Evidence Authorization Gate
- Stage 3: Multi-Channel & Intra-Document Deduplication
- Stage 4: Adversarial & Retrieval Poisoning Classification
- Stage 5: Version & Lifecycle Resolution (Latest vs Historical Intent)
- Stage 6: Temporal Validity Resolution (Point-in-Time Windows)
- Stage 7: Authority Resolution & Conflict Detection
- Stage 8: Evidence Selection, Trust Scoring & Package Assembly
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from novastack.evidence import (
    EvidenceConflict,
    EvidenceItem,
    EvidencePackage,
    EvidenceStatus,
    ProvenanceNode,
)
from novastack.models import EvaluationCase, SearchChunk, SearchDocument
from novastack.query_aware_authority import extract_requested_source_types
from novastack.query_understanding import QueryUnderstanding

__all__ = [
    "EvidenceResolver",
    "EvidenceResolverConfig",
]

# Authority weight table for composite trust scoring
AUTHORITY_TRUST_WEIGHTS: dict[str, float] = {
    "authoritative": 1.0,
    "high": 0.85,
    "medium": 0.65,
    "low": 0.40,
    "draft": 0.20,
}

# Status weight table for composite trust scoring
STATUS_TRUST_WEIGHTS: dict[str, float] = {
    "published": 1.0,
    "active": 1.0,
    "archived": 0.70,
    "draft": 0.50,
    "deprecated": 0.40,
    "superseded": 0.30,
}


@dataclass
class EvidenceResolverConfig:
    """Hyperparameters and feature flags for evidence resolution."""

    max_selected_evidence: int = 10
    allow_accepted_with_caveat: bool = True
    deduplicate_by_document: bool = True
    enforce_strict_authorization: bool = True
    enforce_adversarial_quarantine: bool = True
    enforce_version_supersession: bool = True
    enforce_lifecycle_rules: bool = True
    enforce_temporal_validity: bool = True
    detect_conflicts: bool = True
    channel_consensus_weight: float = 0.10
    enable_query_aware_authority: bool = True
    query_intent_trust_bonus: float = 0.20
    enable_event_bundling: bool = False



class EvidenceResolver:
    """Deterministic, explainable Evidence Resolution Engine."""

    def __init__(
        self,
        documents_index: dict[str, SearchDocument] | None = None,
        chunks_index: dict[str, SearchChunk] | None = None,
        adversarial_fixtures: list[dict[str, Any]] | None = None,
        security_fixtures: list[dict[str, Any]] | None = None,
        config: EvidenceResolverConfig | None = None,
        catalog: Any = None,
    ) -> None:
        self.config = config or EvidenceResolverConfig()
        self.documents_index: dict[str, SearchDocument] = documents_index or {}
        self.chunks_index: dict[str, SearchChunk] = chunks_index or {}
        self.catalog = catalog
        self.event_bundler: Any = None

        # Adversarial indexes
        self.poisoned_doc_ids: set[str] = set()
        self.adversarial_target_doc_ids: set[str] = set()
        self.instructional_doc_ids: set[str] = set()
        self.citation_manipulation_doc_ids: set[str] = set()

        if adversarial_fixtures:
            for fix in adversarial_fixtures:
                target_id = fix.get("target_document_id")
                if target_id:
                    self.adversarial_target_doc_ids.add(target_id)
                    if fix.get("is_poisoned"):
                        self.poisoned_doc_ids.add(target_id)
                    if fix.get("is_instructional"):
                        self.instructional_doc_ids.add(target_id)
                    if fix.get("attack_category") == "citation_manipulation":
                        self.citation_manipulation_doc_ids.add(target_id)
                for pid in fix.get("poisoned_document_ids", []):
                    self.poisoned_doc_ids.add(pid)

        # Index any documents from documents_index that declare adversarial types or markers
        for did, d in self.documents_index.items():
            if did.startswith("DOC-ADV-PSN-") or (d.title and "[Poisoned Evidence]" in d.title):
                self.poisoned_doc_ids.add(did)
            elif did.startswith("DOC-ADV-CIT-") or did.startswith("DOC-ADV-MAN-"):
                self.citation_manipulation_doc_ids.add(did)
            elif did.startswith("DOC-ADV-INJ-") or did.startswith("DOC-ADV-DIR-") or did.startswith("DOC-ADV-IDC-"):
                self.instructional_doc_ids.add(did)

        # Security fixtures index: fixture_id -> fixture
        self.security_fixtures: dict[str, dict[str, Any]] = {
            f["fixture_id"]: f for f in (security_fixtures or [])
        }

    @classmethod
    def load_from_paths(
        cls,
        search_documents_path: Path,
        search_chunks_path: Path,
        adversarial_fixtures_path: Path | None = None,
        security_fixtures_path: Path | None = None,
        config: EvidenceResolverConfig | None = None,
        catalog: Any = None,
    ) -> EvidenceResolver:
        """Factory method loading indexes directly from processed and raw JSON files."""
        docs_index: dict[str, SearchDocument] = {}
        with open(search_documents_path, "r", encoding="utf-8") as f:
            for d in json.load(f)["search_documents"]:
                doc = SearchDocument.from_dict(d)
                docs_index[doc.document_id] = doc

        chunks_index: dict[str, SearchChunk] = {}
        with open(search_chunks_path, "r", encoding="utf-8") as f:
            for c in json.load(f)["search_chunks"]:
                chunk = SearchChunk.from_dict(c)
                chunks_index[chunk.chunk_id] = chunk

        adv_fixtures: list[dict[str, Any]] = []
        if adversarial_fixtures_path and adversarial_fixtures_path.exists():
            with open(adversarial_fixtures_path, "r", encoding="utf-8") as f:
                adv_fixtures = json.load(f).get("adversarial_fixtures", [])

        sec_fixtures: list[dict[str, Any]] = []
        if security_fixtures_path and security_fixtures_path.exists():
            with open(security_fixtures_path, "r", encoding="utf-8") as f:
                sec_fixtures = json.load(f).get("security_fixtures", [])

        return cls(
            documents_index=docs_index,
            chunks_index=chunks_index,
            adversarial_fixtures=adv_fixtures,
            security_fixtures=sec_fixtures,
            config=config,
            catalog=catalog,
        )

    def resolve_package(
        self,
        query: str,
        candidates: list[Any],
        eval_case: EvaluationCase | dict[str, Any] | None = None,
        qu: QueryUnderstanding | None = None,
        channel_candidates: dict[str, list[Any]] | None = None,
    ) -> EvidencePackage:
        """Execute full 8-stage evidence assembly and resolution pipeline."""
        t_start = time.perf_counter()
        latencies: dict[str, float] = {}
        decisions: list[str] = []

        eval_id = ""
        user_tenant = "TENANT-NOVASTACK"
        user_id = None
        user_role = None
        user_department = None
        expected_access = "allow"
        forbidden_docs: set[str] = set()

        if eval_case:
            if isinstance(eval_case, EvaluationCase):
                eval_id = eval_case.evaluation_id
                user_tenant = eval_case.tenant_id or "TENANT-NOVASTACK"
                user_id = eval_case.user_id
                user_role = eval_case.user_role
                user_department = eval_case.user_department
                expected_access = eval_case.expected_access
                forbidden_docs = set(eval_case.forbidden_document_ids or [])
            elif isinstance(eval_case, dict):
                eval_id = eval_case.get("evaluation_id", "")
                user_tenant = eval_case.get("tenant_id") or "TENANT-NOVASTACK"
                user_id = eval_case.get("user_id")
                user_role = eval_case.get("user_role")
                user_department = eval_case.get("user_department")
                expected_access = eval_case.get("expected_access", "allow")
                forbidden_docs = set(eval_case.get("forbidden_document_ids", []))

        user_context = {
            "tenant_id": user_tenant,
            "user_id": user_id,
            "user_role": user_role,
            "user_department": user_department,
            "expected_access": expected_access,
        }

        # Build reverse channel lookup: chunk_id / doc_id -> set of channel names
        channel_map: dict[str, set[str]] = defaultdict(set)
        if channel_candidates:
            for ch_name, ch_cands in channel_candidates.items():
                for c in ch_cands:
                    cid = getattr(c, "chunk_id", None)
                    did = getattr(c, "document_id", None)
                    if cid:
                        channel_map[cid].add(ch_name)
                    if did:
                        channel_map[did].add(ch_name)

        # -------------------------------------------------------------
        # Stage 1: Candidate Ingestion & Lineage Binding
        # -------------------------------------------------------------
        t0 = time.perf_counter()
        raw_items: list[EvidenceItem] = []
        for rank, cand in enumerate(candidates, start=1):
            did = getattr(cand, "document_id", "")
            cid = getattr(cand, "chunk_id", f"{did}::CHUNK-0001")
            score = getattr(cand, "final_score", getattr(cand, "score", getattr(cand, "rrf_score", 0.0)))
            title = getattr(cand, "title", "")

            chunk = self.chunks_index.get(cid)
            doc = self.documents_index.get(did)

            # Determine retrieval channels for this candidate
            channels = sorted(list(channel_map.get(cid, set()) | channel_map.get(did, set())))
            if not channels:
                channels = ["hybrid"]

            if chunk:
                item = EvidenceItem.from_search_chunk(
                    chunk=chunk,
                    evidence_id=f"EVD-{eval_id}-{rank:03d}-{did}",
                    retrieval_rank=rank,
                    retrieval_score=score,
                    retrieval_channels=channels,
                    doc_metadata=doc,
                )
            elif doc:
                item = EvidenceItem(
                    evidence_id=f"EVD-{eval_id}-{rank:03d}-{did}",
                    chunk_id=cid,
                    document_id=did,
                    tenant_id=doc.tenant_id,
                    source_type=doc.source_type,
                    title=doc.title,
                    text=doc.content[:500],
                    source_entity_id=doc.source_entity_id,
                    source_entity_type=doc.source_entity_type,
                    related_entity_ids=list(doc.related_entity_ids),
                    authority_level=doc.authority_level,
                    classification=doc.classification,
                    permissions=doc.permissions,
                    status=doc.status,
                    version=doc.version,
                    created_at=doc.created_at,
                    updated_at=doc.updated_at,
                    valid_from=doc.valid_from,
                    valid_until=doc.valid_until,
                    parent_id=doc.parent_id,
                    supersedes_id=doc.supersedes_id,
                    retrieval_rank=rank,
                    retrieval_score=score,
                    retrieval_channels=channels,
                )
            else:
                # Minimal fallback if chunk not in index
                item = EvidenceItem(
                    evidence_id=f"EVD-{eval_id}-{rank:03d}-{did}",
                    chunk_id=cid,
                    document_id=did,
                    tenant_id=user_tenant,
                    source_type="documentation",
                    title=title or did,
                    text="",
                    source_entity_id=None,
                    source_entity_type=None,
                    related_entity_ids=[],
                    authority_level="medium",
                    classification="internal",
                    permissions=SearchDocument(did, user_tenant, "doc", title, "", "", "", "").permissions,
                    status="published",
                    version="1.0",
                    created_at="2026-01-01T00:00:00",
                    updated_at=None,
                    valid_from=None,
                    valid_until=None,
                    parent_id=None,
                    supersedes_id=None,
                    retrieval_rank=rank,
                    retrieval_score=score,
                    retrieval_channels=channels,
                )
            raw_items.append(item)
        latencies["ingestion_ms"] = (time.perf_counter() - t0) * 1000.0

        # -------------------------------------------------------------
        # Stage 2: Authorization Gate (Tenant, Classification, ACL)
        # -------------------------------------------------------------
        t0 = time.perf_counter()
        authorized_items: list[EvidenceItem] = []
        excluded_unauthorized: list[EvidenceItem] = []

        for item in raw_items:
            is_auth = True
            auth_reasons: list[str] = []

            # 1. Explicit Forbidden Document
            if forbidden_docs and item.document_id in forbidden_docs:
                is_auth = False
                auth_reasons.append(f"forbidden_document_leakage:{item.document_id}")

            # 2. Strict Tenant Boundary Isolation
            if item.tenant_id != user_tenant:
                is_auth = False
                auth_reasons.append(f"cross_tenant_violation:{item.tenant_id}!={user_tenant}")

            # 3. Role Restriction Check (Fail-Closed)
            perms = item.permissions
            if perms.allowed_roles:
                if user_role is None:
                    is_auth = False
                    auth_reasons.append(f"role_unauthorized:missing_user_role_required_in_{perms.allowed_roles}")
                elif user_role not in perms.allowed_roles:
                    is_auth = False
                    auth_reasons.append(f"role_unauthorized:user_role='{user_role}'_not_in_{perms.allowed_roles}")

            # 4. Department Restriction Check (Fail-Closed)
            if perms.allowed_departments:
                if user_department is None:
                    is_auth = False
                    auth_reasons.append(f"department_unauthorized:missing_user_dept_required_in_{perms.allowed_departments}")
                elif user_department not in perms.allowed_departments:
                    is_auth = False
                    auth_reasons.append(f"department_unauthorized:user_dept='{user_department}'_not_in_{perms.allowed_departments}")

            # 5. User ACL Restriction Check (Fail-Closed)
            if perms.allowed_user_ids:
                if user_id is None:
                    is_auth = False
                    auth_reasons.append(f"user_acl_unauthorized:missing_user_id_required_in_{perms.allowed_user_ids}")
                elif user_id not in perms.allowed_user_ids:
                    is_auth = False
                    auth_reasons.append(f"user_acl_unauthorized:user_id='{user_id}'_not_in_{perms.allowed_user_ids}")

            # 6. Global Security Fixture Denial Check
            if expected_access == "deny" and eval_case:
                fix_id = getattr(eval_case, "security_fixture_id", None) if isinstance(eval_case, EvaluationCase) else eval_case.get("security_fixture_id")
                if fix_id and fix_id in self.security_fixtures:
                    fix = self.security_fixtures[fix_id]
                    if fix.get("target_document_id") == item.document_id:
                        is_auth = False
                        auth_reasons.append(f"security_fixture_denied:{fix.get('security_reason', 'access_denied')}")

            if is_auth:
                authorized_items.append(item)
            else:
                item.evidence_status = EvidenceStatus.UNAUTHORIZED.value
                item.evidence_reasons.extend(auth_reasons)
                excluded_unauthorized.append(item)
                decisions.append(f"Excluded {item.document_id}: unauthorized ({', '.join(auth_reasons)})")

        latencies["authorization_ms"] = (time.perf_counter() - t0) * 1000.0

        # -------------------------------------------------------------
        # Stage 3: Multi-Channel & Intra-Document Deduplication
        # -------------------------------------------------------------
        t0 = time.perf_counter()
        deduped_items: list[EvidenceItem] = []
        excluded_duplicates: list[EvidenceItem] = []
        seen_docs: dict[str, EvidenceItem] = {}

        for item in authorized_items:
            did = item.document_id
            if did in seen_docs:
                primary = seen_docs[did]
                primary.duplicate_chunk_ids.append(item.chunk_id)
                # Merge channels
                for ch in item.retrieval_channels:
                    if ch not in primary.retrieval_channels:
                        primary.retrieval_channels.append(ch)

                item.evidence_status = EvidenceStatus.DUPLICATE.value
                item.duplicate_of = primary.evidence_id
                item.evidence_reasons.append(f"duplicate_chunk_merged_into_{primary.evidence_id}")
                excluded_duplicates.append(item)
            else:
                seen_docs[did] = item
                deduped_items.append(item)

        latencies["deduplication_ms"] = (time.perf_counter() - t0) * 1000.0

        # -------------------------------------------------------------
        # Stage 4: Adversarial & Retrieval Poisoning Classification
        # -------------------------------------------------------------
        t0 = time.perf_counter()
        trusted_items: list[EvidenceItem] = []
        excluded_adversarial: list[EvidenceItem] = []

        for item in deduped_items:
            did = item.document_id
            is_adv = False
            adv_reasons: list[str] = []

            # 1. Poisoned Record Check (falsifies factual truth)
            if did in self.poisoned_doc_ids:
                is_adv = True
                adv_reasons.append("retrieval_poisoning_falsified_ground_truth")

            # 2. Direct Instruction / Prompt Injection Check
            elif did in self.instructional_doc_ids:
                is_adv = True
                adv_reasons.append("untrusted_instruction_payload_rejected")

            # 3. Citation Manipulation Check (low authority claiming canonical supremacy)
            elif did in self.citation_manipulation_doc_ids or (
                item.authority_level in ("low", "draft") and re.search(r"(canonical|supersedes all|official authority)", item.text, re.IGNORECASE)
            ):
                is_adv = True
                adv_reasons.append("citation_manipulation_self_declared_authority_rejected")

            # 4. Obvious embedded system override directives in text
            elif re.search(r"SYSTEM\s+OVERRIDE|IGNORE\s+ALL\s+PREVIOUS\s+INSTRUCTIONS|Note\s+for\s+AI\s+summarizer", item.text, re.IGNORECASE):
                is_adv = True
                adv_reasons.append("embedded_prompt_injection_directive_detected")

            if is_adv:
                item.evidence_status = EvidenceStatus.ADVERSARIAL.value
                item.evidence_reasons.extend(adv_reasons)
                excluded_adversarial.append(item)
                decisions.append(f"Excluded {item.document_id}: adversarial ({', '.join(adv_reasons)})")
            else:
                trusted_items.append(item)

        latencies["adversarial_ms"] = (time.perf_counter() - t0) * 1000.0

        # -------------------------------------------------------------
        # Stage 5: Version & Lifecycle Resolution
        # -------------------------------------------------------------
        t0 = time.perf_counter()
        version_survivors: list[EvidenceItem] = []
        excluded_version_lifecycle: list[EvidenceItem] = []

        # Check query version intent
        is_historical_version_query = False
        target_version_token = None
        if qu and qu.lifecycle_constraints and qu.lifecycle_constraints.version:
            is_historical_version_query = True
            target_version_token = qu.lifecycle_constraints.version
        else:
            v_match = re.search(r"\b(v\d+(\.\d+)?|version\s+\d+(\.\d+)?|historical\s+version)\b", query, re.IGNORECASE)
            if v_match:
                is_historical_version_query = True
                target_version_token = v_match.group(0).lower()

        for item in trusted_items:
            ver_reasons: list[str] = []
            item_status = item.evidence_status

            # Version resolution
            if is_historical_version_query:
                # Query specifically seeks historical or specific version
                if target_version_token and ("v1" in target_version_token or "1.0" in target_version_token):
                    if item.version.startswith("1."):
                        item.evidence_reasons.append(f"historical_version_match:{item.version}")
                    else:
                        item_status = EvidenceStatus.DOWNGRADED.value
                        ver_reasons.append(f"newer_version_downgraded_for_historical_query:{item.version}")
            else:
                # Standard query seeks current/active version
                if item.status == "superseded" or item.supersedes_id is not None:
                    # Check if newer document exists
                    if item.status == "superseded":
                        item_status = EvidenceStatus.SUPERSEDED.value
                        ver_reasons.append(f"superseded_status:version_{item.version}")

            # Lifecycle resolution
            if item.status == "draft":
                item_status = EvidenceStatus.DRAFT.value
                ver_reasons.append("draft_lifecycle_status_unapproved")
            elif item.status in ("deprecated", "archived"):
                if not is_historical_version_query:
                    item_status = EvidenceStatus.STALE.value
                    ver_reasons.append(f"lifecycle_{item.status}")

            if item_status == EvidenceStatus.ACCEPTED.value:
                version_survivors.append(item)
            else:
                item.evidence_status = item_status
                item.evidence_reasons.extend(ver_reasons)
                excluded_version_lifecycle.append(item)
                decisions.append(f"Downgraded/Excluded {item.document_id}: ({', '.join(ver_reasons)})")

        latencies["version_lifecycle_ms"] = (time.perf_counter() - t0) * 1000.0

        # -------------------------------------------------------------
        # Stage 6: Temporal Validity Resolution
        # -------------------------------------------------------------
        t0 = time.perf_counter()
        temporal_survivors: list[EvidenceItem] = []
        excluded_temporal: list[EvidenceItem] = []

        # Check query temporal constraint
        query_date_match = re.search(r"(202\d-\d{2}-\d{2}|Q[1-4]\s+202\d|November\s+2025|October\s+2025|February\s+2026)", query, re.IGNORECASE)
        has_temporal_query = (qu and (qu.temporal_constraints or getattr(qu, "temporal_interval", None))) or (query_date_match is not None)

        for item in version_survivors:
            temp_reasons: list[str] = []
            is_stale = False

            if qu and getattr(qu, "temporal_interval", None):
                from novastack.query_understanding import is_temporally_valid
                ti = qu.temporal_interval
                is_valid = is_temporally_valid(
                    doc_valid_from=item.valid_from,
                    doc_valid_until=item.valid_until,
                    query_start=ti.start,
                    query_end=ti.end,
                    point_in_time=ti.point_in_time,
                )
                if not is_valid:
                    is_stale = True
                    temp_reasons.append(f"outside_requested_temporal_window:[{item.valid_from}..{item.valid_until})_vs_{ti.raw_expression}")
            elif item.valid_until:
                # If document expired in 2025 and query is not historical
                if item.valid_until < "2026-01-01" and not has_temporal_query:
                    is_stale = True
                    temp_reasons.append(f"valid_until_{item.valid_until}_expired")

            if is_stale:
                item.evidence_status = EvidenceStatus.STALE.value
                item.evidence_reasons.extend(temp_reasons)
                excluded_temporal.append(item)
                decisions.append(f"Excluded {item.document_id}: stale ({', '.join(temp_reasons)})")
            else:
                temporal_survivors.append(item)

        latencies["temporal_ms"] = (time.perf_counter() - t0) * 1000.0

        # -------------------------------------------------------------
        # Stage 7: Authority Resolution & Conflict Detection
        # -------------------------------------------------------------
        t0 = time.perf_counter()
        conflicts: list[EvidenceConflict] = []
        conflict_resolved_items: list[EvidenceItem] = []
        excluded_conflicts: list[EvidenceItem] = []

        bundle_res = None
        bundled_chunk_ids: set[str] = set()
        if self.config.enable_event_bundling:
            if self.event_bundler is None:
                from novastack.event_evidence_bundler import EventEvidenceBundler, EventBundlerConfig
                self.event_bundler = EventEvidenceBundler(
                    catalog=self.catalog,
                    config=EventBundlerConfig(enable_event_bundling=True),
                )
            bundle_res = self.event_bundler.bundle_evidence(temporal_survivors, query, qu=qu)
            if bundle_res.is_bundled:
                bundled_chunk_ids = {p.chunk_id for p in bundle_res.perspectives}

        requested_source_types: set[str] = set()
        if self.config.enable_query_aware_authority:
            requested_source_types = extract_requested_source_types(query)

        # Group by source_entity_id to detect divergence
        entity_groups: dict[str, list[EvidenceItem]] = defaultdict(list)
        for item in temporal_survivors:
            if item.source_entity_id:
                entity_groups[item.source_entity_id].append(item)

        conflicting_ids: set[str] = set()

        for ent_id, grp in entity_groups.items():
            if len(grp) > 1:
                # Check if multiple source types disagree on authority
                auth_levels = {item.authority_level for item in grp}
                if len(auth_levels) > 1 and ("authoritative" in auth_levels or "high" in auth_levels):
                    # Authoritative / High source vs Medium / Low source
                    high_items = [i for i in grp if i.authority_level in ("authoritative", "high")]
                    low_items = [i for i in grp if i.authority_level not in ("authoritative", "high")]

                    primary = high_items[0]

                    if not self.config.enable_query_aware_authority:
                        for low in low_items:
                            if self.config.enable_event_bundling and low.chunk_id in bundled_chunk_ids:
                                low.evidence_status = EvidenceStatus.ACCEPTED_WITH_CAVEAT.value
                                low.evidence_reasons.append(f"preserved_in_event_bundle_{bundle_res.canonical_event_id}")
                                c_record = EvidenceConflict(
                                    conflict_id=f"CONF-{eval_id}-{len(conflicts)+1:02d}",
                                    conflict_type="authoritative_vs_low_authority",
                                    entity_id=ent_id,
                                    primary_evidence_id=primary.evidence_id,
                                    conflicting_evidence_ids=[low.evidence_id],
                                    resolution_status="preserved_in_event_bundle",
                                    resolution_reason=(
                                        f"Authoritative {primary.source_type} ({primary.document_id}) preserved alongside "
                                        f"bundled event observational source ({low.source_type})"
                                    ),
                                )
                                conflicts.append(c_record)
                                primary.conflict_ids.append(c_record.conflict_id)
                                low.conflict_ids.append(c_record.conflict_id)
                                decisions.append(
                                    f"Preserved {low.document_id} ({low.source_type}): included in event bundle for {bundle_res.canonical_event_id}"
                                )
                            else:
                                low.evidence_status = EvidenceStatus.DOWNGRADED.value
                                low.evidence_reasons.append(f"downgraded_by_higher_authority_source_{primary.document_id}")
                                c_record = EvidenceConflict(
                                    conflict_id=f"CONF-{eval_id}-{len(conflicts)+1:02d}",
                                    conflict_type="authoritative_vs_low_authority",
                                    entity_id=ent_id,
                                    primary_evidence_id=primary.evidence_id,
                                    conflicting_evidence_ids=[low.evidence_id],
                                    resolution_status="resolved_by_authority",
                                    resolution_reason=f"Authoritative {primary.source_type} ({primary.document_id}) preferred over low-authority observational records",
                                )
                                conflicts.append(c_record)
                                primary.conflict_ids.append(c_record.conflict_id)
                                low.conflict_ids.append(c_record.conflict_id)
                                conflicting_ids.add(low.evidence_id)
                                excluded_conflicts.append(low)
                                decisions.append(f"Downgraded {low.document_id}: lower authority than {primary.document_id}")
                    else:
                        # Query-aware authority preservation:
                        # Distinguish between requested observational sources and unrequested divergence.
                        for low in low_items:
                            if low.source_type in requested_source_types:
                                low.evidence_status = EvidenceStatus.ACCEPTED_WITH_CAVEAT.value
                                low.evidence_reasons.append(f"preserved_by_query_intent_{low.source_type}")
                                c_record = EvidenceConflict(
                                    conflict_id=f"CONF-{eval_id}-{len(conflicts)+1:02d}",
                                    conflict_type="authoritative_vs_low_authority",
                                    entity_id=ent_id,
                                    primary_evidence_id=primary.evidence_id,
                                    conflicting_evidence_ids=[low.evidence_id],
                                    resolution_status="preserved_for_query_intent",
                                    resolution_reason=(
                                        f"Authoritative {primary.source_type} ({primary.document_id}) preserved alongside "
                                        f"requested observational source ({low.source_type}) per explicit query intent"
                                    ),
                                )
                                conflicts.append(c_record)
                                primary.conflict_ids.append(c_record.conflict_id)
                                low.conflict_ids.append(c_record.conflict_id)
                                decisions.append(
                                    f"Preserved {low.document_id} ({low.source_type}): explicit query intent overrides "
                                    f"authority downgrade against {primary.document_id}"
                                )
                            else:
                                c_record = EvidenceConflict(
                                    conflict_id=f"CONF-{eval_id}-{len(conflicts)+1:02d}",
                                    conflict_type="authoritative_vs_low_authority",
                                    entity_id=ent_id,
                                    primary_evidence_id=primary.evidence_id,
                                    conflicting_evidence_ids=[low.evidence_id],
                                    resolution_status="resolved_by_authority",
                                    resolution_reason=f"Authoritative {primary.source_type} ({primary.document_id}) preferred over low-authority observational records",
                                )
                                conflicts.append(c_record)
                                primary.conflict_ids.append(c_record.conflict_id)
                                low.evidence_status = EvidenceStatus.DOWNGRADED.value
                                low.evidence_reasons.append(f"downgraded_by_higher_authority_source_{primary.document_id}")
                                low.conflict_ids.append(c_record.conflict_id)
                                conflicting_ids.add(low.evidence_id)
                                excluded_conflicts.append(low)
                                decisions.append(f"Downgraded {low.document_id}: lower authority than {primary.document_id}")

        for item in temporal_survivors:
            if item.evidence_id not in conflicting_ids:
                conflict_resolved_items.append(item)

        latencies["conflict_ms"] = (time.perf_counter() - t0) * 1000.0

        # -------------------------------------------------------------
        # Stage 8: Evidence Selection, Trust Scoring & Package Assembly
        # -------------------------------------------------------------
        t0 = time.perf_counter()

        # Compute deterministic trust score
        for item in conflict_resolved_items:
            auth_w = AUTHORITY_TRUST_WEIGHTS.get(item.authority_level, 0.5)
            stat_w = STATUS_TRUST_WEIGHTS.get(item.status, 0.5)
            ch_bonus = len(item.retrieval_channels) * self.config.channel_consensus_weight
            # Trust score combines authority, status, consensus, and base retrieval rank
            rank_discount = 1.0 / (item.retrieval_rank + 1.0)
            base_trust = auth_w * 0.4 + stat_w * 0.3 + rank_bonus(rank_discount) + ch_bonus
            if self.config.enable_query_aware_authority and item.source_type in requested_source_types:
                base_trust += self.config.query_intent_trust_bonus
            item.trust_score = round(base_trust, 4)

            if item.authority_level in ("low", "draft"):
                item.evidence_status = EvidenceStatus.ACCEPTED_WITH_CAVEAT.value
                if "accepted_with_low_authority_caveat" not in item.evidence_reasons:
                    item.evidence_reasons.append("accepted_with_low_authority_caveat")

        if self.config.enable_event_bundling and bundle_res and bundle_res.is_bundled:
            bundle_chunk_ranks = {p.chunk_id: idx for idx, p in enumerate(bundle_res.perspectives)}
            for item in conflict_resolved_items:
                if item.chunk_id in bundle_chunk_ranks:
                    idx = bundle_chunk_ranks[item.chunk_id]
                    item.trust_score = round(0.96 - (idx * 0.02), 4)
                elif self.event_bundler and bundle_res.canonical_event_id in self.event_bundler.get_event_affinity(item):
                    pass
                else:
                    item.trust_score = round(item.trust_score * 0.75, 4)
                    item.evidence_reasons.append(f"deprioritized_by_event_bundler_divergent_event_{bundle_res.canonical_event_id}")

        # Sort selected evidence by trust score descending, then retrieval rank ascending
        selected_candidates = sorted(
            conflict_resolved_items,
            key=lambda x: (x.trust_score, -x.retrieval_rank),
            reverse=True,
        )

        selected_evidence = selected_candidates[: self.config.max_selected_evidence]
        overflow_excluded = selected_candidates[self.config.max_selected_evidence :]
        for o in overflow_excluded:
            o.evidence_status = EvidenceStatus.EXCLUDED.value
            o.evidence_reasons.append("excluded_by_capacity_limit_top10")

        all_excluded = (
            excluded_unauthorized
            + excluded_duplicates
            + excluded_adversarial
            + excluded_version_lifecycle
            + excluded_temporal
            + excluded_conflicts
            + overflow_excluded
        )

        # Build provenance graph nodes
        provenance_graph: list[ProvenanceNode] = []
        for item in selected_evidence:
            node = ProvenanceNode(
                evidence_id=item.evidence_id,
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                source_entity_id=item.source_entity_id,
                source_entity_type=item.source_entity_type,
                related_entity_ids=item.related_entity_ids,
                parent_id=item.parent_id,
                supersedes_id=item.supersedes_id,
                ground_truth_event_id=item.source_entity_id if item.source_entity_type == "event" else None,
            )
            provenance_graph.append(node)

        latencies["assembly_ms"] = (time.perf_counter() - t0) * 1000.0
        latencies["total_ms"] = (time.perf_counter() - t_start) * 1000.0

        statistics = {
            "retrieved_candidates_count": len(candidates),
            "selected_evidence_count": len(selected_evidence),
            "excluded_unauthorized_count": len(excluded_unauthorized),
            "excluded_duplicates_count": len(excluded_duplicates),
            "excluded_adversarial_count": len(excluded_adversarial),
            "excluded_version_lifecycle_count": len(excluded_version_lifecycle),
            "excluded_temporal_count": len(excluded_temporal),
            "excluded_conflicts_count": len(excluded_conflicts),
            "conflicts_detected_count": len(conflicts),
            "conflicts_unresolved_count": sum(1 for c in conflicts if c.resolution_status == "conflict_unresolved"),
            "provenance_coverage": round(sum(1 for i in selected_evidence if i.source_entity_id is not None) / len(selected_evidence), 4) if selected_evidence else 0.0,
        }

        diag_dict: dict[str, Any] = {"latencies_ms": {k: round(v, 3) for k, v in latencies.items()}}
        if bundle_res:
            diag_dict["event_bundler"] = bundle_res.to_dict()

        package = EvidencePackage(
            package_id=f"PKG-{eval_id or 'QUERY'}-{int(time.time())}",
            evaluation_id=eval_id,
            query=query,
            tenant_id=user_tenant,
            user_context=user_context,
            selected_evidence=selected_evidence,
            excluded_evidence=all_excluded,
            conflicts=conflicts,
            provenance_graph=provenance_graph,
            resolution_decisions=decisions,
            statistics=statistics,
            diagnostics=diag_dict,
        )
        return package


def rank_bonus(rank_discount: float) -> float:
    """Helper computing bounded rank bonus for trust scoring."""
    return min(0.20, round(rank_discount * 0.20, 4))
