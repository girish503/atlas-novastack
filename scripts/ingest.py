#!/usr/bin/env python3
"""Execute the canonical ingestion and normalization pipeline for NovaStack.

Reads raw observational enterprise records from:
    data/raw/novastack/source_records.json

Validates and deterministically normalizes records into canonical SearchDocument objects,
writing the search-ready output to:
    data/processed/novastack/search_documents.json

Usage:
    python scripts/ingest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure src/ is importable when running as a standalone script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.config import DATASET_VERSION, RANDOM_SEED
from novastack.ingestion import ingest_records


def main() -> None:
    raw_path = _PROJECT_ROOT / "data" / "raw" / "novastack" / "source_records.json"
    processed_dir = _PROJECT_ROOT / "data" / "processed" / "novastack"
    processed_path = processed_dir / "search_documents.json"

    if not raw_path.exists():
        print(f"[ERROR] Raw source records not found at {raw_path}")
        sys.exit(1)

    print("NovaStack Canonical Ingestion & Normalization Pipeline (Phase 2A)")
    print(f"Reading raw records from: {raw_path.relative_to(_PROJECT_ROOT)}")

    # Execute ingestion pipeline
    try:
        search_documents, report = ingest_records(raw_path, strict=True)
    except ValueError as err:
        print(f"\n[FATAL] Ingestion validation failed:\n{err}")
        sys.exit(1)

    # Print observability report
    print("\n" + report.format_report())

    # Write normalized search documents
    processed_dir.mkdir(parents=True, exist_ok=True)
    serialized_docs = [doc.to_dict() for doc in search_documents]

    output_payload = {
        "version": DATASET_VERSION,
        "seed": RANDOM_SEED,
        "count": len(serialized_docs),
        "search_documents": serialized_docs,
    }

    with open(processed_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print(f"\n[SUCCESS] Wrote {len(search_documents)} canonical SearchDocument objects to:")
    print(f"  {processed_path.relative_to(_PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
