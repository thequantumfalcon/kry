"""Accounting boundaries with separate processes and retained failure outcomes."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from kry import kry_mint as km, kry_token as kt

ROOT = Path(__file__).resolve().parents[1]


def _disk():
    return json.loads(kt._LEDGER_PATH.read_text(encoding="utf-8"))


def _other_process(code):
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                            text=True, timeout=20,
                            env={"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"),
                                 "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent),
                                 "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_reconcile_preserves_other_process_spend():
    assert km.mint("cache_hit", 1000, evidence="seed") is not None
    assert _other_process("import json; from kry.kry_token import spend; "
                          "print(json.dumps(spend('paid',500)))") == 500
    assert km.reconcile_ledger_from_chain()["reconciled"]
    disk = _disk()
    assert (disk["balance"], disk["total_spent"]) == (500, 500)
    assert [e["amount"] for e in disk["events"] if e["kind"] == "spend"] == [-500]


def test_spend_uses_other_process_current_balance():
    assert km.mint("cache_hit", 100, evidence="seed") is not None
    assert _other_process("import json; from kry.kry_token import spend; "
                          "print(json.dumps(spend('paid',100)))") == 100
    assert kt.spend("paid", 100) == 0
    disk = _disk()
    assert (disk["balance"], disk["total_spent"]) == (0, 100)
    assert sum(-e["amount"] for e in disk["events"] if e["kind"] == "spend") == 100


def test_affordability_refreshes_other_process_debits():
    assert km.mint("cache_hit", 100, evidence="seed") is not None
    _other_process("import json; from kry.kry_token import spend; "
                   "print(json.dumps(spend('paid',100)))")
    assert not kt.can_afford("paid", 1)


@pytest.mark.parametrize("restart", [False, True])
def test_missing_debit_history_blocks_recovery(restart):
    assert km.mint("cache_hit", 1000, evidence="seed") is not None
    assert kt.spend("paid", 900) == 900
    kt._LEDGER_PATH.unlink()
    if restart:
        kt._ledger_instance = None
    result = km.reconcile_ledger_from_chain()
    assert not result["reconciled"] and "debit history" in result["reason"]
    assert not kt._LEDGER_PATH.exists()
    assert not kt.can_afford("paid", 1)


def test_corrupt_debit_history_is_retained_and_blocks_recovery():
    assert km.mint("cache_hit", 1000, evidence="seed") is not None
    assert kt.spend("paid", 900) == 900
    kt._LEDGER_PATH.write_text("{damaged", encoding="utf-8")
    kt._ledger_instance = None
    assert not km.reconcile_ledger_from_chain()["reconciled"]
    assert not kt._LEDGER_PATH.exists()
    assert kt._LEDGER_PATH.with_suffix(".json.corrupt").read_text() == "{damaged"
    assert not kt.can_afford("paid", 1)
    assert kt.spend("paid", 1) == 0
    assert kt.earn(100, "cache_hit") == 0
    prior = km._MINT_LOG_PATH.read_bytes()
    assert km.mint("cache_hit", 100, evidence="new") is None
    assert km._MINT_LOG_PATH.read_bytes() == prior


def test_save_never_replaces_corruption_as_empty():
    assert kt.earn(100, "cache_hit") == 100
    kt._LEDGER_PATH.write_text("{damaged", encoding="utf-8")
    assert kt.earn(10, "cache_hit") == 0
    assert not kt._LEDGER_PATH.exists()
    assert kt._LEDGER_PATH.with_suffix(".json.corrupt").read_text() == "{damaged"
    kt._ledger_instance = None
    assert kt.earn(10, "cache_hit") == 0
    assert not kt._LEDGER_PATH.exists()


@pytest.mark.parametrize("entry", ["mint", "earn"])
def test_huge_integer_is_controlled_refusal(entry):
    if entry == "mint":
        assert km.mint("cache_hit", 10**400) is None
    else:
        assert kt.earn(10**400, "cache_hit") == 0
    assert not kt._LEDGER_PATH.exists()
    assert not km._MINT_LOG_PATH.exists()


def test_mint_refuses_overflowing_chain_aggregate():
    assert km.mint("cache_hit", 1e308, evidence="first") is not None
    prior = km._MINT_LOG_PATH.read_bytes()
    disk = kt._LEDGER_PATH.read_bytes()
    assert km.mint("cache_hit", 1e308, evidence="second") is None
    assert km._MINT_LOG_PATH.read_bytes() == prior
    assert kt._LEDGER_PATH.read_bytes() == disk
    assert math.isfinite(kt.get_ledger().balance)
    assert km.verify_chain()[0]


def test_earn_refuses_overflow_without_poisoning_cache():
    assert kt.earn(1e308, "cache_hit") == 1e308
    prior = kt._LEDGER_PATH.read_bytes()
    assert kt.earn(1e308, "cache_hit") == 0
    assert kt._LEDGER_PATH.read_bytes() == prior
    assert math.isfinite(kt.get_ledger().balance)
    kt.get_ledger().save()


def test_small_valid_receipts_conserve_balance():
    total = 0.0
    for i in range(80):
        receipt = km.mint("continuity_capsule", 1.2, evidence=f"tiny-{i}",
                          avoided_model="gpt-4o-mini", served_model="deepseek")
        assert receipt is not None
        total += receipt.kry_minted
    assert total == pytest.approx(0.0192)
    assert _disk()["balance"] == pytest.approx(total, abs=1e-14)
    assert _disk()["total_earned"] == pytest.approx(total, abs=1e-14)
    assert km.verify_chain()[0]


@pytest.mark.parametrize("after_replace", [False, True])
def test_unconfirmed_spend_is_not_reapplied_or_discarded(monkeypatch, after_replace):
    assert km.mint("cache_hit", 1000, evidence="seed") is not None
    original = os.replace

    def fail(source, target):
        if Path(target) == kt._LEDGER_PATH:
            if after_replace:
                original(source, target)
            raise OSError("constructed persistence failure")
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail)
        assert kt.spend("paid", 600) == 600
    assert kt.spend("paid", 1000) == 400
    disk = _disk()
    assert (disk["balance"], disk["total_spent"]) == (0, 1000)
    assert sorted(-e["amount"] for e in disk["events"] if e["kind"] == "spend") == [400, 600]


@pytest.mark.parametrize("after_replace", [False, True])
def test_reconcile_preserves_unconfirmed_local_spend(monkeypatch, after_replace):
    assert km.mint("cache_hit", 1000, evidence="seed") is not None
    original = os.replace

    def fail(source, target):
        if Path(target) == kt._LEDGER_PATH:
            if after_replace:
                original(source, target)
            raise OSError("constructed persistence failure")
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail)
        assert kt.spend("paid", 600) == 600
    assert km.reconcile_ledger_from_chain()["reconciled"]
    disk = _disk()
    assert (disk["balance"], disk["total_spent"]) == (400, 600)
    assert sum(-e["amount"] for e in disk["events"] if e["kind"] == "spend") == 600


def test_reconcile_does_not_race_mint_balance_update(monkeypatch):
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    original = kt.earn
    results = {}

    def pause(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    def mint():
        results["mint"] = km.mint("cache_hit", 1000, evidence="seed")

    def reconcile():
        results["reconcile"] = km.reconcile_ledger_from_chain()
        done.set()

    monkeypatch.setattr(kt, "earn", pause)
    writer = threading.Thread(target=mint)
    checker = threading.Thread(target=reconcile)
    writer.start()
    try:
        assert entered.wait(5)
        checker.start()
        completed_before_earn = done.wait(0.2)
    finally:
        release.set()
        writer.join(5)
        if checker.ident is not None:
            checker.join(5)
    assert not writer.is_alive() and not checker.is_alive()
    assert not completed_before_earn
    assert results["mint"] is not None and results["reconcile"]["reconciled"]
    assert _disk()["balance"] == 1000


def test_lost_write_ack_is_not_reapplied_after_event_history_rolls_over(monkeypatch):
    original = os.replace

    def replace_then_fail(source, target):
        original(source, target)
        if Path(target) == kt._LEDGER_PATH:
            raise OSError("constructed lost acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace_then_fail)
        assert kt.earn(100, "cache_hit") == 100
    assert _other_process("import json; from kry.kry_token import earn; "
                          "print(json.dumps(sum(earn(1,'cache_hit') for _ in range(501))))") == 501
    assert kt.earn(50, "cache_hit") == 50
    assert _disk()["balance"] == 651
    assert _disk()["total_earned"] == 651


def test_large_finite_accounting_ratio_does_not_overflow():
    ledger = kt.KRYLedger(balance=0, total_earned=1e308, total_spent=1e308)
    assert ledger.efficiency_ratio == 0.5


def test_huge_output_count_has_controlled_numeric_error():
    with pytest.raises(ValueError, match="finite"):
        kt.spend_cost("paid", 10**400)
    with pytest.raises(ValueError, match="finite"):
        kt.spend_cost("or/anthropic/claude-opus-4.7-fast", 1e308)


def test_missing_spent_field_is_not_assumed_zero():
    kt._LEDGER_PATH.write_text(json.dumps({"balance": 100, "total_earned": 100}))
    assert not kt.can_afford("paid", 1)
    assert kt.spend("paid", 1) == 0
    assert not kt._LEDGER_PATH.exists()
    assert kt._LEDGER_PATH.with_suffix(".json.corrupt").exists()


def test_two_processes_share_one_spending_decision():
    assert kt.earn(100, "cache_hit") == 100
    code = ("import json,sys; from kry.kry_token import get_ledger,spend; "
            "get_ledger(); print('ready',flush=True); sys.stdin.readline(); "
            "print(json.dumps(spend('paid',100)),flush=True)")
    workers = [subprocess.Popen([sys.executable, "-B", "-c", code], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env={"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"),
                                     "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent)})
               for _ in range(2)]
    try:
        for worker in workers:
            assert worker.stdout.readline().strip() == "ready"
        for worker in workers:
            worker.stdin.write("go\n")
            worker.stdin.flush()
        results = []
        for worker in workers:
            output, errors = worker.communicate(timeout=20)
            assert worker.returncode == 0, errors
            results.append(json.loads(output))
        assert sorted(results) == [0, 100]
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=5)
    disk = _disk()
    assert disk["balance"] == 0 and disk["total_spent"] == 100
    assert sum(-e["amount"] for e in disk["events"] if e["kind"] == "spend") == 100


def test_cached_empty_ledger_cannot_reset_other_process_history():
    assert kt.get_ledger().balance == 0
    assert _other_process("import json; from kry.kry_mint import mint; "
                          "from kry.kry_token import spend; mint('cache_hit',1000,evidence='seed'); "
                          "print(json.dumps(spend('paid',900)))") == 900
    kt._LEDGER_PATH.unlink()
    assert kt.earn(10, "cache_hit") == 0
    assert not kt._LEDGER_PATH.exists()
    assert not km.reconcile_ledger_from_chain()["reconciled"]
    assert not kt.can_afford("paid", 1)


@pytest.mark.parametrize("after_replace", [False, True])
def test_initial_debit_history_must_persist_before_first_receipt(monkeypatch, after_replace):
    original = os.replace

    def fail(source, target):
        if Path(target) == kt._LEDGER_PATH:
            assert not km._MINT_LOG_PATH.exists()
            if after_replace:
                original(source, target)
            raise OSError("constructed initialization failure")
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail)
        assert km.mint("cache_hit", 1000, evidence="first") is None
    assert not km._MINT_LOG_PATH.exists()
    assert not km._DECAY_STATE_PATH.exists()
    assert kt.get_ledger().balance == 0
    assert km.mint("cache_hit", 1000, evidence="first") is not None
    assert _disk()["balance"] == 1000


def test_failed_quarantine_preserves_corrupt_bytes_and_blocks_credit(monkeypatch):
    kt._LEDGER_PATH.write_text("{damaged")
    original = os.replace

    def fail(source, target):
        if Path(source) == kt._LEDGER_PATH:
            raise PermissionError("constructed retention failure")
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail)
        assert kt.earn(100, "cache_hit") == 0
        assert kt.spend("paid", 1) == 0
    assert kt._LEDGER_PATH.read_text() == "{damaged"
