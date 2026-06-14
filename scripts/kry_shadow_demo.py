#!/usr/bin/env python3
"""KRY shadow-emitter LIVE DEMO — real cheap-vs-frontier on toy coding tasks, real test-pass gate, real receipt.

MECHANISM PROOF (honest label): the tasks are HAND-WRITTEN by us (synthetic), so this proves the EMITTER +
real API + deployable test-check + net-not-gross accounting work END-TO-END on real money. It is NOT a real
external-customer savings anchor — that needs someone else's real frontier traffic + a counterparty.

For each toy task: cheap (haiku) and frontier (sonnet) each write the function; we RUN the task's own tests
(deployable check — no gold answer needed); the saving counts ONLY where the cheap model's code PASSED the
tests (net-not-gross). Real Anthropic usage -> real USD via list prices. served-checked. Emits rows through
scripts/kry_shadow_emitter.emit_row and prints a receipt.

  python3 scripts/kry_shadow_demo.py --dry    # $0, mock calls, verifies the wiring
  python3 scripts/kry_shadow_demo.py           # LIVE: real cheap+frontier calls (~$0.20-0.60), needs the key
"""
from __future__ import annotations
import json, os, re, subprocess, sys, urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_shadow_emitter import emit_row  # the read-only, digest-only row emitter (already self-tested)

CHEAP, FRONTIER = "claude-haiku-4-5-20251001", "claude-sonnet-4-6"
# Approximate list prices, USD per 1M tokens (input, output). Verify at anthropic.com/pricing.
PRICES = {"claude-haiku-4-5-20251001": (1.0, 5.0), "claude-sonnet-4-6": (3.0, 15.0)}
SUFFIX = "\n\nReturn ONLY the complete Python function in a ```python code block, no explanation."

TASKS = [
    {"name": "sum_digits", "prompt": "Write a Python function sum_digits(n) that returns the sum of the decimal digits of a non-negative integer n.",
     "test": "assert sum_digits(0)==0\nassert sum_digits(123)==6\nassert sum_digits(9999)==36"},
    {"name": "is_palindrome", "prompt": "Write a Python function is_palindrome(s) that returns True if the string s reads the same forwards and backwards, else False.",
     "test": "assert is_palindrome('racecar')\nassert not is_palindrome('hello')\nassert is_palindrome('')"},
    {"name": "gcd", "prompt": "Write a Python function gcd(a, b) that returns the greatest common divisor of two positive integers a and b.",
     "test": "assert gcd(12,8)==4\nassert gcd(17,5)==1\nassert gcd(100,10)==10"},
    {"name": "count_vowels", "prompt": "Write a Python function count_vowels(s) that returns the number of vowels (a,e,i,o,u, case-insensitive) in s.",
     "test": "assert count_vowels('hello')==2\nassert count_vowels('XYZ')==0\nassert count_vowels('AeIoU')==5"},
    {"name": "reverse_words", "prompt": "Write a Python function reverse_words(s) that returns s with the order of whitespace-separated words reversed (single spaces in the result).",
     "test": "assert reverse_words('the quick brown')=='brown quick the'\nassert reverse_words('hi')=='hi'"},
    {"name": "two_sum", "prompt": "Write a Python function two_sum(nums, target) returning a tuple (i, j) with i<j and nums[i]+nums[j]==target (first such pair), or None.",
     "test": "assert two_sum([2,7,11,15],9)==(0,1)\nassert two_sum([3,2,4],6)==(1,2)\nassert two_sum([1,2,3],100) is None"},
    {"name": "roman_to_int", "prompt": "Write a Python function roman_to_int(s) that converts a valid uppercase Roman numeral (I,V,X,L,C,D,M) to its integer value.",
     "test": "assert roman_to_int('III')==3\nassert roman_to_int('IV')==4\nassert roman_to_int('MCMXCIV')==1994"},
    {"name": "valid_parens", "prompt": "Write a Python function valid_parens(s) that returns True if every bracket in '()[]{}' is matched and correctly ordered, else False.",
     "test": "assert valid_parens('()[]{}')\nassert not valid_parens('(]')\nassert valid_parens('([{}])')"},
    {"name": "longest_common_prefix", "prompt": "Write a Python function longest_common_prefix(strs) returning the longest common prefix of a list of strings, or '' if none.",
     "test": "assert longest_common_prefix(['flower','flow','flight'])=='fl'\nassert longest_common_prefix(['dog','cat'])==''"},
    {"name": "merge_sorted", "prompt": "Write a Python function merge_sorted(a, b) that merges two already-sorted lists into one sorted list.",
     "test": "assert merge_sorted([1,3,5],[2,4,6])==[1,2,3,4,5,6]\nassert merge_sorted([],[1])==[1]"},
]

