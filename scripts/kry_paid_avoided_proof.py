#!/usr/bin/env python3
"""Real avoided-cost proof: measure displacement savings against a REAL paid call.

Tonight's accepted_run proved reconciliation on real free calls, but its avoided model
was DECLARED. This closes that: for each prompt it serves the cheap (free) leg AND makes
the real PAID frontier call, then reads BOTH costs from OpenRouter's own per-request
generation records. The saving is (paid provider_cost - free provider_cost) — measured,
not asserted. Both legs are F1-reconcilable (real gen-ids).

Needs OPENROUTER_API_KEY with real credit (paid models return HTTP 402 on a $0 account).
A frontier call capped at ~120 tokens is pennies. stdlib only.

  python scripts/kry_paid_avoided_proof.py --prompt "..."        # one prompt
  python scripts/kry_paid_avoided_proof.py --prompts-file real.txt  # one prompt per line
  # real workload: sample prompts from LMSYS-Chat-1M / WildChat -> one per line.
"""
from __future__ import annotations
import argparse, json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

FREE_DEFAULT = "openai/gpt-oss-120b:free"
PAID_FALLBACK = ["anthropic/claude-opus-4.8", "openai/gpt-5.5", "openai/gpt-4o",
                 "anthropic/claude-3.5-sonnet", "openai/gpt-4o-mini"]


def _key() -> str:
    k = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not k:
        sys.exit("OPENROUTER_API_KEY not set")
    return k


def _chat(model: str, prompt: str, key: str, max_tokens: int = 120) -> dict:
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def _gen(gid: str, key: str, tries: int = 8) -> dict:
    for i in range(tries):
        try:
            req = urllib.request.Request(f"https://openrouter.ai/api/v1/generation?id={gid}",
                                         headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r).get("data", {})
            if d.get("total_cost") is not None:
                return d
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return {}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Measure real avoided cost vs a real paid call.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--prompt")
    g.add_argument("--prompts-file")
    p.add_argument("--free-model", default=FREE_DEFAULT)
    p.add_argument("--paid-model", default=None, help="default: try a fallback list of paid frontier models")
    p.add_argument("--max-tokens", type=int, default=120)
    p.add_argument("--out", default="docs/evidence/paid_proof/paid_proof.json")
    args = p.parse_args(argv)

    prompts = [args.prompt] if args.prompt else [ln.strip() for ln in Path(args.prompts_file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    key = _key()
    paid_models = [args.paid_model] if args.paid_model else PAID_FALLBACK

    rows, tot_free, tot_paid = [], 0.0, 0.0
    for prompt in prompts:
        f = _chat(args.free_model, prompt, key, args.max_tokens)
        paid = pmodel = None
        for m in paid_models:
            try:
                paid = _chat(m, prompt, key, args.max_tokens); pmodel = m; break
            except urllib.error.HTTPError as e:
                print(f"  {m} -> HTTP {e.code}" + (" (no credit)" if e.code == 402 else ""), file=sys.stderr)
            except Exception as e:
                print(f"  {m} -> {str(e)[:50]}", file=sys.stderr)
        if not paid:
            sys.exit("no paid model billable — add OpenRouter credit (paid models return 402 on $0).")
        fr, pr = _gen(f["id"], key), _gen(paid["id"], key)
        fc, pc = float(fr.get("total_cost") or 0), float(pr.get("total_cost") or 0)
        tot_free += fc; tot_paid += pc
        rows.append({"served_free": {"model": args.free_model, "gen": f["id"], "cost_usd": fc},
                     "avoided_paid": {"model": pmodel, "gen": paid["id"], "cost_usd": pc},
                     "saving_usd": pc - fc})
        print(f"  saved ${pc - fc:.6f}  (free {args.free_model} ${fc:.6f} vs paid {pmodel} ${pc:.6f})")

    out = {"schema": "kry_paid_avoided_proof/v1", "requests": len(rows),
           "total_free_cost_usd": round(tot_free, 6), "total_avoided_paid_cost_usd": round(tot_paid, 6),
           "total_real_saving_usd": round(tot_paid - tot_free, 6),
           "note": "avoided costs are the providers' OWN recorded total_cost; both legs F1-reconcilable.",
           "rows": rows}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nREAL total saving across {len(rows)} request(s): ${out['total_real_saving_usd']:.6f}  -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
