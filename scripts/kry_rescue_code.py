#!/usr/bin/env python3
"""depth-budget rescue on the DEPLOYABLE class — CODE (HumanEval, executed test-pass gate; NO oracle).

This is the architecturally-complete test: the adequacy gate here is DEPLOYABLE (run the problem's own tests —
you don't need the gold answer), uniting the two surviving levers (verification-asymmetry gate + depth-budget research rescue).

  TIER 1  FAST     Haiku, "return ONLY the function" (direct codegen)        -> tests PASS? keep (savings)
  TIER 2  RESCUE   Haiku, "think step by step, then the function in a block" -> tests PASS? RESCUED (cheap reason budget)
  TIER 3  ESCALATE Sonnet, same reason-then-code                            -> the expensive call we avoid

The "rescue" for code = giving the cheap model room to REASON before coding (the multi-step wall = multi-step; a hard
function is multi-step). Gate = executed tests (deployable). Reuses passes()/load_humaneval()/extract_code()
from kry_code_routing_proof and the served-checked _anthropic from kry_rescue_experiment.

SEALED (before numbers): rescue_rate >= 0.50 = lever SURVIVES on the deployable code class; <= 0.20 = artifact.
Honest scope: HumanEval = toy functions (real code adequacy lower/contested); n small; one model pair; the acceptance-gate measurement
counterfactual confound stands. served-checked; objective executed-test scoring (no LLM judge).
"""
from __future__ import annotations

import json, os, sys, time, urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_code_routing_proof import load_humaneval, passes
from kry_rescue_experiment import _anthropic

CHEAP, FRONTIER = "claude-haiku-4-5-20251001", "claude-sonnet-4-6"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 40   # full HumanEval = 164 (more cheap-failures -> powered)
FAST = ("\n\nComplete this function. Return ONLY the complete Python function "
        "(signature + body, needed imports), in a ```python code block, no explanation.")
REASON = ("\n\nThink step by step about the algorithm and the edge cases, then write the complete Python "
          "function (signature + body, needed imports) in a ```python code block at the end.")


def _call(prompt, model, max_tokens):
    for attempt in range(5):
        try:
            return _anthropic(prompt, model, max_tokens)
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and attempt < 4:
                time.sleep(2 * (attempt + 1)); continue
            raise


def main():
    data = load_humaneval()[:N]
    print(f"CODE rescue (HumanEval, executed-test gate): {len(data)} problems  cheap={CHEAP} frontier={FRONTIER}\n")
    rows = []
    for i, p in enumerate(data):
        fast = _call(p["prompt"] + FAST, CHEAP, 700)
        if passes(p, fast):
            rows.append({"i": i, "outcome": "kept_fast"}); print(f"[{i:2d}] FAST pass"); continue
        resc = _call(p["prompt"] + REASON, CHEAP, 1400)
        if passes(p, resc):
            rows.append({"i": i, "outcome": "rescued"}); print(f"[{i:2d}] FAST fail -> RESCUED"); continue
        front = _call(p["prompt"] + REASON, FRONTIER, 1400)
        fp = passes(p, front)
        rows.append({"i": i, "outcome": "escalated", "frontier_pass": fp})
        print(f"[{i:2d}] FAST fail -> rescue fail -> ESCALATE (frontier_pass={fp})")

    n = len(rows)
    kf = sum(r["outcome"] == "kept_fast" for r in rows)
    rescued = sum(r["outcome"] == "rescued" for r in rows)
    esc = sum(r["outcome"] == "escalated" for r in rows)
    ff = rescued + esc
    fp = sum(1 for r in rows if r["outcome"] == "escalated" and r.get("frontier_pass"))
    rate = round(rescued / ff, 4) if ff else None
    verdict = ("SURVIVES" if (rate is not None and rate >= 0.50)
               else "artifact" if (rate is not None and rate <= 0.20) else "partial")
    out = {"schema": "kry_rescue_code/v1", "benchmark": "HumanEval (executed test-pass gate — DEPLOYABLE, no oracle)",
           "cheap_model": CHEAP, "frontier_model": FRONTIER, "n": n,
           "kept_fast": kf, "fast_fail": ff, "rescued": rescued, "escalated": esc, "frontier_pass_on_escalated": fp,
           "metrics": {"fast_adequacy": round(kf / n, 4), "rescue_rate": rate,
                       "residual_escalation_rate": round(esc / n, 4)},
           "sealed_verdict": verdict,
           "honest_scope": f"HumanEval toy functions; n={n}; Haiku/Sonnet; the acceptance-gate measurement counterfactual confound stands; "
                           "deployable gate = executed tests (no oracle).", "rows": rows}
    Path("docs/evidence/adequacy_gate/rescue_code_results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("\n=== CODE RESCUE (deployable gate) ===")
    print(f"  fast_adequacy: {kf}/{n}={out['metrics']['fast_adequacy']}  rescue_rate: {rescued}/{ff}={rate}  "
          f"residual_escalation: {esc}/{n}={out['metrics']['residual_escalation_rate']}  frontier_pass_on_esc: {fp}/{esc}")
    print(f"  SEALED VERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
