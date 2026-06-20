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

The KRY mint chain proves **integrity**: every receipt links
`chain_hash[i] = SHA256(chain_hash[i-1] : receipt_hash[i])`, so no receipt can be
inserted, removed, or altered after minting, and the conserved balance follows
from the chain. An external party running `verify_attestation()` confirms all of
that without seeing a single prompt.

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
Displacement savings *can* be externally anchored; pure cache savings cannot.

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
| **T0** | `self_reported` | the operator's runtime, full stop | cache hits (counterfactual) — **permanent** floor for them |
| **T1** | `provider_metered` | the provider, for a call that *did* happen | displacement's cheap leg, with a retained real `usage` payload |
| **T2** | `tee_attested` | hardware / TEE measurement witness | the *only* honest anchor for **cache-hit counterfactuals** (zero provider footprint) — slot only, not yet built |
| **T2** | `tlsn_attested` | a TLS-notary signature over a real provider TLS session | cryptographically PROVES a provider call *happened* (displacement's cheap leg) — strictly stronger than T1 (operator can't fabricate the bytes). **Mint shipped: `scripts/kry_tlsn_verify.py` (2026-06-04); mechanism proven end-to-end vs production openrouter.ai.** Does NOT anchor cache-hit counterfactuals — that stays `tee_attested`. |

- The tier is **bound into the receipt hash** (receipt `hash_version >= 2`), so an
 operator cannot upgrade a receipt's tier after the fact without breaking the
 chain. New provider-metered receipts also hash-bind their `metered_tokens`
 (`hash_version = 3`), so reconciliation cannot swap token counts under an
 unchanged receipt hash. The current format (`hash_version = 6`) additionally
 binds each receipt's `receipt_id` into the chain hash, so a T2 tier-promotion's
 `supersedes` target cannot be relabeled onto a different, larger receipt to
 inflate the externally-anchored fraction. Legacy `v1` receipts (pre-tier) default
 to `self_reported` — the honest assumption — and verify bit-for-bit unchanged.
- The attestation exposes a **`veracity` block**: KRY by tier, and a
 **`veracity_floor`** = the fraction backed by an external anchor (T1+T2), *not*
 operator self-report. `verify_attestation()` re-derives the floor from the
 per-link tiers and rejects a misstated one — the trust surface is itself
 tamper-evident, not just asserted.

