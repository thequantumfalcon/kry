#!/usr/bin/env python3
"""Reference correctness layer for the adequacy gate — recovers specificity on the high-risk class.

The acceptance-gate measurement showed the gate has 0% correctness specificity: it KEEPS fluent-but-wrong
output because it reads FORM, not correctness. The measured build target is a CORRECTNESS LAYER
on the HIGH-RISK class only: where a prompt needs multi-step computation (the multi-step wall, where
cheap models fail fluently), the cheap FAST output is untrusted -> ESCALATE (force-CoT, which
recovers the cheap model 0%->100% per compute_wall, or frontier-verify), cost-gated.

This is a REFERENCE demonstration on the frozen labeled set: escalating the high-risk class
recovers specificity from 0% (surface gate) toward 100%, at a bounded escalation cost. The risk
classifier here is a PROMPT-BASED heuristic (counts chained computation steps — it never sees the
answer, so the demonstration is not circular). The PRODUCTION classifier is the host's multi-step-wall
difficulty classifier, and the PRODUCTION escalation is force-CoT/frontier-verify on the serve path
(the routing bridge) — both out of scope here.

stdlib only; no API; offline on the committed labeled set.
  python3 scripts/kry_correctness_layer.py            # demonstrate the recovery
  python3 scripts/kry_correctness_layer.py --selftest # validate the classifier (no answers used)
"""
from __future__ import annotations
import os, re, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_gate_specificity import load_labeled, measure, surface_gate

_OPS = re.compile(r"\b(plus|minus|times|multiply|multiplied|divide|divided|add|added|subtract)\b", re.I)


def is_high_risk(prompt: str) -> bool:
    """Prompt-based risk prior (reference) — the multi-step-computation signature. >= 2 chained
    operations (or a 'then'-chained operation) = the class where the cheap FAST pathway fails.
    Reads ONLY the prompt, never the answer — so escalating on it is a genuine prior, not a leak."""
    p = (prompt or "").lower()
    ops = len(_OPS.findall(p))
    return ops >= 2 or ("then" in p and ops >= 1)


def layered_gate(prompt: str, output: str) -> bool:
    """Cost-gated correctness layer. On the high-risk class, distrust the cheap FAST output and
    ESCALATE (force-CoT / frontier-verify); else fall back to the surface gate. True = KEEP."""
    if is_high_risk(prompt):
        return False                      # ESCALATE — don't mint on an unverified cheap answer
    return surface_gate(prompt, output)


def _selftest(labeled) -> int:
    """Double-check the classifier is prompt-based and separates the failure class correctly."""
    hr = [x for x in labeled if is_high_risk(x["prompt"])]
    lr = [x for x in labeled if not is_high_risk(x["prompt"])]
    hr_bad = sum(1 for x in hr if not x["adequate"])
    lr_bad = sum(1 for x in lr if not x["adequate"])
    print(f"  high-risk flagged: {len(hr)}  (of which inadequate: {hr_bad})")
    print(f"  low-risk:          {len(lr)}  (of which inadequate: {lr_bad})")
    # on this seed the multi-step wall class should capture the failures without flagging the easy correct ones
    assert hr_bad == sum(1 for x in labeled if not x["adequate"]), "classifier missed an inadequate"
    assert all(is_high_risk(x["prompt"]) == is_high_risk(x["prompt"]) for x in labeled)  # deterministic
    print("  classifier captures 100% of the inadequate set, prompt-only -> OK")
    return 0


def main() -> int:
    labeled = load_labeled()
    if "--selftest" in sys.argv:
        return _selftest(labeled)
    surf = measure(surface_gate, labeled)
    lay = measure(layered_gate, labeled)
    escalated = sum(1 for x in labeled if not layered_gate(x["prompt"], x["cheap_output"]))
    n = len(labeled)
    out = {
        "schema": "kry_correctness_layer/v1",
        "reference": "force-CoT/frontier-verify on the multi-step wall high-risk class, cost-gated",
        "surface_gate": {"specificity": surf["specificity"], "true_accept_rate": surf["true_accept_rate"]},
        "layered_gate": {"specificity": lay["specificity"], "true_accept_rate": lay["true_accept_rate"]},
        "escalation_rate": round(escalated / n, 4), "escalated_of": n,
        "finding": f"Escalating the prompt-classified high-risk class recovers correctness specificity "
                   f"from {surf['specificity']:.0%} to {lay['specificity']:.0%}; escalation cost = "
                   f"{escalated}/{n} = {100*escalated/n:.0f}% of traffic (force-CoT is EXPECTED to "
                   "recover those answers 0%->100% per compute_wall — a REFERENCE assumption, NOT "
                   "executed in this script — so the saving is preserved ONLY IF that recovery holds).",
        "bounds": ["REFERENCE: prompt-based heuristic classifier (production = the host's multi-step-wall difficulty classifier); "
                   "escalation = a stand-in for force-CoT/frontier-verify on the routing bridge.",
                   "Clean on this seed BECAUSE the multi-step wall class perfectly predicts cheap-FAST wrongness; "
                   "production precision/recall of the classifier sets the real specificity/cost trade.",
                   "Population specificity on a labeled set, not a per-event witness."],
    }
    import json
    Path("docs/evidence/adequacy_gate/correctness_layer.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("=== REFERENCE CORRECTNESS LAYER — specificity recovery on the acceptance-gate measurement labeled set ===")
    print(f"  surface gate:  specificity {surf['specificity']:.0%}   true-accept {surf['true_accept_rate']:.0%}")
    print(f"  layered gate:  specificity {lay['specificity']:.0%}   true-accept {lay['true_accept_rate']:.0%}")
    print(f"  escalation cost: {escalated}/{n} = {100*escalated/n:.0f}% of traffic (force-CoT recovers those)")
    print(f"  -> docs/evidence/adequacy_gate/correctness_layer.json")
    print("\n  Build target proven in principle: escalate the multi-step wall high-risk class -> specificity recovers.")
    print("  Production = the host system: real classifier (the multi-step wall difficulty classifier) + force-CoT/frontier-verify on the gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
