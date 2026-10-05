"""Phase 5F Step 5: Root cause classification per failure case.
Reads the intermediate forensics JSON and the checkpoint data to do deep per-case analysis.
"""
import json
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
FAIL_IDS = ["EVAL-0088", "EVAL-0090", "EVAL-0092", "EVAL-0096"]

ckpt_b = json.loads((WORKSPACE / "artifacts/phase_5e_checkpoint_backend_b.json").read_text(encoding="utf-8"))
ckpt_a = json.loads((WORKSPACE / "artifacts/phase_5e_checkpoint_backend_a.json").read_text(encoding="utf-8"))
cases_b = {c["evaluation_id"]: c for c in ckpt_b["cases_b"]}
cases_a = {c["evaluation_id"]: c for c in ckpt_a["cases_a"]}
phase4e = json.loads((WORKSPACE / "data/evaluation/novastack/phase_4e_evidence_assembly.json").read_text(encoding="utf-8"))
raw_cases = {c["evaluation_id"]: c for c in phase4e["cases"]}

print("=" * 80)
print("PHASE 5F STEP 5: ROOT CAUSE CLASSIFICATION")
print("=" * 80)

for eid in FAIL_IDS:
    raw = raw_cases[eid]
    ca = cases_a[eid]
    cb = cases_b[eid]
    diag_b = cb["answer_result"].get("diagnostics", {})
    selected = raw["evidence_package"].get("selected_evidence", [])

    print(f"\n{'=' * 70}")
    print(f"CASE: {eid}")
    print(f"Category: {raw.get('query_category')}")
    print(f"Tenant: {raw['tenant_id']}")
    print(f"Forbidden docs: {raw['forbidden_document_ids']}")
    print(f"Expected docs: {raw['expected_document_ids']}")
    print(f"Selected evidence items (count={len(selected)}):")
    for i, ev in enumerate(selected):
        print(f"  [{i+1}] doc_id={ev['document_id']} status={ev.get('evidence_status','?')} score={ev.get('retrieval_score',0):.4f} title={ev.get('title','?')[:60]}")
    print(f"\nQuery: {raw['query']}")
    print(f"\nBACKEND A:")
    print(f"  answer_status: {ca['answer_result']['answer_status']}")
    print(f"  answer_text: {ca['answer_result']['answer_text']}")
    print(f"  failure_category: {ca['answer_result']['failure_category']}")
    print(f"\nBACKEND B:")
    print(f"  answer_status: {cb['answer_result']['answer_status']}")
    print(f"  answer_text: {cb['answer_result']['answer_text']}")
    print(f"  failure_category: {cb['answer_result']['failure_category']}")
    print(f"  citations: {json.dumps(cb['answer_result']['citations'], indent=4)}")
    print(f"\n  Engine telemetry:")
    eng = diag_b.get("engine_telemetry", {})
    print(f"    prompt_eval_count: {eng.get('prompt_eval_count')} tokens")
    print(f"    eval_count (generated): {eng.get('eval_count')} tokens")
    print(f"    prompt_eval_duration_ms: {eng.get('prompt_eval_duration_ms'):.1f}")
    print(f"    eval_duration_ms: {eng.get('eval_duration_ms'):.1f}")
    print(f"  exposed_evidence_ids: {diag_b.get('exposed_evidence_ids')}")
    print(f"  prompt_strategy: {diag_b.get('prompt_strategy')}")
    print(f"  context_strategy: {diag_b.get('context_strategy')}")

print("\n" + "=" * 80)
print("KEY PATTERN SUMMARY")
print("=" * 80)
print("""
FAILING CASES CATEGORIES: cross_tenant (3), role_restricted (1)
ALL 4 FAILURES share these characteristics:
1. Evidence was SELECTED (selected_evidence_count > 0) — model had real content to work with
2. Backend B ANSWERED with specific factual content (citing [EVD-XXX] tags)
3. Backend A produced exact 'Insufficient evidence to answer this question.' abstention phrase
4. Backend B generated short, confident, factually coherent answers (15-30 tokens)
5. Evidence status was 'accepted' for exposed items — the evidence WAS relevant (just forbidden)

PASSING CONTROLS differ in one key way:
- 7 missing_information cases: NO relevant evidence in context → both A and B abstain
- 2 authorization cases: similar restriction but B still abstained
- 2 role_restricted cases (EVAL-0094, EVAL-0097): 0 evidence items → Layer 1 pre-gate fires
- 2 user_acl cases: evidence present but B still abstained
- 2 historical_security cases: evidence present but B still abstained

CRITICAL OBSERVATION:
EVAL-0094, EVAL-0097 (role_restricted, 0 evidence) → B abstained correctly
EVAL-0088, EVAL-0090, EVAL-0092 (cross_tenant, 7-10 evidence items) → B FAILED
EVAL-0096 (role_restricted, 10 evidence items) → B FAILED

The deterministic Layer 1 pre-gate in QuantizedLocalProvider/GroundedAnswerGenerator fires
BEFORE model inference and triggers abstention when evidence_count == 0.
When evidence IS present (selected_evidence_count > 0), the case flows to Layer 3 (model inference).
At Layer 3, Backend B uses llama.cpp/Q4_K_M to generate the final answer.

For CROSS-TENANT cases: the evidence passed IS semantically relevant content from a different tenant,
but marked forbidden. The Q4_K_M model sees real relevant evidence and generates an answer.
Backend A (PyTorch float32 FP32) generates the EXACT abstention phrase verbatim.
Backend B (Q4_K_M GGUF) generates a specific factual answer instead of abstaining.

This behavioral difference appears in CROSS-TENANT scenarios where:
- Evidence IS present in the context window
- Evidence IS semantically relevant to the query
- But the authorization constraint requires abstention

The constraint signal 'If insufficient evidence exists, respond with exactly...' is a SEMANTIC
instruction, not a hard computation. The Q4_K_M quantized model generates a factual answer
in this case — it reads the evidence as sufficient, then follows the answering instruction.
The FP32 model consistently interprets the context as requiring abstention.

This is consistent with CATEGORY F: INFERENCE RUNTIME BEHAVIOR DIFFERENCE
AND CATEGORY G: QUANTIZATION-INDUCED MODEL BEHAVIOR DIFFERENCE

The abstention instruction is calibrated language-level behavior. Quantization changes
the probability distribution at the next-token prediction level. In these cases, the
Q4_K_M model's probability mass shifts toward factual generation rather than the
abstention phrase.
""")
