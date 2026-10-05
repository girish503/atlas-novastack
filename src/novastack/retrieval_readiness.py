"""Retrieval index readiness and corpus audit subsystem — Phase 2C.

Establishes the formal Retrieval Index Contract and executes an exhaustive
audit of the canonical SearchChunk corpus before downstream retrieval
implementations (lexical BM25, dense embeddings, hybrid search, rerankers).

Preserves the strict architectural lineage:
SearchChunk -> SearchDocument -> SourceRecord -> Ground Truth / Provenance
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from novastack.models import (
    AUTHORITY_LEVELS,
    CLASSIFICATION_LEVELS,
    RECORD_STATUSES,
    SOURCE_TYPES,
    EvaluationCase,
    RecordPermissions,
    SearchChunk,
    SearchDocument,
)

__all__ = [
    "PROVENANCE_FIELDS",
    "RANKING_DEBUG_FIELDS",
    "RETRIEVAL_TEXT_FIELDS",
    "RetrievalIndexContract",
    "RetrievalReadinessReport",
    "SECURITY_FILTER_FIELDS",
    "STABLE_IDENTIFIER_FIELDS",
    "TEMPORAL_FILTER_FIELDS",
    "audit_retrieval_readiness",
    "validate_retrieval_contract",
]

# =====================================================================
# Formal Retrieval Index Contract Definitions
# =====================================================================

RETRIEVAL_TEXT_FIELDS: set[str] = {
    "title",
    "text",
}

STABLE_IDENTIFIER_FIELDS: set[str] = {
    "chunk_id",
    "document_id",
    "chunk_index",
    "total_chunks",
}

SECURITY_FILTER_FIELDS: set[str] = {
    "tenant_id",
    "classification",
    "permissions",
    "department",
}

TEMPORAL_FILTER_FIELDS: set[str] = {
    "created_at",
    "updated_at",
    "valid_from",
    "valid_until",
}

PROVENANCE_FIELDS: set[str] = {
    "source_entity_id",
    "source_entity_type",
    "related_entity_ids",
    "parent_id",
    "supersedes_id",
}

RANKING_DEBUG_FIELDS: set[str] = {
    "source_type",
    "authority_level",
    "status",
    "version",
    "author_id",
    "char_count",
    "word_count",
}


@dataclass(frozen=True)
class RetrievalIndexContract:
    """Explicit structural specification for downstream retrieval engine index payloads."""

    retrieval_text: set[str] = field(default_factory=lambda: set(RETRIEVAL_TEXT_FIELDS))
    stable_identifiers: set[str] = field(default_factory=lambda: set(STABLE_IDENTIFIER_FIELDS))
    security_filters: set[str] = field(default_factory=lambda: set(SECURITY_FILTER_FIELDS))
    temporal_filters: set[str] = field(default_factory=lambda: set(TEMPORAL_FILTER_FIELDS))
    provenance: set[str] = field(default_factory=lambda: set(PROVENANCE_FIELDS))
    ranking_debug: set[str] = field(default_factory=lambda: set(RANKING_DEBUG_FIELDS))

    def all_contract_fields(self) -> set[str]:
        """Union of all contract fields across all 6 functional tiers."""
        return (
            self.retrieval_text
            | self.stable_identifiers
            | self.security_filters
            | self.temporal_filters
            | self.provenance
            | self.ranking_debug
        )


def validate_retrieval_contract(chunk: SearchChunk | dict[str, Any]) -> list[str]:
    """Validate that a SearchChunk conforms to the formal Retrieval Index Contract.

    Verifies presence, types, controlled vocabulary values, and structural bounds.
    """
    errors: list[str] = []
    d = chunk.to_dict() if isinstance(chunk, SearchChunk) else dict(chunk)

    # 1. Retrieval Text Fields
    title = d.get("title")
    if not title or not isinstance(title, str) or not title.strip():
        errors.append("Missing or empty 'title' in retrieval text tier.")

    text = d.get("text")
    if not text or not isinstance(text, str) or not text.strip():
        errors.append("Missing or empty 'text' in retrieval text tier.")

    # 2. Stable Identifiers
    cid = d.get("chunk_id")
    did = d.get("document_id")
    cidx = d.get("chunk_index")
    total = d.get("total_chunks")

    if not cid or not isinstance(cid, str):
        errors.append("Missing or invalid 'chunk_id'.")
    if not did or not isinstance(did, str):
        errors.append("Missing or invalid 'document_id'.")
    if cidx is None or not isinstance(cidx, int) or cidx < 0:
        errors.append(f"Invalid 'chunk_index': {cidx}.")
    if total is None or not isinstance(total, int) or total <= 0:
        errors.append(f"Invalid 'total_chunks': {total}.")
    if cidx is not None and total is not None and cidx >= total:
        errors.append(f"chunk_index {cidx} >= total_chunks {total}.")

    if cid and did and cidx is not None:
        expected_cid = f"{did}::CHUNK-{cidx + 1:04d}"
        if cid != expected_cid:
            errors.append(f"chunk_id '{cid}' does not match expected pattern '{expected_cid}'.")

    # 3. Security & Filter Metadata
    tenant = d.get("tenant_id")
    if not tenant or not isinstance(tenant, str):
        errors.append("Missing or invalid 'tenant_id'.")

    classification = d.get("classification")
    if classification not in CLASSIFICATION_LEVELS:
        errors.append(f"Invalid 'classification': {classification}.")

    perms = d.get("permissions")
    if not isinstance(perms, (RecordPermissions, dict)):
        errors.append("Invalid 'permissions' structure.")

    dept = d.get("department")
    if not dept or not isinstance(dept, str):
        errors.append("Missing or invalid 'department'.")

    # 4. Temporal Metadata
    created_at = d.get("created_at")
    if not created_at or not isinstance(created_at, str):
        errors.append("Missing or invalid 'created_at'.")

    # 5. Provenance Metadata
    if "related_entity_ids" not in d or not isinstance(d["related_entity_ids"], list):
        errors.append("Missing or invalid 'related_entity_ids' list.")

    # 6. Ranking & Debug Metadata
    st = d.get("source_type")
    if st not in SOURCE_TYPES:
        errors.append(f"Invalid 'source_type': {st}.")

    auth = d.get("authority_level")
    if auth not in AUTHORITY_LEVELS:
        errors.append(f"Invalid 'authority_level': {auth}.")

    status = d.get("status")
    if status not in RECORD_STATUSES:
        errors.append(f"Invalid 'status': {status}.")

    char_cnt = d.get("char_count")
    word_cnt = d.get("word_count")
    if text:
        if char_cnt != len(text):
            errors.append(f"char_count {char_cnt} != len(text) {len(text)}.")
        if word_cnt != len(text.split()):
            errors.append(f"word_count {word_cnt} != len(text.split()) {len(text.split())}.")

    return errors


# =====================================================================
# Retrieval Readiness Report & Audit Engine
# =====================================================================

@dataclass
class RetrievalReadinessReport:
    """Comprehensive readiness and baseline audit report for retrieval indexing."""

    total_chunks: int = 0
    unique_chunk_ids: int = 0
    unique_document_ids: int = 0
    chunks_per_document_avg: float = 0.0

    # Sizing & Length Metrics
    chunk_length_min: int = 0
    chunk_length_max: int = 0
    chunk_length_avg: float = 0.0
    chunk_length_median: float = 0.0
    chunk_length_percentiles: dict[str, float] = field(default_factory=dict)

    # Distributions
    source_type_distribution: dict[str, int] = field(default_factory=dict)
    classification_distribution: dict[str, int] = field(default_factory=dict)
    tenant_distribution: dict[str, int] = field(default_factory=dict)
    authority_distribution: dict[str, int] = field(default_factory=dict)
    status_distribution: dict[str, int] = field(default_factory=dict)

    # Metadata Coverage
    chunks_with_acl_metadata: int = 0
    chunks_with_temporal_metadata: int = 0
    chunks_with_provenance_metadata: int = 0

    # Quality & Anomaly Indicators
    duplicate_text_group_count: int = 0
    duplicate_text_chunk_count: int = 0
    duplicate_chunk_id_count: int = 0
    orphaned_document_reference_count: int = 0
    malformed_metadata_count: int = 0
    empty_text_count: int = 0
    suspiciously_short_chunk_count: int = 0  # < 100 chars
    suspiciously_large_chunk_count: int = 0  # > 800 chars

    # Evaluation Resolvability
    evaluation_cases_total: int = 0
    evaluation_expected_docs_total: int = 0
    evaluation_expected_docs_resolved: int = 0
    evaluation_expected_docs_resolvability_rate: float = 1.0
    evaluation_unresolvable_doc_ids: list[str] = field(default_factory=list)

    # Errors and Warnings
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert report to JSON-serializable dictionary."""
        return asdict(self)

    def format_report(self) -> str:
        """Format the report into a structured console summary."""
        lines = [
            "============================================================",
            "ATLAS Retrieval Index Readiness & Baseline Audit (Phase 2C)",
            "============================================================",
            f"  Total Chunks Audited:           {self.total_chunks:>6}",
            f"  Unique Chunk IDs:               {self.unique_chunk_ids:>6}",
            f"  Unique Document IDs:            {self.unique_document_ids:>6}",
            f"  Avg Chunks / Document:          {self.chunks_per_document_avg:>6.2f}",
            "",
            "  Chunk Length Metrics (Characters):",
            f"    - Min:                        {self.chunk_length_min:>6}",
            f"    - Max:                        {self.chunk_length_max:>6}",
            f"    - Avg:                        {self.chunk_length_avg:>6.1f}",
            f"    - Median (p50):               {self.chunk_length_median:>6.1f}",
            f"    - p10:                        {self.chunk_length_percentiles.get('p10', 0):>6.1f}",
            f"    - p25:                        {self.chunk_length_percentiles.get('p25', 0):>6.1f}",
            f"    - p75:                        {self.chunk_length_percentiles.get('p75', 0):>6.1f}",
            f"    - p90:                        {self.chunk_length_percentiles.get('p90', 0):>6.1f}",
            f"    - p95:                        {self.chunk_length_percentiles.get('p95', 0):>6.1f}",
            f"    - p99:                        {self.chunk_length_percentiles.get('p99', 0):>6.1f}",
            "",
            "  Metadata Coverage:",
            f"    - Chunks with ACL Metadata:   {self.chunks_with_acl_metadata:>6}",
            f"    - Chunks with Temporal Bounds:{self.chunks_with_temporal_metadata:>6}",
            f"    - Chunks with Provenance Links:{self.chunks_with_provenance_metadata:>6}",
            "",
            "  Corpus Quality & Anomaly Checks:",
            f"    - Duplicate Chunk IDs:        {self.duplicate_chunk_id_count:>6}",
            f"    - Orphaned Document Refs:     {self.orphaned_document_reference_count:>6}",
            f"    - Malformed Contract Records: {self.malformed_metadata_count:>6}",
            f"    - Empty Text Chunks:          {self.empty_text_count:>6}",
            f"    - Suspiciously Short (<100c): {self.suspiciously_short_chunk_count:>6}",
            f"    - Suspiciously Large (>800c): {self.suspiciously_large_chunk_count:>6}",
            f"    - Exact Duplicate Text Groups:{self.duplicate_text_group_count:>6} ({self.duplicate_text_chunk_count} chunk instances)",
            "",
            "  Evaluation Ground Truth Resolvability:",
            f"    - Evaluation Cases Evaluated: {self.evaluation_cases_total:>6}",
            f"    - Expected Document IDs:      {self.evaluation_expected_docs_total:>6}",
            f"    - Resolved Document IDs:      {self.evaluation_expected_docs_resolved:>6}",
            f"    - Resolvability Rate:         {self.evaluation_expected_docs_resolvability_rate * 100:>5.1f}%",
            f"    - Unresolvable Document IDs:  {len(self.evaluation_unresolvable_doc_ids):>6}",
            "",
            "  Chunks by Source Type:",
        ]
        for st, cnt in sorted(self.source_type_distribution.items()):
            lines.append(f"    - {st:<28} {cnt:>6}")

        lines.append("\n  Chunks by Classification:")
        for cls, cnt in sorted(self.classification_distribution.items()):
            lines.append(f"    - {cls:<28} {cnt:>6}")

        lines.append("\n  Chunks by Tenant:")
        for tid, cnt in sorted(self.tenant_distribution.items()):
            lines.append(f"    - {tid:<28} {cnt:>6}")

        lines.append("\n  Chunks by Authority Level:")
        for auth, cnt in sorted(self.authority_distribution.items()):
            lines.append(f"    - {auth:<28} {cnt:>6}")

        lines.append("\n  Chunks by Status:")
        for stat, cnt in sorted(self.status_distribution.items()):
            lines.append(f"    - {stat:<28} {cnt:>6}")

        lines.append("============================================================")

        if self.errors:
            lines.append("\n  Validation Errors:")
            for err in self.errors[:10]:
                lines.append(f"    [ERROR] {err}")
            if len(self.errors) > 10:
                lines.append(f"    ... and {len(self.errors) - 10} more errors")

        return "\n".join(lines)


