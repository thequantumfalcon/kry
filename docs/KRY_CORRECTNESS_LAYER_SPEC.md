# KRY Correctness Layer — build spec

> **What the acceptance-gate measurement proved:** the `adequacy_gate` has **0% correctness
> specificity** — it keeps fluent-but-wrong cheap output because it reads *form*, not *correctness*
> (see [KRY_ADEQUACY_GATE_SPEC.md](KRY_ADEQUACY_GATE_SPEC.md)). This spec is the fix the measurement
> pointed at: a **correctness layer on the high-risk class only**, cost-gated. It makes "accepted
> savings" correctness-anchored, unlocks routing toward the ~22% ceiling, and addresses what the
> market research showed is an **industry-wide** open problem.

## BUILT, MEASURED & WIRED (host system, 2026-06-10) — default-off, operator's call to enable

The host built it with its real multi-step-wall classifier as the prompt-only prior (not the
reference heuristic), wired into its cheap-first routing path, **default-OFF** behind a
correctness-layer flag (mirroring a comparable dormant routing flag). **45/45 tests** — 35 existing
pass flag-off (strict no-op, live behavior unchanged) + 3 flag-on.

**The measured production trade — and the reference's prediction held:**

| | correctness specificity | true-accept | escalation rate |
|---|---|---|---|
| reference (idealized) | 100% | 100% | 29% |
| **production (real)** | **100%** (CI 72–100%, 10/10 escalated) | **62%** | **45%** |

The reference looked clean only because its adequate examples were all non-wall; **production
over-escalates correct-multi-step** (the prompt-only classifier escalates the wall class regardless
of whether the cheap output was right). That is **exactly the precision/recall trade the honest
bounds below predicted** — now measured, with the real cost on the table.

**Honest gate — NOT enabled.** The 45% escalation is on a small frozen set, so flipping the flag is
a **validate-then-enable** step (enable blind → could over-escalate and raise cost). Two
cost-reducers before/at enable: (1) **force-CoT-first** — re-run the cheap tier with CoT before
frontier (CoT recovers the wall class 0%→100% per `compute_wall`, far cheaper than
frontier-verify); (2) **tighten the classifier's precision** to cut the 45% over-escalation without
losing specificity. The `len<2` quirk is also fixed.

**Status: a measured, wired, tested correctness anchor — accepted-savings *can* be
correctness-gated-on-the-risky-class. Enabling is the operator's validate-then-flip call.**

## The idea (measured, not asserted)

Cheap models fail **only on a specific class** — multi-step latent computation (the compute wall),
where `compute_wall` measured gpt-4o-mini at **0% FAST** but **100% with CoT**. So the gate doesn't
need per-event correctness (impossible on a black box); it needs to **distrust the cheap FAST output
on that class and escalate** — force-CoT (which recovers it) or frontier-verify — **cost-gated**
(only the high-risk slice pays).

## Reference demonstration (committed, data-backed)

[`scripts/kry_correctness_layer.py`](../scripts/kry_correctness_layer.py) on the frozen labeled set,
with a **prompt-only** risk classifier (counts chained computation steps — never sees the answer):

| gate | correctness specificity | true-accept | escalation cost |
|---|---|---|---|
| surface (`adequacy_gate` today) | **0%** | 100% | 0% |
| **+ correctness layer** | **100%** | **100%** | **29%** (the high-risk slice) |

Escalating the high-risk class recovers specificity **0%→100%** with **no over-escalation** of the
adequate set, and force-CoT *recovers* the escalated answers (so the saving is preserved, not lost).
**Caveat: this 0%→100% is on the labeled seed that *defined* the high-risk class — it is a result
about that seed, not a generalization; production precision/recall on unseen traffic is unmeasured
(that is the host's generalizing wall-classifier's job, below).** Self-test confirms the classifier
flags 8/8 inadequate, 0 false positives — prompt-only.

## Production hand-off (touches the host's routing path, needs sign-off)

1. **Risk classifier** = the host's real, generalizing multi-step-wall detector, not the reference
   heuristic. Reads the prompt only.
2. **Escalation** = force-CoT (cheapest; recovers cheap-model accuracy) or frontier-verify, applied
   **only** when the classifier flags high-risk. Cost-gated: escalate iff `P(wrong-mint) × mint_value
   > escalation_cost`.
3. **Layer it onto the gate** — do NOT replace the usability axis (the gate's 100% usability
   specificity is real and wanted); add the correctness escalation in front of the KEEP decision.
4. **Re-run the harness** ([`scripts/kry_gate_specificity.py`](../scripts/kry_gate_specificity.py))
   against the layered gate → report the new measured specificity. Target: high specificity with a
   bounded escalation rate.
