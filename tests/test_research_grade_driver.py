"""Step-1 driver (scripts/kry_research_grade.py): fetch -> reconcile -> grade.

These pin the GLUE that turns a real provider export into a research_grade decision —
the >= 0.80 independent-agreement bar from kry_capabilities, applied to the
reconciled_fraction. The pieces it chains (kry_or_fetch, kry_reconcile, the grader)
are tested elsewhere; here we prove the wiring: enough agreement advances the grade,
too little does not, and no oracle cannot.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "kry_research_grade.py"


def _load():
    spec = importlib.util.spec_from_file_location("kry_research_grade_standalone", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _t1_log(tmp_path, n, *, with_or_ref=True):
    """Write a mint log with n provider_metered T1 receipts, each metered [100+i, 200+i]."""
    log = tmp_path / "mint.jsonl"
    lines = []
    for i in range(n):
        rec = {"receipt_id": f"KRY-{i}", "evidence_tier": "provider_metered",
               "metered_tokens": [100 + i, 200 + i], "ts": 1000 + i}
        if with_or_ref:
            rec["detail"] = f"displacement /openrouter:gen-{i}"
        lines.append(json.dumps(rec))
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(log)


def _provider_record(i):
    return {"id": f"gen-{i}", "tokens_prompt": 100 + i, "tokens_completion": 200 + i}


def _write_json(tmp_path, data):
    path = tmp_path / "provider.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_full_agreement_reaches_research_grade(tmp_path):
    mod = _load()
    log = _t1_log(tmp_path, 5)
    export = [_provider_record(i) for i in range(5)]   # 5/5 match -> agreement 1.0
    r = mod.assess(log, provider_export=export)
    assert r["independent_agreement"] == 1.0
    assert r["research_grade_reached"] is True
    assert r["readiness_label"] == "research_grade"


def test_flat_provider_record_reaches_research_grade_for_single_t1(tmp_path):
    mod = _load()
    log = _t1_log(tmp_path, 1)
    export = {"prompt_tokens": 100, "completion_tokens": 200}

    r = mod.assess(log, provider_export=export)

    assert r["reconcile"]["verdict"] == "RECONCILED"
    assert r["reconcile"]["provider_records"] == 1
    assert r["independent_agreement"] == 1.0
    assert r["research_grade_reached"] is True


def test_eighty_percent_is_the_boundary(tmp_path):
    mod = _load()
    log = _t1_log(tmp_path, 5)
    export = [_provider_record(i) for i in range(4)]   # 4/5 = 0.80, exactly the bar
    r = mod.assess(log, provider_export=export)
    assert r["independent_agreement"] == 0.80
    assert r["research_grade_reached"] is True         # >= bar passes


def test_below_bar_does_not_advance(tmp_path):
    mod = _load()
    log = _t1_log(tmp_path, 5)
    export = [_provider_record(i) for i in range(3)]   # 3/5 = 0.60 < 0.80
    r = mod.assess(log, provider_export=export)
    assert r["independent_agreement"] == 0.60
    assert r["research_grade_reached"] is False
    assert r["readiness_label"] == "internally_consistent"
    assert any("agreement" in s for s in r["reasons"])


def test_no_oracle_cannot_advance(tmp_path):
    mod = _load()
    log = _t1_log(tmp_path, 5)
    r = mod.assess(log, provider_export=None)          # nothing fetched yet
    assert r["independent_agreement"] is None
    assert r["research_grade_reached"] is False
    assert any("INDEPENDENT" in s or "agreement" in s for s in r["reasons"])


def test_zero_t1_receipts_cannot_vacuously_reach_research_grade(tmp_path):
    """H2 regression: an empty (zero-T1) reconciliation must NOT read as agreement 1.0.

    Previously a non-None empty provider_export against a mint log with no
    provider_metered receipts produced reconciled_fraction 1.0 (0/0) and vacuously
    cleared the >= 0.80 bar — research_grade with zero external anchoring.
    """
    mod = _load()
    log = tmp_path / "empty.jsonl"
    log.write_text("", encoding="utf-8")                              # zero T1 receipts
    r = mod.assess(str(log), provider_export=[])    # non-None empty export
    assert r["t1_receipts"] == 0
    assert r["independent_agreement"] is None
    assert r["research_grade_reached"] is False
    assert r["readiness_label"] != "research_grade"
    assert r["reconcile"]["verdict"] == "NO_T1_RECEIPTS"


def test_failed_replay_blocks_even_with_full_agreement(tmp_path):
    mod = _load()
    log = _t1_log(tmp_path, 3)
    export = [_provider_record(i) for i in range(3)]
    r = mod.assess(log, provider_export=export, replay_pass_rate=0.9)   # suite not green
    assert r["research_grade_reached"] is False        # both gates required


def test_aggregate_mode_reconciles_under_billed_total(tmp_path):
    mod = _load()
    log = _t1_log(tmp_path, 3)   # summed metered = (100+101+102)+(200+201+202) = 906
    billing = {"tokens_prompt": 1000, "tokens_completion": 1000}   # billed 2000 >= 906
    r = mod.assess(log, provider_export=billing, mode="aggregate", since=1000, until=1003)
    assert r["reconcile"]["verdict"] == "RECONCILED"
    assert r["research_grade_reached"] is True


def test_aggregate_mode_requires_explicit_window(tmp_path, capsys):
    mod = _load()
    log = _t1_log(tmp_path, 3)
    billing = {"tokens_prompt": 1000, "tokens_completion": 1000}

    rc = mod.main([log, "--provider-export", str(_write_json(tmp_path, billing)), "--mode", "aggregate"])
    err = capsys.readouterr().err

    assert rc == 2
    assert "aggregate mode requires --since and --until receipt filters" in err


def test_main_fetch_path_with_fake_opener(tmp_path, monkeypatch, capsys):
    """--fetch pulls records via kry_or_fetch.fetch_generation; stub it to prove the
    end-to-end CLI reaches research_grade and exits 0."""
    mod = _load()
    log = _t1_log(tmp_path, 4)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    # stub the network fetch: return a matching record for each gen id
    def fake_fetch(gen_id, key, **kw):
        i = int(gen_id.split("-")[1])
        return {"id": gen_id, "native_tokens_prompt": 100 + i, "native_tokens_completion": 200 + i}
    monkeypatch.setattr(mod.kry_or_fetch, "fetch_generation", fake_fetch)
    rc = mod.main([log, "--fetch"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "research_grade REACHED" in out


def test_main_no_export_exits_nonzero(tmp_path, capsys):
    mod = _load()
    log = _t1_log(tmp_path, 2)
    rc = mod.main([log])      # no --fetch, no --provider-export
    out = capsys.readouterr().out
    assert rc == 1
    assert "oracle missing" in out


def test_main_rejects_nonstandard_provider_export_json(tmp_path, capsys):
    mod = _load()
    log = _t1_log(tmp_path, 1)
    export = tmp_path / "provider.json"
    export.write_text('[{"tokens_prompt":100,"tokens_completion":NaN}]\n', encoding="utf-8")

    rc = mod.main([log, "--provider-export", str(export)])
    err = capsys.readouterr().err

    assert rc == 1
    assert "provider export unreadable: non-standard JSON constant rejected: NaN" in err
