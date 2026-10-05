#!/usr/bin/env python3
"""Run Query Intent & Failure Taxonomy Diagnostics (Phase 4C-0).

Extracts standardized QueryProfiles for all 120 evaluation cases,
computes cross-tabulation across BM25, Dense, Hybrid, Phase 4B-0,
and Phase 4B-1, and produces:
- data/evaluation/novastack/phase_4c0_query_profiles.json
- docs/PHASE_4C0_REPORT.md

Usage:
    python scripts/run_query_intent_diagnostic.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.query_profiles import (
    CONTROLLED_INTENT_LABELS,
    IntentCrossTabulation,
    QueryProfile,
    build_all_query_profiles,
    generate_failure_cross_tabulation,
)


def _compute_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def generate_markdown_report(
    profiles: list[QueryProfile],
    cross_tab: list[IntentCrossTabulation],
    key_findings: dict[str, Any],
) -> str:
    """Generate exhaustive markdown diagnostic report answering all 13 questions."""
    md = []
    md.append("# Phase 4C-0: Query Intent & Failure Taxonomy Diagnostic Report\n\n")
    md.append("## Executive Summary\n\n")
    md.append(
        "Following the empirical rejection of Hypothesis 1 (H1) in Phase 4B-1 "
        "(where cross-encoder reranking degraded Recall@10 from 0.4719 to 0.4249 and MRR from 0.3296 to 0.2363), "
        "Phase 4C-0 executes a **pure diagnostic milestone** to determine what kind of query understanding each "
        "existing evaluation case requires.\n\n"
    )
    md.append(
        "> **Fundamental Diagnostic Axiom**:\n"
        "> `Query Intent Understanding != Candidate Generation != Relevance Ranking != Source Authority != Authorization Filtering`\n\n"
    )
    md.append("---\n\n")

    # 1. Cross-Tabulation Matrix Table
    md.append("## 1. Intent Failure Cross-Tabulation Matrix\n\n")
    md.append(
        "Performance of all 15 controlled intent dimensions across BM25, Dense, Hybrid RRF, and Cross-Encoder Reranking:\n\n"
    )
    md.append(
        "| Intent Label | Cases (Pos/Tot) | BM25 R@10 | Dense R@10 | RRF R@10 | Rerank R@10 | Union Cov@50 | Med Rank | Cand Gen Fails | Headroom Cases | Rerank Regs |\n"
    )
    md.append(
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    )

    tab_by_intent = {r.intent_label: r for r in cross_tab}
    for label in CONTROLLED_INTENT_LABELS:
        r = tab_by_intent.get(label)
        if not r:
            continue
        med_str = f"{r.median_best_candidate_rank:.1f}" if r.median_best_candidate_rank is not None else "N/A"
        md.append(
            f"| `{r.intent_label}` | {r.positive_cases_count}/{r.total_cases_count} | "
            f"{r.recall_at_10_bm25:.4f} | {r.recall_at_10_dense:.4f} | {r.recall_at_10_rrf:.4f} | "
            f"**{r.recall_at_10_reranker:.4f}** | {r.union_coverage_at_50 * 100:.1f}% | "
            f"{med_str} | {r.candidate_generation_failures} | {r.ranking_headroom_cases} | {r.reranker_regressions} |\n"
        )

    md.append("\n---\n\n")

    # 2. Answers to the 13 Mandatory Questions
    md.append("## 2. Answers to the Thirteen Mandatory Diagnostic Questions\n\n")

    # Q1
    intent_counts = Counter()
    for p in profiles:
        for lbl in p.intent_labels:
            intent_counts[lbl] += 1
    top_intents = intent_counts.most_common(5)
    md.append("### 1. What query intents dominate ATLAS?\n")
    md.append(
        f"Across all 120 evaluation cases, the most frequent query intents are:\n"
    )
    for lbl, cnt in top_intents:
        md.append(f"- **`{lbl}`**: {cnt} cases ({cnt / len(profiles) * 100:.1f}% of corpus)\n")
    md.append(
        "ATLAS queries are heavily **semantic** (unstructured symptom and conceptual inquiries), "
        "**entity_attribute**-focused (seeking properties of incidents, services, and deployments), "
        "and frequently **authority_sensitive** (requiring discrimination between official records and chatter/poisoned content).\n\n"
    )

    # Q2
    pos_rows = [r for r in cross_tab if r.positive_cases_count > 0]
    worst_rrf = sorted(pos_rows, key=lambda x: x.recall_at_10_rrf)[:4]
    worst_rerank = sorted(pos_rows, key=lambda x: x.recall_at_10_reranker)[:4]
    md.append("### 2. Which intents have the worst Recall@10?\n")
    md.append("The lowest-performing intent categories under Hybrid RRF are:\n")
    for r in worst_rrf:
        md.append(f"- **`{r.intent_label}`**: RRF Recall@10 = **{r.recall_at_10_rrf:.4f}** (BM25: {r.recall_at_10_bm25:.4f}, Dense: {r.recall_at_10_dense:.4f})\n")
    md.append("\nUnder the Cross-Encoder Reranker, performance collapsed further in:\n")
    for r in worst_rerank:
        md.append(f"- **`{r.intent_label}`**: Reranker Recall@10 = **{r.recall_at_10_reranker:.4f}** (vs RRF {r.recall_at_10_rrf:.4f})\n")
    md.append("\n")

    # Q3
    good_cov_poor_rank = [
        r for r in pos_rows if r.union_coverage_at_50 >= 0.70 and r.recall_at_10_rrf < 0.50
    ]
    md.append("### 3. Which intents have good candidate coverage but poor ranking?\n")
    if good_cov_poor_rank:
        for r in good_cov_poor_rank:
            md.append(
                f"- **`{r.intent_label}`**: 50-Candidate Union Coverage = **{r.union_coverage_at_50 * 100:.1f}%**, "
                f"but Recall@10 is only **{r.recall_at_10_rrf:.4f}** (with {r.ranking_headroom_cases} cases in ranks 11–50).\n"
            )
        md.append(
            "These categories represent genuine ranking opportunities where target documents are retrieved into the top-50 pool "
            "but displaced from the top-10 by competing distractors.\n\n"
        )
    else:
        md.append("Most low-recall categories also suffered from candidate starvation.\n\n")

    # Q4
    high_gen_fails = sorted(pos_rows, key=lambda x: -x.candidate_generation_failures)[:4]
    md.append("### 4. Which intents have candidate-generation failures?\n")
    md.append("Intents where the target document is completely absent from the 50-candidate union pool:\n")
    for r in high_gen_fails:
        md.append(
            f"- **`{r.intent_label}`**: **{r.candidate_generation_failures} candidate-generation failures** "
            f"({r.candidate_generation_failures / r.positive_cases_count * 100:.1f}% of cases missing from top-50).\n"
        )
    md.append(
        "For these cases, reranking or cross-attention is futile because the target never enters the candidate pool.\n\n"
    )

    # Q5
    temporal_row = tab_by_intent.get("temporal")
    ver_row = tab_by_intent.get("version_lifecycle")
    md.append("### 5. Which intents require temporal/lifecycle reasoning?\n")
    md.append(
        f"- **`temporal`** ({temporal_row.total_cases_count if temporal_row else 0} cases): Queries asking 'what happened before/after', "
        f"event timelines, or duration. Current RRF Recall@10 is **{temporal_row.recall_at_10_rrf:.4f}**.\n"
        f"- **`version_lifecycle`** ({ver_row.total_cases_count if ver_row else 0} cases): Queries requiring tracking version lineages (v1.0 -> v2.0), "
        f"deprecated configurations, or draft vs published status. Current RRF Recall@10 is **{ver_row.recall_at_10_rrf:.4f}**.\n\n"
    )

    # Q6
    rel_row = tab_by_intent.get("relationship")
    md.append("### 6. Which require entity relationships?\n")
    md.append(
        f"- **`relationship`** ({rel_row.total_cases_count if rel_row else 0} cases): Inquiries connecting teams to services (`ownership`), "
        f"pull requests to deployments, or deployments to incidents. "
        f"Because entity metadata is often split across distinct catalog and telemetry records, single-query text match struggles, "
        f"yielding RRF Recall@10 of **{rel_row.recall_at_10_rrf:.4f}**.\n\n"
    )

    # Q7
    mdoc_row = tab_by_intent.get("multi_document")
    mhop_row = tab_by_intent.get("multi_hop")
    md.append("### 7. Which require multiple documents?\n")
    md.append(
        f"- **`multi_document`** ({mdoc_row.total_cases_count if mdoc_row else 0} cases): Ground truth requires gathering complementary facts "
        f"from distinct documents (e.g. initial triage notes + postmortem action items). RRF Recall@10 = **{mdoc_row.recall_at_10_rrf:.4f}**.\n"
        f"- **`multi_hop`** ({mhop_row.total_cases_count if mhop_row else 0} cases): Requires traversing a 3-step causal path ($A \\to B \\to C$). "
        f"RRF Recall@10 = **{mhop_row.recall_at_10_rrf:.4f}**.\n\n"
    )

    # Q8
    auth_sens_row = tab_by_intent.get("authority_sensitive")
    md.append("### 8. Which require authority/provenance?\n")
    md.append(
        f"- **`authority_sensitive`** ({auth_sens_row.total_cases_count if auth_sens_row else 0} cases): Inquiries where the corpus contains "
        f"both authoritative sources (postmortems, canonical runbooks) and low-authority chatter or deliberate poisoned evidence. "
        f"Unfiltered rerankers suffered catastrophic regressions here because poisoned records simulate authoritative language.\n\n"
    )

    # Q9
    auth_row = tab_by_intent.get("authorization_sensitive")
    md.append("### 9. Which require authorization?\n")
    md.append(
        f"- **`authorization_sensitive`** ({auth_row.total_cases_count if auth_row else 0} cases): Inquiries involving confidential HR records, "
        f"executive compensation, master credentials, or cross-tenant boundaries. "
        f"Zero-join pre-filtering preserves 100% tenant isolation, but user/role-level authorization requires identity context.\n\n"
    )

    # Q10
    adv_row = tab_by_intent.get("adversarial")
    md.append("### 10. Which are adversarial?\n")
    md.append(
        f"- **`adversarial`** ({adv_row.total_cases_count if adv_row else 0} cases): Retrieval poisoning (38 poisoned records), "
        f"indirect prompt injection (20 payloads), and citation manipulation. "
        f"In the cross-encoder evaluation, **164 poisoned documents** surfaced in top-10, demonstrating that relevance rankers "
        f"are actively misled by adversarial phrasing.\n\n"
    )

    # Q11
    md.append("### 11. Which failure classes are likely addressable through query understanding?\n")
    md.append(
        "1. **`exact_identifier` & `entity_attribute` expansion**: Expanding bare entity names (`checkout-service`) to include owner team, "
        "known aliases, and canonical catalog IDs directly addresses the 18 candidate-generation failures in `ownership`.\n"
        "2. **`temporal` constraint extraction**: Parsing date intervals (`2026-03`) and chronological markers into explicit metadata filters "
        "prevents contemporary tickets from crowding out historical postmortems.\n"
        "3. **`version_lifecycle` disambiguation**: Identifying version qualifiers (`v2.0` vs current) enables targeted lineage filtering.\n\n"
    )

    # Q12
    md.append("### 12. Which failures cannot be solved by query understanding alone?\n")
    md.append(
        "1. **`adversarial` & `authority_sensitive` poisoning**: Query understanding cannot verify whether a retrieved document is genuine or fabricated. "
        "Solving this requires **Metadata Authority Weighting / Filtering** at the index/retrieval layer.\n"
        "2. **`authorization_sensitive` access control**: Query understanding cannot decide access permissions without user identity context and "
        "authorization middleware.\n"
        "3. **`multi_hop` graph synthesis**: A single retrieval query cannot dynamically fetch intermediate causal nodes ($A \\to B \\to C$) "
        "without iterative decomposition or evidence assembly.\n\n"
    )

    # Q13
    md.append("### 13. What should the next controlled experiment test?\n")
    md.append(
        "The next controlled experiment should be **Phase 4C-1: Query Expansion & Metadata Authority Filtering**:\n"
        "1. **Deterministic Entity/Alias Query Expansion**: Test whether expanding service queries with catalog metadata resolves the 18 candidate-generation bottlenecks.\n"
        "2. **Metadata Authority Weighting**: Test whether applying authority multipliers (e.g. promoting `authority_level: high` official postmortems and penalizing unverified chatter/poisoned records) suppresses the 164 poisoned documents that broke the cross-encoder.\n"
    )

    return "".join(md)


def main() -> None:
    cases_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
    docs_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_documents.json"
    chunks_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_chunks.json"
    raw_path = _PROJECT_ROOT / "data" / "raw" / "novastack" / "source_records.json"
    bm25_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "bm25_baseline.json"
    dense_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "dense_baseline.json"
    hybrid_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "hybrid_baseline.json"
    diag_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "phase_4b0_candidate_diagnostics.json"
    rerank_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "phase_4b1_reranker_baseline.json"

    out_json = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "phase_4c0_query_profiles.json"
    out_md = _PROJECT_ROOT / "docs" / "PHASE_4C0_REPORT.md"

    # Pre-execution immutability hashes
    pre_hashes = {
        "raw": _compute_hash(raw_path),
        "docs": _compute_hash(docs_path),
        "chunks": _compute_hash(chunks_path),
        "cases": _compute_hash(cases_path),
        "bm25": _compute_hash(bm25_path),
        "dense": _compute_hash(dense_path),
        "hybrid": _compute_hash(hybrid_path),
        "diag": _compute_hash(diag_path),
        "rerank": _compute_hash(rerank_path),
    }

    print("[1/4] Loading evaluation cases and baseline artifacts...")
    with open(cases_path, "r", encoding="utf-8") as f:
        cases_data = json.load(f)["evaluation_cases"]

    with open(docs_path, "r", encoding="utf-8") as f:
        docs_list = json.load(f)["search_documents"]
        doc_source_types = {d["document_id"]: d.get("source_type", "") for d in docs_list}

    with open(bm25_path, "r", encoding="utf-8") as f:
        bm25_case_results = json.load(f).get("case_results", [])

    with open(dense_path, "r", encoding="utf-8") as f:
        dense_case_results = json.load(f).get("case_results", [])

    with open(hybrid_path, "r", encoding="utf-8") as f:
        hybrid_case_results = json.load(f).get("case_results", [])

    with open(diag_path, "r", encoding="utf-8") as f:
        diag_data = json.load(f)

    with open(rerank_path, "r", encoding="utf-8") as f:
        rerank_data = json.load(f)

    print(f"[2/4] Building standardized QueryProfiles for {len(cases_data)} cases...")
    profiles = build_all_query_profiles(cases_data, doc_source_types)

    # Intent distribution
    intent_dist = Counter()
    for p in profiles:
        for lbl in p.intent_labels:
            intent_dist[lbl] += 1

    print(f"[3/4] Generating failure cross-tabulation across 15 intent dimensions...")
    cross_tab = generate_failure_cross_tabulation(
        query_profiles=profiles,
        bm25_case_results=bm25_case_results,
        dense_case_results=dense_case_results,
        hybrid_case_results=hybrid_case_results,
        phase_4b0_diag=diag_data,
        phase_4b1_baseline=rerank_data,
    )

    key_findings = {
        "total_cases": len(profiles),
        "positive_cases": sum(1 for p in profiles if len(p.document_ids) > 0),
        "dominant_intents": intent_dist.most_common(5),
        "lowest_recall_intents": [
            {"intent": r.intent_label, "rrf_recall_at_10": r.recall_at_10_rrf}
            for r in sorted(cross_tab, key=lambda x: x.recall_at_10_rrf)[:4]
            if r.positive_cases_count > 0
        ],
        "top_candidate_generation_failure_intents": [
            {"intent": r.intent_label, "fails": r.candidate_generation_failures}
            for r in sorted(cross_tab, key=lambda x: -x.candidate_generation_failures)[:4]
            if r.positive_cases_count > 0
        ],
    }

    report_payload = {
        "version": "0.1.0",
        "total_cases": len(profiles),
        "intent_distribution": dict(intent_dist),
        "query_profiles": [p.to_dict() for p in profiles],
        "cross_tabulation": [r.to_dict() for r in cross_tab],
        "key_findings": key_findings,
    }

    print(f"[4/4] Writing output artifacts...")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report_payload, f, indent=2, ensure_ascii=False)

    md_content = generate_markdown_report(profiles, cross_tab, key_findings)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(md_content)

    print("\n" + "=" * 95)
    print("PHASE 4C-0: QUERY INTENT & FAILURE TAXONOMY DIAGNOSTIC COMPLETE")
    print("=" * 95)
    print(f"{'INTENT LABEL':<26} {'CASES':>7} {'BM25':>7} {'DENSE':>7} {'RRF':>7} {'RERANK':>7} {'UNION':>7} {'CAND_FAILS':>10}")
    print("-" * 95)
    for r in cross_tab:
        print(
            f"{r.intent_label:<26} {r.positive_cases_count:>3}/{r.total_cases_count:<3} "
            f"{r.recall_at_10_bm25:>7.4f} {r.recall_at_10_dense:>7.4f} {r.recall_at_10_rrf:>7.4f} "
            f"{r.recall_at_10_reranker:>7.4f} {r.union_coverage_at_50 * 100:>6.1f}% {r.candidate_generation_failures:>10}"
        )
    print("-" * 95)
    print(f"[SUCCESS] JSON artifact: {out_json.relative_to(_PROJECT_ROOT)}")
    print(f"[SUCCESS] Report:        {out_md.relative_to(_PROJECT_ROOT)}")

    # Verify immutability
    assert pre_hashes["raw"] == _compute_hash(raw_path), "Raw corpus was mutated!"
    assert pre_hashes["docs"] == _compute_hash(docs_path), "Search documents were mutated!"
    assert pre_hashes["chunks"] == _compute_hash(chunks_path), "Search chunks were mutated!"
    assert pre_hashes["cases"] == _compute_hash(cases_path), "Evaluation cases were mutated!"
    assert pre_hashes["bm25"] == _compute_hash(bm25_path), "BM25 baseline was mutated!"
    assert pre_hashes["dense"] == _compute_hash(dense_path), "Dense baseline was mutated!"
    assert pre_hashes["hybrid"] == _compute_hash(hybrid_path), "Hybrid baseline was mutated!"
    assert pre_hashes["diag"] == _compute_hash(diag_path), "Phase 4B-0 diagnostics were mutated!"
    assert pre_hashes["rerank"] == _compute_hash(rerank_path), "Phase 4B-1 baseline was mutated!"
    print("[VERIFIED] All 9 input and baseline artifacts remained 100% immutable.")


if __name__ == "__main__":
    main()
