# Recovering interrupted journal activation

This procedure applies to the unreleased operation journal. It is not part of
the v0.1.14 package. Read [ledger recovery](KRY_LEDGER_RECOVERY.md) and
[operation identity](KRY_OPERATION_IDENTITY.md) first.

Activation writes its backup before replacing the journal and legacy marker.
A crash can leave an intact `kry_ledger.json`, an empty, partial or complete
`kry_ledger.json.pre-v2`, and no `kry_ledger.v2.json`. Accounting and
`acknowledge_ledger_recovery()` correctly refuse that incomplete fence.
The backup is opened exclusively before its contents are written; an empty
backup is therefore a possible interrupted write, not evidence of an empty ledger.

The procedure below completes a reviewed restoration into the journal format.
There is no automatic rollback to legacy mode. Equal legacy and backup bytes,
or an absent journal, cannot distinguish an early activation failure from a
later journal that was lost while old files were restored. Do not use those
observations alone to conclude that a debit never committed.

## Establish the recovery basis

1. Stop every writer and upgrade all installations that share this directory.
   Preserve a private copy of the whole directory, including the legacy ledger,
   `.pre-v2`, any journal, `.pending.*`, `.corrupt*` and `.kry_*` temporary files.
   Retain empty or partial files unchanged. Do not delete or overwrite `.pre-v2`
   to make a new activation attempt succeed.
2. Establish complete accounting and operation history from independently retained
   records through the incident. Document the data directory, software version,
   affected operation ID and parameters, backup hashes, and the basis for deciding
   which writes committed. A bounded event list, target hash or readable temporary
   payload alone does not establish completeness or commitment.
3. If independent records establish that this was the first activation and its
   debit never committed, the reviewed complete legacy accounting can be used
   with an explicitly empty operation map. This is an operator assertion, not a
   conclusion the recovery code can derive. Otherwise restore all established
   debits and operation records. If their completeness or the interrupted
   operation's outcome remains unknown, keep accounting blocked.

## Version 2 envelope

`kry_ledger.v2.json` has exactly three top-level members:

| Member | Required content |
| --- | --- |
| `ledger_format` | The JSON integer `2`. |
| `accounting` | The complete reviewed accounting object, including `balance`, `total_earned`, `total_spent`, and retained `events`, `cycle_count` and recovery metadata. Preserve its established values. |
| `ops` | The complete map from operation IDs to their recorded results. It is separate from the bounded event list. |

Each operation record has exactly `kind`, `amount`, `params_sha256` and `ts`.
`kind` is `spend` or `debit_kry`; `amount` is its finite, nonnegative recorded
result in KRY, including zero or a partial result; `ts` is a finite nonnegative
timestamp; `params_sha256` is the original 64-character lowercase hexadecimal
parameter digest. Preserve IDs and digests; do not invent them from current prices
or a remembered amount. No operation amount can exceed `accounting.total_spent`.
Operation IDs must be nonblank strings without `:` or `,`.

The envelope rejects duplicate member names and nonstandard JSON constants.
Recovery requires a nonnegative balance and nonnegative earned/spent totals.
Validation establishes structural acceptability, not the truth or completeness
of the supplied accounting. Do not flatten a journal or discard an unknown field
from reviewed evidence just to make a candidate parse.

The exact bytes at the standard legacy filename `kry_ledger.json` are the
following UTF-8 line, including its final newline:

```text
{"kry_ledger_tombstone":2,"journal":"kry_ledger.v2.json"}
```

## Prepare and install the reviewed files

Prepare `reviewed-accounting.json` and `reviewed-ops.json` in a private staging
directory outside the runtime directory. For the independently established
never-committed first activation, the latter file deliberately contains `{}`.
For an existing journal it must contain all retained operation records.
This snippet packages those explicit inputs; it does not establish their accuracy,
validate the full ledger contract, or modify runtime files. It refuses to replace
existing candidate files and rejects duplicate input members. Keep the input files
with the incident record. If preparation is interrupted, preserve that staging
directory and use a fresh one; do not install a partially written candidate.

```python
import json
from pathlib import Path

def unique_members(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"duplicate input member: {name}")
        result[name] = value
    return result

accounting = json.loads(Path("reviewed-accounting.json").read_text(encoding="utf-8"),
                        object_pairs_hook=unique_members)
operations = json.loads(Path("reviewed-ops.json").read_text(encoding="utf-8"),
                        object_pairs_hook=unique_members)
envelope = {"ledger_format": 2, "accounting": accounting, "ops": operations}
encoded = json.dumps(envelope, allow_nan=False, indent=2) + "\n"
with Path("recovered-kry_ledger.v2.json").open("x", encoding="utf-8") as output:
    output.write(encoded)
with Path("recovered-kry_ledger.json").open("xb") as output:
    output.write(b'{"kry_ledger_tombstone":2,"journal":"kry_ledger.v2.json"}\n')
```

With all writers still stopped:

1. Preserve the current legacy-path bytes as a new, uniquely named
   `kry_ledger.json.corrupt.activation.<unique-id>` incident in the runtime
   directory before replacing that path. This also retains an intact legacy
   snapshot when `.pre-v2` is empty or partial. Never replace an existing incident.
   Preserve any current journal as a new
   `kry_ledger.v2.json.corrupt.activation.<unique-id>` before replacing it, too.
   Keep every original backup, pending-write file and quarantine unchanged.
2. Install the reviewed envelope at `kry_ledger.v2.json` first, then the exact
   marker at `kry_ledger.json`. For each file, write a temporary file in that same
   directory, flush and synchronize its contents, atomically replace the target,
   and synchronize the directory where the platform supports it. Do not edit a
   live JSON file in place. Windows has no portable stdlib directory-fsync
   equivalent; this procedure does not extend its durability guarantees.
3. If interrupted between those replacements, preserve the new state and resume
   from the same reviewed restoration. A journal beside legacy bytes remains
   blocked. Do not restart the original debit, discard the staged operation map,
   or infer failure from the missing marker. Restoring the marker alone does not
   acknowledge retained incidents.
4. Start a fresh process with `KRY_DATA_DIR` explicitly set to the affected
   directory. Call `acknowledge_ledger_recovery(reason="...")` with the incident
   reference and the basis for the complete restoration. This validates the
   envelope, records the operator assertion and hashes retained pending/quarantine
   incidents. It preserves the operation map; it does not fill missing history.
   If it raises, retain the files, review the outcome and follow the acknowledgement
   failure procedure in [ledger recovery](KRY_LEDGER_RECOVERY.md).
5. Reopen the ledger, inspect availability and totals, and inspect the affected
   operation with `ledger_operation(op_id)`. Reconcile against the mint chain as
   described in ledger recovery. Only then resume writers. For an established
   uncommitted keyed debit, reuse the original ID and parameters; a recorded debit
   must return its stored result. A lookup miss still means only absent from this
   restored snapshot. It does not release a settlement obligation or authorize a
   receiver credit.

Keep the restored journal, marker, original evidence and recovery metadata in
subsequent private backups. The legacy backup remains a migration record and
must never become an automatic source for missing later journal history.
