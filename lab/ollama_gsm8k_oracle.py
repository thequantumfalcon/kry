#!/usr/bin/env python3
"""REAL GSM8K cheap-vs-capable oracle on a node's local Ollama (stdlib only). Run ON the node.

Unlike the trivial arithmetic probe (where cheap == capable, so the gate proves nothing), GSM8K is
hard enough that a 7B genuinely fails a real fraction a 14B handles -> a measurable adequacy GAP, which
is the only regime where routing has value. We run BOTH models with chain-of-thought on real GSM8K
test problems, grade by the exact #### gold number, and report:

  * cheap adequacy, capable adequacy, and the GAP (+ Wilson 95% CIs)
  * the rescue rate  = P(cheap wrong & capable right) -- what escalation must catch
  * routing economics = the cheap-adequate fraction KRY could route cheap on this slice
  * independent_agreement = holdout-vs-truth, bucketed by problem difficulty (gold step count):
        does KRY's small holdout estimate of the per-bucket cheap-paid-rate bracket the real
        production rate? >= 0.80 of buckets passing == the holdout methodology recovers reality.

    python ollama_gsm8k_oracle.py <cheap-model> <capable-model> [N]
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import tempfile
import urllib.request

OLLAMA = "http://localhost:11434/api/generate"
GSM8K_URL = "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl"
COT = ("\n\nSolve step by step. On the last line, give ONLY the final answer in this exact "
       "format:\n#### <number>")


def load_gsm8k(n: int):
    cache = os.path.join(tempfile.gettempdir(), "gsm8k_test.jsonl")
    if not os.path.exists(cache):
        with urllib.request.urlopen(GSM8K_URL, timeout=60) as r, open(cache, "wb") as f:
            f.write(r.read())
    rows = [json.loads(line) for line in open(cache, encoding="utf-8") if line.strip()]
    random.Random(20260613).shuffle(rows)
    return rows[:n]


def gold_of(answer: str) -> int:
    return int(answer.split("####")[-1].strip().replace(",", ""))


def steps_of(answer: str) -> int:
    return answer.count("<<")  # GSM8K annotates each arithmetic step <<a+b=c>>


def ask(model: str, prompt: str, think: bool) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "think": think, "options": {"temperature": 0, "seed": 42}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["response"]


def extract(text: str):
    """Prefer the GSM8K '#### <number>' the model was asked to emit; fall back to last integer."""
    t = (text or "").replace(",", "")
    m = re.findall(r"####\s*(-?\d+)", t)
    if m:
        return int(m[-1])
    nums = re.findall(r"-?\d+", t)
    return int(nums[-1]) if nums else None


def wilson(k: int, n: int):
    if n == 0:
        return (0.0, 1.0)
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


def bucket(steps: int) -> str:
    return "easy(1-2)" if steps <= 2 else ("med(3-4)" if steps <= 4 else "hard(5+)")


if __name__ == "__main__":
    cheap = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:7b"
    capable = sys.argv[2] if len(sys.argv) > 2 else "qwen3:14b"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    rows = load_gsm8k(n)
    print(f"REAL GSM8K: {len(rows)} test problems (seeded sample). cheap={cheap} capable={capable}\n", flush=True)

    recs = []
    for i, row in enumerate(rows):
        gold = gold_of(row["answer"])
        q = row["question"] + COT
        try:
            cap_ok = extract(ask(capable, q, think=True)) == gold
        except Exception:
            cap_ok = False
        try:
            ch_ok = extract(ask(cheap, q, think=False)) == gold
        except Exception:
            ch_ok = False
        recs.append({"b": bucket(steps_of(row["answer"])), "cap": cap_ok, "ch": ch_ok})
        print(f"  [{i + 1}/{len(rows)}] {recs[-1]['b']:9} cheap={'OK' if ch_ok else 'XX'} capable={'OK' if cap_ok else 'XX'}", flush=True)

    ch_ok = sum(r["ch"] for r in recs)
    cap_ok = sum(r["cap"] for r in recs)
    rescue = sum(1 for r in recs if r["cap"] and not r["ch"])
    cl, chh = wilson(ch_ok, n)
    pl, ph = wilson(cap_ok, n)
    print(f"\ncheap   adequacy: {ch_ok}/{n} = {ch_ok / n:.0%}  (95% CI [{cl:.2f},{chh:.2f}])")
    print(f"capable adequacy: {cap_ok}/{n} = {cap_ok / n:.0%}  (95% CI [{pl:.2f},{ph:.2f}])")
    print(f"adequacy GAP: {(cap_ok - ch_ok) / n:+.0%}   rescue rate P(cheap wrong & capable right): {rescue}/{n} = {rescue / n:.0%}")
    print(f"routing economics: KRY could route the {ch_ok}/{n} cheap-adequate problems to the 7B and "
          f"stay correct; the rest must escalate (the real 'paid' rate = {1 - ch_ok / n:.0%}).")

    print("\nholdout-vs-truth (cheap 'paid' = cheap wrong), bucketed by gold difficulty:")
    print(f"{'bucket':10} {'holdout est':>18} {'production truth':>18} {'brackets?':>10}")
    buckets = sorted({r["b"] for r in recs})
    ok_b = 0
    for b in buckets:
        wrong = [0 if r["ch"] else 1 for r in recs if r["b"] == b]  # 1 == cheap inadequate == paid
        hn = max(1, len(wrong) // 2)
        hold, prod = wrong[:hn], wrong[hn:]
        lo, hi = wilson(sum(hold), len(hold))
        truth = sum(prod) / len(prod) if prod else 0.0
        brackets = lo <= truth <= hi
        ok_b += brackets
        print(f"{b:10} {f'{sum(hold)}/{len(hold)} [{lo:.2f},{hi:.2f}]':>18} {f'{sum(prod)}/{len(prod)}={truth:.2f}':>18} {('YES' if brackets else 'no'):>10}")
    agree = ok_b / len(buckets) if buckets else 0.0
    print(f"\nindependent_agreement = {ok_b}/{len(buckets)} = {agree:.2f}  "
          f"({'PASS >= 0.80' if agree >= 0.80 else 'FAIL < 0.80'}) on REAL GSM8K. "
          f"Internal cross-check on own hardware, not an external counterparty.")
