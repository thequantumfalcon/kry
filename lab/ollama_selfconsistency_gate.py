#!/usr/bin/env python3
"""No-gold observable routing gate prototype, tested on real GSM8K (stdlib only). Run ON the node.

The oracle (lab/ollama_gsm8k_oracle.py) measured the *opportunity* with a GOLD-derived gate -- which
cannot exist at inference. The disclosed open problem is an OBSERVABLE gate: decide "is the cheap
model adequate here?" WITHOUT the answer. KRY discloses its current acceptance gate at ~0% correctness
specificity. This tests the most principled no-gold signal --
the cheap model's SELF-CONSISTENCY: sample it K times at temperature; agreement = confidence.

For each GSM8K problem we sample cheap K times, take the majority answer (what self-consistency would
actually output) and the agreement fraction (the gate signal), and reuse the capable column from a
prior deterministic run. We then route cheap when agreement >= threshold, else escalate, and report:
does agreement predict cheap correctness, the route-cheap precision vs the 55% base rate, the routed
system accuracy vs all-cheap / all-capable, and the honest cost (the gate itself costs K cheap calls).

    python ollama_selfconsistency_gate.py <cheap-model> <capable_correct.json> [N] [K]
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import tempfile
import urllib.request
from collections import Counter

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


def ask(model: str, prompt: str, seed: int) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "think": False,
                       "options": {"temperature": 0.8, "seed": seed}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["response"]


def extract(text: str):
    t = (text or "").replace(",", "")
    m = re.findall(r"####\s*(-?\d+)", t)
    if m:
        return int(m[-1])
    nums = re.findall(r"-?\d+", t)
    return int(nums[-1]) if nums else None


if __name__ == "__main__":
    cheap = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:1.5b"
    cap_correct = json.load(open(sys.argv[2], encoding="utf-8")) if len(sys.argv) > 2 else None
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 80
    k = int(sys.argv[4]) if len(sys.argv) > 4 else 5
    rows = load_gsm8k(n)
    print(f"cheap={cheap}  N={n}  K={k} self-consistency samples @ temp 0.8  (no-gold gate)\n", flush=True)

    recs = []  # each: {samples: [..K..], gold, cap}
    for i, row in enumerate(rows):
        gold = gold_of(row["answer"])
        q = row["question"] + COT
        samples = []
        for j in range(k):
            try:
                samples.append(extract(ask(cheap, q, seed=100 + j)))
            except Exception:
                samples.append(None)
        maj, cnt = Counter(samples).most_common(1)[0]
        recs.append({"samples": samples, "gold": gold, "cap": bool(cap_correct[i]) if cap_correct else None})
        print(f"  [{i + 1}/{n}] agree={cnt / k:.1f} cheap_majority={'OK' if maj == gold else 'XX'}", flush=True)

    N = len(recs)
    ratio = 9.0  # 1.5b vs 14b parameter ratio ~ cost proxy
    has_cap = recs[0]["cap"] is not None

    def vote(samples, kk):
        maj, cnt = Counter(samples[:kk]).most_common(1)[0]
        return maj, cnt / kk

    print("\n--- agreement (at full K) predicts cheap correctness ---")
    for lo, hi, lab in [(1.0, 1.01, f"{k}/{k}"), (0.8, 1.0, "high"), (0.6, 0.8, "mid"), (0.0, 0.6, "low")]:
        grp = [r for r in recs if lo <= vote(r["samples"], k)[1] < hi]
        if grp:
            ok = sum(vote(r["samples"], k)[0] == r["gold"] for r in grp)
            print(f"  agreement {lab:5}: {len(grp):2} problems, cheap correct {ok}/{len(grp)} = {ok / len(grp):.0%}")

    if has_cap:
        cap_all = sum(r["cap"] for r in recs) / N
        print(f"\nbaseline all-capable accuracy = {cap_all:.0%}  (gate aims to match/beat this at lower cost)")

    print("\n--- COST/SIGNAL SWEEP: route cheap iff the first K' samples are UNANIMOUS, else escalate ---")
    print(f"{'K(samples)':>10} {'route-cheap':>11} {'route-cheap prec':>17} {'system acc':>11} {'cost (cap-equiv/prob)':>22}")
    for kk in range(2, k + 1):
        routed = [r for r in recs if vote(r["samples"], kk)[1] == 1.0]
        esc = [r for r in recs if vote(r["samples"], kk)[1] < 1.0]
        if not routed:
            continue
        prec = sum(vote(r["samples"], kk)[0] == r["gold"] for r in routed) / len(routed)
        sys_acc = (sum(vote(r["samples"], kk)[0] == r["gold"] for r in routed)
                   + (sum(r["cap"] for r in esc) if has_cap else 0)) / N if has_cap else float("nan")
        cost = (kk / ratio) + (len(esc) / N) * 1.0
        print(f"{kk:>10} {f'{len(routed)}/{N}':>11} {prec:>16.0%} {sys_acc:>11.0%} {cost:>22.2f}")
    print(f"\n(all-capable costs 1.00 cap-equiv/prob. Lower K shrinks gate overhead K/{ratio:.0f}; find the K where")
    print(" precision stays high AND cost < 1.00.) No-gold gate on REAL GSM8K; internal prototype, not wired in.")
