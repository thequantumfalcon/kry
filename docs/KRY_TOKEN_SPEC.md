# KRY Specification v0.1

**Status**: Research artifact — 2026-06-03 
**Implementation**: `src/kry/kry_token.py`
**Cycle verified**: delta_error = 0.00000000 (earn→bank→spend mathematically consistent)

> Release note: this document originally described the host integration.
> For this standalone kry repository, the release surface is `src/kry/*`,
> `scripts/kry_verify.py`, `scripts/kry_savings_report.py`, and the verified
> artifact packet workflow documented in `docs/KRY_VERIFIED_SAVINGS_ARTIFACT.md`.

---

## 1. What KRY Is

KRY is a **proof-of-efficiency compute credit** for LLM inference routing.

You cannot buy KRY. You earn it by running an inference system efficiently —
cache hits, compression savings, semantic deduplication, short-circuited probe
calls. You spend it to purchase routing permission for expensive model calls.

This inverts the conventional compute market:

| Conventional (OpenRouter, Copilot, Together) | KRY |
|---|---|
| Pay money → receive credits | Run efficiently → earn credits |
| Credits deplete through use | Credits accumulate through avoidance |
| Buy more when empty | Earn more by improving the system |
| Value = purchasing power | Value = provable efficiency |

---

## 2. Unit Definition

**1 KRY = 1 frontier-equivalent output token saved or justified.**

Frontier baseline: `or/anthropic/claude-opus-4.8` non-fast = **$25.00 / 1M output tokens**

```
1 KRY = 1 output token of frontier-equivalent compute
 = $0.000025 USD
 = 40,000 KRY / USD
```

This baseline is grounded in the dated price basis in
`src/kry/kry_token.py` (`_MODEL_OUTPUT_USD_PER_M`, `PRICE_BASIS_AS_OF`,
`price_provenance()`).

---

## 3. Earning Rates

KRY is earned when the system avoids a compute call it would otherwise have made:

| Efficiency Event | KRY per Token Saved | Source |
|---|---|---|
| Bridge SHA-256 cache hit | 1.0 | Exact cached response served |
| L3 semantic cache match | 0.8 | Near-match served without backend call |
| Short-circuit (CC probe) | 1.0 | Quota-check/suggestion-mode intercepted |
| Output compression | 0.6 | Directive reduced output token count |
| FeedBag deposit | 0.7 | IV-bag portion of fuel ledger deposit |
| Cache creation | 0.1 | Future hits earn; creation earns fractionally |

**Why discounted rates?** L3 matches are approximate (0.8). Compression saves exist but the call still happened (0.6). Cache creation only earns future value (0.1). The frontier-value anchor (cache_hit = 1.0) is the reference.

---

## 4. Spending Rates (Routing Permission Cost)

| Provider Tier | KRY per 1,000 Output Tokens | Notes |
|---|---|---|
| `google/`, `groq/`, `nim/`, `local/`, `pool/` | **0 KRY** | Free quota — no cost to route here |
| `ghm/` (GitHub Models free) | 5 KRY | Near-free, minimal cost |
| `gh/` (Copilot subscription) | **500 KRY/call** | Flat per-call (premium request quota) |
| `or/anthropic/claude-opus-4.8` | 1,000 KRY / 1k out | $25/M output |
| `or/anthropic/claude-opus-4.8-fast` | 2,000 KRY / 1k out | $50/M |
| `or/deepseek/deepseek-v4-pro` | 44 KRY / 1k out | $1.10/M, MIT license |
| `or/qwen/qwen3.7-max` | 150 KRY / 1k out | $3.75/M |

