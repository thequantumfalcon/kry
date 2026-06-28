#!/usr/bin/env python3
"""kry_action_mcp — thin middleware that turns ordinary MCP tool calls into
tamper-evident action receipts.

Deliberately dict-based and stdlib-only: it operates on plain Python dicts (the
shape MCP tool arguments / results / `_meta` already take), so it wraps a handler
from ANY MCP server or framework without importing an MCP SDK and without adding a
dependency. It imports only kry_action (the package's stdlib core).

Two ways to use it:

  1. Decorator — wrap a tool handler so every call auto-mints a receipt:

        from kry_action_mcp import attested_tool

        @attested_tool("send_email", agent_id="mailbot")
        def send_email(arguments: dict) -> dict:
            ...
            return {"sent": True, "id": "..."}

     Each call appends a content-free receipt to the chain; on return, if the
     result is a dict, the running chain head is stamped into result["_meta"].

  2. Server-witnessed (T1) — bind the tool SERVER's own response as the witness:

        @attested_tool("execute_trade", agent_id="trader",
                       witness=lambda args, result: result["server_signed_receipt"])
        def execute_trade(arguments: dict) -> dict:
            ...

     The `witness` callable returns the external evidence (the server's signed
     response, a notary doc); its commitment is bound and the receipt is minted at
     tier server_witnessed. Without a witness, receipts are self_reported (T0) —
     the honest default.

The chain head a client receives in `_meta` is a running commitment to the action
log. Publish export_anchor() out-of-band and a stranger (kry_action_verify.py
--anchor) can later confirm no action was edited, reordered, inserted, or dropped.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Optional

from kry.kry_action import (
    TIER_SELF_REPORTED,
    TIER_SERVER_WITNESSED,
    record,
    export_anchor,  # re-exported for convenience
)

__all__ = [
    "record_tool_call",
    "attested_tool",
    "stamp_chain_head",
    "export_anchor",
]


def stamp_chain_head(result: Any, receipt) -> Any:
    """If `result` is a dict, stamp the receipt's chain head into result['_meta']
    under 'kry_action'. Returns the result unchanged otherwise (a stranger still has
    the full attestation; the _meta stamp is a convenience for live clients)."""
    if isinstance(result, dict):
        meta = dict(result.get("_meta") or {})
        meta["kry_action"] = {
            "receipt_id": receipt.receipt_id,
            "chain_tip": receipt.chain_hash,
            "evidence_tier": receipt.evidence_tier,
            "action_hash_version": receipt.action_hash_version,
        }
        result = {**result, "_meta": meta}
    return result


def record_tool_call(
    tool: str,
    arguments: Any,
    result: Any,
    *,
    status: str = "ok",
    agent_id: str = "default",
    tier: str = TIER_SELF_REPORTED,
    server_evidence: Any = None,
):
    """Mint a receipt for an already-completed tool call. Returns the ActionReceipt."""
    return record(
        tool,
        arguments,
        result=result,
        has_result=result is not None,
        status=status,
        agent_id=agent_id,
        evidence_tier=tier,
        server_evidence=server_evidence,
    )


def attested_tool(
    name: Optional[str] = None,
    *,
    agent_id: str = "default",
    witness: Optional[Callable[[Any, Any], Any]] = None,
    stamp_meta: bool = True,
) -> Callable:
    """Decorator: wrap a `handler(arguments) -> result` so every call mints a
    receipt. `witness(arguments, result)` (optional) returns external evidence to
    bind at tier server_witnessed. Exceptions are recorded as status='error' and
    re-raised, so a failed action is still in the (tamper-evident) log."""
    def decorator(handler: Callable[[Any], Any]) -> Callable[[Any], Any]:
        tool_name = name or getattr(handler, "__name__", "tool")

        @functools.wraps(handler)
        def wrapper(arguments: Any) -> Any:
            try:
                result = handler(arguments)
            except Exception as exc:  # noqa: BLE001 — record the failure, then re-raise
                record_tool_call(
                    tool_name, arguments, None,
                    status="error", agent_id=agent_id,
                    tier=TIER_SELF_REPORTED,
                    server_evidence={"error": type(exc).__name__, "message": str(exc)[:200]},
                )
                raise

            tier = TIER_SELF_REPORTED
            evidence = None
            if witness is not None:
                evidence = witness(arguments, result)
                if evidence is not None:
                    tier = TIER_SERVER_WITNESSED

            receipt = record_tool_call(
                tool_name, arguments, result,
                status="ok", agent_id=agent_id, tier=tier, server_evidence=evidence,
            )
            if stamp_meta:
                result = stamp_chain_head(result, receipt)
            return result

        return wrapper

    return decorator
