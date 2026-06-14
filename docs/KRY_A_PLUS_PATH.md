# KRY → A+ (`production_ready`): the researched path to real traffic at scale + a real counterparty

**Date:** 2026-06-11 · **Status of KRY:** committed `research_grade` (2026-06-10) · **This doc:** the
external path to the one rung left, grounded in KRY's own Step-2 falsifier and the verified-savings
artifact gate, synthesized from eight primary-source research strands.

> Source discipline: claims below are marked **[V]** (primary-source-verified by the research), or
> **[inf]** (a reasoned inference, not a documented fact — flagged so it is not mistaken for evidence).
> The measured-vs-speculative firewall is load-bearing here; an inflated path is as useless as a timid one.

---

## 0. The frame: A+ is not a code problem

`readiness_label()` already computes `research_grade`. The grader **refuses to let more code buy the top
rung** (`tests/test_capabilities.py::test_a_plus_requires_external_evidence_code_alone_cannot_reach_it`).
A+/`production_ready` is **defined, falsifiable, and external** — and KRY *already built the machinery that
consumes the external evidence*:

- **The falsifier (KRY_READINESS.md, Step 2):** run **real traffic** (not synthetic) through
 `scripts/kry_savings_report.py` **+ a live 2% holdout for a bounded window**, and have the resulting
 attestation **checked by a third party with `scripts/kry_verify.py`**.
- **The gate that grades it (`scripts/kry_verified_artifact.py`):** four gates that map one-to-one onto A+.
 - **Product gate** — usage log has records, savings positive, attestation verifies, totals match.
 - **Science gate** — corpus operator-declared *real*, hash-bound corpus manifest, **pre-registered**
 `kry_validation_plan/v1`, provider export + provenance manifest attached, provider oracle non-vacuous,
 **independent agreement ≥ 0.80**, *and* "the readiness label can reach `production_ready`."
 - **External-review gate** — **outside verification + buyer feedback + legal/claims review** as
 structured, hash-bound JSON. *This is the counterparty, formalized.*
 - **Kill gate** — fails on bad attestation, no savings, reconciliation discrepancy, agreement below bar;
 and it **refuses the bundled synthetic sample** (you cannot fake a real corpus).

So A+ reduces to two external ingredients the gate is waiting for: **(1) real traffic at scale** (fills the
science gate) and **(2) a real counterparty who runs `kry_verify` and signs the external-review evidence**
(flips `external_counterparty_exists` → `True`, `ship_scope` → `external_verified_savings_candidate`,
readiness → `production_ready`). Everything below is how to get those two things.

---

## 1. The verified white space — why a counterparty has a reason to say yes

Three independent research strands hit the **same wall**, which is the strongest evidence in this document:

> **Nobody proves LLM savings independently. The party that measures the savings is always the party paid
> by the savings.**

- **Competitive map — 25+ vendors, primary sources [V]:** every vendor proves savings by *self-reported
 dashboard arithmetic* (its own pricing DB × observed tokens) or a *benchmark plot*. **Zero reconcile
 against the actual provider invoice. Zero run a randomized holdout.** Datadog and Helicone literally label
 their numbers "estimated." ProsperOps' own ESR page states there is **no third-party verification**.
- **Design-partner strand [V]:** the router vendors' headline numbers (Martian "20–97%", Not Diamond
 "30%+") are **benchmark/asserted, not holdout-validated against a real bill** (arxiv 2501.01818,
 "Rerouting LLM Routers").
- **Pilot-venue strand, independently [V]:** "the category you most wanted — a packaged *third party
 validates my savings claim* venue — **does not exist**." That absence is the wedge.

**KRY's uniquely-filled position** is the conjunction no competitor offers: *independent* (the measurer is
not paid by the result) **+** *holdout-validated* (causal, not assumed) **+** *provider-billing-reconciled*
(real dollars, not a pricing table) **+** *stranger-verifiable receipt* (offline re-checkable, trust
neither party). Critically, this makes KRY **complementary to ~90% of the market, not competitive** — it is
a verification layer that *attaches to* a router/gateway's existing traffic rather than another dashboard.

