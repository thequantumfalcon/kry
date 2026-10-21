# Checking a saved Anthropic usage projection

`scripts/kry_anthropic_projection.py` checks one supplied nonstreaming Messages
response before constructing an optional normalized provider row. It supports
only `claude-haiku-4-5-20251001` and the `supplied-anthropic-ordinary/v1` profile.
This is a local consistency check against saved bytes. It does not authenticate
the provider, establish account ownership, create a receipt or calculate a bill.

## Inputs

Keep the complete saved response privately. Supply a separate acquisition JSON:

```json
{
  "schema": "kry_anthropic_acquisition/v1",
  "endpoint": "/v1/messages",
  "http_status": 200,
  "requested_model": "claude-haiku-4-5-20251001",
  "request_id": "req_constructed_example",
  "observed_at_utc": "2026-10-21T00:00:00Z",
  "account_scope_sha256": null
}
```

This is a constructed example. Use the actual supplied `request-id` header, not
the response body's message ID. The timestamp is an operator declaration of
observation time, not a trusted timestamp or billing-period assignment. For a
historical capture, preserve which declarations were recorded directly and which
were reconstructed. Never fill an unknown account scope with a guessed digest.

A declared account/project scope can use a lowercase SHA-256 digest in place of
`null`. The tool does not validate the account or establish how that digest was
acquired. HTTP status must be integer 200, the endpoint must be `/v1/messages`, and
the requested and returned model must both match the pinned profile. Alias and
fallback resolution are outside this profile. Its `req_` and `msg_` identifier
syntax follows the existing estimator; other provider formats are unsupported
here, rather than declared invalid everywhere.

## Run and rebuild

```sh
python3 scripts/kry_anthropic_projection.py response.json \
  --acquisition acquisition.json > projection.json

python3 scripts/kry_anthropic_projection.py response.json \
  --acquisition acquisition.json --verify projection.json
```

Each input is read once as a regular file, bounded to 16 MiB. The direct Python
function `project_anthropic_response(response_raw, acquisition_raw)` accepts the
same bounded byte strings. This separate profile rejects duplicate JSON members,
floating-point literals and nonfinite constants. Existing attestation parser
semantics are unchanged. UTF-8 and the supported exact schemas are required.

Exit 0 means projection succeeded, or the saved report's parsed JSON matched a
rebuild after sorting object keys. Report whitespace is not significant; the
recorded response and acquisition hashes still bind their exact input bytes.
Exit 1 means a saved report differed; exit 2 means invalid or unsupported input.
The command prints JSON and has no persistence, minting or network operation.
Store inputs and results in private operator storage, outside the public repo.

## What is checked

The adapter reuses the ordinary estimator's supported-category validation, then
requires explicit `standard` service tier and speed and integer-zero cache
creation/read counters. Unlike an estimate that states assumptions for missing
metadata, this profile refuses missing or null values for those required fields.

Positive cache, server-tool or thinking counts, fallback iterations, unsupported
usage fields, non-text content and unknown response fields fail. `diagnostics`,
`stop_details` and `container` may be absent or null; non-null values require a
different profile. Optional supported usage metadata retains absence and null
exactly. Unknown optional observations are not synthesized as zero. Successful
projection does not prove the provider's schema exposed every billable category.

Supported stopping conditions are preserved with the estimator's generation
status. Truncation and refusal can still contain usage; neither a recognized stop
nor matching counts demonstrates answer quality or transport/billing finality.

The result includes hashes and byte lengths of both supplied files, separate
HTTP-request and message-ID digests, the observed usage metadata and the exact
token projection. It omits response content and raw provider identifiers. A hash
of saved JSON does not claim to be the hash of the original network bytes.

## Using the provider row

With a declared account scope, `provider_row` has the exact shape accepted by
[individual row commitments](KRY_USAGE_LINKS.md). Unknown scope produces `null`;
the usage projection remains inspectable without manufacturing a scoped row.

A prospective integration must separately record an actual displacement decision
and construct its usage row before explicitly calling `prepare_link` and `mint`.
Ordinary API traffic alone does not establish an avoided-model event. Historical
cache-hit receipts must not be rewritten into displacement receipts.

The existing row commitment binds normalized rows. It does **not** bind this
sidecar's raw-response hash or acquisition metadata. Rebuild this projection from
the retained inputs whenever relying on its consistency claim, and compare its
provider row to the row supplied to the linkage assessment. Changing both source
and sidecar can create a new consistent record; independent source acquisition
and an earlier trusted commitment are separate requirements.

`native_projection_consistent` refers only to supplied saved bytes.
`source_truth_authenticated`, `receipt_binding_verified` and
`external_verified_savings` remain false; `actual_charge_usd` remains null.
The existing linkage assessment's `normalization_verified` flag remains false.
No T0 receipt is promoted by using this adapter. Billing completeness, invoices,
credits, settlement, causal savings and customer benefit remain unresolved.
