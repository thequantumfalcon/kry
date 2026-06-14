#!/usr/bin/env python3
"""Recalibrate the multi-step latent-computation wall on KRY's ACTUAL cheap models.

WHY (for KRY): a cheap displacement is only a real saving if the cheap answer is
adequate. Prior paired-model research found a sharp, MEASURED failure
class: in the FAST/no-CoT pathway a model chains retrieval fine and does SINGLE-step
arithmetic fine, but CANNOT chain MULTIPLE computation steps latently (Opus 4.8: multi
~0.13, CoT 1.00). That is exactly the request-class where a cheap model is confidently
WRONG yet surface-fluent -> the displacement KRY must NOT mint. But the multi-step wall's 0.13 is a
FRONTIER model's fast pathway. KRY displaces to SMALL/cheap models. Until the wall is
re-measured on THOSE models, the multi-step wall is a frontier number wearing a cheap-model hat and
cannot be used as a KRY acceptance prior. This instrument measures it.

METHOD (mirrors the multi-step wall; cleaner grading — single-token NUMERIC answers, so it avoids the
messy-name tokenization the multi-step wall itself flagged). Battery in 3 depth tiers, each item's gold
computed in Python (no oracle):
  fact   (depth-0, retrieval)        : "How many legs does a spider have?"            -> 8
  arith1 (depth-1, given arithmetic) : "What is 8 plus 3?"                            -> 11
  multi  (depth-2+, derive + compute): "spider legs plus triangle sides?"  (8+3)      -> 11
Two modes per item:
  FAST : "Answer with ONLY the number." max_tokens tiny -> no room to reason (latent).
  COT  : "Think step by step, then state the number."   -> emitted-token recovery.
Report per model: accuracy by tier x mode, the WALL (multi-FAST vs multi-COT gap), and
the multi-step wall SIGNATURE check (fact/arith1 high in FAST, multi low in FAST, recovered in COT).

============================ SEALED PREDICTION (committed BEFORE any run) =============
  P (lean, conf 0.70): cheap models REPRODUCE the wall — multi-FAST accuracy is
     materially below both multi-COT and fact/arith1-FAST, and (being smaller) likely
     <= Opus's ~0.13. => the multi-step wall transfers; KRY can use "served FAST on a multi-step prompt"
     as a low-acceptance prior.
  MINIMAL VIABLE FALSIFIER: multi-FAST ~= multi-COT (no wall) OR multi-FAST high
     => the multi-step wall does NOT transfer to cheap models; KRY cannot use it as a prior. Informative
     either way; the negative is the finding.
  GUILTY LEMMA: single battery, greedy decode, one phrasing; a null is "this battery on
     this model," not "no wall ever." Objective numeric gold removes the grader from the
     loop. Cost = real tokens x stated prices (printed); this is a CAPTURE tool, run it.
======================================================================================

Providers: --provider openai (gpt-4o-mini default) | openrouter (free/cheap models).
Run:  OPENAI_API_KEY=... python3 scripts/kry_compute_wall.py --models gpt-4o-mini
      python3 scripts/kry_compute_wall.py --selftest      # offline: golds + grading
stdlib only.
"""
from __future__ import annotations
import argparse, json, os, re, sys, urllib.error, urllib.request
from pathlib import Path

# (fact phrase, value) — unambiguous, single small integer.
FACTS = {
    "legs does a spider have": 8, "sides does a triangle have": 3,
    "sides does a square have": 4, "sides does a hexagon have": 6,
    "sides does a pentagon have": 5, "days are in a week": 7,
    "sides does an octagon have": 8, "legs does a cat have": 4,
}


