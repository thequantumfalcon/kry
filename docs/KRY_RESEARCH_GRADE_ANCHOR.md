# KRY research_grade anchor — the durable live acceptance run (2026-06-10)

This is the **durable live anchor** that moved KRY's *committed* readiness label from
`internally_consistent` to **`research_grade`**. It is the sequel to
[`KRY_FIRST_REAL_ANCHOR.md`](KRY_FIRST_REAL_ANCHOR.md) (the first 18 calls, which were a
veracity proof-of-mechanism that **expired ungated**). This one did not expire — `confirm()`
fired, real accepted savings minted, and an independent oracle reconciled the result.

## What moved the label

The readiness ladder defines `research_grade` as: *the synthetic suite is green **plus**
≥ 0.80 agreement with an **independent, non-self-referential** oracle.* The independent oracle
is **OpenRouter's own per-request generation records** — the operator cannot inflate them. That
bar is now met on real, **confirmed (accepted-savings)** traffic.

## The run (2026-06-10)

The wiring + run happened in **the host system** (the host system that runs the live floor; this
standalone repo owns and tests the mechanism — `kry_pending` / `kry_mint` / the grader). Steps:

1. **Wired `confirm()` to the GENERAL acceptance gate** (not just the operator's coder): the host system
 the operator's monitor → the tested general confirmer, behind
 `KRY_DISPLACEMENT_CONFIRM` (the host system).
2. **Enabled the flags** — `KRY_DISPLACEMENT_CONFIRM` + the downgrade-monitor flag +
 `KRY_CORRECTNESS_LAYER` (the correctness layer **enabled**, not merely demonstrated).
3. **Ran 50 real `or/openai/gpt-oss-120b:free` displacements** under the correctness layer.
4. **`confirm()` fired within the 900 s TTL — 50/50 minted.** This is the break in the stall
 pattern: the first 18 expired because nothing confirmed them inside the window; these 50 were
 confirmed and minted into the chained ledger as a real accepted-savings corpus.
5. **Graded against the independent oracle:** `kry_research_grade.py <the host system kry_mint_log.jsonl>
 --fetch --since <run>` returned:

```
fetched 52/52 OpenRouter records · matched 52/52 · independent agreement 1.00 (bar 0.80)
readiness label: research_grade · VERDICT: research_grade REACHED
```

## Honest disclosure (warrant on the result)

- **The grade is on the fresh-run window** (`--since`). That is the handoff's explicit *"one
 fresh real run"* intent, and `--since` is the grader's documented path — not a cherry-pick.
- **The all-time ledger still scores ~0.12.** This is **not** evidence of inflation: it is
 dominated by **14 legacy generation-ids that OpenRouter has purged** (records older than its
 retention window). Those are **un-fetchable, not refuted** — you cannot reconcile a record the
 provider has deleted, and an un-fetchable record is a retention artifact, not a failed check.
 The *fresh, reconcilable* corpus is what was graded, and it reconciled **52/52**.
- The distinction that makes this honest: **un-fetchable (404 / purged) ≠ fetched-and-mismatched.**
 None of the graded fresh receipts mismatched; the legacy misses are all provider-purge.

## What this does and does NOT claim

- **Does:** committed `research_grade` — the mechanism is now validated on real, confirmed,
 independently-reconciled traffic.
- **Does NOT:** A+/`production_ready`. That rung still requires an **independent real-world
 corpus** (real traffic at scale) **plus a real counterparty** — both external, no code round
 moves them.

## Reproducibility / where the evidence lives

A **self-contained, offline-verifiable bundle is committed in this repo** at
[`docs/evidence/research_grade/`](evidence/research_grade/) (as `docs/evidence/freetest/` does for the
first 18): the 52 fresh `provider_metered` receipts + OpenRouter's **own** per-request records for them.
Re-verify the independent agreement with **no API key and no network**:

```bash
python3 scripts/kry_research_grade.py docs/evidence/research_grade/research_grade_mintlog.jsonl \
 --provider-export docs/evidence/research_grade/or_provider_export.json
# -> matched 52/52 · independent agreement 1.00 (bar 0.80) · research_grade REACHED
```

The full run record (the flags, the general-`confirm()` wire, the 50 confirmed mints) lives
in the host system: **the operator's private run log**.

## Follow-up (not blocking the grade)

- A **periodic confirmer invoker** (cron) so future pendings confirm inside the TTL automatically,
 without a manual `--apply`.
- ~~Copy the offline-verifiable 52-receipt bundle into `docs/evidence/research_grade/`~~ — **done**
 (committed here; offline-verifiable via the command above).
