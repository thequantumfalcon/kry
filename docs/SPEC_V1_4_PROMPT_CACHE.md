# KRY-SPEC v1.4 candidate: prompt-cache profile — development sheet

**Status:** candidate, not started. Per [`SPEC_DEVELOPMENT.md`](SPEC_DEVELOPMENT.md), no spec text is
written until a real external driver exists: an outside user on API billing who wants attested
prompt-cache savings and can supply a provider usage export.
**Companion:** [`PROMPT_CACHE_PLAN.md`](PROMPT_CACHE_PLAN.md) (Stage A, report-only, needs no spec change).

## 1. What v1.3 cannot express

| Section | v1.3 rule | Why prompt caching does not fit |
|---|---|---|
| §3.2 | `metered_tokens` is `[prompt, completion]` or null | Prompt caching needs input, cache read, cache write (per TTL) and output separately |
| §3.3 | the public block binds `metered_tokens` as-is | Nothing binds the cache breakdown, so it cannot be recomputed |
| §3.4.1 | `kry_minted == tokens_saved × earn_rate × M`, with `M` from the published output-price multiplier set | The saving is an input-price discount net of a write premium, not output tokens times an output-price ratio |
| §3.4.2 | `provider_metered` requires a two-element `metered_tokens` | The breakdown has four or five elements |
| §3.6 | a verifier understands `hash_version` 4–7 and fails closed otherwise | A new binding needs a new version, which older verifiers will reject |
| Annex A | `cache_creation` earns 0.0. Annex A is only a rate table; the meaning of `cache_hit` as an avoided frontier call comes from `src/kry/kry_token.py` (line 55) | Neither describes a partial discount on input tokens |

## 2. Provider facts the profile must respect

Sealed at 2026-09-15T04:21:04Z; hashes are in `PROMPT_CACHE_PLAN.md` §2.

- **Anthropic:** `input_tokens` excludes cached tokens. Total input = `cache_read_input_tokens +
  cache_creation_input_tokens + input_tokens`. Writes split into `ephemeral_5m_input_tokens` and
  `ephemeral_1h_input_tokens`. Multipliers: read 0.1x (0.025x on Fable 5.1 and Mythos 5.1),
  5-minute write 1.25x, 1-hour write 2x.
- **OpenAI:** cache usage is reported as `input_tokens_details.cached_tokens` and
  `input_tokens_details.cache_write_tokens`. The guide's reference cost function
  (`calculateInputCost`, in three languages) computes ordinary input as
  `input_tokens - cached_tokens - cache_write_tokens`, so both are counted inside `input_tokens`; its
  example responses agree. GPT-5.6 and later: read 0.1x, write 1.25x. Earlier models are
  model-dependent.
- **Modifiers** that change the effective price: Anthropic batch, US-only inference
  (`inference_geo: "us"`) and fast mode. Real usage objects carry `service_tier`, `speed` and
  `inference_geo`. The values observed in real usage were `"standard"`, `"standard"` and
  `"not_available"`; the prompt caching documentation's example shows `service_tier` `"standard"` and
  `inference_geo` `"global"`.

## 3. Design options

**Option 1 (recommended): an optional profile at a new hash version.**

- `hash_version` 8 adds a link field `usage_breakdown`:
  `{input, cache_read, cache_write_5m, cache_write_1h, output, provider}`. All counts are non-negative
  integers. For OpenAI, `input` means uncached input, normalized at mint time.
- The public block binds `usage_breakdown` and a `price_ref` naming an entry in a new published,
  dated input-price file, `vectors/primitives/input_prices.json` (the counterpart of
  `legal_multipliers.json`).
- New event type `prompt_cache`. Magnitude rule: `kry_minted` equals the saving recomputed from
  `usage_breakdown` and the `price_ref` entry, converted at `USD_PER_KRY`, within a pinned tolerance.
- That rule needs its own clause in SPEC.md, dispatched on `hash_version` 8 and
  `event_type == "prompt_cache"`. §3.4.1's single-multiplier check (`tokens_saved × earn_rate × M`
  against `legal_multipliers.json`) cannot express a weighted sum over several token classes and a
  per-model price entry, so it does not apply to those links. All other links keep §3.4.1 unchanged.
