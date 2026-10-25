"""Batch failures must not bypass cooldowns or claim a complete provider export."""
from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def batch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("batch_fetch", ROOT / "scripts/kry_or_fetch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    log = tmp_path / "mint.jsonl"
    log.write_text("".join(json.dumps({"evidence_tier": "provider_metered",
                                      "detail": f"/openrouter:synthetic-{n}"}) + "\n"
                           for n in range(3)))
    calls, sleeps = [], []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    monkeypatch.setattr(module.time, "time", lambda: 1000)
    monkeypatch.setattr(module.random, "uniform", lambda low, high: 0)
    original = module.fetch_generation

    def install(response):
        def opener(request, timeout):
            calls.append(request.full_url)
            return response(request, len(calls))
        monkeypatch.setattr(module, "fetch_generation",
                            lambda gid, key: original(gid, key, opener=opener))

    return module, log, install, calls, sleeps


def record(index):
    return io.BytesIO(json.dumps({"data": {"id": f"synthetic-{index}",
                         "native_tokens_prompt": 1, "native_tokens_completion": 2}}).encode())


@pytest.mark.parametrize("header,attempts,waits", [
    ("120", 1, []), ("5", 4, [5, 5, 5]), (None, 4, [0, 0, 0]),
    ("bad", 4, [0, 0, 0]), ("Thu, 01 Jan 1970 00:18:40 GMT", 1, []),
    ("9" * 5000, 1, []),
])
@pytest.mark.parametrize("first_success", [False, True])
def test_batch_stops_on_unresolved_rate_limit(batch, capsys, header, attempts, waits, first_success):
    module, log, install, calls, sleeps = batch

    def response(request, count):
        if first_success and count == 1:
            return record(0)
        headers = {} if header is None else {"Retry-After": header}
        raise urllib.error.HTTPError(request.full_url, 429, "limited", headers, io.BytesIO())

    install(response)
    code = module.main([str(log), "--api-key", "unused"])
    output = capsys.readouterr()
    assert code == 1
    assert len(calls) == attempts + int(first_success)
    assert sleeps == waits
    assert len(json.loads(output.out)) == int(first_success)
    assert "rate limited" in output.err
    assert f"synthetic-{int(first_success)}" in output.err
    assert "not attempted" in output.err and "synthetic-2" in output.err
    assert "incomplete export" in output.err
    assert "expired" not in output.err


@pytest.mark.parametrize("failure", ["404", "transport", "no_counts"])
def test_incomplete_export_is_nonzero_and_not_called_expired(batch, tmp_path, capsys, failure):
    module, log, install, calls, _ = batch

    def response(request, count):
        if "synthetic-1" not in request.full_url:
            return record(0 if "synthetic-0" in request.full_url else 2)
        if failure == "404":
            raise urllib.error.HTTPError(request.full_url, 404, "not ready", {}, io.BytesIO())
        if failure == "transport":
            raise urllib.error.URLError("synthetic transport failure")
        return io.BytesIO(b'{"data":{}}')

    install(response)
    target = tmp_path / "partial.json"
    assert module.main([str(log), "--api-key", "unused", "--out", str(target)]) == 1
    output = capsys.readouterr()
    assert len(calls) == 6
    assert [r["id"] for r in json.loads(target.read_text())] == ["synthetic-0", "synthetic-2"]
    assert output.out == ""
    assert "incomplete export" in output.err and "synthetic-1" in output.err
    assert "expired" not in output.err
    assert "rate limited" not in output.err


def test_complete_batch_and_recovered_rate_limit_remain_successful(batch, capsys):
    module, log, install, calls, sleeps = batch

    def response(request, count):
        if count == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "limited",
                                         {"Retry-After": "5"}, io.BytesIO())
        return record(count - 2)

    install(response)
    assert module.main([str(log), "--api-key", "unused"]) == 0
    output = capsys.readouterr()
    assert [r["id"] for r in json.loads(output.out)] == [f"synthetic-{n}" for n in range(3)]
    assert len(calls) == 4 and sleeps == [5]
    assert "incomplete export" not in output.err
