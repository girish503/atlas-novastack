"""Canonical Performance & Latency Benchmark.

Measures in-process query understanding, BM25, dense retrieval, and reranking.
Generation latency is taken from the certified M8 artifact when present and is
explicitly labeled OFFLINE_INFERENCE_ARTIFACT — not HTTP production-path latency.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

WORKSPACE = Path(__file__).resolve().parent.parent.parent


def run_canonical_performance_evaluation(
    data_dir: Path | str = "data",
    artifacts_dir: Path | str = "artifacts",
    num_queries: int = 20,
    write_artifact: bool = True,
) -> Dict[str, Any]:
    from scripts.eval.datasets import load_canonical_dataset
    from scripts.eval.metrics import compute_percentiles

    cases = load_canonical_dataset()[:num_queries]

    qu_latencies: List[float] = []
    retrieval_latencies: List[float] = []
    rerank_latencies: List[float] = []

    from novastack.bm25 import BM25Index
    from novastack.dense import DenseIndex
    from novastack.entity_catalog import EntityCatalog
    from novastack.metadata_diagnostics import build_metadata_snapshot_index
    from novastack.metadata_reranker import MetadataReranker, MetadataRerankerConfig
    from novastack.models import SearchChunk
    from novastack.query_understanding import EntityCatalog as QUEntityCatalog, QueryUnderstandingExtractor
    from scripts.ret_eval_08_h5_1_experiment import H5_1EntityResolver, H5_1QueryUnderstandingOverlay

    raw_dir = WORKSPACE / "data" / "raw" / "novastack"
    processed_dir = WORKSPACE / "data" / "processed" / "novastack"

    with open(processed_dir / "search_chunks.json", "r", encoding="utf-8") as handle:
        chunks = [SearchChunk.from_dict(item) for item in json.load(handle)["search_chunks"]]
    with open(processed_dir / "search_documents.json", "r", encoding="utf-8") as handle:
        documents = json.load(handle)["search_documents"]

    bm25_idx = BM25Index.build_index(chunks)
    dense_idx = DenseIndex.load(
        chunks_path=processed_dir / "search_chunks.json",
        embeddings_path=processed_dir / "dense_embeddings.npz",
        metadata_path=processed_dir / "dense_index_metadata.json",
    )
    catalog = EntityCatalog(raw_dir, processed_dir / "search_chunks.json")
    qu_catalog = QUEntityCatalog(raw_dir)
    base_qu = QueryUnderstandingExtractor(qu_catalog)
    h5_1_res = H5_1EntityResolver(catalog)
    h5_1_overlay = H5_1QueryUnderstandingOverlay(base_qu, h5_1_res, catalog)
    reranker = MetadataReranker(MetadataRerankerConfig())
    meta_idx = build_metadata_snapshot_index(documents, [])

    t_cold = time.perf_counter()
    _ = dense_idx.search("warmup", top_k=2)
    cold_dense_ms = (time.perf_counter() - t_cold) * 1000.0
    _ = bm25_idx.search("warmup", top_k=2)

    for case in cases:
        t_qu_start = time.perf_counter()
        tenant = getattr(case, "tenant_id", None) or "TENANT-NOVASTACK"
        qu, _ = h5_1_overlay.extract(case.query, tenant)
        qu_latencies.append((time.perf_counter() - t_qu_start) * 1000.0)

        t_ret_start = time.perf_counter()
        r_bm25 = bm25_idx.search(case.query, top_k=10)
        r_dense = dense_idx.search(case.query, top_k=10)
        retrieval_latencies.append((time.perf_counter() - t_ret_start) * 1000.0)

        t_rr_start = time.perf_counter()
        _ = reranker.rerank(r_bm25 + r_dense, qu=qu, metadata_index=meta_idx)
        rerank_latencies.append((time.perf_counter() - t_rr_start) * 1000.0)

    m8_path = WORKSPACE / "artifacts" / "phase_05_m8_benchmark_results.json"
    gen_latencies: List[float] = []
    if m8_path.exists():
        with open(m8_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        rows = payload if isinstance(payload, list) else payload.get("case_results", [])
        for row in rows:
            value = row.get("generation_latency_ms")
            if value is not None:
                gen_latencies.append(float(value))

    retrieval_p95 = compute_percentiles(retrieval_latencies)["p95"]
    qu_p95 = compute_percentiles(qu_latencies)["p95"]
    summary = {
        "benchmark_name": "ATLAS Canonical Latency & Performance Benchmark",
        "evaluation_scope": {
            "query_understanding": "IN_PROCESS_WARM",
            "retrieval": "IN_PROCESS_WARM",
            "reranking": "IN_PROCESS_WARM",
            "generation": "OFFLINE_INFERENCE_ARTIFACT",
            "http_production_path": "NOT_MEASURED",
        },
        "sample_size": len(cases),
        "query_understanding_ms": compute_percentiles(qu_latencies),
        "retrieval_ms": compute_percentiles(retrieval_latencies),
        "reranking_ms": compute_percentiles(rerank_latencies),
        "generation_ms": compute_percentiles(gen_latencies),
        "cold_start_characteristics": {
            "dense_search_after_index_load_ms": round(cold_dense_ms, 2),
            "historical_dense_cold_page_in_s": 35.27,
            "historical_note": "Prior operations notes recorded ~35s dense page-in and 12-16s warm GGUF inference.",
        },
        "gate_evaluation": {
            "pass": retrieval_p95 <= 250.0 and qu_p95 <= 250.0,
            "warm_retrieval_p95_le_250ms": retrieval_p95 <= 250.0,
            "warm_query_understanding_p95_le_250ms": qu_p95 <= 250.0,
            "http_e2e_latency": "NOT_MEASURED",
        },
    }

    if write_artifact:
        out_dir = WORKSPACE / Path(artifacts_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "canonical_performance_report.json", "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
    return summary
