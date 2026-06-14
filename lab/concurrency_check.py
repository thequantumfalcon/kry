#!/usr/bin/env python3
"""Lab Test 6 — concurrency correctness of the shared ledger.

Spawns N worker PROCESSES that each record M earns into ONE shared KRY_DATA_DIR (on real
hardware: the NFS/SMB share all four nodes mount). If the delta-merge save is correct,
the final total_earned == N*M*per_earn with NO lost updates. This is the multi-process
analog of four nodes hammering a shared ledger. Pure stdlib.

Usage:
    python lab/concurrency_check.py --workers 4 --earns 200 --share /mnt/share/kry_ledger
    python lab/concurrency_check.py                 # defaults, uses a temp dir
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
_PER_EARN = 100.0   # tokens per earn (cache_hit, avoided gh/opus -> 100 KRY each)

# A worker mints PER_EARN-token cache hits into KRY_DATA_DIR; distinct evidence per earn
# so the decay guard doesn't collapse them.
_WORKER = (
    "import sys,os; sys.path.insert(0,os.environ['SRC']);"
    "import kry.kry_token as kt;"
    "[kt.earn(100,'cache_hit',f'w{os.environ[\"WID\"]}-{i}',avoided_model='gh/claude-opus-4.8')"
    " for i in range(int(os.environ['EARNS']))]"
)


def run(workers: int, earns: int, share: str) -> dict:
    Path(share).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SRC": _SRC, "KRY_DATA_DIR": share, "EARNS": str(earns)}
    procs = []
    for w in range(workers):
        procs.append(subprocess.Popen([sys.executable, "-c", _WORKER],
                                      env={**env, "WID": str(w)}))
    for p in procs:
        p.wait()
    ledger = json.loads((Path(share) / "kry_ledger.json").read_text(encoding="utf-8"))
    expected = workers * earns * _PER_EARN
    got = float(ledger.get("total_earned", 0.0))
    return {
        "workers": workers, "earns_each": earns,
        "expected_total_earned": expected,
        "actual_total_earned": round(got, 2),
        "lost": round(expected - got, 2),
        "pass": abs(expected - got) < 1e-6,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="KRY shared-ledger concurrency check (Test 6)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--earns", type=int, default=200)
    p.add_argument("--share", default=None, help="shared KRY_DATA_DIR (NFS on real nodes)")
    args = p.parse_args(argv)
    share = args.share or tempfile.mkdtemp(prefix="kry_conc_")
    res = run(args.workers, args.earns, share)
    print(json.dumps(res, indent=2))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
