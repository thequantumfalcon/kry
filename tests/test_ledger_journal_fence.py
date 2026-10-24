"""The journal path is isolated from legacy writers and fails closed on interference."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kry import kry_mint as km, kry_token as kt


def journal():
    return kt._LEDGER_PATH.with_name("kry_ledger.v2.json")


def activate():
    assert km.mint("cache_hit", 300, evidence="fence seed") is not None
    legacy = kt._LEDGER_PATH.read_bytes()
    assert kt.debit_kry(100, op_id="first") == 100
    return legacy, kt._LEDGER_PATH.read_bytes(), journal().read_bytes()


def assert_blocked():
    with pytest.raises(kt.LedgerPersistenceError):
        kt.ledger_operation("first")
    with pytest.raises(kt.LedgerPersistenceError):
        kt.debit_kry(100, op_id="first")
    assert kt.spend("paid", 10) == 0
    assert kt.earn(10, "cache_hit") == 0
    assert km.mint("cache_hit", 10, evidence="blocked receipt") is None
    assert not km.reconcile_ledger_from_chain()["reconciled"]
    with pytest.raises(ValueError):
        kt.get_ledger().save()


def test_first_keyed_debit_separates_paths_and_retains_original():
    legacy, marker, encoded = activate()
    raw = json.loads(encoded)
    assert raw["accounting"]["total_spent"] == 100
    assert raw["accounting"]["balance"] == 200
    assert raw["ops"]["first"]["amount"] == 100
    assert marker != legacy and marker != encoded
    assert any(p.read_bytes() == legacy for p in kt._LEDGER_PATH.parent.iterdir()
               if p.is_file() and p not in (kt._LEDGER_PATH, journal()))
    assert kt.debit_kry(100, op_id="first") == 100
    assert journal().read_bytes() == encoded
    assert kt._LEDGER_PATH.read_bytes() == marker


@pytest.mark.parametrize("change", ["missing", "legacy", "malformed", "empty", "duplicate"])
@pytest.mark.parametrize("restart", [False, True])
def test_marker_interference_blocks_all_accounting(change, restart):
    legacy, marker, encoded = activate()
    if change == "missing":
        kt._LEDGER_PATH.unlink()
    else:
        altered = {"legacy": legacy, "malformed": b"{broken", "empty": b"",
                   "duplicate": marker + marker}[change]
        kt._LEDGER_PATH.write_bytes(altered)
    if restart:
        kt._ledger_instance = None
    assert_blocked()
    with pytest.raises((ValueError, OSError)):
        kt.acknowledge_ledger_recovery(reason="marker cannot be repaired implicitly")
    assert journal().read_bytes() == encoded
    if change == "missing":
        assert not kt._LEDGER_PATH.exists()
    else:
        assert kt._LEDGER_PATH.read_bytes() == altered


@pytest.mark.parametrize("restart", [False, True])
def test_missing_journal_is_not_reinitialized(restart):
    _, marker, _ = activate()
    journal().unlink()
    if restart:
        kt._ledger_instance = None
    assert_blocked()
    with pytest.raises((ValueError, OSError)):
        kt.acknowledge_ledger_recovery(reason="missing journal is not zero history")
    assert not journal().exists()
    assert kt._LEDGER_PATH.read_bytes() == marker


def test_legacy_snapshot_at_journal_path_cannot_erase_operation_history():
    legacy, marker, _ = activate()
    journal().write_bytes(legacy)
    with pytest.raises(ValueError):
        kt.acknowledge_ledger_recovery(reason="flat snapshot lacks journal history")
    assert journal().read_bytes() == legacy
    assert_blocked()
    assert kt._LEDGER_PATH.read_bytes() == marker


def test_corrupt_journal_does_not_quarantine_marker():
    _, marker, _ = activate()
    journal().write_bytes(b"{broken journal")
    kt._ledger_instance = None
    assert_blocked()
    assert kt._LEDGER_PATH.read_bytes() == marker
    assert any(p.read_bytes() == b"{broken journal" for p in journal().parent.glob(
        journal().name + ".corrupt*"))


def test_restored_marker_alone_does_not_clear_old_writer_incident():
    _, marker, encoded = activate()
    incident = kt._LEDGER_PATH.with_name(kt._LEDGER_PATH.name + ".corrupt")
    kt._LEDGER_PATH.rename(incident)
    kt._LEDGER_PATH.write_bytes(marker)
    assert_blocked()
    assert journal().read_bytes() == encoded
    kt.acknowledge_ledger_recovery(reason="synthetic complete history and old-writer incident reviewed")
    assert incident.read_bytes() == marker
    assert kt.debit_kry(100, op_id="first") == 100
    assert json.loads(journal().read_bytes())["accounting"]["total_spent"] == 100


def test_marker_is_checked_under_legacy_lock(monkeypatch):
    from contextlib import contextmanager
    activate()
    original = kt._cross_process_lock
    held = False
    paths = []
    read_bytes = Path.read_bytes

    @contextmanager
    def locked(path):
        nonlocal held
        paths.append(path)
        with original(path):
            held = True
            try:
                yield
            finally:
                held = False

    def read(path):
        if path == kt._LEDGER_PATH:
            assert held, "tombstone validation must share the legacy writer lock"
        return read_bytes(path)

    monkeypatch.setattr(kt, "_cross_process_lock", locked)
    monkeypatch.setattr(Path, "read_bytes", read)
    assert kt.ledger_operation("first")["amount"] == 100
    assert kt.debit_kry(100, op_id="first") == 100
    assert kt.earn(1, "cache_hit") == 1
    assert paths and all(p == kt._LEDGER_PATH for p in paths)


def test_retained_migration_backup_blocks_reset_without_mint_log():
    assert kt.earn(300, "cache_hit") == 300
    legacy = kt._LEDGER_PATH.read_bytes()
    assert kt.debit_kry(100, op_id="first") == 100
    assert not km._MINT_LOG_PATH.exists()
    assert kt._LEDGER_PATH.with_name("kry_ledger.json.pre-v2").read_bytes() == legacy
    journal().unlink()
    kt._LEDGER_PATH.unlink()
    kt._ledger_instance = None
    assert_blocked()
    assert not journal().exists() and not kt._LEDGER_PATH.exists()
    assert kt._LEDGER_PATH.with_name("kry_ledger.json.pre-v2").read_bytes() == legacy


def test_earn_and_reconcile_leave_marker_and_migration_backup_unchanged():
    legacy, marker, _ = activate()
    assert km.mint("cache_hit", 10, evidence="after activation") is not None
    assert km.reconcile_ledger_from_chain()["reconciled"]
    assert kt._LEDGER_PATH.read_bytes() == marker
    assert kt._LEDGER_PATH.with_name("kry_ledger.json.pre-v2").read_bytes() == legacy
    raw = json.loads(journal().read_bytes())
    assert raw["accounting"]["total_spent"] == 100
    assert raw["accounting"]["total_earned"] == 310
    assert raw["ops"]["first"]["amount"] == 100


MIGRATION_BOUNDARIES = [
    "payload_temp", "payload_fsync", "backup_open", "backup_fsync",
    "backup_dir_before", "backup_dir_after", "intent_open", "intent_fsync",
    "intent_dir_before", "intent_dir_after", "journal_replace_before",
    "journal_replace_after", "journal_dir_before", "journal_dir_after",
    "marker_temp", "marker_fsync", "marker_replace_before", "marker_replace_after",
    "marker_dir_before", "marker_dir_after", "adopt_before", "adopt_after",
    "unlink_before", "unlink_after",
]


def migration_fault(patch, boundary, error):
    import os
    import tempfile
    create, fdopen, sync = tempfile.mkstemp, os.fdopen, os.fsync
    opening, replace, unlink = Path.open, os.replace, Path.unlink
    directory, adopt = kt._sync_ledger_directory, kt.KRYLedger._adopt
    fired = False
    creates = directories = 0
    in_directory = False
    roles = {}

    def stop(stage):
        nonlocal fired
        if stage == boundary and not fired:
            fired = True
            raise error

    def mkstemp(*args, **kwargs):
        nonlocal creates
        creates += 1
        stop("payload_temp" if creates == 1 else "marker_temp")
        return create(*args, **kwargs)

    def fd_open(fd, mode, *args, **kwargs):
        roles[fd] = "marker" if mode == "wb" else "payload"
        return fdopen(fd, mode, *args, **kwargs)

    def path_open(path, *args, **kwargs):
        role = "backup" if path.name.endswith(".pre-v2") else (
            "intent" if ".pending." in path.name else None)
        if role and args and args[0] in ("x", "xb"):
            stop(role + "_open")
            result = opening(path, *args, **kwargs)
            roles[result.fileno()] = role
            return result
        return opening(path, *args, **kwargs)

    def fsync(fd):
        if not in_directory and fd in roles:
            stop(roles[fd] + "_fsync")
        return sync(fd)

    def sync_directory():
        nonlocal directories, in_directory
        directories += 1
        role = ["backup", "intent", "journal", "marker"][directories - 1]
        stop(role + "_dir_before")
        in_directory = True
        try:
            directory()
        finally:
            in_directory = False
        stop(role + "_dir_after")

    def replacing(source, target):
        role = "journal" if Path(target) == journal() else "marker"
        stop(role + "_replace_before")
        replace(source, target)
        stop(role + "_replace_after")

    def adopting(self, raw):
        if "first" in raw.get("ops", {}):
            stop("adopt_before")
        adopt(self, raw)
        if "first" in raw.get("ops", {}):
            stop("adopt_after")

    def unlinking(path, *args, **kwargs):
        if ".pending." in path.name:
            stop("unlink_before")
        result = unlink(path, *args, **kwargs)
        if ".pending." in path.name:
            stop("unlink_after")
        return result

    patch.setattr(tempfile, "mkstemp", mkstemp)
    patch.setattr(os, "fdopen", fd_open)
    patch.setattr(Path, "open", path_open)
    patch.setattr(os, "fsync", fsync)
    patch.setattr(kt, "_sync_ledger_directory", sync_directory)
    patch.setattr(os, "replace", replacing)
    patch.setattr(kt.KRYLedger, "_adopt", adopting)
    patch.setattr(Path, "unlink", unlinking)
    return lambda: fired


@pytest.mark.parametrize("boundary", MIGRATION_BOUNDARIES)
@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt, asyncio.CancelledError])
def test_activation_fault_matrix(monkeypatch, boundary, error_type):
    assert kt.earn(300, "cache_hit") == 300
    legacy = kt._LEDGER_PATH.read_bytes()
    error = error_type(boundary)
    durable_boundaries = ["adopt_before", "adopt_after", "unlink_before", "unlink_after"]
    with monkeypatch.context() as patch:
        fired = migration_fault(patch, boundary, error)
        try:
            result = kt.debit_kry(100, op_id="first")
        except BaseException as exc:
            if error_type is OSError:
                assert isinstance(exc, kt.LedgerPersistenceError)
            else:
                assert exc is error
        else:
            assert error_type is OSError and boundary in durable_boundaries
            assert result == 100
        assert fired(), boundary
    kt._ledger_instance = None
    if boundary in ["payload_temp", "payload_fsync", "backup_open"]:
        assert kt._LEDGER_PATH.read_bytes() == legacy
        assert not journal().exists() and not kt._ledger_incidents()
        assert not kt._LEDGER_PATH.with_name("kry_ledger.json.pre-v2").exists()
        assert kt.debit_kry(100, op_id="first") == 100
    elif not journal().exists():
        assert kt._LEDGER_PATH.read_bytes() == legacy
        assert_blocked()
        # The surviving pre-migration file is not an automatic recovery basis.
        with pytest.raises(ValueError):
            kt.acknowledge_ledger_recovery(reason="no complete journal restored")
        return
    else:
        raw = json.loads(journal().read_bytes())
        assert raw["accounting"]["total_spent"] == 100
        assert raw["ops"]["first"]["amount"] == 100
        if boundary not in durable_boundaries or kt._ledger_incidents():
            assert_blocked()
            # In this constructed case the full committed record is retained.
            # Restore only its known marker; acknowledgement still binds incidents.
            kt._LEDGER_PATH.write_bytes(kt._ledger_tombstone())
            kt.acknowledge_ledger_recovery(reason="synthetic retained debit and exact marker reviewed")
    assert kt.debit_kry(100, op_id="first") == 100
    assert json.loads(journal().read_bytes())["accounting"]["total_spent"] == 100
    assert kt._LEDGER_PATH.with_name("kry_ledger.json.pre-v2").read_bytes() == legacy


def test_transient_marker_read_error_preserves_both_files(monkeypatch):
    _, marker, encoded = activate()
    original = Path.read_bytes

    def unreadable(path):
        if path == kt._LEDGER_PATH:
            raise OSError("synthetic marker read failure")
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", unreadable)
        with pytest.raises(kt.LedgerPersistenceError):
            kt.ledger_operation("first")
        with pytest.raises(kt.LedgerPersistenceError):
            kt.debit_kry(100, op_id="first")
    assert kt._LEDGER_PATH.read_bytes() == marker
    assert journal().read_bytes() == encoded
    assert not kt._ledger_incidents()
    assert kt.debit_kry(100, op_id="first") == 100


def test_unfenced_prototype_journal_requires_reviewed_relocation():
    # A valid prototype envelope at the old path is not an activated new directory.
    ledger = kt.get_ledger()
    ledger.balance = ledger.total_earned = 100
    raw = {"ledger_format": 2, "accounting": ledger._operation_snapshot(), "ops": {}}
    encoded = json.dumps(raw).encode()
    kt._LEDGER_PATH.write_bytes(encoded)
    kt._ledger_instance = None
    with pytest.raises(ValueError, match="relocation"):
        kt.ledger_operation("missing")
    with pytest.raises(ValueError, match="relocation"):
        kt.acknowledge_ledger_recovery(reason="unfenced prototype is not an activated journal")
    assert kt._LEDGER_PATH.read_bytes() == encoded
    assert not journal().exists()


def test_direct_save_retains_recovery_block_after_marker_interference():
    _, marker, encoded = activate()
    ledger = kt.get_ledger()
    kt._LEDGER_PATH.write_bytes(b"changed legacy path")
    with pytest.raises(ValueError):
        ledger.save()
    kt._LEDGER_PATH.write_bytes(marker)
    with pytest.raises(ValueError):
        ledger.save()
    assert journal().read_bytes() == encoded
    kt.acknowledge_ledger_recovery(reason="synthetic marker interference and complete journal reviewed")
    assert kt.debit_kry(100, op_id="first") == 100
    assert json.loads(journal().read_bytes())["accounting"]["total_spent"] == 100
