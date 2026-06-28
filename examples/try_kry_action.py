#!/usr/bin/env python3
"""try_kry_action — the action-receipt layer, start to finish, in one program.

    record (T0/T1) -> attest (content-free) -> a STRANGER verifies -> the anchor
    catches a re-mint.

Uses a throwaway temp data dir. Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

# Make the package + scripts importable when run from a checkout.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kry-action-"))
    os.environ["KRY_DATA_DIR"] = str(tmp / "kry_data")

    from kry import kry_action
    from kry_action_mcp import attested_tool
    import kry_action_verify

    kry_action.reset_state_for_tests()

    print("  K R Y   —   action receipts")
    print("  every agent action -> a tamper-evident, stranger-verifiable receipt")
    print()
    print("=" * 70)
    print("1. RECORD — an agent takes actions through MCP-style tools")
    print("=" * 70)

    # A self-reported tool (T0): the runtime's word that it called the tool.
    @attested_tool("search_web", agent_id="assistant")
    def search_web(arguments: dict) -> dict:
        return {"hits": ["a", "b", "c"], "query": arguments["q"]}

    # A server-witnessed tool (T1): the server returns a signed receipt we bind.
    @attested_tool("execute_trade", agent_id="assistant",
                   witness=lambda args, result: result["server_signed_receipt"])
    def execute_trade(arguments: dict) -> dict:
        # In reality this comes from the broker/MCP server; here it stands in for the
        # external witness whose commitment is bound into the receipt at tier T1.
        return {"filled": True, "qty": arguments["qty"],
                "server_signed_receipt": {"venue": "X", "sig": "deadbeef", "fill_id": "f-991"}}

    # A failing tool: recorded as status='error', still in the chain.
    @attested_tool("send_email", agent_id="assistant")
    def send_email(arguments: dict) -> dict:
        raise RuntimeError("smtp connection refused")

    r1 = search_web({"q": "verifiable agent logs"})
    print(f"  search_web   -> tier={r1['_meta']['kry_action']['evidence_tier']:15} "
          f"tip={r1['_meta']['kry_action']['chain_tip'][:16]}...")

    r2 = execute_trade({"qty": 10, "symbol": "ACME"})
    print(f"  execute_trade-> tier={r2['_meta']['kry_action']['evidence_tier']:15} "
          f"tip={r2['_meta']['kry_action']['chain_tip'][:16]}...  (bound to server's signed receipt)")

    try:
        send_email({"to": "ceo@example.com", "body": "SECRET PLAN: launch Friday"})
    except RuntimeError:
        pass
    print("  send_email   -> raised; recorded as status='error' (still in the chain)")

    print()
    print("=" * 70)
    print("2. ATTEST — a public, content-free proof of the action log")
    print("=" * 70)
    att = kry_action.build_action_attestation()
    print(f"  actions:        {att['action_count']}")
    print(f"  chain_tip:      {att['chain_tip'][:16]}...")
    print(f"  veracity_floor: {att['veracity']['veracity_floor']}  "
          f"({att['veracity']['anchored_actions']}/{att['veracity']['total_actions']} externally witnessed)")
    print(f"  by tier:        {att['veracity']['by_tier']}")

    # Content-sealing: none of the raw arguments/results appear in the attestation.
    kry_action.assert_no_content_leak(att, [
        "verifiable agent logs", "ACME", "ceo@example.com",
        "SECRET PLAN: launch Friday", "deadbeef", "f-991",
    ])
    print("  content check:  PASS — no raw args/results/secrets in the public proof "
          "(only SHA-256 commitments)")

    att_path = tmp / "action_attestation.json"
    att_path.write_text(json.dumps(att, indent=2))

    print()
    print("=" * 70)
    print("3. VERIFY — a STRANGER checks it (imports nothing from kry)")
    print("=" * 70)
    ok, errors, warnings = kry_action_verify.verify_action_attestation(att)
    print(f"  integrity + tier-honesty + veracity_floor re-derived: "
          f"{'VALID' if ok else 'INVALID'}")
    for w in warnings:
        print(f"  ! {w}")

    print()
    print("=" * 70)
    print("4. ANCHOR — publish the head, then catch a re-mint")
    print("=" * 70)
    anchor = kry_action.export_anchor()
    print(f"  published anchor: count={anchor['count']} tip={anchor['chain_tip'][:16]}...")

    # Forge a re-minted chain: drop the failed send_email and re-derive a clean
    # 2-action chain from genesis. A re-mint is INTERNALLY consistent, so it passes
    # integrity on its own — recompute veracity too so nothing is left stale.
    forged = json.loads(json.dumps(att))
    forged_links = [link for link in forged["links"] if link["tool"] != "send_email"]
    prev = "0" * 64
    for link in forged_links:
        payload = kry_action_verify._receipt_payload(link)
        rh = hashlib.sha256(kry_action_verify._canon(payload).encode()).hexdigest()
        ch = hashlib.sha256(f"{prev}:{rh}".encode()).hexdigest()
        link["receipt_hash"], link["chain_hash"] = rh, ch
        prev = ch
    forged["links"] = forged_links
    forged["action_count"] = len(forged_links)
    forged["chain_tip"] = prev
    forged["veracity"] = kry_action._veracity(forged_links)  # fully internally consistent

    f_ok, _, _ = kry_action_verify.verify_action_attestation(forged)
    print(f"  forged re-mint, integrity ALONE:        {'VALID' if f_ok else 'INVALID'}  "
          "(a re-mint is internally consistent — this is the known limit)")
    a_ok, a_errors = kry_action_verify.verify_against_anchor(forged, anchor)
    print(f"  forged re-mint, checked vs the anchor:  {'OK' if a_ok else 'CAUGHT'}")
    for e in a_errors:
        print(f"    - {e}")

    print()
    print("=" * 70)
    print("The action log is intact, ordered, and append-only; the trust you place in")
    print("the agent's self-report is an explicit, re-derived veracity_floor; and a")
    print("published anchor makes a retroactive re-mint detectable. That is what an")
    print("action receipt proves — and, by construction, what it does not.")
    print(f"(temp dir {tmp} — safe to delete)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
