"""KRY Settlement — trustless two-party transfer protocol.

The piece that makes KRY an EXTERNAL token rather than internal accounting:
two parties who do not trust each other settle a routing transaction using only
the hash-chain attestation as proof. No central clearing house.

The flow ("a token other providers' datacenters would want"):

    Party A (The host system bridge, earned KRY by being efficient)
        wants to route work to Party B (a provider with free/cheap quota).
    A presents: a SettlementOffer + an Attestation (proof of A's KRY balance).
    B independently verifies A's attestation chain (verify_attestation) — B does
        NOT trust A, B verifies the math.
    B accepts → issues a RoutingGrant → A's KRY debited, B's received-KRY credited.
    Both sides hold a signed SettlementReceipt.

Why B accepts KRY:
    B is a free-tier provider (NIM/Groq/AI Studio). Inbound routed traffic is B's
    growth metric (utilization). A's KRY is a verifiable claim that A earned its
    routing rights through proven efficiency — so B accepts KRY as the settlement
    unit and gains utilization. A spends down its reserve to access B's capacity.

The invariant that makes it a currency (NOT minting):
    CONSERVATION. Minting creates KRY from efficiency events. Settlement only
    MOVES existing KRY: A.balance decreases by exactly what B.received increases.
    Total KRY across both parties is unchanged by a settlement. Double-entry.

Distinct from existing systems (confirmed by deep-research, 108 agents):
    OpenRouter settles in USD through a central account. KRY settles peer-to-peer
    in a proof-of-efficiency unit, verified by hash chain, no central ledger.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


def _kry_data_dir() -> Path:
    """Portable data dir. Set KRY_DATA_DIR to relocate; defaults to ./kry_data."""
    d = Path(os.environ.get("KRY_DATA_DIR", "kry_data")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d

logger = logging.getLogger("kry.settlement")


class SettlementPersistenceError(RuntimeError):
    """Settlement could not be durably recorded in the double-spend registry."""


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _json_dumps(value, **kwargs) -> str:
    return json.dumps(value, allow_nan=False, **kwargs)


def _finite_number(value, field: str, *, positive: bool = False,
                   nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite JSON number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if positive and value <= 0:
        raise ValueError(f"{field} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


# ── Settlement artifacts ──────────────────────────────────────────────────────

@dataclass
class SettlementOffer:
    """Party A's offer: spend `kry_amount` for `routing_tokens` of B's capacity."""
    offer_id: str
    from_party: str
    to_party: str
    kry_amount: float
    routing_tokens: int          # how much routing capacity A wants from B
    ts: float


@dataclass
class RoutingGrant:
    """Party B's grant: B will route `routing_tokens` for A in exchange for KRY."""
    grant_id: str
    offer_id: str
    granted_by: str
    routing_tokens: int
    accepted_kry: float
    ts: float
    attested_balance: float = -1.0   # A's attested ceiling at accept time; -1 = unchecked (directly-built grant)


@dataclass
class SettlementReceipt:
    """Signed record both parties hold. Hash binds offer+grant+balances."""
    receipt_id: str
    offer: dict
    grant: dict
    a_balance_before: float
    a_balance_after: float
    b_received_before: float
    b_received_after: float
    conserved: bool              # the currency invariant held (interpret with conservation_basis)
    conservation_basis: str      # "measured" = A's real post-debit balance was read; "self_asserted" = trusts debit_fn
    receipt_hash: str


# ── Party B's receiver ledger (the accepting side) ────────────────────────────

@dataclass
class ReceiverLedger:
    """A provider's view: KRY received in exchange for routing capacity sold."""
    party: str
    received_kry: float = 0.0
    routing_sold: int = 0
    settlements: list = field(default_factory=list)


# ── Protocol ──────────────────────────────────────────────────────────────────

def make_offer(from_party: str, to_party: str, kry_amount: float,
               routing_tokens: int, *, now: float) -> SettlementOffer:
    oid = hashlib.sha256(
        f"{from_party}:{to_party}:{kry_amount}:{routing_tokens}:{now}".encode()
    ).hexdigest()[:12]
    return SettlementOffer(
        offer_id=oid, from_party=from_party, to_party=to_party,
        kry_amount=kry_amount, routing_tokens=routing_tokens, ts=now)


