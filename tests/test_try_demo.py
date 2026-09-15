"""`kry-try` from a source checkout: mint, attest and verify without touching a configured ledger."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_demo_writes_a_valid_attestation_and_leaves_no_ledger_behind(tmp_path):
    real_ledger = tmp_path / "real_ledger"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "KRY_DATA_DIR": str(real_ledger)}
    out = tmp_path / "att.json"
    res = subprocess.run([sys.executable, "-m", "kry.try_demo", "--out", str(out)], cwd=tmp_path, env=env,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "SYNTHETIC" in res.stdout
    assert "VERDICT: VALID" in res.stdout

    att = json.loads(out.read_text(encoding="utf-8"))
    assert att["receipts"] == 2
    assert att["veracity"]["veracity_floor"] == 0.0          # made-up events claim no anchoring
    assert not real_ledger.exists()                          # the configured ledger was never used
    temp_ledger = re.search(r"temporary ledger: (.+) \(deleted on exit\)", res.stdout).group(1)
    assert not Path(temp_ledger).exists()


def test_demo_refuses_to_run_after_the_ledger_modules_are_imported(tmp_path):
    # Their paths were fixed at import time, so the demo could no longer redirect writes to a temp dir.
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "KRY_DATA_DIR": str(tmp_path / "real_ledger")}
    code = ("import sys, kry.kry_mint; from kry import try_demo; "
            f"sys.exit(try_demo.main(['--out', {str(tmp_path / 'att.json')!r}]))")
    res = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    assert res.returncode == 2, res.stdout + res.stderr
    assert "run kry-try in a fresh process" in res.stderr
    assert not (tmp_path / "att.json").exists()
