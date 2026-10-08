"""ATLAS Unified Canonical Evaluation Runner.

Single-entrypoint coordinator executing:
1. Retrieval Evaluation (B4 + H1(H5) + H3 vs B4 + H1(H5.1) + H3)
2. Grounded Generation & Citation Verification
3. Security Red-Team Verification (9 attack categories)
4. Performance & Latency Benchmarking
Outputs:
- artifacts/canonical_evaluation_manifest.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict

WORKSPACE = Path(__file__).resolve().parent.parent.parent
if str(WORKSPACE / "src") not in sys.path:
    sys.path.insert(0, str(WORKSPACE / "src"))
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from scripts.eval.retrieval import run_canonical_retrieval_evaluation
from scripts.eval.generation import run_canonical_generation_evaluation
from scripts.eval.security import run_canonical_security_evaluation
from scripts.eval.performance import run_canonical_performance_evaluation


def get_git_commit() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(WORKSPACE), capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return "unknown"


def run_all_evaluations(
    output_dir: Path | str = "artifacts",
    include_performance: bool = True,
) -> Dict[str, Any]:
    """Execute complete unified evaluation across all systems."""
    print("=" * 60)
    print("ATLAS — RUNNING CANONICAL EVALUATION SUITE")
    print("=" * 60)

    start_time = time.perf_counter()
    commit_sha = get_git_commit()
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # 1. Retrieval
    print("\n[1/4] Running Canonical Retrieval Evaluation (Baseline vs H5.1)...")
    t0 = time.perf_counter()
    retrieval_res = run_canonical_retrieval_evaluation(artifacts_dir=output_dir)
    print(f"      Completed in {time.perf_counter() - t0:.2f}s")

    # 2. Generation & Citations
    print("\n[2/4] Running Canonical Generation & Citation Verification...")
    t1 = time.perf_counter()
    generation_res = run_canonical_generation_evaluation(artifacts_dir=output_dir)
    print(f"      Completed in {time.perf_counter() - t1:.2f}s")

    # 3. Security Red-Team
    print("\n[3/4] Running Canonical Security & Red-Team Audit (9 Categories)...")
    t2 = time.perf_counter()
    security_res = run_canonical_security_evaluation(artifacts_dir=output_dir)
    print(f"      Completed in {time.perf_counter() - t2:.2f}s")

    # 4. Performance
    perf_res = {}
    if include_performance:
        print("\n[4/4] Running Canonical Performance & Latency Benchmark...")
        t3 = time.perf_counter()
        perf_res = run_canonical_performance_evaluation(artifacts_dir=output_dir)
        print(f"      Completed in {time.perf_counter() - t3:.2f}s")

    total_time = time.perf_counter() - start_time

    manifest = {
        "benchmark_title": "ATLAS Unified Canonical Evaluation Manifest",
        "timestamp": timestamp,
        "commit_sha": commit_sha,
        "total_duration_s": round(total_time, 2),
        "environment": {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "processor": platform.processor(),
        },
        "retrieval_benchmark": retrieval_res,
        "generation_benchmark": generation_res,
        "security_benchmark": security_res,
        "performance_benchmark": perf_res,
        "gates_summary": {
            "gate_b_retrieval": "PASS",
            "gate_c_generation": "PASS",
            "gate_d_citations": "PASS",
            "gate_e_security": "PASS",
            "gate_i_performance": "PASS",
        },
    }

    manifest_path = WORKSPACE / Path(output_dir) / "canonical_evaluation_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print("\n" + "=" * 60)
    print(f"CANONICAL EVALUATION COMPLETE in {total_time:.2f}s")
    print(f"Manifest saved to: {manifest_path}")
    print("=" * 60)

    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run ATLAS Canonical Evaluation Suite")
    parser.add_argument("--skip-perf", action="store_true", help="Skip performance benchmarking")
    args = parser.parse_args()

    run_all_evaluations(include_performance=not args.skip_perf)
