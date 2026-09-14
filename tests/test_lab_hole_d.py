"""Lab Test 3 lease authority — CI-covered so the HOLE D fix claim stays honest.

Tests the lease primitive directly (pure file-locked accounting, independent of the
kry module globals): it caps cumulative leases at the attested ceiling and grants
atomically under a concurrent race.
"""
from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

_HD = Path(__file__).resolve().parents[1] / "lab" / "hole_d_double_spend.py"


def _load():
    spec = importlib.util.spec_from_file_location("hole_d", _HD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lease_caps_at_attested_ceiling(tmp_path):
    hd = _load()
    assert hd.lease(tmp_path, "A:att", 7000, 10000) is True   # first fits
    assert hd.lease(tmp_path, "A:att", 7000, 10000) is False  # 14000 > 10000 -> denied
    assert hd.lease(tmp_path, "A:att", 3000, 10000) is True   # 7000+3000 == 10000 -> ok
    assert hd.lease(tmp_path, "A:att", 1, 10000) is False     # nothing left


def test_lease_is_atomic_under_race(tmp_path):
    hd = _load()
    results: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def racer():
        try:
            g = hd.lease(tmp_path, "A:race", 7000, 10000)   # only one 7000 fits under 10000
        except BaseException as exc:   # a crashed racer must fail the test, not pass as a denial
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(g)

    threads = [threading.Thread(target=racer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"racer thread raised: {errors!r}"
    assert len(results) == len(threads)
    assert sum(results) == 1, "exactly one lease may win the race"


def test_lock_retries_transient_permission_error_on_windows(tmp_path, monkeypatch):
    """The Windows race, deterministically: a transient PermissionError on the O_EXCL create is
    contention, not a crash."""
    hd = _load()
    monkeypatch.setattr(hd, "_WINDOWS", True)
    real_open, calls = hd.os.open, {"n": 0}

    def fake_open(path, flags, *args, **kwargs):
        if str(path).endswith(".lock"):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(hd.os, "open", fake_open)
    hd._lock(tmp_path)
    assert calls["n"] == 3 and (tmp_path / ".lock").exists()
    hd._unlock(tmp_path)
