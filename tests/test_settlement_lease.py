"""HOLE D production fix — cross-node lease/nonce/TTL authority.

The registry double-spend guard is per-node: two nodes settling the same attested
balance against their own unmerged registries race through it (HOLE D corollary in
kry_settlement.py). The shared lease authority closes that real-time window. These
pin the authority's contract (grant/deny/TTL/nonce), its cross-PROCESS atomicity
(the actual HOLE D scenario), and that verify_and_accept consults it — while staying
byte-for-byte unchanged when KRY_SETTLE_LEASE_DIR is unset (default single-node).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import kry.kry_settlement as ks

_SRC = str(Path(__file__).resolve().parents[1] / "src")


# ── unit: the authority's grant/deny/TTL/nonce contract ───────────────────────

def test_acquire_lease_grants_within_ceiling(tmp_path):
    assert ks._acquire_lease("A", 7000, 10000, nonce="o1", now=100.0, authdir=tmp_path)


def test_acquire_lease_denies_over_ceiling(tmp_path):
    assert ks._acquire_lease("A", 7000, 10000, nonce="o1", now=100.0, authdir=tmp_path)
    # cumulative 7000 + 7000 = 14000 > 10000 → the second is refused.
    assert not ks._acquire_lease("A", 7000, 10000, nonce="o2", now=100.0, authdir=tmp_path)


def test_acquire_lease_is_per_party(tmp_path):
    """A different party's ceiling is independent — B's lease does not consume A's."""
    assert ks._acquire_lease("A", 9000, 10000, nonce="a1", now=100.0, authdir=tmp_path)
    assert ks._acquire_lease("B", 9000, 10000, nonce="b1", now=100.0, authdir=tmp_path)


def test_acquire_lease_nonce_idempotent(tmp_path):
    """Re-leasing the SAME offer (nonce) must not double-consume balance: a retry
    returns the prior grant, and the headroom is unchanged."""
    assert ks._acquire_lease("A", 7000, 10000, nonce="same", now=100.0, authdir=tmp_path)
    assert ks._acquire_lease("A", 7000, 10000, nonce="same", now=100.0, authdir=tmp_path)
    # Only 7000 is actually held, so a fresh 3000 still fits (7000+3000 == 10000).
    assert ks._acquire_lease("A", 3000, 10000, nonce="other", now=100.0, authdir=tmp_path)


def test_acquire_lease_ttl_frees_abandoned_hold(tmp_path):
    """An unconsummated reservation frees after TTL so balance is not locked
    forever; within TTL it still blocks an over-balance second lease."""
    assert ks._acquire_lease("A", 7000, 10000, nonce="o1", now=0.0, authdir=tmp_path)
    # Within TTL: cumulative 7000 + 7000 > 10000 → denied.
    assert not ks._acquire_lease("A", 7000, 10000, nonce="o2",
                                 now=ks._LEASE_TTL - 1, authdir=tmp_path)
    # After TTL: the first hold is pruned → the new lease fits again.
    assert ks._acquire_lease("A", 7000, 10000, nonce="o3",
                             now=ks._LEASE_TTL + 1, authdir=tmp_path)


# ── cross-process atomicity: the actual HOLE D scenario ───────────────────────

def test_acquire_lease_cross_process_atomic(tmp_path):
    """N real processes race to lease against ONE shared authority on shared
    storage (the HOLE D topology). Exactly floor(ceiling/amount) may win — a
    broken lock would over-grant. amount=3000, ceiling=10000 → exactly 3 of 5."""
    authdir = tmp_path / "authority"
    authdir.mkdir()
    code = (
        "import os, sys; sys.path.insert(0, os.environ['PYTHONPATH'].split(os.pathsep)[0]);"
        "import kry.kry_settlement as ks;"
        "g = ks._acquire_lease('A', 3000, 10000, nonce=os.environ['WID'], now=1e9,"
        " authdir=__import__('pathlib').Path(os.environ['AUTHDIR']));"
        "print('GRANTED' if g else 'DENIED')"
    )
    env = {**os.environ, "PYTHONPATH": _SRC, "AUTHDIR": str(authdir)}
    procs = [subprocess.Popen([sys.executable, "-c", code], env={**env, "WID": f"w{w}"},
                              stdout=subprocess.PIPE, text=True) for w in range(5)]
    granted = sum(p.communicate()[0].strip() == "GRANTED" for p in procs)
    for p in procs:
        assert p.returncode == 0
    assert granted == 3, f"lease over/under-granted under a real process race: {granted}"

    leased = json.loads((authdir / "kry_leases.json").read_text(encoding="utf-8"))
    assert sum(lo["amount"] for lo in leased["A"]) == 9000


