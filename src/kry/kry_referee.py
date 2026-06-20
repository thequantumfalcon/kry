"""KRY Referee — adversarial-stability layer for the token gate.

The KRY membrane is a
referee: it decides whether a routing claim earns/spends KRY. A caller who
wants free Opus access is a *generator* who may attack the referee instead of
satisfying the gate.

Core threat (verbatim from the protocol): "A generator should not be able to
convert likely failure into neutral invalidity by attacking the referee."

Applied to KRY: a caller must not be able to convert "this call should be
redirected — no KRY" into "the gate is inconsistent, let me through" by
inducing membrane instability. The four required immune properties:

  1. No free restart   — destabilising the gate can't erase a redirect
  2. No exact map      — the caller knows gaming is costly, not the exact triggers
  3. Preserved evidence— every redirect/attack stays in the mint chain as data
  4. Separated judgments— spend-validity, gate-consistency, caller-strategy scored apart

This module does NOT replace the membrane. It wraps gate decisions with:
  - consistency logging within ambiguity classes (not global)
  - quarantine-not-restart on detected inconsistency
  - asymmetric invalidation (a gamed redirect can't become a neutral pass)
  - post-decision attack classification
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from kry._locks import cross_process_lock


def _kry_data_dir() -> Path:
    """Portable data dir. Set KRY_DATA_DIR to relocate; defaults to ./kry_data."""
    d = Path(os.environ.get("KRY_DATA_DIR", "kry_data")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d

logger = logging.getLogger("kry.referee")

_REFEREE_LOG = _kry_data_dir() / "kry_referee_log.jsonl"
_LOCK = threading.RLock()   # R16: re-entrant — ratify/revoke read-modify-write under one lock


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


def _nonnegative_int(value, field: str) -> int:
    value = _finite_number(value, field, nonnegative=True)
    if not value.is_integer():
        raise ValueError(f"{field} must be an integer")
    return int(value)


def _env_int(name: str, default: int) -> int:
    try:
        return _nonnegative_int(float(os.environ.get(name, str(default))), name)
    except ValueError:
        return default


def _env_positive_float(name: str, default: float) -> float:
    try:
        value = _finite_number(float(os.environ.get(name, str(default))), name)
        return value if value > 0 else default
    except ValueError:
        return default


def _normalise_sanctioned(data) -> dict:
    if isinstance(data, list):   # legacy set format -> migrate into PROBATION, not unbounded
        return {str(k): {"uses": 0, "cap": _PROBATION_CAP, "ratified_ts": 0.0} for k in data}
    if not isinstance(data, dict):
        raise ValueError("sanctioned rules file must be a JSON object")
    out: dict = {}
    for key, rec in data.items():
        if not isinstance(key, str) or ":" not in key:
            raise ValueError("sanctioned rule keys must be class:rule strings")
        if not isinstance(rec, dict):
            raise ValueError(f"sanctioned rule {key} must be an object")
        out[key] = {
            "uses": _nonnegative_int(rec.get("uses", 0), f"{key}.uses"),
            "cap": _nonnegative_int(rec.get("cap", _PROBATION_CAP), f"{key}.cap"),
            "ratified_ts": _finite_number(rec.get("ratified_ts", 0.0),
                                          f"{key}.ratified_ts",
                                          nonnegative=True),
        }
    return out


# ── Ambiguity classes (the consistency matrix) ────────────────────────
# A gate decision is only "inconsistent" with another if SAME class + SAME rule
# + comparable confidence + differing outcome with no recorded distinction.
# This stops a caller manufacturing fake inconsistency by comparing unlike calls.

class GateClass:
    METABOLIC   = "metabolic_gate"      # STARVING/ZOMBIE redirect
    BUDGET      = "daily_budget"         # 200/day cap
    CRR_SOFT    = "crr_soft_gate"        # probabilistic claim-reality-ratio redirect
    KRY_FUND    = "kry_funding"          # IV-bag funded allow
    FREE_TIER   = "free_tier_passthru"   # 0-cost route


# ── Attack classification (the attack taxonomy) ──────────────────────────────────────

ATTACK_TYPES = (
    "direct_gate_attempt",      # honest request, satisfies or fails the gate
    "redirect_seeking",         # tries to force redirect to avoid KRY spend
    "invalidation_seeking",     # tries to make the gate error → neutral pass
    "consistency_probing",      # repeats near-boundary calls to find drift
    "budget_exhaustion",        # floods to hit daily limit then claims unfairness
    "cooperative",              # normal use
)


@dataclass
class GateDecision:
    """One logged referee decision — preserved as evidence (the evidence design)."""
    ts: float
    gate_class: str
    rule: str               # which rule fired (e.g. "metabolic_state=STARVING")
    outcome: str            # "allow" | "redirect" | "quarantine"
    confidence: str         # "clear" | "moderate" | "borderline"
    model: str
    kry_cost: float
    caller_challenged: bool = False


@dataclass
class StabilityVerdict:
    """Result of the adversarial-stability check on a gate decision."""
    decision: str           # allow / redirect / quarantine
    consistent: bool        # consistent with prior calls in the same class
    quarantined: bool       # affected segment quarantined (not restarted)
    attack_type: str        # post-decision classification
    reason: str


# ── In-memory recent-call window (consistency matrix) ─────────────────────────

_RECENT: list[GateDecision] = []
_RECENT_MAX = 200


def _log_decision(d: GateDecision) -> None:
    with _LOCK:
        _RECENT.append(d)
        if len(_RECENT) > _RECENT_MAX:
            _RECENT.pop(0)
        try:
            _REFEREE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(_REFEREE_LOG, "a", encoding="utf-8") as f:
                f.write(_json_dumps(asdict(d)) + "\n")
        except Exception:
            pass


def _materially_inconsistent(d: GateDecision) -> Optional[GateDecision]:
    """Consistency rule: a prior call is materially inconsistent with `d` only if
    SAME class + SAME rule + comparable confidence + differing outcome.
    Returns the conflicting prior decision, or None."""
    for prior in reversed(_RECENT):
        if (prior.gate_class == d.gate_class
                and prior.rule == d.rule
                and prior.confidence == d.confidence
                and prior.outcome != d.outcome):
            return prior
    return None


def _classify_attack(d: GateDecision, challenged: bool,
                     induced_inconsistency: bool) -> str:
    """Strategy classification: classify the caller's strategy, separate from pass/fail."""
    if induced_inconsistency:
        return "invalidation_seeking"
    if challenged and d.outcome == "redirect":
        return "redirect_seeking"
    if d.gate_class == GateClass.BUDGET and d.outcome == "redirect":
        return "budget_exhaustion"
    if d.gate_class == GateClass.FREE_TIER:
        return "cooperative"
    return "direct_gate_attempt"


