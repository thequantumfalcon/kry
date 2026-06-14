# A no-gold routing gate that works — self-consistency on real GSM8K (2026-06-13)

## Why this matters

Every prior lab result measured the *opportunity* (there is a cheap-adequate slice) using a gate that
reads the **gold answer** — which cannot exist at inference. The disclosed open problem (KRY's `KRY_CORRECTNESS_LAYER` flag) is an **observable**
gate: decide "is the cheap model adequate here?" *without* the answer. KRY discloses its current
acceptance gate at **~0% correctness specificity** — i.e. it carries essentially no signal. This is
the first lab experiment that attacks that blocker rather than re-measuring the prize.

Signal tested: the cheap model's **self-consistency**. Sample it K times at temperature; the fraction
of samples agreeing on an answer is a confidence estimate that uses no gold. Tool:
`lab/ollama_selfconsistency_gate.py`. Corpus: 80 real GSM8K problems (same seeded sample as the
oracle). cheap = `qwen2.5:1.5b`, K = 5 samples @ temp 0.8. The capable column (`qwen3:14b`) is reused
from the deterministic oracle run for the escalation arm. No gold enters the routing decision.

## Result 1 — agreement predicts correctness, near-monotonically

```
agreement 5/5 : 29 problems, cheap correct 29/29 = 100% (Wilson 95% CI [0.88, 1.00])
agreement 4/5 : 20 problems, cheap correct 17/20 = 85%
agreement 3/5 : 15 problems, cheap correct 8/15 = 53%
agreement <=2/5: 16 problems, cheap correct 1/16 = 6%
```

When the 1.5B agrees with itself unanimously it is right 100% of the time on this sample; when it
cannot agree it is right 6%. That is a strong, well-ordered calibration from a signal available at
inference — the opposite of the ~0% disclosed gate.

## Result 2 — as a routing gate it reaches all-capable accuracy

Route cheap when agreement >= threshold, else escalate to capable. Baselines on this sample:
all-cheap (self-consistency majority vote) = 69%, all-capable = 76%.

```
threshold route-cheap route-cheap precision system accuracy gate cost (cap-equiv/problem)
 1.0 29/80 100% 80% 1.19
 0.8 49/80 94% 80% 0.94
 0.6 64/80 84% 76% 0.76
```

At threshold 0.8 the gate routes 61% of traffic to the 1.5B at **94% route-cheap precision** and
**80% system accuracy — above all-capable's 76%** (routing captures cheap's wins *and* capable's
rescues). The disclosed gate specificity was ~0%; this one is 94–100% on the routed set.

## Result 3 — the cost weakness resolves at K=2–3 samples

Re-running while saving all 5 samples and routing on *unanimity over the first K′* gives the full
cost/signal curve (cost = K′ cheap calls + escalations, in capable-equivalents at the ~9× size ratio;
all-capable = 1.00) — `gsm8k_proof/selfconsistency_gate_Ksweep_2026_06_13.txt`:

```
K(samples) route-cheap route-cheap precision system accuracy cost (cap-equiv/problem)
 2 45/80 (56%) 91% 76% 0.66
 3 39/80 95% 79% 0.85
 4 35/80 97% 79% 1.01
 5 29/80 100% 80% 1.19
```

**K=2** matches all-capable accuracy (76%) at **0.66 cost — 34% cheaper**. **K=3** *beats* all-capable
(79% vs 76%) at **0.85 — 15% cheaper**. So the gate does not need 5 samples: two or three cheap
self-consistency samples give a no-gold gate that equals or beats the capable model's accuracy at
meaningfully lower compute. Fewer samples → looser unanimity → more traffic routed cheap but lower
precision; the K=2–3 region is the operating point.

## Honest limits (this is a prototype, not a wired fix or a grade change)

- **Cost is a parameter-ratio proxy, not real pricing.** The 9× ratio (1.5B vs 14B) stands in for
 cost; real API $/token would change the exact numbers, though the qualitative win (a few cheap calls
 ≪ one capable call) holds whenever the capable model is much dearer. At K=5 the gate costs *more*
 than all-capable (1.19) — the win is specifically the K=2–3 region above.
- **Self-consistency also boosts cheap.** Majority-vote over 5 samples lifts the 1.5B from 55%
 (single temp-0 answer, prior oracle) to 69% here. Part of the system gain is voting, not routing.
- **Scale.** N=80, one corpus, one model pair; the agreement buckets are 16–29 problems each. The
 curve is clean but should be replicated at larger N and on non-math corpora before any claim of
 generality. The 100% at 5/5 is `[0.88, 1.00]` with 95% confidence, not a literal guarantee.
- **Not wired in.** This is a standalone lab prototype of a *candidate* signal for the gate the
 0%-specificity disclosure names. It is not integrated into `kry_settlement` / the host system correctness
 layer, and it does not change the readiness label.
- **Internal hardware.** Still a cross-check on the operator's own GPU, not the external arm's-length
 corpus + counterparty that A+/`production_ready` requires.

## Bottom line

The disclosed gate problem ("can an observable signal identify the cheap-adequate slice?") has a
working candidate answer on real data: cheap-model self-consistency is strongly predictive
(100/85/53/6% by agreement level), and at just K=2–3 samples it routes at 91–95% precision and
**matches/beats the capable model's accuracy at 15–34% lower compute**. That is real progress on the
actual blocker — distinct from, and not a substitute for, the external counterparty that remains the
only thing that can move the grade.

## Reproduce

```
python lab/ollama_selfconsistency_gate.py qwen2.5:1.5b capable_correct.json 80 5
```
