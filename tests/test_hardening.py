"""Adversarial regression tests — the attacks we ran, fixed, and now keep fixed.

Each test corresponds to a real vulnerability found by trying to break the system:
  - settlement registry tail TRUNCATION (rollback/double-spend) — HOLE F
  - NEGATIVE / non-finite settlement offers (value-reversal theft vector)
  - cache-replay minting bounded by geometric decay (supply guarantee)
  - compaction must advance the tip checkpoint (no false truncation flag)
"""
from __future__ import annotations

import kry.kry_attest as ka
import kry.kry_mint as km
import kry.kry_settlement as ks


def _fresh_chain():
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = "0" * 64
    km._evidence_mints = {}
    km._decay_loaded = True


def _attestation():
    _fresh_chain()
    km.mint("cache_hit", 1000, "x", evidence="x", avoided_model="gh/claude-opus-4.8")
    return ka.build_attestation().to_public_json()


# ── HOLE F: registry tail truncation / rollback ──────────────────────────────

def test_registry_tail_truncation_is_detected():
    ks._record_settled("A", 1000.0)
    ks._record_settled("A", 2000.0)
    assert ks.verify_registry()[0]
    assert ks._load_registry().get("A") == 3000.0

    # drop the last entry — the surviving prefix is a still-valid hash chain, but
    # the cumulative settled total silently falls, freeing balance to re-spend.
    lines = ks._REGISTRY_PATH.read_text(encoding="utf-8").splitlines()
    ks._REGISTRY_PATH.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    ok, errs = ks.verify_registry()
    assert not ok and any("truncat" in e.lower() for e in errs)
    # and the double-spend guard fails closed on a tampered registry
    assert ks._load_registry().get("__tampered__") is True


# ── Negative / non-finite offers (value-reversal theft) ───────────────────────

def test_nonpositive_and_nonfinite_offers_rejected():
    att = _attestation()
    for amt in (-5000.0, 0.0, float("nan"), float("inf")):
        grant, _ = ks.verify_and_accept(
            ks.make_offer("A", "B", amt, 1000, now=1.0), att, now=2.0)
        assert grant is None, f"offer amount {amt} must be rejected"
    # bad routing_tokens too
    grant, _ = ks.verify_and_accept(
        ks.make_offer("A", "B", 500.0, 0, now=1.0), att, now=2.0)
    assert grant is None
    # a legitimate positive offer still goes through
    grant, _ = ks.verify_and_accept(
        ks.make_offer("Z", "B", 500.0, 1000, now=1.0), att, now=2.0)
    assert grant is not None


def test_negative_offer_cannot_reverse_value_via_conservation():
    """The theft vector: a negative offer once passed the conservation check while
    driving the receiver's balance negative. It must never reach settle()."""
    att = _attestation()
    grant, reason = ks.verify_and_accept(
        ks.make_offer("A", "B", -5000.0, 1000, now=1.0), att, now=2.0)
    assert grant is None and "positive" in reason


# ── Supply: cache-replay bounded by geometric decay ───────────────────────────

def test_replay_of_one_evidence_is_decay_bounded():
    """Replaying ONE cached response can't mint unbounded KRY: the geometric series
    caps total at tokens/(1-decay) = 1000/0.5 = 2000 (rate 1.0, multiplier 1.0)."""
    _fresh_chain()
    total = 0.0
    for _ in range(500):
        r = km.mint("cache_hit", 1000, "same", evidence="SAME_EVIDENCE",
                    avoided_model="gh/claude-opus-4.8")
        if r:
            total += r.kry_minted
    assert total <= 2000.0 + 1e-3      # never exceeds the cap
    assert total > 1900.0              # and converges to it (distinct from a single mint)


# ── Compaction advances the tip (no false truncation) ─────────────────────────

def test_compaction_advances_tip_and_preserves_totals():
    for _ in range(30):
        ks._record_settled("A", 1.0)
    ks._record_settled("B", 7.0)
    before = ks._load_registry()
    assert ks.compact_registry(keep_recent=5) is True   # 31 > 2*5 -> compacts
    ok, errs = ks.verify_registry()
    assert ok, errs                                      # not falsely flagged truncated
    after = ks._load_registry()
    assert abs(after.get("A", 0) - before["A"]) < 1e-9   # per-party totals preserved
    assert abs(after.get("B", 0) - before["B"]) < 1e-9


def test_mint_log_tail_truncation_is_detected():
    """GPT-H2 regression: verify_chain must catch a rolled-back (truncated) mint log via the
    monotonic {count, tip} checkpoint — the chain replay alone validates any shorter prefix."""
    from kry import kry_mint
    kry_mint.mint(event_type="cache_hit", tokens_saved=5000, evidence="trunc-ev-1")
    kry_mint.mint(event_type="cache_hit", tokens_saved=5000, evidence="trunc-ev-2")
    ok, errs = kry_mint.verify_chain()
    assert ok, errs
    lines = kry_mint._MINT_LOG_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
    kry_mint._MINT_LOG_PATH.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")   # drop the last receipt
    ok2, errs2 = kry_mint.verify_chain()
    assert not ok2
    assert any("truncated" in e for e in errs2), errs2


def test_corrupt_decay_file_fails_closed_no_mint():
    """GPT remediation regression: a present-but-unparseable decay state file must NOT reset to {} and
    re-open first-avoidance full credit — minting fails closed (no new receipt) until repaired."""
    from kry import kry_mint
    kry_mint.mint(event_type="cache_hit", tokens_saved=1000, evidence="corrupt-ev-1")
    n_before = len(kry_mint._MINT_LOG_PATH.read_text(encoding="utf-8").splitlines())
    kry_mint._DECAY_STATE_PATH.write_text("}{ not valid json", encoding="utf-8")
    r = kry_mint.mint(event_type="cache_hit", tokens_saved=1000, evidence="corrupt-ev-2")
    assert r is None                              # fail-closed on the corrupt-decay RuntimeError
    n_after = len(kry_mint._MINT_LOG_PATH.read_text(encoding="utf-8").splitlines())
    assert n_after == n_before                    # no new receipt minted
