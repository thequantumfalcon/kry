#!/usr/bin/env python3
"""Standalone cross-MACHINE HOLE-D lease probe (stdlib only). Run ON each node.

KRY's settlement double-spend guard is single-host in the shipped code. HOLE D (docs/
KRY_VERACITY_BINDING.md) is that two machines settling the SAME attested balance against
their own registries aren't caught. The disclosed fix is a shared lease authority. This
probe IS that authority's core: an O_EXCL-locked, ceiling-checked leased.json on shared
storage. Point two real machines at the same authdir (a mounted SMB share) for the same
key; if exactly one of two over-balance leases is GRANTED, the guard extends multi-host.

    python hole_d_lease.py <node-id> <authdir> <key> <amount> <ceiling> [hold-sec]

Emits one JSON line (machine-parseable) + one human line. `lock_wait_s` > 0 on the loser
is direct evidence the O_EXCL lock serialized the two machines over the network FS.
"""
from __future__ import annotations

import argparse
import json
import os
import time


def _lock(authdir: str) -> float:
    lock = os.path.join(authdir, ".lock")
    t0 = time.time()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return time.time() - t0
        except FileExistsError:
            if time.time() - t0 > 30:
                raise TimeoutError("lock wait exceeded 30s")
            time.sleep(0.01)


def _unlock(authdir: str) -> None:
    try:
        os.unlink(os.path.join(authdir, ".lock"))
    except FileNotFoundError:
        pass


def lease(authdir: str, key: str, amount: float, ceiling: float, hold: float = 0.0):
    os.makedirs(authdir, exist_ok=True)
    waited = _lock(authdir)
    try:
        p = os.path.join(authdir, "leased.json")
        data = json.loads(open(p, encoding="utf-8").read()) if os.path.exists(p) else {}
        cur = float(data.get(key, 0.0))
        if hold:
            time.sleep(hold)  # widen the critical section so a concurrent racer must wait on the lock
        granted = cur + amount <= ceiling + 1e-9
        if granted:
            data[key] = cur + amount
            with open(p, "w", encoding="utf-8") as f:
                f.write(json.dumps(data))
        return granted, cur, waited
    finally:
        _unlock(authdir)


if __name__ == "__main__":
    _ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _ap.add_argument("node", help="this node's id (printed only)")
    _ap.add_argument("authdir", help="the shared lease directory (e.g. a mounted SMB share)")
    _ap.add_argument("key", help="lease key, e.g. <party>:<attestation hash>")
    _ap.add_argument("amount", type=float)
    _ap.add_argument("ceiling", type=float, help="the attested balance")
    _ap.add_argument("hold", nargs="?", type=float, default=0.0, help="seconds to hold the lock (default 0)")
    _a = _ap.parse_args()
    node, authdir, key = _a.node, _a.authdir, _a.key
    amount, ceiling, hold = _a.amount, _a.ceiling, _a.hold
    granted, cur, waited = lease(authdir, key, amount, ceiling, hold)
    print(json.dumps({"node": node, "granted": granted, "prior_leased": cur,
                      "lock_wait_s": round(waited, 3), "amount": amount, "ceiling": ceiling, "key": key}))
    print(f"NODE {node}: lease {amount:.0f} (prior {cur:.0f}, ceiling {ceiling:.0f}) -> "
          f"{'GRANTED' if granted else 'DENIED'}  [waited {waited:.3f}s for the lock]")
