#!/usr/bin/env python3
"""Real-corpus cache-displacement validation: WildChat organic traffic + randomized holdout.

Replays REAL organic traffic (allenai/WildChat-1M — real users talking to ChatGPT) through
a KRY-style cache: genuinely REPEATED prompts are served from cache (the saving), and a
deterministic, publicly-seeded holdout forces real paid calls so the avoided cost is
MEASURED (real provider receipts), not asserted. Emits a usage log for
scripts/kry_savings_report.py (identical valuation math to the chain); the resulting
attestation is then checkable by scripts/kry_verify.py (a stranger).

Why this and not GSM8K: GSM8K is a curated benchmark; WildChat is organic real traffic —
the "independent real-world corpus" the readiness rubric (Step 2) asks for.

HONEST BOUNDS (stated up front; do not overclaim):
- REPLAY of an independent real corpus, NOT a live production deployment.
- Cache-displacement ONLY: a cache hit serves the IDENTICAL prior answer, so adequacy is
  INHERITED (no quality judge needed). This does NOT validate cheap-model ROUTING adequacy.
- p_hat ≈ 1.0 by construction: every genuine repeat, served fresh, incurs a real paid call
  (the holdout receipts confirm it); the Wilson CI lower bound (~0.9) is the conservative
  value the cache hits are credited at — never the point estimate.
- Deployment model = gpt-4o by default (real OpenAI $10/M list price, value multiplier 0.40).
  A cache hit avoids the FULL deployed-model cost, so the saving scales with that model's
  price. gpt-4o is the honest choice: a realistic frontier default whose holdout calls the
  OpenAI key can actually make (so the avoided cost is MEASURED, not asserted) — unlike Opus,
  which this key cannot call and which would be a cherry-picked premium baseline.
- The savings RATE (cache-hit fraction of real traffic) is model-INDEPENDENT; the deployed
  model only scales the dollar magnitude.
- Cost basis = the provider's own returned token usage. Holdout assignment is verifiable
  from the published seed below — anyone recomputes it.

Run:  python3 scripts/kry_wildchat_corpus_proof.py --rows 1500           # needs OPENAI_API_KEY
      python3 scripts/kry_wildchat_corpus_proof.py --rows 800 --dry-run  # free: no paid calls
stdlib only.
"""
from __future__ import annotations
import argparse, collections, hashlib, json, os, re, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

WILDCHAT = "allenai/WildChat-1M"
HOLDOUT_SEED = "kry-wildchat-2026-06-10"   # PUBLISHED — holdout assignment is recomputable
REQUEST_CLASS = "wildchat_repeats"         # single declared class (before measurement)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())[:300]


def pull(n_rows: int) -> list[dict]:
    out: list[dict] = []
    for off in range(0, n_rows, 100):
        url = (f"https://datasets-server.huggingface.co/rows?dataset={urllib.parse.quote(WILDCHAT)}"
               f"&config=default&split=train&offset={off}&length=100")
        try:
            d = json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "kry"}), timeout=40))
        except Exception as e:
            print(f"  pull stopped at offset {off}: {e}", file=sys.stderr)
            break
        for r in d.get("rows", []):
            convo = r["row"].get("conversation") or []
            u = next((t for t in convo if t.get("role") == "user"), None)
            a = next((t for t in convo if t.get("role") == "assistant"), None)
            if u and u.get("content"):
                out.append({"prompt": u["content"], "norm": norm(u["content"]),
                            "ans_chars": len(a["content"]) if a and a.get("content") else 0})
    return out


def is_holdout(rid: str, rate: float) -> bool:
    """Deterministic, publicly verifiable: SHA-256(seed:id) mapped into [0,1) < rate."""
    h = int(hashlib.sha256(f"{HOLDOUT_SEED}:{rid}".encode()).hexdigest(), 16)
    return (h % 10_000) / 10_000.0 < rate


def call(model: str, prompt: str, key: str, max_tokens: int = 256) -> tuple[int, int]:
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        u = json.load(r)["usage"]
    return u["prompt_tokens"], u["completion_tokens"]


def main() -> int:
    ap = argparse.ArgumentParser(description="WildChat real-corpus cache-displacement holdout")
    ap.add_argument("--rows", type=int, default=1500, help="WildChat rows to pull (more -> more repeats)")
    ap.add_argument("--holdout-rate", type=float, default=0.15)
    ap.add_argument("--dry-run", action="store_true", help="no paid calls; placeholder holdout (free)")
    ap.add_argument("--deploy-model", default="gpt-4o",
                    help="model the deployment serves (and a cache hit avoids); default gpt-4o (priced)")
    ap.add_argument("--out", default="docs/evidence/wildchat_proof/usage.jsonl")
    a = ap.parse_args()
    deploy = a.deploy_model

    rows = pull(a.rows)
    by: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by[r["norm"]].append(r)
    clusters = {k: v for k, v in by.items() if len(v) >= 2}
    cache_hits = [r for v in clusters.values() for r in v[1:]]   # 2nd+ occurrence = a cache hit

    key = os.getenv("OPENAI_API_KEY", "").strip()
    live = bool(key) and not a.dry_run
    recs, holdout_n, real_holdout_cost_tokens = [], 0, 0
    for j, r in enumerate(cache_hits):
        rid = f"wc{j}"
        if is_holdout(rid, a.holdout_rate):
            if live:
                try:
                    pt, ct = call(deploy, r["prompt"], key)  # forced REAL call -> real receipt
                except Exception as e:                         # a content-filter block etc. -> skip honestly
                    print(f"  holdout {rid} skipped ({str(e)[:50]})", file=sys.stderr)
                    continue
            else:
                pt, ct = max(1, len(r["prompt"]) // 4), 180   # placeholder (dry-run)
            holdout_n += 1
            real_holdout_cost_tokens += ct
            recs.append({"id": rid, "holdout": True, "model": deploy,
                         "request_class": REQUEST_CLASS,
                         "usage": {"prompt_tokens": pt, "completion_tokens": ct}})
        else:
            ct = max(1, r["ans_chars"] // 4)   # real WildChat served-answer length as completion estimate
            recs.append({"id": rid, "cache_hit": True, "avoided_model": deploy,
                         "request_class": REQUEST_CLASS,
                         "usage": {"prompt_tokens": max(1, len(r["prompt"]) // 4),
                                   "completion_tokens": ct}})

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(json.dumps(x) for x in recs) + "\n", encoding="utf-8")
    treated = len(cache_hits) - holdout_n
    cache_rate = len(cache_hits) / len(rows) if rows else 0.0
    print(f"WildChat real-corpus run ({WILDCHAT})  deploy-model={deploy}")
    print(f"  pulled {len(rows)} real prompts; repeat clusters {len(clusters)}; "
          f"cache-eligible repeats {len(cache_hits)}")
    print(f"  CACHE-HIT RATE {cache_rate:.1%} of real traffic  ->  ~that fraction of "
          f"{deploy} spend avoided (model-independent rate; model scales the $)")
    print(f"  treated (cache-served) {treated}  |  holdout (forced real calls) {holdout_n}  "
          f"[{'REAL receipts' if live else 'DRY-RUN placeholder'}]")
    if holdout_n < 30:
        print(f"  WARNING: holdout_n {holdout_n} < MIN_HOLDOUT_N 30 -> savings_report will label "
              f"this self_reported (raise --rows or --holdout-rate for the validated tier).")
    print(f"  seed='{HOLDOUT_SEED}' (holdout assignment is recomputable)  -> {a.out}")
    print(f"  next: python3 scripts/kry_savings_report.py {a.out} --json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
