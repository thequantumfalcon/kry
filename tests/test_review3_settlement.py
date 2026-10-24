"""Real ledger debits must conserve settlement amounts and survive write failures."""
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import random

import pytest

from kry import kry_attest as ka, kry_mint as km, kry_settlement as ks, kry_token as kt


@pytest.fixture(autouse=True)
def _isolated_lease_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("KRY_SETTLE_LEASE_DIR", str(tmp_path / "leases"))


@pytest.fixture
def _single_node_reservations(_isolated_lease_authority, monkeypatch):
    # Local reservations release on failure; shared leases deliberately retain their TTL.
    monkeypatch.delenv("KRY_SETTLE_LEASE_DIR", raising=False)


@pytest.mark.parametrize("model", ["paid", "or/anthropic/claude-opus-4.8"])
@pytest.mark.parametrize("amount", [267898707.93889284, 535347868.6384573,
                                   265311905.32703537, 1e7, 1e308])
def test_unit_rate_preserves_representable_amount(model, amount):
    assert kt.spend_cost(model, amount) == amount


def test_unit_rate_seeded_large_amounts():
    rng = random.Random(20261023)
    for _ in range(100000):
        amount = rng.uniform(1e7, 1e9)
        assert kt.spend_cost("paid", amount) == amount


def _accepted(amount, seed):
    assert km.mint("cache_hit", seed, evidence="settlement-seed") is not None
    offer = ks.make_offer("payer", "receiver", amount, 100, now=1000.0)
    grant, reason = ks.verify_and_accept(
        offer, json.dumps(asdict(ka.build_attestation())), now=1001.0)
    assert grant is not None, reason
    return offer, grant, ks.ReceiverLedger("receiver")


@pytest.mark.parametrize("amount", [267898707.93889284, 535347868.6384573,
                                   265311905.32703537])
def test_large_accepted_settlement_conserves_real_ledger(amount):
    seed = math.ceil(amount) + 1000
    offer, grant, receiver = _accepted(amount, seed)
    receipt = ks.settle(
        offer, grant, debit_a_fn=lambda value: kt.spend("paid", value),
        receiver=receiver, a_balance_before=seed,
        a_balance_after_fn=lambda: kt.get_ledger().balance)
    disk = json.loads(kt._LEDGER_PATH.read_text())
    assert disk["total_spent"] == receiver.received_kry == amount
    assert disk["balance"] == seed - amount
    assert receipt.conservation_basis == "measured"
    assert ks.verify_conservation(receipt)
    assert ks.verify_registry()[0]
    kt._ledger_instance = None
    assert kt.get_ledger().balance == seed - amount


@pytest.mark.parametrize("after_replace", [False, True])
def test_settlement_credits_only_confirmed_ledger_debit(monkeypatch, after_replace):
    offer, grant, receiver = _accepted(100, 100)
    original = os.replace

    def fault(source, target):
        if Path(target) == kt._LEDGER_PATH:
            if after_replace:
                original(source, target)
            raise OSError("constructed ledger failure")
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fault)
        if after_replace:
            receipt = ks.settle(
                offer, grant, debit_a_fn=lambda value: kt.spend("paid", value),
                receiver=receiver, a_balance_before=100,
                a_balance_after_fn=lambda: kt.get_ledger().balance)
            assert ks.verify_conservation(receipt)
        else:
            with pytest.raises(ks.SettlementPersistenceError):
                ks.settle(offer, grant, debit_a_fn=lambda value: kt.spend("paid", value),
                          receiver=receiver, a_balance_before=100)
    expected = 100 if after_replace else 0
    kt._ledger_instance = None
    assert kt.get_ledger().balance == 100 - expected
    assert receiver.received_kry == expected
    assert ks._load_registry().get("payer", 0) == expected
    if not after_replace:
        # A known failed debit stays retriable and must move value just once.
        receipt = ks.settle(
            offer, grant, debit_a_fn=lambda value: kt.spend("paid", value),
            receiver=receiver, a_balance_before=100,
            a_balance_after_fn=lambda: kt.get_ledger().balance)
        assert ks.verify_conservation(receipt)
        assert receiver.received_kry == kt.get_ledger().total_spent == 100


