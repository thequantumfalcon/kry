#!/usr/bin/env python3
"""Try KRY in 30 seconds — the whole loop, end to end.

Run it (no arguments, no setup, no network):

    python examples/try_kry.py

It uses a throwaway data dir (a temp folder, deleted on exit), so it never
touches your real ledger. What it walks through:

  1. EARN    — record efficiency events (a cache hit that avoided an Opus call,
               a compression win, a displacement to a cheaper paid model).
  2. RETAIN  — show retained_dollars(): the honest "value today" = money kept,
               provable against real provider pricing. No counterparty needed.
  3. MINT    — anchor each earn into a SHA-256 hash chain (tamper-evident).
  4. ATTEST  — emit a public, content-sealed proof of the balance.
  5. VERIFY  — hand that proof to scripts/kry_verify.py, which imports NOTHING
               from this package (stdlib only). That is the differentiator: a
               stranger confirms integrity + conservation + magnitude without
               trusting your runtime and without seeing a single prompt.
  6. CARBON  — the second denomination: avoided inference -> kWh -> CO2 (a
               clearly-labeled ESTIMATE, not a certified credit).

The point of the demo is step 5: "anyone can verify this with plain Python."
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Make the example self-contained: add ../src to the path and use a temp data
# dir so `python examples/try_kry.py` just works without env setup.
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
_TMP = tempfile.mkdtemp(prefix="kry_demo_")
os.environ["KRY_DATA_DIR"] = _TMP

from kry import kry_attest, kry_carbon, kry_mint, kry_token  # noqa: E402


def rule(title: str) -> None:
    print(f"\n{'─' * 70}\n{title}\n{'─' * 70}")


def main() -> int:
    rule("1. EARN — efficiency events become KRY (edge-weighted by what they avoided)")
    # A cache hit that avoided a frontier Opus call earns full value.
    kry_mint.mint("cache_hit", 1000, "served from cache instead of Opus",
                  evidence="resp-sha-abc", avoided_model="gh/claude-opus-4.8")
    # A compression win (output reduced, call still happened) — discounted rate.
    kry_mint.mint("compression", 800, "directive trimmed output",
                  evidence="cmp-001", avoided_model="or/anthropic/claude-opus-4.8")
    # A displacement: served by a cheaper PAID model instead of the frontier —
    # earns only the NET price difference, and is provider-metered (T1).
    kry_mint.mint("short_circuit", 1000, "displaced to deepseek /openrouter:gen-demo",
                  evidence="disp-001", avoided_model="gh/claude-opus-4.8",
                  served_model="or/deepseek/deepseek-v4-pro",
                  evidence_tier=kry_mint.TIER_PROVIDER_METERED, metered_tokens=[120, 340])

    st = kry_token.status()
    print(f"  balance:        {st['balance_kry']:>12,.2f} KRY")
    print(f"  lifetime earned:{st['total_earned_kry']:>12,.2f} KRY")
    print(f"  frontier basis: {st['frontier_baseline']}   ({st['kry_per_usd']:,} KRY / USD)")

    rule("2. RETAIN — the honest value TODAY (money kept, no counterparty needed)")
    rd = kry_token.retained_dollars()
    print(f"  retained_usd:                 ${rd['retained_usd']:.4f}")
    print(f"  value_type:                   {rd['value_type']}")
    print(f"  external_counterparty_exists: {rd['external_counterparty_exists']}  (honest label)")

    rule("3. MINT — every earn is a SHA-256 hash-chain receipt (tamper-evident)")
    summary = kry_mint.chain_summary()
    print(f"  receipts:    {summary['receipts']}")
    print(f"  chain_tip:   {summary['chain_tip']}")
    print(f"  chain_valid: {summary['chain_valid']}")
    v = summary["veracity"]
    print(f"  veracity_floor: {v['veracity_floor']}  "
          f"(fraction externally anchored vs operator self-report)")

    rule("4. ATTEST — a public, content-sealed proof of the balance")
    att = kry_attest.build_attestation()
    att_path = Path(_TMP) / "attestation.json"
    att_path.write_text(att.to_public_json(), encoding="utf-8")
    print(f"  wrote {att.receipts} links, total {att.total_kry:,.2f} KRY -> {att_path}")
    print("  (contains only hashes + aggregates — no prompts, responses, or model")
    print("   names beyond the event type; safe to hand to a third party)")

    rule("5. VERIFY — a STRANGER checks it with stdlib only (the differentiator)")
    verifier = _REPO / "scripts" / "kry_verify.py"
    proc = subprocess.run([sys.executable, str(verifier), str(att_path)],
                          capture_output=True, text=True)
    print(proc.stdout.rstrip())
    if proc.returncode != 0:
        print(proc.stderr.rstrip())
        return 1

    rule("6. CARBON — second denomination: avoided inference -> CO2 (ESTIMATE)")
    total_kry = kry_mint.retained_dollars_dated()["total_kry_minted"]
    carbon = kry_carbon.carbon_statement(total_kry)
    print(f"  kry_avoided:        {carbon['kry_avoided']:,.2f}")
    print(f"  energy_kwh_avoided: {carbon['energy_kwh_avoided']:.6f} kWh")
    print(f"  co2_grams_avoided:  {carbon['co2_grams_avoided']:.4f} g")
    print(f"  status:             {carbon['status']}")

    print(f"\n{'═' * 70}")
    print("Done. The balance was minted from real efficiency events, anchored in a")
    print("tamper-evident chain, and verified by a program that trusts nothing in")
    print("this package. That is what 'proof-of-efficiency' means in practice.")
    print(f"(temp data dir {_TMP} — safe to delete)")
    print("═" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
