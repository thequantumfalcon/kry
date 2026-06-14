#!/usr/bin/env python3
"""Validate the ROUTING lever on CODE — objective adequacy = test-pass (no LLM judge).

Routing (serve a cheap model when it's adequate, escalate only failures to the frontier) is
the biggest savings lever, and it was EXCLUDED from the company estimate for lack of validated
adequacy. Code is the ideal class to validate it honestly: "adequate" = the generated code
PASSES THE PROBLEM'S OWN TESTS, executed. (HumanEval — 164 problems, each with hidden asserts.)

For each problem: generate with cheap (gpt-4o-mini) and frontier (gpt-4o), run each through the
test suite, grade pass/fail. The router serves cheap and escalates ONLY failures to frontier:
    routed_cost     = Σ cheap_cost + Σ(over cheap-FAILS) frontier_cost   (failures pay twice)
    all_frontier    = Σ frontier_cost                                    (baseline: all frontier)
    routing_savings = all_frontier − routed_cost ;  rate = savings / all_frontier
This is honest: it charges the escalation cost of every cheap failure, so the rate cannot be
gamed by ignoring misses. Saving rate ≈ cheap_adequacy − (cheap_price / frontier_price).

Needs OPENAI_API_KEY. Executes model-generated HumanEval solutions in a subprocess with a
10s timeout (benign toy functions; this is the standard HumanEval evaluation). stdlib only.
  python3 scripts/kry_code_routing_proof.py [N]
"""
from __future__ import annotations
import gzip, json, os, re, subprocess, sys, tempfile, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_grounded_savings_openai import CHEAP, FRONTIER, PRICES, call, cost
from kry_grounded_savings_proof import wilson

HUMANEVAL_GZ = "https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz"
PROMPT_SUFFIX = ("\n\nComplete this function. Return ONLY the complete Python function "
                 "(including the signature and any needed imports), no markdown, no explanation.")


def load_humaneval() -> list[dict]:
    p = "/tmp/humaneval.jsonl"
    if not os.path.exists(p):
        raw = urllib.request.urlopen(
            urllib.request.Request(HUMANEVAL_GZ, headers={"User-Agent": "kry"}), timeout=40).read()
        Path(p).write_text(gzip.decompress(raw).decode(), encoding="utf-8")
    return [json.loads(line) for line in open(p, encoding="utf-8") if line.strip()]


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text or "", re.S)
    return (m.group(1) if m else (text or "")).strip()


def _header(prompt: str) -> str:
    """Import/preamble lines of the prompt (everything before the target def)."""
    out = []
    for ln in prompt.splitlines():
        if ln.startswith("def "):
            break
        out.append(ln)
    return "\n".join(out)


def passes(problem: dict, generation: str) -> bool:
    """True iff the model's code passes the problem's hidden test suite, executed."""
    code = extract_code(generation)
    ep = problem["entry_point"]
    if f"def {ep}" in code:                       # model returned the full function
        program = f"{_header(problem['prompt'])}\n{code}\n\n{problem['test']}\ncheck({ep})\n"
    else:                                          # model returned only the body (continuation)
        program = f"{problem['prompt']}{code}\n\n{problem['test']}\ncheck({ep})\n"
    path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(program)
            path = f.name
        # SECURITY: run model-GENERATED code with secrets stripped from the env so a malicious or
        # prompt-injected completion cannot read API keys. NOT a full sandbox (no fs/network isolation).
        safe_env = {k: v for k, v in os.environ.items()
                    if not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))}
        r = subprocess.run([sys.executable, path], capture_output=True, timeout=10, env=safe_env)
        return r.returncode == 0
    except Exception:
        return False
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def main() -> int:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        sys.exit("OPENAI_API_KEY not set")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    data = load_humaneval()[:n]
    print(f"HumanEval routing test: {len(data)} problems  cheap={CHEAP} frontier={FRONTIER}\n")

    def proc(p):
        try:
            cg, cpt, cct = call(CHEAP, p["prompt"] + PROMPT_SUFFIX, key, max_tokens=800)
            fg, fpt, fct = call(FRONTIER, p["prompt"] + PROMPT_SUFFIX, key, max_tokens=800)
        except Exception:
            return None
        return {"cheap_pass": passes(p, cg), "frontier_pass": passes(p, fg),
                "cheap_cost": cost(CHEAP, cpt, cct), "frontier_cost": cost(FRONTIER, fpt, fct)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = [r for r in pool.map(proc, data) if r]
    m = len(rows)
    cheap_ok = sum(r["cheap_pass"] for r in rows)
    front_ok = sum(r["frontier_pass"] for r in rows)
    all_frontier = sum(r["frontier_cost"] for r in rows)
    routed = sum(r["cheap_cost"] + (0.0 if r["cheap_pass"] else r["frontier_cost"]) for r in rows)
    savings = all_frontier - routed
    rate = savings / all_frontier if all_frontier else 0.0
    lo, hi = wilson(cheap_ok, m)
    out = {
        "schema": "kry_code_routing_proof/v1", "benchmark": "HumanEval (test-pass adequacy, executed)",
        "cheap_model": CHEAP, "frontier_model": FRONTIER, "price_basis_usd_per_1M": PRICES,
        "problems": m, "cheap_adequate": cheap_ok, "cheap_adequacy_rate": round(cheap_ok / m, 4),
        "cheap_adequacy_wilson_95ci": [lo, hi], "frontier_pass": front_ok,
        "all_frontier_cost_usd": round(all_frontier, 6), "routed_cost_usd": round(routed, 6),
        "routing_savings_usd": round(savings, 6), "routing_savings_rate": round(rate, 4),
        "honest_claim": f"Router serves cheap, escalates the {m-cheap_ok} cheap-failures to frontier "
                        f"(charged twice). Saving = real avoided $ on the {cheap_ok}/{m} the cheap model "
                        "passed the executed tests on.",
        "caveats": ["HumanEval = toy functions; real production code adequacy is lower and contested",
                    "cost = real tokens x stated OpenAI list prices, not provider-recorded",
                    "this validates the ROUTING lever the company estimate excluded"],
    }
    Path("docs/evidence/code_routing").mkdir(parents=True, exist_ok=True)
    Path("docs/evidence/code_routing/code_routing.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("=== ROUTING on code (objective test-pass adequacy) ===")
    print(f"  cheap ({CHEAP}) adequate: {cheap_ok}/{m} = {100*cheap_ok/m:.0f}%  (95% CI [{100*lo:.0f}%, {100*hi:.0f}%])")
    print(f"  frontier ({FRONTIER}) pass (sanity): {front_ok}/{m} = {100*front_ok/m:.0f}%")
    print(f"  all-frontier cost ${all_frontier:.4f}  ->  routed cost ${routed:.4f}")
    print(f"  ROUTING SAVINGS: ${savings:.4f} = {100*rate:.0f}% of frontier spend  (failures escalated, charged twice)")
    print("  -> docs/evidence/code_routing/code_routing.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
