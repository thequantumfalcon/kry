"""Regressions for the two independent deep external audits (2026-06-28).

One test per confirmed finding; each reproduces the demonstrated issue and asserts it is closed.
A1-* = audit 1; F* = audit 2. Pinning tests: if a fix regresses, the matching test fails.
"""
import json

import pytest


# ── A1-1 (HIGH): a promotion superseding a v4/v5 receipt could inflate the anchored floor ──
# v6 binds receipt_id into the chain hash; v4/v5 do NOT, so a v5 receipt_id is mutable. The overlay
# matched superseded receipts by receipt_id (last-wins), so relabeling/colliding a large v5 receipt's
# id onto a promotion's `supersedes` target redirected the promotion's re-tiering onto the large value.
def test_a1_1_v5_promotion_cannot_inflate_anchored_floor():
    import kry.kry_mint as km
    rid_small = "RID-small"
    # A crafted v5 chain (veracity_breakdown trusts the operator's own log, so no chain hashes needed):
    # small self_reported(10), big self_reported(1000) RELABELED onto the small id, and a zero-value
    # tee promotion superseding the small id — the exact relabel exploit, free on v5.
    recs = [
        {"evidence_tier": "self_reported", "kry_minted": 10.0, "receipt_id": rid_small,
         "hash_version": 5, "supersedes": None},
        {"evidence_tier": "self_reported", "kry_minted": 1000.0, "receipt_id": rid_small,   # relabel
         "hash_version": 5, "supersedes": None},
        {"evidence_tier": "tee_attested", "kry_minted": 0.0, "receipt_id": "RID-promo",
         "hash_version": 7, "supersedes": rid_small},
    ]
    km._MINT_LOG_PATH.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    vb = km.veracity_breakdown()
    # The v5 receipts are gated out of the overlay → the promotion finds no hash-bound target →
    # nothing is re-tiered onto tee_attested. (Without the fix this read ~1000/1010.)
    assert vb["anchored_kry"] == 0.0
    assert vb["veracity_floor"] == 0.0


def test_a1_1_duplicate_hash_bound_receipt_id_rejected():
    """A1-1 (defence in depth): even among hash-bound (v6+) receipts, two links sharing a receipt_id
    let the last-wins overlay map pick the larger — so duplicate ids are rejected by both verifiers."""
    pytest.importorskip("kry.kry_attest")
    import kry.kry_attest as ka
    import kry.kry_mint as km
    km.mint("cache_hit", 100.0, evidence="e1", avoided_model="opus")
    km.mint("cache_hit", 200.0, evidence="e2", avoided_model="opus")
    att = json.loads(ka.build_attestation().to_public_json())
    # Force two links to share a receipt_id (a forged chain could do this with valid v6+ hashes).
    rid = att["links"][0].get("receipt_id")
    att["links"][1]["receipt_id"] = rid
    att["attestation_hash"] = ""
    att["attestation_hash"] = ka._attestation_hash(att)
    ok, errs = ka.verify_attestation(json.dumps(att))
    assert not ok
    assert any("duplicate receipt_id" in e or "chain link broken" in e for e in errs), errs


# ── F2 (MED): "externally anchored" overclaimed — the anchor witnesses the event, not the magnitude
def test_f2_veracity_label_is_honest_not_externally_overclaimed():
    import kry.kry_mint as km
    # a [1,1] metered probe backing 100 tokens_saved — the magnitude is NOT bounded by the evidence,
    # and provider_metered is OPERATOR-RUN, so calling the floor "externally anchored" overstates it.
    km.mint("short_circuit", 100.0, "probe", evidence="m1", avoided_model="opus",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[1, 1])
    vb = km.veracity_breakdown()
    assert "anchored_kry" in vb and "externally_anchored_kry" not in vb   # renamed: not all external
    assert vb["anchored_kry"] > 0
    # the note now states plainly that the anchor witnesses the EVENT, not the counterfactual MAGNITUDE
    note = vb["note"]
    assert "operator-run" in note and "MAGNITUDE" in note


# ── A1-4 (MED): the release verifier's dev pins must match pyproject (they had drifted: 9.1.0/0.15.17)
def test_a1_4_release_dev_pins_match_pyproject():
    import importlib.util
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("krv_pins", root / "scripts" / "kry_release_verify.py")
    krv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(krv)
    dev = re.search(r"dev = \[([^\]]*)\]", (root / "pyproject.toml").read_text()).group(1)
    for pin in krv.DEV_REQUIREMENTS:
        assert pin in dev, f"{pin!r} not in pyproject dev pins ({dev})"


