#!/usr/bin/env python3
"""Build and persist the dense semantic vector index — Phase 3B.

Encodes the canonical SearchChunk corpus (data/processed/novastack/search_chunks.json)
using BAAI/bge-small-en-v1.5 and serializes the resulting float32 vectors to:
    data/processed/novastack/dense_embeddings.npz
    data/processed/novastack/dense_index_metadata.json

Usage:
    python scripts/build_dense_index.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

# Ensure src/ is importable when running as a standalone script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.dense import DenseConfig, DenseEncoder, DenseIndex
from novastack.models import SearchChunk


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

    out_embeddings = _PROJECT_ROOT / "data" / "processed" / "novastack" / "dense_embeddings.npz"
    out_metadata = _PROJECT_ROOT / "data" / "processed" / "novastack" / "dense_index_metadata.json"

    # Pre-computation immutability baseline
    for p, name in [
        (raw_path, "Raw source records"),
        (docs_path, "Search documents"),
        (chunks_path, "Search chunks"),
        (eval_path, "Evaluation cases"),
    ]:
        if not p.exists():
            raise FileNotFoundError(f"{name} artifact not found at: {p}")

    raw_hash_before = _compute_hash(raw_path)
    docs_hash_before = _compute_hash(docs_path)
    chunks_hash_before = _compute_hash(chunks_path)
    eval_hash_before = _compute_hash(eval_path)

    print("============================================================")
    print("ATLAS Dense Index Builder — Phase 3B")
    print("============================================================")
    print(f"Loading search chunks from: {chunks_path.relative_to(_PROJECT_ROOT)}")

    with open(chunks_path, "r", encoding="utf-8") as f:
        raw_chunks = json.load(f)["search_chunks"]

    chunks = [SearchChunk.from_dict(c) for c in raw_chunks]
    print(f"Loaded {len(chunks)} chunks.")

    config = DenseConfig(
        model_name="BAAI/bge-small-en-v1.5",
        query_instruction="Represent this sentence for searching relevant passages: ",
        dimension=384,
        normalize_embeddings=True,
        batch_size=32,
    )

    print(f"Initializing encoder: {config.model_name} (dimension: {config.dimension})")
    encoder = DenseEncoder(config=config, device="cpu")

    print(f"Encoding {len(chunks)} passages...")
    start_time = time.perf_counter()
    index = DenseIndex.build_from_chunks(chunks=chunks, encoder=encoder, config=config)
    elapsed = time.perf_counter() - start_time
    print(f"Encoding completed in {elapsed:.2f}s ({len(chunks) / elapsed:.1f} chunks/sec).")

    print(f"Saving vector index artifact: {out_embeddings.relative_to(_PROJECT_ROOT)}")
    print(f"Saving metadata artifact:     {out_metadata.relative_to(_PROJECT_ROOT)}")
    index.save(embeddings_path=out_embeddings, metadata_path=out_metadata)

    # Verify reloading
    print("Verifying index reload integrity...")
    reloaded_index = DenseIndex.load(
        chunks_path=chunks_path,
        embeddings_path=out_embeddings,
        metadata_path=out_metadata,
        encoder=encoder,
    )
    stats = reloaded_index.get_index_statistics()
    print(f"  Total Vectors:        {stats['total_chunks']}")
    print(f"  Vector Shape:         {stats['vector_shape']}")
    print(f"  Embedding Dimension:  {stats['dimension']}")
    print(f"  Normalized:           {stats['normalize_embeddings']}")

    # Post-computation immutability assertion
    assert raw_hash_before == _compute_hash(raw_path), "Raw source records were mutated!"
    assert docs_hash_before == _compute_hash(docs_path), "Search documents were mutated!"
    assert chunks_hash_before == _compute_hash(chunks_path), "Search chunks were mutated!"
    assert eval_hash_before == _compute_hash(eval_path), "Evaluation cases were mutated!"

    print("============================================================")
    print("[SUCCESS] Dense index built and validated successfully.")
    print("All input artifacts remained 100% immutable.")
    print("============================================================")


if __name__ == "__main__":
    main()
