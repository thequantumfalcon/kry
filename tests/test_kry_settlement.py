"""Tests for trustless two-party KRY settlement — the external-token handshake."""
from __future__ import annotations
import json
from kry.kry_settlement import (
    make_offer, verify_and_accept, settle, verify_conservation, ReceiverLedger,
    SettlementPersistenceError)

import pytest


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path, monkeypatch):
    """Each test gets a fresh federated registry — no cross-test pollution."""
    import kry.kry_settlement as ks
    monkeypatch.setattr(ks, "_REGISTRY_PATH", tmp_path / "settlement_reg.json")




def _attestation(total_kry: float) -> str:
    """Build a minimal valid attestation JSON with a real chain."""
    import hashlib
    links = []
    prev = "0" * 64
    per = total_kry / 2
    for i in range(2):
        rh = hashlib.sha256(f"r{i}".encode()).hexdigest()
        ch = hashlib.sha256(f"{prev}:{rh}".encode()).hexdigest()
        links.append({"seq": i+1, "event_type": "cache_hit", "kry_minted": per,
                      "receipt_hash": rh, "chain_hash": ch, "sealed_evidence": "x"})
        prev = ch
    att = {"receipts": 2, "total_kry": total_kry, "usd_equivalent": total_kry*0.000025,
           "chain_head": prev, "chain_valid": True, "event_type_counts": {"cache_hit": 2},
           "links": links,
           "veracity": {
               "by_tier": {"self_reported": total_kry},
               "externally_anchored_kry": 0.0,
               "self_reported_kry": total_kry,
               "veracity_floor": 0.0,
           },
           "attestation_hash": ""}
    att["attestation_hash"] = hashlib.sha256(
        json.dumps(att, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return json.dumps(att)


def test_offer_creation():
    o = make_offer("A", "B", 100.0, 1000, now=1000.0)
    assert o.from_party == "A" and o.kry_amount == 100.0 and o.routing_tokens == 1000


def test_accept_valid_offer():
    o = make_offer("A", "B", 500.0, 5000, now=1000.0)
    grant, reason = verify_and_accept(o, _attestation(1000.0), now=1001.0)
    assert grant is not None and reason == "accepted"


def test_reject_overclaim():
    """B must reject an offer exceeding A's attested balance."""
    o = make_offer("A", "B", 5000.0, 5000, now=1000.0)
    grant, reason = verify_and_accept(o, _attestation(1000.0), now=1001.0)
    assert grant is None and "insufficient" in reason


def test_reject_tampered_attestation():
    """B must reject an attestation whose chain is broken."""
    att = json.loads(_attestation(1000.0))
    att["links"][1]["chain_hash"] = "tampered" + "0"*56  # break the chain
    o = make_offer("A", "B", 100.0, 1000, now=1000.0)
    grant, reason = verify_and_accept(o, json.dumps(att), now=1001.0)
    assert grant is None and "invalid" in reason


def test_settlement_conserves_kry():
    """The currency invariant: A's debit == B's credit, nothing created."""
    o = make_offer("A", "B", 300.0, 3000, now=1000.0)
    grant, _ = verify_and_accept(o, _attestation(1000.0), now=1001.0)
    b = ReceiverLedger(party="B")
    state = {"bal": 1000.0}
    def debit(kry):
        state["bal"] -= kry
        return kry
    receipt = settle(o, grant, debit_a_fn=debit, receiver=b, a_balance_before=1000.0)
    assert receipt.conserved
    assert verify_conservation(receipt)
    a_lost = receipt.a_balance_before - receipt.a_balance_after
    b_gained = receipt.b_received_after - receipt.b_received_before
    assert abs(a_lost - b_gained) < 1e-9
    assert b.received_kry == 300.0 and b.routing_sold == 3000


def test_settlement_receipt_hashed():
    """Both parties hold a hash-bound receipt."""
    o = make_offer("A", "B", 100.0, 1000, now=1000.0)
    grant, _ = verify_and_accept(o, _attestation(1000.0), now=1001.0)
    b = ReceiverLedger(party="B")
    receipt = settle(o, grant, debit_a_fn=lambda k: k, receiver=b, a_balance_before=1000.0)
    assert len(receipt.receipt_hash) == 64  # SHA-256


def test_settlement_fails_closed_if_registry_record_fails(monkeypatch):
    """A settlement without durable registry visibility must not return a receipt."""
    import kry.kry_settlement as ks
    o = make_offer("A", "B", 100.0, 1000, now=1000.0)
    grant, _ = verify_and_accept(o, _attestation(1000.0), now=1001.0)
    b = ReceiverLedger(party="B")

    def fail_record(party, amount):
        raise SettlementPersistenceError("simulated registry failure")

    monkeypatch.setattr(ks, "_record_settled", fail_record)
    with pytest.raises(SettlementPersistenceError):
        settle(o, grant, debit_a_fn=lambda k: k, receiver=b, a_balance_before=1000.0)
    assert b.received_kry == 0.0
    assert b.routing_sold == 0
    assert b.settlements == []


def test_record_settled_fails_if_tip_checkpoint_fails(monkeypatch):
    """The rollback checkpoint write is part of durable settlement persistence."""
    import kry.kry_settlement as ks

    def fail_tip(count, tip):
        raise OSError("no checkpoint")

    monkeypatch.setattr(ks, "_write_tip", fail_tip)
    with pytest.raises(SettlementPersistenceError):
        ks._record_settled("A", 100.0)


def test_partial_debit_still_conserves():
    """If A can only partially pay, conservation still holds on the actual amount."""
    o = make_offer("A", "B", 500.0, 5000, now=1000.0)
    grant, _ = verify_and_accept(o, _attestation(1000.0), now=1001.0)
    b = ReceiverLedger(party="B")
    # A only has 200 actually available
    def debit(kry):
        return min(kry, 200.0)
    receipt = settle(o, grant, debit_a_fn=debit, receiver=b, a_balance_before=200.0)
    assert receipt.conserved
    assert b.received_kry == 200.0  # B credited exactly what A paid


# ── Double-spend prevention (federated registry) ──────────────────────────────

def test_double_spend_blocked_across_counterparties(tmp_path, monkeypatch):
    """The currency-critical test: one attestation cannot settle twice."""
    import kry.kry_settlement as ks
    monkeypatch.setattr(ks, "_REGISTRY_PATH", tmp_path / "reg.json")
    att = _attestation(2000.0)

    B = ReceiverLedger(party="B")
    oB = make_offer("A", "B", 2000.0, 50000, now=1000.0)
    gB, rB = verify_and_accept(oB, att, now=1001.0)
    assert gB is not None  # first settlement OK
    settle(oB, gB, debit_a_fn=lambda k: k, receiver=B, a_balance_before=2000.0)

    # Second settlement, same attestation, different counterparty — must reject
    oC = make_offer("A", "C", 2000.0, 50000, now=1002.0)
    gC, rC = verify_and_accept(oC, att, now=1003.0)
    assert gC is None
    assert "double-spend" in rC


def test_partial_then_remainder_settles(tmp_path, monkeypatch):
    """A can settle in parts up to attested balance, but not beyond."""
    import kry.kry_settlement as ks
    monkeypatch.setattr(ks, "_REGISTRY_PATH", tmp_path / "reg.json")
    att = _attestation(1000.0)
    B = ReceiverLedger(party="B")
    # settle 600
    o1 = make_offer("A", "B", 600.0, 6000, now=1000.0)
    g1, _ = verify_and_accept(o1, att, now=1001.0)
    assert g1 is not None
    settle(o1, g1, debit_a_fn=lambda k: k, receiver=B, a_balance_before=1000.0)
    # settle 400 more — OK (total 1000 == attested)
    o2 = make_offer("A", "B", 400.0, 4000, now=1002.0)
    g2, _ = verify_and_accept(o2, att, now=1003.0)
    assert g2 is not None
    settle(o2, g2, debit_a_fn=lambda k: k, receiver=B, a_balance_before=400.0)
    # settle 1 more — REJECTED (would exceed attested)
    o3 = make_offer("A", "B", 1.0, 10, now=1004.0)
    g3, r3 = verify_and_accept(o3, att, now=1005.0)
    assert g3 is None and "double-spend" in r3
