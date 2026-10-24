"""First-writer interleaving and complete concurrency-worker outcomes."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from kry import kry_mint as km, kry_token as kt

ROOT = Path(__file__).resolve().parents[1]


def test_empty_loader_preserves_absent_parent(tmp_path, monkeypatch):
    path = tmp_path / "not-created" / "kry_ledger.json"
    monkeypatch.setattr(kt, "_LEDGER_PATH", path)
    assert kt.get_ledger().balance == 0
    assert not path.parent.exists()


@pytest.mark.parametrize("history", ["mint_log", "pending", "quarantine"])
def test_missing_history_stays_blocked_when_observed_evidence_moves(tmp_path, history):
    if history == "mint_log":
        assert km.mint("cache_hit", 100, evidence="startup-history") is not None
        guard = km._MINT_LOG_PATH
    else:
        assert kt.earn(100, "cache_hit") == 100
        suffix = ".pending.synthetic" if history == "pending" else ".corrupt"
        guard = kt._LEDGER_PATH.with_name(kt._LEDGER_PATH.name + suffix)
    assert kt.spend("paid", 40) == 40
    if history != "mint_log":
        guard.write_text("retained synthetic incident")
    retained = tmp_path / "retained"
    retained.mkdir()
    kt._LEDGER_PATH.rename(retained / kt._LEDGER_PATH.name)
    kt._ledger_instance = None
    ledger = kt.get_ledger()
    assert ledger._recovery_required
    assert kt.status()["available"] is False
    guard.rename(retained / guard.name)
    assert kt.earn(5, "cache_hit") == 0
    assert kt.spend("paid", 1) == 0
    assert not kt.can_afford("paid", 1)
    assert kt.status()["available"] is False
    with pytest.raises(ValueError, match="debit history unavailable"):
        ledger.save()
    assert not kt._LEDGER_PATH.exists()


def _wait_for_any(paths):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if any(path.exists() for path in paths):
            return
        time.sleep(0.002)
    raise AssertionError(f"process did not reach its startup barrier: {paths}")


def test_empty_loader_waits_for_first_writer_before_inspecting_intents(tmp_path):
    control = tmp_path / "control"
    control.mkdir()
    common = """
import contextlib,json,os,time
from pathlib import Path
from kry import kry_token as kt
control = Path(os.environ['CONTROL_DIR'])
def wait_for(name):
    deadline = time.monotonic()+10
    while not (control/name).exists():
        if time.monotonic()>deadline:
            raise TimeoutError(name)
        time.sleep(0.002)
"""
    writer = common + """
original_sync = kt._sync_ledger_directory
calls = 0
def sync():
    global calls
    calls += 1
    if calls == 1:
        (control/'intent-ready').touch()
        wait_for('release-writer')
    return original_sync()
kt._sync_ledger_directory = sync
print(json.dumps(kt.earn(100,'cache_hit')))
"""
    newcomer = common + """
original_lock, original_read = kt._cross_process_lock, Path.read_bytes
@contextlib.contextmanager
def lock(path):
    (control/'lock-attempted').touch()
    with original_lock(path):
        yield
def read(path):
    if '.pending.' in path.name:
        (control/'unlocked-intent-read').touch()
        wait_for('release-reader')
    return original_read(path)
kt._cross_process_lock = lock
Path.read_bytes = read
print(json.dumps(kt.earn(100,'cache_hit')))
"""
    env = {"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"),
           "PYTHONDONTWRITEBYTECODE": "1", "CONTROL_DIR": str(control),
           "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent)}
    processes = []
    try:
        first = subprocess.Popen([sys.executable, "-B", "-c", writer], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes.append(first)
        _wait_for_any([control / "intent-ready"])
        assert not kt._LEDGER_PATH.exists()
        second = subprocess.Popen([sys.executable, "-B", "-c", newcomer], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes.append(second)
        # The old missing-file fast path reads the live marker before trying
        # the lock. Releasing the writer then deterministically removes it.
        _wait_for_any([control / "lock-attempted", control / "unlocked-intent-read"])
        (control / "release-writer").touch()
        first_output, first_errors = first.communicate(timeout=10)
        (control / "release-reader").touch()
        second_output, second_errors = second.communicate(timeout=10)
        assert first.returncode == 0, first_errors
        assert second.returncode == 0, second_errors
        assert json.loads(first_output) == json.loads(second_output) == 100
        assert not (control / "unlocked-intent-read").exists()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    assert json.loads(kt._LEDGER_PATH.read_text())["total_earned"] == 200
    assert not list(kt._LEDGER_PATH.parent.glob("*.pending.*"))


def _concurrency_module():
    spec = importlib.util.spec_from_file_location("concurrency_check", ROOT / "lab/concurrency_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_concurrency_check_retains_successful_worker_outcomes(tmp_path):
    result = _concurrency_module().run(2, 3, str(tmp_path))
    assert result["pass"] and result["lost"] == 0
    assert len(result["worker_outcomes"]) == 2
    for outcome in result["worker_outcomes"]:
        assert outcome["returncode"] == 0
        assert outcome["reported"] == {"calls": 3, "returned_total": 300}
        assert outcome["stderr"] == ""


def test_concurrency_check_fails_on_child_error_even_if_total_matches(tmp_path, monkeypatch):
    module = _concurrency_module()
    monkeypatch.setattr(module, "_WORKER",
                        "import sys,os; sys.path.insert(0,os.environ['SRC']); "
                        "from kry.kry_token import earn; earn(100,'cache_hit'); "
                        "raise RuntimeError('synthetic worker failure')")
    result = module.run(1, 1, str(tmp_path))
    assert result["actual_total_earned"] == result["expected_total_earned"] == 100
    assert result["lost"] == 0 and not result["pass"]
    assert result["worker_outcomes"][0]["returncode"] != 0
    assert "synthetic worker failure" in result["worker_outcomes"][0]["stderr"]


def test_concurrency_check_requires_confirmed_worker_returns(tmp_path, monkeypatch):
    module = _concurrency_module()
    monkeypatch.setattr(module, "_WORKER",
                        "import sys,os,json; sys.path.insert(0,os.environ['SRC']); "
                        "from kry.kry_token import earn; earn(100,'cache_hit'); "
                        "print(json.dumps({'calls':1,'returned_total':0}))")
    result = module.run(1, 1, str(tmp_path))
    assert result["lost"] == 0 and not result["pass"]
    assert result["worker_outcomes"][0]["returncode"] == 0
    assert result["worker_outcomes"][0]["reported"]["returned_total"] == 0


def test_concurrency_check_retains_all_failures_without_a_ledger(tmp_path, monkeypatch):
    module = _concurrency_module()
    monkeypatch.setattr(module, "_WORKER", "raise RuntimeError('synthetic startup failure')")
    result = module.run(2, 1, str(tmp_path))
    assert not result["pass"] and result["actual_total_earned"] is None
    assert result["lost"] is None and result["ledger_error"]
    assert len(result["worker_outcomes"]) == 2
    assert all(outcome["returncode"] != 0 and "synthetic startup failure" in outcome["stderr"]
               for outcome in result["worker_outcomes"])
