"""Router + truth tooling logic, verified with a fake transport (no Ollama).

Only the real Ollama HTTP call is left to validate on hardware; everything that decides
cache/holdout/routing, the log shapes, and the ground-truth aggregation is CI-covered.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "lab" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_CFG = json.loads((_ROOT / "lab" / "routes.example.json").read_text(encoding="utf-8"))


def _corpus():
    # repeats drive cache hits; ids are stable so holdout assignment is deterministic
    rows = []
    for i in range(200):
        cls = ["summarize", "code", "translate", "greet"][i % 4]
        rows.append({"id": f"r{i}", "request_class": cls, "prompt": f"{cls}-prompt-{i % 40}"})
    return rows


def test_router_produces_clean_logs_with_fake_transport():
    rt = _load("router")
    usage, truth = rt.process(_corpus(), _CFG, transport=rt.fake_transport)
    assert len(usage) == 200
    # no ground truth leaks into KRY's view
    for u in usage:
        assert "hit_paid" not in u
        assert u.get("cache_hit") or u.get("holdout")
        assert u["usage"]["completion_tokens"] >= 0
    # holdout records are real frontier calls; cache_hit records carry avoided_model
    holdouts = [u for u in usage if u.get("holdout")]
    hits = [u for u in usage if u.get("cache_hit")]
    assert holdouts and hits
    # a holdout's recorded model is the frontier ONLY when it genuinely needed it,
    # else the class's cheap local model — that paid-ness is the measured counterfactual
    local_models = {c["model"] for c in _CFG["classes"].values()}
    allowed = local_models | {_CFG["frontier_model"]}
    assert all(u["model"] in allowed for u in holdouts)
    assert not all(u["model"] == _CFG["frontier_model"] for u in holdouts)  # greet etc. stay cheap
    assert all(u["avoided_model"] == _CFG["frontier_model"] for u in hits)
    # truth has both holdout and audit sources, all booleans
    assert {t["source"] for t in truth} <= {"holdout", "audit"}
    assert all(isinstance(t["hit_paid"], bool) for t in truth)


def test_local_accounting_prefix_is_stripped_for_ollama_transport():
    rt = _load("router")
    assert rt.transport_model_name("local/qwen2.5:14b") == "qwen2.5:14b"
    assert rt.transport_model_name("gh/claude-opus-4.8") == "gh/claude-opus-4.8"


def test_router_is_deterministic_for_a_seed():
    rt = _load("router")
    u1, t1 = rt.process(_corpus(), _CFG, transport=rt.fake_transport)
    u2, t2 = rt.process(_corpus(), _CFG, transport=rt.fake_transport)
    assert u1 == u2 and t1 == t2


def test_cache_hits_reuse_first_token_counts():
    rt = _load("router")
    # two identical prompts (not holdout) -> second must reuse the first's token counts
    cfg = dict(_CFG, holdout_rate=0.0, audit_rate=0.0)
    reqs = [{"id": "a", "request_class": "code", "prompt": "same"},
            {"id": "b", "request_class": "code", "prompt": "same"}]
    usage, _ = rt.process(reqs, cfg, transport=rt.fake_transport)
    assert usage[0]["usage"] == usage[1]["usage"]


def test_compute_truth_aggregates_per_class():
    ct = _load("compute_truth")
    lines = [
        {"request_class": "summarize", "hit_paid": True, "source": "audit"},
        {"request_class": "summarize", "hit_paid": True, "source": "audit"},
        {"request_class": "summarize", "hit_paid": False, "source": "audit"},
        {"request_class": "greet", "hit_paid": False, "source": "audit"},
        {"request_class": "summarize", "hit_paid": True, "source": "holdout"},  # excluded by default
    ]
    out = ct.compute(lines, source="audit")
    assert abs(out["summarize"] - 2 / 3) < 1e-3   # holdout-source row excluded (rounded 4dp)
    assert out["greet"] == 0.0
    assert abs(ct.compute(lines, source="all")["summarize"] - 0.75) < 1e-9
