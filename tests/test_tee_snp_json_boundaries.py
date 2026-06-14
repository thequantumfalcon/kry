"""JSON/evidence-boundary checks for TEE verifier glue without crypto fixtures."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_TEE = Path(__file__).resolve().parents[1] / "scripts" / "kry_tee_verify.py"
_SNP = Path(__file__).resolve().parents[1] / "scripts" / "kry_snp_verify.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tee_measurement_json_boundary_rejects_nonstandard_constants():
    mod = _load(_TEE, "kry_tee_verify_json_boundary")
    raw = b'{"measurement_id":"meas-bad","tokens_saved":NaN,"avoided_model":"x"}'

    assert mod._parse_measurement(raw) is None


def test_tee_non_finite_basis_refuses_before_mint():
    mod = _load(_TEE, "kry_tee_verify_nonfinite_basis")
    att = {
        "verified": True,
        "measurement": {
            "measurement_id": "meas-bad",
            "tokens_saved": float("nan"),
            "avoided_model": "gh/claude-opus-4.8",
        },
    }

    res = mod.run(att, event_type="short_circuit", avoided_model=None,
                  served_model=None, tokens_saved=None, measurement_id=None,
                  dry_run=False)

    assert res["verdict"] == "NO_BASIS"
    assert "minted" not in res


def test_snp_measurement_json_boundary_rejects_nonstandard_constants():
    mod = _load(_SNP, "kry_snp_verify_json_boundary")
    raw = b'{"measurement_id":"meas-bad","tokens_saved":Infinity,"avoided_model":"x"}'

    assert mod._parse_measurement(raw) is None


def test_snp_non_finite_cli_basis_refuses_before_mint():
    mod = _load(_SNP, "kry_snp_verify_nonfinite_basis")
    att = {
        "verified": True,
        "parsed_measurement": {
            "measurement_id": "meas-bad",
            "tokens_saved": 100,
            "avoided_model": "gh/claude-opus-4.8",
        },
    }

    res = mod.run(att, event_type="short_circuit", avoided_model=None,
                  served_model=None, tokens_saved=float("inf"),
                  measurement_id=None, dry_run=False)

    assert res["verdict"] == "NO_BASIS"
    assert "minted" not in res