# ── A1-1b (HIGH, round 5): a promotion that FORWARD-references a later receipt must not capture it
def test_a1_1b_forward_reference_promotion_cannot_capture_later_receipt():
    """The round-4 fix gated v6+ + duplicates but not ORDER: a zero-value promotion at position 0
    superseding a receipt minted at position 1 still captured it (floor 1.0, chain intact). A
    promotion may now re-tier ONLY a receipt seen EARLIER in the verified scan."""
    import kry.kry_mint as km
    recs = [
        {"evidence_tier": "tee_attested", "kry_minted": 0.0, "receipt_id": "RID-promo",
         "hash_version": 7, "supersedes": "RID-future"},          # promotion FIRST (position 0)
        {"evidence_tier": "self_reported", "kry_minted": 1000.0, "receipt_id": "RID-future",
         "hash_version": 7, "supersedes": None},                  # target appended AFTER (position 1)
    ]
    km._MINT_LOG_PATH.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    vb = km.veracity_breakdown()
    assert vb["anchored_kry"] == 0.0 and vb["veracity_floor"] == 0.0   # forward reference refused


def test_a1_1b_legit_promotion_of_prior_receipt_still_works():
    """Guard: a NORMAL promotion (target minted BEFORE the promotion) must still re-tier."""
    import kry.kry_mint as km
    recs = [
        {"evidence_tier": "self_reported", "kry_minted": 500.0, "receipt_id": "RID-real",
         "hash_version": 7, "supersedes": None},                  # target FIRST
        {"evidence_tier": "tee_attested", "kry_minted": 0.0, "receipt_id": "RID-promo2",
         "hash_version": 7, "supersedes": "RID-real"},            # promotion AFTER
    ]
    km._MINT_LOG_PATH.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    assert km.veracity_breakdown()["veracity_floor"] == 1.0


# ── overlay lock: CONSUME invariant — a target is promoted at most once
def test_overlay_consume_target_promoted_at_most_once():
    """SAFETY CONTRACT invariant 5: two promotions superseding the SAME receipt re-tier its value
    exactly ONCE (the target is consumed). Without consume the value would move twice and the
    anchored fraction would exceed 1.0 (and a source tier would go negative)."""
    import kry.kry_mint as km
    recs = [
        {"evidence_tier": "self_reported", "kry_minted": 1000.0, "receipt_id": "RID-x",
         "hash_version": 7, "supersedes": None},                  # the only positive-value receipt
        {"evidence_tier": "tee_attested", "kry_minted": 0.0, "receipt_id": "P1",
         "hash_version": 7, "supersedes": "RID-x"},               # promotes RID-x
        {"evidence_tier": "tlsn_attested", "kry_minted": 0.0, "receipt_id": "P2",
         "hash_version": 7, "supersedes": "RID-x"},               # tries to promote it AGAIN
    ]
    km._MINT_LOG_PATH.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    vb = km.veracity_breakdown()
    assert vb["anchored_kry"] == 1000.0                       # anchored exactly once, not 2000
    assert vb["veracity_floor"] == 1.0                        # never exceeds 1.0
    assert vb["by_tier"].get("self_reported", 0.0) >= 0.0     # source tier never goes negative


# ── overlay lock: OUTCOME GUARD — the verifier rejects a (future) over-move that drives a tier < 0
def test_overlay_outcome_guard_rejects_overmove(monkeypatch):
    """Defence in depth for a FUTURE overlay bug: if the overlay ever over-moves (drives a tier
    negative), verify_attestation rejects the attestation even though every per-link hash is intact.
    Simulated by patching the overlay to move value that isn't there."""
    import kry.kry_attest as ka
    import kry.kry_mint as km
    km.mint("cache_hit", 100.0, evidence="e", avoided_model="opus")
    att_json = ka.build_attestation().to_public_json()        # built with the REAL (sound) overlay

    def overmoving_overlay(by_tier, promotions, kry_by_receipt):
        by_tier["self_reported"] = by_tier.get("self_reported", 0.0) - 999.0   # value that isn't there

    monkeypatch.setattr(km, "_apply_promotion_overlay", overmoving_overlay)
    ok, errs = ka.verify_attestation(att_json)
    assert not ok
    assert any("went negative" in e for e in errs), errs


# ── F2 schema break (round 5): the rename must accept the OLD field name as a legacy alias
def test_f2_legacy_externally_anchored_field_still_verifies():
    """Renaming externally_anchored_kry → anchored_kry (round 4) broke otherwise-valid older
    attestations. The verifier now accepts the old field as a read-only alias."""
    import kry.kry_attest as ka
    import kry.kry_mint as km
    km.mint("cache_hit", 100.0, evidence="e", avoided_model="opus")
    att = json.loads(ka.build_attestation().to_public_json())
    v = att["veracity"]
    v["externally_anchored_kry"] = v.pop("anchored_kry")        # an old-style (pre-rename) attestation
    att["attestation_hash"] = ""
    att["attestation_hash"] = ka._attestation_hash(att)
    ok, errs = ka.verify_attestation(json.dumps(att))
    assert ok, errs


