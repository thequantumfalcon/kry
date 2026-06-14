#!/usr/bin/env python3
"""Make a prompt corpus for lab/router.py.

Produces prompts.jsonl — lines {"id","request_class","prompt"} — with REPEATS (a small
pool of distinct prompts per class) so the router gets real cache hits. Sizes so each
class clears the >=30-holdouts rule at the given holdout rate. Pure stdlib.

Usage:
    python lab/make_prompts.py --n 20000 --out prompts.jsonl
    python lab/make_prompts.py --n 8000 --classes summarize,code,translate,greet --pool 60
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

_DEFAULT_CLASSES = ["summarize", "code", "translate", "greet"]


def make(n: int, classes: list, pool: int, seed: int = 0) -> list:
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        cls = classes[i % len(classes)]
        rows.append({"id": f"r{i}", "request_class": cls,
                     "prompt": f"{cls}: {rng.randint(0, max(1, pool) - 1)}"})
    return rows


def min_n_for_holdout(classes: int, holdout_rate: float, per_class: int = 30) -> int:
    """Smallest corpus so each class is expected to clear `per_class` holdouts."""
    return math.ceil(per_class * classes / max(holdout_rate, 1e-9))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate a prompt corpus for the lab router")
    p.add_argument("--n", type=int, default=20000)
    p.add_argument("--classes", default=",".join(_DEFAULT_CLASSES))
    p.add_argument("--pool", type=int, default=80, help="distinct prompts per class (drives cache hits)")
    p.add_argument("--holdout-rate", type=float, default=0.02)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default="prompts.jsonl")
    args = p.parse_args(argv)
    classes = [c.strip() for c in args.classes.split(",") if c.strip()]
    need = min_n_for_holdout(len(classes), args.holdout_rate)
    if args.n < need:
        print(f"warning: --n {args.n} < {need} needed for >=30 holdouts/class at "
              f"holdout_rate {args.holdout_rate} ({len(classes)} classes)", file=sys.stderr)
    rows = make(args.n, classes, args.pool, args.seed)
    Path(args.out).write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} prompts ({len(classes)} classes, pool {args.pool}) -> {args.out}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
