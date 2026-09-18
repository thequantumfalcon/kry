"""The callback minted receipts inside a real LiteLLM process, not just from canned kwargs.

`tests/test_litellm_callback.py` feeds the extractor hand-built events. This file runs LiteLLM
itself — its cache, its callback dispatch, its usage objects — with `mock_response`, so no API key
and no spend are involved. Skipped where litellm is not installed.

Two facts this pins, both learned by running it: LiteLLM dispatches success events on a background
thread, so a short-lived caller must wait before reading the ledger; and a cache hit arrives with
`kwargs["cache_hit"] is True` while an ordinary call arrives with `None`, not `False`.
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

litellm = pytest.importorskip("litellm", reason="litellm is not installed")

_ROOT = Path(__file__).resolve().parents[1]
_WAIT_SECONDS = 30.0


def _load_callback():
    spec = importlib.util.spec_from_file_location(
        "kry_litellm_callback_integration", _ROOT / "scripts" / "kry_litellm_callback.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gateway(monkeypatch, tmp_path):
    """A LiteLLM with local response caching, the kry callback registered, and a private ledger."""
    import kry.kry_attest as ka
    import kry.kry_mint as km
    import kry.kry_token as kt
    from litellm.caching.caching import Cache

    log = tmp_path / "mint.jsonl"
    monkeypatch.setattr(km, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(ka, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(kt, "_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(km, "_DECAY_STATE_PATH", tmp_path / "decay.json")
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = "0" * 64
    km._evidence_mints = {}
    km._decay_loaded = True
    kt._ledger_instance = kt.KRYLedger()

    klc = _load_callback()
    klc._seen_ids.clear()
    klc._seen_order.clear()
    monkeypatch.setattr(litellm, "cache", Cache(type="local"))
    monkeypatch.setattr(litellm, "callbacks", [klc.kry_logger])   # the documented registration
    return klc, km, log


def _rows_when_ready(log: Path, expected: int) -> list[dict]:
    """LiteLLM logs success events on a background thread; wait for them rather than racing."""
    deadline = time.monotonic() + _WAIT_SECONDS
    rows: list[dict] = []
    while time.monotonic() < deadline:
        if log.exists():
            rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
            if len(rows) >= expected:
                return rows
        time.sleep(0.5)
    return rows


def test_a_real_cache_hit_mints_a_receipt(gateway):
    _klc, km, log = gateway
    messages = [{"role": "user", "content": "say ok"}]
    for _ in range(2):   # first call fills the cache, second is served from it
        litellm.completion(model="gpt-4o", messages=messages, mock_response="ok", caching=True)
    rows = _rows_when_ready(log, 1)
    assert len(rows) == 1, f"expected one receipt from the cache hit, got {rows}"
    assert rows[0]["event_type"] == "cache_hit"
    assert rows[0]["evidence_tier"] == "self_reported"
    assert rows[0]["tokens_saved"] > 0
    assert "litellm response-cache hit" in rows[0]["detail"]
    ok, errors = km.verify_chain()
    assert ok, errors


def test_a_routed_request_mints_and_never_copies_the_prompt(gateway):
    _klc, km, log = gateway
    secret = "PROMPT TEXT THAT MUST NOT LEAK"
    litellm.completion(
        model="claude-sonnet-5", messages=[{"role": "user", "content": "route me"}], mock_response="ok",
        metadata={"routing_decision": {"savings_baseline_model": "anthropic/claude-opus-5",
                                       "routed_model": "claude-sonnet-5", "signals": [secret]}})
    rows = _rows_when_ready(log, 1)
    assert len(rows) == 1, f"expected one routed receipt, got {rows}"
    assert rows[0]["event_type"] == "short_circuit"
    assert rows[0]["avoided_model"] == "anthropic/claude-opus-5"
    assert secret not in json.dumps(rows)
    ok, errors = km.verify_chain()
    assert ok, errors
