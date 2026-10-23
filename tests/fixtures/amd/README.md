# Public AMD root certificates

These are public trust-anchor certificates, not operator data or private keys.
The self-signed ARK was extracted from each official AMD KDS chain on 2026-10-23:

- `Milan-ark.der`: <https://kdsintf.amd.com/vcek/v1/Milan/cert_chain>
- `Genoa-ark.der`: <https://kdsintf.amd.com/vcek/v1/Genoa/cert_chain>

The source endpoints are specified by AMD's [VCEK/KDS specification](https://docs.amd.com/v/u/en-US/57230).
DER fingerprints and the supported verification scope are recorded in
[the SNP policy guide](../../../docs/KRY_SNP_POLICY.md).
Tests verify the fingerprints and self-signatures. These certificates are not a
genuine guest-attestation fixture and do not establish hardware execution.
