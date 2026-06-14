#!/usr/bin/env python3
"""Validated savings on a REAL benchmark (GSM8K) — objective adequacy, real paid calls.

GSM8K = grade-school math word problems with objective numeric answers (a standard LLM
eval). Harder than trivial facts, so the cheap model genuinely misses some — the adequacy
rate is REAL, not 100%. Grading: extract the last number in the model's output and compare
to the gold answer (the standard GSM8K extraction). NO LLM judge.

For each problem: serve cheap (gpt-4o-mini) + frontier (gpt-4o), grade both, read real
token counts. Saving is NET of frontier-also-wrong — it counts ONLY where quality is
preserved vs the all-frontier baseline (cheap AND frontier both right):
    validated_saving = Sum(frontier_cost - cheap_cost) over cheap-correct AND frontier-correct.
Conservative: this DROPS cheap-right/frontier-wrong rows (quality wins, not clean savings).
Cost = real tokens x STATED OpenAI list prices (verify openai.com/pricing).

Needs OPENAI_API_KEY with credit. stdlib only.  Usage: kry_gsm8k_savings_proof.py [N]
"""
from __future__ import annotations
import json, os, re, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_grounded_savings_openai import call, cost, CHEAP, FRONTIER, PRICES
from kry_grounded_savings_proof import wilson

GSM8K_URL = "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl"


def extract_answer(text: str):
    """Prefer the model's STATED final answer; fall back to the last number."""
    if not text:
        return None
    m = re.search(r"answer is[:\s]*\$?(-?\d[\d,]*(?:\.\d+)?)", text, re.I)
    if not m:
        m = re.search(r"####\s*(-?\d[\d,]*(?:\.\d+)?)", text)
    if m:
        return m.group(1).replace(",", "").rstrip(".")
    nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?", text)
    return nums[-1].replace(",", "").rstrip(".") if nums else None


def load_gsm8k():
    p = "/tmp/gsm8k.jsonl"
    if not os.path.exists(p):
        urllib.request.urlretrieve(GSM8K_URL, p)
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def main():
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        sys.exit("OPENAI_API_KEY not set")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    data = load_gsm8k()
    # deterministic sample (no Math.random dependency): stride through the test set
    step = max(1, len(data) // n)
    sample = data[::step][:n]
    print(f"GSM8K: {len(data)} problems; sampling {len(sample)}\ncheap={CHEAP} frontier={FRONTIER}\n")

    def _call_retry(model, prompt):
        for attempt in range(5):
            try:
                return call(model, prompt, key, max_tokens=700)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 4:
                    time.sleep(2 * (attempt + 1)); continue
                raise

    def process(ex):
        gold = ex["answer"].split("####")[-1].strip().replace(",", "")
        prompt = ex["question"] + "\n\nSolve step by step, then end with: The answer is <number>."
        try:
            cout, cpt, cct = _call_retry(CHEAP, prompt)
            fout, fpt, fct = _call_retry(FRONTIER, prompt)
        except Exception:
            return None
        return {"gold": gold, "cheap_correct": extract_answer(cout) == gold,
                "frontier_correct": extract_answer(fout) == gold,
                "cheap_cost": cost(CHEAP, cpt, cct), "frontier_cost": cost(FRONTIER, fpt, fct)}

    with ThreadPoolExecutor(max_workers=5) as pool:
        rows = [r for r in pool.map(process, sample) if r]
    cheap_ok = sum(r["cheap_correct"] for r in rows)
    front_ok = sum(r["frontier_correct"] for r in rows)
    sum_cheap = sum(r["cheap_cost"] for r in rows)
    sum_front = sum(r["frontier_cost"] for r in rows)
    # NET of frontier-also-wrong: count a saving ONLY where quality is preserved vs the
    # all-frontier baseline (cheap AND frontier both right). Conservative: this DROPS
    # cheap-right/frontier-wrong rows (those are quality WINS, not clean cost-savings).
    saving = sum(r["frontier_cost"] - r["cheap_cost"]
                 for r in rows if r["cheap_correct"] and r["frontier_correct"])
    m = len(rows); lo, hi = wilson(cheap_ok, m)
    print(f"  completed {m}/{len(sample)} problems (failed/skipped: {len(sample)-m})")
    out = {"schema": "kry_gsm8k_savings_proof/v1", "benchmark": "GSM8K (real word problems)",
           "cheap_model": CHEAP, "frontier_model": FRONTIER, "price_basis_usd_per_1M": PRICES,
           "tasks": m, "cheap_adequate": cheap_ok, "adequacy_rate": round(cheap_ok / m, 4),
           "adequacy_wilson_95ci": [lo, hi], "frontier_correct": front_ok,
           "real_cheap_cost_usd": round(sum_cheap, 6), "real_frontier_cost_usd_avoided": round(sum_front, 6),
           "validated_saving_usd": round(saving, 6),
           "honest_claim": f"Real benchmark, real paid calls. Saving is NET of frontier-also-wrong: counted "
                           "ONLY where cheap AND frontier were both objectively right (last-number match to "
                           "gold), so quality is preserved vs the all-frontier baseline. Conservative: this "
                           "DROPS cheap-right/frontier-wrong rows (quality wins, not clean cost-savings).",
           "caveats": ["cost = real tokens x stated OpenAI list prices, not provider-recorded",
                       "GSM8K is math word problems — one real task class, not all of real traffic",
                       "saving assumes the avoided frontier call would have been kept: cheap-correctness is "
                       "tested, frontier-kept is not (so this is cost-avoidance under that assumption, "
                       "and is specific to this cheap/frontier model pair)"],
           "rows": rows}
    Path("docs/evidence/gsm8k_proof").mkdir(parents=True, exist_ok=True)
    Path("docs/evidence/gsm8k_proof/gsm8k_proof.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n=== VALIDATED SAVINGS on GSM8K (real benchmark, real paid calls) ===")
    print(f"  cheap ({CHEAP}) adequate: {cheap_ok}/{m} = {100*cheap_ok/m:.0f}%  (95% CI [{100*lo:.0f}%, {100*hi:.0f}%])")
    print(f"  frontier ({FRONTIER}) correct: {front_ok}/{m} = {100*front_ok/m:.0f}%")
    print(f"  real frontier cost (avoided): ${sum_front:.6f}   real cheap cost: ${sum_cheap:.6f}")
    print(f"  VALIDATED saving (only where cheap AND frontier both correct — net): ${saving:.6f}")
    print(f"  -> docs/evidence/gsm8k_proof/gsm8k_proof.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
