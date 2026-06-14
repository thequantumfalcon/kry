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
    lock = threading.Lock()

    def racer():
        g = hd.lease(tmp_path, "A:race", 7000, 10000)   # only one 7000 fits under 10000
        with lock:
            results.append(g)

    threads = [threading.Thread(target=racer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 1, "exactly one lease may win the race"
