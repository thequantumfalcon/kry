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
node js/cli.mjs --vectors ../vectors            # run the whole conformance corpus (28/28)
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
exits non-zero. Seed is fixed (1234) for reproducibility. Latest: **N=1,000,000 → 0
divergences**.

## Status

- **SC2 met**: JS verifier passes the corpus; 10⁶-case fuzz shows 0 divergences.
- **SC6 (browser half) met**: `web/index.html` verifies client-side.
- Remaining: the `pipx run <dist> verify` CLI path + a formal timed cold-start transcript;
  an optional Go/Rust static-binary third impl once a toolchain is available.
