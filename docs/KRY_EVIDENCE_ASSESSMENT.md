# Optional supplied-export evidence assessment

`scripts/kry_evidence_assess.py` produces a separate assessment of a public
attestation, its T1 reconciliation manifest and a supplied usage export. It keeps
ordinary reconciliation diagnostics separate from a narrower exact token-pair
consistency result. It does not modify an artifact, mint a receipt, advance a
readiness grade, complete a review or authorize an external financial claim.

This optional feature adds a command. Aggregate and tolerant reconciliation
remain available. The separate plain-log correction keeps imported estimates
self-reported and displays receipt tiers separately; this assessment does not
replace those checks. Legacy eligibility does not establish financial truth.

## Run and verify an assessment

```sh
python3 scripts/kry_evidence_assess.py \
  --attestation attestation.json \
  --t1-manifest t1_manifest.json \
  --provider-export provider.json \
  --provider-export-manifest provider_export_manifest.json > assessment.json

python3 scripts/kry_evidence_assess.py \
  --verify assessment.json \
  --attestation attestation.json \
  --t1-manifest t1_manifest.json \
  --provider-export provider.json \
  --provider-export-manifest provider_export_manifest.json
```

Verification rebuilds the result from the explicit input paths and options on
that command. It never follows paths or configuration found in the saved result.
Supply the same `--mode`, `--tolerance`, `--tolerance-pct`, `--since` and `--until`
options used for assessment. `--help` does not require any data files.

Exit 0 means the assessment completed, or the stored assessment matched its
rebuild. **It does not mean exact support was established.** Inspect
`exact_assurance.supported` and its scope. Exit 1 means the saved assessment did
not match. Exit 2 means unreadable/invalid JSON, invalid command configuration or
an input-size failure. Semantic evidence failures are useful diagnoses: they
normally produce an assessment with exact support false rather than a command
error. An unavailable or invalid analysis is explicitly labeled.

The Python entry points are `assess_evidence(attestation, **options)` and
`verify_assessment(saved_assessment, **explicit_inputs_and_options)`.

## Interpret the two results separately

`analysis` retains the current reconciler's numeric diagnostics and verdict. It
can show aggregate reconciliation, nonzero tolerance or legacy uncached-prompt
matches. Those can help investigate records without establishing the narrower
claim. Legacy prose is omitted and unmatched receipt IDs are hashed; the numbers
and matching policy are unchanged. The underlying reconciler remains available
with its original output. This command performs no network access or fetching.

`exact_assurance` uses the fixed `supplied-export-t1-exact/v1` profile. Support
requires a valid savings attestation; a positive raw T1 amount; v7 T1 billing-field
binding; a complete manifest bound to every T1 identity; and a supplied export
with valid declared provenance. Per-request integer-zero tolerance, explicit
unambiguous prompt/output counters, full exact matching and zero legacy-cache
fallback are required. Filters may narrow diagnostic analysis, but cannot hide
an attestation T1 link from exact support. A rounded agreement fraction of 1.0
cannot substitute for the exact row counts and unmatched list.

An input-only Anthropic prompt count cannot silently substitute for the complete
prompt when native cache-read/cache-write counts are present. The complete prompt
sum can be checked. Missing counters are not affirmative zero evidence. Competing
prompt/output aliases or inclusive prompt aliases mixed with separate native cache
counts cannot support the fixed profile. This is token-pair consistency; it does
not validate a billing-category price model or every provider's usage semantics. When a row
contains nested `usage`, that object is selected, as in the existing reconciler.
Token-like outer metadata is not cross-validated against the selected counters.

Requested aggregate mode or nonzero per-request tolerance deliberately leaves
exact support false. The command does not silently run a second policy. Extra
provider rows may remain unused: the supported scope covers the attestation T1
contribution, not all account activity. A zero-only T1 set cannot promote positive
T0. Positive raw amounts are checked before display rounding.

For a constructed attestation containing 240 T1 KRY and 500 T0 KRY, a complete
exact match can support the **attestation T1 contribution** of 240. It does not
support the T0 remainder or make the combined 740 financially verified. Both raw
and displayed attestation amounts are retained. No usage report is consumed, so
report-component and per-row binding remain unestablished. This is an explicit
scope limit, not a closure of that gap.

## Integrity and source limits

Each explicit file is read once, capped at 16 MiB, decoded as UTF-8 and parsed
with finite JSON numbers. Duplicate member names use the last member, consistent
with the existing specification. The same captured bytes are hashed and supplied
to existing helpers through temporary snapshots. This avoids evaluating a later
reread against an earlier hash; the original files can still change afterward.

Input hashes and the assessment hash detect changes relative to supplied inputs.
Rebuilding also checks the complete claim, configuration and fixed limitations,
including when someone rewrites the stored self-hash. `tool_sha256` lists selected
local assessment/helper source files; it is not a complete dependency inventory,
a signed software identity or an execution attestation. No runtime timestamp is
included in the result. Provenance dates are checked by the existing helper at
the time of assessment, so an invalid future-dated declaration can require a new
assessment later.

A fabricated but internally consistent attestation/export/declaration set can
pass these local checks. Hashes and a `non_synthetic` declaration do not prove
provider authenticity. The supplied provenance is checked for consistency and
required fields; its assertions are not authenticated. Reuse of token counts does
not prove unique request identity. Neither a saved `external_verified_savings`
flag nor this command's success authorizes a new financial claim.

Every result includes these limitations:

- Supplied export bytes are not authenticated provider records.
- Token-pair matching is not a unique request-ID join.
- Report-component and per-row binding to the attestation are not established.
- Actual account charges, invoice settlement and retained cash are not established.
- Causal savings, output correctness and customer benefit are not established.
- Review identity, independence and completion are not assessed or authenticated.
- The attestation T0 remainder is outside the exact attestation T1 scope.
- Legacy artifact eligibility does not authorize this assessment or a financial claim.

The constructed tests establish local behavior only. Actual provider, customer,
review and billing evidence remain separate obligations under
[the claims boundary](CLAIMS_BOUNDARY.md).
