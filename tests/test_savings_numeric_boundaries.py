"""Plain-log token fields keep integer types and explicit alias presence."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_PROMPT = ("prompt_tokens", "tokens_prompt", "input_tokens", "native_tokens_prompt")
_COMPLETION = ("completion_tokens", "tokens_completion", "output_tokens", "native_tokens_completion")


@pytest.fixture
def sr():
    spec = importlib.util.spec_from_file_location("savings_numeric", _ROOT / "scripts" / "kry_savings_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("value", [True, False, "17", "0", 17.0, 17.9, None, [], {}, -1,
                                  float("inf"), float("nan")])
def test_noninteger_token_fields_do_not_create_savings(sr, value):
    record = {"id": "invalid-count", "cache_hit": True, "avoided_model": "gh/claude-opus-4.8",
              "usage": {"completion_tokens": value}}
    assert sr.normalize(record)["completion"] == 0
    assert sr.analyze([record])["saved_kry"] == 0.0


@pytest.mark.parametrize("value", [0, 17, 2**53 + 1, 2**63 - 1, 10**400])
def test_normalization_preserves_exact_json_integer_counts(sr, value):
    record = {"model": "test-model", "usage": {"prompt_tokens": value, "completion_tokens": value}}
    normalized = sr.normalize(record)
    assert normalized["prompt"] == value
    assert normalized["completion"] == value
    assert type(normalized["prompt"]) is int
    assert type(normalized["completion"]) is int


@pytest.mark.parametrize("fields,result_key", [(_PROMPT, "prompt"), (_COMPLETION, "completion")])
@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("value", [0, None])
def test_explicit_zero_or_null_does_not_fall_through_aliases(sr, fields, result_key, index, value):
    usage = {fields[index]: value, fields[index + 1]: 17}
    normalized = sr.normalize({"model": "test-model", "usage": usage})
    assert normalized[result_key] == 0


@pytest.mark.parametrize("fields,result_key", [(_PROMPT, "prompt"), (_COMPLETION, "completion")])
@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_missing_earlier_alias_allows_later_field(sr, fields, result_key, index):
    normalized = sr.normalize({"model": "test-model", "usage": {fields[index]: 17}})
    assert normalized[result_key] == 17


@pytest.mark.parametrize("field", ["prompt_tokens", "tokens_prompt"])
@pytest.mark.parametrize("value", [0, None])
def test_explicit_inclusive_prompt_does_not_acquire_anthropic_cache_counts(sr, field, value):
    usage = {field: value, "input_tokens": 17, "cache_read_input_tokens": 11,
             "cache_creation_input_tokens": 5}
    assert sr.normalize({"model": "test-model", "usage": usage})["prompt"] == 0


def test_zero_anthropic_input_still_keeps_separate_cache_categories(sr):
    usage = {"input_tokens": 0, "cache_read_input_tokens": 11, "cache_creation_input_tokens": 5}
    assert sr.normalize({"model": "test-model", "usage": usage})["prompt"] == 16