**Honest caveat [V, self-flagged]:** KRY's committed readiness is `research_grade` — the white space is
real and verified, but the *productized* proof layer is still maturing (one durable live anchor, not a
fleet of customers). The market gap is a fact; KRY's claim to fill it at scale is the thing this path tests.

---

## 2. The symbiosis map — the two-way value, ranked

The organizing principle (the firewall that separates real symbiosis from wishful): **a partner who merely
*publishes* a savings number is a moderate fit; a partner who *sells* savings to its own customers and must
prove it to close deals is a strong fit.** The second class has a commercial wound KRY cauterizes.

In every row, the trade is the same shape: **KRY gives** an independent, stranger-verifiable,
invoice-reconciled receipt that converts a self-measured number into a provable one; **the partner gives
KRY** real traffic at scale **+** the third-party counterparty/sign-off that is literally KRY's missing A+
ingredient.

| # | Partner | What KRY gives them | What they give KRY | Evidence they want it | Integration surface | Strength |
|---|---------|---------------------|--------------------|-----------------------|---------------------|----------|
| 1 | **Martian** (router) | Turns "save 20–98%" sales claim into a per-deal, board-auditable verified receipt | Enterprise-scale routing traffic **+ a named commercial counterparty** | Publicly claims **20–97%** as core pitch; ~$1.3B valuation → buyers demand proof **[V]** | Router logs chosen-vs-baseline per request → generation-endpoint reconcile | **Strong** |
| 2 | **Not Diamond** (coding-agent router) | Makes "30%+ / up to 10×" + the Rootly case study independently re-runnable | Production coding-agent traffic + a counterparty that already publishes case studies it must defend | Homepage sells "frontier quality at a fraction of the price" **[V]** | Router decision log → per-request reconcile | **Strong** |
| 3 | **Requesty** (gateway, ~90B tok/day) | Verifies the published "$500→$200/mo, 70% identical queries" so it's not just a testimonial | Massive real traffic + a customer base wanting proof of billed savings | Publishes the case + "up to 40% routing / 60% caching" **[V]** | One-line `base_url` swap already in path → webhook/callback | **Strong** |
| 4 | **OpenRouter** (KRY's existing oracle) | A proof-of-savings layer atop usage data — the routing layer whose savings are *checkable* | The per-request cost/usage record KRY reconciles against — **KRY's literal oracle** | "Usage is always included automatically" + `/api/v1/generation` async fetch **[V]** | **Already wired** (`kry_or_fetch`); the real anchor already lives here | **Strong** |
| 5 | **Portkey** (gateway) | Upgrades its per-hit "how much you saved" UI number from operator-asserted to stranger-verifiable | Real gateway traffic + a cache-hit savings ledger | Analytics page literally shows "how much money you saved with each hit" **[V]** | Gateway computes per-request savings → callback/log export | **Strong** |
| 6 | **LiteLLM** (OSS proxy) | A verifiable-receipt callback so users can prove the spend/savings it tracks | Huge OSS install base + `response_cost` exposed per request | Custom-callback architecture, `kwargs["response_cost"]` on every call **[V]** | **Custom Python callback class — lowest-friction surface of all** | **Strong** |
| 7 | **ProjectDiscovery** (published 59→70% cache savings) | Converts the blog claim into a receipt customers/auditors can re-run | A real, already-public savings story + a security-credible brand to co-sign the method | Blog: "we derive the effective per-token rate from real spend"; **NO third-party verification** **[V]** | Anthropic billing/cache-read logs → offline `kry_verify` | **Strong** |
| 8 | **Prem AI** ($15k→$4.5k SaaS case) | Turns the 70%-reduction case study into a verifiable customer receipt | Real case-study traffic + a vendor counterparty selling the savings | Publishes the $15k→$4.5k/mo case as a marketing proof point **[V]** | Prem Studio routing/caching logs → reconcile | **Strong** |
| 9 | **Helicone** (OSS observability) | Verifiable receipt atop the caching dashboard | OSS traffic + per-request cost/cache logs | Markets caching savings % **[V]** — *but acquired Mar 2026, now maintenance mode* | Proxy log export / webhook | **Moderate** |
| 10 | **CloudZero / Pay-i / Finout** (AI-FinOps) | Independent attestation for the unit-economics savings they report to the CFO | Enterprise FinOps traffic + the **finance counterparty** (the exact board/auditor persona) | "$1M+ savings, 50%+ compute reduction" customer cases; whole brand is "AI ROI" **[V]** | Provider billing-API ingest already built → reconcile layer | **Moderate** |
| 11 | **Langfuse / Unify** | Verifiable cost-receipt feature atop token/cost tracking or routing | OSS tracing install base / router traffic | Captures OpenRouter + LiteLLM cost per generation **[V]** | SDK trace / router decision log | **Moderate** |
| 12 | **FinOps Foundation "FinOps for AI"** | A standard "verifiable savings receipt" primitive for the framework | Standards-body distribution + practitioner counterparties | Framework covers token-level cost + ROI attribution **[V]** | Standards adoption (channel, not a code surface) | **Channel** |
| — | **Shared-savings / contingency AI-cost consultancy** | White-label proof-of-savings to justify a gain-share invoice (you must prove the savings you bill a % of) | A pay-for-proof commercial counterparty | *Could not find a named public player* **[inf]** — model is a perfect fit, existence unverified | Engagement-dependent | **Strong-if-real (unverified)** |

### 2.1 The single strongest symbiotic pair: **Martian**
It maximizes both halves at once. **KRY → Martian:** its entire pitch is a number it *cannot independently
prove*; at enterprise scale, procurement and finance will not accept a vendor's own dashboard — KRY's
invoice-reconciled, stranger-verifiable receipt is a *deal-closing* asset. **Martian → KRY:** it supplies
the exact two things between `research_grade` and A+ — a **real-world corpus at enterprise scale** and a
**named commercial counterparty**. It beats OpenRouter (the obvious incumbent, #4) because OpenRouter is an
*oracle without a commercial wound* — it has nothing to prove — whereas Martian has the wound, the scale,
and the counterparty in one partner.

### 2.2 The 3 best white-label "proof-of-savings" partners
**Martian · Not Diamond · Requesty** — each *sells* a savings outcome and therefore has a structural,
recurring need to *prove* it. KRY sits underneath as the white-label receipt they stamp on every customer
report. **Firewall flag [V→inf]:** that these vendors *publicly need to prove savings* is **verified**;
that they would adopt an *external* proof layer rather than build their own dashboard is an **inference** —
the wedge being that a self-built dashboard is *still operator-asserted* and cannot clear a skeptical board.
That gap is real; their willingness to outsource it is exactly what a discovery call tests.

---

## 3. The real-traffic channel — everything resolves to a LiteLLM gateway

The token-count + cost fields KRY's savings report needs live in the **gateway layer**, not in public
leaderboards. The research converged hard:

- **Lowest-friction live hook [V]:** a **LiteLLM `CustomLogger` callback**. On every request it exposes the
 provider's **raw usage object** (`metadata.usage_object`, with `cached_tokens`) alongside `cache_hit` —
 pure-Python, no fork. A ~40-line logger calls `kry_mint(...)` + feeds the holdout. This is KRY's stdlib
 world.
- **Cleanest independent reconciliation [V]:** **OpenRouter's `GET /api/v1/generation?id=`** is the *only*
 source that returns the provider's **authoritative** per-request cost (`native_tokens_*`, `total_cost`,
 `cache_discount`), post-hoc by id, even on $0 free models. **KRY already targets this exact endpoint** —
 the 18-call and 52-call anchors came from here. Every other gateway gives the provider's raw *token*
 witness but a gateway-*estimated* cost; for those, reconcile against the provider's tokens + published
 pricing, never the gateway's dollar field.
- **The two-part shape:** **LiteLLM callback for the live per-request mint/holdout + OpenRouter generation
 record as the external reconciliation anchor** — the same shape KRY already validated.

### 3.1 The best OSS pilot: **Aider** ([github.com/Aider-AI/aider](https://github.com/Aider-AI/aider))
Its `analytics.jsonl` carries `main_model` + `prompt_tokens` + `completion_tokens` + `cost` **per message** —
mapping **1:1** onto KRY's savings-report normalizer with **zero adapter work** [V]. ~15B tokens/week
community-wide, a token-cost-obsessed maintainer, and it already does **weak/editor-model routing** (real
displacements KRY can value, not just cache hits). A user's real Aider log runs through
`kry_savings_report.py` essentially unmodified.

### 3.2 Live (streaming) sources beyond the static corpora you've exhausted
WildChat and LMSYS are **already spent** (KRY has `holdout_validated` on organic WildChat). The remaining
*live* options [V]:
1. **Your own production traffic via a self-hosted LiteLLM gateway** — highest-fidelity, no consent problem,
 streams continuously, carries token+model+cost. *Start here.*
2. **A friendly OSS maintainer's LiteLLM gateway** ("bring your logs") — same field richness; build the ask
 around LiteLLM (Helicone is now maintenance-mode post-acquisition).
3. **Grant-funded OSS traffic** instrumented through LiteLLM — **Claude for Open Source** (launched Feb 2026,
 ~$1,200 × 10,000 OSS-maintainer spots) or OpenAI Researcher Access. This is the most credible path to
 *stranger-owned* live paid traffic — which is exactly the A+ counterparty axis.
 *Caveat [V]:* OpenRouter's Data/Rankings product and LMArena are real live traffic but **aggregate-only /
 cost-less** — useful for market-sizing baselines, not for a per-request holdout.

---

## 4. Manufacturing counterparties via standards — FOCUS + ESR

Instead of cold-selling one buyer, **conform to a standard buyers already trust**, so any FinOps-tooled
buyer can ingest KRY without a bespoke integration.

- **FOCUS (FinOps Open Cost & Usage Spec) — adopt it [V].** Open (CC-BY-4.0), current ratified v1.3
 (2025-12-04), and **explicitly names "SaaS platforms… and internal infrastructure/service platforms" as
 valid data generators** — a savings-proof tool emitting FOCUS rows is legitimate, not a hack. KRY's
 receipt maps cleanly: `ListUnitPrice`/`ListCost` = the provider's published rate × the displaced call's
 tokens (the "would-have-paid"); `BilledCost` = what was actually paid (often $0 on a cache hit);
 `ConsumedQuantity`/`ConsumedUnit` = the token counts; `SkuId`/`ServiceName`/`ProviderName` = model +
 provider. **Caveat [V]:** FOCUS has **no first-class "counterfactual avoided cost" column** — you express
 the avoided call as a `ListCost`-priced row with `BilledCost = 0`, and the savings is the derived delta.
 FOCUS gives *interoperability, not credibility*; **KRY's holdout-backing of the `ListCost` number is the
 off-spec differentiator.**
- **ESR (Effective Savings Rate) — headline the savings as this [V].** Formula:
 `ESR = (Savings − Cost-to-Achieve-Savings) / On-Demand-Equivalent spend`. Restate KRY's "up to ~75%
 cost-avoidance" as **"ESR = X% against [provider]'s published list rate."** The "− Cost-to-Achieve" term
 *forces* the honesty KRY's correctness-layer caveats already track (net out the cheap-model adequacy cost;
 never report a gross % as ESR).

