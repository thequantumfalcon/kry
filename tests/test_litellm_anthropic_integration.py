"""Constructed HTTP responses exercise the installed Anthropic adapter without API access."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
from pathlib import Path

import pytest

litellm = pytest.importorskip("litellm", reason="litellm is not installed")
_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_anthropic_cache_preserves_usage_and_separates_benchmark_from_cost(monkeypatch):
    import httpx
    from litellm.caching.caching import Cache
    from litellm.llms.custom_httpx.http_handler import HTTPHandler

    from kry import kry_attest, kry_mint

    callback = _load_script("kry_litellm_callback")
    pricing = _load_script("kry_anthropic_usage")
    model = "claude-haiku-4-5-20251001"
    raw = {
        "id": "msg_constructed", "type": "message", "role": "assistant",
        "model": model, "content": [{"type": "text", "text": "OK"}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 4,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                  "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
                  "service_tier": "standard", "server_tool_use": None,
                  "output_tokens_details": None, "inference_geo": None, "speed": None},
    }
    requests = []
    estimates = []

    def respond(request):
        assert not requests, "An identical repeat must not make a second provider request"
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        assert request.method == "POST"
        payload = json.loads(request.content)
        assert payload["model"] == model
        assert payload["max_tokens"] == 32
        requests.append(payload)
        estimates.append(pricing.estimate_anthropic_usage(raw, request_id="req_constructed"))
        return httpx.Response(200, request=request, headers={"request-id": "req_constructed"},
                              json=raw)

    class Observer(callback.KryLiteLLMLogger):
        def __init__(self):
            super().__init__()
            self.events = []
            self.done = threading.Condition()

        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            super().log_success_event(kwargs, response_obj, start_time, end_time)
            with self.done:
                self.events.append((kwargs["litellm_call_id"], kwargs.get("cache_hit") is True))
                self.done.notify_all()

    observer = Observer()
    # LiteLLM expands callbacks into these global lists; isolate them so this
    # test's observer cannot mint in a later test after callbacks is restored.
    for name in ("input_callback", "success_callback", "failure_callback",
                 "_async_input_callback", "_async_success_callback", "_async_failure_callback"):
        monkeypatch.setattr(litellm, name, [])
    monkeypatch.setattr(litellm, "cache", Cache(type="local"))
    monkeypatch.setattr(litellm, "callbacks", [observer])
    monkeypatch.setattr(litellm, "num_retries", 0)
    responses = []
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False,
                      follow_redirects=False) as client:
        for index in range(2):
            responses.append(litellm.completion(
                model="anthropic/" + model,
                messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                max_tokens=32, stream=False, caching=True,
                api_key="constructed-test-credential",
                api_base="https://api.anthropic.com/v1/messages",
                client=HTTPHandler(client=client), num_retries=0,
                litellm_call_id=f"constructed-attempt-{index + 1}",
            ))
            with observer.done:
                assert observer.done.wait_for(
                    lambda expected=index + 1: len(observer.events) >= expected, timeout=30)
            if index == 0:
                assert not kry_mint._MINT_LOG_PATH.exists()
    assert len(requests) == 1
    assert observer.events == [("constructed-attempt-1", False), ("constructed-attempt-2", True)]
    first, cached = responses
    assert first.id == cached.id
    assert first.choices[0].message.content == cached.choices[0].message.content == "OK"
    assert first.usage.prompt_tokens == cached.usage.prompt_tokens == 12
    assert first.usage.completion_tokens == cached.usage.completion_tokens == 4
    assert first.usage.total_tokens == cached.usage.total_tokens == 16
    assert first._hidden_params["additional_headers"]["llm_provider-request-id"] == "req_constructed"
    assert estimates[0]["message_id"] == "msg_constructed"
    assert estimates[0]["request_id"] == "req_constructed"
    assert estimates[0]["total_estimate_usd"] == "0.000032"
    assert estimates[0]["actual_charge_usd"] is None
    rows = [json.loads(line) for line in kry_mint._MINT_LOG_PATH.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["event_type"] == "cache_hit"
    assert rows[0]["evidence_tier"] == "self_reported"
    assert rows[0]["tokens_saved"] == 16
    assert rows[0]["evidence_hash"] == hashlib.sha256(
        b"litellm:constructed-attempt-2").hexdigest()
    assert rows[0]["usd_equivalent"] == 0.000080
    assert kry_mint.verify_chain()[0]
    attestation = kry_attest.build_attestation().to_public_json()
    assert kry_attest.verify_attestation(attestation)[0]
    assert json.loads(attestation)["veracity"]["veracity_floor"] == 0.0
