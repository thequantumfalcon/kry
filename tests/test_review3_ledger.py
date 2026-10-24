"""Durable operation outcomes, retained incidents, and actual legacy upgrades."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from kry import kry_mint as km, kry_token as kt

ROOT = Path(__file__).resolve().parents[1]


def disk():
    return json.loads(kt._LEDGER_PATH.read_text())


def other_process(code, *, expected=0):
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                            text=True, timeout=20,
                            env={"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"),
                                 "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent),
                                 "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == expected, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else None


def fail_replace(monkeypatch, *, after=False, unreadable=False):
    original_replace, original_read = os.replace, Path.read_text
    replaced = False

    def replace(source, target):
        nonlocal replaced
        if Path(target) == kt._LEDGER_PATH:
            if after:
                original_replace(source, target)
                replaced = True
            raise OSError("synthetic ledger write failure")
        return original_replace(source, target)

    def read(path, *args, **kwargs):
        if unreadable and replaced and path == kt._LEDGER_PATH:
            raise OSError(errno.EIO, "synthetic unreadable acknowledgement")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(Path, "read_text", read)


@pytest.mark.parametrize("after", [False, True])
def test_failed_spend_competing_process_and_restart(monkeypatch, after):
    assert kt.earn(100, "cache_hit") == 100
    with monkeypatch.context() as patch:
        fail_replace(patch, after=after)
        assert kt.spend("paid", 100) == (100 if after else 0)
    spent_elsewhere = other_process("import json; from kry.kry_token import spend; "
                                   "print(json.dumps(spend('paid',100)))")
    assert spent_elsewhere == (0 if after else 100)
    assert kt.spend("paid", 1) == 0
    assert disk()["total_spent"] == 100
    assert sum(-e["amount"] for e in disk()["events"] if e["kind"] == "spend") == 100
    kt._ledger_instance = None
    assert not kt.can_afford("paid", 1)


def test_failed_spend_retry_is_one_debit(monkeypatch):
    assert kt.earn(100, "cache_hit") == 100
    with monkeypatch.context() as patch:
        fail_replace(patch)
        assert kt.spend("paid", 30) == 0
    assert kt.spend("paid", 30) == 30
    assert disk()["total_spent"] == 30


def test_status_does_not_commit_or_discard_direct_save_delta(monkeypatch):
    assert kt.earn(100, "cache_hit") == 100
    ledger = kt.get_ledger()
    ledger.balance -= 30
    ledger.total_spent += 30
    with monkeypatch.context() as patch:
        fail_replace(patch)
        with pytest.raises(OSError):
            ledger.save()
    retained = kt._LEDGER_PATH.read_bytes()
    assert kt.status()["balance_kry"] == 100
    assert kt.supply()["circulating_kry"] == 100
    assert kt._LEDGER_PATH.read_bytes() == retained
    assert ledger.balance == 70
    ledger.save()
    assert disk()["balance"] == 70


def test_failed_mint_credit_competing_reconcile(monkeypatch):
    assert km.mint("cache_hit", 100, evidence="seed") is not None
    with monkeypatch.context() as patch:
        fail_replace(patch)
        assert km.mint("cache_hit", 50, evidence="failed-credit") is not None
    assert kt.get_ledger().balance == disk()["balance"] == 100
    assert other_process("import json; from kry.kry_mint import reconcile_ledger_from_chain; "
                         "print(json.dumps(reconcile_ledger_from_chain()))")["reconciled"]
    assert kt.spend("paid", 1) == 1
    assert (disk()["total_earned"], disk()["balance"]) == (150, 149)
    assert km.verify_chain()[0]
    kt._ledger_instance = None
    assert km.reconcile_ledger_from_chain()["reconciled"]
    assert disk()["balance"] == 149


def test_failed_reconciliation_does_not_leave_pending_delta(monkeypatch):
    assert km.mint("cache_hit", 100, evidence="seed") is not None
    with monkeypatch.context() as patch:
        fail_replace(patch)
        assert km.mint("cache_hit", 50, evidence="pending-receipt") is not None
        assert not km.reconcile_ledger_from_chain()["reconciled"]
    assert other_process("import json; from kry.kry_mint import reconcile_ledger_from_chain; "
                         "print(json.dumps(reconcile_ledger_from_chain()))")["reconciled"]
    assert kt.spend("paid", 1) == 1
    assert disk()["total_earned"] == 150


@pytest.mark.parametrize("boundary", ["temp_fsync", "intent_create", "intent_fsync"])
def test_precommit_boundaries_do_not_credit_or_leave_pending(monkeypatch, boundary):
    assert kt.earn(100, "cache_hit") == 100
    original_open, original_fsync = Path.open, os.fsync
    fsyncs = 0

    def open_file(path, *args, **kwargs):
        if boundary == "intent_create" and ".pending." in path.name:
            raise OSError("synthetic intent creation failure")
        return original_open(path, *args, **kwargs)

    def fsync(fd):
        nonlocal fsyncs
        fsyncs += 1
        if fsyncs == (1 if boundary == "temp_fsync" else 2):
            raise OSError("synthetic flush failure")
        return original_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", open_file)
        if boundary != "intent_create":
            patch.setattr(os, "fsync", fsync)
        assert kt.earn(25, "cache_hit") == 0
    assert kt.get_ledger().balance == disk()["balance"] == 100
    assert not list(kt._LEDGER_PATH.parent.glob("*.pending.*"))
    assert kt.earn(1, "cache_hit") == 1
    assert disk()["total_earned"] == 101


def test_uncertain_write_blocks_competitor_and_restart(monkeypatch):
    assert kt.earn(100, "cache_hit") == 100
    with monkeypatch.context() as patch:
        fail_replace(patch, after=True, unreadable=True)
        with pytest.raises(kt.LedgerPersistenceError, match="uncertain"):
            kt.spend("paid", 30)
    incidents = kt._ledger_incidents()
    assert incidents and disk()["total_spent"] == 30
    assert not kt.can_afford("paid", 1)
    assert kt.status()["available"] is False
    assert other_process("import json; from kry.kry_token import spend; "
                         "print(json.dumps(spend('paid',30)))") == 0
    kt._ledger_instance = None
    assert kt.status()["balance_kry"] is None
    assert kt.earn(1, "cache_hit") == 0
    # The operator has reviewed the retained committed ledger before acknowledging.
    kt.acknowledge_ledger_recovery(reason="reviewed retained debit and its write intent")
    assert kt._ledger_incidents() == incidents
    assert kt.spend("paid", 1) == 1
    assert disk()["total_spent"] == 31
    kt._ledger_instance = None
    assert kt.can_afford("paid", 1)


def test_invalid_utf8_acknowledgement_is_uncertain_not_a_failed_debit(monkeypatch):
    assert kt.earn(100, "cache_hit") == 100
    original = os.replace

    def damaged_readback(source, target):
        original(source, target)
        if Path(target) == kt._LEDGER_PATH:
            kt._LEDGER_PATH.write_bytes(b"\xff")
            raise OSError("synthetic lost acknowledgement with damaged readback")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", damaged_readback)
        with pytest.raises(kt.LedgerPersistenceError, match="uncertain"):
            kt.spend("paid", 30)
    assert kt._ledger_incidents()
    kt._ledger_instance = None
    assert kt.status()["available"] is False
    assert kt._LEDGER_PATH.with_suffix(".json.corrupt").read_bytes() == b"\xff"


def test_marker_clear_failure_keeps_confirmed_debit(monkeypatch):
    assert kt.earn(100, "cache_hit") == 100
    original = Path.unlink

    def fail(path, *args, **kwargs):
        if ".pending." in path.name:
            raise OSError("synthetic marker cleanup failure")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        assert kt.spend("paid", 30) == 30
    assert disk()["total_spent"] == 30
    kt._ledger_instance = None
    assert not kt.can_afford("paid", 1)
    kt.acknowledge_ledger_recovery(reason="confirmed debit; reviewed failed marker cleanup")
    assert kt.spend("paid", 1) == 1
    assert disk()["total_spent"] == 31


@pytest.mark.parametrize("after_replace", [False, True])
def test_directory_flush_failure_has_explicit_outcome(monkeypatch, after_replace):
    assert kt.earn(100, "cache_hit") == 100
    original = kt._sync_ledger_directory
    calls = 0

    def fail():
        nonlocal calls
        calls += 1
        if calls == (2 if after_replace else 1):
            raise OSError("synthetic directory flush failure")
        return original()

    with monkeypatch.context() as patch:
        patch.setattr(kt, "_sync_ledger_directory", fail)
        if after_replace:
            with pytest.raises(kt.LedgerPersistenceError, match="uncertain"):
                kt.spend("paid", 30)
        else:
            assert kt.spend("paid", 30) == 0
    assert disk()["total_spent"] == (30 if after_replace else 0)
    kt._ledger_instance = None
    assert kt.can_afford("paid", 1) is (not after_replace)
    if after_replace:
        assert kt._ledger_incidents()


@pytest.mark.parametrize("after", [False, True])
def test_crash_with_write_intent_requires_review(after):
    assert kt.earn(100, "cache_hit") == 100
    code = f"""
