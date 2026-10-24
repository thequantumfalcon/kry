"""Recovery acknowledgement preserves reviewed debits and reports completion."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kry import kry_mint as km, kry_token as kt


def disk():
    return json.loads(kt._LEDGER_PATH.read_text())


def restore(*, chain_earned, earned, spent):
    if chain_earned:
        receipt = km.mint("cache_hit", chain_earned, evidence="recovery-control")
        assert receipt is not None and receipt.kry_minted == chain_earned
    raw = {"balance": earned - spent, "total_earned": earned,
           "total_spent": spent, "events": []}
    kt._LEDGER_PATH.write_text(json.dumps(raw))
    incident = kt._LEDGER_PATH.with_suffix(".json.corrupt")
    incident.write_bytes(b"retained synthetic incident\n")
    return raw, incident


@pytest.mark.parametrize("chain_earned", [160, 400])
@pytest.mark.parametrize("cold", [False, True])
def test_negative_restoration_refused_without_side_effects(chain_earned, cold):
    restored, incident = restore(chain_earned=chain_earned, earned=160, spent=225)
    assert km.verify_chain() == (True, [])
    if cold:
        kt._ledger_instance = None
    cached = kt._ledger_instance
    cached_before = cached._operation_snapshot() if cached is not None else None
    retained = {p.name: p.read_bytes() for p in kt._LEDGER_PATH.parent.iterdir()
                if p.is_file()}

    with pytest.raises(ValueError, match="negative"):
        kt.acknowledge_ledger_recovery(reason="reviewed retained debit total")

    assert kt._ledger_instance is cached
    if cached is not None:
        assert cached._operation_snapshot() == cached_before
    assert {p.name: p.read_bytes() for p in kt._LEDGER_PATH.parent.iterdir()
            if p.is_file()} == retained
    assert disk() == restored
    assert incident.read_bytes() == b"retained synthetic incident\n"
    assert kt.status()["available"] is False
    assert kt.spend("paid", 1) == 0
    assert not km.reconcile_ledger_from_chain()["reconciled"]
    assert disk() == restored


@pytest.mark.parametrize("earned,spent", [(400, 225), (225, 225), (0, 0)])
def test_positive_and_zero_recovery_preserve_reviewed_accounting(earned, spent):
    restored, incident = restore(chain_earned=earned, earned=earned, spent=spent)
    kt._ledger_instance = None
    incidents = kt._ledger_incidents()
    assert kt.status()["available"] is False

    assert kt.acknowledge_ledger_recovery(reason="reviewed complete accounting") is None

    assert kt.status()["available"] is True
    for key in restored:
        assert disk()[key] == restored[key]
    assert disk()["recovery"]["incidents"] == incidents
    assert incident.read_bytes() == b"retained synthetic incident\n"
    assert km.reconcile_ledger_from_chain()["reconciled"]
    assert (disk()["balance"], disk()["total_spent"]) == (earned - spent, spent)
    kt._ledger_instance = None
    assert kt.status()["available"] is True


def test_ack_cleanup_failure_requires_explicit_review_and_retry(monkeypatch):
    restored, incident = restore(chain_earned=300, earned=300, spent=80)
    kt._ledger_instance = None
    reviewed_incidents = kt._ledger_incidents()
    original_unlink = Path.unlink
    failed_cleanup = []

    def fail_cleanup(path, *args, **kwargs):
        if (path.parent == kt._LEDGER_PATH.parent and
                path.name.startswith(kt._LEDGER_PATH.name + ".pending.")):
            failed_cleanup.append(path)
            raise PermissionError("synthetic acknowledgement intent cleanup failure")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_cleanup)
        with pytest.raises(kt.LedgerPersistenceError, match="incomplete"):
            kt.acknowledge_ledger_recovery(reason="reviewed original incident")

    assert len(set(failed_cleanup)) == 1
    for key in restored:
        assert disk()[key] == restored[key]
    assert disk()["recovery"]["incidents"] == reviewed_incidents
    retained = kt._ledger_incidents()
    assert set(retained) == set(reviewed_incidents) | {failed_cleanup[0].name}
    assert kt.status()["available"] is False
    assert kt.spend("paid", 1) == 0
    kt._ledger_instance = None
    assert kt.status()["available"] is False
    assert kt.earn(1, "cache_hit") == 0

    # Review includes the new retained intent; removing the fault alone is not recovery.
    kt.acknowledge_ledger_recovery(reason="reviewed committed restoration and cleanup incident")
    assert kt.status()["available"] is True
    assert kt._ledger_incidents() == retained
    assert disk()["recovery"]["incidents"] == retained
    assert incident.read_bytes() == b"retained synthetic incident\n"
    assert disk()["total_spent"] == 80
    assert kt.spend("paid", 1) == 1
    assert disk()["total_spent"] == 81


def test_ack_reports_unreadable_completion_after_confirmed_write(monkeypatch):
    restored, incident = restore(chain_earned=300, earned=300, spent=80)
    kt._ledger_instance = None
    incidents = kt._ledger_incidents()
    original_read, original_replace = Path.read_text, os.replace
    replaced = False

    def replace(source, target):
        nonlocal replaced
        original_replace(source, target)
        if Path(target) == kt._LEDGER_PATH:
            replaced = True

    def fail_read(path, *args, **kwargs):
        if replaced and path == kt._LEDGER_PATH:
            raise OSError("synthetic completion read failure")
        return original_read(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace)
        patch.setattr(Path, "read_text", fail_read)
        with pytest.raises(kt.LedgerPersistenceError, match="incomplete"):
            kt.acknowledge_ledger_recovery(reason="reviewed restored accounting")
        assert kt.status()["available"] is False

    # The write was confirmed; failure to read it back is not proof of rollback.
    for key in restored:
        assert disk()[key] == restored[key]
    assert disk()["recovery"]["incidents"] == incidents
    assert kt._ledger_incidents() == incidents
    assert incident.read_bytes() == b"retained synthetic incident\n"
    kt._ledger_instance = None
    assert kt.status()["available"] is True


def test_ack_resolved_lost_replace_acknowledgement_is_available(monkeypatch):
    restored, incident = restore(chain_earned=300, earned=300, spent=80)
    kt._ledger_instance = None
    incidents = kt._ledger_incidents()
    original_replace = os.replace
    replacements = []

    def lose_acknowledgement(source, target):
        original_replace(source, target)
        if Path(target) == kt._LEDGER_PATH:
            replacements.append(Path(target))
            raise OSError("synthetic lost replace acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", lose_acknowledgement)
        assert kt.acknowledge_ledger_recovery(reason="reviewed restored accounting") is None

    assert replacements == [kt._LEDGER_PATH]
    assert kt._ledger_incidents() == incidents
    assert kt.status()["available"] is True
    for key in restored:
        assert disk()[key] == restored[key]
    assert incident.read_bytes() == b"retained synthetic incident\n"
    kt._ledger_instance = None
    assert kt.status()["available"] is True
    assert kt.spend("paid", 1) == 1
    assert disk()["total_spent"] == 81
