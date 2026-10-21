# Inspecting a mint persistence failure

Stop writers and preserve the data directory before deciding how to recover.
A returned `MintReceipt` confirms a retained receipt, not a successful balance
update. `None` does not prove that nothing was written. The receipt log, decay
reservation, rollback checkpoint and mutable balance are separate writes, not a
cross-file transaction.

The failure diagnostics deliberately omit event details, evidence, paths and
exception text. Keep application logs private; other application logging may
include operator data. The fixed messages distinguish these outcomes:

| Diagnostic | What it establishes | What remains uncertain |
| --- | --- | --- |
| `mint receipt acknowledgement failed; stage=preparation` | Preparation raised an exception; `mint()` returned `None`. | A decay reservation may already have been written. |
| `mint receipt acknowledgement failed; stage=receipt_append` | Append, flush, fsync or file-close acknowledgement failed. | No row, a partial row or a complete row may be retained. |
| `mint receipt acknowledgement failed; stage=receipt_persisted` | An exception followed the receipt write. | Later operations and the caller's acknowledgement are incomplete. |
| `tip checkpoint did not advance or was not acknowledged` | Receipt persistence completed; checkpoint update raised. | The checkpoint may be stale; an exceptional acknowledgement does not establish the exact file outcome. |
| `balance update was not acknowledged` | The receipt was retained; the balance call raised. | The balance may be unchanged or already updated. |
| `KRY earn: ledger persistence unconfirmed` | The balance save raised inside `earn()`. | The credit may be only in memory or already on disk. This warning does not propagate as a mint failure. |
| `KRY spend: ledger persistence unconfirmed` | The debit save raised inside `spend()`. | The debit may be only in memory or already on disk. The mint chain cannot reconstruct an unrecorded debit. |

These messages report exceptions observed by the process. They cannot report a
process killed before logging, certify survival of a power loss, or establish
that an external saving happened.

## Read-only triage

1. Stop all processes that can mint, earn, spend, reconcile or compact the same
   data. Preserve the original files and logs using the operator's backup
   procedure. Work from a retained copy; do not delete or rewrite malformed rows.
2. Confirm the exact `KRY_DATA_DIR` used by the affected process. Preserve
   `kry_mint_log.jsonl`, `kry_mint_tip.json`, `kry_evidence_decay.json` and
   `kry_ledger.json`, including which files were absent. Keep this material private.
3. From the checkout, inspect a retained copy with the command below. Replace the
   path explicitly. It reports file hashes, sizes and chain-verification status,
   without printing receipt contents or verifier error strings. It writes no KRY
   state; the retained copy must remain unchanged throughout inspection.

```sh
KRY_DATA_DIR=/absolute/path/to/retained-copy PYTHONPATH=src python3 -B - <<'PY'
import hashlib
import json
import os
from pathlib import Path
from kry.kry_mint import verify_chain

directory = Path(os.environ["KRY_DATA_DIR"]).expanduser()
for name in ("kry_mint_log.jsonl", "kry_mint_tip.json",
             "kry_evidence_decay.json", "kry_ledger.json"):
    path = directory / name
    if not path.exists():
        print(json.dumps({"file": name, "present": False}))
        continue
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    print(json.dumps({"file": name, "present": True,
                      "bytes": size, "sha256": digest.hexdigest()}))
valid, errors = verify_chain()
print(json.dumps({"chain_valid": valid, "chain_error_count": len(errors)}))
PY
```

4. Review receipt presence, newline completeness and checkpoint count/tip on the
   retained copy. Chain verification alone does not reconcile the balance or
   establish a missing log is legitimate. Compare the persisted ledger's
   `total_earned`, `total_spent` and `balance` with the verified receipt totals and
   independently retained spend records. A valid chain proves recorded integrity,
   not the truth of the underlying efficiency events.
5. Record the discrepancy and choose a recovery procedure before resuming writes.
   Do not blindly retry or mint a replacement receipt. With
   `idempotent_evidence=True`, a complete retained receipt blocks the same evidence
   after restart, including a receipt whose fsync acknowledgement failed. That
   duplicate refusal does **not** repair a missing balance. Generic minting uses
   decay instead of strict deduplication; it is not an equivalent recovery retry.

Do not call `get_ledger()` as a read-only inspection tool: loading a corrupt
ledger may quarantine it and create a fresh in-memory ledger. Likewise,
`reconcile_ledger_from_chain()` writes state. It rebuilds earned totals and
subtracts the available spending total; it cannot discover a debit that was
never persisted. Reconciliation requires an explicit operator decision with
quiesced writers, preserved evidence and a reviewed spending history. This guide
does not prescribe an automatic repair or a destructive reset.

## Tested scope

`tests/test_mint_failure_outcomes.py` injects append-open, receipt-fsync,
checkpoint-replacement, earn acknowledgement and ledger-replacement failures.
Replacement failures are exercised both before and after the actual replacement.
Fresh-process retries pin receipt uniqueness and the remaining missing-balance
case. Spend tests distinguish a retained debit from an in-memory-only debit.
These are temporary-file fault tests, not hardware crash or distributed-storage
certification. No receipt format, verdict, return contract or recovery transaction
is changed by the diagnostics.
