#!/usr/bin/env python3
"""KRY external verifier — a STRANGER's independent check (falsifier #1).

This is the executable form of the claim "a third party can verify a KRY
settlement from the public artifacts alone, without trusting the operator's
runtime." To make that claim honest rather than asserted, this file imports
NOTHING from the KRY package — only the Python standard library. If a verifier
needed package code or a live process, external verifiability would be false.

What a stranger can confirm with only:
    - an attestation JSON (kry.kry_attest.Attestation.to_public_json output)
    - optionally, the settlement registry JSONL (kry_data/kry_settlement_registry.jsonl)
    - optionally, an offer (party + KRY amount)

  1. CHAIN INTEGRITY  — chain_hash[i] == SHA256(chain_hash[i-1] : receipt_hash[i])
                        for every link; no receipt inserted/removed/altered.
  2. CONSERVATION     — sum of per-link kry_minted == declared total_kry, and
                        chain_head == the last link's chain_hash.
  3. VERACITY SURFACE — declared veracity_floor matches the per-link tiers
                        (how much of the balance is anchored by more than self-report
                        — external OR operator-run). See docs/KRY_VERACITY_BINDING.md.
  4. SETTLEMENT       — the registry chain (entry_hash == SHA256(prev:party:amount:grant_ids))
                        is intact, and the offer fits inside
                        attested_balance − already_settled[party] (double-spend).

What a stranger CANNOT learn (by design): any prompt, response, model name, or
cache key — the attestation is content-sealed.

Honest ceiling: this proves INTEGRITY + CONSERVATION + the declared trust surface.
It does NOT prove VERACITY (that the underlying efficiency events happened) — that
is the veracity_floor's job to disclose, not this script's to certify.

Usage:
    kry-verify attestation.json                  # after `pip install kry-attest`
    python3 scripts/kry_verify.py attestation.json
    python3 scripts/kry_verify.py attestation.json --registry kry_data/kry_settlement_registry.jsonl --party A --offer 5000
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import struct
import sys

_GENESIS = "0" * 64

# SPEC §3.5 (P-TOL): the ONE absolute tolerance for every §3.1/§3.5 derived-vs-declared numeric
# comparison, shared verbatim by verifiers/js/verify.mjs and kry.kry_attest. Those values are all
# SPEC-mandated to be round(x,4) or round(x,6), so the smallest REAL discrepancy is 1e-6; 1e-9 sits
# three decades below that and three decades above IEEE-754 accumulation noise at corpus scale, so
# it rejects every misstatement while absorbing last-ulp float noise. It does NOT apply to the
# constants SPEC states separately: §3.4.1's 1e-6 rate / 1e-3 multiplier, §3.7's -0.01 outcome
# guard, §4's 0.01 action floor.
_COMPARE_EPS = 1e-9

# ── Public reference constants (F2: publicly-checkable magnitude) ─────────────
# These MIRROR the published kry package constants (kry_mint._EARN_RATES, the
# per-model $/M price table in kry_token, the frontier baseline) as of
# _PRICE_AS_OF. A stranger copies them from the public repo — that is why a
# standalone verifier can carry them. A drift-guard test
# (tests/test_external_verify.py) asserts they still match the package source,
# so this copy can never silently diverge.
_PRICE_AS_OF = "2026-09-15"
_FRONTIER_USD_PER_M = 25.0
# S3: the KNOWN anchored tiers (must match kry_mint._ANCHORED_TIERS). The anchored fraction counts
# ONLY these — a forged or typo'd tier (e.g. magic_attested) is not anchored and can't inflate the floor.
_ANCHORED_TIERS = frozenset({"holdout_validated", "provider_metered", "tlsn_attested", "tee_attested"})
_EARN_RATES = {
    "cache_hit": 1.0, "l3_semantic_match": 0.8, "short_circuit": 1.0,
    "compression": 0.6, "feed_bag_deposit": 0.7, "continuity_capsule": 0.1,
    "cache_creation": 0.0,
}
_MODEL_USD_PER_M = {
    # Current provider model ids first (the package matches by substring in insertion order), then the
    # older class entries, which stay so receipts minted at those prices still verify.
    "claude-fable-5": 50.0, "claude-sonnet-5": 10.0, "claude-sonnet-4-6": 15.0, "claude-sonnet-4-5": 15.0,
    "claude-haiku-4-5": 5.0,
    "opus": 25.0, "sonnet": 7.5, "haiku": 1.25, "gpt-5": 10.0,
    "gpt-4o-mini": 0.60, "gpt-4o": 10.0,  # OpenAI list prices (mini before gpt-4o for substring match)
    "deepseek-v4-pro": 1.10, "deepseek": 0.55, "qwen": 1.25, "gemini": 0.0,
}


def legal_multipliers() -> set[float]:
    """The set of value multipliers a magnitude may legally use: each model's
    $/M ÷ frontier, plus 0.0 (free), 0.05 (unknown floor), 1.0 (legacy None), AND
    every non-negative pairwise DIFFERENCE of those. A cheaper-PAID displacement
    (e.g. OpenRouter serving instead of a frontier) saves only the price
    DIFFERENCE — vm(avoided) - vm(served) — which is still publicly-checkable
    arithmetic over the same public price table, so it is a legal magnitude."""
    base = {min(1.0, p / _FRONTIER_USD_PER_M) for p in _MODEL_USD_PER_M.values()}
    base |= {0.0, 0.05, 1.0}
    diffs = {round(a - b, 6) for a in base for b in base if a - b > 0}
    return base | diffs


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


_V5_BAD = "nonfinite"      # MUST match kry_mint._V5_BAD


def _canon_f64(x) -> str:
    """REPLICA of kry_mint._canon_f64 — v5 canonical hash-preimage number: the EXACT IEEE-754 double in
    big-endian hex (struct.pack('>d')); total (non-numeric/NaN/inf → sentinel). Byte-identical to the
    minter so this stdlib verifier reproduces the v5 block; a NON-Python verifier reproduces it the same
    way (parse the JSON number to a double, emit its 8 big-endian bytes as hex)."""
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


def _v4_public_block(link: dict) -> str:
    """Standalone REPLICA of kry_mint._v4_public_block — it MUST serialize byte-for-byte identically
    (pinned by test_external_verify, which verifies a real attestation through THIS stdlib verifier).
    Binds the public economic block into chain_hash so a forged tier / kry_minted / earn_rate / token
    count breaks the chain on the public surface here too. v5 binds economic numbers + ts as the EXACT
    IEEE-754 double in big-endian hex (language-neutral); v4 and earlier keep CPython float encoding.
    v6 binds `receipt_id` and v7 binds `event_type` (both plain strings) so a promotion's `supersedes`
    target cannot be re-pointed and a same-rate event-type relabel is caught. Additive +
    version-dispatched — v4/v5/v6 receipts hash byte-identically to before."""
    hv = link.get("hash_version", 1)
    if isinstance(hv, int) and not isinstance(hv, bool) and hv >= 5:
        block = {
            "hash_version": hv,
            "tokens_saved": _canon_f64(link.get("tokens_saved", 0.0)),
            "ts": _canon_f64(link.get("ts")),
            "evidence_tier": link.get("evidence_tier", "self_reported"),
            "metered_tokens": link.get("metered_tokens"),
            "kry_minted": _canon_f64(link.get("kry_minted")),
            "earn_rate": _canon_f64(link.get("earn_rate", 0.0)),
        }
    else:
        block = {
            "hash_version": hv,
            "tokens_saved": link.get("tokens_saved", 0.0),
            "ts": link.get("ts"),
            "evidence_tier": link.get("evidence_tier", "self_reported"),
            "metered_tokens": link.get("metered_tokens"),
            "kry_minted": link.get("kry_minted"),
            "earn_rate": link.get("earn_rate", 0.0),
        }
    # F2: bind a promotion's re-tiering target ONLY when present, byte-identical to the minter, so a
    # forged/altered/removed supersedes breaks the chain here (the public surface) too.
    sup = link.get("supersedes")
    if sup is not None:
        block["supersedes"] = sup
    # v6: bind receipt_id ALWAYS (byte-identical to the minter) so a relabel of a link's
    # receipt_id — the overlay's match key for a promotion's `supersedes` — breaks the chain here.
    if isinstance(hv, int) and not isinstance(hv, bool) and hv >= 6:
        block["receipt_id"] = link.get("receipt_id") or ""
    # v7: bind event_type (byte-identical to the minter) — closes the same-earn_rate relabel.
    if isinstance(hv, int) and not isinstance(hv, bool) and hv >= 7:
        block["event_type"] = link.get("event_type") or ""
    return json.dumps(block, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_load(f):
    return _json_loads(f.read())


def _json_float(raw: str) -> float:
    # JSON exponent overflow must fail during parsing, just like literal Infinity.
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("nonfinite JSON float")
    return value


# SPEC Annex D: bound nesting before either runtime's recursive JSON parser/hash path.
_JSON_MAX_DEPTH = 64
_JSON_MAX_INTEGER_DIGITS = 640
_JSON_MAX_BYTES = 16 * 1024 * 1024


class _JsonSizeError(ValueError):
    """An oversized document is PARSE_ERROR, including when it is an anchor."""


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
    if len(text.encode("utf-8")) > _JSON_MAX_BYTES:
        raise _JsonSizeError("JSON document size limit exceeded (16 MiB)")
    _check_json_depth(text)
    return json.loads(text, parse_constant=_reject_json_constant, parse_float=_json_float,
                      parse_int=_json_int)


def _read_json(path: str):
    # Read bytes to count CRLF and multibyte characters exactly, before decoding or parsing.
    if path == "-":
        raw = sys.stdin.buffer.read(_JSON_MAX_BYTES + 1)
    else:
        with open(path, "rb") as stream:
            raw = stream.read(_JSON_MAX_BYTES + 1)
    if len(raw) > _JSON_MAX_BYTES:
        raise _JsonSizeError("JSON document size limit exceeded (16 MiB)")
    return _json_loads(raw.decode("utf-8"))


def _json_dumps(data: object, **kwargs) -> str:
    kwargs.setdefault("allow_nan", False)
    return json.dumps(data, **kwargs)


def _json_clean(data: object) -> object:
    return json.loads(_json_dumps(data))


def _finite_number(value, field: str, *, positive: bool = False,
                   nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite JSON number")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if positive and value <= 0:
        raise ValueError(f"{field} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _json_integer(value, field: str, *, nonnegative: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a JSON integer")
    if nonnegative and value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _required_string(value, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _attestation_hash(data: dict) -> str:
    """Canonical hash of an attestation with its hash field blanked."""
    canonical = _json_clean(data)
    canonical["attestation_hash"] = ""
    return hashlib.sha256(
        _json_dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _magnitude_errors(link: dict) -> list[str]:
    """F2: recompute magnitude from public data. A receipt that exposes its
    inputs (tokens_saved, earn_rate) must satisfy kry = tokens × rate × M where
    rate is the published EARN_RATES value and M is a published multiplier.
    Legacy/pre-F2 links (no inputs exposed) are skipped — honestly uncheckable."""
    errors: list[str] = []
    try:
        kry_minted = _finite_number(link.get("kry_minted"),
                                    f"seq {link.get('seq')}: kry_minted",
                                    nonnegative=True)
        ts = _finite_number(link.get("tokens_saved", 0.0),
                            f"seq {link.get('seq')}: tokens_saved",
                            nonnegative=True)
        rate = _finite_number(link.get("earn_rate", 0.0),
                              f"seq {link.get('seq')}: earn_rate",
                              nonnegative=True)
    except ValueError as exc:
        return [str(exc)]
    # A link that DECLARES its inputs cannot mint positive KRY from zero tokens/rate
    # (0 × 0 × M = 0) — a positive kry_minted is fabricated (zero-rate magnitude bypass).
    # Only a genuine legacy link that OMITS the inputs is honestly uncheckable.
    declares_inputs = "earn_rate" in link and "tokens_saved" in link
    # F2 legacy exemption is bounded by version: v4 is where the economic block (tokens_saved,
    # earn_rate, kry_minted) became hash-bound, so a v4+ link that omits its inputs is not an
    # honestly-uncheckable legacy receipt — it is a modern receipt dodging the magnitude check.
    hv = link.get("hash_version")
    if isinstance(hv, int) and not isinstance(hv, bool) and hv >= 4 and not declares_inputs:
        return [f"seq {link.get('seq')}: hash_version {hv} link must declare tokens_saved and "
                f"earn_rate — the legacy exemption covers pre-v4 receipts only"]
    et = link.get("event_type", "")
    # F3: an UNKNOWN event_type must still use mint's 0.5 fallback rate — reject an arbitrary rate
    # paired with an off-table event_type instead of silently skipping the check.
    pub_rate = _EARN_RATES.get(et, 0.5)
    if declares_inputs and abs(rate - pub_rate) > 1e-6:
        errors.append(
            f"seq {link.get('seq')}: earn_rate {rate} != published {pub_rate} "
            f"for '{et}' — non-standard rate")
    if ts <= 0 or rate <= 0:
        if declares_inputs and kry_minted > 0:
            errors.append(f"seq {link.get('seq')}: kry_minted {kry_minted} with tokens_saved={ts} "
                          f"/ earn_rate={rate} — magnitude not derivable from declared inputs")
        return errors
    basis = ts * rate
    if basis == 0:
        errors.append(f"seq {link.get('seq')}: magnitude basis underflowed to zero")
        return errors
    implied = (kry_minted / basis if math.isfinite(basis)
               else (kry_minted / ts) / rate)
    if not any(abs(implied - m) <= 1e-3 for m in legal_multipliers()):
        errors.append(
            f"seq {link.get('seq')}: implied price multiplier {implied:.4f} is "
            f"not a published value — magnitude used a non-public price")
    return errors


def _tier_schema_errors(link: dict) -> list[str]:
    """Validate present timestamps and the required T1 billing fields."""
    if link.get("evidence_tier", "self_reported") != "provider_metered":
        if "ts" not in link:
            return []
        try:
            _finite_number(link.get("ts"), f"seq {link.get('seq')}: ts")
        except ValueError as exc:
            return [str(exc)]
        return []
    ts = link.get("ts")
    try:
        _finite_number(ts, "ts", nonnegative=True)
    except ValueError:
        return [f"seq {link.get('seq')}: provider_metered link missing numeric ts"]
    metered = link.get("metered_tokens")
    if not isinstance(metered, list) or len(metered) != 2:
        return [f"seq {link.get('seq')}: provider_metered link missing metered_tokens"]
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in metered):
        return [f"seq {link.get('seq')}: provider_metered metered_tokens must be integers"]
    p, c = metered
    if p < 0 or c < 0:
        return [f"seq {link.get('seq')}: provider_metered metered_tokens must be non-negative"]
    return []


def verify_attestation(attestation: dict) -> tuple[bool, list[str]]:
    """Re-derive the proof chain from the public links alone."""
    errors: list[str] = []
    try:
        _check_value_limits(attestation)
    except ValueError as exc:
        return False, [str(exc)]
    if not isinstance(attestation, dict):
        return False, ["attestation must be a JSON object"]
    if attestation.get("kind") == "kry_action_attestation":
        return False, ["action attestation requires the action verifier"]
    links = attestation.get("links")
    if not isinstance(links, list):
        errors.append("links must be a JSON list")
        links = []
    try:
        receipts = _json_integer(attestation.get("receipts"), "receipts", nonnegative=True)
    except ValueError as exc:
        errors.append(str(exc))
        receipts = None
    if receipts != len(links):
        errors.append(
            f"receipts mismatch: declared {attestation.get('receipts')}, "
            f"links contain {len(links)}")
    if attestation.get("chain_valid") is not True:
        errors.append("chain_valid is not true")
    # SPEC §3.1: these envelope fields MUST be PRESENT. A wholly absent key is not a silent
    # default — an absent total_kry is not a declared 0.0, an absent veracity is not "no claim".
    for field in ("total_kry", "usd_equivalent", "veracity"):
        if field not in attestation:
            errors.append(f"{field} missing — SPEC §3.1 requires it")

    prev = _GENESIS
    prev_version = 0
    running_kry = 0.0
    tier_kry: dict[str, float] = {}
    type_counts: Counter = Counter()
    promotions: list = []      # F5: (superseded_receipt_id, promoting_tier)
    kry_by_receipt: dict = {}  # receipt_id -> (tier, kry), to reproduce the promotion overlay
    for _pos, link in enumerate(links):
        if not isinstance(link, dict):
            errors.append("link must be a JSON object")
            continue
        seq = link.get("seq")
        try:
            _json_integer(seq, f"seq {seq}", nonnegative=True)
            receipt_hash = _required_string(link.get("receipt_hash"), f"seq {seq}: receipt_hash")
            chain_hash = _required_string(link.get("chain_hash"), f"seq {seq}: chain_hash")
            event_type = _required_string(link.get("event_type"), f"seq {seq}: event_type")
            kry_minted = _finite_number(link.get("kry_minted"), f"seq {seq}: kry_minted",
                                        nonnegative=True)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        # v4 binds the public economic block into chain_hash (forged tier/payout/rate breaks the chain
        # on the public surface); legacy (<4) links use the prev:receipt formula.
        hv = link.get("hash_version", 1)
        # SPEC §3.6 (fail closed): this verifier defines the v4..v7 block shapes plus the legacy
        # <=3 formula. An unrecognized version must be INVALID — never hashed with the newest
        # shape we happen to know, which would verify a v8 link against a v7-shaped block.
        if isinstance(hv, bool) or not isinstance(hv, int) or hv > 7:
            errors.append(f"seq {seq}: unrecognized hash_version {link.get('hash_version')!r} — "
                          f"this verifier understands 7 and below (fail closed)")
            prev = chain_hash
            continue
        # Monotonic version: a v4 link can't be followed by a legacy one (partial-tail downgrade).
        if hv < prev_version:
            errors.append(f"seq {seq}: hash_version {hv} < previous {prev_version} — "
                          f"version downgrade (partial-tail rollback attempt)")
        prev_version = max(prev_version, hv)
        if hv >= 4:
            try:
                block = _v4_public_block(link)
            except (ValueError, TypeError) as exc:   # e.g. a NaN in a v4 block field (dict input)
                errors.append(f"seq {seq}: v4 block field not serializable: {exc}")
                prev = chain_hash
                continue
        # SPEC §2.1 hashes the UTF-8 bytes of the preimage, and receipt_hash / prev enter it raw. A
        # lone surrogate (legal as a JSON \u escape) has no UTF-8 encoding, so no chain hash can
        # match it: INVALID, never a crash and never a hash over substituted bytes.
        try:
            expected = (_sha(f"{prev}:{receipt_hash}:{block}") if hv >= 4
                        else _sha(f"{prev}:{receipt_hash}"))
        except UnicodeEncodeError:
            errors.append(f"seq {seq}: chain preimage is not valid Unicode (unpaired surrogate)")
            prev = chain_hash
            continue
        if chain_hash != expected:
            errors.append(
                f"seq {seq}: chain link broken — "
                f"receipt inserted/removed/altered")
        running_kry += kry_minted
        type_counts[event_type] += 1
        tier = link.get("evidence_tier", "self_reported")
        if not isinstance(tier, str):
            errors.append(f"seq {seq}: evidence_tier must be a string")
            tier = "self_reported"
        # The tier is only bound on the PUBLIC surface at v4 (the v4 block above). A pre-v4
        # link claiming a non-self_reported tier is operator-asserted, not chain-bound, so it
        # must not inflate the anchored fraction of the veracity floor — reject and coerce.
        if hv < 4 and tier != "self_reported":
            errors.append(f"seq {seq}: hash_version {hv} cannot carry a non-self_reported "
                          f"tier ({tier}) — unbound on the public surface (only v4+ binds it)")
            tier = "self_reported"
        tier_kry[tier] = tier_kry.get(tier, 0.0) + kry_minted
        rid = link.get("receipt_id")              # F5/A1-1: reproduce the promotion overlay (see below)
        # SPEC §3.2 / Annex C: non-string ids are INVALID, even when no overlay uses them.
        if rid is not None and not isinstance(rid, str):
            errors.append(f"seq {seq}: receipt_id must be a string")
        # A1-1: only a HASH-BOUND (v6+) receipt may anchor a promotion (a v4/v5 receipt_id is mutable);
        # reject duplicate ids (a forged chain could collide two v6+ ids → last-wins picks the larger).
        # SPEC §3.4 / §3.7: only a non-empty STRING id enters the map — a list id is unhashable here,
        # and an integer id would create duplicates and promotions the spec does not define.
        if isinstance(rid, str) and rid and isinstance(hv, int) and not isinstance(hv, bool) and hv >= 6:
            if rid in kry_by_receipt:
                errors.append(f"seq {seq}: duplicate receipt_id {rid!r} among hash-bound receipts")
            kry_by_receipt[rid] = (tier, kry_minted, _pos)
        sup = link.get("supersedes")
        if sup is not None and not isinstance(sup, str):
            errors.append(f"seq {seq}: supersedes must be a string")
        # invariant #4 ENFORCED (stranger replica): a promotion must be zero-value — a positive-value
        # tlsn/tee link that also supersedes keeps its own value only; it cannot re-tier the target too.
        # SPEC §3.7: a promotion names its target with a non-empty STRING supersedes.
        if tier in ("tlsn_attested", "tee_attested") and isinstance(sup, str) and sup and kry_minted <= 0:
            promotions.append((sup, tier, _pos))
        errors.extend(_magnitude_errors(link))   # F2: magnitude is public arithmetic
        errors.extend(_tier_schema_errors(link))
        prev = chain_hash

    # Conservation: the declared aggregate must equal the chain sum.
    try:
        total_kry = _finite_number(attestation.get("total_kry", 0.0), "total_kry",
                                   nonnegative=True)
    except ValueError as exc:
        errors.append(str(exc))
        total_kry = 0.0
    # SPEC §3.1: total_kry MUST equal round(Σ kry_minted, 4) — compare against the SAME rounding
    # the spec mandates, at the one _COMPARE_EPS tolerance (P-TOL).
    derived_total = round(running_kry, 4)
    if abs(derived_total - total_kry) > _COMPARE_EPS:
        errors.append(
            f"total_kry mismatch: declared {attestation.get('total_kry')}, "
            f"chain sums to {derived_total}")

    # Head anchor must match the last link. SPEC §3.1: an EMPTY chain is checked too — its head
    # must be the genesis value, so a declared (or absent) head cannot ride along unverified.
    if links:
        # A non-object last link is already an error above; it has no chain_hash to match.
        last_hash = links[-1].get("chain_hash") if isinstance(links[-1], dict) else None
        if attestation.get("chain_head") != last_hash:
            errors.append("chain_head does not match last link")
    elif attestation.get("chain_head") != _GENESIS:
        errors.append("chain_head must be the genesis value for an empty chain")
    if not isinstance(attestation.get("event_type_counts"), dict):
        errors.append("event_type_counts must be a JSON object")
    # SPEC §3.1 / §3.5: a count is a JSON number. Python's True == 1 would make a boolean count compare
    # equal, which verifiers/js (and the JSON data model) does not.
    elif attestation.get("event_type_counts") != dict(type_counts) or any(
            isinstance(n, bool) for n in attestation["event_type_counts"].values()):
        errors.append(
            f"event_type_counts mismatch: declared {attestation.get('event_type_counts')}, "
            f"links imply {dict(type_counts)}")
    # SPEC §3.1: usd_equivalent MUST equal round(total_kry * 0.000025, 6) — over the ROUNDED
    # total_kry, so a cold implementer derives the same number from the spec text alone.
    expected_usd = round(derived_total * (_FRONTIER_USD_PER_M / 1_000_000), 6)
    try:
        usd_equivalent = _finite_number(attestation.get("usd_equivalent", 0.0),
                                        "usd_equivalent", nonnegative=True)
    except ValueError as exc:
        errors.append(str(exc))
        usd_equivalent = 0.0
    if abs(usd_equivalent - expected_usd) > _COMPARE_EPS:
        errors.append(
            f"usd_equivalent mismatch: declared {attestation.get('usd_equivalent')}, "
            f"links imply {expected_usd}")
    claimed_hash = attestation.get("attestation_hash")
    if not isinstance(claimed_hash, str) or not claimed_hash:
        errors.append("attestation_hash missing")
    else:
        try:
            expected_hash = _attestation_hash(attestation)
        except ValueError as exc:
            errors.append(f"attestation JSON is not standards-compliant: {exc}")
        else:
            if claimed_hash != expected_hash:
                errors.append("attestation_hash mismatch — public metadata may have been altered")

    # F5: reproduce the promotion overlay the minter applies — a zero-value tlsn/tee promotion re-tiers
    # its superseded receipt's value onto the promoting tier, so the declared (overlaid) by_tier is
    # verifiable here. Replica of kry_mint._apply_promotion_overlay; no-op without promotions.
    for src_id, to_tier, promo_pos in promotions:
        src = kry_by_receipt.get(src_id)
        if not src:
            continue
        src_tier, src_kry, src_pos = src
        # A1-1 (order): a promotion may re-tier ONLY a receipt seen EARLIER in the verified scan; a
        # target at/after the promotion's position is a forward-reference capture — refuse it. Consume
        # the target so it can be promoted at most once.
        if src_pos >= promo_pos:
            continue
        if src_kry <= 0:
            continue
        tier_kry[src_tier] = tier_kry.get(src_tier, 0.0) - src_kry
        tier_kry[to_tier] = tier_kry.get(to_tier, 0.0) + src_kry
        del kry_by_receipt[src_id]
    # OUTCOME GUARD (see kry_mint._apply_promotion_overlay SAFETY CONTRACT): the overlay is a pure
    # transfer, so no tier may be negative afterwards — a negative means a promotion moved value that
    # was not there (forged chain / overlay bug), caught even if an invariant above were ever missed.
    if any(val < -0.01 for val in tier_kry.values()):
        errors.append("veracity by_tier went negative after the promotion overlay — "
                      "a promotion moved value that was not there")

    # Trust surface must be honest: declared floor must match the per-link tiers.
    v = attestation.get("veracity")
    # SPEC §3.1 lists `veracity` as required and §3.5 defines it as an OBJECT, so a key that is
    # PRESENT but null / a number / a string / a list is INVALID — a non-object cannot carry the
    # trust surface, and treating it as "no claim" would let an operator drop the floor silently.
    if "veracity" in attestation and not isinstance(v, dict):
        errors.append("veracity must be a JSON object — SPEC §3.5")
    # SPEC §3.5 requires all four fields. A missing sub-key used to SKIP the whole block below
    # (by_tier) or silently read as 0.0 (the numbers), so an operator could drop the trust surface
    # and still verify — while verifiers/js rejected the same input. Require them explicitly.
    # `externally_anchored_kry` stays accepted as the pre-rename alias for `anchored_kry`.
    if isinstance(v, dict):
        for _field in ("by_tier", "self_reported_kry", "veracity_floor"):
            if _field not in v:
                errors.append(f"veracity.{_field} missing — SPEC §3.5 requires it")
        if "anchored_kry" not in v and "externally_anchored_kry" not in v:
            errors.append("veracity.anchored_kry missing — SPEC §3.5 requires it")
    # SPEC §3.5: the floor, anchored_kry and self_reported_kry comparisons run whenever `veracity` is
    # an object. Gated on a non-null by_tier, `"by_tier": null` would switch every one of them
    # off, and an all-self_reported chain could declare veracity_floor 1.0 and verify VALID.
    if isinstance(v, dict):
        # S3: anchored = only KNOWN anchored tiers, not "anything that isn't self_reported".
        anchored = sum(val for t, val in tier_kry.items() if t in _ANCHORED_TIERS)
        # SPEC §3.5: every one of these is a round(...,4) value derived from the ROUNDED
        # anchored/total, so the comparisons below are against the same rounding (P-TOL).
        derived_anchored = round(anchored, 4)
        derived_self = round(tier_kry.get("self_reported", 0.0), 4)
        derived = round(derived_anchored / derived_total, 4) if derived_total > 0 else 0.0
        by_tier = {t: round(val, 4) for t, val in tier_kry.items()}
        claimed_by_tier = v.get("by_tier")
        if not isinstance(claimed_by_tier, dict):
            # An absent map is already reported as missing above; a present non-object (null
            # included) is malformed. Either way there is no declared map to compare below.
            if "by_tier" in v:
                errors.append("veracity.by_tier must be a JSON object")
            claimed_by_tier = None
        # Compare the validated floats only: subtracting a raw arbitrary-size JSON integer from a
        # float raises OverflowError instead of reporting the mismatch (SPEC §3.5).
        finite_by_tier: dict[str, float] = {}
        if claimed_by_tier is not None:
            for tier, value in claimed_by_tier.items():
                if not isinstance(tier, str):
                    errors.append("veracity.by_tier keys must be strings")
                    continue
                try:
                    finite_by_tier[tier] = _finite_number(value, f"veracity.by_tier.{tier}",
                                                          nonnegative=True)
                except ValueError as exc:
                    errors.append(str(exc))
        # P-TOL: the key SET must match exactly (size + every derived key present, so an invented or
        # dropped tier is caught) and every value must land within _COMPARE_EPS of its round-4
        # derivation. Same rule, same number, as verifiers/js and kry_attest.
        if claimed_by_tier is not None and (len(claimed_by_tier) != len(by_tier) or any(
                t not in finite_by_tier or abs(finite_by_tier[t] - by_tier[t]) > _COMPARE_EPS
                for t in by_tier)):
            errors.append(
                f"veracity by_tier mismatch: declared {v.get('by_tier')}, "
                f"links imply {by_tier}")
        try:
            externally_anchored = _finite_number(
                v.get("anchored_kry", v.get("externally_anchored_kry", 0.0)),   # legacy alias (round-4 rename)
                "veracity.anchored_kry",
                nonnegative=True,
            )
        except ValueError as exc:
            errors.append(str(exc))
            externally_anchored = 0.0
        try:
            self_reported = _finite_number(v.get("self_reported_kry", 0.0),
                                           "veracity.self_reported_kry",
                                           nonnegative=True)
        except ValueError as exc:
            errors.append(str(exc))
            self_reported = 0.0
        try:
            veracity_floor = _finite_number(v.get("veracity_floor", 0.0),
                                            "veracity.veracity_floor",
                                            nonnegative=True)
        except ValueError as exc:
            errors.append(str(exc))
            veracity_floor = 0.0
        if abs(externally_anchored - derived_anchored) > _COMPARE_EPS:
            errors.append("anchored_kry mismatch")
        if abs(self_reported - derived_self) > _COMPARE_EPS:
            errors.append("self_reported_kry mismatch")
        if abs(veracity_floor - derived) > _COMPARE_EPS:
            errors.append(
                f"veracity_floor mismatch: declared {v.get('veracity_floor')}, "
                f"links imply {derived:.4f} — trust surface misstated")

    return len(errors) == 0, errors


def verify_registry(entries: list[dict]) -> tuple[bool, list[str]]:
    """Replay the settlement registry chain; any edit breaks a hash."""
    errors: list[str] = []
    prev = _GENESIS
    for i, e in enumerate(entries, 1):
        if not isinstance(e, dict):
            errors.append(f"entry {i}: registry entry must be a JSON object")
            continue
        try:
            party = _required_string(e.get("party"), f"entry {i}: party")
            amount = _finite_number(e.get("amount"), f"entry {i}: amount", positive=True)
            entry_hash = _required_string(e.get("entry_hash"), f"entry {i}: entry_hash")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if len(entry_hash) != 64 or any(ch not in "0123456789abcdef" for ch in entry_hash):
            errors.append(f"entry {i}: entry_hash must be 64 lowercase hex characters")
            continue
        prev_hash = e.get("prev_hash")
        if prev_hash is not None and prev_hash != prev:
            errors.append(f"entry {i} ({party}): prev_hash does not match previous entry")
        # Grant-id component: individual settlements bind their grant_id; compaction
        # checkpoints bind the sorted union of collapsed ids (the no-double-settle guard).
        try:
            if "grant_id" in e and "grant_ids" in e:
                raise ValueError("both grant id forms are present")
            if "grant_ids" in e:
                grant_ids = e["grant_ids"]
                if not isinstance(grant_ids, list):
                    raise ValueError("grant_ids must be a list")
            else:
                grant_ids = [e["grant_id"]] if "grant_id" in e else []
            if any(not isinstance(gid, str) or not gid or ":" in gid or "," in gid
                   for gid in grant_ids):
                raise ValueError("grant ids must be nonempty strings without delimiters")
            if len(set(grant_ids)) != len(grant_ids):
                raise ValueError("duplicate grant ids")
            grant_payload = ",".join(sorted(grant_ids))
        except ValueError:
            errors.append(f"entry {i} ({party}): malformed grant ids — tampered")
            prev = entry_hash
            continue
        try:
            expected = _sha(f"{prev}:{party}:{amount}:{grant_payload}")
        except UnicodeEncodeError:   # an unpaired surrogate has no UTF-8 bytes to hash
            errors.append(f"entry {i}: registry fields are not valid Unicode — tampered")
            prev = entry_hash
            continue
        if entry_hash != expected:
            errors.append(f"entry {i} ({party}): hash broken — tampered")
        prev = entry_hash
    return len(errors) == 0, errors


def settled_by_party(entries: list[dict]) -> dict[str, float]:
    """Cumulative {party: settled_kry} replayed from the registry."""
    totals: dict[str, float] = {}
    for e in entries:
        party = e["party"]
        amount = float(e["amount"])
        totals[party] = totals.get(party, 0.0) + amount
    return totals


def verify_registry_anchor(entries: list[dict], anchor: dict) -> tuple[bool, list[str]]:
    """S7: detect a registry rollback / un-spend against a PUBLISHED registry anchor obtained
    out-of-band. Settled totals are monotonic (only grow; compaction preserves them), so for every
    anchored party the LIVE cumulative-settled must be >= the anchored amount. Standalone replica of
    kry_settlement.verify_registry_against_anchor — only as strong as the anchor's external publication."""
    if not isinstance(anchor, dict) or anchor.get("schema") != "kry_settlement_anchor/v1":
        return False, ["registry anchor must be a kry_settlement_anchor/v1 object"]
    anchored = anchor.get("settled")
    if not isinstance(anchored, dict):
        return False, ["anchor.settled must be an object of {party: cumulative_kry}"]
    ok, errs = verify_registry(entries)
    if not ok:
        return False, ["live registry is not internally valid: " + "; ".join(str(e) for e in errs[:2])]
    live = settled_by_party(entries)
    errors: list[str] = []
    for party, amt in anchored.items():
        try:
            anchored_amt = _finite_number(amt, f"anchor.settled[{party}]", nonnegative=True)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if live.get(party, 0.0) < anchored_amt - 1e-9:
            errors.append(f"party {party}: live settled {live.get(party, 0.0)} < anchored "
                          f"{anchored_amt} — rollback/un-spend detected")
    return len(errors) == 0, errors