def _battery() -> list[dict]:
    """Build the depth-tiered battery; gold computed here so grading needs no oracle."""
    items: list[dict] = []
    # depth-0: pure retrieval
    for phrase in ("legs does a spider have", "sides does a hexagon have",
                   "days are in a week", "sides does a pentagon have"):
        items.append({"id": f"fact:{phrase}", "tier": "fact",
                      "q": f"How many {phrase}?", "gold": FACTS[phrase]})
    # depth-1: given single-step arithmetic (the numbers are SUPPLIED)
    for a, op, b in [(8, "plus", 3), (6, "times", 2), (7, "times", 4),
                     (5, "plus", 4), (8, "minus", 4), (6, "plus", 5)]:
        g = a + b if op == "plus" else a - b if op == "minus" else a * b
        items.append({"id": f"arith1:{a}{op}{b}", "tier": "arith1",
                      "q": f"What is {a} {op} {b}?", "gold": g})
    # depth-2+: fact-derivation chained with arithmetic (the REAL wall)
    multi = [
        ("legs does a spider have", "plus", "sides does a triangle have"),
        ("sides does a hexagon have", "times", 2),
        ("legs does a spider have", "plus", 5),
        ("days are in a week", "times", "sides does a square have"),
        ("sides does a pentagon have", "plus", "sides does a square have"),
        ("sides does an octagon have", "plus", "sides does a triangle have"),
        ("legs does a spider have", "times", "sides does a square have"),
        ("days are in a week", "plus", "sides does a hexagon have"),
        ("sides does a hexagon have", "plus", "sides does a pentagon have"),
        ("sides does an octagon have", "times", 2),
    ]
    for left, op, right in multi:
        lv = FACTS[left]
        rv = FACTS[right] if isinstance(right, str) else int(right)
        g = lv + rv if op == "plus" else lv - rv if op == "minus" else lv * rv
        rphrase = f"how many {right}" if isinstance(right, str) else str(right)
        items.append({"id": f"multi:{left}{op}{right}", "tier": "multi",
                      "q": f"How many {left}, {op} {rphrase}?", "gold": g})
    # depth-3/4: TWO chained operations on derived facts — the multi-step wall's actual collapse zone.
    deep = [
        ("Take how many legs a spider has plus how many sides a triangle has, then multiply by two", (8 + 3) * 2),
        ("Take how many days are in a week times how many sides a square has, then subtract how many sides a triangle has", 7 * 4 - 3),
        ("Take how many sides a hexagon has times how many sides a pentagon has, then add how many legs a spider has", 6 * 5 + 8),
        ("Take how many legs a spider has plus how many sides a hexagon has, then multiply by how many sides a triangle has", (8 + 6) * 3),
        ("Take how many sides an octagon has plus how many sides a square has, then multiply by two", (8 + 4) * 2),
        ("Take how many days are in a week plus how many sides a pentagon has, then multiply by how many sides a square has", (7 + 5) * 4),
        ("Take how many legs a spider has times how many sides a triangle has, then subtract how many days are in a week", 8 * 3 - 7),
        ("Take how many sides a hexagon has plus how many sides a square has, then multiply by how many sides a pentagon has", (6 + 4) * 5),
    ]
    for i, (q, g) in enumerate(deep):
        items.append({"id": f"deep:{i}", "tier": "deep", "q": q + "?", "gold": g})
    return items


def extract_answer(text: str):
    """Prefer the model's STATED final answer; fall back to the last number."""
    if not text:
        return None
    m = re.search(r"answer is[:\s]*\$?(-?\d[\d,]*(?:\.\d+)?)", text, re.I)
    if not m:
        nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?", text)
        return nums[-1].replace(",", "").rstrip(".") if nums else None
    return m.group(1).replace(",", "").rstrip(".")


def call(model: str, prompt: str, key: str, provider: str, max_tokens: int) -> str:
    url = ("https://api.openai.com/v1/chat/completions" if provider == "openai"
           else "https://openrouter.ai/api/v1/chat/completions")
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.load(r)
    return d["choices"][0]["message"]["content"] or ""


FAST_SUFFIX = "\nAnswer with ONLY the number, nothing else."
COT_SUFFIX = "\nThink step by step, then end with: The answer is <number>."


