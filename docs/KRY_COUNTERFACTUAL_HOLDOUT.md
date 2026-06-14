# KRY Counterfactual Holdout — measuring "would the call have happened?"

**Status:** primitive shipped (`src/kry/kry_baseline.py`, tier
`holdout_validated` in `kry_mint.py`, `tests/test_baseline.py`).
**What it fixes:** the load-bearing weakness in `KRY_VERACITY_BINDING.md` — the
counterfactual at the heart of every cache-hit credit.

---

## The problem it attacks

A cache hit is a **counterfactual**: a paid call that *did not happen*. Its
avoidance value rests on an unprovable claim — "absent the cache, a real call
would have been made." The mint chain proves integrity; the veracity ladder
*labels* self-report; neither **measures** the counterfactual. That is why pure
cache-hit balances honestly read `veracity_floor = 0.0`.

The claim decomposes into three parts:

| | claim | provable? |
|---|---|---|
| C1 | a real request arrived | yes — requester-side footprint |
| C2 | the cached answer was a valid substitute | yes — comparable on a sample |
| C3 | **absent the cache, a *paid* call would have happened** | **the counterfactual** |

Only **C3** is hard. And C3 is not a new problem — it is the exact question two
mature fields already answer with the same tool.

## Two fields already solved this — with a randomized holdout

- **Advertising incrementality.** "Did the ad *cause* the sale, or would it have
 happened anyway?" Standard answer (used by ~52% of marketers in 2026): a
 **randomized holdout / ghost-ad control** — withhold the treatment from a random
 slice and measure the difference.
- **Energy-efficiency M&V (IPMVP / ISO 50001 / DOE SEP).** Savings are credible
 only against a **documented baseline**: `Savings = Baseline − Reporting ±
 Adjustments`. This is the certification-grade form of the same idea.

KRY's counterfactual is structurally identical, so it takes the same solution.

## The mechanism

For each cache-eligible request, deterministically assign a small random fraction
(default **2%**) to a **holdout**: bypass the optimization and make the real call.

1. **Holdout calls generate real provider receipts** — a genuine external anchor
 (the same root of trust as a `provider_metered` T1 mint).
2. They **measure `p_hat`** = the fraction of a request-class that genuinely hits
 the **paid** model absent optimization.
3. A **Wilson 95% confidence interval** is computed, and the cached ("treated")
 population is valued at the **CI lower bound** — never overclaiming.

The claim becomes:

> *"For request-class `summarize`, a conservatively-estimated **62%** of cache
> hits avoided a real paid call (point 71%, 95% CI [62%, 79%]), measured by a
> randomized 2% holdout with retained provider receipts."*

That is a documented, auditable baseline — the artifact IPMVP and carbon
**additionality** require. **One mechanism raises veracity *and* carbon
credibility at once.**

## How it lands in the ledger

A new tier sits between self-report and per-event metering:

| Tier | Trust source | Earns it |
|---|---|---|
| `self_reported` (T0) | operator runtime | a raw cache hit, no baseline |
| **`holdout_validated` (T1\*)** | **a randomized holdout w/ real receipts** | **a cache hit valued at the measured-conservative counterfactual** |
| `provider_metered` (T1) | the provider, per call | displacement's metered leg |
| `tee_attested` (T2) | hardware/TEE | not built |

Usage pattern (`kry_baseline.holdout_adjusted_tokens` scales the avoided tokens by
the CI lower bound — honest for *both* the dollar basis and the carbon basis,
since only the measured fraction truly displaced a paid call / avoided the energy):

```python
adj = kry_baseline.holdout_adjusted_tokens("summarize", raw_tokens) # raw × CI_lo
kry_mint.mint("cache_hit", adj, evidence_tier=kry_mint.TIER_HOLDOUT_VALIDATED, ...)
```

`holdout_validated` counts as **anchored** (it lifts `veracity_floor` above 0) but
is **reported on its own tier line**, so a verifier sees precisely how much of the
balance rests on holdout-statistics vs per-event metering — no overclaiming.

## Falsifiers (Galileo round — attacks survived)

