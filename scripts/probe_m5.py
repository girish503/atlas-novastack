"""Targeted probe script for ATLAS 0.5 Milestone M5 Minimum Sufficient Evidence Selection."""

import json
import time
from pathlib import Path

from novastack.citation_validator import CitationValidator
from novastack.context_budgeter import AdaptiveContextBudgeter
from novastack.entity_catalog import EntityCatalog
from novastack.entity_grounding import EntityGroundingGate
from novastack.evidence import EvidenceItem, EvidencePackage
from novastack.evidence_selector import MinimumSufficientEvidenceSelector, classify_evidence_role
from novastack.provider import InferenceServiceAdapter

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def dict_to_evidence_item(d):
    return EvidenceItem(
        evidence_id=d.get("evidence_id", ""),
        chunk_id=d.get("chunk_id", ""),
        document_id=d.get("document_id", ""),
        tenant_id=d.get("tenant_id", ""),
        source_type=d.get("source_type", ""),
        title=d.get("title", ""),
        text=d.get("text", ""),
        source_entity_id=d.get("source_entity_id"),
        source_entity_type=d.get("source_entity_type"),
        related_entity_ids=d.get("related_entity_ids", []),
        authority_level=d.get("authority_level", "medium"),
        classification=d.get("classification", "internal"),
        permissions=None,
        status="published",
        version="v1.0",
        created_at=d.get("created_at", "2025-01-01T00:00:00"),
        updated_at=d.get("updated_at"),
        valid_from=d.get("valid_from"),
        valid_until=d.get("valid_until"),
        parent_id=d.get("parent_id"),
        supersedes_id=d.get("supersedes_id"),
        retrieval_rank=d.get("retrieval_rank", 0),
        retrieval_score=d.get("retrieval_score", 0.0),
        retrieval_channels=d.get("retrieval_channels", []),
        evidence_status=d.get("evidence_status", "accepted"),
    )

def dict_to_evidence_package(d, query, eval_id, tenant_id):
    selected = [dict_to_evidence_item(x) for x in d.get("selected_evidence", [])]
    return EvidencePackage(
        package_id=d.get("package_id", f"PKG-{eval_id}"),
        evaluation_id=eval_id,
        query=query,
        tenant_id=tenant_id,
        user_context={"tenant_id": tenant_id, "roles": ["engineer"], "classification_limit": "internal"},
        selected_evidence=selected,
        excluded_evidence=[],
        conflicts=[],
        provenance_graph=[],
        resolution_decisions=[],
        statistics=d.get("statistics", {}),
        diagnostics=d.get("metrics", {}),
    )

def main():
    catalog = EntityCatalog()
    gate = EntityGroundingGate(catalog=catalog)
    selector = MinimumSufficientEvidenceSelector(catalog=catalog, grounding_gate=gate)
    acb = AdaptiveContextBudgeter(grounding_gate=gate, evidence_selector=selector)
    adapter = InferenceServiceAdapter(model_name="gemma3:1b")

    cases_path = PROJECT_ROOT / "data" / "evaluation" / "novastack" / "phase_4e_evidence_assembly.json"
    raw_cases = json.load(open(cases_path, encoding="utf-8"))["cases"]

    test_ids = ["EVAL-0044", "EVAL-0035", "EVAL-0054", "EVAL-0058"]
    for cid in test_ids:
        c = next(x for x in raw_cases if x["evaluation_id"] == cid)
        pkg = dict_to_evidence_package(c["evidence_package"], c["query"], cid, c["tenant_id"])
        
        # Test selection
        sel_res = selector.select_minimum_sufficient_evidence(pkg)
        print(f"\n[{cid}] Query: {c['query']}")
        print(f"  Plan: {sel_res.plan.required_roles}, Budget: {sel_res.plan.target_document_budget}, Protective: {sel_res.plan.is_protective}")
        print(f"  Selected: {[it.document_id for it in sel_res.selected_items]}")
        print(f"  Roles: {[classify_evidence_role(it).value for it in sel_res.selected_items]}")
        print(f"  Structurally complete: {sel_res.coverage.is_structurally_complete}")

        # Test generation with minimum_sufficient_hierarchical strategy
        t0 = time.perf_counter()
        ans = adapter.generate_answer(
            package=pkg,
            expected_doc_ids=c.get("expected_document_ids", []),
            forbidden_doc_ids=c.get("forbidden_document_ids", []),
            context_strategy="minimum_sufficient_hierarchical",
            max_token_budget=420,
        )
        lat = (time.perf_counter() - t0) * 1000.0
        print(f"  Outcome: {ans.answer_status} in {lat:.1f}ms")
        print(f"  Answer: {ans.answer_text[:120]}...")
        print(f"  Citations: {[c.document_id for c in ans.citations]}")

if __name__ == "__main__":
    main()
