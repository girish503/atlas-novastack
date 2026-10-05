"""Deterministic Citation Validator — Phase 4F.

Validates extracted citations from generated answers against:
1. Citation existence in prompt evidence
2. Membership in package.selected_evidence
3. Corpus existence (document and chunk)
4. Absence from package.excluded_evidence (unauthorized, stale, superseded)
5. Tenant and role authorization
6. Adversarial and retrieval-poisoning classification
7. Mechanical phrase/keyword match in cited evidence text

Distinguishes mechanical citation validity from deep semantic citation correctness
per CTO guidance.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from novastack.evidence import EvidenceItem, EvidencePackage, EvidenceStatus

__all__ = [
    "Citation",
    "CitationStatus",
    "CitationValidator",
]


class CitationStatus:
    """Controlled vocabulary for mechanical citation status."""

    VALID = "VALID"
    INVALID = "INVALID"
    UNAUTHORIZED = "UNAUTHORIZED"
    EXCLUDED = "EXCLUDED"
    UNKNOWN = "UNKNOWN"


CITATION_PATTERN = re.compile(
    r"\[(EVD-[A-Za-z0-9_\-]+|DOC-[A-Za-z0-9_\-]+|\d+)\]",
    re.IGNORECASE,
)


@dataclass
class Citation:
    """Structured representation of a single extracted citation and its validation trace."""

    raw_tag: str
    evidence_id: str
    document_id: str
    chunk_id: str
    title: str
    status: str  # VALID, INVALID, UNAUTHORIZED, EXCLUDED, UNKNOWN
    reasons: list[str] = field(default_factory=list)
    phrase_match_detected: bool = False
    evidence_status: str | None = None
    authority_level: str | None = None
    is_adversarial: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert citation to dictionary."""
        return asdict(self)


