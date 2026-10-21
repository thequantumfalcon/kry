"""Bounded, allowlisted metadata from LiteLLM-normalized callback fields.

This preserves presence at the callback boundary, not the provider's original
JSON. Adapters can default missing counts, mirror aliases or synthesize model
and response identities before this function sees them. No prices or tiers are
computed, and a header digest does not authenticate a provider response.
"""
from __future__ import annotations

import hashlib
import re

_ABSENT = object()
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,511}\Z")
_USAGE = dict.fromkeys(("prompt_tokens", "completion_tokens", "total_tokens",
                       "input_tokens", "output_tokens", "cache_creation_input_tokens",
                       "cache_read_input_tokens", "cost_in_usd_ticks", "context_tokens"), "count")
_USAGE.update({
    "prompt_tokens_details": {
        **dict.fromkeys(("cached_tokens", "cache_write_tokens", "cache_creation_tokens",
                         "text_tokens", "audio_tokens", "image_tokens", "video_tokens"), "count"),
        "cache_creation_token_details": dict.fromkeys(
            ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"), "count"),
    },
    "completion_tokens_details": dict.fromkeys(
        ("reasoning_tokens", "text_tokens", "audio_tokens", "image_tokens", "video_tokens",
         "accepted_prediction_tokens", "rejected_prediction_tokens"), "count"),
    "input_tokens_details": dict.fromkeys(("cached_tokens", "text_tokens", "audio_tokens"), "count"),
    "output_tokens_details": dict.fromkeys(("reasoning_tokens", "text_tokens", "audio_tokens"), "count"),
    "context_details": dict.fromkeys(("input_tokens", "output_tokens"), "count"),
    "server_tool_use": dict.fromkeys(
        ("web_search_requests", "tool_search_requests", "browser_open_requests"), "count"),
    "service_tier": ("standard", "priority", "batch", "default", "flex", "scale", "auto"),
    "speed": ("standard", "fast"),
    "inference_geo": ("global", "us", "not_available"),
})


def _get(value, name):
    return value.get(name, _ABSENT) if isinstance(value, dict) else getattr(value, name, _ABSENT)


def _project(value, schema):
    if value is _ABSENT:
        return {"state": "absent"}
    if value is None:
        return {"state": "null"}
    if isinstance(schema, dict):
        members = value if isinstance(value, dict) else getattr(value, "__dict__", None)
        if not isinstance(members, dict):
            return {"state": "invalid"}
        extras = getattr(value, "model_extra", None) if not isinstance(value, dict) else None
        unsupported = any(key not in schema for key in members)
        if isinstance(extras, dict):
            unsupported = unsupported or any(key not in schema for key in extras)
        return {"state": "value", "fields": {key: _project(_get(value, key), child)
                                               for key, child in schema.items()},
                "unsupported_fields_present": unsupported}
    valid = False
    if schema == "count":
        valid = type(value) is int and 0 <= value <= 2**63 - 1
    elif schema == "bool":
        valid = type(value) is bool
    elif schema == "model":
        valid = isinstance(value, str) and _MODEL.fullmatch(value) is not None
    elif schema == "identity":
        if isinstance(value, str) and _IDENTITY.fullmatch(value) is not None:
            return {"state": "value", "sha256": hashlib.sha256(value.encode("ascii")).hexdigest()}
    elif isinstance(schema, tuple):
        valid = isinstance(value, str) and value in schema
    return {"state": "value", "value": value} if valid else {"state": "invalid"}


def _header_identity(response):
    hidden = _get(response, "_hidden_params")
    headers = _get(hidden, "additional_headers") if hidden is not _ABSENT and hidden is not None else hidden
    if headers is _ABSENT or headers is None:
        return _project(headers, "identity")
    if not isinstance(headers, dict) or len(headers) > 128:
        return {"state": "invalid"}
    candidates = [name for name in headers
                  if isinstance(name, str) and name.lower() == "llm_provider-request-id"]
    if len(candidates) > 1:
        return {"state": "invalid"}
    return _project(headers[candidates[0]] if candidates else _ABSENT, "identity")


def observe_litellm_usage(kwargs, response_obj):
    """Project only allowlisted callback fields; never serialize content containers.

    Counts are exact signed-64-bit nonnegative integers; unsupported values are
    marked invalid rather than copied or coerced. Unknown category presence is
    disclosed without copying its name or value. No evidence completeness is
    implied, and the consumer remains responsible for private durable storage.
    """
    requested = _project(_get(kwargs, "model"), "model")
    reported = _project(_get(response_obj, "model"), "model")
    comparison = "unknown"
    if requested["state"] == reported["state"] == "value":
        comparison = "exact_match" if requested["value"] == reported["value"] else "different"
    return {
        "schema": "kry-litellm-observation-v1",
        "source": "litellm_callback_normalized",
        "source_authenticity": "operator_observed",
        "billing_reconciliation": "not_performed",
        "actual_charge_usd": None,
        "cache_hit": _project(_get(kwargs, "cache_hit"), "bool"),
        "litellm_call_id": _project(_get(kwargs, "litellm_call_id"), "identity"),
        "normalized_response_id": _project(_get(response_obj, "id"), "identity"),
        "callback_requested_model": requested,
        "normalized_response_model": reported,
        "model_comparison": comparison,
        "usage": _project(_get(response_obj, "usage"), _USAGE),
        "provider_request_id_candidate": {
            "origin": "litellm_flattened_header",
            "header": "llm_provider-request-id",
            "duplicate_detection": "unavailable",
            "identity": _header_identity(response_obj),
        },
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Observation helper for KryLiteLLMLogger's optional usage_observer; no capture is run here.")
    parser.parse_args()
    parser.print_help()
