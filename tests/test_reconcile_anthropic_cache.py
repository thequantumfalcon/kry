"""Reconciling provider_metered receipts against Anthropic usage that reports cache tokens.

Anthropic's input_tokens excludes cache reads and writes, so the whole prompt is their sum. Receipts
minted before that rule carry input_tokens alone; they still match and are counted separately.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_RECONCILE = Path(__file__).resolve().parents[1] / "scripts" / "kry_reconcile.py"


def _load():
    spec = importlib.util.spec_from_file_location("kry_reconcile_anthropic", _RECONCILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    import kry.kry_mint as km
    import kry.kry_token as kt
    log = tmp_path / "mint.jsonl"
    monkeypatch.setattr(km, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(kt, "_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(km, "_DECAY_STATE_PATH", tmp_path / "decay.json")
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = "0" * 64
    km._evidence_mints = {}
    km._decay_loaded = True
    kt._ledger_instance = kt.KRYLedger()
    return km, log


_ANTHROPIC_CALL = {"usage": {"input_tokens": 50, "cache_read_input_tokens": 1_000,
                             "cache_creation_input_tokens": 248, "output_tokens": 340}}


def _mint_t1(km, prompt):
    km.mint("short_circuit", 800, "a", evidence="a", avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[prompt, 340])


def test_anthropic_prompt_includes_cache_reads_and_writes():
    assert _load().normalize_provider_record(_ANTHROPIC_CALL) == (1_298, 340)


def test_receipt_with_the_whole_prompt_reconciles(isolated):
    km, log = isolated
    _mint_t1(km, 1_298)
    r = _load()
    res = r.reconcile(r.load_t1_receipts(str(log)), [_ANTHROPIC_CALL])
    assert res["verdict"] == "RECONCILED"
    assert res["matched_legacy_uncached_prompt"] == 0


def test_legacy_receipt_with_uncached_prompt_still_matches_and_is_counted(isolated):
    km, log = isolated
    _mint_t1(km, 50)
    r = _load()
    res = r.reconcile(r.load_t1_receipts(str(log)), [_ANTHROPIC_CALL])
    assert res["verdict"] == "RECONCILED"
    assert res["matched_legacy_uncached_prompt"] == 1


def test_legacy_fallback_does_not_apply_without_cache_tokens(isolated):
    km, log = isolated
    _mint_t1(km, 49)
    r = _load()
    export = [{"usage": {"input_tokens": 50, "output_tokens": 340}}]
    res = r.reconcile(r.load_t1_receipts(str(log)), export)
    assert res["verdict"] == "DISCREPANCY"
    assert res["matched_legacy_uncached_prompt"] == 0


def test_one_call_still_backs_only_one_receipt(isolated):
    km, log = isolated
    _mint_t1(km, 1_298)
    _mint_t1(km, 50)
    r = _load()
    res = r.reconcile(r.load_t1_receipts(str(log)), [_ANTHROPIC_CALL])
    assert res["matched"] == 1 and len(res["unmatched_receipts"]) == 1


def test_openai_style_prompts_are_not_double_counted():
    r = _load()
    chat = {"usage": {"prompt_tokens": 1_000, "completion_tokens": 20,
                      "prompt_tokens_details": {"cached_tokens": 800}}}
    responses = {"usage": {"input_tokens": 15_000, "output_tokens": 20,
                           "input_tokens_details": {"cached_tokens": 12_000, "cache_write_tokens": 3_000}}}
    assert r.normalize_provider_record(chat) == (1_000, 20)
    assert r.normalize_provider_record(responses) == (15_000, 20)
