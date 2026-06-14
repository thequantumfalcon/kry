#!/usr/bin/env bash
# Install the KRY independent TLSNotary as a systemd MANAGED SERVICE (§7a item b).
#
# Closes the WSL2 "nohup reaped on idle" caveat from KRY_T2_FINDINGS_REPORT.md §7a:
# a detached background notary is reaped when the WSL VM idles. systemd keeps it
# running (Restart=always) and brings it back after a reboot. Run this INSIDE the
# notary host's WSL (it needs sudo — the operator has the password; do not pipe it).
#
# Prereqs already on the host (from item B, KRY_T2_FINDINGS_REPORT.md §7a):
#   ~/notary_tcp      the TCP notary binary (built from tlsnotary/notary_tcp.rs)
#   ~/notary_key.hex  the 32-byte hex signing-key SEED — the notary's identity.
#                     Its public key is what verifiers PIN (kry_tlsn_verify --notary-key).
#
# Usage (inside WSL on the notary host):
#   bash install_notary_service.sh            # install + enable + start, print pubkey
#   bash install_notary_service.sh --status   # show status + listening port + pubkey
#   bash install_notary_service.sh --remove    # stop + disable + delete the unit
set -euo pipefail

UNIT=/etc/systemd/system/kry-notary.service
USER_NAME="$(id -un)"
HOME_DIR="$HOME"
BIND="${NOTARY_BIND:-0.0.0.0:7047}"
KEY="${NOTARY_KEY_FILE:-$HOME_DIR/notary_key.hex}"
BIN="${NOTARY_BIN:-$HOME_DIR/notary_tcp}"

case "${1:-install}" in
  --status)
    systemctl status kry-notary --no-pager || true
    echo "--- listening ---"; ss -ltn | grep "${BIND##*:}" || echo "(not listening)"
    echo "--- public key (PIN this on verifiers) ---"
    journalctl -u kry-notary --no-pager | grep -i "public key" | tail -1 || true
    exit 0 ;;
  --remove)
    sudo systemctl disable --now kry-notary || true
    sudo rm -f "$UNIT"; sudo systemctl daemon-reload
    echo "removed kry-notary.service"; exit 0 ;;
esac

[ -x "$BIN" ] || { echo "ERROR: notary binary not found/executable: $BIN" >&2; exit 1; }
[ -r "$KEY" ] || { echo "ERROR: key seed not readable: $KEY" >&2; exit 1; }

echo "Installing kry-notary.service (user=$USER_NAME bind=$BIND)…"
sudo tee "$UNIT" >/dev/null <<UNITEOF
[Unit]
Description=KRY independent TLSNotary (item B / §7a managed service)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$HOME_DIR
# The signing-key SEED lives ONLY on this host — it IS the notary's identity.
ExecStart=/bin/bash -lc 'NOTARY_BIND=$BIND NOTARY_KEY_HEX=\$(cat "$KEY") "$BIN"'
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNITEOF

sudo systemctl daemon-reload
sudo systemctl enable --now kry-notary
sleep 3
echo "--- status ---"; systemctl is-active kry-notary
echo "--- public key (PIN this with: kry_tlsn_verify --notary-key <key>) ---"
journalctl -u kry-notary --no-pager | grep -i "public key" | tail -1 || \
  echo "(check 'journalctl -u kry-notary' for the 'notary public key' line)"
echo
echo "Next: expose to the LAN for the prover (expose_notary_lan.cmd on the Windows host),"
echo "and keep the WSL VM alive across idle — set in %USERPROFILE%\\.wslconfig:"
echo "    [wsl2]"
echo "    vmIdleTimeout=-1"
