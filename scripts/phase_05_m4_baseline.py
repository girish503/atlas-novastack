"""M4 Baseline Capture Script: Characterize current M3 candidate prior to source modification.

Captures:
- positive yield
- negative abstention
- citation precision
- citation completeness
- R@1, R@3, R@5, R@10
- MRR
- NDCG@10
- mean latency
- p95 latency
- context token count
- evidence diversity
- EVAL-0054 status
- EVAL-0058 status

Saves output to artifacts/phase_05_m4_baseline.json.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from novastack.depth_fusion_ablation import compute_ir_metrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("m4_baseline")

_ROOT = Path(__file__).resolve().parent.parent

def main() -> None:
    m3_results_path = _ROOT / "artifacts" / "phase_05_m3_results.json"
    if not m3_results_path.exists():
        raise FileNotFoundError(f"Missing M3 results: {m3_results_path}")

    m3_data = json.loads(m3_results_path.read_text(encoding="utf-8"))
    eval_cases = m3_data["case_evaluations"]

    positive_cases = [c for c in eval_cases if len(c.get("expected_document_ids", [])) > 0]
    negative_cases = [c for c in eval_cases if len(c.get("expected_document_ids", [])) == 0]

    # Positive yield
    pos_answered = sum(1 for c in positive_cases if c["answer_status"] in ("answered", "partially_answered"))
    pos_yield_pct = round((pos_answered / len(positive_cases)) * 100.0, 2)

    # Negative abstention
    neg_abstained = sum(1 for c in negative_cases if c["answer_status"] == "abstained")
    neg_abstention_pct = round((neg_abstained / len(negative_cases)) * 100.0, 2)

    # Citations
    all_citations = []
    for c in eval_cases:
        all_citations.extend(c.get("citations", []))
    valid_citations = [cit for cit in all_citations if str(cit.get("status", "")).upper() == "VALID"]
    citation_precision_pct = round((len(valid_citations) / len(all_citations)) * 100.0, 2) if all_citations else 100.0

    pos_with_valid_cits = sum(
        1 for c in positive_cases
        if c["answer_status"] in ("answered", "partially_answered")
        and any(str(cit.get("status", "")).upper() == "VALID" for cit in c.get("citations", []))
    )
    citation_completeness_pct = round((pos_with_valid_cits / pos_answered) * 100.0, 2) if pos_answered > 0 else 100.0

    # Latencies
    pos_latencies = [c.get("generation_latency_ms", 0.0) for c in positive_cases]
    all_latencies = [c.get("generation_latency_ms", 0.0) for c in eval_cases]
    sorted_pos_lat = sorted(pos_latencies)
    mean_latency_ms = round(sum(pos_latencies) / len(pos_latencies), 2) if pos_latencies else 0.0
    p95_latency_ms = round(sorted_pos_lat[int(len(sorted_pos_lat) * 0.95)], 2) if sorted_pos_lat else 0.0

    # Context Token Count & Evidence Diversity
    # From diagnostics
    token_counts = []
    evidence_diversities = []
    for c in eval_cases:
        diag = c.get("diagnostics", {})
        inp_tok = c.get("input_tokens", 0)
        exp_count = diag.get("exposed_evidence_count", len(diag.get("exposed_evidence_ids", [])))
        if inp_tok > 0:
            token_counts.append(inp_tok)
        if exp_count > 0:
            evidence_diversities.append(exp_count)

    mean_context_tokens = round(sum(token_counts) / len(token_counts), 1) if token_counts else 0.0
    mean_evidence_diversity = round(sum(evidence_diversities) / len(evidence_diversities), 2) if evidence_diversities else 0.0

    # Retrieval Metrics across 101 positive cases using evidence package retrieved docs
    phase4e_path = _ROOT / "data" / "evaluation" / "novastack" / "phase_4e_evidence_assembly.json"
    raw_cases_4e = json.loads(phase4e_path.read_text(encoding="utf-8"))["cases"]
    case_map_4e = {c["evaluation_id"]: c for c in raw_cases_4e}

    recalls_1 = []
    recalls_3 = []
    recalls_5 = []
    recalls_10 = []
    mrrs = []
    ndcgs = []

    for c in positive_cases:
        eid = c["evaluation_id"]
        c_4e = case_map_4e.get(eid, {})
        exp_docs = c.get("expected_document_ids", [])
        
        # Extract retrieved document IDs from evidence package
        pkg = c_4e.get("evidence_package", {})
        selected = pkg.get("selected_evidence", [])
        retrieved_doc_ids = []
        seen_d = set()
        for item in selected:
            d_id = item.get("document_id")
            if d_id and d_id not in seen_d:
                retrieved_doc_ids.append(d_id)
                seen_d.add(d_id)

        ir_res = compute_ir_metrics(
            retrieved_doc_ids=retrieved_doc_ids,
            expected_doc_ids=exp_docs,
            acceptable_doc_ids=c_4e.get("acceptable_document_ids"),
            forbidden_doc_ids=c.get("forbidden_document_ids"),
        )
        recalls_1.append(ir_res.get("recall_at_1", 0.0))
        recalls_3.append(ir_res.get("recall_at_3", 0.0))
        recalls_5.append(ir_res.get("recall_at_5", 0.0))
        recalls_10.append(ir_res.get("recall_at_10", 0.0))
        mrrs.append(ir_res.get("mrr", 0.0))
        ndcgs.append(ir_res.get("ndcg_at_10", 0.0))

    r1 = round(sum(recalls_1) / len(recalls_1), 4) if recalls_1 else 0.0
    r3 = round(sum(recalls_3) / len(recalls_3), 4) if recalls_3 else 0.0
    r5 = round(sum(recalls_5) / len(recalls_5), 4) if recalls_5 else 0.0
    r10 = round(sum(recalls_10) / len(recalls_10), 4) if recalls_10 else 0.0
    mean_mrr = round(sum(mrrs) / len(mrrs), 4) if mrrs else 0.0
    mean_ndcg10 = round(sum(ndcgs) / len(ndcgs), 4) if ndcgs else 0.0

    eval_54 = next((c for c in eval_cases if c["evaluation_id"] == "EVAL-0054"), None)
    eval_58 = next((c for c in eval_cases if c["evaluation_id"] == "EVAL-0058"), None)

    baseline_artifact = {
        "milestone": "ATLAS 0.5-M4 Baseline (Captured from M3 Candidate)",
        "source_candidate": "ATLAS 0.5-M3",
        "benchmark_dataset": "120 canonical cases (101 positive, 19 negative)",
        "metrics": {
            "positive_answer_yield": {
                "answered": pos_answered,
                "total": len(positive_cases),
                "yield_pct": pos_yield_pct,
            },
            "negative_abstention": {
                "abstained": neg_abstained,
                "total": len(negative_cases),
                "abstention_pct": neg_abstention_pct,
            },
            "citation_precision_pct": citation_precision_pct,
            "citation_completeness_pct": citation_completeness_pct,
            "retrieval": {
                "recall_at_1": r1,
                "recall_at_3": r3,
                "recall_at_5": r5,
                "recall_at_10": r10,
                "mrr": mean_mrr,
                "ndcg_at_10": mean_ndcg10,
            },
            "latency_ms": {
                "mean_positive_latency_ms": mean_latency_ms,
                "p95_positive_latency_ms": p95_latency_ms,
            },
            "context_and_evidence": {
                "mean_context_token_count": mean_context_tokens,
                "mean_evidence_diversity_documents": mean_evidence_diversity,
            },
            "critical_regressions": {
                "EVAL-0054": {
                    "query": eval_54.get("query") if eval_54 else "",
                    "answer_status": eval_54.get("answer_status") if eval_54 else "",
                    "abstention_reason": eval_54.get("abstention_reason") if eval_54 else "",
                },
                "EVAL-0058": {
                    "query": eval_58.get("query") if eval_58 else "",
                    "answer_status": eval_58.get("answer_status") if eval_58 else "",
                    "abstention_reason": eval_58.get("abstention_reason") if eval_58 else "",
                },
            },
        },
    }

    out_file = _ROOT / "artifacts" / "phase_05_m4_baseline.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(baseline_artifact, indent=2), encoding="utf-8")
    logger.info(f"✓ Saved M4 baseline artifact to {out_file}")
    logger.info(json.dumps(baseline_artifact["metrics"], indent=2))

if __name__ == "__main__":
    main()
