#!/usr/bin/env python3
"""Adapt the Rust `attestation_verify` console output into a KRY presentation JSON.

The TLSNotary verifier (`cargo run --example attestation_verify`, see
tlsnotary/README.md) prints its result as human-readable text: the notary key it
checked, a "Successfully verified … session with <server> at <time>" line, and the
selectively-disclosed `Data sent:` / `Data received:` transcripts. `kry_tlsn_verify.py`
consumes a structured presentation JSON, not that prose. This tool is the bridge:
it parses the verifier's captured stdout and emits the exact JSON shape
`kry_tlsn_verify.py` mints from.

The "Successfully verified …" line is the proof signal — the patched verifier
(tlsnotary/openrouter-t2.patch) prints it ONLY after the notary signature + real CA
chain check pass. Its presence sets `verified: true`; its absence (a failed/garbled
run) sets `verified: false`, and `kry_tlsn_verify.py` then refuses to mint (fail-closed).

Parses the verbatim format of tlsn @ 28614ef:

    Verifying presentation with {alg} key: {hex}

    **Ask yourself, do you trust this key?**

    -------------------------------------------------------------------
    Successfully verified that the data below came from a session with {server} at {time}.
    Note that the data which the Prover chose not to disclose are shown as X.

    Data sent:
    {sent transcript}

    Data received:
    {recv transcript}
    -------------------------------------------------------------------

stdlib only; no network, no kry import — a pure text→JSON transform.

Usage:
    # from a captured file:
    python3 scripts/kry_tlsn_adapter.py verify_out.txt --out presentation.json
    # or piped straight from the verifier:
    cargo run --release --example attestation_verify | \
        python3 scripts/kry_tlsn_adapter.py - --out presentation.json
    # then mint:
    python3 scripts/kry_tlsn_verify.py presentation.json --server openrouter.ai \
        --avoided-model gh/claude-opus-4.8
"""
from __future__ import annotations

import argparse
import json
import re
import sys

_KEY_RE = re.compile(r"Verifying presentation with\s+(\S+)\s+key:\s+([0-9a-fA-F]+)")
_VERIFIED_RE = re.compile(
    r"Successfully verified that the data below came from a session with\s+(.+?)\s+at\s+(.+?)\.")
_DELIM_RE = re.compile(r"^-{20,}\s*$", re.MULTILINE)


def _json_dumps(value, **kwargs) -> str:
    return json.dumps(value, allow_nan=False, **kwargs)


def _section(text: str, start_marker: str, end_marker: str | None) -> str | None:
    """Text between `start_marker` and the next `end_marker` (or a dashed delimiter
    / end-of-text when end_marker is None). Returns None if start is absent."""
    i = text.find(start_marker)
    if i == -1:
        return None
    rest = text[i + len(start_marker):]
    if end_marker is not None:
        j = rest.find(end_marker)
        if j != -1:
            return rest[:j].strip("\n")
    # bound by the closing dashed delimiter line if present
    m = _DELIM_RE.search(rest)
    if m:
        rest = rest[:m.start()]
    return rest.strip("\n")


def parse_verify_output(text: str) -> dict:
    """Parse captured `attestation_verify` stdout into a presentation dict.

    `verified` is True iff the success line is present. `recv` is the revealed
    response transcript the mint reads usage from; `sent` is included for provenance.
    Undisclosed bytes appear as 'X' (the patched verifier reveals the whole response,
    so a 200 body is intact).
    """
    # SECURITY: anchor the verified-line + notary-key search to the HEADER block (everything before
    # "Data sent:"). The revealed response body (after "Data received:") is attacker-influenceable; a
    # body that embeds a fake "Successfully verified ... came from a session with <server>" line must
    # NOT be able to set verified=true or forge server_name. The genuine verifier prints both lines in
    # the header, never in the body.
    _hdr_end = text.find("Data sent:")
    header = text if _hdr_end == -1 else text[:_hdr_end]
    vm = _VERIFIED_RE.search(header)
    km = _KEY_RE.search(header)
    sent = _section(text, "Data sent:\n", "\nData received:")
    recv = _section(text, "Data received:\n", None)
    return {
        "verified": vm is not None,
        "server_name": vm.group(1).strip() if vm else None,
        "time": vm.group(2).strip() if vm else None,
        "notary_key": km.group(2) if km else None,
        "notary_key_alg": km.group(1) if km else None,
        "sent": sent,
        "recv": recv,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Adapt attestation_verify stdout into a kry_tlsn_verify presentation JSON")
    p.add_argument("verify_output",
                   help="path to captured attestation_verify stdout ('-' for stdin)")
    p.add_argument("--out", default=None, help="write presentation JSON here (default: stdout)")
    p.add_argument("--server", default=None,
                   help="assert/override the verified server_name (errors if it mismatches what was parsed)")
    p.add_argument("--notary-key", default=None, help="override the parsed notary key")
    p.add_argument("--require-verified", action="store_true",
                   help="exit nonzero if the 'Successfully verified' line is absent")
    args = p.parse_args(argv)

    text = sys.stdin.read() if args.verify_output == "-" else open(args.verify_output, encoding="utf-8").read()
    pres = parse_verify_output(text)

    if args.notary_key:
        pres["notary_key"] = args.notary_key
    if args.server:
        parsed = pres.get("server_name")
        if parsed and parsed.lower() != args.server.lower():
            print(f"server mismatch: parsed {parsed!r} != --server {args.server!r} "
                  f"(the verified identity is what the notary signed — do not override it)",
                  file=sys.stderr)
            return 2
        pres["server_name"] = args.server

    if not pres["verified"]:
        print("WARNING: no 'Successfully verified' line found — verified=false. "
              "kry_tlsn_verify.py will refuse to mint. Was the verifier run successful?",
              file=sys.stderr)
        if args.require_verified:
            return 1
    if pres["verified"] and not pres.get("recv"):
        print("WARNING: verified, but no 'Data received:' transcript parsed — "
              "nothing to read usage from.", file=sys.stderr)

    out_json = _json_dumps(pres, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_json + "\n")
        print(f"wrote presentation -> {args.out} "
              f"(verified={pres['verified']}, server={pres['server_name']})", file=sys.stderr)
    else:
        print(out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
