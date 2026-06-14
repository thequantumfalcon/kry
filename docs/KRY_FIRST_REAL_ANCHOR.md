# KRY — First Real External Anchor (OpenRouter free-test reconciliation)

**Status: real, reproducible, and deliberately *not* overclaimed.** This is the first time
KRY's external-anchor machinery met real provider traffic and passed. It is recorded here
with its exact scope so the result is never inflated into something it is not.

## What happened

Between **2026-06-07 and 2026-06-10**, the host system floor served **18 real displacement
calls** on OpenRouter's free model `openai/gpt-oss-120b:free` (avoiding `gh/gpt-5.5`,
`google/gemini-3.1-pro-preview`, `google/gemini-3.5-flash`). Each call was recorded with a
real `/openrouter:<gen-id>` handle and the host's `(prompt, completion)` token counts —
29,427 metered tokens in total.

On **2026-06-09** those 18 receipts were reconciled against **OpenRouter's own per-request
generation records** (`GET /api/v1/generation?id=…`, the provider's authoritative native
token counts, fetched independently):

```
T1 (provider_metered) receipts: 18
provider records matched: 18/18
independent agreement: 1.00 (bar 0.80)
VERDICT: RECONCILED — every T1 claim is anchored to a provider record.
```

## Reproduce it (offline — no key, no network)

The evidence bundle is committed under [`docs/evidence/freetest/`](evidence/freetest/):
`freetest_mintlog.jsonl` (the 18 `provider_metered` receipts — gen-ids + token counts only,
**no prompt/response content**) and `or_provider_export.json` (OpenRouter's own records).
Anyone can re-derive the result with no secrets:

```bash
python3 scripts/kry_reconcile.py docs/evidence/freetest/freetest_mintlog.jsonl \
 --provider-export docs/evidence/freetest/or_provider_export.json --tolerance 2
python3 scripts/kry_research_grade.py docs/evidence/freetest/freetest_mintlog.jsonl \
 --provider-export docs/evidence/freetest/or_provider_export.json --tolerance 2
```

To re-fetch live from OpenRouter (needs the account's `OPENROUTER_API_KEY`; records may
age out): `kry_or_fetch.py <mintlog> --out or.json`, then reconcile.

## What this proves — and what it does NOT

**Proves (veracity axis):** on real calls, the operator's recorded `provider_metered` token
counts **exactly match OpenRouter's own independently-fetched records**. An operator cannot
inflate or fabricate token counts on these receipts without the reconciliation breaking.
This is KRY's first real external witness — the "you can't prove the savings happened"
objection, answered on real data for the call-veracity half.

**Does NOT prove (acceptance axis):** that these were *accepted* dollar-savings. All 18 were
**unconfirmed** — they expired (15) or were rejected (3) in the host system's S1 acceptance gate
(`src/kry/kry_pending.py`), because nothing recorded that the cheap output was *used*.
`confirm()` is wired only to the operator's coder consumer (`the operator's coder`), so
general displacement traffic never lands. **Call-is-real (veracity) ≠ output-was-used
(acceptance).** This bundle is the former, not the latter.

**Scale:** 18 free-model calls is a **proof-of-mechanism, not a corpus.**

## Why the readiness label is still `internally_consistent`

`kry_research_grade.py` prints `research_grade REACHED` for *this reconciliation* because
the staged receipts reconcile at 1.00 ≥ the 0.80 bar. That is true for the bundle. It does
**not** flip the repository's official `readiness_label()`, and we deliberately do not flip
it, because:

1. The repo's **committed** ledger holds no accepted, externally-anchored mints — only this
 staged bundle derived from expired pendings.
2. `research_grade` in the rubric means an independent oracle agrees with KRY's **accepted**
 T1 mints — these were never accepted.

Flipping the label on a proof-of-mechanism over expired displacements would be exactly the
overclaim this project exists to prevent. The honest statement is: **the external-anchor
mechanism is now demonstrated on real traffic; a committed, accepted, reconciled corpus is
the remaining step.**

## The clean path to a real `research_grade` (not more code — one wire)

The pieces all exist and are independently validated; they are not yet connected:

- **Veracity** — proven here (18/18, 1.00).
- **Acceptance** — the host system already has the judgment: `adequacy_gate()`
 (in the operator's routing bridge) and the anytime-valid acceptance monitor.

Wire the acceptance signal to `kry_pending.confirm()` for general displacement traffic (not
just the operator's coder) so accepted displacements **mint**, then reconcile those committed mints.
That yields an independent-agreement number over **real, accepted** savings — a clean,
non-staged `research_grade`. Until then, this bundle stands as the honest first anchor: real,
reproducible, veracity-only, proof-of-mechanism.

*See [`KRY_READINESS.md`](KRY_READINESS.md) for the rubric and the two external steps to A+,
and [`KRY_VERACITY_BINDING.md`](KRY_VERACITY_BINDING.md) for the integrity-vs-veracity model.*
