"""Canonical Metrics Computation for Retrieval, Generation, and Security."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Sequence, Set


def compute_retrieval_ir_metrics(
    retrieved_doc_ids: Sequence[str],
    expected_doc_ids: Sequence[str],
    acceptable_doc_ids: Sequence[str] = (),
    k_values: Sequence[int] = (1, 3, 5, 10),
) -> Dict[str, float]:
    """Compute mathematical IR metrics: Recall@K, Precision@K, Hit@K, MRR, NDCG@10."""
    metrics: Dict[str, float] = {}
    expected_set = set(expected_doc_ids)
    all_valid_set = expected_set | set(acceptable_doc_ids)

    if not expected_set:
        for k in k_values:
            metrics[f"recall@{k}"] = 1.0 if not retrieved_doc_ids[:k] else 0.0
            metrics[f"precision@{k}"] = 1.0 if not retrieved_doc_ids[:k] else 0.0
            metrics[f"hit@{k}"] = 0.0
        metrics["mrr"] = 0.0
        metrics["ndcg@10"] = 0.0
        return metrics

    # Recall, Precision, Hit @ K
    for k in k_values:
        top_k = retrieved_doc_ids[:k]
        hits_in_k = [doc for doc in top_k if doc in all_valid_set]
        metrics[f"recall@{k}"] = len(hits_in_k) / len(expected_set) if expected_set else 0.0
        metrics[f"precision@{k}"] = len(hits_in_k) / k if k > 0 else 0.0
        metrics[f"hit@{k}"] = 1.0 if len(hits_in_k) > 0 else 0.0

    # MRR (Mean Reciprocal Rank)
    mrr = 0.0
    for rank, doc in enumerate(retrieved_doc_ids, start=1):
        if doc in all_valid_set:
            mrr = 1.0 / rank
            break
    metrics["mrr"] = mrr

    # NDCG@10
    top_10 = retrieved_doc_ids[:10]
    dcg = 0.0
    for rank, doc in enumerate(top_10, start=1):
        if doc in expected_set:
            gain = 2.0  # primary relevant
        elif doc in acceptable_doc_ids:
            gain = 1.0  # secondary relevant
        else:
            gain = 0.0
        dcg += gain / math.log2(rank + 1)

    idcg = 0.0
    ideal_gains = sorted([2.0] * len(expected_set) + [1.0] * len(acceptable_doc_ids), reverse=True)[:10]
    for rank, gain in enumerate(ideal_gains, start=1):
        idcg += gain / math.log2(rank + 1)

    metrics["ndcg@10"] = (dcg / idcg) if idcg > 0 else 0.0

    return metrics


def compute_entity_resolution_metrics(
    resolved_entity_ids: Sequence[str],
    expected_entity_ids: Sequence[str],
    catalog_known_entities: Set[str],
) -> Dict[str, Any]:
    """Compute entity recall, wrong entities, and false positive metrics."""
    resolved_set = set(resolved_entity_ids)
    expected_set = set(expected_entity_ids)

    if not expected_set:
        return {
            "entity_recall": 1.0 if not resolved_set else 0.0,
            "expected_entities_count": 0,
            "resolved_entities_count": len(resolved_set),
            "wrong_entities_count": len([e for e in resolved_set if e not in catalog_known_entities]),
            "false_positive_entities": len([e for e in resolved_set if e in catalog_known_entities and e not in expected_set]),
        }

    intersection = resolved_set & expected_set
    recall = len(intersection) / len(expected_set)

    # Wrong entities: resolved IDs that do not exist in catalog
    wrong_entities = [e for e in resolved_set if e not in catalog_known_entities]
    # False positives: resolved valid entities that were NOT expected
    false_positives = [e for e in resolved_set if e in catalog_known_entities and e not in expected_set]

    return {
        "entity_recall": recall,
        "expected_entities_count": len(expected_set),
        "resolved_entities_count": len(resolved_set),
        "wrong_entities_count": len(wrong_entities),
        "wrong_entities": wrong_entities,
        "false_positive_entities": len(false_positives),
    }


def compute_generation_metrics(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute yield, abstention, and citation validity metrics across generation results."""
    total = len(results)
    if total == 0:
        return {}

    positive_cases = [r for r in results if r.get("is_positive", True)]
    negative_cases = [r for r in results if r.get("is_negative", False)]

    # Positive yield: answered and partially_answered both count as a grounded response
    answered_pos = [
        r
        for r in positive_cases
        if r.get("answer_status") in {"answered", "partially_answered"}
    ]
    pos_yield = len(answered_pos) / len(positive_cases) if positive_cases else 0.0

    # Negative safety
    abstained_neg = [r for r in negative_cases if r.get("answer_status") == "abstained"]
    neg_safety = len(abstained_neg) / len(negative_cases) if negative_cases else 1.0

    # Citations
    total_citations = 0
    valid_citations = 0
    unauthorized_citations = 0
    fabricated_citations = 0
    answered_with_citations = 0

    for r in answered_pos:
        citations = r.get("citations", [])
        if citations:
            answered_with_citations += 1
        for cit in citations:
            total_citations += 1
            status = cit.get("status", "INVALID")
            if status == "VALID":
                valid_citations += 1
            elif status == "FABRICATED":
                fabricated_citations += 1
            if cit.get("is_unauthorized", False):
                unauthorized_citations += 1

    citation_precision = valid_citations / total_citations if total_citations > 0 else 1.0
    citation_completeness = answered_with_citations / len(answered_pos) if answered_pos else 0.0

    return {
        "total_cases": total,
        "positive_cases": len(positive_cases),
        "negative_cases": len(negative_cases),
        "positive_answered_count": len(answered_pos),
        "positive_answer_yield": pos_yield,
        "negative_abstained_count": len(abstained_neg),
        "negative_safety_rate": neg_safety,
        "total_citations": total_citations,
        "valid_citations": valid_citations,
        "citation_precision": citation_precision,
        "citation_completeness": citation_completeness,
        "unauthorized_citations": unauthorized_citations,
        "fabricated_citations": fabricated_citations,
    }


def compute_percentiles(latencies: Sequence[float]) -> Dict[str, float]:
    """Compute p50, p95, p99 latencies."""
    if not latencies:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "max": 0.0}

    s = sorted(latencies)
    n = len(s)

    def _perc(p: float) -> float:
        idx = int(math.ceil(p * n)) - 1
        return s[max(0, min(idx, n - 1))]

    return {
        "mean": sum(s) / n,
        "p50": _perc(0.50),
        "p95": _perc(0.95),
        "p99": _perc(0.99),
        "max": s[-1],
    }
