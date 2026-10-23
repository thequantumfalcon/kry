"""Mint failure diagnostics preserve receipt and balance outcome distinctions."""
from __future__ import annotations

import builtins
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

import pytest

from kry import kry_mint as km
from kry import kry_token as kt

ROOT = Path(__file__).resolve().parents[1]
DETAIL = "private-input-sentinel"
FAILURE = "private-exception-sentinel " + DETAIL


def mint():
    return km.mint("cache_hit", 1000, detail=DETAIL, evidence="failure-event",
                   idempotent_evidence=True)


def fail_at(monkeypatch, boundary):
    original_open, original_fsync, original_replace, original_earn = (
        builtins.open, os.fsync, os.replace, kt.earn)

    def append(path, mode="r", *args, **kwargs):
        if Path(path) == km._MINT_LOG_PATH and mode == "a":
            raise OSError(FAILURE)
        return original_open(path, mode, *args, **kwargs)

    def fsync(fd):
        if km._MINT_LOG_PATH.exists() and os.fstat(fd).st_ino == km._MINT_LOG_PATH.stat().st_ino:
            raise OSError(FAILURE)
        return original_fsync(fd)

    def replace(source, target):
        selected = km._mint_tip_path() if boundary == "checkpoint_replace" else kt._LEDGER_PATH
        if Path(target) == selected:
            # The fault under test is the balance update AFTER receipt append.
            # Initial empty-ledger persistence has its own regression coverage.
            if boundary.startswith("ledger_replace") and not km._MINT_LOG_PATH.exists():
                return original_replace(source, target)
            if boundary == "ledger_replace_after":
                original_replace(source, target)
            raise OSError(FAILURE)
        return original_replace(source, target)

    def earn(*args, **kwargs):
        if boundary == "earn_after_update":
            original_earn(*args, **kwargs)
        raise OSError(FAILURE)

    if boundary == "append_open":
        monkeypatch.setattr(builtins, "open", append)
    elif boundary == "receipt_fsync":
        monkeypatch.setattr(os, "fsync", fsync)
    elif boundary in {"earn_before_update", "earn_after_update"}:
        monkeypatch.setattr(kt, "earn", earn)
    else:
        monkeypatch.setattr(os, "replace", replace)


def persisted():
    rows = ([json.loads(line) for line in km._MINT_LOG_PATH.read_text().splitlines()]
            if km._MINT_LOG_PATH.exists() else [])
    ledger = json.loads(kt._LEDGER_PATH.read_text()) if kt._LEDGER_PATH.exists() else {}
    return rows, ledger.get("balance", 0)


def restart():
    code = """
import json
from kry import kry_mint as km, kry_token as kt
receipt = km.mint('cache_hit', 1000, detail='private-input-sentinel',
                  evidence='failure-event', idempotent_evidence=True)
rows = [json.loads(line) for line in km._MINT_LOG_PATH.read_text().splitlines()]
ledger = json.loads(kt._LEDGER_PATH.read_text()) if kt._LEDGER_PATH.exists() else {}
print(json.dumps({'returned_receipt': receipt is not None, 'rows': len(rows),
                  'balance': ledger.get('balance', 0), 'valid_chain': km.verify_chain()[0]}))
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                            text=True, timeout=20,
                            env={"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"),
                                 "KRY_DATA_DIR": str(km._MINT_LOG_PATH.parent),
                                 "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return json.loads(result.stdout)


@pytest.mark.parametrize("boundary,returned,rows,balance,diagnostic", [
    ("append_open", False, 0, 0, "mint receipt acknowledgement failed; stage=receipt_append"),
    ("receipt_fsync", False, 1, 0, "mint receipt acknowledgement failed; stage=receipt_append"),
    ("checkpoint_replace", True, 1, 1000, "tip checkpoint did not advance"),
    ("earn_before_update", True, 1, 0, "balance update was not acknowledged"),
    ("earn_after_update", True, 1, 1000, "balance update was not acknowledged"),
    ("ledger_replace_before", True, 1, 0, "KRY earn: ledger persistence unconfirmed"),
    ("ledger_replace_after", True, 1, 1000, "KRY earn: ledger persistence unconfirmed"),
])
def test_fault_outcome_and_restart(monkeypatch, caplog, boundary, returned, rows, balance, diagnostic):
    caplog.set_level(logging.WARNING)
    with monkeypatch.context() as patch:
        fail_at(patch, boundary)
        receipt = mint()
    retained, on_disk = persisted()
    assert (receipt is not None) is returned
    assert len(retained) == rows and on_disk == balance
    assert km.verify_chain()[0]
    assert restart() == {"returned_receipt": rows == 0, "rows": 1,
                         "balance": 1000 if rows == 0 else balance, "valid_chain": True}
    # Strict duplicate retries never re-earn or repair a missing balance.
    assert restart() == {"returned_receipt": False, "rows": 1,
                         "balance": 1000 if rows == 0 else balance, "valid_chain": True}
    assert diagnostic in caplog.text
    assert DETAIL not in caplog.text and "private-exception-sentinel" not in caplog.text
    if boundary.startswith("ledger_replace"):
        assert not any(record.name == "kry.mint" for record in caplog.records)


@pytest.mark.parametrize("boundary", ["ledger_replace_before", "ledger_replace_after"])
def test_spend_failure_does_not_claim_mint_chain_recovers_debit(monkeypatch, caplog, boundary):
    assert mint() is not None
    caplog.set_level(logging.WARNING)
    caplog.clear()
    with monkeypatch.context() as patch:
        fail_at(patch, boundary)
        spent = kt.spend("or/anthropic/claude-opus-4.8", 1000, detail=DETAIL)
    assert spent > 0
    retained, balance = persisted()
    assert len(retained) == 1
    assert kt.get_ledger().balance == 1000 - spent
    assert balance == (1000 if boundary == "ledger_replace_before" else 1000 - spent)
    assert "KRY spend: ledger persistence unconfirmed" in caplog.text
    assert DETAIL not in caplog.text and "private-exception-sentinel" not in caplog.text
    assert "recovers" not in caplog.text


def test_success_and_expected_refusals_do_not_emit_failure_diagnostics(caplog):
    caplog.set_level(logging.WARNING)
    assert km.mint("cache_hit", 0) is None
    receipt = mint()
    assert receipt is not None
    assert mint() is None
    assert len(persisted()[0]) == 1 and persisted()[1] == receipt.kry_minted
    assert not caplog.records
