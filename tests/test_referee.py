"""kry_referee — adversarial-stability gate + ascension.

Closes a real coverage gap surfaced by the capability audit: kry_referee shipped
untested. Pins the four immune properties that matter: a consistent decision passes,
an induced inconsistency is QUARANTINED but the decision STANDS (no free pass), and
ascension is operator-gated with an intact evidence chain (escalation → ratify).
"""
from __future__ import annotations

import json

import pytest

import kry.kry_referee as kr


@pytest.fixture(autouse=True)
def _reset_referee():
    kr._RECENT.clear()
    kr._esc_times.clear()
    yield


def test_consistent_decision_passes_clean():
    v = kr.review_gate_decision(gate_class=kr.GateClass.BUDGET, rule="daily=200",
                                outcome="allow", confidence="clear", model="m")
    assert v.consistent and not v.quarantined and v.decision == "allow"


def test_induced_inconsistency_quarantines_but_decision_stands():
    # same class+rule+confidence, opposite outcome -> inconsistent
    kr.review_gate_decision(gate_class=kr.GateClass.CRR_SOFT, rule="crr=0.5",
                            outcome="allow", confidence="clear", model="m")
    v = kr.review_gate_decision(gate_class=kr.GateClass.CRR_SOFT, rule="crr=0.5",
                                outcome="redirect", confidence="clear", model="m",
                                caller_challenged=True)
    assert v.consistent is False
    assert v.quarantined is True
    assert v.decision == "redirect"          # the outcome STANDS — no flip to a free pass


def test_ascension_requires_operator_token_and_escalation_evidence(monkeypatch):
    monkeypatch.setenv("KRY_OPERATOR_RATIFY", "secret")
    # no escalation on record yet -> ratify must refuse even with the right token
    assert kr.ratify_ascension("metabolic_gate", "STARVING", "secret") is False
    # escalate (evidence chain), then a wrong token still fails
    assert kr.escalate_quarantine("metabolic_gate", "STARVING", "novel case") != "REJECTED-BUDGET"
    assert kr.ratify_ascension("metabolic_gate", "STARVING", "WRONG") is False
    # right token + escalation evidence -> ratified and now sanctioned
    assert kr.ratify_ascension("metabolic_gate", "STARVING", "secret") is True
    assert kr.is_sanctioned("metabolic_gate", "STARVING") is True


def test_revoke_withdraws_a_sanctioned_rule(monkeypatch):
    monkeypatch.setenv("KRY_OPERATOR_RATIFY", "secret")
    kr.escalate_quarantine("free_tier_passthru", "rule-x", "j")
    assert kr.ratify_ascension("free_tier_passthru", "rule-x", "secret") is True
    assert kr.revoke_ascension("free_tier_passthru", "rule-x", "secret") is True
    assert kr.is_sanctioned("free_tier_passthru", "rule-x") is False


def test_escalation_budget_caps_flooding(monkeypatch):
    # a generator cannot flood the operator queue: budget per class per window
    results = [kr.escalate_quarantine("daily_budget", f"r{i}", "j") for i in range(kr._ESC_BUDGET + 2)]
    assert "REJECTED-BUDGET" in results


def test_corrupted_sanctioned_state_blocks_trust_elevation(monkeypatch):
    monkeypatch.setenv("KRY_OPERATOR_RATIFY", "secret")
    kr._SANCTIONED_PATH.write_text(
        '{"metabolic_gate:STARVING":{"uses":NaN,"cap":100,"ratified_ts":1.0}}\n'
    , encoding="utf-8")
    before = kr._SANCTIONED_PATH.read_text(encoding="utf-8")

    assert kr.is_sanctioned("metabolic_gate", "STARVING") is False
    assert kr.escalate_quarantine("metabolic_gate", "STARVING", "novel case") != "REJECTED-BUDGET"
    assert kr.ratify_ascension("metabolic_gate", "STARVING", "secret") is False
    assert kr._SANCTIONED_PATH.read_text(encoding="utf-8") == before


def test_corrupted_escalation_log_blocks_ratification(monkeypatch):
    monkeypatch.setenv("KRY_OPERATOR_RATIFY", "secret")
    kr._ESCALATION_PATH.write_text(
        '{"gate_class":"metabolic_gate","rule":"STARVING","ts":NaN,'
        '"status":"pending_operator_review"}\n'
    , encoding="utf-8")

    assert kr.ratify_ascension("metabolic_gate", "STARVING", "secret") is False
    assert not kr._SANCTIONED_PATH.exists()


def test_referee_writes_strict_json(monkeypatch):
    monkeypatch.setenv("KRY_OPERATOR_RATIFY", "secret")
    kr.escalate_quarantine("metabolic_gate", "STARVING", "novel case")
    assert kr.ratify_ascension("metabolic_gate", "STARVING", "secret") is True
    data = json.loads(kr._SANCTIONED_PATH.read_text(encoding="utf-8"))

    assert data["metabolic_gate:STARVING"]["uses"] == 0
    with pytest.raises(ValueError, match="kry_cost must be finite"):
        kr.review_gate_decision(
            gate_class=kr.GateClass.BUDGET,
            rule="daily=200",
            outcome="allow",
            confidence="clear",
            model="m",
            kry_cost=float("nan"),
        )


def test_sanctioned_legacy_list_enters_probation_not_unbounded():
    """Audit E: a legacy list-format sanctioned file (or a rule omitting cap) must migrate into
    the bounded PROBATION cap, not an effectively-unbounded 10**9 that bypasses probation."""
    import kry.kry_referee as kr
    s = kr._normalise_sanctioned(["metabolic_gate:rule_x"])
    assert s["metabolic_gate:rule_x"]["cap"] == kr._PROBATION_CAP < 10 ** 9
    s2 = kr._normalise_sanctioned({"daily_budget:r": {"uses": 0}})
    assert s2["daily_budget:r"]["cap"] == kr._PROBATION_CAP