A balance with no displacement traffic reads `veracity_floor = 0.0` (100%
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
 receipt that retains the real `(prompt, completion)` tokens from a genuine
 external provider call. `[p, c]` must be JSON integers, not strings, booleans, or floats.
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
 this attests the displacement **event is real**; the saving **magnitude** still
 rests on the avoided model's *public list price*, which is publicly checkable but
 not provider-attested. Cache hits stay T0 by their nature. Trust moves from
 "the operator" to "the provider", not to zero.
 **Reconciliation (F1):** `scripts/kry_reconcile.py` is an operator/auditor tool
 that checks T1 receipts against the **provider's own usage export** — accessible
 only with the account that made the calls, which IS the external root of trust.
 Two modes, because providers export at different granularity:
 - **`--mode per-request`** (default): greedy one-to-one match of each receipt to
 a provider usage record. A single real call can't back two claims. Use where
 the provider exposes each call — **OpenRouter** generation API, OpenAI/Anthropic
 per-call usage logs.
 - **`--mode aggregate`**: **Google AI Studio / Vertex (where every current T1 mint
 originates) exposes NO per-request export** — only aggregate, SKU-level billing
 totals (Cloud Billing report / BigQuery, hours-delayed). Per-request matching is
 structurally impossible there. Aggregate mode sums the `provider_metered` tokens
 over a `[--since,--until)` window and asserts the sum does **not exceed** the
 provider's billed total (`+--tolerance-pct`) — we can never have metered more
 provider work than was billed; an excess is phantom T1.

 Tested now with synthetic exports (`tests/test_reconcile.py`, 12 cases). To run
 against the **real Google aggregate** (the actual trust upgrade — moves T1 from
 operator-*declared* to provider-*reconciled*):

 1. Note the T1 window: `python3 scripts/kry_reconcile.py kry_data/kry_mint_log.jsonl`
 (no `--provider-export`) prints a PREVIEW — receipt count, summed minted
 tokens, and the `ts` range (unix epoch). That range is the period the billing
 export must cover.
 2. Pull the aggregate from the linked Google account, scoped to the Gemini
 models displacement uses (e.g. `gemini-3.5-flash`) over that window. Either:
 - **Cloud Billing → Reports**, filter SKU to the Generative Language /
 Vertex token SKUs, export CSV; or
 - **BigQuery billing export** (if enabled): `SELECT sku.description,
 SUM(usage.amount) ... WHERE ... GROUP BY 1` for the input/output token SKUs.
 3. Shape it as JSON for the tool — a single aggregate or a list of rows, each
 `{"prompt_tokens": <input>, "completion_tokens": <output>}` (or `{"usage":
 {...}}`; envelopes `{"data":[...]}` are unwrapped). Counts must be
 non-negative JSON integers, not strings, booleans, or floats. Token-vs-character SKUs:
 convert to tokens before making an external claim. A looser `--tolerance-pct`
 is only an operator diagnostic; an external verified-savings candidate must
 stay within the `<=2%` threshold gate.
 4. Run: `python3 scripts/kry_reconcile.py kry_data/kry_mint_log.jsonl --mode aggregate
 --provider-export google_billing.json --since <S> --until <U>`. `RECONCILED`
 (exit 0) = T1 mints are within real billed usage; `DISCREPANCY` (exit 1) =
 minted more than billed → investigate or downgrade to `self_reported`.

3. **TEE / hardware attestation for cache hits (T2).**
 The only path that makes cache-hit savings externally credible. A measurement
 witness inside an attested enclave signs "this cache hit occurred." *Cost:* high —
 real hardware/enclave integration. *Do not start without a costed falsifier.*

**Recommendation:** ship **#1 honestly now** (the label is live), and treat **#2**
as the next incremental precision step when displacement volume justifies it. **#3**
is a research track, not a sprint item, and should be gated behind a hardware-cost
falsifier before any code.

---

## Magnitude is publicly-checkable arithmetic (F2)

Veracity is "did the event happen"; **magnitude** is "is the KRY amount right". These
are separate, and magnitude is fully fixable in software. Each receipt's amount is
`tokens_saved × EARN_RATES[event_type] × value_multiplier(avoided_model)`. The price
basis — `src/kry/kry_token._MODEL_OUTPUT_USD_PER_M`, dated `PRICE_BASIS_AS_OF` with
per-model `quality` (`list` vs honest `estimate`) and `source` via
`price_provenance()` — is public and versioned. The attestation exposes each link's
`tokens_saved` + `earn_rate` (counts and a rate — no content, no model name), so
`scripts/kry_verify.py` **recomputes** every amount and rejects any receipt whose
implied multiplier is not a published value or whose rate is non-standard. This
catches inflation **even when conservation is kept consistent** — a class of forgery
the chain alone misses. Magnitude is reproducible from public data; only the
*counterfactual* (did the avoided call's price apply) remains a public-price
citation, not an operator assertion.

---

## Settlement double-spend scope (HOLE D corollary)

Veracity is about whether a *mint* event is real. A separate trust question lives
on the *settlement* side: can one attested balance be spent twice? The federated
registry (`src/kry/kry_settlement.py`) closes this **for a single node / single
process** — `verify_and_accept()` does an atomic verify+record under
`_REGISTRY_LOCK`, debiting against each party's cumulative settled amount.

State the cross-node limit plainly, because it is easy to over-credit:

- The guard — and the stranger re-check in `scripts/kry_verify.py`
 (`verify_settlement`) — is **post-facto and snapshot-based**. It is sound only
 against the **complete, merged** federation registry it can see.
- **Real-time atomic prevention holds per-process only.** Two nodes settling the
 same attested balance concurrently, each against its own unmerged registry file,
 are not caught until those registries merge. A single-node deployment is fully
 covered; this is a *future* multi-node hole, named now so it isn't discovered
 later.
- If federation ever goes multi-node, the ranked fix by trust÷effort is:
 **lease/nonce/TTL** (best trust÷effort) > signed-sync replicated log >
 primary-registry-node > full consensus (overkill).

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

This is orthogonal to the veracity ladder: a higher `veracity_floor` makes the
*mints* externally credible; the HOLE D corollary bounds what the *settlement*
double-spend check can promise across nodes. Both must be true for a stranger to
rely on a transferred balance.

## Honesty notes

- This is a **framing**, not a cryptographic guarantee of veracity. A determined
 operator can still self-report fabricated T0 events; the point is that the
 attestation now *says so* (`veracity_floor = 0.0`) instead of hiding it.
- `provider_metered` (T1) reduces trust from "the operator" to "the provider" — it
 does not eliminate trust. State that plainly to any counterparty.
- No tier change alters any KRY **amount**. Edge-weighting, decay, and conservation
 are untouched; this layer only *classifies* and *exposes*.
