"""Strict JSON and finite-number boundaries for lab evidence artifacts."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "lab" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_router_jsonl_rejects_nonstandard_constants(tmp_path):
    rt = _load("router")
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text('{"id":"r1","request_class":"code","prompt":NaN}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="non-standard JSON constant NaN"):
        rt._read_jsonl(str(corpus))


def test_router_provider_response_rejects_nonstandard_constants(monkeypatch):
    rt = _load("router")

    class Resp:
        def read(self):
            return b'{"response":"ok","prompt_eval_count":NaN,"eval_count":1}'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr(rt.urllib.request, "urlopen", lambda *args, **kwargs: Resp())

    with pytest.raises(ValueError, match="non-standard JSON constant NaN"):
        rt.ollama_transport("http://node", "local/model", "prompt")


def test_router_provider_tokens_must_be_json_integers(monkeypatch):
    rt = _load("router")

    class Resp:
        def read(self):
            return b'{"response":"ok","prompt_eval_count":true,"eval_count":1}'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr(rt.urllib.request, "urlopen", lambda *args, **kwargs: Resp())

    with pytest.raises(ValueError, match="prompt_eval_count must be a non-negative JSON integer"):
        rt.ollama_transport("http://node", "local/model", "prompt")


def test_router_process_rejects_nonfinite_transport_tokens():
    rt = _load("router")
    cfg = {
        "frontier_model": "gh/frontier",
        "frontier_node": "frontier",
        "nodes": {"local": "http://local", "frontier": "http://frontier"},
        "classes": {"code": {"node": "local", "model": "local/code"}},
        "holdout_rate": 0.0,
        "audit_rate": 0.0,
    }
    reqs = [{"id": "r1", "request_class": "code", "prompt": "same"}]

    def bad_transport(node_url, model, prompt):
        return "ok", float("nan"), 1

    with pytest.raises(ValueError, match="prompt_tokens must be a non-negative JSON integer"):
        rt.process(reqs, cfg, transport=bad_transport)


def test_router_frontier_compare_rejects_nonfinite_threshold():
    rt = _load("router")
    cfg = {
        "frontier_model": "gh/frontier",
        "frontier_node": "frontier",
        "nodes": {"local": "http://local", "frontier": "http://frontier"},
        "classes": {"code": {"node": "local", "model": "local/code"}},
        "judge_threshold": "NaN",
    }

    def transport(node_url, model, prompt):
        return "same answer", 1, 1

    with pytest.raises(ValueError, match="judge_threshold must be finite"):
        rt.frontier_compare_judge("code", "prompt", transport, cfg, None)


def test_router_output_rejects_nonstandard_numbers():
    rt = _load("router")

    with pytest.raises(ValueError, match="Out of range float values"):
        rt._json_dumps({"usage": {"prompt_tokens": float("nan")}})


def test_compute_truth_rejects_nonstandard_constants():
    ct = _load("compute_truth")

    with pytest.raises(ValueError, match="non-standard JSON constant Infinity"):
        ct._json_loads('{"request_class":"code","hit_paid":Infinity,"source":"audit"}')


def test_holdout_truth_check_rejects_nonfinite_truth_rates():
    hc = _load("holdout_truth_check")
    report = {"code": {"holdout_n": 30, "holdout_paid_n": 15, "p_hat": 0.5}}

    with pytest.raises(ValueError, match="truth rate for code must be finite"):
        hc.coverage(report, {"code": float("inf")})


def test_energy_report_rejects_nonstandard_constants():
    er = _load("energy_report")

    with pytest.raises(ValueError, match="non-standard JSON constant NaN"):
        er._json_loads('{"nodes":{"a":{"tokens":NaN,"energy_wh":1}}}')


def test_energy_report_rejects_nonfinite_measurements():
    er = _load("energy_report")

    with pytest.raises(ValueError, match="a.tokens must be finite"):
        er.report({"nodes": {"a": {"tokens": float("nan"), "energy_wh": 1}}})
