# Upgrading to KRY 0.1.17

Version 0.1.17 includes the action-input and shared-receiver fixes made after
0.1.16. It does not change receipt, attestation or settlement-registry formats.
Existing receipts and anchors keep their bytes and verifier verdicts; do not
re-mint historical records to upgrade the software.

## Action inputs: raw content by default

`kry_action.record()` now hashes every raw `args`, present `result` and supplied
`server_evidence` value, including strings that look like lowercase SHA-256
digests. Previously, a 64-character hex string was automatically treated as an
existing commitment and stored unchanged. That could expose a raw value with
that shape.

Callers that supply raw content keep the default `precommitted=False`. Callers
that already compute commitments must pass `precommitted=True`; otherwise their
digest strings are hashed again. Make this choice in the caller based on what
it supplies, not by guessing from the input's appearance.

The examples below create synthetic action receipts. Run them only with a fresh
temporary `KRY_DATA_DIR`, set before importing KRY. For example, in a POSIX shell:

```sh
action_demo_dir=$(mktemp -d)
export KRY_DATA_DIR="$action_demo_dir"
```

Raw input, including a hex-shaped string, is committed internally:

```python
from kry import kry_action

raw_args = "a" * 64
raw = kry_action.record("example", raw_args, result="ok")
assert raw.args_commit == kry_action.commit(raw_args)
assert raw.args_commit != raw_args
```

For existing commitments, opt in explicitly for the whole call:

```python
from kry import kry_action

args_digest = kry_action.commit({"topic": "demo"})
result_digest = kry_action.commit("ok")
evidence_digest = kry_action.commit({"source": "synthetic example"})
sealed = kry_action.record(
    "example",
    args_digest,
    result=result_digest,
    server_evidence=evidence_digest,
    evidence_tier=kry_action.TIER_SERVER_WITNESSED,
    precommitted=True,
)
assert sealed.args_commit == args_digest
assert sealed.result_commit == result_digest
assert sealed.server_evidence_commit == evidence_digest
```

The example uses a declared witnessed tier only to exercise evidence-commitment
storage with synthetic data. It does not establish a real witness. The default
`self_reported` tier deliberately discards `server_evidence`, storing `None`.

With `precommitted=True`, every supplied content value must be a lowercase
64-character hexadecimal SHA-256 digest. Mixed raw and committed content is
rejected with `ValueError` before the receipt is appended. This flag validates
the supplied representation; it does not authenticate the underlying evidence.

`has_result=True` is the default, so omitting `result` in precommitted mode is
invalid: `None` is not a digest. If there was no result, say so explicitly:

```python
from kry import kry_action

args_digest = kry_action.commit({"topic": "demo"})
without_result = kry_action.record(
    "example", args_digest, has_result=False, precommitted=True,
)
assert without_result.result_commit is None
```

Absent `server_evidence` still stores `None`. Updating the writer does not remove
values already retained by an older writer; handle any prior content exposure
separately without silently rewriting the retained chain.

## Concurrent settlements sharing a receiver

Complete `settle()` calls sharing the same `ReceiverLedger` instance now serialize
the receiver snapshot and final update. Previously, overlapping calls could debit
two payers while one receiver credit overwrote the other.

Use one authoritative receiver instance for calls sharing that in-memory balance.
Separate instances remain independent; this fix does not add durable receiver
storage, cross-process receiver coordination or atomic cross-ledger settlement.
Callbacks must be bounded and must not reenter settlement or reverse its lock
order. See [operating limits](OPERATING_LIMITS.md#concurrency-callbacks-and-storage).

## Upgrade procedure

1. Review action callers and select raw or precommitted mode explicitly where
   needed. Test that integration with synthetic inputs before touching live data.
2. Stop every KRY writer using the affected data directory or receiver state.
   Preserve an offline backup of the complete data directory and any host-managed
   in-memory receiver state using the host's existing procedure.
3. Install the intended release in each writer's environment, and verify both
   `kry.__version__` and the installed distribution version are `0.1.17`. Upgrade
   shared writers together; publication alone changes no running installation.
4. Run the documented ledger and attestation checks before resuming writers.
   An intact ledger needs no recovery acknowledgement just for this upgrade.
   Existing incidents still require the specific procedure in
   [ledger recovery](KRY_LEDGER_RECOVERY.md); do not acknowledge an incident merely
   to make a check pass.

KRY's internal accounting and benchmark arithmetic remain separate from actual
provider charges, payment settlement and proven cash savings. This release does
not change those evidence boundaries or supply the deferred settlement-resolution
format.
