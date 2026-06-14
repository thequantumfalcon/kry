# KRY Savings Analysis — measured levers + a LOW-CONFIDENCE company estimate

> **Status: ESTIMATE, not evidence.** The lever rates below are MEASURED on real traffic
> (cited). The company numbers are a **class-level transplant onto described workloads with
> ZERO company-specific traffic measured** — confidence LOW, gross of implementation cost.
> Produced 2026-06-10 via two adversarially-verified estimation passes (each estimate
> challenged for over-claim, then conservatized).

## The measured levers (real traffic, executed/holdout-validated)

| Lever | Measured rate | Source | Honest bound |
|---|---|---|---|
| Exact-match cache | 15.7% of requests (consumer) | [wildchat_proof](evidence/wildchat_proof/FINDINGS.md) | consumer replay; enterprise lower (unique IDs/timestamps break repeats) |
| Prefix caching | 71.2% of input tokens re-sent (CI [69,73]) | [prefix_savings](evidence/prefix_savings/FINDINGS.md) | input-side only; **80–95% already captured** by sophisticated buyers |
| **Routing** | **84% cheap-adequacy on code → up to ~75% cost-avoidance** (model-pair-specific [gpt-4o-mini↔gpt-4o ~16× price]; assumes the avoided frontier call would have been kept — *not* separately tested) | [code_routing](evidence/code_routing/FINDINGS.md) | HumanEval = **toy + contaminated CEILING**; production 25–45% |

## The estimate (averaged, adversarially corrected)

| Company | routing | cache | prefix | NEW total |
|---|---|---|---|---|
| Salesforce ($300M/yr Anthropic) | 4% | 3% | 1% | 2–13% |
| OpenAI top spender (100B tok/mo) | 7% | 5% | 2.5% | 6–30% |
| Uber (Claude Code) | 9% | 2.5% | 3% | 8–22% |
| **Average** | | | | **5.3–21.7%** |

**Most honest single number: ~10% of total LLM spend** (central), within a wide 5–22% band,
conditional on a real-traffic adequacy measurement, gross of engineering cost.

## In dollars per year (only one base is real dollars — the rest are flagged)

| Company | $ base (documented) | NEW saving % | **$/yr saved** |
|---|---|---|---|
| **Salesforce** | **$300M/yr** (stated in $) | 2–13% (central ~8%) | **~$24M/yr** (range $6M–$39M) |
| OpenAI top spender | ~$4–12M/yr (100B tok/mo priced as tokens — *not* billions) | 6–30% | ~$0.2M–$3.6M/yr — % is the real answer |
| Uber | **not public** | 8–22% | n/a until the budget figure |
| **Market TAM** | ~$8.4B model-API spend (*Menlo estimate*) | ~10% central | **~$840M/yr** (range $420M–$1.85B) |

**Only Salesforce's ~$24M/yr rests on a real dollar base** (it's the one company that stated spend
in dollars). The OpenAI "top spender" is the trap: 100B tokens/month is only ~$4–12M/yr of *token*
cost, so the dollar saving is small and the base swings ~30× with model mix — the percentage is the
answer, not the dollars. Everything here is **gross of implementation cost** and leans on the
routing lever, which the acceptance-gate measurement confirmed is gated by the adequacy gate (a correctness-layer path then unlocks it).

## The load-bearing finding

Routing is the **dominant lever AND the most uncertain** — it compounds two numbers measured
on zero company traffic (routable fraction × production cheap-adequacy). The gap between the
~10% central and the 22% ceiling **is the adequacy-gate problem**: routing only pays where a
cheap gate can *safely* escalate, and the repo's own [compute_wall](evidence/compute_wall/FINDINGS.md)
shows cheap models fail *fluently* on hard tasks — a surface gate keeps those wrongly.

## How the number honestly climbs

Not more estimation. Two concrete moves:
1. **Solve the adequacy gate** (measure `adequacy_gate` specificity → safe escalation) — the acceptance-gate work. This is the unlock between ~10% and ~22%.
2. **Measure the real routable fraction** on actual customer traffic, replacing HumanEval's
 toy 84%.

Until then, **~10% is the defensible figure**; the 22% ceiling requires the gate solved and the
routable fraction measured. Claiming the ceiling today would be the exact over-claim the
adversarial audit removed.

## What KRY's role actually is

Not "we find you 40% savings" (much of it — prefix caching — buyers already capture). KRY's
honest value is to **measure, prove, and credit** the genuinely-incremental retained dollars
(the ~10%) via the holdout/attestation/verify machinery — making savings an *auditable,
creditable* quantity — and to unlock routing's upside once the adequacy gate is solved.
