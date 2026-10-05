"""Context-Salience Budgeting & Document-Diverse Evidence Selection — Phase 4G.

Provides principled evidence context compression and budgeting downstream of Phase 4E:
1. Document Diversity Filtering (eliminates intra-document chunk redundancy)
2. Sentence-Level Salience Compression (extracts query-relevant sentences while preserving headers)
3. Adaptive Density Budgeting (optimizes distinct document exposure within token dilution limits)
"""

from __future__ import annotations

import copy
import re
from typing import Any

from novastack.evidence import EvidenceItem, EvidencePackage

__all__ = [
    "AdaptiveContextBudgeter",
    "compress_evidence_item",
    "estimate_token_count",
    "extract_salient_sentences",
    "filter_document_diversity",
]

# Standard English stop words for lexical relevance filtering
STOP_WORDS: frozenset[str] = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any",
    "are", "aren't", "as", "at", "be", "because", "been", "before", "being", "below",
    "between", "both", "but", "by", "can't", "cannot", "could", "couldn't", "did", "didn't",
    "do", "does", "doesn't", "doing", "don't", "down", "during", "each", "few", "for", "from",
    "further", "had", "hadn't", "has", "hasn't", "have", "haven't", "having", "he", "he'd",
    "he'll", "he's", "her", "here", "here's", "hers", "herself", "him", "himself", "his",
    "how", "how's", "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't",
    "it", "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought", "our",
    "ours", "ourselves", "out", "over", "own", "same", "shan't", "she", "she'd", "she'll",
    "she's", "should", "shouldn't", "so", "some", "such", "than", "that", "that's", "the",
    "their", "theirs", "them", "themselves", "then", "there", "there's", "these", "they",
    "they'd", "they'll", "they're", "they've", "this", "those", "through", "to", "too",
    "under", "until", "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've",
    "were", "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would", "wouldn't",
    "you", "you'd", "you'll", "you're", "you've", "your", "yours", "yourself", "yourselves",
})

COMMON_ABBREVIATIONS: tuple[str, ...] = (
    "e.g.", "i.e.", "vs.", "etc.", "v1.0", "v2.0", "v3.0", "approx.", "fig.",
    "inc.", "ltd.", "dr.", "mr.", "ms.", "prof.", "dept.", "sec.", "no.",
)


def filter_document_diversity(
    evidence_items: list[EvidenceItem],
    max_documents: int | None = None,
) -> list[EvidenceItem]:
    """Filter evidence items to retain at most one chunk per unique document.

    Preserves the relative ordering established by upstream retrieval and
    Phase 4E ranking.
    """
    seen_doc_ids: set[str] = set()
    diverse_items: list[EvidenceItem] = []

    for item in evidence_items:
        doc_id = item.document_id
        if doc_id not in seen_doc_ids:
            seen_doc_ids.add(doc_id)
            diverse_items.append(item)
            if max_documents is not None and len(diverse_items) >= max_documents:
                break

    return diverse_items


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


