"""Usage-log privacy boundary — no prompt/response content may ship in a verified packet.

Audit D: the gate was a denylist of exact key spellings, so private content smuggled under an
unlisted key (prompts/the_prompt/msg/text/data/note/query/...) shipped clean. It is now a
word-split private match + an allowlist guard for the bounded usage-log schema.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from kry_artifact_privacy import _provider_export_privacy_errors, _usage_log_privacy_errors  # noqa: E402


@pytest.mark.parametrize("rec", [
    {"prompts": "leak"},
    {"the_prompt": "x"},
    {"system_prompt": "x"},
    {"user_prompt": "x"},
    {"message_content": "x"},
    {"msg": "leak"},
    {"text": "leak"},
    {"data": "a private user prompt here"},
    {"note": "the user asked about X"},
    {"query": "secret"},
    {"conversation": [{"role": "user", "content": "x"}]},
])
def test_usage_log_privacy_catches_bypass_keys(rec):
    assert _usage_log_privacy_errors([rec]), f"{rec} leaked through the privacy gate"


def test_usage_log_privacy_passes_legitimate_record():
    legit = {
        "id": "r1", "request_class": "summarize", "cache_hit": True,
        "avoided_model": "gh/claude-opus-4.8", "served_model": "gh/haiku",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        "tokens_saved": 50, "evidence_tier": "self_reported",
    }
    assert _usage_log_privacy_errors([legit]) == []


def test_usage_log_privacy_does_not_false_flag_generic_metadata():
    # request_id / request_class contain the word "request" but are NOT private content.
    rec = {"id": "x", "request_id": "abc", "request_class": "code", "cache_hit": True,
           "avoided_model": "m", "usage": {"total_tokens": 10}}
    assert _usage_log_privacy_errors([rec]) == []


# Usage as the provider reports it: cache writes with their TTL split, and the fields that change the
# price. The savings report and the reconciler read all of them.
_PROVIDER_USAGE = {
    "input_tokens": 50, "cache_read_input_tokens": 100_000, "cache_creation_input_tokens": 248,
    "cache_creation": {"ephemeral_5m_input_tokens": 148, "ephemeral_1h_input_tokens": 100},
    "output_tokens": 503, "service_tier": "standard", "speed": "standard", "inference_geo": "not_available",
}


def _export(tmp_path, records):
    path = tmp_path / "provider_export.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    return path


def test_usage_log_privacy_passes_provider_cache_and_price_fields():
    call = {"id": "r1", "model": "claude-opus-5", "usage": _PROVIDER_USAGE}
    row = {"model": "claude-sonnet-5", "usage": {**_PROVIDER_USAGE, "service_tier": "priority",
                                                  "inference_geo": "us", "context_window": "200k-1M"}}
    nulls = {"model": "claude-haiku-4-5", "usage": {**_PROVIDER_USAGE, "cache_creation": None, "speed": None}}
    assert _usage_log_privacy_errors([call, row, nulls]) == []


def test_provider_export_privacy_passes_provider_cache_and_price_fields(tmp_path):
    export = _export(tmp_path, [{"id": "g1", "model": "claude-opus-5", "usage": _PROVIDER_USAGE}])
    assert _provider_export_privacy_errors(export) == []


@pytest.mark.parametrize("field", ["service_tier", "speed", "inference_geo", "context_window"])
def test_price_fields_accept_only_documented_values(field, tmp_path):
    record = {"model": "claude-opus-5", "usage": {**_PROVIDER_USAGE, field: "the user asked about X"}}
    assert _usage_log_privacy_errors([record]), f"free text in {field} passed the usage log gate"
    assert _provider_export_privacy_errors(_export(tmp_path, [record])), f"free text in {field} passed the export gate"


def test_cache_creation_carries_only_token_counts():
    usage = {**_PROVIDER_USAGE, "cache_creation": {"ephemeral_5m_input_tokens": 1, "note": "a private prompt"}}
    assert _usage_log_privacy_errors([{"model": "claude-opus-5", "usage": usage}])


# OpenAI reports its cache counters in a nested object and names the standard tier "default".
# Copied from a real response (2026-09-16); `prompt_tokens_details` is the Chat Completions spelling.
_OPENAI_USAGE = {
    "input_tokens": 5897, "input_tokens_details": {"cached_tokens": 5894, "cache_write_tokens": 0},
    "output_tokens": 5, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 5902,
    "service_tier": "default",
}


def test_usage_log_privacy_passes_openai_cache_fields():
    responses = {"id": "o1", "model": "gpt-5.6-luna", "usage": _OPENAI_USAGE}
    chat = {"id": "o2", "model": "gpt-5.6-luna", "usage": {
        "prompt_tokens": 15_000, "completion_tokens": 2,
        "prompt_tokens_details": {"cached_tokens": 12_000, "cache_write_tokens": 3_000}}}
    assert _usage_log_privacy_errors([responses, chat]) == []


def test_provider_export_privacy_passes_openai_cache_fields(tmp_path):
    export = _export(tmp_path, [{"id": "o1", "model": "gpt-5.6-luna", "usage": _OPENAI_USAGE}])
    assert _provider_export_privacy_errors(export) == []


@pytest.mark.parametrize("tier", ["default", "auto", "fast", "ultrafast"])
def test_openai_service_tier_values_are_documented(tier):
    record = {"model": "gpt-5.6-luna", "usage": {**_OPENAI_USAGE, "service_tier": tier}}
    assert _usage_log_privacy_errors([record]) == []


@pytest.mark.parametrize("details", ["input_tokens_details", "prompt_tokens_details"])
def test_openai_token_details_carry_only_token_counts(details):
    # The nested object is allowed for its counts; free text under a new name inside it is not.
    usage = {**_OPENAI_USAGE, details: {"cached_tokens": 1, "note": "a private prompt"}}
    assert _usage_log_privacy_errors([{"model": "gpt-5.6-luna", "usage": usage}])
