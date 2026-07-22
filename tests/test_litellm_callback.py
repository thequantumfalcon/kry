"""The LiteLLM callback mints honest T0 cache_hit receipts and fails closed."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

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
