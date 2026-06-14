"""F1: provider-usage reconciliation — anchor T1 mints to the provider's own log.

A provider_metered receipt must correspond to a real provider usage record. These
tests mint real T1 receipts (with retained metered tokens), then reconcile them
against a synthetic provider export, confirming honest matches pass and a claim
with no provider footprint is flagged.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_RECONCILE = Path(__file__).resolve().parents[1] / "scripts" / "kry_reconcile.py"


def _load():
    spec = importlib.util.spec_from_file_location("kry_reconcile_standalone", _RECONCILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    import kry.kry_token as kt
    import kry.kry_mint as km
    log = tmp_path / "mint.jsonl"
    monkeypatch.setattr(km, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(kt, "_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(km, "_DECAY_STATE_PATH", tmp_path / "decay.json")
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = "0" * 64
    km._evidence_mints = {}
    km._decay_loaded = True
    kt._ledger_instance = kt.KRYLedger()
    return kt, km, log


def test_reconcile_imports_no_kernel():
    src = _RECONCILE.read_text(encoding="utf-8")
    assert "import kernel" not in src and "from kernel" not in src


def test_load_only_picks_provider_metered_with_tokens(isolated):
    kt, km, log = isolated
    km.mint("cache_hit", 1000, "c", evidence="c", avoided_model="gh/claude-opus-4.8")  # T0
    km.mint("short_circuit", 800, "d", evidence="d", avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[120, 340])           # T1
    r = _load()
    t1 = r.load_t1_receipts(str(log))
    assert len(t1) == 1
    assert t1[0]["metered_tokens"] == [120, 340]


def test_all_t1_reconcile_against_provider_export(isolated):
    kt, km, log = isolated
    km.mint("short_circuit", 800, "a", evidence="a", avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[120, 340])
    km.mint("short_circuit", 900, "b", evidence="b", avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[200, 50])
    r = _load()
    t1 = r.load_t1_receipts(str(log))
    # Provider export in OpenRouter generation-API shape (tokens_prompt/completion).
    export = [
        {"tokens_prompt": 200, "tokens_completion": 50},
        {"tokens_prompt": 120, "tokens_completion": 340},
        {"tokens_prompt": 999, "tokens_completion": 1},   # an unrelated call — fine
    ]
    res = r.reconcile(t1, export)
    assert res["verdict"] == "RECONCILED"
    assert res["matched"] == 2 and res["reconciled_fraction"] == 1.0


def test_unmatched_t1_claim_is_flagged(isolated):
    kt, km, log = isolated
    km.mint("short_circuit", 800, "a", evidence="a", avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[120, 340])
    km.mint("short_circuit", 900, "b", evidence="b", avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[200, 50])
    r = _load()
    t1 = r.load_t1_receipts(str(log))
    # Provider log has only ONE of the two claimed calls.
    export = [{"prompt_tokens": 120, "completion_tokens": 340}]
    res = r.reconcile(t1, export)
    assert res["verdict"] == "DISCREPANCY"
    assert len(res["unmatched_receipts"]) == 1
    assert res["unmatched_receipts"][0]["metered"] == [200, 50]


def test_one_provider_call_cannot_back_two_claims(isolated):
    """Greedy one-to-one: N metered claims need N distinct provider calls."""
    kt, km, log = isolated
    for i in range(2):
        km.mint("short_circuit", 800, f"x{i}", evidence=f"x{i}",
                avoided_model="gh/claude-opus-4.8",
                evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[120, 340])
    r = _load()
    t1 = r.load_t1_receipts(str(log))
    export = [{"prompt_tokens": 120, "completion_tokens": 340}]   # only ONE real call
    res = r.reconcile(t1, export)
    assert res["matched"] == 1 and len(res["unmatched_receipts"]) == 1


def test_provider_record_rows_accepts_single_flat_usage_record():
    r = _load()
    record = {"prompt_tokens": 120, "completion_tokens": 340}

    assert r.provider_record_rows(record) == [record]
    assert r.provider_record_rows({"metadata": "not usage"}) == []


# --- aggregate mode (providers with no per-request export, e.g. Google) ---------


def _two_t1(km, log):
    """Mint two T1 receipts: metered totals 460 + 250 = 710."""
    km.mint("short_circuit", 800, "a", evidence="a", avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[120, 340])
    km.mint("short_circuit", 900, "b", evidence="b", avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[200, 50])


def test_aggregate_reconciled_within_billed_total(isolated):
    kt, km, log = isolated
    _two_t1(km, log)
    r = _load()
    t1 = r.load_t1_receipts(str(log))
    # our sum 710 <= provider billed 800
    res = r.aggregate_reconcile(t1, {"prompt_tokens": 400, "completion_tokens": 400})
    assert res["verdict"] == "RECONCILED"
    assert res["our_minted_tokens"]["total"] == 710
    assert res["provider_billed_tokens"]["total"] == 800


def test_aggregate_phantom_excess_flagged(isolated):
    kt, km, log = isolated
    _two_t1(km, log)
    r = _load()
    t1 = r.load_t1_receipts(str(log))
    # our sum 710 > provider billed 100 -> phantom T1
    res = r.aggregate_reconcile(t1, {"prompt_tokens": 50, "completion_tokens": 50})
    assert res["verdict"] == "DISCREPANCY"
    assert res["overclaim_tokens"] == 610


def test_aggregate_tolerance_absorbs_small_overage(isolated):
    kt, km, log = isolated
    _two_t1(km, log)
    r = _load()
    t1 = r.load_t1_receipts(str(log))
    billed = {"prompt_tokens": 350, "completion_tokens": 350}   # 700, our 710 is 1.4% over
    assert r.aggregate_reconcile(t1, billed, tol_pct=5.0)["verdict"] == "RECONCILED"
    assert r.aggregate_reconcile(t1, billed, tol_pct=0.0)["verdict"] == "DISCREPANCY"


def test_aggregate_rejects_impossible_tolerance(isolated):
    kt, km, log = isolated
    _two_t1(km, log)
    r = _load()
    t1 = r.load_t1_receipts(str(log))

    for tol_pct in (float("nan"), float("inf"), -1.0, 5.1):
        with pytest.raises(ValueError):
            r.aggregate_reconcile(t1, {"prompt_tokens": 400, "completion_tokens": 400},
                                  tol_pct=tol_pct)


def test_aggregate_rejects_malformed_t1_metered_tokens():
    r = _load()

    with pytest.raises(ValueError, match="T1 receipt A: metered_tokens must be integers"):
        r.aggregate_reconcile(
            [{"receipt_id": "A", "metered_tokens": [True, 1]}],
            {"prompt_tokens": 1, "completion_tokens": 1},
        )


def test_aggregate_sums_list_of_billing_rows(isolated):
    kt, km, log = isolated
    _two_t1(km, log)
    r = _load()
    t1 = r.load_t1_receipts(str(log))
    rows = [{"prompt_tokens": 200, "completion_tokens": 200},
            {"prompt_tokens": 200, "completion_tokens": 200}]   # summed = 800
    res = r.aggregate_reconcile(t1, rows)
    assert res["verdict"] == "RECONCILED"
    assert res["provider_billed_tokens"]["total"] == 800


def test_aggregate_unwraps_data_envelope(isolated):
    kt, km, log = isolated
    _two_t1(km, log)
    r = _load()
    t1 = r.load_t1_receipts(str(log))
    res = r.aggregate_reconcile(t1, {"data": [{"prompt_tokens": 800, "completion_tokens": 0}]})
    assert res["provider_billed_tokens"]["total"] == 800


def test_aggregate_cli_requires_explicit_receipt_window(isolated, tmp_path, capsys):
    kt, km, log = isolated
    _two_t1(km, log)
    r = _load()
    export = tmp_path / "provider.json"
    export.write_text(json.dumps({"prompt_tokens": 400, "completion_tokens": 400}), encoding="utf-8")

    rc = r.main([str(log), "--mode", "aggregate", "--provider-export", str(export)])
    err = capsys.readouterr().err

    assert rc == 2
    assert "aggregate mode requires --since and --until receipt filters" in err


@pytest.mark.parametrize(
    ("since", "until", "needle"),
    [
        ("2000", "1000", "receipt window requires since < until"),
        ("nan", "2000", "since must be finite"),
        ("1000", "inf", "until must be finite"),
    ],
)
def test_aggregate_cli_rejects_bad_receipt_windows(isolated, tmp_path, capsys,
                                                   since, until, needle):
    kt, km, log = isolated
    _two_t1(km, log)
    r = _load()
    export = tmp_path / "provider.json"
    export.write_text(json.dumps({"prompt_tokens": 400, "completion_tokens": 400}), encoding="utf-8")

    rc = r.main([
        str(log),
        "--mode", "aggregate",
        "--provider-export", str(export),
        "--since", since,
        "--until", until,
    ])
    err = capsys.readouterr().err

    assert rc == 2
    assert needle in err


def test_preview_runs_without_provider_export(isolated, capsys):
    kt, km, log = isolated
    _two_t1(km, log)
    r = _load()
    rc = r.main([str(log)])           # no --provider-export
    out = capsys.readouterr().out
    assert rc == 0
    assert "PREVIEW" in out and "710" in out


def test_window_filter_excludes_out_of_range_receipts(tmp_path):
    r = _load()
    log = tmp_path / "m.jsonl"
    rows = [
        {"receipt_id": "A", "evidence_tier": "provider_metered", "metered_tokens": [10, 10], "ts": 1000.0},
        {"receipt_id": "B", "evidence_tier": "provider_metered", "metered_tokens": [20, 20], "ts": 2000.0},
        {"receipt_id": "C", "evidence_tier": "provider_metered", "metered_tokens": [30, 30], "ts": 3000.0},
    ]
    log.write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
    got = r.load_t1_receipts(str(log), since=1500, until=2500)
    assert [x["receipt_id"] for x in got] == ["B"]


def test_load_accepts_t1_reconciliation_manifest(tmp_path):
    r = _load()
    manifest = tmp_path / "t1_manifest.json"
    manifest.write_text(json.dumps({
        "schema": "kry_t1_reconciliation_manifest/v1",
        "source_mint_log_sha256": "0" * 64,
        "receipt_count": 2,
        "receipts": [
            {"receipt_id": "A", "evidence_tier": "provider_metered", "metered_tokens": [10, 20], "ts": 1000.0},
            {"receipt_id": "B", "evidence_tier": "self_reported", "metered_tokens": [999, 999], "ts": 1001.0},
        ],
    }), encoding="utf-8")

    got = r.load_t1_receipts(str(manifest))

    assert [x["receipt_id"] for x in got] == ["A"]


def test_reconcile_rejects_nonstandard_receipt_json(tmp_path):
    r = _load()
    manifest = tmp_path / "t1_manifest.json"
    manifest.write_text(
        '{"schema":"kry_t1_reconciliation_manifest/v1","receipts":['
        '{"receipt_id":"A","evidence_tier":"provider_metered","metered_tokens":[10,NaN]}]}\n'
    , encoding="utf-8")
    log = tmp_path / "mint.jsonl"
    log.write_text(
        '{"receipt_id":"A","evidence_tier":"provider_metered","metered_tokens":[10,Infinity]}\n'
    , encoding="utf-8")

    with pytest.raises(ValueError, match="non-standard JSON constant rejected: NaN"):
        r.load_t1_receipts(str(manifest))
    with pytest.raises(ValueError, match="non-standard JSON constant rejected: Infinity"):
        r.load_t1_receipts(str(log))


def test_reconcile_cli_rejects_nonstandard_provider_export(isolated, tmp_path):
    kt, km, log = isolated
    km.mint("short_circuit", 800, "a", evidence="a", avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[120, 340])
    r = _load()
    export = tmp_path / "provider.json"
    export.write_text('[{"prompt_tokens":120,"completion_tokens":NaN}]\n', encoding="utf-8")

    with pytest.raises(ValueError, match="non-standard JSON constant rejected: NaN"):
        r.main([str(log), "--provider-export", str(export)])


def test_load_rejects_boolean_t1_metered_tokens(tmp_path):
    r = _load()
    manifest = tmp_path / "t1_manifest.json"
    manifest.write_text(json.dumps({
        "schema": "kry_t1_reconciliation_manifest/v1",
        "source_mint_log_sha256": "0" * 64,
        "receipt_count": 1,
        "receipts": [
            {"receipt_id": "A", "evidence_tier": "provider_metered", "metered_tokens": [True, 20]},
        ],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="metered_tokens must be integers"):
        r.load_t1_receipts(str(manifest))


def test_reconcile_rejects_boolean_provider_tokens(isolated):
    kt, km, log = isolated
    km.mint("short_circuit", 800, "a", evidence="a", avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[120, 340])
    r = _load()
    t1 = r.load_t1_receipts(str(log))

    with pytest.raises(ValueError, match="prompt_tokens must be a non-negative JSON integer"):
        r.reconcile(t1, [{"prompt_tokens": True, "completion_tokens": 340}])


def test_aggregate_rejects_string_provider_tokens(isolated):
    kt, km, log = isolated
    _two_t1(km, log)
    r = _load()
    t1 = r.load_t1_receipts(str(log))

    with pytest.raises(ValueError, match="prompt_tokens must be a non-negative JSON integer"):
        r.aggregate_reconcile(t1, {"prompt_tokens": "800", "completion_tokens": 0})


def test_empty_reconcile_is_undefined_not_one():
    """GPT remediation regression: 0/0 must be undefined (None), never 'perfect agreement' 1.0 —
    for the reconcile() / aggregate_reconcile() helpers themselves, not just the grade driver."""
    mod = _load()
    r = mod.reconcile([], [])
    assert r["reconciled_fraction"] is None
    assert r["verdict"] == "NO_T1_RECEIPTS"
    a = mod.aggregate_reconcile([], {"tokens_prompt": 100, "tokens_completion": 100})
    assert a["reconciled_fraction"] is None
    assert a["verdict"] == "NO_T1_RECEIPTS"
