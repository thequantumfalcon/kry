# KRY `tee_attested` — AWS Nitro Enclaves PoC

This optional PoC obtains an AWS-signed Nitro attestation carrying a parent-supplied
demonstration payload and verifies it with `scripts/kry_tee_verify.py` against the pinned
**AWS Nitro root G1**. A retained attestation captured on 2026-06-06 also has regression coverage.

## What it proves (and what it doesn't)

The parent supplies JSON (`{measurement_id, tokens_saved, avoided_model}`); the enclave
passes those bytes to NSM as `user_data`. It does not observe token usage or calculate savings.
The verifier checks the ES384 COSE signature and X.509 chain, binding that payload to the
signed document. This demonstrates attestation transport and verification, not measurement
truth, avoided provider charges, or provider inference in a TEE. Keep `--dry-run` for this
demonstration: the parent-supplied values are not a basis for a verified-savings claim.

A trusted measurement claim additionally needs reviewed measurement code, trusted input
binding and a verified enclave-image pin. The verifier compares PCR0 only when `--pcr0` is
supplied; this runner does not pin an image. It returns the document's nonce without matching
it to an expected challenge. CLI overrides are operator input, not authenticated payload values.

## Cost

Nitro Enclaves itself has **no surcharge** — you pay only EC2 + ~12 GB gp3 EBS. A
`c6i.xlarge` (4 vCPU, the cheapest viable x86 Nitro-enclave instance; ≥4 vCPU and
non-burstable are required) is on the order of $0.10–0.20/hr on-demand — **confirm the exact
figure in the EC2 pricing console**. A 1–2 hr PoC, **terminated after**, is a few dollars.
To limit ongoing charges, set a budget alarm and use the instance-termination command
printed by `launch.sh`; terminating the enclave alone does not stop EC2 charges.

## Pieces

| file | runs where | what |
|------|-----------|------|
| `enclave/` (Rust + Dockerfile) | inside the enclave | NSM `Request::Attestation` over vsock; returns the signed COSE doc |
| `parent_client.py` | EC2 parent | sends measurement+nonce, writes `attestation.bin` |
| `setup_instance.sh` | EC2 parent | installs nitro-cli + docker, allocates enclave resources |
| `run_poc.sh` | EC2 parent | build EIF → run enclave → fetch attestation → verify |
| `launch.sh` | your Mac | `aws ec2 run-instances` with `--enclave-options Enabled=true` |

## Run it (driving from the Mac)

Run these commands from `poc/nitro` in the reviewed checkout.

```bash
# 0. one-time: configure credentials (you enter them; they are not shared with the agent)
aws configure # or: export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
aws sts get-caller-identity # confirm the account/region

# 1. launch (SPENDS) — needs an EC2 key pair + a security group allowing SSH from your IP
KEY=my-keypair SG=sg-0abc REGION=us-east-1 TYPE=c6i.xlarge bash launch.sh

# 2. copy reviewed, committed source + set up (use the IP launch.sh printed)
git -C ../.. archive --format=tar HEAD | ssh ec2-user@<IP> 'mkdir -p ~/kry && tar -xf - -C ~/kry'
ssh ec2-user@<IP> 'cd kry/poc/nitro && bash setup_instance.sh' # then log out/in

# 3. run the PoC (build EIF, attest, verify)
ssh ec2-user@<IP> 'cd kry/poc/nitro && newgrp ne <<< "bash run_poc.sh"'

# 4. TERMINATE (stops spend)
aws ec2 terminate-instances --region us-east-1 --instance-ids <IID>
```

Expected: step 4 of `run_poc.sh` prints `KRY T2 TEE (Nitro) verification` with the payload's
`measurement id`, declared `tokens saved`, and PCR0. It uses `--dry-run` and mints nothing.
These fields show what the signed document contains; they do not establish savings truth.

## Notes

- The runner uses the assigned enclave CID and terminates only its created enclave
 on exit or a handled interruption. Cleanup failures report that enclave's ID and
 return a failure; terminating the EC2 parent instance remains a separate step.
- The parent setup targets Amazon Linux 2023 x86_64 and explicitly uses Python 3.11;
 AL2023 keeps `python3` on 3.9. `setup_instance.sh` installs only the verifier runtime
 dependencies from `requirements.txt`, with exact versions and wheel hashes. Source
 builds are disabled. `run_poc.sh` uses the same Python 3.11 interpreter. Core KRY
 stays stdlib-only. Refresh the lock when updating this optional runtime; it is not
 a general-purpose lock for other platforms or Python versions.
- The root pin defaults to AWS's published G1 fingerprint (`sha256(DER)`), so a wrong root
 fails closed. Verify the downloaded root per
 <https://docs.aws.amazon.com/enclaves/latest/user/verify-root.html>.
- Crate versions in `enclave/Cargo.toml` are floors; if `cargo build` can't resolve, run
 `cargo update` (or `cargo add aws-nitro-enclaves-nsm-api vsock serde_bytes`) on the instance.
- The upload uses only committed files at `HEAD`; it excludes untracked runtime files and
 `.git`. Review that snapshot before transfer: tracked files can still contain sensitive data.
 Uncommitted fixes are not included, so commit reviewed changes before copying them.