def extract_salient_sentences(
    text: str,
    query: str,
    max_sentences: int = 3,
) -> str:
    """Extract top salient sentences from chunk text while preserving header metadata.

    1. Separates header / metadata block (e.g. title, timestamps, key-value lines).
    2. Scores remaining sentences by query term overlap and phrase matching.
    3. Retains top-K sentences in their original chronological order.
    4. Reattaches the header block.
    """
    clean_text = text.strip()
    if not clean_text:
        return clean_text

    # Detect header block (lines before first blank line, or short key-value pairs)
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
        ):
            header = candidate_header.strip()
            body = candidate_body.strip()

    sentences = _split_into_sentences(body)
    if len(sentences) <= max_sentences:
        return clean_text

    # Extract query tokens
    query_tokens = [
        t.lower() for t in re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", query)
        if t.lower() not in STOP_WORDS
    ]
    query_set = set(query_tokens)
    query_lower = query.lower()

    scored_sentences: list[tuple[float, int, str]] = []
    for idx, sent in enumerate(sentences):
        sent_lower = sent.lower()
        sent_words = set(re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", sent_lower))

        # Overlap score
        overlap = len(sent_words.intersection(query_set))

        # Bonus for multi-word exact subphrase matches (e.g., "checkout service", "connection pool")
        phrase_bonus = 0.0
        for i in range(len(query_tokens) - 1):
            bigram = f"{query_tokens[i]} {query_tokens[i+1]}"
            if bigram in sent_lower:
                phrase_bonus += 1.5

        # Slight positional bias favoring earlier introductory sentences on tie
        positional_bias = (len(sentences) - idx) * 0.001
        total_score = overlap + phrase_bonus + positional_bias
        scored_sentences.append((total_score, idx, sent))

    # Pick top max_sentences
    scored_sentences.sort(key=lambda x: x[0], reverse=True)
    picked = scored_sentences[:max_sentences]
    # Re-sort to preserve original text sequence
    picked.sort(key=lambda x: x[1])

    compressed_body = " ".join(s[2] for s in picked)
    if header:
        return f"{header}\n\n{compressed_body}"
    return compressed_body


def compress_evidence_item(
    item: EvidenceItem,
    query: str,
    max_sentences: int = 3,
) -> EvidenceItem:
    """Return a clone of EvidenceItem with compressed text."""
    compressed_text = extract_salient_sentences(
        item.text,
        query=query,
        max_sentences=max_sentences,
    )
    new_item = copy.copy(item)
    new_item.text = compressed_text
    return new_item


def estimate_token_count(text: str, tokenizer: Any = None) -> int:
    """Estimate token count using tokenizer if available, or fast lexical proxy."""
    if tokenizer is not None and hasattr(tokenizer, "encode"):
        try:
            return len(tokenizer.encode(text, add_special_tokens=False))
        except Exception:
            pass
    # Lexical proxy: ~1.35 tokens per whitespace word
    words = text.split()
    return max(1, int(len(words) * 1.35))


class AdaptiveContextBudgeter:
    """Engine for document-diverse selection and salience compression of evidence packages."""

    def __init__(
        self,
        default_token_budget: int = 750,
        grounding_gate: Any = None,
        hierarchical_budgeter: Any = None,
        evidence_selector: Any = None,
    ):
        self.default_token_budget = default_token_budget
        self._grounding_gate = grounding_gate
        self._hierarchical_budgeter = hierarchical_budgeter
        self._evidence_selector = evidence_selector

    @property
    def grounding_gate(self) -> Any:
        """Lazily initialize the EntityGroundingGate if not provided."""
        if self._grounding_gate is None:
            try:
                from novastack.entity_grounding import EntityGroundingGate
                self._grounding_gate = EntityGroundingGate()
            except Exception as e:
                import logging
                logging.getLogger("novastack.context_budgeter").warning(
                    "Failed to initialize EntityGroundingGate: %s", e
                )
                self._grounding_gate = None
        return self._grounding_gate

    @property
    def hierarchical_budgeter(self) -> Any:
        """Lazily initialize the HierarchicalContextBudgeter if not provided."""
        if self._hierarchical_budgeter is None:
            try:
                from novastack.hierarchical_budgeter import HierarchicalContextBudgeter
                self._hierarchical_budgeter = HierarchicalContextBudgeter()
            except Exception as e:
                import logging
                logging.getLogger("novastack.context_budgeter").warning(
                    "Failed to initialize HierarchicalContextBudgeter: %s", e
                )
                self._hierarchical_budgeter = None
        return self._hierarchical_budgeter

    @property
    def evidence_selector(self) -> Any:
        """Lazily initialize the MinimumSufficientEvidenceSelector if not provided."""
        if self._evidence_selector is None:
            try:
                from novastack.evidence_selector import MinimumSufficientEvidenceSelector, SelectorConfig
                cfg = SelectorConfig(
                    enable_adaptive_depth=True,
                    enable_contrastive_disambiguation=True,
                    enable_targeted_missing_role_recovery=True,
                    require_entity_overlap_for_fill=True,
                    enable_alias_context_notes=True,
                )
                self._evidence_selector = MinimumSufficientEvidenceSelector(
                    grounding_gate=self.grounding_gate,
                    config=cfg,
                )
            except Exception as e:
                import logging
                logging.getLogger("novastack.context_budgeter").warning(
                    "Failed to initialize MinimumSufficientEvidenceSelector: %s", e
                )
                self._evidence_selector = None
        return self._evidence_selector


    def budget_context(
        self,
        package: EvidencePackage,
        strategy: str = "raw_prefix",
        max_items: int | None = None,
        max_documents: int | None = None,
        compress_salience: bool = False,
        calibrate_salience: bool = False,
        max_sentences_per_chunk: int = 3,
        max_token_budget: int | None = None,
        tokenizer: Any = None,
    ) -> list[EvidenceItem]:
        """Select and format evidence items according to the requested budgeting strategy.

        Strategies:
        - 'raw_prefix': Traditional prefix slicing of package.selected_evidence[:max_items].
        - 'document_diversity': Filters at most 1 chunk per document up to max_documents.
        - 'salience_compression': Document-diverse filtering + sentence-level compression per chunk.
        - 'calibrated_salience': Salience compression gated by EntityGroundingGate (preserves full context for ungrounded/sensitive queries).
        - 'adaptive_density': Document-diverse filtering + compression, budgeted dynamically up to max_token_budget.
        """
        raw_items = package.selected_evidence
        if not raw_items:
            return []

        query = package.query
        tenant_id = getattr(package, "tenant_id", "TENANT-NOVASTACK")
        token_cap = max_token_budget or self.default_token_budget

        if strategy == "raw_prefix":
            return raw_items[:max_items] if max_items is not None else list(raw_items)

        elif strategy == "document_diversity":
            limit = max_documents if max_documents is not None else max_items
            return filter_document_diversity(raw_items, max_documents=limit)

        elif strategy == "salience_compression":
            limit = max_documents if max_documents is not None else max_items
            diverse_items = filter_document_diversity(raw_items, max_documents=limit)

            if calibrate_salience and self.grounding_gate is not None:
                grounding = self.grounding_gate.ground_query(query=query, tenant_id=tenant_id)
                calibrated: list[EvidenceItem] = []
                for it in diverse_items:
                    dec = self.grounding_gate.evaluate_compaction_safety(
                        query=query, chunk=it, grounding=grounding, tenant_id=tenant_id
                    )
                    if dec.compaction_eligible:
                        calibrated.append(
                            compress_evidence_item(it, query=query, max_sentences=max_sentences_per_chunk)
                        )
                    else:
                        calibrated.append(it)
                return calibrated

            return [
                compress_evidence_item(it, query=query, max_sentences=max_sentences_per_chunk)
                for it in diverse_items
            ]

        elif strategy == "calibrated_salience":
            limit = max_documents if max_documents is not None else max_items
            diverse_items = filter_document_diversity(raw_items, max_documents=limit)

            if self.grounding_gate is not None:
                grounding = self.grounding_gate.ground_query(query=query, tenant_id=tenant_id)
                calibrated: list[EvidenceItem] = []
                for it in diverse_items:
                    dec = self.grounding_gate.evaluate_compaction_safety(
                        query=query, chunk=it, grounding=grounding, tenant_id=tenant_id
                    )
                    if dec.compaction_eligible:
                        calibrated.append(
                            compress_evidence_item(it, query=query, max_sentences=max_sentences_per_chunk)
                        )
                    else:
                        calibrated.append(it)
                return calibrated

            # Fallback to uncompressed if gate unavailable
            return diverse_items

        elif strategy == "adaptive_density":
            limit = max_documents or 5
            diverse_candidates = filter_document_diversity(raw_items, max_documents=limit)

            budgeted_items: list[EvidenceItem] = []
            cumulative_tokens = 0
            grounding = None
            if compress_salience and calibrate_salience and self.grounding_gate is not None:
                grounding = self.grounding_gate.ground_query(query=query, tenant_id=tenant_id)

            for candidate in diverse_candidates:
                if compress_salience:
                    should_compress = True
                    if calibrate_salience and self.grounding_gate is not None:
                        dec = self.grounding_gate.evaluate_compaction_safety(
                            query=query, chunk=candidate, grounding=grounding, tenant_id=tenant_id
                        )
                        should_compress = dec.compaction_eligible

                    if should_compress:
                        processed = compress_evidence_item(
                            candidate,
                            query=query,
                            max_sentences=max_sentences_per_chunk,
                        )
                    else:
                        processed = candidate
                else:
                    processed = candidate

                item_tokens = estimate_token_count(processed.text, tokenizer=tokenizer)
                if cumulative_tokens + item_tokens > token_cap and len(budgeted_items) >= 2:
                    break

                budgeted_items.append(processed)
                cumulative_tokens += item_tokens

            return budgeted_items

        elif strategy in ("hierarchical_soft_compaction", "hierarchical_budgeting"):
            if self.hierarchical_budgeter is not None:
                limit = max_documents
                return self.hierarchical_budgeter.budget_evidence_package(
                    package=package,
                    max_documents=limit,
                    max_token_budget=max_token_budget or 350,
                    tokenizer=tokenizer,
                )
            return filter_document_diversity(raw_items, max_documents=max_documents or max_items or 3)

        elif strategy in ("minimum_sufficient_selection", "m5_selection"):
            if self.evidence_selector is not None:
                sel_res = self.evidence_selector.select_minimum_sufficient_evidence(
                    package=package,
                    max_documents=max_documents,
                )
                return sel_res.selected_items
            return filter_document_diversity(raw_items, max_documents=max_documents or max_items or 3)

        elif strategy in (
            "minimum_sufficient_hierarchical",
            "minimum_sufficient_budgeting",
            "m5_hierarchical",
            "minimum_sufficient_supporting",
        ):
            if self.evidence_selector is not None:
                # If supporting extension is requested (Config E)
                if strategy == "minimum_sufficient_supporting":
                    old_ext = getattr(self.evidence_selector.config, "enable_supporting_extension", False)
                    self.evidence_selector.config.enable_supporting_extension = True
                    sel_res = self.evidence_selector.select_minimum_sufficient_evidence(
                        package=package,
                        max_documents=max_documents,
                    )
                    self.evidence_selector.config.enable_supporting_extension = old_ext
                else:
                    sel_res = self.evidence_selector.select_minimum_sufficient_evidence(
                        package=package,
                        max_documents=max_documents,
                    )

                selected_raw = sel_res.selected_items
                if self.hierarchical_budgeter is not None and selected_raw:
                    sub_pkg = copy.copy(package)
                    sub_pkg.selected_evidence = selected_raw
                    sub_pkg.context_notes = getattr(package, "context_notes", [])
                    # Budget with soft compaction under 460 token ceiling
                    return self.hierarchical_budgeter.budget_evidence_package(
                        package=sub_pkg,
                        max_documents=len(selected_raw),
                        max_token_budget=max_token_budget or 460,
                        tokenizer=tokenizer,
                    )
                return selected_raw

            if self.hierarchical_budgeter is not None:
                return self.hierarchical_budgeter.budget_evidence_package(
                    package=package,
                    max_documents=max_documents,
                    max_token_budget=max_token_budget or 350,
                    tokenizer=tokenizer,
                )
            return filter_document_diversity(raw_items, max_documents=max_documents or max_items or 3)

        elif strategy in (
            "targeted_evidence_extraction",
            "m8_targeted_extraction",
            "minimum_sufficient_targeted",
        ):
            if self.evidence_selector is not None:
                old_ext = getattr(self.evidence_selector.config, "enable_targeted_evidence_extraction", False)
                self.evidence_selector.config.enable_targeted_evidence_extraction = True
                sel_res = self.evidence_selector.select_minimum_sufficient_evidence(
                    package=package,
                    max_documents=max_documents,
                )
                self.evidence_selector.config.enable_targeted_evidence_extraction = old_ext
                return sel_res.selected_items
            return filter_document_diversity(raw_items, max_documents=max_documents or max_items or 3)

        else:
            raise ValueError(f"Unknown context budgeting strategy: '{strategy}'")

