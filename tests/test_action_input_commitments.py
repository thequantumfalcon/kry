"""Raw hex-looking content must not be mistaken for an existing commitment."""
from __future__ import annotations

import json

import pytest

from kry import kry_action


@pytest.fixture(autouse=True)
def isolated_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("KRY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(kry_action, "_COUNTER", 0)
    monkeypatch.setattr(kry_action, "_CHAIN_TIP", kry_action.GENESIS)


@pytest.mark.parametrize("field", ["args", "result", "server_evidence"])
def test_raw_64_hex_string_is_hashed_before_storage(field):
    secret = "0123456789abcdef" * 4
    values = {"args": {"query": "fixture"}, "result": {"ok": True},
              "server_evidence": {"witness": "fixture"}}
    values[field] = secret
    receipt = kry_action.record("fixture.tool", evidence_tier=kry_action.TIER_SERVER_WITNESSED,
                                **values)
    assert getattr(receipt, field + "_commit") == kry_action.commit(secret)
    assert secret not in json.dumps(kry_action.build_action_attestation())
    assert secret not in kry_action._action_log_path().read_text()


def test_explicit_precommitted_values_are_preserved():
    commits = {field: kry_action.commit({"field": field})
               for field in ("args", "result", "server_evidence")}
    receipt = kry_action.record("fixture.tool", precommitted=True,
                                evidence_tier=kry_action.TIER_SERVER_WITNESSED, **commits)
    for field, value in commits.items():
        assert getattr(receipt, field + "_commit") == value
    assert kry_action.verify_action_chain()[0]


@pytest.mark.parametrize("field", ["args", "result", "server_evidence"])
@pytest.mark.parametrize("invalid", ["raw content", "a" * 63, "A" * 64, 7, {}, []])
def test_precommitted_mode_refuses_raw_or_malformed_values_before_writing(field, invalid):
    values = {name: kry_action.commit(name) for name in ("args", "result", "server_evidence")}
    values[field] = invalid
    with pytest.raises(ValueError, match=field):
        kry_action.record("fixture.tool", precommitted=True, **values)
    assert not kry_action._action_log_path().exists()


@pytest.mark.parametrize("invalid", [None, 0, 1, "false"])
def test_precommitted_mode_requires_a_boolean(invalid):
    with pytest.raises(ValueError, match="precommitted"):
        kry_action.record("fixture.tool", "a" * 64, precommitted=invalid)
    assert not kry_action._action_log_path().exists()


def test_precommitted_mode_keeps_absent_optional_values_absent():
    receipt = kry_action.record("fixture.tool", kry_action.commit(None),
                                precommitted=True, has_result=False)
    assert receipt.result_commit is None
    assert receipt.server_evidence_commit is None
