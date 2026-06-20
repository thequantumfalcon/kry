"""Regression tests for the audit/debug round (findings #1-#30).

Each test locks in a fix for a bug the prior suite missed. Stdlib-only fixes are
covered here; the optional-tier fixes (kry_pqc #19/#20/#21/#28/#29, TEE/SNP
#23/#24/#25) need liboqs / cryptography and are validated separately.
"""
from __future__ import annotations

import json

import pytest


# ── #30 / #4 / #5 / #6 — mint-chain verifier & reconcile ──────────────────────

def test_reconcile_from_chain_preserves_spend():
    """#30: reconcile must subtract total_spent — not resurrect already-spent KRY."""
    import kry.kry_mint as km
    import kry.kry_token as kt
    km.mint("cache_hit", 1000.0, evidence="e", avoided_model="opus")
    kt.spend("or/anthropic/claude-opus-4.8", 500, "route")   # spend 500
    km.reconcile_ledger_from_chain()
    led = kt.get_ledger()
    assert led.total_earned == pytest.approx(1000.0)
    assert led.total_spent == pytest.approx(500.0)
    assert led.balance == pytest.approx(500.0)               # NOT 1000.0
    assert led.balance == pytest.approx(led.total_earned - led.total_spent)


def test_verify_chain_rejects_fabricated_kry_minted():
    """#4: a kry_minted that is not tokens*rate*published_multiplier is rejected."""
    import hashlib
    import kry.kry_mint as km
    km.mint("displacement", 1000.0, evidence="a", avoided_model="opus")
    log = km._MINT_LOG_PATH
    rows = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    r = rows[0]
    r["tokens_saved"] = 9_000_000.0
    r["kry_minted"] = 9_000_000.0          # implied multiplier 2.0 — not a published value
    r["usd_equivalent"] = r["kry_minted"] * 0.000025
    hv = r["hash_version"]
    metered = km._json_dumps(r.get("metered_tokens"), sort_keys=True, separators=(",", ":"))
    r["receipt_hash"] = hashlib.sha256(
        f"{r['event_type']}:{r['tokens_saved']}:{r['ts']}:{r['evidence_hash']}"
        f":{r['evidence_tier']}:{metered}".encode()).hexdigest()
    block = km._v4_public_block(hash_version=hv, tokens_saved=r["tokens_saved"], ts=r["ts"],
        evidence_tier=r["evidence_tier"], metered_tokens=r.get("metered_tokens"),
        kry_minted=r["kry_minted"], earn_rate=r["earn_rate"], receipt_id=r.get("receipt_id"))
    r["chain_hash"] = hashlib.sha256(f"{'0'*64}:{r['receipt_hash']}:{block}".encode()).hexdigest()
    log.write_text(json.dumps(r) + "\n")
    km._write_mint_tip(1, r["chain_hash"])
    ok, errs = km.verify_chain()
    assert not ok
    assert any("multiplier" in e for e in errs)


def test_verify_chain_rejects_provider_metered_without_tokens():
    """#5: provider_metered with metered_tokens=null must NOT report veracity_floor=1.0."""
    import hashlib
    import kry.kry_mint as km
    et, tok, ts, ev = "cache_hit", 1000.0, 1000.0, "ab" * 8
    tier, metered, kry, rate, hv = km.TIER_PROVIDER_METERED, None, 5.0, 0.005, 6
    content = f"{et}:{tok}:{ts}:{ev}:{tier}:{km._json_dumps(metered, sort_keys=True, separators=(',', ':'))}"
    rh = hashlib.sha256(content.encode()).hexdigest()
    block = km._v4_public_block(hash_version=hv, tokens_saved=tok, ts=ts, evidence_tier=tier,
        metered_tokens=metered, kry_minted=kry, earn_rate=rate, receipt_id="KRY-00000001")
    ch = hashlib.sha256(f"{'0'*64}:{rh}:{block}".encode()).hexdigest()
    rec = {"receipt_id": "KRY-00000001", "event_type": et, "tokens_saved": tok, "kry_minted": kry,
           "earn_rate": rate, "ts": ts, "detail": "", "evidence_hash": ev, "receipt_hash": rh,
           "chain_hash": ch, "usd_equivalent": kry * 0.000025, "avoided_model": None,
           "evidence_tier": tier, "hash_version": hv, "metered_tokens": metered, "supersedes": None}
    km._MINT_LOG_PATH.write_text(json.dumps(rec) + "\n")
    km._write_mint_tip(1, ch)
    ok, errs = km.verify_chain()
    assert not ok
    assert any("metered_tokens" in e for e in errs)


