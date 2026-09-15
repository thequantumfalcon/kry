# Plan: valuing provider prompt caching

**Status:** draft for maintainer approval. Nothing here is implemented.
**Companion:** [`SPEC_V1_4_PROMPT_CACHE.md`](SPEC_V1_4_PROMPT_CACHE.md), the spec development sheet for the
attested (Stage B) form.

## 1. Problem

kry cannot see the most common saving in agentic and coding workloads today.

- `scripts/kry_savings_report.py` `analyze()` values only completion tokens: spend is
  `spend_cost(model, completion)`, and cache-hit value is
  `completion * rate * value_multiplier(avoided_model)`.
- The usage fields that carry prompt caching (`cache_read_input_tokens`,
  `cache_creation_input_tokens`, `input_tokens_details.cached_tokens`) are never read.
- `value_multiplier` and the price table `_MODEL_OUTPUT_USD_PER_M` (`src/kry/kry_token.py`) are
  output-price only. The repository has no input-token price anywhere.

On a privately measured agentic workload, the report showed zero saving while prompt caching removed
most of the input-side cost. An outside user on a caching provider would see the same zero.

The obvious shortcut, recording cache reads as `cache_hit` events, is wrong three ways. It values input
tokens at output prices, ignores that cache reads are still billed, and ignores the write premium, so
it overstates the saving. Overstating a saving is the failure kry exists to catch.

## 2. Evidence (sealed before use)

Raw pages were fetched and hashed at 2026-09-15T04:21:04Z. Figures below are copied from those pages.

| Source | SHA-256 |
|---|---|
| https://platform.claude.com/docs/en/about-claude/pricing | `880bdf86b7b7a3dc963c8e356ea88d2b87c1e8e57640a46d7e61d9854e5634c9` |
| https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching | `4a05b04857a90dae03f7133c87913e0751202ce7bf00544e333b1c4af18e4c88` |
| https://developers.openai.com/api/docs/guides/prompt-caching | `64d1e0ee076a967d7553542a9e077ca677baf08cd5d663b7dbb5ad393992cfdb` |
| https://developers.openai.com/api/docs/pricing | `bba0ee6f33e87f15e1802a304e5249769d0df25ee9bd14aec7b1054556c0f1b3` |

**Anthropic**

- Multipliers on the base input price: 5-minute cache write 1.25x, 1-hour cache write 2x, cache read
  0.1x, except 0.025x on Claude Fable 5.1 and Claude Mythos 5.1.
- Usage fields: `input_tokens` is only the tokens after the last cache breakpoint. Total input is
  `cache_read_input_tokens + cache_creation_input_tokens + input_tokens`. A `cache_creation` object
  splits writes into `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`.
- Base input, output ($/MTok): Fable 5.1 and 5: 10, 50. Opus 5, 4.8, 4.7, 4.6, 4.5: 5, 25.
  Sonnet 5: 2, 10. Sonnet 4.6 and 4.5: 3, 15. Haiku 4.5: 1, 5.
- Other modifiers stack on these prices: the Batch API (50%), US-only inference (1.1x), and fast mode
  on Opus 5 and 4.8 ($10 input, $50 output).

**OpenAI**

- The prompt caching guide says that for GPT-5.6 and later, cache writes cost 1.25x and cache reads
  0.1x the uncached input rate. Earlier models have model-dependent cached-input rates.
- Usage fields: `input_tokens_details.cached_tokens` and `input_tokens_details.cache_write_tokens`.
  The guide's own reference cost function (`calculateInputCost`, given in three languages) computes
  `ordinaryInputTokens = inputTokens - cachedTokens - cacheWriteTokens`, so `input_tokens` includes
  both cached and write tokens. This is the opposite of Anthropic. Its example responses agree
  (request 2: `input_tokens` 15,000 with `cached_tokens` 12,000 and `cache_write_tokens` 3,000).
- The pricing page lists Input / Cached input / Cache writes / Output per model. For example, short
  context: `gpt-5.6-sol` $4.00 / $0.40 / $5.00 / $20.00, `gpt-5.6-terra` $2.00 / $0.20 / $2.50 /
  $12.00.

## 3. Findings from this investigation (decisions needed, not part of Stage A)

