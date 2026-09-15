"""`kry-try`: mint a throwaway receipt chain from SYNTHETIC events, attest it, then verify it.

A first look after `pip install kry-attest`, with no repository checkout. The events are made up and
are written to a temporary ledger that is deleted on exit, so a real ledger is never touched. The one
file kept is the public attestation, so the reader can verify it again with `kry-verify` and watch an
edited number get caught.

Both events are self-reported, so the attestation honestly declares `veracity_floor` 0.0: nothing
here is anchored by anything beyond the operator's own say-so.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# kry modules read KRY_DATA_DIR when they are first imported, so the demo must set it before importing
# them. If they are already loaded, the temporary ledger would be ignored and a real one written to.
_LEDGER_MODULES = ("kry.kry_mint", "kry.kry_token", "kry.kry_attest")


def _verifier_command(attestation: Path) -> list[str]:
    """Run the stranger verifier in its own process, never through the `kry` package."""
    if importlib.util.find_spec("kry_verify") is not None:      # installed: the standalone module
        return [sys.executable, "-m", "kry_verify", str(attestation)]
    checkout = Path(__file__).resolve().parents[2] / "scripts" / "kry_verify.py"   # source checkout
    return [sys.executable, str(checkout), str(attestation)]


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):  # a cp1252 Windows console cannot encode the output glyphs
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="kry-try",
        description="Mint a throwaway receipt chain from synthetic events, attest it, and verify it.")
    parser.add_argument("--out", default="kry-demo-attestation.json",
                        help="where to write the public attestation (default: %(default)s)")
    args = parser.parse_args(argv)

    loaded = [name for name in _LEDGER_MODULES if name in sys.modules]
    if loaded:
        print(f"kry-try: {', '.join(loaded)} already imported; run kry-try in a fresh process",
              file=sys.stderr)
        return 2

    out = Path(args.out).resolve()
    data_dir = tempfile.mkdtemp(prefix="kry_try_")
    os.environ["KRY_DATA_DIR"] = data_dir
    try:
        from kry import kry_attest, kry_mint

        print("kry-try: SYNTHETIC demo data. These events are made up; this is not a savings claim.")
        print(f"temporary ledger: {data_dir} (deleted on exit)")
        print()
        print("1. Mint two self-reported efficiency events into a hash-chained ledger")
        kry_mint.mint("cache_hit", 1000, "demo: response served from cache",
                      evidence="demo-cache-1", avoided_model="gh/claude-opus-4.8")
        kry_mint.mint("compression", 800, "demo: output trimmed",
                      evidence="demo-compression-1", avoided_model="or/anthropic/claude-opus-4.8")

        print("2. Build the public attestation (hashes and totals only, no prompts or responses)")
        attestation = kry_attest.build_attestation()
        out.write_text(attestation.to_public_json(), encoding="utf-8")
        print(f"   wrote {attestation.receipts} receipts, {attestation.total_kry:,.2f} KRY -> {out}")

        print("3. Verify it the way a stranger would: a separate program that imports nothing from kry")
        print()
        proc = subprocess.run(_verifier_command(out), capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        if proc.returncode != 0:
            return proc.returncode

        print()
        print("Next:")
        print(f"  kry-verify {out}")
        print("  Then change any kry_minted value in that file and run kry-verify again:")
        print("  the verdict becomes INVALID.")
        return 0
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
