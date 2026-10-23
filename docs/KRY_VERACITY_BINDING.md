# KRY Veracity Binding — falsifier #1(b)

**Status:** primitive shipped; scope decision is the operator's.
**Code:** `src/kry/kry_mint.py` (tiers, `veracity_breakdown`, versioned hashing),
`src/kry/kry_attest.py` (per-link tier + verifiable `veracity` block),
`src/kry/kry_token.py` (dated price provenance — F2).
**Verifier:** `scripts/kry_verify.py` (stdlib-only stranger check).
**Reconcile:** `scripts/kry_reconcile.py` (operator/auditor — F1).
**Tests:** `tests/test_veracity_tier.py`, `tests/test_external_verify.py`,
`tests/test_reconcile.py`.

---

## The problem (load-bearing, previously unsolved)

The KRY mint chain checks **integrity**. Modern receipts link
`chain_hash[i] = SHA256(chain_hash[i-1] : receipt_hash[i] : public_block[i])`, where
the public block binds the fields specified for that hash version in `SPEC.md`.
The verifier checks that linkage and the declared totals without seeing prompts.
Edits break the retained chain, but a complete re-mint or tail removal requires an
independently retained prior chain-head anchor to detect; the chain alone cannot
establish that its history is complete.

Integrity is **not veracity**. The chain says nothing about whether the efficiency
events *actually happened*. An operator can author a perfectly conserved chain of
**fabricated** receipts — claim a million cache hits that never occurred — and it
verifies as intact. A stranger cannot tell an honest chain from a fabricated one
from the attestation alone. They have to trust the runtime that wrote the log.

This is the gate on everything external: settlement against a counterparty, any
legal/loyalty-credit framing, any grant claim. Until it is addressed *honestly*,
KRY is internal accounting, not a token a third party can rely on.

### Why cache hits make this structurally hard

The dominant KRY earner is the **cache hit** — and a cache hit is a *counterfactual*:
a provider call that **did not happen**. It has **zero provider-side footprint**.
No external party — not even the provider whose call was avoided — can attest to a
call that was never made. So cache-hit veracity has no external anchor available
*even in principle*, short of a witness inside the runtime itself (a TEE).

Displacement is different: the **cheap leg that *did* happen** leaves a real
provider `usage` record, and the avoided expensive call has a public list price.
The cheap leg's usage can be reconciled against a provider record. That alone does
not prove the avoided call would have happened or establish cash saved.

This asymmetry is the honest core of the problem, and it is why "just meter it
with the provider" does not rescue the bulk of the balance.

---

## What shipped: the veracity ladder (a framing, not a fix)

We do **not** claim to have solved veracity. We make the trust surface **explicit
and machine-checkable**, so the question "how much do I have to trust the operator?"
has a precise, per-balance answer instead of an unstated assumption.

Every mint is classified by **how the event was witnessed** (weakest → strongest):

| Tier | Constant | Trust source | What earns it |
|------|----------|--------------|---------------|
| **T0** | `self_reported` | the operator's runtime, full stop | ordinary unattested cache logs and callback receipts |
| **T1** | `provider_metered` | the provider, for a call that *did* happen | displacement's cheap leg, with a retained real `usage` payload |
| **T2** | `tee_attested` | a signed enclave attestation document | authenticates the document and its payload. **Verify + mint shipped: `scripts/kry_tee_verify.py` (AWS Nitro, optional `tee` extra); its regression test verifies a genuine Nitro attestation captured 2026-06-06.** The bundled PoC signs parent-supplied demonstration data. Trusted measurement code, input binding and a verified image pin are still required to establish measurement truth; the label does not prove avoided provider charges. |
| **T2** | `tlsn_attested` | a TLS-notary signature over a provider TLS session | Rust `attestation_verify` checks the proof against a trusted notary key. The Python importer trusts `verified: true` and compares fields; it does not verify signatures. **Mint shipped: `scripts/kry_tlsn_verify.py` (2026-06-04); mechanism exercised end-to-end vs production openrouter.ai with a self-hosted notary, not a neutral third party.** Does NOT establish cache-hit counterfactuals. |

- The tier is **bound into the receipt hash** (receipt `hash_version >= 2`), so an
 in-place tier edit breaks the chain. Detecting an operator's complete re-mint
 requires a prior independent anchor. New provider-metered receipts also hash-bind their `metered_tokens`
 (`hash_version = 3`), so reconciliation cannot swap token counts under an
 unchanged receipt hash. The current format (`hash_version = 7`) additionally
 binds each receipt's `receipt_id` (so a T2 tier-promotion's `supersedes` target
 cannot be relabeled onto a different, larger receipt to inflate the externally-
 anchored fraction) and its `event_type` (so a link cannot be relabeled between two
 same-`earn_rate` event types). Legacy `v1` receipts (pre-tier) may only claim
 `self_reported`; a pre-v4 public link claiming a stronger tier is rejected.
