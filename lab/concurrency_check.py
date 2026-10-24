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
    "import sys,os,json; sys.path.insert(0,os.environ['SRC']);"
    "import kry.kry_token as kt;"
    "amounts=[kt.earn(100,'cache_hit',f'w{os.environ[\"WID\"]}-{i}',avoided_model='gh/claude-opus-4.8')"
    " for i in range(int(os.environ['EARNS']))];"
    "print(json.dumps({'calls':len(amounts),'returned_total':sum(amounts)}))"
)


def run(workers: int, earns: int, share: str) -> dict:
    Path(share).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SRC": _SRC, "KRY_DATA_DIR": share, "EARNS": str(earns)}
    procs = []
    for w in range(workers):
        stdout = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        process = subprocess.Popen([sys.executable, "-c", _WORKER],
                                   env={**env, "WID": str(w)}, stdout=stdout, stderr=stderr)
        procs.append((process, stdout, stderr))
    outcomes = []
    for w, (p, stdout, stderr) in enumerate(procs):
        p.wait()
        stdout.seek(0)
        stderr.seek(0)
        output, errors = stdout.read(), stderr.read()
        stdout.close()
        stderr.close()
        try:
            reported = json.loads(output)
        except json.JSONDecodeError:
            reported = {}
        if not isinstance(reported, dict):
            reported = {}
        outcomes.append({"worker": w, "returncode": p.returncode, "stdout": output,
                         "stderr": errors, "reported": reported})
    expected = workers * earns * _PER_EARN
    ledger_error = None
    try:
        ledger = json.loads((Path(share) / "kry_ledger.json").read_text(encoding="utf-8"))
        got = float(ledger["total_earned"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        got = None
        ledger_error = str(exc)
    return {
        "workers": workers, "earns_each": earns,
        "expected_total_earned": expected,
        "actual_total_earned": round(got, 2) if got is not None else None,
        "lost": round(expected - got, 2) if got is not None else None,
        "ledger_error": ledger_error,
        "worker_outcomes": outcomes,
        "pass": (got is not None and abs(expected - got) < 1e-6 and all(
            outcome["returncode"] == 0 and outcome["reported"].get("calls") == earns and
            outcome["reported"].get("returned_total") == earns * _PER_EARN
            for outcome in outcomes)),
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
    for _stream in (sys.stdout, sys.stderr):  # a cp1252 Windows console cannot encode the output glyphs
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    sys.exit(main())
