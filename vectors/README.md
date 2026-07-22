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
- **`PARSE_ERROR`** — the bytes are not standard JSON (they contain `NaN`/`Infinity`);
  your parser MUST reject them before verifying.

The two `primitives/` files are stricter: reproduce every `expected_hex`
(`canon_f64`) and `expected_bytes` (canonical JSON) exactly. If these pass, your
canonicalization matches; the attestation vectors then test the verdict logic on top.

## Layout

```
primitives/     canon_f64.json, canonical_json.json      — encoding, exact-bytes
savings/valid/        *.json  (verdict VALID)            — real built attestations
savings/adversarial/  *.json  (INVALID / PARSE_ERROR)    — one fault each
savings/overlay/      *.json  (VALID / INVALID)          — §3.7 overlay PROFILE only (see below)
savings/anchor/       *.json  (VALID / INVALID)          — §3.8 anchor PROFILE only (input_anchor)
action/valid/         *.json  (VALID)
action/adversarial/   *.json  (INVALID)
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
  v7 `event_type` relabel, `hash_version` downgrade, forged tier, blanked `attestation_hash`,
  `receipts` mismatch, raw `NaN` parse-reject.
- **savings/overlay (profile):** one VALID real promotion; adversarial — forward-reference
  capture, positive-value promoter, duplicate hash-bound id, double-claim.
- **savings/anchor (profile):** each vector carries `input_anchor` (a published
  `kry_chain_anchor/v1`) alongside `input`; the verdict is the standard verdict AND the §3.8
  anchor check. One anchored-valid; adversarial — trailing truncation (VALID standalone —
  only the anchor sees it) and retroactive re-mint. Skip this category if your verifier
  doesn't take an anchor input.
- **action:** valid single + witnessed chain; adversarial — tampered `args_commit`, forged
  anchored tier with no witness, reordered links, duplicate `receipt_id`.

This corpus is the SC1 gate in `KRY_WORLD_CLASS_ROADMAP.md` (§4): a cold-context
implementer given only `SPEC.md` + `vectors/` must pass 100%.
