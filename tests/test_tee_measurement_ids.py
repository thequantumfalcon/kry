"""Measurement-ID boundaries in mint glue; verification outputs are synthetic."""
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
        f"measurement_ids_{kind}", _ROOT / f"scripts/kry_{kind}_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return kind, mod


def _att(kind, measurement_id, presentation):
    result = {"verified": True, "module_id": "test-module", "pcr0": "ab" * 48,
              "chip_id": "cd" * 64, "measurement": "ef" * 48,
              "doc_sha256": str(presentation), "report_sha256": str(presentation),
              "mint_eligible": True, "statement_sha256": f"{presentation:064x}"}
    result["measurement" if kind == "tee" else "parsed_measurement"] = {
        "measurement_id": measurement_id, "tokens_saved": 5000,
        "avoided_model": "gh/claude-opus-4.8"}
    return result


@pytest.mark.parametrize("measurement_id", [
    "run/7", "run 7", "run\t7", "run\n7", "run\u00a07",
    7, True, ["run"], {"id": "run"},
])
@pytest.mark.parametrize("override", [False, True])
def test_invalid_id_cannot_add_to_prior_measurement(adapter, measurement_id, override):
    from kry import kry_mint as km

    kind, mod = adapter
    km.mint("cache_hit", 5000, detail=f"holdout /measurement:{measurement_id}",
            evidence="original-measurement")
    before = km._MINT_LOG_PATH.read_bytes()
    for presentation in (1, 2):
        att = _att(kind, "valid-embedded" if override else measurement_id, presentation)
        result = mod.run(att, **{**_KW, "measurement_id": measurement_id if override else None})
        assert result["verdict"] == "INVALID_MEASUREMENT_ID"
        assert "minted" not in result
        assert km._MINT_LOG_PATH.read_bytes() == before
        assert km.veracity_breakdown()["total_kry"] == 5000
        assert km.verify_chain()[0]


@pytest.mark.parametrize("measurement_id", ["run/7", "run 7"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_invalid_fresh_id_is_rejected_before_writes(adapter, measurement_id, dry_run):
    from kry import kry_mint as km

    kind, mod = adapter
    result = mod.run(_att(kind, measurement_id, 1), **{**_KW, "dry_run": dry_run})
    assert result["verdict"] == "INVALID_MEASUREMENT_ID"
    assert not km._MINT_LOG_PATH.exists()
    assert km.chain_summary()["receipts"] == 0


@pytest.mark.parametrize("override", ["", 0, False])
def test_explicit_empty_or_non_string_override_does_not_fall_back(adapter, override):
    from kry import kry_mint as km

    kind, mod = adapter
    result = mod.run(_att(kind, "valid-embedded", 1), **{**_KW, "measurement_id": override})
    assert result["verdict"] in ("NO_MEASUREMENT_ID", "INVALID_MEASUREMENT_ID")
    assert not km._MINT_LOG_PATH.exists()


@pytest.mark.parametrize("measurement_id", ["run/7", "run 7", "run\t7", "", None, 0, True, ["run"]])
def test_direct_promotion_rejects_invalid_id(measurement_id):
    from kry import kry_mint as km

    with pytest.raises(ValueError, match="measurement_id"):
        km.promote_to_tee(measurement_id, "test-proof")
    assert not km._MINT_LOG_PATH.exists()


@pytest.mark.parametrize("measurement_id", ["run-7", "run:7", 'run"7', r"run\7", "測定7"])
@pytest.mark.parametrize("prior", [False, True])
def test_valid_id_is_credited_once_across_distinct_presentations(adapter, measurement_id, prior):
    from kry import kry_mint as km

    kind, mod = adapter
    if prior:
        original = km.mint("cache_hit", 5000, detail=f"holdout /measurement:{measurement_id}",
                           evidence="original-measurement")
    for presentation in (1, 2):
        result = mod.run(_att(kind, measurement_id, presentation), **_KW)
        if presentation == 1:
            assert result["verdict"] == "OK"
            assert result["minted"]["mode"] == ("tier_upgrade" if prior else "fresh_mint")
            if prior:
                assert result["minted"]["supersedes"] == original.receipt_id
        else:
            assert result["verdict"] == ("ALREADY_UPGRADED" if prior else "NOT_MINTED")
        assert km.veracity_breakdown()["total_kry"] == 5000
        assert km.veracity_breakdown()["tee_attested_kry"] == 5000
        assert km.verify_chain()[0]


@pytest.mark.parametrize("tier", ["self_reported", "tee_attested"])
def test_json_escape_spelling_does_not_hide_measurement(adapter, tier):
    from kry import kry_mint as km

    kind, mod = adapter
    original = km.mint("cache_hit", 5000, detail="holdout /measurement:run-7",
                       evidence="original-measurement", evidence_tier=tier)
    encoded = km._MINT_LOG_PATH.read_text().replace("/measurement:", r"\u002fmeasurement:")
    km._MINT_LOG_PATH.write_text(encoded)
    assert km.verify_chain()[0]
    result = mod.run(_att(kind, "run-7", 1), **_KW)
    if tier == "self_reported":
        assert result["minted"]["mode"] == "tier_upgrade"
        assert result["minted"]["supersedes"] == original.receipt_id
    else:
        assert result["verdict"] == "NOT_MINTED"
    assert km.veracity_breakdown()["total_kry"] == 5000
    assert km.verify_chain()[0]


@pytest.mark.parametrize("measurement_id", ["run/7", "run 7"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_rejects_invalid_override(adapter, measurement_id, dry_run, tmp_path, monkeypatch, capsys):
    from kry import kry_mint as km

    kind, mod = adapter
    document = tmp_path / "synthetic-document"
    document.write_bytes(b"synthetic verification input")
    att = _att(kind, "valid-embedded", 1)
    if kind == "tee":
        monkeypatch.setattr(mod, "_load_root_der", lambda path: (b"test-root", mod.AWS_NITRO_ROOT_G1_DER_SHA256))
        monkeypatch.setattr(mod, "verify_attestation", lambda *args, **kwargs: att)
        argv = [str(document), "--root", str(document)]
    else:
        monkeypatch.setattr(mod, "_load_cert_der", lambda path: b"test-certificate")
        monkeypatch.setattr(mod, "verify_report", lambda *args, **kwargs: att)
        argv = [str(document), "--vcek", str(document), "--ask", str(document),
                "--ark", str(document), "--measurement-json", str(document)]
    argv += ["--measurement-id", measurement_id]
    if dry_run:
        argv.append("--dry-run")
    assert mod.main(argv) == 1
    assert "measurement_id" in capsys.readouterr().out
    assert not km._MINT_LOG_PATH.exists()
