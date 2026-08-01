# SC2 verification run — implementation independence (JS second verifier + differential fuzz)

**Criterion:** SC2 (roadmap §4) — *a second verifier in a different language shows 0 divergences from the Python reference over ≥10⁶ differentially-fuzzed receipts.*

**2026-07-04 run: PASS within the mutation space then in use.**
**2026-08-01 re-run, mutation space expanded with reseal + envelope field deletion: 2,467 divergences over 1,000,000 cases. Three root causes, all in how the two implementations handled *missing* envelope data.**
**2026-08-01 after the fixes (run 3, same expanded space): 0 divergences over 1,000,000 cases. SC2 met at the expanded mutation space.**

## Second implementation

`verifiers/js/verify.mjs` — an independent KRY verifier in **JavaScript** (runs on Node ≥ 18 or Deno; zero dependencies beyond `node:crypto`). Written to `SPEC.md` v1.0; verifies both the savings and action profiles.

- **Corpus:** passes the shared conformance corpus (`vectors/`) — every encoding primitive (exact bytes) and every attestation verdict — the same corpus the Python cold-run used for SC1. **28/28** when this file was first written (2026-07-04); re-measured **46/46** on 2026-08-01 (`node verifiers/js/cli.mjs --vectors vectors`) after the corpus grew.
- **D3 resolved → JavaScript.** Go and Rust toolchains are not present in this environment; Node 26 + Deno 2.7 are. JS is also the direct path to the Phase-1 static browser verify page (SC6), so the v1 second implementation is JS. A Go/Rust static-binary verifier remains a later option (§8 D3).

### The cross-language canonicalization finding

The **inner chain** (receipt_hash / public_block / chain_hash) is language-neutral *by design*: every economic number is bound through `canon_f64` (IEEE-754 big-endian hex), so a JS verifier reproduces it byte-for-byte trivially. The **outer `attestation_hash`**, however, binds *raw* JSON numbers, and CPython's `json` preserves the int-vs-float distinction (by the presence of a decimal point) that `JSON.parse` discards — so a naive JS re-serialization diverges (`1000.0` → `1000`, `2.5e-08` → `2.5e-8`). The JS verifier solves this with a **number-preserving JSON parser** (it keeps each number's exact source literal and emits it verbatim in canonical output), reproducing CPython byte-for-byte without emulating its float `repr`. *Design note for a future spec revision:* migrating the outer hash to `canon_f64` too would make the whole attestation language-neutral without a custom parser.

---

## Run 1 — 2026-07-04, original mutation space

`verifiers/diff_fuzz.py` mints real base attestations, applies 1–3 random structural mutations per case, and compares the verdict of the **Python reference** (`kry_verify.py` / `kry_action_verify.py`) against the **JS verifier** for each case.

Mutation classes in this run: number perturbation, string relabel, reorder/drop/duplicate links, envelope tamper (value replaced in place), tier upgrade, type confusion, raw-malformed injection.

```text
PYTHONPATH=src python3 verifiers/diff_fuzz.py 1000000
→ differential fuzz: N=1000000  divergences=0
  agree: VALID=167196 INVALID=812804 PARSE_ERROR=20000 CRASH=0
```

**0 divergences over 1,000,000 cases** (seed fixed at 1234 → reproducible). Every VALID/INVALID/PARSE_ERROR verdict agreed; neither verifier crashed on any input.

### Reference bugs this run found and fixed

Getting to 0 divergences surfaced three real robustness gaps — all in `scripts/kry_action_verify.py`, all violations of SPEC §1 ("MUST fail closed… never a crash"):

1. **Non-string `receipt_id` crashed the dedup set** (`[]`/`{}` → `TypeError: unhashable`). Fixed: fail closed (INVALID) on a non-string `receipt_id`; SPEC §4.3 updated to require a string id.
2. **Non-string `receipt_id` (e.g. int `0`) was silently accepted** as VALID. Same fix resolves it.
3. **A truthy non-dict `veracity` (e.g. `1`) crashed** (`att.get("veracity") or {}` → `(1).get(...)`). Fixed with the same `isinstance(dict) else {}` guard the savings verifier already uses.

---

## Run 2 — 2026-08-01, expanded mutation space

### What changed in the harness, and why

Run 1's mutation space had a structural blind spot. The outer `attestation_hash` is a **keyless self-hash** over the attestation's own public metadata — there is no secret in it, so a producer can re-hash any edit and the result still parses. Run 1's `mutate()` never re-hashed, so almost every mutant carried a stale outer hash; **both** verifiers then rejected on that one shared comparison, and the envelope / veracity / chain checks *behind* it were never what decided the verdict. Run 1 also never **deleted** a field: its envelope class only replaced a value in place, so "key absent" — a distinct branch from "key present but wrong" in both verifiers — was unreachable.

