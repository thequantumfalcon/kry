"""Explicit snapshot benchmarks and private callback rejection diagnostics."""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import pytest

from kry import kry_mint as km, kry_token as kt

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def callback():
    spec = importlib.util.spec_from_file_location(
        "review3_pricing_callback", ROOT / "scripts" / "kry_litellm_callback.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("model,benchmark,expected", [
    ("claude-opus-4-1-20250805", "opus", 1.0),
    ("anthropic/claude-opus-4-5-20251101", "opus", 1.0),
    ("openai/gpt-4o-2024-08-06", "gpt-4o", 0.4),
    ("gpt-4o-2024-11-20", "gpt-4o", 0.4),
    ("or/openai/gpt-4o-mini-2024-07-18", "gpt-4o-mini", 0.024),
])
def test_supported_snapshots_use_the_declared_dated_benchmark(model, benchmark, expected):
    assert kt.value_multiplier(model) == expected
    basis = kt.price_provenance()["models"][benchmark]
    assert basis["as_of"] == "2026-06-03"
    assert basis["usd_per_m"] / kt.FRONTIER_USD_PER_M_OUTPUT == expected
    receipt = km.mint("cache_hit", 100, evidence=model, avoided_model=model)
    assert receipt is not None and receipt.kry_minted == 100 * expected
    assert km.verify_chain()[0]


@pytest.mark.parametrize("model", [
    "claude-opus-4-5-20990101", "claude-opus-4-1-20250806",
    "gpt-4o-2024-08-07", "gpt-4o-mini-2024-07-19", "made-up-gpt-4o-2024-08-06",
    "unknown/openai/gpt-4o-2024-08-06", "deepseek-chat", "deepseek/deepseek-reasoner",
    "deepseek-flash",
])
def test_unlisted_dates_and_ambiguous_native_aliases_remain_unpriced(model):
    assert kt._priced_multiplier(model) is None
    assert kt.value_multiplier(model) == 0
    assert kt.net_value_multiplier("opus", model) == 0


def _event(model, baseline=None):
    return {"cache_hit": baseline is None, "model": model,
            "litellm_call_id": "PRIVATE_CALL_ID", "messages": ["PRIVATE_PROMPT"],
            "litellm_params": {"metadata": {
                "account_id": "PRIVATE_ACCOUNT_ID", "routing_decision": {
                    "savings_baseline_model": baseline, "signals": ["PRIVATE_SIGNAL"]}}}}


RESPONSE = {"usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}


def test_dated_served_model_receives_only_the_benchmark_difference(callback):
    receipt = callback.mint_from_litellm(
        _event("openai/gpt-4o-mini-2024-07-18", "gpt-4o-2024-08-06"), RESPONSE)
    assert receipt is not None
    assert receipt.kry_minted == pytest.approx(7.52)
    assert kt.get_ledger().balance == pytest.approx(7.52)
    assert km.verify_chain()[0]


def test_unpriced_cache_logs_reason_and_keeps_zero_value_receipt(callback, caplog):
    with caplog.at_level(logging.WARNING, logger="kry.litellm"):
        receipt = callback.mint_from_litellm(_event("PRIVATE_UNKNOWN_MODEL"), RESPONSE)
    assert receipt is not None and receipt.kry_minted == 0
    assert "unpriced_avoided_model" in caplog.text
    assert "zero benchmark credit" in caplog.text
    assert "PRIVATE_" not in caplog.text
    assert kt.get_ledger().balance == 0


@pytest.mark.parametrize("baseline,served,reason", [
    ("PRIVATE_UNKNOWN_BASELINE", "gpt-4o-mini", "unpriced_avoided_model"),
    ("gpt-4o", "PRIVATE_UNKNOWN_SERVED", "unpriced_served_model"),
    ("PRIVATE_UNKNOWN_BOTH", "PRIVATE_UNKNOWN_BOTH", "unpriced_avoided_model"),
])
def test_unpriced_route_logs_reason_without_request_data(callback, caplog, baseline, served, reason):
    with caplog.at_level(logging.WARNING, logger="kry.litellm"):
        assert callback.mint_from_litellm(_event(served, baseline), RESPONSE) is None
    assert reason in caplog.text and "no receipt" in caplog.text
    assert "PRIVATE_" not in caplog.text
    assert not km._MINT_LOG_PATH.exists()


@pytest.mark.parametrize("baseline,served", [
    ("gpt-4o-mini", "gpt-4o"), ("gpt-4o", "openai/gpt-4o"),
    ("google/gemini", "gpt-4o-mini"),
])
def test_known_noncheaper_routes_are_not_reported_as_unpriced(callback, caplog, baseline, served):
    with caplog.at_level(logging.DEBUG, logger="kry.litellm"):
        assert callback.mint_from_litellm(_event(served, baseline), RESPONSE) is None
    assert "no_positive_benchmark_difference" in caplog.text
    assert "unpriced" not in caplog.text
    assert not km._MINT_LOG_PATH.exists()


def test_known_free_cache_and_route_keep_their_declared_policy(callback, caplog):
    with caplog.at_level(logging.DEBUG, logger="kry.litellm"):
        cached = callback.mint_from_litellm(_event("google/gemini"), RESPONSE)
        routed = callback.mint_from_litellm(_event("google/gemini", "gpt-4o"), RESPONSE)
    assert cached is not None and cached.kry_minted == 0
    assert routed is not None and routed.kry_minted == 8
    assert "known_free_avoided_model" in caplog.text
    assert "unpriced" not in caplog.text
    assert "PRIVATE_" not in caplog.text
