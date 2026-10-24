# Internal ledger recovery

The mint chain records earnings, not spending. A valid chain cannot reconstruct
missing debit history or prove real savings. The internal balance ledger and the
separate settlement registry have different responsibilities.

For opt-in keyed debits, see [operation identity](KRY_OPERATION_IDENTITY.md).
Those calls record their result with the balance and require the complete
versioned journal during recovery. Activated directories use `kry_ledger.v2.json`
and an exact tombstone at `kry_ledger.json`, with the original legacy lock.
The unkeyed return behavior below remains
unchanged; keyed calls raise on unrecorded failure rather than returning zero.

`spend()` reads current state and records its decision under the ledger's process
lock. It records internal accounting; it is not transactional authorization for an
external action. A positive `spend()` or `earn()` return confirms its ledger write.
A known pre-commit failure returns zero and discards that operation's in-memory
delta; a caller can retry without an old delta merging over a competing writer.
A replace whose acknowledgement was lost still counts as successful when the
exact written bytes are proven under the lock. An uncertain outcome raises
`LedgerPersistenceError` and blocks accounting until explicit recovery; do not
retry it as a new operation. Interrupts propagate to the caller. Public earn,
spend and reconciliation roll back uncommitted in-memory changes on cancellation
and block accounting if that cleanup fails. An interrupted cache refresh also
blocks that process before its partially updated cache can be reused. A raised
interrupt does not prove that no write occurred. Preserve and review retained
state before retrying the logical operation. `can_afford()` reads a durable
snapshot without committing pending edits or reserving capacity. Reconciliation
holds the mint lock through the chain snapshot and preserves current ledger
debits. Mints retain that lock until their balance update finishes. A failed
credit leaves no pending delta that could replay after another process reconciles.

Settlement records its obligation before invoking the debit callback. An exception,
interrupt or unusable callback return leaves the debit outcome unknown: the grant
stays consumed and the receiver is not credited. Review both sides and the retained
obligation; ledger recovery acknowledgement alone does not authorize retrying that
grant. Confirmed zero-debit shortfalls retain the registry rollback path; successful
rollback leaves the grant retriable. The registry and balance ledger remain
separate records.
A reported finite partial debit replaces the existing obligation with the reported
amount while retaining its consumed grant. If that adjustment fails, the full
obligation, an updated partial obligation, or a registry/checkpoint mismatch can
remain; all prevent retrying that grant. The receiver receives no credit. Review
the retained records before resolving the outstanding debit.

If a ledger is missing while mint history remains, or its contents cannot be
parsed and validated, automatic recovery refuses to create spendable value.
An initial empty ledger must persist before the first receipt is appended. A
cached empty object does not override missing history created by another process.
Corrupt bytes are retained beside the active ledger as `kry_ledger.json.corrupt`
or `kry_ledger.v2.json.corrupt`; another damaged file is
retained under a unique suffix. An existing quarantine is not replaced. A blank
object returned after quarantine is not permission to save a fresh balance.
Read I/O failures do not quarantine bytes: accounting reports unavailable and
retries a fresh read on the next operation. `status()` exposes `available: false`
and null totals while the ledger is unavailable, rather than a fabricated zero.
Invalid JSON or UTF-8 still requires recovery.

Before replacing the ledger, a writer flushes a uniquely named
`kry_ledger.json.pending.<id>` or `kry_ledger.v2.json.pending.<id>` intent
containing the target hash. A confirmed
write or known pre-replace failure removes its intent. A crash, uncertain outcome,
or intent-cleanup failure retains it and blocks other writers and restarts.
Cleanup failure does not turn a proven committed debit into a zero return.
Even an exact target match after restart requires deliberate incident review;
the intent is local accounting evidence, not independent provenance.
On POSIX, directory synchronization orders the intent's creation before replacement
and confirms the replacement's directory entry before success. Failure of the
post-replace directory synchronization is uncertain even if the new bytes are
readable. Windows lacks a portable stdlib directory synchronization equivalent;
its guarantees remain bounded by the platform's file flush and replacement behavior.

Stop and upgrade every writer sharing a data directory before using these rules.
Older versions do not enforce pending-write incidents or their recovery metadata;
running an old writer or downgrading against this directory can bypass those guards.
Keep the pre-upgrade backup, and review any rollback or restored snapshot explicitly.

Before recovery:

1. Stop writers using the affected data directory. Keep the ledger, quarantines,
   mint log, tip checkpoint and decay state unchanged in a private backup.
2. Recover debit totals from a previously trusted ledger plus complete retained
   debit records through the incident. The bounded event list is not a complete
   historical debit journal. Do not infer zero spending from absent records.
