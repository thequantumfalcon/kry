#!/usr/bin/env python3
"""KRY provider reconciliation (F1) — anchor T1 mints to the provider's own log.

A `provider_metered` (T1) receipt claims the displaced answer was produced by a
real EXTERNAL provider call. This tool checks that claim against the PROVIDER'S
OWN usage record: for each T1 receipt's retained (prompt, completion) token count,
confirm a matching record exists in a usage export the operator/auditor pulled
from the provider's API. A T1 receipt with NO matching provider record is a
metered claim whose external anchor is missing — investigate or downgrade it.

This is an OPERATOR/AUDITOR tool, not the anonymous-stranger verifier
(scripts/kry_verify.py): reconciliation needs access to the provider account whose
key made the calls. That access IS the external root of trust — the provider's
billing/activity log is the witness. stdlib only; reads either the private mint log
or a minimal `kry_t1_reconciliation_manifest/v1` derived from it.

Two modes:
  - per-request (default): greedy 1:1 match of each T1 receipt to a provider
    usage record. Requires a per-request export — OpenRouter's generation API
    exposes this; OpenAI/Anthropic per-call usage logs do too.
  - aggregate: some providers (notably Google AI Studio / Vertex) expose NO
    per-request export, only aggregate SKU-level billing totals (Cloud Billing
    report / BigQuery, hours-delayed). There, sum our provider_metered tokens
    over a window and assert it does not EXCEED the provider's billed total
    (+tolerance) — an excess is phantom T1. Window the receipts with --since/--until.

Provider export: a JSON list of usage records (per-request) or an aggregate
total / list of aggregates (aggregate mode). Common shapes are normalised —
OpenAI/Anthropic `{usage:{prompt_tokens,completion_tokens}}`, OpenRouter
generation API `{tokens_prompt,tokens_completion}`, or flat
`{prompt_tokens,completion_tokens}`.

Usage:
    python3 scripts/kry_reconcile.py kry_data/kry_mint_log.jsonl --provider-export or_usage.json
    python3 scripts/kry_reconcile.py kry_data/kry_mint_log.jsonl --provider-export g.json --tolerance 2
    python3 scripts/kry_reconcile.py kry_data/kry_mint_log.jsonl --mode aggregate \
        --provider-export google_billing.json --since 1780553000 --until 1780554000
"""
from __future__ import annotations

import argparse
import json
import math
import sys

T1_MANIFEST_SCHEMA = "kry_t1_reconciliation_manifest/v1"
MAX_AGGREGATE_TOLERANCE_PCT = 5.0
_PROVIDER_TOKEN_KEYS = (
    "prompt_tokens",
    "tokens_prompt",
    "input_tokens",
    "completion_tokens",
    "tokens_completion",
    "output_tokens",
)


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_load(f):
    return json.load(f, parse_constant=_reject_json_constant)


def _json_loads(text: str):
    return json.loads(text, parse_constant=_reject_json_constant)


