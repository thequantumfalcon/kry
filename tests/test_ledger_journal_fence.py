"""The journal path is isolated from legacy writers and fails closed on interference."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kry import kry_mint as km, kry_token as kt
from journal_activation_faults import ACTIVATION_POINTS, LEGACY_POINTS


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


MIGRATION_BOUNDARIES += [point for point in ACTIVATION_POINTS if point not in LEGACY_POINTS.values()]


def migration_fault(patch, boundary, error):
    from journal_activation_faults import install
    fired = False

    def stop(stage):
        nonlocal fired
        if stage == LEGACY_POINTS.get(boundary, boundary) and not fired:
            fired = True
            raise error

    install(patch, stop)
    return lambda: fired


@pytest.mark.parametrize("boundary", MIGRATION_BOUNDARIES)
@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt, asyncio.CancelledError])
def test_activation_fault_matrix(monkeypatch, boundary, error_type):
    assert kt.earn(300, "cache_hit") == 300
    legacy = kt._LEDGER_PATH.read_bytes()
    error = error_type(boundary)
    durable_boundaries = ["adopt_before", "adopt_after", "unlink_before", "unlink_after",
                          "intent_remove.dir_before", "intent_remove.dir_after"]
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
    if not journal().exists() and not kt._ledger_incidents():
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
        if kt._ledger_incidents():
            assert_blocked()
            # In this constructed case the full committed record is retained.
            # Generic acknowledgement cannot consume an active activation attempt.
            with pytest.raises(ValueError, match="activation"):
                kt.acknowledge_ledger_recovery(reason="generic recovery is not activation completion")
            kt.complete_journal_activation()
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
