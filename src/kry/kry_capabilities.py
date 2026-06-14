"""KRY Capabilities & Readiness — the pre-dated rubric for "how good is this, really".

Adapted from a prior epistemic-readiness model. It exists to answer "what grade is KRY?" with an INDEPENDENT,
mechanically-checkable rubric instead of self-narrated adjectives — which is exactly
the provenance a prior audit found missing.

Two pieces:

  1. CAPABILITIES — every capability KRY claims, each marked
     `implemented | scaffolded | not_guaranteed`, with the SYMBOLS/FILES/TESTS that
     back it and an HONEST_LIMIT. `verify_capabilities()` mechanically checks that
     every `implemented` claim resolves to real importable code and existing tests —
     so "implemented" is falsifiable, not asserted. Out-of-scope items (e.g. proving a
     self-reported cache hit really happened) are shipped as DISCLOSURES, not defects.

  2. readiness_label() — the readiness ladder, weakest → strongest evidence:
       prototype < prototype_plus < internally_consistent < research_grade < production_ready
     - internally_consistent: the SYNTHETIC test suite passes (self-consistency only).
     - research_grade: + agreement >= 0.80 with an INDEPENDENT (non-self-referential)
       oracle — for KRY, the provider's OWN billing via F1 reconciliation.
     - production_ready ("A+"): + validation on an independent REAL-WORLD corpus
       (real traffic) AND a clean capability audit.
     The top label STRUCTURALLY requires external evidence — code alone cannot reach
     it, by design. That is the honest answer to "how do we get to A+".
"""
from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parents[2]   # src/kry/kry_capabilities.py -> repo root

INDEPENDENT_AGREEMENT_BAR = 0.80


@dataclass(frozen=True)
class Capability:
    name: str
    status: str                 # implemented | scaffolded | not_guaranteed
    symbols: tuple = ()         # importable "kry.module:function" — checked by import+attr
    files: tuple = ()           # repo-relative paths (e.g. scripts) — checked by existence
    tests: tuple = ()           # repo-relative test files — checked by existence
    honest_limit: str = ""


CAPABILITIES: tuple = (
    Capability("efficiency_accounting", "implemented",
               symbols=("kry.kry_token:earn", "kry.kry_token:spend", "kry.kry_token:verify_cycle"),
               tests=("tests/test_kry_token.py",),
               honest_limit="Counts self-reported savings; not proof the event happened."),
    Capability("hash_chain_receipts", "implemented",
               symbols=("kry.kry_mint:verify_chain", "kry.kry_mint:veracity_breakdown"),
               tests=("tests/test_veracity_tier.py",),
               honest_limit="Proves integrity (untampered + conserved), NOT veracity."),
    Capability("content_sealed_attestation", "implemented",
               symbols=("kry.kry_attest:build_attestation", "kry.kry_attest:verify_attestation"),
               files=("scripts/kry_verify.py",), tests=("tests/test_external_verify.py",),
               honest_limit="A stranger verifies integrity+conservation+magnitude, not that tokens are real."),
    Capability("magnitude_recompute_f2", "implemented",
               files=("scripts/kry_verify.py",), tests=("tests/test_external_verify.py",),
               honest_limit="Recomputes price arithmetic; does NOT bound raw token counts."),
    Capability("provider_reconciliation_f1", "implemented",
               files=("scripts/kry_reconcile.py", "scripts/kry_or_fetch.py"),
               tests=("tests/test_reconcile.py", "tests/test_or_fetch.py"),
               honest_limit="Needs the provider account that made the calls (the external root of trust)."),
    Capability("conservation_settlement", "implemented",
               symbols=("kry.kry_settlement:settle", "kry.kry_settlement:verify_conservation"),
               tests=("tests/test_kry_settlement.py",),
               honest_limit="Moves KRY without creating it; federated, not an open exchange."),
    Capability("double_spend_and_rollback_guard", "implemented",
               symbols=("kry.kry_settlement:verify_and_accept", "kry.kry_settlement:verify_registry"),
               tests=("tests/test_hardening.py",),
               honest_limit="Single-node atomic + tail-truncation guard (HOLE F); cross-node is post-facto (HOLE D)."),
    Capability("supply_decay", "implemented",
               symbols=("kry.kry_mint:mint",), tests=("tests/test_hardening.py",),
               honest_limit="Bounds cache-replay minting; cannot tell genuine recurrence from replay."),
    Capability("counterfactual_holdout", "implemented",
               symbols=("kry.kry_baseline:wilson_interval", "kry.kry_baseline:holdout_adjusted_tokens"),
               tests=("tests/test_baseline.py", "tests/test_stress.py"),
               honest_limit="Population estimate validated on SYNTHETIC data only; not real traffic."),
    Capability("savings_report", "implemented",
               files=("scripts/kry_savings_report.py",),
               tests=("tests/test_savings_report.py", "tests/test_stress.py"),
               honest_limit="Operator tool; the numbers are only as honest as the input log."),
    Capability("honesty_sanctions_ess", "implemented",
               symbols=("kry.kry_sanctions:min_audit_rate", "kry.kry_sanctions:honesty_is_stable",
                        "kry.kry_sanctions:record_reconciliation"),
               tests=("tests/test_sanctions.py",),
               honest_limit="Bounds the EQUILIBRIUM, not the one-shot; needs Sybil-resistant identity for a real penalty."),
    Capability("carbon_estimate", "implemented",
               symbols=("kry.kry_carbon:carbon_statement",), tests=("tests/test_carbon.py",),
               honest_limit="Labeled ESTIMATE; NOT a certified carbon credit."),
    Capability("referee_adversarial_stability", "implemented",
               symbols=("kry.kry_referee:review_gate_decision", "kry.kry_referee:ratify_ascension"),
               tests=("tests/test_referee.py",),
               honest_limit="Audits inconsistency; does not guarantee referee independence."),

    # ── Out of scope — DISCLOSURES (datasheet), not defects ───────────────────
    Capability("per_event_counterfactual_proof", "not_guaranteed",
               honest_limit="A cache hit is a counterfactual; only a population holdout estimate or a "
                            "TEE (T2, unbuilt) can witness it — never a per-event cryptographic proof."),
    Capability("source_truth_of_self_report", "not_guaranteed",
               honest_limit="No software can prove a self_reported saving really happened; veracity_floor=0.0 "
                            "is the honest label, not a fixable bug."),
    Capability("sybil_resistant_identity", "not_guaranteed",
               honest_limit="The reputation penalty is only real if identity is costly to rebuild; not provided."),
    Capability("cross_node_realtime_double_spend", "scaffolded",
               honest_limit="Single-node only (HOLE D); multi-node needs lease/nonce/replicated-log/consensus."),
    Capability("real_world_validated_savings", "not_guaranteed",
               honest_limit="Requires an independent real-world labeled corpus / real traffic; synthetic only today."),
)


