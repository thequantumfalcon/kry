"""Stress + property tests on synthetic-at-scale data (examples/gen_dataset.py).

Beyond "it didn't crash": these assert the holdout estimator RECOVERS the embedded
ground-truth paid-rate, that scale invariants hold, that the full mint->attest->
stranger-verify loop survives generated data, and that malformed records can never
crash the report or produce a negative saving.
"""
from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path

import pytest

from kry.kry_baseline import wilson_interval

_ROOT = Path(__file__).resolve().parents[1]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gen():
    return _load(_ROOT / "examples" / "gen_dataset.py", "gen_dataset")


@pytest.fixture
def sr():
    return _load(_ROOT / "scripts" / "kry_savings_report.py", "kry_savings_report")


def test_scale_invariants_hold(gen, sr):
    """20k records: savings non-negative, floor in [0,1], by_kind partitions records."""
    records = gen.generate(20_000, holdout_rate=0.08, seed=3)
    rep = sr.analyze(records)
    assert rep["records"] == 20_000
    assert rep["saved_kry"] >= 0.0
    assert 0.0 <= rep["veracity"]["veracity_floor"] <= 1.0
    assert sum(rep["by_kind"].values()) == rep["records"]
    # the three savings tiers sum to total saved (no leakage)
    v = rep["veracity"]
    tier_sum = v["self_reported_kry"] + v["holdout_validated_kry"] + v["provider_metered_kry"]
    assert abs(tier_sum - rep["saved_kry"]) < 1.0


def test_holdout_recovers_ground_truth(gen, sr):
    """The estimator is CORRECT: for every class with enough holdout, the 95% Wilson
    CI contains the embedded true paid-rate. This is the whole claim — the holdout
    measures the counterfactual — checked against known truth."""
    records = gen.generate(40_000, holdout_rate=0.1, seed=1)
    rep = sr.analyze(records)
    truth = gen.ground_truth()
    measured = [(c, b) for c, b in rep["by_class"].items() if b["holdout_n"] >= 30]
    assert len(measured) >= 4                       # most classes got measured at this scale
    covered = 0
    for cls, b in measured:
        lo, hi = wilson_interval(b["holdout_paid_n"], b["holdout_n"])
        if lo - 1e-9 <= truth[cls] <= hi + 1e-9:
            covered += 1
    # 95% CIs over a handful of classes: allow at most one to miss (it is a CI).
    assert covered >= len(measured) - 1


def test_generated_data_mint_attest_verify(gen, sr, tmp_path, monkeypatch):
    """The full operator loop survives generated data and a stranger validates it."""
    import kry.kry_attest as ka
    import kry.kry_mint as km
    import kry.kry_token as kt
    log = tmp_path / "mint.jsonl"
    monkeypatch.setattr(km, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(ka, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(kt, "_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(km, "_DECAY_STATE_PATH", tmp_path / "decay.json")
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = "0" * 64
    km._evidence_mints = {}
    km._decay_loaded = True
    kt._ledger_instance = kt.KRYLedger()

    records = gen.generate(400, holdout_rate=0.2, seed=5)
    att_path = tmp_path / "att.json"
    sr._mint_and_attest(records, str(att_path))

    att = json.loads(att_path.read_text(encoding="utf-8"))
    v = _load(_ROOT / "scripts" / "kry_verify.py", "kry_verify_standalone")
    ok, errs = v.verify_attestation(att)
    assert ok, errs                                  # integrity + conservation + magnitude
    # conservation: declared total equals the sum of links
    assert abs(sum(lk["kry_minted"] for lk in att["links"]) - att["total_kry"]) < 0.01


def test_fuzz_malformed_records_never_crash(sr):
    """Random malformed records mixed with valid ones: never raise, never negative."""
    rnd = random.Random(11)
    junk_tokens = [-5, 0, "x", None, float("inf"), float("nan"), 10 ** 15, 3.5]
    models = ["gh/claude-opus-4.8", "google/gemini", "or/deepseek/deepseek-v4-pro", "", None]
    for _ in range(50):
        recs = []
        for j in range(rnd.randint(0, 40)):
            recs.append({
                "id": f"f{j}",
                "model": rnd.choice(models),
                "avoided_model": rnd.choice(models),
                "cache_hit": rnd.random() < 0.5,
                "holdout": rnd.random() < 0.2,
                "request_class": rnd.choice(["a", "b", "", None]),
                "usage": {"completion_tokens": rnd.choice(junk_tokens),
                          "prompt_tokens": rnd.choice(junk_tokens)},
            })
        rep = sr.analyze(recs)                       # must not raise
        assert rep["saved_kry"] >= 0.0
        assert 0.0 <= rep["veracity"]["veracity_floor"] <= 1.0
