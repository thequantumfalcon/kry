"""KRY Baseline — the counterfactual holdout (the answer to "would the call have happened?").

The hardest honest problem in KRY: a cache hit is a COUNTERFACTUAL — a paid call
that did not happen — so its avoidance value rests on an unprovable claim ("absent
the cache, a real call would have been made"). The mint chain proves integrity, the
veracity ladder labels self-report, but neither MEASURES the counterfactual. That is
why pure cache-hit balances read veracity_floor = 0.0.

This module measures it, the way two mature fields already solve the identical
problem:

  - Advertising INCREMENTALITY ("did the ad cause the sale, or would it have
    happened anyway?") — solved with a randomized HOLDOUT / ghost-ad control.
  - Energy-efficiency M&V (IPMVP / ISO 50001) — savings are credible only against
    a documented BASELINE: Savings = Baseline − Reporting ± Adjustments.

The mechanism: for each cache-eligible request, deterministically (auditably)
assign a small random fraction to a HOLDOUT — bypass the optimization and make the
real call. The holdout:

  1. generates REAL provider receipts (genuine external anchor — T1),
  2. measures p_hat = the fraction of requests in a class that genuinely hit the
     PAID model absent optimization,
  3. yields a confidence interval (Wilson score), so the cached ("treated")
     population is valued at the CONSERVATIVE lower bound — never overclaiming.

The counterfactual stops being "trust me" and becomes "measured: class C hit the
paid model p_hat of the time (95% CI [lo, hi]); here is the randomized holdout."
That is a documented, auditable baseline — the same artifact IPMVP and carbon
additionality require, so this fix raises veracity AND carbon credibility at once.

Honest bounds (stated, not hidden):
  - This is a POPULATION estimate, not a per-event proof. It earns the
    `holdout_validated` tier (kry_mint): stronger than self_reported, weaker than
    per-event provider_metered. Value = the CI lower bound (conservative).
  - The holdout has a COST = holdout_rate × avoided value — the honest price of
    veracity. Keep the rate small (1–2%); it buys a tight CI at volume.
  - Holdout assignment is a deterministic hash under a PUBLISHED seed, so it is
    unbiased + auditable and an operator cannot grind request-ids to dodge it
    without leaving a record. Commit the seed publicly for the guarantee to hold.

Pure stdlib. No new dependencies.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from kry._locks import cross_process_lock

logger = logging.getLogger("kry.baseline")


# Strict JSON boundary — the same convention every other KRY persistence module uses
# (kry_settlement/_mint/_pending/_token/_referee/_sanctions): reject NaN/Infinity on the
# way in AND out, so a corrupted or externally-written baseline file cannot smuggle a
# non-finite count into the estimator (a NaN holdout_n otherwise crashes avoidance_estimate).
def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _json_dumps(value, **kwargs) -> str:
    return json.dumps(value, allow_nan=False, **kwargs)


def _finite_number(value, field: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite JSON number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if nonnegative and value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _kry_data_dir() -> Path:
    """Portable data dir. Set KRY_DATA_DIR to relocate; defaults to ./kry_data."""
    d = Path(os.environ.get("KRY_DATA_DIR", "kry_data")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


_BASELINE_PATH = _kry_data_dir() / "kry_baseline.json"
_LOCK = threading.RLock()

# Fraction of cache-eligible requests forced to the real call to measure the
# counterfactual. The honest price of veracity = this × avoided value.
def _env_rate(name: str, default: float) -> float:
    """A holdout rate must be a finite fraction in [0, 1]; fall back to the default on a NaN/inf/
    out-of-range/unparseable env value rather than letting it corrupt the deterministic assignment."""
    try:
        raw = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return raw if math.isfinite(raw) and 0.0 <= raw <= 1.0 else default


HOLDOUT_RATE = _env_rate("KRY_HOLDOUT_RATE", 0.02)   # 2%

# Published seed: salts the deterministic holdout assignment so it is unbiased and
# an operator cannot grind request-ids to dodge holdout. COMMIT THIS PUBLICLY — the
# unbiasedness guarantee rests on the seed being fixed in advance, not chosen per run.
HOLDOUT_SEED = os.environ.get("KRY_HOLDOUT_SEED", "kry-holdout-v1")

_Z_95 = 1.959963984540054   # standard normal quantile for a 95% two-sided interval


# ── Deterministic, auditable holdout assignment ───────────────────────────────

def holdout_score(request_id: str) -> float:
    """Uniform [0,1) score for a request, from SHA-256(seed:request_id). Anyone with
    the published seed recomputes it — the assignment is verifiable, not asserted."""
    h = hashlib.sha256(f"{HOLDOUT_SEED}:{request_id}".encode()).hexdigest()[:13]
    return int(h, 16) / float(1 << 52)   # 13 hex digits = 52 bits → uniform in [0,1)


def is_holdout(request_id: str, rate: float = HOLDOUT_RATE) -> bool:
    """True if this request is in the holdout (force the real call to measure the
    counterfactual). Deterministic in request_id, so a request's status is stable
    and auditable. rate<=0 disables holdout (measurement off); rate>=1 forces all."""
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    return holdout_score(request_id) < rate


# ── Wilson score confidence interval (stdlib; correct for small n and p near 0/1) ─

def wilson_interval(successes: int, n: int, z: float = _Z_95) -> tuple[float, float]:
    """Two-sided Wilson score interval for a binomial proportion. Returns (lo, hi).

    Chosen over the naive normal approximation because the counterfactual rate can
    be near 0 or 1 and the holdout n is small — exactly where Wilson stays valid and
    the normal approximation breaks (and can exceed [0,1]). With n=0 the rate is
    unknown → (0.0, 1.0): the conservative lower bound is 0, so NO counterfactual
    credit is granted without measurement (fail-closed)."""
    if n <= 0:
        return 0.0, 1.0
    # HOLE #16: clamp successes into [0, n] so a corrupted/externally-written store with
    # successes > n (phat > 1) can't make phat*(1-phat) negative and crash math.sqrt with a
    # ValueError that propagates uncaught through avoidance_estimate/holdout_report. Conservative,
    # fail-closed degradation (successes==n → hi=1; ==0 → lo=0) instead of a hard crash.
    successes = min(max(int(successes), 0), n)
    phat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = phat + z2 / (2 * n)
    margin = z * math.sqrt(phat * (1.0 - phat) / n + z2 / (4 * n * n))
    lo = max(0.0, (center - margin) / denom)
    hi = min(1.0, (center + margin) / denom)
    # Exact endpoints (Wilson lower=0 at k=0, upper=1 at k=n); clamp away float drift.
    if successes == 0:
        lo = 0.0
    if successes == n:
        hi = 1.0
    return lo, hi


# ── Persisted per-class observation counts ────────────────────────────────────

@dataclass
class ClassStats:
    holdout_n: int = 0        # holdout requests observed (real calls made)
    holdout_paid_n: int = 0   # of those, how many actually hit the PAID model
    treated_n: int = 0        # optimized (cache-served) requests in this class


def _load() -> dict:
    try:
        if _BASELINE_PATH.exists():
            return _json_loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("kry_baseline: could not load %s (%s) — treating as empty", _BASELINE_PATH, exc)
    return {}


def _save(state: dict) -> None:
    try:
        _BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_BASELINE_PATH.parent, prefix=".baseline_")
        with os.fdopen(fd, "w") as f:
            f.write(_json_dumps(state, indent=2))
        os.replace(tmp, _BASELINE_PATH)
    except Exception as exc:
        logger.warning("kry_baseline: could not persist holdout state (%s) — measurement may be LOST", exc)


def _bucket(state: dict, request_class: str) -> dict:
    return state.setdefault(request_class, {"holdout_n": 0, "holdout_paid_n": 0,
                                            "treated_n": 0, "updated": 0.0})


def observe_holdout(request_class: str, hit_paid: bool) -> None:
    """Record a HOLDOUT outcome: a forced real call was made; did it hit the paid
    model? This is the measurement of the counterfactual for `request_class`.

    Cross-process safe: _LOCK serializes threads and the file lock serializes
    processes/nodes (the same fix kry_sanctions has), so concurrent holdout writers
    don't lose each other's counts — _load() re-reads fresh under the lock."""
    with _LOCK, cross_process_lock(_BASELINE_PATH):
        state = _load()
        b = _bucket(state, request_class)
        b["holdout_n"] += 1
        if hit_paid:
            b["holdout_paid_n"] += 1
        b["updated"] = time.time()
        _save(state)


def observe_treated(request_class: str, n: int = 1) -> None:
    """Record optimized (cache-served) requests in `request_class` — the population
    whose avoided value is estimated from the holdout-measured rate."""
    with _LOCK, cross_process_lock(_BASELINE_PATH):
        state = _load()
        b = _bucket(state, request_class)
        # treated_n is a cumulative population count — reject NaN/inf and negative n at the boundary
        # (a negative n would decrement the treated population; the same _finite_number guard every
        # other module applies to its numeric inputs).
        b["treated_n"] += int(_finite_number(n, "observe_treated.n", nonnegative=True))
        b["updated"] = time.time()
        _save(state)


# ── Estimation ────────────────────────────────────────────────────────────────

def avoidance_estimate(request_class: str) -> dict:
    """Measured counterfactual for a class: p_hat (point) + Wilson 95% CI, plus the
    holdout/treated counts. `conservative` is the CI lower bound — the rate to value
    the treated population at, so the claim never exceeds what the holdout supports."""
    state = _load()
    b = state.get(request_class, {})
    n = int(b.get("holdout_n", 0))
    k = int(b.get("holdout_paid_n", 0))
    treated = int(b.get("treated_n", 0))
    lo, hi = wilson_interval(k, n)
    return {
        "request_class": request_class,
        "p_hat": round(k / n, 4) if n else None,    # point estimate (None = unmeasured)
        "ci_lo": round(lo, 4),                        # conservative rate (value at this)
        "ci_hi": round(hi, 4),
        "holdout_n": n,
        "holdout_paid_n": k,
        "treated_n": treated,
        "measured": n > 0,
    }


def holdout_adjusted_tokens(request_class: str, raw_tokens: float,
                            *, conservative: bool = True) -> float:
    """Scale raw avoided tokens by the holdout-measured counterfactual rate.

    Returns raw_tokens × (CI lower bound if conservative else p_hat). This is the
    EXPECTED avoided compute — honest for BOTH bases: the dollar value (only the
    measured fraction truly displaced a paid call) and the carbon value (only that
    fraction truly avoided the energy). Unmeasured class → 0.0 (fail-closed: no
    counterfactual credit without a baseline). Mint a cache hit with these adjusted
    tokens and evidence_tier=holdout_validated to put the measured baseline on-chain.
    """
    if raw_tokens <= 0:
        return 0.0
    est = avoidance_estimate(request_class)
    if not est["measured"]:
        return 0.0
    factor = est["ci_lo"] if conservative else (est["p_hat"] or 0.0)
    return raw_tokens * factor


def holdout_report() -> dict:
    """IPMVP-style baseline surface across all measured classes: per-class p_hat +
    CI + counts, the total holdout measurement cost (calls forced), and an honest
    note. This is the auditable artifact a counterparty, auditor, or carbon verifier
    reads to see the counterfactual is MEASURED, not asserted."""
    state = _load()
    classes = []
    total_holdout = total_paid = total_treated = 0
    for cls in sorted(state):
        est = avoidance_estimate(cls)
        classes.append(est)
        total_holdout += est["holdout_n"]
        total_paid += est["holdout_paid_n"]
        total_treated += est["treated_n"]
    return {
        "method": "randomized holdout (incrementality / IPMVP baseline)",
        "holdout_rate": HOLDOUT_RATE,
        "holdout_seed": HOLDOUT_SEED,
        "classes": classes,
        "total_holdout_calls": total_holdout,    # the measurement COST (forced real calls)
        "total_holdout_paid": total_paid,
        "total_treated": total_treated,
        "note": ("counterfactual MEASURED by a deterministic randomized holdout with "
                 "retained provider receipts; treated populations valued at the Wilson "
                 "95% CI lower bound (conservative). Population estimate, not per-event "
                 "proof — see docs/KRY_COUNTERFACTUAL_HOLDOUT.md"),
    }
