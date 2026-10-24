# Recovering interrupted journal activation

This procedure applies to the operation journal introduced in v0.1.15.
Read [ledger recovery](KRY_LEDGER_RECOVERY.md) and
[operation identity](KRY_OPERATION_IDENTITY.md) first.

New activations publish a versioned attempt record before the backup, journal,
legacy tombstone and completion sentinel. An interrupted attempt blocks ordinary
accounting and generic `acknowledge_ledger_recovery()`. Two explicit APIs handle
the supported recovery states under the original ledger lock. Neither API proves
historical completeness or an external debit outcome.

## New activation protocol

Stop all writers and preserve the entire directory before recovery. Keep the
ledger, journal, `.pre-v2`, `.v2-activated`, pending and quarantine incidents, and
`.kry_activation_*` temporary files. Do not delete evidence to satisfy a guard.

| Retained state | Supported action |
| --- | --- |
| No published activation intent or completion evidence | Ordinary retry with the same operation ID; unpublished temporary files alone do not change accounting. |
| Valid `kry_ledger.json.pending.activation-<id>`, no completion evidence | Operator-reviewed abandonment may preserve complete current legacy accounting. |
| Exact attempted journal plus a valid activation intent | Explicit completion can finish the tombstone and sentinel. |
| Journal, journal incident, tombstone or completion sentinel present | Abandonment refuses, even if another file is missing. |
| `kry_ledger.json.pending.abandon-<id>` | Resume the same frozen abandonment review; changed accounting or evidence refuses. |
| Malformed, conflicting, unreadable or symlinked required evidence | Preserve the bytes and establish a reviewed restoration; neither helper guesses. |

The activation intent binds a 128-bit attempt ID, the exact original legacy hash
(or explicit original absence), and the intended journal hash. Each new immutable
file is written to a temporary file, flushed, verified and published without
replacing existing evidence. The final `.pre-v2` is complete or absent; a temporary
file may be partial. A directory synchronization barrier follows publication on
POSIX. Windows retains the existing limits of portable file flush/replacement;
there is no portable stdlib directory-fsync guarantee.

To finish a retained candidate:

```python
from kry.kry_token import complete_journal_activation, ledger_operation

# All writers are stopped; the complete retained attempt has been reviewed.
complete_journal_activation()
record = ledger_operation(original_operation_id)
```

Completion requires the exact candidate envelope and backup bound by the intent,
the original legacy bytes or exact tombstone, and the expected incident set. It
refuses if an intervening legacy writer changed accounting. It writes the missing
completion pieces and removes the active intent after their durability barriers.
Retrying the original keyed debit then returns its recorded result. If completion
is interrupted, preserve the state and resume it while the intent remains. If the
intent was already removed and the complete fence is healthy, inspect the recorded
operation and use normal same-ID replay; completion does not require a second call.

For a pre-journal attempt, only when independent retained records establish that
the current legacy accounting omits no committed journal debit:

```python
from kry.kry_token import abandon_incomplete_journal_activation

receipt = abandon_incomplete_journal_activation(
    reason="Incident reference and basis establishing complete retained accounting",
)
assert receipt["basis"] == "operator_asserted"
```

Abandonment does not subtract or restore a guessed amount. It preserves the current
accounting values and events, including reviewed intervening legacy changes. It
records original and observed hashes and whether they differ. A changed digest
does not authenticate the change: the operator must establish its legitimacy.

The helper first publishes a durable abandonment plan. It archives the observed
legacy bytes, activation intent, complete backup when present, and the assertion
under uniquely named `kry_ledger.json.corrupt.activation.<id>.*` incident files.
Here `corrupt` is the existing retained-incident namespace, not a claim that every
archived byte was corrupt. Their hashes are bound in recovery metadata before
the active plan is removed. Unpublished temporary files remain preserved but are
not authoritative incident records; retain them in the private incident backup.

If interrupted, calling abandonment again resumes the original frozen review.
Changing the reason argument does not replace that review. Any subsequent change
to the reviewed accounting or incident files blocks resumption. An immediate
repeat after success returns the retained receipt without changing accounting.
A later generic acknowledgement may replace that convenience receipt field, but
the archived assertion and incident bindings remain. Normal keyed activation can
then proceed using the caller's independently established retry decision.

The sentinel `kry_ledger.json.v2-activated` binds the attempt ID and initial journal
hash. New journals retain that identity in `accounting.recovery.journal_activation`.
Ordinary writes and generic recovery preserve it. The healthy reader checks the
completion identity. After the intent is removed, the initial hash is retained
for review and is not revalidated against historical journal bytes or later states.
Missing or malformed completion evidence, or a mismatched attempt identity, blocks
new accounting. Restore the reviewed evidence; do not fabricate a new sentinel.
Previously created journals without this metadata and sentinel remain supported
under their original recovery contract; they are not silently relabeled.

**Local absence cannot prove nonmovement.** Deleting or replacing every completion
record together can make a completed activation indistinguishable from an early
attempt. Abandonment is an operator assertion, not a historical rollback detector.
It does not release a settlement obligation or authorize a receiver credit. Keep
accounting blocked when the required completeness or operation outcome is unknown.

## Earlier activation protocol and reviewed restoration

The earlier unreleased writer opened its final backup before writing the intent.
Its interrupted `.pre-v2` can be empty, partial or complete. Such a directory has
no versioned activation intent for the new helpers to resolve. The procedure below
documents explicit restoration for that older protocol and its envelope format.
Do not remove a new-protocol intent or sentinel to force this older recovery path.
There is no automatic fallback from missing journal history to a legacy backup.

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
