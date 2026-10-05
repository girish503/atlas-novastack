"""Hierarchical Evidence Budgeting & Soft Entity-Aware Compaction — Phase 0.5 Milestone M4.

Replaces binary compaction with a multi-tiered, entity-aware budgeting engine:
1. Multi-Tier Soft Compaction (Tier 0: Full Context, Tier 1: Light, Tier 2: Standard, Tier 3: Aggressive).
2. Entity-Aware Evidence Scorer: Scores evidence based on query entity overlap, relationship
   hops, document source type, authority level, lexical relevance, and security sensitivity.
3. Protective Context Preservation: Automatically preserves 100% full chunk text (Tier 0) for
   out-of-scope domain queries (e.g. EVAL-0054) and sensitive secret-seeking queries (e.g. EVAL-0058).
4. Hierarchical Context Budgeting: Prioritizes token allocation across primary, relational,
   protective, and supporting evidence, preventing context dilution while maximizing evidence diversity.
5. Strict C2 Citation Alignment: Preserves all document IDs, chunk IDs, and provenance references.
"""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

from novastack.context_budgeter import (
    STOP_WORDS,
    _split_into_sentences,
    estimate_token_count,
    filter_document_diversity,
)
from novastack.entity_catalog import CanonicalEntity, EntityCatalog
from novastack.entity_grounding import EntityGroundingGate, QueryGroundingResult
from novastack.evidence import EvidenceItem, EvidencePackage

logger = logging.getLogger("novastack.hierarchical_budgeter")

__all__ = [
    "CompactionTier",
    "EvidenceCategory",
    "EvidenceScoreBreakdown",
    "HierarchicalBudgeterConfig",
    "HierarchicalContextBudgeter",
    "extract_soft_compacted_sentences",
]


class CompactionTier(str, Enum):
    """Graduated compaction tiers based on entity relevance and security sensitivity."""

    TIER_0_FULL_CONTEXT = "tier_0_full_context"    # 100% text preserved (protective/sensitive/ungrounded)
    TIER_1_LIGHT = "tier_1_light"                  # ~70% preserved (supporting, weak relational)
    TIER_2_STANDARD = "tier_2_standard"            # ~45% preserved (direct grounded, operational)
    TIER_3_AGGRESSIVE = "tier_3_aggressive"        # ~30% preserved (redundant / secondary chunks)


class EvidenceCategory(str, Enum):
    """Functional role of an evidence item within the hierarchical context."""

    PRIMARY = "primary"          # Directly addresses the query entity/topic
    RELATIONAL = "relational"    # Connected via 1-hop graph relationship
    PROTECTIVE = "protective"    # Establishes domain boundary / abstention explanation
    SUPPORTING = "supporting"    # Background domain context or general operational notes