| Attack | Answer |
|---|---|
| "Holdout costs money." | Yes: cost = `holdout_rate × avoided value`. At 1–2% it is the quantified **price of veracity**, surfaced in `holdout_report()`, not hidden. |
| "Operator grinds request-ids to dodge holdout." | Assignment = `SHA-256(published_seed : request_id)`. Auditable; grinding leaves a record. **Commit the seed publicly** — that is the unbiasedness guarantee. |
| "Classes are gamed to inflate p̂." | Classes declared before measurement; holdout is per-request, so requests can't be reclassified post-hoc without breaking the hash assignment. |
| "It's a population estimate, not per-event proof." | **Correct — and not overclaimed.** Own tier line; valued at the CI floor; an unmeasured class earns **0** (fail-closed). |
| "Small `n` breaks the normal approximation." | Wilson score interval (not naive normal) — valid for small `n` and `p` near 0/1; `n=0 → [0,1] → lo=0 → no credit`. |

## Honest ceiling

This **measures** the counterfactual rate; it does not **prove** any single event's
counterfactual. It moves cache-hit veracity from "the operator's word" to "a
randomized experiment with real receipts and a stated confidence interval" — the
strongest honest claim available short of a TEE (T2). That is the bar incrementality
and IPMVP hold, and it is enough for a counterparty, an auditor, or a carbon
verifier to rely on a *bounded* number instead of an asserted one.

---

## Adversarial testing & known limits (what we tried to break)

The report pipeline (`scripts/kry_savings_report.py`) and the holdout estimator
were stress-tested with hostile and at-scale data (`tests/test_stress.py`,
`tests/test_savings_report.py`, generator `examples/gen_dataset.py`). What held,
what was hardened, and what is a structural boundary:

**Hardened (was a bug, now closed + regression-tested):**
- **Malformed token counts** — negative, `NaN`, `inf`, strings, missing — clamp to
 0. They used to produce a *negative saving* or crash the whole run; now one bad
 record contributes nothing and never raises.
- **Thin / fabricated holdout** — a handful of holdout records can no longer buy the
 `holdout_validated` label. Below `MIN_HOLDOUT_N` (default 30) a class falls back to
 `self_reported` (floor contribution 0). The Wilson CI already discounts the
 *magnitude* for small `n`; this guards the *trust label*.
- **Replay** — re-minting the same evidence is bounded by geometric decay
 (`kry_mint`), so 1,000 replays of one cache hit converge to ≤ 2× a single mint.

**Validated correct at scale:**
- 50k-record logs analyse in < 0.5s; savings ≥ 0, `veracity_floor ∈ [0,1]`, and the
 tier breakdown partitions the total — always.
- **Estimator recovery:** against the generator's *embedded ground truth*, every
 request-class with ≥ 30 holdout samples has a 95% Wilson CI that **contains the
 true paid-rate** (e.g. true 0.85 → measured p̂ 0.850, CI [0.824, 0.874]). The
 holdout doesn't just run — it *recovers the counterfactual it claims to measure*.

**Structural boundary (cannot be fixed in software — stated plainly):**
- **Verification proves arithmetic, not that token counts are real.** An operator
 can mint one cache hit claiming a trillion avoided tokens; `kry_verify.py` returns
 **VALID** (integrity + conservation + magnitude all hold — the multiplier is a
 legal published price). **But that balance reads `veracity_floor = 0.0`** — the
 honest label: 100% self-reported, anchored by nothing. The system never *claims*
 a fabricated number is trustworthy; it *labels* it unanchored.
- The only way to raise the floor is **≥ 30 real holdout samples whose forced calls
 left provider receipts** — and those receipts are reconcilable against the
 provider's own usage export (`scripts/kry_reconcile.py`, `scripts/kry_or_fetch.py`).
 So fabrication is not *prevented*, but gaining *trust* requires real, externally
 reconcilable calls. That is the honest ceiling: the holdout moves veracity from
 "the operator's word" to "a randomized experiment with receipts" — not to zero
 trust, and not to cryptographic certainty (that needs the unbuilt TEE/T2 tier).

The takeaway: the trust surface is **layered**. Integrity (the chain) is absolute;
magnitude (the price) is publicly recomputable; veracity (did it happen) is
*disclosed* as a floor and *raised* only by reconcilable holdout measurement. A
verifier is never asked to trust more than the floor says.

---

*Method grounded in: advertising incrementality / ghost-ad holdouts; IPMVP / ISO
50001 measurement & verification baselines. See the market review for sources.*
