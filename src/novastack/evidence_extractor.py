"""Targeted Evidence Extraction Hierarchy — ATLAS 0.5 Milestone M8.

Implements bounded sentence-level evidence extraction hierarchy:
Level 0: FULL DOCUMENT (Tier 0: protective boundary, out-of-scope, explicit security policy)
Level 1: RELEVANT CHUNKS (Tier 1: primary chunk with entity-grounded relevancy)
Level 2: RELEVANT SENTENCES (Tier 2: sentences extracted around entity anchors,
         subject-verb relations, negation, temporal markers, and causal bridges)
Level 3: STRUCTURAL CONTEXT (Tier 3: header/metadata preserved for entity provenance)

Invariants:
- Preserves 100% full text for protective/out-of-scope queries (EVAL-0054, EVAL-0058).
- Retains exact document_id, chunk_id, and evidence_id mapping so C2 citations verify 100.0%.
- Operates strictly downstream of security authorization checks.
"""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from novastack.evidence import EvidenceItem, EvidencePackage
from novastack.hierarchical_budgeter import PROTECTIVE_TEXT_PATTERNS

logger = logging.getLogger("novastack.evidence_extractor")

__all__ = [
    "ExtractionLevel",
    "TargetedEvidenceExtractor",
    "extract_targeted_evidence_item",
    "extract_targeted_package_evidence",
]


class ExtractionLevel(str, Enum):
    """Hierarchical level of evidence extraction."""

    FULL_DOCUMENT = "full_document"          # Level 0: 100% uncompressed text
    RELEVANT_CHUNKS = "relevant_chunks"      # Level 1: chunk-level selection
    RELEVANT_SENTENCES = "relevant_sentences"  # Level 2: targeted sentences with relations
    STRUCTURAL_CONTEXT = "structural_context"  # Level 3: structural context + anchors


# Common abbreviations to prevent false sentence splits
COMMON_ABBREVIATIONS: tuple[str, ...] = (
    "e.g.", "i.e.", "vs.", "etc.", "v1.0", "v2.0", "v2.4.1", "v3.0", "v4.0",
    "approx.", "fig.", "inc.", "ltd.", "dr.", "mr.", "ms.", "prof.", "dept.",
    "sec.", "no.", "ref.", "p95", "p99", "u.s.", "u.k.",
)

# Negation keywords to preserve
NEGATION_TERMS: frozenset[str] = frozenset({
    "not", "never", "no", "none", "neither", "nor", "cannot", "can't",
    "don't", "didn't", "wasn't", "weren't", "isn't", "aren't", "failed",
    "failure", "unsuccessful", "prevented", "rejected", "disabled", "without",
})

# Causal bridge terms to preserve
CAUSAL_TERMS: frozenset[str] = frozenset({
    "because", "due", "caused", "causing", "results", "resulted", "resulting",
    "leads", "led", "leading", "trigger", "triggered", "triggering", "root",
    "reason", "why", "mitigated", "resolved", "fixed", "fix", "resolution",
})

# Subject-verb relation patterns
RELATION_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(?:caused\s+by|due\s+to|triggered\s+by|resulted\s+from)\b", re.I),
    re.compile(r"\b(?:owned\s+by|belongs\s+to|managed\s+by|maintained\s+by)\b", re.I),
    re.compile(r"\b(?:fixed\s+by|resolved\s+by|fixed\s+in|mitigated\s+by)\b", re.I),
    re.compile(r"\b(?:deployed\s+in|introduced\s+in|rolled\s+out\s+in)\b", re.I),
    re.compile(r"\b(?:underlying\s+cause|root\s+cause|triggering\s+factor)\b", re.I),
    re.compile(r"\b(?:remediation|action\s+item|escalation\s+sla)\b", re.I),
)

# Temporal patterns
TEMPORAL_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b", re.I),  # versions: v2.4.1, 1.8.0
    re.compile(r"\b(?:q[1-4]\s+\d{4}|\d{4}-\d{2}-\d{2})\b", re.I),  # dates: Q1 2025, 2025-03-09
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:utc|gmt|pst|est)?\b", re.I),  # time: 20:22 UTC
    re.compile(r"\b\d+\s*(?:minutes|hours|seconds|days|weeks|months|ms)\b", re.I),  # duration
)


def _split_into_sentences(text: str) -> list[str]:
    """Split text into sentences while protecting known abbreviations."""
    protected = text
    for abb in COMMON_ABBREVIATIONS:
        if abb in protected.lower():
            pattern = re.compile(re.escape(abb), re.IGNORECASE)
            protected = pattern.sub(abb.replace(".", "§"), protected)

    raw_sentences = re.split(r"(?<=[.!?])\s+", protected)
    sentences: list[str] = []
    for s in raw_sentences:
        clean = s.replace("§", ".").strip()
        if clean:
            sentences.append(clean)
    return sentences


def _extract_header(text: str) -> tuple[str, str]:
    """Extract structural context / header block from chunk text."""
    clean_text = text.strip()
    if "\n\n" in clean_text:
        parts = clean_text.split("\n\n", 1)
        first_block = parts[0].strip()
        if len(first_block) <= 300 and (
            first_block.startswith("#")
            or ":" in first_block
            or "INCIDENT" in first_block.upper()
            or "POSTMORTEM" in first_block.upper()
            or "RUNBOOK" in first_block.upper()
            or "DEPLOYMENT" in first_block.upper()
        ):
            return first_block, parts[1].strip()
    return "", clean_text


