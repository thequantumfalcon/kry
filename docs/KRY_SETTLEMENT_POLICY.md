# Settlement identity and registry boundaries

KRY's settlement prototype checks accounting under an exact party label. It does
not authenticate a person, assign ownership of an attestation or establish a
transferable asset. A `VALID` result from `verify_settlement` means the supplied
attestation, registry snapshot and offer pass their arithmetic checks.

## What the default check does not prevent

After an attestation's balance is settled under `A`, a second offer under `A` is
subject to that party's settled balance. An offer under `A ` or `C` uses another
registry key. Even an authenticated `C` can present `A`'s public attestation;
the attestation does not carry an authenticated owner. Adding a holder string
and recomputing an unkeyed hash would not solve this.

## Required host policy when ownership matters

The existing `set_settlement_guard(guard)` hook runs at `verify_and_accept` before
a grant is issued. The host must supply trust inputs independently of the offer:

1. Obtain the principal from the host's authenticated request context. Reject an
   unknown principal and require `offer.from_party` to equal its enrolled account
   identifier. Do not silently strip or merge existing identifiers.
2. Enroll that account's receipt chain through an independently trusted process.
   Retain a non-genesis anchor with its count and chain hash. Do not auto-enroll
   whichever caller first presents a public attestation.
3. Check that the offered attestation retains the enrolled prefix using
   `scripts/kry_verify.py::verify_attestation_against_anchor`, in addition to the normal full attestation check.
   Reject unknown chains and re-minted prefixes. An anchor identifies a retained
   chain prefix; it does not prove the original events were true.
4. Keep the accepted offer, grant and authenticated identity bound together in
   the host's workflow. The hook runs at acceptance only. Prevent callers from
   constructing unchecked grants, bypassing it or substituting another offer
   before `settle`.
5. Use the same canonical account identity across reservations, leases and the
   settlement registry. All participating nodes need the relevant shared
   authority; a local registry snapshot does not prevent concurrent remote spends.

Register the policy once and obtain request identity from an appropriate isolated
request context. Changing a global guard for each concurrent caller would mix
identities. The test policy uses constructed identities and enrollment records;
it demonstrates enforcement given trusted inputs, not an identity service.

## Registry encoding

The existing registry hash format is retained. A scalar `grant_id`, or each
element of `grant_ids`, must be a nonempty string without `:` or `,`. Lists must
not contain duplicate IDs, and the two fields cannot occur together. Those
constraints prevent moving colon-separated fields or collapsing comma-joined
grant IDs without changing a hash. They are enforced when writing and reading.

Legacy rows with no grant-ID field and empty legacy compaction lists remain
readable. Such records do not retrospectively gain per-grant replay protection.
Party strings may contain colons; the constrained grant component and numeric
amount remain unambiguous from the right. No existing party is renamed.

The registry hash and receipt hash are unkeyed integrity checks, not digital
signatures. A future canonical, versioned registry format would aid interoperability;
it would still need separate authentication, ownership and publication policies.
