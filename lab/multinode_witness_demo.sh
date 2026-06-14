#!/usr/bin/env bash
# Full 4-node lab on ONE machine (separate per-node dirs + a local SHARE), using the real node.py code.
# Demonstrates, end to end:
#   - cross-node stranger verify (stdlib) + WITNESS-CONSISTENT
#   - HOLE D: double-spend WITHOUT the lease, refused WITH the lease (the production settlement path)
#   - the cross-node TIP-WITNESS catching a rollback the self-contained verifier cannot (it is
#     internally valid) — the external anchor that closes the v4 full-rebuild / rollback ceiling.
# On the real cluster, replace each python3 call with the same command on nodes A/B/C/D over your
# shared mount (see lab/PLAYBOOK.md Phases 4-5). Expected final exit: nonzero (the witness fires).
set -u
cd "$(dirname "$0")/.."
ROOT="$(mktemp -d)"; SHARE="$ROOT/share"

echo "== Node A earns (receipt 1), publishes; save its valid 1-receipt state =="
python3 lab/node.py earner --share "$SHARE" --kry-dir "$ROOT/kryA" --amount 5000
cp "$SHARE/attestation.json" "$ROOT/att_v1.json"
echo "== Node A earns again (receipt 2), republishes =="
python3 lab/node.py earner --share "$SHARE" --kry-dir "$ROOT/kryA" --amount 6000

echo "== Nodes B, C, D each INDEPENDENTLY witness A's current tip =="
python3 lab/node.py witness --share "$SHARE" --witness-id nodeB
python3 lab/node.py witness --share "$SHARE" --witness-id nodeC
python3 lab/node.py witness --share "$SHARE" --witness-id nodeD

echo "== Stranger verify (stdlib) + cross-node witness =="
python3 lab/node.py verify --share "$SHARE"

echo "== HOLE D, NO lease: B and C both settle against A (double-spend) =="
python3 lab/node.py accept --share "$SHARE" --kry-dir "$ROOT/kryB" --party A --offer 7000
python3 lab/node.py accept --share "$SHARE" --kry-dir "$ROOT/kryC" --party A --offer 7000
echo "== HOLE D, WITH lease: the second accept is refused =="
python3 lab/node.py accept --share "$SHARE" --kry-dir "$ROOT/kryB2" --party A --offer 7000 --use-lease
python3 lab/node.py accept --share "$SHARE" --kry-dir "$ROOT/kryC2" --party A --offer 7000 --use-lease

echo "== A ROLLS BACK: republishes its earlier (internally-VALID) 1-receipt attestation =="
cp "$ROOT/att_v1.json" "$SHARE/attestation.json"
echo "== VERIFY: stdlib PASSES the valid rollback; the WITNESS catches it (nonzero exit) =="
python3 lab/node.py verify --share "$SHARE"
