"""TEE mint adapters must select the identity in the verified measurement.

These fixtures exercise post-verification glue, not hardware attestation.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_KW = dict(event_type="cache_hit", avoided_model=None, served_model=None,
           tokens_saved=None, measurement_id=None, dry_run=False)


@pytest.fixture(params=["tee", "snp"])
def adapter(request):
    kind = request.param
    spec = importlib.util.spec_from_file_location(
        f"identity_binding_{kind}", _ROOT / f"scripts/kry_{kind}_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    att = {"verified": True, "module_id": "constructed-module", "pcr0": "ab" * 48,
           "chip_id": "cd" * 64, "measurement": "ef" * 48, "doc_sha256": "01" * 32,
           "report_sha256": "02" * 32, "statement_sha256": "03" * 32,
           "mint_eligible": True}
    field = "measurement" if kind == "tee" else "parsed_measurement"
    att[field] = {"measurement_id": "measured-A", "tokens_saved": 1000,
                  "avoided_model": "gh/claude-opus-4.8"}
    return kind, mod, att, field


@pytest.mark.parametrize("prior", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_different_identity_cannot_mint_or_promote(adapter, prior, dry_run):
    from kry import kry_mint as km

    _, mod, att, _ = adapter
    if prior:
        km.mint("cache_hit", 1000, detail="/measurement:unrelated-B", evidence="prior-B",
                avoided_model="gh/claude-opus-4.8")
    before = km._MINT_LOG_PATH.read_bytes() if km._MINT_LOG_PATH.exists() else None
    result = mod.run(att, **{**_KW, "measurement_id": "unrelated-B", "dry_run": dry_run})
    assert result["verdict"] == "MEASUREMENT_ID_MISMATCH"
    assert "minted" not in result
    assert (km._MINT_LOG_PATH.read_bytes() if km._MINT_LOG_PATH.exists() else None) == before
    assert km.veracity_breakdown()["tee_attested_kry"] == 0


@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_cannot_supply_missing_authenticated_identity(adapter, dry_run):
    from kry import kry_mint as km

    _, mod, att, field = adapter
    del att[field]["measurement_id"]
    result = mod.run(att, **{**_KW, "measurement_id": "operator-only", "dry_run": dry_run})
    assert result["verdict"] == "NO_MEASUREMENT_ID"
    assert "minted" not in result
    assert not km._MINT_LOG_PATH.exists()


def test_matching_identity_assertion_still_credits_once(adapter):
    from kry import kry_mint as km

    _, mod, att, _ = adapter
    original = km.mint("cache_hit", 1000, detail="/measurement:measured-A", evidence="prior-A",
                       avoided_model="gh/claude-opus-4.8")
    first = mod.run(att, **{**_KW, "measurement_id": "measured-A"})
    assert first["verdict"] == "OK"
    assert first["minted"]["supersedes"] == original.receipt_id
    assert mod.run(att, **_KW)["verdict"] == "ALREADY_UPGRADED"
    assert km.veracity_breakdown()["total_kry"] == 1000
    assert km.veracity_breakdown()["tee_attested_kry"] == 1000
    assert km.verify_chain()[0]


@pytest.mark.parametrize("identity", [0, False, [], {}, "run/7", "run 7", ""])
def test_cli_cannot_replace_invalid_payload_identity(adapter, identity):
    from kry import kry_mint as km

    _, mod, att, field = adapter
    att[field]["measurement_id"] = identity
    result = mod.run(att, **{**_KW, "measurement_id": "measured-A"})
    assert result["verdict"] == "INVALID_MEASUREMENT_ID"
    assert not km._MINT_LOG_PATH.exists()


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_identity_refusal_is_nonzero(adapter, missing, dry_run, tmp_path, monkeypatch, capsys):
    from kry import kry_mint as km

    kind, mod, att, field = adapter
    if missing:
        del att[field]["measurement_id"]
    document = tmp_path / "constructed-verification-input"
    document.write_bytes(b"constructed input; verifier output stubbed")
    if kind == "tee":
        monkeypatch.setattr(mod, "_load_root_der", lambda path: (b"root", mod.AWS_NITRO_ROOT_G1_DER_SHA256))
        monkeypatch.setattr(mod, "verify_attestation", lambda *args, **kwargs: att)
        argv = [str(document), "--root", str(document)]
    else:
        monkeypatch.setattr(mod, "_load_cert_der", lambda path: b"certificate")
        monkeypatch.setattr(mod, "verify_report", lambda *args, **kwargs: att)
        argv = [str(document), "--vcek", str(document), "--ask", str(document),
                "--ark", str(document), "--measurement-json", str(document)]
    argv += ["--measurement-id", "unrelated-B"]
    if dry_run:
        argv.append("--dry-run")
    assert mod.main(argv) == 1
    assert "measurement_id" in capsys.readouterr().out
    assert not km._MINT_LOG_PATH.exists()
