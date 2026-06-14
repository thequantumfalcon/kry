# Prefix-caching lever on real multi-turn traffic — defeating the "one lever / consumer-chat" caveats

**Date:** 2026-06-10 · **Instrument:** [`scripts/kry_multiturn_prefix_savings.py`](../../../scripts/kry_multiturn_prefix_savings.py)
· **Corpus:** allenai/WildChat-1M, multi-turn conversations only · **Cost:** free (no API)

## What it measures and why

The 15.7% exact-match number had two honest caveats: it's *one* lever, and it's from *consumer
chat*. Multi-turn / agent / coding workloads re-send the **entire prior context on every call** —
the same structural pattern (shared system prompt + history + RAG every turn). That re-sent
prefix is cacheable; providers bill cached reads at ~10–50% of full price. This measures the
prefix lever directly, on real multi-turn traffic.

## Result (468 real multi-turn conversations)

- aggregate input tokens **2,317,294**; re-sent prefix (cacheable) **1,636,900**
- **prefix-cacheable fraction of input: 70.6%** (spend-weighted) · median per-conversation **46.8%**
- → input savings at **Anthropic** cached-read (~90% off): **63.6%**
- → input savings at **OpenAI** cached-input (~50% off): **35.3%**

The spend-weighted 70.6% exceeds the median 46.8% because long conversations dominate token
volume — exactly where context-heavy agent/coding workloads live.

## Robustness (5 disjoint folds + bootstrap)

One clean pull (1,327 multi-turn conversations) split into 5 disjoint folds: **71.4 / 73.3 /
70.4 / 71.8 / 69.1%** — **mean 71.2%, spread 4.2 pts**, bootstrap 95% CI **[69.2%, 73.2%]**
(2,000 resamples). Cross-offset samples (offsets 0–150k) independently landed 65–70%. The
prefix-cacheable fraction is stable across folds and slices; earlier empty slices were HF
datasets-server rate-limiting (cumulative request count), not data. The instrument supports a
`start` offset and retries transient resets.

## What it defeats (and what it doesn't)

- **Defeats "consumer-chat-specific":** this IS the agent/coding pattern (re-sent context),
 measured on real traffic, not asserted.
- **Defeats "one lever":** the prefix lever is now a measured number (70.6% cacheable →
 35–64% input savings), additive to the 15.7% exact-match cache (different mechanism — exact
 repeats *across* requests vs. re-sent context *within* a conversation; no double-count).

## Honest bounds

1. **Input-side only.** Output tokens are never cacheable. This is a fraction of *input* spend.
 It matters most for input-heavy workloads (big system prompts, RAG, code context, long
 history) — i.e. exactly agents/coding — and less for short-prompt/long-output workloads.
2. **Prefix caching is a provider feature, not a KRY invention.** Anthropic/OpenAI already
 ship it; a sophisticated buyer (e.g. Salesforce on Anthropic) may *already* capture part of
 this, so it is not all incremental on top of their current bill. KRY's role is to **measure,
 prove, and credit** the retained dollars across levers (via the holdout/attestation/verify
 machinery) — and to capture the levers a given deployment isn't using yet (cross-user
 exact-match cache, semantic dedup, routing).
3. **char/4 token estimate.** The fraction is a ratio of token counts, so it is approximately
 tokenizer-independent.

## Remaining R&D (not yet defeated)

- **Semantic near-duplicate caching** — raises the exact-match 15.7% (paraphrases become hits);
 measurable next.
- **Routing (cheaper model when adequate)** — honestly cannot be claimed without the
 acceptance-gate specificity work (the acceptance-gate measurement). Not counted.
