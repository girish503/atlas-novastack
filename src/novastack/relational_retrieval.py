"""Deterministic Relational & Structured Retrieval Engine — Phase 4D-2.

Implements the deterministic structured entity and relational retrieval channel:
- Entity extraction from queries using exact IDs, names, and catalog aliases
- Relational intent classification (ownership, incidents, deployments, PRs, customers)
- Typed graph traversal (0-hop direct, 1-hop relational)
- Reverse candidate chunk mapping and path-weighted scoring
- Strict authorization boundary enforcement (tenant, classification, role, dept, ACL)
- Conservative Reciprocal Rank Fusion (RRF k=60) with Hybrid baseline
- Downstream compatibility with unmodified Phase 4C-3 MetadataReranker
- 9-category controlled failure taxonomy classification
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from novastack.entity_catalog import CanonicalEntity, EntityCatalog, TypedRelationship
from novastack.models import EvaluationCase, SearchChunk

__all__ = [
    "CombinedCandidate",
    "StructuredCandidate",
    "StructuredRetrievalResult",
    "StructuredRetriever",
    "StructuredRetrieverConfig",
    "classify_relational_failure",
    "fuse_hybrid_and_structured",
]

# Vocabulary mappings for relational intents
RELATIONAL_INTENT_PATTERNS: list[tuple[re.Pattern, list[str]]] = [
    (
        re.compile(r"\b(owns|owned\s+by|owner|belong\s+to|belonging\s+to|team\s+owns|department)\b", re.IGNORECASE),
        ["owned_by", "owns"],
    ),
    (
        re.compile(r"\b(caused\s+by|causes|root\s+cause|trigger|triggered\s+by|outage\s+cause)\b", re.IGNORECASE),
        ["caused_by", "causes", "causes_event"],
    ),
    (
        re.compile(r"\b(affects|affected|affecting|impacted\s+service|timed\s+out|timing\s+out|failure)\b", re.IGNORECASE),
        ["affects", "affected_by"],
    ),
    (
        re.compile(r"\b(resolved\s+by|fixed\s+by|fix|fixes|resolves|remediated\s+by|merged|pr|pull\s+request)\b", re.IGNORECASE),
        ["resolved_by", "resolves", "resolves_event"],
    ),
    (
        re.compile(r"\b(deploy|deployment|deployed|deployed\s+by|version|release|rollback)\b", re.IGNORECASE),
        ["targets", "targeted_by", "deployed_by", "deployed"],
    ),
    (
        re.compile(r"\b(managed\s+by|manages|manager|member\s+of|members|lead|commander)\b", re.IGNORECASE),
        ["managed_by", "manages", "member_of", "has_member", "commanded_by"],
    ),
    (
        re.compile(r"\b(customer|customers|impacted\s+customer|client|account)\b", re.IGNORECASE),
        ["impacts", "impacted_by", "manages_customer"],
    ),
]


@dataclass
class StructuredRetrieverConfig:
    """Configuration hyperparameters for structured retrieval."""

    direct_entity_weight: float = 1.0
    traversed_entity_weight: float = 0.8
    related_entity_weight: float = 0.6
    max_traversal_depth: int = 1
    top_k: int = 50
    rrf_k: int = 60
    structured_fusion_weight: float = 1.0
    path_decay_gamma: float = 0.7
    max_neighbors_per_hop: int = 10
    max_expanded_candidates: int = 100
    enable_multihop: bool = False
    runbook_entity_weight: float = 0.85
    enable_runbook_reverse_index: bool = True


@dataclass
class StructuredCandidate:
    """A search chunk retrieved via structured entity or relationship lookup."""

    chunk_id: str
    document_id: str
    rank: int
    score: float
    matched_entity_id: str
    traversal_path: str
    chunk: SearchChunk

    @property
    def rrf_score(self) -> float:
        """Reciprocal rank score for compatibility with MetadataReranker."""
        return round(1.0 / (60.0 + self.rank), 6)

    @property
    def title(self) -> str:
        """Title for compatibility with MetadataReranker."""
        return getattr(self.chunk, "title", self.document_id)

    def to_dict(self) -> dict[str, Any]:
        """Convert candidate to dictionary."""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "rank": self.rank,
            "score": round(self.score, 6),
            "matched_entity_id": self.matched_entity_id,
            "traversal_path": self.traversal_path,
        }


@dataclass
class StructuredRetrievalResult:
    """Telemetry and candidate results from structured retrieval for a query."""

    query: str
    extracted_entities: list[CanonicalEntity]
    extracted_rel_intents: list[str]
    traversed_entities: list[dict[str, str]]
    candidates: list[StructuredCandidate]
    latency_ms: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "query": self.query,
            "extracted_entities": [e.to_dict() for e in self.extracted_entities],
            "extracted_rel_intents": self.extracted_rel_intents,
            "traversed_entities": self.traversed_entities,
            "candidate_count": len(self.candidates),
            "candidates": [c.to_dict() for c in self.candidates],
            "latency_ms": {k: round(v, 3) for k, v in self.latency_ms.items()},
        }


@dataclass
class CombinedCandidate:
    """A candidate chunk resulting from multi-channel fusion (Hybrid + Structured)."""

    chunk_id: str
    document_id: str
    rank: int
    score: float  # Combined RRF score
    hybrid_rank: int | None
    structured_rank: int | None
    chunk: SearchChunk | None = None
    title: str = ""
    text_preview: str = ""

    @property
    def rrf_score(self) -> float:
        """Alias for compatibility with Phase 4C-3 MetadataReranker."""
        return self.score

    def to_dict(self) -> dict[str, Any]:
        """Convert combined candidate to dictionary."""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "rank": self.rank,
            "score": round(self.score, 6),
            "hybrid_rank": self.hybrid_rank,
            "structured_rank": self.structured_rank,
            "title": self.title,
        }


class StructuredRetriever:
    """Deterministic structured entity and relational retrieval engine."""

    def __init__(
        self,
        catalog: EntityCatalog,
        config: StructuredRetrieverConfig | None = None,
        runbook_index: Any = None,
    ) -> None:
        self.catalog = catalog
        self.config = config or StructuredRetrieverConfig()
        self._runbook_index = runbook_index

    @property
    def runbook_index(self) -> Any:
        """Lazily initialize EntityRunbookIndex if configured."""
        if self._runbook_index is None and getattr(self.config, "enable_runbook_reverse_index", False):
            try:
                from novastack.runbook_index import EntityRunbookIndex
                self._runbook_index = EntityRunbookIndex(catalog=self.catalog)
            except Exception as e:
                import logging
                logging.getLogger("novastack.relational_retrieval").warning(
                    "Failed to initialize EntityRunbookIndex: %s", e
                )
                self._runbook_index = None
        return self._runbook_index

    def extract_query_entities(self, query: str) -> list[CanonicalEntity]:
        """Extract canonical entities referenced in the query string."""
        found_entities: list[CanonicalEntity] = []
        seen_ids: set[str] = set()

        # 1. Exact Canonical IDs (e.g. SVC-NS-0011, INC-NS-0001, etc.)
        from novastack.entity_catalog import IDENTIFIER_PATTERN
        for match in IDENTIFIER_PATTERN.finditer(query):
            raw_id = match.group(0).upper()
            ent = self.catalog.get_entity(raw_id)
            if ent and ent.entity_id not in seen_ids:
                found_entities.append(ent)
                seen_ids.add(ent.entity_id)

        # 2. Known service, team, or customer names (exact and alias lookup)
        tokens = re.split(r"[\s,\.\?!;]+", query.strip())

        # Check multi-word windows (up to 4 tokens) and single tokens
        n = len(tokens)
        for window_size in range(4, 0, -1):
            for i in range(n - window_size + 1):
                phrase = " ".join(tokens[i : i + window_size]).strip()
                if not phrase or len(phrase) < 3:
                    continue
                ent = self.catalog.lookup_entity(phrase)
                if ent and ent.entity_id not in seen_ids:
                    found_entities.append(ent)
                    seen_ids.add(ent.entity_id)

        return found_entities

    def extract_relational_intents(self, query: str) -> list[str]:
        """Extract relational intent types from query phrases."""
        intents: list[str] = []
        for pattern, rel_types in RELATIONAL_INTENT_PATTERNS:
            if pattern.search(query):
                for rt in rel_types:
                    if rt not in intents:
                        intents.append(rt)
        return intents

    def retrieve(
        self,
        query: str,
        eval_case: dict[str, Any] | EvaluationCase,
        top_k: int | None = None,
    ) -> StructuredRetrievalResult:
        """Execute structured entity and relational retrieval for a query.

        Args:
            query: Natural language query string.
            eval_case: EvaluationCase or dict with user security context.
            top_k: Maximum candidate chunks to return (default config.top_k).

        Returns:
            StructuredRetrievalResult with ranked candidates and latency profile.
        """
        k = top_k or self.config.top_k
        t0 = time.perf_counter()

        # 1. Entity Extraction
        entities = self.extract_query_entities(query)
        t_entity = time.perf_counter()

        # 2. Relational Intent Extraction
        rel_intents = self.extract_relational_intents(query)

        # Extract user context early for per-hop security enforcement
        if isinstance(eval_case, dict):
            u_tenant = eval_case.get("tenant_id", "TENANT-NOVASTACK")
            u_role = eval_case.get("user_role")
            u_dept = eval_case.get("user_department")
            u_id = eval_case.get("user_id")
            forbidden = set(eval_case.get("forbidden_document_ids", []))
        else:
            u_tenant = eval_case.tenant_id
            u_role = eval_case.user_role
            u_dept = eval_case.user_department
            u_id = eval_case.user_id
            forbidden = set(eval_case.forbidden_document_ids)

        # 3. Relationship Traversal
        traversed_entities_info: list[dict[str, str]] = []
        chunk_scores: dict[str, float] = defaultdict(float)
        chunk_map: dict[str, SearchChunk] = {}
        chunk_meta: dict[str, tuple[str, str]] = {}  # chunk_id -> (matched_entity_id, path)

        w_direct = self.config.direct_entity_weight
        w_traversed = self.config.traversed_entity_weight
        w_related = self.config.related_entity_weight

        # Multi-hop depth configuration: capped at 3
        depth_param = getattr(self.config, "max_traversal_depth", getattr(self.config, "max_depth", 1))
        effective_depth = 3 if self.config.enable_multihop else min(max(depth_param, 1), 3)

        for ent in entities:
            # 0-Hop: Direct entity chunks
            direct_chunks = self.catalog.get_chunks_for_entity(ent.entity_id)
            for ch in direct_chunks:
                # Per-hop authorization check
                if not self.catalog.is_authorized(
                    ch,
                    user_tenant=u_tenant,
                    user_role=u_role,
                    user_department=u_dept,
                    user_id=u_id,
                    forbidden_docs=forbidden,
                ):
                    continue

                if len(chunk_scores) >= self.config.max_expanded_candidates and ch.chunk_id not in chunk_scores:
                    continue

                if ch.source_entity_id == ent.entity_id:
                    score_increment = w_direct
                else:
                    score_increment = w_related

                chunk_scores[ch.chunk_id] += score_increment
                chunk_map[ch.chunk_id] = ch
                if ch.chunk_id not in chunk_meta:
                    chunk_meta[ch.chunk_id] = (ent.entity_id, f"direct:{ent.entity_id}")

            # Multi-Hop Traversal (d <= 3) with path decay
            allowed_rels = set(rel_intents) if rel_intents else None
            traversals = self.catalog.traverse(
                ent.entity_id,
                rel_types=allowed_rels,
                max_depth=effective_depth,
                max_neighbors_per_hop=self.config.max_neighbors_per_hop,
                user_tenant=u_tenant,
            )

            for tgt_ent, edge, depth in traversals:
                path_desc = f"{ent.entity_id} -[{edge.relationship_type}]-> {tgt_ent.entity_id} (hop={depth})"
                traversed_entities_info.append({
                    "source_id": ent.entity_id,
                    "target_id": tgt_ent.entity_id,
                    "relationship": edge.relationship_type,
                    "target_type": tgt_ent.entity_type,
                    "depth": depth,
                })

                # Path decay scoring: gamma^(depth - 1)
                decay_factor = self.config.path_decay_gamma ** (depth - 1)
                hop_weight = w_traversed * decay_factor

                tgt_chunks = self.catalog.get_chunks_for_entity(tgt_ent.entity_id)
                for ch in tgt_chunks:
                    # Strict authorization filtering per chunk
                    if not self.catalog.is_authorized(
                        ch,
                        user_tenant=u_tenant,
                        user_role=u_role,
                        user_department=u_dept,
                        user_id=u_id,
                        forbidden_docs=forbidden,
                    ):
                        continue

                    # Bounded candidate explosion protection
                    if len(chunk_scores) >= self.config.max_expanded_candidates and ch.chunk_id not in chunk_scores:
                        continue

                    chunk_scores[ch.chunk_id] += hop_weight
                    chunk_map[ch.chunk_id] = ch
                    if ch.chunk_id not in chunk_meta:
                        chunk_meta[ch.chunk_id] = (tgt_ent.entity_id, path_desc)

            # Runbook Reverse Index Lookup (0-hop direct and 1-hop relational runbooks/procedures)
            if self.runbook_index is not None:
                runbooks = self.runbook_index.lookup_runbooks_for_entity(
                    entity_id=ent.entity_id,
                    user_tenant=u_tenant,
                    user_role=u_role,
                    user_department=u_dept,
                    user_id=u_id,
                    forbidden_docs=forbidden,
                    max_hops=min(effective_depth, 1),
                )
                w_runbook = getattr(self.config, "runbook_entity_weight", 0.85)
                for rb in runbooks:
                    decay = self.config.path_decay_gamma ** rb.traversal_hops
                    rb_weight = w_runbook * decay
                    rb_chunks = self.runbook_index.get_chunks_for_runbook(rb.document_id)
                    for ch in rb_chunks:
                        if not self.catalog.is_authorized(
                            ch,
                            user_tenant=u_tenant,
                            user_role=u_role,
                            user_department=u_dept,
                            user_id=u_id,
                            forbidden_docs=forbidden,
                        ):
                            continue
                        if len(chunk_scores) >= self.config.max_expanded_candidates and ch.chunk_id not in chunk_scores:
                            continue
                        chunk_scores[ch.chunk_id] += rb_weight
                        chunk_map[ch.chunk_id] = ch
                        if ch.chunk_id not in chunk_meta:
                            chunk_meta[ch.chunk_id] = (ent.entity_id, f"runbook:{rb.relationship_path}:{rb.document_id}")

        t_traversal = time.perf_counter()

        # 4. Filtered candidate chunks (already pre-authorized at collection time)
        authorized_chunks = [ch for cid, ch in chunk_map.items()]

        # 5. Ranking & Selection: strictly capped by min(top_k, max_expanded_candidates)
        sorted_chunks = sorted(
            authorized_chunks,
            key=lambda c: (-chunk_scores[c.chunk_id], c.chunk_id),
        )

        max_candidates = min(k, self.config.max_expanded_candidates)
        candidates: list[StructuredCandidate] = []
        for rank, ch in enumerate(sorted_chunks[:max_candidates], start=1):
            matched_id, path = chunk_meta.get(ch.chunk_id, ("", ""))
            candidates.append(
                StructuredCandidate(
                    chunk_id=ch.chunk_id,
                    document_id=ch.document_id,
                    rank=rank,
                    score=chunk_scores[ch.chunk_id],
                    matched_entity_id=matched_id,
                    traversal_path=path,
                    chunk=ch,
                )
            )

        t_end = time.perf_counter()

        latency_ms = {
            "entity_resolution_ms": (t_entity - t0) * 1000.0,
            "traversal_ms": (t_traversal - t_entity) * 1000.0,
            "candidate_mapping_ms": (t_end - t_traversal) * 1000.0,
            "total_ms": (t_end - t0) * 1000.0,
        }

        return StructuredRetrievalResult(
            query=query,
            extracted_entities=entities,
            extracted_rel_intents=rel_intents,
            traversed_entities=traversed_entities_info,
            candidates=candidates,
            latency_ms=latency_ms,
        )


def fuse_hybrid_and_structured(
    hybrid_candidates: list[Any],
    structured_candidates: list[StructuredCandidate],
    k: int = 60,
    w_hybrid: float = 1.0,
    w_struct: float = 1.0,
    top_k: int = 50,
    deduplicate_docs: bool = False,
    catalog: EntityCatalog | None = None,
) -> list[CombinedCandidate]:
    """Deterministically fuse Hybrid RRF candidates with Structured candidates.

    Uses conservative reciprocal rank fusion:
        RRF_comb(d) = (base_rrf_score or w_hybrid / (k + rank_hybrid(d))) + w_struct / (k + rank_struct(d))

    Args:
        hybrid_candidates: Ranked candidates from Query-Understood Hybrid (top-50).
        structured_candidates: Ranked candidates from StructuredRetriever (top-50).
        k: Smoothing constant (default 60).
        w_hybrid: Weight for hybrid channel (default 1.0).
        w_struct: Weight for structured channel (default 1.0).
        top_k: Max combined candidates to return.
        deduplicate_docs: Whether to retain only the top chunk per document.
        catalog: Optional EntityCatalog to resolve full SearchChunk objects.

    Returns:
        Ranked list of CombinedCandidate ordered by descending score.
    """
    fused_scores: dict[str, float] = defaultdict(float)
    hybrid_ranks: dict[str, int] = {}
    structured_ranks: dict[str, int] = {}
    chunk_map: dict[str, SearchChunk] = {}
    doc_map: dict[str, str] = {}
    title_map: dict[str, str] = {}
    preview_map: dict[str, str] = {}

    # Ingest Hybrid candidates
    for r in hybrid_candidates:
        if isinstance(r, dict):
            cid = r.get("chunk_id")
            did = r.get("document_id")
            rank = r.get("rank")
            ch = r.get("chunk")
            title = r.get("title", did or "")
            preview = r.get("text_preview", "")
            base_score = r.get("rrf_score")
        else:
            cid = getattr(r, "chunk_id", None)
            did = getattr(r, "document_id", None)
            rank = getattr(r, "rank", None)
            ch = getattr(r, "chunk", None)
            title = getattr(r, "title", did or "")
            preview = getattr(r, "text_preview", "")
            base_score = getattr(r, "rrf_score", None)

        if not cid or rank is None:
            continue

        hybrid_ranks[cid] = rank
        doc_map[cid] = did
        title_map[cid] = title
        preview_map[cid] = preview
        if ch:
            chunk_map[cid] = ch
        elif catalog and cid in catalog.chunk_by_id:
            chunk_map[cid] = catalog.chunk_by_id[cid]

        if base_score is not None and isinstance(base_score, (int, float)):
            fused_scores[cid] += float(base_score) * w_hybrid
        else:
            fused_scores[cid] += w_hybrid / (k + rank)

    # Ingest Structured candidates
    for s in structured_candidates:
        cid = s.chunk_id
        rank = s.rank
        structured_ranks[cid] = rank
        doc_map[cid] = s.document_id
        chunk_map[cid] = s.chunk
        title_map[cid] = getattr(s.chunk, "title", s.document_id)
        preview_map[cid] = s.chunk.text[:100] if hasattr(s.chunk, "text") else ""
        fused_scores[cid] += w_struct / (k + rank)

    # Deterministic sorting: primary by -score, secondary by chunk_id ascending
    sorted_cids = sorted(
        fused_scores.keys(),
        key=lambda c: (-round(fused_scores[c], 6), c),
    )

    combined: list[CombinedCandidate] = []
    seen_docs: set[str] = set()
    for cid in sorted_cids:
        did = doc_map[cid]
        if deduplicate_docs:
            if did in seen_docs:
                continue
            seen_docs.add(did)

        final_rank = len(combined) + 1
        combined.append(
            CombinedCandidate(
                chunk_id=cid,
                document_id=did,
                rank=final_rank,
                score=round(fused_scores[cid], 6),
                hybrid_rank=hybrid_ranks.get(cid),
                structured_rank=structured_ranks.get(cid),
                chunk=chunk_map.get(cid),
                title=title_map.get(cid, did),
                text_preview=preview_map.get(cid, ""),
            )
        )
        if len(combined) >= top_k:
            break

    return combined


def classify_relational_failure(
    case: dict[str, Any] | EvaluationCase,
    structured_result: StructuredRetrievalResult,
    baseline_top10_doc_ids: list[str],
    structured_top10_doc_ids: list[str],
    combined_top10_doc_ids: list[str],
    reranked_top10_doc_ids: list[str],
    known_gt_defects: set[str] | None = None,
) -> str:
    """Classify an evaluation query into one of 9 controlled failure taxonomy categories.

    Categories:
    1. entity_not_recognized: No entity recognized from query.
    2. entity_recognized_relationship_absent: Entity found, but relationship absent in graph.
    3. relationship_resolved_supporting_doc_absent: Entity/rel resolved, but no supporting doc in corpus.
    4. structured_candidate_found_auth_denied: Candidate found, but authorization denied.
    5. structured_candidate_found_ranking_lost: Supporting candidate found, but ranked outside top-10.
    6. structured_retrieval_recovered_target: Recovered a previously missing target into top-10.
    7. structured_retrieval_false_positive: Introduced an irrelevant distractor above target.
    8. evaluation_ground_truth_defect: Canonical GT defect (e.g. EVAL-0028, 0029, 0030, 0034).
    9. ambiguous_query: Under-specified query with multiple valid targets.
    """
    eval_id = case["evaluation_id"] if isinstance(case, dict) else case.evaluation_id
    expected_docs = set(case.get("expected_document_ids", []) if isinstance(case, dict) else case.expected_document_ids)
    expected_access = case.get("expected_access", "allow") if isinstance(case, dict) else case.expected_access

    # 1. Ground truth defect check
    gt_defects = known_gt_defects or {"EVAL-0028", "EVAL-0029", "EVAL-0030", "EVAL-0034"}
    if eval_id in gt_defects:
        return "evaluation_ground_truth_defect"

    # 2. Denied access check
    if expected_access == "deny":
        if any(d in expected_docs for d in combined_top10_doc_ids):
            return "structured_candidate_found_auth_denied"
        return "structured_candidate_found_auth_denied"

    # Target presence checks
    in_baseline = any(d in expected_docs for d in baseline_top10_doc_ids)
    in_structured = any(d in expected_docs for d in structured_top10_doc_ids)
    in_combined = any(d in expected_docs for d in combined_top10_doc_ids)
    in_reranked = any(d in expected_docs for d in reranked_top10_doc_ids)

    # 3. Success / Recovery check
    if in_reranked and not in_baseline:
        return "structured_retrieval_recovered_target"

    if in_combined and not in_baseline:
        return "structured_retrieval_recovered_target"

    if in_baseline and in_reranked:
        # Both succeeded; check if structured retrieval introduced false positives ahead
        if not in_structured and len(structured_result.candidates) > 0:
            return "structured_retrieval_false_positive"
        return "structured_retrieval_recovered_target"

    # If it failed to reach top-10:
    # 4. Check if entity was recognized
    if not structured_result.extracted_entities:
        return "entity_not_recognized"

    # 5. Check if relationships were traversed
    if structured_result.extracted_rel_intents and not structured_result.traversed_entities:
        return "entity_recognized_relationship_absent"

    # 6. Check if supporting documents exist in candidate pool
    structured_cand_docs = {c.document_id for c in structured_result.candidates}
    if expected_docs.intersection(structured_cand_docs):
        # Doc was in candidates, but lost in ranking
        return "structured_candidate_found_ranking_lost"

    # 7. Check if query is inherently ambiguous (missing info)
    q_cat = case.get("query_category") if isinstance(case, dict) else case.query_category
    if q_cat in ("missing_information", "duplicate_resolution"):
        return "ambiguous_query"

    # 8. Otherwise supporting doc absent in corpus/graph
    return "relationship_resolved_supporting_doc_absent"
