# KRY-SPEC v1.0 — Receipt & Attestation Verification

**Status:** normative, versioned. **Date:** 2026-07-04. **Supersedes:** `docs/KRY_TOKEN_SPEC.md` v0.1 (descriptive; predates hash v7 and the action layer).
**Reference implementation:** `src/kry/` + `scripts/kry_verify.py` + `scripts/kry_action_verify.py`.
**Conformance corpus:** `vectors/` (generated from the reference code by `vectors/generate.py`).

This document specifies exactly what a **verifier** must compute to check a kry attestation, in enough detail that an implementer who has never read `src/kry` can write a conformant verifier in any language and pass 100% of `vectors/`. Producing (minting) receipts is out of scope — only verification is normative.

The key words MUST, MUST NOT, SHALL, SHOULD, MAY are to be interpreted as in RFC 2119.

There are two attestation profiles that share one canonicalization discipline:
- **Savings attestation** (§3) — proof that efficiency events earned KRY credit; verified by `kry_verify.py`.
- **Action attestation** (§4) — tamper-evident log of agent actions; verified by `kry_action_verify.py`.

A conforming verifier MUST **fail closed**: any parse error, unknown version, missing required field, or unmet check yields **INVALID** (or PARSE_ERROR for malformed JSON), never a crash and never a pass.

---

## 1. Conformance

An implementation is **conformant** iff, given only this document and the `vectors/` corpus (and NOT `src/kry`), for every vector it reproduces the `expected.verdict` from the vector's `input` (or `input_raw_text`):
- `VALID` — all checks pass.
- `INVALID` — at least one check fails (the reference also emits human-readable reasons; reproducing the exact reason strings is NOT required, only the verdict).
- `PARSE_ERROR` — the input is not standard JSON (e.g. contains `NaN`/`Infinity`) and MUST be rejected before verification.

Encoding primitives (§2) are pinned by `vectors/primitives/`; a conformant verifier MUST reproduce every `expected_hex` / `expected_bytes` there.

---

## 2. Canonicalization (shared)

### 2.1 Canonical JSON

`canon(value)` is the byte string produced by serializing `value` as JSON with, in Python terms, `json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True)`:

1. **Object keys sorted** lexicographically by Unicode code point, at **every** nesting level.
2. **No insignificant whitespace** — item separator `,`, key/value separator `:`.
3. **Non-ASCII escaped** as `\uXXXX` (lowercase hex; astral code points as UTF-16 surrogate pairs). ASCII control/quote/backslash escaped per JSON.
4. **`NaN`, `Infinity`, `-Infinity` are forbidden** — both on parse (reject → PARSE_ERROR) and on output.
5. Numbers are emitted in their JSON form (integers without a decimal point; e.g. `1.5`, `true`, `null` unchanged).

Worked examples (from `vectors/primitives/canonical_json.json`):

| input | `canon` output |
|---|---|
| `{"b":1,"a":0,"m":2}` | `{"a":0,"b":1,"m":2}` |
| `{"z":[3,2,1],"a":{"y":1,"x":2}}` | `{"a":{"x":2,"y":1},"z":[3,2,1]}` |
| `{"k":"café","emoji":"😀"}` | `{"emoji":"😀","k":"café"}` |
| `{"n":1.5,"z":null,"b":true}` | `{"b":true,"n":1.5,"z":null}` |

Hashes are `SHA-256` over the **UTF-8 bytes** of a preimage; all hashes are lowercase hex. `SHA256(s)` below means `sha256(s.encode("utf-8")).hexdigest()`.

### 2.2 Language-neutral number encoding — `canon_f64`

Economic numbers and timestamps are bound into hash preimages as the **exact IEEE-754 double, big-endian, hex-encoded** — never as a formatted decimal string (which would diverge across languages):

```
canon_f64(x):
    d = the IEEE-754 binary64 value of x          # Python float(x); JS Number; Rust f64; Go float64
    if x is not a finite number:  return SENTINEL  # see below
    return hex(big_endian_bytes(d))               # 16 lowercase hex chars
```

`big_endian_bytes(d)` = `struct.pack(">d", d)` (Python) = `DataView.setFloat64(0,d,false)` (JS) = `d.to_be_bytes()` (Rust) = `binary.BigEndian`+`math.Float64bits` (Go). An **integer and the equal-valued float encode identically** (`1` and `1.0` → `3ff0000000000000`).

