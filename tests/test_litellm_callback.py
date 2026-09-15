"""The LiteLLM callback mints honest T0 cache_hit receipts and fails closed."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "kry_litellm_callback", ROOT / "scripts" / "kry_litellm_callback.py")
klc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(klc)


def _event(call_id="call-1", **over):
    kwargs = {"cache_hit": True, "model": "gh/claude-opus-4.8",
              "litellm_call_id": call_id, "response_cost": 0.0125}
    kwargs.update(over)
    return kwargs


RESP = {"usage": {"total_tokens": 1000}}


def _fresh_seen():
    klc._seen_ids.clear()
    klc._seen_order.clear()


def test_cache_hit_mints_t0_receipt_with_usage_tokens():
    _fresh_seen()
    import kry.kry_mint as km
    receipt = klc.mint_from_litellm(_event(), RESP)
    assert receipt is not None
    ok, errors = km.verify_chain()
    assert ok, errors
    import json
    row = json.loads(km._MINT_LOG_PATH.read_text().splitlines()[-1])
    assert row["event_type"] == "cache_hit"
    assert row["tokens_saved"] == 1000.0
    assert row["evidence_tier"] == "self_reported"      # the honest T0 label
    assert "litellm:call-1" in row["detail"]


def test_no_cache_hit_means_no_receipt():
    _fresh_seen()
    assert klc.mint_from_litellm(_event(cache_hit=False), RESP) is None
    assert klc.mint_from_litellm(_event(cache_hit=None), RESP) is None
    assert klc.mint_from_litellm({}, RESP) is None


def test_fail_closed_on_missing_or_malformed_fields():
    _fresh_seen()
    assert klc.mint_from_litellm(_event(model=""), RESP) is None
    assert klc.mint_from_litellm(_event(model=None), RESP) is None
    assert klc.mint_from_litellm(_event(litellm_call_id=""), RESP) is None
    assert klc.mint_from_litellm(_event(), {"usage": {}}) is None
    assert klc.mint_from_litellm(_event(), {"usage": {"total_tokens": 0}}) is None
    assert klc.mint_from_litellm(_event(), {"usage": {"total_tokens": True}}) is None
    assert klc.mint_from_litellm(_event(), None) is None
    assert klc.mint_from_litellm("not-a-dict", RESP) is None


def test_same_call_id_mints_once():
    _fresh_seen()
    import kry.kry_mint as km
    assert klc.mint_from_litellm(_event("replay-1"), RESP) is not None
    assert klc.mint_from_litellm(_event("replay-1"), RESP) is None
    rows = km._MINT_LOG_PATH.read_text().splitlines()
    assert len(rows) == 1


def test_object_shaped_response_and_prompt_completion_fallback():
    _fresh_seen()
    resp = SimpleNamespace(usage=SimpleNamespace(
        total_tokens=None, prompt_tokens=400, completion_tokens=200))
    fields = klc.receipt_fields_from_litellm(_event("obj-1"), resp)
    assert fields is not None and fields["tokens_saved"] == 600.0


def test_nan_cost_is_excluded_from_detail():
    _fresh_seen()
    fields = klc.receipt_fields_from_litellm(_event(response_cost=float("nan")), RESP)
    assert fields is not None and "cost_estimate" not in fields["detail"]


def test_never_raises_on_hostile_shapes():
    _fresh_seen()
    for hostile in (
        {"cache_hit": True, "model": 5, "litellm_call_id": "x"},
        {"cache_hit": True, "model": "m", "litellm_call_id": ["x"]},
    ):
        assert klc.mint_from_litellm(hostile, RESP) is None
    assert klc.mint_from_litellm(_event(), {"usage": "garbage"}) is None


def test_cache_hit_detail_carries_litellm_saved_cache_cost_as_context():
    _fresh_seen()
    fields = klc.receipt_fields_from_litellm(
        _event(standard_logging_object={"saved_cache_cost": 0.0421}), RESP)
    assert fields is not None and "litellm_saved_cache_cost=0.042100" in fields["detail"]
    nan = klc.receipt_fields_from_litellm(
        _event(standard_logging_object={"saved_cache_cost": float("nan")}), RESP)
    assert nan is not None and "saved_cache_cost" not in nan["detail"]


# ── Auto-router receipts ─────────────────────────────────────────────────────

ROUTED_RESP = {"usage": {"prompt_tokens": 1800, "completion_tokens": 500, "total_tokens": 2300}}


def _routed(call_id="route-1", metadata_key="litellm_metadata", decision=None, **over):
    decision = {"router_model_name": "auto", "routed_model": "claude-sonnet-5",
                "savings_baseline_model": "anthropic/claude-opus-5",
                "signals": ["SECRET PROMPT SIGNAL"], "matched_keyword": "SECRET KEYWORD",
                **(decision or {})}
    kwargs = {"cache_hit": False, "model": "claude-sonnet-5", "litellm_call_id": call_id,
              "litellm_params": {metadata_key: {"routing_decision": decision}},
              "standard_logging_object": {"autorouter_savings": 0.0105}}
    kwargs.update(over)
    return kwargs


def test_routed_request_mints_a_t0_short_circuit_receipt():
    _fresh_seen()
    import json

    import kry.kry_mint as km
    receipt = klc.mint_from_litellm(_routed(), ROUTED_RESP)
    assert receipt is not None
    ok, errors = km.verify_chain()
    assert ok, errors
    row = json.loads(km._MINT_LOG_PATH.read_text().splitlines()[-1])
    assert row["event_type"] == "short_circuit"
    assert row["tokens_saved"] == 500.0                   # output tokens, as the savings report counts them
    assert row["avoided_model"] == "anthropic/claude-opus-5"
    # The served model is not stored on the receipt; it nets its own price out of the credit:
    # 500 output tokens x earn rate 1.0 x (Opus 5 1.0 - Sonnet 5 0.4).
    assert row["earn_rate"] == 1.0
    assert row["kry_minted"] == pytest.approx(300.0)
    assert row["evidence_tier"] == "self_reported"
    assert "served=claude-sonnet-5" in row["detail"]
    assert "litellm_autorouter_savings=0.010500" in row["detail"]


def test_routing_decision_is_read_from_either_metadata_key():
    _fresh_seen()
    assert klc.route_fields_from_litellm(_routed(metadata_key="metadata"), ROUTED_RESP) is not None
    assert klc.route_fields_from_litellm(_routed(metadata_key="litellm_metadata"), ROUTED_RESP) is not None


def test_prompt_quoting_routing_fields_never_reach_the_receipt():
    _fresh_seen()
    fields = klc.route_fields_from_litellm(_routed(), ROUTED_RESP)
    assert fields is not None
    blob = repr(fields)
    assert "SECRET PROMPT SIGNAL" not in blob and "SECRET KEYWORD" not in blob


def test_no_router_receipt_without_a_real_cheaper_route():
    _fresh_seen()
    # no routing decision, or no baseline in it
    assert klc.route_fields_from_litellm(_routed(litellm_params={}), ROUTED_RESP) is None
    assert klc.route_fields_from_litellm(
        _routed(decision={"savings_baseline_model": None}), ROUTED_RESP) is None
    # the router's own internal calls (classifier, shadow eval) are not caller traffic
    internal = _routed()
    internal["litellm_params"]["litellm_metadata"]["internal_call_origin"] = "autorouter_classifier"
    assert klc.route_fields_from_litellm(internal, ROUTED_RESP) is None
    # served the baseline itself, with or without a provider prefix
    assert klc.route_fields_from_litellm(_routed(model="claude-opus-5"), ROUTED_RESP) is None
    # routed to a model that is not cheaper at kry's published prices
    assert klc.route_fields_from_litellm(
        _routed(model="claude-opus-5-fast", decision={"savings_baseline_model": "claude-sonnet-5"}),
        ROUTED_RESP) is None
    # a response-cache hit is a cache_hit receipt, never a route receipt
    assert klc.route_fields_from_litellm(_routed(cache_hit=True), ROUTED_RESP) is None


def test_router_receipts_fail_closed_on_malformed_events():
    _fresh_seen()
    assert klc.route_fields_from_litellm(_routed(), {"usage": {"completion_tokens": 0}}) is None
    assert klc.route_fields_from_litellm(_routed(), {"usage": {}}) is None
    assert klc.route_fields_from_litellm(_routed(model=""), ROUTED_RESP) is None
    assert klc.route_fields_from_litellm(_routed(litellm_call_id=""), ROUTED_RESP) is None
    assert klc.route_fields_from_litellm(_routed(litellm_params="garbage"), ROUTED_RESP) is None
    assert klc.route_fields_from_litellm(
        _routed(litellm_params={"litellm_metadata": {"routing_decision": "garbage"}}), ROUTED_RESP) is None
    assert klc.mint_from_litellm("not-a-dict", ROUTED_RESP) is None


def test_same_routed_call_mints_once():
    _fresh_seen()
    import kry.kry_mint as km
    assert klc.mint_from_litellm(_routed("route-replay"), ROUTED_RESP) is not None
    assert klc.mint_from_litellm(_routed("route-replay"), ROUTED_RESP) is None
    assert len(km._MINT_LOG_PATH.read_text().splitlines()) == 1
