# KRY Adequacy-Gate Specificity — the acceptance-gate measurement

> **The keystone.** KRY and its host system mint "accepted savings" when an `adequacy_gate` KEEPS a
> cheap displacement. That only means something if the gate actually REJECTS fluent-but-wrong cheap
> output. Before this measurement the gate was **default-KEEP on surface signals with UNMEASURED
> specificity**. This turns "assumed adequate" into "measured adequate" — and it is where routing's
> dollar upside, the token's grade, and the differentiator all unlock at once.

## MEASURED (2026-06-10) — the keystone is a number, not an assumption

The real `adequacy_gate` was run against frozen labeled sets (seed ported verbatim, SHA-256
identical). Result, on two axes:

- **Correctness specificity: 0%** (CI 0–28%) — it wrongly KEEPS **10/10** confident-wrong outputs,
  including **2 fluent-prose** wrong answers (552 & 987 chars of well-formed reasoning that dropped
  a step), kept *identically* to the 8 bare wrong numbers. Its keep/escalate decision tracks output
  **form** (non-empty, parses, long enough), never correctness — confirmed on both ends.
- **Usability specificity: 100%** (CI 65–100%) — it catches **7/7** of the failures it is *designed*
  to catch. So 0% means "**not a correctness gate**," not "broken."

**The falsifier resolved to the predicted negative:** minting "accepted savings" on `adequacy_gate`
alone carries **measured-near-zero correctness-veracity** — shown with data, not asserted. (A quirk
also surfaced: true-accept 65%, not 100% — `len(text) < 2` over-escalates correct single-digit
answers = real wasted spend. Flagged, not fixed — measuring was the task.)

**Build target (concrete):** a correctness layer on the **high-risk class only** — force-CoT /
frontier-verify on the multi-step-computation wall, cost-gated — then re-run this harness against
the layered gate. See [KRY_CORRECTNESS_LAYER_SPEC.md](KRY_CORRECTNESS_LAYER_SPEC.md).

## The problem (independently confirmed)

Cheap models fail **fluently** — they emit confident, well-formed, *wrong* answers. A gate that
reads fluency cannot see wrongness. This is not just our finding:

- **Ours** ([compute_wall](evidence/compute_wall/FINDINGS.md)): gpt-4o-mini, 0% on ≥3-step latent
  computation, confident wrong numbers (e.g. 22→"20", 42→"144").
- **Academic** (arXiv 2601.00513, AAAI 2026): **50–69% of cheap-model *correct* answers contain
  fundamentally flawed reasoning**, invisible to accuracy.
- **Routing** (LLMRouterBench): top routers hit only **23–25%** accuracy on hard cases.

## The method

Take a **held-fixed labeled control set** with OBJECTIVE ground truth, run the gate, measure its
accept rates. Freeze the set so the gate cannot be tuned to it post-hoc.

- **Labeled set**: `(prompt, cheap_output, adequate: bool)`, ground truth objective (numeric match,
  test-pass) — no judge. The seed (28 examples: 20 adequate, 8 fluent-but-wrong) is committed at
  [`evidence/adequacy_gate/labeled_seed.jsonl`](evidence/adequacy_gate/labeled_seed.jsonl),
  extracted from the `compute_wall` run. **Expand** with `code_routing` (138 pass / 26 fail)
  and GSM8K (131 / 19) outputs for a richer, larger measurement.
- **Gate**: a predicate `gate(prompt, output) -> KEEP | ESCALATE`.
- **Harness**: [`scripts/kry_gate_specificity.py`](../scripts/kry_gate_specificity.py) — generic.
  The host system imports its real `adequacy_gate` and calls `measure(gate, labeled)`.

## Metrics

| metric | meaning | want |
|---|---|---|
| **true-accept rate** (sensitivity) | of *adequate* outputs, fraction KEPT | high (don't over-escalate) |
| **false-accept rate** (1−specificity) | of *inadequate* outputs, fraction WRONGLY KEPT | **low — the killer** |
| **specificity** | of *inadequate* outputs, fraction correctly ESCALATED | high |

Report all three with Wilson 95% CIs.

## Demonstrated baseline (the proof-of-need)

A **surface-signal gate** (accept anything non-empty/fluent/non-refusing) on the frozen seed:

- true-accept **100%** (keeps adequate — fine)
- false-accept **100%** → **specificity 0%** — it wrongly accepts **8/8** confident-wrong outputs.

**A default-KEEP gate gives "accepted savings" zero veracity.** That is exactly the gap to close.

## Falsifier

- **PASS**: the real gate's measured specificity clears a stated bar (e.g. rejects ≥ X% of
  fluent-but-wrong) while keeping ≥ Y% of adequate → "accepted savings" carries real specificity.
- **FAIL**: the gate is surface-only → high false-accept → near-zero specificity → "accepted"
  is unanchored. **That negative is the finding** — it means the accepted-savings anchor is weaker
  than it looks. Either way, the assumption becomes a measurement.

## Honest ceiling

This measures **population specificity** on a labeled set — the strongest honest claim available.
It is **not** a per-event correctness witness; per-event correctness on black-box output is
structurally out of scope (`per_event_counterfactual_proof = not_guaranteed`). The gate moves
"accepted" from *assumed* specificity to *measured* specificity — no more, and that is enough for
an auditor to rely on a bounded number instead of an asserted one.

## Hand-off for an integrating host

1. Port [`scripts/kry_gate_specificity.py`](../scripts/kry_gate_specificity.py) + the seed set.
2. Replace `surface_gate` with the host's real `adequacy_gate`.
3. Expand the labeled set with real cheap-model outputs (code-test-pass, numeric) — the harder, the better.
4. Report the gate's specificity with a CI. If low, that is the build target for the gate itself
   (e.g. force-CoT / frontier-verify on the high-risk class).
