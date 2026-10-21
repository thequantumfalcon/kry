# Individual usage-row commitments

`scripts/kry_usage_link.py` adds a prospective, opt-in link between individual
ordinary displacement estimates, supplied provider rows and existing receipts.
It leaves the original verifier format and aggregate report interfaces unchanged.
This is a separate individual report, not an approval of a legacy aggregate packet.

A report total can match a receipt total even when the individual contributions
have been swapped. This profile commits each selected usage row, provider row,
scoped identifier and estimated contribution before the receipt is minted. The
assessment recomputes the full receipt content hash and verifies the public chain.
Matching token counts alone cannot open a receipt committed to another request.

## Prepare a row

Both files use an explicit normalized envelope. A provider row is:

```json
{
  "identity": {
    "provider": "anthropic",
    "account_scope_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "namespace": "http_request_id",
    "request_id_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "model": "claude-sonnet-5",
  "service_tier": "standard",
  "usage": {"prompt_tokens": 2000, "completion_tokens": 400}
}
```

The usage row contains the same fields plus:

```json
{
  "event_type": "short_circuit",
  "served_model": "claude-sonnet-5",
  "avoided_model": "gh/claude-opus-4.8"
}
```

These are constructed examples, not authenticated records or recommended current
model prices. The account digest must represent the same billing account/project
scope on both sides. The request digest must hash the actual identifier in the
stated namespace. HTTP request IDs and response IDs are distinct namespaces; do
not substitute one for another or infer an identifier from matching token counts.
Providers use the exact identifiers `anthropic`, `openai` or `xai`; aliases are
rejected. One provider/account scope must use one identifier namespace in an
assessment. Cross-namespace identity equivalence is not established. Identifiers
and account scope are operator declarations, not authenticated claims.

```sh
python3 scripts/kry_usage_link.py prepare \
  --usage-row usage-row.json --provider-row provider-row.json > prepared.json
```

The result contains a content-free `report_row` and `mint_arguments`. Preparing
has no persistence or network effect. To create a receipt, an integration can
pass those arguments explicitly to `kry.kry_mint.mint`:

```python
prepared = prepare_link(usage_row, provider_row)
receipt = mint(**prepared['mint_arguments'])
```

The receipt remains `self_reported`. Exact local agreement does not promote
supplied records to T1. The opt-in mint call uses the existing idempotent-evidence
behavior; its documented persistence and recovery limits still apply. A `None`
return is not proof that no write occurred. Preserve the prepared row and returned
receipt identifier alongside the original captures in private storage.

## Verify the selected individual report

Put usage and provider rows in JSON arrays. The mapping file is an array with
one entry for every supplied row:

```json
{
  "usage_index": 0,
  "provider_index": 0,
  "receipt_id": "KRY-00000001",
  "report_row": {"...": "the complete prepared report_row object"}
}
```

```sh
python3 scripts/kry_usage_link.py assess \
  --attestation attestation.json --usage-rows usage-rows.json \
  --provider-rows provider-rows.json --links links.json > linked-report.json

python3 scripts/kry_usage_link.py assess --verify linked-report.json \
  --attestation attestation.json --usage-rows usage-rows.json \
  --provider-rows provider-rows.json --links links.json
```

Assessment rejects altered report contributions, unequal identities/models/token
pairs, duplicate identities or reused row/receipt references, incomplete supplied
row coverage and receipts that do not open to the committed row. Only v7 T0
`short_circuit` receipts are supported. The full attestation must verify, but other
receipts in that chain are explicitly excluded from the individual report scope.
Old valid receipts that committed only an ID cannot be retroactively bound to a
new full row without creating new evidence; their original verdict is unchanged.

Exit 0 means the prepared/linked profile completed, or a saved report matched an
explicit rebuild. Exit 1 means a saved report mismatched. Exit 2 means invalid or
unsupported inputs. Each file is bounded to 16 MiB and read once. Input file hashes
and the complete recomputed result bind the saved assessment to the supplied bytes.

## Scope and remaining evidence

Only one ordinary text response per incurred displacement is modeled. Unexpected
fields, native cache categories, reasoning/tool usage, fallback iterations, batch
and nonstandard service tiers are unsupported. Gateway cache hits avoid a call;
they cannot be given a fictitious incurred provider request. Cache lineage and
counterfactual savings require different evidence and a separate profile.

The normalized envelopes deliberately do not contain prompts or responses.
`normalization_verified` is false: this command does not prove that a native
capture was projected faithfully or that categories were not removed beforehand.
Retain native captures and their documented extraction separately. Hashing a
normalized row cannot authenticate the original provider or repair a lossy adapter.

Amounts use the existing KRY output-price benchmark and its known model-matching
behavior. They are neither complete category-specific API prices nor invoices.
`actual_charge_usd` stays null and `external_verified_savings` stays false.
A valid opening proves a committed association among supplied data, not source
truth, the avoided expensive call, output quality or customer benefit. A trusted
earlier chain anchor is still required to detect a complete retrospective remint.

For aggregate diagnostics and exact token-pair consistency without new minting,
see [the optional evidence assessment](KRY_EVIDENCE_ASSESSMENT.md). For the release
claim limits, see [the claims boundary](CLAIMS_BOUNDARY.md).