def test_verify_chain_rejects_edited_usd_equivalent():
    """#6: usd_equivalent is re-derived from kry_minted; an edit breaks the chain."""
    import kry.kry_mint as km
    km.mint("cache_hit", 1000.0, evidence="e", avoided_model="opus")
    log = km._MINT_LOG_PATH
    rec = json.loads(log.read_text().splitlines()[0])
    rec["usd_equivalent"] = 999999.0       # tamper the "dated retained dollars" figure
    log.write_text(json.dumps(rec) + "\n")
    ok, errs = km.verify_chain()
    assert not ok
    assert any("usd_equivalent" in e for e in errs)


# ── #10 — forged promotion (receipt_id relabel) ───────────────────────────────

def test_attestation_rejects_relabeled_receipt_id():
    """#10: relabeling a self_reported link's receipt_id to a promotion's supersedes is rejected."""
    import kry.kry_mint as km
    import kry.kry_attest as ka
    km.mint("cache_hit", 100000.0, detail="big", evidence="big", avoided_model="opus")
    km.mint("displacement", 100.0, detail="routed /openrouter:gen-1", evidence="g1",
            avoided_model="opus")
    assert km.promote_to_tlsn("gen-1", "tlsn-binding", "promote") is not None
    att = json.loads(ka.build_attestation().to_public_json())
    assert ka.verify_attestation(json.dumps(att))[0]
    prom = next(lk for lk in att["links"] if lk.get("supersedes"))
    big = max((lk for lk in att["links"] if lk["evidence_tier"] == "self_reported"),
              key=lambda lk: lk["kry_minted"])
    big["receipt_id"] = prom["supersedes"]          # the relabel attack
    att["attestation_hash"] = ""
    att["attestation_hash"] = ka._attestation_hash(att)
    ok, errs = ka.verify_attestation(json.dumps(att))
    assert not ok


def test_minted_receipts_are_hash_version_6():
    """#10: new mints bind receipt_id at v6 (v4/v5 stay byte-compatible via dispatch)."""
    import kry.kry_mint as km
    r = km.mint("cache_hit", 100.0, evidence="e", avoided_model="opus")
    assert r is not None and r.hash_version == 6


# ── #1/#17 & #2 & #3 — ledger accounting ──────────────────────────────────────

def test_save_clamps_balance_nonnegative_and_keeps_invariant():
    """#1/#17: a debit that would drive the merged balance below 0 is refused at save()."""
    import kry.kry_token as kt
    kt._LEDGER_PATH.write_text(json.dumps(
        {"balance": 100.0, "total_earned": 100.0, "total_spent": 0.0, "cycle_count": 0, "events": []}))
    led = kt.KRYLedger.load_or_create()
    # simulate an over-spend reaching save(): in-memory thinks it spent 160 against a stale 100
    led.balance = -60.0
    led.total_spent = 160.0
    led.save()
    disk = json.loads(kt._LEDGER_PATH.read_text())
    assert disk["balance"] >= 0.0
    assert disk["balance"] == pytest.approx(disk["total_earned"] - disk["total_spent"])


def test_events_merge_across_writers():
    """#2: two ledger instances sharing the disk path don't clobber each other's events."""
    import kry.kry_token as kt
    a = kt.KRYLedger()
    b = kt.KRYLedger()
    a.balance = a.total_earned = 100.0
    a.events.append(kt.KRYEvent(ts=1.0, kind="earn", source="cacheA", amount=100.0, tx_id="A1"))
    b.balance = b.total_earned = 50.0
    b.events.append(kt.KRYEvent(ts=2.0, kind="earn", source="cacheB", amount=50.0, tx_id="B1"))
    a.save()
    b.save()
    disk = json.loads(kt._LEDGER_PATH.read_text())
    assert sorted(e["source"] for e in disk["events"]) == ["cacheA", "cacheB"]
    assert disk["total_earned"] == pytest.approx(150.0)


def test_efficiency_ratio_correct_below_one_kry():
    """#3: the max(1.0, total) clamp no longer understates the ratio for tiny ledgers."""
    import kry.kry_token as kt
    led = kt.KRYLedger(balance=0.0, total_earned=0.3, total_spent=0.3)
    assert led.efficiency_ratio == pytest.approx(0.5)
    assert kt.KRYLedger().efficiency_ratio == 0.0      # zero-guard preserved


# ── #7 — settlement transactional ordering ────────────────────────────────────

