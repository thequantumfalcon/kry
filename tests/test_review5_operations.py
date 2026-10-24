"""Durable caller operation identity for local accounting, not external settlement."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from kry import kry_mint as km, kry_token as kt

ROOT = Path(__file__).resolve().parents[1]


def ledger_path():
    journal = kt._LEDGER_PATH.with_name("kry_ledger.v2.json")
    return journal if journal.exists() else kt._LEDGER_PATH


def disk():
    return json.loads(ledger_path().read_text())


def other(code):
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], check=True, capture_output=True,
        text=True, timeout=30, env={**os.environ, "PYTHONPATH": str(ROOT / "src"),
            "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent), "PYTHONDONTWRITEBYTECODE": "1"})
    return json.loads(result.stdout)


def test_spend_replay_is_durable_and_does_not_reprice(monkeypatch):
    assert kt.earn(300, "cache_hit") == 300
    assert kt.spend("paid", 100, "logical request", op_id="request-1") == 100
    before = ledger_path().read_bytes()
    record = kt.ledger_operation("request-1")
    assert record["kind"] == "spend" and record["amount"] == 100
    assert len(record["params_sha256"]) == 64 and record["ts"] > 0
    monkeypatch.setattr(kt, "spend_cost", lambda *args: pytest.fail("Replay must not reprice"))
    assert kt.spend("paid", 100.0, "logical request", op_id="request-1") == 100
    assert ledger_path().read_bytes() == before
    assert other("import json; from kry import kry_token as k; "
                 "print(json.dumps(k.spend('paid',100,'logical request',op_id='request-1')))") == 100
    assert ledger_path().read_bytes() == before


def test_direct_partial_result_survives_later_earnings():
    assert kt.earn(40, "cache_hit") == 40
    assert kt.debit_kry(100, op_id="partial") == 40
    assert kt.earn(300, "cache_hit") == 300
    assert kt.debit_kry(100.0, op_id="partial") == 40
    assert kt.get_ledger().balance == 300
    assert kt.ledger_operation("partial")["amount"] == 40


@pytest.mark.parametrize("model,tokens,detail", [
    ("different", 100, "same"), ("paid", 101, "same"), ("paid", 100, "different")])
def test_same_key_different_spend_parameters_refused(model, tokens, detail):
    kt.earn(500, "cache_hit")
    kt.spend("paid", 100, "same", op_id="bound")
    before = ledger_path().read_bytes()
    with pytest.raises(ValueError, match="parameters"):
        kt.spend(model, tokens, detail, op_id="bound")
    assert ledger_path().read_bytes() == before


def test_same_key_cannot_change_operation_kind():
    kt.earn(500, "cache_hit")
    kt.debit_kry(100, op_id="bound")
    before = ledger_path().read_bytes()
    with pytest.raises(ValueError, match="parameters"):
        kt.spend("paid", 100, op_id="bound")
    with pytest.raises(ValueError, match="parameters"):
        kt.debit_kry(101, op_id="bound")
    assert ledger_path().read_bytes() == before


@pytest.mark.parametrize("case", ["empty", "free", "zero"])
def test_zero_is_a_durable_outcome(case):
    def call():
        if case == "free":
            return kt.spend("local/model", 100, op_id="zero")
        return kt.debit_kry(0 if case == "zero" else 100, op_id="zero")
    assert call() == 0
    assert kt.ledger_operation("zero")["amount"] == 0
    kt.earn(300, "cache_hit")
    assert call() == 0
    assert kt.get_ledger().balance == 300
    assert kt.ledger_operation("absent") is None


@pytest.mark.parametrize("amount", [-1, True, False, float("nan"), float("inf"), 10**400])
def test_invalid_direct_amount_has_no_side_effect(amount):
    kt.earn(300, "cache_hit")
    before = kt._LEDGER_PATH.read_bytes()
    with pytest.raises(ValueError):
        kt.debit_kry(amount, op_id="invalid")
    assert kt._LEDGER_PATH.read_bytes() == before


@pytest.mark.parametrize("op_id", [None, "", "  ", "a:b", "a,b", 1, []])
def test_invalid_operation_id_has_no_side_effect(op_id):
    kt.earn(300, "cache_hit")
    before = kt._LEDGER_PATH.read_bytes()
    with pytest.raises(ValueError):
        kt.debit_kry(1, op_id=op_id)
    assert kt._LEDGER_PATH.read_bytes() == before


@pytest.mark.parametrize("field", ["model", "detail"])
def test_keyed_string_parameters_are_validated(field):
    kt.earn(300, "cache_hit")
    args = {"model": "paid", "output_tokens": 1, "detail": "", "op_id": "invalid"}
    args[field] = None
    before = kt._LEDGER_PATH.read_bytes()
    with pytest.raises(ValueError):
        kt.spend(**args)
    assert kt._LEDGER_PATH.read_bytes() == before


def test_legacy_query_does_not_claim_known_absence():
    kt.earn(300, "cache_hit")
    before = kt._LEDGER_PATH.read_bytes()
    with pytest.raises(ValueError, match="journal"):
        kt.ledger_operation("absent")
    assert kt._LEDGER_PATH.read_bytes() == before


def test_operations_survive_event_retention_and_return_copies():
    kt.earn(100, "cache_hit")
    kt.debit_kry(10, op_id="retained")
    for i in range(501):
        assert kt.earn(1, "cache_hit", str(i)) == 1
    raw = disk()
    assert len(raw["accounting"]["events"]) == 500
    assert not any(event["kind"] == "spend" for event in raw["accounting"]["events"])
    record = kt.ledger_operation("retained")
    record["amount"] = 99
    assert kt.ledger_operation("retained")["amount"] == 10
    assert kt.debit_kry(10, op_id="retained") == 10
    assert kt.get_ledger().total_spent == 10


def test_concurrent_processes_record_one_operation():
    kt.earn(300, "cache_hit")
    code = ("import json; from kry import kry_token as k; input(); "
            "print(json.dumps(k.debit_kry(100,op_id='shared')),flush=True)")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
           "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent), "PYTHONDONTWRITEBYTECODE": "1"}
    children = [subprocess.Popen([sys.executable, "-B", "-c", code], env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True) for _ in range(4)]
    try:
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        answers = []
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stderr
            answers.append(json.loads(stdout))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    assert answers == [100] * 4
    assert disk()["accounting"]["total_spent"] == 100
    assert list(disk()["ops"]) == ["shared"]


def test_stale_direct_save_preserves_other_process_operations():
    kt.earn(300, "cache_hit")
    kt.debit_kry(10, op_id="local")
    ledger = kt.get_ledger()
    assert other("import json; from kry import kry_token as k; "
                 "print(json.dumps(k.debit_kry(20,op_id='child')))") == 20
    ledger.balance += 1
    ledger.total_earned += 1
    ledger.save()
    assert set(disk()["ops"]) == {"local", "child"}
    assert kt.ledger_operation("child")["amount"] == 20
    assert disk()["accounting"]["total_spent"] == 30


def test_reconciliation_and_ack_preserve_operations():
    assert km.mint("cache_hit", 300, evidence="seed") is not None
    kt.debit_kry(100, op_id="preserved")
    record = kt.ledger_operation("preserved")
    assert km.reconcile_ledger_from_chain()["reconciled"]
    incident = kt._LEDGER_PATH.with_name(kt._LEDGER_PATH.name + ".pending.synthetic")
    incident.write_text('{"review":"synthetic retained incident"}')
    with pytest.raises(kt.LedgerPersistenceError):
        kt.ledger_operation("preserved")
    with pytest.raises(kt.LedgerPersistenceError):
        kt.debit_kry(100, op_id="preserved")
    kt.acknowledge_ledger_recovery(reason="synthetic complete v2 restoration reviewed")
    assert kt.ledger_operation("preserved") == record
    assert kt.debit_kry(100, op_id="preserved") == 100
    assert disk()["accounting"]["total_spent"] == 100


@pytest.mark.parametrize("cancel", [KeyboardInterrupt, asyncio.CancelledError])
def test_post_unlink_cancellation_replays_once_after_restart(monkeypatch, cancel):
    kt.earn(300, "cache_hit")
    original = Path.unlink
    error = cancel("after actual cleanup")
    fired = False

    def remove(path, *args, **kwargs):
        nonlocal fired
        result = original(path, *args, **kwargs)
        if ".pending." in path.name and not fired:
            fired = True
            raise error
        return result

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", remove)
        with pytest.raises(cancel) as caught:
            kt.debit_kry(100, op_id="cancelled")
    assert caught.value is error and not kt._ledger_incidents()
    assert other("import json; from kry import kry_token as k; "
                 "print(json.dumps(k.debit_kry(100,op_id='cancelled')))") == 100
    assert disk()["accounting"]["total_spent"] == 100


@pytest.mark.parametrize("after_replace", [False, True])
def test_keyed_replace_failure_is_unreported_or_exactly_replayed(monkeypatch, after_replace):
    kt.earn(300, "cache_hit")
    assert kt.debit_kry(0, op_id="activate") == 0
    original = os.replace

    def replace(source, target):
        if Path(target) == kt._LEDGER_PATH.with_name("kry_ledger.v2.json"):
            if after_replace:
                original(source, target)
            raise OSError("synthetic lost replace acknowledgement")
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace)
        if after_replace:
            assert kt.debit_kry(100, op_id="write") == 100
        else:
            with pytest.raises(kt.LedgerPersistenceError):
                kt.debit_kry(100, op_id="write")
    assert kt.debit_kry(100, op_id="write") == 100
    assert disk()["accounting"]["total_spent"] == 100


def test_unrepresentable_keyed_debit_is_rejected_before_writing():
    kt.earn(1e20, "cache_hit")
    before = kt._LEDGER_PATH.read_bytes()
    with pytest.raises(ValueError, match="represent"):
        kt.debit_kry(1, op_id="tiny")
    assert kt._LEDGER_PATH.read_bytes() == before


def test_unkeyed_contract_and_format_stay_legacy(monkeypatch):
    kt.earn(300, "cache_hit")
    assert kt.spend("paid", 100) == 100
    assert "ledger_format" not in disk()
    original = os.replace

    def replace(source, target):
        if Path(target) == kt._LEDGER_PATH:
            raise OSError("synthetic precommit failure")
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace)
        assert kt.spend("paid", 100) == 0
    assert disk()["total_spent"] == 100


@pytest.mark.parametrize("damage", ["version-bool", "version-unknown", "missing-ops", "ops-list",
    "bad-id", "negative-amount", "amount-bool", "amount-exceeds-spent", "bad-hash", "bad-kind",
    "nonfinite-time", "extra-field"])
def test_invalid_v2_cannot_be_acknowledged_or_rewritten(damage):
    kt.earn(300, "cache_hit")
    kt.debit_kry(10, op_id="valid")
    raw = disk()
    entry = raw["ops"]["valid"]
    if damage == "version-bool":
        raw["ledger_format"] = True
    elif damage == "version-unknown":
        raw["ledger_format"] = 3
    elif damage == "missing-ops":
        del raw["ops"]
    elif damage == "ops-list":
        raw["ops"] = []
    elif damage == "bad-id":
        raw["ops"]["invalid:id"] = raw["ops"].pop("valid")
    elif damage == "negative-amount":
        entry["amount"] = -1
    elif damage == "amount-bool":
        entry["amount"] = True
    elif damage == "amount-exceeds-spent":
        entry["amount"] = 11
    elif damage == "bad-hash":
        entry["params_sha256"] = "not-a-hash"
    elif damage == "bad-kind":
        entry["kind"] = "earn"
    elif damage == "nonfinite-time":
        entry["ts"] = float("inf")
    elif damage == "extra-field":
        entry["unexpected"] = 1
    damaged = json.dumps(raw).encode()
    ledger_path().write_bytes(damaged)
    with pytest.raises((ValueError, KeyError, TypeError)):
        kt.acknowledge_ledger_recovery(reason="invalid schema must not be accepted")
    assert ledger_path().read_bytes() == damaged
    kt._ledger_instance = None
    with pytest.raises(kt.LedgerPersistenceError):
        kt.debit_kry(1, op_id="new")
    assert any(path.read_bytes() == damaged for path in kt._LEDGER_PATH.parent.glob("*.corrupt*"))


def test_v2_envelope_omits_legacy_required_scalar_roots():
    kt.earn(300, "cache_hit")
    kt.debit_kry(10, op_id="guard")
    raw = disk()
    assert set(raw) == {"ledger_format", "accounting", "ops"}
    assert raw["ledger_format"] == 2 and type(raw["ledger_format"]) is int
    # The v0.1.14 adoption/ACK contract requires these scalar keys at the root.
    for name in ["balance", "total_earned", "total_spent"]:
        with pytest.raises(KeyError):
            copy.deepcopy(raw)[name]


@pytest.mark.parametrize("api", ["spend", "debit_kry"])
def test_keyed_numeric_identity_rejects_lossy_integer_conversion(api):
    kt.earn(1e20, "cache_hit")
    def call(number):
        if api == "spend":
            return kt.spend("paid", number, op_id="precision")
        return kt.debit_kry(number, op_id="precision")
    assert call(2**53) == 2**53
    before = ledger_path().read_bytes()
    with pytest.raises(ValueError, match="representable"):
        call(2**53 + 1)
    assert ledger_path().read_bytes() == before
    assert call(float(2**53)) == 2**53


def test_keyed_zero_does_not_clamp_a_negative_restored_balance():
    kt.earn(300, "cache_hit")
    raw = disk()
    raw["balance"], raw["total_spent"] = -1, 301
    kt._LEDGER_PATH.write_text(json.dumps(raw))
    kt._ledger_instance = None
    before = kt._LEDGER_PATH.read_bytes()
    with pytest.raises(ValueError, match="negative"):
        kt.debit_kry(0, op_id="zero")
    assert kt._LEDGER_PATH.read_bytes() == before


def test_keyed_total_spent_overflow_is_not_written():
    ledger = kt.get_ledger()
    ledger.balance = ledger.total_earned = ledger.total_spent = 1e308
    ledger.save()
    before = kt._LEDGER_PATH.read_bytes()
    with pytest.raises(ValueError, match="finite"):
        kt.debit_kry(1e308, op_id="overflow")
    assert kt._LEDGER_PATH.read_bytes() == before


def test_duplicate_operation_id_cannot_be_silently_replaced_during_ack():
    kt.earn(300, "cache_hit")
    kt.debit_kry(10, op_id="duplicate")
    text = ledger_path().read_text()
    record = json.dumps(disk()["ops"]["duplicate"])
    text = text.replace('"ops": {', '"ops": {"duplicate": ' + record + ',')
    ledger_path().write_text(text)
    before = ledger_path().read_bytes()
    with pytest.raises(ValueError, match="duplicate"):
        kt.acknowledge_ledger_recovery(reason="duplicate record is not reviewed history")
    assert ledger_path().read_bytes() == before


def test_restored_lossy_operation_amount_cannot_pass_rounded_spent_bound():
    kt.earn(1e20, "cache_hit")
    kt.debit_kry(2**53, op_id="restored")
    raw = disk()
    raw["ops"]["restored"]["amount"] = 2**53 + 1
    assert raw["ops"]["restored"]["amount"] > raw["accounting"]["total_spent"]
    damaged = json.dumps(raw).encode()
    ledger_path().write_bytes(damaged)
    with pytest.raises(ValueError, match="representable"):
        kt.acknowledge_ledger_recovery(reason="restored operation must obey its exact amount bound")
    assert ledger_path().read_bytes() == damaged
    kt._ledger_instance = None
    with pytest.raises(kt.LedgerPersistenceError):
        kt.ledger_operation("restored")
    with pytest.raises(kt.LedgerPersistenceError):
        kt.debit_kry(2**53, op_id="restored")
    assert any(path.read_bytes() == damaged for path in kt._LEDGER_PATH.parent.glob("*.corrupt*"))


def test_operation_amount_is_compared_to_original_total_spent_before_rounding():
    kt.earn(1e20, "cache_hit")
    kt.debit_kry(2**53 + 4, op_id="restored")
    raw = disk()
    raw["accounting"]["total_spent"] = 2**53 + 3
    assert raw["ops"]["restored"]["amount"] > raw["accounting"]["total_spent"]
    damaged = json.dumps(raw).encode()
    ledger_path().write_bytes(damaged)
    with pytest.raises(ValueError, match="exceeds"):
        kt.acknowledge_ledger_recovery(reason="rounded header must not enlarge the operation bound")
    assert ledger_path().read_bytes() == damaged
    kt._ledger_instance = None
    with pytest.raises(kt.LedgerPersistenceError):
        kt.ledger_operation("restored")
    assert any(path.read_bytes() == damaged for path in kt._LEDGER_PATH.parent.glob("*.corrupt*"))


PRECOMMIT_BOUNDARIES = ["temp_create", "temp_fsync", "intent_create", "intent_fsync",
                        "intent_directory", "replace_before"]
POSTCOMMIT_BOUNDARIES = ["replace_after", "target_directory_before", "target_directory_after",
                         "adopt_before", "adopt_after", "unlink_before", "unlink_after"]


def fault_at(patch, boundary, error):
    """One failure at a real persistence boundary; all other calls perform real I/O."""
    import tempfile
    original_create, original_sync = tempfile.mkstemp, os.fsync
    original_open, original_unlink = Path.open, Path.unlink
    original_replace, original_adopt = os.replace, kt.KRYLedger._adopt
    original_directory = kt._sync_ledger_directory
    fired = False
    syncs = directories = 0

    def stop(stage):
        nonlocal fired
        if stage == boundary and not fired:
            fired = True
            raise error

    def create(*args, **kwargs):
        stop("temp_create")
        return original_create(*args, **kwargs)

    def sync(fd):
        nonlocal syncs
        syncs += 1
        if syncs in (1, 2):
            stop("temp_fsync" if syncs == 1 else "intent_fsync")
        return original_sync(fd)

    def opening(path, *args, **kwargs):
        if ".pending." in path.name:
            stop("intent_create")
        return original_open(path, *args, **kwargs)

    def directory():
        nonlocal directories
        directories += 1
        stop("intent_directory" if directories == 1 else "target_directory_before")
        original_directory()
        if directories == 2:
            stop("target_directory_after")

    def replace(source, target):
        if Path(target) == kt._LEDGER_PATH.with_name("kry_ledger.v2.json"):
            stop("replace_before")
            original_replace(source, target)
            stop("replace_after")
        else:
            original_replace(source, target)

    def adopt(self, raw):
        if "fault" in raw.get("ops", {}):
            stop("adopt_before")
        original_adopt(self, raw)
        if "fault" in raw.get("ops", {}):
            stop("adopt_after")

    def unlink(path, *args, **kwargs):
        if ".pending." in path.name:
            stop("unlink_before")
        result = original_unlink(path, *args, **kwargs)
        if ".pending." in path.name:
            stop("unlink_after")
        return result

    patch.setattr(tempfile, "mkstemp", create)
    patch.setattr(os, "fsync", sync)
    patch.setattr(Path, "open", opening)
    patch.setattr(kt, "_sync_ledger_directory", directory)
    patch.setattr(os, "replace", replace)
    patch.setattr(kt.KRYLedger, "_adopt", adopt)
    patch.setattr(Path, "unlink", unlink)
    return lambda: fired


@pytest.mark.parametrize("api", ["spend", "debit_kry"])
@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt, asyncio.CancelledError])
@pytest.mark.parametrize("boundary", PRECOMMIT_BOUNDARIES + POSTCOMMIT_BOUNDARIES)
def test_keyed_persistence_fault_matrix(monkeypatch, api, error_type, boundary):
    kt.earn(300, "cache_hit")
    assert kt.debit_kry(0, op_id="activate") == 0
    before = ledger_path().read_bytes()
    error = error_type(boundary)
    call = (lambda: kt.spend("paid", 100, op_id="fault")) if api == "spend" else (
        lambda: kt.debit_kry(100, op_id="fault"))
    observed = None
    with monkeypatch.context() as patch:
        fired = fault_at(patch, boundary, error)
        try:
            observed = call()
        except BaseException as exc:
            if error_type is OSError:
                assert isinstance(exc, kt.LedgerPersistenceError)
            else:
                assert exc is error
        else:
            assert error_type is OSError and boundary in POSTCOMMIT_BOUNDARIES
            assert observed == 100
        assert fired()
    if boundary in PRECOMMIT_BOUNDARIES:
        assert ledger_path().read_bytes() == before
        assert not kt._ledger_incidents()
        assert kt.get_ledger().total_spent == 0
    else:
        assert disk()["accounting"]["total_spent"] == 100
        assert disk()["ops"]["fault"]["amount"] == 100
        if kt._ledger_incidents():
            with pytest.raises(kt.LedgerPersistenceError):
                kt.ledger_operation("fault")
            kt.acknowledge_ledger_recovery(reason="synthetic complete committed record reviewed")
    retry = ("k.spend('paid',100,op_id='fault')" if api == "spend" else
             "k.debit_kry(100,op_id='fault')")
    assert other("import json; from kry import kry_token as k; print(json.dumps(" + retry + "))") == 100
    assert disk()["accounting"]["total_spent"] == 100
    assert kt.ledger_operation("fault")["amount"] == 100
