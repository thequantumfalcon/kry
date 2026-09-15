#!/usr/bin/env python3
"""kry_litellm_callback — mint kry receipts from LiteLLM's callback surface.

LiteLLM (gateway/proxy) exposes a documented per-request hook: a CustomLogger
subclass receives every completed call with `kwargs["cache_hit"]` (its response
cache answered — the provider call did NOT happen) and `kwargs["response_cost"]`
(LiteLLM's price-table estimate). This module turns each response-cache hit into
a kry `cache_hit` receipt, so a proxy's claimed cache savings become a
tamper-evident, stranger-verifiable ledger instead of a dashboard number.

THE HONEST EVIDENCE BOUNDARY (read before quoting numbers):
  * Every receipt minted here is tier `self_reported` (T0). The cache-hit signal
    comes from the operator's own gateway — no external party witnesses it, so
    the attestation's `veracity_floor` correctly stays 0.0 until receipts are
    reconciled or anchored by stronger tiers. This integration proves the ledger
    of claimed hits is INTACT and honestly priced; it cannot prove the hits
    happened. That is kry's integrity-vs-veracity line, working as designed.
  * `cache_hit` is LiteLLM's RESPONSE cache, not provider-side prompt caching
    (`cached_tokens`); prompt-cache savings need a separate signal.
  * `response_cost` is LiteLLM's estimate from its price table (may be absent).
    It is recorded in the receipt detail as context, never used as evidence; so are
    LiteLLM's `saved_cache_cost` and `autorouter_savings` figures.

AUTO-ROUTER RECEIPTS: when LiteLLM's auto-router sends a request to a cheaper model,
the request metadata carries `routing_decision.savings_baseline_model`, the model the
router measures savings against. That becomes a T0 `short_circuit` receipt (the event
the savings report mints for a displacement): `avoided_model` = the baseline,
`served_model` = the model that ran, `tokens_saved` = output tokens, valued at kry's
published price difference. The counterfactual is the operator's router configuration,
not an observed call. LiteLLM's own figure is signed and prices input and cache tokens
too, so the two need not agree. The decision's `signals` and keyword fields quote the
caller's prompt and are never copied. The router's internal calls are skipped.

Deliberately dict-based and stdlib-only: the extractor operates on the plain
kwargs/response shapes LiteLLM already passes, so it is testable without
litellm installed; the CustomLogger subclass is defined only when litellm is
importable. Imports only kry.kry_mint and kry.kry_token (the package's stdlib core).

Use with the LiteLLM proxy (litellm_config.yaml):

    litellm_settings:
      callbacks: kry_litellm_callback.kry_logger

or in Python:

    import litellm
    from kry_litellm_callback import KryLiteLLMLogger
    litellm.callbacks = [KryLiteLLMLogger()]

Then attest + verify as usual: kry_attest builds the public attestation, and a
stranger runs `python3 scripts/kry_verify.py attestation.json` (or the JS
verifier) offline. Publish a chain anchor to make re-mints/truncation evident.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Optional

# In-process replay guard: LiteLLM may retry/replay a logging event; one call id
# mints at most once per process. (Cross-process dedup is not attempted for T0
# receipts — duplicates would be the operator's own ledger inflating itself,
# which the magnitude/conservation checks price but cannot disprove; that is
# exactly what the T0 label discloses.)
_SEEN_MAX = 4096
_seen_ids: set = set()
_seen_order: deque = deque()


def _usage_total_tokens(response_obj: Any) -> Optional[int]:
    """Total tokens of the served (cached) response — the size of the avoided call."""
    usage = None
    if isinstance(response_obj, dict):
        usage = response_obj.get("usage")
    elif response_obj is not None:
        usage = getattr(response_obj, "usage", None)
    if usage is None:
        return None
    def _field(name: str):
        if isinstance(usage, dict):
            return usage.get(name)
        return getattr(usage, name, None)
    total = _field("total_tokens")
    if isinstance(total, bool) or not isinstance(total, int):
        # fall back to prompt + completion if total is absent
        p, c = _field("prompt_tokens"), _field("completion_tokens")
        if (isinstance(p, int) and not isinstance(p, bool)
                and isinstance(c, int) and not isinstance(c, bool)):
            total = p + c
        else:
            return None
    return total if total > 0 else None


def _finite(value: Any) -> Optional[float]:
    """A real, finite number, or None (bools, NaN and infinities excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def _standard_logging_number(kwargs: dict, key: str) -> Optional[float]:
    """A numeric field of LiteLLM's standard logging payload, recorded as context only."""
    payload = kwargs.get("standard_logging_object")
    return _finite(payload.get(key)) if isinstance(payload, dict) else None


def _completion_tokens(response_obj: Any) -> Optional[int]:
    """Output tokens of the served response: what a cheaper route saves on."""
    usage = response_obj.get("usage") if isinstance(response_obj, dict) else getattr(response_obj, "usage", None)
    value = usage.get("completion_tokens") if isinstance(usage, dict) else getattr(usage, "completion_tokens", None)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _request_metadata(kwargs: dict) -> Optional[dict]:
    """The request metadata LiteLLM's auto-router writes into (`metadata` or `litellm_metadata`)."""
    params = kwargs.get("litellm_params")
    if not isinstance(params, dict):
        return None
    merged: dict = {}
    for key in ("metadata", "litellm_metadata"):   # litellm_metadata wins: the router reads it first
        value = params.get(key)
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _bare_model(name: str) -> str:
    return name.rsplit("/", 1)[-1].lower()


def receipt_fields_from_litellm(kwargs: dict, response_obj: Any) -> Optional[dict]:
    """Extract mint inputs from a LiteLLM success-callback event, or None.

    Fail-closed on anything malformed or missing: no cache hit, no model, no
    usable token count, or no call id -> no receipt (never a guessed one).
    """
    if not isinstance(kwargs, dict) or kwargs.get("cache_hit") is not True:
        return None
    model = kwargs.get("model")
    if not isinstance(model, str) or not model:
        return None
    call_id = kwargs.get("litellm_call_id")
    if not isinstance(call_id, str) or not call_id:
        return None
    tokens = _usage_total_tokens(response_obj)
    if tokens is None:
        return None
    cost = kwargs.get("response_cost")
    cost_note = (f" litellm_cost_estimate={cost:.6f}"
                 if isinstance(cost, (int, float)) and not isinstance(cost, bool)
                 and cost == cost else "")   # NaN != NaN -> excluded
    saved = _standard_logging_number(kwargs, "saved_cache_cost")
    saved_note = f" litellm_saved_cache_cost={saved:.6f}" if saved is not None else ""
    return {
        "tokens_saved": float(tokens),
        "avoided_model": model,
        "detail": f"litellm response-cache hit /litellm:{call_id}{cost_note}{saved_note}",
        "evidence": f"litellm:{call_id}",
    }


def route_fields_from_litellm(kwargs: dict, response_obj: Any) -> Optional[dict]:
    """Extract short_circuit mint inputs for a request LiteLLM's auto-router sent to a cheaper model.

    The counterfactual is the router's own `routing_decision.savings_baseline_model`; the served
    model is the one the call actually ran on. Only model names and LiteLLM's own dollar figure are
    copied: the decision's `signals` and keyword fields quote the caller's prompt and are never read.
    Fail-closed: no routing decision, the router's internal calls, the baseline served itself, no
    output tokens, or a route that is not cheaper at kry's published prices -> no receipt.
    """
    if not isinstance(kwargs, dict) or kwargs.get("cache_hit") is True:
        return None
    metadata = _request_metadata(kwargs)
    if not metadata or metadata.get("internal_call_origin"):
        return None
    decision = metadata.get("routing_decision")
    if not isinstance(decision, dict):
        return None
    baseline = decision.get("savings_baseline_model")
    served = kwargs.get("model")
    call_id = kwargs.get("litellm_call_id")
    if not all(isinstance(value, str) and value for value in (baseline, served, call_id)):
        return None
    if _bare_model(baseline) == _bare_model(served):
        return None
    tokens = _completion_tokens(response_obj)
    if tokens is None:
        return None
    from kry.kry_token import net_value_multiplier
    if net_value_multiplier(baseline, served) <= 0:
        return None   # not cheaper at kry's published prices: nothing to credit
    savings = _standard_logging_number(kwargs, "autorouter_savings")
    savings_note = f" litellm_autorouter_savings={savings:.6f}" if savings is not None else ""
    return {
        "tokens_saved": float(tokens),
        "avoided_model": baseline,
        "served_model": served,
        "detail": f"litellm auto-router /litellm:{call_id} baseline={baseline} served={served}{savings_note}",
        "evidence": f"litellm-route:{call_id}",
    }


def mint_from_litellm(kwargs: dict, response_obj: Any) -> Optional[Any]:
    """Mint a T0 receipt from one LiteLLM success event: cache_hit for a response-cache hit,
    short_circuit for a request the auto-router sent to a cheaper model (or None).

    Never raises: this runs inside a serving gateway's logging hot path, and a
    foreign library's kwargs are a system boundary — attestation must not break
    traffic. A failed mint is a missing receipt, not a corrupted ledger.
    """
    try:
        event_type = "cache_hit"
        fields = receipt_fields_from_litellm(kwargs, response_obj)
        if fields is None:
            event_type = "short_circuit"
            fields = route_fields_from_litellm(kwargs, response_obj)
        if fields is None:
            return None
        call_id = fields["evidence"]
        if call_id in _seen_ids:
            return None
        from kry import kry_mint
        receipt = kry_mint.mint(
            event_type, fields["tokens_saved"], fields["detail"],
            evidence=fields["evidence"], avoided_model=fields["avoided_model"],
            served_model=fields.get("served_model"))
        if receipt is not None:
            _seen_ids.add(call_id)
            _seen_order.append(call_id)
            if len(_seen_order) > _SEEN_MAX:
                _seen_ids.discard(_seen_order.popleft())
        return receipt
    except Exception:
        return None


try:
    from litellm.integrations.custom_logger import CustomLogger  # type: ignore
except Exception:                                    # litellm not installed
    CustomLogger = None

if CustomLogger is not None:
    class KryLiteLLMLogger(CustomLogger):
        """LiteLLM CustomLogger that mints a kry receipt per response-cache hit."""

        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            mint_from_litellm(kwargs, response_obj)

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            mint_from_litellm(kwargs, response_obj)

    kry_logger = KryLiteLLMLogger()   # instance for litellm_config.yaml `callbacks:`
else:
    KryLiteLLMLogger = None
    kry_logger = None


if __name__ == "__main__":
    # Self-test on a canned event (no litellm needed): extractor + fail-closed paths.
    import sys
    for _stream in (sys.stdout, sys.stderr):  # a cp1252 Windows console cannot encode the output glyphs
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    ev = {"cache_hit": True, "model": "gh/claude-opus-4.8",
          "litellm_call_id": "demo-1", "response_cost": 0.0125}
    resp = {"usage": {"total_tokens": 1000}}
    fields = receipt_fields_from_litellm(ev, resp)
    assert fields and fields["tokens_saved"] == 1000.0 and fields["evidence"] == "litellm:demo-1"
    assert receipt_fields_from_litellm({**ev, "cache_hit": False}, resp) is None
    assert receipt_fields_from_litellm(ev, {"usage": {}}) is None
    assert receipt_fields_from_litellm({**ev, "litellm_call_id": ""}, resp) is None
    print("self-test OK:", fields["detail"])
