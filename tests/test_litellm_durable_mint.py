"""Committed callback evidence is unique across retries, restarts and workers."""
from __future__ import annotations

import builtins
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "durable_callback", ROOT / "scripts/kry_litellm_callback.py")
callback = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(callback)
RESPONSE = {"usage": {"total_tokens": 1000}}


def event(call_id="durable", model="anthropic/claude-opus-4"):
    return {"cache_hit": True, "model": model, "litellm_call_id": call_id}


@pytest.fixture(autouse=True)
def reset_guard():
    callback._seen_ids.clear()
    callback._seen_order.clear()


def rows(km):
    return [json.loads(line) for line in km._MINT_LOG_PATH.read_text().splitlines()]


def test_failed_append_retry_keeps_all_supplied_tokens(monkeypatch):
    import kry.kry_mint as km

    original = builtins.open

    def unavailable(path, mode="r", *args, **kwargs):
        if Path(path) == km._MINT_LOG_PATH and mode == "a":
            raise OSError("constructed append failure")
        return original(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", unavailable)
        assert callback.mint_from_litellm(event(), RESPONSE) is None
    evidence_hash = hashlib.sha256(b"litellm:durable").hexdigest()
    assert json.loads(km._DECAY_STATE_PATH.read_text())[evidence_hash][0] == 1
    retried = callback.mint_from_litellm(event(), RESPONSE)
    assert retried is not None and retried.tokens_saved == 1000
    assert len(rows(km)) == 1
    assert json.loads(km._DECAY_STATE_PATH.read_text())[evidence_hash][0] == 2
    callback._seen_ids.clear()
    callback._seen_order.clear()
    assert callback.mint_from_litellm(event(), RESPONSE) is None
    assert json.loads(km._DECAY_STATE_PATH.read_text())[evidence_hash][0] == 2


def test_complete_append_with_failed_acknowledgement_is_not_reminted(monkeypatch):
    import kry.kry_mint as km

    original = os.fsync

    def uncertain(fd):
        if (km._MINT_LOG_PATH.exists()
                and os.fstat(fd).st_ino == km._MINT_LOG_PATH.stat().st_ino):
            raise OSError("constructed receipt fsync failure")
        return original(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", uncertain)
        assert callback.mint_from_litellm(event(), RESPONSE) is None
    assert len(rows(km)) == 1
    assert callback.mint_from_litellm(event(), RESPONSE) is None
    assert len(rows(km)) == 1
    assert km.verify_chain()[0]
    evidence_hash = hashlib.sha256(b"litellm:durable").hexdigest()
    assert json.loads(km._DECAY_STATE_PATH.read_text())[evidence_hash][0] == 1


@pytest.mark.parametrize("broken", ["{partial", "{}\n"])
def test_corrupt_log_refuses_mint_without_spending_decay(broken):
    import kry.kry_mint as km

    km._MINT_LOG_PATH.write_text(broken)
    assert callback.mint_from_litellm(event(), RESPONSE) is None
    assert km._MINT_LOG_PATH.read_text() == broken
    assert not km._DECAY_STATE_PATH.exists()


def test_unterminated_record_refuses_append():
    import kry.kry_mint as km

    assert callback.mint_from_litellm(event(), RESPONSE) is not None
    retained = km._MINT_LOG_PATH.read_text().rstrip("\n")
    km._MINT_LOG_PATH.write_text(retained)
    assert callback.mint_from_litellm(event("next"), RESPONSE) is None
    assert km._MINT_LOG_PATH.read_text() == retained


@pytest.mark.parametrize("checkpoint", ["{broken", '{"count":0,"tip":"not-genesis"}'])
def test_missing_log_does_not_ignore_invalid_checkpoint(checkpoint):
    import kry.kry_mint as km

    km._mint_tip_path().write_text(checkpoint)
    assert callback.mint_from_litellm(event(), RESPONSE) is None
    assert not km._MINT_LOG_PATH.exists()
    assert km._mint_tip_path().read_text() == checkpoint


def test_truncated_retained_log_is_not_extended():
    import kry.kry_mint as km

    for index in range(3):
        assert callback.mint_from_litellm(event(str(index)), RESPONSE) is not None
    retained = km._MINT_LOG_PATH.read_text().splitlines()[0] + "\n"
    km._MINT_LOG_PATH.write_text(retained)
    assert callback.mint_from_litellm(event("new"), RESPONSE) is None
    assert km._MINT_LOG_PATH.read_text() == retained


def test_existing_generic_repeated_evidence_does_not_block_unrelated_callback():
    import kry.kry_mint as km

    for _ in range(2):
        assert km.mint("cache_hit", 1000, evidence="generic-repeated") is not None
    assert callback.mint_from_litellm(event(), RESPONSE) is not None
    assert len(rows(km)) == 3
    assert km.verify_chain()[0]


def test_strict_then_generic_replay_preserves_shared_decay_bound():
    import kry.kry_mint as km

    first = callback.mint_from_litellm(event("mixed"), RESPONSE)
    assert first is not None and first.tokens_saved == 1000
    for _ in range(20):
        km.mint("cache_hit", 1000, evidence="litellm:mixed",
                avoided_model="anthropic/claude-opus-4")
    retained = rows(km)
    assert retained[1]["tokens_saved"] == 500
    assert 1900 < sum(row["tokens_saved"] for row in retained) <= 2000
    assert km.verify_chain()[0]


def test_zero_value_committed_receipt_still_blocks_replay():
    import kry.kry_mint as km

    first = callback.mint_from_litellm(event(model="google/gemini-flash"), RESPONSE)
    assert first is not None and first.kry_minted == 0
    callback._seen_ids.clear()
    callback._seen_order.clear()
    assert callback.mint_from_litellm(event(model="google/gemini-flash"), RESPONSE) is None
    assert len(rows(km)) == 1


def test_committed_evidence_survives_4096_key_fast_path_eviction(monkeypatch):
    import kry.kry_mint as km

    assert callback.mint_from_litellm(event("oldest"), RESPONSE) is not None
    # Exercise the bounded memory algorithm, not 4,096 durable log appends.
    with monkeypatch.context() as patch:
        patch.setattr(km, "mint", lambda *args, **kwargs: object())
        for index in range(4096):
            assert callback.mint_from_litellm(event(f"fill-{index}"), RESPONSE) is not None
    assert "litellm:oldest" not in callback._seen_ids
    assert callback.mint_from_litellm(event("oldest"), RESPONSE) is None
    assert len(rows(km)) == 1


WORKER = """
import json, sys, time
from pathlib import Path
import kry_litellm_callback as callback
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text("ready")
    deadline = time.monotonic() + 20
    while not Path(sys.argv[2]).exists():
        if time.monotonic() > deadline:
            raise RuntimeError("release deadline")
        time.sleep(0.01)
receipt = callback.mint_from_litellm(
    {"cache_hit": True, "model": "anthropic/claude-opus-4", "litellm_call_id": "shared"},
    {"usage": {"total_tokens": 1000}})
print(json.dumps({"minted": receipt is not None}))
"""


def worker_env(directory):
    return {
        "PATH": os.defpath, "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts"))),
        "KRY_DATA_DIR": str(directory), "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def test_same_callback_after_process_restart_mints_once(tmp_path):
    share = tmp_path / "restart"
    share.mkdir()
    results = [subprocess.run([sys.executable, "-c", WORKER], env=worker_env(share),
                              capture_output=True, text=True, timeout=30) for _ in range(2)]
    assert all(result.returncode == 0 for result in results)
    minted = [json.loads(result.stdout)["minted"] for result in results]
    assert minted == [True, False]
    assert len((share / "kry_mint_log.jsonl").read_text().splitlines()) == 1


