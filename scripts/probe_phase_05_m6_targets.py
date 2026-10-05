"""Targeted probe script for ATLAS 0.5 Milestone M6 key cases.
Tests:
- Track A: EVAL-0079, EVAL-0080, EVAL-0081, EVAL-0082
- Track B: EVAL-0044, EVAL-0045, EVAL-0048
- Track C: EVAL-0009, EVAL-0027, EVAL-0030
- Negative Controls: EVAL-0054, EVAL-0058
"""

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.provider import InferenceServiceAdapter
from novastack.evidence import EvidenceConflict, EvidenceItem, EvidencePackage, EvidenceStatus
from novastack.models import RecordPermissions

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("m6_probe")


def dict_to_evidence_item(d: dict[str, Any]) -> EvidenceItem:
    """Deserialize an EvidenceItem from a dictionary."""
    perms_data = d.get("permissions")
    if isinstance(perms_data, dict):
        perms = RecordPermissions(
            allowed_roles=list(perms_data.get("allowed_roles", [])),
            allowed_departments=list(perms_data.get("allowed_departments", [])),
            allowed_teams=list(perms_data.get("allowed_teams", [])),
            allowed_user_ids=list(perms_data.get("allowed_user_ids", [])),
        )
    else:
        perms = RecordPermissions()
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
        permissions=perms,
        status=d.get("status", "published"),
        version=d.get("version", "v1.0"),
        created_at=d.get("created_at", ""),
        updated_at=d.get("updated_at"),
        valid_from=d.get("valid_from"),
        valid_until=d.get("valid_until"),
        parent_id=d.get("parent_id"),
        supersedes_id=d.get("supersedes_id"),
        retrieval_rank=d.get("retrieval_rank", 0),
        retrieval_score=d.get("retrieval_score", 0.0),
        retrieval_channels=d.get("retrieval_channels", []),
        evidence_status=d.get("evidence_status", EvidenceStatus.ACCEPTED.value),
        evidence_reasons=d.get("evidence_reasons", []),
        conflict_ids=d.get("conflict_ids", []),
        duplicate_of=d.get("duplicate_of"),
        duplicate_chunk_ids=d.get("duplicate_chunk_ids", []),
    )


def dict_to_evidence_package(d: dict[str, Any], query: str, eval_id: str, tenant_id: str) -> EvidencePackage:
    """Convert JSON dict back into typed EvidencePackage."""
    selected = [dict_to_evidence_item(item) for item in d.get("selected_evidence", [])]
    excluded = [dict_to_evidence_item(item) for item in d.get("excluded_evidence", [])]
    conflicts_data = d.get("conflicts", [])
    conflicts = [
        EvidenceConflict(
            conflict_id=c.get("conflict_id", ""),
            conflict_type=c.get("conflict_type", ""),
            entity_id=c.get("entity_id"),
            primary_evidence_id=c.get("primary_evidence_id", ""),
            conflicting_evidence_ids=c.get("conflicting_evidence_ids", []),
            resolution_status=c.get("resolution_status", ""),
            resolution_reason=c.get("resolution_reason", ""),
        )
        for c in conflicts_data
    ]
    return EvidencePackage(
        package_id=d.get("package_id", f"PKG-{eval_id}"),
        evaluation_id=eval_id,
        query=query,
        tenant_id=tenant_id,
        user_context={"tenant_id": tenant_id, "roles": ["engineer"], "classification_limit": "internal"},
        selected_evidence=selected,
        excluded_evidence=excluded,
        conflicts=conflicts,
        provenance_graph=[],
        resolution_decisions=[],
        statistics=d.get("statistics", {}),
        diagnostics=d.get("metrics", {}),
    )


def main():
    docs_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_documents.json"
    chunks_path = _PROJECT_ROOT / "data" / "processed" / "novastack" / "search_chunks.json"
    docs_data = json.loads(docs_path.read_text(encoding="utf-8"))
    chunks_data = json.loads(chunks_path.read_text(encoding="utf-8"))
    corpus_doc_ids = {d["document_id"] for d in (docs_data.get("search_documents", []) if isinstance(docs_data, dict) else docs_data)}
    corpus_chunk_ids = {c["chunk_id"] for c in (chunks_data.get("search_chunks", []) if isinstance(chunks_data, dict) else chunks_data)}

    phase4e_path = _PROJECT_ROOT / "data" / "evaluation" / "novastack" / "phase_4e_evidence_assembly.json"
    cases = json.load(open(phase4e_path, "r", encoding="utf-8"))["cases"]

    case_map = {c["evaluation_id"]: c for c in cases}
    target_ids = [
        "EVAL-0079", "EVAL-0080", "EVAL-0081", "EVAL-0082",
        "EVAL-0044", "EVAL-0045", "EVAL-0048",
        "EVAL-0009", "EVAL-0027", "EVAL-0030",
        "EVAL-0054", "EVAL-0058",
    ]

    provider = InferenceServiceAdapter(
        service_url="http://127.0.0.1:8001",
        model_name="gemma3:1b",
        default_timeout_seconds=60.0,
        corpus_doc_ids=corpus_doc_ids,
        corpus_chunk_ids=corpus_chunk_ids,
    )

    logger.info("Executing M6 targeted probe across 12 target cases...")
    for tid in target_ids:
        c = case_map[tid]
        pkg = dict_to_evidence_package(c["evidence_package"], c["query"], tid, c["tenant_id"])
        t0 = time.perf_counter()
        res = provider.generate_answer(
            package=pkg,
            context_strategy="minimum_sufficient_hierarchical",
            max_token_budget=350,
            prompt_strategy="config_a_calibrated",
            citation_resolver="c2",
            max_new_tokens=60,
            expected_doc_ids=c.get("expected_document_ids", []),
            forbidden_doc_ids=c.get("forbidden_document_ids", []),
            timeout_seconds=60.0,
        )
        dur = (time.perf_counter() - t0) * 1000.0
        cits = [cit.evidence_id for cit in res.citations]
        valid_cits = [cit for cit in res.citations if getattr(cit.status, "value", str(cit.status)) == "valid"]
        logger.info(
            f"Case {tid}: status={res.answer_status} in {dur:.1f}ms | cits={len(valid_cits)}/{len(cits)} | answer={res.answer_text[:65]}..."
        )


if __name__ == "__main__":
    main()