def _load_chunks_list(
    source: list[SearchChunk] | list[dict[str, Any]] | Path | str | dict[str, Any],
) -> list[SearchChunk]:
    """Load SearchChunk instances from various input formats."""
    if isinstance(source, (str, Path)):
        p = Path(source)
        if not p.exists():
            raise FileNotFoundError(f"Search chunks file not found at: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _load_chunks_list(data)

    if isinstance(source, dict):
        chunks_data = source.get("search_chunks", [])
        return [SearchChunk.from_dict(c) for c in chunks_data]

    if isinstance(source, list):
        if not source:
            return []
        if isinstance(source[0], SearchChunk):
            return source  # type: ignore[return-value]
        if isinstance(source[0], dict):
            return [SearchChunk.from_dict(c) for c in source]  # type: ignore[arg-type]

    raise TypeError(f"Unsupported source type for _load_chunks_list: {type(source)}")


def _load_documents_map(
    source: dict[str, SearchDocument] | list[SearchDocument] | Path | str | dict[str, Any] | None,
) -> dict[str, SearchDocument]:
    """Load SearchDocument map keyed by document_id."""
    if source is None:
        return {}

    if isinstance(source, (str, Path)):
        p = Path(source)
        if not p.exists():
            raise FileNotFoundError(f"Search documents file not found at: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _load_documents_map(data)

    if isinstance(source, dict):
        if "search_documents" in source:
            docs_list = source["search_documents"]
            return {d["document_id"]: SearchDocument.from_dict(d) for d in docs_list}
        return source  # type: ignore[return-value]

    if isinstance(source, list):
        out = {}
        for item in source:
            if isinstance(item, SearchDocument):
                out[item.document_id] = item
            elif isinstance(item, dict):
                doc = SearchDocument.from_dict(item)
                out[doc.document_id] = doc
        return out

    raise TypeError(f"Unsupported source type for _load_documents_map: {type(source)}")


def _load_eval_cases(
    source: list[EvaluationCase] | list[dict[str, Any]] | Path | str | dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Load EvaluationCase list."""
    if source is None:
        return []

    if isinstance(source, (str, Path)):
        p = Path(source)
        if not p.exists():
            raise FileNotFoundError(f"Evaluation cases file not found at: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _load_eval_cases(data)

    if isinstance(source, dict):
        return source.get("evaluation_cases", [])

    if isinstance(source, list):
        out: list[dict[str, Any]] = []
        for item in source:
            if isinstance(item, EvaluationCase):
                out.append(item.to_dict())
            elif isinstance(item, dict):
                out.append(item)
        return out

    raise TypeError(f"Unsupported source type for _load_eval_cases: {type(source)}")


def audit_retrieval_readiness(
    chunks: list[SearchChunk] | Path | str | dict[str, Any],
    documents: dict[str, SearchDocument] | list[SearchDocument] | Path | str | dict[str, Any] | None = None,
    evaluation_cases: list[EvaluationCase] | list[dict[str, Any]] | Path | str | dict[str, Any] | None = None,
) -> RetrievalReadinessReport:
    """Perform an exhaustive retrieval-readiness audit across the SearchChunk corpus.

    Evaluates:
    - Contract compliance across all 6 tiers (Text, IDs, Security, Temporal, Provenance, Ranking)
    - Structural and index constraints (uniqueness, empty text, suspicious sizes)
    - Metadata distributions (source types, classifications, tenants, authorities, statuses)
    - Parent document referential linkage
    - Natural duplicate text groups
    - Evaluation ground truth document ID resolvability
    """
    chunk_list = _load_chunks_list(chunks)
    doc_map = _load_documents_map(documents)
    eval_list = _load_eval_cases(evaluation_cases)

    report = RetrievalReadinessReport(total_chunks=len(chunk_list))
    if not chunk_list:
        report.warnings.append("Corpus contains 0 chunks.")
        return report

    seen_chunk_ids: set[str] = set()
    duplicate_chunk_ids = 0
    orphaned_docs = 0
    empty_texts = 0
    malformed_metadata = 0
    short_chunks = 0
    large_chunks = 0

    lengths: list[int] = []
    text_counter: Counter[str] = Counter()
    st_counter: Counter[str] = Counter()
    cls_counter: Counter[str] = Counter()
    tenant_counter: Counter[str] = Counter()
    auth_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()

    acl_count = 0
    temporal_count = 0
    provenance_count = 0

    chunk_doc_ids: set[str] = set()

    for c in chunk_list:
        # Validate formal contract
        contract_errs = validate_retrieval_contract(c)
        if contract_errs:
            malformed_metadata += 1
            report.errors.extend(contract_errs)

        # Unique chunk ID check
        if c.chunk_id in seen_chunk_ids:
            duplicate_chunk_ids += 1
            report.errors.append(f"Duplicate chunk ID: '{c.chunk_id}'.")
        else:
            seen_chunk_ids.add(c.chunk_id)

        # Text length & content
        cleaned_text = c.text.strip()
        if not cleaned_text:
            empty_texts += 1
            report.errors.append(f"Empty text in chunk '{c.chunk_id}'.")

        text_len = c.char_count
        lengths.append(text_len)
        text_counter[cleaned_text] += 1

        if text_len < 100:
            short_chunks += 1
        if text_len > 800:
            large_chunks += 1

        # Parent document reference check
        chunk_doc_ids.add(c.document_id)
        if doc_map and c.document_id not in doc_map:
            orphaned_docs += 1
            report.errors.append(
                f"Chunk '{c.chunk_id}' references unknown parent document '{c.document_id}'."
            )

        # Distributions
        st_counter[c.source_type] += 1
        cls_counter[c.classification] += 1
        tenant_counter[c.tenant_id] += 1
        auth_counter[c.authority_level] += 1
        status_counter[c.status] += 1

        # Metadata tracking
        perms = c.permissions
        if perms and (perms.allowed_roles or perms.allowed_departments or perms.allowed_teams or perms.allowed_user_ids):
            acl_count += 1

        if c.valid_from or c.valid_until:
            temporal_count += 1

        if c.source_entity_id or c.related_entity_ids:
            provenance_count += 1

    # Populate primary metrics
    report.unique_chunk_ids = len(seen_chunk_ids)
    report.unique_document_ids = len(chunk_doc_ids)
    if chunk_doc_ids:
        report.chunks_per_document_avg = len(chunk_list) / len(chunk_doc_ids)

    # Length metrics & percentiles
    if lengths:
        report.chunk_length_min = min(lengths)
        report.chunk_length_max = max(lengths)
        report.chunk_length_avg = sum(lengths) / len(lengths)
        report.chunk_length_median = statistics.median(lengths)

        sorted_lens = sorted(lengths)
        n = len(sorted_lens)
        report.chunk_length_percentiles = {
            "p10": statistics.quantiles(sorted_lens, n=10)[0] if n >= 10 else float(sorted_lens[0]),
            "p25": statistics.quantiles(sorted_lens, n=4)[0] if n >= 4 else float(sorted_lens[0]),
            "p50": statistics.median(sorted_lens),
            "p75": statistics.quantiles(sorted_lens, n=4)[2] if n >= 4 else float(sorted_lens[-1]),
            "p90": statistics.quantiles(sorted_lens, n=10)[8] if n >= 10 else float(sorted_lens[-1]),
            "p95": statistics.quantiles(sorted_lens, n=20)[18] if n >= 20 else float(sorted_lens[-1]),
            "p99": statistics.quantiles(sorted_lens, n=100)[98] if n >= 100 else float(sorted_lens[-1]),
        }

    # Distributions
    report.source_type_distribution = dict(st_counter)
    report.classification_distribution = dict(cls_counter)
    report.tenant_distribution = dict(tenant_counter)
    report.authority_distribution = dict(auth_counter)
    report.status_distribution = dict(status_counter)

    # Coverage
    report.chunks_with_acl_metadata = acl_count
    report.chunks_with_temporal_metadata = temporal_count
    report.chunks_with_provenance_metadata = provenance_count

    # Quality indicators
    report.duplicate_chunk_id_count = duplicate_chunk_ids
    report.orphaned_document_reference_count = orphaned_docs
    report.malformed_metadata_count = malformed_metadata
    report.empty_text_count = empty_texts
    report.suspiciously_short_chunk_count = short_chunks
    report.suspiciously_large_chunk_count = large_chunks

    # Exact duplicate text analysis (audit only)
    dup_groups = {txt: cnt for txt, cnt in text_counter.items() if cnt > 1}
    report.duplicate_text_group_count = len(dup_groups)
    report.duplicate_text_chunk_count = sum(dup_groups.values())

    # Evaluation case resolvability
    if eval_list:
        report.evaluation_cases_total = len(eval_list)
        all_expected_docs: set[str] = set()
        for ec in eval_list:
            all_expected_docs.update(ec.get("expected_document_ids", []))
            all_expected_docs.update(ec.get("required_document_ids", []))
            all_expected_docs.update(ec.get("acceptable_document_ids", []))
            all_expected_docs.update(ec.get("forbidden_document_ids", []))

        report.evaluation_expected_docs_total = len(all_expected_docs)
        resolved_docs = all_expected_docs.intersection(chunk_doc_ids)
        unresolved_docs = sorted(all_expected_docs - chunk_doc_ids)

        report.evaluation_expected_docs_resolved = len(resolved_docs)
        report.evaluation_unresolvable_doc_ids = unresolved_docs
        if all_expected_docs:
            report.evaluation_expected_docs_resolvability_rate = len(resolved_docs) / len(all_expected_docs)

        if unresolved_docs:
            report.errors.append(
                f"Evaluation cases contain {len(unresolved_docs)} unresolvable document IDs: {unresolved_docs[:5]}."
            )

    return report