# ── integration: verify_and_accept consults the authority ─────────────────────

def _mint_and_attest():
    import kry.kry_attest as ka
    import kry.kry_mint as km
    km.mint("cache_hit", 10_000, "earned", evidence="A-epoch", avoided_model="gh/claude-opus-4.8")
    att_json = ka.build_attestation().to_public_json()
    attested = json.loads(att_json)["total_kry"]
    return att_json, attested


def test_verify_and_accept_lease_denies_cross_node_double_spend(tmp_path, monkeypatch):
    """Two 'nodes' with their OWN empty registries each pass the per-node guard
    against the same attestation (the hole). With a shared lease authority the
    second over-balance accept is refused. Each node is a SEPARATE process; we
    simulate that by clearing the in-process reservation between accepts, so it is
    the shared FILE LEASE — not the in-process guard — that denies node C."""
    att_json, attested = _mint_and_attest()
    amount = attested * 0.6
    monkeypatch.setenv("KRY_SETTLE_LEASE_DIR", str(tmp_path / "authority"))
    ks._PENDING_RESERVATIONS.clear()

    # Node B: its own (empty) registry → per-node guard passes; lease reserves 0.6.
    ks._REGISTRY_PATH = tmp_path / "nodeB" / "reg.jsonl"
    offer_b = ks.make_offer("A", "B", amount, 1000, now=time.time())
    grant_b, reason_b = ks.verify_and_accept(offer_b, att_json, now=time.time())
    assert grant_b is not None, reason_b

    # Node C in a SEPARATE process: clear the in-process reservation so the shared lease
    # (0.6 + 0.6 = 1.2 × attested), not the in-process guard, is what denies it.
    ks._PENDING_RESERVATIONS.clear()
    ks._REGISTRY_PATH = tmp_path / "nodeC" / "reg.jsonl"
    offer_c = ks.make_offer("A", "C", amount, 1000, now=time.time())
    grant_c, reason_c = ks.verify_and_accept(offer_c, att_json, now=time.time())
    assert grant_c is None and "lease denied" in reason_c, reason_c


def test_verify_and_accept_closes_in_process_race(tmp_path, monkeypatch):
    """Default single-node (no KRY_SETTLE_LEASE_DIR): the in-process reservation closes
    the verify->settle TOCTOU. A's first accept reserves its balance, so a second accept
    that would push A over its attested balance is denied — even across two registry
    files (nodes) in the same process. (The CROSS-process race is what the file lease
    handles; see test_acquire_lease_cross_process_atomic.)"""
    monkeypatch.delenv("KRY_SETTLE_LEASE_DIR", raising=False)
    ks._PENDING_RESERVATIONS.clear()
    att_json, attested = _mint_and_attest()
    amount = attested * 0.6      # two of these (120%) exceed A's attested balance

    ks._REGISTRY_PATH = tmp_path / "nodeB" / "reg.jsonl"
    g_b, _ = ks.verify_and_accept(ks.make_offer("A", "B", amount, 1000, now=time.time()),
                                  att_json, now=time.time())
    ks._REGISTRY_PATH = tmp_path / "nodeC" / "reg.jsonl"
    g_c, reason_c = ks.verify_and_accept(ks.make_offer("A", "C", amount, 1000, now=time.time()),
                                         att_json, now=time.time())
    assert g_b is not None                       # first accept reserves A's balance
    assert g_c is None                           # second denied — in-process race closed
    assert "double-spend" in reason_c
    assert not (tmp_path / "authority").exists()   # no file-lease machinery touched


def test_concurrent_accepts_do_not_overspend(tmp_path, monkeypatch):
    """N threads concurrently accept offers from the same party A against one attested
    balance; the in-process reservation (serialized by _REGISTRY_LOCK) must cap grants at
    floor(balance/amount) — no overspend in the verify->settle window."""
    import threading
    monkeypatch.delenv("KRY_SETTLE_LEASE_DIR", raising=False)
    ks._PENDING_RESERVATIONS.clear()
    ks._REGISTRY_PATH = tmp_path / "reg.jsonl"
    att_json, attested = _mint_and_attest()
    amount = attested * 0.34          # at most 2 fit (3 * 0.34 = 1.02 > 1.0)
    now = time.time()
    grants, lock = [], threading.Lock()

    def worker(i):
        g, _ = ks.verify_and_accept(
            ks.make_offer("A", f"B{i}", amount, 1000, now=now + i * 1e-6), att_json, now=now)
        if g is not None:
            with lock:
                grants.append(g)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(grants) == 2, f"expected 2 grants (no overspend), got {len(grants)}"


