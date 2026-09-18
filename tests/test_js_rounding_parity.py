"""The JS verifier must round exactly as the Python reference does.

SPEC's `round(x, n)` is round-half-even on the exact binary value. The JS verifier used to round by
scaling (`Math.round(x * 1e4) / 1e4`), which injects the multiply's own error: about 4% of
five-decimal magnitudes rounded to a different value — 1e-4 apart, four decades above the 1e-9
comparison tolerance — so each verifier would reject documents the other accepted. Neither the corpus
nor the differential fuzzer produced such a value, which is why this pins it directly.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node is not installed")


def _cases() -> list[tuple[float, int]]:
    cases: list[tuple[float, int]] = []
    for n in range(1, 900):                       # five-decimal magnitudes: the old failure mode
        cases.append((round(n * 1.00005, 5), 4))
        cases.append((round(n * 7.000005, 6), 6))
    for k in range(1, 600):                       # dyadic values: the genuine half-even ties
        cases.append((k / 32, 4))
        cases.append((k / 1024, 4))
        cases.append((k / 64, 6))
    return cases


def test_js_rounding_matches_python(tmp_path):
    cases = _cases()
    (tmp_path / "cases.json").write_text(json.dumps([[x, n] for x, n in cases]), encoding="utf-8")
    runner = tmp_path / "run.mjs"
    runner.write_text(
        'import { roundDec } from "%s";\n'
        'import { readFileSync } from "node:fs";\n'
        'const cases = JSON.parse(readFileSync(process.argv[2], "utf8"));\n'
        'console.log(JSON.stringify(cases.map(([x, n]) => roundDec(x, n))));\n'
        % (_ROOT / "verifiers" / "js" / "verify.mjs").as_uri(),
        encoding="utf-8")
    out = subprocess.run([_NODE, str(runner), str(tmp_path / "cases.json")],
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stdout + out.stderr
    js = json.loads(out.stdout.strip().splitlines()[-1])
    mismatches = [(x, n, round(x, n), j) for (x, n), j in zip(cases, js) if round(x, n) != j]
    assert not mismatches, f"{len(mismatches)} of {len(cases)} diverge, e.g. {mismatches[:3]}"