### 4.1 The free recognition move (no membership gate): **join FOCUS as a Contributing Member**
You do **not** need paid FinOps Foundation membership to contribute to FOCUS — sign the CLA (no cost), then
file issues/PRs and join community calls [V]. **The white-space play:** FOCUS has no "verified savings /
avoided cost" construct — filing a well-formed **savings-attestation extension** issue, with KRY's holdout
receipt as the reference implementation, is a credible path to being the *named originator of that schema
slot*. Recognition without a single sale. (Joining the "FinOps for AI" working group as a practitioner is
Rank 2; FOCUS-Conformant certification is the aspirational rung but **gated behind a multi-person
credentialed-staff floor a solo founder fails today** — track, don't wait on it.)

### 4.2 A genuine white-space finding: **no verified-savings registry exists**
There is **no Verra/Gold-Standard analog for compute/LLM savings** — no body that stamps "this savings claim
is independently verified" [V]. The adjacent things are AI *governance* certs (ISO 42001, SOC 2 — they
certify process, not a savings claim's veracity) and cost-*attribution* tools (measure, don't verify).
**Strategy read [inf]:** don't try to *be* the registry yet (no counterparty trusts a solo founder's
registry); get the *methodology* recognized inside FOCUS (§4.1) so the receipt is portable, and position KRY
as the verification *method* a future FinOps-blessed savings registry would adopt. KRY's holdout + receipt
is plausibly the VVB-equivalent methodology such a registry would need.

