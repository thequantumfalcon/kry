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
import math
import struct
import sys

ACTION_HASH_VERSION = 1
GENESIS = "0" * 64
_V5_BAD = "ff" * 8
TIER_SELF_REPORTED = "self_reported"
_ANCHORED_TIERS = {"server_witnessed", "attested"}


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_float(raw: str) -> float:
    # JSON exponent overflow must fail during parsing, just like literal Infinity.
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("nonfinite JSON float")
    return value


# SPEC Annex D: bound nesting before either runtime's recursive JSON parser/hash path.
_JSON_MAX_DEPTH = 64
_JSON_MAX_INTEGER_DIGITS = 640


def _check_json_depth(text: str) -> None:
    depth = 0
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > _JSON_MAX_DEPTH:
                raise ValueError("JSON nesting limit exceeded (64)")
        elif char in "]}":
            depth -= 1


def _check_value_limits(value) -> None:
    # Decoded-object APIs need the same boundary before recursive hashing/error rendering.
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, int) and abs(item) >= 10 ** _JSON_MAX_INTEGER_DIGITS:
            raise ValueError("JSON integer digit limit exceeded (640)")
        if isinstance(item, (dict, list)):
            depth += 1
            if depth > _JSON_MAX_DEPTH:
                raise ValueError("JSON nesting limit exceeded (64)")
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, depth) for child in children)


def _json_int(raw: str) -> int:
    # Below Python's configurable conversion threshold; no process-global limit changes.
    if len(raw) - raw.startswith("-") > _JSON_MAX_INTEGER_DIGITS:
        raise ValueError("JSON integer digit limit exceeded (640)")
    return int(raw)


def _json_loads(text: str):
    _check_json_depth(text)
    return json.loads(text, parse_constant=_reject_json_constant, parse_float=_json_float,
                      parse_int=_json_int)


def _canon(value) -> str:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _canon_f64(x) -> str:
    """REPLICA of kry_action._canon_f64 — EXACT IEEE-754 double, big-endian hex."""
    # SPEC §2.2: only a JSON number is encoded; anything else is non-numeric and takes the sentinel.
    # float() alone also accepts strings ("1700000001.0", "1_700_000_001") and booleans, so those
    # would hash exactly like the number they spell.
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return _V5_BAD
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
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
    # SPEC §4.4: only the anchored tier STRINGS count. A list/object tier (hash-bound like any other
    # field) is non-anchored, and testing it for set membership would raise TypeError.
    if not isinstance(tier, str):
        return TIER_SELF_REPORTED
    if tier in _ANCHORED_TIERS and not link.get("server_evidence_commit"):
        return TIER_SELF_REPORTED  # forged anchored tier with no witness -> not credited
    return tier


