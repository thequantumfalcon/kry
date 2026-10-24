"""Cancellation preserves operation outcomes without replaying cached deltas."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from kry import kry_mint as km, kry_token as kt

ROOT = Path(__file__).resolve().parents[1]
CANCELLATIONS = [KeyboardInterrupt, asyncio.CancelledError]
PRECOMMIT = ["temp_create", "temp_fsync", "intent_create", "intent_fsync",
             "intent_directory", "replace_before"]
POSTREPLACE = ["replace_after", "target_directory_before", "target_directory_after",
               "adopt_before", "adopt_after", "marker_clear_before", "marker_clear_after"]


def disk():
    return json.loads(kt._LEDGER_PATH.read_text())


def fresh_status():
    result = subprocess.run(
        [sys.executable, "-B", "-c", "import json; from kry.kry_token import status; print(json.dumps(status()))"],
        capture_output=True, text=True, check=True, timeout=20,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"),
             "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent), "PYTHONDONTWRITEBYTECODE": "1"})
    return json.loads(result.stdout)


def interrupt_at(patch, boundary, error):
    originals = (tempfile.mkstemp, os.fsync, Path.open, kt._sync_ledger_directory,
                 os.replace, kt.KRYLedger._adopt, Path.unlink)
    create, flush, opening, directory, replace, adopt, unlink = originals
    fired = False
    flushes = directories = 0

    def stop(name):
        nonlocal fired
        if name == boundary and not fired:
            fired = True
            raise error

    def make_temp(*args, **kwargs):
        stop("temp_create")
        return create(*args, **kwargs)

    def fsync(fd):
        nonlocal flushes
        flushes += 1
        if flushes == 1:
            stop("temp_fsync")
        elif flushes == 2:
            stop("intent_fsync")
        return flush(fd)

    def open_file(path, *args, **kwargs):
        if ".pending." in path.name:
            stop("intent_create")
        return opening(path, *args, **kwargs)

    def sync_directory():
        nonlocal directories
        directories += 1
        if directories == 1:
            stop("intent_directory")
        elif directories == 2:
            stop("target_directory_before")
        directory()
        if directories == 2:
            stop("target_directory_after")

    def replace_file(source, target):
        if Path(target) == kt._LEDGER_PATH:
            stop("replace_before")
            replace(source, target)
            stop("replace_after")
        else:
            replace(source, target)

    def adopt_state(self, raw):
        if raw["total_spent"] == 100:
            stop("adopt_before")
        adopt(self, raw)
        if raw["total_spent"] == 100:
            stop("adopt_after")

    def remove(path, *args, **kwargs):
        if ".pending." in path.name:
            stop("marker_clear_before")
        unlink(path, *args, **kwargs)
        if ".pending." in path.name:
            stop("marker_clear_after")

    patch.setattr(tempfile, "mkstemp", make_temp)
    patch.setattr(os, "fsync", fsync)
    patch.setattr(Path, "open", open_file)
    patch.setattr(kt, "_sync_ledger_directory", sync_directory)
    patch.setattr(os, "replace", replace_file)
    patch.setattr(kt.KRYLedger, "_adopt", adopt_state)
    patch.setattr(Path, "unlink", remove)


@pytest.mark.parametrize("cancel", CANCELLATIONS)
@pytest.mark.parametrize("boundary", PRECOMMIT)
def test_precommit_interrupt_rolls_back_before_read_or_retry(monkeypatch, cancel, boundary):
    assert kt.earn(300, "cache_hit") == 300
    before = kt._LEDGER_PATH.read_bytes()
    error = cancel(boundary)
    with monkeypatch.context() as patch:
        interrupt_at(patch, boundary, error)
        with pytest.raises(cancel) as caught:
            kt.spend("paid", 100)
    assert caught.value is error
    assert kt.get_ledger().total_spent == 0
    assert not kt._ledger_incidents()
    assert not list(kt._LEDGER_PATH.parent.glob(".kry_*"))
    assert fresh_status()["total_spent_kry"] == 0
    assert kt.can_afford("paid", 1)
    assert kt._LEDGER_PATH.read_bytes() == before
    assert kt.spend("paid", 100) == 100
    assert disk()["total_spent"] == 100


@pytest.mark.parametrize("cancel", CANCELLATIONS)
@pytest.mark.parametrize("boundary", POSTREPLACE)
def test_postreplace_interrupt_keeps_durable_outcome_and_propagates(monkeypatch, cancel, boundary):
    assert kt.earn(300, "cache_hit") == 300
    error = cancel(boundary)
    with monkeypatch.context() as patch:
        interrupt_at(patch, boundary, error)
        with pytest.raises(cancel) as caught:
            kt.spend("paid", 100)
    assert caught.value is error
    assert disk()["total_spent"] == 100
    uncertain = boundary.startswith("target_directory_")
    assert kt.get_ledger().total_spent == (0 if uncertain else 100)
    assert kt.get_ledger()._last_write_committed is (not uncertain)
    if boundary == "marker_clear_after":
        # The commit and cleanup completed, but the caller missed its return.
        # No API without a caller operation ID promises an exactly-once retry.
        assert not kt._ledger_incidents()
        assert fresh_status()["total_spent_kry"] == 100
        assert kt.can_afford("paid", 1)
    else:
        assert kt._ledger_incidents()
        assert fresh_status()["available"] is False
        assert not kt.can_afford("paid", 1)
        assert kt.spend("paid", 100) == 0
    assert disk()["total_spent"] == 100


@pytest.mark.parametrize("operation", ["spend", "earn", "reconcile"])
@pytest.mark.parametrize("cancel", CANCELLATIONS)
def test_interrupt_before_commit_helper_restores_public_mutations(monkeypatch, operation, cancel):
    assert km.mint("cache_hit", 300, evidence="seed") is not None
    if operation == "reconcile":
        with monkeypatch.context() as patch:
            patch.setattr(kt, "earn", lambda *args, **kwargs: 0)
            assert km.mint("cache_hit", 100, evidence="awaiting credit") is not None
    ledger = kt.get_ledger()
    assert ledger._last_write_committed
    before = ledger._operation_snapshot()
    retained = kt._LEDGER_PATH.read_bytes()
    error = cancel("before helper")

    def fail(*args, **kwargs):
        raise error

    with monkeypatch.context() as patch:
        if operation == "reconcile":
            original_max = max

            def max_after_earnings(*args):
                if args == (0.0, 400.0):
                    raise error
                return original_max(*args)

            patch.setattr(km, "max", max_after_earnings, raising=False)
            call = km.reconcile_ledger_from_chain
        else:
            patch.setattr(kt.time, "time", fail)
            call = (lambda: kt.spend("paid", 100)) if operation == "spend" else (lambda: kt.earn(100, "cache_hit"))
        with pytest.raises(cancel) as caught:
            call()
    assert caught.value is error
    assert ledger._operation_snapshot() == before
    assert not ledger._last_write_committed
    assert kt.can_afford("paid", 1)
    assert kt._LEDGER_PATH.read_bytes() == retained


def test_affordability_does_not_commit_direct_save_pending_delta(monkeypatch):
    assert kt.earn(300, "cache_hit") == 300
    ledger = kt.get_ledger()
    ledger.balance -= 100
    ledger.total_spent += 100
    retained = kt._LEDGER_PATH.read_bytes()
    with monkeypatch.context() as patch:
        interrupt_at(patch, "temp_fsync", KeyboardInterrupt())
        with pytest.raises(KeyboardInterrupt):
            ledger.save()
    assert ledger.total_spent == 100  # Explicit direct-save edits remain pending.
    assert kt.can_afford("paid", 250)  # Query the durable300, not pending200.
    assert kt._LEDGER_PATH.read_bytes() == retained
    assert ledger.total_spent == 100
    ledger.save()
    assert disk()["total_spent"] == 100


@pytest.mark.parametrize("boundary", ["readback", "proof_barrier"])
def test_interrupt_during_lost_ack_proof_retains_uncertain_state(monkeypatch, boundary):
    assert kt.earn(300, "cache_hit") == 300
    replace, read, sync = os.replace, Path.read_text, kt._sync_ledger_directory
    replaced = False
    error = asyncio.CancelledError(boundary)

    def lose_ack(source, target):
        nonlocal replaced
        replace(source, target)
        if Path(target) == kt._LEDGER_PATH:
            replaced = True
            raise OSError("lost replace acknowledgement")

    def fail_read(path, *args, **kwargs):
        if replaced and path == kt._LEDGER_PATH and boundary == "readback":
            raise error
        return read(path, *args, **kwargs)

    def fail_sync():
        if replaced and boundary == "proof_barrier":
            raise error
        sync()

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", lose_ack)
        patch.setattr(Path, "read_text", fail_read)
        patch.setattr(kt, "_sync_ledger_directory", fail_sync)
        with pytest.raises(asyncio.CancelledError) as caught:
            kt.spend("paid", 100)
    assert caught.value is error
    assert kt.get_ledger()._recovery_required
    assert kt.get_ledger().total_spent == 0
    assert disk()["total_spent"] == 100
    assert kt._ledger_incidents()
    assert fresh_status()["available"] is False


@pytest.mark.parametrize("cleanup", ["temp_unlink", "snapshot_restore"])
def test_cleanup_error_preserves_original_precommit_cancellation(monkeypatch, cleanup):
    assert kt.earn(300, "cache_hit") == 300
    retained = kt._LEDGER_PATH.read_bytes()
    error = KeyboardInterrupt("original cancellation")
    with monkeypatch.context() as patch:
        if cleanup == "temp_unlink":
            interrupt_at(patch, "temp_fsync", error)
            original_unlink = os.unlink

            def fail_temp(path, *args, **kwargs):
                if Path(path).name.startswith(".kry_"):
                    raise OSError("temp cleanup failed")
                return original_unlink(path, *args, **kwargs)

            patch.setattr(os, "unlink", fail_temp)
        else:
            original_adopt = kt.KRYLedger._adopt
            interrupted = False

            def interrupt():
                nonlocal interrupted
                interrupted = True
                raise error

            def fail_restore(self, raw):
                if interrupted:
                    raise OSError("snapshot restoration failed")
                return original_adopt(self, raw)

            patch.setattr(kt.time, "time", interrupt)
            patch.setattr(kt.KRYLedger, "_adopt", fail_restore)
        with pytest.raises(KeyboardInterrupt) as caught:
            kt.spend("paid", 100)
    assert caught.value is error
    assert kt.get_ledger()._recovery_required
    assert not kt.can_afford("paid", 1)
    assert kt.status()["available"] is False
    assert kt._LEDGER_PATH.read_bytes() == retained
    # No replace or durable intent occurred; a restart sees the unchanged ledger.
    assert fresh_status()["total_spent_kry"] == 0


def test_adoption_error_during_cancelled_lost_ack_keeps_incident(monkeypatch):
    assert kt.earn(300, "cache_hit") == 300
    replace, adopt = os.replace, kt.KRYLedger._adopt
    error = asyncio.CancelledError("replace acknowledgement interrupted")

    def replace_and_cancel(source, target):
        replace(source, target)
        if Path(target) == kt._LEDGER_PATH:
            raise error

    def fail_adoption(self, raw):
        if raw["total_spent"] == 100:
            raise OSError("adoption interrupted by another failure")
        return adopt(self, raw)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace_and_cancel)
        patch.setattr(kt.KRYLedger, "_adopt", fail_adoption)
        with pytest.raises(asyncio.CancelledError) as caught:
            kt.spend("paid", 100)
    assert caught.value is error
    assert kt.get_ledger()._recovery_required
    assert kt.get_ledger().total_spent == 0
    assert disk()["total_spent"] == 100
    assert kt._ledger_incidents()
    assert not kt.can_afford("paid", 1)
    assert fresh_status()["available"] is False


@pytest.mark.parametrize("cancel", [None, *CANCELLATIONS])
def test_refresh_adoption_cannot_replay_another_writers_debit(cancel):
    assert kt.earn(300, "cache_hit") == 300
    ledger = kt.get_ledger()
    subprocess.run(
        [sys.executable, "-B", "-c",
         "from kry.kry_token import spend; assert spend('paid', 100) == 100"],
        capture_output=True, text=True, check=True, timeout=20,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"),
             "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent), "PYTHONDONTWRITEBYTECODE": "1"})
    retained = kt._LEDGER_PATH.read_bytes()
    assert disk()["total_spent"] == 100
    assert ledger._baseline["total_spent"] == 0
    if cancel is None:
        assert kt.spend("paid", 100) == 100
        assert ledger.total_spent == ledger._baseline["total_spent"] == 200
        assert disk()["total_spent"] == 200
        assert not kt._ledger_incidents()
        return

    lines, start = inspect.getsourcelines(kt.KRYLedger._adopt)
    boundary = next(start + i for i, line in enumerate(lines)
                    if "self.events, self.cycle_count" in line)
    error = cancel("refresh interrupted after scalars, before baseline")
    fired = False

    def interrupt(frame, event, arg):
        nonlocal fired
        if (not fired and event == "line" and frame.f_code is kt.KRYLedger._adopt.__code__
                and frame.f_lineno == boundary and frame.f_locals.get("self") is ledger):
            fired = True
            raise error
        return interrupt

    previous_trace = sys.gettrace()
    try:
        sys.settrace(interrupt)
        with pytest.raises(cancel) as caught:
            kt.spend("paid", 100)
    finally:
        sys.settrace(previous_trace)
    assert fired
    assert caught.value is error
    assert ledger._recovery_required
    assert kt.status()["available"] is False
    assert not kt.can_afford("paid", 1)
    assert kt.spend("paid", 100) == 0
    assert kt._LEDGER_PATH.read_bytes() == retained
    assert not kt._ledger_incidents()
    # Refresh wrote nothing: another instance can use the unchanged durable state.
    cold = fresh_status()
    assert cold["available"] is True
    assert cold["total_spent_kry"] == 100
