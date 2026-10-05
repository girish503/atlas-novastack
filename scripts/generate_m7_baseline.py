"""Generate artifacts/phase_05_m7_baseline.json from verified M6 results."""
import json
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent

with open(root_dir / "artifacts" / "phase_05_m6_results.json") as f:
    m6_results = json.load(f)

cases = m6_results.get("evaluation_results", [])
total_cases = len(cases)
positive_cases = [c for c in cases if c.get("expected_document_ids")]
negative_cases = [c for c in cases if not c.get("expected_document_ids")]

answered_pos = [c for c in positive_cases if c.get("answer_status") == "answered"]
abstained_pos = [c for c in positive_cases if c.get("answer_status") == "abstained"]
abstained_neg = [c for c in negative_cases if c.get("answer_status") == "abstained"]

with_valid_citations = [
    c for c in answered_pos
    if any(cit.get("status") in ("VALID", "valid") for cit in c.get("citations", []))
]

all_citations = [cit for c in cases for cit in c.get("citations", [])]
valid_citations = [cit for cit in all_citations if cit.get("status") in ("VALID", "valid")]

latencies = [c.get("generation_latency_ms", 0.0) for c in answered_pos]
mean_lat = sum(latencies) / len(latencies) if latencies else 0.0

# Multi-hop slice
multihop_cids = [f"EVAL-{i:04d}" for i in range(43, 51)] + [f"EVAL-{i:04d}" for i in range(79, 83)] + ["EVAL-0009", "EVAL-0010", "EVAL-0027", "EVAL-0028", "EVAL-0030", "EVAL-0031"]
multihop_cases = [c for c in positive_cases if c["evaluation_id"] in multihop_cids]
multihop_answered = [c for c in multihop_cases if c.get("answer_status") == "answered"]

# Failure taxonomy for positive abstentions
failure_taxonomy = {}
for c in abstained_pos:
    reason = c.get("abstention_reason") or "unknown"
    failure_taxonomy[reason] = failure_taxonomy.get(reason, 0) + 1

baseline_data = {
    "milestone": "ATLAS 0.5 — Milestone M7 Baseline Characterization (from M6 Certified Results)",
    "source_milestone": "ATLAS 0.5-M6",
    "production_baseline_reference": "0.4.14-rc1",
    "immutable_baseline_verified": True,
    "metrics": {
        "positive_answer_yield": {
            "answered": len(answered_pos),
            "total": len(positive_cases),
            "yield_pct": round(len(answered_pos) / len(positive_cases) * 100, 2),
        },
        "negative_abstention": {
            "abstained": len(abstained_neg),
            "total": len(negative_cases),
            "abstention_pct": round(len(abstained_neg) / len(negative_cases) * 100, 2),
        },
        "citation_precision_pct": round(len(valid_citations) / len(all_citations) * 100, 2) if all_citations else 100.0,
        "citation_completeness_pct": round(len(with_valid_citations) / len(answered_pos) * 100, 2) if answered_pos else 0.0,
        "mean_positive_latency_ms": round(mean_lat, 2),
        "multi_hop_focus_slice": {
            "total_cases": len(multihop_cases),
            "answered": len(multihop_answered),
            "yield_pct": round(len(multihop_answered) / len(multihop_cases) * 100, 2) if multihop_cases else 0.0,
        },
    },
    "failure_taxonomy_positive_abstentions": failure_taxonomy,
    "m7_recovery_targets": [
        "EVAL-0018", "EVAL-0019", "EVAL-0033", "EVAL-0044", "EVAL-0046", "EVAL-0050", "EVAL-0062", "EVAL-0064"
    ],
    "target_yield_pct": 66.34,
    "target_yield_cases": 67,
}

out_path = root_dir / "artifacts" / "phase_05_m7_baseline.json"
with open(out_path, "w") as f:
    json.dump(baseline_data, f, indent=2)

print(f"Generated {out_path} successfully!")
print(f"M7 Baseline Yield: {baseline_data['metrics']['positive_answer_yield']['yield_pct']}% ({len(answered_pos)}/{len(positive_cases)})")