- The attestation exposes a **`veracity` block**: KRY by tier, and a
 **`veracity_floor`** = the fraction assigned to declared tiers above bare operator
 self-report — provider-metered / TEE / TLSNotary *or* an operator-run randomized
 holdout (`holdout_validated`). `verify_attestation()` re-derives the floor from the
 per-link tiers and rejects a misstated one. It checks the labels' consistency;
 their underlying source evidence requires separate verification.

A balance containing only `self_reported` receipts reads `veracity_floor = 0.0` (100%
`self_reported`). That is the **honest label** for what KRY is by default:
internal-operator measurement.

---

## The scope decision (operator's call — this primitive makes each option honest)

The primitive does not pick a path; it makes whichever path is chosen *legible*.

1. **Accept "internal-operator-measurement-only" scope.**
 Cheapest, fully honest *today*. `veracity_floor = 0.0` is published as-is; KRY is
 an internal efficiency ledger, and no external-reliance claim is made. Settlement
 stays a federated-trust arrangement between parties who already trust the operator.
 *Cost:* none. *Ceiling:* cannot support a stranger relying on the balance.

2. **Provider-metered anchoring for displacement (T1).** — primitive **supported**.
 `mint(..., evidence_tier="provider_metered", metered_tokens=[p, c])` records a T1
 receipt that retains supplied `(prompt, completion)` counts. The host system is
 responsible for retaining the genuine external source. `[p, c]` must be JSON integers, not strings, booleans, or floats.
 New receipts hash-bind `[p, c]` and expose those counts in the public attestation
 as token counts, not content. Provider-metered public attestation links also
 expose the receipt `ts` as content-free billing-window metadata, and the verified
 artifact gate rejects a T1 manifest whose `ts` differs from that attestation link.
 The *wiring* that decides
 when a displacement actually
 earns T1 (a real external provider served the answer and left a usage footprint)
 lives in the **host** that routes calls — this package provides the primitive the
 host mints against. The operator's **own** substrate (local models, owned pools)
 stays `self_reported`: it is not a third party. *Honest ceiling, stated plainly:*
 a tier label alone does not attest that the displacement **event is real**.
 Its KRY amount follows the published benchmark; actual provider charges and the
 avoided alternative require separate evidence. Cache-hit callbacks remain T0.
 **Reconciliation (F1):** `scripts/kry_reconcile.py` is an operator/auditor tool
 that checks T1 counts against **supplied usage JSON**. The operator/auditor must
 independently establish the source, account, coverage and normalization. The
 tool does not fetch records or authenticate a supplied file. Two modes are available:
 - **`--mode per-request`** (default): greedy one-to-one match of each receipt to
 a supplied record by token counts, not request identity. Each supplied row is used
 at most once; duplicate rows or repeated token pairs do not establish unique calls.
 Use a separately retrievable per-request source, such as OpenRouter's generation API.
 Standard OpenAI/Anthropic organization reports are time-bucket aggregates, not
 per-call exports; inference access alone does not establish reporting access.
 - **`--mode aggregate`**: use when the available export provides window totals.
 It sums the `provider_metered` tokens over a required `[--since,--until)` window
 and checks that the sum does **not exceed** the supplied total plus
 `--tolerance-pct`. The decimal tolerance is applied exactly to integer totals.
 This bounds the supplied token-count claim; it does not establish request identity,
 billing completeness or an actual charge.

 Constructed cases cover these modes in `tests/test_reconcile.py`,
 `tests/test_reconcile_anthropic_cache.py` and `tests/test_reconcile_boundaries.py`.
 To assess an aggregate export:

 1. Note the T1 window: `python3 scripts/kry_reconcile.py kry_data/kry_mint_log.jsonl`
 (no `--provider-export`) prints a PREVIEW — receipt count, summed minted
 tokens, and the `ts` range (unix epoch). That range is the period the billing
 export must cover.
 2. Obtain the provider-generated usage export for that account, model scope and
 window. Establish reporting access and the available accounting fields before
 spending money on a validation run. Retain the original privately and document
 any conversion into the reconciler's token categories.
 3. Shape it as JSON for the tool — a single aggregate or a list of rows, each
 `{"prompt_tokens": <input>, "completion_tokens": <output>}` (or `{"usage":
 {...}}`; envelopes `{"data":[...]}` are unwrapped). Counts must be
 non-negative JSON integers, not strings, booleans, or floats. Do not treat
 character counts, dollar totals or unknown categories as token counts. A looser `--tolerance-pct`
 is only an operator diagnostic; an external verified-savings candidate must
 stay within the `<=2%` threshold gate.
 4. Run: `python3 scripts/kry_reconcile.py kry_data/kry_mint_log.jsonl --mode aggregate
 --provider-export usage.json --since <S> --until <U>`. `RECONCILED`
 (exit 0) means the positive T1 token total is within the supplied total plus
 tolerance. `DISCREPANCY` (exit 1) means it exceeds that allowance.
 `NO_T1_RECEIPTS` (exit 1) means no metered tokens remain to assess, including an
 empty or zero-token window. None of these outcomes proves credit application,
 invoice settlement or cash savings.

