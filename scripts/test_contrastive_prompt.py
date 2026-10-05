import json
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))
import urllib.request
from novastack.provider import InferenceServiceAdapter
from scripts.probe_phase_05_m6_targets import dict_to_evidence_package

cases = json.load(open('data/evaluation/novastack/phase_4e_evidence_assembly.json', 'r', encoding='utf-8'))['cases']

for cid in ['EVAL-0079', 'EVAL-0080', 'EVAL-0081', 'EVAL-0082']:
    c = next(x for x in cases if x['evaluation_id'] == cid)
    pkg = dict_to_evidence_package(c['evidence_package'], c['query'], cid, c['tenant_id'])
    provider = InferenceServiceAdapter()
    budgeted = provider._generator.budgeter.budget_context(pkg, strategy='minimum_sufficient_hierarchical')
    evidence_blocks = []
    for idx, item in enumerate(budgeted):
        evd_tag = f"EVD-{idx + 1:03d}"
        clean_text = item.text.strip()
        block = f'<evidence_data id="{evd_tag}" doc_id="{item.document_id}" title="{item.title}">\n{clean_text}\n</evidence_data>'
        evidence_blocks.append(block)
    evidence_str = "\n\n".join(evidence_blocks)

    inst = (
        "You are an enterprise AI assistant for ATLAS. Answer the user's question using ONLY the facts explicitly provided in the evidence items below.\n\n"
        "Rules:\n"
        "1. Answer the question using the facts provided in the evidence items. For questions asking whether an issue was caused by option A or option B, confirm the cause that is supported by the evidence.\n"
        "2. If the evidence does not contain the answer, respond EXACTLY: \"Insufficient evidence to answer this question.\"\n"
        "3. Every factual statement must cite its supporting evidence using [EVD-XXX] notation (e.g., [EVD-001]).\n"
        "4. Answer concisely in 1 to 2 sentences."
    )
    prompt = f"{inst}\n\nEVIDENCE:\n{evidence_str}\n\nQUESTION: {c['query']}\n\nANSWER (cite [EVD-XXX]):\n\nAssistant:"
    payload = json.dumps({'prompt': prompt, 'max_new_tokens': 60, 'temperature': 0.0}).encode()
    req = urllib.request.Request('http://127.0.0.1:8001/generate', data=payload, headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode())
        print(cid, 'RESPONSE:', repr(res.get('generated_text', '')))
