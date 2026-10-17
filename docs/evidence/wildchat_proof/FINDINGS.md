# Real-corpus cache-displacement savings — WildChat organic traffic (gpt-4o deployment)

**Date:** 2026-06-10 · **Instrument:** [`scripts/kry_wildchat_corpus_proof.py`](../../../scripts/kry_wildchat_corpus_proof.py)
· **Corpus:** allenai/WildChat-1M (real users → ChatGPT) · **Deployment model:** gpt-4o
($10/M list price, value multiplier 0.40) · **Holdout seed:** `kry-wildchat-2026-06-10`

> **Correction (2026-10-17): this bundle no longer verifies or reproduces. It is kept as a
> historical record.** The data files in this directory are unchanged; nothing was re-minted.
>
> - **Verification.** `attestation.json` was minted on 2026-06-10 from 406 links that carry no
>   `hash_version` (read as legacy v1) yet are labelled `holdout_validated`. Commit `e1195c1`
>   (2026-06-15) made the verifiers reject a pre-v4 link carrying any tier other than
>   `self_reported`, because v1 does not bind the tier on the public surface (SPEC §3.4 step 4).
>   Run against the bundle today, `scripts/kry_verify.py`, `node verifiers/js/cli.mjs` and
>   `kry_attest.verify_attestation` all return **INVALID**: 406 tier-binding errors, a
>   `veracity_floor` re-derived as 0.0 (all 37,020.4252 KRY counted as `self_reported`) against the
>   declared 1.0, and an `attestation_hash` mismatch. The hash mismatch comes from commit `b360faa`
>   (2026-06-27), which renamed the file's `veracity.externally_anchored_kry` key to
>   `anchored_kry` without recomputing `attestation_hash`. The bundle as first committed
>   (`7e73ae5`) verified VALID under `scripts/kry_verify.py` as it stood before `e1195c1`.
> - **Figures.** `python3 scripts/kry_savings_report.py docs/evidence/wildchat_proof/usage.jsonl`
>   still reports SAVED 37,025.96 KRY ($0.9256). It now lists the 46 gpt-4o holdout calls as
>   `unpriced calls: 46` and does not count them in SPEND, so the holdout cost prints as
>   0.00 KRY and `efficiency_ratio` as 100.00%. The $0.20 holdout cost, the +$0.73 net and the 82%
>   efficiency below therefore do not reproduce under current pricing.
>
> The changed wording, old and new, is logged at the end of this file.

## What ran (real organic traffic, real receipts)

- Pulled **2,971 real prompts** → **191 genuine repeat clusters** → **467 cache-eligible repeats**.
- **421** served from cache (the saving); **46** forced through a publicly-seeded randomized
 holdout = **real gpt-4o calls with real receipts**.
- `kry_savings_report.py` → **`holdout_validated`** (p̂ 100%, Wilson floor 92%).
- mint → attestation → `kry_verify.py`: **VALID**, **veracity_floor 1.0** on 2026-06-10;
 **INVALID** under every verifier since `e1195c1` (2026-06-15) — see the correction above.

## The savings as reported on 2026-06-10 (not reproducible today — see the correction above)

| metric | value |
|---|---|
| **cache-hit rate** | **15.7%** of real traffic → ~that fraction of gpt-4o spend avoided |
| **SAVED (retained)** | **$0.93** (37,026 KRY, entirely holdout-validated) |
| holdout "price of veracity" | $0.20 → **net +$0.73** (2026-06-10; does not reproduce: the current report prices the holdout at 0.00 KRY) |
| efficiency_ratio | **82%** (saved / (saved + measurement cost)) (2026-06-10; the current report prints 100.00%) |
| stranger verification | **VALID** on 2026-06-10; **INVALID** today (legacy v1 links cannot carry `holdout_validated`) |

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

## Wording changes (2026-10-17)

Old → new, verbatim:

- "- mint → attestation → `kry_verify.py`: **VALID**, **veracity_floor 1.0**." →
  "- mint → attestation → `kry_verify.py`: **VALID**, **veracity_floor 1.0** on 2026-06-10;
  **INVALID** under every verifier since `e1195c1` (2026-06-15) — see the correction above."
- "## The savings (decent, net-positive, verified)" →
  "## The savings as reported on 2026-06-10 (not reproducible today — see the correction above)"
- "| holdout "price of veracity" | $0.20 → **net +$0.73** |" →
  "| holdout "price of veracity" | $0.20 → **net +$0.73** (2026-06-10; does not reproduce: the
  current report prices the holdout at 0.00 KRY) |"
- "| efficiency_ratio | **82%** (saved / (saved + measurement cost)) |" →
  "| efficiency_ratio | **82%** (saved / (saved + measurement cost)) (2026-06-10; the current report
  prints 100.00%) |"
- "| stranger verification | **VALID** (integrity + conservation + magnitude) |" →
  "| stranger verification | **VALID** on 2026-06-10; **INVALID** today (legacy v1 links cannot carry
  `holdout_validated`) |"
