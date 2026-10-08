#!/usr/bin/env python3
"""CLI wrapper to run ATLAS canonical evaluation suite."""

import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

from scripts.eval.runner import run_all_evaluations

if __name__ == "__main__":
    run_all_evaluations()