def test_settle_failed_debit_leaves_no_phantom_obligation():
    """#7: a debit that moves nothing rolls the registry back and keeps the grant retriable."""
    import kry.kry_mint as km
    import kry.kry_attest as ka
    import kry.kry_settlement as ks
    km.mint("cache_hit", 1000.0, evidence="e", avoided_model="opus")
    att = ka.build_attestation().to_public_json()
    o = ks.make_offer("A", "B", 500.0, 5000, now=1000.0)
    grant, _ = ks.verify_and_accept(o, att, now=1001.0)
    b = ks.ReceiverLedger(party="B")
    with pytest.raises(ks.SettlementPersistenceError):
        ks.settle(o, grant, debit_a_fn=lambda k: 0.0, receiver=b, a_balance_before=1000.0)
    assert ks._load_registry().get("A", 0.0) == 0.0       # no phantom obligation
    assert b.received_kry == 0.0
    assert ks.verify_registry()[0]                         # registry still valid
    b2 = ks.ReceiverLedger(party="B")
    ks.settle(o, grant, debit_a_fn=lambda k: k, receiver=b2, a_balance_before=1000.0)
    assert b2.received_kry == pytest.approx(500.0)         # grant was NOT burned


# ── #13 — sanctions persist failure ───────────────────────────────────────────

def test_record_reconciliation_raises_on_persist_failure(monkeypatch):
    """#13: a persist failure must raise, not return a fabricated un-persisted reputation."""
    import kry.kry_sanctions as ksanc
    ksanc.record_reconciliation("eve", confirmed=False)   # first sanction persists
    before = ksanc.reputation("eve")

    def boom(*a, **k):
        raise PermissionError("read-only mount")
    monkeypatch.setattr(ksanc.tempfile, "mkstemp", boom)
    with pytest.raises(PermissionError):
        ksanc.record_reconciliation("eve", confirmed=False)
    # reputation() only reads (no mkstemp), so leave the patch in place — calling
    # monkeypatch.undo() here would also revert the autouse isolation fixture's patches.
    assert ksanc.reputation("eve") == pytest.approx(before)   # durable value unchanged


# ── #14 / #15 — pending displacements ─────────────────────────────────────────

def test_pending_write_ahead_no_double_mint(monkeypatch):
    """#14: a crash after a landed mint but before persist must not re-mint on retry."""
    import kry.kry_pending as kp
    import kry.kry_mint as km
    pid = kp.record_pending(
        {"event_type": "cache_hit", "tokens_saved": 1000, "evidence": "uniq-42",
         "avoided_model": "opus"})

    def crashy(**kw):
        raise RuntimeError("crash after the write-ahead persist")
    monkeypatch.setattr(kp, "mint", crashy)
    with pytest.raises(RuntimeError):
        kp.confirm(pid)
    # The retry bails on the persisted "confirmed" status BEFORE calling mint, so leaving `mint`
    # patched is fine — and avoids monkeypatch.undo() reverting the autouse isolation fixture.
    assert kp.stats().get("confirmed") == 1       # status persisted BEFORE the mint
    assert kp.confirm(pid) is None                # retry bails — no second mint
    assert km.chain_summary()["receipts"] == 0


def test_pending_rejects_nonfinite_ttl():
    """#15: a NaN/inf ttl is rejected at the boundary (else the pending never expires)."""
    import kry.kry_pending as kp
    for bad in (float("nan"), float("inf"), -1.0):
        with pytest.raises(ValueError):
            kp.record_pending({"event_type": "cache_hit", "tokens_saved": 1000}, ttl=bad)


def test_pending_load_rejects_nan_constant():
    """#15: a NaN-poisoned store reads as empty (fail-closed: nothing mints)."""
    import kry.kry_pending as kp
    kp._PENDING_PATH.write_text(
        '{"PEND-x": {"created_ts": 0.0, "ttl": NaN, "status": "pending", '
        '"mint_kwargs": {"event_type": "cache_hit", "tokens_saved": 1000}}}')
    assert kp.stats() == {}
    assert kp.sweep_expired() == 0


# ── #16 — wilson interval ─────────────────────────────────────────────────────

def test_wilson_interval_clamps_out_of_range():
    """#16: successes > n no longer crashes math.sqrt with a negative radicand."""
    import kry.kry_baseline as kb
    lo, hi = kb.wilson_interval(15, 10)       # corrupted store: paid_n > n
    assert 0.0 <= lo <= hi <= 1.0


