"""Fabricated provider responses for the separate Anthropic usage estimate."""
from __future__ import annotations

import copy
from decimal import Decimal, localcontext
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "kry_anthropic_usage.py"
_SPEC = importlib.util.spec_from_file_location("kry_anthropic_usage", _SCRIPT)
usage_estimate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(usage_estimate)


@pytest.fixture
def response():
    return {
        "id": "msg_fabricated",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5-20251001",
        "content": [{"type": "text", "text": "Fabricated content, never copied"}],
        "usage": {"input_tokens": 14, "output_tokens": 4},
        "stop_reason": "end_turn",
        "stop_sequence": None,
    }


def estimate(response, **kwargs):
    return usage_estimate.estimate_anthropic_usage(
        response, request_id=kwargs.get("request_id", "req_fabricated"))


def test_prices_input_and_output_separately_without_claiming_a_bill(response):
    result = estimate(response)
    assert result["input_estimate_usd"] == "0.000014"
    assert result["output_estimate_usd"] == "0.00002"
    assert result["total_estimate_usd"] == "0.000034"
    assert result["observed_usage"] == {"input_tokens": 14, "output_tokens": 4}
    assert result["message_id"] == "msg_fabricated"
    assert result["request_id"] == "req_fabricated"
    assert result["pricing"]["as_of"] == "2026-10-20"
    assert result["pricing"]["input_usd_per_million_tokens"] == "1"
    assert result["pricing"]["output_usd_per_million_tokens"] == "5"
    assert result["actual_charge_usd"] is None
    assert result["financial_status"] == "unverified"
    assert "Fabricated content" not in json.dumps(result)


def test_decimal_estimate_is_exact_despite_callers_low_precision(response):
    response["usage"] = {"input_tokens": 123456789012345678901234567890, "output_tokens": 3}
    with localcontext() as context:
        context.prec = 2
        result = estimate(response)
    assert result["total_estimate_usd"] == "123456789012345678901234.567905"
    assert Decimal(result["total_estimate_usd"]).is_finite()