def _score_sentence(sentence: str, query: str, entity_tokens: set[str]) -> float:
    """Compute targeted relevance score for a sentence."""
    s_lower = sentence.lower()
    q_words = set(re.findall(r"\b[a-zA-Z0-9_\-]+\b", query.lower()))
    s_words = set(re.findall(r"\b[a-zA-Z0-9_\-]+\b", s_lower))

    # Base lexical overlap
    overlap = len(q_words & s_words)
    score = float(overlap * 2.0)

    # Entity anchor match (highest weight)
    for et in entity_tokens:
        if et and et in s_lower:
            score += 5.0

    # Subject-verb relation patterns
    for pat in RELATION_PATTERNS:
        if pat.search(sentence):
            score += 3.0

    # Causal bridges
    for ct in CAUSAL_TERMS:
        if ct in s_words:
            score += 1.5

    # Negation preservation
    for nt in NEGATION_TERMS:
        if nt in s_words:
            score += 1.0

    # Temporal markers
    for tpat in TEMPORAL_PATTERNS:
        if tpat.search(sentence):
            score += 1.5

    return score


def extract_targeted_evidence_item(
    item: EvidenceItem,
    query: str,
    is_protective: bool = False,
    max_sentences: int = 3,
    extracted_entity_ids: list[str] | None = None,
) -> EvidenceItem:
    """Extract targeted relevant sentences and structural context from an EvidenceItem.

    Retains exact document_id, chunk_id, and evidence_id mapping for 100% C2 citation validity.
    """
    clean_text = item.text.strip()
    if not clean_text:
        return item

    # Level 0: Protective queries MUST preserve 100% full text (Tier 0 invariant)
    if is_protective:
        return item

    # Check for protective patterns in chunk text
    for pat in PROTECTIVE_TEXT_PATTERNS:
        if pat.search(clean_text):
            return item

    header, body = _extract_header(clean_text)
    sentences = _split_into_sentences(body)

    # If body has very few sentences, keep as-is
    if len(sentences) <= max_sentences:
        return item

    # Build entity tokens set from query and known entity IDs
    entity_tokens: set[str] = set()
    for eid in (extracted_entity_ids or []):
        entity_tokens.add(eid.lower())

    # Extract uppercase identifiers like SVC-NS-0001, INC-NS-0001, DEP-NS-0001
    for m in re.finditer(r"\b[A-Z]{2,4}-[A-Z0-9_\-]+\b", query):
        entity_tokens.add(m.group(0).lower())

    # Score sentences
    scored_sentences: list[tuple[int, float, str]] = []
    for idx, s in enumerate(sentences):
        score = _score_sentence(s, query, entity_tokens)
        scored_sentences.append((idx, score, s))

    # Sort by score descending, pick top-K
    scored_sentences.sort(key=lambda x: x[1], reverse=True)
    selected_scored = scored_sentences[:max_sentences]

    # Re-sort in original chronological / document order
    selected_scored.sort(key=lambda x: x[0])

    extracted_body = " ".join(s for _, _, s in selected_scored)

    if header:
        new_text = f"{header}\n\n{extracted_body}"
    else:
        new_text = extracted_body

    # Construct new EvidenceItem with identical metadata
    new_item = copy.copy(item)
    new_item.text = new_text
    return new_item


class TargetedEvidenceExtractor:
    """Production-grade Targeted Evidence Extractor for ATLAS 0.5."""

    def __init__(self, default_max_sentences: int = 3) -> None:
        self.default_max_sentences = default_max_sentences

    def extract(
        self,
        item: EvidenceItem,
        query: str,
        is_protective: bool = False,
        max_sentences: int | None = None,
        extracted_entity_ids: list[str] | None = None,
    ) -> EvidenceItem:
        """Extract targeted evidence from an individual EvidenceItem."""
        limit = max_sentences or self.default_max_sentences
        return extract_targeted_evidence_item(
            item=item,
            query=query,
            is_protective=is_protective,
            max_sentences=limit,
            extracted_entity_ids=extracted_entity_ids,
        )

    def extract_package(
        self,
        package: EvidencePackage,
        is_protective: bool = False,
        max_sentences: int | None = None,
        extracted_entity_ids: list[str] | None = None,
    ) -> list[EvidenceItem]:
        """Extract targeted evidence across all selected items in an EvidencePackage."""
        limit = max_sentences or self.default_max_sentences
        extracted_items: list[EvidenceItem] = []
        for it in package.selected_evidence:
            ext_it = self.extract(
                item=it,
                query=package.query,
                is_protective=is_protective,
                max_sentences=limit,
                extracted_entity_ids=extracted_entity_ids,
            )
            extracted_items.append(ext_it)
        return extracted_items


def extract_targeted_package_evidence(
    package: EvidencePackage,
    is_protective: bool = False,
    max_sentences: int = 3,
    extracted_entity_ids: list[str] | None = None,
) -> list[EvidenceItem]:
    """Functional convenience wrapper for package-level targeted extraction."""
    extractor = TargetedEvidenceExtractor(default_max_sentences=max_sentences)
    return extractor.extract_package(
        package=package,
        is_protective=is_protective,
        max_sentences=max_sentences,
        extracted_entity_ids=extracted_entity_ids,
    )
