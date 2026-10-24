#!/usr/bin/env python3
"""kry_litellm_callback — mint kry receipts from LiteLLM's callback surface.

LiteLLM (gateway/proxy) exposes a documented per-request hook: a CustomLogger
subclass receives every completed call with `kwargs["cache_hit"]` (its response
cache answered — the provider call did NOT happen) and `kwargs["response_cost"]`
(LiteLLM's price-table estimate). This module records eligible response-cache
events as kry `cache_hit` receipts in a tamper-evident, stranger-verifiable ledger.

THE HONEST EVIDENCE BOUNDARY (read before quoting numbers):
  * Every receipt minted here is tier `self_reported` (T0). The cache-hit signal
    comes from the operator's own gateway — no external party witnesses it, so
    the attestation's `veracity_floor` stays 0.0 for these receipts. Verification
    checks the recorded history and KRY benchmark arithmetic; it cannot prove
    the hits happened or establish actual provider charges.
  * Wording correction: "INTACT and honestly priced" becomes "recorded history
    and KRY benchmark arithmetic". The callback values total prompt + completion
    tokens at an output-price multiplier. Provider input/output billing can
    differ; usd_equivalent is a benchmark, not an invoice or proven cash saving.
  * `cache_hit` is LiteLLM's RESPONSE cache, not provider-side prompt caching
    (`cached_tokens`); prompt-cache savings need a separate signal.
  * `response_cost` is LiteLLM's estimate from its price table (may be absent).
    It is recorded in the receipt detail as context, never used as evidence; so are
    LiteLLM's `saved_cache_cost` and `autorouter_savings` figures.
  * The evidence key uses LiteLLM's call id, not a provider request/message id.
    Retain provider identifiers separately; see docs/KRY_LITELLM.md.

OPERATIONAL LIMITS: callbacks complete asynchronously relative to serving.
Use an independent attempt inventory and bounded completion wait. Stop on missing
receipts, persistence failure or process interruption; do not reset decay or
remint an uncertain event under a new id. The retained receipt log prevents
duplicate evidence across restarts/workers where its file lock is supported.
Callback mints reserve shared decay allowance but credit first events in full;
failed-append retries retain that full credit. Partial writes still require
operator recovery; a missing return can mean an uncertain committed receipt.

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

import logging
from collections import deque
from threading import Lock
from typing import Any, Optional

# Bounded in-process fast path. The retained mint log supplies authoritative
# evidence deduplication under its cross-process lock, including after eviction.
_SEEN_MAX = 4096
_seen_ids: set = set()
_seen_order: deque = deque()
_seen_lock = Lock()
logger = logging.getLogger("kry.litellm")


def _usage_counts(response_obj: Any) -> Optional[tuple[Optional[int], Optional[int], Optional[int]]]:
    """Validate supplied total/prompt/completion counts without filling unknown fields."""
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
    total, p, c = (_field(name) for name in ("total_tokens", "prompt_tokens", "completion_tokens"))
    if any(value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0)
           for value in (total, p, c)):
        return None
    if total is not None and any(value is not None and value > total for value in (p, c)):
        return None
    if total is not None and p is not None and c is not None and total != p + c:
        return None
    return total, p, c


def _usage_total_tokens(response_obj: Any) -> Optional[int]:
    """Total tokens of the served (cached) response — the size of the avoided call."""
    counts = _usage_counts(response_obj)
    if counts is None:
        return None
    total, p, c = counts
    if total is None:   # LiteLLM may represent an unavailable total as None.
        if p is None or c is None:
            return None
        total = p + c
    return total if total > 0 else None


def _finite(value: Any) -> Optional[float]:
    """A real, finite number, or None (bools, NaN and infinities excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    try:
        return float(value)
    except OverflowError:
        return None


def _standard_logging_number(kwargs: dict, key: str) -> Optional[float]:
    """A numeric field of LiteLLM's standard logging payload, recorded as context only."""
    payload = kwargs.get("standard_logging_object")
    return _finite(payload.get(key)) if isinstance(payload, dict) else None


def _completion_tokens(response_obj: Any) -> Optional[int]:
    """Output tokens of the served response: what a cheaper route saves on."""
    counts = _usage_counts(response_obj)
    if counts is None:
        return None
    value = counts[2]
    return value if value is not None and value > 0 else None


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
    from kry.kry_token import _priced_multiplier
    multiplier = _priced_multiplier(model)
    if multiplier is None:
        # Fixed reasons only: model names and callback metadata can contain private data.
        logger.warning("KRY LiteLLM cache: unpriced_avoided_model; zero benchmark credit")
    elif multiplier == 0:
        logger.debug("KRY LiteLLM cache: known_free_avoided_model; zero benchmark credit")
    cost = _finite(kwargs.get("response_cost"))
    cost_note = f" litellm_cost_estimate={cost:.6f}" if cost is not None else ""
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
    tokens = _completion_tokens(response_obj)
    if tokens is None:
        return None
    from kry.kry_token import _priced_multiplier, net_value_multiplier
    avoided_price, served_price = _priced_multiplier(baseline), _priced_multiplier(served)
    if avoided_price is None:
        logger.warning("KRY LiteLLM route: unpriced_avoided_model; no receipt")
    if served_price is None:
        logger.warning("KRY LiteLLM route: unpriced_served_model; no receipt")
    if avoided_price is None or served_price is None:
        return None
    if _bare_model(baseline) == _bare_model(served) or net_value_multiplier(baseline, served) <= 0:
        logger.debug("KRY LiteLLM route: no_positive_benchmark_difference; no receipt")
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
    traffic. A missing return can reflect an uncertain append; retain the log
    and reconcile it before retrying. Partial/corrupt logs fail closed.
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
        with _seen_lock:
            if call_id in _seen_ids:
                return None
            from kry import kry_mint
            receipt = kry_mint.mint(
                event_type, fields["tokens_saved"], fields["detail"],
                evidence=fields["evidence"], avoided_model=fields["avoided_model"],
                served_model=fields.get("served_model"), idempotent_evidence=True)
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
        """Mint eligible T0 receipts; optionally observe bounded normalized metadata.

        usage_observer must be a fast synchronous callable. It owns persistence;
        failures are counted locally and do not stop serving or receipt minting.
        """

        def __init__(self, *args, usage_observer=None, **kwargs):
            import inspect
            if usage_observer is not None and (
                not callable(usage_observer) or inspect.iscoroutinefunction(usage_observer)
                or inspect.iscoroutinefunction(getattr(usage_observer, "__call__", None))
            ):
                raise TypeError("usage_observer must be a synchronous callable")
            super().__init__(*args, **kwargs)
            self._usage_observer = usage_observer
            self.observation_failures = 0
            self._observation_lock = Lock()
            if usage_observer is not None:
                # Load the sibling helper for both proxy imports and file-based integrations.
                import importlib.util
                from pathlib import Path
                spec = importlib.util.spec_from_file_location(
                    "kry_litellm_observation", Path(__file__).with_name("kry_litellm_observation.py"))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._project_usage = module.observe_litellm_usage

        def _observe_usage(self, kwargs, response_obj):
            if self._usage_observer is None:
                return
            try:
                import inspect
                result = self._usage_observer(self._project_usage(kwargs, response_obj))
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise TypeError("usage_observer must complete synchronously")
            except Exception:
                with self._observation_lock:
                    self.observation_failures += 1
                import logging
                logging.getLogger(__name__).warning("kry usage observation failed")

        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            self._observe_usage(kwargs, response_obj)
            mint_from_litellm(kwargs, response_obj)

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            self._observe_usage(kwargs, response_obj)
            mint_from_litellm(kwargs, response_obj)

    kry_logger = KryLiteLLMLogger()   # instance for litellm_config.yaml `callbacks:`
else:
    KryLiteLLMLogger = None
    kry_logger = None


if __name__ == "__main__":
    # Self-test on a canned event (no litellm needed): extractor + fail-closed paths.
    import argparse
    import sys
    for _stream in (sys.stdout, sys.stderr):  # a cp1252 Windows console cannot encode the output glyphs
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    argparse.ArgumentParser(
        description="Self-test the extractor on a canned event (no litellm needed). As a LiteLLM "
                    "callback, load this module from litellm_config.yaml; see the module docstring.",
    ).parse_args()
    ev = {"cache_hit": True, "model": "gh/claude-opus-4.8",
          "litellm_call_id": "demo-1", "response_cost": 0.0125}
    resp = {"usage": {"total_tokens": 1000}}
    fields = receipt_fields_from_litellm(ev, resp)
    assert fields and fields["tokens_saved"] == 1000.0 and fields["evidence"] == "litellm:demo-1"
    assert receipt_fields_from_litellm({**ev, "cache_hit": False}, resp) is None
    assert receipt_fields_from_litellm(ev, {"usage": {}}) is None
    assert receipt_fields_from_litellm({**ev, "litellm_call_id": ""}, resp) is None
    print("self-test OK:", fields["detail"])
