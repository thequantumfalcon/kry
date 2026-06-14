"""Counterfactual holdout (kry_baseline) — the measured answer to "would the call
have happened?".

These pin the discovery: a randomized holdout MEASURES the counterfactual rate with
a confidence interval, the treated population is valued CONSERVATIVELY (CI lower
bound), unmeasured classes earn nothing (fail-closed), and a holdout-validated mint
lifts veracity_floor above the pure-self-report 0.0 — honestly, on its own tier.
"""
from __future__ import annotations

import kry.kry_baseline as kb


# ── Deterministic, auditable holdout assignment ───────────────────────────────

def test_holdout_assignment_is_deterministic():
    assert kb.is_holdout("req-123") == kb.is_holdout("req-123")  # stable for same id
    assert 0.0 <= kb.holdout_score("req-123") < 1.0

def test_holdout_rate_is_approximately_honored():
    # Over many ids, ~HOLDOUT_RATE land in holdout (statistical, generous tolerance).
    n = 20000
    hits = sum(kb.is_holdout(f"req-{i}", rate=0.05) for i in range(n))
    assert 0.04 < hits / n < 0.06

def test_holdout_rate_bounds():
    assert kb.is_holdout("anything", rate=0.0) is False   # measurement off
    assert kb.is_holdout("anything", rate=1.0) is True    # force all


# ── Wilson score interval ─────────────────────────────────────────────────────

def test_wilson_unmeasured_is_full_uncertainty():
    assert kb.wilson_interval(0, 0) == (0.0, 1.0)  # no data -> conservative lo=0

def test_wilson_known_value():
    lo, hi = kb.wilson_interval(8, 10)              # textbook ~[0.490, 0.943]
    assert abs(lo - 0.490) < 0.01 and abs(hi - 0.943) < 0.01

def test_wilson_extremes_stay_in_unit_interval():
    lo0, hi0 = kb.wilson_interval(0, 10)
    assert lo0 == 0.0 and 0.0 < hi0 < 1.0
    lo1, hi1 = kb.wilson_interval(10, 10)
    assert hi1 == 1.0 and 0.0 < lo1 < 1.0


# ── Observation + estimation ──────────────────────────────────────────────────

def test_estimate_reflects_observations():
    for _ in range(7):
        kb.observe_holdout("classC", hit_paid=True)
    for _ in range(3):
        kb.observe_holdout("classC", hit_paid=False)
    kb.observe_treated("classC", n=500)
    est = kb.avoidance_estimate("classC")
    assert est["holdout_n"] == 10 and est["holdout_paid_n"] == 7
    assert est["p_hat"] == 0.7
    assert 0.0 < est["ci_lo"] < est["p_hat"] < est["ci_hi"] <= 1.0
    assert est["treated_n"] == 500 and est["measured"] is True

def test_unmeasured_class_is_failed_closed():
    est = kb.avoidance_estimate("never-seen")
    assert est["measured"] is False and est["p_hat"] is None
    assert kb.holdout_adjusted_tokens("never-seen", 1000) == 0.0  # no baseline -> no credit


# ── Conservative valuation ────────────────────────────────────────────────────

def test_holdout_adjusted_tokens_is_conservative():
    for _ in range(8):
        kb.observe_holdout("cls", hit_paid=True)
    for _ in range(2):
        kb.observe_holdout("cls", hit_paid=False)
    est = kb.avoidance_estimate("cls")
    raw = 1000.0
    conservative = kb.holdout_adjusted_tokens("cls", raw, conservative=True)
    point = kb.holdout_adjusted_tokens("cls", raw, conservative=False)
    assert conservative == raw * est["ci_lo"]
    assert point == raw * est["p_hat"]
    assert conservative < point  # the CI lower bound never overclaims vs the point


# ── End-to-end: holdout-validated mint lifts veracity_floor honestly ──────────

def test_holdout_validated_mint_raises_veracity_floor():
    import kry.kry_mint as km
    import kry.kry_token as kt
    kt._ledger_instance = kt.KRYLedger()

    # Measure a baseline for the class (randomized holdout, real outcomes).
    for _ in range(9):
        kb.observe_holdout("summarize", hit_paid=True)
    kb.observe_holdout("summarize", hit_paid=False)        # p_hat = 0.9

    # A pure self-reported cache hit (the old, unanchored way).
    km.mint("cache_hit", 1000, "plain", evidence="e0", avoided_model="gh/claude-opus-4.8")
    floor_before = km.veracity_breakdown()["veracity_floor"]

    # A holdout-validated cache hit: tokens scaled to the measured-conservative
    # counterfactual, tagged with the holdout_validated tier.
    adj = kb.holdout_adjusted_tokens("summarize", 1000)
    assert 0 < adj < 1000                                   # conservatively discounted
    km.mint("cache_hit", adj, "validated", evidence="e1",
            avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_HOLDOUT_VALIDATED)

    vb = km.veracity_breakdown()
    assert km.verify_chain()[0]                             # chain still intact
    assert vb["veracity_floor"] > floor_before              # anchored share rose
    assert 0.0 < vb["veracity_floor"] < 1.0                 # honest: not 0, not 1
    assert km.TIER_HOLDOUT_VALIDATED in vb["by_tier"]       # exposed on its own line


def test_holdout_report_shape():
    kb.observe_holdout("a", hit_paid=True)
    kb.observe_treated("a", n=10)
    rep = kb.holdout_report()
    assert rep["holdout_rate"] == kb.HOLDOUT_RATE
    assert rep["total_holdout_calls"] >= 1
    assert any(c["request_class"] == "a" for c in rep["classes"])
    assert "incrementality" in rep["method"]
