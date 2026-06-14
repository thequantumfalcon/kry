# Lab oracle — real GSM8K cheap-vs-capable adequacy + holdout-vs-truth (2026-06-13)

## What this is

The KRY routing thesis (route adequate traffic to a cheaper model, keep the cost saved) measured on a
**real corpus** — GSM8K test problems — on the lab GPU (RTX 5080), with a **deterministic
`####`-gold gate** (no LLM judge). Tool: `lab/ollama_gsm8k_oracle.py`. Both models run with
chain-of-thought; the answer is the GSM8K `#### <number>` (fallback: last integer); temp 0, model
seed 42; N = 80 problems from a seeded shuffle of the GSM8K test set.

This supersedes the earlier trivial-arithmetic probe, which was non-discriminating (cheap == capable
== 90% on grade-school sums proves only that both models can add).

## Result 1 — there is no gap between a 7B and a 14B on GSM8K (so that pair can't demonstrate routing)

`cheap=qwen2.5:7b` vs `capable=qwen3:14b`, N=80
(`gsm8k_proof/lab_oracle_7b_vs_14b_2026_06_13.txt`):

```
cheap adequacy: 68/80 = 85% capable adequacy: 67/80 = 84% -> tied (CIs overlap)
```

Honest caveat that killed this as a headline: re-running a single `capable=XX` case under the same
settings made it pass, so the capable column carried a few percent of **measurement noise**
(answer-extraction + thinking-length non-determinism). With cheap and capable statistically tied and
the capable count not fully trustworthy, this pair shows only that **a 7B is already adequate on
GSM8K** — a real point for KRY (route 7B-class math traffic cheap, lose ~nothing), but it leaves no
gap for a gate to *discriminate*, which is what makes routing non-trivial. That motivated hardening
the extraction (`####` format + seed) and using a genuinely cheaper model.

## Result 2 (headline) — a genuinely cheaper model shows a large, difficulty-structured gap

`cheap=qwen2.5:1.5b` (≈9× smaller) vs `capable=qwen3:14b`, N=80, identical prompts for both
(`gsm8k_proof/lab_oracle_1p5b_vs_14b_2026_06_13.txt`):

```
cheap adequacy (1.5B): 44/80 = 55% (95% CI [0.44, 0.65])
capable adequacy (14B): 61/80 = 76% (95% CI [0.66, 0.84])
adequacy GAP: +21 pts rescue P(cheap wrong & capable right): 25/80 = 31% both wrong: 11/80 = 14%
```

The +21 pt gap is **far larger than the few-percent measurement noise**, so the conclusion (the 14B
is materially better here) is robust even though the absolute 76% is itself prompt/extraction
sensitive. Because both models saw the **same prompt and gate in the same run**, the *gap* is the
clean, comparable quantity (not the cross-run absolute scores).

Cheap-model failure **rises with problem difficulty** (gold step count) — the slice is structured,
not random:

```
bucket cheap-fail (production truth)
easy(1-2) 6/17 = 35%
med(3-4) 8/17 = 47%
hard(5+) 4/6 = 67%
```

## Holdout-vs-truth — the KRY accounting recovers reality

For each difficulty bucket, KRY estimates the "paid" rate (= cheap-inadequate rate) from a small
**holdout** sample alone, with a Wilson 95% CI, and we check whether that CI brackets the bucket's
real production rate:

```
bucket holdout est production truth brackets?
easy(1-2) 6/17 [0.17, 0.59] 6/17 = 0.35 YES
med(3-4) 9/17 [0.31, 0.74] 8/17 = 0.47 YES
hard(5+) 3/6 [0.19, 0.81] 4/6 = 0.67 YES

independent_agreement = 3/3 = 1.00 (PASS >= 0.80)
```

The holdout methodology recovers the true per-bucket rate on **real** GSM8K traffic — the lab
instance of PLAYBOOK Phase-2's `independent_agreement`, with a deterministic gate that is strictly
more rigorous than the playbook's LLM judge.

## What this establishes — and the three honest limits

- **Establishes:** on a real corpus there is a real, difficulty-structured adequacy gap between a
 cheap and a capable model (the regime where routing has value), KRY could route the cheap-adequate
 ~55% to the small model and escalate the rest, and KRY's holdout-based paid-rate accounting
 recovers the true rate (`independent_agreement` 1.00).

- **Limit 1 — the gate is an oracle here, not a deployable one.** The difficulty bucket is derived
 from the gold answer's step count, i.e. it is *not observable at inference*. So the 55% "routable"
 figure is the **opportunity ceiling a perfect gate would reach**, not what a real no-gold gate
 achieves. Building that observable gate is the open problem; the project already discloses the
 current correctness layer at **0% acceptance-gate specificity** (`KRY_CORRECTNESS_LAYER`,
 the acceptance-gate spec). This run measures the prize, not the gate.

- **Limit 2 — absolute scores are prompt/extraction sensitive.** Only the within-run *gap* and the
 *holdout-vs-truth* relationship are reported as findings; the absolute 76%/55% should not be quoted
 as model benchmarks.

- **Limit 3 — internal hardware, not an external counterparty.** This is a cross-check on the
 operator's own GPU. It strengthens the internal evidence base; it is **not** the arm's-length
 real-world corpus + counterparty that A+/`production_ready` requires. That grade-mover stays
 external.

## Reproduce

```
# on a node with Ollama + the two models:
python lab/ollama_gsm8k_oracle.py qwen2.5:1.5b qwen3:14b 80
```
