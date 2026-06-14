"""F1 (OpenRouter): fetch per-request generation records for reconciliation.

OpenRouter is the one displacement provider with a post-hoc per-request usage
record. The host stamps each provider_metered receipt's detail with
`/openrouter:<id>`; kry_or_fetch reads those ids and pulls the provider's own
token counts so kry_reconcile --mode per-request can match them. These tests pin
id-extraction, the native-count preference, and the async-flush polling — with an
injected opener so no network is touched.
"""
from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

_FETCH = Path(__file__).resolve().parents[1] / "scripts" / "kry_or_fetch.py"


def _load():
    spec = importlib.util.spec_from_file_location("kry_or_fetch_standalone", _FETCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_log(tmp_path, rows):
    log = tmp_path / "mint.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(log)


def test_extract_or_ids_only_provider_metered_with_or_ref(tmp_path):
    r = _load()
    log = _write_log(tmp_path, [
        {"evidence_tier": "provider_metered", "detail": "disp/or/x/p10/c20/openrouter:gen-AAA"},
        {"evidence_tier": "provider_metered", "detail": "disp/google/y/p5/c5"},  # no OR ref
        {"evidence_tier": "self_reported", "detail": "disp/local/z/openrouter:gen-NOPE"},  # not T1
        {"evidence_tier": "provider_metered", "detail": "disp/or/x/openrouter:gen-BBB"},
    ])
    assert r.extract_or_ids(log) == ["gen-AAA", "gen-BBB"]


def test_extract_or_ids_dedups_preserving_order(tmp_path):
    r = _load()
    log = _write_log(tmp_path, [
        {"evidence_tier": "provider_metered", "detail": "a/openrouter:gen-1"},
        {"evidence_tier": "provider_metered", "detail": "b/openrouter:gen-2"},
        {"evidence_tier": "provider_metered", "detail": "c/openrouter:gen-1"},  # dup
    ])
    assert r.extract_or_ids(log) == ["gen-1", "gen-2"]


def test_extract_or_ids_rejects_nonstandard_json_constants(tmp_path):
    r = _load()
    log = tmp_path / "mint.jsonl"
    log.write_text(
        '{"evidence_tier":"provider_metered","detail":"a/openrouter:gen-1","kry_minted":NaN}\n'
    , encoding="utf-8")

    with pytest.raises(ValueError, match="non-standard JSON constant NaN"):
        r.extract_or_ids(str(log))


def test_to_export_record_prefers_native_counts():
    r = _load()
    gen = {"id": "gen-1", "tokens_prompt": 11, "tokens_completion": 22,
           "native_tokens_prompt": 10, "native_tokens_completion": 20,
           "total_cost": 0.0001, "provider_name": "Groq"}
    rec = r.to_export_record(gen)
    assert rec["tokens_prompt"] == 10 and rec["tokens_completion"] == 20   # native preferred
    assert rec["total_cost"] == 0.0001


def test_to_export_record_falls_back_to_normalized():
    r = _load()
    gen = {"id": "gen-2", "tokens_prompt": 7, "tokens_completion": 3}  # no native
    rec = r.to_export_record(gen)
    assert rec["tokens_prompt"] == 7 and rec["tokens_completion"] == 3


def test_to_export_record_rejects_boolean_native_counts():
    r = _load()
    gen = {"id": "gen-bad", "native_tokens_prompt": True, "native_tokens_completion": 3}

    with pytest.raises(ValueError, match="native_tokens_prompt must be a non-negative JSON integer"):
        r.to_export_record(gen)


def test_to_export_record_rejects_string_normalized_counts():
    r = _load()
    gen = {"id": "gen-bad", "tokens_prompt": "7", "tokens_completion": 3}

    with pytest.raises(ValueError, match="tokens_prompt must be a non-negative JSON integer"):
        r.to_export_record(gen)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def read(self):
        return json.dumps(self._payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_fetch_generation_polls_then_succeeds():
    r = _load()
    calls = {"n": 0}

    def opener(req, timeout=30):
        calls["n"] += 1
        if calls["n"] == 1:                       # first poll: not flushed yet
            raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, io.BytesIO(b""))
        return _FakeResp({"data": {"id": "gen-1", "native_tokens_prompt": 12,
                                   "native_tokens_completion": 34}})

    got = r.fetch_generation("gen-1", "sk-or-test", retries=3, backoff=0.0, opener=opener)
    assert got is not None and got["native_tokens_prompt"] == 12
    assert calls["n"] == 2                          # polled twice (404 then 200)


def test_fetch_generation_gives_up_when_never_available():
    r = _load()

    def opener(req, timeout=30):
        raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, io.BytesIO(b""))

    got = r.fetch_generation("gen-x", "sk-or-test", retries=3, backoff=0.0, opener=opener)
    assert got is None


def test_fetch_generation_rejects_nonstandard_json_constants():
    r = _load()

    def opener(req, timeout=30):
        return _FakeResp({"data": {"id": "gen-bad", "native_tokens_prompt": float("nan")}})

    with pytest.raises(ValueError, match="non-standard JSON constant NaN"):
        r.fetch_generation("gen-bad", "sk-or-test", retries=1, backoff=0.0, opener=opener)


def test_export_json_rejects_nonstandard_numbers():
    r = _load()

    with pytest.raises(ValueError, match="Out of range float values"):
        r._json_dumps([{"id": "gen-bad", "total_cost": float("nan")}], indent=2)


def test_main_no_or_receipts_emits_empty_export(tmp_path, capsys):
    r = _load()
    log = _write_log(tmp_path, [
        {"evidence_tier": "provider_metered", "detail": "disp/google/y/p5/c5"},  # google, no OR ref
    ])
    rc = r.main([log])
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out) == []
