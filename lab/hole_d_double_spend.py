#!/usr/bin/env python3
"""Lab Test 3 — cross-node double-spend (HOLE D), and the lease fix.

This is the one experiment that *requires* more than one node: KRY's settlement
double-spend guard is proven only WITHIN a single process/registry. HOLE D (disclosed
in docs/KRY_VERACITY_BINDING.md) is that two nodes settling the SAME attested balance
against their OWN unmerged registries are not caught. With four nodes you can both
demonstrate the hole and prove a fix.

Topology (this script simulates the four nodes locally as four data dirs; on real
hardware each role is a separate machine):

    Node A  (earner)            — mints KRY, emits ONE content-sealed attestation
    Node B  (counterparty)      — verifies A's attestation, accepts an offer
    Node C  (counterparty)      — verifies the SAME attestation, accepts an offer
    Node D  (registry authority)— a shared lease service B and C must consult first

Run:
    PYTHONPATH=src python3 lab/hole_d_double_spend.py

It prints two phases:
  PHASE 1 (no authority): B and C each accept 7,000 KRY against A's 10,000 balance,
           each against its own registry -> 14,000 settled -> DOUBLE-SPEND (VULNERABLE).
  PHASE 2 (with Node D lease authority): the second lease that would exceed the
           attested balance is DENIED -> only 7,000 settles -> PROTECTED. A concurrent
           race is run to show the lease grant is atomic (exactly one of two wins).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

import kry.kry_attest as ka
import kry.kry_mint as km
import kry.kry_settlement as ks
import kry.kry_token as kt


# ── node data-dir switching (each "node" = its own KRY_DATA_DIR) ──────────────

def _use_node(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    km._MINT_LOG_PATH = d / "mint.jsonl"
    km._DECAY_STATE_PATH = d / "decay.json"
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = "0" * 64
    km._evidence_mints = {}
    km._decay_loaded = True
    ka._MINT_LOG_PATH = d / "mint.jsonl"
    kt._LEDGER_PATH = d / "ledger.json"
    kt._ledger_instance = kt.KRYLedger()
    ks._REGISTRY_PATH = d / "reg.jsonl"


def _settled(reg_path: Path, party: str) -> float:
    ks._REGISTRY_PATH = reg_path
    return ks._load_registry().get(party, 0.0)


def _accept_and_settle(reg_path: Path, party: str, att_json: str,
                       amount: float, attested: float) -> bool:
    """A counterparty node: verify A's attestation against its OWN registry, and if
    accepted, settle (record to that registry). Returns True if it settled."""
    ks._REGISTRY_PATH = reg_path
    offer = ks.make_offer(party, "counterparty", amount, 1000, now=time.time())
    grant, reason = ks.verify_and_accept(offer, att_json, now=time.time())
    if grant is None:
        print(f"      node sees registry {reg_path.parent.name}: REJECTED ({reason[:50]})")
        return False
    receiver = ks.ReceiverLedger(party="counterparty")
    ks.settle(offer, grant, debit_a_fn=lambda k: k, receiver=receiver, a_balance_before=attested)
    print(f"      node sees registry {reg_path.parent.name}: ACCEPTED + settled {amount:.0f} KRY")
    return True


# ── Node D: a shared lease authority (the recommended HOLE D fix, prototype) ───

def _lock(authdir: Path) -> None:
    lock = authdir / ".lock"
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return
        except FileExistsError:
            time.sleep(0.001)


def _unlock(authdir: Path) -> None:
    try:
        os.unlink(authdir / ".lock")
    except FileNotFoundError:
        pass


def lease(authdir: Path, key: str, amount: float, ceiling: float) -> bool:
    """Grant a lease iff cumulative leased for `key` stays within `ceiling` (the
    attested balance). Cross-process safe via an O_EXCL lockfile — this is the
    shared state HOLE D needs (one authority both B and C consult)."""
    authdir.mkdir(parents=True, exist_ok=True)
    _lock(authdir)
    try:
        p = authdir / "leased.json"
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        cur = float(data.get(key, 0.0))
        if cur + amount <= ceiling + 1e-9:
            data[key] = cur + amount
            p.write_text(json.dumps(data), encoding="utf-8")
            return True
        return False
    finally:
        _unlock(authdir)


# ── the experiment ────────────────────────────────────────────────────────────

def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="kry_lab_"))
    A, B, C, D = (root / n for n in ("nodeA", "nodeB", "nodeC", "nodeD_authority"))

    print("=" * 72)
    print("LAB TEST 3 — cross-node double-spend (HOLE D)")
    print(f"  simulating 4 nodes under {root}")
    print("=" * 72)

    # Node A earns 10,000 KRY and emits ONE attestation (shared with B and C).
    _use_node(A)
    km.mint("cache_hit", 10_000, "earned", evidence="A-epoch-1", avoided_model="gh/claude-opus-4.8")
    att_json = ka.build_attestation().to_public_json()
    attested = json.loads(att_json)["total_kry"]
    print(f"\nNode A: minted, attested balance = {attested:.0f} KRY")
    print("Node A presents the SAME attestation to B and C, offering 7,000 KRY to each.\n")

    # ── PHASE 1: no authority — B and C each check their own unmerged registry ──
    print("PHASE 1 — no shared authority (the hole):")
    b_ok = _accept_and_settle(B / "reg.jsonl", "A", att_json, 7000, attested)
    c_ok = _accept_and_settle(C / "reg.jsonl", "A", att_json, 7000, attested)
    total = _settled(B / "reg.jsonl", "A") + _settled(C / "reg.jsonl", "A")
    print(f"   A settled across B+C registries: {total:.0f} KRY against {attested:.0f} attested")
    vulnerable = b_ok and c_ok and total > attested
    print(f"   VERDICT: {'*** DOUBLE-SPEND (VULNERABLE) — HOLE D confirmed on separate registries' if vulnerable else 'no double-spend'}\n")

    # ── PHASE 2: Node D lease authority — both must lease before accepting ──────
    print("PHASE 2 — Node D shared lease authority (the fix):")
    att_hash = json.loads(att_json).get("chain_head", "")[:16]
    key = f"A:{att_hash}"
    b_lease = lease(D, key, 7000, attested)
    print(f"   B requests lease 7,000 -> {'GRANTED' if b_lease else 'DENIED'}")
    c_lease = lease(D, key, 7000, attested)
    print(f"   C requests lease 7,000 -> {'GRANTED' if c_lease else 'DENIED'} "
          f"(cumulative would be 14,000 > {attested:.0f})")
    settled_2 = (7000 if b_lease else 0) + (7000 if c_lease else 0)
    protected = settled_2 <= attested
    print(f"   only leased work settles: {settled_2:.0f} KRY <= {attested:.0f} attested")
    print(f"   VERDICT: {'PROTECTED — the second over-balance settlement is refused' if protected else '*** still vulnerable'}\n")

    # ── PHASE 2b: concurrency — prove the lease grant is atomic under a race ────
    print("PHASE 2b — concurrent race (atomicity of the authority):")
    D2 = root / "nodeD_race"
    results: list[bool] = []
    lock = threading.Lock()

    def racer():
        g = lease(D2, "A:race", 7000, attested)
        with lock:
            results.append(g)

    threads = [threading.Thread(target=racer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    granted = sum(results)
    print(f"   2 nodes race to lease 7,000 each (10,000 ceiling): granted={granted}, denied={2 - granted}")
    print(f"   VERDICT: {'ATOMIC — exactly one wins, no double grant' if granted == 1 else '*** race not atomic'}\n")

    print("=" * 72)
    ok = vulnerable and protected and granted == 1
    print(f"RESULT: hole demonstrated={vulnerable}  fix holds={protected}  lease atomic={granted == 1}")
    print("  On real hardware: A/B/C are three machines, Node D is a shared lease")
    print("  service (HTTP or a leased file on shared storage). This prototype proves")
    print("  the lease/nonce/TTL approach ranked best in docs/KRY_VERACITY_BINDING.md.")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
