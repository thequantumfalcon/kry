# verifiers/ — independent KRY verifiers + differential fuzz

Implementation-independence artifacts for the SC2 gate (roadmap §4). Everything here
verifies against [`../SPEC.md`](../SPEC.md) and the shared corpus [`../vectors/`](../vectors/).

## `js/verify.mjs` — second implementation (JavaScript)

A dependency-free KRY verifier (Node ≥ 18 or Deno) for both attestation profiles,
written to the spec — the "different language" half of SC2.

```bash
node js/verify.mjs ../path/to/attestation.json     # prints VERDICT: VALID|INVALID, exit 0/1
node js/verify.mjs --vectors ../vectors            # run the whole conformance corpus (28/28)
node js/verify.mjs --batch cases.ndjson mult.json  # one verdict per line (used by the fuzzer)
```

It parses with a **number-preserving parser** so it reproduces CPython's outer
`attestation_hash` byte-for-byte (see `docs/evidence/spec_v1_sc2_run.md` for why).

## `diff_fuzz.py` — differential fuzz (Python reference vs JS)

Mutates real minted attestations and compares the verdict of the Python reference
(`scripts/kry_verify.py` / `scripts/kry_action_verify.py`) against `js/verify.mjs`.

```bash
PYTHONPATH=src python3 verifiers/diff_fuzz.py [N]   # default 20000; SC2 bar is 1000000
```

Prints `divergences=0` on success; any divergence is written to `divergences/` as a
case file `{py, js, input}` and exits non-zero. Seed is fixed (1234) for reproducibility.

Latest: **N=1,000,000 → 0 divergences** (`docs/evidence/spec_v1_sc2_run.md`).

## Status

- SC2 met: JS verifier passes the corpus; 10⁶-case fuzz shows 0 divergences.
- Remaining Phase 1: WASM/static browser verify page (SC6); optional Go/Rust static-binary
  third impl once a toolchain is available.