3. **TEE / hardware attestation for cache hits (T2).**
 A measurement witness inside an attested enclave can sign a cache-event record.
 That attests the measurement environment; it does not prove the counterfactual
 provider call, an avoided charge or cash savings. *Cost:* high — real hardware/enclave
 integration. *Do not start without a costed falsifier.*

**Recommendation:** ship **#1 honestly now** (the label is live), and treat **#2**
as the next incremental precision step when displacement volume justifies it. **#3**
is a research track, not a sprint item, and should be gated behind a hardware-cost
falsifier before any code.

---

## Magnitude is publicly-checkable arithmetic (F2)

Veracity is "did the event happen"; **magnitude** is "is the KRY amount right". These
are separate: the verifier checks magnitude against the published benchmark. Each receipt's amount is
`tokens_saved × EARN_RATES[event_type] × value_multiplier(avoided_model)`. The price
basis — `src/kry/kry_token._MODEL_OUTPUT_USD_PER_M`, dated `PRICE_BASIS_AS_OF` with
per-model `quality` (`list` vs honest `estimate`) and `source` via
`price_provenance()` — is public and versioned. The attestation exposes each link's
`tokens_saved` + `earn_rate` (counts and a rate — no content, no model name), so
`scripts/kry_verify.py` **recomputes** amounts where those inputs are present and
rejects an implied multiplier outside the published set or a non-standard rate.
Accepted legacy links may omit the inputs; their amounts remain unchecked, and the
current CLI reports checked and unchecked counts. This
catches inflation **even when conservation is kept consistent** — a class of forgery
the chain alone misses. Reproducible benchmark arithmetic does not establish which
model was avoided, whether the alternative would have been called, or the actual
provider charge.

---

## Settlement double-spend scope (HOLE D corollary)

Veracity is about whether a *mint* event is real. A separate trust question lives
on the *settlement* side: can one attested balance be spent twice? The federated
registry (`src/kry/kry_settlement.py`) protects a **single host with a local
filesystem**: a thread lock and cross-process file lock serialize updates, and
commit re-checks each party's ceiling against the retained registry.

State the cross-node limit plainly, because it is easy to over-credit:

- The guard — and the stranger re-check in `scripts/kry_verify.py`
 (`verify_settlement`) — is **post-facto and snapshot-based**. It is sound only
 against the **complete, merged** federation registry it can see.
- **Cross-process prevention requires working local file locks.** It does not
 provide default real-time coordination between nodes with separate registries;
 conflicting transfers may be detected only after the registries are merged.
- An optional shared lease/nonce/TTL authority is implemented and default-off:
 `KRY_SETTLE_LEASE_DIR` must point to storage reached by every participating node.
 Its exclusive-create lock is best-effort on some network filesystems; periodic
 registry merge remains a backstop. Neither this path nor the local file locks
 justify an unconditional multi-node guarantee.

### HOLE F (registry tail truncation / rollback)

A hash chain detects *edits* to existing entries but **not removal of the tail**: an
attacker who drops the last settlement entry leaves a still-valid shorter chain,
silently lowering a party's cumulative settled amount and freeing balance to
re-spend (a found-and-fixed double-spend vector). `kry_settlement` now persists a
monotonic **tip checkpoint** `{count, tip}` beside the registry; `verify_registry`
fails closed when the live log is **shorter than** the checkpoint, and
`_load_registry` poisons the registry so settlement refuses. `compact_registry`
advances the checkpoint so legitimate shrinking is not flagged.

Honest ceiling (same scope as HOLE D): the checkpoint is a local file too, so a
disk-level attacker could roll back both consistently. The fix converts **silent**
rollback into **detected** rollback under normal operation and against accidental
truncation/corruption; for adversarial multi-party use, **publish the tip** (it is
content-free, like the attestation `chain_head`) so the rollback is externally
detectable.

This is orthogonal to the veracity ladder: `veracity_floor` describes the *mints'*
declared evidence tiers; the HOLE D corollary bounds what the *settlement*
double-spend check can promise across nodes. Neither establishes source truth or
actual financial settlement.

## Honesty notes

- This is a **framing**, not a cryptographic guarantee of veracity. A determined
 operator can still self-report fabricated T0 events; the point is that the
 attestation now *says so* (`veracity_floor = 0.0`) instead of hiding it.
- `provider_metered` (T1) records a provider-source claim. Trust can move toward
 the provider only after independently checking the underlying records; the tier
 label and a successful comparison alone do not authenticate them.
- No tier change alters any KRY **amount**. Edge-weighting, decay, and conservation
 are untouched; this layer only *classifies* and *exposes*.
