# KRY notary — managed-service deployment (§7a item b)

Turns the proven item-B independent TLSNotary (`tlsnotary/notary_tcp.rs`, proven 10/10,
`docs/KRY_T2_FINDINGS_REPORT.md §7a`) into a **managed service** so it survives WSL idle
and reboots — closing the "`nohup` notary reaped on VM idle" caveat. This is item **(b)**
of the §7a hardening. It does **not** by itself achieve item **(c)** (a genuinely
neutral third-party operator) — running it on your own node makes the notary *durable*
and *independent of the prover process*, not *socially neutral*. (c) is an organizational
step: hand the key + this service to a party who is not you.

## What's where
- `install_notary_service.sh` — run **inside the notary host's WSL** (needs sudo; the
 operator types their own password — never piped). Installs a systemd unit
 (`Restart=always`, starts on boot), then prints the public key to PIN.
- `expose_notary_lan.cmd` — run on the notary host's **Windows** side. Port-proxies the
 WSL port to the LAN so a prover on another machine can reach it.

## Deploy (on the notary host — e.g. node-b)
```bash
# inside WSL (key seed ~/notary_key.hex + binary ~/notary_tcp already present):
bash install_notary_service.sh # install + enable + start; prints the pubkey
bash install_notary_service.sh --status # verify: active + listening + pubkey
```
```cmd
:: on the Windows side, to expose it to the prover over the LAN:
expose_notary_lan.cmd
```
Keep the WSL VM alive across idle — in `%USERPROFILE%\.wslconfig`:
```ini
[wsl2]
vmIdleTimeout=-1
```

## Use it (on the prover + verifier)
```bash
# prover (the notary host (WSL)): point the MPC-TLS session at the independent notary
NOTARY_ADDR=<notary-host-LAN-IP>:7047 \
SERVER_HOST=openrouter.ai SERVER_PORT=443 SERVER_DOMAIN=openrouter.ai \
URI="/api/v1/generation?id=<gen-id>" AUTH_HEADER="Bearer $OPENROUTER_API_KEY" \
 cargo run --release --example attestation_prove
cargo run --release --example attestation_present
cargo run --release --example attestation_verify | python3 scripts/kry_tlsn_adapter.py - --out pres.json

# verifier: PIN the notary's published key — a presentation from any OTHER notary is refused
python3 scripts/kry_tlsn_verify.py pres.json --server openrouter.ai \
 --notary-key <the notary public key printed at install>
```

The `--notary-key` pin (shipped 2026-06-06, `scripts/kry_tlsn_verify.py`) is what makes
running an independent notary *meaningful*: without it a verifier would accept a
presentation from any notary, including one the prover stood up. With the service +
the pin, a stranger can demand "notarized by THIS key, durably operated" — the honest
remaining gap is only (c), who holds the key.

## Teardown
```bash
bash install_notary_service.sh --remove # WSL: stop + disable + delete the unit
```
```cmd
expose_notary_lan.cmd remove :: Windows: drop the portproxy + firewall rule
```
