"""Aggregate verdicts preserve empty windows and exact integer boundaries."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def reconciler():
    path = Path(__file__).resolve().parents[1] / "scripts" / "kry_reconcile.py"
    spec = importlib.util.spec_from_file_location("kry_reconcile_boundaries", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _aggregate_cli(reconciler, tmp_path, receipts, provider_total, tolerance=0):
    mint_log = tmp_path / "receipts.json"
    mint_log.write_text(json.dumps(receipts), encoding="utf-8")
    export = tmp_path / "provider.json"
    export.write_text(json.dumps({"prompt_tokens": provider_total, "completion_tokens": 0}),
                      encoding="utf-8")
    return reconciler.main([
        str(mint_log), "--mode", "aggregate", "--provider-export", str(export),
        "--since", "1000", "--until", "2000", "--tolerance-pct", str(tolerance),
    ])


@pytest.mark.parametrize("receipts", [
    [],
    [{"evidence_tier": "provider_metered", "metered_tokens": [10, 6], "ts": 2000}],
    [{"evidence_tier": "provider_metered", "metered_tokens": [0, 0], "ts": 1500}],
], ids=["empty", "outside-window", "zero-only"])
def test_aggregate_cli_without_metered_tokens_is_not_reconciled(
        reconciler, tmp_path, capsys, receipts):
    rc = _aggregate_cli(reconciler, tmp_path, receipts, 1000)
    output = capsys.readouterr().out

    assert rc == 1
    assert "VERDICT: NO_T1_RECEIPTS" in output
    assert "VERDICT: RECONCILED" not in output


@pytest.mark.parametrize(("provider_total", "our_total", "tolerance", "verdict"), [
    (1000, 1000, 0, "RECONCILED"),
    (1000, 1001, 0, "DISCREPANCY"),
    (1000, 1001, 0.1, "RECONCILED"),
    (1000, 1002, 0.1, "DISCREPANCY"),
    (1000, 1050, 5, "RECONCILED"),
    (1000, 1051, 5, "DISCREPANCY"),
    (9007199254740995, 9007199254740995, 0, "RECONCILED"),
    (9007199254740995, 9007199254740996, 0, "DISCREPANCY"),
    (10**400, 10**400, 0, "RECONCILED"),
    (10**400, 10**400 + 1, 0, "DISCREPANCY"),
    (10**400, 1, 0, "RECONCILED"),
], ids=["exact", "zero-excess", "decimal-edge", "decimal-excess", "maximum-edge",
        "maximum-excess", "large-exact", "large-excess", "huge-exact", "huge-excess",
        "huge-provider-surplus"])
def test_aggregate_integer_tolerance_boundary(
        reconciler, provider_total, our_total, tolerance, verdict):
    result = reconciler.aggregate_reconcile(
        [{"receipt_id": "A", "metered_tokens": [our_total - 1, 1]}],
        [{"prompt_tokens": provider_total - 1, "completion_tokens": 0},
         {"prompt_tokens": 0, "completion_tokens": 1}],
        tol_pct=tolerance,
    )

    assert result["verdict"] == verdict
    assert result["our_minted_tokens"]["total"] == our_total
    assert result["provider_billed_tokens"]["total"] == provider_total
    assert result["overclaim_tokens"] == our_total - provider_total
    if provider_total >= our_total:
        assert result["reconciled_fraction"] == 1.0


@pytest.mark.parametrize(("provider_total", "our_total", "tolerance", "rc", "verdict"), [
    (1000, 1001, 0.1, 0, "RECONCILED"),
    (9007199254740995, 9007199254740996, 0, 1, "DISCREPANCY"),
])
def test_aggregate_cli_uses_exact_tolerance(
        reconciler, tmp_path, capsys, provider_total, our_total, tolerance, rc, verdict):
    receipts = [{"evidence_tier": "provider_metered", "metered_tokens": [our_total, 0],
                 "ts": 1500}]

    assert _aggregate_cli(reconciler, tmp_path, receipts, provider_total, tolerance) == rc
    assert f"VERDICT: {verdict}" in capsys.readouterr().out
