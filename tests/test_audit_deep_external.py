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
