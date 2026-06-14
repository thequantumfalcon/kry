#!/usr/bin/env python3
"""Measure an acceptance GATE's specificity on a held-fixed labeled set — the acceptance-gate measurement discipline.

KRY/the host system mint "accepted savings" when an adequacy_gate KEEPS a cheap displacement. That only
MEANS something if the gate actually REJECTS fluent-but-wrong cheap output. Today the gate is
"default-KEEP on surface signals" with UNMEASURED specificity. This instrument measures a gate's
true/false accept rate on a FROZEN labeled control set with OBJECTIVE ground truth — so "passed
adequacy" carries a measured number, not the assumed one its own docstring admits.

LABELED SET: (prompt, cheap_output, adequate: bool). Ground truth is objective (numeric/test),
no judge. Seed extracted from tonight's compute_wall run (deep-tier FAST = confident-WRONG;
shallow = adequate). Expand with code_routing / GSM8K outputs for a richer measurement.

GATE: a predicate gate(prompt, output) -> True (KEEP/accept/mint) | False (ESCALATE/reject).

METRICS:
  true_accept_rate  (sensitivity)   = of ADEQUATE outputs, fraction KEPT  (high = no over-escalation)
  false_accept_rate (1-specificity) = of INADEQUATE outputs, fraction WRONGLY KEPT  <- the killer
  specificity                        = of INADEQUATE outputs, fraction correctly ESCALATED

A surface-signal gate (accept anything fluent/non-empty/non-refusing) is EXPECTED to score ~0
specificity on confident-wrong output — that negative is the acceptance-gate measurement proof-of-need. The host system plugs its
REAL adequacy_gate into measure() to get its own number.

HONEST CEILING: this is POPULATION specificity on a labeled set, NOT a per-event correctness
witness; per-event correctness on black-box output stays structurally out of scope. Market
grounding: cheap models fail fluently (arXiv 2601.00513: 50-69% of correct answers have flawed
reasoning); routers hit 23-25% on hard cases (LLMRouterBench). stdlib only; no API.
"""
from __future__ import annotations
import json, os, re, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_grounded_savings_proof import wilson

SEED = "docs/evidence/adequacy_gate/labeled_seed.jsonl"
REFUSAL = re.compile(r"\b(i can'?t|i cannot|i don'?t know|not sure|unsure|unable to|as an ai)\b", re.I)


def build_seed() -> list[dict]:
    """Freeze a labeled control set from tonight's compute_wall run (joined to its prompts)."""
    from kry_compute_wall import _battery
    cw = json.load(open("docs/evidence/compute_wall/compute_wall.json", encoding="utf-8"))
    q = {it["id"]: it["q"] for it in _battery()}
    rows = cw["results"][0]["rows"]
    out = [{"prompt": q[r["id"]], "cheap_output": r["fast_raw"], "gold": r["gold"],
            "adequate": bool(r["fast_ok"]), "source": "compute_wall:fast"}
           for r in rows if r["id"] in q]
    Path(SEED).parent.mkdir(parents=True, exist_ok=True)
    Path(SEED).write_text("\n".join(json.dumps(x) for x in out) + "\n", encoding="utf-8")
    return out


def load_labeled() -> list[dict]:
    return [json.loads(line) for line in open(SEED, encoding="utf-8")] if os.path.exists(SEED) else build_seed()


def surface_gate(prompt: str, output: str) -> bool:
    """BASELINE 'default-KEEP' gate: accept any non-empty, fluent, non-refusing answer. It can see
    FLUENCY but not WRONGNESS. True = KEEP/accept (mint); False = ESCALATE/reject."""
    o = (output or "").strip()
    if not o or REFUSAL.search(o):
        return False
    return True


def measure(gate, labeled: list[dict]) -> dict:
    good = [x for x in labeled if x["adequate"]]
    bad = [x for x in labeled if not x["adequate"]]
    kept_good = sum(1 for x in good if gate(x["prompt"], x["cheap_output"]))
    kept_bad = sum(1 for x in bad if gate(x["prompt"], x["cheap_output"]))   # FALSE ACCEPTS
    return {
        "n_adequate": len(good), "n_inadequate": len(bad),
        "true_accept_rate": round(kept_good / len(good), 4) if good else None,
        "true_accept_95ci": list(wilson(kept_good, len(good))) if good else None,
        "false_accept_rate": round(kept_bad / len(bad), 4) if bad else None,
        "false_accept_95ci": list(wilson(kept_bad, len(bad))) if bad else None,
        "specificity": round(1 - kept_bad / len(bad), 4) if bad else None,
        "wrongly_accepted": kept_bad, "of_inadequate": len(bad),
    }


def main() -> int:
    labeled = load_labeled()
    r = measure(surface_gate, labeled)
    out = {"schema": "kry_gate_specificity/v1", "gate": "baseline surface-signal (default-KEEP)",
           "labeled_set": SEED, "metrics": r,
           "finding": f"The surface gate WRONGLY ACCEPTS {r['wrongly_accepted']}/{r['of_inadequate']} "
                      f"fluent-but-wrong outputs (specificity {r['specificity']}). A gate that cannot "
                      "reject confident-wrong cheap output gives 'accepted savings' zero veracity.",
           "honest_ceiling": "POPULATION specificity on a labeled set, not a per-event witness.",
           "market_grounding": ["arXiv 2601.00513: cheap models fail fluently (50-69% of correct "
                                "answers have flawed reasoning)", "LLMRouterBench: routers 23-25% on hard"]}
    Path("docs/evidence/adequacy_gate").mkdir(parents=True, exist_ok=True)
    Path("docs/evidence/adequacy_gate/gate_specificity.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("=== GATE SPECIFICITY (the acceptance-gate measurement discipline) — baseline surface-signal gate ===")
    print(f"  labeled set: {r['n_adequate']} adequate + {r['n_inadequate']} fluent-but-wrong (frozen)")
    print(f"  true-accept (keeps adequate):  {r['true_accept_rate']:.0%}  (good: no over-escalation)")
    print(f"  FALSE-accept (keeps WRONG):     {r['false_accept_rate']:.0%}  ->  specificity {r['specificity']:.0%}")
    print(f"  => wrongly accepts {r['wrongly_accepted']}/{r['of_inadequate']} confident-wrong outputs")
    print(f"  -> docs/evidence/adequacy_gate/gate_specificity.json")
    print("\n  the acceptance-gate measurement hand-off: the host system plugs its REAL adequacy_gate into measure() -> its own specificity,")
    print("  replacing 'assumed' with 'measured'. Expand the seed set with code_routing / GSM8K outputs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
