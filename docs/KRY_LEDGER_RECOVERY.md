# Internal ledger recovery

The mint chain records earnings, not spending. A valid chain cannot reconstruct
missing debit history or prove real savings. The internal balance ledger and the
separate settlement registry have different responsibilities.

`spend()` reads current state and records its decision under the ledger's process
lock. It records internal accounting; it is not transactional authorization for an
external action. For compatibility, a write failure can return the selected amount
and emit an unconfirmed-persistence warning. That return is not evidence of a
durable debit or permission to proceed. `can_afford()` refreshes its snapshot but
does not reserve capacity. Reconciliation
holds the mint lock through the chain snapshot and preserves current ledger
debits. Mints retain that lock until their balance update finishes, preventing
reconciliation from counting an appended receipt and then crediting it again.

If a ledger is missing while mint history remains, or its contents cannot be
parsed and validated, automatic recovery refuses to create spendable value.
An initial empty ledger must persist before the first receipt is appended. A
cached empty object does not override missing history created by another process.
Corrupt bytes are retained as `kry_ledger.json.corrupt`; another damaged file is
retained under a unique suffix. An existing quarantine is not replaced. A blank
object returned after quarantine is not permission to save a fresh balance.

Before recovery:

1. Stop writers using the affected data directory. Keep the ledger, quarantines,
   mint log, tip checkpoint and decay state unchanged in a private backup.
2. Recover debit totals from a previously trusted ledger plus complete retained
   debit records through the incident. The bounded event list is not a complete
   historical debit journal. Do not infer zero spending from absent records.
3. Restore a reviewed ledger containing its known balance, total earned and total
   spent. Keep the incident evidence. If the debit total cannot be established,
   leave accounting blocked; the earn-only chain cannot supply the missing fact.
4. Reopen the ledger and run `reconcile_ledger_from_chain()`. It verifies the chain
   and aligns earnings while retaining supplied debit totals. Debits exceeding
   chain earnings cause refusal, rather than being erased. Review the result
   before resuming writers.

The restored ledger remains operator-supplied accounting. Reconciliation does
not authenticate the recovered debit total, bind ownership, establish a provider
charge or prove financial settlement. Deleting the entire history also removes
the evidence needed to distinguish a lost history from a fresh installation.

These locks do not create a transaction across the receipt log, checkpoint and
balance file. A crash can retain a receipt without its credit, or lose a debit
whose persistence was explicitly reported as unconfirmed. A successful receipt
return does not by itself confirm a durable balance update. Stop on those
warnings and reconcile retained records before authorizing further work; retrying
under a different evidence ID is not recovery. A lost replace acknowledgement is
checked while the ledger lock still excludes other writers, without relying on
IDs surviving the bounded event list. Filesystem durability and locking limits
still apply, especially on shared network filesystems.

Ledger scalars retain floating-point precision on disk; display rounding no
longer changes the accounting basis. New numeric inputs and cumulative totals
must remain finite. Historical receipts and their verifier verdicts are not
rewritten by these checks.
