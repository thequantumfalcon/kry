# KRY Biomimicry — how nature verifies an unobservable claim

**Question.** A KRY cache hit is a *counterfactual* — a paid call that did not happen,
with no footprint. `kry_verify.py` will call a fabricated claim VALID (it reads
`veracity_floor = 0.0`). Can it be rejected? **Nature faced this exact problem for ~3.5
billion years** — symbionts, signallers, and cells all *claim* costly services a partner
cannot directly observe — and the answer it converged on is not "prevent cheating" but
**"make cheating not pay, so honesty is the stable equilibrium."** This note grounds
that in established biology and records what was built from it.

**Honesty tiering (so nothing is overclaimed):**
- 🔬 **Established** — peer-reviewed biology, cited.
- 🔗 **Analogy** — the mapping we draw to KRY (inspiration, not proof).
- 🛠 **In KRY** — what actually shipped (`src/kry/kry_sanctions.py`) or is proposed.

---

## 1. Host sanctions — monitor output, penalise cheats 🔬

**Biology.** In the legume–rhizobium symbiosis, the plant cannot see whether a
root-nodule bacterium is actually fixing nitrogen. Kiers et al. (2003, *Nature*) showed
the plant **monitors output and sanctions cheats**: nodules experimentally prevented
from fixing N₂ (Ar:O₂ atmosphere) suffered a **~50% cut in rhizobial reproductive
success**. Monitoring + punishment stabilises the mutualism against free-riders.