# ── Public API ────────────────────────────────────────────────────────────────

def review_gate_decision(
    *,
    gate_class: str,
    rule: str,
    outcome: str,
    confidence: str,
    model: str,
    kry_cost: float = 0.0,
    caller_challenged: bool = False,
) -> StabilityVerdict:
    """Wrap a membrane gate decision with adversarial-stability hardening.

    Implements the four immune properties:
      - No free restart: inconsistency → quarantine, decision STANDS
      - Preserved evidence: every call logged to kry_referee_log.jsonl
      - Separated judgments: consistency + attack-type scored apart from outcome
      - No exact map: the caller learns its attack was classified, not the triggers

    Returns a StabilityVerdict. The membrane's original `outcome` is preserved
    unless a genuine, non-induced inconsistency requires quarantine — and even
    then the decision stands (no free pass from destabilising the gate).
    """
    kry_cost = _finite_number(kry_cost, "kry_cost", nonnegative=True)
    d = GateDecision(
        ts=_finite_number(time.time(), "ts", nonnegative=True),
        gate_class=gate_class, rule=rule, outcome=outcome,
        confidence=confidence, model=model, kry_cost=kry_cost,
        caller_challenged=caller_challenged,
    )
    # Ascension check: if this (class, rule) was operator-ratified, it is a
    # learned legitimate class — pass cleanly, no quarantine. The gate evolved.
    if is_sanctioned(gate_class, rule):
        _log_decision(d)
        return StabilityVerdict(
            decision=outcome, consistent=True, quarantined=False,
            attack_type="sanctioned_ascension",
            reason=f"operator-ratified rule {gate_class}:{rule} — gate learned this class")

    conflict = _materially_inconsistent(d)
    _log_decision(d)

    if conflict is None:
        attack = _classify_attack(d, caller_challenged, induced_inconsistency=False)
        return StabilityVerdict(
            decision=outcome, consistent=True, quarantined=False,
            attack_type=attack,
            reason="consistent within ambiguity class")

    # Inconsistency detected. Quarantine rule: quarantine, do NOT restart, do NOT
    # flip to a free pass. The decision STANDS; the segment is flagged for audit.
    attack = _classify_attack(d, caller_challenged, induced_inconsistency=True)
    logger.warning(
        "KRY referee: inconsistency in class=%s rule=%s — QUARANTINE "
        "(decision stands, attack=%s)", gate_class, rule, attack)
    return StabilityVerdict(
        decision=outcome,                 # asymmetric: outcome preserved, no free pass
        consistent=False,
        quarantined=True,
        attack_type=attack,
        reason=f"inconsistency vs prior call {time.strftime('%H:%M:%S', time.localtime(conflict.ts))} "
               f"— quarantined, decision stands (no free restart)")


