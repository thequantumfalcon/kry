# kry × LiteLLM — record your proxy's cache events

Records eligible LiteLLM response-cache events as kry `cache_hit` receipts in a
tamper-evident, stranger-verifiable ledger. One file, no new dependencies:
`scripts/kry_litellm_callback.py`
imports only the kry stdlib core; LiteLLM is imported lazily (the extractor is fully
testable without it).

## The honest evidence boundary — read first

- Every receipt minted here is tier **`self_reported` (T0)**: the cache-hit signal comes
  from the operator's own gateway, so the attestation's `veracity_floor` correctly stays
  `0.0` for these receipts. Verification checks that the ledger of claimed hits is
  **intact, with verifiable KRY benchmark arithmetic**. It cannot prove the hits happened
  or establish actual provider charges. Publish a chain anchor
  (`scripts/kry_chain_anchor.py`) to also make retroactive edits/truncation detectable.
- `cache_hit` is **LiteLLM's response cache**, not provider-side prompt caching
  (`cached_tokens`) — prompt-cache savings need a separate signal.
- `response_cost` is LiteLLM's **price-table estimate**; a finite numeric value is
  recorded in the receipt detail as context, never used as evidence. Invalid optional
  cost estimates are omitted without suppressing an otherwise eligible receipt.

Wording correction: “attest your proxy's cache savings” → “record your proxy's cache events”;
“intact and honestly priced” → “intact, with verifiable KRY benchmark arithmetic”.

## KRY valuation and provider cost are separate

The cache callback credits the cached response's **total prompt plus completion tokens**
at KRY's output-price multiplier. The receipt's `usd_equivalent` is that benchmark
valuation. Provider billing can price input, output, cache reads and cache writes
differently; verifier acceptance does not make the benchmark an invoice amount.

