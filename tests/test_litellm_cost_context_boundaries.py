"""Optional cost estimates cannot suppress otherwise eligible callback receipts."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def callback(monkeypatch):
    monkeypatch.setitem(sys.modules, "litellm.integrations.custom_logger",
                        SimpleNamespace(CustomLogger=None))
    spec = importlib.util.spec_from_file_location(
        "callback_cost_context", ROOT / "scripts" / "kry_litellm_callback.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(field, value):
    kwargs = {"cache_hit": True, "model": "claude-haiku-4-5-20251001",
              "litellm_call_id": "constructed-cost-context"}
    if field == "response_cost":
        kwargs[field] = value
    else:
        kwargs["standard_logging_object"] = {field: value}
    if field == "autorouter_savings":
        kwargs.update(cache_hit=False, model="claude-sonnet-5",
                      litellm_params={"litellm_metadata": {"routing_decision": {
                          "savings_baseline_model": "anthropic/claude-opus-5"}}})
    return kwargs


CONTEXT_FIELDS = [
    ("response_cost", "litellm_cost_estimate"),
    ("saved_cache_cost", "litellm_saved_cache_cost"),
    ("autorouter_savings", "litellm_autorouter_savings"),
]


@pytest.mark.parametrize("field,marker", CONTEXT_FIELDS)
@pytest.mark.parametrize("value", [10**400, -(10**400), float("inf"), float("-inf"),
                                  float("nan"), True, "0.01", None],
                         ids=["huge-positive", "huge-negative", "positive-infinity",
                              "negative-infinity", "nan", "boolean", "text", "null"])
def test_invalid_cost_context_is_omitted_without_losing_receipt(callback, field, marker, value):
    from kry import kry_mint

    kwargs = event(field, value)
    response = {"usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}}
    extract = (callback.route_fields_from_litellm if field == "autorouter_savings"
               else callback.receipt_fields_from_litellm)
    expected_tokens = 6 if field == "autorouter_savings" else 16
    fields = extract(kwargs, response)
    assert fields is not None and fields["tokens_saved"] == expected_tokens
    assert marker not in fields["detail"]
    receipt = callback.mint_from_litellm(kwargs, response)
    assert receipt is not None and receipt.tokens_saved == expected_tokens
    assert receipt.evidence_tier == "self_reported"
    assert marker not in receipt.detail
    assert callback.mint_from_litellm(kwargs, response) is None
    rows = [json.loads(line) for line in kry_mint._MINT_LOG_PATH.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["tokens_saved"] == expected_tokens
    assert kry_mint.verify_chain()[0]


@pytest.mark.parametrize("field,marker", CONTEXT_FIELDS)
@pytest.mark.parametrize("value", [0, 2, 0.0125, -0.01])
def test_finite_cost_context_is_retained(callback, field, marker, value):
    kwargs = event(field, value)
    response = {"usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}}
    extract = (callback.route_fields_from_litellm if field == "autorouter_savings"
               else callback.receipt_fields_from_litellm)
    fields = extract(kwargs, response)
    assert fields is not None
    assert f"{marker}={value:.6f}" in fields["detail"]