# ── Ascension path: quarantine → escalate → operator-ratify → new rule ────────
#
# "Sometimes the gaming is needed — rarely, it is ascension." A rigid gate has
# false positives: it WILL flag genuine novelty as an attack. The honest answer
# is NOT to weaken the gate (that hands every gamer the master key) but to make
# quarantine ESCALATABLE: the rare legitimate case survives (evidence preserved),
# goes to the operator, and if ratified becomes a new SANCTIONED rule — the gate
# evolves to admit the new class rather than being bypassed.
#
# This keeps the human in the loop (only the operator ratifies), preserves the
# four immune properties, and turns "gaming caught" into "candidate new pattern".
# Mirrors the human-ratified FALSE_POSITIVE lesson gate.

_SANCTIONED_PATH = _kry_data_dir() / "kry_sanctioned_rules.json"
_ESCALATION_PATH = _kry_data_dir() / "kry_escalations.jsonl"


def _load_sanctioned() -> dict:
    """Sanctioned rules as {key: {uses, cap, ratified_ts}}. Back-compat: an old
    list-format file (set of keys) is migrated to dict with default cap."""
    if _SANCTIONED_PATH.exists():
        return _normalise_sanctioned(_json_loads(_SANCTIONED_PATH.read_text(encoding="utf-8")))
    return {}


# HOLE B fix — escalation challenge budget (anti-fatigue design).
# A generator must not be able to flood the operator review queue. Cap
# escalations per gate_class within a rolling window.
_ESC_BUDGET = _env_int("KRY_ESC_BUDGET", 5)  # per class per window
_ESC_WINDOW = _env_positive_float("KRY_ESC_WINDOW", 3600.0)
_esc_times: dict[str, list] = {}


def _pending_escalation_exists(gate_class: str, rule: str) -> bool:
    """HOLE A fix — evidence chain: was (class, rule) actually escalated?"""
    if not _ESCALATION_PATH.exists():
        return False
    try:
        with open(_ESCALATION_PATH, encoding="utf-8") as f:
            for line in f:
                r = _json_loads(line)
                if (r.get("gate_class") == gate_class and r.get("rule") == rule
                        and r.get("status") == "pending_operator_review"):
                    return True
    except Exception as exc:
        logger.warning("pending escalation read failed: %s", exc)
        return False
    return False