def test_same_callback_in_concurrent_processes_mints_once(tmp_path):
    share = tmp_path / "workers"
    share.mkdir()
    release = tmp_path / "release"
    ready = [tmp_path / f"ready-{index}" for index in range(2)]
    workers = [
        subprocess.Popen([sys.executable, "-c", WORKER, str(marker), str(release)],
                         env=worker_env(share), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
        for marker in ready
    ]
    try:
        deadline = time.monotonic() + 20
        while not all(marker.exists() for marker in ready) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert all(marker.exists() for marker in ready)
        release.write_text("go")
        outputs = [worker.communicate(timeout=30) for worker in workers]
        assert all(worker.returncode == 0 for worker in workers), outputs
        assert sum(json.loads(out)["minted"] for out, _ in outputs) == 1
        assert len((share / "kry_mint_log.jsonl").read_text().splitlines()) == 1
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.communicate()


def test_waiting_generic_writer_cannot_reserve_before_strict_commit(tmp_path):
    share = tmp_path / "waiting-generic"
    share.mkdir()
    ready, release = tmp_path / "generic-before-lock", tmp_path / "release-generic"
    code = """
import sys,time
from contextlib import contextmanager
from pathlib import Path
import kry._locks as locks
import kry.kry_mint as km
original = locks.cross_process_lock
waiting = True
@contextmanager
def paused(path):
    global waiting
    if Path(path) == km._MINT_LOG_PATH and waiting:
        waiting = False
        Path(sys.argv[1]).write_text("generic has not acquired mint lock")
        deadline = time.monotonic() + 20
        while not Path(sys.argv[2]).exists():
            if time.monotonic() > deadline:
                raise RuntimeError("release deadline")
            time.sleep(0.01)
    with original(path):
        yield
locks.cross_process_lock = paused
for _ in range(20):
    km.mint("cache_hit", 1000, evidence="litellm:shared",
            avoided_model="anthropic/claude-opus-4")
"""
    generic = subprocess.Popen(
        [sys.executable, "-c", code, str(ready), str(release)],
        env=worker_env(share), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        strict = subprocess.run([sys.executable, "-c", WORKER], env=worker_env(share),
                                capture_output=True, text=True, timeout=30)
        assert strict.returncode == 0, strict.stderr
        assert json.loads(strict.stdout)["minted"]
        release.write_text("go")
        output = generic.communicate(timeout=30)
        assert generic.returncode == 0, output
        retained = [json.loads(line) for line in
                    (share / "kry_mint_log.jsonl").read_text().splitlines()]
        assert retained[0]["tokens_saved"] == 1000
        assert retained[1]["tokens_saved"] == 500
        assert 1900 < sum(row["tokens_saved"] for row in retained) <= 2000
    finally:
        if generic.poll() is None:
            generic.kill()
            generic.communicate()


def test_concurrent_strict_and_generic_writers_share_decay_budget(tmp_path):
    share = tmp_path / "mixed-workers"
    share.mkdir()
    strict_ready, generic_ready, release = (
        tmp_path / name for name in ("strict-ready", "generic-ready", "release-mixed"))
    strict_code = """
import sys,time
from pathlib import Path
import kry.kry_mint as km
import kry_litellm_callback as callback
original = km._write_mint_tip
def held_checkpoint(*args):
    original(*args)
    Path(sys.argv[1]).write_text("strict holds mint lock")
    deadline = time.monotonic() + 20
    while not Path(sys.argv[2]).exists():
        if time.monotonic() > deadline:
            raise RuntimeError("release deadline")
        time.sleep(0.01)
km._write_mint_tip = held_checkpoint
receipt = callback.mint_from_litellm(
    {"cache_hit":True,"model":"anthropic/claude-opus-4","litellm_call_id":"mixed"},
    {"usage":{"total_tokens":1000}})
assert receipt is not None and receipt.tokens_saved == 1000
"""
    generic_code = """
import sys
from pathlib import Path
import kry.kry_mint as km
Path(sys.argv[1]).write_text("generic ready")
for _ in range(20):
    km.mint("cache_hit", 1000, evidence="litellm:mixed",
            avoided_model="anthropic/claude-opus-4")
"""
    workers = []

    def wait_for(path):
        deadline = time.monotonic() + 20
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert path.exists()

    try:
        workers.append(subprocess.Popen(
            [sys.executable, "-c", strict_code, str(strict_ready), str(release)],
            env=worker_env(share), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        wait_for(strict_ready)
        workers.append(subprocess.Popen(
            [sys.executable, "-c", generic_code, str(generic_ready)],
            env=worker_env(share), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        wait_for(generic_ready)
        release.write_text("go")
        outputs = [worker.communicate(timeout=30) for worker in workers]
        assert all(worker.returncode == 0 for worker in workers), outputs
        retained = [json.loads(line) for line in (share / "kry_mint_log.jsonl").read_text().splitlines()]
        assert retained[0]["tokens_saved"] == 1000
        assert retained[1]["tokens_saved"] == 500
        assert 1900 < sum(row["tokens_saved"] for row in retained) <= 2000
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.communicate()
