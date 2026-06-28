"""Regression tests for the settlement / tier / reconcile audit (S1-S9, H-class).

Each test reproduces the audit's exploit against the live code and asserts it is now closed.
"""
import hashlib
import importlib.util
import json

import pytest


def _stdlib_verifier():
    spec = importlib.util.spec_from_file_location("kv_audit", "scripts/kry_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tlsn():
    spec = importlib.util.spec_from_file_location("ktv_audit", "scripts/kry_tlsn_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── S1 (HIGH): settlement could spend pure self_reported, veracity_floor=0 KRY with no policy ──
def test_s1_settlement_min_veracity_floor_policy():
    import kry.kry_attest as a
    import kry.kry_mint as m
    import kry.kry_settlement as s
    for i in range(3):
        m.mint("cache_hit", 1000, evidence=f"s1-{i}", avoided_model="gh/claude-opus-4.8")
    att = a.build_attestation().to_public_json()
    assert json.loads(att)["veracity"]["veracity_floor"] == 0.0
    o = s.make_offer("A", "B", 100.0, 1000, now=1.0)
    g, _ = s.verify_and_accept(o, att, now=1.0)                          # default accepts (back-compat)
    assert g is not None
    g2, why = s.verify_and_accept(o, att, now=1.0, min_veracity_floor=1.0)
    assert g2 is None and "veracity floor" in why                       # opt-in policy rejects unanchored


# ── S2 (HIGH): settle recorded the full amount while a debit moved zero (conserved=True) ──
def test_s2_settlement_debit_must_match_committed_amount():
    import kry.kry_settlement as s
    recv = s.ReceiverLedger("B")
    o = s.make_offer("A", "B", 100.0, 1000, now=1.0)
    g = s.RoutingGrant(grant_id="gS2", offer_id=o.offer_id, granted_by="B",
                       routing_tokens=1000, accepted_kry=100.0, ts=1.0)
    with pytest.raises(s.SettlementPersistenceError):
        s.settle(o, g, debit_a_fn=lambda amt: 0.0, receiver=recv, a_balance_before=1000.0)
    assert recv.received_kry == 0.0


# ── S3 (HIGH): a forged/unknown tier ("magic_attested") was counted as externally anchored ──
def test_s3_forged_unknown_tier_does_not_inflate_floor():
    import kry.kry_attest as a
    import kry.kry_mint as m
    m.mint("cache_hit", 1000.0, evidence="s3")
    d = json.loads(a.build_attestation().to_public_json())
    link = d["links"][0]
    link["evidence_tier"] = "magic_attested"
    # Reconstruct the chain_hash at the link's OWN version (v6, incl. receipt_id) so the chain stays
    # internally consistent and the rejection comes purely from the S3 unknown-tier check — not an
    # incidental chain mismatch from reconstructing at the wrong version.
    block = m._v4_public_block(hash_version=link["hash_version"], tokens_saved=link["tokens_saved"],
                               ts=link["ts"], evidence_tier="magic_attested",
                               metered_tokens=link.get("metered_tokens"),
                               kry_minted=link["kry_minted"], earn_rate=link["earn_rate"],
                               supersedes=link.get("supersedes"), receipt_id=link.get("receipt_id"),
                               event_type=link.get("event_type"))
    link["chain_hash"] = hashlib.sha256(
        ("0" * 64 + ":" + link["receipt_hash"] + ":" + block).encode()).hexdigest()
    d["chain_head"] = link["chain_hash"]
    d["veracity"] = {"by_tier": {"magic_attested": link["kry_minted"]},
                     "anchored_kry": link["kry_minted"], "self_reported_kry": 0.0,
                     "veracity_floor": 1.0}
    d["attestation_hash"] = a._attestation_hash(d)
    assert not a.verify_attestation(json.dumps(d))[0]                   # package verifier rejects
    assert not _stdlib_verifier().verify_attestation(d)[0]             # stdlib stranger verifier rejects


# ── S4 (HIGH): reconcile_ledger_from_chain reported success but didn't restore the disk ledger ──
def test_s4_reconcile_restores_deleted_and_polluted_ledger():
    import kry.kry_mint as m
    import kry.kry_token as kt
    m.mint("cache_hit", 1000.0, evidence="s4", avoided_model="gh/claude-opus-4.8")
    kt._LEDGER_PATH.unlink(missing_ok=True)            # deleted ledger
    kt._ledger_instance = None
    res = m.reconcile_ledger_from_chain()
    assert res["reconciled"] and res["balance_kry"] > 0
    assert json.loads(kt._LEDGER_PATH.read_text())["balance"] == res["balance_kry"]
    assert kt.get_ledger().balance == res["balance_kry"]
    kt._ledger_instance = None                          # polluted ledger -> chain total wins
    kt._LEDGER_PATH.write_text(json.dumps({"balance": 9999.0, "total_earned": 9999.0,
                                           "total_spent": 0.0, "cycle_count": 0}))
    res2 = m.reconcile_ledger_from_chain()
    assert abs(json.loads(kt._LEDGER_PATH.read_text())["balance"] - res2["balance_kry"]) < 1e-9


# ── S5 (HIGH): one receipt promoted to BOTH tlsn AND tee -> veracity_floor 2.0, negative self_reported ──
def test_s5_receipt_cannot_be_promoted_to_two_anchored_tiers():
    import kry.kry_mint as m
    m.mint("displacement", 1000, "served /openrouter:gen-x measurement:meas-x",
           avoided_model="gh/claude-opus-4.8")
    assert m.promote_to_tlsn("gen-x", "tlsn:b", "T2") is not None
    assert m.promote_to_tee("meas-x", "tee:b", "T2") is None            # 2nd anchored promotion blocked
    vb = m.veracity_breakdown()
    assert 0.0 <= vb["veracity_floor"] <= 1.0                           # no impossible floor
    assert vb["by_tier"].get("self_reported", 0.0) >= 0.0              # no negative tier


# ── S6 (MED): TLSN minted full avoided value with a real served cost but no served model ──
def test_s6_tlsn_refuses_full_credit_when_served_cost_unaccounted():
    ktv = _tlsn()

    def pres(cost):
        body = ('{"data":{"id":"gen-x","native_tokens_prompt":120,"native_tokens_completion":300,'
                '"total_cost":%s,"provider_name":"DeepInfra"}}' % cost)
        recv = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + body
        return {"verified": True, "server_name": "openrouter.ai", "recv": recv,
                "notary_key": "ab" * 32,
                "sent": "GET /api/v1/generation?id=gen-x HTTP/1.1\r\nHost: openrouter.ai\r\n\r\n"}

    kw = dict(expect_server="openrouter.ai", event_type="displacement",
              avoided_model="gh/claude-opus-4.8", served_model=None, tokens_saved=None,
              require_status=200, dry_run=False, expect_notary="ab" * 32)
    real_cost = ktv.run(pres("0.5"), **kw)             # real served cost, unknown served model
    assert real_cost["verdict"] == "NO_SERVED_MODEL"   # refuse rather than over-credit
    free_leg = ktv.run(pres("0.0"), **kw)              # $0 served leg -> full value is correct
    assert free_leg["verdict"] == "OK" and free_leg["minted"] is not None
    # a cost we cannot net to zero must NEVER mint full credit. numeric non-finite (NaN/Inf) is caught at
    # the parse layer (REJECTED); a string-typed total_cost ("garbage") parses past it but float() raises,
    # so the original gate treated it as $0 and over-credited — the S6 gate now refuses it (NO_SERVED_MODEL).
    for un_nettable in ("NaN", "Infinity", '"garbage"'):
        r = ktv.run(pres(un_nettable), **kw)
        assert r.get("minted") is None and r["verdict"] in ("REJECTED", "NO_SERVED_MODEL")

    # a BLANK served model ("model":"" / "   ") is non-None but falsy: it must NOT skip the served-cost
    # gate (which keyed on `served is None`) while netting nothing -> full credit on a real cost.
    def pres_model(model):
        body = ('{"data":{"id":"gen-x","native_tokens_prompt":120,"native_tokens_completion":300,'
                '"total_cost":0.5,"model":%s,"provider_name":"DeepInfra"}}' % json.dumps(model))
        recv = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + body
        return {"verified": True, "server_name": "openrouter.ai", "recv": recv,
                "notary_key": "ab" * 32,
                "sent": "GET /api/v1/generation?id=gen-x HTTP/1.1\r\nHost: openrouter.ai\r\n\r\n"}

    for blank in ("", "   "):
        r = ktv.run(pres_model(blank), **kw)
        assert r.get("minted") is None and r["verdict"] == "NO_SERVED_MODEL"


# ── S7 (MED): standalone CLI verifier now has a registry-anchor rollback check ──
def test_s7_registry_anchor_detects_rollback():
    kv = _stdlib_verifier()
    # a live registry where alice has cumulatively settled 100 (one hash-chained entry)
    eh = hashlib.sha256(f"{'0' * 64}:alice:100.0:g1".encode()).hexdigest()
    entries = [{"party": "alice", "amount": 100.0, "grant_id": "g1",
                "prev_hash": "0" * 64, "entry_hash": eh}]
    good = {"schema": "kry_settlement_anchor/v1", "settled": {"alice": 100.0}}
    assert kv.verify_registry_anchor(entries, good)[0]                  # live == anchored: OK
    rollback = {"schema": "kry_settlement_anchor/v1", "settled": {"alice": 500.0}}
    ok, errs = kv.verify_registry_anchor(entries, rollback)             # live 100 < anchored 500
    assert not ok and any("rollback/un-spend" in e for e in errs)


# ── S9 (MED): harden-runner present on the CI runner ──
def test_s9_ci_has_harden_runner():
    from pathlib import Path
    ci = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    text = ci.read_text()
    assert "step-security/harden-runner" in text
    assert text.count("step-security/harden-runner") >= 3   # all three jobs hardened
