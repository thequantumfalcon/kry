#!/usr/bin/env python3
"""Measure the PREFIX-CACHING savings lever on real MULTI-TURN traffic.

Defeats two caveats on the 15.7% exact-match number at once:
  - "the rate is consumer-chat-specific": multi-turn conversations re-send the ENTIRE prior
    context on every call — the same structural pattern as agent/coding workloads (shared
    system prompt + history + RAG every call). So this measures the agent-representative lever.
  - "15.7% is one lever": the re-sent prefix is cacheable; providers bill cached reads at
    ~10-50% of full price. This is a SECOND, additive lever, measured here on real traffic.

Method: for each real WildChat multi-turn conversation, simulate the standard API pattern —
to answer user turn i you send all prior messages + the new user message. The prior messages
were already sent in the previous request, so they are a cacheable PREFIX. Aggregate the
fraction of input tokens that are re-sent prefix, and the savings under documented discounts.

Token counts are char/4 estimates; the prefix FRACTION is ~tokenizer-independent (it is a
ratio of token counts). Input-side only — output tokens are never cacheable. Free; no API.

  python3 scripts/kry_multiturn_prefix_savings.py [N_rows]
"""
from __future__ import annotations
import json, statistics, sys, time, urllib.parse, urllib.request
from pathlib import Path

WILDCHAT = "allenai/WildChat-1M"
ANTHROPIC_CACHE_READ = 0.10   # documented: cached reads ≈ 10% of full price (≈90% off)
OPENAI_CACHE_INPUT = 0.50     # documented: cached input ≈ 50% off


def toks(s: str) -> int:
    return max(1, len(s or "") // 4)


def pull(n_rows: int, start: int = 0) -> list[list[int]]:
    out: list[list[int]] = []
    for off in range(start, start + n_rows, 100):
        url = (f"https://datasets-server.huggingface.co/rows?dataset={urllib.parse.quote(WILDCHAT)}"
               f"&config=default&split=train&offset={off}&length=100")
        d = None
        for attempt in range(4):                       # retry transient resets / rate-limits
            try:
                d = json.load(urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "kry"}), timeout=40))
                break
            except Exception as e:
                if attempt == 3:
                    print(f"  pull gave up at {off}: {e}", file=sys.stderr)
                else:
                    time.sleep(1.5 * (attempt + 1))
        if d is None:
            break
        for r in d.get("rows", []):
            c = r["row"].get("conversation") or []
            if sum(1 for m in c if m.get("role") == "user") >= 2:   # multi-turn only
                out.append([toks(m.get("content", "")) for m in c])
    return out


def convo_stats(tl: list[int]) -> tuple[int, int]:
    """Return (total_input_tokens, cacheable_prefix_tokens) over all user turns of one convo.
    Request for the user msg at index j sends tl[:j+1]; the prefix tl[:prev] was already sent
    in the previous request, so it is cacheable."""
    total = cache = prev = 0
    for j in range(0, len(tl), 2):          # user messages at even indices (convo starts with user)
        total += sum(tl[:j + 1])
        cache += sum(tl[:prev])
        prev = j + 1                        # this request's input becomes the next request's prefix
    return total, cache


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    convos = pull(n, start)
    T = C = 0
    fracs = []
    for tl in convos:
        t, c = convo_stats(tl)
        T += t
        C += c
        if t:
            fracs.append(c / t)
    frac = C / T if T else 0.0
    med = statistics.median(fracs) if fracs else 0.0
    out = {
        "schema": "kry_multiturn_prefix_savings/v1", "corpus": WILDCHAT,
        "multi_turn_convos": len(convos),
        "aggregate_input_tokens": T, "cacheable_prefix_tokens": C,
        "prefix_cacheable_fraction": round(frac, 4),
        "median_per_convo_fraction": round(med, 4),
        "savings_anthropic_cached_read_pct": round(frac * (1 - ANTHROPIC_CACHE_READ), 4),
        "savings_openai_cached_input_pct": round(frac * (1 - OPENAI_CACHE_INPUT), 4),
        "bounds": ["input-side only (output never cacheable); char/4 token estimate; "
                   "assumes standard re-send-full-history multi-turn + provider prefix caching; "
                   "this is the prefix lever, SEPARATE from and additive to the 15.7% exact-match cache"],
    }
    Path("docs/evidence/prefix_savings").mkdir(parents=True, exist_ok=True)
    Path("docs/evidence/prefix_savings/prefix_savings.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"multi-turn convos analysed: {len(convos)} (real WildChat, >=2 user turns)")
    print(f"  aggregate input tokens {T:,} | re-sent prefix (cacheable) {C:,}")
    print(f"  PREFIX-CACHEABLE FRACTION OF INPUT: {frac:.1%}  (median per-convo {med:.1%})")
    print(f"  -> input savings at Anthropic cached-read (~90% off): {frac*(1-ANTHROPIC_CACHE_READ):.1%}")
    print(f"  -> input savings at OpenAI cached-input (~50% off):   {frac*(1-OPENAI_CACHE_INPUT):.1%}")
    print("  -> docs/evidence/prefix_savings/prefix_savings.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
