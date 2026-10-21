"""Malformed supplied counts cannot hide behind a total or a routing path."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def callback():
    spec = importlib.util.spec_from_file_location("callback_boundaries", ROOT / "scripts" / "kry_litellm_callback.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(kind):
    if kind == "cache":
        return {"cache_hit": True, "model": "gh/claude-opus-4.8", "litellm_call_id": "boundary-cache"}
    return {"cache_hit": False, "model": "claude-sonnet-5", "litellm_call_id": "boundary-route",
            "litellm_params": {"litellm_metadata": {"routing_decision": {
                "savings_baseline_model": "anthropic/claude-opus-5"}}}}


def response(usage, object_shape):
    return SimpleNamespace(usage=SimpleNamespace(**usage)) if object_shape else {"usage": usage}


@pytest.mark.parametrize("usage", [
    {"total_tokens": 1000, "prompt_tokens": 1, "completion_tokens": 1},
    {"total_tokens": 0, "prompt_tokens": 400, "completion_tokens": 200},
    {"total_tokens": 600, "prompt_tokens": -100, "completion_tokens": 700},
    {"total_tokens": True, "prompt_tokens": 400, "completion_tokens": 200},
    {"total_tokens": "600", "prompt_tokens": 400, "completion_tokens": 200},
    {"total_tokens": 600.0, "prompt_tokens": 400, "completion_tokens": 200},
    {"total_tokens": 600, "prompt_tokens": True, "completion_tokens": 599},
    {"total_tokens": 600, "prompt_tokens": "400", "completion_tokens": 200},
    {"total_tokens": 600, "prompt_tokens": 400.0, "completion_tokens": 200},
    {"total_tokens": 600, "prompt_tokens": 400, "completion_tokens": True},
    {"total_tokens": -1, "prompt_tokens": 400, "completion_tokens": 200},
])
@pytest.mark.parametrize("object_shape", [False, True])
@pytest.mark.parametrize("kind", ["cache", "route"])
def test_supplied_invalid_or_contradictory_counts_never_mint(callback, usage, object_shape, kind):
    from kry import kry_mint as km

    kwargs, payload = event(kind), response(usage, object_shape)
    extract = callback.receipt_fields_from_litellm if kind == "cache" else callback.route_fields_from_litellm
    assert extract(kwargs, payload) is None
    assert callback.mint_from_litellm(kwargs, payload) is None
    assert not km._MINT_LOG_PATH.exists()


@pytest.mark.parametrize("usage", [
    {"total_tokens": 1, "prompt_tokens": 2},
    {"total_tokens": 1, "completion_tokens": 2},
])
@pytest.mark.parametrize("object_shape", [False, True])
@pytest.mark.parametrize("kind", ["cache", "route"])
def test_known_component_cannot_exceed_total(callback, usage, object_shape, kind):
    from kry import kry_mint as km

    kwargs, payload = event(kind), response(usage, object_shape)
    extract = callback.receipt_fields_from_litellm if kind == "cache" else callback.route_fields_from_litellm
    assert extract(kwargs, payload) is None
    assert callback.mint_from_litellm(kwargs, payload) is None
    assert not km._MINT_LOG_PATH.exists()


@pytest.mark.parametrize("usage,cache_tokens,route_tokens", [
    ({"total_tokens": 600}, 600, None),
    ({"total_tokens": 600, "prompt_tokens": None, "completion_tokens": None}, 600, None),
    ({"prompt_tokens": 400, "completion_tokens": 200}, 600, 200),
    ({"total_tokens": None, "prompt_tokens": 400, "completion_tokens": 200}, 600, 200),
    ({"total_tokens": 600, "prompt_tokens": 400, "completion_tokens": 200}, 600, 200),
    ({"total_tokens": 600, "prompt_tokens": 400}, 600, None),
    ({"completion_tokens": 200}, None, 200),
])
@pytest.mark.parametrize("object_shape", [False, True])
@pytest.mark.parametrize("kind", ["cache", "route"])
def test_supported_shapes_keep_value_tier_and_dedup(callback, usage, cache_tokens, route_tokens,
                                                   object_shape, kind):
    from kry import kry_mint as km

    kwargs, payload = event(kind), response(usage, object_shape)
    expected = cache_tokens if kind == "cache" else route_tokens
    receipt = callback.mint_from_litellm(kwargs, payload)
    if expected is None:
        assert receipt is None
        assert not km._MINT_LOG_PATH.exists()
    else:
        assert receipt is not None
        assert receipt.tokens_saved == expected
        assert receipt.evidence_tier == "self_reported"
        assert callback.mint_from_litellm(kwargs, payload) is None
        assert len(km._MINT_LOG_PATH.read_text().splitlines()) == 1
        assert km.verify_chain()[0]
