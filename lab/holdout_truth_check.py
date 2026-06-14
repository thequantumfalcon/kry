#!/usr/bin/env python3
"""Lab Test 1 helper — does KRY's holdout CI bracket the REAL counterfactual rate?

This is the independent-oracle check that moves the grade to research_grade. Because
you control the lab router, you can log EVERY request's true outcome and compute the
true per-class paid-rate (independent of KRY's math). KRY, meanwhile, only sees the 2%
holdout. This tool checks whether KRY's Wilson 95% CI (recomputed from the savings
report's per-class holdout counts) contains the true rate — i.e. the estimator is
honest on real traffic, not just synthetic.

Inputs:
  --report   : the JSON from `python3 scripts/kry_savings_report.py LOG --json`
  --truth    : {"summarize": 0.85, "code": 0.70, ...}  (your measured full-stream rates)

Pass: >= 80% of measured classes have their CI bracket the true rate (the readiness
INDEPENDENT_AGREEMENT_BAR). Pure stdlib + kry.kry_baseline.

Usage:
    python3 scripts/kry_savings_report.py usage_real.jsonl --json > report.json
    python lab/holdout_truth_check.py --report report.json --truth truth.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kry.kry_baseline import wilson_interval  # noqa: E402

BAR = 0.80


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant {value} is not allowed")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _json_dumps(value, **kwargs) -> str:
    return json.dumps(value, allow_nan=False, **kwargs)


def _finite_rate(value, field: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


def coverage(report_by_class: dict, truth: dict, *, min_holdout_n: int = 30) -> dict:
    rows = []
    covered = 0
    checked = 0
    for cls, b in report_by_class.items():
        if int(b.get("holdout_n", 0)) < min_holdout_n:
            continue
        t = truth.get(cls)
        if t is None:
            continue
        t = _finite_rate(t, f"truth rate for {cls}")
        lo, hi = wilson_interval(int(b["holdout_paid_n"]), int(b["holdout_n"]))
        inside = lo - 1e-9 <= t <= hi + 1e-9
        covered += int(inside)
        checked += 1
        rows.append({"class": cls, "true_rate": round(float(t), 4),
                     "p_hat": b.get("p_hat"), "ci": [round(lo, 4), round(hi, 4)],
                     "covers_truth": inside})
    agreement = (covered / checked) if checked else None
    return {
        "classes_checked": checked,
        "covered": covered,
        "agreement": round(agreement, 4) if agreement is not None else None,
        "bar": BAR,
        "pass": agreement is not None and agreement >= BAR,
        "rows": rows,
        "note": ("agreement = fraction of measured classes whose holdout CI brackets the "
                 "real per-stream rate; >= bar feeds readiness_label(independent_agreement=...)"),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Check KRY holdout CI against real per-stream truth")
    p.add_argument("--report", required=True, help="kry_savings_report.py --json output")
    p.add_argument("--truth", required=True, help="JSON {class: true_paid_rate}")
    p.add_argument("--min-holdout-n", type=int, default=30)
    args = p.parse_args(argv)
    rep = _json_loads(Path(args.report).read_text(encoding="utf-8"))
    truth = _json_loads(Path(args.truth).read_text(encoding="utf-8"))
    out = coverage(rep.get("by_class", {}), truth, min_holdout_n=args.min_holdout_n)
    print(_json_dumps(out, indent=2))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
