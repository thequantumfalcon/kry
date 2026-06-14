#!/usr/bin/env python3
"""Deterministically generate the depth-budget rescue battery — multi-step problems, golds COMPUTED in-code.

Per the project's rule (never hand-transcribe a number — risks fabricated constants): the gold is
computed from a facts table + operation functions, not typed. The prompt gives NO numbers — the cheap model
must DERIVE the facts (spider->8) itself, so these are genuine fact-derivation + multi-step-COMPUTATION items
(the multi-step wall class) where a cheap one-shot is expected to fail fluently and depth-matched budget may rescue.

Output: docs/evidence/adequacy_gate/rescue_battery.jsonl  ({"prompt":..., "gold":..., "depth_ops":N} per line)
Feed it to scripts/kry_rescue_experiment.py (operator-gated real run; free local Ollama or paid API).
"""
from __future__ import annotations

import json
from pathlib import Path

# fact table (the cheap model must retrieve these — retrieval is free; the COMPUTATION is the wall)
FACTS = {
    "legs a spider has": 8, "sides a triangle has": 3, "sides a square has": 4, "sides a pentagon has": 5,
    "sides a hexagon has": 6, "days in a week": 7, "sides an octagon has": 8, "fingers on one hand": 5,
    "wheels on a car": 4, "sides a heptagon has": 7,
}
OPS = {"plus": lambda a, b: a + b, "minus": lambda a, b: a - b, "times": lambda a, b: a * b}
# two-step templates: (A op1 B) op2 C  -> depth 2 (two arithmetic ops); C is a fact or a small literal
FK = list(FACTS)


def build():
    items, seen = [], set()
    # deterministic walk: every (a,b) fact pair x every op1 x {op2 with a literal 2, op2 with a third fact}
    for i, a in enumerate(FK):
        for b in FK[i + 1:]:
            for o1 in ("plus", "times"):
                for o2 in ("times", "minus"):
                    base = OPS[o1](FACTS[a], FACTS[b])
                    # variant 1: second op against a literal 2
                    g = OPS[o2](base, 2)
                    if g > 0:
                        key = (a, b, o1, o2, "lit2")
                        if key not in seen:
                            seen.add(key)
                            items.append({"prompt": f"Take how many {a} {o1} how many {b}, then {o2} 2.",
                                          "gold": g, "depth_ops": 2})
                    # variant 2: second op against a third distinct fact
                    c = FK[(i + 3) % len(FK)]
                    if c not in (a, b):
                        g2 = OPS[o2](base, FACTS[c])
                        if g2 > 0:
                            key = (a, b, o1, o2, c)
                            if key not in seen:
                                seen.add(key)
                                items.append({"prompt": f"Take how many {a} {o1} how many {b}, "
                                                        f"then {o2} how many {c}.",
                                              "gold": g2, "depth_ops": 2})
    return items[:40]   # cap a clean battery


def main():
    items = build()
    out = Path(__file__).resolve().parents[1] / "docs/evidence/adequacy_gate/rescue_battery.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(x) for x in items) + "\n", encoding="utf-8")
    # self-check: golds are integers, prompts carry no digits except the literal '2' (no fact leak)
    import re
    for x in items:
        assert isinstance(x["gold"], int)
        leaked = [n for n in re.findall(r"\d+", x["prompt"]) if n != "2"]
        assert not leaked, f"fact leaked into prompt: {x['prompt']}"
    print(f"wrote {len(items)} items -> {out}")
    print("sample:", json.dumps(items[0]))
    print(f"depth_ops=2 (multi-step) items: {sum(i['depth_ops'] == 2 for i in items)}/{len(items)}")


if __name__ == "__main__":
    main()
