"""Semantic-aware document chunking subsystem — Phase 2B.

Transforms canonical SearchDocument objects into structured SearchChunk instances
serving as the retrieval units for lexical BM25, dense embeddings, hybrid search,
authorization pre-filtering, and reranking.

Preserves the strict architectural lineage:
SearchChunk -> SearchDocument -> SourceRecord -> Ground Truth / Provenance
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from novastack.models import (
    RecordPermissions,
    SearchChunk,
    SearchDocument,
)

# Re-export SearchChunk for convenient imports from novastack.chunking
__all__ = [
    "ChunkingConfig",
    "ChunkingReport",
    "SearchChunk",
    "chunk_document",
    "chunk_documents",
    "load_search_documents",
    "split_into_blocks",
    "validate_chunks",
]


@dataclass
class ChunkingConfig:
    """Hyperparameter configuration for the baseline document chunker."""

    target_chunk_size: int = 500   # Target chunk length in characters (~100 words, ~125 tokens)
    max_chunk_size: int = 800      # Hard ceiling for chunk length (~160 words, ~200 tokens)
    min_chunk_size: int = 100      # Minimum chunk size threshold (~20 words)
    overlap_size: int = 100        # Overlap window between consecutive chunks (~15–25 words)


@dataclass
class ChunkingReport:
    """Observability report capturing metrics from a document chunking execution."""

    input_document_count: int = 0
    output_chunk_count: int = 0
    average_chunks_per_document: float = 0.0
    min_chunk_size: int = 0
    max_chunk_size: int = 0
    avg_chunk_size: float = 0.0
    chunk_count_by_source_type: dict[str, int] = field(default_factory=dict)
    chunk_count_by_classification: dict[str, int] = field(default_factory=dict)
    chunk_count_by_tenant: dict[str, int] = field(default_factory=dict)
    documents_single_chunk: int = 0
    documents_multi_chunk: int = 0
    documents_zero_chunks: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def format_report(self) -> str:
        """Format the chunking metrics into a structured console summary."""
        lines = [
            "============================================================",
            "ATLAS Document Chunking Report (Phase 2B)",
            "============================================================",
            f"  Input Documents:                {self.input_document_count:>6}",
            f"  Output Search Chunks:           {self.output_chunk_count:>6}",
            f"  Average Chunks / Document:      {self.average_chunks_per_document:>6.2f}",
            f"  Single-Chunk Documents:         {self.documents_single_chunk:>6}",
            f"  Multi-Chunk Documents:          {self.documents_multi_chunk:>6}",
            f"  Zero-Chunk Documents:           {self.documents_zero_chunks:>6}",
            "",
            "  Chunk Size Metrics (Characters):",
            f"    - Min Chunk Size:             {self.min_chunk_size:>6}",
            f"    - Max Chunk Size:             {self.max_chunk_size:>6}",
            f"    - Avg Chunk Size:             {self.avg_chunk_size:>6.1f}",
            "",
            "  Chunks by Source Type:",
        ]
        for st, cnt in sorted(self.chunk_count_by_source_type.items()):
            lines.append(f"    - {st:<28} {cnt:>6}")

        lines.append("\n  Chunks by Classification:")
        for cls, cnt in sorted(self.chunk_count_by_classification.items()):
            lines.append(f"    - {cls:<28} {cnt:>6}")

        lines.append("\n  Chunks by Tenant:")
        for tid, cnt in sorted(self.chunk_count_by_tenant.items()):
            lines.append(f"    - {tid:<28} {cnt:>6}")

        lines.append("============================================================")

        if self.errors:
            lines.append("\n  Validation Errors:")
            for err in self.errors[:10]:
                lines.append(f"    [ERROR] {err}")
            if len(self.errors) > 10:
                lines.append(f"    ... and {len(self.errors) - 10} more errors")

        return "\n".join(lines)


def split_into_blocks(text: str) -> list[dict[str, str]]:
    """Parse text into structural Markdown blocks.

    Distinguishes:
    - Fenced code blocks (kept intact as coherent units)
    - Markdown headings (#, ##, ###)
    - Standard paragraphs, lists, and key-value blocks
    """
    lines = text.split("\n")
    blocks: list[dict[str, str]] = []
    current_lines: list[str] = []
    in_code = False

    for line in lines:
        stripped = line.strip()

        # Fenced code block toggling
        if stripped.startswith("```"):
            in_code = not in_code
            current_lines.append(line)
            if not in_code:
                # Fenced block ended
                blocks.append({"type": "code", "text": "\n".join(current_lines)})
                current_lines = []
            continue

        if in_code:
            current_lines.append(line)
            continue

        # Blank line outside code flushes current block
        if not stripped:
            if current_lines:
                b_text = "\n".join(current_lines)
                is_heading = current_lines[0].strip().startswith("#") and len(current_lines) == 1
                blocks.append({"type": "heading" if is_heading else "paragraph", "text": b_text})
                current_lines = []
        else:
            # Check if line is a standalone markdown heading
            if stripped.startswith("# ") or stripped.startswith("## ") or stripped.startswith("### "):
                if current_lines:
                    b_text = "\n".join(current_lines)
                    is_heading = current_lines[0].strip().startswith("#") and len(current_lines) == 1
                    blocks.append({"type": "heading" if is_heading else "paragraph", "text": b_text})
                    current_lines = []
                current_lines.append(line)
            else:
                current_lines.append(line)

    if current_lines:
        b_text = "\n".join(current_lines)
        is_heading = current_lines[0].strip().startswith("#") and len(current_lines) == 1
        blocks.append({"type": "heading" if is_heading else "paragraph", "text": b_text})

    return blocks


def _decompose_block(block: dict[str, str], max_size: int) -> list[dict[str, str]]:
    """Decompose an oversized block exceeding max_size along clean boundaries."""
    b_text = block["text"]
    if len(b_text) <= max_size:
        return [block]

    b_type = block["type"]
    if b_type == "code":
        # Split code along line boundaries
        lines = b_text.split("\n")
        decomposed: list[dict[str, str]] = []
        cur: list[str] = []
        cur_len = 0
        for l in lines:
            if cur_len + len(l) + 1 <= max_size:
                cur.append(l)
                cur_len += len(l) + 1
            else:
                if cur:
                    decomposed.append({"type": "code", "text": "\n".join(cur)})
                cur = [l]
                cur_len = len(l)
        if cur:
            decomposed.append({"type": "code", "text": "\n".join(cur)})
        return decomposed

    # For standard text, split along sentence boundaries first
    sentences = re.split(r"(?<=[.?!])\s+", b_text)
    decomposed: list[dict[str, str]] = []
    cur_sentences: list[str] = []
    cur_len = 0

    for s in sentences:
        if len(s) > max_size:
            # Sentence itself exceeds max_size; split along words
            if cur_sentences:
                decomposed.append({"type": "paragraph", "text": " ".join(cur_sentences)})
                cur_sentences = []
                cur_len = 0
            words = s.split()
            cur_words: list[str] = []
            word_len = 0
            for w in words:
                if word_len + len(w) + 1 <= max_size:
                    cur_words.append(w)
                    word_len += len(w) + 1
                else:
                    if cur_words:
                        decomposed.append({"type": "paragraph", "text": " ".join(cur_words)})
                    cur_words = [w]
                    word_len = len(w)
            if cur_words:
                decomposed.append({"type": "paragraph", "text": " ".join(cur_words)})
        elif cur_len + len(s) + 1 <= max_size:
            cur_sentences.append(s)
            cur_len += len(s) + 1
        else:
            if cur_sentences:
                decomposed.append({"type": "paragraph", "text": " ".join(cur_sentences)})
            cur_sentences = [s]
            cur_len = len(s)

    if cur_sentences:
        decomposed.append({"type": "paragraph", "text": " ".join(cur_sentences)})

    return decomposed


def chunk_document(
    document: SearchDocument,
    config: ChunkingConfig | None = None,
) -> list[SearchChunk]:
    """Deterministically chunk a canonical SearchDocument into SearchChunk instances.

    Guarantees:
    - Zero-join security inheritance (tenant_id, classification, permissions)
    - Full temporal, identity, authority, and provenance inheritance
    - Heading lookahead preventing orphaned headings at chunk boundaries
    - Coherent code block handling
    - Word-aligned overlap between adjacent chunks
    - Deterministic chunk IDs: {document_id}::CHUNK-{chunk_index+1:04d}
    """
    if config is None:
        config = ChunkingConfig()

    content = document.content.strip()

    # Empty content edge case
    if not content:
        raw_chunks = [""]
    elif len(content) <= config.target_chunk_size:
        # Document fits within target chunk size; retain as a single cohesive unit
        raw_chunks = [content]
    else:
        # Parse into structural Markdown blocks
        raw_blocks = split_into_blocks(content)
        decomposed_blocks: list[dict[str, str]] = []
        for b in raw_blocks:
            decomposed_blocks.extend(_decompose_block(b, config.max_chunk_size))

        raw_chunks: list[str] = []
        current_blocks: list[str] = []
        current_len = 0

        for i, b in enumerate(decomposed_blocks):
            b_type = b["type"]
            b_text = b["text"]
            sep_len = 2 if current_blocks else 0
            projected = current_len + sep_len + len(b_text)

            # Heading lookahead: prevent leaving a heading isolated at the end of a chunk
            if b_type == "heading" and current_blocks:
                next_len = len(decomposed_blocks[i + 1]["text"]) if i + 1 < len(decomposed_blocks) else 0
                if current_len >= config.min_chunk_size and (projected + 2 + next_len > config.max_chunk_size):
                    raw_chunks.append("\n\n".join(current_blocks))
                    current_blocks = [b_text]
                    current_len = len(b_text)
                    continue

            # Standard chunk flush boundary
            if current_blocks and (
                projected > config.max_chunk_size
                or (current_len >= config.target_chunk_size and projected > config.target_chunk_size)
            ):
                raw_chunks.append("\n\n".join(current_blocks))
                current_blocks = [b_text]
                current_len = len(b_text)
            else:
                current_blocks.append(b_text)
                current_len = projected

        if current_blocks:
            raw_chunks.append("\n\n".join(current_blocks))

        # Merge undersized trailing chunk if it can fit into previous chunk
        if len(raw_chunks) > 1 and len(raw_chunks[-1]) < config.min_chunk_size:
            combined_len = len(raw_chunks[-2]) + 2 + len(raw_chunks[-1])
            if combined_len <= config.max_chunk_size:
                merged = raw_chunks[-2] + "\n\n" + raw_chunks[-1]
                raw_chunks = raw_chunks[:-2] + [merged]

    # Apply word-aligned overlap across multi-chunk boundaries
    if config.overlap_size > 0 and len(raw_chunks) > 1:
        overlapped_chunks = [raw_chunks[0]]
        for i in range(1, len(raw_chunks)):
            prev = raw_chunks[i - 1]
            curr = raw_chunks[i]
            if len(prev) <= config.overlap_size:
                overlap = prev
            else:
                tail = prev[-config.overlap_size :]
                space_idx = tail.find(" ")
                if space_idx != -1 and space_idx < len(tail) - 10:
                    overlap = tail[space_idx + 1 :]
                else:
                    overlap = tail
            overlap = overlap.strip()
            if overlap and not curr.startswith(overlap):
                overlapped_chunks.append(f"{overlap}\n\n{curr}")
            else:
                overlapped_chunks.append(curr)
        raw_chunks = overlapped_chunks

    total_chunks = len(raw_chunks)
    search_chunks: list[SearchChunk] = []

    # Deep copy / clone permissions to ensure strict isolation
    perms = document.permissions
    cloned_perms = RecordPermissions(
        allowed_roles=list(perms.allowed_roles),
        allowed_departments=list(perms.allowed_departments),
        allowed_teams=list(perms.allowed_teams),
        allowed_user_ids=list(perms.allowed_user_ids),
    )

    for idx, chunk_text in enumerate(raw_chunks):
        chunk_id = f"{document.document_id}::CHUNK-{idx + 1:04d}"
        search_chunk = SearchChunk(
            chunk_id=chunk_id,
            document_id=document.document_id,
            tenant_id=document.tenant_id,
            chunk_index=idx,
            total_chunks=total_chunks,
            title=document.title,
            text=chunk_text,
            char_count=len(chunk_text),
            word_count=len(chunk_text.split()),
            source_type=document.source_type,
            department=document.department,
            author_id=document.author_id,
            classification=document.classification,
            permissions=cloned_perms,
            authority_level=document.authority_level,
            status=document.status,
            version=document.version,
            created_at=document.created_at,
            updated_at=document.updated_at,
            valid_from=document.valid_from,
            valid_until=document.valid_until,
            parent_id=document.parent_id,
            supersedes_id=document.supersedes_id,
            source_entity_id=document.source_entity_id,
            source_entity_type=document.source_entity_type,
            related_entity_ids=list(document.related_entity_ids),
        )
        search_chunks.append(search_chunk)

    return search_chunks


def load_search_documents(
    source: list[SearchDocument] | list[dict[str, Any]] | Path | str | dict[str, Any],
) -> list[SearchDocument]:
    """Load SearchDocument instances from various input formats."""
    if isinstance(source, (str, Path)):
        p = Path(source)
        if not p.exists():
            raise FileNotFoundError(f"Search documents file not found at: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return load_search_documents(data)

    if isinstance(source, dict):
        docs_data = source.get("search_documents", [])
        return [SearchDocument.from_dict(d) for d in docs_data]

    if isinstance(source, list):
        if not source:
            return []
        if isinstance(source[0], SearchDocument):
            return source  # type: ignore[return-value]
        if isinstance(source[0], dict):
            return [SearchDocument.from_dict(d) for d in source]  # type: ignore[arg-type]

    raise TypeError(f"Unsupported source type for load_search_documents: {type(source)}")


def chunk_documents(
    documents: list[SearchDocument] | Path | str | dict[str, Any],
    config: ChunkingConfig | None = None,
) -> tuple[list[SearchChunk], ChunkingReport]:
    """Batch chunk canonical SearchDocument objects into SearchChunk instances.

    Args:
        documents: Source documents (list, path, or dict).
        config: Optional ChunkingConfig hyperparameters.

    Returns:
        tuple[list[SearchChunk], ChunkingReport]: Generated chunks and execution report.
    """
    if config is None:
        config = ChunkingConfig()

    docs = load_search_documents(documents)
    report = ChunkingReport(input_document_count=len(docs))

    all_chunks: list[SearchChunk] = []
    st_counts: Counter[str] = Counter()
    cls_counts: Counter[str] = Counter()
    tenant_counts: Counter[str] = Counter()

    single_count = 0
    multi_count = 0
    zero_count = 0

    for doc in docs:
        chunks = chunk_document(doc, config)
        count = len(chunks)

        if count == 0:
            zero_count += 1
        elif count == 1:
            single_count += 1
        else:
            multi_count += 1

        for c in chunks:
            all_chunks.append(c)
            st_counts[c.source_type] += 1
            cls_counts[c.classification] += 1
            tenant_counts[c.tenant_id] += 1

    report.output_chunk_count = len(all_chunks)
    report.documents_single_chunk = single_count
    report.documents_multi_chunk = multi_count
    report.documents_zero_chunks = zero_count

    if docs:
        report.average_chunks_per_document = len(all_chunks) / len(docs)

    if all_chunks:
        sizes = [c.char_count for c in all_chunks]
        report.min_chunk_size = min(sizes)
        report.max_chunk_size = max(sizes)
        report.avg_chunk_size = sum(sizes) / len(sizes)

    report.chunk_count_by_source_type = dict(st_counts)
    report.chunk_count_by_classification = dict(cls_counts)
    report.chunk_count_by_tenant = dict(tenant_counts)

    return all_chunks, report


def validate_chunks(
    chunks: list[SearchChunk],
    parent_documents: dict[str, SearchDocument] | list[SearchDocument] | None = None,
) -> tuple[list[str], list[str]]:
    """Validate referential integrity, security inheritance, and structural constraints of chunks.

    Checks:
    - Global uniqueness of chunk_id
    - Non-empty chunk text
    - Valid chunk index bounds (0 <= chunk_index < total_chunks)
    - Consistency of total_chunks per document_id
    - Deterministic chunk_id naming format
    - Strict tenant_id inheritance from parent document
    - Strict classification inheritance from parent document
    - Strict permissions inheritance from parent document
    - Strict source metadata inheritance (author, department, source_type, authority, status, version)
    - Strict temporal and provenance inheritance
    """
    errors: list[str] = []
    warnings: list[str] = []

    seen_chunk_ids: set[str] = set()
    chunks_by_doc: dict[str, list[SearchChunk]] = defaultdict(list)

    # Convert parent_documents to dict if list provided
    parents_map: dict[str, SearchDocument] = {}
    if parent_documents is not None:
        if isinstance(parent_documents, list):
            parents_map = {d.document_id: d for d in parent_documents}
        elif isinstance(parent_documents, dict):
            parents_map = parent_documents

    for c in chunks:
        # 1. Unique chunk ID
        if not c.chunk_id:
            errors.append("Chunk missing chunk_id.")
        elif c.chunk_id in seen_chunk_ids:
            errors.append(f"Duplicate chunk_id: '{c.chunk_id}'.")
        else:
            seen_chunk_ids.add(c.chunk_id)

        # 2. Non-empty text
        if not c.text or not c.text.strip():
            errors.append(f"Chunk '{c.chunk_id}' has empty text.")

        # 3. Document reference
        if not c.document_id:
            errors.append(f"Chunk '{c.chunk_id}' missing document_id.")
        else:
            chunks_by_doc[c.document_id].append(c)

        # 4. Deterministic ID format: {document_id}::CHUNK-{chunk_index+1:04d}
        expected_id = f"{c.document_id}::CHUNK-{c.chunk_index + 1:04d}"
        if c.chunk_id != expected_id:
            errors.append(
                f"Chunk ID '{c.chunk_id}' does not match expected deterministic pattern '{expected_id}'."
            )

        # 5. Chunk index bounds
        if c.chunk_index < 0:
            errors.append(f"Chunk '{c.chunk_id}' has negative chunk_index: {c.chunk_index}.")
        if c.chunk_index >= c.total_chunks:
            errors.append(
                f"Chunk '{c.chunk_id}' has chunk_index {c.chunk_index} >= total_chunks {c.total_chunks}."
            )

        # 6. Character and word counts
        if c.char_count != len(c.text):
            warnings.append(
                f"Chunk '{c.chunk_id}' char_count {c.char_count} does not match len(text) {len(c.text)}."
            )
        if c.word_count != len(c.text.split()):
            warnings.append(
                f"Chunk '{c.chunk_id}' word_count {c.word_count} does not match len(text.split()) {len(c.text.split())}."
            )

        # 7. Parent document referential validation (if parent documents supplied)
        if parents_map:
            parent = parents_map.get(c.document_id)
            if not parent:
                errors.append(f"Chunk '{c.chunk_id}' references unknown parent document '{c.document_id}'.")
            else:
                # Security inheritance: tenant
                if c.tenant_id != parent.tenant_id:
                    errors.append(
                        f"Chunk '{c.chunk_id}' tenant_id '{c.tenant_id}' does not match parent '{parent.tenant_id}'."
                    )
                # Security inheritance: classification
                if c.classification != parent.classification:
                    errors.append(
                        f"Chunk '{c.chunk_id}' classification '{c.classification}' does not match parent '{parent.classification}'."
                    )
                # Security inheritance: permissions
                c_p = c.permissions
                p_p = parent.permissions
                if (
                    c_p.allowed_roles != p_p.allowed_roles
                    or c_p.allowed_departments != p_p.allowed_departments
                    or c_p.allowed_teams != p_p.allowed_teams
                    or c_p.allowed_user_ids != p_p.allowed_user_ids
                ):
                    errors.append(
                        f"Chunk '{c.chunk_id}' permissions do not match parent document permissions."
                    )

                # Source metadata inheritance
                if c.source_type != parent.source_type:
                    errors.append(f"Chunk '{c.chunk_id}' source_type mismatch with parent.")
                if c.department != parent.department:
                    errors.append(f"Chunk '{c.chunk_id}' department mismatch with parent.")
                if c.author_id != parent.author_id:
                    errors.append(f"Chunk '{c.chunk_id}' author_id mismatch with parent.")
                if c.authority_level != parent.authority_level:
                    errors.append(f"Chunk '{c.chunk_id}' authority_level mismatch with parent.")
                if c.status != parent.status:
                    errors.append(f"Chunk '{c.chunk_id}' status mismatch with parent.")
                if c.version != parent.version:
                    errors.append(f"Chunk '{c.chunk_id}' version mismatch with parent.")

                # Temporal inheritance
                if c.created_at != parent.created_at:
                    errors.append(f"Chunk '{c.chunk_id}' created_at mismatch with parent.")
                if c.updated_at != parent.updated_at:
                    errors.append(f"Chunk '{c.chunk_id}' updated_at mismatch with parent.")
                if c.valid_from != parent.valid_from:
                    errors.append(f"Chunk '{c.chunk_id}' valid_from mismatch with parent.")
                if c.valid_until != parent.valid_until:
                    errors.append(f"Chunk '{c.chunk_id}' valid_until mismatch with parent.")

                # Provenance inheritance
                if c.source_entity_id != parent.source_entity_id:
                    errors.append(f"Chunk '{c.chunk_id}' source_entity_id mismatch with parent.")
                if c.source_entity_type != parent.source_entity_type:
                    errors.append(f"Chunk '{c.chunk_id}' source_entity_type mismatch with parent.")
                if c.related_entity_ids != parent.related_entity_ids:
                    errors.append(f"Chunk '{c.chunk_id}' related_entity_ids mismatch with parent.")
                if c.parent_id != parent.parent_id:
                    errors.append(f"Chunk '{c.chunk_id}' parent_id mismatch with parent.")
                if c.supersedes_id != parent.supersedes_id:
                    errors.append(f"Chunk '{c.chunk_id}' supersedes_id mismatch with parent.")

    # 8. Check total_chunks consistency per document
    for doc_id, doc_chunks in chunks_by_doc.items():
        expected_total = len(doc_chunks)
        indexes = [c.chunk_index for c in doc_chunks]
        if sorted(indexes) != list(range(expected_total)):
            errors.append(
                f"Document '{doc_id}' chunk indexes {indexes} are not continuous from 0 to {expected_total - 1}."
            )
        for c in doc_chunks:
            if c.total_chunks != expected_total:
                errors.append(
                    f"Chunk '{c.chunk_id}' has total_chunks {c.total_chunks} but document has {expected_total} chunks."
                )

    return errors, warnings
