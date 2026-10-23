# KRY Readiness — the pre-dated rubric (and how to actually get there)

A prior audit found the real bug behind the
"perfect → A+ → problems" drift: **"A+" was never *defined*.** It had no independent,
pre-dated criterion, so it could be claimed, walked back, and chased forever. This
fixes that by adopting an **external, pre-dated rubric** — a prior epistemic-readiness ladder — and grading KRY against it *mechanically*.

Rubric and grader live in `src/kry/kry_capabilities.py` (`readiness_label`,
`verify_capabilities`), checked by `tests/test_capabilities.py`. The grade is computed,
not asserted.

## The ladder (weakest → strongest evidence)

```
prototype < prototype_plus < internally_consistent < research_grade < production_ready
```

| Level | Evidence required | KRY status |
|---|---|---|
| internally_consistent | the **synthetic** test suite is fully green | ✅ cleared |
| research_grade | + agreement **≥ 0.80** with an **independent, non-self-referential** oracle | Historical 2026-06-10 result; retained files match 52/52 IDs and counts. Current per-request grading additionally requires exact IDs and `RECONCILED`. See evidence limits below. |
| production_ready | + validation on an **independent real-world corpus** (real traffic) **+ clean capability audit** | ❌ needs real traffic + counterparty |

**The top label structurally requires external evidence.** That is not pessimism — it
is the grader refusing to let *more code* buy a grade only *real data* can earn
(`tests/test_capabilities.py::test_production_ready_requires_external_evidence_code_alone_cannot_reach_it`).

## Historical readiness record and current evidence limits

```
audit_clean: True # all 14 'implemented' capabilities resolve to real code + tests
status counts: 14 implemented · 1 scaffolded · 4 not_guaranteed (disclosed limits)
historical run label (2026-06-10): research_grade
```

**Milestone (2026-06-09):** the external-anchor *mechanism* was first demonstrated on **real**
OpenRouter traffic — 18 real free-model displacement calls reconciled 18/18 (agreement 1.00). Those
18 were *unconfirmed/expired* displacements (a veracity proof-of-mechanism, not an accepted-savings
corpus), so the label correctly stayed `internally_consistent` then. Bundle:
[`KRY_FIRST_REAL_ANCHOR.md`](KRY_FIRST_REAL_ANCHOR.md).

**Retained 2026-06-10 bundle:** all 52 request IDs match one-to-one and their token
counts agree. The records contain **0 KRY and $0 provider cost**, from free-tier
self-traffic. The original driver reported `research_grade` using token-count
matching; that algorithm also accepted rewritten IDs and partial discrepant
coverage. The current driver rejects those cases.

The historical v2 hashes do not bind `metered_tokens` or request IDs. Rechecking the
files is an offline consistency check, not authentication of the provider export,
proof of nonzero savings, or financial settlement. Current v7 hashes bind token
counts but not request IDs in `detail`. A manifest's optional request-ID digest
supports comparison without publishing the raw ID; it does not establish source
provenance. Retained-file hash tests detect later edits only. Full scope:
[`KRY_RESEARCH_GRADE_ANCHOR.md`](KRY_RESEARCH_GRADE_ANCHOR.md).

Current local verification command:

```bash
python3 -m pytest tests/ -q
```

Optional crypto/TEE/PQC tiers are evidence add-ons, not hidden prerequisites for the
stdlib label. If their dependencies are missing, those tests must skip or fail closed;
they do not turn synthetic self-consistency into real-world validation.

An offline `research_grade` result remains conditional on the supplied records'
provenance and the reported synthetic replay result. It is not a substitute for
independent real-world validation, buyer evidence or external review.

## The two steps to `production_ready`

### Step 1 → `research_grade`: reconcile identifiable provider records

Use provider records obtained through an authorized export or reporting API and
retain their collection window and provenance. For per-request grading, every
receipt must have a unique matching request ID and matching token counts within
the selected tolerance. Missing, duplicated, conflicting or mismatched IDs fail;
the `RECONCILED` verdict is required. Token-count-only legacy comparisons remain
available as diagnostics but cannot advance the driver or artifact grade.

```bash
python3 scripts/kry_research_grade.py kry_data/kry_mint_log.jsonl --fetch
# requires OPENROUTER_API_KEY; fetches records within the selected window
```

Or recheck already retained records without network access:

```bash
python3 scripts/kry_research_grade.py docs/evidence/research_grade/research_grade_mintlog.jsonl \
 --provider-export docs/evidence/research_grade/or_provider_export.json
```

The generic readiness rubric still uses the pre-existing ≥ 0.80 threshold. The
raw match fraction is diagnostic; a `DISCREPANCY` cannot advance the grade even
when that fraction is ≥ 0.80. Aggregate mode also requires `RECONCILED`, with an
explicit time window and its configured tolerance; it compares window totals,
not individual requests. Neither mode authenticates a supplied export.

### Step 2 → `production_ready`: an independent real-world corpus
Run **real traffic** (not `examples/gen_dataset.py` synthetic) through the savings
report + a live 2% holdout for a bounded window, and have the resulting attestation
checked by a third party with `scripts/kry_verify.py`.

- **MVF (pass):** on real logs, the holdout CI brackets the reconciled provider rate,
 the savings number survives a stranger's verification, and the capability audit stays
 clean.
- **MVF (fail):** the real-world holdout rate falls outside the synthetic expectation,
 or a real log breaks the report → the model was overfit to synthetic data. Again, the
 negative is the finding.

## What `production_ready` does NOT require (scope ≠ validation)

Four capabilities are **permanently out of scope for any software** and are shipped as
**disclosures**, not defects (a datasheet, not a TODO):

- `per_event_counterfactual_proof` — a cache hit is a counterfactual; only a population
 holdout or a TEE can witness it, never a per-event cryptographic proof.
- `source_truth_of_self_report` — no software proves a self-reported saving happened;
 `veracity_floor = 0.0` is the honest label.
- `sybil_resistant_identity` — the sanction penalty is only real with costly identity.
- `real_world_validated_savings` — closed by Step 2 above, not by code.

Blocking `production_ready` on these would conflate *scope* with *validation*. It requires the audit to
be clean and the real-corpus bar met — **with the out-of-scope items honestly disclosed**.

## Remaining evidence requirements

The retained 52-record bundle supports exact ID and token consistency with zero
recorded savings and charges. It does not establish independent acceptance,
paid customer value, a counterfactual savings amount or payment settlement.
`production_ready` requires independent real-world evidence and a clean capability
audit; passing more local tests cannot supply that external evidence.