- **Weakest point:** a second price file to maintain and publish; every price change is a corpus
  change.

**Option 2 (not recommended): express the saving through the existing multiplier rule.**

- Encode the saving as `tokens_saved` in frontier-output-token equivalents with new legal multipliers.
- **Weakest point:** the write premium and read price disappear into one number, so a stranger cannot
  recompute the discount from the provider's own fields. That defeats the point of the profile.

**Option 3 (not recommended): keep prompt caching report-only forever.**

- **Weakest point:** prompt-cache savings could never be attested or anchored.

## 4. Tier and reconciliation

- `provider_metered` only when every `usage_breakdown` field matches a provider usage export record,
  one-to-one. This means extending `scripts/kry_reconcile.py` to compare the full breakdown instead of
  `(prompt, completion)`.
- Otherwise the link is `self_reported`, and `veracity_floor` counts it as such.
- Fix the v1.3 ambiguity at the same time: define which provider fields make up the "prompt" in
  `[prompt, completion]` for Anthropic records that carry cache fields.

## 5. Vectors (ground rule 2: vectors or it did not happen)

**Valid:**

- A cache-read-only link.
- A mixed 5-minute and 1-hour write link.
- A Fable 5.1 link using the 0.025x read multiplier.
- A `provider_metered` link with an anchor.

**Adversarial:**

- A saving inflated beyond the recomputed value.
- The read multiplier omitted, so reads are valued as free.
- The write premium omitted.
- `price_ref` naming an entry that does not exist.
- An unknown model valued at a guessed price.
- `cache_read` larger than total input for an OpenAI-normalized record.
- `provider_metered` without a `usage_breakdown`.
- A hash_version 8 link in a chain read by a v1.3-only verifier: expected to fail closed.

## 6. Separate candidate: refresh the output price basis

Not part of the prompt-cache profile, but found in the same investigation.

- `PRICE_BASIS_AS_OF` is 2026-06-03. `_MODEL_OUTPUT_USD_PER_M` and its copy in `scripts/kry_verify.py`
  have Sonnet 7.5 and Haiku 1.25. The current list prices are Sonnet 5 $10 and Haiku 4.5 $5.
- Fable ($50 output) is absent and floors to 0.05. `min(1.0, price / 25)` would cap it at 1.0.
- **Ground rule 1** says existing vectors and verdicts never change meaning. So any refresh must be
  additive: add current models and their multipliers, keep existing multipliers legal, then
  regenerate `legal_multipliers.json`, the verifier copy and the browser page's hard-coded list, and
  confirm every existing vector keeps its verdict.
- **Weakest point:** keeping old multipliers legal means receipts minted at stale prices remain valid.
  That is correct for history, but it must be documented.

## 7. Acceptance bar

Items 1–4 are `SPEC_DEVELOPMENT.md` ground rules 1–4. Items 5 and 6 are this repository's existing
practice (the SC2 differential fuzz and the release gate), added here as requirements for this profile.

1. Additive: version-dispatched at hash_version 8; every v1.0–v1.3 vector and verdict unchanged.
2. Vectors generated by `vectors/generate.py`, never hand-written.
3. The Python reference and `verifiers/js` both pass the full corpus.
4. Fail-closed: a verifier that does not claim the profile rejects hash_version 8.
5. The differential fuzz gains mutation classes for `usage_breakdown` and `price_ref`, and shows zero
   divergences at the established sample size.
6. Release gate and CI green; the claims boundary updated.

## 8. Open questions for the maintainer

1. Should `price_ref` point at a price file per release, or at one file that only grows?
2. Should negative savings (writes never read back) be mintable as zero, rejected, or recorded as spend?
3. Should the batch, US-only and fast-mode modifiers be profile fields, or out of scope for v1.4?
4. Should the output price refresh (§6) ship before, with, or independently of v1.4?

## 9. Considered and rejected

- **Counting cache reads as `cache_hit`:** values input tokens at output prices and ignores read and
  write costs.
- **Substring model matching for input prices:** the existing output table matches "opus" inside any
  model name. Input prices differ across versions that share a substring (Opus 4.1 $15 vs Opus 5 $5),
  so the profile uses exact provider model ids.
