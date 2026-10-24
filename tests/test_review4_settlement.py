"""Unknown debit outcomes consume the grant until both sides are reviewed."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from kry import kry_attest as ka, kry_mint as km, kry_settlement as ks, kry_token as kt

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _single_node_settlement(monkeypatch):
    monkeypatch.delenv("KRY_SETTLE_LEASE_DIR", raising=False)


def _accepted():
    assert km.mint("cache_hit", 500, evidence="synthetic-review4-seed") is not None
    offer = ks.make_offer("payer", "receiver", 100, 1000, now=1000)
    grant, reason = ks.verify_and_accept(offer, ka.build_attestation().to_public_json(), now=1001)
    assert grant is not None, reason
    return offer, grant, ks.ReceiverLedger("receiver")


def _disk():
    return json.loads(kt._LEDGER_PATH.read_text())


def _assert_consumed(offer, grant, receiver):
    assert receiver.received_kry == 0
    assert ks.verify_registry()[0]
    assert ks._load_registry().get("payer", 0) == 100
    assert ks._registry_entries()[0]["grant_id"] == grant.grant_id
    assert not ks._PENDING_RESERVATIONS.get("payer")
    calls = []
    with pytest.raises(ks.SettlementPersistenceError, match="already settled"):
        ks.settle(offer, grant, debit_a_fn=lambda amount: calls.append(amount),
                  receiver=receiver, a_balance_before=kt.get_ledger().balance)
    assert not calls


@pytest.mark.parametrize("restart", [False, True])
def test_uncertain_ledger_debit_cannot_retry_after_recovery(monkeypatch, restart):
    offer, grant, receiver = _accepted()
    sync = kt._sync_ledger_directory
    calls = 0

    def fail_after_replace():
        nonlocal calls
        calls += 1
        if calls == 2:
            assert _disk()["total_spent"] == 100
            raise OSError("synthetic post-replace directory failure")
        return sync()

    with monkeypatch.context() as patch:
        patch.setattr(kt, "_sync_ledger_directory", fail_after_replace)
        with pytest.raises(kt.LedgerPersistenceError, match="uncertain"):
            ks.settle(offer, grant, debit_a_fn=lambda amount: kt.spend("paid", amount),
                      receiver=receiver, a_balance_before=500)
    assert _disk()["total_spent"] == 100
    assert kt.status()["available"] is False
    assert len(list(kt._LEDGER_PATH.parent.glob("*.pending.*"))) == 1
    _assert_consumed(offer, grant, receiver)
    kt.acknowledge_ledger_recovery(reason="synthetic test: reviewed retained debit and intent")
    assert kt.status()["available"] is True
    if restart:
        code = """
import json,sys
from kry import kry_settlement as ks, kry_token as kt
offer, grant = ks.SettlementOffer(**json.loads(sys.argv[1])), ks.RoutingGrant(**json.loads(sys.argv[2]))
calls = []
receiver = ks.ReceiverLedger('receiver')
try:
    ks.settle(offer, grant, debit_a_fn=lambda amount: calls.append(amount),
              receiver=receiver, a_balance_before=kt.get_ledger().balance)
except ks.SettlementPersistenceError as exc:
    assert 'already settled' in str(exc)
else:
    raise AssertionError('same grant was retried')
assert not calls and receiver.received_kry == 0
assert kt.status()['total_spent_kry'] == ks._load_registry()['payer'] == 100
print('consumed grant preserved after restart')
"""
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1",
               "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent)}
        result = subprocess.run([sys.executable, "-B", "-c", code,
                                 json.dumps(asdict(offer)), json.dumps(asdict(grant))],
                                env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert "consumed grant preserved after restart" in result.stdout
    else:
        _assert_consumed(offer, grant, receiver)
    assert _disk()["total_spent"] == 100
    assert receiver.received_kry == 0


@pytest.mark.parametrize("after_debit", [False, True])
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError, KeyboardInterrupt, SystemExit])
def test_raised_callback_outcome_consumes_grant(error_type, after_debit):
    offer, grant, receiver = _accepted()
    error = error_type("synthetic callback interruption")

    def debit(amount):
        if after_debit:
            assert kt.spend("paid", amount) == amount
        raise error

    with pytest.raises(error_type) as caught:
        ks.settle(offer, grant, debit_a_fn=debit, receiver=receiver, a_balance_before=500)
    assert caught.value is error
    _assert_consumed(offer, grant, receiver)
    assert _disk()["total_spent"] == (100 if after_debit else 0)


@pytest.mark.parametrize("reported", [float("nan"), float("inf"), -1, "100", None, True])
def test_unusable_callback_result_cannot_authorize_another_debit(reported):
    offer, grant, receiver = _accepted()

    def debit(amount):
        assert kt.spend("paid", amount) == amount
        return reported

    with pytest.raises(ValueError, match="debited_kry"):
        ks.settle(offer, grant, debit_a_fn=debit, receiver=receiver, a_balance_before=500)
    _assert_consumed(offer, grant, receiver)
    assert _disk()["total_spent"] == 100


def _assert_partial_cannot_retry(offer, grant, receiver):
    assert _disk()["total_spent"] == 40
    assert receiver.received_kry == 0
    calls = []
    with pytest.raises(ks.SettlementPersistenceError):
        ks.settle(offer, grant, debit_a_fn=lambda amount: calls.append(amount) or amount,
                  receiver=receiver, a_balance_before=460)
    assert not calls
    assert not ks._PENDING_RESERVATIONS.get("payer")
    code = """
