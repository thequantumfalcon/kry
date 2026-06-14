#!/usr/bin/env python3
"""Lab node entrypoint — run the cross-machine roles over your shared folder (SHARE).

Wraps Tests 3 (HOLE D) and 4 (stranger verify) for REAL nodes. SHARE is a folder all
nodes mount (NFS/SMB); it holds attestation.json and the lease dir. Each node keeps its
OWN ledger/registry in --kry-dir (so registries are genuinely unmerged — the HOLE D
condition). The lease (O_EXCL on the shared FS) is the recommended fix. Pure stdlib.

Real run (4 machines):
    # Node A (earner):
    python lab/node.py earner   --share /mnt/share --kry-dir ~/kryA
    # Node B and Node C (counterparties), WITHOUT the lease -> double-spend shows:
    python lab/node.py accept   --share /mnt/share --kry-dir ~/kryB --party A --offer 7000
    python lab/node.py accept   --share /mnt/share --kry-dir ~/kryC --party A --offer 7000
    # ...now WITH the lease (Node D's lease dir lives in SHARE) -> second is refused:
    python lab/node.py accept   --share /mnt/share --kry-dir ~/kryB --party A --offer 7000 --use-lease
    python lab/node.py accept   --share /mnt/share --kry-dir ~/kryC --party A --offer 7000 --use-lease
    # Any node (stranger verify, stdlib only):
    python lab/node.py verify   --share /mnt/share
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import kry.kry_attest as ka  # noqa: E402
import kry.kry_mint as km  # noqa: E402
import kry.kry_settlement as ks  # noqa: E402
import kry.kry_token as kt  # noqa: E402

def _bind(kry_dir: Path) -> None:
    kry_dir.mkdir(parents=True, exist_ok=True)
    km._MINT_LOG_PATH = kry_dir / "mint.jsonl"
    km._DECAY_STATE_PATH = kry_dir / "decay.json"
    ka._MINT_LOG_PATH = kry_dir / "mint.jsonl"
    kt._LEDGER_PATH = kry_dir / "ledger.json"
    kt._ledger_instance = kt.KRYLedger()
    ks._REGISTRY_PATH = kry_dir / "reg.jsonl"


def earner(share: Path, kry_dir: Path, amount: float) -> int:
    share.mkdir(parents=True, exist_ok=True)
    _bind(kry_dir)
    km.mint("cache_hit", amount, "lab-earn", evidence=f"earn-{time.time()}",
            avoided_model="gh/claude-opus-4.8")
    att = ka.build_attestation()
    (share / "attestation.json").write_text(att.to_public_json(), encoding="utf-8")
    print(f"earner: minted, attested {att.total_kry:.0f} KRY -> {share/'attestation.json'}")
    return 0


def accept(share: Path, kry_dir: Path, party: str, offer: float, use_lease: bool) -> int:
    _bind(kry_dir)
    att_json = (share / "attestation.json").read_text(encoding="utf-8")
    attested = json.loads(att_json).get("total_kry", 0.0)
    # The lease (HOLE D fix) lives INSIDE kry_settlement.verify_and_accept: point it
    # at the shared lease dir so every node reserves against one ceiling. This
    # exercises the real production path, not a lab-only prototype — so Test 3
    # validates what actually ships.
    if use_lease:
        os.environ["KRY_SETTLE_LEASE_DIR"] = str(share / "leases")
    else:
        os.environ.pop("KRY_SETTLE_LEASE_DIR", None)
    offer_obj = ks.make_offer(party, kry_dir.name, offer, 1000, now=time.time())
    grant, reason = ks.verify_and_accept(offer_obj, att_json, now=time.time())
    if grant is None:
        print(f"accept[{kry_dir.name}]: REJECTED ({reason[:80]})")
        return 0
    receiver = ks.ReceiverLedger(party=kry_dir.name)
    ks.settle(offer_obj, grant, debit_a_fn=lambda k: k, receiver=receiver, a_balance_before=attested)
    print(f"accept[{kry_dir.name}]: ACCEPTED + settled {offer:.0f} KRY against its own registry")
    return 0


def _witness_violations(att: dict, share: Path) -> list[str]:
    """Cross-node anchor check: each witness file under SHARE/witness records a {count, tip} it once
    saw for this chain. A self-contained attestation can't detect its OWN rollback or version-downgrade
    (the operator controls every byte) — but an independent node that WITNESSED a higher/v4 tip can.
    The witnessed link (at seq=count) must still appear in the attestation with the same chain_hash;
    a shorter chain = rollback, a different tip at that seq = tamper/downgrade."""
    wdir = share / "witness"
    if not wdir.exists():
        return []
    links = att.get("links", [])
    by_seq = {lk.get("seq"): lk for lk in links if isinstance(lk, dict)}
    violations: list[str] = []
    for wf in sorted(wdir.glob("*.json")):
        try:
            w = json.loads(wf.read_text(encoding="utf-8"))
            wc, wt = int(w["count"]), str(w["tip"])
        except Exception:
            continue
        if wc <= 0:
            continue
        lk = by_seq.get(wc)
        if lk is None:
            violations.append(f"{wf.stem}: witnessed count {wc} but attestation has only "
                              f"{len(links)} receipts — ROLLBACK")
        elif lk.get("chain_hash") != wt:
            violations.append(f"{wf.stem}: witnessed tip at seq {wc} differs from attestation — "
                              f"TAMPER/DOWNGRADE")
    return violations


def witness(share: Path, witness_id: str) -> int:
    """Independently record the tip of the currently-published attestation. Run this on every OTHER
    node after the earner publishes — those records become the external anchor that catches a later
    rollback/downgrade (which a single self-attesting node cannot catch for itself)."""
    att = json.loads((share / "attestation.json").read_text(encoding="utf-8"))
    wdir = share / "witness"
    wdir.mkdir(parents=True, exist_ok=True)
    rec = {"witness_id": witness_id, "count": att.get("receipts", 0),
           "tip": att.get("chain_head", ""), "ts": time.time()}
    (wdir / f"{witness_id}.json").write_text(json.dumps(rec), encoding="utf-8")
    print(f"witness[{witness_id}]: recorded count={rec['count']} tip={str(rec['tip'])[:16]}…")
    return 0


def verify(share: Path) -> int:
    import subprocess
    verifier = Path(__file__).resolve().parents[1] / "scripts" / "kry_verify.py"
    rc = subprocess.call([sys.executable, str(verifier), str(share / "attestation.json")])
    # Cross-node witness check: catches a rollback/downgrade the self-contained attestation cannot.
    try:
        att = json.loads((share / "attestation.json").read_text(encoding="utf-8"))
    except Exception:
        return rc
    violations = _witness_violations(att, share)
    if violations:
        print("WITNESS-VIOLATION (a cross-node anchor caught a forgery the self-attestation could not):")
        for v in violations:
            print(f"  - {v}")
        return 1
    wdir = share / "witness"
    n = len(list(wdir.glob("*.json"))) if wdir.exists() else 0
    if n:
        print(f"WITNESS-CONSISTENT: attestation agrees with {n} cross-node tip witness(es).")
    return rc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="KRY lab node role (cross-machine over SHARE)")
    p.add_argument("role", choices=["earner", "accept", "verify", "witness"])
    p.add_argument("--share", required=True)
    p.add_argument("--kry-dir", default="kry_data")
    p.add_argument("--party", default="A")
    p.add_argument("--offer", type=float, default=7000)
    p.add_argument("--amount", type=float, default=10000, help="earner: KRY to mint")
    p.add_argument("--use-lease", action="store_true")
    p.add_argument("--witness-id", default="witness", help="witness: this node's id")
    args = p.parse_args(argv)
    share = Path(args.share)
    share.mkdir(parents=True, exist_ok=True)
    if args.role == "earner":
        return earner(share, Path(args.kry_dir), args.amount)
    if args.role == "accept":
        return accept(share, Path(args.kry_dir), args.party, args.offer, args.use_lease)
    if args.role == "witness":
        return witness(share, args.witness_id)
    return verify(share)


if __name__ == "__main__":
    sys.exit(main())