# ── SETTLE-1: offer_id is spender-settable — idempotency must key on the CANONICAL content identity ──

def test_lease_nonce_collision_cannot_double_spend(tmp_path, monkeypatch):
    """A spender bypasses make_offer and reuses ONE offer_id across two offers to DIFFERENT recipients.
    Before the fix the cross-node lease took the second as an idempotent replay (same nonce) and bypassed
    the ceiling — ~120% of the attested balance settled across two nodes. The lease nonce is now the
    offer's canonical content identity, so node C's over-balance accept is denied."""
    att_json, attested = _mint_and_attest()
    amount = attested * 0.6
    monkeypatch.setenv("KRY_SETTLE_LEASE_DIR", str(tmp_path / "authority"))
    ks._PENDING_RESERVATIONS.clear()
    now = time.time()
    offer_b = ks.SettlementOffer(offer_id="COLLIDE", from_party="A", to_party="B",
                                 kry_amount=amount, routing_tokens=1000, ts=now)
    offer_c = ks.SettlementOffer(offer_id="COLLIDE", from_party="A", to_party="C",
                                 kry_amount=amount, routing_tokens=1000, ts=now)
    ks._REGISTRY_PATH = tmp_path / "nodeB" / "reg.jsonl"
    grant_b, reason_b = ks.verify_and_accept(offer_b, att_json, now=now)
    assert grant_b is not None, reason_b
    ks._PENDING_RESERVATIONS.clear()                     # node C runs in a separate process
    ks._REGISTRY_PATH = tmp_path / "nodeC" / "reg.jsonl"
    grant_c, reason_c = ks.verify_and_accept(offer_c, att_json, now=now)
    assert grant_c is None and "lease denied" in reason_c, reason_c


def test_in_process_offer_id_collision_cannot_double_spend(tmp_path, monkeypatch):
    """The in-process verify->settle reservation ALSO keyed idempotency on offer_id. Two offers sharing
    an offer_id but different recipients, in the in-flight window, must both count against the ceiling
    (canonical identity), so the second is denied — even with no file lease configured."""
    monkeypatch.delenv("KRY_SETTLE_LEASE_DIR", raising=False)
    ks._PENDING_RESERVATIONS.clear()
    att_json, attested = _mint_and_attest()
    amount = attested * 0.6
    now = time.time()
    offer_b = ks.SettlementOffer(offer_id="COLLIDE", from_party="A", to_party="B",
                                 kry_amount=amount, routing_tokens=1000, ts=now)
    offer_c = ks.SettlementOffer(offer_id="COLLIDE", from_party="A", to_party="C",
                                 kry_amount=amount, routing_tokens=1000, ts=now)
    ks._REGISTRY_PATH = tmp_path / "nodeB" / "reg.jsonl"
    g_b, _ = ks.verify_and_accept(offer_b, att_json, now=now)
    ks._REGISTRY_PATH = tmp_path / "nodeC" / "reg.jsonl"     # different node, SAME process (in-flight)
    g_c, reason_c = ks.verify_and_accept(offer_c, att_json, now=now)
    assert g_b is not None
    assert g_c is None and "double-spend" in reason_c, reason_c


def test_settlement_guard_opt_in(monkeypatch):
    """SANC-1: a registered settlement guard refuses by policy; default (None) leaves behaviour unchanged."""
    monkeypatch.delenv("KRY_SETTLE_LEASE_DIR", raising=False)
    ks._PENDING_RESERVATIONS.clear()
    att_json, attested = _mint_and_attest()
    now = time.time()
    offer = ks.make_offer("A", "B", attested * 0.5, 1000, now=now)
    g_ok, _ = ks.verify_and_accept(offer, att_json, now=now)          # default: no guard -> accepted
    assert g_ok is not None
    ks._PENDING_RESERVATIONS.clear()
    ks.set_settlement_guard(lambda offer, att: "counterparty below reputation floor")  # opt in a refusing guard
    try:
        g_no, reason = ks.verify_and_accept(offer, att_json, now=now)
        assert g_no is None and "policy guard" in reason, reason
    finally:
        ks.set_settlement_guard(None)   # restore default OFF so the module global never leaks