# ── Federated shared registry — double-spend prevention ───────────────────────
#
# A double-spend probe proved one attestation can buy routing from N
# counterparties (each verifies the chain as valid; the attestation is a stale
# snapshot). A nonce alone doesn't fix it (A mints N fresh attestations of the
# same pre-settlement balance). The real fix needs SHARED STATE: a registry,
# shared across the federated nodes, tracking each party's CUMULATIVE settled
# amount against its attested balance. This is NOT a central clearing house —
# it is federated shared state among cooperating-but-distinct nodes, which is
# also the legally-clean model (open exchange → security+MSB; federation → not).
# Honest ceiling: this closes double-spend for a FEDERATION; fully-trustless
# settlement among adversarial strangers needs global consensus (out of scope,
# and legally undesirable).
#
# Locking scope (state it plainly): registry verify+record is held under BOTH the in-process
# _REGISTRY_LOCK (thread-safety for the in-memory reservation map) AND a cross_process_lock on the
# registry file. The accept-time reservation is a PER-PROCESS optimization only (it does not cross
# processes); the AUTHORITATIVE double-spend bound is re-checked at COMMIT in settle() against the
# persisted registry total under the cross-process lock, so single-HOST, multi-process settlement is
# correct (the second committer fails closed), matching kry_mint/kry_sanctions.
# What is NOT covered is cross-NODE: the double-spend guard and the stranger re-check
# in scripts/kry_verify.py (verify_settlement) are post-facto and SNAPSHOT-based, sound
# only against the COMPLETE, MERGED federation registry. Two NODES settling the same
# attested balance concurrently, each against its own unmerged registry file, are not
# caught until those registries merge (default-off file lease narrows that window). The
# ranked multi-node fix by trust÷effort: lease/nonce/TTL (best) > signed-sync replicated
# log > primary-registry-node > full consensus (overkill). See docs/KRY_VERACITY_BINDING.md.
_REGISTRY_PATH = _kry_data_dir() / "kry_settlement_registry.jsonl"
_REGISTRY_LOCK = threading.Lock()   # thread-safety; paired with cross_process_lock(_REGISTRY_PATH)

# In-process reservation ledger — closes the verify_and_accept -> settle TOCTOU WITHIN
# one process. The registry only updates on settle(), so two concurrent accepts for the
# same party used to both pass the guard before either settled (the file-based lease
# below covers only the CROSS-node window and is default-off). This map, read and written
# under _REGISTRY_LOCK, holds each party's accepted-but-not-yet-settled amounts so the
# second accept sees the first. {party: [{"offer_id","amount","ts"}]}; entries expire
# after _LEASE_TTL so an abandoned (never-settled) accept eventually frees the balance.
_PENDING_RESERVATIONS: dict = {}


