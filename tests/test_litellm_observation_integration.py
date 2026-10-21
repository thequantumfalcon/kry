"""Constructed responses exercise installed LiteLLM; no live provider traffic."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
from pathlib import Path

import pytest

litellm = pytest.importorskip("litellm", reason="litellm is not installed")
ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("usage", [{}, {"input_tokens": None}, {"input_tokens": 0}])
def test_upstream_defaults_are_not_mislabeled_original_provider_presence(usage):
    from litellm.llms.anthropic.chat.transformation import AnthropicConfig
    normalized = AnthropicConfig().calculate_usage({**usage, "output_tokens": 7}, None)
    result = _load("kry_litellm_observation").observe_litellm_usage({}, {"usage": normalized})
    assert result["source"] == "litellm_callback_normalized"
    assert result["usage"]["fields"]["prompt_tokens"] == {"state": "value", "value": 0}
    assert result["usage"]["fields"]["input_tokens"] == {"state": "absent"}


def test_mock_transport_dispatch_retains_categories_and_header_without_extra_mint(monkeypatch):
    import httpx
    from litellm.caching.caching import Cache
    from litellm.llms.custom_httpx.http_handler import HTTPHandler
    from kry import kry_attest, kry_mint

    callback = _load("kry_litellm_callback")
    model = "claude-haiku-4-5-20251001"
    raw = {"id": "msg_constructed", "type": "message", "role": "assistant", "model": model,
           "content": [{"type": "text", "text": "SECRET_OUTPUT"}], "stop_reason": "end_turn",
           "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 7,
               "cache_creation_input_tokens": 20, "cache_read_input_tokens": 40,
               "cache_creation": {"ephemeral_5m_input_tokens": 8, "ephemeral_1h_input_tokens": 12},
               "service_tier": "standard", "speed": None, "inference_geo": None}}
    requests = []
    observations = []

    def respond(request):
        assert not requests, "Response cache must prevent a second provider request"
        assert request.method == "POST"
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        payload = json.loads(request.content)
        assert payload["model"] == model and payload["max_tokens"] == 32
        requests.append(True)
        return httpx.Response(200, request=request, headers={"request-id": "req_constructed"}, json=raw)

    class Logger(callback.KryLiteLLMLogger):
        def __init__(self):
            super().__init__(usage_observer=observations.append)
            self.done = threading.Condition()
            self.completed = 0

        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            super().log_success_event(kwargs, response_obj, start_time, end_time)
            with self.done:
                self.completed += 1
                self.done.notify_all()

    logger = Logger()
    for name in ("input_callback", "success_callback", "failure_callback",
                 "_async_input_callback", "_async_success_callback", "_async_failure_callback"):
        monkeypatch.setattr(litellm, name, [])
    monkeypatch.setattr(litellm, "callbacks", [logger])
    monkeypatch.setattr(litellm, "cache", Cache(type="local"))
    monkeypatch.setattr(litellm, "num_retries", 0)
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False, follow_redirects=False) as client:
        for index in range(2):
            litellm.completion(model="anthropic/" + model,
                messages=[{"role": "user", "content": "SECRET_PROMPT"}], max_tokens=32,
                stream=False, caching=True, api_key="constructed-test-credential",
                api_base="https://api.anthropic.com/v1/messages", client=HTTPHandler(client=client),
                num_retries=0, litellm_call_id=f"observation-attempt-{index + 1}")
            with logger.done:
                assert logger.done.wait_for(lambda expected=index + 1: logger.completed >= expected, timeout=30)
            if index == 0:
                assert not kry_mint._MINT_LOG_PATH.exists()
    assert len(requests) == 1 and len(observations) == 2
    assert logger.observation_failures == 0
    for observation in observations:
        usage = observation["usage"]["fields"]
        assert usage["prompt_tokens"]["value"] == 160
        assert usage["completion_tokens"]["value"] == 7
        assert usage["total_tokens"]["value"] == 167
        assert usage["cache_read_input_tokens"]["value"] == 40
        assert usage["cache_creation_input_tokens"]["value"] == 20
        details = usage["prompt_tokens_details"]["fields"]["cache_creation_token_details"]["fields"]
        assert details["ephemeral_5m_input_tokens"]["value"] == 8
        assert details["ephemeral_1h_input_tokens"]["value"] == 12
        assert observation["actual_charge_usd"] is None
    assert observations[0]["provider_request_id_candidate"]["identity"]["sha256"] == hashlib.sha256(
        b"req_constructed").hexdigest()
    assert observations[0]["litellm_call_id"] != observations[1]["litellm_call_id"]
    assert "SECRET" not in json.dumps(observations)
    rows = [json.loads(line) for line in kry_mint._MINT_LOG_PATH.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["tokens_saved"] == 167
    assert rows[0]["evidence_tier"] == "self_reported"
    attestation = kry_attest.build_attestation().to_public_json()
    assert kry_attest.verify_attestation(attestation)[0]
    assert json.loads(attestation)["veracity"]["veracity_floor"] == 0.0
