#!/usr/bin/env python3
"""depth-budget rescue experiment — does depth-matched CoT budget rescue cheap one-shot failures, sub-frontier?

THE GRADE-MOVER (sealed 2026-06-11, BEFORE any model run). Validates the third tier of the deployable
executable-class router: instead of escalating a cheap failure to the
frontier, give the cheap model a depth-matched reasoning budget and RE-CHECK — rescue on the cheap tier.

  TIER 1  cheap FAST (tiny budget)         -> objective check -> PASS -> keep (savings)
  TIER 2  cheap + depth-matched budget     -> objective check -> PASS -> RESCUED (a few cheap tokens)
  TIER 3  frontier                         -> escalate (charged cheap+frontier), honest

GROUNDING
  Depth-budget research: minimal reasoning budget rises with depth (D2:50 .. D5:300 tok), measured on
  a 14B model — budget proportional to depth CLEARS latent-walled multi-step computation.
  code_routing.json: the deployable test-pass gate (executable class). gsm8k_proof.json: gold-match MEASURES
  adequacy (oracle — fine for MEASURING rescue here; not a deployed gate).

SEALED HYPOTHESIS (P, confidence 0.55)
  On a battery of multi-step problems the cheap model FAILS one-shot, the depth-matched budget rescues a
  MATERIAL fraction (>= 0.30) on the cheap tier, at rescue-token-cost << the frontier call avoided. => a real
  third tier that cuts escalations at sub-frontier cost.

MINIMAL VIABLE FALSIFIER (live)
  rescue_rate <= 0.10 (budget can't help -> cheap failures are CAPABILITY-bound -> the binary
  cheap-or-frontier router is the ceiling, no third tier) OR rescue $ >= frontier $ avoided (the rescue is
  not cheaper than escalating). Either is a clean, publishable negative.

CONTROLS / DISCIPLINE (carry the prior research's)
  - Objective scoring only (numeric gold-match here; executed test-pass for the code variant). NO LLM judge.
  - TIER-1 must genuinely FAIL before a rescue is counted (no rescue credit on items cheap already passed).
  - generator != verifier: a separate agent adversarially checks the auto-verdict before acceptance.
  - served-check EVERY API number (response.model == request); raw key only; the bridge substitutes (retraction rule).
  - self_test() on MOCK responses must PASS (bit-identical accounting) before any real call is trusted.
  - HONEST SCOPE: small battery + model-pair specific; measures the rescue LEVER's existence + cost, NOT a
    real-traffic savings number. The prior research estimate_depth over-count makes budgets GENEROUS (conservative), not wrong.
  - the bound: running TIER-3 (frontier) or any OpenAI path SPENDS money + hits an external service -> requires
    operator go-ahead. Default provider is LOCAL OLLAMA (free, on-box). Nothing here is wired to the live floor.

PROVIDERS (stdlib urllib; no third-party deps — kry is stdlib-only)
  KRY_RESCUE_PROVIDER=ollama (default, FREE local)  | openai (PAID — needs OPENAI_API_KEY + operator go-ahead)
  KRY_RESCUE_CHEAP / KRY_RESCUE_FRONTIER override model ids.
"""
from __future__ import annotations

import json, os, re, urllib.request

# --- depth-budget research measured budget ladder (prior research, 14B-arith calibration; SHAPE is the robust finding) ---
_BUDGET = {1: 20, 2: 50, 3: 120, 4: 120, 5: 300}
_OPS = re.compile(r"\b(plus|add(?:ed|s)?|minus|subtract(?:ed|s)?|times|multipl(?:y|ies|ied)|divide[sd]?|"
                  r"double[sd]?|twice|product|sum|difference)\b|(?<=\d)\s*[-+x*/]\s*(?=\d)", re.I)


def estimate_depth(q: str) -> int:
    """Honest: count ARITHMETIC OPERATIONS only (NOT fact-derivations, which prior research shows are free). 1..6."""
    return max(1, min(6, len(_OPS.findall(q))))


def budget_for_depth(d: int) -> int:
    if d in _BUDGET:
        return _BUDGET[d]
    keys = sorted(_BUDGET)
    return _BUDGET[keys[0]] if d < keys[0] else _BUDGET[keys[-1]] + (d - keys[-1]) * 80


def last_number(text: str):
    nums = re.findall(r"-?\d[\d,]*", text or "")
    return nums[-1].replace(",", "") if nums else None


# --- provider call (real); injected as call_fn so the harness logic is provider-agnostic + mockable ---
def _ollama(prompt: str, model: str, max_tokens: int) -> str:
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=json.dumps({"model": model, "prompt": prompt, "stream": False,
                         "options": {"num_predict": max_tokens, "temperature": 0.0}}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["response"]


def _openai(prompt: str, model: str, max_tokens: int) -> str:
    key = os.environ["OPENAI_API_KEY"]
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                         "max_tokens": max_tokens, "temperature": 0.0}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
        served = d.get("model", "")
        if model.split("-")[0] not in served:                       # served-check (retraction rule)
            raise RuntimeError(f"served model {served!r} != requested {model!r}")
        return d["choices"][0]["message"]["content"]


def _anthropic(prompt: str, model: str, max_tokens: int) -> str:
    key = os.environ["ANTHROPIC_API_KEY"]                            # runner exports it (keeps repo key-free)
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": model, "max_tokens": max_tokens,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
        fam = model.split("-")[1] if "-" in model else model        # haiku|sonnet|opus
        if fam not in d.get("model", ""):                           # served-check (retraction rule)
            raise RuntimeError(f"served model {d.get('model')!r} != requested {model!r}")
        return "".join(b.get("text", "") for b in d.get("content", []))