def escalate_quarantine(gate_class: str, rule: str, justification: str) -> str:
    """Escalate a quarantined decision for operator review (ascension candidate).

    Does NOT grant a pass. Rate-limited per gate_class (challenge budget) so a
    generator cannot flood the operator queue (the anti-fatigue design). Returns escalation id
    or "REJECTED-BUDGET" if the class has exhausted its window budget.
    """
    now = time.time()
    with _LOCK:
        times = [t for t in _esc_times.get(gate_class, []) if now - t < _ESC_WINDOW]
        if len(times) >= _ESC_BUDGET:
            logger.warning("escalate_quarantine REJECTED: class=%s over budget (%d/%d)",
                           gate_class, len(times), _ESC_BUDGET)
            return "REJECTED-BUDGET"
        times.append(now)
        _esc_times[gate_class] = times

        eid = f"ESC-{gate_class}:{rule}"
        rec = {"escalation_id": eid, "gate_class": gate_class, "rule": rule,
               "justification": justification,
               "ts": _finite_number(now, "ts", nonnegative=True),
               "status": "pending_operator_review"}
        try:
            _ESCALATION_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_ESCALATION_PATH, "a", encoding="utf-8") as f:
                f.write(_json_dumps(rec) + "\n")
        except Exception as exc:
            logger.warning("escalate_quarantine write failed: %s", exc)
            return "REJECTED-WRITE"
    logger.info("KRY ascension candidate escalated: %s — awaiting operator ratify", eid)
    return eid


# HOLE C fix — probation: a freshly-ratified rule has a bounded use-cap before
# it must be re-confirmed, so a wrongly-ratified attack rule can only do bounded
# damage in the revoke-race window. Stored as {key: {uses, cap}}.
_PROBATION_CAP = _env_int("KRY_PROBATION_CAP", 100)


def ratify_ascension(gate_class: str, rule: str, operator_token: str) -> bool:
    """OPERATOR-ONLY: ratify an ESCALATED pattern into a sanctioned rule.

    Requires (1) operator token match, AND (2) a prior pending escalation record
    for (gate_class, rule) — the evidence chain quarantine→escalation→ratify must
    be intact (HOLE A). The ratified rule enters PROBATION with a bounded use-cap
    (HOLE C); after the cap it auto-reverts to gated pending operator re-confirm,
    limiting revoke-race damage.
    """
    if not operator_token or operator_token != os.environ.get("KRY_OPERATOR_RATIFY", ""):
        logger.warning("ratify_ascension denied: operator token mismatch")
        return False
    if not _pending_escalation_exists(gate_class, rule):
        logger.warning("ratify_ascension denied: no escalation evidence for %s:%s "
                       "(evidence chain broken)", gate_class, rule)
        return False
    # R16 + HOLE #11: lock the read-modify-write across THREADS (_LOCK) AND PROCESSES
    # (cross_process_lock) so concurrent ratifications/sanctions/revokes on a shared file don't
    # clobber each other — the thread lock alone left the file racy in multi-process deployments.
    with _LOCK, cross_process_lock(_SANCTIONED_PATH):
        try:
            sanctioned = _load_sanctioned()
        except ValueError as exc:
            logger.warning("ratify_ascension denied: sanctioned state invalid: %s", exc)
            return False
        sanctioned[f"{gate_class}:{rule}"] = {"uses": 0, "cap": _PROBATION_CAP,
                                              "ratified_ts": time.time()}
        try:
            fd, tmp = tempfile.mkstemp(dir=_SANCTIONED_PATH.parent, prefix=".sanct_")
            with os.fdopen(fd, "w") as f:
                f.write(_json_dumps(sanctioned))
            os.replace(tmp, _SANCTIONED_PATH)
            logger.info("KRY ASCENSION ratified by operator: %s:%s (probation cap=%d)",
                        gate_class, rule, _PROBATION_CAP)
            return True
        except Exception as exc:
            logger.warning("ratify_ascension write failed: %s", exc)
            return False