# ── overlay lock #2 (audit 2026-06-28): a POSITIVE-VALUE promotion link cannot double-count ──
# invariant #4 ("a promotion is itself zero-value") was ASSERTED in the safety contract but never
# ENFORCED at the enqueue: a tlsn/tee link that BOTH minted its own value AND carried `supersedes`
# had its own value booked to the anchored tier, then the overlay moved the target's value on TOP —
# one anchored receipt double-counting an unrelated one (forged veracity_floor 1.0 vs 0.333, confirmed
# passing the stranger verifier). The enqueue now requires the promoting link's own value to be zero,
# at all four overlay sites (kry_mint, kry_attest build + verify, scripts/kry_verify).
def test_overlay_positive_value_promotion_cannot_double_count():
    import kry.kry_mint as km
    recs = [
        {"evidence_tier": "self_reported", "kry_minted": 1000.0, "receipt_id": "RID-A",
         "hash_version": 7, "supersedes": None},
        {"evidence_tier": "tlsn_attested", "kry_minted": 500.0, "receipt_id": "RID-P",
         "hash_version": 7, "supersedes": "RID-A"},   # earns its OWN 500 AND supersedes -> NOT a promotion
    ]
    km._MINT_LOG_PATH.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    vb = km.veracity_breakdown()
    assert vb["anchored_kry"] == 500.0                      # the tlsn link's own 500 ONLY, not 1500
    assert vb["veracity_floor"] == round(500 / 1500, 4)     # 0.3333, not the forged 1.0
    assert vb["by_tier"].get("self_reported") == 1000.0     # the self_reported 1000 stays put


# ── MINT-1 (audit 2026-06-28): fresh-T2/tee mint dedup must be ATOMIC with the append (under the lock) ──
# The fresh-mint path credited T2 value when no prior receipt existed; two presentations of the SAME
# provider generation / measurement that differ only in transient bytes (Date header, notary sig) produce
# different evidence_hashes, so the byte-identical replay decay can't collapse them. The dedup check ran
# BEFORE mint() and outside its lock (racy). mint() now takes an in-lock dedup_check. (We also extended
# the fix to the tee/snp fresh path, which had NO fresh-dedup at all — same race class.)
def test_mint_dedup_check_refuses_duplicate_fresh_t2_gen():
    import kry.kry_mint as km
    common = dict(avoided_model="opus", evidence_tier=km.TIER_TLSN_ATTESTED)
    dc = lambda: km._find_fresh_t2_receipt_for_gen("gen-DUP") is not None  # noqa: E731
    r1 = km.mint("displacement", 100.0, detail="tlsn /openrouter:gen-DUP", evidence="bytes-A",
                 dedup_check=dc, **common)
    assert r1 is not None and r1.kry_minted > 0
    # second presentation: DIFFERENT bytes -> decay can't catch it, but the in-lock dedup_check must.
    r2 = km.mint("displacement", 100.0, detail="tlsn /openrouter:gen-DUP", evidence="bytes-B",
                 dedup_check=dc, **common)
    assert r2 is None, "second fresh-T2 mint of the same gen id must be refused under the lock"


def test_mint_dedup_check_refuses_duplicate_fresh_tee_measurement():
    import kry.kry_mint as km
    common = dict(avoided_model="opus", evidence_tier=km.TIER_TEE_ATTESTED)
    dc = lambda: km._find_fresh_tee_receipt_for_measurement("meas-DUP") is not None  # noqa: E731
    r1 = km.mint("holdout", 100.0, detail="tee /measurement:meas-DUP", evidence="att-A",
                 dedup_check=dc, **common)
    assert r1 is not None and r1.kry_minted > 0
    r2 = km.mint("holdout", 100.0, detail="tee /measurement:meas-DUP", evidence="att-B",
                 dedup_check=dc, **common)
    assert r2 is None, "second fresh-tee mint of the same measurement id must be refused under the lock"


# ── new control (opt-in): per-window issuance cap, default OFF ──
def test_issuance_window_cap_opt_in(monkeypatch):
    """With KRY_MINT_WINDOW_CAP set, minting beyond the cap in the rolling window is refused; unset
    (the default), minting is unbounded. Bounds honest-but-fabricated at-scale minting for operators
    who opt in — conservation governs transfer only, decay only collapses byte-identical replays."""
    import kry.kry_mint as km
    monkeypatch.setenv("KRY_MINT_WINDOW_CAP", "150")
    monkeypatch.setenv("KRY_MINT_WINDOW_SEC", "3600")
    assert km.mint("cache_hit", 100.0, evidence="e1", avoided_model="opus") is not None   # 100 <= 150
    assert km.mint("cache_hit", 100.0, evidence="e2", avoided_model="opus") is None        # 200 > 150 -> refused
    # cap removed -> unbounded again (default behaviour)
    monkeypatch.delenv("KRY_MINT_WINDOW_CAP")
    assert km.mint("cache_hit", 100.0, evidence="e3", avoided_model="opus") is not None
