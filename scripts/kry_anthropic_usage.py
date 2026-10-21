"""Separate list-price estimate for a narrow raw Anthropic Messages response.

This is observed usage priced at a dated rate, not a KRY receipt valuation,
an invoice, or independently reconciled spend. No content is returned or saved.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, localcontext
import json
from pathlib import Path
import re

MODEL = "claude-haiku-4-5-20251001"
PRICE_AS_OF = "2026-10-20"
PRICE_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
_INPUT_USD_PER_M = Decimal("1")
_OUTPUT_USD_PER_M = Decimal("5")
_COUNTS = ("input_tokens", "output_tokens")
_CACHE_COUNTS = ("cache_creation_input_tokens", "cache_read_input_tokens")
_CACHE_DETAILS = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
_TOOL_COUNTS = ("web_search_requests", "web_fetch_requests")
_JSON_MAX_BYTES = 16 * 1024 * 1024
_GENERATION_STATUS = {
    "end_turn": "ended",
    "stop_sequence": "stopped_by_sequence",
    "max_tokens": "truncated",
    "refusal": "refused",
    "model_context_window_exceeded": "truncated",
}


def estimate_anthropic_usage(response: dict, *, request_id: str) -> dict:
    """Price ordinary nonstreaming text usage, rejecting unsupported categories.

    Only the pinned model's standard input/output rates are supported. Required
    usage counts must be present; absent optional metadata is not reported as
    an observed zero. Provider identifiers are preserved, never synthesized.
    Optional metadata may be null, which remains unknown in observed_usage.
    Supplied cache/tool counters and thinking-token breakdowns must be integer
    zero; service_tier and speed must be "standard". inference_geo may be
    "global" or "not_available"; the latter does not identify a location.
    The pinned Haiku 4.5 model uses standard rates with no geo surcharge.
    The estimate assumes ordinary text with no cache/tool usage when those
    optional observations are absent or null. Other categories are unsupported.
    A recognized ordinary nonstreaming stop reason is required. Truncated or
    refused output can still have priced usage; generation_status describes
    the reported stop, not answer adequacy, transport completion, or billing.
    """
    if not isinstance(response, dict):
        raise ValueError("response must be an object")
    if (response.get("type") != "message" or response.get("role") != "assistant"
            or response.get("model") != MODEL):
        raise ValueError("unsupported response type, role, or model")
    stop_reason = response.get("stop_reason")
    if not isinstance(stop_reason, str) or stop_reason not in _GENERATION_STATUS:
        raise ValueError("missing or unsupported ordinary nonstreaming stop reason")
    if response.get("container") is not None:
        raise ValueError("container-tool usage is outside the ordinary-text scope")
    message_id = response.get("id")
    if not isinstance(message_id, str) or not re.fullmatch(r"msg_[A-Za-z0-9_-]{1,160}", message_id):
        raise ValueError("missing or invalid provider message id")
    if not isinstance(request_id, str) or not re.fullmatch(r"req_[A-Za-z0-9_-]{1,160}", request_id):
        raise ValueError("missing or invalid provider request id")
    content = response.get("content")
    if (not isinstance(content, list) or not content
            or any(not isinstance(block, dict)
                   or not {"type", "text"} <= set(block)
                   or set(block) - {"type", "text", "citations"}
                   or block.get("type") != "text" or not isinstance(block.get("text"), str)
                   or ("citations" in block and block["citations"] not in (None, []))
                   for block in content)):
        raise ValueError("only ordinary text content is supported")
    usage = response.get("usage")
    allowed = set(_COUNTS + _CACHE_COUNTS) | {
        "cache_creation", "service_tier", "speed", "inference_geo",
        "server_tool_use", "output_tokens_details",
    }
    if (not isinstance(usage, dict) or not set(_COUNTS) <= set(usage)
            or set(usage) - allowed):
        raise ValueError("missing usage counts or unsupported usage category")
    for name in _COUNTS + _CACHE_COUNTS:
        if name in _CACHE_COUNTS and usage.get(name) is None:
            continue
        if name in usage and (type(usage[name]) is not int or usage[name] < 0):
            raise ValueError("usage counts must be nonnegative integers")
    if any(usage.get(name) not in (None, 0) for name in _CACHE_COUNTS):
        raise ValueError("prompt-cache pricing is not supported")
    if usage.get("cache_creation") is not None:
        details = usage["cache_creation"]
        if (not isinstance(details, dict) or set(details) != set(_CACHE_DETAILS)
                or any(type(value) is not int or value != 0 for value in details.values())):
            raise ValueError("prompt-cache pricing is not supported")
    if usage.get("service_tier") not in (None, "standard"):
        raise ValueError("only standard service pricing is supported")
    if usage.get("speed") not in (None, "standard"):
        raise ValueError("only standard speed pricing is supported")
    if usage.get("inference_geo") not in (None, "global", "not_available"):
        raise ValueError("unsupported inference geography for the pinned model")
    if usage.get("server_tool_use") is not None:
        tools = usage["server_tool_use"]
        if (not isinstance(tools, dict) or not tools or set(tools) - set(_TOOL_COUNTS)
                or any(type(value) is not int or value != 0 for value in tools.values())):
            raise ValueError("server-tool pricing is not supported")
    if usage.get("output_tokens_details") is not None:
        details = usage["output_tokens_details"]
        if (not isinstance(details, dict) or set(details) != {"thinking_tokens"}
                or type(details["thinking_tokens"]) is not int or details["thinking_tokens"] != 0):
            raise ValueError("nonzero thinking usage is outside the ordinary-text scope")
    input_tokens, output_tokens = (usage[name] for name in _COUNTS)
    with localcontext() as context:
        context.prec = max(len(str(input_tokens)), len(str(output_tokens)), 28) + 8
        input_cost = Decimal(input_tokens) * _INPUT_USD_PER_M / Decimal("1000000")
        output_cost = Decimal(output_tokens) * _OUTPUT_USD_PER_M / Decimal("1000000")
        total_cost = input_cost + output_cost
    return {
        "schema": "kry_anthropic_usage_estimate/v1",
        "model": MODEL,
        "message_id": message_id,
        "request_id": request_id,
        "stop_reason": stop_reason,
        "generation_status": _GENERATION_STATUS[stop_reason],
        "observed_usage": {
            name: dict(value) if isinstance(value, dict) else value
            for name, value in usage.items()
        },
        "pricing": {
            "as_of": PRICE_AS_OF,
            "source": PRICE_SOURCE,
            "basis": "standard input/output list prices",
            "assumptions": (
                "Ordinary text, standard service/speed, no cache or server-tool usage. "
                "Pinned Haiku 4.5 standard rates have no geo surcharge; not_available "
                "does not identify an inference location. Absent or null optional metadata "
                "is unknown, not an observed zero."
            ),
            "input_usd_per_million_tokens": format(_INPUT_USD_PER_M, "f"),
            "output_usd_per_million_tokens": format(_OUTPUT_USD_PER_M, "f"),
        },
        "input_estimate_usd": format(input_cost, "f"),
        "output_estimate_usd": format(output_cost, "f"),
        "total_estimate_usd": format(total_cost, "f"),
        "actual_charge_usd": None,
        "financial_status": "unverified",
    }


def _reject_json_constant(value: str) -> None:
    raise ValueError("nonstandard JSON constant")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Estimate ordinary Anthropic text usage at pinned list prices; not a bill.")
    parser.add_argument("response", type=Path, help="Raw nonstreaming Messages response JSON")
    parser.add_argument("--request-id", required=True, help="Provider request-id response header")
    args = parser.parse_args()
    try:
        with args.response.open("rb") as stream:
            raw = stream.read(_JSON_MAX_BYTES + 1)
        if len(raw) > _JSON_MAX_BYTES:
            raise ValueError("response JSON exceeds 16 MiB")
        response = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        result = estimate_anthropic_usage(response, request_id=args.request_id)
    except RecursionError:
        parser.error("response JSON is too deeply nested")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
