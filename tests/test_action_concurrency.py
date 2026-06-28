"""CONC-2 — kry_action.record() must be cross-process safe.

record() previously chained off a per-process in-memory tip under an in-process lock only, so a
multi-worker MCP/ASGI server forked the action chain (each worker appended off its own stale tip).
It now re-reads the authoritative file tip under a cross-process lock on every append. This pins
that N real processes racing to record against ONE KRY_DATA_DIR produce a single intact chain.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
_GENESIS = "0" * 64


def test_record_cross_process_no_chain_fork(tmp_path):
    """5 processes × 10 records against one shared log → exactly 50 links that re-derive from genesis
    with no duplicate ids and no broken link. A fork (the old behaviour) breaks the chain equality."""
    per_proc = 10
    n_proc = 5
    code = (
        "import os, sys; sys.path.insert(0, os.environ['PYTHONPATH'].split(os.pathsep)[0]);"
        "import kry.kry_action as ka;"
        f"[ka.record('tool', {{'i': i, 'w': os.environ['WID']}}) for i in range({per_proc})]"
    )
    env = {**os.environ, "PYTHONPATH": _SRC, "KRY_DATA_DIR": str(tmp_path)}
    procs = [subprocess.Popen([sys.executable, "-c", code], env={**env, "WID": f"w{w}"})
             for w in range(n_proc)]
    for p in procs:
        p.wait()
        assert p.returncode == 0

    lines = [ln for ln in (tmp_path / "kry_action_log.jsonl").read_text().splitlines() if ln.strip()]
    assert len(lines) == n_proc * per_proc, f"lost/forked writes: got {len(lines)} of {n_proc * per_proc}"

    prev, seen = _GENESIS, set()
    for ln in lines:
        rec = json.loads(ln)
        assert rec["receipt_id"] not in seen, f"duplicate receipt_id {rec['receipt_id']} — chain forked"
        seen.add(rec["receipt_id"])
        expected = hashlib.sha256(f"{prev}:{rec['receipt_hash']}".encode()).hexdigest()
        assert rec["chain_hash"] == expected, "chain link broken — concurrent writers forked the chain"
        prev = rec["chain_hash"]
