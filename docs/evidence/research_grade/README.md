# research_grade anchor — offline-verifiable bundle (2026-06-10)

The self-contained evidence for KRY's committed **`research_grade`** label (see
[`../../KRY_RESEARCH_GRADE_ANCHOR.md`](../../KRY_RESEARCH_GRADE_ANCHOR.md)). You can re-verify the
independent-oracle agreement **offline** — no API key, no network — from the two files here.

## Files
- **`research_grade_mintlog.jsonl`** — the 52 fresh `provider_metered` (T1) mint receipts from the
 2026-06-10 durable run (the exact `--since 1781153168` window the grader read). Content-free: each
 carries a real OpenRouter `gen-…` id + host-metered token counts, no prompt/completion text.
- **`or_provider_export.json`** — OpenRouter's **own** per-request generation records for those 52
 gen-ids, captured 2026-06-10 (`id`, `native_tokens_prompt/completion`, `total_cost`, `provider_name`).
 This is the independent, non-self-referential oracle.

## Re-verify it yourself (offline, stranger-reproducible)
```bash
python3 scripts/kry_research_grade.py docs/evidence/research_grade/research_grade_mintlog.jsonl \
 --provider-export docs/evidence/research_grade/or_provider_export.json
# -> matched 52/52 · independent agreement 1.00 (bar 0.80) · research_grade REACHED
```
(or just the reconciliation: `python3 scripts/kry_reconcile.py research_grade_mintlog.jsonl --provider-export or_provider_export.json` → 52/52 matched.)

## Honest scope
- This is the **fresh-run window** (`--since`) — the handoff's explicit "one fresh real run" intent.
 The *all-time* host-system ledger still scores ~0.12 because 14 **legacy** gen-ids are OpenRouter-purged
 (older than its retention window) — genuinely **un-fetchable, not refuted**. The fresh corpus is the
 reconcilable one, and it reconciled **52/52 at tolerance 0**.
- `research_grade`, **not** A+: `production_ready` still needs an independent real-world corpus at
 scale + a real counterparty (external).

## Provenance
Extracted from the live host-system ledger `kry_data/kry_mint_log.jsonl` (the host system
that ran the floor); full run record: the operator's private run log. `confirm()`
fired 50/50 within the 900 s TTL — the stall that expired the first 18 (see `../freetest/`) is broken.
