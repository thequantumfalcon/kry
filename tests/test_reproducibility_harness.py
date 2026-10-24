"""A proof summary cannot override a failed child or an empty requested run."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from lab import run_local
from scripts import kry_release_verify as release_verify


ROOT = Path(__file__).resolve().parents[1]


def _bash() -> str:
    if os.name == "nt":
        git_exec = Path(subprocess.check_output(["git", "--exec-path"], text=True).strip())
        return str(git_exec.parents[2] / "bin" / "bash.exe")
    return "bash"


@pytest.fixture
def harness(tmp_path):
    runner = tmp_path / "runner"
    runner.write_text('#!/bin/sh\nexec "$KRY_HARNESS_TEST_PYTHON" "$KRY_HARNESS_TEST_CHILD" "$@"\n',
                      encoding="utf-8")
    runner.chmod(0o755)
    child = tmp_path / "child.py"
    child.write_text('''import json, os, sys
args = sys.argv[1:]
if args[0] == "-c":
    exec(args[1])
    raise SystemExit(0)
phase = "pytest" if args[:2] == ["-m", "pytest"] else args[0]
with open(os.environ["KRY_HARNESS_TEST_TRACE"], "a", encoding="utf-8") as output:
    output.write(json.dumps({"phase": phase, "addopts": os.environ.get("PYTEST_ADDOPTS")}) + "\\n")
messages = {
    "pytest": "1 passed in 0.01s",
    "lab/run_local.py": "All local tests pass.",
    "lab/concurrency_check.py": '{"pass": true, "lost": 0.0}',
    "lab/hole_d_double_spend.py": "RESULT: hole demonstrated=True  fix holds=True  lease atomic=True",
}
print(messages[phase])
raise SystemExit(23 if os.environ.get("KRY_HARNESS_TEST_FAIL") == phase else 0)
''', encoding="utf-8")
    trace = tmp_path / "trace.jsonl"

    def run(rounds="1", fail="", addopts=None):
        env = dict(os.environ, PYTHON=runner.as_posix(), KRY_HARNESS_TEST_PYTHON=Path(sys.executable).as_posix(),
                   KRY_HARNESS_TEST_CHILD=child.as_posix(), KRY_HARNESS_TEST_TRACE=str(trace),
                   KRY_HARNESS_TEST_FAIL=fail)
        env.pop("PYTEST_ADDOPTS", None)
        if addopts is not None:
            env["PYTEST_ADDOPTS"] = addopts
        result = subprocess.run([_bash(), "lab/reproduce.sh", rounds], cwd=ROOT, env=env,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=30)
        rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []
        return result, rows

    return run


@pytest.mark.parametrize("phase", ["pytest", "lab/run_local.py", "lab/concurrency_check.py",
                                   "lab/hole_d_double_spend.py"])
def test_nonzero_child_refuses_success_marker(harness, phase):
    result, rows = harness(fail=phase)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "ALL PROOFS REPRODUCIBLE" not in result.stdout
    assert len(rows) == 4


@pytest.mark.parametrize("rounds", ["0", "-1", "nonsense", "1.5", ""])
def test_invalid_round_count_executes_no_proofs(harness, rounds):
    result, rows = harness(rounds=rounds)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "ALL PROOFS REPRODUCIBLE" not in result.stdout
    assert rows == []


def test_requested_rounds_execute_each_proof_family(harness):
    result, rows = harness(rounds="2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL PROOFS REPRODUCIBLE across 2 rounds" in result.stdout
    assert len(rows) == 8
    assert all(sum(row["phase"] == phase for row in rows) == 2 for phase in
               ("pytest", "lab/run_local.py", "lab/concurrency_check.py", "lab/hole_d_double_spend.py"))


def test_harness_does_not_inherit_pytest_selection(harness):
    result, rows = harness(addopts="-k selected_test")
    assert result.returncode == 0, result.stdout + result.stderr
    assert rows[0]["phase"] == "pytest"
    assert rows[0]["addopts"] is None


def test_release_gate_does_not_inherit_pytest_selection(monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k selected_test")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    env = release_verify._clean_python_env({"PYTHON": "pinned-test-python"})
    assert "PYTEST_ADDOPTS" not in env
    assert env["PYTHON"] == "pinned-test-python"
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


@pytest.mark.parametrize("output", ["fix holds=True  lease atomic=True", '{"pass": true, "lost": 0.0}'])
def test_local_runner_rejects_nonzero_child(output):
    with pytest.raises(subprocess.CalledProcessError) as raised:
        run_local._run([sys.executable, "-c", f"import sys; print({output!r}); sys.exit(23)"])
    assert raised.value.returncode == 23
    assert raised.value.stdout.strip() == output


def test_local_runner_preserves_successful_output():
    result = run_local._run([sys.executable, "-c", "print('proof output')"])
    assert result.returncode == 0
    assert result.stdout.strip() == "proof output"


@pytest.mark.parametrize("failed_racer", [0, 1])
def test_lease_race_requires_both_worker_outcomes(tmp_path, failed_racer):
    code = f"""
import itertools
from pathlib import Path
from lab import hole_d_double_spend as proof
original = proof.lease
calls = itertools.count()
def lease(authdir, *args):
    if Path(authdir).name == 'nodeD_race' and next(calls) == {failed_racer}:
        raise OSError('constructed race worker failure')
    return original(authdir, *args)
proof.lease = lease
raise SystemExit(proof.main())
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), KRY_DATA_DIR=str(tmp_path / "data"),
               TMPDIR=str(tmp_path))
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=30)
    assert "constructed race worker failure" in result.stderr
    assert result.returncode != 0, result.stdout + result.stderr
    assert "lease atomic=True" not in result.stdout


def test_local_proof_preserves_caller_data(tmp_path):
    caller = tmp_path / "operator-data"
    caller.mkdir()
    (caller / "canary.txt").write_bytes(b"retained operator data\n")
    before = {path.name: path.read_bytes() for path in caller.iterdir()}
    proofs = tmp_path / "proofs"
    proofs.mkdir()
    env = dict(os.environ, KRY_DATA_DIR=str(caller), TMPDIR=str(proofs))
    result = subprocess.run([sys.executable, "-B", "lab/run_local.py"], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All local tests pass." in result.stdout
    assert {path.name: path.read_bytes() for path in caller.iterdir()} == before
    assert list(proofs.glob("kry_runlocal_*/kry_data/kry_reputation.json"))
