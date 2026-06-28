#!/usr/bin/env python3
"""kry_action_verify — the STRANGER's check for an action attestation.

Imports NO part of the kry package. It re-implements the action-receipt hashing
from the spec, so a passing verdict is meaningful precisely because this code does
not trust the producer. Stdlib only; runs anywhere Python >= 3.11 runs. The hashing
is byte-identical to a non-Python verifier (canonical JSON + IEEE-754 big-endian
floats), so a Rust/JS/Go re-implementation reaches the same verdict.

What it checks
  integrity   every receipt_hash re-derives from the public fields; every chain_hash
              re-derives from prev:receipt_hash; the chain links from genesis (0x64).
              Any edit / reorder / insert / drop breaks it -> INVALID.
  tier honesty a link claiming server_witnessed / attested with NO server_evidence_commit
              is COERCED to self_reported (a forged anchored tier cannot inflate the floor).
  veracity    the claimed veracity_floor must equal the floor re-derived from the
              (coerced) per-link tiers, else INVALID.
  anchor      (--anchor) the attestation's tip at the anchor's count must match a
              PUBLISHED {count, chain_tip}. This is what catches a full genesis
              re-mint: a re-minted chain verifies clean on its own, but its tip will
              not match the anchor the operator published earlier.

  python3 kry_action_verify.py attestation.json
  python3 kry_action_verify.py attestation.json --anchor anchor.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys

ACTION_HASH_VERSION = 1
GENESIS = "0" * 64
_V5_BAD = "ff" * 8
TIER_SELF_REPORTED = "self_reported"
_ANCHORED_TIERS = {"server_witnessed", "attested"}


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_loads(text: str):
    return json.loads(text, parse_constant=_reject_json_constant)


def _canon(value) -> str:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _canon_f64(x) -> str:
    """REPLICA of kry_action._canon_f64 — EXACT IEEE-754 double, big-endian hex."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return _V5_BAD
    if f != f or f in (float("inf"), float("-inf")):
        return _V5_BAD
    return struct.pack(">d", f).hex()


def _receipt_payload(link: dict) -> dict:
    """REPLICA of kry_action.ActionReceipt._payload — must serialize byte-for-byte
    identically to the minter. Missing fields map to fixed defaults so a tampered
    link yields a clean MISMATCH rather than a crash."""
    return {
        "action_hash_version": ACTION_HASH_VERSION,
        "tool": link.get("tool", ""),
        "args_commit": link.get("args_commit", ""),
        "result_commit": link.get("result_commit"),
        "status": link.get("status", ""),
        "ts": _canon_f64(link.get("ts")),
        "agent_id": link.get("agent_id", ""),
        "evidence_tier": link.get("evidence_tier", ""),
        "server_evidence_commit": link.get("server_evidence_commit"),
    }


def _effective_tier(link: dict) -> str:
    tier = link.get("evidence_tier", TIER_SELF_REPORTED)
    if tier in _ANCHORED_TIERS and not link.get("server_evidence_commit"):
        return TIER_SELF_REPORTED  # forged anchored tier with no witness -> not credited
    return tier