class CitationValidator:
    """Deterministic Citation Validator for Phase 4F Grounded Generation."""

    def __init__(
        self,
        corpus_doc_ids: set[str] | None = None,
        corpus_chunk_ids: set[str] | None = None,
    ):
        self.corpus_doc_ids = corpus_doc_ids or set()
        self.corpus_chunk_ids = corpus_chunk_ids or set()

    def extract_citation_tags(self, text: str) -> list[str]:
        """Extract all citation tags like [EVD-001], [DOC-...], [1] from text."""
        return CITATION_PATTERN.findall(text)

    def validate_citations(
        self,
        text: str,
        package: EvidencePackage,
        corpus_doc_ids: set[str] | None = None,
        corpus_chunk_ids: set[str] | None = None,
    ) -> tuple[list[Citation], str, list[str]]:
        """Extract and validate all citations in answer text against the EvidencePackage.

        Returns:
            - list of Citation objects
            - overall citation status: 'valid', 'partially_valid', 'invalid', or 'none'
            - list of unsupported claims / validation failure descriptions
        """
        doc_ids = corpus_doc_ids or self.corpus_doc_ids
        chunk_ids = corpus_chunk_ids or self.corpus_chunk_ids

        raw_tags = self.extract_citation_tags(text)
        if not raw_tags:
            return [], "none", []

        # Index selected evidence by evidence_id, document_id, chunk_id, and sequential index
        selected_by_evidence_id: dict[str, EvidenceItem] = {}
        selected_by_doc_id: dict[str, EvidenceItem] = {}
        selected_by_chunk_id: dict[str, EvidenceItem] = {}
        selected_by_index: dict[str, EvidenceItem] = {}

        for idx, item in enumerate(package.selected_evidence):
            selected_by_evidence_id[item.evidence_id.upper()] = item
            if item.document_id:
                selected_by_doc_id[item.document_id.upper()] = item
            if item.chunk_id:
                selected_by_chunk_id[item.chunk_id.upper()] = item

            idx_1 = idx + 1
            selected_by_index[f"EVD-{idx_1:03d}"] = item
            selected_by_index[f"EVD-{idx_1}"] = item
            selected_by_index[str(idx_1)] = item

        # Index excluded evidence if available
        excluded_by_doc_id: dict[str, Any] = {}
        excluded_by_evidence_id: dict[str, Any] = {}
        for item in package.excluded_evidence:
            if hasattr(item, "evidence_id"):
                excluded_by_evidence_id[item.evidence_id.upper()] = item
                if item.document_id:
                    excluded_by_doc_id[item.document_id.upper()] = item
            elif isinstance(item, dict):
                eid = item.get("evidence_id") or ""
                did = item.get("doc_id") or item.get("document_id") or ""
                if eid:
                    excluded_by_evidence_id[eid.upper()] = item
                if did:
                    excluded_by_doc_id[did.upper()] = item

        validated_citations: list[Citation] = []
        unsupported_claims: list[str] = []

        for raw_tag in raw_tags:
            tag_clean = raw_tag.strip().upper()
            reasons: list[str] = []
            matched_item: EvidenceItem | None = None

            # Resolve citation target
            if tag_clean in selected_by_evidence_id:
                matched_item = selected_by_evidence_id[tag_clean]
            elif tag_clean in selected_by_index:
                matched_item = selected_by_index[tag_clean]
            elif tag_clean in selected_by_doc_id:
                matched_item = selected_by_doc_id[tag_clean]
            elif tag_clean in selected_by_chunk_id:
                matched_item = selected_by_chunk_id[tag_clean]

            if matched_item is not None:
                # Target found in selected evidence
                status = CitationStatus.VALID
                phrase_match = False

                # 1. Corpus existence check
                if doc_ids and matched_item.document_id.upper() not in {d.upper() for d in doc_ids}:
                    reasons.append(f"document_{matched_item.document_id}_not_in_corpus")
                    status = CitationStatus.INVALID

                if chunk_ids and matched_item.chunk_id.upper() not in {c.upper() for c in chunk_ids}:
                    reasons.append(f"chunk_{matched_item.chunk_id}_not_in_corpus")
                    status = CitationStatus.INVALID

                # 2. Evidence usability check
                if not matched_item.is_usable_evidence():
                    reasons.append(f"evidence_status_not_accepted_{matched_item.evidence_status}")
                    status = CitationStatus.INVALID

                # 3. Adversarial check
                is_adv = (
                    matched_item.evidence_status == EvidenceStatus.ADVERSARIAL.value
                    or "adversarial" in matched_item.evidence_reasons
                    or matched_item.source_type == "adversarial_fixture"
                )
                if is_adv:
                    reasons.append("adversarial_content_quarantined")
                    status = CitationStatus.INVALID

                # 4. Mechanical text phrase match
                if matched_item.text:
                    words = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9_\-]{4,}\b", matched_item.text)]
                    answer_words = set(re.findall(r"\b[a-zA-Z0-9_\-]{4,}\b", text.lower()))
                    common = set(words).intersection(answer_words)
                    if len(common) >= 2:
                        phrase_match = True

                if status == CitationStatus.VALID:
                    reasons.append("verified_selected_authorized_evidence")

                citation = Citation(
                    raw_tag=f"[{raw_tag}]",
                    evidence_id=matched_item.evidence_id,
                    document_id=matched_item.document_id,
                    chunk_id=matched_item.chunk_id,
                    title=matched_item.title,
                    status=status,
                    reasons=reasons,
                    phrase_match_detected=phrase_match,
                    evidence_status=matched_item.evidence_status,
                    authority_level=matched_item.authority_level,
                    is_adversarial=is_adv,
                )
                validated_citations.append(citation)
                if status != CitationStatus.VALID:
                    unsupported_claims.append(f"Citation [{raw_tag}] marked {status}: {', '.join(reasons)}")

            else:
                # Not in selected evidence — check excluded or unknown
                matched_excluded = excluded_by_doc_id.get(tag_clean) or excluded_by_evidence_id.get(tag_clean)
                if matched_excluded is not None:
                    ex_status = (
                        matched_excluded.evidence_status
                        if hasattr(matched_excluded, "evidence_status")
                        else matched_excluded.get("status", "excluded")
                    )
                    ex_reasons = (
                        matched_excluded.evidence_reasons
                        if hasattr(matched_excluded, "evidence_reasons")
                        else matched_excluded.get("reasons", [])
                    )
                    ex_doc = (
                        matched_excluded.document_id
                        if hasattr(matched_excluded, "document_id")
                        else matched_excluded.get("doc_id", tag_clean)
                    )

                    if ex_status == EvidenceStatus.UNAUTHORIZED.value or "unauthorized" in str(ex_reasons).lower():
                        c_status = CitationStatus.UNAUTHORIZED
                        reasons.append("citation_references_unauthorized_evidence")
                    elif ex_status == EvidenceStatus.ADVERSARIAL.value or "adversarial" in str(ex_reasons).lower():
                        c_status = CitationStatus.INVALID
                        reasons.append("citation_references_adversarial_quarantined_evidence")
                    else:
                        c_status = CitationStatus.EXCLUDED
                        reasons.append(f"citation_references_excluded_evidence_{ex_status}")

                    citation = Citation(
                        raw_tag=f"[{raw_tag}]",
                        evidence_id=getattr(matched_excluded, "evidence_id", tag_clean),
                        document_id=ex_doc,
                        chunk_id=getattr(matched_excluded, "chunk_id", ""),
                        title=getattr(matched_excluded, "title", ""),
                        status=c_status,
                        reasons=reasons,
                        phrase_match_detected=False,
                        evidence_status=ex_status,
                        authority_level=getattr(matched_excluded, "authority_level", "none"),
                        is_adversarial=(c_status == CitationStatus.INVALID),
                    )
                    validated_citations.append(citation)
                    unsupported_claims.append(f"Citation [{raw_tag}] marked {c_status}: {', '.join(reasons)}")
                else:
                    if doc_ids and tag_clean in {d.upper() for d in doc_ids}:
                        citation = Citation(
                            raw_tag=f"[{raw_tag}]",
                            evidence_id=tag_clean,
                            document_id=tag_clean,
                            chunk_id="",
                            title="",
                            status=CitationStatus.EXCLUDED,
                            reasons=["document_exists_in_corpus_but_not_in_evidence_package"],
                            phrase_match_detected=False,
                        )
                    else:
                        citation = Citation(
                            raw_tag=f"[{raw_tag}]",
                            evidence_id=raw_tag,
                            document_id="",
                            chunk_id="",
                            title="",
                            status=CitationStatus.UNKNOWN,
                            reasons=["phantom_citation_not_in_evidence_or_corpus"],
                            phrase_match_detected=False,
                        )
                    validated_citations.append(citation)
                    unsupported_claims.append(f"Citation [{raw_tag}] marked {citation.status}: {', '.join(citation.reasons)}")

        # Determine overall citation validity
        valid_count = sum(1 for c in validated_citations if c.status == CitationStatus.VALID)
        if valid_count == len(validated_citations) and valid_count > 0:
            overall_status = "valid"
        elif valid_count > 0:
            overall_status = "partially_valid"
        else:
            overall_status = "invalid"

        return validated_citations, overall_status, unsupported_claims
