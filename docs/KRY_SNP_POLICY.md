# Optional SNP verification and mint policy

The SNP path is an optional prototype. Signature inspection and permission to mint
`tee_attested` value are separate results. No genuine SNP hardware report has yet
been retained as a regression fixture; synthetic tests do not establish a live
deployment or the truth of a saving.

`verify_report(...)["verified"]` checks a signature and certificate chain under
the supplied root. A custom root can satisfy that check. It cannot satisfy
`mint_eligible`: minting additionally requires one of the recognized AMD roots
below and a separately selected policy matching the report and VCEK. `run()` is
an internal consumer of that already-verified result, not a verifier for arbitrary
JSON claiming `mint_eligible: true`.

## Enrollment and supported scope

Use a policy obtained independently of the report producer. Set `schema` to
`kry_snp_policy/v1`, `product` to `Milan` or `Genoa`, `report_version` to `2`, and
`measurement` to the exact 96-hex-character expected launch measurement. `min_tcb`
must explicitly contain integer byte values for `bootloader`, `tee`, `snp` and
`microcode`; the four minima cannot all be zero. Select each minimum from reviewed,
dated AMD guidance for the actual product and deployment. This repository supplies
no universal security floor or recommended example values.

The report's current, reported, committed and launch TCB components must each meet
those minima. Reported TCB must not exceed current TCB. The VCEK's structure,
product, hardware ID and reported TCB extensions must agree with the report and
policy. Debug must be disabled, VMPL must be zero, the leaf key must use P-384,
and supported-version reserved fields and signature padding must be canonical.
Other report versions, VLEK/CSVCEK, Turin, Venice and unsupported product families
cannot mint. Their layouts must be reviewed before adding support.

```sh
python3 scripts/kry_snp_verify.py report.bin \
  --vcek vcek.der --ask ask.pem --ark ark.pem \
  --measurement-json measurement.json --policy enrolled-policy.json
```

`--dry-run` allows signature inspection without minting. Its output explicitly
states whether hardware policy is satisfied, including on the final line. Exit 0
means the supplied-root signature inspection and measurement parsing succeeded;
it does not mean the report satisfies AMD hardware policy or is eligible to mint.
Custom-root inspection can succeed with hardware policy unsatisfied.
`--ark-sha256` is an additional file
pin, not a way to replace the recognized vendor roots. No network request is made
by verification. Root rotation requires a reviewed code update. Revocation feeds
are not fetched or checked by this prototype; enrollment must review current AMD
collateral and advisories. A matching policy does not establish latest firmware,
runtime correctness, counterfactual savings, external validation or settlement.

## Root provenance

On 2026-10-23, each chain was fetched over HTTPS from AMD KDS. The second PEM
certificate was the self-signed ARK; its DER digest was independently checked with
OpenSSL. Public DER copies in `tests/fixtures/amd/` pin the regression inputs.

| Product | Official source | ARK SHA-256 of DER |
| --- | --- | --- |
| Milan | [AMD Milan chain](https://kdsintf.amd.com/vcek/v1/Milan/cert_chain) | `69d063b45344d26a2e94e1f4210de49ef555308287d4c174445c95639a540bcd` |
| Genoa | [AMD Genoa chain](https://kdsintf.amd.com/vcek/v1/Genoa/cert_chain) | `4c6598d19c18719c5dfd4a7d335f674e5bfe1d8f800cea2cf270c10d103db2f1` |

Sources for the supported field checks are AMD's [SNP firmware ABI 56860](https://docs.amd.com/api/khub/documents/NJQrpYY7KZGtlxDEdBGtzA/content)
and [VCEK/KDS specification 57230](https://docs.amd.com/v/u/en-US/57230).
TCB layouts vary by family; fields from newer layouts must not be applied to an
older report version. The legacy unwrapped and DER-octet forms of the 64-byte
family-19h VCEK hardware ID are accepted, with exact content comparison.

## Replay identity and retained ledgers

`report_sha256` remains the digest of the complete source file for audit identity.
`statement_sha256` hashes the verified signed body, excluding signature encoding.
New SNP replay evidence uses `snp-statement/v1` and records that digest in the
receipt detail. Changing unsigned padding is rejected; changing signature
presentation cannot create a fresh replay bucket.

An old SNP receipt has no recoverable signed-body identity in its retained receipt
alone. If the ledger contains a `tee_attested(snp)` row without the new statement
marker bound by its evidence hash, further SNP minting returns `MIGRATION_REQUIRED`. The check also runs under
the mint lock before fresh credit. It does not block other TEE profiles. Keep the
old ledger and original reports for a separately reviewed migration; do not delete
history or reset decay state to bypass this check. Historical receipts, balances
and attestation verdicts are not rewritten.

## Input bounds

Measurement and policy JSON are bounded to 16 MiB, certificates to 64 KiB, and raw
reports to 1,184 bytes. Oversized, malformed, invalid-UTF-8 and excessively nested
inputs produce controlled refusals. Cryptographic dependencies remain optional.
