#!/usr/bin/env python3
"""KRY tee_attested PoC — parent side.

Send the KRY measurement (as user_data) and a fresh random nonce to the enclave over
vsock, receive the signed Nitro attestation document, and write it to a file. The
measurement JSON is exactly what scripts/kry_tee_verify.py reads back out of the
attested user_data. Requires Linux (socket.AF_VSOCK); run on the EC2 parent instance.

Usage: python3 parent_client.py [ENCLAVE_CID] [OUT_FILE]   (defaults: 16, attestation.bin)
Env overrides: KRY_MEASUREMENT_ID, KRY_TOKENS_SAVED, KRY_AVOIDED_MODEL
"""
import json
import os
import socket
import struct
import sys

ENCLAVE_CID = int(sys.argv[1]) if len(sys.argv) > 1 else 16
OUT = sys.argv[2] if len(sys.argv) > 2 else "attestation.bin"
PORT = 5005

measurement = {
    "measurement_id": os.environ.get("KRY_MEASUREMENT_ID", "poc-nitro-001"),
    "tokens_saved": float(os.environ.get("KRY_TOKENS_SAVED", "420")),
    "avoided_model": os.environ.get("KRY_AVOIDED_MODEL", "gh/claude-opus-4.8"),
}
user_data = json.dumps(measurement, separators=(",", ":")).encode()
nonce = os.urandom(16)
assert len(user_data) <= 1024, "user_data must be <= 1024 bytes (Nitro attestation limit)"


def send_frame(s, b):
    s.sendall(struct.pack(">I", len(b)) + b)


def recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise EOFError("enclave closed the connection early")
        buf += chunk
    return buf


def main():
    s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    s.connect((ENCLAVE_CID, PORT))
    send_frame(s, user_data)
    send_frame(s, nonce)
    doc_len = struct.unpack(">I", recv_exact(s, 4))[0]
    doc = recv_exact(s, doc_len) if doc_len else b""
    s.close()
    if not doc:
        print("ERROR: enclave returned an empty attestation document", file=sys.stderr)
        return 1
    with open(OUT, "wb") as f:
        f.write(doc)
    print(f"wrote {len(doc)} bytes -> {OUT}")
    print(f"measurement: {measurement}")
    print(f"nonce: {nonce.hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
