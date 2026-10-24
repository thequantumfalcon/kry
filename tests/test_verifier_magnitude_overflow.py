"""Finite public fields must not turn an overflowed basis into a legal zero ratio."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from kry import kry_attest, kry_mint, kry_token


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.fixture(params=[(1.234567e308, False), (sys.float_info.max, True)],
                ids=["illegal-ratio", "legal-ratio"])
def overflow_attestation(tmp_path, request):
    receipt = kry_mint.MintReceipt.create(
        "KRY-00000001", "cache_hit", sys.float_info.max, "finite overflow fixture",
        "b" * 64, "0" * 64)
    receipt.earn_rate = 1.0000001  # within the existing published-rate tolerance
    receipt.kry_minted, expected_valid = request.param
    receipt.usd_equivalent = receipt.kry_minted * 0.000025
    block = kry_mint._v4_public_block(
        hash_version=receipt.hash_version, tokens_saved=receipt.tokens_saved, ts=receipt.ts,
        evidence_tier=receipt.evidence_tier, metered_tokens=receipt.metered_tokens,
        kry_minted=receipt.kry_minted, earn_rate=receipt.earn_rate,
        supersedes=receipt.supersedes, receipt_id=receipt.receipt_id, event_type=receipt.event_type)
    receipt.chain_hash = hashlib.sha256(
        f"{'0' * 64}:{receipt.receipt_hash}:{block}".encode()).hexdigest()
    log = tmp_path / "mint.jsonl"
    log.write_text(json.dumps(kry_mint._receipt_record(receipt)) + "\n", encoding="utf-8")
    return kry_attest.build_attestation(log).to_public_json(), expected_valid, log


@pytest.mark.parametrize("implementation", ["package", "stranger"])
def test_magnitude_overflow_checks_real_ratio(overflow_attestation, implementation):
    encoded, expected_valid, _ = overflow_attestation
    if implementation == "package":
        valid, errors = kry_attest.verify_attestation(encoded)
    else:
        spec = importlib.util.spec_from_file_location("overflow_verifier", ROOT / "scripts/kry_verify.py")
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        valid, errors = verifier.verify_attestation(json.loads(encoded))
    assert valid is expected_valid
    assert bool(errors) is not expected_valid


@pytest.mark.parametrize("implementation", ["python", pytest.param(
    "javascript", marks=pytest.mark.skipif(NODE is None, reason="Node.js is not installed"))])
def test_magnitude_overflow_cli_checks_real_ratio(tmp_path, overflow_attestation, implementation):
    encoded, expected_valid, _ = overflow_attestation
    path = tmp_path / "attestation.json"
    path.write_text(encoded, encoding="utf-8")
    command = ([sys.executable, str(ROOT / "scripts/kry_verify.py")]
               if implementation == "python" else [NODE, str(ROOT / "verifiers/js/cli.mjs")])
    result = subprocess.run([*command, str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == (0 if expected_valid else 1), result.stdout + result.stderr
    assert ("VERDICT: VALID" if expected_valid else "VERDICT: INVALID") in result.stdout
    assert "Traceback" not in result.stderr


def test_internal_chain_checks_real_ratio(overflow_attestation, monkeypatch):
    _, expected_valid, log = overflow_attestation
    monkeypatch.setattr(kry_mint, "_MINT_LOG_PATH", log)
    valid, errors = kry_mint.verify_chain()
    assert valid is expected_valid
    assert bool(errors) is not expected_valid


def test_reconciliation_requires_legal_magnitude(overflow_attestation, monkeypatch):
    _, expected_valid, log = overflow_attestation
    ledger = kry_token.get_ledger()
    ledger.save()
    original = kry_token._LEDGER_PATH.read_bytes()
    monkeypatch.setattr(kry_mint, "_MINT_LOG_PATH", log)
    result = kry_mint.reconcile_ledger_from_chain()
    assert result["reconciled"] is expected_valid
    if expected_valid:
        assert ledger.balance == sys.float_info.max
    else:
        assert result["reason"] == "mint chain tampered"
        assert kry_token._LEDGER_PATH.read_bytes() == original
        assert ledger.balance == ledger.total_earned == 0
