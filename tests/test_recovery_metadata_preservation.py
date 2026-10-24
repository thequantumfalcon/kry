"""Ordinary writers preserve the recovery evidence read under the ledger lock."""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from kry import _journal_activation as ja
from kry import kry_token as kt

ROOT = Path(__file__).resolve().parents[1]
OP = "a" * 32


def other(code):
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                            text=True, timeout=30,
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src"),
                                 "KRY_DATA_DIR": str(kt._LEDGER_PATH.parent)})
    assert result.returncode == 0, (result.stdout, result.stderr)


def accounting():
    path = kt._journal_path() if kt._journal_path().exists() else kt._LEDGER_PATH
    raw = json.loads(path.read_bytes())
    return raw.get("accounting", raw)


@pytest.mark.parametrize("operation", ["save", "edit_save", "edit_earn", "earn"])
@pytest.mark.parametrize("acknowledged", [False, True])
def test_stale_handle_preserves_other_process_activation(operation, acknowledged):
    assert kt.earn(300, "cache_hit") == 300
    if acknowledged:
        kt.acknowledge_ledger_recovery(reason="synthetic review before activation")
    ledger = kt.get_ledger()
    other("from kry import kry_token as k; assert k.debit_kry(100,op_id='a'*32)==100")
    identity = accounting()["recovery"]["journal_activation"]
    if operation.startswith("edit_"):
        ledger.balance += 10
        ledger.total_earned += 10
    if operation.endswith("save"):
        ledger.save()
    else:
        assert kt.earn(1, "cache_hit") == 1
    assert accounting()["recovery"]["journal_activation"] == identity
    assert ledger._recovery == accounting()["recovery"]
    assert kt.ledger_operation(OP)["amount"] == 100
    expected = 200 + (10 if operation.startswith("edit_") else 0) + (1 if operation.endswith("earn") else 0)
    assert accounting()["balance"] == expected
    assert kt.debit_kry(1, op_id="b" * 32) == 1
    kt.acknowledge_ledger_recovery(reason="synthetic complete accounting reviewed")
    assert accounting()["recovery"]["journal_activation"] == identity
    assert accounting()["balance"] == expected - 1
    assert accounting()["total_spent"] == 101
    assert kt.debit_kry(1, op_id="b" * 32) == 1
    assert accounting()["total_spent"] == 101


@pytest.mark.parametrize("journal", [False, True])
@pytest.mark.parametrize("operation", ["save", "edit_save", "edit_earn"])
def test_stale_handle_preserves_other_process_acknowledgement(journal, operation):
    assert kt.earn(300, "cache_hit") == 300
    if journal:
        assert kt.debit_kry(10, op_id=OP) == 10
    kt.acknowledge_ledger_recovery(reason="first synthetic acknowledgement")
    stale = kt.get_ledger()
    before = accounting()["recovery"]
    incident = kt._LEDGER_PATH.with_name(kt._LEDGER_PATH.name + ".pending.synthetic")
    incident.write_text("retained synthetic incident")
    other("from kry import kry_token as k; k.acknowledge_ledger_recovery(reason='new synthetic acknowledgement')")
    fresh = accounting()["recovery"]
    assert fresh["reason"] == "new synthetic acknowledgement"
    assert fresh["ts"] > before["ts"]
    assert fresh["incidents"] == kt._ledger_incidents() != before["incidents"]
    if journal:
        assert fresh["journal_activation"] == before["journal_activation"]
    if operation.startswith("edit_"):
        stale.balance += 2
        stale.total_earned += 2
    if operation.endswith("save"):
        stale.save()
    else:
        assert kt.earn(1, "cache_hit") == 1
    assert accounting()["recovery"] == fresh
    assert stale._recovery == fresh
    assert kt.earn(1, "cache_hit") == 1
    if journal:
        assert kt.debit_kry(10, op_id=OP) == 10
        assert accounting()["total_spent"] == 10


def interrupt_after_backup(monkeypatch, op_id):
    original = ja._Activation.publish

    def publish(self, path, data, *, replace=False):
        original(self, path, data, replace=replace)
        if path == self.backup:
            raise KeyboardInterrupt("synthetic interruption after backup publication")

    with monkeypatch.context() as patch:
        patch.setattr(ja._Activation, "publish", publish)
        with pytest.raises(KeyboardInterrupt, match="synthetic interruption"):
            kt.debit_kry(100, op_id=op_id)
    kt._ledger_instance = None


def test_old_abandonment_receipt_cannot_resolve_new_backup(monkeypatch):
    assert kt.earn(300, "cache_hit") == 300
    interrupt_after_backup(monkeypatch, OP)
    first = kt.abandon_incomplete_journal_activation(reason="first synthetic abandoned attempt")
    assert kt.abandon_incomplete_journal_activation(reason="idempotent repeat") == first
    interrupt_after_backup(monkeypatch, "b" * 32)
    intents = list(kt._LEDGER_PATH.parent.glob("kry_ledger.json.pending.activation-*"))
    assert len(intents) == 1
    intent_bytes = intents[0].read_bytes()
    intents[0].unlink()  # Deliberate operator-mistake control on synthetic files.
    backup = kt._legacy_backup_path().read_bytes()
    ledger = kt._LEDGER_PATH.read_bytes()
    with pytest.raises(ValueError, match="backup.*intent|intent.*backup"):
        kt.abandon_incomplete_journal_activation(reason="new attempt requires its own evidence")
    assert kt._legacy_backup_path().read_bytes() == backup
    assert kt._LEDGER_PATH.read_bytes() == ledger
    with pytest.raises(kt.LedgerPersistenceError):
        kt.debit_kry(1, op_id="c" * 32)
    intents[0].write_bytes(intent_bytes)
    second = kt.abandon_incomplete_journal_activation(reason="restored exact second intent")
    assert second["attempt_id"] != first["attempt_id"]
    assert kt.abandon_incomplete_journal_activation(reason="repeat second receipt") == second
    assert accounting()["balance"] == 300
    assert kt.debit_kry(100, op_id="b" * 32) == 100
    assert kt.debit_kry(100, op_id="b" * 32) == 100
    assert accounting()["total_spent"] == 100


@pytest.mark.parametrize("error_name", ["EPERM", "EOPNOTSUPP", "ENOTSUP"])
def test_unsupported_hard_links_explain_requirement_without_debit(monkeypatch, error_name):
    assert kt.earn(300, "cache_hit") == 300
    root = kt._LEDGER_PATH.parent
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}

    def refuse(*args, **kwargs):
        raise OSError(getattr(errno, error_name), "synthetic link denial")

    with monkeypatch.context() as patch:
        patch.setattr(ja.os, "link", refuse)
        for _ in range(2):
            with pytest.raises(kt.LedgerPersistenceError, match="hard.link"):
                kt.debit_kry(100, op_id=OP)
    assert all((root / name).read_bytes() == value for name, value in before.items())
    assert not kt._journal_path().exists()
    assert not kt._legacy_backup_path().exists()
    assert not list(root.glob("kry_ledger.json.pending.*"))
    assert accounting()["total_spent"] == 0
    assert kt.spend("gh/claude-opus-4.8", 1) > 0
    spent = accounting()["total_spent"]
    assert kt.earn(300, "cache_hit") == 300
    assert kt.debit_kry(100, op_id=OP) == 100
    assert kt.debit_kry(100, op_id=OP) == 100
    assert accounting()["total_spent"] == spent + 100
