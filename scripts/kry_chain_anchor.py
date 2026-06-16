#!/usr/bin/env python3
"""kry chain-head anchor — the external root of trust on the mint chain head.

verify_chain proves the mint chain is INTERNALLY consistent; it cannot tell an honest
chain from one an operator re-derived from genesis (keyless SHA-256 + a local checkpoint).
The only way to make a retroactive re-mint detectable is an EXTERNAL anchor on the chain
head. This tool provides it with no new dependency: export a content-free commitment
{count, tip} and PUBLISH it to an append-only medium you cannot silently rewrite — a git
commit, a public timestamp, a transparency log, a notarized note. A verifier who later
holds that PUBLISHED anchor (obtained out-of-band, like a pinned key) proves your chain
still carries the anchored prefix; a re-mint of any receipt at or before `count` is caught.

    python3 scripts/kry_chain_anchor.py export > chain_anchor.json   # then PUBLISH it
    python3 scripts/kry_chain_anchor.py verify --anchor chain_anchor.json

A stranger checking an attestation pins the same anchor with:
    python3 scripts/kry_verify.py attestation.json --anchor chain_anchor.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kry.kry_mint import export_chain_anchor, verify_chain_against_anchor  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Export/verify a kry chain-head anchor (re-mint evidence).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export", help="print the current chain-head anchor JSON (then PUBLISH it externally)")
    pv = sub.add_parser("verify", help="check the live mint chain against a PUBLISHED anchor")
    pv.add_argument("--anchor", required=True, help="path to the published anchor JSON")
    args = p.parse_args(argv)

    if args.cmd == "export":
        print(json.dumps(export_chain_anchor(), indent=2))
        return 0

    try:
        with open(args.anchor, encoding="utf-8") as f:
            anchor = json.load(f)
    except Exception as exc:
        print(f"anchor check: FAIL — anchor unreadable: {exc}")
        return 1
    ok, errors = verify_chain_against_anchor(anchor)
    print("anchor check:",
          "PASS — chain still carries the published anchor prefix (no re-mint)"
          if ok else "FAIL — re-mint/rollback vs the published anchor")
    for e in errors:
        print(f"  - {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