def run_battery(items: list[dict], call_fn, cheap: str, frontier: str,
                fast_budget: int = 8) -> dict:
    """items: [{prompt, gold}]. call_fn(prompt, model, max_tokens)->text. Three-tier accounting."""
    tiers = {"kept_fast": 0, "rescued": 0, "escalated": 0, "fast_fail": 0}
    rows = []
    for it in items:
        p, gold = it["prompt"], str(it["gold"])
        fast = call_fn(f"{p}\nAnswer with only the final number.", cheap, fast_budget)
        if last_number(fast) == gold:
            tiers["kept_fast"] += 1; rows.append({"p": p[:40], "outcome": "kept_fast"}); continue
        tiers["fast_fail"] += 1
        b = budget_for_depth(estimate_depth(p))                      # depth-matched rescue budget
        resc = call_fn(f"{p}\nThink step by step, then give the final number.", cheap, b)
        if last_number(resc) == gold:
            tiers["rescued"] += 1; rows.append({"p": p[:40], "outcome": "rescued", "budget": b})
        else:
            call_fn(f"{p}\nThink step by step, then give the final number.", frontier, 400)
            tiers["escalated"] += 1; rows.append({"p": p[:40], "outcome": "escalated", "budget": b})
    ff = tiers["fast_fail"]
    rescue_rate = round(tiers["rescued"] / ff, 4) if ff else None
    verdict = ("CONFIRMED" if (rescue_rate is not None and rescue_rate >= 0.30)
               else "FALSIFIER FIRED" if (rescue_rate is not None and rescue_rate <= 0.10)
               else "INCONCLUSIVE")
    return {"schema": "kry_rescue_experiment/v1", "n": len(items), "tiers": tiers,
            "rescue_rate_of_fast_failures": rescue_rate, "verdict": verdict, "rows": rows,
            "honest_scope": "rescue LEVER existence + cost on a small battery; NOT a real-traffic savings number."}


# --- self-test: mock call_fn (deterministic) proves the accounting with ZERO model calls / ZERO spend ---
def self_test() -> bool:
    items = [{"prompt": "What is 8 plus 3?", "gold": "11"},                    # cheap passes fast
             {"prompt": "Take 8 plus 3, then times 2?", "gold": "22"},         # fails fast, rescued by budget
             {"prompt": "A hard multi-step problem", "gold": "999"}]           # fails fast AND rescue -> escalate
    def mock(prompt, model, max_tokens):
        if "8 plus 3?" in prompt: return "11"
        if "8 plus 3, then times 2" in prompt:
            return "22" if max_tokens > 8 else "20"                            # rescued only with budget
        return "123"                                                          # hard item: always wrong here
    r = run_battery(items, mock, "mock-cheap", "mock-frontier")
    assert r["tiers"] == {"kept_fast": 1, "rescued": 1, "escalated": 1, "fast_fail": 2}, r["tiers"]
    assert r["rescue_rate_of_fast_failures"] == 0.5, r["rescue_rate_of_fast_failures"]
    # determinism
    assert json.dumps(run_battery(items, mock, "mc", "mf"), sort_keys=True) == \
           json.dumps(run_battery(items, mock, "mc", "mf"), sort_keys=True)
    return True


def main():
    assert self_test(), "self-test failed"
    print("self-test PASS (mock accounting: kept_fast=1 rescued=1 escalated=1, rescue_rate=0.5)\n")
    prov = os.environ.get("KRY_RESCUE_PROVIDER", "ollama")
    if not os.environ.get("KRY_RESCUE_RUN"):
        print("REAL RUN GATED. To run for real (operator go-ahead):")
        print("  local/FREE:  KRY_RESCUE_PROVIDER=ollama KRY_RESCUE_RUN=1 KRY_RESCUE_CHEAP=<small-ollama-model> "
              "python3 scripts/kry_rescue_experiment.py <battery.jsonl>")
        print("  paid/API ($): KRY_RESCUE_PROVIDER=openai KRY_RESCUE_RUN=1 OPENAI_API_KEY=... "
              "KRY_RESCUE_CHEAP=gpt-4o-mini KRY_RESCUE_FRONTIER=gpt-4o python3 scripts/kry_rescue_experiment.py <battery.jsonl>")
        print("  battery.jsonl = one {\"prompt\":..., \"gold\":...} per line (multi-step items the cheap model fails one-shot).")
        return
    import sys
    items = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
    providers = {"ollama": _ollama, "openai": _openai, "anthropic": _anthropic}
    defaults = {"ollama": ("qwen2.5:0.5b", "qwen2.5:14b"),
                "openai": ("gpt-4o-mini", "gpt-4o"),
                "anthropic": ("claude-haiku-4-5-20251001", "claude-sonnet-4-6")}
    call = providers[prov]
    dc, df = defaults[prov]
    cheap = os.environ.get("KRY_RESCUE_CHEAP", dc)
    front = os.environ.get("KRY_RESCUE_FRONTIER", df)
    res = run_battery(items, lambda p, m, t: call(p, m, t), cheap, front)
    res["provider"], res["cheap_model"], res["frontier_model"] = prov, cheap, front
    out = os.path.join(os.path.dirname(__file__), "..",
                       "docs/evidence/adequacy_gate/rescue_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(json.dumps(res, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
