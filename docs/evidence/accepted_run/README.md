# Accepted-savings reconciliation — committed, real, research_grade

This is the second real external anchor, and it closes the half the first one
([../freetest/](../freetest/)) left open. The freetest reconciled *expired, unaccepted*
displacements — **veracity only**. This bundle reconciles displacements that **passed the
acceptance gate before they were minted** — **accepted savings**.

## Result (offline-verifiable from this bundle)

```
T1 (provider_metered) receipts: 8
provider records matched: 8/8
independent agreement: 1.00 (bar 0.80)
VERDICT: research_grade REACHED
```

Reproduce with no key (the provider records are committed here):

```bash
python3 scripts/kry_research_grade.py docs/evidence/accepted_run/accepted_mintlog.jsonl \
 --provider-export docs/evidence/accepted_run/or_provider_export.json --tolerance 2
```

## What was actually done

1. Made **8 real calls** to OpenRouter's free model `openai/gpt-oss-120b:free`.
2. **Accepted** each via the same `adequacy_gate` criterion the bridge uses (non-empty, no
 refusal/error marker, not truncated). All 8 passed.
3. **Minted** the accepted ones as committed, hash-chained `provider_metered` receipts
 (each carrying its real `/openrouter:<gen-id>`) into an **isolated, throwaway**
 `KRY_DATA_DIR` — **not** the live host-system floor.
4. **Reconciled** those committed mints against OpenRouter's own per-request generation
 records (`kry_or_fetch` → `kry_research_grade`): 8/8 match → agreement 1.00.

The acceptance wire for general traffic lives in
a downstream consumer in the host system (+ the operator's monitor
veto, with a 6-test suite); here the same adequacy criterion was applied directly in the
isolated run, because editing the bridge and running the live floor were both off-limits.

## Honest scope — what this is NOT

- **Isolated demonstration batch**, not organic the host system floor traffic — the calls were
 generated for this run.
- **The avoided model is DECLARED** (`anthropic/claude-opus-4.8`), not a real routing
 decision — so the dollar *magnitude* is illustrative. The **veracity** (provider confirms
 the real token counts) and **acceptance** (adequacy passed) are real.
- **"Accepted" = passed `adequacy_gate`** — a serve-time/quality proxy, not a per-event
 correctness proof (that needs a task oracle, as in the operator's coder path).
- **The repository's official `readiness_label()` is NOT flipped.** This is an isolated
 bundle, like freetest; the committed reference ledger is unchanged. What this proves is
 that the **accepted → minted → reconciled mechanism works end-to-end on real calls**.

Generated 2026-06-10.