Two mutation classes were added (all Run-1 classes kept):

- **reseal** — after mutating, recompute `attestation_hash` with the minter's hasher (`kry_attest._attestation_hash`), on a 50% coin flip and only when the mutant still declares the field.
- **envelope field deletion** — delete a top-level SPEC §3.1/§4.2 envelope key, or (30% of the time) a key inside `veracity`.

Measured reach, same seed either way:

| over 20,000 savings mutants | Run 1 space | Run 2 space |
| --- | --- | --- |
| mutants whose Python verdict carries an `attestation_hash` error | 17,370 (86.9%) | 8,946 (44.7%) |
| mutants with a top-level envelope key **absent** | 0 (0.0%) | 854 (4.3%) |
| mutants with a `veracity` sub-key **absent** | 0 (0.0%) | 805 (4.0%) |

### Seal

Raw stdout of the run, captured before any analysis:

```text
sha256(raw run output) = 6919cf734acb125fa2fa25f549701ad548fe6c51ff2eec33a32c3b1a866a2ca4
command               = PYTHONPATH=src python3 verifiers/diff_fuzz.py 1000000
seed                  = 1234 (fixed in diff_fuzz.py)
python                = CPython 3.14.6   node = v26.5.0   platform = darwin 25.5.0
process exit code     = 1
```

The run was repeated at the end of the session and the second stdout is **byte-identical** to the sealed one (same sha256), so the figures below are not an artifact of a tree that moved mid-run.

### Transcription

Literal figures from the sealed output, the recorded divergence files and the replay classification below. No verdict attached in this section.

```text
differential fuzz: N=1000000  divergences=2467
  agree: VALID=166112 INVALID=811421 PARSE_ERROR=20000 CRASH=0
```

- N = 1,000,000
- divergences = 2,467
- agreeing verdicts = 997,533 (VALID 166,112 · INVALID 811,421 · PARSE_ERROR 20,000 · CRASH 0)
- divergence cases written to `verifiers/divergences/` = 200 (the harness caps recording at 200)
- mutation classes exercised = number perturbation · string relabel · reorder/drop · envelope tamper · link duplication · tier upgrade / metered fiddling · type confusion · **envelope field deletion** · server_evidence toggle · **reseal** · raw-malformed injection (1 in 50 cases)
- re-running `diff_fuzz.py 20000` twice gave `divergences=54` both times

The harness records only the first 200 divergent inputs, so the same stream was replayed
by a separate script (same seed, same chunking; it reproduces `divergences=2467` exactly)
to classify **all** of them by which envelope key the input is missing:

```text
sha256(classification output) = 7015507d612c2236bac8c56382628cb45e0bb6461485a7e79b3b80bee1283a7d
replayed N=1000000  divergences=2467
    1076  profile=savings  py=VALID js=INVALID  no by_tier + no veracity_floor
     666  profile=savings  py=VALID js=INVALID  no by_tier
     519  profile=savings  py=INVALID js=VALID  no event_type_counts
     202  profile=savings  py=INVALID js=VALID  no veracity_floor
       4  profile=savings  py=INVALID js=VALID  no event_type_counts + no veracity_floor
```

All 2,467 are savings-profile; the action profile produced none. No residual "other" bucket.

The 200 the harness did record carry the two verifiers' own reason strings:

| n | Python | JS | Python's reasons | JS's reasons | shape of the input |
| --- | --- | --- | --- | --- | --- |
| 86 | VALID | INVALID | (none) | `veracity.by_tier missing`, `anchored_kry mismatch`, `self_reported_kry mismatch` | `veracity` replaced by `{}` |
| 53 | VALID | INVALID | (none) | `veracity.by_tier missing` | `veracity.by_tier` absent |
| 52 | INVALID | VALID | `event_type_counts must be a JSON object` | (none) | `event_type_counts` absent |
| 8 | INVALID | VALID | `veracity_floor mismatch` | (none) | `veracity.veracity_floor` absent |
| 1 | INVALID | VALID | both of the above | (none) | both keys absent |

No recorded divergence names `attestation_hash` on either side.

### Minimal reproducers

Each is a real minted 2-link savings attestation with exactly **one key deleted** and the outer hash resealed; the unmodified base verifies VALID on both sides.