**Key insight:** Free tiers cost 0 KRY. A system that earns KRY through efficiency can route freely to free tiers, building a reserve. That reserve is spent only when an expensive model is genuinely warranted (a claim-reality-ratio check passes the operator's spend gate).

---

## 5. Biological Parallel

The KRY economy maps directly to cellular ATP metabolism:

| Biological | KRY |
|---|---|
| Glucose → fast ATP | Cache hit → fast KRY (immediate routing permission) |
| Fatty acid → slow ATP | Compression win → slow KRY (banked efficiency) |
| Phosphocreatine reserve | conserved KRY balance |
| Glucokinase threshold | routing/admission gate in the host using KRY |
| STARVING → glycolysis | low-cost/local routing mode |
| Multi-compartment stomach | earned/spent ledger plus mint receipts |

The standalone KRY ledger in `src/kry/kry_token.py`, the mint chain in
`src/kry/kry_mint.py`, and the public attestation in `src/kry/kry_attest.py`
are the wallet/proof surface for this release.

---

## 6. The Novel Gap (vs. Existing Systems)

**OpenRouter Credits**: Prepaid USD → per-call debits. No earning mechanism. Value = purchasing power only.

**Together.ai / Replicate**: Same model — buy credits, spend credits.

**GPU hours (AWS/GCP/Azure)**: Denominated in real compute-time, not inference efficiency. Cannot be earned, only purchased.

**Bittensor (TAO)**: Compute on a blockchain, but: (a) not LLM-specific, (b) proof-of-work (energy expenditure), not proof-of-efficiency. High carbon footprint.

**KRY gap**: No existing system denominates tokens in **avoided compute**. The token is proof-of-efficiency — you earn by NOT wasting, not by burning more energy.

**Why providers would accept KRY:**
- Free-tier providers (NIM, Groq, AI Studio) receive inbound routed traffic when KRY is spent on 0-cost tiers. They get **utilization** — their free quota fills with real work, which is their growth metric.
- Paid providers receive only high-warrant calls (CRR gate ensures the call is genuinely justified). This means higher-quality traffic with lower abandonment.
- The token aligns incentives: the more efficient the sending system, the more routing permission it accumulates, the more traffic it can distribute.

---

## 7. Minimal Viable Falsifier

The concept is falsified if:
1. Running one full earn→bank→spend cycle produces mathematically inconsistent accounting (delta ≠ 0)
2. The exchange rates cannot be grounded in real provider pricing
3. Free tiers cannot actually be routing destinations (i.e., providers block non-authenticated routing)

**Cycle verification (run 2026-06-03):**
```
earned: +2,950 KRY ($0.0738 frontier-equiv)
spent: -522 KRY (500 gh/ call + 22 or/deepseek)
balance: 2,428 KRY
delta_error: 0.00000000
consistent: True
efficiency_ratio: 84.97%
```

---

## 8. Infrastructure Already Built

KRY requires no service dependency to run locally. The current standalone release
surface is:

| Repository component | KRY Role |
|---|---|
| `src/kry/kry_token.py` | Token standard, ledger, earn/spend accounting |
| `src/kry/kry_mint.py` | Hash-chained mint receipts and evidence tiers |
| `src/kry/kry_attest.py` | Public attestation and veracity surface |
| `src/kry/kry_settlement.py` | Federated transfer and double-spend guard |
| `scripts/kry_verify.py` | Stranger verifier, independent of the package internals |
| `scripts/kry_savings_report.py` | Usage-log retained-dollar report and mint/attest driver |
| `scripts/kry_verified_artifact.py` | Packet gate for product/science/review/kill evidence |

Host routers can integrate these primitives, but host-specific `kernel/...`
paths are not part of this repository's release candidate.

---

## 9. Path to External Market

**Phase 1 (current):** Internal KRY ledger. Earn through efficiency, spend on bridge routing. Proves the economy works mathematically. Already complete.

**Phase 2:** A host router exposes KRY balance via API. External agents can query their earning rate and spending capacity.

**Phase 3:** Provider SDK. A provider can register as a "KRY-accepting destination" — they accept inbound routed traffic in exchange for being in the routing pool. NIM, Groq, AI Studio are natural first partners (they already give free quota for utilization).

**Phase 4:** Cross-operator federation. Two host routers can exchange KRY for load-balancing across operator pools. The token becomes the inter-operator settlement layer.

---

## 10. Open Questions (not answered yet)

1. **Sybil resistance**: What prevents a system from faking cache hits to earn KRY? Answer: hash-chain anchored earning events — each earn is recorded with the SHA-256 of the cached response, verifiable by any auditor.

2. **Provider verification**: How does a provider verify that a KRY-spending router actually ran efficiently? Answer: the KRY ledger is hash-chain linked — the proof-of-efficiency is cryptographically bound to the routing events.

3. **Exchange rate stability**: If frontier Opus pricing changes, KRY values shift. Answer: peg to a basket (Opus + DeepSeek + Haiku weighted by usage share) rather than single frontier.

4. **Gaming through deliberate cache misses**: A system could earn KRY by hitting cache, then deliberately miss cache to justify expensive calls. Answer: the operator's spend gate CRR check requires `cache_checked=True` for full warrant — cache misses don't justify expensive calls unless entropy routing also confirms complexity.

---

*Authored during a 2026-06-03 research session; updated for
the standalone kry release candidate. Implementation:
`src/kry/kry_token.py` · Tests: `tests/test_kry_token.py`,
`tests/test_external_verify.py`, `tests/test_verified_artifact.py`.*
