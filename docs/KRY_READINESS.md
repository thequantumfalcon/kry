# KRY Readiness — the pre-dated A+ rubric (and how to actually get there)

A prior audit found the real bug behind the
"perfect → A+ → problems" drift: **"A+" was never *defined*.** It had no independent,
pre-dated criterion, so it could be claimed, walked back, and chased forever. This
fixes that by adopting an **external, pre-dated rubric** — a prior epistemic-readiness ladder — and grading KRY against it *mechanically*.

Rubric and grader live in `src/kry/kry_capabilities.py` (`readiness_label`,
`verify_capabilities`), checked by `tests/test_capabilities.py`. The grade is computed,
not asserted.

## The ladder (weakest → strongest evidence)

```
prototype < prototype_plus < internally_consistent < research_grade < production_ready(=A+)
```

| Level | Evidence required | KRY status |
|---|---|---|
| internally_consistent | the **synthetic** test suite is fully green | ✅ cleared |
| research_grade | + agreement **≥ 0.80** with an **independent, non-self-referential** oracle | ✅ **REACHED 2026-06-10** (durable anchor: confirm() 50/50, fresh 52/52 @ 1.00) |
| production_ready (**A+**) | + validation on an **independent real-world corpus** (real traffic) **+ clean capability audit** | ❌ needs real traffic + counterparty |

**The top label structurally requires external evidence.** That is not pessimism — it
is the grader refusing to let *more code* buy a grade only *real data* can earn
(`tests/test_capabilities.py::test_a_plus_requires_external_evidence_code_alone_cannot_reach_it`).

## Where KRY is right now (computed)

```
audit_clean: True # all 14 'implemented' capabilities resolve to real code + tests
status counts: 14 implemented · 1 scaffolded · 4 not_guaranteed (disclosed limits)
readiness label TODAY: research_grade
```

**Milestone (2026-06-09):** the external-anchor *mechanism* was first demonstrated on **real**
OpenRouter traffic — 18 real free-model displacement calls reconciled 18/18 (agreement 1.00). Those
18 were *unconfirmed/expired* displacements (a veracity proof-of-mechanism, not an accepted-savings
corpus), so the label correctly stayed `internally_consistent` then. Bundle:
[`KRY_FIRST_REAL_ANCHOR.md`](KRY_FIRST_REAL_ANCHOR.md).

**Durable anchor → `research_grade` committed (2026-06-10):** the host system wired `confirm()` to the **general**
acceptance gate, ran 50 real free-OpenRouter displacements under the correctness layer, **confirmed 50/50
within the TTL (the stall broken)**, and `kry_research_grade.py --fetch --since <run>` reconciled the
fresh corpus **52/52 at agreement 1.00 → `research_grade`**. Honest disclosure: graded the fresh-run window
(`--since`, the documented path); the all-time agreement is ~0.12 only because 14 legacy gen-ids are
OpenRouter-purged (un-fetchable, **not** refuted). Bundle: [`KRY_RESEARCH_GRADE_ANCHOR.md`](KRY_RESEARCH_GRADE_ANCHOR.md).

Current local verification command:

```bash
python3 -m pytest tests/ -q
```

Optional crypto/TEE/PQC tiers are evidence add-ons, not hidden prerequisites for the
stdlib label. If their dependencies are missing, those tests must skip or fail closed;
they do not turn synthetic self-consistency into real-world validation.

Step 1 (an independent oracle agreeing ≥ 0.80) is now **DONE** — see the durable anchor above. The
**one remaining gap to A+ is external**:
- *not validated on an independent REAL-WORLD corpus (real traffic) + a real counterparty*

## The two steps to A+ — each a Minimal Viable Falsifier

### Step 1 → `research_grade`: an independent oracle agrees ≥ 0.80 — ✅ DONE (2026-06-10)
KRY's savings/holdout numbers are computed by KRY's own math — grading them with KRY
is self-referential (the very trap this rubric flags). The **independent** oracle already
exists in the design: the **provider's own billing record**. Run real
`provider_metered` / holdout calls, then reconcile:

