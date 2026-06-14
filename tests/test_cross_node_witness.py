"""Cross-node tip-witness (lab/node.py) — the OTHER lab nodes as the external anchor.

A single self-attesting node can't detect its OWN rollback or version-downgrade (it controls every
byte). An independent node that once WITNESSED a higher/v4 tip can: the witnessed link (at seq=count)
must still appear in the attestation with the same chain_hash. This closes the residual that the v4
chain-binding + monotonic check cannot — the full all-legacy rebuild — using a node the operator
does not control (within the lab, the other nodes; with a real counterparty, them).
"""
from __future__ import annotations

import importlib.util
import json
import pathlib


def _node():
    spec = importlib.util.spec_from_file_location(
        "labnode", pathlib.Path(__file__).resolve().parents[1] / "lab" / "node.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _att(receipts, links):
    return {"receipts": receipts, "chain_head": links[-1]["chain_hash"] if links else "0" * 64,
            "links": links}


def test_witness_is_consistent_with_the_honest_attestation(tmp_path):
    node = _node()
    share = tmp_path / "share"
    (share / "witness").mkdir(parents=True)
    honest = _att(2, [{"seq": 1, "chain_hash": "TIP1"}, {"seq": 2, "chain_hash": "TIP2"}])
    (share / "witness" / "nodeB.json").write_text(json.dumps({"count": 2, "tip": "TIP2"}), encoding="utf-8")
    assert node._witness_violations(honest, share) == []


def test_witness_catches_rollback(tmp_path):
    node = _node()
    share = tmp_path / "share"
    (share / "witness").mkdir(parents=True)
    (share / "witness" / "nodeB.json").write_text(json.dumps({"count": 2, "tip": "TIP2"}), encoding="utf-8")
    rolled = _att(1, [{"seq": 1, "chain_hash": "TIP1"}])          # one receipt dropped
    v = node._witness_violations(rolled, share)
    assert any("ROLLBACK" in x for x in v), v


def test_witness_catches_downgrade_or_tamper(tmp_path):
    node = _node()
    share = tmp_path / "share"
    (share / "witness").mkdir(parents=True)
    (share / "witness" / "nodeB.json").write_text(json.dumps({"count": 2, "tip": "TIP2"}), encoding="utf-8")
    # same length, but seq-2 tip differs (re-stamped as legacy / tampered) — the full-rebuild case the
    # self-contained verifier cannot catch
    forged = _att(2, [{"seq": 1, "chain_hash": "TIP1"}, {"seq": 2, "chain_hash": "FORGED"}])
    v = node._witness_violations(forged, share)
    assert any("TAMPER/DOWNGRADE" in x for x in v), v


def test_no_witness_files_means_no_violation(tmp_path):
    node = _node()
    share = tmp_path / "share"
    share.mkdir()
    honest = _att(1, [{"seq": 1, "chain_hash": "TIP1"}])
    assert node._witness_violations(honest, share) == []
