"""Optional callback observations preserve normalized metadata, never provider truth."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _observe(kwargs=None, response=None):
    return _load("kry_litellm_observation").observe_litellm_usage(
        {} if kwargs is None else kwargs, {} if response is None else response)


def _count(observation, name="total_tokens"):
    return observation["usage"]["fields"][name]


@pytest.mark.parametrize("response,state", [({}, "absent"), ({"usage": None}, "null"),
                                           ({"usage": []}, "invalid"), ({"usage": {}}, "value")])
def test_usage_parent_presence(response, state):
    assert _observe(response=response)["usage"]["state"] == state


@pytest.mark.parametrize("usage,expected", [({}, {"state": "absent"}),
    ({"total_tokens": None}, {"state": "null"}),
    ({"total_tokens": 0}, {"state": "value", "value": 0}),
    ({"total_tokens": 9007199254740993}, {"state": "value", "value": 9007199254740993})])
def test_literal_count_presence_and_precision(usage, expected):
    actual = _count(_observe(response={"usage": usage}))
    assert actual == expected
    assert json.loads(json.dumps(actual, allow_nan=False)) == expected


@pytest.mark.parametrize("value", [True, 1.0, -1, "SECRET_COUNT", float("nan"),
                                  float("inf"), 2**63, {"prompt": "SECRET_COUNT"}])
def test_invalid_counts_do_not_coerce_or_copy_content(value):
    actual = _observe(response={"usage": {"total_tokens": value}})
    assert _count(actual) == {"state": "invalid"}
    assert "SECRET_COUNT" not in json.dumps(actual, allow_nan=False)


def test_nested_categories_are_not_summed_or_aliased():
    usage = {"prompt_tokens": 160, "completion_tokens": 7, "total_tokens": 167,
             "cache_read_input_tokens": 40, "cache_creation_input_tokens": 20,
             "prompt_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 20,
                 "text_tokens": 100, "cache_creation_token_details": {
                     "ephemeral_5m_input_tokens": 8, "ephemeral_1h_input_tokens": 12}},
             "context_details": {"input_tokens": 160, "output_tokens": 99},
             "completion_tokens_details": {"reasoning_tokens": 3},
             "cost_in_usd_ticks": 9007199254740993, "cost": 0.01}
    first = _observe(response={"usage": usage})
    fields = first["usage"]["fields"]
    assert fields["prompt_tokens"]["value"] == 160
    assert fields["total_tokens"]["value"] == 167
    details = fields["prompt_tokens_details"]["fields"]
    assert details["cache_write_tokens"]["value"] == 20
    assert details["cache_creation_tokens"] == {"state": "absent"}
    assert details["cache_creation_token_details"]["fields"]["ephemeral_1h_input_tokens"]["value"] == 12
    assert fields["context_details"]["fields"]["output_tokens"]["value"] == 99
    assert fields["cost_in_usd_ticks"]["value"] == 9007199254740993
    second = _observe(response={"usage": {**usage, "cache_read_input_tokens": 0}})
    assert _count(first) == _count(second)
    assert first != second
    assert first["actual_charge_usd"] is None
    assert first["billing_reconciliation"] == "not_performed"


def test_object_usage_and_unsupported_fields_remain_bounded():
    response = SimpleNamespace(model="model-a", usage=SimpleNamespace(
        prompt_tokens=0, completion_tokens=None, total_tokens=0, iterations=[{"text": "SECRET"}]))
    actual = _observe({"model": "provider/model-a"}, response)
    assert _count(actual, "prompt_tokens") == {"state": "value", "value": 0}
    assert _count(actual, "completion_tokens") == {"state": "null"}
    assert actual["usage"]["unsupported_fields_present"] is True
    assert actual["source"] == "litellm_callback_normalized"
    assert actual["source_authenticity"] == "operator_observed"
    assert "SECRET" not in json.dumps(actual)


@pytest.mark.parametrize("reported,state", [("model-a", "exact_match"),
    ("provider/model-a", "different"), ("model-b", "different"), (None, "unknown"),
    ("SECRET MODEL\n", "unknown")])
def test_requested_and_reported_model_are_not_canonicalized(reported, state):
    actual = _observe({"model": "model-a"}, {"model": reported})
    assert actual["model_comparison"] == state
    assert actual["callback_requested_model"] == {"state": "value", "value": "model-a"}
    assert "SECRET MODEL" not in json.dumps(actual)


def test_header_and_identity_namespaces_are_hashed():
    actual = _observe({"litellm_call_id": "call_constructed"}, {
        "id": "chatcmpl_constructed", "_hidden_params": {"additional_headers": {
            "llm_provider-request-id": "req_constructed", "authorization": "SECRET_KEY"}}})
    header = actual["provider_request_id_candidate"]
    assert header["origin"] == "litellm_flattened_header"
    assert header["duplicate_detection"] == "unavailable"
    assert header["identity"] == {"state": "value", "sha256": hashlib.sha256(b"req_constructed").hexdigest()}
    assert actual["litellm_call_id"]["sha256"] == hashlib.sha256(b"call_constructed").hexdigest()
    assert actual["normalized_response_id"]["sha256"] == hashlib.sha256(b"chatcmpl_constructed").hexdigest()
    text = json.dumps(actual)
    assert all(secret not in text for secret in ("SECRET_KEY", "req_constructed", "call_constructed", "chatcmpl_constructed"))
    assert _observe()["provider_request_id_candidate"]["identity"] == {"state": "absent"}


@pytest.mark.parametrize("headers", [
    {"llm_provider-request-id": "one,two"}, {"llm_provider-request-id": "one\ntwo"},
    {"llm_provider-request-id": ""}, {"llm_provider-request-id": "x" * 513},
    {"llm_provider-request-id": "one", "LLM_PROVIDER-REQUEST-ID": "two"}])
def test_ambiguous_or_malformed_header_has_no_join_digest(headers):
    actual = _observe(response={"_hidden_params": {"additional_headers": headers}})
    assert actual["provider_request_id_candidate"]["identity"] == {"state": "invalid"}


def test_non_allowlisted_header_values_are_not_accessed():
    class Headers(dict):
        def items(self):
            raise AssertionError("Must not enumerate header values")

        def __getitem__(self, name):
            assert name == "llm_provider-request-id"
            return super().__getitem__(name)
    headers = Headers({"authorization": "SECRET_KEY", "llm_provider-request-id": "req_constructed"})
    result = _observe(response={"_hidden_params": {"additional_headers": headers}})
    assert result["provider_request_id_candidate"]["identity"]["sha256"] == hashlib.sha256(
        b"req_constructed").hexdigest()


def test_content_traps_are_never_serialized_or_accessed():
    class Response:
        usage = SimpleNamespace(total_tokens=0)
        model = "model-a"
        _hidden_params = {"original_response": "SECRET_CONTENT",
                          "provider_specific_fields": {"tool_results": "SECRET_TOOL"}}

        @property
        def choices(self):
            raise AssertionError("Must not inspect output content")

        def model_dump(self, **kwargs):
            raise AssertionError("Must not serialize whole response")

    actual = _observe({"messages": "SECRET_PROMPT", "litellm_params": {
        "metadata": {"signals": "SECRET_SIGNALS"}}}, Response())
    assert "SECRET" not in json.dumps(actual)


def _logger_module(monkeypatch):
    # The unit hook tests do not require or import a third-party SDK.
    class CustomLogger:
        def __init__(self, *args, **kwargs):
            self.forwarded_init = (args, kwargs)
    monkeypatch.setitem(sys.modules, "litellm.integrations.custom_logger",
                        SimpleNamespace(CustomLogger=CustomLogger))
    return _load("kry_litellm_callback")


@pytest.mark.parametrize("asynchronous", [False, True])
def test_hook_observes_each_callback_but_deduplicates_mints(monkeypatch, asynchronous):
    from kry import kry_mint
    module = _logger_module(monkeypatch)
    observations = []
    logger = module.KryLiteLLMLogger(usage_observer=observations.append)
    response = {"usage": {"total_tokens": 16}, "model": "claude-haiku-4-5-20251001"}
    kwargs = {"model": "claude-haiku-4-5-20251001", "litellm_call_id": "local-1", "cache_hit": False}
    def dispatch():
        if asynchronous:
            asyncio.run(logger.async_log_success_event(kwargs, response, None, None))
        else:
            logger.log_success_event(kwargs, response, None, None)
    dispatch()
    assert not kry_mint._MINT_LOG_PATH.exists()
    kwargs["cache_hit"] = True
    dispatch()
    dispatch()
    assert len(observations) == 3
    rows = [json.loads(row) for row in kry_mint._MINT_LOG_PATH.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["evidence_tier"] == "self_reported"
    assert rows[0]["tokens_saved"] == 16
    assert logger.observation_failures == 0


def test_observer_failure_is_sanitized_and_does_not_stop_mint(monkeypatch, caplog):
    from kry import kry_mint
    module = _logger_module(monkeypatch)
    def fail(observation):
        raise RuntimeError("SECRET_EXCEPTION")
    logger = module.KryLiteLLMLogger(usage_observer=fail)
    logger.log_success_event({"model": "claude-haiku-4-5-20251001", "litellm_call_id": "failure-1",
                              "cache_hit": True}, {"usage": {"total_tokens": 16}}, None, None)
    assert logger.observation_failures == 1
    assert "usage observation failed" in caplog.text
    assert "SECRET_EXCEPTION" not in caplog.text
    assert len(kry_mint._MINT_LOG_PATH.read_text().splitlines()) == 1


def test_async_observer_is_rejected_and_default_constructor_is_compatible(monkeypatch):
    module = _logger_module(monkeypatch)
    async def asynchronous(observation):
        pass
    with pytest.raises(TypeError, match="synchronous"):
        module.KryLiteLLMLogger(usage_observer=asynchronous)
    logger = module.KryLiteLLMLogger("retained-arg", retained_option=True)
    assert logger.forwarded_init == (("retained-arg",), {"retained_option": True})
    logger.log_success_event({}, {}, None, None)
    assert logger.observation_failures == 0


def test_sync_observer_returning_coroutine_is_reported_not_abandoned(monkeypatch, caplog):
    module = _logger_module(monkeypatch)
    async def result():
        pass
    logger = module.KryLiteLLMLogger(usage_observer=lambda observation: result())
    logger.log_success_event({}, {}, None, None)
    assert logger.observation_failures == 1
    assert "usage observation failed" in caplog.text
