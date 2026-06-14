#!/usr/bin/env python3
"""Lab Phase 1 — the router/earner that produces the REAL corpus.

Runs on Node A. For each request it: checks a cache; with a small probability forces a
HOLDOUT (a real frontier call — KRY's economical 2% measurement); otherwise serves from
cache or routes to a local node. It writes:

  usage_real.jsonl  — KRY's view (no ground truth leaks in): cache_hit / holdout records
  truth_full.jsonl  — your INDEPENDENT ground truth: did the request genuinely need the
                      expensive model? Judged on a separate `audit` sample (and on every
                      holdout), so Test 1's oracle is independent of KRY's 2% holdout.

The Ollama HTTP call and the quality `judge` are INJECTABLE. Defaults: a real Ollama
transport (validate on your hardware) and a config-probability judge (for --dry-run and
CI). On the cluster, pass a real judge (e.g. compare the local answer to the frontier's).
Pure stdlib.

Config (routes.json) — see lab/routes.example.json. Corpus: JSONL of
{"id","request_class","prompt"} (repeat prompts to create real cache hits).

Usage:
    python lab/router.py --config lab/routes.example.json --corpus prompts.jsonl \\
        --out usage_real.jsonl --truth-out truth_full.jsonl
    python lab/router.py --config ... --corpus ... --dry-run        # no Ollama needed
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kry.kry_baseline import is_holdout  # noqa: E402


# ── injectable transport + judge (defaults below; override on the cluster) ─────

def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant {value} is not allowed")


def _json_loads(raw):
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return json.loads(raw, parse_constant=_reject_json_constant)


def _json_dumps(value, **kwargs) -> str:
    return json.dumps(value, allow_nan=False, **kwargs)


def _json_token(value, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative JSON integer")
    return value


def transport_model_name(model: str) -> str:
    """Model name to send to the serving backend after KRY accounting prefixes."""
    return model.removeprefix("local/")


def ollama_transport(node_url: str, model: str, prompt: str):
    """Real Ollama call. Returns (response_text, prompt_tokens, completion_tokens).
    Validate on your hardware — not exercised by CI."""
    body = _json_dumps({"model": transport_model_name(model), "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(node_url.rstrip("/") + "/api/generate", body,
                                 {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = _json_loads(r.read())
    return (d.get("response", ""),
            _json_token(d.get("prompt_eval_count", 0), "prompt_eval_count"),
            _json_token(d.get("eval_count", 0), "eval_count"))


def fake_transport(node_url: str, model: str, prompt: str):
    """Deterministic stand-in for --dry-run / CI (no network)."""
    return f"[{transport_model_name(model)}] echo", max(1, len(prompt) // 4), 50


def prob_judge(request_class: str, prompt: str, transport, cfg, rng) -> bool:
    """Default ground-truth judge: draw from the configured per-class true paid-rate.
    On the cluster, replace with a real quality check (e.g. does the cheap answer match
    the frontier's?) — that is the honest oracle; the prob is only for dry-run/CI."""
    threshold = float(cfg.get("true_paid_prob", {}).get(request_class, 0.0))
    if not math.isfinite(threshold):
        raise ValueError("true_paid_prob must be finite")
    return rng.random() < threshold


def _tokset(s: str) -> set:
    return set(s.lower().split())


def frontier_compare_judge(request_class: str, prompt: str, transport, cfg, rng) -> bool:
    """A REAL (heuristic) judge for the cluster: serve the prompt on BOTH the cheap route
    and the frontier, and call it 'needed the frontier' (hit_paid) when the cheap answer
    diverges from the frontier's beyond `judge_threshold` (Jaccard token overlap). This
    costs two calls per judged request — the honest price of ground truth. It is a
    heuristic proxy for quality; tune `judge_threshold` and spot-check it. For a stronger
    oracle, swap in a semantic/grader model."""
    route = cfg["classes"][request_class]
    cheap, _, _ = transport(cfg["nodes"][route["node"]], route["model"], prompt)
    front, _, _ = transport(cfg["nodes"][cfg["frontier_node"]], cfg["frontier_model"], prompt)
    a, b = _tokset(cheap), _tokset(front)
    overlap = len(a & b) / len(a | b) if (a | b) else 1.0
    return overlap < _finite_rate(cfg.get("judge_threshold", 0.6), "judge_threshold")


JUDGES = {"prob": prob_judge, "frontier-compare": frontier_compare_judge}


# ── core (fully testable) ─────────────────────────────────────────────────────

def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _finite_rate(value, field: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


def _tokens_pair(pt, ct) -> tuple[int, int]:
    return _json_token(pt, "prompt_tokens"), _json_token(ct, "completion_tokens")


def process(requests, cfg, *, transport=ollama_transport, judge=prob_judge):
    """Return (usage_lines, truth_lines). No ground truth enters usage_lines."""
    frontier = cfg["frontier_model"]
    f_url = cfg["nodes"][cfg["frontier_node"]]
    holdout_rate = _finite_rate(cfg.get("holdout_rate", 0.02), "holdout_rate")
    audit_rate = _finite_rate(cfg.get("audit_rate", 0.25), "audit_rate")
    rng = random.Random(int(cfg.get("seed", 7)))
    cache: dict = {}
    usage: list = []
    truth: list = []

    for r in requests:
        rid, cls, prompt = r["id"], r["request_class"], r["prompt"]
        key = _sha(prompt)
        if is_holdout(rid, holdout_rate):
            # Holdout = serve via the BASELINE policy (no optimization) and record WHICH
            # model it actually needed. The judge decides if the cheap answer was good
            # enough; if not, the baseline escalates to the (paid) frontier. So the
            # recorded model's paid-ness == the real counterfactual the savings report
            # reads (spend_cost(model) > 0), not "we always called the frontier".
            hit_paid = bool(judge(cls, prompt, transport, cfg, rng))
            if hit_paid:
                _, pt, ct = transport(f_url, frontier, prompt)
                pt, ct = _tokens_pair(pt, ct)
                model = frontier
            else:
                route = cfg["classes"][cls]
                _, pt, ct = transport(cfg["nodes"][route["node"]], route["model"], prompt)
                pt, ct = _tokens_pair(pt, ct)
                model = route["model"]                              # local/free — not paid
            usage.append({"id": rid, "request_class": cls, "holdout": True,
                          "model": model, "usage": {"prompt_tokens": pt, "completion_tokens": ct}})
            truth.append({"id": rid, "request_class": cls, "hit_paid": hit_paid, "source": "holdout"})
            cache.setdefault(key, (pt, ct))
            continue
        if key in cache:
            pt, ct = cache[key]
        else:
            route = cfg["classes"][cls]
            _, pt, ct = transport(cfg["nodes"][route["node"]], route["model"], prompt)
            pt, ct = _tokens_pair(pt, ct)
            cache[key] = (pt, ct)
        usage.append({"id": rid, "request_class": cls, "cache_hit": True,
                      "avoided_model": frontier, "usage": {"prompt_tokens": pt, "completion_tokens": ct}})
        # independent ground-truth audit (separate sample, frontier-judged, truth only)
        if rng.random() < audit_rate:
            truth.append({"id": rid, "request_class": cls,
                          "hit_paid": bool(judge(cls, prompt, transport, cfg, rng)), "source": "audit"})
    return usage, truth


def _read_jsonl(path: str) -> list:
    return [_json_loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="KRY lab router/earner — produces the real corpus")
    p.add_argument("--config", required=True)
    p.add_argument("--corpus", required=True, help="JSONL of {id,request_class,prompt}")
    p.add_argument("--out", default="usage_real.jsonl")
    p.add_argument("--truth-out", default="truth_full.jsonl")
    p.add_argument("--dry-run", action="store_true", help="use the fake transport (no Ollama)")
    p.add_argument("--judge", choices=list(JUDGES), default="prob",
                   help="ground-truth judge: 'prob' (dry-run stand-in) or 'frontier-compare' "
                        "(real: compares cheap vs frontier answers; costs 2 calls/judged req)")
    args = p.parse_args(argv)

    cfg = _json_loads(Path(args.config).read_text(encoding="utf-8"))
    requests = _read_jsonl(args.corpus)
    transport = fake_transport if args.dry_run else ollama_transport
    usage, truth = process(requests, cfg, transport=transport, judge=JUDGES[args.judge])

    Path(args.out).write_text("\n".join(_json_dumps(u) for u in usage) + "\n", encoding="utf-8")
    Path(args.truth_out).write_text("\n".join(_json_dumps(t) for t in truth) + "\n", encoding="utf-8")
    ch = sum(1 for u in usage if u.get("cache_hit"))
    ho = sum(1 for u in usage if u.get("holdout"))
    print(f"wrote {len(usage)} usage ({ch} cache_hit, {ho} holdout) -> {args.out}", file=sys.stderr)
    print(f"wrote {len(truth)} truth records -> {args.truth_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
