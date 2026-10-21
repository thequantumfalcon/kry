#!/usr/bin/env python3
"""KRY savings report — estimate savings from an operator-supplied routing log.

This operator tool reads JSON/JSONL usage records, values reported cache hits and
model displacements, and estimates paid-call spend using the package price tables.
Reported holdout counts can discount the estimate using a Wilson lower bound.
Neither the log's shape nor its sample size authenticates the underlying events.
Every imported saving is self_reported with veracity_floor 0, including holdout
and displacement records. Dollars are estimates, not reconciled bill amounts.

--mint / --attest can record these self-reported estimates in the operator's chain.
The stranger verifier checks integrity and public arithmetic where checkable;
it does not certify that the imported calls, savings or holdouts really happened.

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
import sys
from pathlib import Path

# Operator tool: reuse the package for valuation (same math as the chain). Add src/
# so `python3 scripts/kry_savings_report.py` works without PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kry.kry_baseline import wilson_interval  # noqa: E402
from kry.kry_prompt_cache import output_price_usd_per_m  # noqa: E402
from kry.kry_prompt_cache import summarize as summarize_prompt_cache  # noqa: E402
from kry.kry_token import (  # noqa: E402
    EARN_RATES,
    FRONTIER_USD_PER_M_OUTPUT,
    SPEND_RATES,
    USD_PER_KRY,
    net_value_multiplier,
    spend_cost,
    value_multiplier,
)

# Minimum reported holdout sample before applying the conservative Wilson factor.
# This affects estimated magnitude only; any number of unauthenticated records
# remains self_reported and cannot establish an externally anchored trust tier.
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
    """Keep non-negative JSON integer counts exactly. Malformed counts (including
    booleans, strings and floats) clamp to 0 without creating a positive saving."""
    return v if type(v) is int and v >= 0 else 0


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
    if value is None or (type(value) is int and value == 0):
        return False
    if type(value) is int and value == 1:
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
    has an unusable container, model/class field, or boolean flag."""
    if not isinstance(rec, dict):
        return None
    # A cache hit is served from cache, so it may carry no `model` (no call was
    # made) — only the `avoided_model` it stood in for. Accept either.
    model = rec.get("model") or rec.get("model_name") or rec.get("avoided_model")
    if not isinstance(model, str) or not model:
        return None
    u = rec.get("usage", rec)
    if not isinstance(u, dict):
        return None
    # An explicitly present zero or malformed value must not select a later alias.
    prompt = next((u[key] for key in ("prompt_tokens", "tokens_prompt", "input_tokens",
                                     "native_tokens_prompt") if key in u), 0)
    if "prompt_tokens" not in u and "tokens_prompt" not in u and "input_tokens" in u:
        # Anthropic reports cache reads and writes outside input_tokens; the whole prompt is the sum.
        prompt = (_safe_tokens(u.get("input_tokens")) + _safe_tokens(u.get("cache_read_input_tokens", 0))
                  + _safe_tokens(u.get("cache_creation_input_tokens", 0)))
    completion = next((u[key] for key in ("completion_tokens", "tokens_completion", "output_tokens",
                                         "native_tokens_completion") if key in u), 0)
    try:
        cache_hit = _json_bool(rec.get("cache_hit", rec.get("cached", False)), "cache_hit")
        holdout = _json_bool(rec.get("holdout", False), "holdout")
    except ValueError:
        return None   # malformed boolean flag → unusable record (skip rather than miscount)
    served_model = rec.get("served_model")
    avoided_model = rec.get("avoided_model") or (model if cache_hit else None)
    if any(value is not None and not isinstance(value, str) for value in (served_model, avoided_model)):
        return None
    # Request class: prefer explicit; else the model being avoided (cache hit) or
    # the model called (holdout / real). Holdout and treated must share a class to
    # match, so an explicit request_class is recommended for accuracy.
    request_class = (rec.get("request_class") or rec.get("class") or rec.get("tag")
                     or avoided_model or model)
    if not isinstance(request_class, str):
        return None
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


def _report_spend(model: str, completion: int) -> tuple[float, bool]:
    """(KRY, priced) for the report's SPEND. Gateway ids in SPEND_RATES keep their rate; an exact
    provider model id uses its dated list output price; anything else is unpriced and not charged.
    spend_cost itself still charges unknown models the frontier rate, which keeps routing costs
    fail-closed in the ledger, but that fallback is not a real price and would skew this report."""
    if any(model.startswith(prefix) or model == prefix for prefix in SPEND_RATES):
        return spend_cost(model, completion), True
    price = output_price_usd_per_m(model)
    if price is not None:
        return completion * float(price) / FRONTIER_USD_PER_M_OUTPUT, True
    return 0.0, False