**🔗 Analogy.** KRY's `kry_reconcile.py` / `kry_or_fetch.py` already *monitor* output
(reconcile a metered claim against the provider's own usage record). What was missing
was the *sanction* — a consequence for a party whose claims don't reconcile.

**🛠 In KRY.** `kry_sanctions.record_reconciliation(party, confirmed)` drops a party's
reputation **multiplicatively** on a discrepancy (default ×0.5 — the rhizobial ~50%
cut) and raises it slowly on a confirmation. Trust is slow to earn, fast to lose.

## 2. Reciprocal rewards / biological markets — reward good partners 🔬

**Biology.** Kiers et al. (2011, *Science*) showed plants and arbuscular mycorrhizal
fungi run a **biological market**: plants send more carbon to fungal threads delivering
more phosphorus, and fungi send more nutrients to roots paying more carbon. Stability
comes from **bidirectional** preferential reward — "cheaters get punished and the good
guys get rewarded."

**🔗 Analogy.** Trust shouldn't only punish; it should *reward* — make honesty cheaper,
not just safer.

**🛠 In KRY.** `audit_rate_for(party)` audits high-reputation parties at the **floor
(2%)** and low-reputation parties toward **100%**. Good partners get a light touch
(less friction); slackers get heavy audit. Honesty becomes the low-cost path.

## 3. Costly signalling — tax cheating, not the honest signal 🔬

**Biology.** Zahavi's handicap principle (1975) held that signals are honest because
they are costly; Grafen (1990) gave the evolutionarily-stable formalisation. **Crucially,
the modern refinement** (Penn & Számadó, 2020, *Biological Reviews*) is that honesty is
stabilised by a **condition-dependent trade-off that makes *cheating* differentially
costly** — the cost at the honest equilibrium can be low or even zero. Honesty is cheap
*for the honest*; it's *lying* that must be expensive.

**🔗 Analogy.** Don't tax honest reporting (that just adds friction). Tax **getting
caught**. The holdout's cost falls on measurement; the penalty falls on fabrication.

**🛠 In KRY.** The ESS condition below sets the audit/penalty so the *expected payoff of
fabricating is negative*, while an honest report stays free.

## 4. Kinetic proofreading — spend energy to suppress errors exponentially 🔬

**Biology.** Hopfield (1974, *PNAS*) and Ninio (1975) showed cells achieve fidelity far
beyond thermodynamic equilibrium by inserting an **irreversible, energy-consuming delay**
between recognition and commitment — giving a wrong substrate extra chances to fall off.
Each independent check multiplies specificity; *n* checks suppress errors ~exponentially,
paid for in free energy.

**🔗 Analogy.** A flagged claim need not be accepted or rejected once. Route it through
*n* independent checks (holdout + reconciliation + a second provider witness); a fake
survives all *n* with probability ≈ qⁿ — exponential suppression at a linear cost.

**🛠 In KRY.** Proposed (not yet built): escalate audit depth for low-reputation parties
(a proofreading "delay" before a claim commits to the trusted ledger). The hook is
`audit_rate_for` rising toward 1.0 as reputation falls.

## 5. Immune two-signal rule — require an independent second signal 🔬

**Biology.** A T cell needs **two signals** to activate: antigen recognition (signal 1)
**and** costimulation (signal 2). Signal 1 alone induces **anergy** (silencing), not
action — the two-signal model (Bretscher & Cohn, 1970; Janeway, 1989). Self-reactive
clones are deleted by negative selection. The default is *fail-closed*.

**🔗 Analogy.** A self-reported claim is **signal 1 only**. It must not, by itself, earn
trust — it should be "anergic": recorded, but trust-weight 0 until an independent anchor
(signal 2) arrives.

**🛠 In KRY.** `two_signal_trust()` formalises exactly this: trust accrues only to the
externally-anchored fraction; self-report alone is anergic. This *is* `veracity_floor`,
recast as the immune costimulation rule.

## 6. Quorum-sensing policing — couple the public good to a private one 🔬

**Biology.** *Pseudomonas aeruginosa* "cheaters" that skip making shared public goods can
invade — but Dandekar et al. (2012, *Science*) showed quorum sensing also controls
**private** goods (intracellular metabolism), so a cheater that ignores the signal also
loses a private benefit. The coupling imposes a **metabolic constraint** that makes
cheating intrinsically self-penalising.

**🔗 Analogy.** Bind the cheap-to-fake claim to something the claimant *needs*, so faking
is self-limiting (a cheater who skips real holdout calls also forfeits the settlement /
reputation those calls would earn).

**🛠 In KRY.** Reputation *is* the coupled private good: it gates `audit_rate_for` and can
gate settlement acceptance — skipping honest reconciliation forfeits the low-friction
trust that honest parties enjoy.

---

## The honesty-stability (ESS) condition — the unifying arithmetic 🛠

A deliberately simple model (it is **not** a claim that biology computes this formula —
it is how KRY operationalises the convergent lesson above). A fabricated claim of value
*V* nets, in expectation:

```
E[cheat] = (1 − h)·V − h·(λ·V)
```

where **h** = audit/reconciliation rate (the holdout, kry_baseline) and **λ** = penalty
multiple on getting caught (reputation + escrow loss). Fabrication does not pay
(`E ≤ 0`) exactly when:

```
 h · (1 + λ) ≥ 1 ⇔ h ≥ 1/(1+λ) ⇔ λ ≥ (1−h)/h
```

This makes the design knobs explicit and is implemented in `kry_sanctions`:

| audit rate `h` | minimum penalty `λ` for stable honesty |
|---|---|
| 50% | 1× |
| 10% | 9× |
| 5% | 19× |
| **2%** | **49×** |

So KRY's 2% holdout is *exactly enough* to make fabrication unprofitable **if** a caught
cheat forfeits ~49× the cheated amount (reputation collapse + lost settlement). The
mechanism delivers that: one discrepancy multiplies reputation down and drives the
cheater's audit rate toward 100%, while honest partners settle into the 2% floor.

## What this does — and does NOT — solve

- **Does:** turns the fabricated cache hit from "undetectable and unpriced" into "stable
 to *report honestly*, costly to *fake repeatedly*." The fabricator's first lie still
 verifies VALID — but it reads `veracity_floor = 0.0`, and a *party* who keeps it up
 loses reputation, gets force-audited, and finds cheating a losing strategy. That is how
 nature stabilises every one of the mutualisms above.
- **Does not:** make a single fabricated claim cryptographically impossible (no system
 can — it's a counterfactual). It bounds the *equilibrium*, not the *one-shot*. Absolute
 per-event proof still requires the unbuilt TEE/T2 tier.
- **Caveat:** the ESS arithmetic is a first-order model. Real adversaries collude, and λ
 is only real if reputation is *costly to rebuild* (Sybil-resistant identity) — itself an
 open problem, flagged honestly.

---

**Sources (established biology):**
[Zahavi/Grafen handicap principle — Signalling theory (Wikipedia)](https://en.wikipedia.org/wiki/Signalling_theory) ·
[Penn & Számadó 2020, *Biological Reviews* — the handicap principle reassessed](https://onlinelibrary.wiley.com/doi/full/10.1111/brv.12563) ·
[Kiers et al. 2003, *Nature* — Host sanctions and the legume–rhizobium mutualism](https://www.nature.com/articles/nature01931) ·
[Kiers et al. 2011, *Science* — Reciprocal rewards stabilize cooperation in the mycorrhizal symbiosis](https://www.science.org/doi/10.1126/science.1208473) ·
[Hopfield 1974, *PNAS* 71(10):4135 — Kinetic proofreading (overview)](https://en.wikipedia.org/wiki/Kinetic_proofreading) ·
[Two-signal model & costimulation / negative selection (Nature Sci Rep review)](https://www.nature.com/articles/srep00769) ·
[Dandekar et al. 2012, *Science* — Quorum sensing and metabolic incentives to cooperate](https://www.science.org/doi/10.1126/science.1227289)
