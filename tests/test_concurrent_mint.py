"""Regression: the mint chain must not fork under concurrent / multi-writer use.

The real defect (caught live 2026-06-04): the chained append cached the chain
tip + receipt counter in process memory and serialized only with a threading
lock. A second writer (a long-running process + an ad-hoc script, or two nodes
on a shared KRY_DATA_DIR) minting from a stale in-memory tip forked the chain —
duplicate receipt_ids and broken links. The fix re-reads the AUTHORITATIVE tip
from the file under a cross-process lock on every mint AND promotion. These tests
pin that behaviour for both append paths.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")


def test_stale_in_memory_tip_does_not_fork():
    """A second writer's stale in-memory tip is the exact fork trigger. Simulate
    it: corrupt the cached tip + counter between mints (as a divergent process
    would have), and the next mint must still chain off the file's real tip."""
    import kry.kry_mint as km

    km.mint("cache_hit", 50, "first")

    # Simulate a second process that never saw the first mint: garbage tip,
    # rewound counter. The old code chained off these and forked.
    km._CHAIN_TIP = "deadbeef" * 8
    km._RECEIPT_COUNTER = 0

    km.mint("cache_hit", 50, "second")

    ok, errs = km.verify_chain()
    assert ok, f"chain forked under stale in-memory state: {errs}"

    ids = [json.loads(line)["receipt_id"] for line in open(km._MINT_LOG_PATH, encoding="utf-8")]
    assert ids == ["KRY-00000001", "KRY-00000002"], ids


def test_promotion_chains_off_file_tip_not_stale_memory():
    """promote_to_tlsn() must re-read the file tip too — a stale in-memory tip
    between the host's T1 mint and the verifier's T2 promotion would otherwise
    fork at the promotion receipt."""
    import kry.kry_mint as km

    # A T1 displacement receipt the host would have stamped for an OpenRouter gen.
    km.mint(
        "displacement",
        100,
        "served via cheap leg /openrouter:gen-xyz",
        avoided_model="gh/claude-opus-4.8",
        evidence_tier=km.TIER_PROVIDER_METERED,
        metered_tokens=[10, 20],
    )

    # Verifier runs in a fresh process: its in-memory tip never saw the T1 mint.
    km._CHAIN_TIP = "deadbeef" * 8
    km._RECEIPT_COUNTER = 0

    promoted = km.promote_to_tlsn("gen-xyz", "tlsn:notarized-bytes", "T2 attest")
    assert promoted is not None, "promotion should upgrade the prior T1 receipt"

    ok, errs = km.verify_chain()
    assert ok, f"chain forked at promotion under stale in-memory state: {errs}"

    # Idempotent: a re-run does not stack a second promotion.
    assert km.promote_to_tlsn("gen-xyz", "tlsn:notarized-bytes", "T2 attest") is None
    ok, errs = km.verify_chain()
    assert ok, errs


def test_concurrent_threads_keep_chain_valid():
    import kry.kry_mint as km

    def burst(tag):
        for i in range(15):
            km.mint("cache_hit", 50, f"{tag}/{i}")

    threads = [threading.Thread(target=burst, args=(t,)) for t in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ids = [json.loads(line)["receipt_id"] for line in open(km._MINT_LOG_PATH, encoding="utf-8")]
    assert len(ids) == len(set(ids)) == 90, f"{len(ids)} lines, {len(set(ids))} unique"
    ok, errs = km.verify_chain()
    assert ok, errs


def test_concurrent_processes_keep_chain_valid(tmp_path):
    """The actual defect was cross-PROCESS, not cross-thread. Spawn N real
    processes against one shared KRY_DATA_DIR; each mints M receipts. The chain
    must have N*M unique receipt_ids and verify clean — the cross-process lock +
    file-tip re-read is what makes this hold."""
    share = tmp_path / "share"
    share.mkdir()
    n, m = 5, 12
    code = (
        "import os, sys; sys.path.insert(0, os.environ['PYTHONPATH'].split(os.pathsep)[0]);"
        "import kry.kry_mint as km;"
        "[km.mint('cache_hit', 50, f\"{os.environ['WID']}/{i}\", evidence=f\"{os.environ['WID']}/{i}\")"
        f" for i in range({m})]"
    )
    env = {**os.environ, "PYTHONPATH": _SRC, "KRY_DATA_DIR": str(share)}
    procs = [subprocess.Popen([sys.executable, "-c", code], env={**env, "WID": str(w)})
             for w in range(n)]
    for p in procs:
        assert p.wait() == 0

    log = share / "kry_mint_log.jsonl"
    ids = [json.loads(ln)["receipt_id"] for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(ids) == len(set(ids)) == n * m, (
        f"forked across processes: {len(ids)} lines, {len(set(ids))} unique, want {n * m}"
    )

    # Verify the chain in a clean subprocess bound to the shared dir.
    verify = (
        "import os, sys; sys.path.insert(0, os.environ['PYTHONPATH'].split(os.pathsep)[0]);"
        "import kry.kry_mint as km;"
        "ok, errs = km.verify_chain();"
        "sys.exit(0 if ok else 1)"
    )
    rc = subprocess.run([sys.executable, "-c", verify], env=env).returncode
    assert rc == 0, "chain failed verification after concurrent multi-process mint"
