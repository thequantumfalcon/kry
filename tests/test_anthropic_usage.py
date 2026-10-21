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


@pytest.mark.parametrize("reason,status", [
    ("end_turn", "ended"),
    ("stop_sequence", "stopped_by_sequence"),
    ("max_tokens", "truncated"),
    ("refusal", "refused"),
    ("model_context_window_exceeded", "truncated"),
])
def test_generation_status_does_not_change_usage_or_establish_a_bill(response, reason, status):
    response["stop_reason"] = reason
    if reason == "stop_sequence":
        response["stop_sequence"] = "PRIVATE_CONSTRUCTED_STOP_SEQUENCE"
    before = copy.deepcopy(response)
    result = estimate(response)
    assert result["stop_reason"] == reason
    assert result["generation_status"] == status
    assert result["observed_usage"] == before["usage"]
    assert result["total_estimate_usd"] == "0.000034"
    assert result["actual_charge_usd"] is None
    assert result["financial_status"] == "unverified"
    assert "PRIVATE_CONSTRUCTED_STOP_SEQUENCE" not in json.dumps(result)
    assert "Fabricated content" not in json.dumps(result)
    assert response == before


@pytest.mark.parametrize("reason", [
    None, False, 0, 1.0, [], {}, "unknown", "tool_use", "pause_turn",
])
def test_unsupported_or_unknown_stopping_condition_fails_closed(response, reason):
    response["stop_reason"] = reason
    with pytest.raises(ValueError, match="stop reason"):
        estimate(response)


def test_missing_stop_reason_is_not_assumed_to_be_a_finished_turn(response):
    del response["stop_reason"]
    with pytest.raises(ValueError, match="stop reason"):
        estimate(response)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_cli_nonstandard_json_constant_is_rejected_even_outside_usage(response, tmp_path, constant):
    source = tmp_path / "fabricated.json"
    source.write_text(json.dumps(response)[:-1] + ', "ignored": ' + constant + '}', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), str(source), "--request-id", "req_cli"],
        capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "nonstandard JSON constant" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Fabricated content" not in result.stderr


@pytest.mark.parametrize("extra_bytes", [0, 1])
def test_cli_document_byte_limit(response, tmp_path, extra_bytes):
    source = tmp_path / "fabricated.json"
    raw = json.dumps(response).encode("utf-8")
    source.write_bytes(raw + b" " * (16 * 1024 * 1024 + extra_bytes - len(raw)))
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), str(source), "--request-id", "req_cli"],
        capture_output=True, text=True)
    if extra_bytes:
        assert result.returncode != 0
        assert result.stdout == ""
        assert "16 MiB" in result.stderr
        assert "Fabricated content" not in result.stderr
    else:
        assert result.returncode == 0
        assert json.loads(result.stdout)["total_estimate_usd"] == "0.000034"
        assert result.stderr == ""


def test_cli_deep_json_is_a_controlled_error(response, tmp_path):
    source = tmp_path / "fabricated.json"
    document = json.dumps(response)[:-1] + ', "ignored": ' + '[' * 2000 + '0' + ']' * 2000 + '}'
    try:
        json.loads(document)
        parser_overflow = False
    except RecursionError:
        parser_overflow = True
    source.write_text(document, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), str(source), "--request-id", "req_cli"],
        capture_output=True, text=True)
    if parser_overflow:
        assert result.returncode != 0
        assert result.stdout == ""
        assert "too deeply nested" in result.stderr
    else:
        assert result.returncode == 0
        assert json.loads(result.stdout)["total_estimate_usd"] == "0.000034"
        assert result.stderr == ""
    assert "Traceback" not in result.stderr
    assert "Fabricated content" not in result.stderr


def test_cli_bounded_read_does_not_load_an_entire_response(response, monkeypatch, capsys):
    class BoundedResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            assert size == 16 * 1024 * 1024 + 1
            return json.dumps(response).encode("utf-8")

    def open_response(path, mode, *args, **kwargs):
        assert mode == "rb"
        return BoundedResponse()

    monkeypatch.setattr(Path, "open", open_response)
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT), "fabricated.json", "--request-id", "req_cli"])
    assert usage_estimate.main() == 0
    assert json.loads(capsys.readouterr().out)["total_estimate_usd"] == "0.000034"


def test_cli_parser_overflow_never_leaks_traceback_or_exception_text(
        response, tmp_path, monkeypatch, capsys):
    source = tmp_path / "fabricated.json"
    source.write_text(json.dumps(response), encoding="utf-8")

    def overflow(*args, **kwargs):
        raise RecursionError("PRIVATE_PARSER_DETAIL")

    monkeypatch.setattr(usage_estimate.json, "loads", overflow)
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT), str(source), "--request-id", "req_cli"])
    with pytest.raises(SystemExit) as error:
        usage_estimate.main()
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "too deeply nested" in captured.err
    assert "Traceback" not in captured.err
    assert "PRIVATE_PARSER_DETAIL" not in captured.err


@pytest.mark.parametrize("iterations", [
    None, [], {}, False,
    [{"type": "message", "model": usage_estimate.MODEL,
      "input_tokens": 14, "output_tokens": 4,
      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}],
    [{"type": "fallback_message", "model": usage_estimate.MODEL,
      "input_tokens": 14, "output_tokens": 4,
      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}],
    [{"type": "message", "model": "unsupported-model",
      "input_tokens": 200, "output_tokens": 5,
      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
     {"type": "fallback_message", "model": usage_estimate.MODEL,
      "input_tokens": 14, "output_tokens": 4,
      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}],
])
def test_iteration_metadata_cannot_be_priced_as_an_ordinary_single_attempt(response, iterations):
    # Constructed shapes: no claim that the pinned model supports fallback.
    # Additional attempts must not disappear behind valid top-level counts.
    response["usage"]["iterations"] = iterations
    before = copy.deepcopy(response)
    with pytest.raises(ValueError, match="unsupported usage category"):
        estimate(response)
    assert response == before


def test_fallback_content_is_outside_the_ordinary_text_profile(response):
    response["content"].insert(0, {
        "type": "fallback", "from": {"model": "unsupported-model"},
        "to": {"model": usage_estimate.MODEL},
    })
    with pytest.raises(ValueError, match="ordinary text"):
        estimate(response)


def test_cli_iterations_emit_no_partial_price_or_response_content(response, tmp_path):
    response["usage"]["iterations"] = [{
        "type": "fallback_message", "model": usage_estimate.MODEL,
        "input_tokens": 14, "output_tokens": 4,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    }]
    source = tmp_path / "response.json"
    source.write_text(json.dumps(response))
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), str(source), "--request-id", "req_fabricated"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "unsupported usage category" in result.stderr
    assert "Fabricated content" not in result.stderr
