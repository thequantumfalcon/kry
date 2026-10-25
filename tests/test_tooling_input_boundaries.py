"""Explicit verifier configuration and read-only provider retry boundaries."""
from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import urllib.error
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("config", [
    "missing", "empty_path", "{", "{}", "null", '[]', '{"multipliers":null}',
    '{"multipliers":"bad"}', '{"multipliers":[]}', '{"multipliers":[true]}',
    '{"multipliers":["1"]}', '{"multipliers":[-1]}',
    '{"multipliers":[1e999]}',
])
def test_batch_bad_explicit_multipliers_fail_before_verdict(tmp_path, config):
    vector = json.loads((ROOT / "vectors/savings/valid/single_self_reported.json").read_text())
    inputs = tmp_path / "inputs.ndjson"
    inputs.write_text(json.dumps(vector["input"]) + "\n")
    multipliers = tmp_path / "multipliers.json"
    if config != "missing":
        multipliers.write_text(config)
    result = subprocess.run(
        [NODE, str(ROOT / "verifiers/js/cli.mjs"), "--batch", str(inputs),
         "" if config == "empty_path" else str(multipliers)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert result.stdout == "", "configuration failure must not emit an attestation verdict"
    assert "multiplier" in result.stderr.lower()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("explicit", [False, True])
def test_batch_defaults_and_valid_configuration_keep_mixed_verdicts(tmp_path, explicit):
    att = json.loads((ROOT / "vectors/savings/valid/single_self_reported.json").read_text())["input"]
    inputs = tmp_path / "inputs.ndjson"
    inputs.write_text(json.dumps(att) + "\n{}\n")
    command = [NODE, str(ROOT / "verifiers/js/cli.mjs"), "--batch", str(inputs)]
    if explicit:
        command.append(str(ROOT / "vectors/primitives/legal_multipliers.json"))
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "VALID\nINVALID\n"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("kind", ["invalid_utf8", "oversize"])
def test_batch_multiplier_bytes_are_bounded_and_utf8(tmp_path, kind):
    inputs = tmp_path / "inputs.ndjson"
    inputs.write_text("{}\n")
    config = tmp_path / "config.json"
    raw = (b'{"multipliers":[1],"note":"\xff"}' if kind == "invalid_utf8"
           else b'{"multipliers":[1]}' + b" " * (16 * 1024 * 1024))
    config.write_bytes(raw)
    result = subprocess.run(
        [NODE, str(ROOT / "verifiers/js/cli.mjs"), "--batch", str(inputs), str(config)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "multiplier" in result.stderr


@pytest.fixture
def fetch(monkeypatch):
    spec = importlib.util.spec_from_file_location("bounded_fetch", ROOT / "scripts/kry_or_fetch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sleeps = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    return module, sleeps


def unavailable(req, timeout):
    raise urllib.error.HTTPError(req.full_url, 404, "not ready", {}, io.BytesIO())


def test_fetch_never_sleeps_after_final_attempt(fetch):
    module, sleeps = fetch
    assert module.fetch_generation("synthetic", "unused", opener=unavailable) is None
    assert len(sleeps) == 3


def test_fetch_uses_bounded_jitter_between_attempts(fetch, monkeypatch):
    module, sleeps = fetch
    bounds = []

    def jitter(low, high):
        bounds.append((low, high))
        return high / 2

    import random
    monkeypatch.setattr(random, "uniform", jitter)
    module.fetch_generation("synthetic", "unused", backoff=20, opener=unavailable)
    assert bounds == [(0, 20), (0, 30), (0, 30)]
    assert sleeps == [10, 15, 15]


def test_fetch_caps_large_integer_backoff_without_overflow(fetch, monkeypatch):
    module, sleeps = fetch
    import random
    monkeypatch.setattr(random, "uniform", lambda low, high: high)
    assert module.fetch_generation("synthetic", "unused", retries=2,
                                   backoff=10**1000, opener=unavailable) is None
    assert sleeps == [30]


@pytest.mark.parametrize("header,expected", [
    ("5", [5]),
    ("Thu, 01 Jan 1970 00:16:45 GMT", [5]),
    ("31", []),
    ("9" * 5000, []),
    ("bad", [0]),
    ("-1", [0]),
    ("1.5", [0]),
    ("Thu, 01 Jan 1970 00:00:00 GMT", [0]),
])
def test_fetch_retry_after_respected_or_stops_within_wait_budget(fetch, monkeypatch, header, expected):
    module, sleeps = fetch
    calls = []
    monkeypatch.setattr(module.time, "time", lambda: 1000)

    def opener(req, timeout):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 429, "rate limited",
                                     {"Retry-After": header}, io.BytesIO())

    with pytest.raises(module.RateLimitError):
        module.fetch_generation("synthetic", "unused", retries=2, backoff=0, opener=opener)
    assert sleeps == expected
    assert len(calls) == (2 if expected else 1)


@pytest.mark.parametrize("kwargs", [
    {"retries": 0}, {"retries": -1}, {"retries": True}, {"retries": 1.5},
    {"backoff": -1}, {"backoff": True}, {"backoff": float("inf")},
    {"backoff": float("nan")},
])
def test_fetch_invalid_retry_parameters_refuse_before_request(fetch, kwargs):
    module, sleeps = fetch
    calls = []

    def opener(req, timeout):
        calls.append(req.full_url)
        return io.BytesIO(b'{"data":{"native_tokens_prompt":1}}')

    with pytest.raises(ValueError, match="retries|backoff"):
        module.fetch_generation("synthetic", "unused", opener=opener, **kwargs)
    assert calls == []
    assert sleeps == []


def test_fetch_success_and_nonretryable_error_do_not_sleep(fetch):
    module, sleeps = fetch

    def success(req, timeout):
        return io.BytesIO(b'{"data":{"native_tokens_prompt":1}}')

    assert module.fetch_generation("synthetic", "unused", opener=success) == {"native_tokens_prompt": 1}

    def denied(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO())

    with pytest.raises(urllib.error.HTTPError) as exc:
        module.fetch_generation("synthetic", "unused", opener=denied)
    assert exc.value.code == 401
    assert sleeps == []
