"""Test isolation for the standalone KRY package.

Every KRY module binds its persistence path from ``_kry_data_dir()`` at import
time (``KRY_DATA_DIR`` env, default ``./kry_data``). Without isolation the whole
suite shares one ledger/mint-log/registry, and ``save()``'s delta-merge makes
tests accumulate into each other. The autouse fixture below points every module's
path attribute at a fresh per-test tmp dir and resets the cached singleton, so
each test starts from an empty, isolated state.
"""
from __future__ import annotations

import importlib

import pytest

# module → (path-attribute names bound at import from _kry_data_dir())
_PATH_ATTRS = {
    "kry.kry_token": ["_LEDGER_PATH"],
    "kry.kry_mint": ["_MINT_LOG_PATH", "_DECAY_STATE_PATH"],
    "kry.kry_attest": ["_MINT_LOG_PATH"],
    "kry.kry_referee": ["_REFEREE_LOG", "_SANCTIONED_PATH", "_ESCALATION_PATH"],
    "kry.kry_settlement": ["_REGISTRY_PATH"],
    "kry.kry_baseline": ["_BASELINE_PATH"],
    "kry.kry_sanctions": ["_REP_PATH"],
    "kry.kry_pending": ["_PENDING_PATH", "_LOCK_PATH"],
}


@pytest.fixture(autouse=True)
def _isolate_kry_state(tmp_path, monkeypatch):
    monkeypatch.setenv("KRY_DATA_DIR", str(tmp_path))
    for modname, attrs in _PATH_ATTRS.items():
        try:
            mod = importlib.import_module(modname)
        except Exception:
            continue
        for attr in attrs:
            if hasattr(mod, attr):
                # keep the original filename (mint-log is shared by mint + attest)
                fname = getattr(mod, attr).name
                monkeypatch.setattr(mod, attr, tmp_path / fname)
        if hasattr(mod, "_ledger_instance"):
            monkeypatch.setattr(mod, "_ledger_instance", None)

    # kry_mint seeds its chain globals at IMPORT time via _initialise_from_log(), which
    # reads the REAL kry_data log before this fixture repoints the path. On a machine with
    # real mints that leaks chain tip/counter (and decay state) into the suite — a populated
    # ledger would otherwise break tests (e.g. test_baseline). Reset to genesis so every test
    # starts from an empty, isolated chain regardless of the host's real ledger.
    try:
        km = importlib.import_module("kry.kry_mint")
        monkeypatch.setattr(km, "_RECEIPT_COUNTER", 0)
        monkeypatch.setattr(km, "_CHAIN_TIP", "0" * 64)
        monkeypatch.setattr(km, "_evidence_mints", {})
        monkeypatch.setattr(km, "_decay_loaded", True)
    except Exception:
        pass

    # kry_settlement keeps an in-process reservation map (closes the verify->settle
    # TOCTOU within one process). Reset it so reservations never leak between tests.
    try:
        kset = importlib.import_module("kry.kry_settlement")
        monkeypatch.setattr(kset, "_PENDING_RESERVATIONS", {})
    except Exception:
        pass
    yield
