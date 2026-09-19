# Verifier compatibility decision — 2026-09-19

The owner approved tightening verification after review of commits `67f7adf..3e03130`.
Some documents accepted by one existing verifier will now be rejected. This is an explicit
compatibility correction to KRY-SPEC v1.3, not a new receipt format or hash version.

| Input | Corrected behavior |
|---|---|
| Overflowing float literal such as `1e309`, invalid escape, unescaped control character | PARSE_ERROR before hashing |
| Integer too large for a finite economic quantity | INVALID, without a conversion crash |
| String, boolean, null or container in a required numeric trust summary | INVALID; a present action floor also requires a finite number |
| Savings-shaped document declaring `kry_action_attestation` | Savings verifier refuses; dispatcher applies the action profile |
| Equivalent float spellings (`1e3`, `1000.00`, `1000.0`) | Normalize to `1000.0` for all hashes; integer `1000` remains distinct |
| Hash computed from a noncanonical raw float spelling | INVALID |
| Astral and BMP object keys | Sort by Unicode code point before hashing |
| Incorrect total produced by rounding twice | INVALID; correctly rounded large finite totals are accepted |
| Unknown link version | INVALID; carry its declared chain hash forward and exclude it from numeric, count and tier derivations |

All pre-existing vector documents and expected verdicts are preserved. The manifest gains
27 cases under `vectors/hardening/`; the generator obtains expected verdicts from the corrected
reference. Receipt-level tests additionally build public blocks directly from the spec rather
than calling the minter. Passing this corpus and the sampled tests is bounded evidence, not a
proof of agreement on every possible input.

Producers should continue using the existing attestation builder. Pipelines may reformat a
float without changing its numeric type, but must not turn an integer into a float or vice
versa. Previously accepted malformed documents must be corrected and resealed by their
producer; verification does not repair or grandfather them. Existing published artifacts are
not rewritten.

The stranger CLI now prints `VERDICT: PARSE_ERROR` for JSON parsing failures instead of
`VERDICT: INVALID`. Both outcomes retain exit code 1; file access failures remain `INVALID`.
Consumers matching the verdict text should recognize all three SPEC outcomes.

The artifact boundary separately tightens three token-detail containers to documented
counter objects. Counters must be nonnegative integers, excluding booleans; optional Chat
Completions counters may be null. Free text, unknown counter names, lists and nested objects
are refused before bundle creation. This changes acceptance of unsafe export shapes, not
receipt valuation or the disclosed limits on veracity.
