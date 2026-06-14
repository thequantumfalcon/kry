"""kry_pqc -- optional post-quantum authenticity tier for KRY (liboqs ML-DSA).

KRY core (``src/kry/*``) stays zero-dependency, pure stdlib, and does NOT import
this package. This tier alone requires liboqs-python (the ``oqs`` module); see
``requirements.txt``. It operates only on KRY's *output* (an attestation file), so
it can never drift from or break the core.

What it adds, by axis:
    integrity    -- KRY's SHA-256 hash chain                               [KRY core]
    authenticity -- ML-DSA signatures bind an attestation to a public key  [this tier]
    veracity     -- TEE (Nitro / SEV-SNP) + TLSNotary                      [KRY tiers]

Public API (lazily loaded, so ``import kry_pqc`` does not require ``oqs`` until used):
    signer.generate_keypair / sign_attestation        -- single-signer authenticity
    threshold.make_policy / contribute / combine /     -- m-of-n trust distribution
        verify_threshold
    verify.main                                        -- stranger-runnable verifier
"""

_LAZY = {
    "DEFAULT_ALG",
    "SCHEME",
    "fingerprint",
    "generate_keypair",
    "sign_attestation",
    "sign_bytes",
}

__all__ = sorted(_LAZY)


def __getattr__(name):
    # PEP 562 lazy attribute access: defer importing `signer` (and thus `oqs`)
    # until a convenience symbol is actually requested.
    if name in _LAZY:
        from kry_pqc import signer
        return getattr(signer, name)
    raise AttributeError(f"module 'kry_pqc' has no attribute {name!r}")