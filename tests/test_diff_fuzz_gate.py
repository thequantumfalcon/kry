"""The differential harness must reject process failures and shared crashes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Import in a child: the harness replaces time.time and configures mint state.
RUNNER = r'''
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

source, work, config = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])
spec = importlib.util.spec_from_file_location("diff_fuzz", source / "verifiers/diff_fuzz.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
harness.ROOT = work
harness.WORK = work / "verifiers/.fuzzwork"
(work / "verifiers").mkdir()
if config.get("construct_bases"):
    def savings(events):
        mixed = any(e.get("evidence_tier") == "provider_metered" for e in events)
        if mixed and config.get("fail_mixed"):
            raise RuntimeError("controlled mixed-base failure")
        return {"mixed": mixed}
    harness.build_savings = savings
    harness.build_savings_at_version = lambda *args: {}
    harness.build_action = lambda *args: {}
    print(json.dumps(harness.bases()))
    sys.exit(0)
harness.reset = lambda: None
harness.bases = lambda: [{"case": "base"}]
harness.mutate = lambda att, rng: {"case": "mutant"}
harness.py_verdict = lambda text: (
    config.get("base_python", "VALID") if json.loads(text)["case"] == "base"
    else config["python"])

def node(args, **kwargs):
    inputs = Path(args[3]).read_text().splitlines()
    base = json.loads(inputs[0])["case"] == "base"
    verdict = config.get("base_node", "VALID") if base else config["node"]
    count = len(inputs) if base else config.get("node_count", len(inputs))
    code = 0 if base else config.get("node_exit", 0)
    return subprocess.CompletedProcess(args, code, (verdict + "\n") * count,
                                       "controlled node failure" if code else "")

harness.subprocess.run = node
sys.argv = ["diff_fuzz.py", "1"]
harness.main()
'''


def run_harness(tmp_path, **config):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), KRY_FUZZ_SEED="1234")
    return subprocess.run([sys.executable, "-c", RUNNER, str(ROOT), str(tmp_path),
                           json.dumps(config)], env=env, capture_output=True,
                          text=True, timeout=30)


def test_shared_crash_fails(tmp_path):
    result = run_harness(tmp_path, python="CRASH", node="CRASH")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "CRASH" in result.stdout


def test_nonzero_node_exit_with_complete_matching_output_fails(tmp_path):
    result = run_harness(tmp_path, python="INVALID", node="INVALID", node_exit=17)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "17" in result.stdout


def test_mixed_base_construction_failure_stops_run(tmp_path):
    result = run_harness(tmp_path, construct_bases=True, fail_mixed=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "controlled mixed-base failure" in result.stderr


def test_mixed_base_is_included(tmp_path):
    result = run_harness(tmp_path, construct_bases=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(base.get("mixed") for base in json.loads(result.stdout))


@pytest.mark.parametrize("verdict", ["VALID", "INVALID"])
def test_matching_verdicts_pass(tmp_path, verdict):
    result = run_harness(tmp_path, python=verdict, node=verdict)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "divergences=0" in result.stdout


def test_divergence_still_fails_and_retains_input(tmp_path):
    result = run_harness(tmp_path, python="INVALID", node="VALID")
    assert result.returncode != 0, result.stdout + result.stderr
    vector = json.loads((tmp_path / "verifiers/divergences/div_0000.json").read_text())
    assert vector == {"py": "INVALID", "js": "VALID", "input": '{"case": "mutant"}'}


def test_extra_node_verdict_still_fails(tmp_path):
    result = run_harness(tmp_path, python="INVALID", node="INVALID", node_count=2)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "FATAL: js emitted 2 verdicts for 1 inputs" in result.stdout


@pytest.mark.parametrize("base", ["base_python", "base_node"])
def test_invalid_base_still_fails(tmp_path, base):
    result = run_harness(tmp_path, python="VALID", node="VALID", **{base: "INVALID"})
    assert result.returncode != 0, result.stdout + result.stderr
    assert "AssertionError" in result.stderr
