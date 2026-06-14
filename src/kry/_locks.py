"""Cross-process file lock — shared by the persistence layers.

The ledger, the supply-decay state, and the reputation ledger all do a read-modify-write
of a small JSON file. A threading lock only serializes THREADS; concurrent PROCESSES or
NODES sharing the file race and lose updates (a real bug the lab's Test 6 caught). This
gives a best-effort EXCLUSIVE lock across processes via POSIX flock; it is a no-op where
fcntl is unavailable (Windows), where the in-process locks still apply.

NOTE: flock over NFS is unreliable. For production multi-NODE, prefer per-node state +
reconciliation (the mint chain) over one shared mutable file. This makes single-host
multi-process correct and is the honest best-effort for a shared FS that supports flock.
"""
from __future__ import annotations

import contextlib
import time

try:
    import fcntl as _fcntl
except ImportError:               # pragma: no cover - Windows
    _fcntl = None
    try:
        import msvcrt as _msvcrt  # Windows: real mandatory byte-range locking
    except ImportError:           # pragma: no cover
        _msvcrt = None
else:
    _msvcrt = None


@contextlib.contextmanager
def cross_process_lock(path):
    """Hold an exclusive lock on `<path>.lock` for the duration of the block.

    POSIX uses flock; Windows uses msvcrt.locking (a mandatory byte-range lock) — a real
    cross-process lock there too, not a no-op (the no-op let concurrent Windows minting fork
    the chain). If neither is available the in-process locks still apply.
    """
    lockpath = str(path) + ".lock"
    if _fcntl is not None:
        lf = open(lockpath, "w", encoding="utf-8")
        try:
            _fcntl.flock(lf, _fcntl.LOCK_EX)
            yield
        finally:
            try:
                _fcntl.flock(lf, _fcntl.LOCK_UN)
            finally:
                lf.close()
        return
    if _msvcrt is not None:       # pragma: no cover - Windows
        lf = open(lockpath, "a+", encoding="utf-8")
        try:
            while True:           # spin-wait: LK_NBLCK raises immediately if another process holds it
                try:
                    lf.seek(0)
                    _msvcrt.locking(lf.fileno(), _msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            yield
        finally:
            try:
                lf.seek(0)
                _msvcrt.locking(lf.fileno(), _msvcrt.LK_UNLCK, 1)
            finally:
                lf.close()
        return
    yield                         # pragma: no cover - no locking primitive available
