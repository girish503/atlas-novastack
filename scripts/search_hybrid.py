#!/usr/bin/env python3
"""Interactive Hybrid search CLI for NovaStack (Phase 4A).

Executes hybrid retrieval combining BM25 lexical search and Dense semantic search
with Reciprocal Rank Fusion (RRF) over the canonical SearchChunk corpus.

Usage:
    python scripts/search_hybrid.py --query "checkout timeout outage INC-NS-0001" --top-k 5
    python scripts/search_hybrid.py --query "rate limiter configuration" --department "Engineering"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure src/ is importable when running as a standalone script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.bm25 import BM25Config, BM25Index
from novastack.dense import DenseConfig, DenseEncoder, DenseIndex
from novastack.hybrid import HybridConfig, HybridRetriever


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the NovaStack Hybrid (BM25 + Dense + RRF) Retrieval Engine")
    parser.add_argument("--query", "-q", type=str, required=True, help="Search query string")
    parser.add_argument("--top-k", "-k", type=int, default=10, help="Maximum number of candidates (default: 10)")
    parser.add_argument("--rrf-k", type=int, default=60, help="RRF smoothing constant k (default: 60)")
    parser.add_argument("--retriever-top-k", type=int, default=50, help="Per-channel candidate retrieval depth (default: 50)")
    parser.add_argument("--tenant-id", type=str, default=None, help="Filter by tenant ID (e.g. TENANT-NOVASTACK)")
    parser.add_argument("--classification", type=str, default=None, help="Filter by classification (public, internal, etc.)")
    parser.add_argument("--department", type=str, default=None, help="Filter by department (Engineering, Security, etc.)")
    parser.add_argument("--source-type", type=str, default=None, help="Filter by source type (postmortem, policy, etc.)")
    parser.add_argument("--status", type=str, default=None, help="Filter by status (published, draft, etc.)")

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

    # Initialize BM25 and Dense indices
    bm25_config = BM25Config()
    bm25_index = BM25Index.build_index(chunks_path, config=bm25_config)

    dense_config = DenseConfig()
    encoder = DenseEncoder(config=dense_config, device="cpu")
    dense_index = DenseIndex.load(
        chunks_path=chunks_path,
        embeddings_path=embeddings_path,
        metadata_path=metadata_path if metadata_path.exists() else None,
        encoder=encoder,
        config=dense_config,
    )

    hybrid_config = HybridConfig(rrf_k=args.rrf_k, retriever_top_k=args.retriever_top_k)
    retriever = HybridRetriever(bm25_index=bm25_index, dense_index=dense_index, config=hybrid_config)

    results = retriever.search(query=args.query, top_k=args.top_k, filters=filters if filters else None)

    print("================================================================================")
    print(f"Hybrid (BM25 + Dense + RRF k={args.rrf_k}) Search Results for Query: '{args.query}'")
    if filters:
        print(f"Filters Applied: {filters}")
    print(f"Top {len(results)} candidate(s) retrieved from {len(dense_index.chunks)} indexed chunks:")
    print("================================================================================")

    if not results:
        print("  No matching candidates found.")
        return

    for r in results:
        bm25_info = f"Rank {r.bm25_rank} (score {r.bm25_score:.2f})" if r.bm25_rank else "Not in top-k"
        dense_info = f"Rank {r.dense_rank} (score {r.dense_score:.4f})" if r.dense_rank else "Not in top-k"
        print(f"[{r.rank:>2}] RRF Score: {r.score:>8.6f} | Chunk ID: {r.chunk_id}")
        print(f"     Channels:   BM25: {bm25_info} | Dense: {dense_info}")
        print(f"     Doc ID:     {r.document_id} (Type: {r.source_type}, Tenant: {r.tenant_id})")
        print(f"     Title:      {r.title}")
        print(f"     Preview:    {r.text_preview}")
        print("--------------------------------------------------------------------------------")


if __name__ == "__main__":
    main()