def _registry_entries() -> list:
    """Append-only hash-chained settlement log. Each entry:
    {party, amount, prev_hash, entry_hash}. Tamper-evident (HOLE E)."""
    out: list = []
    if not _REGISTRY_PATH.exists():
        return out
    with open(_REGISTRY_PATH, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = _json_loads(line)
                rec["amount"] = _finite_number(rec.get("amount"), "amount",
                                               positive=True)
                if not isinstance(rec.get("party"), str) or not rec["party"]:
                    raise ValueError("party must be a non-empty string")
                if not isinstance(rec.get("prev_hash"), str):
                    raise ValueError("prev_hash must be a string")
                if not isinstance(rec.get("entry_hash"), str):
                    raise ValueError("entry_hash must be a string")
            except Exception as exc:
                raise ValueError(f"registry line {lineno}: {exc}") from exc
            out.append(rec)
    return out


# ── Tip checkpoint — truncation/rollback guard (HOLE F) ───────────────────────
#
# A hash chain detects EDITS to existing entries but NOT removal of the tail: an
# attacker who drops the last settlement entry leaves a still-valid shorter chain,
# silently lowering a party's cumulative settled amount and freeing balance to
# re-spend. We persist a monotonic checkpoint {count, tip} alongside the registry;
# verify_registry fails closed if the live log is SHORTER than the checkpoint.
# Honest ceiling (same scope as HOLE D): the checkpoint is a local file too, so a
# disk-level attacker could roll back both consistently — for adversarial use,
# PUBLISH the tip (it is content-free, like the attestation chain_head). This turns
# SILENT rollback into DETECTED rollback under normal operation and accidental
# truncation/corruption. The tip path derives from the registry path so test
# isolation (which repoints _REGISTRY_PATH) carries it automatically.

def _tip_path() -> Path:
    return _REGISTRY_PATH.with_name("kry_settlement_tip.json")


def _read_tip() -> Optional[dict]:
    p = _tip_path()
    if p.exists():
        tip = _json_loads(p.read_text(encoding="utf-8"))
        count = _finite_number(tip.get("count", 0), "tip.count",
                               nonnegative=True)
        if not float(count).is_integer():
            raise ValueError("tip.count must be an integer")
        if not isinstance(tip.get("tip"), str):
            raise ValueError("tip.tip must be a string")
        return {"count": int(count), "tip": tip["tip"]}
    return None


def _write_tip(count: int, tip: str) -> None:
    p = _tip_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tip_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(_json_dumps({"count": count, "tip": tip}))
            f.flush()
            os.fsync(f.fileno())   # durability: the checkpoint must survive a crash, not tear
        os.replace(tmp, p)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def compact_registry(keep_recent: int = 1000) -> bool:
    """R15: bound the append-only registry. Collapse old entries into ONE
    checkpoint per party (cumulative total), preserving the hash chain from a
    fresh genesis. Keeps verify/replay O(parties + keep_recent) instead of
    O(all-settlements-ever). Only compacts when over 2×keep_recent to amortize.

    The checkpoint preserves per-party cumulative-settled exactly, so the
    double-spend guard is unaffected; only the per-event history older than the
    tail is summarized (the mint chain + receipts remain the full audit trail).
    """
    try:
        entries = _registry_entries()
    except ValueError:
        return False
    if len(entries) <= 2 * keep_recent:
        return False
    valid, _ = verify_registry()
    if not valid:
        return False  # never compact a tampered chain — fail closed
    # Sum everything except the recent tail into per-party checkpoints
    old, tail = entries[:-keep_recent], entries[-keep_recent:]
    totals: dict = {}
    party_gids: dict = {}
    for e in old:
        totals[e["party"]] = totals.get(e["party"], 0.0) + e["amount"]
        # Preserve consumed grant-ids so the no-double-settle guard survives compaction.
        party_gids.setdefault(e["party"], set()).update(
            e["grant_ids"] if "grant_ids" in e
            else ([e["grant_id"]] if e.get("grant_id") else []))
    from kry._locks import cross_process_lock
    with _REGISTRY_LOCK, cross_process_lock(_REGISTRY_PATH):
        prev = "0" * 64
        lines = []
        for party, amt in totals.items():
            gids = sorted(party_gids.get(party, ()))
            eh = hashlib.sha256(
                f"{prev}:{party}:{amt}:{','.join(gids)}".encode()).hexdigest()
            lines.append({"party": party, "amount": amt, "grant_ids": gids,
                          "prev_hash": prev, "entry_hash": eh, "checkpoint": True})
            prev = eh
        for e in tail:  # re-chain the preserved tail onto the checkpoint
            eh = hashlib.sha256(
                f"{prev}:{e['party']}:{e['amount']}:{_entry_grant_payload(e)}".encode()).hexdigest()
            e = {**e, "prev_hash": prev, "entry_hash": eh}
            lines.append(e)
            prev = eh
        import tempfile as _tf
        fd, tmp = _tf.mkstemp(dir=_REGISTRY_PATH.parent, prefix=".regc_")
        with os.fdopen(fd, "w") as f:
            for ln in lines:
                f.write(_json_dumps(ln) + "\n")
        os.replace(tmp, _REGISTRY_PATH)
        _write_tip(len(lines), prev)   # HOLE F: compaction legitimately shrinks the log
    return True


def _entry_grant_payload(e: dict) -> str:
    """The grant-id component bound into a registry entry's hash. An individual
    settlement binds its single grant_id; a compaction checkpoint binds the sorted
    union of the grant_ids it collapsed — so the consume-once (no-double-settle)
    guard is part of the tamper-evident chain and survives compaction."""
    if "grant_ids" in e:
        return ",".join(sorted(e.get("grant_ids") or ()))
    gid = e.get("grant_id")
    return gid if gid else ""


def verify_registry() -> tuple[bool, list]:
    """Replay the chain; detect any tampering (HOLE E fix). An edited or deleted
    entry breaks the hash chain and is caught here."""
    errors = []
    prev = "0" * 64
    count = 0
    try:
        entries = _registry_entries()
    except ValueError as exc:
        return False, [str(exc)]
    for i, e in enumerate(entries, 1):
        count = i
        expect = hashlib.sha256(
            f"{prev}:{e['party']}:{e['amount']}:{_entry_grant_payload(e)}".encode()).hexdigest()
        if e.get("entry_hash") != expect:
            errors.append(f"entry {i} ({e.get('party')}): hash broken — tampered")
        prev = e.get("entry_hash", prev)
    # HOLE F: truncation/rollback guard. If a checkpoint exists, the live log must
    # be at least as long and end at the same tip — a shorter log is a rolled-back
    # double-spend attempt, which the chain alone cannot see.
    try:
        tip = _read_tip()
    except ValueError as exc:
        errors.append(f"registry tip invalid: {exc}")
        tip = None
    if tip is not None:
        if count < int(tip.get("count", 0)):
            errors.append(
                f"registry truncated: {count} entries < checkpoint {tip.get('count')} "
                f"— rollback/double-spend attempt")
        elif count == int(tip.get("count", 0)) and prev != tip.get("tip"):
            errors.append("registry tip mismatch — tampered")
    return len(errors) == 0, errors


def _load_registry() -> dict:
    """Cumulative {party: settled_kry} replayed from the tamper-evident chain.
    If the chain is broken (tampered), returns a poisoned registry that
    over-counts every party to MAX so settlements fail-closed until audited."""
    valid, errs = verify_registry()
    if not valid:
        logger.error("settlement registry TAMPERED: %s — failing closed", errs[:2])
        return {"__tampered__": True}
    totals: dict = {}
    for e in _registry_entries():
        totals[e["party"]] = totals.get(e["party"], 0.0) + e["amount"]
    return totals


# ── External settlement-registry anchor (HOLE-F fix: the rollback checkpoint is LOCAL) ──
#
# The HOLE-F tip checkpoint catches a truncation only if the attacker does NOT also rewrite the
# checkpoint — but it is a local file in the same trust domain, so an operator rewrites it too and
# rolls the registry back to UN-SPEND. The only durable fix is an EXTERNAL commitment. The operator
# exports the per-party cumulative-settled totals and PUBLISHES them to an append-only medium; a
# verifier holding that published anchor detects a rollback because settled totals can only grow
# (compaction preserves them), so a live total BELOW the anchored total is an un-spend.

REGISTRY_ANCHOR_SCHEMA = "kry_settlement_anchor/v1"


def export_registry_anchor() -> dict:
    """Content-free commitment to the per-party cumulative-settled totals, for the operator to
    PUBLISH externally. (Party identifiers can be hashed by the caller if they are sensitive.)
    Once published, a rollback that un-spends a party is detected by verify_registry_against_anchor."""
    settled = _load_registry()
    if settled.get("__tampered__"):
        settled = {}
    return {"schema": REGISTRY_ANCHOR_SCHEMA,
            "settled": {p: round(a, 4) for p, a in settled.items() if p != "__tampered__"}}


def verify_registry_against_anchor(anchor: dict) -> tuple[bool, list[str]]:
    """Detect a rollback/un-spend against a PUBLISHED registry anchor obtained out-of-band. Settled
    totals are monotonic (only grow; compaction preserves them), so for every anchored party the
    LIVE cumulative-settled must be >= the anchored amount. A truncation that frees balance to
    re-spend drops it below — caught here. Only as strong as the anchor's external publication."""
    if not isinstance(anchor, dict) or anchor.get("schema") != REGISTRY_ANCHOR_SCHEMA:
        return False, [f"anchor must be a {REGISTRY_ANCHOR_SCHEMA} object"]
    anchored = anchor.get("settled")
    if not isinstance(anchored, dict):
        return False, ["anchor.settled must be an object of {party: cumulative_kry}"]
    valid, errs = verify_registry()
    if not valid:
        return False, ["live registry is not internally valid: " + "; ".join(str(e) for e in errs[:2])]
    live = _load_registry()
    if live.get("__tampered__"):
        return False, ["live registry tampered — failing closed"]
    errors: list[str] = []
    for party, amt in anchored.items():
        try:
            anchored_amt = _finite_number(amt, f"anchor.settled[{party}]", nonnegative=True)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if live.get(party, 0.0) < anchored_amt - 1e-9:
            errors.append(f"party {party}: live settled {live.get(party, 0.0)} < anchored "
                          f"{anchored_amt} — rollback/un-spend detected")
    return len(errors) == 0, errors


def _record_settled(party: str, amount: float, grant_id: str) -> None:
    """Append a hash-chained settlement entry (tamper-evident).

    Persistence is part of settlement correctness: if the registry or rollback
    checkpoint cannot be durably updated, callers must fail closed rather than
    returning a receipt the double-spend guard cannot see.

    `grant_id` is consumed once: a second settle() of the same grant (the
    accept->settle->settle in-process double-record) is rejected here, and the
    id is bound into the chain so a stranger re-verifies the one-settle invariant.
    """
    try:
        amount = _finite_number(amount, "settlement amount", positive=True)
    except ValueError as exc:
        raise SettlementPersistenceError(f"invalid settlement amount {amount}: {exc}") from exc
    if not isinstance(grant_id, str) or not grant_id:
        raise SettlementPersistenceError("settlement requires a non-empty grant_id")
    try:
        entries = _registry_entries()
    except ValueError as exc:
        raise SettlementPersistenceError(f"settlement registry invalid: {exc}") from exc
    if any(grant_id == e.get("grant_id") or grant_id in e.get("grant_ids", ())
           for e in entries):
        raise SettlementPersistenceError(
            f"grant {grant_id} already settled — refusing double-settle")
    prev = entries[-1]["entry_hash"] if entries else "0" * 64
    entry_hash = hashlib.sha256(f"{prev}:{party}:{amount}:{grant_id}".encode()).hexdigest()
    rec = {"party": party, "amount": amount, "grant_id": grant_id,
           "prev_hash": prev, "entry_hash": entry_hash}
    try:
        _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_REGISTRY_PATH, "a", encoding="utf-8") as f:
            f.write(_json_dumps(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())   # durability: a torn append reads as tampered -> fail-closed DoS
        _write_tip(len(entries) + 1, entry_hash)   # HOLE F: advance the rollback checkpoint
    except Exception as exc:
        raise SettlementPersistenceError(
            f"settlement registry write failed for {party}: {exc}"
        ) from exc


# ── HOLE D fix: cross-node lease/nonce/TTL authority (real-time federation guard) ─
#
# The registry guard above is sound only against a MERGED federation registry; two
# nodes settling the same attested balance concurrently, each against its own
# unmerged registry, race straight through it (the HOLE D corollary documented
# above). The ranked fix — lease/nonce/TTL — closes that REAL-TIME window: before a
# node issues a grant it RESERVES the amount with a shared authority — one file on
# storage every node reaches (NFS/SMB). The authority atomically grants iff the
# party's cumulative ACTIVE (non-expired) leases stay within its verified attested
# ceiling.
#
#   key    = the spending party — cumulative across ALL its attestations, so minting
#            N fresh attestations of the same balance cannot lift the ceiling (the
#            multi-attestation hole the registry comment above calls out).
#   nonce  = offer_id — re-leasing the same offer is idempotent, never double-counted
#            (a retried/replayed accept does not consume balance twice).
#   ttl    = how long an unconsummated reservation holds balance before it frees,
#            covering the verify→settle→registry-merge window. Honest scope (same as
#            the registry's "merged registry" ceiling): the lease closes the
#            real-time race; periodic registry merge closes the long term.
#
# Default-OFF: active only when KRY_SETTLE_LEASE_DIR points at shared storage, so
# single-node behaviour is byte-for-byte unchanged. O_EXCL (not flock) is used on
# purpose — flock over NFS is unreliable (see _locks.py); an O_EXCL create is the
# portable cross-host mutex, and it is the primitive the lab prototype proved
# (lab/hole_d_double_spend.py). Honest ceiling: O_EXCL over NFS is itself only
# best-effort on some exotic servers, but it is the strongest portable choice and
# the merge-based registry remains the long-term backstop.

try:
    _LEASE_TTL = _finite_number(float(os.environ.get("KRY_SETTLE_LEASE_TTL", "3600")),
                                "KRY_SETTLE_LEASE_TTL", positive=True)
except ValueError:
    _LEASE_TTL = 3600.0  # seconds


def _lease_dir() -> Optional[Path]:
    d = os.environ.get("KRY_SETTLE_LEASE_DIR")
    return Path(d).expanduser() if d else None


_LEASE_LOCK_STALE_S = 30.0   # a .lock older than this is presumed orphaned (holder crashed)


def _lease_lock(authdir: Path) -> None:
    lock = authdir / ".lock"
    deadline = time.monotonic() + 60.0   # bounded: never spin forever on an orphaned lock
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()}:{time.time():.3f}".encode())
            finally:
                os.close(fd)
            return
        except FileExistsError:
            # Steal an orphaned lock — a holder that crashed between create and unlink would
            # otherwise deadlock every future settlement (availability DoS).
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue   # lock vanished — race the create again
            if age > _LEASE_LOCK_STALE_S:
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() > deadline:
                raise SettlementPersistenceError(
                    "lease lock contended > 60s — aborting rather than deadlocking")
            time.sleep(0.001)


