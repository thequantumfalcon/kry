"""Round-5 audit regressions — one test per finding (4 HIGH, 3 MEDIUM).

Each test reproduces the ORIGINAL exploit from the combined Claude+GPT review and asserts it is
now closed. They are pinning tests: if a future change reopens any finding, the matching test fails.
"""
import json
import os
import subprocess
import sys
import importlib.util

import pytest


# ── F1 (HIGH): spend() did not validate output_tokens — negative inflated the balance, NaN poisoned it
def test_f1_spend_rejects_negative_and_nan_output_tokens():
    import kry.kry_token as t
    t.earn(100000, "cache_hit", "seed")
    before = t.get_ledger().balance
    with pytest.raises(ValueError):
        t.spend("or/anthropic/claude-opus-4.8", -1000)     # was: balance 100000 -> 101000
    with pytest.raises(ValueError):
        t.spend("or/anthropic/claude-opus-4.8", float("nan"))  # was: balance -> nan
    assert t.get_ledger().balance == before                # no inflation, no poison
    with pytest.raises(ValueError):
        t.spend_cost("or/anthropic/claude-opus-4.8", -5)   # validated at the choke point


# ── F2 (HIGH): a promotion's `supersedes` was not hash-bound — re-pointing it inflated veracity_floor
def test_f2_promotion_supersedes_is_tamper_evident():
    import kry.kry_mint as km
    km.mint("displacement", 1000, "served via cheap leg /openrouter:gen-f2",
            avoided_model="gh/claude-opus-4.8")
    assert km.promote_to_tlsn("gen-f2", "tlsn:bytes", "T2") is not None
    assert km.verify_chain()[0]                  # legit promotion: supersedes bound at creation
    lines = km._MINT_LOG_PATH.read_text().splitlines()
    promo = json.loads(lines[-1])
    assert promo.get("supersedes")               # the promotion carries a bound target
    promo["supersedes"] = "KRY-99999999"         # re-point it at a different receipt (the exploit)
    lines[-1] = json.dumps(promo)
    km._MINT_LOG_PATH.write_text("\n".join(lines) + "\n")
    ok, _ = km.verify_chain()
    assert not ok                                # F2: supersedes is chain-bound → re-pointing breaks it


# ── F3 (HIGH): the PACKAGE verifier accepted a forged legacy (pre-v4) non-self_reported tier
def test_f3_package_verifier_rejects_forged_legacy_tier():
    import kry.kry_mint as km
    import kry.kry_attest as ka
    km.mint("cache_hit", 1000.0, evidence="e1")
    d = json.loads(ka.build_attestation().to_public_json())
    d["links"][0]["hash_version"] = 2            # downgrade to a version that does not bind the tier
    d["links"][0]["evidence_tier"] = "tee_attested"   # forge an external tier to steal veracity
    ok, errs = ka.verify_attestation(json.dumps(d))
    assert not ok
    assert any("cannot carry a non-self_reported" in e for e in errs), errs  # the ported guard fired


# ── F4 (HIGH): settle() debited A before the registry commit — a commit failure destroyed value
def test_f4_settlement_does_not_debit_on_commit_failure():
    import kry.kry_settlement as s
    a = {"bal": 1000.0}

    def debit(k):
        a["bal"] -= k
        return k

    recv = s.ReceiverLedger("B")
    offer = s.make_offer("A", "B", 100.0, 1000, now=1.0)
    grant = s.RoutingGrant(grant_id="gF4", offer_id=offer.offer_id, granted_by="B",
                           routing_tokens=1000, accepted_kry=100.0, ts=1.0)
    orig = s._record_settled

    def boom(*args, **kwargs):
        raise s.SettlementPersistenceError("simulated registry write failure")

    s._record_settled = boom
    try:
        with pytest.raises(s.SettlementPersistenceError):
            s.settle(offer, grant, debit_a_fn=debit, receiver=recv, a_balance_before=1000.0)
    finally:
        s._record_settled = orig
    assert a["bal"] == 1000.0          # F4: commit-first — the debit never ran
    assert recv.received_kry == 0.0    # B not credited either; nothing moved


# ── F5 (MED): the public attestation ignored the promotion overlay → its floor disagreed with internal
def test_f5_public_and_internal_veracity_floor_agree_after_promotion():
    import kry.kry_mint as km
    import kry.kry_attest as ka
    km.mint("displacement", 1000, "served via cheap leg /openrouter:gen-f5",
            avoided_model="gh/claude-opus-4.8")               # self_reported value
    assert km.promote_to_tlsn("gen-f5", "tlsn:bytes", "T2") is not None
    internal = km.veracity_breakdown()["veracity_floor"]
    public = json.loads(ka.build_attestation().to_public_json())["veracity"]["veracity_floor"]
    assert internal > 0.0                       # the promotion lifted the internal floor
    assert abs(internal - public) < 1e-9        # F5: the public surface now applies the SAME overlay


# ── F6 (MED): KRY_MINT_DECAY only required >= 0 — 1 disabled replay decay, > 1 amplified it
def test_f6_decay_env_rejects_out_of_range():
    for value, expected in [("1", 0.5), ("2", 0.5), ("1.5", 0.5), ("-0.1", 0.5), ("0.3", 0.3)]:
        env = {**os.environ, "KRY_MINT_DECAY": value, "PYTHONPATH": "src"}
        out = subprocess.run([sys.executable, "-c", "import kry.kry_mint as m; print(m._DECAY)"],
                             capture_output=True, text=True, env=env, cwd=os.getcwd())
        assert float(out.stdout.strip()) == expected, (value, out.stdout, out.stderr)


# ── F7 (MED): the TLSN receipt bound only the recv transcript, not the full verifier presentation
def test_f7_evidence_binds_full_presentation():
    spec = importlib.util.spec_from_file_location("ktv_f7", "scripts/kry_tlsn_verify.py")
    ktv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ktv)
    base = {"verified": True, "server_name": "openrouter.ai",
            "notary_key": "ab" * 32, "recv": "HTTP/1.1 200 OK\r\n\r\n{}"}
    b1 = ktv._evidence_binding(base)
    b2 = ktv._evidence_binding({**base, "proof": "tampered-or-extra"})   # differs OUTSIDE recv
    assert b1 != b2                                     # F7: the FULL presentation is bound now
    assert ktv._evidence_binding(dict(base)) == b1     # replay of the same presentation is stable
