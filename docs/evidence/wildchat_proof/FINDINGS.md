# Real-corpus cache-displacement savings — WildChat organic traffic (gpt-4o deployment)

**Date:** 2026-06-10 · **Instrument:** [`scripts/kry_wildchat_corpus_proof.py`](../../../scripts/kry_wildchat_corpus_proof.py)
· **Corpus:** allenai/WildChat-1M (real users → ChatGPT) · **Deployment model:** gpt-4o
($10/M list price, value multiplier 0.40) · **Holdout seed:** `kry-wildchat-2026-06-10`

## What ran (real organic traffic, real receipts)

- Pulled **2,971 real prompts** → **191 genuine repeat clusters** → **467 cache-eligible repeats**.
- **421** served from cache (the saving); **46** forced through a publicly-seeded randomized
 holdout = **real gpt-4o calls with real receipts**.
- `kry_savings_report.py` → **`holdout_validated`** (p̂ 100%, Wilson floor 92%).
- mint → attestation → `kry_verify.py`: **VALID**, **veracity_floor 1.0**.

## The savings (decent, net-positive, verified)

| metric | value |
|---|---|
| **cache-hit rate** | **15.7%** of real traffic → ~that fraction of gpt-4o spend avoided |
| **SAVED (retained)** | **$0.93** (37,026 KRY, entirely holdout-validated) |
| holdout "price of veracity" | $0.20 → **net +$0.73** |
| efficiency_ratio | **82%** (saved / (saved + measurement cost)) |
| stranger verification | **VALID** (integrity + conservation + magnitude) |

**The headline that scales: a 15.7% spend reduction from caching genuine repeats.** That rate
is **model-independent** — it holds at any volume; the deployed model only sets the dollar
magnitude. The 8–10% holdout here is inflated to clear the 30-sample floor on a small run; at
production scale a 2% holdout shrinks the measurement cost ~5× and widens the net.

## On the model choice (why gpt-4o, not Opus)

gpt-4o is the honest sweet spot: a realistic frontier default at a real list price whose
holdout calls the OpenAI key can **actually make** — so the avoided cost is **measured, not
asserted**. Opus ($25/M) would inflate the figure 2.5× but (a) the key cannot call it, so the
holdout couldn't validate it, and (b) "Opus-for-everything" is a cherry-picked premium
baseline. gpt-4o + gpt-4o-mini were added to the price table at their real public list prices
(auditable; the verifier's mirror updated in lockstep; full suite 475/475 green).

## Honest bounds (do not overclaim)

1. **Replay, not deployment** — replay of a real corpus, not live production traffic.
2. **Cache-displacement only** — a cache hit serves the *identical* prior answer, so adequacy
 is inherited (no quality judge). Does **not** validate cheap-model *routing* adequacy.
3. **p̂ ≈ 1.0 by construction** — a genuine repeat served fresh *does* incur a paid call (the
 receipts confirm it); the CI floor 0.92 is the conservative credit. Population estimate,
 not a per-event witness.
4. **No PII committed** — the usage log stores only token counts, ids, and the class.

## Label — held at research_grade, NOT flipped

Per the pre-registered rubric a clean run like this would satisfy `real_corpus_validated`. The
honest call **holds the top label** until (a) it runs on **live** traffic (not a replay) and
(b) the acceptance-gate specificity (the acceptance-gate measurement) is **measured**. Real-corpus evidence toward
A+, deliberately not the flip.
