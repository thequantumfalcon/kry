"""Report-only prompt-cache valuation (docs/PROMPT_CACHE_PLAN.md, Stage A).

Figures come from the provider's documentation: the pricing page's Opus 5 worked example (40,000
cache-read tokens cost $0.02 instead of $0.20) and the prompt caching page's mixed-TTL usage example.
"""
from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from kry import kry_prompt_cache as pc


def _usage(**fields):
    return {"input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, **fields}


def test_provider_worked_example_saves_eighteen_cents():
    r = pc.value_record("claude-opus-5", _usage(input_tokens=10_000, cache_read_input_tokens=40_000))
    assert r["status"] == "priced"
    assert r["without_caching_usd"] == Decimal("0.25")
    assert r["actual_usd"] == Decimal("0.07")
    assert r["saving_usd"] == Decimal("0.18")


def test_mixed_ttl_example_prices_each_write_class():
    usage = _usage(input_tokens=50, cache_read_input_tokens=100_000, cache_creation_input_tokens=248,
                   cache_creation={"ephemeral_5m_input_tokens": 148, "ephemeral_1h_input_tokens": 100})
    r = pc.value_record("claude-opus-5", usage)
    # read saving 100,000 x 0.9 x $5/M, minus write premiums 148 x 0.25 x $5/M and 100 x 1.0 x $5/M
    assert r["saving_usd"] == Decimal("0.449315")
    assert (r["cache_write_5m"], r["cache_write_1h"], r["ttl_assumed_1h"]) == (148, 100, False)


def test_fable_5_1_reads_use_the_lower_multiplier():
    r = pc.value_record("claude-fable-5-1", _usage(cache_read_input_tokens=1_000_000))
    assert r["actual_usd"] == Decimal("0.25")
    assert r["saving_usd"] == Decimal("9.75")


def test_writes_never_read_back_are_a_negative_saving():
    usage = _usage(cache_creation_input_tokens=1_000,
                   cache_creation={"ephemeral_5m_input_tokens": 1_000, "ephemeral_1h_input_tokens": 0})
    assert pc.value_record("claude-opus-5", usage)["saving_usd"] == Decimal("-0.00125")


def test_missing_ttl_split_is_priced_at_the_one_hour_rate():
    known = pc.value_record("claude-opus-5", _usage(
        cache_read_input_tokens=10_000, cache_creation_input_tokens=500,
        cache_creation={"ephemeral_5m_input_tokens": 500, "ephemeral_1h_input_tokens": 0}))
    assumed = pc.value_record("claude-opus-5", _usage(cache_read_input_tokens=10_000,
                                                      cache_creation_input_tokens=500))
    assert assumed["ttl_assumed_1h"] is True and assumed["cache_write_1h"] == 500
    assert assumed["saving_usd"] < known["saving_usd"]


def test_split_that_does_not_add_up_is_malformed():
    usage = _usage(cache_creation_input_tokens=500,
                   cache_creation={"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 100})
    assert pc.value_record("claude-opus-5", usage)["status"] == "malformed"


def test_unknown_models_are_unpriced_and_the_haiku_snapshot_is_priced():
    assert pc.value_record("claude-mythos-5-1", _usage())["status"] == "unpriced"
    assert pc.value_record("gpt-5.6-sol", _usage())["status"] == "unpriced"
    assert pc.value_record(None, _usage())["status"] == "unpriced"
    assert pc.value_record("claude-haiku-4-5-20251001", _usage())["model"] == "claude-haiku-4-5"


@pytest.mark.parametrize("modifier", [{"service_tier": "batch"}, {"service_tier": "priority"},
                                      {"speed": "fast"}, {"inference_geo": "us"}])
def test_price_modifiers_are_excluded(modifier):
    assert pc.value_record("claude-opus-5", _usage(**modifier))["status"] == "modifier_excluded"


@pytest.mark.parametrize("standard", [{}, {"service_tier": "standard"}, {"speed": "standard"},
                                      {"inference_geo": "global"}, {"inference_geo": "not_available"}])
def test_standard_usage_is_priced(standard):
    assert pc.value_record("claude-opus-5", _usage(**standard))["status"] == "priced"


@pytest.mark.parametrize("usage", [
    {"cache_read_input_tokens": 5},                            # no input_tokens field
    _usage(input_tokens=-1), _usage(cache_read_input_tokens=True), _usage(input_tokens="10"),
    "not a dict",
])
def test_malformed_usage_is_counted_not_valued(usage):
    assert pc.value_record("claude-opus-5", usage)["status"] == "malformed"


def test_summary_counts_every_record_and_labels_the_figure():
    calls = [
        ("claude-opus-5", _usage(input_tokens=10_000, cache_read_input_tokens=40_000)),
        ("claude-opus-5", _usage(cache_creation_input_tokens=100)),             # TTL assumed
        ("mystery-model", _usage()),
        ("claude-opus-5", _usage(speed="fast")),
        ("claude-opus-5", _usage(input_tokens=-5)),
    ]
    s = pc.summarize(calls)
    assert s["records"] == {"priced": 2, "unpriced": 1, "modifier_excluded": 1, "malformed": 1,
                            "ttl_assumed_1h": 1}
    assert s["unpriced_models"] == {"mystery-model": 1}
    assert s["label"] == pc.LABEL and "not minted" in s["label"]
    assert s["price_basis_as_of"] == pc.PRICE_BASIS_AS_OF
    # 0.18 saved on the first call; the second writes 100 tokens at the 1-hour premium ($5/M x 1.0)
    assert s["saving_usd"] == 0.1795
    assert s["by_model"]["claude-opus-5"]["records"] == 2


def test_empty_summary_has_no_percentage():
    s = pc.summarize([])
    assert s["saving_usd"] == 0.0 and s["saving_pct_of_input_cost"] is None


def test_price_table_follows_the_documented_multipliers():
    for model, (base, w5, w1h, read, _out) in pc._ANTHROPIC_PRICES.items():
        base = Decimal(base)
        assert Decimal(w5) == base * Decimal("1.25"), model
        assert Decimal(w1h) == base * 2, model
        expected_read = Decimal("0.025") if model == "claude-fable-5-1" else Decimal("0.1")
        assert Decimal(read) == base * expected_read, model


def test_output_price_is_exact_or_none():
    assert pc.output_price_usd_per_m("claude-sonnet-5") == Decimal("10")
    assert pc.output_price_usd_per_m("or/anthropic/claude-opus-4.8") is None


def test_module_imports_nothing_that_mints():
    tree = ast.parse(Path(pc.__file__).read_text(encoding="utf-8"))
    modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    modules |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert modules <= {"__future__", "decimal"}, sorted(modules)
