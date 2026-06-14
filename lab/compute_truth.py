#!/usr/bin/env python3
"""Lab helper — turn truth_full.jsonl into truth.json (per-class true paid-rate).

The INDEPENDENT oracle for Test 1: the fraction of each class that genuinely needed the
expensive model, measured on the `audit` sample (disjoint from KRY's 2% holdout). Output
feeds lab/holdout_truth_check.py. Pure stdlib.

Usage:
    python lab/compute_truth.py truth_full.jsonl --out truth.json        # audit sample only
    python lab/compute_truth.py truth_full.jsonl --source all            # include holdout too
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant {value} is not allowed")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _json_dumps(value, **kwargs) -> str:
    return json.dumps(value, allow_nan=False, **kwargs)


def compute(truth_lines: list, *, source: str = "audit") -> dict:
    """Per-class mean hit_paid. `source`='audit' keeps the oracle independent of KRY's
    holdout; 'all' also folds in holdout-sample judgments."""
    agg: dict = {}
    for t in truth_lines:
        if source != "all" and t.get("source") != source:
            continue
        cls = t["request_class"]
        n, k = agg.get(cls, (0, 0))
        agg[cls] = (n + 1, k + (1 if t.get("hit_paid") else 0))
    return {cls: round(k / n, 4) for cls, (n, k) in agg.items() if n}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compute per-class true paid-rate from truth_full.jsonl")
    p.add_argument("truth_full")
    p.add_argument("--source", choices=["audit", "holdout", "all"], default="audit")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    lines = [_json_loads(ln) for ln in Path(args.truth_full).read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = compute(lines, source=args.source)
    text = _json_dumps(out, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {len(out)} classes -> {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
