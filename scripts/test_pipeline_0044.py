import json
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from scripts.probe_phase_05_m6_targets import dict_to_evidence_package
from novastack.provider import InferenceServiceAdapter
from novastack.evidence_selector import MinimumSufficientEvidenceSelector

cases = json.load(open('data/evaluation/novastack/phase_4e_evidence_assembly.json', 'r', encoding='utf-8'))['cases']
c = next(x for x in cases if x['evaluation_id'] == 'EVAL-0045')
pkg = dict_to_evidence_package(c['evidence_package'], c['query'], 'EVAL-0045', c['tenant_id'])

provider = InferenceServiceAdapter()
selector = provider._generator.budgeter.evidence_selector

sel_res = selector.select_minimum_sufficient_evidence(pkg)
print("Sel_res items count:", len(sel_res.selected_items))
print("Sel_res docs:", [it.document_id for it in sel_res.selected_items])
print("Sel_res recovered count:", len(sel_res.diagnostics.get("recovered_items", [])))

budgeted = provider._generator.budgeter.budget_context(pkg, strategy='minimum_sufficient_hierarchical')
print("Budgeted count:", len(budgeted))
print("Budgeted docs:", [it.document_id for it in budgeted])

res = provider.generate_answer(
    package=pkg,
    context_strategy='minimum_sufficient_hierarchical',
    max_token_budget=420,
    prompt_strategy='config_a_calibrated',
    citation_resolver='c2',
    max_new_tokens=80,
    expected_doc_ids=c.get('expected_document_ids', []),
    forbidden_doc_ids=c.get('forbidden_document_ids', []),
    timeout_seconds=60.0,
)
print("Answer status:", res.answer_status)
print("Abstention reason:", res.abstention_reason)
print("Answer text:", repr(res.answer_text))
print("Citations:", [c.evidence_id for c in res.citations])
print("Unsupported claims:", res.unsupported_claims)
print("Diagnostics:", res.diagnostics)
