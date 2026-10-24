"""An unterminated retained JSONL record must not be joined to a new receipt."""
from __future__ import annotations

import pytest

from kry import kry_action as action, kry_mint as mint, kry_token as token


def files(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir()
            if path.is_file() and not path.name.endswith(".lock")}


@pytest.mark.parametrize("tail", [b"", b"\r"])
def test_generic_mint_refuses_unterminated_record_before_accounting_changes(tail):
    first = mint.mint("cache_hit", 100, evidence="first complete receipt")
    assert first is not None
    path = mint._MINT_LOG_PATH
    path.write_bytes(path.read_bytes()[:-1] + tail)
    assert mint.verify_chain() == (True, [])
    before = files(path.parent)
    ledger = token.get_ledger()
    state = ledger._operation_snapshot()
    decay = mint._evidence_mints.copy()
    counter, tip = mint._RECEIPT_COUNTER, mint._CHAIN_TIP
    assert mint.mint("cache_hit", 100, evidence="second complete receipt") is None
    assert files(path.parent) == before
    assert ledger._operation_snapshot() == state
    assert mint._evidence_mints == decay
    assert (mint._RECEIPT_COUNTER, mint._CHAIN_TIP) == (counter, tip)
    assert mint.verify_chain() == (True, [])


@pytest.mark.parametrize("tail", [b"", b"\r"])
def test_action_refuses_unterminated_record_without_appending(tail):
    action.record("synthetic", {"sequence": 1}, result="ok")
    path = action._action_log_path()
    path.write_bytes(path.read_bytes()[:-1] + tail)
    assert action.verify_action_chain() == (True, [])
    before = files(path.parent)
    counter, tip = action._COUNTER, action._CHAIN_TIP
    with pytest.raises(ValueError, match="unterminated"):
        action.record("synthetic", {"sequence": 2}, result="ok")
    assert files(path.parent) == before
    assert (action._COUNTER, action._CHAIN_TIP) == (counter, tip)
    assert action.verify_action_chain() == (True, [])


def test_newline_terminated_mint_appends_and_credits_normally():
    assert mint.mint("cache_hit", 100, evidence="first") is not None
    assert mint.mint("cache_hit", 100, evidence="second") is not None
    assert len(mint._MINT_LOG_PATH.read_text().splitlines()) == 2
    assert token.get_ledger().balance == 200
    assert mint.verify_chain() == (True, [])


def test_newline_terminated_action_appends_normally():
    action.record("synthetic", {"sequence": 1}, result="ok")
    action.record("synthetic", {"sequence": 2}, result="ok")
    assert len(action._action_log_path().read_text().splitlines()) == 2
    assert action.verify_action_chain() == (True, [])
