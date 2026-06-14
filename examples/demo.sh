#!/usr/bin/env bash
# KRY — live, start-to-finish demo of the token in action.
#
# Runs the REAL package end-to-end (no setup, no network): earn → retained $ →
# mint (hash chain) → attest → a STRANGER verifies → veracity → carbon, then an
# operator's routing-log → verifiable savings statement, then the T2 pointer.
# Paced for screen-recording.
#
#   bash examples/demo.sh                 # run it live
#   KRY_DEMO_PACE=0 bash examples/demo.sh # instant (CI / quick check)
#   asciinema rec kry.cast -c 'bash examples/demo.sh'   # record → share (see tlsnotary/README.md)
#
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=${PYTHON:-python3}
P=${KRY_DEMO_PACE:-1.4}                 # seconds between sections
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

c()   { printf '\033[1;36m%s\033[0m\n' "$*"; }
g()   { printf '\033[1;32m%s\033[0m\n' "$*"; }
dim() { printf '\033[2m%s\033[0m\n' "$*"; }
step(){ echo; c "━━━━━━ $* ━━━━━━"; sleep "$P"; }

clear 2>/dev/null || true
g "  K R Y   —   Proof-of-Efficiency Compute Credit"
dim "  earn by provably avoiding inference cost · prove it to a stranger · stdlib only"
sleep "$P"

step "FULL LIFECYCLE  —  earn → mint → attest → STRANGER-verifies → carbon"
dim "The whole loop, on real efficiency events, in one program:"
TMPDIR=/tmp "$PY" examples/try_kry.py
sleep "$P"

step "OPERATOR VIEW  —  a real routing log → a verifiable savings statement"
dim "SAVED vs SPEND + veracity_floor; --mint anchors it, --attest emits the public proof."
KRY_DATA_DIR="$TMP" "$PY" scripts/kry_savings_report.py examples/sample_usage_log.jsonl \
    --mint --attest "$TMP/att.json" 2>&1 | sed -e "s#$TMP#<tmp>#g" -e 's/ (verify:[^)]*)//' -e 's/^/  /'
sleep "$P"

step "STRANGER CHECK  —  verify that statement with stdlib only (imports nothing from KRY)"
"$PY" scripts/kry_verify.py "$TMP/att.json" | sed 's/^/  /' || true
g "  ↑ confirmed by code that does NOT trust the producer — the whole point."
sleep "$P"

step "T2  —  the same trust model, anchored to a REAL provider's TLS response"
dim "TLSNotary proves what openrouter.ai returned, verifiable by a stranger with real CA roots."
dim "Proven end-to-end (2026-06-04):  docs/KRY_T2_FINDINGS_REPORT.md  ·  tlsnotary/"
echo
g "  earn → mint → attest → verify → (T1 reconcile / T2 notarize).  That is proof-of-efficiency."
echo
