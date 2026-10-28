# Operating KRY within its guarantees

KRY verifies receipt integrity and specified arithmetic. A valid receipt does not
establish that a counterfactual saving happened, that a provider charged an account,
or that money settled. [Claims boundary](CLAIMS_BOUNDARY.md) governs these statements.

## Records and recovery

The mint chain records earnings. The balance ledger records spending as well as
earnings; the settlement registry records separate obligations. Replaying the mint
chain cannot recover missing debit history. Keep private backups of the complete
data directory and retained incidents, not just public attestations.

Use [ledger recovery](KRY_LEDGER_RECOVERY.md) for accounting incidents and
[activation recovery](KRY_JOURNAL_ACTIVATION_RECOVERY.md) for interrupted migrations.
Stop all writers before an upgrade, activation or operator recovery decision.
Do not erase incidents or acknowledge an incomplete restored journal to resume work.
An absent operation in an incomplete snapshot does not prove that no debit occurred.

[Keyed debits](KRY_OPERATION_IDENTITY.md) atomically record their result with the
balance. Reuse the same operation ID and parameters for a retry. Unkeyed calls retain
the documented ambiguity around interruption: a raised interrupt does not prove
that nothing committed. Durability checks and recovery procedures do not replace
orderly shutdown, incident monitoring or tested backups.

## Concurrency, callbacks and storage

The ordinary file locks coordinate cooperating processes on one host. Filesystem
locking, atomic replacement and flush behavior are part of the operating assumptions;
network storage must not be assumed equivalent to a local filesystem. The optional
cross-node lease has separate shared-storage and timing assumptions. It does not
turn independent registries into a consensus system. See the implementation's
`src/kry/_locks.py` and [settlement policy](KRY_SETTLEMENT_POLICY.md).

`settle()` serializes calls sharing one `ReceiverLedger` instance from the initial
receiver snapshot through the final balance, routing and receipt-list updates. The
lock order is receiver, in-process registry, then registry file. The debit callback
runs under all three; the independent post-debit balance callback runs under the
receiver lock after the registry locks are released. Keep both callbacks bounded,
avoid network waits, and do not reenter settlement or acquire locks in an order that
can cycle back to a receiver or the registry. KRY does not impose a timeout on
arbitrary callbacks or guarantee deadlock freedom for host code. A debit callback must
return `0` for a confirmed refusal that moved nothing; raising means the outcome is
unknown and retains the obligation. Automatic operation-ID forwarding, obligation
resolution and durable receiver deduplication are not implemented in this version.
The caller still owns payer concurrency and the accuracy of `a_balance_before`;
receiver serialization does not refresh or reserve that supplied payer snapshot.

Receiver synchronization is instance-local and in-process. Separate instances with
the same party label do not share a balance or a lock; the host must retain one
authoritative instance for concurrent calls. Receiver state remains in memory, and
direct host mutations are outside this synchronization. Copies and deserialized
receivers get independent locks. Stop callers before capturing an operator-owned
snapshot, whether through `dataclasses.asdict`, copying or serialization. Those
operations do not supply durable receiver storage or cross-process coordination.

The operation journal retains every ID and is rewritten with each ledger replacement.
The bounded event list is not that journal. Monitor disk use and latency as history
grows; there is no supported journal pruning API. These files suit a small internal
ledger, not an unbounded payment workload. Hard links are required for keyed intent
publication. Windows directory durability differs from POSIX; consult the recovery
guide before relying on a specific storage setup.

## Fetching evidence and optional integrations

`kry_or_fetch.py` retries only reads. Its default is four attempts, each with a
30-second request timeout. Between attempts it uses exponential full jitter starting
at 0.5 seconds, capped at 30 seconds per wait. A valid `Retry-After` sets the minimum
wait; an unresolved 429 raises `RateLimitError` when that delay exceeds 30 seconds
or the final attempt is reached. The CLI stops before the next ID, reports the
rate-limited and unattempted IDs separately, and exits 1. Other unavailable records
also make the export incomplete and the exit code nonzero. Partial exports contain
only fetched records; check the exit status before using them for reconciliation.
The research-grade driver's inline fetch stops before assessment on a rate limit.
There is no sleep after the last failure. This is an attempt and wait bound,
not an end-to-end deadline: blocking I/O and scheduling still affect elapsed time.
It does not retry inference, purchase credits or authenticate supplied export files.

Provider prompt-cache reporting values reported usage at dated prices. It is separate
from the in-memory ledger cache used by accounting; fixing interrupted ledger-cache
refreshes does not implement or validate a provider prompt cache.

PQC, TEE, Nitro and TLSNotary integrations remain optional with their own prerequisites
and trust inputs. A declared tier does not identify an independent witness by itself.
A pinned self-operated notary is not evidence of independent attestation. See
[release tier rules](RELEASE_CHECKLIST.md#optional-or-prototype-surfaces) and
[deferred evidence contracts](SPEC_DEVELOPMENT.md#deferred-contract-acceptance).
