"""Lab lease selection is scoped to verification and leaves caller state intact."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from kry import kry_settlement as ks
from lab import hole_d_double_spend as proof
from lab import node


ROOT = Path(__file__).resolve().parents[1]
LEASE_ENV = "KRY_SETTLE_LEASE_DIR"


@pytest.mark.parametrize("target", ["node-with-lease", "node-without-lease", "hole"])
@pytest.mark.parametrize("caller", [None, "", "operator"])
@pytest.mark.parametrize("outcome", ["accepted", "rejected", "raised", "interrupted"])
def test_lab_accept_restores_caller_lease_environment(tmp_path, monkeypatch, target, caller, outcome):
    original = str(tmp_path / caller) if caller else caller
    if original is None:
        monkeypatch.delenv(LEASE_ENV, raising=False)
    else:
        monkeypatch.setenv(LEASE_ENV, original)
    share = tmp_path / "share"
    share.mkdir()
    attestation = '{"total_kry":10000}'
    (share / "attestation.json").write_text(attestation, encoding="utf-8")
    expected = str(share / "leases") if target == "node-with-lease" else None
    error = KeyboardInterrupt("constructed interruption") if outcome == "interrupted" else RuntimeError("constructed failure")
    calls = []

    def verify(*args, **kwargs):
        calls.append("verify")
        assert os.environ.get(LEASE_ENV) == expected
        if outcome in ("raised", "interrupted"):
            raise error
        return (object(), "accepted") if outcome == "accepted" else (None, "denied")

    def settle(*args, **kwargs):
        calls.append("settle")
        assert os.environ.get(LEASE_ENV) == original

    monkeypatch.setattr(ks, "verify_and_accept", verify)
    monkeypatch.setattr(ks, "settle", settle)

    def accept():
        if target == "hole":
            return proof._accept_and_settle(tmp_path / "node" / "reg.jsonl", "A", attestation, 7000, 10000)
        return node.accept(share, tmp_path / "node", "A", 7000, target == "node-with-lease")

    try:
        if outcome in ("raised", "interrupted"):
            with pytest.raises(type(error)) as raised:
                accept()
            assert raised.value is error
        else:
            result = accept()
            assert result == (outcome == "accepted" if target == "hole" else 0)
        assert calls == (["verify", "settle"] if outcome == "accepted" else ["verify"])
    finally:
        assert os.environ.get(LEASE_ENV) == original


@pytest.mark.parametrize("script", ["lab/hole_d_double_spend.py", "lab/run_local.py"])
def test_synthetic_proof_preserves_inherited_lease_authority(tmp_path, script):
    authority = tmp_path / "operator-leases"
    assert ks._acquire_lease("A", 10000, 10000, nonce="operator-hold", now=time.time(), authdir=authority)
    (authority / "canary.txt").write_bytes(b"retained operator lease data\n")
    before = {path.name: path.read_bytes() for path in authority.iterdir()}
    scratch = tmp_path / "proofs"
    scratch.mkdir()
    env = dict(os.environ, KRY_SETTLE_LEASE_DIR=str(authority), KRY_DATA_DIR=str(tmp_path / "data"),
               PYTHONPATH=str(ROOT / "src"), TMPDIR=str(scratch))
    result = subprocess.run([sys.executable, "-B", script], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = ("All local tests pass." if script == "lab/run_local.py" else
                "RESULT: hole demonstrated=True  fix holds=True  lease atomic=True")
    assert expected in result.stdout
    assert {path.name: path.read_bytes() for path in authority.iterdir()} == before
