# HOLE D closed on real hardware — cross-machine lease over SMB (2026-06-13)

## Claim under test

KRY's settlement double-spend guard is, in the shipped code, proven only **within a single
process/registry** (`docs/KRY_VERACITY_BINDING.md`, HOLE D). The disclosed fix is a **shared
lease authority** that two counterparties must consult before settling against the same attested
balance. Until now that fix was demonstrated only by `lab/hole_d_double_spend.py` simulating four
nodes as four data dirs **in one process** — it never proved the atomic lease survives a **real
network filesystem across distinct machines**. That was the audit's open ceiling.

## What was run

Two **physically separate** Windows machines (node-b = <node-b-ip>, node-c = HOST-C), each authenticating
to a third machine's SMB share (`\\HOST-A\share`, host = HOST-A) from its own SSH session,
contended for **one** lease on shared storage via `lab/hole_d_lease.py` — a stdlib-only standalone of
the same `O_EXCL`-locked, ceiling-checked `leased.json` authority used in
`lab/hole_d_double_spend.py:lease()`.

Setup honesty: the share was mounted per-command with an explicit credential read from stdin
(never written to disk, never on a command line, `/persistent:no`), so each node's SMB session was
ephemeral. Both nodes ran Python 3.13.12.

### Test 1 — cross-machine ceiling (sequential)

A is attested at 10,000 KRY. node-b leases 7,000; then node-c tries to lease 7,000 more for the **same key**.

```
NODE node-b: lease 7000 (prior 0, ceiling 10000) -> GRANTED
NODE node-c: lease 7000 (prior 7000, ceiling 10000) -> DENIED
```

node-c — a different machine — read `prior_leased = 7000` from the shared `leased.json` over SMB and was
refused (14,000 > 10,000). The shared state is **visible across machines**; the guard is no longer
single-registry.

### Test 2 — atomicity under a real race (concurrent, ×5 across both launch orders)

Fresh key. node-b and node-c launched **simultaneously**, each holding the lock for a fixed interval so a
true contender must block on it. Three rounds with node-b launched first, then two more with node-c launched
first (to test whether the winner is just launch-order):

```
round 1 (node-b first): granted=1 winner=node-b loser blocked 2.348s on the lock -> OK atomic over SMB
round 2 (node-b first): granted=1 winner=node-b loser blocked 2.390s on the lock -> OK atomic over SMB
round 3 (node-b first): granted=1 winner=node-b loser blocked 2.379s on the lock -> OK atomic over SMB
round 4 (node-c first): granted=1 winner=node-b loser blocked 2.291s on the lock -> OK atomic over SMB
round 5 (node-c first): granted=1 winner=node-b loser blocked 2.382s on the lock -> OK atomic over SMB
```

In every round **exactly one** of two over-balance leases was granted, and the loser **measurably
blocked ~2.3–2.4 s** spinning on the other machine's `O_EXCL` lockfile before acquiring it and being
refused by the ceiling. That blocking interval is the direct evidence the lock **serialized two
distinct machines over the network filesystem** — `O_EXCL` create maps to SMB `FILE_CREATE`
(exclusive), which the share honored.

Honest scope: node-b wins all five rounds — including the two where node-c was launched first — so node-b is not
winning on launch order but because it consistently reaches the lock first (faster SMB path / process
start). The race is therefore won **deterministically by the faster machine**, not a coin flip. The
invariant proven is **"no double-grant, and the second machine is genuinely forced to wait on the
shared lock,"** which held in all 5 rounds regardless of launch order — not "the winner is
unpredictable."

## What this does and does not establish

- **Does:** the disclosed HOLE-D fix (one shared lease authority both counterparties consult) works
 across real machines on a real shared FS, with atomic single-winner semantics — not just in a
 single-process simulation. The audit's open multi-host ceiling is closed for the SMB-shared-storage
 topology.
- **Does not:** make the *shipped* `kry_settlement` multi-host by itself — the production settlement
 path still keys its guard to a single registry. Wiring settlement to consult a shared lease
 authority (file-on-shared-storage or an HTTP lease service) remains a protocol change, now
 de-risked by this demonstration. It also does not cover NFS (whose `O_EXCL` is historically
 unreliable) — the result is specific to SMB here.

## Reproduce

`lab/hole_d_lease.py` is the standalone authority core. Point two machines at the same `authdir` on
shared storage for the same key, amount over half the ceiling:

```
# on each machine, with the share reachable:
python hole_d_lease.py <node-id> <shared-authdir> A:key 7000 10000 [hold-sec]
```

Exactly one of two should print `GRANTED`; the other prints `DENIED` with a non-zero `lock_wait_s`.