def verify_capabilities() -> dict:
    """Mechanically check every `implemented` capability resolves to real code + tests.
    A failure here means an 'implemented' claim is not backed — the audit is not clean."""
    failures: list[str] = []
    for cap in CAPABILITIES:
        if cap.status != "implemented":
            continue
        for sym in cap.symbols:
            mod, _, fn = sym.partition(":")
            try:
                m = importlib.import_module(mod)
            except Exception as exc:
                failures.append(f"{cap.name}: cannot import {mod} ({exc})")
                continue
            if fn and not hasattr(m, fn):
                failures.append(f"{cap.name}: {mod}.{fn} does not exist")
        for path in (*cap.files, *cap.tests):
            if not (_REPO / path).exists():
                failures.append(f"{cap.name}: missing {path}")
    return {"failures": failures, "clean": not failures}


def audit_summary() -> dict:
    counts: dict = {}
    for cap in CAPABILITIES:
        counts[cap.status] = counts.get(cap.status, 0) + 1
    return {
        "status_counts": counts,
        "capabilities": [asdict(c) for c in CAPABILITIES],
        "disclosed_limits": [c.name for c in CAPABILITIES if c.status == "not_guaranteed"],
    }


@dataclass
class ReadinessReport:
    label: str
    reasons: list = field(default_factory=list)
    audit_clean: bool = False
    disclosed_limits: list = field(default_factory=list)


def readiness_label(*, replay_pass_rate: float,
                    independent_agreement: Optional[float] = None,
                    real_corpus_validated: bool = False,
                    audit_clean: Optional[bool] = None) -> ReadinessReport:
    """KRY's readiness on the readiness ladder. Top label ('production_ready' = A+) requires
    external evidence (independent agreement + a real-world corpus), by design."""
    if audit_clean is None:
        audit_clean = verify_capabilities()["clean"]
    disclosed = [c.name for c in CAPABILITIES if c.status == "not_guaranteed"]

    reasons: list[str] = []
    if replay_pass_rate < 1.0:
        reasons.append("synthetic test suite is not fully green")
    if independent_agreement is None:
        reasons.append("no INDEPENDENT (non-self-referential) agreement supplied — "
                       "run F1 reconciliation against a real provider export")
    elif independent_agreement < INDEPENDENT_AGREEMENT_BAR:
        reasons.append(f"independent agreement {independent_agreement:.2f} < bar {INDEPENDENT_AGREEMENT_BAR:.2f}")
    if not real_corpus_validated:
        reasons.append("not validated on an independent REAL-WORLD corpus (real traffic)")
    if not audit_clean:
        reasons.append("capability audit not clean — an 'implemented' claim does not resolve to code/tests")

    independent_pass = independent_agreement is not None and independent_agreement >= INDEPENDENT_AGREEMENT_BAR
    if replay_pass_rate >= 1.0 and independent_pass and real_corpus_validated and audit_clean:
        label = "production_ready"
    elif replay_pass_rate >= 1.0 and independent_pass:
        label = "research_grade"
    elif replay_pass_rate >= 1.0:
        label = "internally_consistent"
    elif replay_pass_rate >= 0.8:
        label = "prototype_plus"
    else:
        label = "prototype"
    return ReadinessReport(label, reasons, audit_clean, disclosed)