def verify_action_attestation(att: dict) -> tuple[bool, list[str], list[str]]:
    """Returns (ok, errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    if att.get("kind") != "kry_action_attestation":
        errors.append(f"not an action attestation (kind={att.get('kind')!r})")
        return False, errors, warnings
    hv = att.get("action_hash_version")
    if hv != ACTION_HASH_VERSION:
        errors.append(f"unsupported action_hash_version {hv} (this verifier: {ACTION_HASH_VERSION})")
        return False, errors, warnings

    links = att.get("links")
    if not isinstance(links, list):
        errors.append("links missing or not a list")
        return False, errors, warnings

    # ── integrity: re-derive the chain from genesis ──
    prev = GENESIS
    seen_ids: set[str] = set()
    last_ts = None
    for i, link in enumerate(links):
        if not isinstance(link, dict):
            errors.append(f"link {i}: not an object")
            return False, errors, warnings
        rid = link.get("receipt_id", f"#{i}")
        if rid in seen_ids:
            errors.append(f"link {i} ({rid}): duplicate receipt_id")
            return False, errors, warnings
        seen_ids.add(rid)

        exp_receipt = hashlib.sha256(_canon(_receipt_payload(link)).encode()).hexdigest()
        if link.get("receipt_hash") != exp_receipt:
            errors.append(f"link {i} ({rid}): receipt_hash mismatch — a field was tampered")
            return False, errors, warnings
        exp_chain = hashlib.sha256(f"{prev}:{exp_receipt}".encode()).hexdigest()
        if link.get("chain_hash") != exp_chain:
            errors.append(f"link {i} ({rid}): chain_hash mismatch — "
                          "broken / reordered / inserted / dropped link")
            return False, errors, warnings
        prev = exp_chain

        # ts is bound (so it can't be silently changed) but real systems can record
        # slightly out-of-order timestamps under concurrency: WARN, don't fail.
        ts = link.get("ts")
        if isinstance(ts, (int, float)) and last_ts is not None and ts < last_ts:
            warnings.append(f"link {i} ({rid}): ts goes backwards "
                            "(allowed under concurrency; the chain still fixes order)")
        if isinstance(ts, (int, float)):
            last_ts = ts

    # ── chain tip ──
    declared_tip = att.get("chain_tip")
    if declared_tip != prev:
        errors.append(f"chain_tip mismatch: declared {declared_tip}, re-derived {prev}")
        return False, errors, warnings
    if att.get("action_count") != len(links):
        errors.append(f"action_count mismatch: declared {att.get('action_count')}, got {len(links)}")
        return False, errors, warnings

    # ── veracity_floor re-derivation (from coerced tiers) ──
    total = len(links)
    anchored = sum(1 for link in links if _effective_tier(link) in _ANCHORED_TIERS)
    coerced = sum(1 for link in links
                  if link.get("evidence_tier") in _ANCHORED_TIERS and not link.get("server_evidence_commit"))
    if coerced:
        warnings.append(f"{coerced} link(s) claimed an anchored tier with NO witness — "
                        "coerced to self_reported (not credited to the floor)")
    derived_floor = round(anchored / total, 4) if total > 0 else 0.0
    v = att.get("veracity") or {}
    claimed = v.get("veracity_floor")
    if isinstance(claimed, (int, float)):
        if abs(float(claimed) - derived_floor) > 0.01:
            errors.append(f"veracity_floor mismatch: claimed {claimed}, re-derived {derived_floor}")
            return False, errors, warnings
    else:
        warnings.append("attestation declared no veracity_floor; re-derived below")

    return (len(errors) == 0), errors, warnings


def verify_against_anchor(att: dict, anchor: dict) -> tuple[bool, list[str]]:
    """The published anchor closes the re-mint hole: a chain re-minted from genesis
    verifies clean on its own, but cannot reproduce a tip the operator published before."""
    errors: list[str] = []
    if anchor.get("kind") != "kry_action_anchor":
        errors.append(f"not an action anchor (kind={anchor.get('kind')!r})")
        return False, errors
    a_count = anchor.get("count")
    a_tip = anchor.get("chain_tip")
    links = att.get("links") or []
    if not isinstance(a_count, int) or a_count < 0:
        errors.append("anchor count missing/invalid")
        return False, errors
    if a_count > len(links):
        errors.append(f"anchor commits to {a_count} actions but attestation has only {len(links)} "
                      "— actions were DROPPED after the anchor was published")
        return False, errors
    # The chain_hash at position a_count must equal the published tip.
    tip_at = GENESIS if a_count == 0 else links[a_count - 1].get("chain_hash")
    if tip_at != a_tip:
        errors.append(f"anchor mismatch at count {a_count}: published tip {a_tip}, "
                      f"attestation tip {tip_at} — the log was RE-MINTED / edited after publication")
        return False, errors
    return True, errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="kry action-chain external verifier (stdlib-only)")
    p.add_argument("attestation", help="path to an action attestation JSON")
    p.add_argument("--anchor", help="path to a PUBLISHED {count, chain_tip} anchor (re-mint detection)")
    args = p.parse_args(argv)

    with open(args.attestation) as fh:
        att = _json_loads(fh.read())

    ok, errors, warnings = verify_action_attestation(att)
    v = att.get("veracity") or {}

    print("kry action-chain external verification")
    print(f"  actions:         {att.get('action_count')} (re-derived from links)")
    print(f"  chain_tip:       {att.get('chain_tip')}")
    floor = v.get("veracity_floor")
    print(f"  veracity_floor:  {floor} (fraction backed by more than self-report — server-witnessed/"
          "attested; a 'server' witness is only as external as its wiring; rest is the agent's own word)")
    bt = v.get("by_tier")
    if bt:
        print(f"  by tier:         {bt}")

    if args.anchor:
        with open(args.anchor) as fh:
            anchor = _json_loads(fh.read())
        a_ok, a_errors = verify_against_anchor(att, anchor)
        if a_ok:
            print(f"  anchor check:    OK — matches published tip at count {anchor.get('count')} "
                  "(retroactive re-mint would be caught here)")
        else:
            ok = False
            errors.extend(a_errors)
    else:
        print("  anchor check:    NONE — without --anchor a full genesis re-mint passes integrity.")
        print("                   The operator PUBLISHES export_anchor() out-of-band; re-run with --anchor.")

    for w in warnings:
        print(f"  ! {w}")

    if ok:
        print("  VERDICT: VALID — the action log is intact, ordered, and append-only "
              "(read veracity_floor for what is the agent's own word).")
        return 0
    print("  VERDICT: INVALID")
    for e in errors:
        print(f"    - {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
