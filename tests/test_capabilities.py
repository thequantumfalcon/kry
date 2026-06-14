"""KRY capability audit + readiness — the pre-dated A+ rubric, made falsifiable.

Mirrors the readiness model: every 'implemented' capability must resolve to real
code + tests (audit_clean), impossible things must NOT be marked implemented, and the
top label ('production_ready' = A+) is unreachable without external real-world evidence.
"""
from __future__ import annotations

import kry.kry_capabilities as cap


def test_audit_is_clean_every_implemented_claim_resolves():
    result = cap.verify_capabilities()
    assert result["clean"], result["failures"]   # no 'implemented' claim is unbacked


def test_impossible_capabilities_are_not_marked_implemented():
    out_of_scope = {
        "per_event_counterfactual_proof",
        "source_truth_of_self_report",
        "sybil_resistant_identity",
        "real_world_validated_savings",
    }
    by_name = {c.name: c for c in cap.CAPABILITIES}
    for name in out_of_scope:
        assert by_name[name].status != "implemented", f"{name} cannot be 'implemented'"
        assert by_name[name].honest_limit                # must carry a disclosure


def test_every_capability_has_an_honest_limit():
    for c in cap.CAPABILITIES:
        assert c.honest_limit, f"{c.name} must disclose an honest limit"


def test_today_is_internally_consistent_not_higher():
    """Synthetic suite green + clean audit, but no independent oracle and no real
    corpus -> the honest label is 'internally_consistent', NOT research/production."""
    r = cap.readiness_label(replay_pass_rate=1.0, independent_agreement=None,
                            real_corpus_validated=False)
    assert r.label == "internally_consistent"
    assert r.audit_clean is True
    assert any("real-world" in reason.lower() for reason in r.reasons)


def test_a_plus_requires_external_evidence_code_alone_cannot_reach_it():
    # synthetic-only, even with a clean audit, never reaches the top
    assert cap.readiness_label(replay_pass_rate=1.0).label != "production_ready"
    # add an independent oracle -> research_grade (still not A+)
    assert cap.readiness_label(replay_pass_rate=1.0, independent_agreement=0.9,
                               real_corpus_validated=False).label == "research_grade"
    # add a real-world corpus + clean audit -> production_ready (A+)
    assert cap.readiness_label(replay_pass_rate=1.0, independent_agreement=0.9,
                               real_corpus_validated=True, audit_clean=True).label == "production_ready"


def test_audit_failure_blocks_the_top_label():
    # if the audit is not clean, the top label is refused even with all external evidence
    r = cap.readiness_label(replay_pass_rate=1.0, independent_agreement=0.9,
                            real_corpus_validated=True, audit_clean=False)
    assert r.label != "production_ready"
