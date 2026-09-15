"""The savings report's prompt-cache block, spend pricing and Anthropic prompt totals.

The prompt-cache figure is reported beside the minted savings and never added to them
(docs/PROMPT_CACHE_PLAN.md, Stage A).
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SAMPLE = _ROOT / "examples" / "sample_usage_log.jsonl"


@pytest.fixture
def sr():
    spec = importlib.util.spec_from_file_location("kry_savings_report_pc", _ROOT / "scripts" / "kry_savings_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sample():
    return [json.loads(ln) for ln in _SAMPLE.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _opus5_call(**usage):
    base = {"input_tokens": 10_000, "cache_read_input_tokens": 40_000, "cache_creation_input_tokens": 0,
            "output_tokens": 100}
    return {"id": "p1", "model": "claude-opus-5", "usage": {**base, **usage}}


def test_block_values_the_provider_worked_example(sr):
    pc = sr.analyze([_opus5_call()])["prompt_cache"]
    assert pc["records"]["priced"] == 1
    assert (pc["without_caching_usd"], pc["actual_usd"], pc["saving_usd"]) == (0.25, 0.07, 0.18)
    assert "not minted, not attested, self-reported" in pc["label"]


def test_cache_fields_never_change_minted_figures(sr):
    plain = sr.analyze(_sample())
    with_cache = copy.deepcopy(_sample())
    for rec in with_cache:
        if not rec.get("cache_hit"):
            rec["usage"]["cache_read_input_tokens"] = 5_000
    cached = sr.analyze(with_cache)
    for key in ("saved_kry", "saved_usd", "spend_kry", "spend_usd", "efficiency_ratio", "veracity",
                "by_kind", "by_class", "holdout", "unpriced_spend_calls"):
        assert cached[key] == plain[key], key


def test_cache_hits_are_not_provider_calls(sr):
    hit = {"id": "h", "cache_hit": True, "avoided_model": "claude-opus-5",
           "usage": {"input_tokens": 1, "cache_read_input_tokens": 99_999, "output_tokens": 500}}
    assert sr.analyze([hit])["prompt_cache"]["records"]["priced"] == 0


def test_strict_baseline_keeps_the_block_separate(sr):
    records = [{"id": "c", "cache_hit": True, "avoided_model": "gh/claude-opus-4.8",
                "usage": {"completion_tokens": 500}}, _opus5_call()]
    loose, strict = sr.analyze(records), sr.analyze(records, strict_baseline=True)
    assert loose["saved_kry"] > 0 and strict["saved_kry"] == 0.0
    assert strict["prompt_cache"] == loose["prompt_cache"]
    assert strict["prompt_cache"]["saving_usd"] == 0.18


def test_spend_uses_list_prices_and_leaves_unknown_models_unpriced(sr):
    records = [
        {"id": "s", "model": "claude-sonnet-5", "usage": {"input_tokens": 10, "output_tokens": 1_000}},
        {"id": "u", "model": "mystery-model-9", "usage": {"completion_tokens": 1_000}},
        {"id": "o", "model": "or/anthropic/claude-opus-4.8", "usage": {"completion_tokens": 1_000}},
    ]
    rep = sr.analyze(records)
    # Sonnet 5 lists $10/M output: 1,000 tokens = 400 KRY at $25/M per 1,000 KRY. The gateway id keeps
    # its SPEND_RATES rate (1,000 KRY). The unknown model is counted, not charged the frontier rate.
    assert rep["spend_kry"] == 1_400.0
    assert rep["unpriced_spend_calls"] == 1
    assert rep["by_kind"]["paid_call"] == 3      # paid/free classification still follows spend_cost


def test_normalize_counts_anthropic_cache_tokens_in_the_prompt(sr):
    anthropic = sr.normalize({"id": "a", "model": "claude-opus-5", "usage": {
        "input_tokens": 50, "cache_read_input_tokens": 1_000, "cache_creation_input_tokens": 248,
        "output_tokens": 7}})
    assert anthropic["prompt"] == 1_298
    openai_style = sr.normalize({"id": "b", "model": "x", "usage": {
        "prompt_tokens": 900, "cache_read_input_tokens": 1_000, "completion_tokens": 1}})
    assert openai_style["prompt"] == 900


def test_text_report_labels_the_block(sr, capsys):
    sr._print(sr.analyze([_opus5_call()]))
    out = capsys.readouterr().out
    assert "not minted, not attested, self-reported" in out
    assert "$0.1800 of $0.2500" in out
