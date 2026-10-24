"""Activation-specific recovery preserves reviewed accounting and retained history."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from kry import kry_token as kt
from kry import _journal_activation as ja
from journal_activation_faults import ACTIVATION_POINTS, ABANDON_POINTS, COMPLETE_POINTS

ROOT = Path(__file__).resolve().parents[1]
OP = "0123456789abcdef0123456789abcdef"
REASON = "Synthetic review: retained accounting omits no committed journal debit"


def crash(operation, point):
    code = '''
import os, sys
import pytest
from journal_activation_faults import install
from kry import kry_token as kt
def stop(point):
    if point == sys.argv[2]:
        print(point, flush=True)
        os._exit(77)
with pytest.MonkeyPatch.context() as patch:
    install(patch, stop)
    if sys.argv[1] == "activate":
        kt.debit_kry(100, op_id=sys.argv[3])
    elif sys.argv[1] == "abandon":
        kt.abandon_incomplete_journal_activation(reason=sys.argv[4])
    elif sys.argv[1] == "complete":
        kt.complete_journal_activation()
'''
    result = subprocess.run([sys.executable, "-B", "-c", code, operation, point, OP, REASON],
                            capture_output=True, text=True, timeout=30, check=False,
                            env={**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "tests"))),
                                 "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent), "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 77, (point, result.stdout, result.stderr)
    assert result.stdout.strip() == point
    kt._ledger_instance = None


def disk():
    path = kt._journal_path() if kt._journal_path().exists() else kt._LEDGER_PATH
    raw = json.loads(path.read_bytes())
    return raw.get("accounting", raw)


def file_bytes():
    return {p.name: p.read_bytes() for p in kt._LEDGER_PATH.parent.iterdir() if p.is_file()}


def blocked():
    with pytest.raises(ValueError, match="activation"):
        kt.acknowledge_ledger_recovery(reason="generic recovery must not consume activation")
    assert kt.spend("paid", 10) == 0
    assert kt.earn(10, "cache_hit") == 0
    with pytest.raises(kt.LedgerPersistenceError):
        kt.debit_kry(100, op_id=OP)


def exactly_once(balance=200):
    assert kt.debit_kry(100, op_id=OP) == 100
    before = file_bytes()
    assert kt.debit_kry(100, op_id=OP) == 100
    assert file_bytes() == before
    assert kt.ledger_operation(OP)["amount"] == 100
    assert disk()["balance"] == balance
    assert disk()["total_spent"] == 300 - balance


def test_active_activation_intent_cannot_be_generically_acknowledged():
    assert kt.earn(300, "cache_hit") == 300
    original = kt._LEDGER_PATH.read_bytes()
    attempt = "a" * 32
    intent = kt._LEDGER_PATH.with_name(kt._LEDGER_PATH.name + ".pending.activation-" + attempt)
    intent.write_text(json.dumps({"schema": "activation/1", "activation": True,
                                 "attempt_id": attempt, "legacy_sha256": hashlib.sha256(original).hexdigest(),
                                 "journal_sha256": "b" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="activation"):
        kt.acknowledge_ledger_recovery(reason="generic acknowledgement is not activation recovery")
    assert kt._LEDGER_PATH.read_bytes() == original
    assert kt.spend("paid", 10) == 0


def test_first_activation_barriers_publish_intent_before_backup(monkeypatch):
    assert kt.earn(300, "cache_hit") == 300
    original = kt._LEDGER_PATH.read_bytes()
    fsync = os.fsync
    calls = 0

    def interrupt(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("constructed second activation durability failure")
        fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", interrupt)
        with pytest.raises(kt.LedgerPersistenceError):
            kt.debit_kry(100, op_id="a" * 32)
    assert calls >= 2
    assert kt._LEDGER_PATH.read_bytes() == original
    intents = list(kt._LEDGER_PATH.parent.glob(kt._LEDGER_PATH.name + ".pending.activation-*"))
    assert len(intents) == 1
    assert json.loads(intents[0].read_text())["legacy_sha256"] == hashlib.sha256(original).hexdigest()
    backup = kt._legacy_backup_path()
    assert not backup.exists() or backup.read_bytes() == original


@pytest.mark.parametrize("point", ACTIVATION_POINTS)
def test_activation_abrupt_exit_and_recovery(point):
    assert kt.earn(300, "cache_hit") == 300
    original = disk()
    crash("activate", point)
    protocol = ja._Activation()
    if protocol.journal.exists():
        before = file_bytes()
        with pytest.raises(ValueError, match="completion evidence"):
            kt.abandon_incomplete_journal_activation(reason=REASON)
        assert file_bytes() == before
        if protocol.files(".pending.activation-*"):
            blocked()
            kt.complete_journal_activation()
    elif protocol.files(".pending.activation-*"):
        blocked()
        receipt = kt.abandon_incomplete_journal_activation(reason=REASON)
        assert receipt["basis"] == "operator_asserted"
        assert kt.abandon_incomplete_journal_activation(reason=REASON) == receipt
        assert {k: v for k, v in disk().items() if k != "recovery"} == {
            k: v for k, v in original.items() if k != "recovery"}
    else:
        assert disk() == original
    exactly_once()


@pytest.mark.parametrize("point", ABANDON_POINTS)
def test_abandonment_abrupt_exit_and_resume(point):
    assert kt.earn(300, "cache_hit") == 300
    original = disk()
    crash("activate", "backup.dir_after")
    crash("abandon", point)
    protocol = ja._Activation()
    if protocol.files(".pending.activation-*") or protocol.files(".pending.abandon-*"):
        blocked()
    receipt = kt.abandon_incomplete_journal_activation(reason=REASON)
    assert kt.abandon_incomplete_journal_activation(reason=REASON) == receipt
    assert disk()["recovery"]["incidents"] == kt._ledger_incidents()
    assert {k: v for k, v in disk().items() if k != "recovery"} == {
        k: v for k, v in original.items() if k != "recovery"}
    assert not protocol.files(".pending.*") and not protocol.backup.exists()
    exactly_once()


@pytest.mark.parametrize("point", COMPLETE_POINTS)
def test_completion_abrupt_exit_and_resume(point):
    assert kt.earn(300, "cache_hit") == 300
    crash("activate", "journal.dir_after")
    crash("complete", point)
    with pytest.raises(ValueError, match="completion evidence"):
        kt.abandon_incomplete_journal_activation(reason=REASON)
    if ja._Activation().files(".pending.activation-*"):
        kt.complete_journal_activation()
    exactly_once()


def test_existing_journal_without_activation_metadata_remains_supported():
    assert kt.earn(300, "cache_hit") == 300
    exactly_once()
    raw = json.loads(kt._journal_path().read_bytes())
    raw["accounting"]["recovery"].pop("journal_activation")
    kt._journal_path().write_text(json.dumps(raw), encoding="utf-8")
    ja._Activation().sentinel.unlink()
    kt._ledger_instance = None
    exactly_once()
    kt.acknowledge_ledger_recovery(reason="reviewed older protocol journal")
    assert "journal_activation" not in disk()["recovery"]
    assert not ja._Activation().sentinel.exists()
    exactly_once()


@pytest.mark.parametrize("change", ["missing", "partial", "symlink", "other_attempt", "duplicate"])
def test_new_completion_sentinel_cannot_be_lost_or_changed(change):
    kt.earn(300, "cache_hit")
    exactly_once()
    sentinel = ja._Activation().sentinel
    initial = sentinel.read_bytes()
    if change in ("missing", "symlink"):
        sentinel.unlink()
        if change == "symlink":
            sentinel.symlink_to(sentinel.with_name("not-present"))
    elif change == "partial":
        sentinel.write_bytes(b"{partial")
    elif change == "duplicate":
        sentinel.write_bytes(initial.replace(b'{', b'{"schema":"activation-complete/1",', 1))
    else:
        raw = json.loads(initial)
        raw["attempt_id"] = "0" * 32
        sentinel.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        kt.acknowledge_ledger_recovery(reason="completion marker must be retained")
    with pytest.raises(kt.LedgerPersistenceError):
        kt.debit_kry(100, op_id=OP)
    assert disk()["total_spent"] == 100


def test_later_writes_and_generic_recovery_preserve_activation_identity():
    kt.earn(300, "cache_hit")
    exactly_once()
    sentinel = ja._Activation().sentinel.read_bytes()
    identity = disk()["recovery"]["journal_activation"]
    assert kt.debit_kry(10, op_id="second") == 10
    kt.acknowledge_ledger_recovery(reason="later reviewed recovery preserves activation identity")
    assert disk()["recovery"]["journal_activation"] == identity
    assert ja._Activation().sentinel.read_bytes() == sentinel
    assert json.loads(sentinel)["initial_journal_sha256"] != hashlib.sha256(kt._journal_path().read_bytes()).hexdigest()
    assert kt.debit_kry(100, op_id=OP) == 100
    assert disk()["total_spent"] == 110


def test_changed_legacy_accounting_is_retained_only_before_frozen_assertion():
    kt.earn(300, "cache_hit")
    crash("activate", "backup.dir_after")
    raw = disk()
    raw["balance"], raw["total_spent"] = 290, 10
    kt._LEDGER_PATH.write_text(json.dumps(raw), encoding="utf-8")
    receipt = kt.abandon_incomplete_journal_activation(reason=REASON)
    assert receipt["legacy_changed"] is True
    assert disk()["events"] == raw["events"]
    exactly_once(balance=190)


def test_changed_accounting_after_frozen_assertion_refuses():
    kt.earn(300, "cache_hit")
    crash("activate", "backup.dir_after")
    crash("abandon", "resolution_plan.dir_after")
    raw = disk()
    raw["balance"], raw["total_spent"] = 290, 10
    kt._LEDGER_PATH.write_text(json.dumps(raw), encoding="utf-8")
    before = file_bytes()
    with pytest.raises(ValueError, match="changed after operator assertion"):
        kt.abandon_incomplete_journal_activation(reason=REASON)
    assert file_bytes() == before


@pytest.mark.parametrize("evidence", ["journal", "sentinel", "marker", "pending", "corrupt", "partial_backup",
                                     "bad_intent", "missing_legacy", "conflicting_intent", "symlink"])
def test_abandonment_refuses_incomplete_or_completion_evidence(evidence):
    kt.earn(300, "cache_hit")
    crash("activate", "backup.dir_after")
    p = ja._Activation()
    if evidence in ("journal", "sentinel"):
        getattr(p, evidence).write_bytes(b"incomplete evidence")
    elif evidence == "marker":
        p.legacy.write_bytes(kt._ledger_tombstone())
    elif evidence in ("pending", "corrupt"):
        p.journal.with_name(p.journal.name + "." + evidence + ".test").write_bytes(b"retained")
    elif evidence == "partial_backup":
        p.backup.write_bytes(b"")
    elif evidence == "bad_intent":
        p.files(".pending.activation-*")[0].write_bytes(b"[]")
    elif evidence == "missing_legacy":
        p.legacy.unlink()
    elif evidence == "conflicting_intent":
        p.legacy.with_name(p.legacy.name + ".pending.activation-second").write_bytes(b"{}")
    elif evidence == "symlink":
        p.sentinel.symlink_to(p.root / "not-present")
    before = file_bytes()
    with pytest.raises((ValueError, OSError)):
        kt.abandon_incomplete_journal_activation(reason=REASON)
    assert file_bytes() == before


@pytest.mark.parametrize("case", ["path", "reason", "timestamp", "incident", "snapshot", "field", "type", "backup"])
def test_malformed_abandonment_plan_is_not_applied(case):
    kt.earn(300, "cache_hit")
    crash("activate", "backup.dir_after")
    crash("abandon", "resolution_plan.dir_after")
    plan_path = ja._Activation().files(".pending.abandon-*")[0]
    plan = json.loads(plan_path.read_bytes())
    if case == "path":
        plan["intent"]["attempt_id"] = "../../outside"
    elif case == "reason":
        plan["reason"] = " "
    elif case == "timestamp":
        plan["ts"] = -1
    elif case == "incident":
        plan["incidents"]["../../outside"] = "a" * 64
    elif case == "snapshot":
        plan["legacy_text"] = plan["legacy_text"].replace("300.0", "400.0")
    elif case == "field":
        plan["unsupported"] = True
    elif case == "type":
        plan = []
    elif case == "backup":
        plan["backup_sha256"] = "0" * 64
    plan_path.write_bytes(ja.encoded(plan))
    before = file_bytes()
    with pytest.raises(ValueError):
        kt.abandon_incomplete_journal_activation(reason=REASON)
    assert file_bytes() == before


def test_initial_absence_is_explicit_and_zero_operation_stays_zero():
    crash("activate", "intent.dir_after")
    receipt = kt.abandon_incomplete_journal_activation(reason=REASON)
    assert receipt["original_legacy_sha256"] is None
    assert kt.debit_kry(100, op_id=OP) == 0
    assert kt.earn(300, "cache_hit") == 300
    assert kt.debit_kry(100, op_id=OP) == 0
    assert disk()["balance"] == 300 and disk()["total_spent"] == 0


def test_original_existing_accounting_cannot_be_recast_as_initial_absence():
    kt.earn(300, "cache_hit")
    crash("activate", "backup.dir_after")
    crash("abandon", "resolution_plan.dir_after")
    plan_path = ja._Activation().files(".pending.abandon-*")[0]
    plan = ja.parsed(plan_path.read_bytes())
    plan["legacy_text"] = None
    plan["observed_legacy_sha256"] = None
    plan_path.write_bytes(ja.encoded(plan))
    kt._LEDGER_PATH.unlink()
    before = file_bytes()
    with pytest.raises(ValueError, match="original absence"):
        kt.abandon_incomplete_journal_activation(reason=REASON)
    assert file_bytes() == before


def test_no_attempt_does_not_create_recovered_accounting():
    with pytest.raises(ValueError):
        kt.abandon_incomplete_journal_activation(reason=REASON)
    with pytest.raises(ValueError):
        kt.complete_journal_activation()
    assert not kt._LEDGER_PATH.exists() and not kt._journal_path().exists()


def test_activation_cleanup_read_failure_preserves_cancellation(monkeypatch):
    kt.earn(300, "cache_hit")
    publish, read = ja._Activation.publish, ja.read
    committed_intent = False
    interruption = KeyboardInterrupt("constructed cancellation")

    def cancelling(self, path, data, **kwargs):
        nonlocal committed_intent
        publish(self, path, data, **kwargs)
        if ".pending.activation-" in path.name:
            committed_intent = True
            raise interruption

    def unreadable(path):
        if committed_intent and ".pending.activation-" in path.name:
            raise OSError("constructed cleanup read failure")
        return read(path)

    with monkeypatch.context() as patch:
        patch.setattr(ja._Activation, "publish", cancelling)
        patch.setattr(ja, "read", unreadable)
        with pytest.raises(KeyboardInterrupt) as caught:
            kt.debit_kry(100, op_id=OP)
        assert caught.value is interruption
        assert kt.get_ledger()._recovery_required
    blocked()
    kt.abandon_incomplete_journal_activation(reason=REASON)
    exactly_once()


def test_two_processes_share_activation_lock_and_operation_identity():
    kt.earn(300, "cache_hit")
    command = [sys.executable, "-B", "-c",
               "from kry import kry_token as k; assert k.debit_kry(100, op_id='" + OP + "') == 100"]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent)}
    children = [subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for _ in range(2)]
    try:
        for child in children:
            output = child.communicate(timeout=30)
            assert child.returncode == 0, output
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    exactly_once()


def test_completion_rejects_intent_from_another_attempt():
    kt.earn(300, "cache_hit")
    crash("activate", "journal.dir_after")
    p = ja._Activation()
    original = p.files(".pending.activation-*")[0]
    value = json.loads(original.read_bytes())
    value["attempt_id"] = "e" * 32
    renamed = original.with_name(p.legacy.name + ".pending.activation-" + value["attempt_id"])
    renamed.write_bytes(ja.encoded(value))
    original.unlink()
    before = file_bytes()
    with pytest.raises(ValueError, match="identity"):
        kt.complete_journal_activation()
    assert file_bytes() == before


def test_activated_legacy_marker_symlink_blocks_reads_and_writes():
    kt.earn(300, "cache_hit")
    exactly_once()
    marker = kt._LEDGER_PATH.read_bytes()
    retained = kt._LEDGER_PATH.with_name("retained-marker")
    retained.write_bytes(marker)
    kt._LEDGER_PATH.unlink()
    kt._LEDGER_PATH.symlink_to(retained)
    before = kt._journal_path().read_bytes()
    with pytest.raises(kt.LedgerPersistenceError):
        kt.ledger_operation(OP)
    with pytest.raises(kt.LedgerPersistenceError):
        kt.debit_kry(100, op_id=OP)
    assert kt._journal_path().read_bytes() == before
