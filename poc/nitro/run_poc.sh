#!/usr/bin/env bash
# Run the KRY Nitro attestation PoC ON the EC2 parent instance (after setup_instance.sh).
# Builds the enclave EIF, runs it, has the parent fetch a REAL signed attestation carrying
# parent-supplied demonstration payload, then verifies it with scripts/kry_tee_verify.py
# against the genuine AWS Nitro root. This does not establish measurement truth.
set -euo pipefail
cd "$(dirname "$0")"

echo "== 1. build enclave image + EIF =="
docker build -t kry-attest-enclave:latest ./enclave
nitro-cli build-enclave --docker-uri kry-attest-enclave:latest --output-file kry-attest.eif

echo "== 2. run the enclave =="
ENCLAVE_INFO=$(nitro-cli run-enclave --eif-path kry-attest.eif --cpu-count 2 --memory 768)
printf '%s\n' "$ENCLAVE_INFO"
ENCLAVE_ID=$(python3.11 -c 'import json,sys; print(json.load(sys.stdin)["EnclaveID"])' <<< "$ENCLAVE_INFO")
cleanup() {
    local status=$?
    local cleanup_status=0
    trap - EXIT
    nitro-cli terminate-enclave --enclave-id "$ENCLAVE_ID" || cleanup_status=$?
    if (( cleanup_status != 0 )); then
        echo "Failed to terminate enclave $ENCLAVE_ID; terminate this enclave manually." >&2
    fi
    if (( status == 0 )); then
        status=$cleanup_status
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
CID=$(python3.11 -c 'import json,sys; print(json.load(sys.stdin)["EnclaveCID"])' <<< "$ENCLAVE_INFO")
sleep 3
nitro-cli describe-enclaves

echo "== 3. parent -> enclave: demonstration payload + nonce; receive attestation =="
python3.11 parent_client.py "$CID" attestation.bin

echo "== 4. fetch + verify against the REAL AWS Nitro root =="
curl -sSfO https://aws-nitro-enclaves.amazonaws.com/AWS_NitroEnclaves_Root-G1.zip
unzip -o AWS_NitroEnclaves_Root-G1.zip >/dev/null
ROOT=$(ls -1 *.pem | head -1)
echo "root cert: $ROOT"
# --root-sha256 defaults to AWS's published G1 fingerprint, so a wrong root fails closed.
python3.11 ../../scripts/kry_tee_verify.py attestation.bin --root "$ROOT" --dry-run

echo "== 5. terminate enclave $ENCLAVE_ID on exit =="
echo
echo "Signature verification completed. The supplied savings value remains a demonstration."
echo "This run uses --dry-run and does not mint a receipt or terminate the EC2 instance."
