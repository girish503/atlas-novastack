import json
from pathlib import Path
from novastack.evidence import EvidenceItem
from novastack.evidence_selector import classify_evidence_role

raw_cases = json.load(open('data/evaluation/novastack/phase_4e_evidence_assembly.json', encoding='utf-8'))['cases']
c44 = next(x for x in raw_cases if x['evaluation_id'] == 'EVAL-0044')
print('EVAL-0044 expected docs:', c44.get('expected_document_ids'))
print('All candidates in selected_evidence:')
for it_dict in c44['evidence_package']['selected_evidence']:
    it = EvidenceItem(
        evidence_id=it_dict.get('evidence_id', ''),
        chunk_id=it_dict.get('chunk_id', ''),
        document_id=it_dict.get('document_id', ''),
        tenant_id=it_dict.get('tenant_id', ''),
        source_type=it_dict.get('source_type', ''),
        title=it_dict.get('title', ''),
        text=it_dict.get('text', ''),
        source_entity_id=it_dict.get('source_entity_id'),
        source_entity_type=it_dict.get('source_entity_type'),
        related_entity_ids=it_dict.get('related_entity_ids', []),
        authority_level=it_dict.get('authority_level', 'medium'),
        classification=it_dict.get('classification', 'internal'),
        permissions=None,
        status='published',
        version='v1.0',
        created_at=it_dict.get('created_at', ''),
        updated_at=it_dict.get('updated_at'),
        valid_from=it_dict.get('valid_from'),
        valid_until=it_dict.get('valid_until'),
        parent_id=it_dict.get('parent_id'),
        supersedes_id=it_dict.get('supersedes_id'),
        retrieval_rank=it_dict.get('retrieval_rank', 0),
        retrieval_score=it_dict.get('retrieval_score', 0.0),
        retrieval_channels=it_dict.get('retrieval_channels', []),
        evidence_status=it_dict.get('evidence_status', 'accepted'),
    )
    role = classify_evidence_role(it)
    print(f"  doc={it.document_id:25} src={str(it.source_entity_id):15} rel={str(it.related_entity_ids):30} role={role.value:20}")
