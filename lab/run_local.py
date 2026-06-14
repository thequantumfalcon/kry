#!/usr/bin/env python3
"""Prove the WHOLE pipeline on ONE machine before touching the cluster.

Runs every test that doesn't need real hardware (corpus -> router dry-run -> Test 1
holdout-vs-truth, Test 2 energy, Test 3 HOLE D, Test 4 F1 reconcile->research_grade,
Test 5 sanctions, Test 6 concurrency) and prints a PASS/FAIL table. If this is green,
the only things left for the real nodes are the live model calls (router
--judge frontier-compare), the wall-meter readings, and Test 4's cross-machine
stranger-verify leg.

Usage:
    python lab/run_local.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PY = sys.executable
_ENV = {"PYTHONPATH": str(_ROOT / "src")}


def _run(cmd, **kw):
    import os
    return subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **_ENV}, **kw)


def main() -> int:
    d = Path(tempfile.mkdtemp(prefix="kry_runlocal_"))
    results = []

    # Phase 1: corpus -> router (dry-run) -> truth + report
    _run([_PY, "lab/make_prompts.py", "--n", "20000", "--out", str(d / "p.jsonl")], cwd=_ROOT)
    _run([_PY, "lab/router.py", "--config", "lab/routes.example.json", "--corpus", str(d / "p.jsonl"),
          "--out", str(d / "u.jsonl"), "--truth-out", str(d / "tf.jsonl"), "--dry-run"], cwd=_ROOT)
    _run([_PY, "lab/compute_truth.py", str(d / "tf.jsonl"), "--out", str(d / "truth.json")], cwd=_ROOT)
    rep = _run([_PY, "scripts/kry_savings_report.py", str(d / "u.jsonl"), "--json"], cwd=_ROOT)
    (d / "report.json").write_text(rep.stdout, encoding="utf-8")

    # Test 1
    t1 = _run([_PY, "lab/holdout_truth_check.py", "--report", str(d / "report.json"),
               "--truth", str(d / "truth.json")], cwd=_ROOT)
    a1 = json.loads(t1.stdout)
    results.append(("Test 1  holdout vs real truth", a1["pass"], f"agreement {a1['agreement']}"))

    # Test 2 (synthetic measurements stand in for the wall meter)
    (d / "meas.json").write_text(json.dumps({"grid_co2_g_per_kwh": 400,
        "nodes": {"rtx5080": {"tokens": 50000, "energy_wh": 38.0},
                  "mac_m4": {"tokens": 50000, "energy_wh": 7.0}}}), encoding="utf-8")
    e = json.loads(_run([_PY, "lab/energy_report.py", str(d / "meas.json")], cwd=_ROOT).stdout)
    ok2 = e["per_million_tokens_displaced"]["avoided_co2_g"] > 0
    results.append(("Test 2  measured energy->carbon", ok2,
                    f"{e['per_million_tokens_displaced']['avoided_co2_g']} g CO2/M tok (demo)"))

    # Test 3 HOLE D
    h = _run([_PY, "lab/hole_d_double_spend.py"], cwd=_ROOT)
    ok3 = "fix holds=True" in h.stdout and "lease atomic=True" in h.stdout
    results.append(("Test 3  cross-node double-spend", ok3, "vulnerable -> protected, lease atomic"))

    # Test 4 (F1 reconcile leg): synthetic T1 mints + a matching provider export ->
    # kry_research_grade should reach research_grade. The stranger-verify + cross-machine
    # legs of Test 4 still need the cluster; this dry-proves the reconcile->grade pipeline.
    ml = d / "t1.jsonl"
    ml.write_text("\n".join(json.dumps({
        "receipt_id": f"KRY-{i}", "evidence_tier": "provider_metered",
        "metered_tokens": [100 + i, 200 + i], "detail": f"x /openrouter:gen-{i}", "ts": 1000 + i,
    }) for i in range(5)) + "\n", encoding="utf-8")
    (d / "or.json").write_text(json.dumps(
        [{"id": f"gen-{i}", "tokens_prompt": 100 + i, "tokens_completion": 200 + i} for i in range(5)]), encoding="utf-8")
    rg = _run([_PY, "scripts/kry_research_grade.py", str(ml), "--provider-export", str(d / "or.json")],
              cwd=_ROOT)
    ok4 = rg.returncode == 0 and "research_grade REACHED" in rg.stdout
    results.append(("Test 4  F1 reconcile -> research_grade", ok4, "agreement 1.00 vs bar 0.80 (synthetic)"))

    # Test 5 sanctions
    s = _run([_PY, "-c",
        "import kry.kry_sanctions as ks;"
        "[ks.record_reconciliation('honest',True) for _ in range(8)];"
        "[ks.record_reconciliation('cheater',False) for _ in range(6)];"
        "print(ks.audit_rate_for('honest'), ks.audit_rate_for('cheater'))"], cwd=_ROOT)
    hon, ch = map(float, s.stdout.split())
    results.append(("Test 5  sanctions (ESS)", hon < 0.2 and ch > 0.5 and ch > hon,
                    f"honest {hon:.2f} / cheater {ch:.2f}"))

    # Test 6 concurrency
    c = json.loads(_run([_PY, "lab/concurrency_check.py", "--workers", "4", "--earns", "150",
                         "--share", str(d / "conc")], cwd=_ROOT).stdout)
    results.append(("Test 6  shared-ledger concurrency", c["pass"], f"lost {c['lost']}"))

    print("\n" + "=" * 64)
    print("LOCAL PROOF (one machine, no models, no meters)")
    print("=" * 64)
    allok = True
    for name, ok, detail in results:
        allok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:34s} {detail}")
    print("=" * 64)
    print("All local tests pass." if allok else "Some local tests FAILED — see above.")
    print("Next on the cluster: router --judge frontier-compare (real truth) + wall-meter")
    print("readings for Test 2 + lab/node.py for the real cross-machine HOLE D / verify.")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