def _batch_origin(rec: dict) -> bool:
    """Whether a record came from a provider Batch API export. Both providers key their batch result
    rows by `custom_id`. Batch bills at 50%, so valuing such a record at standard rates would report
    twice the true saving; tagging it lets the usual price-modifier rule exclude and count it.
    A record that both looks batch-billed and claims another tier is read as batch: the two
    disagree, and only one of the readings can overstate the saving."""
    return bool(rec.get("custom_id")) or rec.get("batch") is True


def _provider_calls(records: list[dict], batch: bool = False):
    """(model, usage) for each record that was a real provider call. A cache hit served by the
    gateway made no call, so it carries no provider cache usage. Batch-billed records are tagged so
    they are excluded rather than priced at standard rates."""
    for rec in records:
        n = normalize(rec)
        if n is None or n["cache_hit"]:
            continue
        usage = rec.get("usage", rec)
        if isinstance(usage, dict) and rec.get("service_tier") is not None:
            tier = rec["service_tier"]
            if usage.get("service_tier") is not None and usage["service_tier"] != tier:
                # Contradictory billing metadata cannot establish the standard price basis.
                tier = "conflicting_service_tiers"
            usage = {**usage, "service_tier": tier}
        if (batch or _batch_origin(rec)) and isinstance(usage, dict):
            usage = {**usage, "service_tier": "batch"}
        yield (n["served_model"] or n["model"]), usage


