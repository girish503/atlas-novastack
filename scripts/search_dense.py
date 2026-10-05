#!/usr/bin/env python3
"""Interactive Dense Semantic search CLI for NovaStack (Phase 3B).

Performs dense vector retrieval using BAAI/bge-small-en-v1.5 over the canonical
SearchChunk corpus with pre-scoring filtering by tenancy, classification,
department, source type, and status.

Usage:
    python scripts/search_dense.py --query "checkout timeout outage INC-NS-0001" --top-k 5
    python scripts/search_dense.py --query "rate limiter configuration" --department "Engineering"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure src/ is importable when running as a standalone script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.dense import DenseConfig, DenseEncoder, DenseIndex


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the NovaStack Dense Semantic Retrieval Engine")
    parser.add_argument("--query", "-q", type=str, required=True, help="Search query string")
    parser.add_argument("--top-k", "-k", type=int, default=10, help="Maximum number of candidates (default: 10)")
    parser.add_argument("--tenant-id", type=str, default=None, help="Filter by tenant ID (e.g. TENANT-NOVASTACK)")
    parser.add_argument("--classification", type=str, default=None, help="Filter by classification (public, internal, etc.)")
    parser.add_argument("--department", type=str, default=None, help="Filter by department (Engineering, Security, etc.)")
    parser.add_argument("--source-type", type=str, default=None, help="Filter by source type (postmortem, policy, etc.)")
    parser.add_argument("--status", type=str, default=None, help="Filter by status (published, draft, etc.)")
    parser.add_argument("--model-name", type=str, default="BAAI/bge-small-en-v1.5", help="Embedding model name")

    args = parser.parse_args()

    chunks_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_chunks.json"
    embeddings_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "dense_embeddings.npz"
    metadata_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "dense_index_metadata.json"

    if not chunks_path.exists():
        print(f"[ERROR] Chunks artifact not found at {chunks_path}")
        sys.exit(1)

    if not embeddings_path.exists():
        print(f"[ERROR] Dense embeddings not found at {embeddings_path}")
        print("Please build the dense index first via: python scripts/build_dense_index.py")
        sys.exit(1)

    filters = {}
    if args.tenant_id:
        filters["tenant_id"] = args.tenant_id
    if args.classification:
        filters["classification"] = args.classification
    if args.department:
        filters["department"] = args.department
    if args.source_type:
        filters["source_type"] = args.source_type
    if args.status:
        filters["status"] = args.status

    config = DenseConfig(model_name=args.model_name)
    encoder = DenseEncoder(config=config, device="cpu")
    index = DenseIndex.load(
        chunks_path=chunks_path,
        embeddings_path=embeddings_path,
        metadata_path=metadata_path if metadata_path.exists() else None,
        encoder=encoder,
        config=config,
    )

    results = index.search(query=args.query, top_k=args.top_k, filters=filters if filters else None)

    print("================================================================================")
    print(f"Dense Semantic Search Results for Query: '{args.query}'")
    if filters:
        print(f"Filters Applied: {filters}")
    print(f"Top {len(results)} candidate(s) retrieved from {len(index.chunks)} indexed chunks:")
    print("================================================================================")

    if not results:
        print("  No matching candidates found.")
        return

    for r in results:
        print(f"[{r.rank:>2}] Score: {r.score:>7.4f} | Chunk ID: {r.chunk_id}")
        print(f"     Doc ID:     {r.document_id} (Type: {r.source_type}, Tenant: {r.tenant_id})")
        print(f"     Title:      {r.title}")
        print(f"     Preview:    {r.text_preview}")
        print("--------------------------------------------------------------------------------")


if __name__ == "__main__":
    main()
