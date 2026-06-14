#!/usr/bin/env python3
"""depth-budget rescue lever on a REAL hard class (GSM8K) — does it survive, or was it a depth-2-arithmetic artifact?

The prior depth-2 run got rescue_rate 0.80 (->~1.0 at adequate budget) but on the EASIEST class, and ALL its
escalations were budget-TRUNCATION not capability. This re-runs the lever on GSM8K (grade-school math word
problems) with an ADEQUATE rescue budget (512 tok) so any failure is GENUINE capability, not truncation.

  TIER 1  FAST     Haiku, "Answer with only the final number.", max_tokens=12   -> last_number==gold? keep (savings)
  TIER 2  RESCUE   Haiku, "Think step by step, then give the final number.", max_tokens=512 -> ==gold? RESCUED
  TIER 3  ESCALATE Sonnet (frontier), same step-by-step prompt, max_tokens=512  -> the expensive call we avoid

SEALED INTERPRETATION (stated BEFORE reading numbers, in the runner docstring + report):
  rescue_rate >= 0.50 on GSM8K  = lever SURVIVES on a real hard class
  rescue_rate <= 0.20           = easy-class artifact (GSM8K failures are capability-bound, budget can't help)
  between                       = partial

DISCIPLINE: served-check every call (_anthropic raises if served != requested); objective gold-match scoring only
(NO LLM judge); keep raw FAST + rescue text for an audit sample; generator != verifier (recompute downstream).

NOT a deployment savings %. n=120, ONE model pair, ONE benchmark. Reuses _anthropic + last_number from
kry_rescue_experiment (NOT run_battery — that uses the depth ladder, unreliable on word problems; we use a
fixed ADEQUATE 512-tok rescue budget instead).
"""
from __future__ import annotations

import json, os, sys, time, urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_rescue_experiment import _anthropic, last_number

GSM8K_URL = "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl"
CHEAP = "claude-haiku-4-5-20251001"
FRONTIER = "claude-sonnet-4-6"
FAST_BUDGET = 12
RESCUE_BUDGET = 512
N = 120


def load_gsm8k():
    p = "/tmp/gsm8k.jsonl"
    if not os.path.exists(p):
        import urllib.request
        urllib.request.urlretrieve(GSM8K_URL, p)
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def _call(prompt: str, model: str, max_tokens: int) -> str:
    """_anthropic with a small retry on transient 429/529 (does NOT swallow served-check errors)."""
    for attempt in range(5):
        try:
            return _anthropic(prompt, model, max_tokens)
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and attempt < 4:
                time.sleep(2 * (attempt + 1)); continue
            raise


