#!/usr/bin/env python3
"""KRY savings report — turn a routing log into a verifiable savings statement.

Point this at a usage export from the gateway you already run (LiteLLM, OpenRouter,
Helicone, or a flat JSON/JSONL of calls) and it produces the number you can put in
front of a FinOps team, a customer, or an auditor:

    how much did caching + routing actually SAVE — and how much of that is
    MEASURED (holdout-validated) vs self-reported.

It is an OPERATOR tool (like scripts/kry_reconcile.py): it reads the operator's own
logs and may write into the operator's own mint chain. It reuses the KRY package for
valuation, so the report and the on-chain receipts use identical math. The resulting
attestation is then checkable by the stdlib-only stranger verifier
(scripts/kry_verify.py) — the operator computes, a third party verifies.

What it does:
  - SPEND   : sums the real cost of paid calls (KRY and frontier-equivalent USD).
  - SAVED   : values each cache hit at what it AVOIDED (edge-weighted by the avoided
              model's price), and each displacement at the NET price difference.
  - VERACITY: if the log carries holdout records (forced real calls — see
              docs/KRY_COUNTERFACTUAL_HOLDOUT.md), it MEASURES the counterfactual
              rate per request-class (Wilson 95% CI) and values the matching cache
              hits at the CONSERVATIVE lower bound, tagged `holdout_validated`.
              Classes with no holdout fall back to full value, tagged `self_reported`
              (veracity_floor contribution 0 — the honest label).
  - --mint / --attest : optionally write the savings into the hash chain and emit a
              public attestation a stranger can verify.

Input record fields (common gateway shapes are normalised automatically):
  id / request_id          — request identifier
  model / model_name       — the model that served (or would have, for a cache hit)
  usage.{prompt,completion}_tokens (or tokens_prompt/completion, input/output_tokens)
  cache_hit / cached  (bool)        — served from cache/optimization (a SAVING)
  holdout            (bool)         — a forced real call to MEASURE the counterfactual
  request_class / class / tag       — request type (recommended; else the model is used)
  avoided_model                     — what a cache hit avoided (default: `model`)
  served_model                      — for a displacement: the cheaper model actually used

Usage:
    python3 scripts/kry_savings_report.py usage.jsonl
    python3 scripts/kry_savings_report.py usage.jsonl --mint --attest attestation.json
    python3 scripts/kry_verify.py attestation.json        # a stranger checks it
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# Operator tool: reuse the package for valuation (same math as the chain). Add src/
# so `python3 scripts/kry_savings_report.py` works without PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kry.kry_baseline import wilson_interval  # noqa: E402
from kry.kry_token import (  # noqa: E402
    EARN_RATES,
    USD_PER_KRY,
    net_value_multiplier,
    spend_cost,
    value_multiplier,
)

# Minimum holdout sample before a class earns the `holdout_validated` trust LABEL.
# Below this the measured magnitude is too thin to claim validation, so the savings
# fall back to self_reported (full value, honest floor-0 label) — this is what stops
# a handful of fabricated holdout records from claiming external anchoring. The
# Wilson CI already discounts the magnitude for small n; this guards the LABEL.
import os  # noqa: E402

MIN_HOLDOUT_N = int(os.environ.get("KRY_MIN_HOLDOUT_N", "30"))


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_loads(text: str):
    return json.loads(text, parse_constant=_reject_json_constant)


def _json_dumps(data: object, **kwargs) -> str:
    kwargs.setdefault("allow_nan", False)
    return json.dumps(data, **kwargs)


def _safe_tokens(v) -> int:
    """Coerce a token count to a non-negative finite int. Hostile or malformed logs
    (negative, NaN, inf, strings, None) must not poison the savings number or crash
    the run — they clamp to 0 (the record is counted but contributes nothing)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(x) or x < 0:
        return 0
    return int(x)


