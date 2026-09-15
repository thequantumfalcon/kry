"""The JS CLI's single-file mode must verify a real attestation, as README tells outsiders to.

`node verifiers/js/cli.mjs <attestation.json>` used to skip loading the published multiplier set
(SPEC §3.4.1), so verify.mjs fell back to {1.0} and rejected every attestation whose magnitude used
another multiplier. Skipped where node is not installed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node is not installed")


def _mint_sample_attestation(tmp_path: Path) -> Path:
    att = tmp_path / "att.json"
    env = {**os.environ, "KRY_DATA_DIR": str(tmp_path / "kry_data"), "PYTHONPATH": str(_ROOT / "src")}
    subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "kry_savings_report.py"),
         str(_ROOT / "examples" / "sample_usage_log.jsonl"), "--mint", "--attest", str(att)],
        cwd=tmp_path, env=env, check=True, capture_output=True)
    return att


def _run_cli(cli: Path, att: Path) -> subprocess.CompletedProcess:
    return subprocess.run([_NODE, str(cli), str(att)], capture_output=True, text=True, encoding="utf-8")


def test_single_file_mode_accepts_a_real_attestation(tmp_path):
    att = _mint_sample_attestation(tmp_path)
    result = _run_cli(_ROOT / "verifiers" / "js" / "cli.mjs", att)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERDICT: VALID" in result.stdout


def test_single_file_mode_without_the_corpus_warns_and_rejects(tmp_path):
    # Control: the same CLI with no vectors/ beside it reproduces the old behaviour, so the test
    # above only passes because the multiplier set was loaded.
    att = _mint_sample_attestation(tmp_path)
    js = tmp_path / "bare" / "verifiers" / "js"
    js.mkdir(parents=True)
    for name in ("cli.mjs", "verify.mjs"):
        shutil.copy(_ROOT / "verifiers" / "js" / name, js / name)
    result = _run_cli(js / "cli.mjs", att)
    assert result.returncode == 1
    assert "VERDICT: INVALID" in result.stdout
    assert "legal_multipliers.json not found" in result.stderr
