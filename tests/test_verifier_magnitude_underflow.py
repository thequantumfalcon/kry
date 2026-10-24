"""Finite public inputs whose product underflows must be rejected without a crash."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from kry import kry_attest as ka, kry_mint as km


ROOT = Path(__file__).resolve().parents[1]


def attestation(tmp_path, event_type):
    receipt = km.MintReceipt.create(
        "KRY-00000001", event_type, 5e-324, "minimum positive float",
        "a" * 64, "0" * 64)
    log = tmp_path / "mint.jsonl"
    log.write_text(json.dumps(km._receipt_record(receipt)) + "\n")
    return ka.build_attestation(log).to_public_json()


def stranger_verifier():
    spec = importlib.util.spec_from_file_location("underflow_verifier", ROOT / "scripts/kry_verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("event_type", ["continuity_capsule", "unknown_event"])
@pytest.mark.parametrize("implementation", ["package", "stranger"])
def test_python_verifiers_reject_underflow_without_raising(tmp_path, event_type, implementation):
    encoded = attestation(tmp_path, event_type)
    if implementation == "package":
        ok, errors = ka.verify_attestation(encoded)
    else:
        ok, errors = stranger_verifier().verify_attestation(json.loads(encoded))
    assert not ok and errors


@pytest.mark.parametrize("event_type", ["continuity_capsule", "unknown_event"])
def test_underflow_cli_verdict_matches_javascript(tmp_path, event_type):
    path = tmp_path / "attestation.json"
    path.write_text(attestation(tmp_path, event_type))
    python = subprocess.run(
        [sys.executable, str(ROOT / "scripts/kry_verify.py"), str(path)],
        capture_output=True, text=True, check=False)
    assert python.returncode == 1
    assert "VERDICT: INVALID" in python.stdout
    assert "Traceback" not in python.stderr
    node = shutil.which("node")
    if node is not None:
        javascript = subprocess.run(
            [node, str(ROOT / "verifiers/js/cli.mjs"), str(path)],
            capture_output=True, text=True, check=False)
        assert javascript.returncode == 1
        assert "VERDICT: INVALID" in javascript.stdout


def test_positive_subnormal_product_keeps_existing_verdict(tmp_path):
    encoded = attestation(tmp_path, "cache_hit")
    assert ka.verify_attestation(encoded) == (True, [])
    assert stranger_verifier().verify_attestation(json.loads(encoded)) == (True, [])


@pytest.mark.parametrize("event_type, expected_valid", [
    ("continuity_capsule", False), ("unknown_event", False), ("cache_hit", True)])
def test_internal_chain_underflow_keeps_existing_verdict(tmp_path, monkeypatch,
                                                       event_type, expected_valid):
    attestation(tmp_path, event_type)
    monkeypatch.setattr(km, "_MINT_LOG_PATH", tmp_path / "mint.jsonl")
    valid, errors = km.verify_chain()
    assert valid is expected_valid
    assert bool(errors) is not expected_valid