`SENTINEL` (for a non-numeric / `NaN` / `±Infinity` field — only ever present in a tampered receipt, so the effect is a clean hash MISMATCH) differs per profile:
- Savings profile: the ASCII string `"nonfinite"`.
- Action profile: the ASCII string `"ffffffffffffffff"`.

Worked examples (`vectors/primitives/canon_f64.json`):

| input | `canon_f64` |
|---|---|
| `0.0` | `0000000000000000` |
| `1.0` or `1` | `3ff0000000000000` |
| `-1.0` | `bff0000000000000` |
| `1000.0` | `408f400000000000` |
| `0.1` | `3fb999999999999a` |
| `2.5e-08` | `3e5ad7f29abcaf48` |

---

## 3. Savings attestation

### 3.1 Envelope

An attestation is a JSON object with these fields (all MUST be present):

| field | type | meaning |
|---|---|---|
| `receipts` | integer ≥ 0 | MUST equal `len(links)` |
| `chain_valid` | bool | MUST be `true` |
| `links` | array | the receipt chain, in order (§3.2) |
| `total_kry` | number | MUST equal `round(Σ link.kry_minted, 4)` |
| `usd_equivalent` | number | MUST equal `round(total_kry * 0.000025, 6)` |
| `event_type_counts` | object | MUST equal `{event_type: count}` over links |
| `chain_head` | string | MUST equal the last link's `chain_hash` (or the genesis `"0"*64` if no links) |
| `veracity` | object | §3.5 |
| `attestation_hash` | string | MUST equal `SHA256(canon(att'))` where `att'` is the whole object with `attestation_hash` set to `""` |

### 3.2 Link

Each link is a JSON object. Fields consumed by verification:

| field | type | notes |
|---|---|---|
| `seq` | integer ≥ 0 | position |
| `hash_version` | integer | governs binding; this spec covers v4–v7. See §3.6 |
| `event_type` | non-empty string | e.g. `cache_hit` (Annex A) |
| `tokens_saved` | number ≥ 0 | raw tokens the event saved |
| `ts` | number | unix seconds |
| `evidence_tier` | string | Annex B |
| `metered_tokens` | `[int,int]` or `null` | `[prompt,completion]`; required for `provider_metered` |
| `kry_minted` | number ≥ 0 | credit minted |
| `earn_rate` | number ≥ 0 | rate applied |
| `receipt_id` | string | bound at v6+ |
| `supersedes` | string (optional) | promotion target; bound only when present (overlay profile — §3.7) |
| `receipt_hash` | non-empty string | opaque; the private preimage seals evidence and is NOT recomputed by a verifier |
| `chain_hash` | non-empty string | §3.3 |
| `sealed_evidence` | string | opaque; not verified |

### 3.3 The public block and the chain hash

For `hash_version >= 5`, the **public block** is `canon(B)` where `B` is built in this exact shape (values are `canon_f64`-encoded where shown; `canon` then sorts the keys):

```
B = {
  "hash_version": hash_version,                 # integer, as-is
  "tokens_saved": canon_f64(tokens_saved),      # 16-hex string
  "ts":           canon_f64(ts),                # 16-hex string
  "evidence_tier": evidence_tier,               # string, as-is
  "metered_tokens": metered_tokens,             # [int,int] or null, as-is
  "kry_minted":   canon_f64(kry_minted),        # 16-hex string
  "earn_rate":    canon_f64(earn_rate),         # 16-hex string
}
if supersedes is present (not null): B["supersedes"] = supersedes   # string
if hash_version >= 6: B["receipt_id"] = receipt_id or ""            # string
if hash_version >= 7: B["event_type"] = event_type or ""           # string
public_block = canon(B)
```

For `hash_version == 4`, `B` is the same but the four numeric fields (`tokens_saved`, `ts`, `kry_minted`, `earn_rate`) are the raw JSON numbers, not `canon_f64`. (v4 is legacy; the vectors are v7.)

The **chain hash** is:

```
hash_version >= 4:  chain_hash == SHA256( f"{prev}:{receipt_hash}:{public_block}" )
hash_version <= 3:  chain_hash == SHA256( f"{prev}:{receipt_hash}" )
```

