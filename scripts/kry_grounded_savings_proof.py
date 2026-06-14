#!/usr/bin/env python3
"""Validated savings proof on GROUNDED tasks — real provider costs + OBJECTIVE adequacy.

For each task (gold answer computed in Python; NO LLM judge):
  - serve the FREE model (cheap leg) AND the PAID frontier model (the avoided model)
  - grade each output by token match to the gold answer
  - read BOTH real costs from OpenRouter's own per-request generation records
A saving is NET of frontier-also-wrong — it counts ONLY where quality is preserved vs the
all-frontier baseline (the FREE AND frontier answers were both correct):
    validated_saving = Sum(frontier_cost - free_cost) over free-correct AND frontier-correct
Conservative: this DROPS free-right/frontier-wrong tasks (quality wins, not clean savings).
Reports the adequacy rate with a Wilson 95% CI.

HONEST BOUNDED CLAIM: groundable tasks (objective answers) are the EASY case for
adequacy. This does NOT represent open-ended real traffic where "adequate" is contested.
What it proves: real avoided cost (measured, not declared) + a real, quality-gated saving
on a task class where adequacy is objectively checkable.

Needs OPENROUTER_API_KEY with credit. stdlib only.
"""
from __future__ import annotations
import json, math, os, random, re, sys, time, urllib.error, urllib.request
from pathlib import Path

FREE = "openai/gpt-oss-120b:free"
PAID_FALLBACK = ["anthropic/claude-opus-4.8", "openai/gpt-5.5", "openai/gpt-4o", "anthropic/claude-3.5-sonnet"]


def _key():
    k = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not k:
        sys.exit("OPENROUTER_API_KEY not set")
    return k


def build_tasks(seed=20260610):
    random.seed(seed)
    SYM = {"gold": "Au", "oxygen": "O", "iron": "Fe", "sodium": "Na",
           "hydrogen": "H", "carbon": "C", "silver": "Ag", "potassium": "K"}
    CAP = {"France": "Paris", "Japan": "Tokyo", "Egypt": "Cairo",
           "Canada": "Ottawa", "Brazil": "Brasilia", "Norway": "Oslo"}
    t = []
    for _ in range(8):
        a, b = random.randint(11, 99), random.randint(11, 99)
        if random.random() < 0.5:
            t.append((f"What is {a} + {b}? Reply with only the number.", str(a + b)))
        else:
            t.append((f"What is {a} * {b}? Reply with only the number.", str(a * b)))
    for k, v in SYM.items():
        t.append((f"What is the chemical symbol for {k}? Reply with only the symbol.", v))
    for k, v in CAP.items():
        t.append((f"What is the capital city of {k}? Reply with only the city.", v))
    for w in ("provenance", "attestation"):
        t.append((f"How many letters are in the word '{w}'? Reply with only the number.", str(len(w))))
    return t  # 24 grounded tasks


def grade(output: str, gold: str) -> bool:
    """Objective: gold (lowercased) must appear as a standalone token in the output."""
    toks = re.findall(r"[a-z0-9]+", (output or "").lower())
    return gold.lower() in toks


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (round(max(0.0, c - h), 4), round(min(1.0, c + h), 4))


def chat(model, prompt, key, max_tokens=120):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.load(r)
    return d["id"], (d["choices"][0]["message"]["content"] or "")


def gen_cost(gid, key, tries=8):
    for i in range(tries):
        try:
            req = urllib.request.Request(f"https://openrouter.ai/api/v1/generation?id={gid}",
                                         headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r).get("data", {})
            if d.get("total_cost") is not None:
                return float(d["total_cost"])
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def main():
    key = _key()
    tasks = build_tasks()
    # find a billable paid frontier model
    paid_model = None
    for m in PAID_FALLBACK:
        try:
            chat(m, "Reply with the number 1.", key, 5); paid_model = m; break
        except urllib.error.HTTPError as e:
            print(f"  probe {m} -> HTTP {e.code}" + (" (no credit)" if e.code == 402 else ""), file=sys.stderr)
    if not paid_model:
        sys.exit("no billable paid model — add OpenRouter credit.")
    print(f"frontier (avoided) model: {paid_model}\nrunning {len(tasks)} grounded tasks...\n")

    # phase 1: all calls (fast); phase 2: fetch costs after records flush
    calls = []
    for prompt, gold in tasks:
        fid, fout = chat(FREE, prompt, key)
        pid, pout = chat(paid_model, prompt, key)
        calls.append((gold, fout, pout, fid, pid))
        print(f"  gold={gold:<8} free={'OK ' if grade(fout, gold) else 'MISS'} "
              f"frontier={'OK ' if grade(pout, gold) else 'MISS'}")
    print("\nletting provider records flush, then reading real costs...")
    time.sleep(6)
    rows, free_ok, frontier_ok = [], 0, 0
    sum_free = sum_frontier = saving = 0.0
    for gold, fout, pout, fid, pid in calls:
        fcorrect, pcorrect = grade(fout, gold), grade(pout, gold)
        fcost = gen_cost(fid, key) or 0.0
        pcost = gen_cost(pid, key) or 0.0
        free_ok += fcorrect; frontier_ok += pcorrect
        sum_free += fcost; sum_frontier += pcost
        if fcorrect and pcorrect:        # NET of frontier-also-wrong: saving counts ONLY where
            saving += (pcost - fcost)     # quality is preserved vs the all-frontier baseline.
            # Conservative: DROPS free-right/frontier-wrong rows (quality wins, not clean savings).
        rows.append({"gold": gold, "free_correct": fcorrect, "frontier_correct": pcorrect,
                     "free_cost": fcost, "frontier_cost": pcost, "free_gen": fid, "frontier_gen": pid})

    n = len(tasks)
    lo, hi = wilson(free_ok, n)
    out = {
        "schema": "kry_grounded_savings_proof/v1",
        "free_model": FREE, "frontier_model": paid_model, "tasks": n,
        "free_adequate": free_ok, "adequacy_rate": round(free_ok / n, 4),
        "adequacy_wilson_95ci": [lo, hi],
        "frontier_correct": frontier_ok,
        "real_free_cost_usd": round(sum_free, 6),
        "real_frontier_cost_usd_avoided": round(sum_frontier, 6),
        "validated_saving_usd": round(saving, 6),
        "honest_claim": "Real provider costs (measured, not declared). Saving is NET of frontier-also-wrong: "
                        "counted ONLY where the free AND frontier answers were both objectively correct, so "
                        "quality is preserved vs the all-frontier baseline. Conservative: this DROPS "
                        "free-right/frontier-wrong tasks (quality wins, not clean cost-savings).",
        "bounded_claim": "Grounded tasks = the EASY case for adequacy (objective answers). Does NOT "
                         "represent open-ended real traffic; that needs different adequacy validation.",
        "rows": rows,
    }
    Path("docs/evidence/grounded_proof").mkdir(parents=True, exist_ok=True)
    Path("docs/evidence/grounded_proof/grounded_proof.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("\n=== VALIDATED SAVINGS PROOF (real costs, objective adequacy) ===")
    print(f"  free model adequate: {free_ok}/{n} = {100*free_ok/n:.0f}%  (95% CI [{100*lo:.0f}%, {100*hi:.0f}%])")
    print(f"  frontier correct:    {frontier_ok}/{n} (sanity)")
    print(f"  real frontier cost (avoided): ${sum_frontier:.6f}   real free cost: ${sum_free:.6f}")
    print(f"  VALIDATED saving (only where free AND frontier both correct — net): ${saving:.6f}")
    print("  -> docs/evidence/grounded_proof/grounded_proof.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
