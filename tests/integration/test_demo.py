"""Phase 13 checkpoint: the acceptance demo prints unambiguous AC-4/5/6 evidence."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def test_demo_script_runs_and_demonstrates_acceptance():
    env = {**os.environ, "AS_OF_DATE": "2026-06-22", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "scripts/demo.py"], cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = proc.stdout
    assert "AC-5" in out and "financial audit rows for request: 1" in out
    assert "AC-6" in out and "matching audit events emitted: 1" in out
    assert "AC-4" in out and "executed=partial_refund" in out
    assert "guardrail_violation_count              0" in out