def _load_records(path: str) -> list[dict]:
    """Read a JSON array or JSONL file of usage records."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text[0] == "[":
        data = _json_loads(text)
        return data if isinstance(data, list) else [data]
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            out.append(_json_loads(line))
    return out


def _json_bool(value, field: str) -> bool:
    """Strict boolean parse. `bool("false")` is True in Python, so a JSON log carrying the STRING
    "false"/"0"/"no" would otherwise be misclassified as a cache hit and OVER-count savings. Accept the
    real boolean, the 0/1 ints, and the common string spellings; reject anything else (the caller skips
    the record rather than guess)."""
    if isinstance(value, bool):
        return value
    if value in (None, 0):
        return False
    if value == 1:
        return True
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    raise ValueError(f"{field} must be a JSON boolean (got {value!r})")


def normalize(rec: dict) -> dict | None:
    """Normalise a gateway record to the fields this tool needs. Returns None if it
    carries no model (unusable) or carries a malformed boolean flag."""
    # A cache hit is served from cache, so it may carry no `model` (no call was
    # made) — only the `avoided_model` it stood in for. Accept either.
    model = rec.get("model") or rec.get("model_name") or rec.get("avoided_model")
    if not model:
        return None
    u = rec.get("usage", rec)
    prompt = (u.get("prompt_tokens") or u.get("tokens_prompt")
              or u.get("input_tokens") or u.get("native_tokens_prompt") or 0)
    completion = (u.get("completion_tokens") or u.get("tokens_completion")
                  or u.get("output_tokens") or u.get("native_tokens_completion") or 0)
    try:
        cache_hit = _json_bool(rec.get("cache_hit", rec.get("cached", False)), "cache_hit")
        holdout = _json_bool(rec.get("holdout", False), "holdout")
    except ValueError:
        return None   # malformed boolean flag → unusable record (skip rather than miscount)
    served_model = rec.get("served_model")
    avoided_model = rec.get("avoided_model") or (model if cache_hit else None)
    # Request class: prefer explicit; else the model being avoided (cache hit) or
    # the model called (holdout / real). Holdout and treated must share a class to
    # match, so an explicit request_class is recommended for accuracy.
    request_class = (rec.get("request_class") or rec.get("class") or rec.get("tag")
                     or avoided_model or model)
    return {
        "id": rec.get("id") or rec.get("request_id") or rec.get("requestId") or "",
        "model": model,
        "prompt": _safe_tokens(prompt),
        "completion": _safe_tokens(completion),
        "cache_hit": cache_hit,
        "holdout": holdout,
        "served_model": served_model,
        "avoided_model": avoided_model,
        "request_class": request_class,
    }


def analyze(records: list[dict], strict_baseline: bool = False) -> dict:
    """Compute the savings report (read-only — no minting, no persisted state).

    strict_baseline (external-facing mode): value cache-hit classes WITHOUT a measured holdout at 0
    instead of full self-reported value, so an external report never presents un-validated savings as
    dollars. Default False keeps the internal-analysis behaviour (full value, honest self_reported
    label, veracity_floor still 0).

    Two passes: first tally per-class holdout outcomes (the counterfactual
    measurement), then value each cache hit at the holdout-conservative rate where a
    baseline exists, full value (self_reported) where it does not.
    """
    norm = [n for n in (normalize(r) for r in records) if n]

    # Pass 1 — classify, accumulate spend, build the per-class holdout baseline.
    by_class: dict[str, dict] = {}

    def bucket(cls: str) -> dict:
        return by_class.setdefault(cls, {"treated_n": 0, "holdout_n": 0,
                                         "holdout_paid_n": 0, "saved_kry": 0.0,
                                         "tier": "self_reported"})

    spend_kry = 0.0
    holdout_cost_kry = 0.0
    treated: list[dict] = []
    displacements: list[dict] = []
    by_kind = {"cache_hit": 0, "holdout": 0, "displacement": 0, "paid_call": 0,
               "free_call": 0}

    for n in norm:
        cls = n["request_class"]
        b = bucket(cls)
        if n["holdout"]:
            by_kind["holdout"] += 1
            cost = spend_cost(n["model"], n["completion"])
            spend_kry += cost
            holdout_cost_kry += cost
            b["holdout_n"] += 1
            if cost > 0:                      # the forced call genuinely hit a PAID model
                b["holdout_paid_n"] += 1
        elif n["cache_hit"]:
            by_kind["cache_hit"] += 1
            b["treated_n"] += 1
            treated.append(n)
        elif n["served_model"] and n["avoided_model"] and n["served_model"] != n["avoided_model"]:
            by_kind["displacement"] += 1
            displacements.append(n)
            spend_kry += spend_cost(n["served_model"], n["completion"])  # the cheap leg is real spend
        else:
            cost = spend_cost(n["model"], n["completion"])
            by_kind["paid_call" if cost > 0 else "free_call"] += 1
            spend_kry += cost

    # Pass 2 — value the treated cache hits against the measured baseline.
    tier_kry = {"self_reported": 0.0, "holdout_validated": 0.0, "provider_metered": 0.0}
    saved_kry = 0.0
    rate = EARN_RATES["cache_hit"]
    for n in treated:
        b = by_class[n["request_class"]]
        if b["holdout_n"] >= MIN_HOLDOUT_N:
            lo, _ = wilson_interval(b["holdout_paid_n"], b["holdout_n"])
            factor, tier = lo, "holdout_validated"      # measured on enough samples -> conservative + validated
        else:
            # unmeasured OR too-thin a holdout to claim validation -> full value, honest
            # self_reported label (floor contribution 0). Stops a few fabricated holdout
            # records from buying the validated tier; thin samples must grow to count.
            # --strict-baseline zeroes these for external reports (don't present un-validated savings).
            factor, tier = (0.0 if strict_baseline else 1.0), "self_reported"
        kry = n["completion"] * rate * value_multiplier(n["avoided_model"]) * factor
        saved_kry += kry
        b["saved_kry"] += kry
        b["tier"] = tier
        tier_kry[tier] += kry

    # Displacements: net saving, anchored by the real cheap leg (provider_metered).
    for n in displacements:
        kry = n["completion"] * EARN_RATES.get("short_circuit", 1.0) * \
            net_value_multiplier(n["avoided_model"], n["served_model"])
        saved_kry += kry
        b = by_class[n["request_class"]]
        b["saved_kry"] += kry
        if b["treated_n"] == 0:        # label a pure-displacement class by its real tier
            b["tier"] = "provider_metered"
        tier_kry["provider_metered"] += kry

    anchored = tier_kry["holdout_validated"] + tier_kry["provider_metered"]
    total_flow = saved_kry + spend_kry
    return {
        "records": len(norm),
        "strict_baseline": strict_baseline,
        "by_kind": by_kind,
        "saved_kry": round(saved_kry, 2),
        "saved_usd": round(saved_kry * USD_PER_KRY, 4),       # retained dollars
        "spend_kry": round(spend_kry, 2),
        "spend_usd": round(spend_kry * USD_PER_KRY, 4),
        "efficiency_ratio": round(saved_kry / total_flow, 4) if total_flow else 0.0,
        "veracity": {
            "self_reported_kry": round(tier_kry["self_reported"], 2),
            "holdout_validated_kry": round(tier_kry["holdout_validated"], 2),
            "provider_metered_kry": round(tier_kry["provider_metered"], 2),
            "veracity_floor": round(anchored / saved_kry, 4) if saved_kry > 0 else 0.0,
        },
        "holdout": {
            "measurement_cost_kry": round(holdout_cost_kry, 2),
            "measurement_cost_usd": round(holdout_cost_kry * USD_PER_KRY, 4),
            "classes_measured": sum(1 for b in by_class.values() if b["holdout_n"] > 0),
        },
        "by_class": {
            cls: {
                "treated_n": b["treated_n"],
                "holdout_n": b["holdout_n"],
                "holdout_paid_n": b["holdout_paid_n"],
                "p_hat": round(b["holdout_paid_n"] / b["holdout_n"], 4) if b["holdout_n"] else None,
                "ci_lo": round(wilson_interval(b["holdout_paid_n"], b["holdout_n"])[0], 4)
                          if b["holdout_n"] else None,
                "saved_kry": round(b["saved_kry"], 2),
                "tier": b["tier"],
            }
            for cls, b in sorted(by_class.items())
        },
    }


def _mint_and_attest(records: list[dict], attest_path: str | None) -> str | None:
    """Write the savings into the hash chain (cache hits + displacements) and,
    optionally, emit a public attestation a stranger can verify."""
    from kry.kry_attest import build_attestation
    from kry.kry_baseline import wilson_interval as _wi
    from kry.kry_mint import (
        TIER_HOLDOUT_VALIDATED,
        TIER_PROVIDER_METERED,
        TIER_SELF_REPORTED,
        mint,
    )

    norm = [n for n in (normalize(r) for r in records) if n]
    # rebuild per-class holdout counts for the conservative factor
    counts: dict[str, list[int]] = {}
    for n in norm:
        if n["holdout"]:
            c = counts.setdefault(n["request_class"], [0, 0])
            c[0] += 1
            if spend_cost(n["model"], n["completion"]) > 0:
                c[1] += 1
    for n in norm:
        if n["cache_hit"]:
            hn, hp = counts.get(n["request_class"], [0, 0])
            if hn >= MIN_HOLDOUT_N:
                lo, _ = _wi(hp, hn)
                tokens, tier = n["completion"] * lo, TIER_HOLDOUT_VALIDATED
            else:
                tokens, tier = float(n["completion"]), TIER_SELF_REPORTED
            if tokens >= 1.0:
                mint("cache_hit", tokens, f"savings-report:{n['id']}",
                     evidence=f"sr:{n['id']}", avoided_model=n["avoided_model"],
                     evidence_tier=tier)
        elif n["served_model"] and n["avoided_model"] and n["served_model"] != n["avoided_model"]:
            mint("short_circuit", n["completion"], f"displacement:{n['id']}",
                 evidence=f"disp:{n['id']}", avoided_model=n["avoided_model"],
                 served_model=n["served_model"], evidence_tier=TIER_PROVIDER_METERED,
                 metered_tokens=[n["prompt"], n["completion"]])
    if attest_path:
        att = build_attestation()
        Path(attest_path).write_text(att.to_public_json(), encoding="utf-8")
        return attest_path
    return None


def _print(report: dict) -> None:
    v = report["veracity"]
    h = report["holdout"]
    print("KRY savings report")
    print(f"  records analysed:     {report['records']}  {report['by_kind']}")
    print(f"  SAVED (retained):     {report['saved_kry']:>12,.2f} KRY   "
          f"= ${report['saved_usd']:,.4f}")
    print(f"  SPEND (real):         {report['spend_kry']:>12,.2f} KRY   "
          f"= ${report['spend_usd']:,.4f}")
    print(f"  efficiency_ratio:     {report['efficiency_ratio']:.2%}  (saved / (saved+spend))")
    print(f"  veracity_floor:       {v['veracity_floor']:.2%}  "
          f"(holdout-validated + provider-metered share of savings)")
    print(f"    self_reported:      {v['self_reported_kry']:>12,.2f} KRY")
    print(f"    holdout_validated:  {v['holdout_validated_kry']:>12,.2f} KRY")
    print(f"    provider_metered:   {v['provider_metered_kry']:>12,.2f} KRY")
    if h["classes_measured"]:
        print(f"  holdout measurement:  {h['classes_measured']} class(es) measured; "
              f"cost {h['measurement_cost_kry']:,.2f} KRY (${h['measurement_cost_usd']:,.4f}) "
              f"— the price of veracity")
    print("  by request-class:")
    for cls, b in report["by_class"].items():
        ph = "—" if b["p_hat"] is None else f"{b['p_hat']:.0%} (CI≥{b['ci_lo']:.0%})"
        print(f"    {cls:36s} treated={b['treated_n']:<5} holdout={b['holdout_n']:<4} "
              f"p̂={ph:<16} saved={b['saved_kry']:>10,.2f} KRY [{b['tier']}]")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="KRY savings report from a routing log")
    p.add_argument("usage_log", help="JSON array or JSONL of usage/routing records")
    p.add_argument("--json", action="store_true", help="emit the report as JSON")
    p.add_argument("--mint", action="store_true",
                   help="write the savings into the KRY mint chain (operator's own ledger)")
    p.add_argument("--attest", default=None,
                   help="with --mint: write a public attestation JSON here (verify with kry_verify.py)")
    p.add_argument("--strict-baseline", action="store_true",
                   help="value cache-hit savings WITHOUT a measured holdout at 0 (external reports — "
                        "never present un-validated savings as dollars)")
    args = p.parse_args(argv)

    records = _load_records(args.usage_log)
    report = analyze(records, strict_baseline=args.strict_baseline)

    if args.json:
        print(_json_dumps(report, indent=2))
    else:
        _print(report)

    if args.mint:
        out = _mint_and_attest(records, args.attest)
        print("\n  minted savings into the chain"
              + (f"; attestation -> {out} (verify: python3 scripts/kry_verify.py {out})"
                 if out else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