Constructed example, ordinary text on `claude-haiku-4-5-20251001`: 12 input tokens and
4 output tokens at the [published Haiku 4.5 rates](https://platform.claude.com/docs/en/about-claude/pricing)
(checked 2026-10-20) give `(12 × $1 + 4 × $5) / 1,000,000 = $0.000032`.
KRY values all 16 tokens at its $5/M output benchmark, giving `$0.000080`.
The first number estimates a supplied usage record at list prices; the second is KRY
arithmetic. Neither proves an account debit or the cost of a counterfactual call.
Keep actual charges pending until the relevant billing evidence is available.

From a repository checkout, estimate this model's input and output separately from
a retained raw Anthropic Messages response and its provider `request-id` header:

```bash
python3 scripts/kry_anthropic_usage.py response.json --request-id req_example
```

The offline helper emits decimal cost estimates, pricing provenance and the raw message
and request identifiers. It leaves `actual_charge_usd` null and `financial_status`
unverified. Its scope is `claude-haiku-4-5-20251001` ordinary text with no prompt caching,
tools or thinking; unsupported models and categories reject instead of using a generic
fallback rate. The price basis assumes standard first-party service: a non-null
`usage.service_tier` must be `standard`, and an absent or null tier does not establish it.
Batch discounts, regional premiums and negotiated prices are not calculated.
For this pinned Haiku 4.5 model, [standard pricing applies without a geo
surcharge](https://platform.claude.com/docs/en/manage-claude/data-residency);
do not send the `inference_geo` request parameter, which the model does not support.
The helper accepts `usage.inference_geo` as `global`, `not_available` or null.
It preserves `not_available`, also listed in the [usage-report
documentation](https://platform.claude.com/docs/en/manage-claude/usage-cost-api),
without treating it as an observed global location. Other non-null values reject.
The returned pricing assumptions are part of the estimate: missing or null optional
metadata stays unknown and does not prove the absence of cache or tool charges.
Use the helper only for the declared ordinary-request scope.
Keep `response.json` private. This does not fetch an account report,
change a KRY receipt or upgrade its evidence tier.

## Explicit model identities and unpriced events

Model lookup uses exact supported names and explicit aliases. It does not remove
arbitrary date suffixes or search for a family name inside an unknown identifier.
These snapshots use the existing **2026-06-03 KRY benchmark** entries returned by
`kry_token.price_provenance()`; this compatibility mapping does not refresh prices
or assert a current provider invoice rate:

| Explicit snapshot | KRY benchmark entry | Output benchmark per million tokens |
|---|---|---:|
| `claude-opus-4-1-20250805`, `claude-opus-4-5-20251101` | `opus` | $25.00 |
| `gpt-4o-2024-08-06`, `gpt-4o-2024-11-20` | `gpt-4o` | $10.00 |
| `gpt-4o-mini-2024-07-18` | `gpt-4o-mini` | $0.60 |

Supported provider namespaces such as `anthropic/`, `openai/` and `or/openai/`
resolve these same names. Snapshot identities were checked on 2026-10-23 against
[Anthropic's model lifecycle table](https://platform.claude.com/docs/en/about-claude/model-deprecations)
and the official [GPT-4o](https://developers.openai.com/api/docs/models/gpt-4o) and
[GPT-4o mini](https://developers.openai.com/api/docs/models/gpt-4o-mini) model pages.
Identity support does not establish present API access: the Opus 4.1 snapshot is
retired, while the listed GPT snapshots are deprecated. An invented date or name
remains unpriced even when it resembles a supported family.

`deepseek-chat` and `deepseek-reasoner` remain unpriced. DeepSeek's
[retirement announcement](https://api-docs.deepseek.com/news/news260424/) says these
names routed to V4 Flash before becoming inaccessible after July 24, 2026.
Their names alone do not select a stable model and request-era price; assigning
the generic `deepseek` benchmark would assume that missing information.
`deepseek-flash` is also unpriced. No current rate is inferred from a similar name.
Already issued receipts keep their historical verification semantics.

The `kry.litellm` logger emits WARNING reasons `unpriced_avoided_model` or
`unpriced_served_model`. An unpriced response-cache event retains its zero-value
receipt; an unpriced route produces no receipt because unknown served cost cannot
be counted as free. DEBUG reasons `known_free_avoided_model` and
`no_positive_benchmark_difference` distinguish the existing free-route policy and
known non-cheaper routes. These messages contain fixed reason text only, without
model strings, prompts, call IDs or account metadata. Check the private gateway
configuration to resolve an unpriced model; do not rename it to a priced family
without establishing the intended benchmark mapping.

## Setup

LiteLLM proxy (`litellm_config.yaml`), with the script on `PYTHONPATH`:

```yaml
litellm_settings:
  callbacks: kry_litellm_callback.kry_logger
```

Or in Python:

```python
import litellm
from kry_litellm_callback import KryLiteLLMLogger
litellm.callbacks = [KryLiteLLMLogger()]
```

An eligible response-cache hit attempts to mint one receipt (`tokens_saved` = the cached
response's total tokens, used as an operator-asserted proxy for the avoided call;
`avoided_model` = the requested model; the LiteLLM
call id is the evidence key). The callback checks the retained valid receipt log
under its file lock, so these updated callback writers cannot mint a committed
evidence hash again after guard eviction, restart or delivery to another updated
worker sharing that log. Zero-value
receipts count too. The bounded 4,096-key memory guard is only a fast path.
This guarantee requires a filesystem supporting the repository's process lock;
it does not cover separate logs, unreliable network filesystem locks or deletion
of the authoritative history. Old or generic mint callers still use their own
decay contract and can append repeated evidence. All concurrent writers must use
the updated mint implementation's shared lock ordering; mixed-version writers
are outside this guarantee. Missing required identity or invalid usage prevents a
receipt; optional contextual cost estimates do not determine eligibility. The
logging path never raises into your serving traffic.

Both callback paths reject non-null token counts that are boolean, non-integer or
negative, and reject a supplied total that differs from prompt plus completion
when all three counts are available. A known component cannot exceed the total,
even if the other component is unknown. A response-cache hit may use a positive total
alone, or fall back to prompt plus completion when the total is absent or null.
A route may use completion alone. Missing or null optional counts are unknown;
these consistency checks do not authenticate the reported usage.

## Auto-router receipts

When LiteLLM's auto-router sends a request to a cheaper model, the request metadata carries
`routing_decision.savings_baseline_model`: the model the router measures its savings against.
Each such request mints one `short_circuit` receipt, the same event the savings report mints for
a displacement:

- `avoided_model` is the router's baseline; `served_model` is the model that actually ran.
- `tokens_saved` is the response's output tokens, valued at kry's published price difference
  between the two models.
- Nothing is minted for the router's own internal calls (classifier, shadow evaluation), when the
  baseline served the request itself, or when the route is not cheaper at kry's published prices.
- LiteLLM's `autorouter_savings` figure is recorded in the receipt detail as context. It is signed
  and also prices input and cache tokens, so it need not match kry's value.
- The decision's `signals` and keyword fields quote the caller's prompt; they are never copied.

The receipt is `self_reported` (T0): the counterfactual is your router configuration.
Verification checks the recorded history and KRY's output-price-difference arithmetic.
It does not establish that the baseline would have been called or determine the provider's
category-specific bill.

## Two things worth knowing when you wire this up

- **LiteLLM logs success events on a background thread.** A serving response can finish before
  its callback. Wait for callback completion with a deadline before checking the receipt inventory,
  as the integration tests wait for receipts; a fixed sleep alone does not establish completeness.
- **Register the logger once, in `callbacks`** (the setting shown above). On a cache hit LiteLLM
  passes `cache_hit=True`; in the tested 1.101.0 paths an ordinary call's field is absent or `None`, which is
  why the callback tests for `is True` rather than truthiness.

The dedicated `litellm-integration` CI job installs LiteLLM 1.101.0 and runs
`tests/test_litellm_integration.py` using mocked completions and
`tests/test_litellm_anthropic_integration.py` using a constructed HTTP response through the
provider adapter. These tests exercise real cache and callback dispatch, without API keys or
paid provider calls. The stdlib-only jobs may skip these optional modules; the integration
job requires the dependency to import successfully.

The [2026-10-20 synthetic API evaluation](evidence/LITELLM_SYNTHETIC_API_2026_10_20.md)
records a bounded live run, the earlier answer-format failures and the limits of
the privately retained evidence. It also lists publicly repeatable offline checks.

## Keep request identifiers and evidence sources distinct

Use a unique LiteLLM call ID for each attempted request. The callback binds its receipt to
`sha256("litellm:" + litellm_call_id)`; this is not a provider-issued identifier.
In LiteLLM 1.101.0's non-streaming Anthropic adapter, the normalized `chatcmpl` response
ID is generated locally. Preserve the raw Anthropic `msg_` message ID separately at the
HTTP boundary. The first normalized response can be joined through
`_hidden_params["additional_headers"]["llm_provider-request-id"]` to the provider's
`request-id` header. Cached responses can lose that header; retain the first response's
mapping and compare the cached response ID, request fingerprint, content digest and usage.
These private adapter fields require rechecking when upgrading LiteLLM.

An operator-captured API response remains operator-retained evidence. The standard
[Anthropic usage/cost reports](https://platform.claude.com/docs/en/manage-claude/usage-cost-api)
and [OpenAI organization usage API](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/completions)
return time-bucket aggregates. Do not describe them as per-request exports or infer that
an inference credential grants reporting access. `kry_reconcile.py` needs a separately
retrievable source with the required coverage; accepting a JSON shape does not establish
its provenance. Its per-request mode matches token pairs, not request identity.

## Bounded single-process evaluation procedure

1. Set `KRY_DATA_DIR` before importing KRY, isolate the run's storage and cache, and verify
   writes before dispatch. Retain a request inventory independently of the callback.
2. Set finite request, output-token and timeout limits. For a cache-repeat check, enforce
   the provider-send ceiling at the transport boundary; a missed cache must not silently
   create another paid request. Disable retries and fallback providers.
3. Register the callback once. Record unique attempt IDs before dispatch, then wait for
   each expected callback with a deadline. Match the inventory to persisted receipts:
   ordinary calls without routing metadata mint none; eligible cache hits mint one.
4. Stop on a missing or duplicate callback/receipt, unexplained token change, failed write,
   process interruption or verification failure. Preserve the run; do not retry uncertain
   delivery, reset decay counters or remint the same event under a new ID.
5. Verify the chain and attestation, retaining any anchor separately. Keep benchmark value,
   list-price estimates and observed account charges in distinct fields.

This procedure bounds an evaluation; it is not durable gateway delivery. Callback mints
opt into `mint(..., idempotent_evidence=True)`: first events use full supplied tokens,
and committed evidence hashes cannot mint twice through this path. Both strict and
generic writers reserve shared decay allowance while holding the mint-file lock,
before append. A failed append can consume a conservative reservation; a strict
retry still receives full first-event tokens. Later generic replays use the shared
decay count, preventing a second full first-event credit.
A complete retained receipt blocks reminting even if its acknowledgement failed.
That is replay suppression for a visible record, not proof of power-loss durability.
An incomplete or corrupt log refuses further mints and requires operator recovery;
the callback does not repair it or reconstruct a lost acknowledgement.

The default generic `mint()` path still applies geometric decay and can conservatively
consume allowance before a failed append. The opt-in path is not an atomic transaction
across the receipt log, rollback checkpoint and balance ledger, nor a guarantee against
power loss, history deletion or missed callbacks. An uncertain append may leave a
receipt without the subsequent balance update; inspect the retained chain and use
operator-led ledger reconciliation rather than assuming both committed.
Storage preflight does not eliminate
later failure. Production delivery and crash recovery still need a separate contract.
Authoritative deduplication scans and verifies the retained log; no production
throughput or multi-node filesystem guarantee is implied.

## Attest and hand it to a stranger

```bash
python3 - > attestation.json <<'EOF'
from kry import kry_attest
print(kry_attest.build_attestation().to_public_json())
EOF
# → attestation.json; then anyone, offline, without running your code:
python3 scripts/kry_verify.py attestation.json
node verifiers/js/cli.mjs attestation.json          # independent implementation
```

The verifier re-derives the chain, the KRY benchmark arithmetic, and the `veracity_floor` — and
the floor will honestly read `0.0` for a pure cache-hit ledger. That is the feature, not
a bug: the number states exactly how much still rests on your word, instead of hiding it
behind a green checkmark. See `docs/CLAIMS_BOUNDARY.md` for what you may claim on top.

## Optional private usage observation

`KryLiteLLMLogger(usage_observer=save_private_observation)` calls your fast,
synchronous callable with one sanitized observation before receipt minting in
each success callback. The default logger does not capture observations. This
hook adds no storage or network sink; your application owns private persistence
and must compare retained observations with its independent attempt inventory.
Register the logger once. Repeated callbacks produce repeated observations while
receipt deduplication still prevents a second mint for the same evidence key.

`scripts/kry_litellm_observation.py` preserves an allowlist of normalized usage
categories, including cache TTL splits, reasoning/context counts and exact xAI
cost ticks when exposed. Counts remain integers up to `2**63 - 1`, including
values above JavaScript's exact-number range. Missing, null, zero and malformed
fields have distinct states; unsupported fields are flagged without copying
names or values. Categories are not summed or priced. Requested and normalized
response model names remain separate, with exact string comparison only.

The projection hashes LiteLLM call and normalized response identifiers. Its
provider request-ID candidate comes only from the tested
`_hidden_params.additional_headers["llm_provider-request-id"]` field. No raw
identifier, prompt, generated text, arbitrary metadata or other header is copied.
Flattened headers cannot reveal duplicate wire headers, and ambiguous candidates
are unusable. Model names can contain private routing labels: retain the entire
observation privately unless separately reviewed for publication.

These are **LiteLLM-normalized callback observations**. Adapters can already have
replaced missing/null counts with zero or synthesized identities. The hook cannot
recover those distinctions, authenticate a provider, authorize a model's price,
prove all attempts were captured or reconcile an invoice. It always leaves
`actual_charge_usd` null and `billing_reconciliation` as `not_performed`.
The installed-adapter test uses constructed HTTP responses and response caching;
it does not contact a provider or establish real billing behavior.

Observer errors increment `logger.observation_failures` and emit only the fixed
message `kry usage observation failed`; serving and receipt minting continue.
That counter is process-local, resets with the logger, and does not establish
persistence. Stop evidence processing on any failure or unexplained missing
observation. Async observers are unsupported; a slow synchronous observer can
delay the callback. This feature changes no receipt arithmetic or evidence tier.
