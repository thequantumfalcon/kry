"""KRY pending displacements — defer a displacement mint until the served output
is CONFIRMED used downstream (S1: true downstream-acceptance gating).

The chained mint ledger (kry_mint) is the veracity surface: anything minted there
counts toward veracity_floor immediately. A displacement served by a free tier is
only an HONEST saving if its output was actually accepted — a fluent-but-wrong
free answer the caller discards is not a saving, and minting it lifts the floor on
unaccepted work. This module holds such displacements in a side store (NOT the
chained ledger) until a downstream consumer confirms the output was used; only
then does confirm() call the real mint(). Unconfirmed pendings expire and never
mint — the floor never rises on work that was not accepted.

Opt-in on the producing side (the bridge gates this behind KRY_DISPLACEMENT_DEFER).
The side store mirrors kry_mint's conventions: a cross-process file lock + atomic
tempfile→os.replace writes, so a long-running bridge and an ad-hoc consumer can
record/confirm concurrently without corruption.
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
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from kry.kry_mint import MintReceipt, mint


logger = logging.getLogger("kry.pending")


class PendingStoreCorrupt(RuntimeError):
    """The pending store is present but unparseable. Raised instead of silently
    resetting to ``{}`` — a fresh store would erase confirm()'s write-ahead
    idempotency state and open a re-mint window."""


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _kry_data_dir() -> Path:
    """Portable data dir. Set KRY_DATA_DIR to relocate; defaults to ./kry_data."""
    d = Path(os.environ.get("KRY_DATA_DIR", "kry_data")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


_PENDING_PATH = _kry_data_dir() / "kry_pending.json"
_LOCK_PATH = _kry_data_dir() / "kry_pending.lock"
_THREAD_LOCK = threading.Lock()

# A displacement not confirmed within this window is assumed unaccepted and
# expires (no mint). Generous enough for an async downstream consumer to act.
_DEFAULT_TTL = float(os.environ.get("KRY_DISPLACEMENT_PENDING_TTL", "900"))  # 15 min

try:
    import fcntl as _fcntl

    def _flock_ex(f) -> None:
        _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)

    def _flock_un(f) -> None:
        _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)
except ImportError:  # pragma: no cover - non-POSIX fallback
    def _flock_ex(f) -> None:
        pass

    def _flock_un(f) -> None:
        pass


@contextmanager
def _locked():
    """Serialize read-modify-write across threads AND processes — the bridge
    records pendings while a separate consumer process confirms them."""
    with _THREAD_LOCK:
        _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        f = open(_LOCK_PATH, "w", encoding="utf-8")
        try:
            _flock_ex(f)
            yield
        finally:
            _flock_un(f)
            f.close()


def _load() -> dict:
    if not _PENDING_PATH.exists():
        return {}
    try:
        with open(_PENDING_PATH, encoding="utf-8") as f:
            # HOLE #15: reject NaN/Infinity (mirrors kry_mint). A NaN `ttl` makes the expiry check
            # `now > created_ts + NaN` always False, so the pending never expires and stays mintable
            # forever. A poisoned/externally-written store is treated as corrupt below.
            return json.loads(f.read(), parse_constant=_reject_json_constant)
    except FileNotFoundError:
        return {}  # vanished between exists() and open() — genuinely absent
    except (json.JSONDecodeError, ValueError) as e:
        # M5: a PRESENT-but-corrupt store must NOT silently reset to {} — that erases the
        # write-ahead confirm() idempotency state and opens a re-mint window (a previously
        # confirmed displacement could be recorded and confirmed a second time). Quarantine
        # the bad file for forensics, log loudly, and FAIL CLOSED. This mirrors
        # kry_mint._decayed_tokens (also fails closed on a present-but-unparseable file);
        # contrast kry_token's ledger, which may reset to a fresh balance because that is the
        # honest UNDERcount direction — here a fresh store is the OVERcount risk.
        quarantine = _PENDING_PATH.with_name(_PENDING_PATH.name + ".corrupt")
        try:
            os.replace(_PENDING_PATH, quarantine)
        except OSError:
            quarantine = None
        logger.error("kry_pending: corrupt store %s quarantined to %s (%s); refusing to proceed",
                     _PENDING_PATH, quarantine, e)
        raise PendingStoreCorrupt(
            f"pending store {_PENDING_PATH} is present but unparseable: {e}") from e


def _store(data: dict) -> None:
    _PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(_PENDING_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            # HOLE #15 (store/load symmetry): the reader rejects NaN/Infinity, so the writer must too —
            # else a single non-finite value (e.g. an inf tokens_saved in mint_kwargs) serializes as
            # "Infinity" and the NEXT _load() rejects the WHOLE file, destroying every co-resident
            # pending. allow_nan=False raises HERE instead, BEFORE os.replace, so the atomic write
            # leaves the prior valid store intact and the bad value surfaces to the caller.
            json.dump(data, f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _PENDING_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _new_id(mint_kwargs: dict) -> str:
    seed = (f"{time.time_ns()}:{os.getpid()}:"
            f"{mint_kwargs.get('evidence') or mint_kwargs.get('detail') or ''}")
    return "PEND-" + hashlib.sha256(seed.encode()).hexdigest()[:16]


# ── Public API ───────────────────────────────────────────────────────────────

def record_pending(mint_kwargs: dict, ttl: float | None = None) -> str:
    """Record a displacement candidate WITHOUT minting. Returns a pending_id the
    consumer later passes to confirm(). `mint_kwargs` are the exact keyword args
    forwarded to kry_mint.mint() on confirmation (event_type + tokens_saved
    required)."""
    if "event_type" not in mint_kwargs or "tokens_saved" not in mint_kwargs:
        raise ValueError("mint_kwargs needs at least event_type and tokens_saved")
    # HOLE #15: reject a non-finite/negative ttl at the boundary — a NaN ttl would make the pending
    # never expire (now > created_ts + NaN is always False) and stay mintable indefinitely.
    t = float(ttl if ttl is not None else _DEFAULT_TTL)
    if not math.isfinite(t) or t < 0:
        raise ValueError("ttl must be a finite, non-negative number")
    pid = _new_id(mint_kwargs)
    with _locked():
        data = _load()
        data[pid] = {
            "created_ts": time.time(),
            "ttl": t,
            "status": "pending",
            "mint_kwargs": dict(mint_kwargs),
        }
        _store(data)
    return pid


def confirm(pending_id: str) -> Optional[MintReceipt]:
    """Confirm a pending displacement was ACCEPTED downstream → mint it for real.

    Idempotent: a second confirm of the same id mints nothing (returns None). The
    status flips to "confirmed" under the lock BEFORE the mint, so a concurrent
    confirm sees the flip and bails — the mint happens at most once. Returns the
    MintReceipt, or None if the id is unknown / expired / rejected /
    already-confirmed (or if mint() itself declines, e.g. decayed replay — the
    saving is then dropped, never double-counted: undercount is the honest
    direction for a veracity floor)."""
    with _locked():
        data = _load()
        rec = data.get(pending_id)
        if not rec or rec["status"] != "pending":
            return None
        if time.time() > rec["created_ts"] + rec["ttl"]:
            rec["status"] = "expired"
            _store(data)
            return None
        # HOLE #14: WRITE-AHEAD the flip — persist "confirmed" to disk BEFORE minting. The old order
        # (flip in memory → mint → _store) meant a crash between a landed mint and the persist left the
        # on-disk status "pending", so a retry after restart minted the SAME displacement a second time
        # (double-count). Persisting first makes a post-mint crash drop at most one saving (the honest
        # undercount direction for a veracity floor), never a double.
        rec["status"] = "confirmed"
        _store(data)
        receipt = mint(**rec["mint_kwargs"])
        rec["receipt_id"] = receipt.receipt_id if receipt else None
        _store(data)
        return receipt


def reject(pending_id: str) -> bool:
    """Mark a pending displacement as NOT accepted — it will never mint. Returns
    True if a pending entry was rejected, False otherwise."""
    with _locked():
        data = _load()
        rec = data.get(pending_id)
        if not rec or rec["status"] != "pending":
            return False
        rec["status"] = "rejected"
        _store(data)
        return True


def sweep_expired(now: float | None = None) -> int:
    """Mark every past-TTL pending as expired; returns how many were swept. A
    monitor calls this so unaccepted pendings cannot mint later — the floor never
    rises on work that was never confirmed."""
    now = time.time() if now is None else now
    swept = 0
    with _locked():
        data = _load()
        for rec in data.values():
            if rec["status"] == "pending" and now > rec["created_ts"] + rec["ttl"]:
                rec["status"] = "expired"
                swept += 1
        if swept:
            _store(data)
    return swept


def stats() -> dict:
    """Counts by status — for the closed loop (a monitor consumes this; decision
    #1: no telemetry without a consumer)."""
    out: dict = {}
    for rec in _load().values():
        out[rec["status"]] = out.get(rec["status"], 0) + 1
    return out
