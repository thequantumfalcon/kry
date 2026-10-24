# Retrying internal debits

This API is unreleased and is not part of the v0.1.14 package.

The keyed debit API records an operation ID and its result in the same atomic
ledger replacement as the balance change. Retrying the same ID with the same
parameters returns that result without another debit. It protects internal KRY
accounting while the complete ledger history is retained. It does not make an
external API request, provider charge or receiver credit exactly once.

This API is opt-in. Before the first keyed debit, stop every writer sharing the
data directory, make a private backup, and upgrade all writers to a version that
supports the operation journal. Do not activate it in a directory used by older
installations or a host integration that reads the old ledger JSON directly.

## Using an operation ID

Generate and persist an ID in the caller's work record before attempting its debit.
Use an identifier generated from at least 128 random bits, for example
`secrets.token_hex(16)`. The parser checks syntax, not randomness. IDs must be nonempty strings
without `:` or `,`. Never generate a new ID merely because a call was interrupted.

```python
from kry.kry_token import debit_kry, ledger_operation, spend

# In practice, load this ID from the caller's retained work record.
operation_id = "a7876198f5f84687a239db689cc39ace6"
debited = debit_kry(100.0, op_id=operation_id)
assert debit_kry(100.0, op_id=operation_id) == debited
record = ledger_operation(operation_id)

# A different work item may use the existing model/token calculation.
debited_for_route = spend(
    "or/anthropic/claude-opus-4.8", 100, "routing allowance",
    op_id="29178965b53146f18d94c7a45dc3d4107",
)
```

`debit_kry(amount, *, op_id)` accepts an amount in KRY directly. `spend(model,
output_tokens, detail="", *, op_id=None)` retains the existing unkeyed behavior
when `op_id` is omitted. Both debit up to the available balance; inspect the
returned amount for a shortfall. A recorded zero or partial debit remains that
operation's result even if more funds arrive later. A new decision requires a
new work item and ID.

The operation's kind and request parameters are bound to a SHA-256 digest. An
ID reused with different parameters raises `ValueError` before another debit.
For `spend`, the request includes the model, token count and detail. A successful
retry returns the stored result even if the price table subsequently changes.
Do not alternate `spend` and `debit_kry` for the same ID.
Keyed numeric inputs must be finite and nonnegative; integers that would lose
precision when converted to the ledger's floating-point representation are refused.

A keyed call returns only a durably recorded result, including zero. A failed
write or unavailable ledger raises instead of returning an unrecorded zero.
Interruptions still propagate. After reviewing an unresolved write, restoring the
complete journal and following [ledger recovery](KRY_LEDGER_RECOVERY.md), retry with the same ID and parameters
or inspect `ledger_operation(op_id)`. A replay does not write another event.

## What lookup establishes

`ledger_operation(op_id)` reads the healthy durable ledger under its lock. It
returns a copy of the recorded operation or `None` if that ID is absent from the
current journal. It refuses an unavailable ledger or a legacy ledger that has no
operation journal. A lookup does not flush pending in-memory changes.

`None` means **not found in this snapshot**. It does not mean “no debit occurred.”
An unkeyed debit, a different directory, an incomplete restore or rollback can
leave no matching ID. The journal is local accounting, not independently signed
evidence. Do not use a lookup miss to release a settlement obligation, and do not
credit a receiver merely because a payer debit record exists.

## Storage and recovery

The first successful keyed write changes only that data directory's ledger to a
versioned envelope containing its accounting state and permanent `ops` map.
There is no additional journal file or separate balance/journal commit. The
existing lock, replacement, intent and recovery protocol still applies.
Operation IDs are separate from the bounded 500-event list and are never pruned
in this version. The journal grows with the number of distinct IDs, and each
ledger replacement includes the full map. This is a small local ledger design,
not a high-volume payment database.

Keep the complete envelope, including every operation record, in backups and
restores. Earnings, reconciliation and recovery acknowledgement preserve the
journal; acknowledgement does not reconstruct missing IDs or establish that a
restored snapshot is complete. Do not flatten the envelope into legacy scalars.
Do not remove entries to reduce file size. No pruning API or watermark is
implemented; old IDs remain valid for replay while their records are retained.

Do not downgrade an activated directory. v0.1.14 refuses this envelope and may
quarantine its unchanged bytes; v0.1.12 can instead overwrite it with a blank
legacy ledger. A format marker cannot repair historical writer behavior. If an
old writer touches the directory, stop writers, preserve all retained files and
review the complete journal before resuming. Restoring an older valid snapshot
or deleting the entire history is not automatically detected.

## Settlement scope

The current settlement registry still consumes a grant after an unknown debit
outcome. This patch does not add a resolution API, release capacity, retry a
consumed grant, pass IDs automatically to callbacks or change registry anchors.
The callback must return `0` for a confirmed refusal that moved nothing; an
exception leaves its outcome unknown. Review the retained records rather than
editing the registry.

Versioned registry resolutions, monotonic obligation/release totals, canonical
hash encoding, longer new grant IDs, journal-bound debit adapters and durable
receiver deduplication need a separate registry profile. So do safe ID pruning
and evidence sufficient to establish nonoccurrence. The local operation journal
is a prerequisite for that work, not completion of the settlement protocol.
