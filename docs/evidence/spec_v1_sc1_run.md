# SC1 verification run — KRY-SPEC v1.0 spec sufficiency

**Date:** 2026-07-04 · **Criterion:** SC1 (roadmap §4) — *an implementer who has never read the Python passes all vectors, given only `SPEC.md` + `vectors/`.* · **Result: PASS (100%).**

## Method

Two independent, fresh-context cold implementers (isolated Claude sessions) were each given a hard isolation contract: **read only `/SPEC.md` and `/vectors/**`; never read `src/`, `scripts/`, or `tests/`.** Each wrote a from-scratch stdlib-only Python verifier and ran it over the entire corpus (`vectors/manifest.json`). The corpus itself is generated from the reference implementation by `vectors/generate.py`, so every `expected` verdict / hash / byte-string is ground truth that cannot drift from the code.

Conformance bar: reproduce every primitive `expected_hex` (`canon_f64`) and `expected_bytes` (canonical JSON) exactly, and match every attestation vector's `expected.verdict` (`VALID` / `INVALID` / `PARSE_ERROR`). Reason strings were not required to match.

## Results

| Run | Corpus | Passed | Spec gaps found |
|---|---|---|---|
| 1 (initial spec) | 17 vectors | **17 / 17** | 1 real: §3.4.1 did not enumerate the published price-multiplier set (implementer embedded `{1.0}`, which passes the corpus but would wrongly reject other legitimate multipliers). Plus minor reason-string ambiguities. |
| 2 (after fix) | 18 vectors | **18 / 18** | multiplier gap **closed**; remaining items are verdict-neutral edge cases outside v1.0 scope (see below). |

Between runs, the one substantive gap was closed by adding `vectors/primitives/legal_multipliers.json` (the authoritative published-multiplier set, generated from the reference price table) and pointing §3.4.1 at it as normative; plus three prose tightenings (chain `prev` = the *recomputed* value; unknown action tier = non-anchored; fail-closed on unknown `hash_version`).

## Independence quality

- Run 2's implementer **declined to read `vectors/generate.py`** even though the isolation wording technically permitted "any file under vectors/", recognizing that the generator is the reference and reading it would defeat the test. This strengthens the independence claim.
- Both implementers reproduced the byte-exact `canon_f64` and canonical-JSON primitives *before* touching any attestation vector, so the hash-construction agreement is not a coincidence of the higher-level verdicts.

## Remaining ambiguities (recorded, verdict-neutral, out of v1.0 scope)

None changed any vector's verdict. All concern behavior on inputs the v1.0 corpus does not exercise: boolean-in-a-numeric-field type confusion; `hash_version` 4/5 legacy blocks; an unrecognized (non-standard) tier string's severity; and short-circuit-vs-accumulate ordering of the reasons list. The promotion-overlay / published-anchor semantics are deliberately deferred (SPEC §3.7, Annex C) with their own future corpus. These are candidates for a spec v1.1 with additional worked examples.

## Artifacts

- Spec: `SPEC.md` (v1.0)
- Corpus + generator: `vectors/` (`generate.py`, `manifest.json`, `primitives/`, `savings/`, `action/`)
- Cold verifiers (scratch, not committed): `cold_verifier.py`, `cold_verifier2.py` — each an independent from-scratch implementation passing 100%.

## Honest scope

SC1 (spec *sufficiency* — a stranger can implement a verifier from the doc) is met. This is **not** SC2 (implementation *independence* — a second verifier in a **different language**, 0 divergences over ≥10⁶ differentially-fuzzed receipts), which remains Phase 1. Both cold implementers here wrote Python; the next step is a Go/Rust verifier + a differential fuzz harness against the reference.