5. **Fix the quirk while you're in there**: `len(text) < 2` over-escalates correct single-digit
   answers (65% true-accept) — real wasted spend, separate from this layer.

## Honest bounds

- The reference is **clean because the wall class perfectly predicts cheap-FAST wrongness on this
  seed**; production specificity *and* cost are set by the real classifier's **precision/recall** —
  a loose classifier over-escalates (cost ↑), a tight one misses (specificity ↓). The harness
  measures both, so it's a tunable, *measured* trade — not a hope.
- It's **population** specificity, not a per-event correctness witness (`per_event_counterfactual_proof
  = not_guaranteed`). The layer moves "accepted" from *form-checked* to *correctness-gated-on-the-
  risky-class* — the strongest honest claim, and enough to anchor accepted-savings.

## Addendum — self-consistency as the precision layer (proposed 2026-06-14, NOT built)

**Targets the one measured weakness above.** The production layer escalates the whole wall class
*prompt-only*, so it over-escalates correct-multi-step → **45% escalation**. Cost-reducer #2 above
asks to "tighten the classifier's precision." This is a concrete, lab-measured candidate for exactly
that.

**The signal — cheap-model self-consistency (the output axis the prompt-only classifier lacks).**
Sample the cheap tier K times at temperature; the fraction agreeing on an answer is a confidence
estimate that uses **no gold and no frontier**. On real GSM8K (N=80, qwen2.5:1.5b vs qwen3:14b —
[`evidence/NO_GOLD_SELFCONSISTENCY_GATE_2026_06_13.md`](evidence/NO_GOLD_SELFCONSISTENCY_GATE_2026_06_13.md))
agreement predicts cheap correctness near-monotonically — 5/5→100%, 4/5→85%, 3/5→53%, ≤2/5→6% — and
as a gate at **K=2–3 samples** it routes the confident slice cheap at **91–95% precision** while
matching/beating all-capable accuracy at **15–34% lower compute**. It is the missing axis: the
prompt-only classifier cannot tell "hard **and** cheap-wrong" from "hard **but**
cheap-confident-and-right"; self-consistency can.

**Integration — a two-stage gate, layered, not a replacement:**

1. **The prompt-only wall classifier stays as the free prefilter** — flags the high-risk class; the
   easy majority never pays for sampling.
2. **Self-consistency only on the flagged class** (K=2–3): route cheap when the samples agree,
   escalate (force-CoT, then frontier) only when they don't. This is what cuts the 45% — the
   confident-right-but-hard cases stop being escalated.
3. **Synergy with cost-reducer #1 (force-CoT-first):** sample the *CoT* answers, not the FAST
   answers. force-CoT already recovers the wall class (0%→100% per `compute_wall`), so sampling its
   agreement both routes and recovers in one step.

**The honesty line that must not blur — this is a ROUTING signal, NOT a correctness witness.**
Self-consistency decides *whether to escalate*, never *that the cheap output is correct*. Correctness
still comes from the deterministic gate or the escalated frontier — nothing is minted "correct"
because the cheap model agreed with itself. So it **does not** breach the savings firewall's
`no semantic-agreement-as-correctness` rule: that rule forbids treating cheap≈frontier agreement as
proof of correctness; this is the cheap model's *self*-agreement, used only to gate routing.
`per_event_counterfactual_proof = not_guaranteed` is unchanged — still population-level.

**Honest costs, and what must be measured before enabling:**

- **Sampling cost.** Adds K−1 extra cheap runs on the flagged slice. The cost-gate
  (`escalate iff P(wrong-mint)×value > escalation_cost`) must now also carry the **sampling** cost;
  the layer wins only where (sampling cost) < (spend saved by cutting over-escalation). K=2–3 keeps
  it small; the prefilter keeps it off the easy majority.
- **Benchmark, not production.** The 100/85/53/6 calibration is N=80 GSM8K, one model pair;
  real-traffic precision/recall is **unmeasured** — the same caveat the prompt-only classifier
  carries. Re-run the harness against the layered gate for the real number.
- **Verifiability burden.** KRY sells *verifiable* savings; a temperature-sampled decision is
  non-deterministic. Seed the samples and **log the K answers + the agreement into the receipt**, so
  a stranger-verifier can confirm the route was justified. Without that the gate is unauditable.

**Sequencing & status.** NOT built, NOT wired — a proposed precision layer, default-off by the same
posture as the correctness-layer flag. Validate-then-enable, and the cleanest validation set is a
**real pilot's traffic** (which also turns the GSM8K calibration into a measured-on-their-distribution
number). Build it when there is real traffic to measure it against; until then it is a queued
candidate, not a claim.
