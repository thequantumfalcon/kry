#!/usr/bin/env python3
"""LEVEL-1 measured sim — cheap-vs-frontier on MBPP (~974 real coding tasks), executed-test DEPLOYABLE gate.

A heavy, stranger-verifiable MEASURED savings number on a PUBLIC corpus. Honest scope: MBPP = real tasks but NOT real
company traffic, and NOT an external-counterparty anchor; benchmark code is easier than production, so the rate is an
UPPER bound on the checkable slice. net-not-gross, served-checked, deployable gate (no oracle), digest-only rows via
kry_shadow_emitter. HARD BUDGET CAP: stops before cumulative real spend exceeds --budget (default $15).

  python3 scripts/kry_shadow_sim.py --dry                  # $0, uses reference solutions, verifies wiring
  python3 scripts/kry_shadow_sim.py [N] [--budget 15]      # LIVE; N default = all MBPP
"""
from __future__ import annotations
import json, math, os, re, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_shadow_demo import call, cost, CHEAP, FRONTIER, PRICES
from kry_shadow_emitter import emit_row
import kry_shadow_demo
kry_shadow_demo.PRICES.setdefault("claude-opus-4-8", (15.0, 75.0))   # Opus 4.8 list price (frontier-pair headroom test)
FRONTIER = os.environ.get("KRY_FRONTIER", FRONTIER)                  # allow frontier override (e.g. Opus) to test the rate ceiling
CHEAP = os.environ.get("KRY_CHEAP", CHEAP)

MBPP_URL = "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/mbpp.jsonl"
PROMPT = ("You are an expert Python programmer. Task: {text}\n\nYour function must pass these tests:\n{tests}\n\n"
          "Return ONLY the complete Python function in a ```python code block, no explanation.")


def load_mbpp():
    cache = Path(os.path.expanduser("~/.cache/kry_mbpp.jsonl"))
    if cache.exists():
        text = cache.read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(MBPP_URL, timeout=90) as r:
            text = r.read().decode()
        cache.parent.mkdir(parents=True, exist_ok=True); cache.write_text(text, encoding="utf-8")
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def _extract(t):
    m = re.search(r"```(?:python)?\s*\n(.*?)```", t, re.S)
    return m.group(1) if m else t


def gen(p, model, dry):
    """Return (text, in_tok, out_tok). dry => the MBPP reference solution (tests pass) at $0."""
    if dry:
        return f"```python\n{p['code']}\n```", 150, 200
    prompt = PROMPT.format(text=p["text"], tests="\n".join(p["test_list"]))
    for a in range(5):
        try:
            return call(prompt, model, 700)
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and a < 4:
                time.sleep(2 * (a + 1)); continue
            raise


def run_mbpp(code, p):
    src = (p.get("test_setup_code", "") + "\n" + code + "\n" + "\n".join(p["test_list"]) + "\nprint('KRYOK')\n")
    try:
        # SECURITY: run model-GENERATED code with secrets stripped from the env so a malicious or
        # prompt-injected completion cannot read API keys. NOT a full sandbox (no fs/network isolation).
        safe_env = {k: v for k, v in os.environ.items()
                    if not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))}
        r = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True, timeout=10, env=safe_env)
        return r.returncode == 0 and "KRYOK" in r.stdout
    except Exception:
        return False


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    phat = k / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (round(centre - half, 4), round(centre + half, 4))