def main():
    data = load_gsm8k()
    sample = data[:N]                       # deterministic slice: first 120
    print(f"GSM8K rescue battery: {len(sample)} problems  cheap={CHEAP} frontier={FRONTIER}\n"
          f"FAST budget={FAST_BUDGET}  RESCUE budget={RESCUE_BUDGET} (ADEQUATE, fixed — not the depth ladder)\n")

    rows = []
    for i, ex in enumerate(sample):
        q = ex["question"]
        gold = ex["answer"].split("####")[-1].strip().replace(",", "")
        # TIER 1 FAST
        fast_out = _call(f"{q}\nAnswer with only the final number.", CHEAP, FAST_BUDGET)
        fast_correct = last_number(fast_out) == gold
        row = {"i": i, "gold": gold, "fast_out": fast_out, "fast_correct": fast_correct,
               "rescued": False, "escalated": False, "frontier_correct": None,
               "rescue_out": None, "frontier_out": None}
        if fast_correct:
            rows.append(row)
            print(f"[{i:3d}] FAST pass  (gold={gold})")
            continue
        # TIER 2 RESCUE
        resc_out = _call(f"{q}\nThink step by step, then give the final number.", CHEAP, RESCUE_BUDGET)
        rescued = last_number(resc_out) == gold
        row["rescue_out"] = resc_out
        row["rescued"] = rescued
        if rescued:
            rows.append(row)
            print(f"[{i:3d}] FAST fail -> RESCUED  (gold={gold})")
            continue
        # TIER 3 ESCALATE to frontier
        front_out = _call(f"{q}\nThink step by step, then give the final number.", FRONTIER, RESCUE_BUDGET)
        front_correct = last_number(front_out) == gold
        row["escalated"] = True
        row["frontier_out"] = front_out
        row["frontier_correct"] = front_correct
        rows.append(row)
        print(f"[{i:3d}] FAST fail -> rescue fail -> ESCALATE  (frontier_correct={front_correct}, gold={gold})")

    # METRICS (computed once here; report recomputes independently from rows)
    n = len(rows)
    kept_fast = sum(r["fast_correct"] for r in rows)
    fast_fail = n - kept_fast
    rescued = sum(r["rescued"] for r in rows)
    escalated = sum(r["escalated"] for r in rows)
    front_pass = sum(1 for r in rows if r["escalated"] and r["frontier_correct"])
    rescue_rate = round(rescued / fast_fail, 4) if fast_fail else None
    interp = ("SURVIVES" if (rescue_rate is not None and rescue_rate >= 0.50)
              else "easy-class-artifact" if (rescue_rate is not None and rescue_rate <= 0.20)
              else "partial")

    # cost estimate from call counts (Haiku $0.80/$4 per M, Sonnet $3/$15) — approximate (output tokens unknown,
    # so we bound output by the budget cap; this OVER-estimates output cost -> conservative-high)
    haiku_fast_calls = n                    # every item gets a FAST call
    haiku_rescue_calls = fast_fail          # every fast-fail gets a rescue call
    sonnet_calls = escalated
    out = {
        "schema": "kry_rescue_gsm8k/v1",
        "benchmark": "GSM8K (real grade-school math word problems)",
        "sample": f"first {N} of {len(data)} test problems (deterministic slice)",
        "cheap_model": CHEAP, "frontier_model": FRONTIER,
        "fast_budget": FAST_BUDGET, "rescue_budget": RESCUE_BUDGET,
        "n": n, "kept_fast": kept_fast, "fast_fail": fast_fail,
        "rescued": rescued, "escalated": escalated, "frontier_pass_on_escalated": front_pass,
        "metrics": {
            "fast_adequacy": round(kept_fast / n, 4),
            "rescue_rate": rescue_rate,
            "residual_escalation_rate": round(escalated / n, 4),
            "frontier_pass_rate_on_escalated": round(front_pass / escalated, 4) if escalated else None,
        },
        "sealed_interpretation": {
            "rule": "rescue_rate>=0.50 SURVIVES | <=0.20 easy-class-artifact | between partial",
            "verdict": interp,
        },
        "call_counts": {"haiku_fast": haiku_fast_calls, "haiku_rescue": haiku_rescue_calls, "sonnet": sonnet_calls},
        "honest_scope": ("rescue LEVER survival on ONE real hard class (GSM8K math), ONE model pair (Haiku/Sonnet); "
                         "NOT a deployment savings %. Adequate fixed 512-tok rescue budget so failures are genuine "
                         "capability, not truncation."),
        "rows": rows,
    }
    Path("docs/evidence/adequacy_gate").mkdir(parents=True, exist_ok=True)
    outpath = "docs/evidence/adequacy_gate/rescue_gsm8k_results.json"
    Path(outpath).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n=== GSM8K RESCUE BATTERY ===")
    print(f"  fast_adequacy:            {kept_fast}/{n} = {out['metrics']['fast_adequacy']}")
    print(f"  rescue_rate:              {rescued}/{fast_fail} = {rescue_rate}")
    print(f"  residual_escalation_rate: {escalated}/{n} = {out['metrics']['residual_escalation_rate']}")
    print(f"  frontier_pass_on_escalated: {front_pass}/{escalated} = {out['metrics']['frontier_pass_rate_on_escalated']}")
    print(f"  SEALED VERDICT: {interp}")
    print(f"  -> {outpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