# Correct reference impls — used ONLY by --dry to verify the happy path at $0 (never sent to a model).
IMPLS = {
    "sum_digits": "def sum_digits(n):\n    return sum(int(c) for c in str(n))",
    "is_palindrome": "def is_palindrome(s):\n    return s == s[::-1]",
    "gcd": "def gcd(a,b):\n    while b: a,b=b,a%b\n    return a",
    "count_vowels": "def count_vowels(s):\n    return sum(c.lower() in 'aeiou' for c in s)",
    "reverse_words": "def reverse_words(s):\n    return ' '.join(s.split()[::-1])",
    "two_sum": ("def two_sum(nums,target):\n    seen={}\n    for j,x in enumerate(nums):\n"
                "        if target-x in seen: return (seen[target-x], j)\n        seen[x]=j\n    return None"),
    "roman_to_int": ("def roman_to_int(s):\n    v={'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}\n    t=0\n"
                     "    for i,c in enumerate(s):\n        if i+1<len(s) and v[c]<v[s[i+1]]: t-=v[c]\n        else: t+=v[c]\n    return t"),
    "valid_parens": ("def valid_parens(s):\n    st=[]; m={')':'(',']':'[','}':'{'}\n    for c in s:\n"
                     "        if c in '([{': st.append(c)\n        elif not st or st.pop()!=m[c]: return False\n    return not st"),
    "longest_common_prefix": ("def longest_common_prefix(strs):\n    if not strs: return ''\n    p=strs[0]\n"
                              "    for s in strs[1:]:\n        while not s.startswith(p): p=p[:-1]\n    return p"),
    "merge_sorted": "def merge_sorted(a,b):\n    import heapq\n    return list(heapq.merge(a,b))",
}


def _extract(text):
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    return m.group(1) if m else text


def call(prompt, model, max_tokens=700):
    """Real Anthropic call -> (text, input_tokens, output_tokens). served-checked (retraction rule)."""
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": model, "max_tokens": max_tokens,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    fam = model.split("-")[1]
    if fam not in d.get("model", ""):
        raise RuntimeError(f"served {d.get('model')!r} != requested {model!r}")
    text = "".join(b.get("text", "") for b in d.get("content", []))
    u = d.get("usage", {})
    return text, u.get("input_tokens", 0), u.get("output_tokens", 0)


def mock_call(prompt, model, max_tokens=700):
    name = re.search(r"function (\w+)\(", prompt).group(1)
    return f"```python\n{IMPLS[name]}\n```", 200, 120


def cost(model, it, ot):
    ip, op = PRICES[model]
    return (it * ip + ot * op) / 1e6


def run_tests(code, test):
    src = code + "\n\n" + test + "\nprint('KRYOK')\n"
    try:
        # SECURITY: run model-GENERATED code with secrets stripped from the env so a malicious or
        # prompt-injected completion cannot read API keys. NOT a full sandbox (no fs/network isolation).
        safe_env = {k: v for k, v in os.environ.items()
                    if not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))}
        r = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True, timeout=10, env=safe_env)
        return r.returncode == 0 and "KRYOK" in r.stdout
    except Exception:
        return False


