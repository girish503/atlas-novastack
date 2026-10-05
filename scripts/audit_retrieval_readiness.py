#!/usr/bin/env python3
"""Execute the retrieval index readiness and corpus audit for NovaStack (Phase 2C).

Inspects the canonical SearchChunk artifact, verifies referential mapping back to
SearchDocuments, validates contract compliance across all 6 functional tiers,
evaluates duplicate text groups, and tests evaluation ground truth resolvability.

Writes the audit report to:
    data/processed/novastack/retrieval_readiness.json

Usage:
    python scripts/audit_retrieval_readiness.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# Ensure src/ is importable when running as a standalone script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.config import DATASET_VERSION, RANDOM_SEED
from novastack.retrieval_readiness import audit_retrieval_readiness


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
    eval_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
    report_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "retrieval_readiness.json"

    # Pre-audit validation of required input files
    for p, name in [
        (raw_path, "Raw source records"),
        (docs_path, "Search documents"),
        (chunks_path, "Search chunks"),
        (eval_path, "Evaluation cases"),
    ]:
        if not p.exists():
            print(f"[ERROR] {name} not found at {p}")
            sys.exit(1)

    # Compute baseline checksums to verify strict immutability
    raw_hash_before = _compute_hash(raw_path)
    docs_hash_before = _compute_hash(docs_path)
    chunks_hash_before = _compute_hash(chunks_path)
    eval_hash_before = _compute_hash(eval_path)

    print("NovaStack Retrieval Index Readiness & Baseline Audit (Phase 2C)")
    print(f"Auditing chunks from: {chunks_path.relative_to(_PROJECT_ROOT)}")

    # Execute comprehensive readiness audit
    report = audit_retrieval_readiness(
        chunks=chunks_path,
        documents=docs_path,
        evaluation_cases=eval_path,
    )

    # Print formatted console report
    print("\n" + report.format_report())

    if report.errors:
        print(f"\n[FATAL] Retrieval readiness audit encountered {len(report.errors)} error(s):")
        for err in report.errors[:10]:
            print(f"  - {err}")
        sys.exit(1)

    # Export machine-readable report
    report_dict = report.to_dict()
    output_payload = {
        "version": DATASET_VERSION,
        "seed": RANDOM_SEED,
        "retrieval_readiness_report": report_dict,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print(f"\n[SUCCESS] Wrote machine-readable audit report to:")
    print(f"  {report_path.relative_to(_PROJECT_ROOT)}")

    # Immutability assertions
    assert raw_hash_before == _compute_hash(raw_path), "Raw source records were mutated!"
    assert docs_hash_before == _compute_hash(docs_path), "Search documents were mutated!"
    assert chunks_hash_before == _compute_hash(chunks_path), "Search chunks were mutated!"
    assert eval_hash_before == _compute_hash(eval_path), "Evaluation cases were mutated!"

    print("[VERIFIED] Raw records, documents, chunks, and evaluation cases remained 100% immutable.")


if __name__ == "__main__":
    main()
