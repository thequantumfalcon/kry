# Synthetic demo transcript

This representative output was previously embedded in the README. It is preserved
verbatim from the reviewed source so that historical labels and numbers remain
inspectable. It is not output from the current validation run. The source is the
[README at commit 4efb1a9](https://github.com/thequantumfalcon/kry/blob/4efb1a9731baaf6930ac66de7b50e65d241e56d1/README.md).

The demo uses synthetic events. Its dollar labels are benchmark valuations, not
observed account charges or evidence of customer savings. Phrases such as “money
kept” in this retained output must be read within the current
[claims boundary](CLAIMS_BOUNDARY.md). Tier labels alone do not authenticate witnesses,
and the historical self-hosted TLSNotary demonstration does not establish a neutral
third-party witness.

Run `bash examples/demo.sh` from a full checkout to produce a new demonstration.
Return to the [Quickstart](../README.md#quickstart) for installation and verification.

```text
  K R Y   —   Proof-of-Efficiency Compute Credit
  earn by avoiding inference cost · a stranger verifies the ledger · stdlib only

━━━━━━ FULL LIFECYCLE  —  earn → mint → attest → STRANGER-verifies → carbon ━━━━━━
The whole loop, on sample efficiency events, in one program:

──────────────────────────────────────────────────────────────────────
1. EARN — efficiency events become KRY (edge-weighted by what they avoided)
──────────────────────────────────────────────────────────────────────
  balance:            2,436.00 KRY
  lifetime earned:    2,436.00 KRY
  frontier basis: $25.0/M output tokens   (40,000 KRY / USD)

──────────────────────────────────────────────────────────────────────
2. RETAIN — the honest value TODAY (money kept, no counterparty needed)
──────────────────────────────────────────────────────────────────────
  retained_usd:                 $0.0609
  value_type:                   retained_dollars (money kept) — NOT a tradeable token
  external_counterparty_exists: False  (honest label)

──────────────────────────────────────────────────────────────────────
3. MINT — every earn is a SHA-256 hash-chain receipt (tamper-evident)
──────────────────────────────────────────────────────────────────────
  receipts:    3
  chain_tip:   2bcb408aefd147b6...
  chain_valid: True
  veracity_floor: 0.3924  (fraction anchored by more than self-report — external OR operator-run)

──────────────────────────────────────────────────────────────────────
4. ATTEST — a public, content-sealed proof of the balance
──────────────────────────────────────────────────────────────────────
  wrote 3 links, total 2,436.00 KRY -> <tmpdir>/attestation.json
  (contains only hashes + aggregates — no prompts, responses, or model
   names beyond the event type; safe to hand to a third party)

──────────────────────────────────────────────────────────────────────
5. VERIFY — a STRANGER checks it with stdlib only (the differentiator)
──────────────────────────────────────────────────────────────────────
KRY external verification — attestation
  receipts:        3 (recomputed from links)
  total_kry:       2436.0 (recomputed from links, not the declared field)
  veracity_floor:  0.3924 (fraction assigned to declared tiers above self-report; source evidence requires separate verification)
                   ↳ anchored tiers are chain-bound LABELS — check TEE evidence with kry_tee_verify
                     TLSN proofs require Rust attestation_verify with a trusted notary key
                     kry_tlsn_verify trusts external verifier output; it does not check signatures
  price basis:     $25.0/M frontier, as of 2026-09-15 (published benchmark; see magnitude coverage)
  magnitude:       3/3 links checked; 0 unchecked (legacy inputs absent)
  anchor check:    NONE — ⚠ the anchored fraction is OPERATOR-ASSERTED
                   here: a genesis re-mint with upgraded tiers passes this check.
                   Re-run with --anchor <operator's pre-published chain head> to make
                   a retroactive re-mint detectable.
  VERDICT: VALID — integrity + conservation + magnitude (where checkable) hold; source truth is not verified (read veracity_floor as declared tiers).

──────────────────────────────────────────────────────────────────────
6. CARBON — second denomination: avoided inference -> CO2 (ESTIMATE)
──────────────────────────────────────────────────────────────────────
  kry_avoided:        2,436.00
  energy_kwh_avoided: 0.001353 kWh
  co2_grams_avoided:  0.5413 g
  status:             ESTIMATE — not a certified carbon credit (grid region unknowable: inference_geo redacted)

══════════════════════════════════════════════════════════════════════
Done. The balance was minted from sample efficiency events, anchored in a
tamper-evident chain, and verified by a program that trusts nothing in
this package. That is what 'proof-of-efficiency' means in practice.
(temp data dir <tmpdir> — safe to delete)
══════════════════════════════════════════════════════════════════════

━━━━━━ OPERATOR VIEW  —  a sample routing log → a verifiable savings statement ━━━━━━
SAVED vs SPEND + veracity_floor; --mint anchors it, --attest emits the public proof.
  
    minted savings into the chain; attestation -> <tmp>/att.json
  KRY savings report
    records analysed:     48  {'cache_hit': 10, 'holdout': 35, 'displacement': 1, 'paid_call': 2, 'free_call': 0}
    SAVED (estimated):         3,080.50 KRY   = $0.0770
    SPEND (estimated):         17,017.60 KRY   = $0.4254
    efficiency_ratio:     15.33%  (saved / (saved+spend))
    veracity_floor:       0.00%  (operator-supplied log; source truth is not verified)
      self_reported:          3,080.50 KRY
      holdout_validated:          0.00 KRY
      provider_metered:           0.00 KRY
    holdout measurement:  1 class(es) measured; cost 15,000.00 KRY ($0.3750) — estimated cost of reported holdouts
    prompt cache:         provider prompt-cache discount, computed from reported usage at list price; not minted, not attested, self-reported (list prices as of 2026-09-16)
      records:            priced 0, unpriced 38, modifier-excluded 0, malformed 0; 1-hour write price assumed for 0
    by request-class:
      code                                 treated=0     holdout=0    p̂=—                saved=    382.40 KRY [self_reported]
      greet                                treated=2     holdout=0    p̂=—                saved=      0.00 KRY [self_reported]
      summarize                            treated=5     holdout=35   p̂=86% (CI≥71%)     saved=  1,798.10 KRY [self_reported]
      translate                            treated=3     holdout=0    p̂=—                saved=    900.00 KRY [self_reported]

━━━━━━ STRANGER CHECK  —  verify that statement with stdlib only (imports nothing from KRY) ━━━━━━
  KRY external verification — attestation
    receipts:        11 (recomputed from links)
    total_kry:       3080.4984 (recomputed from links, not the declared field)
    veracity_floor:  0.0000 (fraction assigned to declared tiers above self-report; source evidence requires separate verification)
    price basis:     $25.0/M frontier, as of 2026-09-15 (published benchmark; see magnitude coverage)
    magnitude:       11/11 links checked; 0 unchecked (legacy inputs absent)
    VERDICT: VALID — integrity + conservation + magnitude (where checkable) hold; source truth is not verified (read veracity_floor as declared tiers).
  ↑ confirmed by code that does NOT trust the producer — the whole point.

━━━━━━ T2  —  the same trust model, anchored to a REAL provider's TLS response ━━━━━━
TLSNotary proves what openrouter.ai returned, verifiable by a stranger with real CA roots.
Mechanism proven (2026-06-04) — NOT yet trustless: self-hosted notary != neutral party (§5).
  docs/KRY_T2_FINDINGS_REPORT.md  ·  tlsnotary/

  earn → mint → attest → verify → (T1 reconcile / T2 notarize).  That is proof-of-efficiency.
```
