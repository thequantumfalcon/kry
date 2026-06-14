#!/usr/bin/env python3
"""Validated savings proof — OpenAI path (cheaper model vs frontier, real tokens).

Same honest method as kry_grounded_savings_proof.py (objective adequacy, saving counted
ONLY where the cheap AND frontier answers were both correct — NET of frontier-also-wrong),
adapted to OpenAI's API. OpenAI returns real
token counts but NOT per-request cost, so cost = real tokens x STATED public list prices
(printed in the output; verify at openai.com/pricing). Slightly weaker than OpenRouter's
provider-recorded cost, but the token counts are real and the price basis is explicit.

Needs OPENAI_API_KEY with billing/credit. stdlib only.
"""
from __future__ import annotations
import json, os, sys, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_grounded_savings_proof import build_tasks, grade, wilson  # reuse the honest grading

CHEAP, FRONTIER = "gpt-4o-mini", "gpt-4o"
# STATED OpenAI list prices, USD per 1M tokens (input, output). Verify at openai.com/pricing.
PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00)}


def call(model, prompt, key, max_tokens=60):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    u = d["usage"]
    return d["choices"][0]["message"]["content"] or "", u["prompt_tokens"], u["completion_tokens"]


def cost(model, pt, ct):
    ip, op = PRICES[model]
    return (pt * ip + ct * op) / 1e6


def main():
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        sys.exit("OPENAI_API_KEY not set")
    tasks = build_tasks()[:16]
    print(f"cheap={CHEAP}  frontier(avoided)={FRONTIER}  ({len(tasks)} grounded tasks)\n")
    rows, cheap_ok, front_ok = [], 0, 0
    sum_cheap = sum_front = saving = 0.0
    for prompt, gold in tasks:
        try:
            cout, cpt, cct = call(CHEAP, prompt, key)
            fout, fpt, fct = call(FRONTIER, prompt, key)
        except urllib.error.HTTPError as e:
            sys.exit(f"HTTP {e.code}: {e.read()[:160].decode('utf8','ignore')}")
        cc, fc = cost(CHEAP, cpt, cct), cost(FRONTIER, fpt, fct)
        cok, fok = grade(cout, gold), grade(fout, gold)
        cheap_ok += cok; front_ok += fok; sum_cheap += cc; sum_front += fc
        if cok and fok:                  # NET of frontier-also-wrong: saving counts ONLY where
            saving += (fc - cc)          # quality is preserved vs the all-frontier baseline.
            # Conservative: DROPS cheap-right/frontier-wrong rows (quality wins, not clean savings).
        rows.append({"gold": gold, "cheap_correct": cok, "frontier_correct": fok,
                     "cheap_cost": cc, "frontier_cost": fc})
        print(f"  gold={gold:<8} cheap={'OK ' if cok else 'MISS'} frontier={'OK ' if fok else 'MISS'}")
    n = len(tasks); lo, hi = wilson(cheap_ok, n)
    out = {"schema": "kry_grounded_savings_openai/v1", "cheap_model": CHEAP, "frontier_model": FRONTIER,
           "price_basis_usd_per_1M": PRICES,
           "price_note": "real token counts; cost at STATED OpenAI list prices — verify openai.com/pricing",
           "tasks": n, "cheap_adequate": cheap_ok, "adequacy_rate": round(cheap_ok / n, 4),
           "adequacy_wilson_95ci": [lo, hi], "frontier_correct": front_ok,
           "real_cheap_cost_usd": round(sum_cheap, 6), "real_frontier_cost_usd_avoided": round(sum_front, 6),
           "validated_saving_usd": round(saving, 6),
           "honest_claim": "Saving is NET of frontier-also-wrong: counted ONLY on tasks where the cheap "
                           "model AND the frontier counterfactual were both objectively correct.",
           "bounded_claim": "Grounded tasks = the easy case for adequacy; not open-ended real traffic. "
                            "Cost is real-tokens x stated public prices, not provider-recorded.",
           "rows": rows}
    Path("docs/evidence/grounded_proof_openai").mkdir(parents=True, exist_ok=True)
    Path("docs/evidence/grounded_proof_openai/grounded_proof.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n=== VALIDATED SAVINGS (real tokens, objective adequacy, OpenAI) ===")
    print(f"  cheap ({CHEAP}) adequate: {cheap_ok}/{n} = {100*cheap_ok/n:.0f}%  (95% CI [{100*lo:.0f}%, {100*hi:.0f}%])")
    print(f"  frontier correct (sanity):  {front_ok}/{n}")
    print(f"  real frontier cost (avoided): ${sum_front:.6f}   real cheap cost: ${sum_cheap:.6f}")
    print(f"  VALIDATED saving (only where cheap AND frontier both correct — net): ${saving:.6f}")
    print(f"  -> docs/evidence/grounded_proof_openai/grounded_proof.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