def verify_settlement(
    attestation: dict,
    registry_entries: list[dict],
    party: str,
    offer_amount: float,
) -> tuple[bool, list[str]]:
    """Snapshot accounting check: attestation valid + registry intact +
    the offer fits inside the attested balance net of what the party already
    settled under the supplied party label. Ownership is not verified: a
    different party can present the same public attestation. Authentication
    and independently enrolled identity-to-chain binding are external policy.

    NOTE (HOLE D corollary): this registry check is post-facto and snapshot-based
    — it is sound only against the COMPLETE, MERGED federation registry. It cannot
    catch two nodes concurrently settling the same balance against unmerged
    registries; real-time atomic prevention is per-process. See
    docs/KRY_VERACITY_BINDING.md."""
    ok_att, errs = verify_attestation(attestation)
    errors = list(errs)
    try:
        party = _required_string(party, "settlement party")
        offer_amount = _finite_number(offer_amount, "offer amount", positive=True)
    except ValueError as exc:
        errors.append(str(exc))
    ok_reg, reg_errs = verify_registry(registry_entries)
    errors += reg_errs
    if ok_att and ok_reg and not errors:
        attested = attestation.get("total_kry", 0.0)
        already = settled_by_party(registry_entries).get(party, 0.0)
        available = attested - already
        if offer_amount > available + 0.01:
            errors.append(
                f"double-spend/overclaim: offer {offer_amount:.0f} > available "
                f"{available:.0f} (attested {attested:.0f} − settled {already:.0f})")
    return len(errors) == 0, errors


