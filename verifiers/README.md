# verifiers/ — independent KRY verifiers + differential fuzz

Implementation-independence artifacts (SC2) and the stranger-facing browser verify
page (half of SC6). Everything verifies against [`../SPEC.md`](../SPEC.md) and the
shared corpus [`../vectors/`](../vectors/).

## `js/verify.mjs` — second implementation (JavaScript), environment-agnostic

A dependency-free KRY verifier for both attestation profiles, written to the spec —
the "different language" half of SC2. It uses **no** Node or DOM APIs (pure-JS
SHA-256, a number-preserving JSON parser), so the **same file** runs under Node,
Deno, and in a browser. It exports `verdict(text) -> "VALID" | "INVALID" | "PARSE_ERROR"`.

The number-preserving parser is what lets it reproduce CPython's outer
`attestation_hash` byte-for-byte (see `docs/evidence/spec_v1_sc2_run.md`); the inner
chain is language-neutral by design (`canon_f64`).

## `js/cli.mjs` — Node CLI + corpus runner

```bash
node js/cli.mjs ../path/to/attestation.json     # prints VERDICT: VALID|INVALID, exit 0/1
node js/cli.mjs --vectors ../vectors            # run the whole conformance corpus (46/46)
node js/cli.mjs --batch cases.ndjson mult.json  # one verdict per line (used by the fuzzer)
```

## `web/index.html` — static browser verify page (SC6)

A single static page that imports `js/verify.mjs` and verifies a pasted receipt
**entirely client-side** — no server, no upload, auditable by view-source. Host it on
GitHub Pages, or serve locally:

```bash
python3 -m http.server -d verifiers 8000   # then open http://localhost:8000/web/
```

(ES-module imports don't load over `file://`, so it needs a static server or Pages.)

## `diff_fuzz.py` — differential fuzz (Python reference vs JS)

Mutates real minted attestations and compares the verdict of the Python reference
(`scripts/kry_verify.py` / `scripts/kry_action_verify.py`) against `js/verify.mjs`.

```bash
PYTHONPATH=src python3 verifiers/diff_fuzz.py [N]   # default 20000; SC2 bar is 1000000
```

Prints `divergences=0` on success; any divergence is written to `divergences/` and
exits non-zero. Seed is fixed (1234) for reproducibility.

Each case gets 1–3 mutations, then two steps that decide whether it reaches anything:

- **reseal** — the outer `attestation_hash` is a *keyless* self-hash over the
  attestation's own public metadata, so a producer can re-hash any edit. A mutant left
  unsealed is rejected by both verifiers on that one shared comparison and never
  exercises the checks behind it, so half of the mutants that still declare the field
  are re-hashed with the minter's hasher.
- **envelope field deletion** — "key absent" is a different branch from "key present but
  wrong" in both verifiers, and the tamper class only produces the second.

**Latest: N=1,000,000 → 0 divergences** (2026-08-01 run 3, with those two classes in the
mutation space). The intermediate run 2 over the same space showed **2,467** divergences —
all savings-profile disagreements over a *missing* SPEC §3.1/§3.5 envelope key — which were
closed by requiring the absent key on whichever side had been skipping the check. The
2026-07-04 N=1,000,000 → 0 result stands for the space it ran in, which had neither class.
See [`../docs/evidence/spec_v1_sc2_run.md`](../docs/evidence/spec_v1_sc2_run.md) for all
three sealed runs, the classification and the minimal reproducers.

## Status

- **SC2 met at the expanded mutation space**: the JS verifier passes the corpus 46/46 and the
  10⁶-case fuzz shows 0 divergences with reseal and envelope field-deletion both in the
  mutation space. The three run-2 root causes — an absent `veracity.by_tier`, an absent
  `event_type_counts` (`verify.mjs` had no such check at all) and an absent
  `veracity.veracity_floor` — are closed in both verifiers under one SPEC §3.5 rule: every
  required envelope key must be PRESENT, and an absent key is INVALID rather than a skipped
  check. Five vectors pin the class. Scope: this is agreement between two implementations
  written in this repository, not evidence about a third-party implementer working from
  `SPEC.md` alone.
- **SC6 (browser half) met**: `web/index.html` verifies client-side.
- Remaining: the `pipx run <dist> verify` CLI path + a formal timed cold-start transcript;
  an optional Go/Rust static-binary third impl once a toolchain is available.