1. **The output price basis is stale.** `PRICE_BASIS_AS_OF = "2026-06-03"`. The table has Sonnet at
   7.5 ("estimate") and Haiku at 1.25, but current list output prices are Sonnet 5 $10 and Haiku 4.5
   $5. Fable models are absent, so they get the 0.05 unknown-model floor. The same prices are duplicated
   in `scripts/kry_verify.py` (`_PRICE_AS_OF` line 66, the `_MODEL_USD_PER_M` dict lines 76-80,
   `legal_multipliers` lines 83-90), feed `vectors/primitives/legal_multipliers.json`, and the derived
   multiplier array is hard-coded in `verifiers/web/index.html` (line 90). Changing it touches the conformance corpus, so it belongs
   on the spec sheet.
2. **The reconciler's prompt count is ambiguous for Anthropic.** `scripts/kry_reconcile.py`
   `normalize_provider_record` reads `input_tokens` as the prompt count. For Anthropic that excludes
   cached tokens, so a `provider_metered` receipt on a cached request is compared against uncached
   input only.
   **Decision (Stage A):** both the reconciler and the savings report's `normalize` count the whole
   Anthropic prompt. Receipts minted with the old count still match and are reported separately as
   legacy matches.
3. **Spend and value disagree for unmatched models.** `spend_cost` charges any model with no
   `SPEND_RATES` prefix at the Opus rate, while `value_multiplier` gives unknown models 0.05. This skews
   the report's efficiency ratio.
   **Decision (Stage A):** fixed in the report, not in `spend_cost`. `spend_cost` also prices routing in
   the ledger (`spend`, `can_afford`), where charging unknown models the frontier rate keeps routing
   fail-closed. The report now uses `SPEND_RATES` for gateway ids, the dated list output price for exact
   provider model ids, and counts anything else as an unpriced call left out of SPEND. The paid/free
   classification still follows `spend_cost`, so the analysis and mint paths stay aligned.

## 4. Non-goals

- Recording cache reads as `cache_hit` or any other minted event in Stage A.
- Claiming savings for flat-rate subscription usage. Dollar figures are list-price equivalents.
- Batch, US-only inference and fast mode pricing in the first version. Such records are excluded and
  counted, not guessed.
- Providers other than Anthropic and OpenAI.

## 5. Stage A: report the prompt-cache discount (no spec change)

Stage A adds a clearly labeled figure to the savings report. It mints nothing, changes no attestation,
and leaves `saved_kry`, `veracity`, the chain and the corpus untouched.

**A1. Dated input price basis.** New module `src/kry/kry_prompt_cache.py`, stdlib only:

- Per-model base input price, cache read price, 5-minute write price and 1-hour write price, copied
  from the sealed pages. The table records its `as_of` date (2026-09-15) once, with every source URL
  and page SHA-256 (`PRICE_SOURCES`).
- Models are matched by an explicit table of provider model ids (for example `claude-opus-5` →
  Claude Opus 5), not by substring. An unrecognized model is reported as unpriced and excluded.
- Kept separate from `_MODEL_OUTPUT_USD_PER_M`, so Stage A cannot change minting or verification.

**A2. Valuation, a pure function per usage record.**

- Anthropic:
  - without caching = (input + cache_read + cache_write) x base input
  - actual = input x base input + cache_read x read price + write_5m x 5m price + write_1h x 1h price
  - saving = without caching − actual
- OpenAI (`input_tokens` includes cached and write tokens; see §2):
  - uncached = input − cached − write; a record where that is negative is malformed and excluded
  - without caching = input x base input
  - actual = uncached x base input + cached x cached-input price + write x cache-write price
  - saving = without caching − actual
- If the TTL split is missing, all writes are priced at the 1-hour rate. That is the most expensive
  write, so the saving is never overstated. Such records are counted.
- A negative saving (writes that were never read back) is reported as negative, not floored.
- Records priced differently from the standard table are excluded and counted, until those modifiers
  are modeled: `speed` of `"fast"`, `inference_geo` of `"us"`, or a `service_tier` other than
  `"standard"`. An absent field, `"global"` or `"not_available"` counts as standard. (Values seen in
  real Anthropic usage objects: `service_tier` `"standard"`, `speed` `"standard"`, `inference_geo`
  `"not_available"`. The prompt caching documentation's example shows `service_tier` `"standard"` and
  `inference_geo` `"global"`. The full value sets must be confirmed from the API reference before
  implementation.)