| id | edit | Python | JS |
| --- | --- | --- | --- |
| D1 | `del att["veracity"]["by_tier"]` | **VALID** (no errors) | **INVALID** — `veracity.by_tier missing` |
| D2 | `del att["event_type_counts"]` | **INVALID** — `event_type_counts must be a JSON object` | **VALID** |
| D3 | `del att["veracity"]["veracity_floor"]` | **INVALID** — `veracity_floor mismatch: declared None, links imply 0.0421` | **VALID** |
| control | (none) | VALID | VALID |

```python
# reproduce: from a base built by diff_fuzz.build_savings(...)
att = json.loads(json.dumps(BASE))
del att["veracity"]["by_tier"]                      # D1  (D2/D3: the other two keys)
att["attestation_hash"] = kry_attest._attestation_hash(att)   # reseal
kry_verify.verify_attestation(att)                  # → (True, [])
# node verifiers/js/cli.mjs --batch <one-line ndjson> vectors/primitives/legal_multipliers.json
#                                                    → INVALID
```

### Interpretation

Everything below this line is interpretation, not transcription.

The clusters reduce to three distinct disagreements about **missing** envelope data, and each is a substantive read of SPEC §3.1/§3.5 rather than a coding slip:

1. **D1 — no `veracity.by_tier` (key absent, or `veracity` itself `{}`).** `kry_verify.verify_attestation` gates its entire trust-surface re-derivation on `v.get("by_tier") is not None`, so an attestation that omits `by_tier` gets *no* trust-surface check at all and passes. `verify.mjs` treats it as a §3.1/§3.5 completeness failure. The two implementations disagree on which is right; the Python behaviour is the permissive one, and it is the one that lets a producer drop the tier split and keep a VALID verdict. Note the `veracity = {}` route (86 of the 139 such cases the harness recorded) was already producible by Run 1's envelope-tamper class — it was the **reseal**, not the deletion class, that stopped the stale outer hash from masking it. This is the largest cluster: 1,742 of the 2,467.
2. **D2 — an absent `event_type_counts`.** SPEC §3.1 lists it as MUST and both Python implementations (`kry_verify.py`, `kry.kry_attest`) check its presence, type and value. `verify.mjs` does not reference `event_type_counts` anywhere — the check is absent from the second implementation, not merely divergent on the absent-key branch.
3. **D3 — an absent `veracity.veracity_floor`.** Python defaults the missing field to `0.0` and compares it against the derived floor, so the omission reads as a misstated floor. `verify.mjs` skips the comparison when the field is not number-like. Same input, opposite verdicts.

**These three are unresolved.** Fixing any of them requires deciding, in SPEC §3.1/§3.5, what an absent envelope key means, and then changing `scripts/kry_verify.py` and/or `verifiers/js/verify.mjs` to match. Neither file was changed in this run; only `verifiers/diff_fuzz.py` was.

What the numbers do and do not license:

- The 997,533 agreeing verdicts still cover everything Run 1 covered, plus the resealed region. Each of the 1,742 D1 cases is a mutant Python rules VALID, which it can only do after recomputing the outer `attestation_hash` over an arbitrarily perturbed set of number literals and matching it; and *every* JS-side rejection of a Python-VALID mutant is accounted for by a missing `by_tier`, with none left over that a hash disagreement could hide in. Across the 139 such cases the harness recorded in full, JS's reasons never mention the hash. The number-preserving-parser claim above therefore has direct differential support at scale, which the stale-hash mutants of Run 1 could not give it.
- All 2,467 divergences are envelope-completeness cases (full classification above, no residual bucket). Nothing in this run contradicts the inner-chain or `canon_f64` agreement.
- No input in either run produced a crash from either verifier (`CRASH=0` in both).

## Scope of the SC2 claim — old wording and new

The headline claim's scope changed. Old and new wording, verbatim.

**Before — this file, 2026-07-04:**
> **Date:** 2026-07-04 · **Criterion:** SC2 (roadmap §4) — *a second verifier in a different language shows 0 divergences from the Python reference over ≥10⁶ differentially-fuzzed receipts.* · **Result: PASS.**
>
> SC2 (a second-language verifier, 0 divergences over ≥10⁶ fuzzed receipts) is met.

**After — this file, 2026-08-01:**
> **Criterion:** SC2 (roadmap §4) — *a second verifier in a different language shows 0 divergences from the Python reference over ≥10⁶ differentially-fuzzed receipts.*
>
> **2026-07-04 run: PASS within the mutation space then in use.**
> **2026-08-01 re-run, mutation space expanded with reseal + envelope field deletion: 2,467 divergences over 1,000,000 cases. Three root causes, all in how the two implementations handled *missing* envelope data.**
**2026-08-01 after the fixes (run 3, same expanded space): 0 divergences over 1,000,000 cases. SC2 met at the expanded mutation space.**

