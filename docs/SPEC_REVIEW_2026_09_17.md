# Spec review by independent reimplementation — 2026-09-17

A fresh verifier was written from `SPEC.md` and the `vectors/` corpus alone, with no access to
`src/kry/`, `verifiers/`, `tests/` or `scripts/`. The exercise tests one thing the corpus cannot test
by itself: **whether the written spec is complete enough that a stranger's implementation agrees with
ours.**

**Outcome.** It matched all 36 corpus verdicts on its first run, and still found **25 places where
the text left a choice**. The corpus corrected none of them — every gap below is a point where two
conformant verifiers can disagree while both pass the corpus.

That is the headline: a clean corpus run is not evidence of an unambiguous spec.

**Correction, 2026-09-19.** Later receipt-level and raw-JSON tests reproduced false accepts,
false rejects and a privacy bypass despite green CI. The original agreement claims below were
too broad. The approved corrections and compatibility effects are recorded in
[the compatibility decision](VERIFIER_COMPATIBILITY_2026_09_19.md).

## Fixed in the original change

| # | Section | Defect | Fix |
|---|---------|--------|-----|
| G2 | §3.5 | "ANCHORED tiers are all tiers except `self_reported`" lets an **invented** tier string claim `veracity_floor: 1.0`. The reference verifiers always counted only the enumerated set; the text did not say so, and the independent implementation took the permissive reading. | §3.5 now states the enumerated set explicitly. Vector `unknown_tier_claims_anchored`. |
| G3 | §3.4.1 | "A link that omits its inputs is legacy and honestly uncheckable — skip" was unbounded, so a **current-version** link could omit `tokens_saved`, skip the rate and multiplier checks, and mint an arbitrary amount. This one was real in the implementations too, not only in the text. | Exemption bounded at `hash_version` 4, where the economic block became hash-bound. Fixed in all three verifiers. Vector `magnitude_inputs_omitted`. |

| G10 | §3.5 | `round(x, 4)` pinned no rounding mode — and the two implementations had already diverged: the JS verifier rounded by scaling (`Math.round(x * 1e4) / 1e4`), which injects float error, so **~4% of five-decimal magnitudes** rounded differently from the Python reference (e.g. `2122.59595` → `2122.5959` vs `2122.596`). Either verifier would have rejected a document the other accepted. The corpus never caught it, and neither did the differential fuzzer. | The initial repair passed a reported 91,997-value sample but still double-rounded large values and rejected values at least 1e21. The 2026-09-19 repair converts the rounded decimal once; new receipt vectors cover large magnitudes and genuine four- and six-place ties. |

## Rules clarified in the original spec edit

The original edit claimed these were already shared behavior. Subsequent testing disproved that
for G1 (the Python savings verifiers accepted an action declaration) and G6 (JS still counted
unknown-version links in derivations). Both implementations now follow those stated rules.

| # | Section | What the text now says | Evidence in the code |
|---|---------|------------------------|----------------------|
| G1 | §3 | A document whose `kind` is `kry_action_attestation` is verified under §4, everything else under §3 — and dispatch is on the document, not on the wrapper a vector file adds. | The JS verifier dispatches on exactly that field; the standalone Python verifier implements §3 only. |
| G5 | §3.3/§3.4/§3.6 | The pre-v4 rules are **live, not vestigial**: `hash_version <= 3`, or an absent field (meaning `1`), is legacy and verified with the §3.3 formula, restricted by step 4 and §3.4.1. | All three verifiers reject only a version **above** 7, and each carries a comment saying legacy support is deliberate. The earlier reading of these rules as unreachable was wrong: the "fail closed" sentence is conditional on a verifier that implements only 4..7. |
| G6 | §3.4 | An unrecognized version makes the link INVALID, its **declared** `chain_hash` becomes `prev`, and the link is excluded from `total_kry`, `event_type_counts` and the tier sums. | The reference sets `prev = chain_hash` and skips the link before any derivation. |
| G7 | §3.8 | The anchor resolves to the **first** link whose `seq` equals `count`; `seq` is bound by no hash and need not be unique. | The reference takes the first match; a last-match verifier returns the opposite verdict on a duplicate-`seq` document. |
| G8 | §3.4 | Duplicate hash-bound `receipt_id`s are INVALID for **every** verifier, not only one claiming the overlay profile. | The check sits in the main per-link loop, outside the profile. |
| G9 | §3.7 | `position` is the link's **index in `links`**, not its `seq`. | The reference enumerates links and stores the index. |

## Canonicalization findings and corrected interpretation

The original corpus contained no valid pre-v7 chain. Additional canonical v4, v5 and v6 chains
were accepted by both implementations, disproving the initial assertion that JS could not
verify Python-minted v4 chains. Replacing `1000.0` by `1e3` exposed a different problem:
Python normalized the parsed float while JS hashed its raw spelling. The v4 chain hash and
all versions' outer hashes could therefore differ.

Preserving literal spelling was not the only possible implementation. Retaining the integer
versus float distinction and then normalizing the parsed value reproduces the Python preimage.
That is now the rule in SPEC §2.1. Equivalent float spellings remain accepted; hashing raw
noncanonical spellings is rejected. The original claim that divergence could only produce a
false INVALID was wrong: resealing over the raw spelling made JS accept a hash Python rejected.

Canonical literals alone also did not guarantee agreement: Unicode key ordering, overflow,
nonnumeric trust summaries and large rounded totals exposed further differences. New vectors
and receipt-level tests cover these reproduced failures. No finite sample establishes universal
agreement. Remaining spec questions outside these approved fixes have not been resolved here.

## Corpus blind spots this exposed

- **There were no valid pre-v7 savings vectors.** The correction adds valid v4, v5 and v6 chains; legacy branches still need separately scoped coverage.
- **Mutation coverage is limited.** Some adversarial vectors already recomputed chain hashes;
  claiming none did was incorrect. The original fuzzer did not construct correctly chained large
  economic values or malformed raw strings, so agreement in its mutation stream missed these cases.
- **`seq` is unconstrained and unbound**, and one corpus document already contains a duplicate.