def test_explicit_zero_cache_metadata_does_not_change_price_or_alias_input(response):
    response["usage"].update(
        cache_creation_input_tokens=0, cache_read_input_tokens=0, service_tier="standard",
        cache_creation={"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0})
    before = copy.deepcopy(response)
    result = estimate(response)
    assert response == before
    assert result["observed_usage"] == response["usage"]
    assert result["total_estimate_usd"] == "0.000034"
    result["observed_usage"]["cache_creation"]["ephemeral_5m_input_tokens"] = 99
    assert response == before


def test_haiku_45_unavailable_geo_preserves_metadata_with_zero_cache_counts(response):
    response["usage"].update(
        cache_creation_input_tokens=0, cache_read_input_tokens=0, service_tier="standard",
        cache_creation={"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
        inference_geo="not_available",
    )
    before = copy.deepcopy(response)
    result = estimate(response)
    assert result["observed_usage"] == before["usage"]
    assert result["observed_usage"]["inference_geo"] == "not_available"
    assert result["total_estimate_usd"] == "0.000034"
    assert result["actual_charge_usd"] is None
    assert result["financial_status"] == "unverified"
    assert "global inference" not in result["pricing"]["assumptions"]
    assert response == before


def test_nullable_sdk_metadata_stays_unknown_and_does_not_change_estimate(response):
    names = (
        "cache_creation_input_tokens", "cache_read_input_tokens", "cache_creation",
        "service_tier", "speed", "inference_geo", "server_tool_use", "output_tokens_details",
    )
    response["usage"].update({name: None for name in names})
    response["content"][0]["citations"] = None
    response["container"] = None
    result = estimate(response)
    assert result["total_estimate_usd"] == "0.000034"
    assert result["observed_usage"] == response["usage"]
    assert all(result["observed_usage"][name] is None for name in names)
    assert "unknown, not an observed zero" in result["pricing"]["assumptions"]
    assert result["actual_charge_usd"] is None


def test_standard_zero_metadata_is_preserved_without_double_counting(response):
    response["usage"].update(
        service_tier="standard", speed="standard", inference_geo="global",
        server_tool_use={"web_fetch_requests": 0, "web_search_requests": 0},
        output_tokens_details={"thinking_tokens": 0},
    )
    response["content"][0]["citations"] = []
    before = copy.deepcopy(response)
    result = estimate(response)
    assert result["total_estimate_usd"] == "0.000034"
    assert result["observed_usage"] == before["usage"]
    result["observed_usage"]["server_tool_use"]["web_fetch_requests"] = 99
    result["observed_usage"]["output_tokens_details"]["thinking_tokens"] = 99
    assert response == before


def test_omitted_optional_counts_are_not_filled_in(response):
    response["usage"]["server_tool_use"] = {"web_search_requests": 0}
    result = estimate(response)
    assert result["observed_usage"] == response["usage"]
    assert "web_fetch_requests" not in result["observed_usage"]["server_tool_use"]
    assert "cache_read_input_tokens" not in result["observed_usage"]
    assert result["total_estimate_usd"] == "0.000034"


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_required_counts_are_never_filled_in(response, field):
    del response["usage"][field]
    with pytest.raises(ValueError, match="missing usage"):
        estimate(response)


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
@pytest.mark.parametrize("value", [False, True, -1, 1.0, "1", None])
def test_invalid_token_counts_fail_closed(response, field, value):
    response["usage"][field] = value
    with pytest.raises(ValueError, match="nonnegative integers"):
        estimate(response)


@pytest.mark.parametrize("field", ["cache_creation_input_tokens", "cache_read_input_tokens"])
@pytest.mark.parametrize("value", [False, True, -1, 1.0, "1"])
def test_invalid_optional_token_counts_fail_closed(response, field, value):
    response["usage"][field] = value
    with pytest.raises(ValueError, match="nonnegative integers"):
        estimate(response)


@pytest.mark.parametrize("extra", [
    {"cache_creation_input_tokens": 1},
    {"cache_read_input_tokens": 1},
    {"cache_creation": {"ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 0}},
    {"cache_creation": {"ephemeral_5m_input_tokens": False, "ephemeral_1h_input_tokens": 0}},
    {"cache_creation": {}},
    {"server_tool_use": {"web_search_requests": 1}},
    {"server_tool_use": {"web_fetch_requests": 1}},
    {"server_tool_use": {"web_search_requests": False}},
    {"server_tool_use": {"web_search_requests": 0.0}},
    {"server_tool_use": {"code_execution_requests": 0}},
    {"server_tool_use": {}},
    {"server_tool_use": []},
    {"thinking_tokens": 1},
    {"output_tokens_details": {"thinking_tokens": 1}},
    {"output_tokens_details": {"thinking_tokens": False}},
    {"output_tokens_details": {"thinking_tokens": 0.0}},
    {"output_tokens_details": {"thinking_tokens": 0, "other_tokens": 0}},
    {"output_tokens_details": {}},
    {"output_tokens_details": []},
    {"service_tier": "priority"},
    {"service_tier": "batch"},
    {"speed": "fast"},
    {"inference_geo": "us"},
    {"inference_geo": "unknown"},
    {"new_billable_category": 0},
])
def test_unsupported_usage_categories_fail_closed(response, extra):
    response["usage"].update(extra)
    with pytest.raises(ValueError):
        estimate(response)


@pytest.mark.parametrize("replacement", [
    {"model": "claude-haiku-4-5"},
    {"model": "claude-sonnet-4-5-20250929"},
    {"type": "message_start"},
    {"role": "user"},
    {"id": "chatcmpl_generated"},
    {"id": None},
    {"content": [{"type": "thinking", "thinking": "not supported"}]},
    {"content": [{"type": "tool_use", "name": "example"}]},
    {"content": [{"type": "text", "text": "OK", "citations": [{"type": "page_location"}]}]},
    {"content": [{"type": "text", "text": "OK", "citations": {}}]},
    {"content": [{"type": "text", "text": "OK", "citations": ""}]},
    {"content": [{"type": "text", "text": "OK", "unrecognized_field": None}]},
    {"content": []},
    {"usage": None},
    {"container": {"id": "container_fabricated"}},
])
def test_unsupported_raw_response_fails_closed(response, replacement):
    response.update(replacement)
    with pytest.raises(ValueError):
        estimate(response)


@pytest.mark.parametrize("request_id", [None, "", "fabricated", "req_bad\nheader"])
def test_request_identifier_requires_provider_header_shape(response, request_id):
    with pytest.raises(ValueError, match="provider request id"):
        estimate(response, request_id=request_id)


def test_cli_outputs_only_separate_estimate(response, tmp_path):
    source = tmp_path / "fabricated.json"
    source.write_text(json.dumps(response), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), str(source), "--request-id", "req_cli"],
        capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    assert payload["request_id"] == "req_cli"
    assert payload["total_estimate_usd"] == "0.000034"
    assert payload["actual_charge_usd"] is None
    assert result.stderr == ""


def test_cli_bad_usage_produces_no_estimate(response, tmp_path):
    del response["usage"]["output_tokens"]
    source = tmp_path / "fabricated.json"
    source.write_text(json.dumps(response), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), str(source), "--request-id", "req_cli"],
        capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "missing usage" in result.stderr
    assert "Fabricated content" not in result.stderr