import json,sys
from kry import kry_settlement as ks, kry_token as kt
offer, grant = ks.SettlementOffer(**json.loads(sys.argv[1])), ks.RoutingGrant(**json.loads(sys.argv[2]))
calls = []
receiver = ks.ReceiverLedger('receiver')
try:
    ks.settle(offer, grant, debit_a_fn=lambda amount: calls.append(amount) or amount,
              receiver=receiver, a_balance_before=460)
except ks.SettlementPersistenceError:
    pass
else:
    raise AssertionError('partial debit grant was retried after restart')
assert not calls and receiver.received_kry == 0
assert kt.status()['total_spent_kry'] == 40
"""
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1",
           "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent)}
    result = subprocess.run([sys.executable, "-B", "-c", code,
                             json.dumps(asdict(offer)), json.dumps(asdict(grant))],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("prior_entry", [False, True])
@pytest.mark.parametrize("point", ["temp_fsync", "before_replace", "after_replace",
                                  "before_tip", "after_tip"])
@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt, SystemExit])
def test_partial_adjustment_failure_never_releases_grant(monkeypatch, prior_entry,
                                                       point, error_type):
    offer, grant, receiver = _accepted()
    if prior_entry:
        ks._record_settled("earlier-party", 7.0, "earlier-grant")
    prefix = ks._REGISTRY_PATH.read_bytes() if prior_entry else b""
    error = error_type("synthetic partial adjustment failure")
    mkstemp, fsync, replace, write_tip = (ks.tempfile.mkstemp, ks.os.fsync,
                                       ks.os.replace, ks._write_tip)
    armed = False
    fired = False
    temporary_fds = set()

    def fail():
        nonlocal fired
        fired = True
        raise error

    def track_temporary(*args, **kwargs):
        fd, path = mkstemp(*args, **kwargs)
        if armed and kwargs.get("prefix", "").startswith(".reg"):
            temporary_fds.add(fd)
        return fd, path

    def interrupt_fsync(fd):
        if armed and not fired and point == "temp_fsync" and fd in temporary_fds:
            fail()
        return fsync(fd)

    def interrupt_replace(source, destination):
        target = armed and not fired and Path(destination) == ks._REGISTRY_PATH
        if target and point == "before_replace":
            fail()
        result = replace(source, destination)
        if target and point == "after_replace":
            fail()
        return result

    def interrupt_tip(count, tip):
        entries = ks._registry_entries()
        target = armed and not fired and entries and entries[-1]["amount"] == 40
        if target and point == "before_tip":
            fail()
        result = write_tip(count, tip)
        if target and point == "after_tip":
            fail()
        return result

    def debit(amount):
        nonlocal armed
        assert amount == 100
        assert kt.spend("partial", 40) == 40
        armed = True
        return 40

    with monkeypatch.context() as patch:
        patch.setattr(ks.tempfile, "mkstemp", track_temporary)
        patch.setattr(ks.os, "fsync", interrupt_fsync)
        patch.setattr(ks.os, "replace", interrupt_replace)
        patch.setattr(ks, "_write_tip", interrupt_tip)
        expected = ks.SettlementPersistenceError if error_type is OSError else error_type
        with pytest.raises(expected) as caught:
            ks.settle(offer, grant, debit_a_fn=debit, receiver=receiver, a_balance_before=500)
        if error_type is not OSError:
            assert caught.value is error
    assert fired
    assert ks._REGISTRY_PATH.read_bytes().startswith(prefix)
    entries = ks._registry_entries()
    assert entries[-1]["grant_id"] == grant.grant_id
    expected_amount = 100 if point in ("temp_fsync", "before_replace") else 40
    assert entries[-1]["amount"] == expected_amount
    assert ks.verify_registry()[0] is (point not in ("after_replace", "before_tip"))
    if prior_entry:
        assert entries[0]["party"] == "earlier-party" and entries[0]["amount"] == 7
    _assert_partial_cannot_retry(offer, grant, receiver)


def test_partial_debit_never_needs_an_unprotected_second_append(monkeypatch):
    offer, grant, receiver = _accepted()
    record = ks._record_settled

    def fail_partial_append(party, amount, grant_id):
        if amount == 40:
            raise ks.SettlementPersistenceError("synthetic failure before partial append")
        return record(party, amount, grant_id)

    monkeypatch.setattr(ks, "_record_settled", fail_partial_append)
    with pytest.raises(ks.SettlementPersistenceError):
        ks.settle(offer, grant, debit_a_fn=lambda amount: kt.spend("partial", 40),
                  receiver=receiver, a_balance_before=500)
    _assert_partial_cannot_retry(offer, grant, receiver)
    assert ks.verify_registry()[0]
    assert ks._load_registry()["payer"] == 40


@pytest.mark.parametrize("prior_entry", [False, True])
def test_successful_partial_adjustment_keeps_exact_amount_and_prefix(prior_entry):
    offer, grant, receiver = _accepted()
    if prior_entry:
        ks._record_settled("earlier-party", 7.0, "earlier-grant")
    prefix = ks._REGISTRY_PATH.read_bytes() if prior_entry else b""
    with pytest.raises(ks.SettlementPersistenceError, match="debit moved 40"):
        ks.settle(offer, grant, debit_a_fn=lambda amount: kt.spend("partial", 40),
                  receiver=receiver, a_balance_before=500)
    assert ks._REGISTRY_PATH.read_bytes().startswith(prefix)
    assert ks.verify_registry()[0]
    assert ks._load_registry()["payer"] == 40
    _assert_partial_cannot_retry(offer, grant, receiver)
