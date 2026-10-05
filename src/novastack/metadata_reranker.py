"""Controlled Metadata-Aware Reranker — Phase 4C-3.

Implements a deterministic, explainable metadata-aware ranking policy
operating strictly inside the existing Phase 4C-1 candidate pools.

Design Principles:
1. Relevance Retrieval Primacy: Operates ONLY inside existing candidate pools;
   never replaces relevance scoring.
2. Transparent Additive Scoring: final_score = base_rrf_score + metadata_score.
3. Strict Feature Attribution: Exposes exact contribution of each metadata dimension.
4. Zero Tuning / Fixed Weights: Fixed mathematical weights derived from first principles.
5. Zero-Trust Security: Authority is NOT authorization; forbidden/unauthorized
   documents can never outrank allowed documents.
6. Deterministic & Zero LLM: Pure Python logic without probabilistic or neural models.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from novastack.metadata_diagnostics import DocumentMetadataSnapshot
from novastack.query_understanding import QueryUnderstanding

__all__ = [
    "CandidateRerankingDetail",
    "MetadataReranker",
    "MetadataRerankerConfig",
]

# Fixed, documented weights for authority levels
DEFAULT_AUTHORITY_WEIGHTS: dict[str, float] = {
    "authoritative": 0.0040,
    "high": 0.0020,
    "medium": 0.0000,
    "low": -0.0020,
    "draft": -0.0040,
}

# Fixed, documented weights for lifecycle statuses
DEFAULT_LIFECYCLE_WEIGHTS: dict[str, float] = {
    "published": 0.0020,
    "archived": -0.0010,
    "draft": -0.0020,
    "deprecated": -0.0030,
    "superseded": -0.0040,
}


@dataclass
class MetadataRerankerConfig:
    """Configuration and feature flags for metadata-aware reranking."""

    # Feature flags for ablation experiments A-G
    enable_authority: bool = True
    enable_lifecycle: bool = True
    enable_version_temporal: bool = True
    enable_provenance: bool = True

    # Fixed, documented weights
    authority_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_AUTHORITY_WEIGHTS))
    lifecycle_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_LIFECYCLE_WEIGHTS))

    # Lifecycle contextual adjustment
    lifecycle_active_boost: float = 0.0020
    lifecycle_stale_penalty: float = -0.0020

    # Version / Temporal adjustments
    version_match_boost: float = 0.0030
    version_mismatch_penalty: float = -0.0020
    temporal_match_boost: float = 0.0030
    temporal_mismatch_penalty: float = -0.0020
    recency_boost: float = 0.0015

    # Provenance adjustments
    provenance_entity_match_boost: float = 0.0030
    provenance_related_match_boost: float = 0.0015
    provenance_structural_boost: float = 0.0010
    provenance_missing_penalty: float = -0.0020

    # Security enforcement
    security_penalty: float = -1000.0


@dataclass
class CandidateRerankingDetail:
    """Full explainability record for a reranked candidate chunk/document."""

    chunk_id: str
    document_id: str
    title: str
    base_rrf_score: float
    metadata_score: float
    authority_contribution: float
    lifecycle_contribution: float
    temporal_version_contribution: float
    provenance_contribution: float
    final_score: float
    rank_before: int
    rank_after: int
    is_poisoned: bool
    authority_level: str
    status: str
    version: str
    source_entity_id: str | None
    is_forbidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MetadataReranker:
    """Deterministic, explainable metadata reranker."""

    def __init__(self, config: MetadataRerankerConfig | None = None) -> None:
        self.config = config or MetadataRerankerConfig()

    def rerank(
        self,
        candidates: list[Any],
        qu: QueryUnderstanding | None,
        metadata_index: dict[str, DocumentMetadataSnapshot],
        forbidden_doc_ids: set[str] | None = None,
        enforce_security: bool = True,
    ) -> list[CandidateRerankingDetail]:
        """Rerank candidates using configured metadata policies.
        
        candidates: List of CandidateMetadataRecord or objects with document_id, chunk_id, rank, rrf_score.
        qu: QueryUnderstanding object extracted for this query.
        metadata_index: Lookup mapping document_id -> DocumentMetadataSnapshot.
        forbidden_doc_ids: Set of document IDs that are forbidden for this query context.
        enforce_security: If True, forbidden documents are strictly penalized so they cannot outrank allowed documents.
        """
        forb_set = set(forbidden_doc_ids or [])
        details: list[CandidateRerankingDetail] = []

        # Precompute candidate timestamps for relative recency if applicable
        all_created_ats: list[str] = []
        if self.config.enable_version_temporal and qu:
            for cand in candidates:
                meta = metadata_index.get(cand.document_id)
                if meta and meta.created_at:
                    all_created_ats.append(meta.created_at)
        all_created_ats.sort()
        recent_threshold = all_created_ats[int(len(all_created_ats) * 0.75)] if all_created_ats else ""

        for cand in candidates:
            doc_id = cand.document_id
            chunk_id = getattr(cand, "chunk_id", doc_id)
            title = getattr(cand, "title", doc_id)
            rank_before = getattr(cand, "rank", 0)
            base_rrf = round(getattr(cand, "rrf_score", 0.0), 6)

            meta = metadata_index.get(doc_id)
            if not meta:
                # If no metadata snapshot exists, score adjustment is 0
                details.append(
                    CandidateRerankingDetail(
                        chunk_id=chunk_id,
                        document_id=doc_id,
                        title=title,
                        base_rrf_score=base_rrf,
                        metadata_score=0.0,
                        authority_contribution=0.0,
                        lifecycle_contribution=0.0,
                        temporal_version_contribution=0.0,
                        provenance_contribution=0.0,
                        final_score=base_rrf,
                        rank_before=rank_before,
                        rank_after=rank_before,
                        is_poisoned=False,
                        authority_level="medium",
                        status="published",
                        version="1.0.0",
                        source_entity_id=None,
                        is_forbidden=(doc_id in forb_set),
                    )
                )
                continue

            auth_contrib = 0.0
            life_contrib = 0.0
            temp_contrib = 0.0
            prov_contrib = 0.0

            is_forbidden = doc_id in forb_set

            # 1. Authority Contribution
            if self.config.enable_authority and not (enforce_security and is_forbidden):
                auth_contrib = self.config.authority_weights.get(meta.authority_level, 0.0)

            # 2. Lifecycle Contribution
            if self.config.enable_lifecycle and not (enforce_security and is_forbidden):
                base_life = self.config.lifecycle_weights.get(meta.status, 0.0)
                life_contrib += base_life

                if qu and qu.lifecycle_constraints:
                    lc = qu.lifecycle_constraints
                    if (lc.active or lc.latest) and meta.status == "published":
                        life_contrib += self.config.lifecycle_active_boost
                    elif (lc.active or lc.latest) and meta.status in ("superseded", "deprecated", "archived"):
                        life_contrib += self.config.lifecycle_stale_penalty
                    elif lc.published and meta.status == "published":
                        life_contrib += self.config.lifecycle_active_boost
                    elif getattr(lc, "draft", False) and meta.status == "draft":
                        life_contrib += self.config.lifecycle_active_boost

            # 3. Version / Temporal Contribution
            if self.config.enable_version_temporal and qu and not (enforce_security and is_forbidden):
                # Version constraint alignment
                if qu.lifecycle_constraints and qu.lifecycle_constraints.version:
                    target_ver = qu.lifecycle_constraints.version.lower().strip()
                    doc_ver = (meta.version or "").lower().strip()
                    if target_ver in doc_ver or doc_ver.startswith(target_ver):
                        temp_contrib += self.config.version_match_boost
                    else:
                        temp_contrib += self.config.version_mismatch_penalty

                # Date / interval constraint alignment
                if getattr(qu, "temporal_interval", None):
                    from novastack.query_understanding import is_temporally_valid
                    ti = qu.temporal_interval
                    is_valid = is_temporally_valid(
                        doc_valid_from=meta.valid_from,
                        doc_valid_until=meta.valid_until,
                        query_start=ti.start,
                        query_end=ti.end,
                        point_in_time=ti.point_in_time,
                    )
                    if is_valid:
                        temp_contrib += self.config.temporal_match_boost
                    else:
                        temp_contrib += self.config.temporal_mismatch_penalty
                elif qu.temporal_constraints:
                    matched_date = False
                    for tc in qu.temporal_constraints:
                        if tc.type == "explicit_date" and tc.value:
                            date_str = tc.value
                            doc_c = (meta.created_at or "")[: len(date_str)]
                            doc_vf = (meta.valid_from or "")[: len(date_str)]
                            doc_vu = (meta.valid_until or "")[: len(date_str)]
                            if date_str in (doc_c, doc_vf, doc_vu) or doc_c.startswith(date_str):
                                matched_date = True
                            elif meta.valid_until and meta.valid_until < date_str:
                                temp_contrib += self.config.temporal_mismatch_penalty
                        elif tc.type == "relative_marker" and tc.value in ("recent", "latest", "newest"):
                            if meta.created_at and recent_threshold and meta.created_at >= recent_threshold:
                                temp_contrib += self.config.recency_boost

                    if matched_date:
                        temp_contrib += self.config.temporal_match_boost

            # 4. Provenance Contribution
            if self.config.enable_provenance and not (enforce_security and is_forbidden):
                extracted_entity_ids: set[str] = set()
                if qu and qu.entities:
                    extracted_entity_ids = {e.entity_id for e in qu.entities}

                if extracted_entity_ids:
                    if meta.source_entity_id in extracted_entity_ids:
                        prov_contrib += self.config.provenance_entity_match_boost
                    elif any(r_id in extracted_entity_ids for r_id in meta.related_entity_ids):
                        prov_contrib += self.config.provenance_related_match_boost
                    elif meta.parent_id or meta.supersedes_id:
                        prov_contrib += self.config.provenance_structural_boost
                    elif not meta.source_entity_id and not meta.related_entity_ids:
                        prov_contrib += self.config.provenance_missing_penalty
                else:
                    # No entity extracted: baseline structural check
                    if meta.source_entity_id:
                        prov_contrib += self.config.provenance_structural_boost
                    elif not meta.source_entity_id and not meta.related_entity_ids:
                        prov_contrib += self.config.provenance_missing_penalty

            metadata_score = round(auth_contrib + life_contrib + temp_contrib + prov_contrib, 6)
            final_score = round(base_rrf + metadata_score, 6)

            # Security invariant: forbidden documents can never outrank allowed documents
            if enforce_security and is_forbidden:
                final_score += self.config.security_penalty

            details.append(
                CandidateRerankingDetail(
                    chunk_id=chunk_id,
                    document_id=doc_id,
                    title=title,
                    base_rrf_score=base_rrf,
                    metadata_score=metadata_score,
                    authority_contribution=round(auth_contrib, 6),
                    lifecycle_contribution=round(life_contrib, 6),
                    temporal_version_contribution=round(temp_contrib, 6),
                    provenance_contribution=round(prov_contrib, 6),
                    final_score=final_score,
                    rank_before=rank_before,
                    rank_after=0,  # Will be assigned below after sorting
                    is_poisoned=meta.is_poisoned,
                    authority_level=meta.authority_level,
                    status=meta.status,
                    version=meta.version,
                    source_entity_id=meta.source_entity_id,
                    is_forbidden=is_forbidden,
                )
            )

        # Deterministic sorting: sort by -final_score, then break ties using rank_before
        sorted_details = sorted(details, key=lambda x: (-x.final_score, x.rank_before, x.chunk_id))

        # Assign rank_after
        for rank, d in enumerate(sorted_details, start=1):
            d.rank_after = rank

        return sorted_details
