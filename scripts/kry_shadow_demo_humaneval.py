#!/usr/bin/env python3
"""HumanEval variant of the shadow demo — real benchmark, cheap FAILS some, so the receipt shows the
fall-back-to-frontier dynamic AND the net-not-gross gate FIRING (cheap-fail rows -> $0 saving).

Still SYNTHETIC (a public benchmark, not real external traffic) — same honest ceiling as the toy demo, just a
realistic adequacy rate instead of 10/10. Reuses the repo's battle-tested load_humaneval()/passes() (executed
test gate, no oracle) + the served-checked call() + the digest-only emit_row().

  python3 scripts/kry_shadow_demo_humaneval.py [N]    # default 25; LIVE real calls (~$0.40)
"""
from __future__ import annotations
import json, os, sys, time, urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_code_routing_proof import load_humaneval, passes
from kry_shadow_demo import call, cost, CHEAP, FRONTIER, PRICES
from kry_shadow_emitter import emit_row

N = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 25
FAST = ("\n\nComplete this function. Return ONLY the complete Python function (signature + body, needed "
        "imports), in a ```python code block, no explanation.")


def _call(prompt, model, mt):
    for a in range(5):
        try:
            return call(prompt, model, mt)
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and a < 4:
                time.sleep(2 * (a + 1)); continue
            raise


def main():
    pool = load_humaneval()
    step = max(1, len(pool) // N)
    data = pool[::step][:N]   # even spread across the FULL benchmark (easy->hard), not just the easy front
    print(f"HumanEval shadow demo (LIVE): {len(data)} problems  cheap={CHEAP}  frontier={FRONTIER}\n")
    rows, saving, cp, fp, cs, fs = [], 0.0, 0, 0, 0.0, 0.0
    for i, p in enumerate(data):
        ctext, cit, cot = _call(p["prompt"] + FAST, CHEAP, 800)
        ftext, fit, fot = _call(p["prompt"] + FAST, FRONTIER, 800)
        cpass, fpass = passes(p, ctext), passes(p, ftext)
        ccost, fcost = cost(CHEAP, cit, cot), cost(FRONTIER, fit, fot)
        cp += cpass; fp += fpass; cs += ccost; fs += fcost
        row = emit_row(
            frame_id=f"he-{i}", request_id=f"he-{i}", intent_text=p.get("task_id", f"he-{i}"),
            requested_model="best/code", served_model=FRONTIER,
            measurement_class="deployable_validated", correctness_source="deployable",
            cheap_fast_correct=cpass, deployable_validator_pass=cpass, frontier_correct=fpass,
            cheap_fast_cost_usd=ccost, frontier_holdout_cost_usd=fcost,
            cheap_fast_output_tokens=cot, frontier_holdout_output_tokens=fot,
            response_cost_usd=fcost, provider_cost_source="pricing_table",
            checkable_slice="code_executable", deterministic_check_kind="unit_test",
            deterministic_check_receipt=f"{p.get('task_id')}: cheap={'pass' if cpass else 'fail'} frontier={'pass' if fpass else 'fail'}",
            output_axis_class="short_answer", latency_class="background")
        rows.append(row); saving += row["measured_row_value_usd"]
        print(f"[{i:2d}] {p.get('task_id','?'):14s} cheap={'PASS' if cpass else 'fail'} "
              f"frontier={'PASS' if fpass else 'fail'}  -> row saving ${row['measured_row_value_usd']:.5f}")
    n = len(rows)
    cheap_fail = n - cp
    summary = {
        "schema": "kry_shadow_demo_summary/v1",
        "label": "MECHANISM PROOF on a public benchmark (HumanEval) — NOT a real external-customer anchor",
        "honest_scope": "HumanEval = public benchmark (synthetic, not real external traffic); deployable gate = "
                        "executed tests (no oracle); net-not-gross (saving ONLY where the cheap code passed). "
                        "Shows the realistic cheap-fail/fall-back-to-frontier dynamic the toy demo could not.",
        "mode": "live_real_api", "cheap_model": CHEAP, "frontier_model": FRONTIER, "price_basis_usd_per_1M": PRICES,
        "tasks": n, "cheap_test_pass": cp, "cheap_fail_rows_zeroed": cheap_fail, "frontier_test_pass": fp,
        "cheap_adequacy_rate": round(cp / n, 4) if n else None,
        "cheap_spend_usd": round(cs, 6), "frontier_spend_usd_measured_baseline": round(fs, 6),
        "measured_net_saving_usd": round(saving, 6),
        "saving_rule": "sum over rows where the cheap code passed the deployable tests of (frontier_cost - cheap_cost); "
                       "cheap-fail rows contribute $0 (net-not-gross gate fires).",
        "p0_pass_rows": sum(r["p0_pass"] for r in rows), "row_digests": [r["row_digest"] for r in rows],
    }
    outdir = Path("docs/evidence/shadow_demo"); outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "shadow_demo_rows_humaneval.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (outdir / "shadow_demo_summary_humaneval.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n=== RECEIPT (LIVE — HumanEval) ===")
    print(f"  cheap adequacy (passed deployable tests): {cp}/{n} = {100*cp/n:.0f}%   "
          f"cheap-fail rows zeroed: {cheap_fail}   frontier passed: {fp}/{n}")
    print(f"  MEASURED net saving (cheap-passed rows, net-not-gross): ${saving:.6f}")
    print(f"  demo's own API spend: cheap ${cs:.6f} + frontier ${fs:.6f} = ${cs+fs:.6f}")
    print(f"  rows: {outdir}/shadow_demo_rows_humaneval.jsonl   summary: {outdir}/shadow_demo_summary_humaneval.json")
    print(f"  (MECHANISM proof on a public benchmark — not a real external anchor)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
