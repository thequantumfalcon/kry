# KRY-SPEC conformance vectors

Ground-truth test vectors for [`SPEC.md`](../SPEC.md), generated **from the reference
implementation** by [`generate.py`](generate.py) so they cannot drift from the code:

```
PYTHONPATH=src python3 vectors/generate.py
```

## What a conformant verifier must do

Read **only** `SPEC.md` and this directory (never `src/kry`). For each vector, compute a
verdict from its `input` (or `input_raw_text`) and match the vector's `expected.verdict`:

- **`VALID`** — every check in SPEC §3.4 / §4.3 passes.
- **`INVALID`** — at least one check fails. (The reference also lists `reasons`; you need
  only match the *verdict*, not the reason strings.)
- **`PARSE_ERROR`** — the bytes are malformed JSON (e.g. an invalid `\u` escape or an
  unescaped control character in a string), contain `NaN`/`Infinity`, or hold a float literal
  that overflows binary64 (e.g. `1e309`) — SPEC §1; your parser MUST reject them before verifying.

The `canon_f64.json` and `canonical_json.json` primitives are stricter: reproduce every
`expected_hex` (`canon_f64`) and `expected_bytes` (canonical JSON) exactly. If these pass, your
canonicalization matches; the attestation vectors then test the verdict logic on top. The third
primitive, `legal_multipliers.json`, carries no verdict: it is the authoritative published
price-multiplier set that the SPEC §3.4.1 magnitude check MUST use.

## Layout

```
primitives/     canon_f64.json, canonical_json.json      — encoding, exact-bytes
                legal_multipliers.json                   — the published multiplier set (§3.4.1)
hardening/            *.json  (VALID / INVALID / PARSE_ERROR) — raw-text cases, savings + action
                                                         (v1.3 correction, 2026-09-19)
savings/valid/        *.json  (verdict VALID)            — real built attestations
savings/adversarial/  *.json  (INVALID / PARSE_ERROR)    — one fault each
savings/overlay/      *.json  (VALID / INVALID)          — §3.7 overlay PROFILE only (see below)
savings/anchor/       *.json  (VALID / INVALID)          — §3.8 anchor PROFILE only (input_anchor)
action/valid/         *.json  (VALID)
action/adversarial/   *.json  (INVALID)
hardening/            *.json  (VALID / INVALID / PARSE_ERROR) — raw wire text (input_raw_text)
manifest.json   — every vector id, category, expected verdict
```

Each attestation vector file:

```json
{ "id": "...", "kind": "...", "description": "...",
  "input": { ...the attestation... },        // or "input_raw_text" for parse tests
  "expected": { "verdict": "VALID|INVALID|PARSE_ERROR", "reasons": [...] },
  "rationale": "why this verdict" }
```

## The overlay profile category

`savings/overlay/` exercises the SPEC §3.7 **promotion-overlay profile** (optional): one VALID
real promotion plus four adversarial cases (forward-reference capture, positive-value promoter,
duplicate hash-bound `receipt_id`, double-claim). Verdict-match these vectors **only if your
verifier claims the profile**. A verifier that does not implement the profile MUST instead fail
closed on any savings attestation containing a non-null `supersedes` — and must then skip this
category (its VALID vector would refuse). The bundled `verifiers/js` implements the profile.

## Coverage (see `manifest.json` for the authoritative count)

- **primitives:** float→IEEE-754-hex (incl. `1` vs `1.0`, non-finite sentinels), canonical
  JSON (key sort at depth, `\uXXXX` escaping, `NaN` rejection).
- **savings:** valid single + 3-link chains; adversarial — illegal magnitude multiplier,
  v7 `event_type` relabel, `hash_version` downgrade, `hash_version` above the v4–v7 range this
  spec defines (§3.6 fail-closed — the chain is otherwise self-consistent, so a verifier that
  guesses the newest shape it knows returns VALID and fails this vector), forged tier, blanked
  `attestation_hash`, `receipts` mismatch, absent `total_kry`, absent `veracity`, `veracity`
  present but `null`, empty chain whose `chain_head` is not the genesis value, raw `NaN`
  parse-reject.
- **savings/overlay (profile):** one VALID real promotion; adversarial — forward-reference
  capture, positive-value promoter, duplicate hash-bound id, double-claim.
- **savings/anchor (profile):** each vector carries `input_anchor` (a published
  `kry_chain_anchor/v1`) alongside `input`; the verdict is the standard verdict AND the §3.8
  anchor check. One anchored-valid; adversarial — trailing truncation (VALID standalone —
  only the anchor sees it) and retroactive re-mint. Skip this category if your verifier
  doesn't take an anchor input.
- **action:** valid single + witnessed chain; adversarial — tampered `args_commit`, forged
  anchored tier with no witness, reordered links, duplicate `receipt_id`.
- **hardening:** raw-wire regressions for reproduced verifier defects, either profile (dispatch on
  the document's own `kind`). Besides the SPEC Annex C cases: a `null` `veracity.by_tier` beside
  a forged `veracity_floor` (a non-object map does not switch off the floor comparison); event
  types and tiers named `__proto__` / `constructor` (ordinary map keys: honest ones verify, a
  forged rate, dropped count or misstated tier value does not).

This corpus is the SC1 gate: *an implementer who has never read the Python passes all vectors,
given only `SPEC.md` + `vectors/`* (the criterion as stated in
[`../docs/evidence/spec_v1_sc1_run.md`](../docs/evidence/spec_v1_sc1_run.md), which records the
2026-07-04 run against it).
