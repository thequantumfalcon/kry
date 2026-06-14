#!/usr/bin/env bash
# Run the KRY Nitro attestation PoC ON the EC2 parent instance (after setup_instance.sh).
# Builds the enclave EIF, runs it, has the parent fetch a REAL signed attestation carrying
# a KRY measurement, then verifies it with scripts/kry_tee_verify.py against the genuine
# AWS Nitro root. Proves the tee_attested verify path works on real hardware end-to-end.
set -euo pipefail
cd "$(dirname "$0")"
CID=16

echo "== 1. build enclave image + EIF =="
docker build -t kry-attest-enclave:latest ./enclave
nitro-cli build-enclave --docker-uri kry-attest-enclave:latest --output-file kry-attest.eif

echo "== 2. run the enclave (CID $CID) =="
nitro-cli terminate-enclave --all >/dev/null 2>&1 || true
nitro-cli run-enclave --eif-path kry-attest.eif --cpu-count 2 --memory 768 --enclave-cid "$CID"
sleep 3
nitro-cli describe-enclaves

echo "== 3. parent -> enclave: measurement + nonce; receive attestation =="
python3 parent_client.py "$CID" attestation.bin

echo "== 4. fetch + verify against the REAL AWS Nitro root =="
curl -sSfO https://aws-nitro-enclaves.amazonaws.com/AWS_NitroEnclaves_Root-G1.zip
unzip -o AWS_NitroEnclaves_Root-G1.zip >/dev/null
ROOT=$(ls -1 *.pem | head -1)
echo "root cert: $ROOT"
# --root-sha256 defaults to AWS's published G1 fingerprint, so a wrong root fails closed.
python3 ../../scripts/kry_tee_verify.py attestation.bin --root "$ROOT" --dry-run

echo "== 5. terminate the enclave =="
nitro-cli terminate-enclave --all
echo
echo "PoC complete. Step 4 should print 'KRY T2 TEE (Nitro) verification' with verified fields."
echo "To actually MINT (writes to kry_data): drop --dry-run and 'pip install cryptography' + run from repo root."