def test_large_actual_shortfall_is_still_rejected():
    amount = 267898707.93889284
    seed = math.ceil(amount) + 1000
    offer, grant, receiver = _accepted(amount, seed)
    with pytest.raises(ks.SettlementPersistenceError):
        ks.settle(offer, grant, debit_a_fn=lambda value: kt.spend("paid", value - 1),
                  receiver=receiver, a_balance_before=seed)
    assert receiver.received_kry == 0
    assert kt.get_ledger().total_spent == amount - 1
    assert ks._load_registry()["payer"] == amount - 1


def test_measured_fractional_transfer_uses_bounded_forward_arithmetic():
    amount, seed = 100000000.1, 1000000000.0
    offer, grant, receiver = _accepted(amount, seed)
    receipt = ks.settle(
        offer, grant, debit_a_fn=lambda value: kt.spend("paid", value),
        receiver=receiver, a_balance_before=seed,
        a_balance_after_fn=lambda: kt.get_ledger().balance)
    assert receipt.conserved and ks.verify_conservation(receipt)
    assert receiver.received_kry == kt.get_ledger().total_spent == amount
    # A nearby balance is not accepted merely because the account is large.
    receipt.a_balance_after = math.nextafter(receipt.a_balance_after, math.inf)
    assert not ks.verify_conservation(receipt)


@pytest.mark.parametrize("seed,amount,received", [
    (1e30, 1e14, 0), (1e30, 1, 0), (1000, 100, 1e30),
])
def test_unrepresentable_transfer_refuses_before_debit(seed, amount, received):
    offer = ks.make_offer("payer", "receiver", amount, 100, now=1000)
    grant = ks.RoutingGrant("precision-control", offer.offer_id, "receiver", 100, amount, 1001)
    receiver = ks.ReceiverLedger("receiver", received_kry=received)
    calls = []
    with pytest.raises(ks.SettlementPersistenceError, match="precision"):
        ks.settle(offer, grant, debit_a_fn=lambda value: calls.append(value) or value,
                  receiver=receiver, a_balance_before=seed)
    assert not calls
    assert receiver.received_kry == received
    assert not ks._REGISTRY_PATH.exists()


def test_receiver_overflow_refuses_before_debit():
    amount = 1e308
    offer = ks.make_offer("payer", "receiver", amount, 100, now=1000)
    grant = ks.RoutingGrant("overflow-control", offer.offer_id, "receiver", 100, amount, 1001)
    receiver = ks.ReceiverLedger("receiver", received_kry=amount)
    calls = []
    with pytest.raises(ValueError, match="finite"):
        ks.settle(offer, grant, debit_a_fn=lambda value: calls.append(value) or value,
                  receiver=receiver, a_balance_before=amount)
    assert not calls
    assert not ks._REGISTRY_PATH.exists()


@pytest.mark.parametrize("amount,seed,received", [(100, 1000, 1e30), (1e308, 1e308, 1e308)])
def test_numeric_preflight_failure_releases_accepted_reservation(
        amount, seed, received, _single_node_reservations):
    offer, grant, receiver = _accepted(amount, seed)
    receiver.received_kry = received
    calls = []
    with pytest.raises((ValueError, ks.SettlementPersistenceError)):
        ks.settle(offer, grant, debit_a_fn=lambda value: calls.append(value) or value,
                  receiver=receiver, a_balance_before=seed)
    assert not calls
    assert not ks._PENDING_RESERVATIONS.get("payer")
    next_offer = ks.make_offer("payer", "other", seed, 100, now=1002)
    next_grant, reason = ks.verify_and_accept(
        next_offer, json.dumps(asdict(ka.build_attestation())), now=1003)
    assert next_grant is not None, reason