**Before — `verifiers/README.md`, 2026-07-04:**
> Prints `divergences=0` on success; any divergence is written to `divergences/` and exits non-zero. Seed is fixed (1234) for reproducibility. Latest: **N=1,000,000 → 0 divergences**.
>
> - **SC2 met**: JS verifier passes the corpus; 10⁶-case fuzz shows 0 divergences.

**After — `verifiers/README.md`, 2026-08-01:**
> **Latest: N=1,000,000 → 2,467 divergences** (2026-08-01, with those two classes in the mutation space). The earlier N=1,000,000 → 0 result stands for the space it ran in, which had neither.
>
> - **SC2 NOT met**: the JS verifier passes the corpus 41/41, but the 10⁶-case fuzz shows 2,467 divergences at the expanded mutation space.

The earlier run is not withdrawn and its number is not disputed: within its mutation space it did show 0 divergences over 10⁶ cases, and the three `kry_action_verify.py` fail-closed bugs it found are real and fixed. What it did not cover is the resealed and key-absent region, so it could not have reached these three.

---

## Run 3 — 2026-08-01, after the fixes, same expanded mutation space

### Seal — run 3

Raw stdout captured before any analysis:

```text
sha256(raw run output) = c084bc404c22fdd0534e0e7709496023622d75879e61dd5e43735367ff1c73c2
command               = PYTHONPATH=src python3 verifiers/diff_fuzz.py 1000000
seed                  = 1234 (fixed in diff_fuzz.py)
python                = CPython 3.14.6   node = v26.5.0   platform = Darwin 25.5.0
process exit code     = 0
```

### Transcription — run 3

Literal figures from the sealed output. No verdict attached in this section.

```text
differential fuzz: N=1000000  divergences=0
  agree: VALID=165199 INVALID=814801 PARSE_ERROR=20000 CRASH=0
```

- N = 1,000,000 · divergences = 0 · CRASH = 0
- mutation space identical to run 2 (reseal + envelope field deletion both present)
- corpus at the time of the run: 36 vector files / 46 checks, JS 46/46

### What changed between run 2 and run 3

Each of D1–D3 was a disagreement about a **missing** key, and each was closed by making the
absence an explicit error on the side that had been skipping the check — never by relaxing the
side that rejected:

| id | edit | run 2 | run 3 | fix |
| --- | --- | --- | --- | --- |
| D1 | `veracity.by_tier` deleted | py VALID / js INVALID | both INVALID | the Python paths gated the whole veracity block on `by_tier is not None`, so an absent key skipped it; all four §3.5 fields are now required |
| D2 | `event_type_counts` deleted | py INVALID / js VALID | both INVALID | `verify.mjs` built the per-type tally while walking the chain but never compared it — the §3.1 check was absent, now implemented |
| D3 | `veracity.veracity_floor` deleted | py INVALID / js VALID | both INVALID | `verify.mjs` gated the comparison on `isNumLike(...)`, so an absent floor skipped it; presence is now required |

A fourth, found only by re-running the fuzz after D1–D3 (24 divergences at N=50,000): the Python
presence check covered `anchored_kry` and `self_reported_kry` while the JavaScript one did not.
Closed the same way. This is the argument for re-running the fuzz after every fix rather than
reasoning about coverage.

Five vectors now pin the class (`veracity_{by_tier,anchored_kry,self_reported_kry,veracity_floor}_missing`
and `event_type_counts_missing`), so it cannot silently reopen.

## Honest scope

The second implementation is real and environment-agnostic (pure-JS SHA-256, number-preserving
parser, no Node/DOM APIs), it passes the shared corpus 46/46, and the static browser verify page
(`verifiers/web/index.html`, the same `verify.mjs` client-side) is the SC6 browser path. As of run
3, SC2's stated bar — 0 divergences over ≥10⁶ differentially-fuzzed receipts — is met, and met at
a strictly larger mutation space than the run that first claimed it.

What that does and does not say: it is evidence about **two** implementations, both written in
this repository. It is not evidence that a third-party implementer working from `SPEC.md` alone
would agree — that is SC1's question, and the tolerance and envelope rules exercised here only
became derivable from the spec text in this same change. A Go/Rust static-binary third
implementation remains the stronger test if a toolchain lands.

The fuzz seed is fixed for reproducibility; a CI job should run it with a **fresh seed each run**
as a standing gate — which would have caught D1–D3 at the moment the deletion class landed
instead of one manual run later.
