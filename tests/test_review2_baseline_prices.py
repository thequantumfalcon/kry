"""Persisted holdout state and pending configuration must fail closed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from kry import kry_baseline as kb
from kry import kry_token as kt


def _state(**overrides):
    return {"class": {"holdout_n": 10, "holdout_paid_n": 8, "treated_n": 100, **overrides}}


def _write(raw):
    kb._BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    kb._BASELINE_PATH.write_text(raw)


@pytest.mark.parametrize("raw", ["{broken", "[]", "null", '{"class": null}',
                                '{"class": {}}', '{"class": {"holdout_n": NaN}}'])
def test_corrupt_baseline_blocks_repeated_reads_and_writes_without_losing_bytes(raw):
    _write(raw)
    original = kb._BASELINE_PATH.read_bytes()
    for operation in (lambda: kb.holdout_adjusted_tokens("class", 1000),
                      lambda: kb.observe_holdout("class", True),
                      lambda: kb.observe_treated("class", 1), kb.holdout_report):
        with pytest.raises(RuntimeError, match="baseline"):
            operation()
        assert kb._BASELINE_PATH.read_bytes() == original


@pytest.mark.parametrize("field,value", [
    ("holdout_n", -1), ("holdout_n", True), ("holdout_n", 10.0),
    ("holdout_n", "10"), ("holdout_n", 2**53),
    ("holdout_paid_n", 11), ("holdout_paid_n", -1), ("holdout_paid_n", False),
    ("holdout_paid_n", 8.0), ("treated_n", -1), ("treated_n", 1.5),
    ("treated_n", True), ("updated", -1), ("updated", "now"),
])
def test_invalid_stored_counts_do_not_produce_estimates(field, value):
    _write(json.dumps(_state(**{field: value})))
    with pytest.raises(RuntimeError, match="baseline"):
        kb.avoidance_estimate("class")


def test_corruption_cannot_replace_long_history_with_one_paid_observation():
    _write(json.dumps(_state(holdout_n=1000, holdout_paid_n=50)))
    assert kb.holdout_adjusted_tokens("class", 1000) <= 38.2
    _write("{interrupted")
    with pytest.raises(RuntimeError, match="baseline"):
        kb.observe_holdout("class", True)
    with pytest.raises(RuntimeError, match="baseline"):
        kb.holdout_adjusted_tokens("class", 1000)


def test_conservative_lower_bound_rounds_down():
    _write(json.dumps(_state()))
    lo, _ = kb.wilson_interval(8, 10)
    estimate = kb.avoidance_estimate("class")
    assert estimate["ci_lo"] == 0.4901
    assert estimate["ci_lo"] <= lo
    assert kb.holdout_adjusted_tokens("class", 1000) <= lo * 1000


def test_baseline_write_flushes_and_syncs_before_replace(monkeypatch):
    seen = []
    original_fsync, original_replace = os.fsync, os.replace

    def fsync(fd):
        assert os.fstat(fd).st_size > 0
        seen.append("fsync")
        original_fsync(fd)

    def replace(src, dst):
        assert seen == ["fsync"]
        seen.append("replace")
        original_replace(src, dst)

    monkeypatch.setattr(kb.os, "fsync", fsync)
    monkeypatch.setattr(kb.os, "replace", replace)
    kb.observe_holdout("class", True)
    assert seen == ["fsync", "replace"]


def test_failed_baseline_persistence_keeps_old_bytes_and_reports_failure(monkeypatch):
    _write(json.dumps(_state()))
    before = kb._BASELINE_PATH.read_bytes()

    def fail_fsync(fd):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(kb.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="synthetic disk failure"):
        kb.observe_holdout("class", True)
    assert kb._BASELINE_PATH.read_bytes() == before
    assert not list(kb._BASELINE_PATH.parent.glob(".baseline_*"))


@pytest.mark.parametrize("value", [True, 1.5, -1, 2**53])
def test_treated_count_requires_a_bounded_integer_before_writing(value):
    with pytest.raises(ValueError):
        kb.observe_treated("class", value)
    assert not kb._BASELINE_PATH.exists()


@pytest.mark.parametrize("value", [1, "yes", [], None])
def test_holdout_outcome_requires_boolean_before_writing(value):
    with pytest.raises(ValueError):
        kb.observe_holdout("class", value)
    assert not kb._BASELINE_PATH.exists()


def test_baseline_count_overflow_does_not_replace_existing_state():
    _write(json.dumps(_state(holdout_n=2**53 - 1, holdout_paid_n=0)))
    before = kb._BASELINE_PATH.read_bytes()
    with pytest.raises(ValueError):
        kb.observe_holdout("class", True)
    assert kb._BASELINE_PATH.read_bytes() == before


@pytest.mark.parametrize("value", ["15m", "", "NaN", "Infinity", "-1"])
def test_bad_default_pending_ttl_is_controlled_at_operation_not_import(tmp_path, value):
    env = {**os.environ, "KRY_DATA_DIR": str(tmp_path / "fresh"),
           "KRY_DISPLACEMENT_PENDING_TTL": value,
           "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    code = """
from kry import kry_pending as kp
print('imported')
try:
    kp.record_pending({'event_type': 'cache_hit', 'tokens_saved': 1})
except ValueError as exc:
    assert 'finite, non-negative' in str(exc)
    assert 'seconds' in str(exc)
    print('controlled refusal')
else:
    raise AssertionError('invalid TTL accepted')
assert not kp._PENDING_PATH.exists()
"""
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["imported", "controlled refusal"]


@pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-6-luna", "invented-opus-name", "notgpt-5model",
                                 "claude-sonnet-5-budget", "unknown/opus", "some-unlisted-model"])
def test_unpriced_model_never_inherits_a_substring_price(model):
    assert kt.value_multiplier(model) == 0.0


@pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-6-luna", "unknown-paid-model", "", "groq-paid/model"])
def test_unpriced_served_model_is_not_treated_as_free(model):
    assert kt.net_value_multiplier("opus", model) == 0.0


@pytest.mark.parametrize("model,expected", [
    ("anthropic/claude-sonnet-4.6", 0.6), ("or/anthropic/claude-haiku-4.5", 0.2),
])
def test_explicit_dotted_aliases_use_their_supported_price(model, expected):
    assert kt.value_multiplier(model) == expected


def test_unpriced_mint_and_earn_do_not_issue_credit():
    from kry import kry_mint as km
    assert kt.earn(1000, "cache_hit", avoided_model="gpt-5.6-luna") == 0
    receipt = km.mint("cache_hit", 1000, evidence="unpriced", avoided_model="gpt-5.6-luna")
    assert receipt is not None and receipt.kry_minted == 0
    assert kt.get_ledger().balance == 0


def test_known_free_routing_and_legacy_none_remain_distinct_from_unpriced():
    assert kt.value_multiplier(None) == 1.0
    assert kt.net_value_multiplier("opus", None) == 1.0
    assert kt.net_value_multiplier("opus", "google/gemini") == 1.0
    assert kt.net_value_multiplier("unknown", "google/gemini") == 0.0