def main(argv):
    dry = "--dry" in argv
    args = [a for a in argv[1:] if a != "--dry"]
    budget = 15.0
    if "--budget" in args:
        bi = args.index("--budget")
        budget = float(args[bi + 1])
        args = args[:bi] + args[bi + 2:]     # drop --budget + its value so it isn't read as N
    nums = [a for a in args if a.isdigit()]
    cap_n = int(nums[0]) if nums else None

    data = load_mbpp()
    if cap_n:
        data = data[:cap_n]
    print(f"KRY Level-1 sim {'(DRY $0)' if dry else f'(LIVE, budget cap ${budget})'}  MBPP={len(data)}  "
          f"cheap={CHEAP} frontier={FRONTIER}\n")
    outdir = Path("docs/evidence/level1_sim"); outdir.mkdir(parents=True, exist_ok=True)
    tag = "dry" if dry else "live"
    rowf = open(outdir / f"level1_sim_rows_{tag}.jsonl", "w", buffering=1, encoding="utf-8")   # line-buffered: crash-safe + monitorable
    rows, saving, cp, fp, cs, fs, done = [], 0.0, 0, 0, 0.0, 0.0, 0
    for i, p in enumerate(data):
        if not dry and (cs + fs) >= budget:
            print(f"\n** BUDGET CAP ${budget:.2f} reached after {done} problems (spent ${cs+fs:.4f}); stopping. **", flush=True)
            break
        try:
            ctext, cit, cot = gen(p, CHEAP, dry)
            ftext, fit, fot = gen(p, FRONTIER, dry)
        except Exception as e:
            print(f"  [skip {i}] {type(e).__name__}: {e}", flush=True); continue
        cpass, fpass = run_mbpp(_extract(ctext), p), run_mbpp(_extract(ftext), p)
        ccost, fcost = cost(CHEAP, cit, cot), cost(FRONTIER, fit, fot)
        cp += cpass; fp += fpass; cs += ccost; fs += fcost; done += 1
        row = emit_row(
            frame_id=f"mbpp-{p.get('task_id', i)}", request_id=f"mbpp-{p.get('task_id', i)}",
            intent_text=str(p.get("task_id", i)), requested_model="best/code", served_model=FRONTIER,
            measurement_class="deployable_validated", correctness_source="deployable",
            cheap_fast_correct=cpass, deployable_validator_pass=cpass, frontier_correct=fpass,
            cheap_fast_cost_usd=ccost, frontier_holdout_cost_usd=fcost,
            cheap_fast_output_tokens=cot, frontier_holdout_output_tokens=fot,
            response_cost_usd=fcost, provider_cost_source="pricing_table",
            checkable_slice="code_executable", deterministic_check_kind="unit_test",
            deterministic_check_receipt=f"mbpp/{p.get('task_id')}: cheap={'pass' if cpass else 'fail'} frontier={'pass' if fpass else 'fail'}",
            output_axis_class="short_answer", latency_class="background")
        rows.append(row); saving += row["measured_row_value_usd"]
        rowf.write(json.dumps(row) + "\n")          # incremental: never lose progress
        if done % 10 == 0 or dry:
            print(f"  [{done:3d}/{len(data)}] cheap_pass={cp} frontier_pass={fp}  net=${saving:.4f}  spent=${cs+fs:.4f}", flush=True)
    rowf.close()
    n = len(rows)
    lo, hi = wilson(cp, n)
    summary = {
        "schema": "kry_level1_sim_summary/v1",
        "label": "MEASURED on a PUBLIC corpus (MBPP) — NOT real company traffic, NOT an external-counterparty anchor",
        "honest_scope": "MBPP = public benchmark (real tasks, not real company traffic); deployable gate = executed "
                        "tests (no oracle); net-not-gross; benchmark code is easier than production so the rate is an "
                        "UPPER bound on the checkable slice; model-pair specific. Mechanism/lever-rate anchor only.",
        "mode": "dry_reference_no_spend" if dry else "live_real_api", "budget_cap_usd": budget,
        "cheap_model": CHEAP, "frontier_model": FRONTIER, "price_basis_usd_per_1M": PRICES,
        "tasks_measured": n, "cheap_test_pass": cp, "cheap_adequacy_rate": round(cp / n, 4) if n else None,
        "cheap_adequacy_wilson95": [lo, hi], "cheap_fail_rows_zeroed": n - cp, "frontier_test_pass": fp,
        "cheap_spend_usd": round(cs, 6), "frontier_spend_usd_measured_baseline": round(fs, 6),
        "demo_total_spend_usd": round(cs + fs, 6),
        "measured_net_saving_usd": round(saving, 6),
        "net_cost_reduction_rate": round(saving / fs, 4) if fs else None,
        "saving_rule": "sum over cheap-test-pass rows of (frontier_cost - cheap_cost); cheap-fail rows -> $0; "
                       "verbose-cheap rows that cost more -> negative (net-not-gross).",
        "p0_pass_rows": sum(r["p0_pass"] for r in rows), "row_digests": [r["row_digest"] for r in rows],
    }
    (outdir / f"level1_sim_summary_{tag}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n=== LEVEL-1 RECEIPT ({'DRY' if dry else 'LIVE'}) ===")
    print(f"  cheap adequacy: {cp}/{n} = {100*cp/n:.1f}%  (Wilson95 [{100*lo:.1f}%, {100*hi:.1f}%])   "
          f"cheap-fail zeroed: {n-cp}   frontier pass: {fp}/{n}")
    print(f"  MEASURED net saving (net-not-gross): ${saving:.4f}   net cost-reduction: "
          f"{summary['net_cost_reduction_rate']}")
    print(f"  real spend: cheap ${cs:.4f} + frontier ${fs:.4f} = ${cs+fs:.4f}  (cap ${budget})")
    print(f"  rows: {outdir}/level1_sim_rows_{tag}.jsonl   summary: {outdir}/level1_sim_summary_{tag}.json")
    print(f"  (MEASURED on a public benchmark — not real company traffic; not the external anchor)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
