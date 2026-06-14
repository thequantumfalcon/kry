"""Cross-process concurrency for the persistence layers that do read-modify-write.

The ledger bug (lost updates under concurrent processes) had the same root cause in the
supply-decay state and the reputation ledger. These spawn real processes against a shared
KRY_DATA_DIR and assert nothing is lost — the cross-process file lock holds.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")


def _spawn(code: str, share: Path, n: int) -> None:
    env = {**os.environ, "PYTHONPATH": _SRC, "KRY_DATA_DIR": str(share)}
    procs = [subprocess.Popen([sys.executable, "-c", code], env={**env, "WID": str(w)})
             for w in range(n)]
    for p in procs:
        assert p.wait() == 0


def test_decay_cap_holds_across_processes(tmp_path):
    """Several processes minting the SAME evidence must not breach the replay cap
    (tokens/(1-decay) = 1000/0.5 = 2000). Without the cross-process lock each process
    would read count=0 and over-mint."""
    share = tmp_path / "share"
    share.mkdir()
    # each process mints the same evidence 100x; 4 processes => 400 attempts on one evidence
    code = (
        "import os,sys; sys.path.insert(0,os.environ['PYTHONPATH'].split(os.pathsep)[0]);"
        "import kry.kry_mint as m;"
        "[m.mint('cache_hit',1000,'same',evidence='SHARED_EV',avoided_model='gh/claude-opus-4.8')"
        " for _ in range(100)]"
    )
    _spawn(code, share, n=4)
    # sum minted for that evidence from the mint log
    log = share / "kry_mint_log.jsonl"
    total = sum(json.loads(ln)["kry_minted"]
                for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip())
    assert total <= 2000.0 + 1.0, f"replay cap breached across processes: {total}"


def test_reputation_updates_not_lost_across_processes(tmp_path):
    """N processes each record M confirmations for one party; the confirmed count must
    equal N*M (no lost updates)."""
    share = tmp_path / "share"
    share.mkdir()
    n, m = 4, 50
    code = (
        "import os,sys; sys.path.insert(0,os.environ['PYTHONPATH'].split(os.pathsep)[0]);"
        "import kry.kry_sanctions as ks;"
        f"[ks.record_reconciliation('A', True) for _ in range({m})]"
    )
    _spawn(code, share, n=n)
    rep = json.loads((share / "kry_reputation.json").read_text(encoding="utf-8"))
    assert rep["A"]["confirmed"] == n * m, f"lost reputation updates: {rep['A']['confirmed']} != {n*m}"
