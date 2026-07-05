# SC2 verification run — implementation independence (JS second verifier + differential fuzz)

**Date:** 2026-07-04 · **Criterion:** SC2 (roadmap §4) — *a second verifier in a different language shows 0 divergences from the Python reference over ≥10⁶ differentially-fuzzed receipts.* · **Result: PASS.**

## Second implementation

`verifiers/js/verify.mjs` — an independent KRY verifier in **JavaScript** (runs on Node ≥ 18 or Deno; zero dependencies beyond `node:crypto`). Written to `SPEC.md` v1.0; verifies both the savings and action profiles.

- **Corpus:** passes **28/28** of the shared conformance corpus (`vectors/`) — every encoding primitive (exact bytes) and every attestation verdict — the same corpus the Python cold-run used for SC1.
- **D3 resolved → JavaScript.** Go and Rust toolchains are not present in this environment; Node 26 + Deno 2.7 are. JS is also the direct path to the Phase-1 static browser verify page (SC6), so the v1 second implementation is JS. A Go/Rust static-binary verifier remains a later option (§8 D3).

### The cross-language canonicalization finding

The **inner chain** (receipt_hash / public_block / chain_hash) is language-neutral *by design*: every economic number is bound through `canon_f64` (IEEE-754 big-endian hex), so a JS verifier reproduces it byte-for-byte trivially. The **outer `attestation_hash`**, however, binds *raw* JSON numbers, and CPython's `json` preserves the int-vs-float distinction (by the presence of a decimal point) that `JSON.parse` discards — so a naive JS re-serialization diverges (`1000.0` → `1000`, `2.5e-08` → `2.5e-8`). The JS verifier solves this with a **number-preserving JSON parser** (it keeps each number's exact source literal and emits it verbatim in canonical output), reproducing CPython byte-for-byte without emulating its float `repr`. *Design note for a future spec revision:* migrating the outer hash to `canon_f64` too would make the whole attestation language-neutral without a custom parser.

## Differential fuzz

`verifiers/diff_fuzz.py` mints real base attestations, applies 1–3 random structural mutations per case (number perturbation, string relabel, reorder/drop/duplicate links, envelope tamper, tier upgrade, type confusion, raw-malformed injection), and compares the verdict of the **Python reference** (`kry_verify.py` / `kry_action_verify.py`) against the **JS verifier** for each case.

```
PYTHONPATH=src python3 verifiers/diff_fuzz.py 1000000
→ differential fuzz: N=1000000  divergences=0
  agree: VALID=167196 INVALID=812804 PARSE_ERROR=20000 CRASH=0
```

**0 divergences over 1,000,000 cases** (seed fixed at 1234 → reproducible). Every VALID/INVALID/PARSE_ERROR verdict agrees; neither verifier crashed on any input.

## Reference bugs found and fixed (differential fuzzing earning its keep)

Getting to 0 divergences surfaced three real robustness gaps — all in `scripts/kry_action_verify.py`, all violations of SPEC §1 ("MUST fail closed… never a crash"):

1. **Non-string `receipt_id` crashed the dedup set** (`[]`/`{}` → `TypeError: unhashable`). Fixed: fail closed (INVALID) on a non-string `receipt_id`; SPEC §4.3 updated to require a string id.
2. **Non-string `receipt_id` (e.g. int `0`) was silently accepted** as VALID. Same fix resolves it.
3. **A truthy non-dict `veracity` (e.g. `1`) crashed** (`att.get("veracity") or {}` → `(1).get(...)`). Fixed with the same `isinstance(dict) else {}` guard the savings verifier already uses — the #26 audit hardened this exact class in `kry_verify.py` but missed the action verifier; fuzzing found the inconsistency.

Full test suite after both fixes: **559 passed, 4 skipped.** The JS verifier was independently hardened to fail closed on the same inputs. Both fixes are in the same commit as this evidence.

## Honest scope

SC2 (a second-language verifier, 0 divergences over ≥10⁶ fuzzed receipts) is met. Remaining Phase-1 items: a WASM/static browser verify page (the rest of SC6), and — if a toolchain lands — a Go/Rust static-binary third implementation. The fuzz seed is fixed for reproducibility; a CI job should run it (a fresh seed each run) as a standing gate.