---

## 5. The commercial structure — shared-savings makes the counterparty *want* the trade

The proven alignment model for cost-optimization infra is **shared-savings / gainshare**: ProsperOps charges
**30–35% of *realized* savings, nothing if nothing is saved** [V]. This *is* the proof mechanism — the
vendor can't inflate the number because the buyer's own bill is ground truth, so **the commercial model and
the proof requirement are the same artifact.** For KRY this is doubly aligned: a buyer risks ~nothing
(no-savings-no-fee), and a receipt KRY is *paid a percentage of* cannot be inflated without the buyer's
invoice exposing it. Pilot variants that de-risk further [V]: money-back-if-criteria-not-met, or
base-fee + success-milestone.

Two of KRY's core design choices are **independently validated by the FinOps Foundation** [V]:
*realized ≠ avoided savings* (= KRY's `internally_consistent` vs `research_grade` axis) and
**cost-per-successful-output** (= KRY's adequacy-gate / correctness-layer). KRY is measuring what the
standards body says to measure.

---

## 6. The counterparty — who to approach and what they need to say yes

**Approach the platform / infra engineer or FinOps champion first** [V] — the only role with *both
visibility and authority* (they can grant the holdout and the billing read-access, and they're the technical
skeptic whose "yes" the CFO trusts). To say "yes, these savings are real" they need: (i) a **holdout result
from their own traffic** with no quality regression (cost-per-successful-output); (ii) savings as **ESR vs
their provider's published rate**; (iii) the dollar line **reconciling to their actual invoice**; (iv) a
receipt they can **independently re-verify** (`kry_verify`, not "trust us"). Get their yes and the CFO
conversation is short — the CFO needs auditability/traceability, which that chain provides.

**The buyers-of-proof live in one place: the FinOps Foundation Community Slack + "FinOps for AI" working
groups** [V] — practitioners whose literal mandate is defending a savings number to finance. "You verify it
yourself, offline, no trust in me required" is their exact pain. **This is the fastest path to the one thing
KRY lacks — a confirming external counterparty — and it is free and open today.**

---

## 7. The staged path to A+

Each stage states its verification, in KRY's own step→verify idiom.

- **Stage 0 — Instrument + standardize (this week, solo).**
 Stand up a LiteLLM gateway in front of *your own* real paid traffic; add the KRY `CustomLogger` →
 `kry_mint` + 2% holdout; emit the receipt in **FOCUS** rows and headline **ESR**. Join **FOCUS as a
 Contributing Member** and file the savings-attestation extension issue.
 *Verify:* `kry_savings_report.py` runs on the live log; `kry_research_grade.py --fetch` still reconciles
 ≥ 0.80 against OpenRouter's record; the FOCUS issue is filed.

- **Stage 1 — Secure stranger-owned live traffic (1–3 partners, not a cohort).**
 Open the warmest conversations in parallel: **ProjectDiscovery** (cleanest methodological match —
 numbers-led, via public Discord/GitHub), the **white-label trio** (Martian/Not Diamond/Requesty — "we make
 your savings claim independently provable"), and **Aider's** community (1:1 log fit). Offer the
 shared-savings / no-savings-no-fee structure. De-risk the partner's cost with **Anthropic Startup ($25k) /
 OpenRouter / Claude-for-Open-Source** credits so the pilot is free to them.
 *Verify:* one partner grants billing read-access + permission to run a holdout on a real traffic slice.

- **Stage 2 — Run the real-traffic holdout (a bounded 30–90 day window, pre-registered).**
 Generate the `kry_validation_plan/v1` **before** the run (the gate's pre-registration guard against picking
 the window/tolerance after seeing the result). Run real traffic + the live 2% holdout; collect the
 provider export; build the T1 manifest.
 *Verify:* `kry_research_grade.py` ≥ 0.80 on the partner's real corpus; `kry_verified_artifact.py` science
 gate passes (corpus=real, manifests hash-bound, agreement met).

- **Stage 3 — The counterparty signs off (flips the label).**
 The partner's platform/FinOps engineer runs `kry_verify` + the doctor, and returns the three structured
 evidence files: **outside_review** (verdict `verified`), **buyer_feedback** (materiality ≥ 10% avoidable
 spend or ≥ $5k/mo), **legal_review** (`approved_with_limits`, tradeable-token disclaimed).
 *Verify:* `kry_verified_artifact.py --verify-artifact` →
 `ship_scope = external_verified_savings_candidate`, `external_verified_savings = true`; `readiness_label()`
 → **`production_ready` / A+.**

---

## 8. The Minimal-Viable A+ Falsifier (exactly what counts)

Grounded in Step 2 **+** the verified-artifact gate, the smallest thing that legitimately earns A+:

- **Counterparty:** ONE external org (not the operator); a stranger-recruited partner beats a network favor.
- **Scope:** a single real production workload (or a defined slice) with a **randomized 2% holdout**, on
 **real traffic**, for a **bounded, pre-registered window**.
- **Evidence the counterparty can verify — all three:** (1) the **holdout** shows incremental, causal
 savings with **no quality regression** (cost-per-successful-output); (2) the dollars **reconcile to the
 provider's actual invoice/usage export** (≥ 0.80 independent agreement, the science-gate bar); (3) a
 **stranger-verifiable receipt** their own engineer re-derives via `kry_verify` — not "trust us."
- **Sign-off:** a named **platform/FinOps owner** attests, and returns the outside_review + buyer_feedback +
 legal_review JSON the external-review gate requires.

**Pass** → the gate computes `external_verified_savings_candidate` / `production_ready`; A+ is *earned*, not
asserted. **Fail is information, not a setback** — if real accepted displacements don't reconcile, or the
real-world holdout rate falls outside the synthetic expectation, the model was overfit to synthetic data, and
*that negative is the contribution.*

---

## 9. Do this now — first moves + the clocks

1. **Join the FinOps Foundation Community Slack today** ([finops.org/community/community-slack](https://www.finops.org/community/community-slack/))
 and post in the FinOps-for-AI channels. Lead with "verify it yourself, offline." This is the single
 highest-probability route to a confirming counterparty, and it's free.
2. **Open the ProjectDiscovery conversation** ([the cache-savings blog](https://projectdiscovery.io/blog/how-we-cut-llm-cost-with-prompt-caching))
 — numbers-led, via their public Discord/GitHub. They independently arrived at KRY's exact primitive and
 have zero third-party verification; the method is already theirs.
3. **Join FOCUS as a Contributing Member** ([focus.finops.org/about-focus](https://focus.finops.org/about-focus/))
 and file the savings-attestation extension issue — recognition with no sale and no membership fee.
4. **Stand up the LiteLLM-callback + Aider-log pilot** on your own traffic ([Aider repo](https://github.com/Aider-AI/aider)).
5. **Time-sensitive clock:** **Entrepreneur First** has a hard deadline of **June 22, 2026** (solo
 technical founders) — the only hard expiry among the strong funding fits; do it in parallel only if you
 want the funding track. (AI Grant's OSS track at [aigrant.org](https://aigrant.org/) is the realistic
 non-dilutive ask for a stdlib-only verifier.)
6. **Pre-stage the provider-credit programs** (Anthropic $25k / OpenRouter / Claude for Open Source) so a
 Stage-1 partner runs the pilot at zero cost.

---

## 10. The honest ceiling & caveats (what this path does *not* buy)

- **Provider REST APIs do not cryptographically sign usage** [V]. OpenAI, Anthropic, and OpenRouter all
 return rich per-request token/cost fields over TLS, but **none is a signed receipt** — it's an
 authenticated readout the *account owner controls*. `provider_metered` is a **corroboration tier, not a
 proof tier**; do not label it "stranger-verifiable" on its own. **The actual top-tier proof path is the
 one KRY already owns: TLS-notary / T2** (`kry_tlsn_*`) — converting the un-signed readout into something a
 stranger verifies without the provider's cooperation. A+ via the artifact gate is reachable *without* T2,
 but the strongest possible receipt needs it.
- **Router-vendor adoption is an inference, not a fact** [inf]. They verifiably *need* to prove savings;
 whether they outsource it to KRY vs. build their own (still operator-asserted) dashboard is the discovery
 question.
- **The shared-savings consultancy class is unverified** [inf] — the model is the theoretically perfect KRY
 home, but no named public player surfaced. Confirming one is the highest-value follow-up lead.
- **KRY's maturity** [V, self-flagged] — committed `research_grade`, one durable live anchor. The white
 space and the mechanism are verified; "fills it at scale" is the claim this path is designed to test, not
 a result already in hand.

---

## Appendix — link directory

**KRY internals:** `docs/KRY_READINESS.md` (Step 2 falsifier) · `docs/KRY_VERIFIED_SAVINGS_ARTIFACT.md`
(the gate) · `docs/KRY_RESEARCH_GRADE_ANCHOR.md` · `scripts/kry_verified_artifact.py` · `scripts/kry_verify.py`
· `scripts/kry_or_fetch.py` · `scripts/kry_savings_report.py`.

**Real-traffic channel:** https://github.com/BerriAI/litellm · https://docs.litellm.ai/docs/observability/custom_callback ·
https://docs.litellm.ai/docs/proxy/logging_spec · https://openrouter.ai/docs/api/api-reference/generations/get-generation ·
https://openrouter.ai/docs/cookbook/administration/usage-accounting · https://github.com/Aider-AI/aider ·
https://github.com/Aider-AI/aider/blob/main/aider/website/assets/sample-analytics.jsonl

**Symbiosis partners:** https://work.withmartian.com/ · https://github.com/withmartian/routerbench ·
https://www.notdiamond.ai/ · https://www.requesty.ai/ · https://openrouter.ai/ · https://portkey.ai/ ·
https://www.helicone.ai/ · https://projectdiscovery.io/blog/how-we-cut-llm-cost-with-prompt-caching ·
https://blog.premai.io/llm-cost-optimization-8-strategies-that-cut-api-spend-by-80-2026-guide/ ·
https://www.cloudzero.com/ · https://www.pay-i.com/ · https://www.finout.io/megabill · https://langfuse.com/

**Standards / ecosystem:** https://focus.finops.org/ · https://focus.finops.org/about-focus/ ·
https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec ·
https://www.finops.org/wg/how-to-calculate-effective-savings-rate-esr/ · https://www.finops.org/wg/finops-for-ai-overview/ ·
https://www.finops.org/community/community-slack/ · https://www.finops.org/join/ ·
https://www.prosperops.com/effective-savings-rate/ · https://verra.org/programs/verified-carbon-standard/

**Pilot venues / funding:** https://www.finops.org/community/community-slack/ · https://apply.joinef.com/ (June 22) ·
https://aigrant.org/ · https://www.southparkcommons.com/founder-fellowship/ · https://speedrun.a16z.com/apply ·
https://www.ycombinator.com/cofounder-matching · https://www.anthropic.com/startup-program-official-terms ·
https://news.ycombinator.com/show

**Skeptic's anchors:** https://arxiv.org/pdf/2501.01818 (router savings not holdout-validated) ·
https://www.prosperops.com/effective-savings-rate/ (admits no third-party verification) ·
https://docs.datadoghq.com/llm_observability/monitoring/cost/ ("estimated" cost).