def verify_action_attestation(att: dict) -> tuple[bool, list[str], list[str]]:
    """Returns (ok, errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        _check_value_limits(att)
    except ValueError as exc:
        return False, [str(exc)], warnings

    if not isinstance(att, dict):      # SPEC §1: a non-object document is INVALID, never a crash
        return False, ["attestation must be a JSON object"], warnings
    if att.get("kind") != "kry_action_attestation":
        errors.append(f"not an action attestation (kind={att.get('kind')!r})")
        return False, errors, warnings
    hv = att.get("action_hash_version")
    if isinstance(hv, bool) or hv != ACTION_HASH_VERSION:   # True == 1 in Python, not in JSON
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
        rid = link.get("receipt_id")
        if not isinstance(rid, str):
            # Fail closed on a non-string receipt_id (SPEC §4.3): a list/dict is unhashable
            # and previously crashed the dedup set; an int/other type is not a valid id. An ABSENT
            # id is not a string either, so it gets no default (a unique "#i" would verify it).
            errors.append(f"link {i}: receipt_id must be a string")
            return False, errors, warnings
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
    action_count = att.get("action_count")
    if isinstance(action_count, bool) or action_count != len(links):   # True == 1 in Python only
        errors.append(f"action_count mismatch: declared {att.get('action_count')}, got {len(links)}")
        return False, errors, warnings

    # ── veracity_floor re-derivation (from coerced tiers) ──
    total = len(links)
    anchored = sum(1 for link in links if _effective_tier(link) in _ANCHORED_TIERS)
    coerced = sum(1 for link in links
                  if isinstance(link.get("evidence_tier"), str)
                  and link.get("evidence_tier") in _ANCHORED_TIERS and not link.get("server_evidence_commit"))
    if coerced:
        warnings.append(f"{coerced} link(s) claimed an anchored tier with NO witness — "
                        "coerced to self_reported (not credited to the floor)")
    derived_floor = round(anchored / total, 4) if total > 0 else 0.0
    # A non-dict `veracity` (e.g. an int/string from a tampered attestation) is ignored, not
    # crashed on — same isinstance-guard the savings verifier (kry_verify) uses. `... or {}`
    # alone mishandled a TRUTHY non-dict (e.g. veracity=1 -> (1).get(...) AttributeError).
    _v = att.get("veracity")
    v = _v if isinstance(_v, dict) else {}
    claimed = v.get("veracity_floor")
    if "veracity_floor" in v:
        if isinstance(claimed, bool) or not isinstance(claimed, (int, float)):
            return False, ["veracity_floor must be a finite JSON number"], warnings
        try:
            finite = math.isfinite(claimed)
        except OverflowError:
            finite = False
        if not finite:
            return False, ["veracity_floor must be a finite JSON number"], warnings
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
    try:
        _check_value_limits(att)
        _check_value_limits(anchor)
    except ValueError as exc:
        return False, [str(exc)]
    # Both inputs are untrusted JSON: a non-object anchor, a bool count or a non-list of links is
    # INVALID here, not an AttributeError / KeyError out of the CLI.
    if not isinstance(anchor, dict) or anchor.get("kind") != "kry_action_anchor":
        kind = anchor.get("kind") if isinstance(anchor, dict) else None
        errors.append(f"not an action anchor (kind={kind!r})")
        return False, errors
    a_count = anchor.get("count")
    a_tip = anchor.get("chain_tip")
    # Equal absent/malformed hashes do not establish a published commitment.
    if not isinstance(a_tip, str) or len(a_tip) != 64 or any(c not in "0123456789abcdef" for c in a_tip):
        return False, ["anchor chain_tip must be a 64-char lowercase hex chain hash"]
    links = att.get("links") if isinstance(att, dict) else None
    if not isinstance(links, list):
        errors.append("attestation has no links list to check against the anchor")
        return False, errors
    if isinstance(a_count, bool) or not isinstance(a_count, int) or a_count < 0:
        errors.append("anchor count missing/invalid")
        return False, errors
    if a_count > len(links):
        errors.append(f"anchor commits to {a_count} actions but attestation has only {len(links)} "
                      "— actions were DROPPED after the anchor was published")
        return False, errors
    # The chain_hash at position a_count must equal the published tip.
    last = links[a_count - 1] if a_count else None
    if a_count and not isinstance(last, dict):
        return False, ["attestation anchor link must be an object"]
    tip_at = GENESIS if a_count == 0 else last.get("chain_hash")
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

    # JSON is UTF-8: an implicit locale read (cp1252 on Windows) mangles a raw non-ASCII field and
    # reports a VALID attestation as a receipt_hash mismatch.
    # SPEC §4.3: malformed JSON, NaN/Infinity or an overflowing float is PARSE_ERROR, and a document
    # that is not an object is INVALID; neither may escape as a traceback.
    try:
        with open(args.attestation, encoding="utf-8") as fh:
            att = _json_loads(fh.read())
    except ValueError as exc:
        print("kry action-chain external verification")
        print("  VERDICT: PARSE_ERROR")
        print(f"    - invalid attestation JSON: {exc}")
        return 1
    except Exception as exc:
        print("kry action-chain external verification")
        print("  VERDICT: INVALID")
        print(f"    - attestation unreadable: {exc}")
        return 1
    if not isinstance(att, dict):
        print("kry action-chain external verification")
        print("  VERDICT: INVALID")
        print("    - attestation JSON must be an object")
        return 1

    ok, errors, warnings = verify_action_attestation(att)
    # A truthy non-object veracity (1, "x", [1]) survives `or {}`; it must not reach .get below.
    _v = att.get("veracity")
    v = _v if isinstance(_v, dict) else {}

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
        try:
            with open(args.anchor, encoding="utf-8") as fh:
                anchor = _json_loads(fh.read())
        except Exception as exc:      # an unreadable or malformed anchor fails the check, not the CLI
            a_ok, a_errors = False, [f"anchor unreadable: {exc}"]
        else:
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
    for _stream in (sys.stdout, sys.stderr):  # a cp1252 Windows console cannot encode the output glyphs
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    sys.exit(main())