where `prev` is the previous link's `chain_hash`, or the genesis `"0"*64` for the first link. A verifier recomputes `chain_hash` for every link and MUST report INVALID on any mismatch (a mismatch means a link was inserted, removed, reordered, or a bound field was altered).

### 3.4 Verification procedure (verdict)

Parse the input as JSON, rejecting `NaN`/`Infinity` (§2.1) → PARSE_ERROR on failure. Then, over the object, the verifier MUST check ALL of the following; the attestation is **VALID** iff none fail:

**Envelope:** `receipts == len(links)`; `chain_valid is true`; `total_kry`, `usd_equivalent`, `event_type_counts`, `chain_head`, `veracity` (§3.5) and `attestation_hash` all match their derivations in §3.1.

**Per link, in order** (maintaining `prev`, and `prev_version` = max hash_version seen so far):
1. `seq` is an integer ≥ 0; `receipt_hash`, `chain_hash`, `event_type` are non-empty strings; `kry_minted` is a finite number ≥ 0.
2. **Version monotonicity:** `hash_version` MUST NOT be `< prev_version` (a downgrade is a partial-tail rollback). Update `prev_version = max(prev_version, hash_version)`.
3. **Chain:** recompute `chain_hash` per §3.3; MUST match.
4. **Tier binding:** if `hash_version < 4` and `evidence_tier != "self_reported"` → INVALID (a pre-v4 link cannot carry an anchored tier; it is unbound on the public surface).
5. **Magnitude** (§3.4.1).
6. **Tier schema** (§3.4.2).
7. Set `prev` to the value **recomputed** in step 3 (re-derive the chain from genesis; do not carry a link's declared `chain_hash` forward).

A verifier that understands only `hash_version` in `4..7` (this spec) MUST fail closed (INVALID) on any other value.

#### 3.4.1 Magnitude (public arithmetic)

A link that DECLARES both `earn_rate` and `tokens_saved` MUST satisfy `kry_minted == tokens_saved × earn_rate × M` for a **published** price multiplier `M`:
- Let `pub_rate = EARN_RATES.get(event_type, 0.5)` (Annex A). If `|earn_rate − pub_rate| > 1e-6` → INVALID (non-standard rate).
- If `tokens_saved <= 0` or `earn_rate <= 0`: a declared-input link with `kry_minted > 0` → INVALID (zero-rate magnitude bypass); otherwise skip.
- Else `implied = kry_minted / (tokens_saved × earn_rate)`. If `implied` is not within `1e-3` of any **published multiplier** → INVALID (non-public price). The authoritative published-multiplier set is `vectors/primitives/legal_multipliers.json` (`multipliers` array); a conformant verifier MUST use that set. It includes `1.0` (frontier) and excludes `0.5` (see `vectors/savings/adversarial/magnitude_illegal_multiplier.json`).

A link that omits its inputs is legacy and honestly uncheckable — skip (do not fail).

#### 3.4.2 Tier schema

If `evidence_tier == "provider_metered"`: `ts` MUST be a numeric value ≥ 0, and `metered_tokens` MUST be a two-element array of non-negative integers `[prompt, completion]`. Otherwise → INVALID. Other tiers impose no metered-token requirement here.

### 3.5 Veracity

`veracity` MUST be an object with `by_tier` (`{tier: round(Σ kry_minted for that tier, 4)}`), `anchored_kry` (`round(Σ kry_minted over ANCHORED tiers, 4)`), `self_reported_kry` (`round(Σ kry_minted for self_reported, 4)`), and `veracity_floor` (`round(anchored_kry / total_kry, 4)`, or `0.0` if `total_kry == 0`). ANCHORED tiers are all tiers except `self_reported` (Annex B). A conformant verifier re-derives these from the links and MUST report INVALID on mismatch (tolerance as in the reference; the vectors use exact values).

### 3.6 Versioning / fail-closed

`hash_version` is an integer. A verifier that does not understand a link's `hash_version` MUST fail closed (INVALID), never guess. Versions are additive and monotonic within a chain (§3.4 step 2). This spec defines v4–v7; v5+ is the language-neutral (`canon_f64`) form and is what the corpus uses.

### 3.7 Promotion-overlay profile (optional, normative when claimed)

A **promotion** re-tiers value that was already minted: a ZERO-value `tlsn_attested` or `tee_attested` link whose `supersedes` names an EARLIER receipt's `receipt_id` moves that receipt's value onto the promoting tier. (The T2 attestation strengthens HOW a saving was witnessed; it does not create a new saving, so the promoting link itself carries no value.)

The overlay is an optional conformance **profile**. A verifier claiming it MUST, during the §3.4 scan:

1. Build a map `receipt_id → (tier, kry_minted, position)` over links whose `receipt_id` is a non-empty **string** and whose `hash_version >= 6` (a v4/v5 id is not hash-bound and MUST NOT enter the map). A duplicate hash-bound id is an ERROR (INVALID) — the lookup would be ambiguous.
2. Collect a promotion `(supersedes, tier, position)` for every link with `evidence_tier ∈ {tlsn_attested, tee_attested}`, a non-empty **string** `supersedes`, and `kry_minted <= 0`. A positive-value link is NOT a promotion — it keeps its own value only.

After the scan, in link order, for each collected promotion: look up `supersedes` in the map; skip if absent; skip unless the target's position is **strictly earlier** than the promotion's (a forward reference is a capture attack); skip unless the target's value is positive; otherwise subtract the value from the target's tier, add it to the promoting tier, and **delete** the map entry (a receipt is promoted at most once). Afterwards no tier total may be below `-0.01` (**outcome guard** — the overlay is a pure transfer; a negative tier is an ERROR). The §3.5 comparison then runs against the **overlaid** totals.

A verifier that does NOT claim this profile MUST fail closed (INVALID) on any savings attestation containing a link with a non-null `supersedes` — an overlay-free floor computed from such an attestation can silently disagree with the reference. Profile vectors live in `vectors/savings/overlay/`; only profile-claiming verifiers run that category (see `vectors/README.md`).

Published-anchor re-mint/truncation detection is its own profile — §3.8.

### 3.8 Chain-head anchor profile (optional, normative when claimed)

`verify` (§3.4) proves a chain is internally consistent; it cannot tell an honest chain from
one the operator re-derived from genesis, and it cannot see **trailing truncation** — a prefix
of a valid chain is itself a valid chain. The **chain-head anchor** closes both gaps: a
content-free commitment the operator PUBLISHED externally (append-only medium, out-of-band):

```json
{ "schema": "kry_chain_anchor/v1", "count": <int >= 0>, "tip": <64-char hex chain_hash> }
```

A verifier claiming this profile takes the anchor as a **second input** and, in addition to
the §3.4 verdict, MUST check: a malformed anchor (wrong `schema`, non-integer/negative
`count`, `tip` not a 64-char string) is INVALID; if `count == 0`, `tip` must equal the genesis
value (§3.3), else INVALID; otherwise the attestation must contain a link whose `seq` equals
`count` — **no such link means the chain is shorter than the published anchor
(rollback/re-mint/truncation): INVALID** — and that link's `chain_hash` must equal `tip` —
**a mismatch is a retroactive re-mint: INVALID**.

Trust caveat (normative to *state*, impossible to check): the anchor is only as strong as its
external publication. An anchor handed over by the operator at verify time proves nothing.

Vectors: `vectors/savings/anchor/` (each carries `input_anchor` alongside `input`; the
verdict is the §3.4 verdict AND the anchor check). A verifier that does not claim this
profile simply cannot offer the re-mint/truncation check — there is no fail-closed
obligation, because the anchor is an extra input, not an attestation field.

---

## 4. Action attestation

Actions are content-free: every field a verifier needs is public (raw arguments/results appear only as SHA-256 commitments), so a stranger recomputes `receipt_hash` in full.

### 4.1 Envelope

| field | type | check |
|---|---|---|
| `kind` | string | MUST equal `"kry_action_attestation"` else INVALID |
| `action_hash_version` | integer | MUST equal `1` (this spec); any other → INVALID (fail closed) |
| `links` | array | §4.2 |
| `chain_tip` | string | MUST equal the last re-derived `chain_hash` |
| `action_count` | integer | MUST equal `len(links)` |
| `veracity` | object (optional) | if it declares `veracity_floor`, it MUST match §4.4 within `0.01` |

### 4.2 Link and hashes

Each link exposes: `receipt_id`, `tool`, `args_commit`, `result_commit` (or `null`), `status`, `ts`, `agent_id`, `evidence_tier`, `server_evidence_commit` (or `null`), plus `receipt_hash`, `chain_hash`.

The **payload** is:

```
P = {
  "action_hash_version": 1,
  "tool": tool,
  "args_commit": args_commit,
  "result_commit": result_commit,          # or null
  "status": status,
  "ts": canon_f64(ts),                      # action SENTINEL is "ffffffffffffffff"
  "agent_id": agent_id,
  "evidence_tier": evidence_tier,
  "server_evidence_commit": server_evidence_commit,   # or null
}
receipt_hash == SHA256(canon(P))
chain_hash   == SHA256( f"{prev}:{receipt_hash}" )     # prev = previous chain_hash or "0"*64
```

A SHA-256 commitment is `commit(v) = SHA256(canon(v))`.

### 4.3 Verification procedure

Parse (reject `NaN`/`Infinity` → PARSE_ERROR). Then: check `kind` and `action_hash_version`. Re-derive the chain from genesis; for each link, in order:
1. `receipt_id` MUST be a string and unique within the attestation (non-string or duplicate → INVALID; fail closed — never crash on a non-string id).
2. Recompute `receipt_hash` per §4.2; MUST match (else a field was tampered).
3. Recompute `chain_hash`; MUST match (else broken/reordered/inserted/dropped).
4. A `ts` that goes backwards is a **WARNING, not a failure** (concurrency is allowed; the chain still fixes order).

Then `chain_tip` MUST equal the final re-derived hash and `action_count == len(links)`; and if a `veracity_floor` is declared it MUST match §4.4.

### 4.4 Veracity floor + tier coercion

Tiers: `self_reported` (T0), `server_witnessed` (T1), `attested` (T2). ANCHORED = {T1, T2}; any other tier string is non-anchored (fail closed). A link that claims an anchored tier but carries **no** `server_evidence_commit` is a forgery and MUST be **coerced to `self_reported`** for the floor (and SHOULD warn). `veracity_floor = round(anchored / total, 4)` over the coerced tiers, `0.0` if empty. If the attestation declares a `veracity_floor` that differs from the re-derived value by `> 0.01` → INVALID.

---

## Annex A — EARN_RATES (from `src/kry/kry_mint.py`)

| event_type | rate | | event_type | rate |
|---|---|---|---|---|
| `cache_hit` | 1.0 | | `compression` | 0.6 |
| `l3_semantic_match` | 0.8 | | `feed_bag_deposit` | 0.7 |
| `short_circuit` | 1.0 | | `cache_creation` | 0.0 |
| `continuity_capsule` | 0.1 | | *(unknown)* | 0.5 (fallback) |

## Annex B — Savings tiers

`self_reported` (T0, permanent floor) · `holdout_validated` (T1*) · `provider_metered` (T1, honest anchor) · `tee_attested` (T2) · `tlsn_attested` (T2). ANCHORED = every tier except `self_reported`.

## Annex C — Changelog

- **v1.2 (2026-07-21):** §3.8 **chain-head anchor profile**: the published `{count, tip}` anchor becomes an optional profile with vectors (`vectors/savings/anchor/` — anchored-valid, trailing-truncation detected, retroactive re-mint detected; the truncation vector verifies VALID standalone, pinning that chain-walking alone cannot see a dropped tail). Anchor vectors carry `input_anchor` as a second verifier input. Additive: every v1.0/v1.1 vector and verdict unchanged.
- **v1.1 (2026-07-21):** §3.7 promotion overlay promoted from informative to an optional, normatively-specified **profile** with its own vector category (`vectors/savings/overlay/` — one VALID promotion, four adversarial: forward-reference, positive-value promoter, duplicate hash-bound id, double-claim). Non-profile verifiers MUST fail closed on a non-null `supersedes`. Published-anchor semantics remain deferred. Additive: every v1.0 vector and verdict is unchanged.
- **v1.0 (2026-07-04):** first normative spec. Covers canonical JSON, `canon_f64`, savings v4–v7 chain + magnitude + tier-schema + veracity + envelope verdict, and the action profile. Promotion-overlay/anchor semantics deferred to a later revision (§3.7).
