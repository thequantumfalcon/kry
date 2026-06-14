#!/usr/bin/env python3
"""Minimal REAL cheap-vs-capable adequacy probe on a node's local Ollama (run ON the node).

The KRY routing thesis on real local models + GPU: send deterministically-checkable questions to a
CHEAP and a CAPABLE model, grade by EXACT answer (no LLM judge), and report the cheap model's adequacy
= the fraction of this slice KRY could route to the cheap model and stay correct. stdlib only (urllib).

    python lab/ollama_adequacy_probe.py <cheap-model> <capable-model>
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

OLLAMA = "http://localhost:11434/api/generate"
QUESTIONS = [
    ("What is 17 * 24? Reply with only the final number.", 408),
    ("A train goes 60 miles in 1.5 hours. Its speed in mph? Reply with only the final number.", 40),
    ("A $25 shirt is 20% off. The sale price in dollars? Reply with only the final number.", 20),
    ("What is 144 / 12 + 7? Reply with only the final number.", 19),
    ("Sum of the integers from 1 to 10? Reply with only the final number.", 55),
    ("What is 15% of 200? Reply with only the final number.", 30),
    ("If 3x = 21, what is x? Reply with only the final number.", 7),
    ("Area of an 8 by 5 rectangle? Reply with only the final number.", 40),
]


def ask(model: str, prompt: str) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "think": False, "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["response"]


def last_int(text: str):
    nums = re.findall(r"-?\d+", text.replace(",", ""))
    return int(nums[-1]) if nums else None


def run(model: str) -> tuple[int, int]:
    ok = 0
    for q, gold in QUESTIONS:
        try:
            got = last_int(ask(model, q))
        except Exception as e:
            print(f"  [err] {model}: {e}")
            continue
        hit = got == gold
        ok += hit
        print(f"  {'OK ' if hit else 'XX '} {model:30s} got={got} gold={gold}")
    return ok, len(QUESTIONS)


if __name__ == "__main__":
    cheap = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:7b"
    capable = sys.argv[2] if len(sys.argv) > 2 else "qwen3:14b"
    print(f"=== CAPABLE: {capable} ===")
    cap_ok, n = run(capable)
    print(f"=== CHEAP: {cheap} ===")
    cheap_ok, _ = run(cheap)
    print(f"\nCapable adequacy: {cap_ok}/{n} = {cap_ok / n:.0%}")
    print(f"Cheap adequacy:   {cheap_ok}/{n} = {cheap_ok / n:.0%}")
    print(f"-> KRY can route {cheap_ok}/{n} of this slice to the CHEAP model and stay correct "
          f"(deterministic exact-answer gate, no LLM judge); the rest escalate.")
