"""kry_savings_report — turn a real routing log into a verifiable savings statement.

Pins the operator-tool behavior end to end: normalization of common shapes, the
SAVED/SPEND/veracity math, conservative holdout valuation, the honest free-tier zero,
and the full mint -> attest -> stranger-verifies loop.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SR = _ROOT / "scripts" / "kry_savings_report.py"
_VERIFIER = _ROOT / "scripts" / "kry_verify.py"
_SAMPLE = _ROOT / "examples" / "sample_usage_log.jsonl"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sr():
    return _load(_SR, "kry_savings_report")


def test_normalize_accepts_cache_hit_without_model(sr):
    """A cache hit may carry only avoided_model (no call was made) — still usable."""
    n = sr.normalize({"id": "x", "cache_hit": True,
                      "avoided_model": "gh/claude-opus-4.8",
                      "usage": {"completion_tokens": 500}})
    assert n is not None
    assert n["cache_hit"] and n["avoided_model"] == "gh/claude-opus-4.8"
    assert n["completion"] == 500
    # a record with neither model nor avoided_model is unusable
    assert sr.normalize({"id": "y", "usage": {"completion_tokens": 1}}) is None


def test_analyze_sample_log(sr):
    records = [json.loads(ln) for ln in _SAMPLE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rep = sr.analyze(records)

    assert rep["by_kind"]["cache_hit"] == 10
    assert rep["by_kind"]["holdout"] == 35
    assert rep["by_kind"]["displacement"] == 1
    assert rep["by_kind"]["paid_call"] == 2
    assert rep["saved_kry"] > 0 and rep["spend_kry"] > 0
    assert 0.0 < rep["veracity"]["veracity_floor"] < 1.0   # honest mix, not 0, not 1

    cls = rep["by_class"]
    # summarize: measured -> holdout_validated, valued at CI lower bound (< full)
    assert cls["summarize"]["tier"] == "holdout_validated"
    assert 0.0 < cls["summarize"]["ci_lo"] < 1.0
    full_summarize = 5 * 500 * 1.0 * 1.0           # 5 hits x 500 tok x rate1 x opus-mult1
    assert cls["summarize"]["saved_kry"] < full_summarize
    # translate: no holdout -> self_reported at full value (3 x 300 x 1 x 1)
    assert cls["translate"]["tier"] == "self_reported"
    assert abs(cls["translate"]["saved_kry"] - 900.0) < 1e-6
    # greet: caching a FREE-tier call saved nothing (honest zero)
    assert cls["greet"]["saved_kry"] == 0.0
    # displacement class is labeled by its real anchored tier
    assert cls["code"]["tier"] == "provider_metered"

    # holdout has a measured, non-zero price of veracity
    assert rep["holdout"]["classes_measured"] == 1
    assert rep["holdout"]["measurement_cost_kry"] > 0


def test_hostile_tokens_do_not_poison_or_crash(sr):
    """Negative, NaN, inf, string, or missing token counts must clamp to 0 — never
    a negative saving, never an exception (one bad record can't poison the report)."""
    hostile = [
        {"id": "neg", "cache_hit": True, "avoided_model": "gh/claude-opus-4.8",
         "usage": {"completion_tokens": -1_000_000}},
        {"id": "inf", "cache_hit": True, "avoided_model": "gh/claude-opus-4.8",
         "usage": {"completion_tokens": float("inf")}},
        {"id": "nan", "cache_hit": True, "avoided_model": "gh/claude-opus-4.8",
         "usage": {"completion_tokens": float("nan")}},
        {"id": "str", "cache_hit": True, "avoided_model": "gh/claude-opus-4.8",
         "usage": {"completion_tokens": "abc"}},
    ]
    rep = sr.analyze(hostile)        # must not raise
    assert rep["saved_kry"] == 0.0   # all clamped to 0, no negative savings
    assert 0.0 <= rep["veracity"]["veracity_floor"] <= 1.0


def test_usage_log_parser_rejects_nonstandard_json_constants(sr, tmp_path):
    array_log = tmp_path / "usage-array.json"
    array_log.write_text('[{"id":"bad","model":"gh/claude-opus-4.8","usage":{"completion_tokens":NaN}}]\n', encoding="utf-8")
    jsonl_log = tmp_path / "usage.jsonl"
    jsonl_log.write_text('{"id":"bad","model":"gh/claude-opus-4.8","usage":{"completion_tokens":Infinity}}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="non-standard JSON constant rejected: NaN"):
        sr._load_records(str(array_log))
    with pytest.raises(ValueError, match="non-standard JSON constant rejected: Infinity"):
        sr._load_records(str(jsonl_log))
    with pytest.raises(ValueError, match="Out of range float values"):
        sr._json_dumps({"bad": float("nan")})


def test_thin_holdout_does_not_earn_validation(sr):
    """A handful of (possibly fabricated) holdout records must NOT buy the
    holdout_validated trust label — below MIN_HOLDOUT_N it falls back to
    self_reported (floor contribution 0). The Wilson CI guards magnitude; this
    guards the label."""
    recs = [{"id": f"h{i}", "request_class": "x", "holdout": True,
             "model": "gh/claude-opus-4.8", "usage": {"completion_tokens": 500}}
            for i in range(2)]
    recs += [{"id": f"c{i}", "request_class": "x", "cache_hit": True,
              "avoided_model": "gh/claude-opus-4.8", "usage": {"completion_tokens": 500}}
             for i in range(1000)]
    rep = sr.analyze(recs)
    assert rep["by_class"]["x"]["tier"] == "self_reported"
    assert rep["veracity"]["veracity_floor"] == 0.0


def test_thin_holdout_mint_does_not_attest_validation(sr, tmp_path, monkeypatch):
    """The write path must match analyze(): below MIN_HOLDOUT_N, --mint cannot
    turn a thin holdout into an anchored public attestation."""
    import kry.kry_mint as km
    import kry.kry_token as kt
    import kry.kry_attest as ka
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

    recs = [{"id": f"h{i}", "request_class": "x", "holdout": True,
             "model": "gh/claude-opus-4.8", "usage": {"completion_tokens": 500}}
            for i in range(2)]
    recs += [{"id": f"c{i}", "request_class": "x", "cache_hit": True,
              "avoided_model": "gh/claude-opus-4.8", "usage": {"completion_tokens": 500}}
             for i in range(5)]
    report = sr.analyze(recs)
    assert report["by_class"]["x"]["tier"] == "self_reported"
    assert report["veracity"]["veracity_floor"] == 0.0

    att_path = tmp_path / "att.json"
    sr._mint_and_attest(recs, str(att_path))
    att = json.loads(att_path.read_text(encoding="utf-8"))
    assert att["veracity"]["veracity_floor"] == 0.0
    assert att["veracity"]["by_tier"] == {"self_reported": 2500.0}


def test_analyze_is_read_only(sr, tmp_path, monkeypatch):
    """The report must not mutate any persisted KRY state (no mint, no baseline write)."""
    monkeypatch.setenv("KRY_DATA_DIR", str(tmp_path))
    records = [json.loads(ln) for ln in _SAMPLE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    sr.analyze(records)
    # analyze() writes nothing — the data dir stays empty of ledgers/logs.
    assert not any(tmp_path.iterdir())


def test_mint_and_attest_produces_verifiable_attestation(sr, tmp_path, monkeypatch):
    import kry.kry_mint as km
    import kry.kry_token as kt
    import kry.kry_attest as ka
    recon = _load(_ROOT / "scripts" / "kry_reconcile.py", "kry_reconcile_standalone")
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

    records = [json.loads(ln) for ln in _SAMPLE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    att_path = tmp_path / "att.json"
    out = sr._mint_and_attest(records, str(att_path))
    assert out == str(att_path)

    att = json.loads(att_path.read_text(encoding="utf-8"))
    v = _load(_VERIFIER, "kry_verify_standalone")
    ok, errs = v.verify_attestation(att)
    assert ok, errs                                   # a stranger validates it
    assert att["veracity"]["veracity_floor"] > 0      # holdout + displacement anchored

    t1 = recon.load_t1_receipts(str(log))
    assert len(t1) == 1
    assert t1[0]["metered_tokens"] == [2000, 400]


def test_cli_mint_hint_uses_python3(sr, tmp_path, monkeypatch, capsys):
    import kry.kry_mint as km
    import kry.kry_token as kt
    import kry.kry_attest as ka
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
    att_path = tmp_path / "att.json"

    assert sr.main([str(_SAMPLE), "--mint", "--attest", str(att_path)]) == 0

    err = capsys.readouterr().err
    assert "verify: python3 scripts/kry_verify.py" in err
    assert "verify: python scripts/kry_verify.py" not in err
