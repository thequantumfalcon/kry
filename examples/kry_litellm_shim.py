#!/usr/bin/env python3
"""KRY drop-in shadow shim for LiteLLM — measure cheap-vs-frontier savings on YOUR real traffic, read-only.

DEPLOYMENT TEMPLATE (runs in YOUR infra). Requires `pip install litellm`. The KRY core it imports
(`kry_shadow_emitter`) is pure stdlib. This is MEASURE-ONLY: it fires AFTER your response is served, never changes
or delays it, and writes DIGEST-ONLY rows (no prompts/responses leave your boundary).

WHAT IT DOES, per request on your checkable slice (you decide via `is_checkable`):
  1. read the real served call's model / tokens / cost (LiteLLM gives `response_cost`),
  2. run a cheap-shadow model on the same prompt (via your already-configured LiteLLM),
  3. run YOUR deterministic check on the cheap output (your unit tests / SQL-exec / JSON-schema — NOT a model judge),
  4. if the cheap output PASSES, record retained dollars (frontier_cost − cheap_cost) as a digest-only row, net-not-gross.
Output: `kry_shadow_rows.jsonl` → hand to `scripts/kry_verify` for a stranger-verifiable savings receipt.

SETUP (~5 min):
  1. `pip install litellm`; put `scripts/kry_shadow_emitter.py` on PYTHONPATH.
  2. Fill in the three CONFIG hooks below: CHEAP_MODEL, `is_checkable(...)`, `deterministic_check(...)`.
  3. Register: `litellm.callbacks = [KryShadowLogger()]` (SDK) or add it in your LiteLLM proxy config.
  4. Run your traffic. Rows land in `kry_shadow_rows.jsonl`. Verify them with `scripts/kry_verify`.

  python3 examples/kry_litellm_shim.py --selftest   # mock — no litellm, no network — proves the emit flow

PRIVACY: only digests + counts + costs are written. Raw prompt/response never persist (the emitter's privacy gate
rejects them). DELIVERABLE, not a claim: it measures; it does not assert savings until your check confirms them.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from kry_shadow_emitter import emit_row  # pure stdlib

# ----- CONFIG: the three hooks YOU fill in -----------------------------------
CHEAP_MODEL = os.environ.get("KRY_CHEAP_MODEL", "claude-haiku-4-5-20251001")
ROWS_PATH = Path(os.environ.get("KRY_ROWS_PATH", "kry_shadow_rows.jsonl"))


def is_checkable(request_messages, served_model) -> str | None:
    """Return a checkable_slice tag if this request has a deterministic check, else None.
    REPLACE with your own rule (e.g. route/tag == code, or the request targets your SQL agent)."""
    return None  # default: nothing is measured until you opt a slice in


def deterministic_check(request_messages, candidate_output) -> bool:
    """Return True iff `candidate_output` is objectively correct by a DETERMINISTIC gate — your unit tests,
    SQL execute-to-correct-result, or JSON-schema+exact-match. NEVER a model judge or semantic similarity.
    REPLACE with your real check."""
    raise NotImplementedError("plug in your deterministic check (tests / SQL-exec / schema)")
# -----------------------------------------------------------------------------


def _cheap_shadow(messages):
    """Run the cheap model on the same prompt via the customer's LiteLLM. Returns (text, in_tok, out_tok, cost)."""
    import litellm  # customer infra has it
    r = litellm.completion(model=CHEAP_MODEL, messages=messages, max_tokens=900)
    u = r.get("usage", {}) if isinstance(r, dict) else r.usage
    text = r["choices"][0]["message"]["content"] if isinstance(r, dict) else r.choices[0].message.content
    cost = litellm.completion_cost(completion_response=r)
    # `u` is a dict (litellm dict response) or a usage object — read uniformly so a falsy 0 on an
    # object path can't fall through to `u.get(...)` (objects have no .get -> AttributeError).
    pt = u.get("prompt_tokens", 0) if isinstance(u, dict) else getattr(u, "prompt_tokens", 0)
    ct = u.get("completion_tokens", 0) if isinstance(u, dict) else getattr(u, "completion_tokens", 0)
    return text or "", pt or 0, ct or 0, cost


def _process(kwargs, response_obj, *, cheap_fn=_cheap_shadow):
    """Core flow (stdlib + the injected cheap_fn) — testable without litellm. Returns a row or None."""
    messages = kwargs.get("messages") or []
    served_model = (response_obj or {}).get("model") or kwargs.get("model")
    slice_tag = is_checkable(messages, served_model)
    if not slice_tag:
        return None
    served_cost = kwargs.get("response_cost")
    u = (response_obj or {}).get("usage", {}) or {}
    ctext, cit, cot, ccost = cheap_fn(messages)
    cpass = bool(deterministic_check(messages, ctext))
    row = emit_row(
        frame_id=kwargs.get("litellm_call_id", "req"), request_id=kwargs.get("litellm_call_id", "req"),
        requested_model=kwargs.get("model"), served_model=served_model,
        measurement_class="deployable_validated", correctness_source="deployable",
        cheap_fast_correct=cpass, deployable_validator_pass=cpass,
        cheap_fast_cost_usd=ccost, frontier_holdout_cost_usd=served_cost,
        cheap_fast_output_tokens=cot, frontier_holdout_output_tokens=u.get("completion_tokens"),
        response_cost_usd=served_cost, provider_cost_source="provider_receipt",
        checkable_slice=slice_tag, deterministic_check_kind="customer_deterministic",
        deterministic_check_receipt=f"{slice_tag}:{'pass' if cpass else 'fail'}",
        latency_class="background")
    return row


def _append(row):
    with open(ROWS_PATH, "a", buffering=1, encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


try:
    from litellm.integrations.custom_logger import CustomLogger

    class KryShadowLogger(CustomLogger):
        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            try:
                row = _process(kwargs, response_obj)
                if row:
                    _append(row)
            except Exception:
                pass  # measure-only: a shim error must never affect the customer's serving path
except Exception:  # litellm not installed (e.g. during --selftest) — the core flow is still usable
    KryShadowLogger = None  # type: ignore


def _selftest():
    """Mock the served call + cheap-shadow + check — no litellm, no network. Proves the emit flow + privacy."""
    global deterministic_check, is_checkable
    is_checkable = lambda m, s: "code_executable"          # opt everything in
    deterministic_check = lambda m, out: out == "def add(a,b): return a+b"  # a real deterministic gate
    kwargs = {"litellm_call_id": "test-1", "model": "best/code",
              "messages": [{"role": "user", "content": "add two ints"}], "response_cost": 0.012}
    response_obj = {"model": "frontier/opus", "usage": {"completion_tokens": 120}}
    # PASS case: cheap returns the correct function
    row = _process(kwargs, response_obj, cheap_fn=lambda m: ("def add(a,b): return a+b", 30, 40, 0.0008))
    assert row and row["p0_pass"] is True and row["measured_row_value_usd"] > 0
    assert "messages" not in row and "prompt" not in row and "response" not in row  # privacy
    # FAIL case: cheap returns wrong code -> $0 (net-not-gross gate fires)
    row2 = _process(kwargs, response_obj, cheap_fn=lambda m: ("def add(a,b): return a-b", 30, 40, 0.0008))
    assert row2 and row2["p0_pass"] is False and row2["measured_row_value_usd"] == 0.0
    print("kry_litellm_shim self-test: PASS (mock; emit flow + net-not-gross + privacy gate verified)")
    print(f"  pass-row value: ${row['measured_row_value_usd']:.4f}   fail-row value: ${row2['measured_row_value_usd']:.4f}")


if __name__ == "__main__":
    _selftest()