def main(dry):
    fn = mock_call
    if not dry:
        fn = call
    print(f"KRY shadow demo {'(DRY — mock, $0)' if dry else '(LIVE — real API)'}  "
          f"cheap={CHEAP}  frontier={FRONTIER}  tasks={len(TASKS)}\n")
    rows, saving, cheap_pass, front_pass, cheap_spend, front_spend = [], 0.0, 0, 0, 0.0, 0.0
    for i, t in enumerate(TASKS):
        ctext, cit, cot = fn(t["prompt"] + SUFFIX, CHEAP)
        ftext, fit, fot = fn(t["prompt"] + SUFFIX, FRONTIER)
        cpass, fpass = run_tests(_extract(ctext), t["test"]), run_tests(_extract(ftext), t["test"])
        ccost, fcost = cost(CHEAP, cit, cot), cost(FRONTIER, fit, fot)
        cheap_pass += cpass; front_pass += fpass; cheap_spend += ccost; front_spend += fcost
        row = emit_row(
            frame_id=f"demo-{i}", request_id=f"demo-{i}", intent_text=t["prompt"],
            requested_model="best/code", served_model=FRONTIER,
            measurement_class="deployable_validated", correctness_source="deployable",
            cheap_fast_correct=cpass, deployable_validator_pass=cpass, frontier_correct=fpass,
            cheap_fast_cost_usd=ccost, frontier_holdout_cost_usd=fcost,
            cheap_fast_output_tokens=cot, frontier_holdout_output_tokens=fot,
            response_cost_usd=fcost, provider_cost_source="pricing_table",
            checkable_slice="code_executable", deterministic_check_kind="unit_test",
            deterministic_check_receipt=f"{t['name']}: cheap={'pass' if cpass else 'fail'} frontier={'pass' if fpass else 'fail'}",
            output_axis_class="short_answer", latency_class="background")
        rows.append(row); saving += row["measured_row_value_usd"]
        print(f"[{i:2d}] {t['name']:22s} cheap={'PASS' if cpass else 'fail'} "
              f"frontier={'PASS' if fpass else 'fail'}  cheap=${ccost:.5f} frontier=${fcost:.5f} "
              f"-> row saving ${row['measured_row_value_usd']:.5f}")
    n = len(rows)
    summary = {
        "schema": "kry_shadow_demo_summary/v1",
        "label": "MECHANISM PROOF on synthetic toy coding tasks — NOT a real external-customer savings anchor",
        "honest_scope": "tasks hand-written by us (synthetic); deployable gate = executed unit tests (no oracle); "
                        "net-not-gross; saving counts ONLY where the cheap model's code PASSED the tests. "
                        "Toy functions => cheap adequacy is optimistic; real/harder code is lower.",
        "mode": "dry_mock_no_spend" if dry else "live_real_api",
        "cheap_model": CHEAP, "frontier_model": FRONTIER, "price_basis_usd_per_1M": PRICES,
        "tasks": n, "cheap_test_pass": cheap_pass, "frontier_test_pass": front_pass,
        "cheap_spend_usd": round(cheap_spend, 6), "frontier_spend_usd_measured_baseline": round(front_spend, 6),
        "measured_net_saving_usd": round(saving, 6),
        "saving_rule": "sum over rows where the cheap code passed the deployable tests of (frontier_cost - cheap_cost)",
        "p0_pass_rows": sum(r["p0_pass"] for r in rows),
        "row_digests": [r["row_digest"] for r in rows],
    }
    outdir = Path("docs/evidence/shadow_demo"); outdir.mkdir(parents=True, exist_ok=True)
    tag = "dry" if dry else "live"
    (outdir / f"shadow_demo_rows_{tag}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (outdir / f"shadow_demo_summary_{tag}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n=== RECEIPT ({'DRY' if dry else 'LIVE'}) ===")
    print(f"  cheap passed deployable tests: {cheap_pass}/{n}   frontier passed: {front_pass}/{n}")
    print(f"  MEASURED net saving (cheap-passed rows, net-not-gross): ${saving:.6f}")
    print(f"  demo's own API spend: cheap ${cheap_spend:.6f} + frontier ${front_spend:.6f} = ${cheap_spend+front_spend:.6f}")
    print(f"  rows: {outdir}/shadow_demo_rows_{tag}.jsonl   summary: {outdir}/shadow_demo_summary_{tag}.json")
    print("  (MECHANISM proof on synthetic tasks — not a real external anchor)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(dry="--dry" in sys.argv))
