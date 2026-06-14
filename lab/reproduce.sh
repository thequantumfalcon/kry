#!/usr/bin/env bash
# Reproduce ALL of KRY's proofs over N rounds (default 10) — confirm they are
# deterministic, not one-off. Exits non-zero if ANY round of ANY proof fails.
#
#   bash lab/reproduce.sh            # 10 rounds
#   bash lab/reproduce.sh 25         # 25 rounds
#
# Proof families:
#   1. full test suite        (stdlib suite; optional crypto tests skip if unavailable)
#   2. lab/run_local.py       (lab Tests 1,2,3,5,6 end-to-end, no models/meters)
#   3. concurrency_check.py   (cross-process ledger: lost==0)
#   4. hole_d_double_spend.py (cross-node double-spend shown + lease fix + atomic)
set -u
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PYTHON="${PYTHON:-python3}"
ROUNDS="${1:-10}"
fail=0

echo "== KRY reproducibility — $ROUNDS rounds ($PYTHON) =="

echo "-- [1/4] full test suite --"
for r in $(seq 1 "$ROUNDS"); do
  out=$("$PYTHON" -m pytest tests/ -q 2>&1)
  rc=$?
  summary=$(printf "%s\n" "$out" | tail -1)
  if [ "$rc" -eq 0 ] && echo "$summary" | grep -q "passed"; then
    echo "  round $r: $summary"
  else echo "  round $r: *** FAIL: $summary"; fail=1; fi
done

echo "-- [2/4] lab/run_local.py (lab Tests 1,2,3,5,6) --"
for r in $(seq 1 "$ROUNDS"); do
  if "$PYTHON" lab/run_local.py 2>&1 | grep -q "All local tests pass"; then
    echo "  round $r: ALL PASS"
  else echo "  round $r: *** FAIL"; fail=1; fi
done

echo "-- [3/4] cross-process concurrency (lost must be 0) --"
for r in $(seq 1 "$ROUNDS"); do
  v=$("$PYTHON" lab/concurrency_check.py --workers 4 --earns 200 2>/dev/null \
        | "$PYTHON" -c "import json,sys;d=json.load(sys.stdin);print('OK' if d['pass'] else 'FAIL', d['lost'])")
  echo "  round $r: lost=${v#* } ($(echo "$v" | cut -d' ' -f1))"
  [ "${v%% *}" = "OK" ] || fail=1
done

echo "-- [4/4] HOLE D cross-node double-spend + lease --"
for r in $(seq 1 "$ROUNDS"); do
  if "$PYTHON" lab/hole_d_double_spend.py 2>&1 | grep -q "hole demonstrated=True  fix holds=True  lease atomic=True"; then
    echo "  round $r: demonstrated + fixed + atomic"
  else echo "  round $r: *** FAIL"; fail=1; fi
done

echo "=================================================="
if [ "$fail" -eq 0 ]; then
  echo "ALL PROOFS REPRODUCIBLE across $ROUNDS rounds ✅"
else
  echo "*** SOME PROOF FAILED — see above"
fi
exit "$fail"
