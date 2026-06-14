#!/usr/bin/env bash
# Parent-instance setup for the KRY Nitro attestation PoC (Amazon Linux 2023, x86_64).
# Installs the Nitro Enclaves CLI + Docker, allocates 2 vCPU / 768 MiB to enclaves, and
# installs the one Python dep the verifier needs (cryptography). Run once, then re-login.
set -euo pipefail

sudo dnf install -y aws-nitro-enclaves-cli aws-nitro-enclaves-cli-devel docker git python3-pip unzip
sudo usermod -aG ne "$USER" || true
sudo usermod -aG docker "$USER" || true
sudo systemctl enable --now docker

# Allocate resources to enclaves (must be >= what run-enclave requests).
sudo sed -i 's/^cpu_count:.*/cpu_count: 2/' /etc/nitro_enclaves/allocator.yaml
sudo sed -i 's/^memory_mib:.*/memory_mib: 768/' /etc/nitro_enclaves/allocator.yaml
sudo systemctl enable --now nitro-enclaves-allocator.service

pip3 install --user cryptography

echo
echo "setup done. Log out and back in (or run 'newgrp ne; newgrp docker') so the ne/docker"
echo "group memberships take effect, then run: bash run_poc.sh"
