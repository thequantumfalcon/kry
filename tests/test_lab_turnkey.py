"""CI coverage for the turnkey lab pieces: corpus generator, the frontier-compare judge,
the cross-machine node lease, and the concurrency driver core."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "lab" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_make_prompts_shape_and_repeats():
    mp = _load("make_prompts")
    rows = mp.make(400, ["summarize", "code"], pool=10, seed=1)
    assert len(rows) == 400
    assert {r["request_class"] for r in rows} == {"summarize", "code"}
    # pool=10 -> at most 10 distinct prompts per class -> cache hits guaranteed
    assert len({r["prompt"] for r in rows if r["request_class"] == "code"}) <= 10
    assert mp.min_n_for_holdout(4, 0.02) == 6000


def test_frontier_compare_judge_flags_divergence():
    rt = _load("router")
    cfg = json.loads((_ROOT / "lab" / "routes.example.json").read_text(encoding="utf-8"))

    # identical answers -> overlap 1.0 -> NOT paid (cheap was good enough)
    def same(url, model, prompt):
        return "the answer is x", 1, 1
    assert rt.frontier_compare_judge("code", "q", same, cfg, None) is False

    # divergent answers -> low overlap -> hit_paid (needed the frontier)
    def diff(url, model, prompt):
        return ("cheap weak reply" if "11434" in url and model != cfg["frontier_model"]
                else "completely different frontier text entirely"), 1, 1
    # route node url vs frontier node url differ in the example config, so responses differ
    assert rt.frontier_compare_judge("code", "q", diff, cfg, None) is True


def test_concurrency_no_lost_updates(tmp_path):
    cc = _load("concurrency_check")
    res = cc.run(workers=4, earns=50, share=str(tmp_path / "share"))
    assert res["pass"] is True            # 4 x 50 x 100 == total_earned, nothing lost
    assert res["actual_total_earned"] == res["expected_total_earned"]


def test_node_earner_then_lease_blocks_second_accept(tmp_path):
    node = _load("node")
    share = tmp_path / "share"
    node.earner(share, tmp_path / "kryA", amount=10000)
    assert (share / "attestation.json").exists()
    # two counterparties, each its own registry, WITH the lease: only the first 7000 fits
    node.accept(share, tmp_path / "kryB", "A", 7000, use_lease=True)
    node.accept(share, tmp_path / "kryC", "A", 7000, use_lease=True)
    # The HOLE D lease lives in the SHIPPING settlement path (kry_settlement._acquire_lease),
    # which writes authdir/kry_leases.json as {party: [{"amount","ts","nonce"}, ...]}.
    leased = json.loads((share / "leases" / "kry_leases.json").read_text(encoding="utf-8"))
    total_leased = sum(float(lo["amount"]) for holds in leased.values() for lo in holds)
    assert total_leased <= 10000 + 1e-9      # the lease ceiling held across nodes
    assert total_leased == 7000              # only the first offer fit; the second was refused
