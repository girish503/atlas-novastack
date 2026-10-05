"""Candidate-Starvation Diagnostic Reconciliation Engine — Phase 4D-0.1.

Reconciles diagnostic findings from Phase 4D-0:
1. Reconciles candidate-depth discrepancies (channel_depth_recoverable vs hybrid_depth_recoverable).
2. Formally introduces N_fusion_suppression for EVAL-0076 (and notes EVAL-0086, EVAL-0105).
3. Partitions the 23 cases into:
   - Security exclusions (expected_access == 'deny': 0 cases among 23 positive cases)
   - Evaluation ground-truth defects (4 cases)
   - Fusion suppression (1 case for EVAL-0076; 3 total channel-in-50 cases)
   - Genuine retrieval failures (12 cases, or 18 including security-domain allow cases)
4. Evaluates depth-100 counterfactual recovery headroom across BM25, Dense, and Hybrid.
5. Diagnoses entity/identifier, semantic, and relationship representation gaps.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = [
    "EntityIdentifierSignals",
    "RECONCILIATION_PARTITIONS",
    "RECONCILED_ROOT_CAUSE_TAXONOMY",
    "ReconciliationTelemetry",
    "ReconciledCaseDiagnostic",
    "RelationshipSignals",
    "SemanticSignals",
    "StarvationReconciliationSummary",
]

RECONCILED_ROOT_CAUSE_TAXONOMY: dict[str, str] = {
    "A_lexical_mismatch": "Target has insufficient lexical overlap with the original query",
    "B_identifier_mismatch": "The query refers to an identifier/entity form that is not adequately represented in the indexed text",
    "C_entity_alias_mismatch": "The query uses a shorthand/alias while the document uses another canonical representation",
    "D_semantic_mismatch": "The query and target are semantically related but the dense model fails to place the target in top-50",
    "E_multi_concept_mismatch": "The query contains multiple concepts distributed across different representations and neither retrieval channel adequately captures the combination",
    "F_relationship_representation_gap": "The answer depends on an entity relationship that is not adequately represented in searchable text",
    "G_temporal_representation_gap": "The query requires temporal reasoning but the relevant temporal signal is not adequately represented for retrieval",
    "H_lifecycle_representation_gap": "The query requires current/latest/version/supersession semantics that are not sufficiently represented in retrieval text",
    "I_chunking_representation_gap": "The relevant evidence exists in the document but the chunk representation prevents effective retrieval",
    "J_filtering_or_security_exclusion": "The target was excluded by an existing legitimate filter (security/tenant/classification)",
    "K_candidate_depth_effect": "The target is close enough (e.g. rank 51-100) that increasing candidate depth could plausibly recover it",
    "L_corpus_or_ground_truth_issue": "The target/evidence relationship itself appears inconsistent or misaligned in the evaluation benchmark",
    "M_insufficient_evidence": "Available evidence is insufficient to establish a confident root cause",
    "N_fusion_suppression": "Target was retrieved within top-50 by an individual channel (BM25 or Dense), but was dropped below candidate pool cutoff (rank > 50) during Reciprocal Rank Fusion",
}

RECONCILIATION_PARTITIONS: dict[str, str] = {
    "security_exclusion": "Legitimate authorization denial (expected_access == 'deny')",
    "evaluation_ground_truth_defect": "Evaluation benchmark ground-truth misalignment or defect",
    "fusion_suppression": "Retrieved in individual channel top-50 but suppressed by RRF below cutoff",
    "genuine_retrieval_failure": "Genuine retrieval representation gap in lexical, dense, or hybrid indexing",
}


@dataclass
class ReconciliationTelemetry:
    """Detailed telemetry across channels, depths, and fusion."""

    bm25_full_rank: int | None
    dense_full_rank: int | None
    bm25_score: float | None
    dense_score: float | None
    rrf_50_rank: int | None
    candidate_pool_rank: int | None
    rrf_full_rank: int | None
    rrf_score: float | None
    in_bm25_top50: bool
    in_dense_top50: bool
    in_pool_top50: bool
    in_bm25_top100: bool
    in_dense_top100: bool
    in_pool_top100: bool
    channel_depth_recoverable: bool  # Ranks 51-100 in BM25 or Dense full scan
    hybrid_depth_recoverable: bool   # Present in RRF pool of size 100 with channel top-100
    is_51_100_bm25: bool
    is_51_100_dense: bool
    is_51_100_hybrid: bool
    score_gap_to_top50: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EntityIdentifierSignals:
    """Detailed entity and identifier diagnostic signals."""

    source_entity_id: str | None
    canonical_name: str | None
    has_canonical_id_in_text: bool
    has_canonical_name_in_text: bool
    has_aliases_in_text: bool
    query_has_id_in_text: bool
    query_has_id_in_title: bool
    is_addressable_by_entity_representation: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SemanticSignals:
    """Detailed dense semantic diagnostic signals."""

    dense_similarity_score: float | None
    is_dense_similarity_low: bool
    retrieved_by_title_query: bool
    title_query_bm25_rank: int | None
    retrieved_by_name_query: bool
    name_query_bm25_rank: int | None
    retrieved_by_id_query: bool
    id_query_bm25_rank: int | None
    failure_type: str  # 'embedding_vector_space_failure' | 'unsearchable_target_content' | 'none'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RelationshipSignals:
    """Detailed relational representation signals."""

    requires_multi_entity: bool
    relationship_present_in_text: bool
    relationship_in_metadata_only: bool
    relationship_absent_from_text: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReconciledCaseDiagnostic:
    """Full reconciled diagnostic profile for a candidate-starvation case."""

    evaluation_id: str
    query: str
    evaluation_category: str
    expected_access: str
    target_document_id: str
    target_document_title: str
    reconciliation_partition: str
    phase_4d0_primary_cause: str
    reconciled_primary_cause: str
    secondary_root_causes: list[str]
    telemetry: ReconciliationTelemetry
    entity_signals: EntityIdentifierSignals
    semantic_signals: SemanticSignals
    relationship_signals: RelationshipSignals
    reconciliation_rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "query": self.query,
            "evaluation_category": self.evaluation_category,
            "expected_access": self.expected_access,
            "target_document_id": self.target_document_id,
            "target_document_title": self.target_document_title,
            "reconciliation_partition": self.reconciliation_partition,
            "phase_4d0_primary_cause": self.phase_4d0_primary_cause,
            "reconciled_primary_cause": self.reconciled_primary_cause,
            "secondary_root_causes": self.secondary_root_causes,
            "telemetry": self.telemetry.to_dict(),
            "entity_signals": self.entity_signals.to_dict(),
            "semantic_signals": self.semantic_signals.to_dict(),
            "relationship_signals": self.relationship_signals.to_dict(),
            "reconciliation_rationale": self.reconciliation_rationale,
        }


@dataclass
class StarvationReconciliationSummary:
    """Summary metrics of the Phase 4D-0.1 reconciliation."""

    total_starvation_cases: int
    partition_counts: dict[str, int]
    phase_4d0_cause_distribution: dict[str, int]
    reconciled_cause_distribution: dict[str, int]
    channel_depth_recoverable_count: int
    hybrid_depth_recoverable_count: int
    fusion_suppression_count: int
    genuine_retrieval_failure_count: int
    ground_truth_defects_count: int
    security_exclusions_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