import os
from pathlib import Path
from kry import kry_token as kt
original = os.replace
def crash(source, target):
    if Path(target) == kt._LEDGER_PATH:
        if {after!r}:
            original(source, target)
        os._exit(23)
    return original(source, target)
os.replace = crash
kt.spend('paid',30)
"""
    other_process(code, expected=23)
    assert disk()["total_spent"] == (30 if after else 0)
    assert not kt.can_afford("paid", 1)
    kt._ledger_instance = None
    assert kt.status()["available"] is False
    # Both matching and mismatching target hashes remain evidence for deliberate review.
    kt.acknowledge_ledger_recovery(reason="reviewed crash boundary and actual retained debit")
    assert kt.spend("paid", 1) == 1
    assert disk()["total_spent"] == (31 if after else 1)


@pytest.mark.parametrize("cold", [False, True])
def test_transient_read_preserves_bytes_and_reports_unavailable(monkeypatch, cold):
    assert km.mint("cache_hit", 100, evidence="seed") is not None
    assert kt.spend("paid", 60) == 60
    retained = kt._LEDGER_PATH.read_bytes()
    if cold:
        kt._ledger_instance = None
    original = Path.read_text

    def fail(path, *args, **kwargs):
        if path == kt._LEDGER_PATH:
            raise OSError(errno.EMFILE, "synthetic read failure")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", fail)
        status = kt.status()
        assert status["available"] is False and status["balance_kry"] is None
        assert not kt.can_afford("paid", 1)
        for report in (kt.supply, kt.retained_dollars, kt.reconciled_savings):
            with pytest.raises(OSError):
                report()
    assert kt._LEDGER_PATH.read_bytes() == retained
    assert not kt._ledger_incidents()
    assert kt.status()["available"] is True
    assert kt.spend("paid", 1) == 1
    assert disk()["total_spent"] == 61


def install_legacy(name):
    # These bytes were generated by unmodified 8d236bf public APIs, not invented
    # blank/zero snapshots. The source revision is retained in the fixture.
    fixture = json.loads((ROOT / "tests/fixtures/review3_legacy_ledgers.json").read_text())
    assert fixture["source_commit"] == "8d236bfae9ac32ab87cbe859b88d8c46f2c7d381"
    for filename, content in fixture["cases"][name].items():
        (kt._LEDGER_PATH.parent / filename).write_text(content)
    kt._ledger_instance = None
    km._decay_loaded = False
    assert km.verify_chain()[0]


def test_invalid_utf8_is_retained_as_corrupt():
    assert kt.earn(100, "cache_hit") == 100
    malformed = b"{\xff}"
    kt._LEDGER_PATH.write_bytes(malformed)
    assert not kt.can_afford("paid", 1)
    assert not kt._LEDGER_PATH.exists()
    assert kt._LEDGER_PATH.with_suffix(".json.corrupt").read_bytes() == malformed
    assert kt.status()["available"] is False


@pytest.mark.parametrize("cold", [False, True])
def test_deep_json_is_controlled_and_retained(cold):
    assert kt.earn(100, "cache_hit") == 100
    nested = ("[" * 2000 + "0" + "]" * 2000).encode()
    kt._LEDGER_PATH.write_bytes(nested)
    if cold:
        kt._ledger_instance = None
    result = kt.status()
    assert result["available"] is False and result["balance_kry"] is None
    assert not kt._LEDGER_PATH.exists()
    assert kt._LEDGER_PATH.with_suffix(".json.corrupt").read_bytes() == nested
    assert kt.spend("paid", 1) == 0


def test_actual_legacy_blank_requires_reviewed_debit_restore():
    install_legacy("blank_after_quarantine")
    incidents = kt._ledger_incidents()
    assert disk()["total_spent"] == 0
    assert kt.spend("paid", 1) == 0
    assert kt.earn(10, "cache_hit") == 0
    assert km.mint("cache_hit", 10, evidence="upgrade") is None
    assert not km.reconcile_ledger_from_chain()["reconciled"]
    restored = {"balance": 40, "total_earned": 100, "total_spent": 60, "events": []}
    kt._LEDGER_PATH.write_text(json.dumps(restored))
    assert not kt.can_afford("paid", 1)
    kt.acknowledge_ledger_recovery(reason="restored complete retained debit records through incident")
    assert kt._ledger_incidents() == incidents
    assert km.mint("cache_hit", 10, evidence="upgrade") is not None
    for _ in range(3):
        assert km.reconcile_ledger_from_chain()["reconciled"]
    assert (disk()["balance"], disk()["total_spent"]) == (50, 60)
    kt._ledger_instance = None
    assert kt.can_afford("paid", 50)


@pytest.mark.parametrize("fixture", ["zero_only", "zero_with_lost_debits"])
def test_actual_legacy_zero_chain_does_not_prove_no_debits(fixture):
    install_legacy(fixture)
    assert not kt._LEDGER_PATH.exists()
    assert kt.earn(1, "cache_hit") == 0
    assert km.mint("cache_hit", 1, evidence="upgrade") is None
    assert not km.reconcile_ledger_from_chain()["reconciled"]
    assert not kt._LEDGER_PATH.exists()


def test_clean_legacy_zero_history_has_explicit_migration():
    install_legacy("zero_only")
    # An operator establishes no prior direct credits/debits from retained records.
    kt._LEDGER_PATH.write_text(json.dumps({"balance": 0, "total_earned": 0,
                                          "total_spent": 0, "events": []}))
    kt.acknowledge_ledger_recovery(reason="reviewed clean legacy zero-only installation")
    assert km.mint("cache_hit", 10, evidence="after-migration") is not None
    assert km.reconcile_ledger_from_chain()["reconciled"]
    assert disk()["balance"] == 10


def test_recovery_ack_does_not_cover_new_incident():
    assert kt.earn(100, "cache_hit") == 100
    incident = kt._LEDGER_PATH.with_suffix(".json.corrupt")
    incident.write_text("retained synthetic incident")
    assert not kt.can_afford("paid", 1)
    kt.acknowledge_ledger_recovery(reason="independently reviewed all debit history")
    assert kt.can_afford("paid", 1)
    incident.write_text("changed synthetic incident")
    assert not kt.can_afford("paid", 1)
    kt._ledger_instance = None
    assert kt.spend("paid", 1) == 0


def test_drained_roundoff_reconciles_without_creating_credit():
    assert km.mint("compression", 23, evidence="fractional") is not None
    for _ in range(3):
        assert kt.spend("ghm", 20) == 0.1
    kt.spend("paid", 100)
    spent = disk()["total_spent"]
    assert spent == 13.8
    for _ in range(3):
        assert km.reconcile_ledger_from_chain()["reconciled"]
        assert disk()["balance"] == 0
        assert disk()["total_spent"] == spent
    assert kt.spend("paid", 1) == 0


@pytest.mark.parametrize("earned,extra", [(100.0, 1e-10), (1e12, 0.01)])
def test_reconcile_does_not_forgive_material_excess_debits(earned, extra):
    assert km.mint("cache_hit", earned, evidence="seed") is not None
    raw = disk()
    raw["balance"], raw["total_spent"] = 0, earned + extra
    kt._LEDGER_PATH.write_text(json.dumps(raw))
    kt._ledger_instance = None
    retained = kt._LEDGER_PATH.read_bytes()
    assert not km.reconcile_ledger_from_chain()["reconciled"]
    assert kt._LEDGER_PATH.read_bytes() == retained
