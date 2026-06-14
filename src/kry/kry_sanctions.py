"""KRY Sanctions — make cheating not pay (nature's answer to the fabricated claim).

The fabricated-cache-hit problem has no cryptographic fix: a self-reported saving is
a counterfactual with no footprint, so `kry_verify.py` will call a fabricated claim
VALID (it reads `veracity_floor = 0.0` — honest, but not rejected). Nature faced the
identical problem — symbionts, signallers, and cells all CLAIM costly services that a
partner cannot directly observe — and almost never solved it by making cheating
impossible. It solved it by making cheating **not pay**, so honesty is the stable
equilibrium. This module ports that, grounded in established biology:

  - HOST SANCTIONS (legume–rhizobium, Kiers et al. 2003 Nature): the host MONITORS
    symbiont output and penalises cheats (~50% fitness cut). → reputation drops
    sharply when a party's claims fail reconciliation (kry_reconcile / kry_or_fetch).
  - RECIPROCAL REWARDS / biological market (mycorrhiza, Kiers et al. 2011 Science):
    good partners get MORE trade, slackers get less; stability comes from bidirectional
    control. → high-reputation parties are audited LESS (cheaper to deal with); low
    reputation is audited MORE (escalating sanction).
  - COSTLY SIGNALLING / trade-off honesty (Zahavi 1975; Grafen 1990; modern: Penn &
    Számadó 2020 — honesty is stabilised by a DIFFERENTIAL cost on CHEATING, not by a
    costly signal per se). → we don't tax honest reporting; we tax getting caught.
  - QUORUM-SENSING POLICING / metabolic coupling (Dandekar et al. 2012 Science): bind
    a public good to a private one so a cheater is intrinsically penalised. → the audit
    rate is set so the EXPECTED payoff of fabricating is negative (an ESS condition).

The honesty-stability (ESS) condition this module enforces — a simple, explicit model:
a fabricated claim of value V nets, in expectation,

    E[cheat] = (1 - h)·V  -  h·(λ·V)            # gain V if unaudited; lose λ·V if caught

where h = audit/reconciliation rate and λ = penalty multiple (reputation + escrow loss).
Cheating does not pay (E ≤ 0) exactly when **h·(1 + λ) ≥ 1**, i.e. h ≥ 1/(1+λ). That is
why a 2% audit suffices at a 49× penalty, and why a weak penalty demands heavy audit.
The holdout (kry_baseline) provides the audit; this module sets its rate from the
economics and runs the sanction/reward loop. Pure stdlib.

NOTE (tiering, for honesty): the biological mechanisms are established science. The
ESS arithmetic here is a deliberately simple model, not a claim that biology computes
this formula — see docs/KRY_BIOMIMICRY.md.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path


def _kry_data_dir() -> Path:
    """Portable data dir. Set KRY_DATA_DIR to relocate; defaults to ./kry_data."""
    d = Path(os.environ.get("KRY_DATA_DIR", "kry_data")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


_REP_PATH = _kry_data_dir() / "kry_reputation.json"
_LOCK = threading.RLock()


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _json_dumps(value, **kwargs) -> str:
    return json.dumps(value, allow_nan=False, **kwargs)


def _finite_number(value, field: str, *, minimum: float | None = None,
                   maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite JSON number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} must be <= {maximum}")
    return value


def _nonnegative_int(value, field: str) -> int:
    value = _finite_number(value, field, minimum=0)
    if not value.is_integer():
        raise ValueError(f"{field} must be an integer")
    return int(value)


def _rate_env(name: str, default: float) -> float:
    try:
        return _finite_number(float(os.environ.get(name, str(default))), name,
                              minimum=0.0, maximum=1.0)
    except ValueError:
        return default


def _normalise_rep_state(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("reputation ledger must be a JSON object")
    out: dict = {}
    for party, rec in data.items():
        if not isinstance(party, str) or not party:
            raise ValueError("party keys must be non-empty strings")
        if not isinstance(rec, dict):
            raise ValueError(f"{party} reputation entry must be an object")
        out[party] = {
            "reputation": _finite_number(rec.get("reputation", _PRIOR),
                                         f"{party}.reputation",
                                         minimum=0.0,
                                         maximum=1.0),
            "confirmed": _nonnegative_int(rec.get("confirmed", 0), f"{party}.confirmed"),
            "discrepancy": _nonnegative_int(rec.get("discrepancy", 0), f"{party}.discrepancy"),
            "updated": _finite_number(rec.get("updated", 0.0), f"{party}.updated",
                                      minimum=0.0),
        }
    return out

# Reputation dynamics — asymmetric, like trust everywhere and like host sanctions:
# slow to earn, fast to lose. A discrepancy multiplies reputation down (the legume
# cutting resources to a non-fixing nodule); a confirmation nudges it up toward 1.
_PRIOR = _rate_env("KRY_REP_PRIOR", 0.5)    # neutral start
_GAIN = _rate_env("KRY_REP_GAIN", 0.2)      # confirm: r += GAIN·(1-r)
_SANCTION = _rate_env("KRY_REP_SANCTION", 0.5)  # discrepancy: r *= (1-SANCTION)

# Audit-rate band: how much of a party's claims get reconciled. High reputation →
# light touch (reciprocal reward); low reputation → heavy audit (escalating sanction).
_AUDIT_MIN = _rate_env("KRY_AUDIT_MIN", 0.02)   # trusted floor (2%)
_AUDIT_MAX = _rate_env("KRY_AUDIT_MAX", 1.0)    # untrusted -> audit all


# ── ESS / honesty-stability arithmetic (explicit model — see module docstring) ─

def min_audit_rate(penalty_lambda: float) -> float:
    """Smallest audit rate h that makes fabricating unprofitable for a given penalty
    multiple λ: h ≥ 1/(1+λ). λ=49 → 0.02 (2%); λ=9 → 0.10; λ=0 → 1.0 (audit everything)."""
    penalty_lambda = _finite_number(penalty_lambda, "penalty_lambda", minimum=0.0)
    return 1.0 / (1.0 + penalty_lambda)


def min_penalty(audit_rate: float) -> float:
    """Smallest penalty multiple λ that makes fabricating unprofitable at audit rate h:
    λ ≥ (1-h)/h. h=0.02 → 49×; h=0.1 → 9×; h=1.0 → 0×."""
    audit_rate = _finite_number(audit_rate, "audit_rate", minimum=0.0, maximum=1.0)
    if audit_rate <= 0:
        return float("inf")
    return (1.0 - audit_rate) / audit_rate


def honesty_is_stable(audit_rate: float, penalty_lambda: float) -> dict:
    """Is honesty the stable strategy at this (audit rate, penalty)? Stable iff
    h·(1+λ) ≥ 1. Returns the verdict plus the margin (how far above/below the line)."""
    audit_rate = _finite_number(audit_rate, "audit_rate", minimum=0.0, maximum=1.0)
    penalty_lambda = _finite_number(penalty_lambda, "penalty_lambda", minimum=0.0)
    score = audit_rate * (1.0 + penalty_lambda)
    return {
        "audit_rate": audit_rate,
        "penalty_lambda": penalty_lambda,
        "stability_score": round(score, 4),   # ≥1 ⇒ cheating does not pay
        "honesty_stable": score >= 1.0,
        "margin": round(score - 1.0, 4),
    }


# ── Reputation ledger (host monitoring + sanctions + reciprocal reward) ────────

@dataclass
class Rep:
    reputation: float = _PRIOR
    confirmed: int = 0
    discrepancy: int = 0
    updated: float = 0.0


def _load() -> dict:
    if _REP_PATH.exists():
        return _normalise_rep_state(_json_loads(_REP_PATH.read_text(encoding="utf-8")))
    return {}


def _save(state: dict) -> None:
    try:
        state = _normalise_rep_state(state)
        _REP_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_REP_PATH.parent, prefix=".rep_")
        with os.fdopen(fd, "w") as f:
            f.write(_json_dumps(state, indent=2))
        os.replace(tmp, _REP_PATH)
    except Exception:
        pass


def reputation(party: str) -> float:
    """Current reputation r ∈ [0,1] for a party (neutral prior if unseen)."""
    try:
        return float(_load().get(party, {}).get("reputation", _PRIOR))
    except ValueError:
        return 0.0


def record_reconciliation(party: str, confirmed: bool) -> float:
    """Feed back a monitoring outcome (e.g. from kry_reconcile): a party's claim either
    reconciled against the provider's own record (confirmed) or did not (discrepancy →
    sanction). Returns the new reputation. Confirmations build slowly; a discrepancy
    multiplies reputation down hard — the host-sanction asymmetry that makes sustained
    cheating a losing strategy.

    Cross-process safe: _LOCK serializes threads, the file lock serializes processes/nodes
    so concurrent reputation updates don't lose each other (the ledger/decay fix applied
    here too — _load() reads fresh from disk, so under the lock the read-modify-write is
    atomic across nodes)."""
    from kry._locks import cross_process_lock
    with _LOCK, cross_process_lock(_REP_PATH):
        try:
            state = _load()
        except ValueError:
            return 0.0
        e = state.get(party, {"reputation": _PRIOR, "confirmed": 0, "discrepancy": 0})
        r = _finite_number(e["reputation"], "reputation", minimum=0.0, maximum=1.0)
        if confirmed:
            r = r + _GAIN * (1.0 - r)        # asymptotic approach to 1
            e["confirmed"] = int(e["confirmed"]) + 1
        else:
            r = r * (1.0 - _SANCTION)        # multiplicative sanction
            e["discrepancy"] = int(e["discrepancy"]) + 1
        e["reputation"] = max(0.0, min(1.0, r))
        e["updated"] = time.time()
        state[party] = e
        _save(state)
        return e["reputation"]


def audit_rate_for(party: str, *, h_min: float = _AUDIT_MIN, h_max: float = _AUDIT_MAX) -> float:
    """Reciprocal-reward / escalating-sanction audit rate: trusted parties (high
    reputation) are audited at the floor h_min; reputation falls → audit rises toward
    h_max. h_eff = h_min + (1 - r)·(h_max - h_min)."""
    h_min = _finite_number(h_min, "h_min", minimum=0.0, maximum=1.0)
    h_max = _finite_number(h_max, "h_max", minimum=h_min, maximum=1.0)
    r = reputation(party)
    return h_min + (1.0 - r) * (h_max - h_min)


def two_signal_trust(self_reported_kry: float, anchored_kry: float) -> dict:
    """Immune costimulation rule (Bretscher–Cohn 1970; Janeway 1989): an action needs
    TWO signals. Signal 1 alone (a self-reported claim) does NOT earn trust — it is
    'anergic' (recorded, but trust-weight 0), exactly as a T cell seeing antigen
    without costimulation is silenced rather than activated. Trust accrues only to the
    anchored (signal-2-backed) fraction. This formalises veracity_floor as the
    two-signal requirement."""
    self_reported_kry = _finite_number(self_reported_kry, "self_reported_kry",
                                       minimum=0.0)
    anchored_kry = _finite_number(anchored_kry, "anchored_kry", minimum=0.0)
    total = self_reported_kry + anchored_kry
    return {
        "trusted_kry": round(anchored_kry, 4),            # signal-2 backed
        "anergic_kry": round(self_reported_kry, 4),       # signal-1 only → no trust
        "trust_fraction": round(anchored_kry / total, 4) if total > 0 else 0.0,
        "rule": "two-signal: trust requires an external anchor (costimulation), not self-report alone",
    }


def sanctions_report() -> dict:
    """Per-party reputation, counts, and the current audit rate each has earned."""
    try:
        state = _load()
        state_valid = True
        errors: list[str] = []
    except ValueError as exc:
        state = {}
        state_valid = False
        errors = [str(exc)]
    parties = {
        p: {
            "reputation": round(float(e.get("reputation", _PRIOR)), 4),
            "confirmed": int(e.get("confirmed", 0)),
            "discrepancy": int(e.get("discrepancy", 0)),
            "audit_rate": round(audit_rate_for(p), 4),
        }
        for p, e in sorted(state.items())
    }
    return {
        "state_valid": state_valid,
        "errors": errors,
        "parties": parties,
        "ess_note": "honesty is stable when audit_rate·(1+penalty) ≥ 1 (see min_audit_rate)",
        "grounding": "host sanctions (Kiers 2003), reciprocal rewards (Kiers 2011), "
                     "costly-signalling trade-off (Penn & Számadó 2020) — docs/KRY_BIOMIMICRY.md",
    }