def _read_registry(path: str) -> list[dict]:
    entries: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(_json_loads(line))
    return entries


def verify_attestation_against_anchor(attestation: dict, anchor: dict) -> tuple[bool, list[str]]:
    """Anchor consistency only; call verify_attestation separately for full validity.
    The attestation must be an object with a links list whose objects expose seq and chain_hash.
    An empty list is consistent with a count-zero genesis anchor, but not a positive count.
    verify_attestation proves the chain is internally consistent;
    it cannot tell an honest chain from one the operator re-derived from genesis. A chain-head
    anchor {count, tip} the operator PUBLISHED externally closes that gap: the attestation's link
    at seq==count must still have chain_hash==tip. A re-mint of any receipt <= count changes that
    hash. Only meaningful if the anchor came from the operator's external publication, obtained
    out-of-band — an anchor handed over at verify time proves nothing."""
    try:
        _check_value_limits(attestation)
        _check_value_limits(anchor)
    except ValueError as exc:
        return False, [str(exc)]
    if not isinstance(attestation, dict):
        return False, ["attestation must be a JSON object"]
    links = attestation.get("links")
    if not isinstance(links, list):
        return False, ["attestation has no links to check against the anchor"]
    if any(not isinstance(link, dict) or "seq" not in link or "chain_hash" not in link
           for link in links):
        return False, ["anchor links must be objects with seq and chain_hash"]
    if not isinstance(anchor, dict) or anchor.get("schema") != "kry_chain_anchor/v1":
        return False, ["anchor must be a kry_chain_anchor/v1 object"]
    count, tip = anchor.get("count"), anchor.get("tip")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return False, ["anchor.count must be a non-negative integer"]
    if not isinstance(tip, str) or len(tip) != 64:
        return False, ["anchor.tip must be a 64-char hex chain hash"]
    if count == 0:
        return (tip == _GENESIS), ([] if tip == _GENESIS else ["anchor.count 0 but tip is not genesis"])
    match = next((ln for ln in links if isinstance(ln["seq"], int)
                  and not isinstance(ln["seq"], bool) and ln["seq"] == count), None)
    if match is None:
        return False, [f"attestation has no link at seq {count} — chain shorter than the published "
                       f"anchor (rollback/re-mint/truncation)"]
    if match.get("chain_hash") != tip:
        return False, [f"chain hash at seq {count} does not match the published anchor — "
                       f"retroactive re-mint detected"]
    return True, []


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="KRY external verifier (stdlib-only)")
    p.add_argument("attestation", help="path to attestation JSON")
    p.add_argument("--registry", help="path to settlement registry JSONL")
    p.add_argument("--party", help="settling party (with --offer)")
    p.add_argument("--offer", type=float, help="offered KRY amount to check")
    p.add_argument("--anchor", help="path to a PUBLISHED kry_chain_anchor JSON; checks the "
                                    "attestation's chain still carries the anchored prefix (re-mint check)")
    p.add_argument("--registry-anchor", help="path to a PUBLISHED kry_settlement_anchor/v1 JSON; with "
                                             "--registry, detects a settlement-registry rollback (live "
                                             "per-party settled must not drop below the anchored totals)")
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        print("VERDICT: INVALID")
        return 1

    try:
        att = _read_json(args.attestation)
    except ValueError as exc:
        print("KRY external verification — attestation")
        print("  VERDICT: PARSE_ERROR")
        print(f"    - invalid attestation JSON: {exc}")
        return 1
    except Exception as exc:
        print("KRY external verification — attestation")
        print("  VERDICT: INVALID")
        print(f"    - attestation unreadable: {exc}")
        return 1
    if not isinstance(att, dict):
        print("KRY external verification — attestation")
        print("  VERDICT: INVALID")
        print("    - attestation JSON must be an object")
        return 1

    if args.registry and args.party and args.offer is not None:
        scope = f"settlement: {args.party} offering {args.offer:.0f} KRY"
        try:
            entries = _read_registry(args.registry)
        except Exception as exc:
            ok, errors = False, [f"registry unreadable: {exc}"]
        else:
            ok, errors = verify_settlement(att, entries, args.party, args.offer)
    else:
        ok, errors = verify_attestation(att)
        scope = "attestation"

    anchor_line = None
    if args.anchor is not None:
        try:
            anchor = _read_json(args.anchor)
            a_ok, a_errs = verify_attestation_against_anchor(att, anchor)
        except _JsonSizeError as exc:
            print("VERDICT: PARSE_ERROR")
            print(f"  - invalid anchor JSON: {exc}")
            return 1
        except Exception as exc:
            a_ok, a_errs = False, [f"anchor unreadable: {exc}"]
        ok = ok and a_ok
        errors = errors + [f"anchor: {e}" for e in a_errs]
        anchor_line = ("PASS — chain still carries this anchor's prefix (anchored entries not re-minted)"
                       if a_ok else "FAIL — re-mint/rollback vs this anchor")

    # S7: registry rollback check against a published settlement anchor (out-of-band).
    registry_anchor_line = None
    if args.registry_anchor is not None:
        if not args.registry:
            ok = False
            errors = errors + ["registry-anchor: --registry-anchor requires --registry"]
            registry_anchor_line = "FAIL — needs --registry"
        else:
            try:
                reg_anchor = _read_json(args.registry_anchor)
                ra_ok, ra_errs = verify_registry_anchor(_read_registry(args.registry), reg_anchor)
            except _JsonSizeError as exc:
                print("VERDICT: PARSE_ERROR")
                print(f"  - invalid registry anchor JSON: {exc}")
                return 1
            except Exception as exc:
                ra_ok, ra_errs = False, [f"registry anchor unreadable: {exc}"]
            ok = ok and ra_ok
            errors = errors + [f"registry-anchor: {e}" for e in ra_errs]
            registry_anchor_line = ("PASS — live settled >= published per-party totals (no rollback)"
                                    if ra_ok else "FAIL — registry rollback/un-spend vs the anchor")

    # HOLE #22: coerce a non-dict `veracity` (incl. JSON null) to {} so the display below can't
    # crash the stranger-facing CLI with an AttributeError on `v.get(...)`. verify_attestation
    # already flags such an input INVALID (SPEC §3.5), but the display block runs BEFORE the
    # VERDICT print. The `{}` default only covers an ABSENT key, not a present non-dict value.
    _v = att.get("veracity")
    v = _v if isinstance(_v, dict) else {}
    # HOLE #22 (cont.): veracity_floor is operator-declared and may be a non-numeric string in a
    # malformed (but hash-consistent) attestation. verify_attestation already flags such an input
    # INVALID, but the float(...) branch conditions below run in the DISPLAY block BEFORE the VERDICT
    # print, so float("bad") would crash the stranger-facing CLI with a traceback instead of showing a
    # clean INVALID. Coerce once with the same isinstance guard used for kry_minted above; the raw value
    # is still echoed verbatim on the veracity_floor line, but a non-numeric floor counts as 0.0 here.
    _floor_raw = v.get("veracity_floor", 0.0)
    _floor = _floor_raw if isinstance(_floor_raw, (int, float)) and not isinstance(_floor_raw, bool) else 0.0
    # Display the verifier's OWN recomputed figures, not the operator-declared `receipts`/
    # `total_kry` fields — so a reader never mistakes an echoed claim for a verified number.
    _links = att.get("links") if isinstance(att.get("links"), list) else []
    # Summed as validated floats, like the verifier's own total: a float beside an arbitrary-size
    # JSON integer raises OverflowError — here, before the VERDICT line (verify_attestation rejects it).
    _recomputed_total = 0.0
    for ln in _links:
        try:
            _recomputed_total += _finite_number(ln.get("kry_minted") if isinstance(ln, dict) else None,
                                                "kry_minted")
        except ValueError:
            continue
    print(f"KRY external verification — {scope}")
    print(f"  receipts:        {len(_links)} (recomputed from links)")
    print(f"  total_kry:       {round(_recomputed_total, 4)} (recomputed from links, not the declared field)")
    print(f"  veracity_floor:  {v.get('veracity_floor', 0.0)} "
          f"(fraction assigned to declared tiers above self-report; "
          f"source evidence requires separate verification)")
    if _floor > 0.0:
        # H2: this verifier confirms anchored tiers are CHAIN-BOUND and internally consistent, but it does
        # NOT establish that underlying TEE/TLSN evidence was independently verified.
        print("                   ↳ anchored tiers are chain-bound LABELS — check TEE evidence with kry_tee_verify")
        print("                     TLSN proofs require Rust attestation_verify with a trusted notary key")
        print("                     kry_tlsn_verify trusts external verifier output; it does not check signatures")
    print(f"  price basis:     ${_FRONTIER_USD_PER_M}/M frontier, as of {_PRICE_AS_OF} "
          f"(published benchmark; see magnitude coverage)")
    if args.registry:
        print("  ownership:       not verified; registry accounting is keyed by the supplied party label")
    if ok:
        _checked_magnitudes = sum("tokens_saved" in ln and "earn_rate" in ln for ln in _links)
        print(f"  magnitude:       {_checked_magnitudes}/{len(_links)} links checked; "
              f"{len(_links) - _checked_magnitudes} unchecked (legacy inputs absent)")
    if anchor_line:
        print(f"  anchor check:    {anchor_line}")
    elif _floor > 0.0:
        # F1 (independent audit): a chain claiming ANY non-self_reported (anchored) value can be a
        # genesis RE-MINT — internally consistent but with forged tiers. A keyless SHA-256 chain
        # cannot tell an honest anchored chain from a re-minted one; only a pre-published anchor can.
        # Say so LOUDLY in the slot a reader checks, so a stranger never reads "VALID + veracity_floor"
        # as proof the anchored fraction is real.
        print("  anchor check:    NONE — ⚠ the anchored fraction is OPERATOR-ASSERTED")
        print("                   here: a genesis re-mint with upgraded tiers passes this check.")
        print("                   Re-run with --anchor <operator's pre-published chain head> to make")
        print("                   a retroactive re-mint detectable.")
    if registry_anchor_line:
        print(f"  registry anchor: {registry_anchor_line}")
    if ok:
        print("  VERDICT: VALID — integrity + conservation + magnitude (where checkable) hold; "
              "source truth is not verified (read veracity_floor as declared tiers).")
    else:
        print("  VERDICT: INVALID")
        for e in errors:
            print(f"    - {e}")
    return 0 if ok else 1


def console_main() -> int:
    """Entry point for the `kry-verify` command installed by `pip install kry-attest`."""
    for _stream in (sys.stdout, sys.stderr):  # a cp1252 Windows console cannot encode the output glyphs
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    return main()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):  # a cp1252 Windows console cannot encode the output glyphs
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    sys.exit(main())
