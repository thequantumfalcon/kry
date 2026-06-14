#!/usr/bin/env python3
"""Mint a KRY T2 receipt from a verified TLSNotary presentation (the T2 anchor).

The cryptographic root of trust is the Rust `tlsn` verifier (`attestation_verify`,
see tlsnotary/README.md): it checks the notary's signature and the real CA chain,
then reveals the selectively-disclosed transcript of a genuine TLS session with the
provider. This tool consumes that ALREADY-VERIFIED output, sanity-checks it actually
attests a successful call to the expected provider, extracts the provider's own
usage from the notarized response body, and mints a `tlsn_attested` (T2) receipt —
folding it into `veracity_floor` via the same hash-chained mint path as every other
KRY earn.

What T2 (TLS-notary) does and does NOT prove — stated plainly so it is never
overclaimed (consistent with docs/KRY_VERACITY_BINDING.md):
  - PROVES: the provider returned exactly these bytes over a real TLS session. The
    operator cannot fabricate them. This is strictly STRONGER than T1
    `provider_metered`, which trusts that the operator RETAINED a real usage payload.
  - WITNESSES a call that HAPPENED — displacement's cheap leg. It does NOT witness a
    cache-hit counterfactual (a call that never reached a provider leaves zero
    footprint to notarize; that remains TEE-only).
  - The SAVING MAGNITUDE still rests on the avoided model's public list price
    (`--avoided-model`), which is publicly checkable but not notarized — same honest
    ceiling as T1.

Trust ceiling (docs/KRY_T2_FINDINGS_REPORT.md §5): a self-hosted notary is not yet a
neutral third party. This mints what the mechanism honestly supports today; moving
the notary off the prover host is the separate step that earns trustless-to-a-stranger.

INPUT — a "verified presentation" JSON, produced from the Rust verifier's output:
    {
      "verified":    true,                 # REQUIRED: the verifier confirmed sig + CA chain
      "server_name": "openrouter.ai",      # REQUIRED: the verified TLS server identity
      "recv":        "HTTP/1.1 200 OK\r\n...\r\n\r\n{\"data\":{...}}",  # revealed response
      "sent":        "GET /api/v1/generation?id=... HTTP/1.1\r\n...",   # revealed request (optional)
      "notary_key":  "<hex k256 verifying key>",   # recorded into the evidence binding; pin with --notary-key
      "time":        1780554000                    # optional — attestation time (unix), provenance
    }
The notarized response that carries real per-request token usage is
`GET /api/v1/generation?id=<id>` (the authed 200). `/api/v1/credits` attests
account-level credit usage (dollars), not per-request tokens — pass --tokens-saved
explicitly if you mint against that.

stdlib only; imports kry only to mint (the mint log never leaves the machine).

Usage:
    # inspect a presentation without minting:
    python3 scripts/kry_tlsn_verify.py presentation.json --server openrouter.ai --dry-run
    # mint a T2 receipt for a displacement that avoided Opus, served by a free OR model:
    python3 scripts/kry_tlsn_verify.py presentation.json --server openrouter.ai \
        --avoided-model gh/claude-opus-4.8 --served-model or/free/some-model
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant {value} is not allowed")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _json_load(handle):
    return json.load(handle, parse_constant=_reject_json_constant)


def _json_token(value, field: str) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative JSON integer")
    return value


def _first_present(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _positive_finite_number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    value = float(value)
    return value if math.isfinite(value) and value > 0 else 0.0


def _parse_http_response(recv: str) -> tuple[int | None, dict | None]:
    """Split a raw HTTP response transcript into (status_code, json_body).

    Tolerates \\r\\n or \\n separators and chunked-transfer artifacts: parses the
    status line, then recovers the JSON object from the body even when chunk-size
    lines surround it. Returns (status, None) when no JSON body is present.
    """
    if not recv:
        return None, None
    # status line: "HTTP/1.1 200 OK"
    first = recv.split("\n", 1)[0].strip()
    m = re.match(r"HTTP/\d\.\d\s+(\d{3})", first)
    status = int(m.group(1)) if m else None

    # body = everything after the first blank line (header/body separator)
    sep = "\r\n\r\n" if "\r\n\r\n" in recv else ("\n\n" if "\n\n" in recv else None)
    body_txt = recv.split(sep, 1)[1] if sep else ""

    body: dict | None = None
    if body_txt.strip():
        try:
            body = _json_loads(body_txt)
        except json.JSONDecodeError:
            # chunked / framed body — recover the outermost JSON object
            s, e = body_txt.find("{"), body_txt.rfind("}")
            if s != -1 and e > s:
                try:
                    body = _json_loads(body_txt[s:e + 1])
                except json.JSONDecodeError:
                    body = None
    return status, body if isinstance(body, dict) else None


def _extract_usage(body: dict) -> tuple[int, int, dict]:
    """Pull (prompt_tokens, completion_tokens) from a provider response body.

    Unwraps a `data` envelope (OpenRouter generation), then reads the common
    field spellings — provider-native counts preferred. Also surfaces a few
    provenance fields (generation id, cost) for the receipt detail. Returns
    (prompt, completion, extras); zeros when the body carries no token counts
    (e.g. /api/v1/credits, which attests dollars, not per-request tokens).
    """
    u = body.get("data", body) if isinstance(body.get("data"), dict) else body
    usage = u.get("usage") if isinstance(u.get("usage"), dict) else {}
    p = _first_present(u.get("native_tokens_prompt"), u.get("tokens_prompt"),
                       u.get("prompt_tokens"), usage.get("prompt_tokens"),
                       u.get("input_tokens"))
    c = _first_present(u.get("native_tokens_completion"), u.get("tokens_completion"),
                       u.get("completion_tokens"), usage.get("completion_tokens"),
                       u.get("output_tokens"))
    extras = {
        "id": u.get("id"),
        "total_cost": u.get("total_cost"),
        "provider_name": u.get("provider_name"),
        "model": u.get("model"),
    }
    return _json_token(p, "provider prompt token count"), _json_token(
        c, "provider completion token count"), extras


def _norm_hex(s: str | None) -> str:
    """Normalize a hex key for EXACT comparison: strip whitespace, drop an optional
    0x prefix, lowercase. Returns '' for falsy input."""
    if not s:
        return ""
    s = s.strip().lower()
    return s[2:] if s.startswith("0x") else s


def validate_presentation(pres: dict, *, expect_server: str | None = None,
                          require_status: int = 200,
                          expect_notary: str | None = None) -> tuple[bool, list[str]]:
    """Fail-closed checks before any mint. Accumulates all reasons (not fail-fast).

    A receipt is minted ONLY if the presentation says verification passed, names
    the expected server, the notarized response is the required status, and — when a
    notary key is pinned (--notary-key) — was notarized by EXACTLY that notary.
    """
    errs: list[str] = []
    if pres.get("verified") is not True:
        errs.append("presentation is not marked verified=true — the Rust verifier "
                    "(attestation_verify) is the root of trust; run it first")
    server = (pres.get("server_name") or "").strip()
    if not server:
        errs.append("no server_name — the verified TLS server identity is required")
    elif expect_server and server.lower() != expect_server.lower():
        errs.append(f"server_name {server!r} != expected {expect_server!r}")
    # Notary pin (the third-party-trust gate, docs/KRY_T2_FINDINGS_REPORT.md §7a): when a
    # key is pinned we accept ONLY presentations notarized by exactly that notary. Without
    # this, a verified presentation from ANY notary the operator stands up would mint — the
    # signature/CA chain check alone does not bind WHICH notary vouched. Exact full-key match
    # only (a prefix pin is a weaker guarantee; fail-closed wins). Absent both is also refused.
    if expect_notary:
        got, want = _norm_hex(pres.get("notary_key")), _norm_hex(expect_notary)
        if not got:
            errs.append("a notary key was pinned (--notary-key) but the presentation carries "
                        "no notary_key — refusing to mint against an unidentified notary")
        elif got != want:
            errs.append(f"notarized by an UNPINNED notary: key {got[:16]}… != pinned "
                        f"{want[:16]}… (--notary-key) — refusing (only the vetted notary "
                        f"is trusted to vouch)")
    if not (pres.get("recv") or "").strip():
        errs.append("no recv transcript — nothing to read the attested usage from")
    else:
        try:
            status, _ = _parse_http_response(pres["recv"])
        except ValueError as e:
            errs.append(f"recv transcript JSON is not standards-compliant: {e}")
            return (not errs), errs
        if status is None:
            errs.append("could not parse an HTTP status from the recv transcript")
        elif status != require_status:
            errs.append(f"attested response status {status} != required {require_status} "
                        f"(a non-{require_status} response carries no usable usage)")
    return (not errs), errs


def _evidence_binding(pres: dict) -> str:
    """Bind the receipt to THIS notarized session: server + notary key + a hash of
    the revealed response. Replaying the same presentation yields the same evidence
    → the mint decay collapses the repeat to dust (no double-minting one attestation)."""
    recv = pres.get("recv") or ""
    recv_h = hashlib.sha256(recv.encode()).hexdigest()
    return f"tlsn:{pres.get('server_name')}:{pres.get('notary_key', '')}:{recv_h}"


def _avoided_from_routing(gen_id: str) -> str | None:
    """The avoided model the HOST recorded for this generation — the genuine routing
    decision, not a CLI declaration. The host stamps `/openrouter:<id>` + `avoided_model`
    on the receipt it mints when it routes a displacement (the kry_or_fetch contract);
    we read that prior record back. Returns the recorded avoided_model, or None when the
    host logged no routing decision for this id (then the displacement value is 0 unless
    an --avoided-model is passed explicitly — the counterfactual is never invented)."""
    from kry import kry_mint
    p = kry_mint._MINT_LOG_PATH
    marker = f"/openrouter:{gen_id}"
    found = None
    try:
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or marker not in line:
                    continue
                rec = _json_loads(line)
                if marker in str(rec.get("detail") or "") and rec.get("avoided_model"):
                    found = rec["avoided_model"]   # last (most recent) wins
    except Exception:
        return None
    return found


def run(pres: dict, *, expect_server: str | None, event_type: str,
        avoided_model: str | None, served_model: str | None,
        tokens_saved: float | None, require_status: int, dry_run: bool,
        expect_notary: str | None = None) -> dict:
    """Validate → extract → (optionally) mint. Returns a result dict for printing."""
    ok, errs = validate_presentation(pres, expect_server=expect_server,
                                     require_status=require_status,
                                     expect_notary=expect_notary)
    if not ok:
        return {"verdict": "REJECTED", "errors": errs}

    _, body = _parse_http_response(pres["recv"])
    prompt = completion = 0
    extras: dict = {}
    if body is not None:
        try:
            prompt, completion, extras = _extract_usage(body)
        except ValueError as e:
            return {"verdict": "REJECTED", "errors": [str(e)]}

    basis = tokens_saved if tokens_saved is not None else completion
    basis = _positive_finite_number(basis)
    server = pres.get("server_name")
    notary = pres.get("notary_key") or ""
    gen_id = extras.get("id")

    # Resolve the displacement legs from GENUINE sources, not a declaration:
    #  - served = the model the provider actually ran, cryptographically attested in the
    #    notarized body itself (strongest possible); --served-model overrides.
    #  - avoided = the model the host's routing decision displaced, read back from the
    #    host's recorded routing receipt for this gen id; --avoided-model overrides.
    #    Absent both, it stays None → 0 displacement value (honest, never invented).
    served = served_model or extras.get("model")
    served_src = "cli" if served_model else ("attested-body" if extras.get("model") else None)
    avoided = avoided_model or (_avoided_from_routing(gen_id) if gen_id else None)
    avoided_src = "cli" if avoided_model else ("routing-log" if avoided else None)

    result: dict = {
        "verdict": "OK",
        "server_name": server,
        "attested_tokens": {"prompt": prompt, "completion": completion},
        "attested_cost": extras.get("total_cost"),
        "generation_id": gen_id,
        "served_model": {"value": served, "source": served_src},
        "avoided_model": {"value": avoided, "source": avoided_src},
        "notary_key_fp": (notary[:16] + "…") if notary else None,
        "notary_pinned": bool(expect_notary),
        "attestation_time": pres.get("time"),
        "tokens_saved_basis": basis,
    }

    if basis <= 0:
        result["verdict"] = "NO_BASIS"
        result["note"] = ("notarized response carries no completion tokens "
                          "(e.g. /api/v1/credits attests dollars, not per-request "
                          "tokens) — pass --tokens-saved to mint against it")
        return result

    if dry_run:
        result["minted"] = None
        result["note"] = "dry-run: validated + parsed, no receipt minted"
        return result

    # Honest displacement gate: minting needs a GENUINE avoided model (routing record or
    # explicit --avoided-model). We must NOT fall through to mint with avoided=None, because
    # value_multiplier(None) defaults to 1.0 ("never under-credit") — that would silently
    # credit FULL displacement value off a counterfactual we cannot substantiate.
    if avoided is None:
        result["verdict"] = "NO_DISPLACEMENT_CONTEXT"
        result["note"] = ("no routing decision recorded for this gen id and no "
                          "--avoided-model given — refusing to mint displacement value we "
                          "can't substantiate (the counterfactual is never invented). Route "
                          "the call through the host so it stamps /openrouter:<id>+avoided_model, "
                          "or pass --avoided-model explicitly.")
        return result

    from kry import kry_mint
    before = kry_mint.veracity_breakdown()
    # Stamp provenance into detail (stored RAW): server, notary fp, and — when the
    # body exposes an OpenRouter generation id — the /openrouter:<id> handle, so the
    # SAME receipt is ALSO F1-reconcilable via scripts/kry_or_fetch.py.
    detail = f"tlsn_attested {server} status={require_status} /tlsn:{notary[:12]}"
    if gen_id:
        detail += f" /openrouter:{gen_id}"
    evidence = _evidence_binding(pres)

    # Double-credit resolution (docs/KRY_T2_FINDINGS_REPORT.md §7b, option iii): when the
    # HOST already minted this gen id as a T1 displacement, the saving has been credited
    # ONCE. T2 must UPGRADE that receipt's tier, NOT re-credit the saving. So if a prior
    # host receipt EXISTS for this gen id, we take the promotion path EXCLUSIVELY — we must
    # never fall through to a fresh mint (that would re-credit on a re-run). Only when NO
    # prior host receipt exists (standalone/manual run) does T2 mint fresh value.
    t1_prior = kry_mint._find_t1_receipt_for_gen(gen_id) if gen_id else None
    if t1_prior is not None:
        promotion = kry_mint.promote_to_tlsn(gen_id, evidence, detail)
        if promotion is None:
            # T1 exists but is already upgraded — idempotent no-op, NOT a fresh mint
            result["verdict"] = "ALREADY_UPGRADED"
            result["note"] = (f"gen id was already T1-credited and already upgraded to "
                              f"tlsn_attested (T1 {t1_prior.get('receipt_id')}) — no-op")
            return result
        receipt, superseded_id, moved_kry = promotion
        after = kry_mint.veracity_breakdown()
        result["minted"] = {
            "receipt_id": receipt.receipt_id,
            "mode": "tier_upgrade",        # net-zero: re-tiers a prior T1 receipt, no new value
            "supersedes": superseded_id,
            "kry_re_tiered": round(moved_kry, 4),
            "evidence_tier": receipt.evidence_tier,
            "chain_hash": receipt.chain_hash[:16] + "…",
        }
        result["veracity_floor"] = {"before": before["veracity_floor"],
                                    "after": after["veracity_floor"]}
        result["tlsn_attested_fraction"] = {"before": before["tlsn_attested_fraction"],
                                            "after": after["tlsn_attested_fraction"]}
        return result

    receipt = kry_mint.mint(
        event_type=event_type,
        tokens_saved=basis,
        detail=detail,
        evidence=evidence,
        avoided_model=avoided,
        served_model=served,
        evidence_tier=kry_mint.TIER_TLSN_ATTESTED,
        metered_tokens=[prompt, completion],
    )
    if receipt is None:
        result["verdict"] = "NOT_MINTED"
        result["note"] = ("mint returned None — basis decayed to dust (this "
                          "attestation was already minted) or was rejected at the boundary")
        return result
    after = kry_mint.veracity_breakdown()
    result["minted"] = {
        "receipt_id": receipt.receipt_id,
        "mode": "fresh_mint",            # no prior host T1 receipt → first credit for this saving
        "kry_minted": round(receipt.kry_minted, 4),
        "evidence_tier": receipt.evidence_tier,
        "chain_hash": receipt.chain_hash[:16] + "…",
    }
    result["veracity_floor"] = {"before": before["veracity_floor"],
                                "after": after["veracity_floor"]}
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Mint a KRY T2 (tlsn_attested) receipt from a verified TLSNotary presentation")
    p.add_argument("presentation", help="verified-presentation JSON (see module docstring)")
    p.add_argument("--server", default=None,
                   help="expected TLS server_name — minting is refused if it doesn't match")
    p.add_argument("--notary-key", default=None,
                   help="PIN the expected notary's hex k256 verifying key — minting is refused "
                        "unless the presentation was notarized by EXACTLY this notary (exact "
                        "full-key match). The third-party-trust gate: without it, a presentation "
                        "from any notary the operator stands up would mint. Default: unpinned")
    p.add_argument("--event-type", default="short_circuit",
                   help="efficiency event this attested call backs (default: short_circuit)")
    p.add_argument("--avoided-model", default=None,
                   help="OVERRIDE the avoided model. Default: read the host's recorded "
                        "routing decision for this gen id (a prior /openrouter:<id> receipt). "
                        "Absent both → 0 KRY (the counterfactual is never invented)")
    p.add_argument("--served-model", default=None,
                   help="OVERRIDE the served model. Default: the model in the notarized "
                        "body (cryptographically attested); its cost is netted out")
    p.add_argument("--tokens-saved", type=float, default=None,
                   help="saving basis (default: the attested completion tokens)")
    p.add_argument("--require-status", type=int, default=200,
                   help="the notarized response must be this HTTP status (default 200)")
    p.add_argument("--dry-run", action="store_true",
                   help="validate + parse + report only — mint nothing")
    args = p.parse_args(argv)

    with open(args.presentation, encoding="utf-8") as f:
        pres = _json_load(f)

    result = run(pres, expect_server=args.server, event_type=args.event_type,
                 avoided_model=args.avoided_model, served_model=args.served_model,
                 tokens_saved=args.tokens_saved, require_status=args.require_status,
                 dry_run=args.dry_run, expect_notary=args.notary_key)

    if result["verdict"] == "REJECTED":
        print("KRY T2 TLS-notary mint — REJECTED (fail-closed):")
        for e in result["errors"]:
            print(f"  - {e}")
        return 1

    at = result["attested_tokens"]
    print("KRY T2 TLS-notary verification")
    print(f"  server (verified):   {result['server_name']}")
    print(f"  attested tokens:     prompt {at['prompt']} + completion {at['completion']}")
    if result.get("attested_cost") is not None:
        print(f"  attested cost (USD): {result['attested_cost']}")
    if result.get("generation_id"):
        print(f"  generation id:       {result['generation_id']}")
    sv, av = result.get("served_model", {}), result.get("avoided_model", {})
    print(f"  served model:        {sv.get('value')}  (source: {sv.get('source')})")
    print(f"  avoided model:       {av.get('value')}  (source: {av.get('source')})")
    if result.get("notary_key_fp"):
        pinned = "  (pinned ✓ — verified against --notary-key)" if result.get("notary_pinned") else ""
        print(f"  notary key:          {result['notary_key_fp']}{pinned}")

    if result["verdict"] in ("NO_BASIS", "NO_DISPLACEMENT_CONTEXT"):
        print(f"  -> {result['note']}")
        return 1
    if result["verdict"] == "ALREADY_UPGRADED":
        print(f"  -> {result['note']}")
        return 0   # idempotent no-op, not an error
    if result.get("minted") is None:      # dry-run or not-minted
        print(f"  -> {result.get('note', 'no receipt minted')}")
        return 0
    if result["verdict"] == "NOT_MINTED":
        print(f"  -> {result['note']}")
        return 1

    m = result["minted"]
    vf = result["veracity_floor"]
    if m.get("mode") == "tier_upgrade":
        # net-zero: a prior host T1 receipt was UPGRADED to tlsn_attested, no new value
        print(f"  UPGRADED {m['receipt_id']}: re-tiered {m['kry_re_tiered']} KRY "
              f"{m['supersedes']} -> tlsn_attested  chain={m['chain_hash']}")
        print("  (no new value minted — the saving was credited once at T1)")
        tf = result.get("tlsn_attested_fraction", {})
        if tf:
            print(f"  tlsn_attested_fraction: {tf['before']} -> {tf['after']}")
    else:
        print(f"  MINTED {m['receipt_id']}: {m['kry_minted']} KRY  tier={m['evidence_tier']}  "
              f"chain={m['chain_hash']}")
    print(f"  veracity_floor: {vf['before']} -> {vf['after']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