**A3. Report integration.** `kry_savings_report` gains a separate `prompt_cache` block in `--json` and
a separate text section:

- Totals in tokens and in list-price dollars, per model.
- Counts of priced, unpriced, malformed, modifier-excluded and conservative-TTL records.
- The price basis date, source URLs and seals.
- A fixed label: "provider prompt-cache discount, computed from reported usage at list price; not
  minted, not attested, self-reported".
- `--strict-baseline` still shows the block with the same label. The figure is never added to
  `saved_kry` or `efficiency_ratio`.

**A4. OpenAI.** Add GPT-5.6-and-later models from the sealed pricing page. Earlier OpenAI models stay
unpriced until their cached-input rates are sealed.

**A5. Tests** (new `tests/test_prompt_cache.py`):

- The provider's own worked example: Claude Opus 5, 40,000 cache-read tokens cost $0.02 instead of
  $0.20, a $0.18 saving.
- A mixed-TTL record, a Fable 5.1 record at 0.025x, and a write-only record with a negative saving.
- A missing TTL split priced conservatively; an unknown model excluded; an OpenAI record with cached
  tokens inside `input_tokens`; a malformed record where cached exceeds input.
- Modifier records excluded; the JSON block shape and label.
- With `--strict-baseline`, the prompt-cache block keeps its label and list-price dollars while the
  existing self-reported cache-hit savings are zeroed, and the two are never combined in any total.
- The block never changes `saved_kry`, spend, efficiency, veracity, by-kind or by-class figures: run
  the same log with and without cache fields and compare. The block does not touch the mint path. The
  separate prompt-count fix (finding 3.2) does intentionally change `metered_tokens` on new
  displacement receipts whose usage reports cache tokens; an end-to-end test mints such a receipt,
  reads the chain back and reconciles it.

**A6. Validation.**

- Re-run the private workload offline and compare Stage A against an independent hand calculation.
  Its figures stay private.
- The conformance corpus, `vectors/generate.py` drift check and full test suite must be unchanged.
- The release gate must pass.

**A7. Documentation.**

- A short section in `docs/VERIFY_FIRST_RECEIPT.md` or the README explaining the new block.
- An entry in `docs/CLAIMS_BOUNDARY.md`: the prompt-cache discount is reported, not attested.
- A CHANGELOG entry.

## 6. Stage B: attested prompt-cache receipts (trigger-gated)

Start Stage B only when an outside user on API billing asks for attested prompt-cache savings and can
supply a provider usage export. The design and acceptance bar are in the spec sheet. Stage B would
be SPEC v1.4.

## 7. Success criteria

1. Stage A reproduces the provider documentation's worked example exactly.
2. Stage A leaves every minted value, attestation, vector and verifier result unchanged.
3. For an outside user with an API invoice, the reported prompt-cache discount matches the invoice's
   cache line items to within rounding. This is the outreach demonstration, and it needs that user.
4. No record is ever valued at a guessed price. Unknown or modified records are excluded and counted.

## 8. Sequencing and approvals

1. Approve this plan and the spec sheet.
2. Stage A PR: A1-A3, A5-A7 (Anthropic). Local tests, Linux release gate, CI, merge.
3. Stage A follow-up PR: A4 (OpenAI).
4. Separately, decide findings 3.1-3.3.
5. Stage B only after a real request, starting from the spec sheet.

## 9. Risks

- **Prices change.** Mitigation: every price carries `as_of`, source and seal, and the report prints
  them. A refresh is a reviewed edit with a new seal.
- **Readers mistake the report figure for an attested saving.** Mitigation: separate block, fixed label,
  never summed into `saved_kry`, and a claims-boundary entry.
- **Provider usage semantics differ.** Anthropic's documentation says `input_tokens` excludes cached
  tokens; OpenAI's reference cost function treats `input_tokens` as including them. Mitigation:
  provider-specific parsing with a test pinning each rule. The same difference is behind finding 3.2
  (the reconciler's prompt count), so Stage A must not rely on `kry_reconcile.py` for validation.
- **Scope creep into the stale output price table.** Mitigation: kept as a separate decision on the
  spec sheet.
