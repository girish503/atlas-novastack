"""Experimental Boundary Sentence Stitching Layer — Phase 4K-A.

Restores syntactically complete sentences across chunk boundaries when retrieval
selects a chunk that begins with a severed sentence or clause.

Core Rules & Constraints:
1. Same document_id is mandatory.
2. Same tenant_id is mandatory.
3. Same authorized evidence scope is mandatory.
4. Adjacent chunk must be the immediate predecessor (chunk_index - 1).
5. Do not pull arbitrary earlier chunks or cross-document chunks.
6. Do not pull superseded, deprecated, draft, or adversarial chunks unless
   permitted by the active EvidencePackage.
7. Do not change evidence ranking or increase Top-K.
8. Do not modify the citation resolver.
9. If the chunk already begins at a sentence boundary, do nothing.
10. If sentence completeness cannot be established safely, leave chunk unchanged.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from novastack.evidence import EvidenceItem, EvidencePackage
from novastack.models import RecordPermissions, SearchChunk

WORKSPACE = Path(__file__).resolve().parent.parent.parent


class BoundarySentenceStitcher:
    """Deterministic Boundary Sentence Stitcher for post-retrieval context preparation."""

    def __init__(
        self,
        chunks_index: dict[str, SearchChunk | dict[str, Any]] | None = None,
        chunks_path: Path | str | None = None,
    ):
        self._chunks_index = chunks_index
        self._chunks_path = Path(chunks_path) if chunks_path else WORKSPACE / "data" / "processed" / "novastack" / "search_chunks.json"

    def _get_chunks_index(self) -> dict[str, SearchChunk | dict[str, Any]]:
        """Lazy-load search chunks index if not provided at initialization."""
        if self._chunks_index is None:
            if not self._chunks_path.exists():
                return {}
            raw = json.loads(self._chunks_path.read_text(encoding="utf-8"))
            chunks_list = raw.get("search_chunks", [])
            self._chunks_index = {c["chunk_id"]: c for c in chunks_list}
        return self._chunks_index

    def stitch_boundary_sentences(
        self,
        evidence_items: list[EvidenceItem],
        package: EvidencePackage | None = None,
        enabled: bool = False,
    ) -> tuple[list[EvidenceItem], list[dict[str, Any]]]:
        """Stitch missing sentence prefixes to boundary-severed evidence items.

        Args:
            evidence_items: Exposed EvidenceItems selected for generation context.
            package: Active EvidencePackage providing security and exclusion context.
            enabled: Experiment flag. When False, returns items unchanged.

        Returns:
            tuple of (stitched_evidence_items, list_of_stitching_diagnostic_records)
        """
        if not enabled or not evidence_items:
            return evidence_items, []

        chunks_idx = self._get_chunks_index()
        if not chunks_idx:
            return evidence_items, []

        # Collect excluded/adversarial identifiers from package to prevent safety bypass
        excluded_chunk_ids: set[str] = set()
        excluded_doc_ids: set[str] = set()
        if package and package.excluded_evidence:
            for ex in package.excluded_evidence:
                if isinstance(ex, dict):
                    if ex.get("chunk_id"):
                        excluded_chunk_ids.add(str(ex["chunk_id"]).upper())
                    if ex.get("document_id"):
                        excluded_doc_ids.add(str(ex["document_id"]).upper())
                    if ex.get("doc_id"):
                        excluded_doc_ids.add(str(ex["doc_id"]).upper())
                elif hasattr(ex, "chunk_id") and ex.chunk_id:
                    excluded_chunk_ids.add(str(ex.chunk_id).upper())
                elif hasattr(ex, "document_id") and ex.document_id:
                    excluded_doc_ids.add(str(ex.document_id).upper())

        stitched_items: list[EvidenceItem] = []
        stitching_log: list[dict[str, Any]] = []

        for item in evidence_items:
            cid = item.chunk_id
            if not cid or "::CHUNK-" not in cid:
                stitched_items.append(item)
                continue

            doc_id, idx_str = cid.split("::CHUNK-")
            try:
                c_idx = int(idx_str)
            except ValueError:
                stitched_items.append(item)
                continue

            # First chunk (chunk_index=0) has no predecessor
            if c_idx <= 1:
                stitched_items.append(item)
                continue

            raw_text = item.text
            stripped = raw_text.lstrip()
            if not stripped:
                stitched_items.append(item)
                continue

            first_char = stripped[0]
            # Check if chunk begins with an incomplete sentence or severed clause
            is_incomplete_start = first_char.islower() or first_char in (")", "]", "}", ",", ";", ":")
            if not is_incomplete_start:
                # Rule 9: If chunk already begins at a sentence boundary, do nothing
                stitched_items.append(item)
                continue

            # Rule 4: Immediate predecessor only (chunk_index - 1)
            pred_cid = f"{doc_id}::CHUNK-{c_idx - 1:04d}"
            pred_chunk = chunks_idx.get(pred_cid)
            if not pred_chunk:
                # Rule 10: Predecessor not found, leave unchanged
                stitched_items.append(item)
                continue

            # Extract fields across SearchChunk dataclass or dictionary
            p_doc_id = pred_chunk.document_id if hasattr(pred_chunk, "document_id") else pred_chunk.get("document_id")
            p_tenant_id = pred_chunk.tenant_id if hasattr(pred_chunk, "tenant_id") else pred_chunk.get("tenant_id")
            p_class = pred_chunk.classification if hasattr(pred_chunk, "classification") else pred_chunk.get("classification")
            p_status = pred_chunk.status if hasattr(pred_chunk, "status") else pred_chunk.get("status")
            p_text = pred_chunk.text if hasattr(pred_chunk, "text") else pred_chunk.get("text", "")

            # Rule 1: Same document_id is mandatory
            if p_doc_id != item.document_id:
                stitched_items.append(item)
                continue

            # Rule 2: Same tenant_id is mandatory
            if p_tenant_id != item.tenant_id:
                stitched_items.append(item)
                continue

            # Rule 3: Same authorized evidence scope (classification level must match)
            if p_class != item.classification:
                stitched_items.append(item)
                continue

            # Rule 7: Do not pull superseded/deprecated/draft unless item permits it
            if p_status in ("deprecated", "superseded", "draft") and item.status not in ("deprecated", "superseded", "draft"):
                stitched_items.append(item)
                continue

            # Rule 7 (Security): Do not pull adversarial or excluded predecessor
            if pred_cid.upper() in excluded_chunk_ids or (p_doc_id and p_doc_id.upper() in excluded_doc_ids):
                stitched_items.append(item)
                continue

            # Check if predecessor has explicit adversarial flag
            if hasattr(pred_chunk, "is_adversarial") and pred_chunk.is_adversarial:
                stitched_items.append(item)
                continue
            if isinstance(pred_chunk, dict) and pred_chunk.get("is_adversarial"):
                stitched_items.append(item)
                continue

            # Locate overlap anchor in predecessor chunk
            first_block = stripped.split("\n\n")[0].strip()
            pos = p_text.rfind(first_block)
            if pos == -1:
                first_line = stripped.split("\n")[0].strip()
                pos = p_text.rfind(first_line)

            if pos == -1:
                # Overlap anchor could not be safely located; leave chunk unchanged
                stitched_items.append(item)
                continue

            preceding = p_text[:pos]
            # Sentence boundaries: preceded by sentence terminator + space, or newlines
            boundaries = list(re.finditer(r'(?:[\.\?\!]\s+|\n+)', preceding))
            if boundaries:
                start_idx = boundaries[-1].end()
                missing_prefix = preceding[start_idx:]
            else:
                missing_prefix = preceding

            missing_prefix = missing_prefix.lstrip()
            if not missing_prefix:
                stitched_items.append(item)
                continue

            # Construct stitched EvidenceItem preserving all metadata
            new_item = copy.copy(item)
            new_item.text = missing_prefix + raw_text
            stitched_items.append(new_item)

            stitching_log.append({
                "evidence_id": item.evidence_id,
                "chunk_id": item.chunk_id,
                "document_id": item.document_id,
                "predecessor_chunk_id": pred_cid,
                "predecessor_tenant_id": p_tenant_id,
                "chars_added": len(missing_prefix),
                "stitched_prefix": missing_prefix,
            })

        return stitched_items, stitching_log
