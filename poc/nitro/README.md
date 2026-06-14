# KRY `tee_attested` — AWS Nitro Enclaves PoC

Proves the `tee_attested` verify path (`scripts/kry_tee_verify.py`) works against a **real**
AWS-signed Nitro attestation chaining to the genuine **AWS Nitro root G1** — not just the
synthetic-but-real-crypto fixtures the unit tests already cover.

## What it proves (and what it doesn't)

A genuine enclave produces an attestation document whose `user_data` carries a KRY
measurement (`{measurement_id, tokens_saved, avoided_model}`). The verifier confirms the
ES384 COSE signature + the X.509 chain to AWS's published root, then mints/upgrades a
`tee_attested` receipt. This proves *the measurement ran in attested hardware the operator
cannot fabricate* — it does **not** prove any model provider's inference ran in a TEE
(no provider exposes that). `tlsn_attested` remains the provider-call proof.

## Cost

Nitro Enclaves itself has **no surcharge** — you pay only EC2 + ~12 GB gp3 EBS. A
`c6i.xlarge` (4 vCPU, the cheapest viable x86 Nitro-enclave instance; ≥4 vCPU and
non-burstable are required) is on the order of $0.10–0.20/hr on-demand — **confirm the exact
figure in the EC2 pricing console**. A 1–2 hr PoC, **terminated after**, is a few dollars.
The only real risk is forgetting to terminate; set a budget alarm and use the terminate
command printed by `launch.sh`.

## Pieces

| file | runs where | what |
|------|-----------|------|
| `enclave/` (Rust + Dockerfile) | inside the enclave | NSM `Request::Attestation` over vsock; returns the signed COSE doc |
| `parent_client.py` | EC2 parent | sends measurement+nonce, writes `attestation.bin` |
| `setup_instance.sh` | EC2 parent | installs nitro-cli + docker, allocates enclave resources |
| `run_poc.sh` | EC2 parent | build EIF → run enclave → fetch attestation → verify |
| `launch.sh` | your Mac | `aws ec2 run-instances` with `--enclave-options Enabled=true` |

## Run it (driving from the Mac)

```bash
# 0. one-time: configure credentials (you enter them; they are not shared with the agent)
aws configure # or: export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
aws sts get-caller-identity # confirm the account/region

# 1. launch (SPENDS) — needs an EC2 key pair + a security group allowing SSH from your IP
KEY=my-keypair SG=sg-0abc REGION=us-east-1 TYPE=c6i.xlarge bash launch.sh

# 2. copy the repo over + set up (use the IP launch.sh printed)
rsync -az --exclude .git --exclude kry_data ../../ ec2-user@<IP>:~/kry/
ssh ec2-user@<IP> 'cd kry/poc/nitro && bash setup_instance.sh' # then log out/in

# 3. run the PoC (build EIF, attest, verify)
ssh ec2-user@<IP> 'cd kry/poc/nitro && newgrp ne <<< "bash run_poc.sh"'

# 4. TERMINATE (stops spend)
aws ec2 terminate-instances --region us-east-1 --instance-ids <IID>
```

Expected: step 4 of `run_poc.sh` prints `KRY T2 TEE (Nitro) verification` with the attested
`measurement id`, `tokens saved`, and PCR0 — i.e. a real AWS attestation verified by the
pure-Python verifier. To actually mint (not just `--dry-run`), drop `--dry-run` and run from
the repo root so `kry` is importable.

## Notes

- The verifier needs `cryptography` (`pip install cryptography` / `kry[tee]`);
 `setup_instance.sh` installs it. Core KRY stays stdlib-only.
- The root pin defaults to AWS's published G1 fingerprint (`sha256(DER)`), so a wrong root
 fails closed. Verify the downloaded root per
 <https://docs.aws.amazon.com/enclaves/latest/user/verify-root.html>.
- Crate versions in `enclave/Cargo.toml` are floors; if `cargo build` can't resolve, run
 `cargo update` (or `cargo add aws-nitro-enclaves-nsm-api vsock serde_bytes`) on the instance.
- No `KRY_DATA_DIR`/keys/`kry_data` are copied to EC2; nothing sensitive leaves your machine.
