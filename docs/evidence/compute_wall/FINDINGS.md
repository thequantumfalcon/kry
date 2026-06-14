# the multi-step latent-computation wall — recalibrated on KRY's cheap leg

**Date:** 2026-06-10 · **Instrument:** [`scripts/kry_compute_wall.py`](../../../scripts/kry_compute_wall.py)
· **Model:** `gpt-4o-mini` (OpenAI) — a real KRY cheap-displacement target · **Grading:**
objective numeric gold computed in Python (no oracle, no LLM judge).

## Why this run exists

The host system arc (the multi-step wall, `paired-model research`) found a sharp, measured failure class: in the
FAST/no-CoT pathway a model does retrieval and **single-step** arithmetic fine but cannot
chain **multiple** computation steps latently (Opus 4.8: multi ≈ 0.13, CoT 1.00). That is
the exact request-class where a cheap model is **confidently wrong yet fluent** — the
displacement KRY must not mint. But the multi-step wall's number is a *frontier* model's fast pathway.
KRY displaces to *cheap* models. Until the wall is re-measured on those, the multi-step wall is a frontier
number wearing a cheap-model hat. This grounds it.

## Sealed prediction (committed before the run) — CONFIRMED

> Cheap models reproduce the wall: multi-step FAST collapses below CoT and below the
> shallow tiers. Falsifier: FAST ≈ CoT (no wall) → the multi-step wall doesn't transfer.

## Result — a depth cliff, sharper than the frontier

| tier | depth | FAST (latent) | CoT |
|---|---|---|---|
| fact | retrieval | 100% | 100% |
| arith1 | 1 given op | 100% | 100% |
| multi | 2 (1 derived op) | 100% | 100% |
| **deep** | **3–4 chained** | **0%** | **100%** |

The wall is a **cliff between depth-2 (100% latent) and depth-3/4 (0% latent)**, fully
recovered by CoT. gpt-4o-mini's latent multi-step pathway is *more* brittle than Opus's
~0.13. The controls are dispositive: CoT=100% proves the items are solvable (so 0% is a
genuine latent-pathway failure, not unsolvable items), and shallow tiers at 100% FAST
prove it is not a parsing/format artifact.

## The failures are confident wrong numbers (receipts in `compute_wall.json`)

Not refusals, not truncation — a plausible wrong number every time:

| gold | FAST pred | gold | FAST pred |
|---|---|---|---|
| 22 = (8+3)×2 | 20 | 24 = (8+4)×2 | 32 |
| 25 = 7×4−3 | 20 | 48 = (7+5)×4 | 60 |
| 38 = 6×5+8 | 78 | 17 = 8×3−7 | 19 |
| 42 = (8+6)×3 | 144 | 50 = (6+4)×5 | 70 |

Every CoT answer was correct.

## What it means for KRY (grounded, not borrowed)

A cheap displacement that (a) requires ≥3-step latent computation **and** (b) is served
without CoT is **~0% reliable and fails fluently** — exactly the case a surface-signal
`adequacy_gate` would wrongly KEEP. This is a real, evidence-based acceptance prior on
KRY's *actual* cheap leg: **don't mint such displacements — force CoT, or route to a
frontier model.** It sharpens the multi-step wall→KRY mapping the way the gate needs: the danger isn't
"any multi-step prompt" (depth-2 is fine at 100%), it's specifically **deep (≥3-step)
latent computation**.

## Honest bounds (guilty lemma)

One battery, one phrasing, greedy decode, one cheap model. The number is "this battery on
this model," not "no cheap model ever" — recalibrate per model before relying on it. This
is a population prior on a request-**class**, not a per-event correctness witness; the
latter stays structurally out of scope (`per_event_counterfactual_proof = not_guaranteed`).
What it removes is the excuse to treat the multi-step wall as portable: on the model KRY actually uses, the
wall is real, sharp, and located.