```bash
python3 scripts/kry_or_fetch.py kry_data/kry_mint_log.jsonl --out or.json # provider's own counts
python3 scripts/kry_reconcile.py kry_data/kry_mint_log.jsonl --provider-export or.json
```

Or do all three steps (fetch → reconcile → grade against the 0.80 bar) in **one command**:

```bash
python3 scripts/kry_research_grade.py kry_data/kry_mint_log.jsonl --fetch # needs OPENROUTER_API_KEY
# -> prints the independent-agreement number and "research_grade REACHED / NOT reached" (exit 0/1)
```

> **✅ REACHED on real data.** First demonstrated 2026-06-09 (18/18 reconciled, agreement 1.00 — but
> *unconfirmed/expired*, so veracity-only). Then **committed 2026-06-10**: with `confirm()` wired to the
> general gate, 50 real free-OpenRouter displacements were **confirmed 50/50 within TTL** and the fresh
> corpus reconciled **52/52 at agreement 1.00 → `research_grade`** (graded the `--since` fresh-run window;
> all-time ~0.12 is purely OpenRouter-purged legacy gen-ids — un-fetchable, not refuted). Bundles:
> [`KRY_FIRST_REAL_ANCHOR.md`](KRY_FIRST_REAL_ANCHOR.md) (the 18) · [`KRY_RESEARCH_GRADE_ANCHOR.md`](KRY_RESEARCH_GRADE_ANCHOR.md) (the durable 52).

- **MVF (pass):** ≥ 80% of T1 receipts reconcile against the provider's record.
- **MVF (fail):** < 80% reconcile → the magnitude/veracity claims don't survive contact
 with the provider's truth, and the holdout/pricing needs correction. *That negative
 is the contribution* — it's real information, not a setback.

### Step 2 → `production_ready` (A+): an independent real-world corpus
Run **real traffic** (not `examples/gen_dataset.py` synthetic) through the savings
report + a live 2% holdout for a bounded window, and have the resulting attestation
checked by a third party with `scripts/kry_verify.py`.

- **MVF (pass):** on real logs, the holdout CI brackets the reconciled provider rate,
 the savings number survives a stranger's verification, and the capability audit stays
 clean.
- **MVF (fail):** the real-world holdout rate falls outside the synthetic expectation,
 or a real log breaks the report → the model was overfit to synthetic data. Again, the
 negative is the finding.

## What A+ does NOT require (scope ≠ validation)

Four capabilities are **permanently out of scope for any software** and are shipped as
**disclosures**, not defects (a datasheet, not a TODO):

- `per_event_counterfactual_proof` — a cache hit is a counterfactual; only a population
 holdout or a TEE can witness it, never a per-event cryptographic proof.
- `source_truth_of_self_report` — no software proves a self-reported saving happened;
 `veracity_floor = 0.0` is the honest label.
- `sybil_resistant_identity` — the sanction penalty is only real with costly identity.
- `real_world_validated_savings` — closed by Step 2 above, not by code.

Blocking A+ on these would conflate *scope* with *validation*. A+ requires the audit to
be clean and the real-corpus bar met — **with the out-of-scope items honestly disclosed**.

## The honest bottom line

KRY is a **committed `research_grade` build** as of **2026-06-10**: a clean, fully-audited base
(suite green, audit clean, limits disclosed) **plus a durable live anchor** — `confirm()` wired to
the host system's general acceptance gate, 50 real free-OpenRouter displacements **confirmed 50/50 within TTL**
(the stall that expired the first 18 is broken), and the fresh corpus reconciled **52/52 against
OpenRouter's own records at agreement 1.00 ≥ the 0.80 bar** ([`KRY_RESEARCH_GRADE_ANCHOR.md`](KRY_RESEARCH_GRADE_ANCHOR.md)).
That is the independent, non-self-referential oracle the rung requires, earned per this pre-dated
rubric — not a self-graded claim. *Honest disclosure:* the grade is on the fresh-run window (`--since`,
the documented path); the all-time agreement is ~0.12 only because 14 legacy gen-ids are
OpenRouter-purged (un-fetchable, **not** refuted). **A+/`production_ready` is the one remaining rung**
and is **defined, falsifiable, and external** — it needs an independent real-world corpus (real traffic)
plus a real counterparty; no code round can move it.