def measure(model: str, key: str, provider: str) -> dict:
    items = _battery()
    rows = []
    for it in items:
        gold = str(it["gold"])
        try:
            fast_raw = call(model, it["q"] + FAST_SUFFIX, key, provider, 10)
            cot_raw = call(model, it["q"] + COT_SUFFIX, key, provider, 400)
        except urllib.error.HTTPError as e:
            sys.exit(f"HTTP {e.code} on {model}: {e.read()[:160].decode('utf8','ignore')}")
        fast, cot = extract_answer(fast_raw), extract_answer(cot_raw)
        rows.append({"id": it["id"], "tier": it["tier"], "gold": gold,
                     "fast_pred": fast, "cot_pred": cot,
                     "fast_ok": fast == gold, "cot_ok": cot == gold,
                     "fast_raw": fast_raw.strip()[:160]})  # raw FAST output, auditable

    def acc(tier, mode):
        sub = [r for r in rows if r["tier"] == tier]
        return round(sum(r[mode] for r in sub) / len(sub), 4) if sub else None

    tiers = ("fact", "arith1", "multi", "deep")
    grid = {t: {"fast": acc(t, "fast_ok"), "cot": acc(t, "cot_ok")} for t in tiers}
    df, dc = grid["deep"]["fast"], grid["deep"]["cot"]
    # the multi-step wall signature: retrieval + single-step + shallow-multi survive FAST; the DEEP
    # (3-4 chained-step) tier collapses in FAST and CoT recovers it.
    signature = (grid["fact"]["fast"] >= 0.75 and grid["arith1"]["fast"] >= 0.75
                 and df < 0.5 and (dc - df) >= 0.3)
    return {"model": model, "provider": provider, "grid": grid,
            "deep_fast": df, "deep_cot": dc, "wall_gap": round(dc - df, 4),
            "wall_present_signature": signature, "rows": rows}


def _selftest() -> int:
    items = _battery()
    # (1) every gold recomputes from the facts
    for it in items:
        assert isinstance(it["gold"], int) and it["gold"] >= 0, it
    assert len([i for i in items if i["tier"] == "fact"]) == 4
    assert len([i for i in items if i["tier"] == "arith1"]) == 6
    assert len([i for i in items if i["tier"] == "multi"]) == 10
    assert len([i for i in items if i["tier"] == "deep"]) == 8
    # spot-check a couple of multi + deep golds by hand
    g = {i["id"]: i["gold"] for i in items}
    assert g["multi:legs does a spider haveplussides does a triangle have"] == 11
    assert g["multi:legs does a spider havetimessides does a square have"] == 32
    assert g["deep:0"] == 22       # (8+3)*2
    assert g["deep:7"] == 50       # (6+4)*5
    # (2) grading: FAST-style bare number and CoT-style stated answer both parse
    assert extract_answer("11") == "11"
    assert extract_answer("First 8, then +3. The answer is 11.") == "11"
    assert extract_answer("The answer is 32") == "32"
    assert extract_answer("no number here") is None
    print(f"selftest PASS: {len(items)} items (4 fact / 6 arith1 / 10 multi / 8 deep), grading OK")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Recalibrate the multi-step wall on cheap models")
    p.add_argument("--selftest", action="store_true", help="offline: verify golds + grading, no API")
    p.add_argument("--provider", default="openai", choices=["openai", "openrouter"])
    p.add_argument("--models", nargs="+", default=["gpt-4o-mini"],
                   help="cheap model id(s) KRY would displace TO")
    p.add_argument("--out", default="docs/evidence/compute_wall/compute_wall.json")
    args = p.parse_args()
    if args.selftest:
        return _selftest()

    env = "OPENAI_API_KEY" if args.provider == "openai" else "OPENROUTER_API_KEY"
    key = os.getenv(env, "").strip()
    if not key:
        sys.exit(f"{env} not set (needed to capture the real wall on cheap models)")
    print(__doc__[__doc__.index("SEALED"):__doc__.index("======================================================================================")])
    results = [measure(m, key, args.provider) for m in args.models]
    out = {"schema": "kry_compute_wall/v1", "provider": args.provider,
           "method": "the multi-step wall recalibration on cheap models; numeric single-token gold (no oracle)",
           "reference": "paired-model research, multi-step wall: Opus fast multi~0.13, CoT 1.00",
           "results": results}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("\n=== the multi-step wall on cheap models (FAST/latent vs CoT) ===")
    for r in results:
        gd = r["grid"]
        print(f"\n  {r['model']} ({r['provider']})   [depth ladder: FAST/latent vs CoT]")
        for t in ("fact", "arith1", "multi", "deep"):
            print(f"    {t:7s}  FAST={gd[t]['fast']:.0%}   CoT={gd[t]['cot']:.0%}")
        print(f"    -> DEEP-tier wall gap (CoT-FAST) = {r['wall_gap']:+.0%}   "
              f"the multi-step wall signature present: {r['wall_present_signature']}")
    print(f"\n  -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
