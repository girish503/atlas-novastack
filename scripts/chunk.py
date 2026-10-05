#!/usr/bin/env python3
"""Execute the semantic-aware document chunking pipeline for NovaStack.

Reads canonical SearchDocument objects from:
    data/processed/novastack/search_documents.json

Chunks documents into retrieval-ready SearchChunk objects, validating referential
integrity and security inheritance, and writes the output to:
    data/processed/novastack/search_chunks.json

Usage:
    python scripts/chunk.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# Ensure src/ is importable when running as a standalone script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.chunking import (
    ChunkingConfig,
    chunk_documents,
    load_search_documents,
    validate_chunks,
)
from novastack.config import DATASET_VERSION, RANDOM_SEED


def _compute_hash(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    raw_path = _PROJECT_ROOT / "data" / "raw" / "novastack" / "source_records.json"
    docs_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_documents.json"
    chunks_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_chunks.json"

    if not docs_path.exists():
        print(f"[ERROR] Processed search documents not found at {docs_path}")
        sys.exit(1)

    # Compute baseline hashes to verify immutability
    raw_hash_before = _compute_hash(raw_path) if raw_path.exists() else None
    docs_hash_before = _compute_hash(docs_path)

    print("NovaStack Document Chunking Pipeline (Phase 2B)")
    print(f"Reading search documents from: {docs_path.relative_to(_PROJECT_ROOT)}")

    # Load canonical documents
    documents = load_search_documents(docs_path)
    parents_map = {d.document_id: d for d in documents}

    # Execute chunking pipeline
    config = ChunkingConfig(
        target_chunk_size=500,
        max_chunk_size=800,
        min_chunk_size=100,
        overlap_size=100,
    )
    search_chunks, report = chunk_documents(documents, config=config)

    # Validate generated chunks
    errors, warnings = validate_chunks(search_chunks, parent_documents=parents_map)
    report.errors = errors
    report.warnings = warnings

    if errors:
        print(f"\n[FATAL] Chunk validation failed with {len(errors)} error(s):")
        for err in errors[:10]:
            print(f"  - {err}")
        sys.exit(1)

    # Print observability report
    print("\n" + report.format_report())

    # Serialize and write chunks
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    serialized_chunks = [c.to_dict() for c in search_chunks]

    output_payload = {
        "version": DATASET_VERSION,
        "seed": RANDOM_SEED,
        "count": len(serialized_chunks),
        "search_chunks": serialized_chunks,
    }

    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print(f"\n[SUCCESS] Wrote {len(search_chunks)} canonical SearchChunk objects to:")
    print(f"  {chunks_path.relative_to(_PROJECT_ROOT)}")

    # Verify immutability
    if raw_hash_before is not None:
        raw_hash_after = _compute_hash(raw_path)
        assert raw_hash_before == raw_hash_after, "Raw source records were mutated!"

    docs_hash_after = _compute_hash(docs_path)
    assert docs_hash_before == docs_hash_after, "Processed search documents were mutated!"

    print("[VERIFIED] Source records and SearchDocuments remained 100% immutable.")


if __name__ == "__main__":
    main()