# ── #22 — verify_attestation accepts a null veracity (CLI must not crash on it) ─

def test_verify_attestation_accepts_null_veracity():
    """#22: veracity=null is VALID (the CLI display, fixed separately, must not crash on it)."""
    import kry.kry_mint as km
    import kry.kry_attest as ka
    km.mint("cache_hit", 1000.0, evidence="e", avoided_model="opus")
    att = json.loads(ka.build_attestation().to_public_json())
    att["veracity"] = None
    att["attestation_hash"] = ""
    att["attestation_hash"] = ka._attestation_hash(att)
    assert ka.verify_attestation(json.dumps(att))[0] is True


# ── #26 / #27 — tlsn gen-id binding ───────────────────────────────────────────

def test_gen_id_exact_match_not_substring():
    """#27: a short gen id must not resolve a longer session's receipt."""
    import kry.kry_mint as km
    km.mint("displacement", 100.0, detail="routed /openrouter:gen-1234abcd", evidence="g1",
            avoided_model="or/free/cheap")
    assert km._find_t1_receipt_for_gen("gen-1") is None
    assert km._find_t1_receipt_for_gen("gen-1234abcd") is not None


def test_fresh_t2_dedup_detects_prior_mint():
    """#26: a prior fresh tlsn_attested mint for a gen id is found (verifier refuses the 2nd)."""
    import kry.kry_mint as km
    km.mint("short_circuit", 475.0,
            detail="tlsn openrouter.ai status=200 /openrouter:gen-SAME", evidence="pres-1",
            avoided_model="opus", evidence_tier=km.TIER_TLSN_ATTESTED)
    assert km._find_fresh_t2_receipt_for_gen("gen-SAME") is not None
    assert km._find_fresh_t2_receipt_for_gen("gen-OTHER") is None


# ── Review-round regressions (the fixes' own fixes) ───────────────────────────

def test_events_merge_preserves_distinct_legacy_events():
    """Review #1: distinct legacy/empty-tx_id events that share a payload are NOT collapsed by save()."""
    import kry.kry_token as kt
    ev = {"ts": 1700000000.5, "kind": "earn", "source": "cache_hit", "amount": 1.0,
          "detail": "[avoided=opus,x1.00]"}
    kt._LEDGER_PATH.write_text(json.dumps(
        {"balance": 5.0, "total_earned": 5.0, "total_spent": 0.0, "cycle_count": 0,
         "events": [dict(ev) for _ in range(5)]}))      # 5 distinct, identical-payload, empty tx_id
    led = kt.KRYLedger.load_or_create()
    led.save()
    disk = json.loads(kt._LEDGER_PATH.read_text())
    assert len(disk["events"]) == 5                       # none collapsed by a tuple key
    assert sum(e["amount"] for e in disk["events"]) == pytest.approx(disk["total_earned"])


def test_settle_partial_debit_records_real_movement():
    """Review #3: a partial debit fails closed (B=0) but the registry records A's ACTUAL movement —
    not a clean 0 that would under-count A's spending and let it over-spend across counterparties."""
    import kry.kry_mint as km
    import kry.kry_attest as ka
    import kry.kry_settlement as ks
    km.mint("cache_hit", 1000.0, evidence="e", avoided_model="opus")
    att = ka.build_attestation().to_public_json()
    o = ks.make_offer("A", "B", 500.0, 5000, now=1000.0)
    grant, _ = ks.verify_and_accept(o, att, now=1001.0)
    b = ks.ReceiverLedger(party="B")
    with pytest.raises(ks.SettlementPersistenceError):
        ks.settle(o, grant, debit_a_fn=lambda k: 300.0, receiver=b, a_balance_before=1000.0)
    assert b.received_kry == 0.0                          # fail closed — no partial credit to B
    assert ks._load_registry().get("A", 0.0) == pytest.approx(300.0)   # A's REAL movement recorded
    assert ks.verify_registry()[0]                        # registry internally valid


def test_pending_store_rejects_nonfinite_symmetrically(monkeypatch):
    """Review #5: a non-finite value in mint_kwargs is rejected at WRITE time, leaving prior pendings."""
    import kry.kry_pending as kp
    kp.record_pending({"event_type": "cache_hit", "tokens_saved": 10})   # a good pending first
    with pytest.raises(ValueError):
        kp.record_pending({"event_type": "cache_hit", "tokens_saved": float("inf")})
    # the prior good pending must survive (atomic write never replaced the store with a poison value)
    assert kp.stats().get("pending") == 1
