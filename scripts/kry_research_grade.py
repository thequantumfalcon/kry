#!/usr/bin/env python3
"""KRY Step-1 driver: fetch → reconcile → research_grade decision, in one command.

`docs/KRY_READINESS.md` Step 1 is the move from `internally_consistent` to
`research_grade`: an INDEPENDENT (non-self-referential) oracle — the provider's own
usage record — must agree with KRY's T1 mints at >= 0.80. Until now that was three
manual steps (kry_or_fetch → kry_reconcile → hand-call readiness_label, PLAYBOOK
Phase 8). This driver chains them and prints the one answer that matters: did the
grade advance, and by what agreement number.

It reimplements NOTHING — it imports the existing, separately-tested pieces:
  - kry_or_fetch    (pull OpenRouter per-request records — the external witness)
  - kry_reconcile   (greedy 1:1 / aggregate match → reconciled_fraction)
  - kry.kry_capabilities.readiness_label (the mechanical grader; the 0.80 bar lives there)

The reconciled_fraction IS the independent_agreement signal: in per-request mode the
fraction of T1 receipts a distinct provider record backs; in aggregate mode the share
of our minted tokens the provider's billed total covers. replay_pass_rate defaults to
1.0 (the synthetic suite is green — prove it with `bash lab/reproduce.sh 10`); pass
--replay-pass-rate to override. audit_clean is computed by the grader itself.

stdlib only. The private mint log can stay on the operator machine; a
`kry_t1_reconciliation_manifest/v1` carries the minimal T1 rows needed for a
portable packet. `kry_or_fetch`'s GET to the provider's own API for the account's
own records is the sole egress when `--fetch` is used.

Usage:
    # bring your own provider export (already fetched):
    python3 scripts/kry_research_grade.py kry_data/kry_mint_log.jsonl --provider-export or.json
    # or fetch it inline (needs OPENROUTER_API_KEY):
    python3 scripts/kry_research_grade.py kry_data/kry_mint_log.jsonl --fetch
    # Google-style aggregate billing (no per-request export):
    python3 scripts/kry_research_grade.py kry_data/kry_mint_log.jsonl \
        --provider-export billing.json --mode aggregate --since 1780553000 --until 1780554000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))   # import sibling scripts
sys.path.insert(0, str(_ROOT / "src"))                     # import the stdlib package
import kry_or_fetch          # noqa: E402
import kry_reconcile         # noqa: E402
from kry import kry_capabilities   # noqa: E402


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_load(f):
    return json.load(f, parse_constant=_reject_json_constant)


def assess(mint_log: str, *, provider_export: object | None,
           mode: str = "per-request", tolerance: int = 0, tolerance_pct: float = 5.0,
           since: float | None = None, until: float | None = None,
           replay_pass_rate: float = 1.0) -> dict:
    """Reconcile T1 mints against the provider export, then grade. Returns a result
    dict. `provider_export` is the parsed export (list/dict) or None (no oracle yet)."""
    if mode == "aggregate":
        since, until = kry_reconcile._validate_window(
            since,
            until,
            require_complete=True,
        )
        tolerance_pct = kry_reconcile._validate_tolerance_pct(tolerance_pct)
    else:
        since, until = kry_reconcile._validate_window(since, until)
        tolerance = kry_reconcile._validate_tolerance(tolerance)
    t1 = kry_reconcile.load_t1_receipts(mint_log, since=since, until=until)
    if not t1:
        # No provider_metered (T1) receipts in window: nothing to reconcile. 0/0 is
        # UNDEFINED, not perfect agreement — never let an empty reconciliation vacuously
        # clear the >= 0.80 grade bar (a non-None empty provider_export must not pass).
        agreement = None
        recon = {"verdict": "NO_T1_RECEIPTS", "reconciled_fraction": None,
                 "note": "no provider_metered (T1) receipts in window — agreement is "
                         "undefined; the grade cannot advance without an external anchor"}
    elif provider_export is None:
        agreement = None
        recon = {"verdict": "NO_ORACLE", "reconciled_fraction": None}
    elif mode == "aggregate":
        recon = kry_reconcile.aggregate_reconcile(t1, provider_export, tol_pct=tolerance_pct)
        agreement = recon["reconciled_fraction"]
    else:
        records = kry_reconcile.provider_record_rows(provider_export)
        recon = kry_reconcile.reconcile(t1, records, tolerance=tolerance)
        agreement = recon["reconciled_fraction"]

    grade = kry_capabilities.readiness_label(
        replay_pass_rate=replay_pass_rate,
        independent_agreement=agreement,           # None when no oracle yet
        real_corpus_validated=False,               # Step 2, not this driver
        audit_clean=None,                           # let the grader self-check
    )
    reached = grade.label in ("research_grade", "production_ready")
    return {
        "t1_receipts": len(t1),
        "mode": mode,
        "reconcile": recon,
        "independent_agreement": agreement,
        "bar": kry_capabilities.INDEPENDENT_AGREEMENT_BAR,
        "readiness_label": grade.label,
        "research_grade_reached": reached,
        "reasons": grade.reasons,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="KRY Step-1: fetch → reconcile → research_grade decision (one command)")
    p.add_argument("mint_log", help="path to the private mint log or T1 reconciliation manifest")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--provider-export", default=None,
                   help="pre-fetched provider usage export (JSON). Omit + --fetch to pull it inline")
    g.add_argument("--fetch", action="store_true",
                   help="fetch the OpenRouter per-request export inline (needs OPENROUTER_API_KEY)")
    p.add_argument("--mode", choices=["per-request", "aggregate"], default="per-request",
                   help="per-request (OpenRouter) or aggregate (Google-style billing)")
    p.add_argument("--tolerance", type=int, default=0,
                   help="per-request: allowed per-side token difference (default 0 = exact)")
    p.add_argument("--tolerance-pct", type=float, default=5.0,
                   help="aggregate: %% our-sum may exceed provider total")
    p.add_argument("--since", type=float, default=None, help="aggregate: receipts with ts >= SINCE")
    p.add_argument("--until", type=float, default=None, help="aggregate: receipts with ts < UNTIL")
    p.add_argument("--replay-pass-rate", type=float, default=1.0,
                   help="synthetic suite pass rate (default 1.0; prove via lab/reproduce.sh)")
    p.add_argument("--api-key", default=None, help="OpenRouter key (default $OPENROUTER_API_KEY)")
    args = p.parse_args(argv)

    export: object | None = None
    if args.fetch:
        key = args.api_key or os.getenv("OPENROUTER_API_KEY", "").strip()
        if not key:
            print("OPENROUTER_API_KEY not set — cannot --fetch.", file=sys.stderr)
            return 2
        ids = kry_or_fetch.extract_or_ids(args.mint_log)
        export = [kry_or_fetch.to_export_record(g) for g in
                  (kry_or_fetch.fetch_generation(i, key) for i in ids) if g is not None]
        print(f"fetched {len(export)}/{len(ids)} OpenRouter records", file=sys.stderr)
    elif args.provider_export:
        try:
            with open(args.provider_export, encoding="utf-8") as f:
                export = _json_load(f)
        except Exception as exc:
            print(f"provider export unreadable: {exc}", file=sys.stderr)
            return 1

    try:
        r = assess(args.mint_log, provider_export=export, mode=args.mode,
                   tolerance=args.tolerance, tolerance_pct=args.tolerance_pct,
                   since=args.since, until=args.until, replay_pass_rate=args.replay_pass_rate)
    except ValueError as exc:
        print(f"assessment invalid: {exc}", file=sys.stderr)
        return 2

    print("KRY Step-1 — research_grade assessment")
    print(f"  T1 (provider_metered) receipts: {r['t1_receipts']}")
    if r["independent_agreement"] is None:
        print("  independent agreement:          (no provider export — pass --provider-export or --fetch)")
        print("  -> oracle missing; cannot advance past internally_consistent")
    else:
        recon = r["reconcile"]
        if r["mode"] == "per-request":
            print(f"  provider records matched:       {recon['matched']}/{r['t1_receipts']}")
        else:
            ours = recon["our_minted_tokens"]["total"]
            prov = recon["provider_billed_tokens"]["total"]
            print(f"  our minted / provider billed:   {ours} / {prov} tokens")
        print(f"  independent agreement:          {r['independent_agreement']:.2f}  "
              f"(bar {r['bar']:.2f})")
    print(f"  readiness label:                {r['readiness_label']}")
    if r["research_grade_reached"]:
        print("  VERDICT: research_grade REACHED — an independent oracle agrees >= the bar.")
        return 0
    print("  VERDICT: research_grade NOT reached:")
    for reason in r["reasons"]:
        print(f"    - {reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
