from __future__ import annotations

import hashlib
import json
import math

import pytest


def _attestation(total_kry: float) -> str:
    links = []
    prev = "0" * 64
    per = total_kry / 2
    for i in range(2):
        rh = hashlib.sha256(f"r{i}".encode()).hexdigest()
        ch = hashlib.sha256(f"{prev}:{rh}".encode()).hexdigest()
        links.append({
            "seq": i + 1,
            "event_type": "cache_hit",
            "kry_minted": per,
            "receipt_hash": rh,
            "chain_hash": ch,
            "sealed_evidence": "x",
        })
        prev = ch
    att = {
        "receipts": 2,
        "total_kry": total_kry,
        "usd_equivalent": total_kry * 0.000025,
        "chain_head": prev,
        "chain_valid": True,
        "event_type_counts": {"cache_hit": 2},
        "links": links,
        "veracity": {
            "by_tier": {"self_reported": total_kry},
            "externally_anchored_kry": 0.0,
            "self_reported_kry": total_kry,
            "veracity_floor": 0.0,
        },
        "attestation_hash": "",
    }
    att["attestation_hash"] = hashlib.sha256(
        json.dumps(att, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return json.dumps(att)


def test_mint_chain_rejects_nonstandard_json_constant_and_reconcile_fails_closed():
    import kry.kry_mint as km

    km._MINT_LOG_PATH.write_text(
        '{"receipt_id":"KRY-00000001","event_type":"cache_hit",'
        '"tokens_saved":NaN,"kry_minted":100.0,"earn_rate":1.0,'
        '"ts":1.0,"detail":"","evidence_hash":"abc","receipt_hash":"x",'
        '"chain_hash":"y","usd_equivalent":0.0025}\n'
    , encoding="utf-8")

    ok, errs = km.verify_chain()
    reconciled = km.reconcile_ledger_from_chain()
    summary = km.chain_summary()

    assert not ok
    assert any("non-standard JSON constant rejected: NaN" in e for e in errs)
    assert reconciled["reconciled"] is False
    assert summary["chain_valid"] is False
    assert not math.isnan(summary["total_kry_minted"])
    assert summary["total_kry_minted"] == 0.0


def test_mint_summaries_reject_stringy_nonfinite_numeric_fields():
    import kry.kry_mint as km

    km._MINT_LOG_PATH.write_text(json.dumps({
        "receipt_id": "KRY-00000001",
        "event_type": "cache_hit",
        "tokens_saved": 100.0,
        "kry_minted": "NaN",
        "earn_rate": 1.0,
        "ts": 1.0,
        "detail": "",
        "evidence_hash": "abc",
        "receipt_hash": "x",
        "chain_hash": "y",
        "usd_equivalent": 0.0025,
    }) + "\n", encoding="utf-8")

    ok, errs = km.verify_chain()
    dated = km.retained_dollars_dated()
    breakdown = km.veracity_breakdown()

    assert not ok
    assert any("kry_minted must be a finite JSON number" in e for e in errs)
    assert dated["chain_valid"] is False
    assert dated["total_kry_minted"] == 0.0
    assert breakdown["total_kry"] == 0.0


def test_kry_ledger_does_not_preserve_nonstandard_numbers_on_save():
    import kry.kry_token as kt

    kt._LEDGER_PATH.write_text(
        '{"balance":NaN,"total_earned":NaN,"total_spent":0.0,'
        '"cycle_count":0,"events":[]}\n'
    , encoding="utf-8")
    ledger = kt.KRYLedger.load_or_create()
    ledger.balance = 10.0
    ledger.total_earned = 10.0
    ledger.save()

    raw = kt._LEDGER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)

    assert "NaN" not in raw
    assert data["balance"] == 10.0
    assert data["total_earned"] == 10.0


def test_settlement_registry_rejects_nonstandard_json_constant():
    import kry.kry_settlement as ks

    ks._REGISTRY_PATH.write_text(
        '{"party":"A","amount":NaN,"prev_hash":"0","entry_hash":"x"}\n'
    , encoding="utf-8")
    offer = ks.make_offer("A", "B", 100.0, 1000, now=1.0)

    ok, errs = ks.verify_registry()
    grant, reason = ks.verify_and_accept(offer, _attestation(1000.0), now=2.0)

    assert not ok
    assert any("non-standard JSON constant rejected: NaN" in e for e in errs)
    assert grant is None
    assert "registry tampered" in reason


def test_settlement_registry_rejects_missing_hash_fields():
    import kry.kry_settlement as ks

    ks._REGISTRY_PATH.write_text('{"party":"A","amount":1.0}\n', encoding="utf-8")

    ok, errs = ks.verify_registry()

    assert not ok
    assert any("prev_hash must be a string" in e for e in errs)


def test_settlement_lease_rejects_corrupted_authority(tmp_path, monkeypatch):
    import kry.kry_settlement as ks

    authdir = tmp_path / "leases"
    authdir.mkdir()
    (authdir / "kry_leases.json").write_text(
        '{"A":[{"amount":NaN,"ts":1.0,"nonce":"old"}]}\n'
    , encoding="utf-8")
    monkeypatch.setenv("KRY_SETTLE_LEASE_DIR", str(authdir))
    offer = ks.make_offer("A", "B", 100.0, 1000, now=10.0)

    grant, reason = ks.verify_and_accept(offer, _attestation(1000.0), now=11.0)

    assert grant is None
    assert "cross-node lease denied" in reason


def test_settle_rejects_nonfinite_direct_debit_result():
    import kry.kry_settlement as ks

    offer = ks.make_offer("A", "B", 100.0, 1000, now=1.0)
    grant = ks.RoutingGrant(
        grant_id="g",
        offer_id=offer.offer_id,
        granted_by="B",
        routing_tokens=1000,
        accepted_kry=100.0,
        ts=2.0,
    )

    with pytest.raises(ValueError, match="debited_kry must be finite"):
        ks.settle(
            offer,
            grant,
            debit_a_fn=lambda _kry: float("nan"),
            receiver=ks.ReceiverLedger(party="B"),
            a_balance_before=100.0,
        )
