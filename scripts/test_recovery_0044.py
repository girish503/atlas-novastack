import json
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from scripts.probe_phase_05_m6_targets import dict_to_evidence_package, dict_to_evidence_item
from novastack.provider import InferenceServiceAdapter

cases = json.load(open('data/evaluation/novastack/phase_4e_evidence_assembly.json', 'r', encoding='utf-8'))['cases']
c = next(x for x in cases if x['evaluation_id'] == 'EVAL-0045')

raw = json.load(open('data/processed/novastack/search_chunks.json', 'r', encoding='utf-8'))
chunks = raw.get('search_chunks', raw)
inc_chunk = next(x for x in chunks if 'DOC-INC-INC-NS-0002-01' in x['document_id'])
pm_chunk = next(x for x in chunks if 'DOC-PM-EVT-NS-0002' in x['document_id'])
dep_chunk = next(x for x in chunks if 'DOC-DEP-DEP-NS-0002' in x['document_id'])
pr_chunk = next(x for x in chunks if 'DOC-PR-PR-NS-0002' in x['document_id'])

items = [
    dict_to_evidence_item(inc_chunk),
    dict_to_evidence_item(pm_chunk),
    dict_to_evidence_item(dep_chunk),
    dict_to_evidence_item(pr_chunk),
]
for idx, it in enumerate(items):
    it.evidence_id = f"EVD-{idx+1:03d}"
pkg = dict_to_evidence_package(c['evidence_package'], c['query'], 'EVAL-0045', c['tenant_id'])
pkg.selected_evidence = items


provider = InferenceServiceAdapter()
res = provider.generate_answer(
    package=pkg,
    context_strategy='raw_prefix',
    max_token_budget=420,
    prompt_strategy='config_a_calibrated',
    citation_resolver='c2',
    max_new_tokens=100,
    expected_doc_ids=c.get('expected_document_ids', []),
    forbidden_doc_ids=c.get('forbidden_document_ids', []),
)
print('STATUS:', res.answer_status)
print('ANSWER:', res.answer_text)
print('CITATIONS:', [c.evidence_id for c in res.citations])
print('VALID CITATIONS:', [c.evidence_id for c in res.citations if getattr(c.status, 'value', str(c.status)) == 'valid'])
