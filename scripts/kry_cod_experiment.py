#!/usr/bin/env python3
"""CoD lever measurement — does output-capping preserve the deployable (test-pass) rate while cutting output tokens?

3 arms on the SAME model + SAME MBPP problems, executed-test gate (deployable, no oracle):
  verbose : "think step by step, then the function"            (reasoning-heavy output)
  cod     : "terse draft notes (<=5 words/step), then function" (capped reasoning — Chain-of-Draft)
  terse   : "return ONLY the function"                          (no reasoning — the floor)

Measures per-arm pass-rate + mean output tokens. The CoD lever's value = output-token reduction vs VERBOSE at EQUAL
test-pass (net-not-gross: a CoD pass-rate DROP vs verbose is a quality LOSS, flagged — not a free saving). This STACKS
on routing (different axis: output tokens, not model choice). Honest scope: MBPP public benchmark; $ scales with the
model's output price (capping a frontier's output saves far more $ than capping a cheap model's).

  python3 scripts/kry_cod_experiment.py [N] [--model claude-sonnet-4-6] [--budget 4]
"""
from __future__ import annotations
import json, os, sys, time, urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_shadow_sim import load_mbpp, run_mbpp, _extract
from kry_shadow_demo import call, cost, FRONTIER, PRICES

ARMS = {
    "verbose": "\n\nThink step by step about the algorithm and the edge cases, then write the complete Python "
               "function in a ```python code block.",
    "cod":     "\n\nDraft your approach as terse notes — at most 5 words per step — then write the complete Python "
               "function in a ```python code block. Keep everything minimal.",
    "terse":   "\n\nReturn ONLY the complete Python function in a ```python code block, no explanation.",
}


def _call(prompt, model, mt):
    for a in range(5):
        try:
            return call(prompt, model, mt)
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and a < 4:
                time.sleep(2 * (a + 1)); continue
            raise


def main(argv):
    model = argv[argv.index("--model") + 1] if "--model" in argv else FRONTIER
    budget = float(argv[argv.index("--budget") + 1]) if "--budget" in argv else 4.0
    nums = [a for a in argv[1:] if a.isdigit()]
    N = int(nums[0]) if nums else 50
    data = load_mbpp(); step = max(1, len(data) // N); data = data[::step][:N]
    out_price = PRICES[model][1]   # USD per 1M output tokens
    print(f"CoD experiment: model={model} (out ${out_price}/1M)  N={len(data)}  arms={list(ARMS)}\n", flush=True)
    outdir = Path("docs/evidence/cod_experiment"); outdir.mkdir(parents=True, exist_ok=True)
    rowf = open(outdir / "cod_rows.jsonl", "w", buffering=1, encoding="utf-8")
    rows, spend, done = [], 0.0, 0
    for i, p in enumerate(data):
        if spend >= budget:
            print(f"** budget cap ${budget} hit after {done} **", flush=True); break
        base = (f"You are an expert Python programmer. Task: {p['text']}\n\nYour function must pass these tests:\n"
                + "\n".join(p["test_list"]))
        rec = {"task_id": p.get("task_id", i)}
        try:
            for arm, suffix in ARMS.items():
                text, it, ot = _call(base + suffix, model, 900)
                rec[arm] = {"pass": run_mbpp(_extract(text), p), "out_tok": ot, "in_tok": it,
                            "cost": cost(model, it, ot)}
                spend += rec[arm]["cost"]
        except Exception as e:
            print(f"  [skip {i}] {type(e).__name__}: {e}", flush=True); continue
        rows.append(rec); rowf.write(json.dumps(rec) + "\n"); done += 1
        if done % 10 == 0:
            vp = sum(r["verbose"]["pass"] for r in rows); cp = sum(r["cod"]["pass"] for r in rows)
            print(f"  [{done:3d}/{len(data)}] verbose_pass={vp} cod_pass={cp}  spent=${spend:.3f}", flush=True)
    rowf.close()
    n = len(rows)

    def rate(arm): return sum(r[arm]["pass"] for r in rows) / n if n else 0
    def mean_ot(arm): return sum(r[arm]["out_tok"] for r in rows) / n if n else 0
    # CoD vs VERBOSE, on rows where BOTH pass (quality-preserved): output-token reduction + $ at this model's price
    both = [r for r in rows if r["verbose"]["pass"] and r["cod"]["pass"]]
    red = [1 - r["cod"]["out_tok"] / r["verbose"]["out_tok"] for r in both if r["verbose"]["out_tok"]]
    cod_red = sum(red) / len(red) if red else 0.0
    saved_tok = sum(r["verbose"]["out_tok"] - r["cod"]["out_tok"] for r in both)
    saved_usd = saved_tok * out_price / 1e6
    summary = {
        "schema": "kry_cod_experiment/v1",
        "label": "MEASURED on MBPP (public benchmark); CoD = output-axis lever, stacks on routing; net-not-gross",
        "model": model, "out_price_usd_per_1M": out_price, "n": n, "budget_cap_usd": budget,
        "pass_rate": {a: round(rate(a), 4) for a in ARMS},
        "mean_output_tokens": {a: round(mean_ot(a), 1) for a in ARMS},
        "cod_vs_verbose": {
            "both_pass_rows": len(both),
            "cod_pass_delta_vs_verbose": round(rate("cod") - rate("verbose"), 4),
            "mean_output_token_reduction": round(cod_red, 4),
            "output_tokens_saved_on_both_pass": saved_tok,
            "usd_saved_this_model": round(saved_usd, 6),
            "usd_saved_if_opus_output_price": round(saved_tok * 75.0 / 1e6, 6),
        },
        "honest_note": "CoD is a free win ONLY if cod_pass_delta ≈ 0 (quality preserved). A negative delta means "
                       "capping reasoning HURT correctness — then it's a tradeoff, not a free saving.",
    }
    (outdir / "cod_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n=== CoD RECEIPT ({model}) ===")
    print(f"  pass-rate:  verbose {rate('verbose'):.1%}  cod {rate('cod'):.1%}  terse {rate('terse'):.1%}")
    print(f"  mean out-tok: verbose {mean_ot('verbose'):.0f}  cod {mean_ot('cod'):.0f}  terse {mean_ot('terse'):.0f}")
    print(f"  CoD pass-delta vs verbose: {rate('cod')-rate('verbose'):+.1%}  (≈0 = quality preserved = free win)")
    print(f"  CoD output-token reduction (both-pass rows): {cod_red:.1%}  -> saved {saved_tok} tok = "
          f"${saved_usd:.4f} @ this model; ${saved_tok*75.0/1e6:.4f} @ Opus output price")
    print(f"  spend ${spend:.4f}  rows: {outdir}/cod_rows.jsonl")
    return 0


if __name__ == "__main__":
    main(sys.argv)
