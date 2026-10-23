# Historical research-grade run and retained evidence (2026-06-10)

The original run reported `research_grade`. This page separates that historical
result from what can be reproduced from the public files today. The retained
bundle and its original notes remain unchanged under
[`docs/evidence/research_grade/`](evidence/research_grade/).

## What the retained files establish

- There are 52 receipt records and 52 provider records, each with 52 unique
  request IDs. All IDs match one-to-one; all paired token counts match.
- The records contain **0 KRY minted and $0 total provider cost**. This is
  free-tier self-traffic, not a demonstration of paid customer savings.
- All retained receipts use `hash_version` 2. Their hashes bind neither the
  metered token counts nor request IDs in `detail`. Hash-chain verification
  alone therefore cannot establish those fields' integrity.
- The current pinning test protects the retained files against subsequent edits.
  It cannot authenticate their original acquisition or the provider's accounting.

The original notes describe the host system's acceptance gate confirming these
receipts within its TTL, following an earlier 18-call run that expired. That
operational account is historical context. Its private run log, all-time ledger
and explanations for missing older provider records are not independently
established by this public bundle. The current offline check does not prove that
an acceptance decision was correct or that a payment settled.

## Reproduce the current comparison

No API key or network is required:

```bash
python3 scripts/kry_research_grade.py docs/evidence/research_grade/research_grade_mintlog.jsonl \
 --provider-export docs/evidence/research_grade/or_provider_export.json
```

The current per-request driver requires a one-to-one request-ID join, matching
token counts within the configured tolerance and a `RECONCILED` verdict. The
unchanged bundle meets those file-consistency checks. Rewritten IDs or removal
of ten records now fail the grade. The former token-only algorithm could accept
both mutations; its historical result must not be read as having checked IDs.

The generic readiness rubric retains its ≥ 0.80 threshold. The raw fraction
remains a diagnostic, but discrepant evidence is ineligible for advancing the
driver or artifact grade. Token-count-only matching of two IDless inputs remains
a labeled legacy consistency check and cannot advance that grade.

## Receipt format and provenance limits

Current minting uses v7 and binds metered tokens. It still does not hash-bind the
request ID carried in `detail`. A public T1 manifest may contain a SHA-256 request
reference for comparison without exposing the raw ID. That digest is neither
anonymization nor an authenticated receipt-to-provider binding; callers can
supply a different reference unless independent evidence constrains it.

An offline match therefore means the supplied files agree. Provider authenticity,
correct acceptance decisions, nonzero avoided cost, independent acceptance and
financial settlement need their own evidence. `research_grade` is a conditional
rubric result, not certification of those claims. See
[`CLAIMS_BOUNDARY.md`](CLAIMS_BOUNDARY.md) and [`KRY_READINESS.md`](KRY_READINESS.md).