def _lease_unlock(authdir: Path) -> None:
    try:
        os.unlink(authdir / ".lock")
    except FileNotFoundError:
        pass


def _lease_write(p: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".lease_")
    with os.fdopen(fd, "w") as f:
        f.write(_json_dumps(data))
    os.replace(tmp, p)


def _acquire_lease(party: str, amount: float, ceiling: float, *, nonce: str,
                   now: float, authdir: Path) -> bool:
    """Atomically reserve `amount` for `party` against `ceiling` on the shared
    authority. Grants iff cumulative ACTIVE (non-expired) leased + amount <=
    ceiling. Re-leasing the same nonce is idempotent (returns the prior grant, no
    double count). Cross-host safe via an O_EXCL lockfile."""
    authdir.mkdir(parents=True, exist_ok=True)
    _lease_lock(authdir)
    try:
        p = authdir / "kry_leases.json"
        try:
            data = _json_loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception:
            return False
        leases = data.get(party, [])
        # Drop expired holds (TTL) — frees balance from abandoned settlements.
        try:
            leases = [
                {
                    "amount": _finite_number(lo.get("amount", 0.0), "lease.amount",
                                             positive=True),
                    "ts": _finite_number(lo.get("ts", 0.0), "lease.ts",
                                         nonnegative=True),
                    "nonce": str(lo.get("nonce", "")),
                }
                for lo in leases
                if now - _finite_number(lo.get("ts", 0.0), "lease.ts",
                                        nonnegative=True) < _LEASE_TTL
            ]
        except Exception:
            return False
        # Idempotent replay: the same offer already holds a lease.
        if any(lo.get("nonce") == nonce for lo in leases):
            data[party] = leases
            _lease_write(p, data)
            return True
        active = sum(_finite_number(lo.get("amount", 0.0), "lease.amount",
                                    positive=True) for lo in leases)
        granted = active + amount <= ceiling + 1e-9
        if granted:
            leases.append({"amount": amount, "ts": now, "nonce": nonce})
        data[party] = leases   # persist expiry pruning even on denial
        _lease_write(p, data)
        return granted
    finally:
        _lease_unlock(authdir)


