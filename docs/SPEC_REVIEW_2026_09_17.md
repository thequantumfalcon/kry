# Spec review by independent reimplementation — 2026-09-17

A fresh verifier was written from `SPEC.md` and the `vectors/` corpus alone, with no access to
`src/kry/`, `verifiers/`, `tests/` or `scripts/`. The exercise tests one thing the corpus cannot test
by itself: **whether the written spec is complete enough that a stranger's implementation agrees with
ours.**

**Outcome.** It matched all 36 corpus verdicts on its first run, and still found **25 places where
the text left a choice**. The corpus corrected none of them — every gap below is a point where two
conformant verifiers can disagree while both pass the corpus.

That is the headline: a clean corpus run is not evidence of an unambiguous spec.

## Fixed in this change

| # | Section | Defect | Fix |
|---|---------|--------|-----|
| G2 | §3.5 | "ANCHORED tiers are all tiers except `self_reported`" lets an **invented** tier string claim `veracity_floor: 1.0`. The reference verifiers always counted only the enumerated set; the text did not say so, and the independent implementation took the permissive reading. | §3.5 now states the enumerated set explicitly. Vector `unknown_tier_claims_anchored`. |
| G3 | §3.4.1 | "A link that omits its inputs is legacy and honestly uncheckable — skip" was unbounded, so a **current-version** link could omit `tokens_saved`, skip the rate and multiplier checks, and mint an arbitrary amount. This one was real in the implementations too, not only in the text. | Exemption bounded at `hash_version` 4, where the economic block became hash-bound. Fixed in all three verifiers. Vector `magnitude_inputs_omitted`. |

| G10 | §3.5 | `round(x, 4)` pinned no rounding mode — and the two implementations had already diverged: the JS verifier rounded by scaling (`Math.round(x * 1e4) / 1e4`), which injects float error, so **~4% of five-decimal magnitudes** rounded differently from the Python reference (e.g. `2122.59595` → `2122.5959` vs `2122.596`). Either verifier would have rejected a document the other accepted. The corpus never caught it, and neither did the differential fuzzer. | JS now rounds the exact decimal expansion half-even; verified against the reference on 91,997 values including dyadic ties, 0 divergences. §3.5 states the rule and warns against rounding by scaling. |

## Clarified in the spec text (no behaviour change)

Each of these was checked against the code first, and the text now states what all three verifiers
already do. No verifier was modified and no verdict moves.

| # | Section | What the text now says | Evidence in the code |
|---|---------|------------------------|----------------------|
| G1 | §3 | A document whose `kind` is `kry_action_attestation` is verified under §4, everything else under §3 — and dispatch is on the document, not on the wrapper a vector file adds. | The JS verifier dispatches on exactly that field; the standalone Python verifier implements §3 only. |
| G5 | §3.3/§3.4/§3.6 | The pre-v4 rules are **live, not vestigial**: `hash_version <= 3`, or an absent field (meaning `1`), is legacy and verified with the §3.3 formula, restricted by step 4 and §3.4.1. | All three verifiers reject only a version **above** 7, and each carries a comment saying legacy support is deliberate. The earlier reading of these rules as unreachable was wrong: the "fail closed" sentence is conditional on a verifier that implements only 4..7. |
| G6 | §3.4 | An unrecognized version makes the link INVALID, its **declared** `chain_hash` becomes `prev`, and the link is excluded from `total_kry`, `event_type_counts` and the tier sums. | The reference sets `prev = chain_hash` and skips the link before any derivation. |
| G7 | §3.8 | The anchor resolves to the **first** link whose `seq` equals `count`; `seq` is bound by no hash and need not be unique. | The reference takes the first match; a last-match verifier returns the opposite verdict on a duplicate-`seq` document. |
| G8 | §3.4 | Duplicate hash-bound `receipt_id`s are INVALID for **every** verifier, not only one claiming the overlay profile. | The check sits in the main per-link loop, outside the profile. |
| G9 | §3.7 | `position` is the link's **index in `links`**, not its `seq`. | The reference enumerates links and stores the index. |

## Open, verdict-affecting

| # | Section | What is unsettled |
|---|---------|-------------------|
| G4 | §3.3 | The `hash_version == 4` branch hashes "the raw JSON numbers", which is host-language dependent: `1000` and `1000.0` produce different chain hashes, and a JS verifier cannot verify a Python-minted v4 chain. **The corpus is 100% v7**, so the v4–v6 branches are entirely untested. |

Fifteen further items are lower-risk (shape and strictness questions: what `by_tier` key a non-string
tier takes, whether `3.0` satisfies "integer", whether unknown envelope keys are allowed, what an
action `chain_tip` means with zero links, and so on).

## Found afterwards, by building the chains the corpus never contains

The corpus has no pre-v7 chain, so nothing checked whether the two verifiers agree on one. Building
valid v4, v5 and v6 chains with the reference's own block builder and running both verifiers gave:

| Document | Python | JS | JS's reasons |
|---|---|---|---|
| v4, v5, v6, v7 with canonical literals | VALID | VALID | — |
| v4 with `1e3` in place of `1000.0` | VALID | INVALID | chain broken, then `attestation_hash` mismatch |
| v5 with `1e3` in place of `1000.0` | VALID | INVALID | `attestation_hash` mismatch only |

Two distinct things, and the second is wider than the v4 question that prompted the test.

1. **v4's chain hash is literal-dependent, and v5 fixes it.** The v5 chain survives the re-spelling —
   `canon_f64` binds the value, not the text — while v4's breaks. That is the documented reason v5
   exists, now demonstrated rather than assumed.
2. **The outer `attestation_hash` is literal-dependent at every version, v7 included.** `canon` is
   defined as `json.dumps` over the **parsed value**, which a Python verifier implements directly. A
   verifier in a language without separate integer and float types cannot: after parsing, `1000.0` and
   `1000` are the same value, so re-serializing would render Python's `1000.0` as `1000` and reject
   every Python-minted attestation. The JS verifier therefore preserves the literal it received — the
   only workable choice — and the two agree on any document whose literals are already canonical, which
   includes everything kry mints and the whole corpus.

Neither implementation can adopt the other's rule without breaking real documents, so §2.1 now states
the missing requirement: **the wire form must already be canonical**, and a verifier MAY reject input
spelled otherwise. The divergence produces a false INVALID, never a false VALID, so it cannot inflate a
claim — but it is exactly the cross-implementation agreement the two verifiers exist to demonstrate.

**Still open here:** a Python verifier accepts non-canonical literals where the JS one rejects them.
Making Python reject them too would give identical verdicts on every input; it would also start
rejecting hand-edited or third-party-formatted documents it accepts today. That is a behaviour change,
so it is left as a decision rather than folded into a documentation change.

## Corpus blind spots this exposed

- **Every vector is `hash_version` 7.** Four version branches the spec makes mandatory are untested.
- **No vector mints around a bad value** — the adversarial vectors tamper an existing document, so the
  chain hash fires first and the semantic rule underneath is never exercised. That is why G2 and G3
  survived 46 vectors.
- **`seq` is unconstrained and unbound**, and one corpus document already contains a duplicate.

## Suggested order

1. G1 and G10 are one sentence each and remove whole classes of disagreement.
2. G7 and G9 need one definition each (`seq` uniqueness or an explicit tie-break; "position" = index).
3. G4 deserves either v4–v6 vectors or an explicit statement that pre-v7 minting is out of scope.
4. G5, G6 and G8 are consistency edits.