3. Restore a reviewed ledger containing its known `balance`, `total_earned`,
   `total_spent`, and `events`. Recovery acknowledgement refuses a negative
   restored balance before writing anything; it does not clamp that balance or
   reduce the debit total. Establish the complete accounting basis before
   acknowledgement, including any earnings missing from a stale snapshot.
   Do not erase spending or invent credits to make the balance nonnegative.
   If keyed debits have been activated, restore the entire versioned envelope
   at `kry_ledger.v2.json` and its exact legacy-path tombstone, not just these
   scalars. Preserve changed tombstone bytes as a uniquely named
   `kry_ledger.json.corrupt.<id>` incident before restoring the marker.
   Acknowledgement refuses an absent or altered marker and never falls back to
   the retained `kry_ledger.json.pre-v2` migration backup. Interrupted activation
   also requires review of both files and the retained intent; do not infer
   whether the keyed debit committed from the marker alone.
   Missing IDs cannot be reconstructed
   from the bounded event list, and a valid old snapshot is not proof of completeness.
   Keep the incident evidence. If the debit total
   cannot be established, leave accounting blocked; the earn-only chain cannot
   supply the missing fact. A legacy blank replacement beside a quarantine is
   ambiguous and remains blocked even though that replacement parses correctly.
4. Call `kry_token.acknowledge_ledger_recovery(reason="...")` with the reason and
   evidence for the reviewed restoration. This records an operator assertion and
   hashes of retained quarantine and pending-write files in the restored ledger.
   It does not reconstruct or authenticate any totals. The incident files stay in
   place; a new or changed incident requires another review. Preserve this
   recovery metadata with subsequent backups. A normal return means the ledger
   was readable and available under the lock when acknowledgement completed.
   If acknowledgement raises, do not assume nothing was written or retry blindly:
   its metadata can have committed while cleanup of its own pending intent failed.
   Keep writers stopped, preserve the new evidence, and review the retained ledger
   and incidents before another explicit acknowledgement. Accounting remains
   blocked when an unacknowledged intent remains. A lost replace acknowledgement
   resolved by exact bytes and the durability check can still complete normally.
5. Reopen the ledger and run `reconcile_ledger_from_chain()`. It verifies the chain
   and aligns earnings while retaining supplied debit totals. Debits exceeding
   chain earnings cause refusal, rather than being erased. Review the result
   before resuming writers.

Some old versions wrote zero-credit receipts without creating a ledger. A
zero-only chain does not prove there were no direct earnings or debits. If
independent retained records establish that this was a clean installation with
no such activity, deliberately restore `{"balance": 0, "total_earned": 0,
"total_spent": 0, "events": []}` and follow the acknowledgement step above.
Otherwise restore the established totals or remain blocked. Do not automatically
initialize missing accounting from the zero-only chain.

The restored ledger remains operator-supplied accounting. Reconciliation does
not authenticate the recovered debit total, bind ownership, establish a provider
charge or prove financial settlement. Deleting the entire history also removes
the evidence needed to distinguish a lost history from a fresh installation.

These locks do not create a transaction across the receipt log, checkpoint and
balance file. A crash can retain a receipt without its credit. A successful receipt
return does not by itself confirm a durable balance update. Stop on those
warnings and reconcile retained records before authorizing further work; retrying
under a different evidence ID is not recovery. A lost replace acknowledgement is
checked while the ledger lock still excludes other writers, without relying on
IDs surviving the bounded event list. Filesystem durability and locking limits
still apply, especially on shared network filesystems.

Direct `KRYLedger.save()` is a lower-level compatibility API: on a known failed
write it raises and retains explicitly edited pending deltas for a later save.
It is not a debit authorization API. Public earn, spend and reconciliation restore
their operation snapshots on known failures and never retain those deltas.

Ledger scalars retain floating-point precision on disk; display rounding no
longer changes the accounting basis. New numeric inputs and cumulative totals
must remain finite. Historical receipts and their verifier verdicts are not
rewritten by these checks.
Reconciliation accepts at most eight floating-point units of representation error,
capped at `1e-9` KRY, when recorded spending barely exceeds chain earnings. It
preserves the debit total and sets the residual balance to zero; it neither adds
credit nor forgives a material excess. Repeated reconciliation has the same result.

Settlement keeps its existing debit-versus-obligation shortfall check. For measured
balance conservation, subtraction can lose low bits even when the forward debit
and credit are correct. An additional check accepts only exact forward equations,
positive represented movement on both sides, and discrepancies no larger than
`1e-6` KRY for either movement or their difference. This is an absolute bound,
not a percentage of the balance. Transfers whose expected balance changes cannot
meet it, or whose receiver total would overflow, refuse before invoking the debit.
This numeric check does not make the separate ledgers an atomic payment system.
