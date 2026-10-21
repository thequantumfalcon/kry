# Separate Anthropic usage estimate

`scripts/kry_anthropic_usage.py` prices a supplied ordinary, nonstreaming
`claude-haiku-4-5-20251001` Messages response at its dated input/output list
prices. It does not make a request, mint a receipt, verify the source, or
reconcile an invoice. See [the integration guide](KRY_LITELLM.md#kry-valuation-and-provider-cost-are-separate)
for the distinction between provider cost estimates and KRY receipt valuation.

```bash
python3 scripts/kry_anthropic_usage.py response.json --request-id req_example
```

Keep the raw response and output private: the response can contain content,
and the estimate preserves supplied provider message and request identifiers.
The output contains usage metadata and prices, without response text or a
custom stop sequence. It leaves `actual_charge_usd` null and
`financial_status` unverified.

## Stopping condition and usage are separate

A recognized ordinary nonstreaming `stop_reason` is required. The estimate
preserves that value and adds `generation_status`:

| Provider stop reason | Generation status | Meaning |
| --- | --- | --- |
| `end_turn` | `ended` | The provider reports a natural end. |
| `stop_sequence` | `stopped_by_sequence` | A configured sequence stopped generation. |
| `max_tokens` | `truncated` | Generation reached its token limit. |
| `model_context_window_exceeded` | `truncated` | Generation reached its context limit. |
| `refusal` | `refused` | The provider reports a refusal. |

These meanings follow the [provider's stop-reason documentation](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons),
checked October 21, 2026. They do not establish that an answer is adequate,
that a transport capture is complete, or that money was charged. Truncation
and refusal do not erase reported token consumption: supported usage receives
the same list-price arithmetic while retaining its stopping condition.

Missing, null, malformed, or unknown stop reasons reject. `tool_use` and
`pause_turn` also reject because tool workflows are outside this helper's
ordinary-text scope. The helper does not assemble streaming events or decide
whether to continue a conversation.

## Input and pricing boundaries

Required input/output counts must be nonnegative JSON integers. Supported
optional usage fields preserve absence, null, and explicit zero distinctly.
Supplied cache/tool/thinking counts must be integer zero; unknown or nonzero
unsupported categories reject. Non-null service tier and speed must be
`standard`; supported geography metadata is preserved as documented in the
integration guide. Missing optional metadata remains unknown, with the stated
ordinary-request pricing assumptions; it does not prove zero additional fees.

The CLI accepts UTF-8 JSON documents up to **16 MiB**, including whitespace,
and reads at most that limit plus one byte. Larger files, nonstandard JSON
constants (`NaN`, `Infinity`, `-Infinity`), and parser nesting overflow produce
an error with no estimate. The file-size limit applies to this CLI, not to
the already-decoded dictionary function or every KRY parser. This helper is
a narrow usage estimator, not a complete validator of the provider's schema.

Its estimates do not include negotiated rates, credits, discounts, invoice
rounding, account-scope completeness, or per-request billing attribution.
The [claims boundary](CLAIMS_BOUNDARY.md) remains authoritative.