def verify_and_accept(
    offer: SettlementOffer,
    attestation_json: str,
    *,
    now: float,
) -> tuple[Optional[RoutingGrant], str]:
    """Party B's side: verify A's attestation, then accept or reject the offer.

    B does NOT trust A. B verifies:
      1. A's attestation chain is internally valid (no tampering)
      2. A's attested balance, MINUS what A has already settled (federated
         registry), covers this offer — closes double-spend across counterparties

    Returns (RoutingGrant, reason) — grant is None if rejected.
    """
    from kry.kry_attest import verify_attestation

    # Reject malformed offers BEFORE any balance math: a non-positive or non-finite
    # kry_amount otherwise passes the overclaim + double-spend checks and settles
    # "conserved" while moving value BACKWARDS (A gains, B goes negative) — a theft
    # vector. B does not trust A, so B validates the offer shape itself.
    try:
        offer_amount = _finite_number(offer.kry_amount, "offer amount", positive=True)
    except ValueError:
        return None, f"invalid offer amount {offer.kry_amount}: must be positive and finite"
    if (isinstance(offer.routing_tokens, bool)
            or not isinstance(offer.routing_tokens, int)
            or offer.routing_tokens <= 0):
        return None, f"invalid routing_tokens {offer.routing_tokens}: must be a positive int"

    valid, errs = verify_attestation(attestation_json)
    if not valid:
        return None, f"attestation invalid: {errs[:2]}"

    try:
        att = _json_loads(attestation_json)
        attested_balance = _finite_number(att.get("total_kry", 0.0), "total_kry",
                                          nonnegative=True)
    except ValueError as exc:
        return None, f"attestation invalid: {exc}"
    # Overclaim check first (clearest rejection): offer exceeds attested balance.
    if attested_balance < offer_amount:
        return None, (f"insufficient attested balance: "
                      f"{attested_balance:.0f} < {offer_amount:.0f}")
    # Double-spend guard (under lock). Counts BOTH committed settlements (the registry)
    # and this process's accepted-but-not-yet-settled reservations, so two concurrent
    # accepts for the same party cannot both pass before either settles (the in-process
    # verify->settle TOCTOU). On success it reserves the amount; settle() clears it.
    from kry._locks import cross_process_lock
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _REGISTRY_LOCK, cross_process_lock(_REGISTRY_PATH):
        registry = _load_registry()
        if registry.get("__tampered__"):
            return None, "settlement registry tampered — failing closed (HOLE E)"
        already_settled = registry.get(offer.from_party, 0.0)
        pending = [r for r in _PENDING_RESERVATIONS.get(offer.from_party, [])
                   if now - r["ts"] < _LEASE_TTL and r["offer_id"] != offer.offer_id]
        reserved = sum(r["amount"] for r in pending)
        available = attested_balance - already_settled - reserved
        if available < offer_amount:
            _PENDING_RESERVATIONS[offer.from_party] = pending  # persist the TTL pruning
            return None, (f"double-spend guard: attested {attested_balance:.0f} − "
                          f"already-settled {already_settled:.0f} − in-flight {reserved:.0f} "
                          f"= {available:.0f} < offered {offer_amount:.0f}")
        pending.append({"offer_id": offer.offer_id, "amount": offer_amount, "ts": now})
        _PENDING_RESERVATIONS[offer.from_party] = pending

    # HOLE D fix: the registry guard above is per-node. When a shared lease
    # authority is configured, RESERVE the amount there too so two nodes cannot
    # both accept against the same attested balance in the real-time window before
    # their registries merge. Default-off (no KRY_SETTLE_LEASE_DIR → unchanged).
    authdir = _lease_dir()
    if authdir is not None:
        granted = False
        try:
            granted = _acquire_lease(
                offer.from_party, offer_amount, attested_balance,
                nonce=offer.offer_id, now=now, authdir=authdir,
            )
        finally:
            # On DENIAL *or* an exception from the lease layer, drop the in-process reservation we
            # appended above — leaving it reduces this party's available balance by offer_amount
            # until _LEASE_TTL (up to an hour), wrongly denying later legitimate offers for a call
            # that issued no grant. (finally covers the exception path the prior fix missed.)
            if not granted:
                with _REGISTRY_LOCK:
                    _PENDING_RESERVATIONS[offer.from_party] = [
                        r for r in _PENDING_RESERVATIONS.get(offer.from_party, [])
                        if r["offer_id"] != offer.offer_id
                    ]
        if not granted:
            return None, (f"cross-node lease denied (HOLE D): {offer.from_party} "
                          f"cumulative active leases + {offer_amount:.0f} exceed "
                          f"attested {attested_balance:.0f}")

    gid = hashlib.sha256(f"{offer.offer_id}:{offer.to_party}:{now}".encode()).hexdigest()[:12]
    grant = RoutingGrant(
        grant_id=gid, offer_id=offer.offer_id, granted_by=offer.to_party,
        routing_tokens=offer.routing_tokens, accepted_kry=offer_amount, ts=now,
        attested_balance=attested_balance)
    logger.info("settlement: %s accepted offer %s (%.0f KRY for %d tokens)",
                offer.to_party, offer.offer_id, offer_amount, offer.routing_tokens)
    return grant, "accepted"


