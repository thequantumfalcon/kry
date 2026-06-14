"""CI coverage for the lab analysis helpers (energy report + holdout truth check),
so the credibility tooling is itself verified with synthetic inputs."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    path = _ROOT / "lab" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── energy_report ─────────────────────────────────────────────────────────────

def test_j_per_token_and_avoided():
    er = _load("energy_report")
    assert abs(er.j_per_token(10.0, 5000) - 7.2) < 1e-9          # 10 Wh / 5000 tok
    # 1M tokens, 7.2 -> 2.0 J/tok avoided = 5.2e6 J = 1.4444 kWh
    assert abs(er.avoided_kwh(1_000_000, 7.2, 2.0) - 1.4444) < 1e-3
    assert er.avoided_kwh(1_000_000, 2.0, 7.2) == 0.0           # 'greener' that isn't -> 0


def test_energy_report_picks_greenest():
    er = _load("energy_report")
    rep = er.report({"grid_co2_g_per_kwh": 400,
                     "nodes": {"rtx5080": {"tokens": 50000, "energy_wh": 38.0},
                               "mac_m4": {"tokens": 50000, "energy_wh": 7.0}}})
    assert rep["greenest_node"] == "mac_m4"
    assert rep["dirtiest_node"] == "rtx5080"
    assert rep["per_million_tokens_displaced"]["avoided_kwh"] > 0
    assert rep["per_million_tokens_displaced"]["avoided_co2_g"] > 0


# ── holdout_truth_check ───────────────────────────────────────────────────────

def test_coverage_passes_when_ci_brackets_truth():
    htc = _load("holdout_truth_check")
    # 85/100 holdout paid -> p_hat 0.85; truth 0.85 -> inside CI
    by_class = {"summarize": {"holdout_n": 100, "holdout_paid_n": 85, "p_hat": 0.85},
                "code": {"holdout_n": 100, "holdout_paid_n": 70, "p_hat": 0.70}}
    out = htc.coverage(by_class, {"summarize": 0.85, "code": 0.70})
    assert out["classes_checked"] == 2 and out["covered"] == 2
    assert out["agreement"] == 1.0 and out["pass"] is True


def test_coverage_flags_biased_estimate_and_skips_thin_classes():
    htc = _load("holdout_truth_check")
    by_class = {
        "ok": {"holdout_n": 200, "holdout_paid_n": 100, "p_hat": 0.5},   # CI ~[0.43,0.57]
        "biased": {"holdout_n": 200, "holdout_paid_n": 180, "p_hat": 0.9},  # CI ~[0.85,0.93]
        "thin": {"holdout_n": 5, "holdout_paid_n": 5, "p_hat": 1.0},      # below min_holdout_n -> skipped
    }
    truth = {"ok": 0.5, "biased": 0.5, "thin": 1.0}   # 'biased' true rate 0.5 is far outside its CI
    out = htc.coverage(by_class, truth, min_holdout_n=30)
    assert out["classes_checked"] == 2          # 'thin' skipped
    assert out["covered"] == 1                  # 'ok' covers, 'biased' does not
    assert out["pass"] is False                 # 50% < 80% bar
