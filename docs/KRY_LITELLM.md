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
- `response_cost` is LiteLLM's **price-table estimate**; it is recorded in the receipt
  detail as context, never used as evidence.

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
are outside this guarantee. Anything malformed or missing fails closed:
no receipt, never a guessed one, and the logging path never raises into your serving
traffic.

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