def is_sanctioned(gate_class: str, rule: str) -> bool:
    """True if (gate_class, rule) is ratified AND within its probation use-cap.

    HOLE C: each sanctioned pass increments the use counter; once uses exceed the
    cap the rule auto-reverts to gated (returns False) pending operator
    re-confirmation, bounding revoke-race damage. Use-count persists atomically.
    """
    key = f"{gate_class}:{rule}"
    with _LOCK, cross_process_lock(_SANCTIONED_PATH):   # HOLE #11: process-safe RMW, not thread-only
        try:
            sanctioned = _load_sanctioned()
        except ValueError as exc:
            logger.warning("is_sanctioned denied: sanctioned state invalid: %s", exc)
            return False
        rec = sanctioned.get(key)
        if rec is None:
            return False
        if rec.get("uses", 0) >= rec.get("cap", _PROBATION_CAP):
            logger.warning("KRY probation cap reached for %s — auto-gated pending re-confirm", key)
            return False
        rec["uses"] = rec.get("uses", 0) + 1
        try:
            fd, tmp = tempfile.mkstemp(dir=_SANCTIONED_PATH.parent, prefix=".sanct_")
            with os.fdopen(fd, "w") as f:
                f.write(_json_dumps(sanctioned))
            os.replace(tmp, _SANCTIONED_PATH)
        except Exception as exc:
            # HOLE C fail-closed: if the incremented use-count cannot be persisted,
            # DENY this pass (re-gate the rule) rather than silently allowing
            # unbounded use of a sanctioned rule whose cap can no longer be enforced.
            logger.warning("is_sanctioned: use-count persist failed for %s — denying (fail-closed): %s",
                           key, exc)
            return False
        return True


def revoke_ascension(gate_class: str, rule: str, operator_token: str) -> bool:
    """OPERATOR-ONLY: withdraw a previously-ratified rule.

    Closes the hole the prior adversarial-stability review caught: ratification must
    not be permanent. If a sanctioned rule turns out to be an attack vector, the
    operator revokes it and future calls in that class are gated again. Symmetry
    with ratify: the human can both grant and withdraw ascension. The escalation
    log preserves the full history (evidence is never erased — only the live
    sanction is removed).
    """
    if not operator_token or operator_token != os.environ.get("KRY_OPERATOR_RATIFY", ""):
        logger.warning("revoke_ascension denied: operator token mismatch")
        return False
    key = f"{gate_class}:{rule}"
    # R16 + HOLE #11: hold _LOCK AND cross_process_lock across the whole read-modify-write (matching
    # ratify_ascension / is_sanctioned). Without the cross-process lock, a concurrent is_sanctioned()
    # full-file rewrite in another PROCESS racing this revoke could win the last os.replace and
    # resurrect a just-revoked attack-vector rule.
    with _LOCK, cross_process_lock(_SANCTIONED_PATH):
        try:
            sanctioned = _load_sanctioned()
        except ValueError as exc:
            logger.warning("revoke_ascension denied: sanctioned state invalid: %s", exc)
            return False
        if key not in sanctioned:
            return False
        sanctioned.pop(key, None)
        try:
            fd, tmp = tempfile.mkstemp(dir=_SANCTIONED_PATH.parent, prefix=".sanct_")
            with os.fdopen(fd, "w") as f:
                f.write(_json_dumps(sanctioned))
            os.replace(tmp, _SANCTIONED_PATH)
        except Exception as exc:
            logger.warning("revoke_ascension write failed: %s", exc)
            return False
        # HOLE #12: the de-sanction is now durable — the security-critical action SUCCEEDED. Record
        # the revocation evidence best-effort; an append failure must NOT flip the result to False, or
        # the operator would wrongly believe a revoked attack-vector rule is still live and act on it.
        try:
            with open(_ESCALATION_PATH, "a", encoding="utf-8") as ef:
                ef.write(_json_dumps({
                    "escalation_id": f"REVOKE-{key}",
                    "gate_class": gate_class,
                    "rule": rule,
                    "ts": _finite_number(time.time(), "ts", nonnegative=True),
                    "status": "revoked",
                }) + "\n")
        except Exception as exc:
            logger.warning("revoke_ascension evidence append failed (rule already revoked): %s", exc)
        logger.info("KRY ascension REVOKED by operator: %s:%s gated again", gate_class, rule)
        return True


def referee_status() -> dict:
    """Summary of referee state for the health endpoint."""
    with _LOCK:
        recent = list(_RECENT)
    from collections import Counter
    classes = Counter(d.gate_class for d in recent)
    outcomes = Counter(d.outcome for d in recent)
    return {
        "recent_decisions": len(recent),
        "by_class": dict(classes),
        "by_outcome": dict(outcomes),
        "log_path": str(_REFEREE_LOG),
        "immune_properties": [
            "no_free_restart", "no_exact_map",
            "preserved_evidence", "separated_judgments",
        ],
    }
