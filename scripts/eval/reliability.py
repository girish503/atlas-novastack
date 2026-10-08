"""Canonical reliability evaluation by executing existing fail-closed tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

WORKSPACE = Path(__file__).resolve().parent.parent.parent

RELIABILITY_TESTS = [
    "tests/test_phase_4o_resilience.py",
    "tests/test_phase_4m_auth_fail_closed.py",
    "tests/test_phase_4q_ingestion_reliability.py",
]


def run_canonical_reliability_evaluation(
    artifacts_dir: Path | str = "artifacts",
    write_artifact: bool = True,
    extra_tests: List[str] | None = None,
) -> Dict[str, Any]:
    tests = list(RELIABILITY_TESTS)
    if extra_tests:
        tests.extend(extra_tests)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *tests, "-q", "--tb=line"],
        cwd=str(WORKSPACE),
        capture_output=True,
        text=True,
    )
    summary = {
        "benchmark_name": "ATLAS Canonical Reliability Evaluation",
        "evaluation_scope": "PYTEST_IN_PROCESS",
        "tests": tests,
        "returncode": completed.returncode,
        "stdout_tail": "\n".join(completed.stdout.splitlines()[-40:]),
        "stderr_tail": "\n".join(completed.stderr.splitlines()[-20:]),
        "gate_evaluation": {"pass": completed.returncode == 0},
    }
    if write_artifact:
        out_dir = WORKSPACE / Path(artifacts_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "canonical_reliability_report.json", "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
    return summary
