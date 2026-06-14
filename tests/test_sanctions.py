"""KRY Sanctions — make cheating not pay (nature's anti-fabrication mechanism).

Pins the ESS honesty-stability arithmetic and the host-sanction / reciprocal-reward
reputation dynamics ported from Kiers 2003/2011 + costly-signalling theory.
"""
from __future__ import annotations

import math

import pytest

import kry.kry_sanctions as ks


# ── ESS / honesty-stability arithmetic ────────────────────────────────────────

def test_min_audit_rate_matches_penalty():
    assert abs(ks.min_audit_rate(49) - 0.02) < 1e-9   # 49x penalty -> 2% audit suffices
    assert abs(ks.min_audit_rate(9) - 0.10) < 1e-9
    assert ks.min_audit_rate(0) == 1.0                # no penalty -> must audit everything

def test_min_penalty_matches_audit():
    assert abs(ks.min_penalty(0.02) - 49.0) < 1e-9
    assert abs(ks.min_penalty(0.10) - 9.0) < 1e-9
    assert ks.min_penalty(1.0) == 0.0

def test_honesty_stability_threshold():
    assert ks.honesty_is_stable(0.02, 49)["honesty_stable"] is True    # exactly on the line
    assert ks.honesty_is_stable(0.10, 9)["honesty_stable"] is True
    assert ks.honesty_is_stable(0.05, 9)["honesty_stable"] is False    # 0.05*10 = 0.5 < 1
    assert ks.honesty_is_stable(0.02, 49)["stability_score"] == 1.0


# ── Reputation: host sanctions + reciprocal reward ────────────────────────────

def test_unseen_party_has_neutral_prior():
    assert ks.reputation("newcomer") == ks._PRIOR

def test_confirmation_builds_reputation_slowly():
    r0 = ks.reputation("p")
    r1 = ks.record_reconciliation("p", confirmed=True)
    assert r1 > r0 and r1 < 1.0
    # repeated confirmations approach but never exceed 1
    for _ in range(50):
        r = ks.record_reconciliation("p", confirmed=True)
    assert 0.99 < r <= 1.0

def test_discrepancy_sanctions_hard_and_asymmetrically():
    # one confirmation then one discrepancy must leave reputation BELOW the prior:
    # losing trust is faster than gaining it (the host-sanction asymmetry).
    ks.record_reconciliation("q", confirmed=True)      # small gain
    r_after_sanction = ks.record_reconciliation("q", confirmed=False)  # multiplicative cut
    assert r_after_sanction < ks._PRIOR
    rec = ks.sanctions_report()["parties"]["q"]
    assert rec["confirmed"] == 1 and rec["discrepancy"] == 1

def test_audit_rate_escalates_as_reputation_falls():
    # a trusted party is audited near the floor; a sanctioned one near the ceiling.
    for _ in range(40):
        ks.record_reconciliation("good", confirmed=True)
    for _ in range(6):
        ks.record_reconciliation("bad", confirmed=False)
    good_rate = ks.audit_rate_for("good")
    bad_rate = ks.audit_rate_for("bad")
    assert good_rate < 0.1                  # reciprocal reward: light touch
    assert bad_rate > 0.5                   # escalating sanction: heavy audit
    assert good_rate >= ks._AUDIT_MIN and bad_rate <= ks._AUDIT_MAX


# ── Two-signal (immune costimulation) trust rule ──────────────────────────────

def test_two_signal_trust_ignores_self_report():
    t = ks.two_signal_trust(self_reported_kry=1000.0, anchored_kry=0.0)
    assert t["trusted_kry"] == 0.0 and t["anergic_kry"] == 1000.0
    assert t["trust_fraction"] == 0.0       # signal-1 only -> anergy, no trust
    t2 = ks.two_signal_trust(self_reported_kry=600.0, anchored_kry=400.0)
    assert t2["trusted_kry"] == 400.0 and abs(t2["trust_fraction"] - 0.4) < 1e-9


def test_fabrication_is_unprofitable_under_recommended_settings():
    """The headline: at the package defaults a fabricated claim does not pay. With
    the 2% audit floor, the penalty needed is 49x — and at >=49x, honesty is stable."""
    h = ks._AUDIT_MIN                       # 0.02
    needed = ks.min_penalty(h)              # 49
    assert ks.honesty_is_stable(h, needed)["honesty_stable"]


def test_corrupted_reputation_state_fails_closed():
    ks._REP_PATH.write_text(
        '{"party-a":{"reputation":NaN,"confirmed":10,"discrepancy":0,"updated":1.0}}\n'
    , encoding="utf-8")
    before = ks._REP_PATH.read_text(encoding="utf-8")

    assert ks.reputation("party-a") == 0.0
    assert ks.audit_rate_for("party-a") == ks._AUDIT_MAX
    assert ks.record_reconciliation("party-a", confirmed=True) == 0.0
    report = ks.sanctions_report()

    assert report["state_valid"] is False
    assert "non-standard JSON constant rejected: NaN" in report["errors"][0]
    assert ks._REP_PATH.read_text(encoding="utf-8") == before


def test_sanctions_math_rejects_nonfinite_inputs():
    with pytest.raises(ValueError, match="penalty_lambda must be finite"):
        ks.min_audit_rate(float("nan"))
    with pytest.raises(ValueError, match="audit_rate must be finite"):
        ks.min_penalty(float("nan"))
    with pytest.raises(ValueError, match="self_reported_kry must be finite"):
        ks.two_signal_trust(float("inf"), 0.0)

    assert math.isinf(ks.min_penalty(0.0))