def settle(
    offer: SettlementOffer,
    grant: RoutingGrant,
    *,
    debit_a_fn,          # callable(kry) -> amount the debit REPORTS moving (A's spend side)
    receiver: ReceiverLedger,
    a_balance_before: float,
    a_balance_after_fn=None,   # callable() -> A's REAL balance after the debit (independent read)
) -> SettlementReceipt:
    """Execute the settlement: debit A, credit B, check conservation.

    debit_a_fn debits A's real KRY ledger and returns the amount it REPORTS moving;
    receiver is B's ReceiverLedger, credited by exactly that amount.

    Conservation honesty: if a_balance_after_fn is given, A's real post-debit balance
    is read INDEPENDENTLY and conservation is checked against the real ledger movement
    (conservation_basis="measured") — this catches a debit_fn that reports moving X
    while the ledger moved something else (the previously tautological check). Without
    it, conservation only confirms the debit reported the agreed grant amount
    (conservation_basis="self_asserted"); the receipt LABELS which, so a stranger never
    reads an unverified check as a guarantee.
    """
    amount = _finite_number(grant.accepted_kry, "accepted_kry", positive=True)
    a_balance_before = _finite_number(a_balance_before, "a_balance_before",
                                      nonnegative=True)
    b_before = _finite_number(receiver.received_kry, "b_received_before",
                              nonnegative=True)
    # F4 (transactional ordering): COMMIT TO THE REGISTRY FIRST — ceiling check + persist the
    # obligation + clear the in-process reservation, under the lock, BEFORE any real KRY moves. The
    # previous order debited A (debit_a_fn) and only THEN wrote the registry, so a ceiling rejection
    # OR a registry-write failure left A debited with B never credited — value destroyed. Reserving
    # the agreed `amount` first makes settlement transactional: if this block raises, NOTHING was
    # debited. (Recording the agreed amount, not the post-hoc reported `debited`, is also strictly
    # safer against a lying debit_fn that under-reports to dodge its ceiling.)
    from kry._locks import cross_process_lock
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _REGISTRY_LOCK, cross_process_lock(_REGISTRY_PATH):
        # accept-time reservations are PER-PROCESS (in-memory), so two processes can both pass the
        # accept guard for one attested balance. The AUTHORITATIVE double-spend check is HERE, at
        # commit, against the COMMITTED registry total under the cross-process lock — the second
        # committer fails closed BEFORE debiting. (A directly-built grant carries attested_balance=-1
        # and is skipped.) Registries are per-node and don't merge in real time, so a cross-node
        # lease (not released here) holds A's spent amount until its TTL — conservative by design.
        if grant.attested_balance >= 0:
            committed = _load_registry()
            if committed.get("__tampered__"):
                raise SettlementPersistenceError("settlement registry tampered — failing closed")
            already = committed.get(offer.from_party, 0.0)
            if already + amount > grant.attested_balance + 1e-9:
                raise SettlementPersistenceError(
                    f"double-spend at commit: {offer.from_party} settled {already:.0f}+{amount:.0f} "
                    f"> attested {grant.attested_balance:.0f} — failing closed")
        _record_settled(offer.from_party, amount, grant.grant_id)
        _PENDING_RESERVATIONS[offer.from_party] = [
            r for r in _PENDING_RESERVATIONS.get(offer.from_party, [])
            if r["offer_id"] != offer.offer_id
        ]

    # Registry committed and the obligation recorded — NOW move real KRY: debit A, then credit B.
    debited = _finite_number(debit_a_fn(amount), "debited_kry", nonnegative=True)
    b_after = b_before + debited

    if a_balance_after_fn is not None:
        # Independent observation of A's ledger — the only thing that catches a lying debit.
        a_after = _finite_number(a_balance_after_fn(), "a_balance_after", nonnegative=True)
        conserved = abs((a_balance_before - a_after) - debited) < 1e-9  # A's REAL move == B's credit
        conservation_basis = "measured"
    else:
        # No independent read: B is credited exactly what the debit REPORTS moving, so the
        # invariant holds relative to that report — but it is NOT verified against A's real
        # ledger. LABEL it self_asserted so a consumer never reads conserved as a guarantee.
        a_after = a_balance_before - debited
        conserved = True
        conservation_basis = "self_asserted"

    rid = hashlib.sha256(f"{offer.offer_id}:{grant.grant_id}:{debited}".encode()).hexdigest()[:12]
    receipt = SettlementReceipt(
        receipt_id=rid, offer=asdict(offer), grant=asdict(grant),
        a_balance_before=a_balance_before, a_balance_after=a_after,
        b_received_before=b_before, b_received_after=b_after,
        conserved=conserved,
        conservation_basis=conservation_basis,
        receipt_hash="",
    )
    receipt.receipt_hash = hashlib.sha256(
        _json_dumps(asdict(receipt), sort_keys=True).encode()
    ).hexdigest()
    receiver.received_kry = b_after                 # B gains exactly `debited`
    receiver.routing_sold += grant.routing_tokens
    receiver.settlements.append(receipt.receipt_id)
    if not conserved:
        logger.error("SETTLEMENT CONSERVATION VIOLATED — receipt %s", rid)
    return receipt


def verify_conservation(receipt: SettlementReceipt) -> bool:
    """An auditor's check: confirm the settlement moved KRY without creating it."""
    a_lost = receipt.a_balance_before - receipt.a_balance_after
    b_gained = receipt.b_received_after - receipt.b_received_before
    return abs(a_lost - b_gained) < 1e-9 and receipt.conserved