def analyze(records: list[dict], strict_baseline: bool = False, batch: bool = False) -> dict:
    """Compute the savings report (read-only — no minting, no persisted state).

    strict_baseline: value cache-hit classes WITHOUT sufficient reported holdout at 0
    instead of full self-reported value. Either mode leaves source assurance unchanged:
    every imported saving is self_reported with veracity_floor 0.

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
    unpriced_spend_calls = 0
    treated: list[dict] = []
    displacements: list[dict] = []
    by_kind = {"cache_hit": 0, "holdout": 0, "displacement": 0, "paid_call": 0,
               "free_call": 0}

    for n in norm:
        cls = n["request_class"]
        b = bucket(cls)
        if n["holdout"]:
            by_kind["holdout"] += 1
            cost, priced = _report_spend(n["model"], n["completion"])
            unpriced_spend_calls += not priced
            spend_kry += cost
            holdout_cost_kry += cost
            b["holdout_n"] += 1
            if spend_cost(n["model"], n["completion"]) > 0:   # the forced call genuinely hit a PAID model
                b["holdout_paid_n"] += 1
        elif n["cache_hit"]:
            by_kind["cache_hit"] += 1
            b["treated_n"] += 1
            treated.append(n)
        elif n["served_model"] and n["avoided_model"] and n["served_model"] != n["avoided_model"]:
            by_kind["displacement"] += 1
            displacements.append(n)
            cost, priced = _report_spend(n["served_model"], n["completion"])
            unpriced_spend_calls += not priced
            spend_kry += cost                  # estimated spend for the reported cheap leg
        else:
            by_kind["paid_call" if spend_cost(n["model"], n["completion"]) > 0 else "free_call"] += 1
            cost, priced = _report_spend(n["model"], n["completion"])
            unpriced_spend_calls += not priced
            spend_kry += cost

    # Pass 2 — value the treated cache hits against the measured baseline.
    tier_kry = {"self_reported": 0.0, "holdout_validated": 0.0, "provider_metered": 0.0}
    saved_kry = 0.0
    rate = EARN_RATES["cache_hit"]
    for n in treated:
        b = by_class[n["request_class"]]
        if b["holdout_n"] >= MIN_HOLDOUT_N:
            lo, _ = wilson_interval(b["holdout_paid_n"], b["holdout_n"])
            factor, tier = lo, "self_reported"   # statistical discount, not authenticated evidence
        else:
            # Insufficient reported holdout: retain the existing full-value estimate,
            # or zero it in strict mode. Neither choice establishes source truth.
            factor, tier = (0.0 if strict_baseline else 1.0), "self_reported"
        kry = n["completion"] * rate * value_multiplier(n["avoided_model"]) * factor
        saved_kry += kry
        b["saved_kry"] += kry
        b["tier"] = tier
        tier_kry[tier] += kry

    # Displacements: estimated net saving; an imported usage payload is not authentication.
    for n in displacements:
        kry = n["completion"] * EARN_RATES.get("short_circuit", 1.0) * \
            net_value_multiplier(n["avoided_model"], n["served_model"])
        saved_kry += kry
        b = by_class[n["request_class"]]
        b["saved_kry"] += kry
        tier_kry["self_reported"] += kry

    anchored = tier_kry["holdout_validated"] + tier_kry["provider_metered"]
    total_flow = saved_kry + spend_kry
    return {
        "records": len(norm),
        "strict_baseline": strict_baseline,
        "source_assurance": {
            "source": "operator_supplied_log",
            "authenticated": False,
            "scope": "imported_savings_estimates",
        },
        "by_kind": by_kind,
        "saved_kry": round(saved_kry, 2),
        "saved_usd": round(saved_kry * USD_PER_KRY, 4),       # list-price-equivalent estimate
        "spend_kry": round(spend_kry, 2),
        "spend_usd": round(spend_kry * USD_PER_KRY, 4),
        "unpriced_spend_calls": unpriced_spend_calls,
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
        # Reported beside the savings above, never added to them: not minted, not attested.
        "prompt_cache": summarize_prompt_cache(_provider_calls(records, batch)),
    }


def _mint_and_attest(records: list[dict], attest_path: str | None) -> str | None:
    """Write the savings into the hash chain (cache hits + displacements) and,
    optionally, emit a public attestation a stranger can verify."""
    from kry.kry_attest import build_attestation
    from kry.kry_baseline import wilson_interval as _wi
    from kry.kry_mint import TIER_SELF_REPORTED, mint

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
                tokens, tier = n["completion"] * lo, TIER_SELF_REPORTED
            else:
                tokens, tier = float(n["completion"]), TIER_SELF_REPORTED
            if tokens >= 1.0:
                mint("cache_hit", tokens, f"savings-report:{n['id']}",
                     evidence=f"sr:{n['id']}", avoided_model=n["avoided_model"],
                     evidence_tier=tier)
        elif n["served_model"] and n["avoided_model"] and n["served_model"] != n["avoided_model"]:
            mint("short_circuit", n["completion"], f"displacement:{n['id']}",
                 evidence=f"disp:{n['id']}", avoided_model=n["avoided_model"],
                 served_model=n["served_model"], evidence_tier=TIER_SELF_REPORTED)
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
    print(f"  SAVED (estimated):     {report['saved_kry']:>12,.2f} KRY   "
          f"= ${report['saved_usd']:,.4f}")
    print(f"  SPEND (estimated):         {report['spend_kry']:>12,.2f} KRY   "
          f"= ${report['spend_usd']:,.4f}")
    print(f"  efficiency_ratio:     {report['efficiency_ratio']:.2%}  (saved / (saved+spend))")
    print(f"  veracity_floor:       {v['veracity_floor']:.2%}  "
          f"(operator-supplied log; source truth is not verified)")
    print(f"    self_reported:      {v['self_reported_kry']:>12,.2f} KRY")
    print(f"    holdout_validated:  {v['holdout_validated_kry']:>12,.2f} KRY")
    print(f"    provider_metered:   {v['provider_metered_kry']:>12,.2f} KRY")
    if h["classes_measured"]:
        print(f"  holdout measurement:  {h['classes_measured']} class(es) measured; "
              f"cost {h['measurement_cost_kry']:,.2f} KRY (${h['measurement_cost_usd']:,.4f}) "
              f"— estimated cost of reported holdouts")
    if report["unpriced_spend_calls"]:
        print(f"  unpriced calls:       {report['unpriced_spend_calls']} (no spend rate or dated list "
              f"price for the model; not counted in SPEND)")
    pc = report["prompt_cache"]
    r = pc["records"]
    print(f"  prompt cache:         {pc['label']} (list prices as of {pc['price_basis_as_of']})")
    if r["priced"]:
        pct = "—" if pc["saving_pct_of_input_cost"] is None else f"{pc['saving_pct_of_input_cost']:.2%}"
        print(f"    saving:             ${pc['saving_usd']:,.4f} of ${pc['without_caching_usd']:,.4f} "
              f"input-side cost without caching ({pct})")
    print(f"    records:            priced {r['priced']}, unpriced {r['unpriced']}, "
          f"modifier-excluded {r['modifier_excluded']}, malformed {r['malformed']}; "
          f"1-hour write price assumed for {r['ttl_assumed_1h']}")
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
    p.add_argument("--batch", action="store_true",
                   help="every record in this log was billed through a provider Batch API (50%% of "
                        "standard): value nothing in the prompt-cache block at standard rates")
    p.add_argument("--strict-baseline", action="store_true",
                   help="value cache-hit savings WITHOUT sufficient reported holdout at 0; "
                        "all imported savings remain self-reported estimates")
    args = p.parse_args(argv)

    records = _load_records(args.usage_log)
    report = analyze(records, strict_baseline=args.strict_baseline, batch=args.batch)

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
    for _stream in (sys.stdout, sys.stderr):  # a cp1252 Windows console cannot encode the output glyphs
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    sys.exit(main())