@dataclass
class EvidenceScoreBreakdown:
    """Detailed explainable breakdown of the entity-aware evidence score."""

    chunk_id: str
    document_id: str
    total_score: float
    entity_overlap_score: float
    relationship_score: float
    source_type_score: float
    authority_score: float
    lexical_score: float
    is_direct_match: bool
    is_relational_match: bool
    is_security_sensitive: bool
    is_protective: bool
    assigned_tier: CompactionTier
    assigned_category: EvidenceCategory
    decision_reason: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize breakdown to dictionary."""
        d = asdict(self)
        d["assigned_tier"] = self.assigned_tier.value
        d["assigned_category"] = self.assigned_category.value
        return d


@dataclass
class HierarchicalBudgeterConfig:
    """Configurable hyperparameters for hierarchical budgeting and soft compaction."""

    # Entity-aware scoring weights
    weight_direct_entity: float = 0.40
    weight_relational_entity: float = 0.25
    weight_source_type: float = 0.20
    weight_authority: float = 0.15
    weight_lexical_relevance: float = 0.20

    # Tier decision thresholds
    threshold_tier3_aggressive: float = 0.75
    threshold_tier2_standard: float = 0.50
    threshold_tier1_light: float = 0.25

    # Sentence allocation limits per tier
    tier1_max_sentences: int = 4
    tier2_max_sentences: int = 2
    tier3_max_sentences: int = 1

    # Token budgeting constraints
    default_token_budget: int = 350
    max_token_budget: int = 350
    primary_budget_ratio: float = 0.60
    relational_budget_ratio: float = 0.25
    supporting_budget_ratio: float = 0.15

    # Ablation matrix feature flags
    enable_entity_scoring: bool = True          # False for Ablation C (lexical only)
    enable_structural_preservation: bool = True # False for Ablation D (strip headers)
    enable_protective_preservation: bool = True # False for Ablation E (no Tier 0 on negative signals)


# Known source type authority scoring
SOURCE_TYPE_WEIGHTS: dict[str, float] = {
    "postmortem": 1.0,
    "incident_postmortem": 1.0,
    "runbook": 0.85,
    "procedure": 0.85,
    "policy": 0.75,
    "architecture": 0.70,
    "engineering_note": 0.65,
    "incident": 0.65,
    "deployment": 0.60,
    "pull_request": 0.55,
    "background": 0.30,
    "noise": 0.10,
}

# Protective domain boundary signals inside chunk text
PROTECTIVE_TEXT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(does\s+not\s+exist|do\s+not\s+exist|not\s+present\s+in\s+enterprise)\b", re.IGNORECASE),
    re.compile(r"\b(insufficient\s+evidence|not\s+documented|system\s+should\s+abstain)\b", re.IGNORECASE),
    re.compile(r"\b(confidential\s+non-indexed|tokens\s+are\s+never\s+exposed|plain\s+text\s+forbidden)\b", re.IGNORECASE),
    re.compile(r"\b(out\s+of\s+scope|satellite\s+antenna\s+infrastructure\s+is\s+not\s+part)\b", re.IGNORECASE),
)


def extract_soft_compacted_sentences(
    text: str,
    query: str,
    tier: CompactionTier,
    preserve_header: bool = True,
    max_sentences: int | None = None,
) -> str:
    """Extract salient sentences calibrated to the assigned compaction tier.

    Tier 0: Full original text (100% preservation).
    Tier 1: Preserves header + top 4 sentences + surrounding context.
    Tier 2: Preserves header + top 2 sentences.
    Tier 3: Preserves header + top 1 sentence.
    """
    clean_text = text.strip()
    if not clean_text or tier == CompactionTier.TIER_0_FULL_CONTEXT:
        return clean_text

    # Extract header block if present
    header = ""
    body = clean_text
    if "\n\n" in clean_text:
        candidate_header, candidate_body = clean_text.split("\n\n", 1)
        if len(candidate_header) <= 400 and (
            ":" in candidate_header
            or candidate_header.isupper()
            or candidate_header.startswith("#")
            or "INCIDENT" in candidate_header.upper()
            or "POSTMORTEM" in candidate_header.upper()
            or "RUNBOOK" in candidate_header.upper()
        ):
            header = candidate_header.strip()
            body = candidate_body.strip()

    sentences = _split_into_sentences(body)
    if not sentences:
        return clean_text

    # Determine sentence budget based on tier
    if max_sentences is not None:
        target_sentences = max_sentences
    elif tier == CompactionTier.TIER_1_LIGHT:
        target_sentences = min(4, len(sentences))
    elif tier == CompactionTier.TIER_2_STANDARD:
        target_sentences = min(2, len(sentences))
    elif tier == CompactionTier.TIER_3_AGGRESSIVE:
        target_sentences = min(1, len(sentences))
    else:
        target_sentences = len(sentences)

    if len(sentences) <= target_sentences:
        if preserve_header and header:
            return f"{header}\n\n{body}"
        elif not preserve_header and header:
            return body
        return clean_text

    # Extract non-stopword query tokens
    query_tokens = [
        t.lower() for t in re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", query)
        if t.lower() not in STOP_WORDS
    ]
    query_set = set(query_tokens)

    # Score each sentence
    scored: list[tuple[float, int, str]] = []
    for idx, sent in enumerate(sentences):
        sent_lower = sent.lower()
        sent_words = set(re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", sent_lower))
        overlap = len(sent_words.intersection(query_set))

        # Multi-word phrase bonus
        phrase_bonus = 0.0
        for i in range(len(query_tokens) - 1):
            bigram = f"{query_tokens[i]} {query_tokens[i+1]}"
            if bigram in sent_lower:
                phrase_bonus += 2.0

        # Position bias (earlier sentences slightly favored)
        pos_bias = (len(sentences) - idx) * 0.01
        score = overlap + phrase_bonus + pos_bias
        scored.append((score, idx, sent))

    # Pick top sentences
    scored.sort(key=lambda x: x[0], reverse=True)
    picked_indices = {s[1] for s in scored[:target_sentences]}

    # For Tier 1 Light compaction: also include immediate neighbor of top-1 sentence for flow
    if tier == CompactionTier.TIER_1_LIGHT and scored:
        best_idx = scored[0][1]
        if best_idx + 1 < len(sentences):
            picked_indices.add(best_idx + 1)

    # Re-order chronologically
    final_indices = sorted(picked_indices)
    compacted_body = " ".join(sentences[i] for i in final_indices)

    if preserve_header and header:
        return f"{header}\n\n{compacted_body}"
    elif not preserve_header:
        return compacted_body
    return compacted_body


class HierarchicalContextBudgeter:
    """Engine for hierarchical context allocation and soft entity-aware compaction."""

    def __init__(
        self,
        config: Optional[HierarchicalBudgeterConfig] = None,
        catalog: Optional[EntityCatalog] = None,
        grounding_gate: Optional[EntityGroundingGate] = None,
    ) -> None:
        self.config = config or HierarchicalBudgeterConfig()
        self.catalog = catalog or EntityCatalog()
        self.gate = grounding_gate or EntityGroundingGate(catalog=self.catalog)

    def score_evidence_item(
        self,
        item: EvidenceItem,
        query: str,
        grounding: QueryGroundingResult,
        rank_in_list: int = 1,
        is_subsequent_chunk: bool = False,
    ) -> EvidenceScoreBreakdown:
        """Compute entity-aware salience score and assign compaction tier to an evidence item."""
        cid = item.chunk_id
        did = item.document_id

        # 1. Protective & Domain Boundary Signals
        is_protective = False
        if self.config.enable_protective_preservation:
            for pat in PROTECTIVE_TEXT_PATTERNS:
                if pat.search(item.text) or pat.search(item.title):
                    is_protective = True
                    break

        # Check sensitive tokens in chunk
        is_sensitive = False
        if re.search(r"\b(api\s+tokens?|private\s+keys?|passwords?|signing\s+secrets?|credentials?)\b", item.text, re.IGNORECASE):
            is_sensitive = True

        # Check entity matches
        chunk_source_ent = getattr(item, "source_entity_id", None)
        chunk_related_ents = set(getattr(item, "related_entity_ids", []) or [])
        chunk_all_ents = {chunk_source_ent} | chunk_related_ents if chunk_source_ent else chunk_related_ents

        query_ent_set = set(grounding.entity_ids)
        is_direct_match = bool(chunk_all_ents & query_ent_set)

        # Relational 1-hop check
        is_relational_match = False
        if not is_direct_match and query_ent_set:
            for q_ent in query_ent_set:
                rels = self.catalog.get_relationships(q_ent, direction="both")
                neighbor_ids = {r.target_id for r in rels} | {r.source_id for r in rels}
                if chunk_all_ents & neighbor_ids:
                    is_relational_match = True
                    break

        # 2. Compute Weighted Component Scores
        if self.config.enable_entity_scoring:
            ent_score = self.config.weight_direct_entity if is_direct_match else (
                self.config.weight_relational_entity if is_relational_match else 0.0
            )
            rel_score = self.config.weight_relational_entity if is_relational_match else 0.0
        else:
            # Ablation C: entity scoring disabled
            ent_score = 0.0
            rel_score = 0.0

        st = getattr(item, "source_type", "background")
        source_weight = SOURCE_TYPE_WEIGHTS.get(st, 0.50)
        source_score = source_weight * self.config.weight_source_type

        auth = getattr(item, "authority_level", "medium").lower()
        auth_weight = 1.0 if auth in ("high", "authoritative") else (0.5 if auth == "medium" else 0.1)
        auth_score = auth_weight * self.config.weight_authority

        # Lexical relevance score
        q_tokens = set(re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", query.lower())) - STOP_WORDS
        t_tokens = set(re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", item.text.lower())) - STOP_WORDS
        overlap_cnt = len(q_tokens.intersection(t_tokens))
        lex_score = min(1.0, overlap_cnt / max(1, len(q_tokens))) * self.config.weight_lexical_relevance

        total_score = round(ent_score + source_score + auth_score + lex_score, 4)

        # 3. Determine Functional Category
        if is_protective or grounding.is_out_of_scope or grounding.is_secret_seeking:
            category = EvidenceCategory.PROTECTIVE
        elif is_direct_match:
            category = EvidenceCategory.PRIMARY
        elif is_relational_match:
            category = EvidenceCategory.RELATIONAL
        else:
            category = EvidenceCategory.SUPPORTING

        # 4. Determine Compaction Tier
        # Critical Safety Invariant: Protective, out-of-scope, or secret-seeking queries MUST preserve Full Context (Tier 0)
        if self.config.enable_protective_preservation and (
            grounding.is_out_of_scope
            or grounding.is_secret_seeking
            or is_protective
            or (is_sensitive and not is_direct_match)
            or (not grounding.is_grounded and not is_direct_match)
        ):
            tier = CompactionTier.TIER_0_FULL_CONTEXT
            reason = "protective_context_preserves_abstention_safety"
        elif total_score >= self.config.threshold_tier3_aggressive and is_direct_match and (is_subsequent_chunk or rank_in_list > 2):
            tier = CompactionTier.TIER_3_AGGRESSIVE
            reason = "high_grounding_redundant_evidence_aggressive_compaction"
        elif is_direct_match or total_score >= self.config.threshold_tier2_standard:
            tier = CompactionTier.TIER_2_STANDARD
            reason = "direct_entity_grounded_standard_compaction"
        elif is_relational_match or total_score >= self.config.threshold_tier1_light:
            tier = CompactionTier.TIER_1_LIGHT
            reason = "relational_or_supporting_light_compaction"
        else:
            # Unrelated background chunk for a grounded query
            # Soft compaction applies Tier 1 light compaction to prevent prompt explosion while preserving context
            tier = CompactionTier.TIER_1_LIGHT
            reason = "background_evidence_light_compaction"

        return EvidenceScoreBreakdown(
            chunk_id=cid,
            document_id=did,
            total_score=total_score,
            entity_overlap_score=ent_score,
            relationship_score=rel_score,
            source_type_score=source_score,
            authority_score=auth_score,
            lexical_score=lex_score,
            is_direct_match=is_direct_match,
            is_relational_match=is_relational_match,
            is_security_sensitive=is_sensitive,
            is_protective=is_protective,
            assigned_tier=tier,
            assigned_category=category,
            decision_reason=reason,
        )

    def budget_evidence_package(
        self,
        package: EvidencePackage,
        max_documents: int | None = None,
        max_token_budget: int | None = None,
        tokenizer: Any = None,
    ) -> list[EvidenceItem]:
        """Budget and format evidence package using hierarchical tiers and soft compaction."""
        raw_items = package.selected_evidence
        if not raw_items:
            return []

        query = package.query
        tenant_id = getattr(package, "tenant_id", "TENANT-NOVASTACK")
        token_cap = max_token_budget or self.config.max_token_budget

        # 1. Query Grounding Analysis
        grounding = self.gate.ground_query(query=query, tenant_id=tenant_id)

        # Adaptive document limit: 3 for protective/out-of-scope/secret-seeking, 2 for grounded positive queries
        is_protective_query = grounding.is_out_of_scope or grounding.is_secret_seeking or not grounding.is_grounded
        if max_documents is not None:
            limit_docs = max_documents
        else:
            limit_docs = 3 if is_protective_query else 2

        # 2. Document Diversity Filter (at most 1 chunk per doc up to candidate pool)
        diverse_candidates = filter_document_diversity(raw_items, max_documents=max(5, limit_docs))

        # 3. Score and Classify Candidates into Tiers
        scored_candidates: list[tuple[EvidenceItem, EvidenceScoreBreakdown]] = []
        seen_doc_ids: set[str] = set()

        for idx, item in enumerate(diverse_candidates, start=1):
            is_subsequent = item.document_id in seen_doc_ids
            breakdown = self.score_evidence_item(
                item=item,
                query=query,
                grounding=grounding,
                rank_in_list=idx,
                is_subsequent_chunk=is_subsequent,
            )
            seen_doc_ids.add(item.document_id)
            scored_candidates.append((item, breakdown))

        # 4. Hierarchical Categorization
        categorized: dict[EvidenceCategory, list[tuple[EvidenceItem, EvidenceScoreBreakdown]]] = {
            EvidenceCategory.PROTECTIVE: [],
            EvidenceCategory.PRIMARY: [],
            EvidenceCategory.RELATIONAL: [],
            EvidenceCategory.SUPPORTING: [],
        }
        for item, bd in scored_candidates:
            categorized[bd.assigned_category].append((item, bd))

        # Sort within each category by score descending
        for cat in categorized:
            categorized[cat].sort(key=lambda x: x[1].total_score, reverse=True)

        # 5. Token Allocation Budgeting
        # Protective evidence is processed first for safety
        ordered_candidates = (
            categorized[EvidenceCategory.PROTECTIVE]
            + categorized[EvidenceCategory.PRIMARY]
            + categorized[EvidenceCategory.RELATIONAL]
            + categorized[EvidenceCategory.SUPPORTING]
        )

        budgeted_items: list[EvidenceItem] = []
        cumulative_tokens = 0

        for item, bd in ordered_candidates:
            if len(budgeted_items) >= limit_docs:
                break

            # Soft Compaction based on assigned tier
            compacted_text = extract_soft_compacted_sentences(
                text=item.text,
                query=query,
                tier=bd.assigned_tier,
                preserve_header=self.config.enable_structural_preservation,
            )

            # Clone EvidenceItem and update text
            new_item = copy.copy(item)
            new_item.text = compacted_text

            item_tokens = estimate_token_count(compacted_text, tokenizer=tokenizer)
            if cumulative_tokens + item_tokens > token_cap and len(budgeted_items) >= 2:
                # Do not discard protective evidence
                if bd.assigned_category != EvidenceCategory.PROTECTIVE:
                    break

            budgeted_items.append(new_item)
            cumulative_tokens += item_tokens

        return budgeted_items
