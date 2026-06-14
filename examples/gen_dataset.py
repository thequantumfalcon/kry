#!/usr/bin/env python3
"""Synthetic routing-log generator — realistic external data to stress KRY.

Produces a usage log in the shape scripts/kry_savings_report.py consumes, with an
embedded GROUND TRUTH (each request-class has a known true paid-rate). That lets a
test do more than "it didn't crash": it can assert the randomized holdout actually
RECOVERS the true counterfactual rate (the estimator is correct), and that savings,
veracity, and conservation invariants hold at scale.

Each request-class has:
  - a primary model it would route to (what a cache hit avoids),
  - true_paid: the real fraction of requests in the class that genuinely need the
    PAID model (the counterfactual the holdout is meant to measure),
  - cache_rate: fraction served from cache (the treated population),
  - a token range.

Pure stdlib; deterministic for a given --seed.

Usage:
    python examples/gen_dataset.py --n 20000 --holdout-rate 0.05 --out big.jsonl
    python scripts/kry_savings_report.py big.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys

_FREE = "google/gemini-3-flash"

# class -> (primary_model, true_paid, cache_rate, tok_lo, tok_hi, weight)
CLASSES: dict[str, tuple] = {
    "summarize": ("gh/claude-opus-4.8",            0.85, 0.60, 300, 700, 0.30),
    "code":      ("or/anthropic/claude-opus-4.8",  0.70, 0.40, 500, 1500, 0.25),
    "translate": ("or/deepseek/deepseek-v4-pro",   0.50, 0.55, 200, 400, 0.20),
    "qa":        ("or/qwen/qwen3.7-max",           0.40, 0.50, 200, 600, 0.15),
    "greet":     (_FREE,                            0.00, 0.80, 20,  80,  0.10),
}


def ground_truth() -> dict:
    return {c: v[1] for c, v in CLASSES.items()}


def generate(n: int, holdout_rate: float, seed: int = 0) -> list[dict]:
    rnd = random.Random(seed)
    names = list(CLASSES)
    weights = [CLASSES[c][5] for c in names]
    rows: list[dict] = []
    for i in range(n):
        cls = rnd.choices(names, weights=weights, k=1)[0]
        primary, true_paid, cache_rate, lo, hi, _ = CLASSES[cls]
        comp = rnd.randint(lo, hi)
        prompt = rnd.randint(lo * 2, hi * 3)
        usage = {"prompt_tokens": prompt, "completion_tokens": comp}
        rid = f"req-{i}"
        if rnd.random() < holdout_rate:                       # forced real call (measurement)
            hit_paid = rnd.random() < true_paid
            rows.append({"id": rid, "request_class": cls, "holdout": True,
                         "model": primary if hit_paid else _FREE, "usage": usage})
        elif rnd.random() < cache_rate:                       # served from cache (treated)
            rows.append({"id": rid, "request_class": cls, "cache_hit": True,
                         "avoided_model": primary, "usage": usage})
        else:                                                 # a real (non-holdout) call
            hit_paid = rnd.random() < true_paid
            rows.append({"id": rid, "request_class": cls,
                         "model": primary if hit_paid else _FREE, "usage": usage})
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate a synthetic KRY routing log")
    p.add_argument("--n", type=int, default=20000)
    p.add_argument("--holdout-rate", type=float, default=0.05)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default=None, help="output JSONL (default stdout)")
    p.add_argument("--truth-out", default=None, help="write ground-truth paid-rates JSON here")
    args = p.parse_args(argv)

    rows = generate(args.n, args.holdout_rate, args.seed)
    out = "\n".join(json.dumps(r) for r in rows) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"wrote {len(rows)} records -> {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(out)
    if args.truth_out:
        with open(args.truth_out, "w", encoding="utf-8") as f:
            json.dump(ground_truth(), f, indent=2)
        print(f"ground truth -> {args.truth_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