def _finite_float(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


def _validate_window(since, until, *, require_complete: bool = False) -> tuple[float | None, float | None]:
    if require_complete and (since is None or until is None):
        raise ValueError("aggregate mode requires --since and --until receipt filters")
    if since is not None:
        since = _finite_float(since, "since")
    if until is not None:
        until = _finite_float(until, "until")
    if since is not None and until is not None and since >= until:
        raise ValueError("receipt window requires since < until")
    return since, until


def _validate_tolerance(tolerance: int) -> int:
    if isinstance(tolerance, bool) or not isinstance(tolerance, int) or tolerance < 0:
        raise ValueError("tolerance must be a non-negative integer")
    return tolerance


def _validate_tolerance_pct(tol_pct: float) -> float:
    tol_pct = _finite_float(tol_pct, "tolerance_pct")
    if tol_pct < 0:
        raise ValueError("tolerance_pct must be non-negative")
    if tol_pct > MAX_AGGREGATE_TOLERANCE_PCT:
        raise ValueError(
            f"tolerance_pct must be <= {MAX_AGGREGATE_TOLERANCE_PCT:.1f}"
        )
    return tol_pct


def _metered_pair(value) -> list[int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("metered_tokens must be [prompt, completion]")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        raise ValueError("metered_tokens must be integers")
    prompt, completion = value
    if prompt < 0 or completion < 0:
        raise ValueError("metered_tokens must be non-negative")
    return [prompt, completion]


def _receipt_rows(path: str) -> list[dict]:
    """Load receipt rows from a private JSONL mint log or a T1 manifest."""
    text = open(path, encoding="utf-8").read().strip()
    if not text:
        return []
    try:
        payload = _json_loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and payload.get("schema") == T1_MANIFEST_SCHEMA:
        rows = payload.get("receipts", [])
        if not isinstance(rows, list):
            raise ValueError("T1 manifest receipts must be a list")
        return rows
    if isinstance(payload, list):
        return payload
    return [_json_loads(line) for line in text.splitlines() if line.strip()]


def load_t1_receipts(mint_log_path: str, *,
                     since: float | None = None,
                     until: float | None = None) -> list[dict]:
    """Provider_metered receipts that carry a retained (prompt, completion).

    Optional [since, until) window on the receipt `ts` (unix epoch seconds) so the
    summed T1 tokens can be matched to a provider aggregate export covering the
    same period. A receipt with no `ts` is excluded when any window bound is set.
    The path may be the private JSONL mint log or a shareable
    `kry_t1_reconciliation_manifest/v1` file.
    """
    since, until = _validate_window(since, until)
    out: list[dict] = []
    for rec in _receipt_rows(mint_log_path):
        if rec.get("evidence_tier") != "provider_metered":
            continue
        try:
            metered = _metered_pair(rec.get("metered_tokens"))
        except ValueError as exc:
            raise ValueError(f"T1 receipt {rec.get('receipt_id')}: {exc}") from exc
        ts = rec.get("ts")
        if since is not None and (ts is None or ts < since):
            continue
        if until is not None and (ts is None or ts >= until):
            continue
        row = dict(rec)
        row["metered_tokens"] = metered
        out.append(row)
    return out


def normalize_provider_record(rec: dict) -> tuple[int, int]:
    """Normalise a provider usage record to (prompt, completion) tokens."""
    if not isinstance(rec, dict):
        raise ValueError("provider record must be an object")
    u = rec.get("usage", rec)
    if not isinstance(u, dict):
        raise ValueError("provider usage must be an object")
    p = _provider_token_value(u, ("prompt_tokens", "tokens_prompt", "input_tokens"))
    c = _provider_token_value(u, ("completion_tokens", "tokens_completion", "output_tokens"))
    return p, c


def _norm_provider(rec: dict) -> tuple[int, int]:
    return normalize_provider_record(rec)


def _provider_token_value(record: dict, keys: tuple[str, ...]) -> int:
    for key in keys:
        if key in record:
            value = record[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{key} must be a non-negative JSON integer")
            return value
    return 0


def _looks_like_provider_record(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    usage = value.get("usage")
    if isinstance(usage, dict):
        return any(key in usage for key in _PROVIDER_TOKEN_KEYS)
    return any(key in value for key in _PROVIDER_TOKEN_KEYS)


def provider_record_rows(raw: object) -> list[dict]:
    """Return per-request provider rows from a list, envelope, or single usage row."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("data", "records"):
            rows = raw.get(key)
            if isinstance(rows, list):
                return rows
        if _looks_like_provider_record(raw):
            return [raw]
    return []


def reconcile(t1_receipts: list[dict], provider_records: list[dict],
              *, tolerance: int = 0) -> dict:
    """Match each T1 receipt's metered tokens to a provider usage record.

    Greedy one-to-one: each provider record backs at most one receipt (so N
    receipts need N distinct provider calls — a single real call can't anchor
    many claims). tolerance allows minor per-side token accounting differences.
    """
    tolerance = _validate_tolerance(tolerance)
    pool = [_norm_provider(r) for r in provider_records]
    used = [False] * len(pool)
    matched: list[dict] = []
    unmatched: list[dict] = []
    for rcpt in t1_receipts:
        try:
            rp, rc = _metered_pair(rcpt.get("metered_tokens"))
        except ValueError as exc:
            raise ValueError(f"T1 receipt {rcpt.get('receipt_id')}: {exc}") from exc
        hit = None
        for i, (pp, cc) in enumerate(pool):
            if not used[i] and abs(pp - rp) <= tolerance and abs(cc - rc) <= tolerance:
                hit = i
                break
        if hit is not None:
            used[hit] = True
            matched.append({"receipt_id": rcpt.get("receipt_id"), "metered": [rp, rc]})
        else:
            unmatched.append({"receipt_id": rcpt.get("receipt_id"), "metered": [rp, rc]})
    total = len(t1_receipts)
    return {
        "t1_receipts": total,
        "provider_records": len(pool),
        "matched": len(matched),
        "unmatched_receipts": unmatched,
        # 0/0 is UNDEFINED, never "perfect agreement" — an empty reconciliation must not read as 1.0
        # for any caller (the grade driver guards this too, but the helper must not lie at the source).
        "reconciled_fraction": round(len(matched) / total, 4) if total else None,
        "verdict": ("NO_T1_RECEIPTS" if not total
                    else "RECONCILED" if not unmatched else "DISCREPANCY"),
        "note": ("unmatched T1 receipts = a provider_metered claim with NO "
                 "corresponding provider usage record → the external anchor is "
                 "missing for that KRY; investigate or downgrade to self_reported"),
    }


def aggregate_reconcile(t1_receipts: list[dict], provider_records: object,
                        *, tol_pct: float = 5.0) -> dict:
    """Reconcile SUMMED T1 metered tokens against a provider AGGREGATE total.

    For providers (e.g. Google) that expose NO per-request usage export — only
    aggregate, SKU-level billing totals (Cloud Billing report / BigQuery export,
    hours-delayed). Per-request matching (`reconcile`) is impossible there, so we
    check the one invariant an aggregate CAN witness:

        sum(provider_metered tokens we minted)  <=  provider billed total  (+tol)

    We can never have metered MORE provider work than the provider actually
    billed; an EXCESS is phantom T1 — mints claiming more external calls than
    happened. A small tolerance absorbs billing rounding / tokenizer drift. (The
    converse — provider billed MORE than we minted — is fine: not every provider
    call is a displacement mint.)

    provider_records: one aggregate object, a list of them (summed), or a
    {data:[...]} / {records:[...]} envelope.
    """
    tol_pct = _validate_tolerance_pct(tol_pct)
    if isinstance(provider_records, dict):
        if isinstance(provider_records.get("data"), list):
            provider_records = provider_records["data"]
        elif isinstance(provider_records.get("records"), list):
            provider_records = provider_records["records"]
        else:
            provider_records = [provider_records]

    prov_p = prov_c = 0
    for r in provider_records:
        p, c = _norm_provider(r)
        prov_p += p
        prov_c += c
    prov_total = prov_p + prov_c

    sum_p = 0
    sum_c = 0
    for r in t1_receipts:
        try:
            p, c = _metered_pair(r.get("metered_tokens"))
        except ValueError as exc:
            raise ValueError(f"T1 receipt {r.get('receipt_id')}: {exc}") from exc
        sum_p += p
        sum_c += c
    our_total = sum_p + sum_c

    allowance = prov_total * (1.0 + tol_pct / 100.0)
    reconciled = our_total <= allowance
    return {
        "mode": "aggregate",
        "t1_receipts": len(t1_receipts),
        "our_minted_tokens": {"prompt": sum_p, "completion": sum_c, "total": our_total},
        "provider_billed_tokens": {"prompt": prov_p, "completion": prov_c, "total": prov_total},
        "tolerance_pct": tol_pct,
        "overclaim_tokens": our_total - prov_total,
        "reconciled_fraction": round(min(1.0, prov_total / our_total), 4) if our_total else None,
        "verdict": ("NO_T1_RECEIPTS" if not our_total
                    else "RECONCILED" if reconciled else "DISCREPANCY"),
        "note": ("aggregate F1: summed provider_metered tokens must not exceed the "
                 "provider's billed total for the same model+window (+tolerance); an "
                 "EXCESS = phantom T1 mints claiming more provider work than billed"),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="KRY T1 provider reconciliation (operator/auditor)")
    p.add_argument("mint_log", help="path to the private mint log or T1 reconciliation manifest")
    p.add_argument("--provider-export", default=None,
                   help="JSON: per-request usage records (per-request mode) or an "
                        "aggregate total / list of aggregates (aggregate mode). "
                        "Omit to PREVIEW the T1 receipts + ts window you need to cover.")
    p.add_argument("--mode", choices=["per-request", "aggregate"], default="per-request",
                   help="per-request: greedy 1:1 match (providers that export each call, "
                        "e.g. OpenRouter). aggregate: sum-vs-billed-total (providers with "
                        "only aggregate billing, e.g. Google)")
    p.add_argument("--tolerance", type=int, default=0,
                   help="per-request: allowed per-side token difference (default 0 = exact)")
    p.add_argument("--tolerance-pct", type=float, default=2.0,
                   help="aggregate: allowed %% our-sum may exceed provider total "
                        "(rounding/tokenizer drift; max 5.0)")
    p.add_argument("--since", type=float, default=None,
                   help="aggregate: only receipts with ts >= SINCE (unix epoch) — match the billing window")
    p.add_argument("--until", type=float, default=None,
                   help="aggregate: only receipts with ts < UNTIL (unix epoch) — match the billing window")
    args = p.parse_args(argv)

    try:
        if args.mode == "aggregate":
            args.since, args.until = _validate_window(
                args.since,
                args.until,
                require_complete=True,
            )
            args.tolerance_pct = _validate_tolerance_pct(args.tolerance_pct)
        else:
            args.since, args.until = _validate_window(args.since, args.until)
            args.tolerance = _validate_tolerance(args.tolerance)
    except ValueError as exc:
        print(f"kry_reconcile: {exc}", file=sys.stderr)
        return 2

    t1 = load_t1_receipts(args.mint_log, since=args.since, until=args.until)

    if args.provider_export is None:                 # preview: no export to reconcile yet
        sp = sum(int((r.get("metered_tokens") or [0, 0])[0]) for r in t1)
        sc = sum(int((r.get("metered_tokens") or [0, 0])[1]) for r in t1)
        tss = [r.get("ts") for r in t1 if r.get("ts") is not None]
        print("KRY T1 reconciliation — PREVIEW (no --provider-export given)")
        print(f"  T1 (provider_metered) receipts: {len(t1)}")
        print(f"  summed minted tokens:           {sp + sc} (prompt {sp} + completion {sc})")
        if tss:
            print(f"  ts window to cover:             {min(tss):.0f} .. {max(tss):.0f} (unix epoch)")
        print("  -> pull the provider's usage/billing export for that window, then re-run "
              "with --provider-export")
        return 0

    with open(args.provider_export, encoding="utf-8") as f:
        raw = _json_load(f)

    if args.mode == "aggregate":
        result = aggregate_reconcile(t1, raw, tol_pct=args.tolerance_pct)
        ours, prov = result["our_minted_tokens"], result["provider_billed_tokens"]
        if t1:
            tss = [r.get("ts") for r in t1 if r.get("ts") is not None]
            if tss:
                print(f"KRY T1 aggregate reconciliation  (receipt ts window: {min(tss):.0f}..{max(tss):.0f})")
            else:
                print("KRY T1 aggregate reconciliation")
        else:
            print("KRY T1 aggregate reconciliation")
        print(f"  T1 (provider_metered) receipts: {result['t1_receipts']}")
        print(f"  our minted tokens:              {ours['total']} "
              f"(prompt {ours['prompt']} + completion {ours['completion']})")
        print(f"  provider billed total:          {prov['total']} "
              f"(prompt {prov['prompt']} + completion {prov['completion']})")
        print(f"  tolerance:                      {result['tolerance_pct']:.1f}%")
        if result["verdict"] == "DISCREPANCY":
            print(f"  VERDICT: DISCREPANCY — minted {result['overclaim_tokens']} tokens "
                  f"OVER the provider's billed total: phantom T1 (more claimed than billed).")
            return 1
        print("  VERDICT: RECONCILED — minted T1 tokens are within the provider's billed total.")
        return 0

    records = provider_record_rows(raw)
    result = reconcile(t1, records, tolerance=args.tolerance)
    print("KRY T1 provider reconciliation")
    print(f"  T1 (provider_metered) receipts: {result['t1_receipts']}")
    print(f"  provider usage records:         {result['provider_records']}")
    # reconciled_fraction is None when there are no T1 receipts (division by zero) — guard the format
    # so the CLI prints a verdict instead of crashing with `None * 100`.
    frac = result["reconciled_fraction"]
    frac_label = "n/a" if frac is None else f"{frac * 100:.0f}%"
    print(f"  matched:                        {result['matched']} ({frac_label})")
    if result.get("verdict") == "NO_T1_RECEIPTS":
        print("  VERDICT: NO_T1_RECEIPTS — no provider_metered receipts to reconcile.")
        return 1
    if result["unmatched_receipts"]:
        print(f"  VERDICT: DISCREPANCY — {len(result['unmatched_receipts'])} "
              f"T1 receipt(s) with no provider record:")
        for u in result["unmatched_receipts"]:
            print(f"    - {u['receipt_id']} metered={u['metered']}")
        return 1
    print("  VERDICT: RECONCILED — every T1 claim is anchored to a provider record.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
