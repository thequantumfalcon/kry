"""Plain-log source assurance remains separate from declared receipt evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name + "_candidate", ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("holdouts", [30, 1000])
def test_plain_log_sample_size_cannot_authenticate_evidence(holdouts, tmp_path):
    sr = load("kry_savings_report")
    records = [{"id": f"h{i}", "model": "gh/claude-opus-4.8", "holdout": True,
                "request_class": "x", "usage": {"completion_tokens": 500}}
               for i in range(holdouts)]
    records += [{"id": "cache", "avoided_model": "gh/claude-opus-4.8", "cache_hit": True,
                 "request_class": "x", "evidence_tier": "holdout_validated",
                 "usage": {"completion_tokens": 500}},
                {"id": "displacement", "avoided_model": "gh/claude-opus-4.8",
                 "served_model": "claude-sonnet-5", "evidence_tier": "provider_metered",
                 "usage": {"input_tokens": 2000, "output_tokens": 400}}]
    report = sr.analyze(records)
    assert report["saved_kry"] > 0
    assert 0 < report["by_class"]["x"]["ci_lo"] < 1
    assert report["veracity"]["veracity_floor"] == 0
    assert report["veracity"]["self_reported_kry"] == report["saved_kry"]
    assert all(row["tier"] == "self_reported" for row in report["by_class"].values())
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    att = json.loads(att_path.read_text())
    assert att["veracity"]["veracity_floor"] == 0
    assert all(link["evidence_tier"] == "self_reported" for link in att["links"])
    assert load("kry_verify").verify_attestation(att)[0]


def test_positive_explicit_t1_preserves_separate_artifact_assurance_scopes(tmp_path):
    from kry import kry_mint as km
    from kry.kry_attest import build_attestation

    sr = load("kry_savings_report")
    records = [{"id": "cache", "avoided_model": "gh/claude-opus-4.8", "cache_hit": True,
                "usage": {"completion_tokens": 500}},
               {"id": "provider", "avoided_model": "gh/claude-opus-4.8",
                "served_model": "claude-sonnet-5",
                "usage": {"prompt_tokens": 2000, "completion_tokens": 400}}]
    sr._mint_and_attest(records[:1], None)
    receipt = km.mint("short_circuit", 400, "explicit synthetic provider fixture", evidence="provider",
                      avoided_model="gh/claude-opus-4.8", served_model="claude-sonnet-5",
                      evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[2000, 400])
    assert receipt is not None and receipt.kry_minted > 0
    usage = tmp_path / "usage.jsonl"
    usage.write_text("".join(json.dumps(row) + "\n" for row in records))
    att_path = tmp_path / "attestation.json"
    att_path.write_text(build_attestation().to_public_json())
    artifact = load("kry_verified_artifact").build_artifact(
        str(usage), attestation=str(att_path), mint_log=str(km._MINT_LOG_PATH))
    checks = {row["name"]: row for row in artifact["gates"]["product"]["checks"]}
    assert artifact["savings_report"]["veracity"]["veracity_floor"] == 0
    assert json.loads(att_path.read_text())["veracity"]["veracity_floor"] > 0
    assert checks["report_source_assurance_self_reported"]["pass"] is True
    assert all(check["pass"] for check in checks.values())
    assert artifact["assurance_scopes"]["attestation_declared_veracity"]["veracity_floor"] > 0
    assert artifact["assurance_scopes"]["attestation_source_truth_authenticated"] is False
    print(json.dumps({"scope": "synthetic fixture, not authenticated provider evidence",
                      "positive_t1_kry": receipt.kry_minted,
                      "product_gate": artifact["gates"]["product"],
                      "source_floor": artifact["savings_report"]["veracity"]["veracity_floor"]}, sort_keys=True))


@pytest.mark.parametrize("field", ["floor", "holdout", "provider", "self_total", "class_tier", "authenticated"])
def test_artifact_rejects_upgraded_imported_report_assurance(field, tmp_path, monkeypatch):
    sr = load("kry_savings_report")
    records = [{"id": "cache", "avoided_model": "gh/claude-opus-4.8", "cache_hit": True,
                "usage": {"completion_tokens": 500}}]
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps(records), encoding="utf-8")
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    art = load("kry_verified_artifact")
    report = art.kry_savings_report.analyze(records)
    if field == "floor":
        report["veracity"]["veracity_floor"] = 1.0
    elif field == "holdout":
        report["veracity"]["holdout_validated_kry"] = 1.0
    elif field == "provider":
        report["veracity"]["provider_metered_kry"] = 1.0
    elif field == "self_total":
        report["veracity"]["self_reported_kry"] -= 1
    elif field == "class_tier":
        next(iter(report["by_class"].values()))["tier"] = "provider_metered"
    else:
        report["source_assurance"]["authenticated"] = True
    monkeypatch.setattr(art.kry_savings_report, "analyze", lambda _: report)
    artifact = art.build_artifact(str(usage), attestation=str(att_path))
    checks = {row["name"]: row for row in artifact["gates"]["product"]["checks"]}
    assert checks["report_source_assurance_self_reported"]["pass"] is False
    assert artifact["ship_scope"] == "do_not_ship"


def test_finops_preserves_estimate_and_receipt_scope_separation(tmp_path, monkeypatch):
    from test_verified_artifact import _mint_explicit_fixture

    sr = load("kry_savings_report")
    records = [{"id": "cache", "avoided_model": "gh/claude-opus-4.8", "cache_hit": True,
                "usage": {"completion_tokens": 500}},
               {"id": "provider", "avoided_model": "gh/claude-opus-4.8",
                "served_model": "claude-sonnet-5",
                "usage": {"prompt_tokens": 2000, "completion_tokens": 400}}]
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps(records), encoding="utf-8")
    att_path = tmp_path / "attestation.json"
    _mint_explicit_fixture(sr, records, att_path)
    artifact = load("kry_verified_artifact").build_artifact(str(usage), attestation=str(att_path))
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    finops = load("kry_finops_report")
    report = finops.build_report(artifact_path, trust_local_inputs=True)
    assert report["ok"] is True
    assert report["estimated_savings_usd"] == report["retained_dollars"] == 0.0185
    assert report["amount_basis"] == "operator_supplied_log_list_price_estimate"
    assert report["veracity_floor"] == 0.0
    assert report["assurance_scopes"]["attestation_declared_veracity"]["veracity_floor"] == 0.3243
    assert report["assurance_scopes"]["attestation_source_truth_authenticated"] is False
    markdown = finops.render_markdown(report)
    assert "Monetary figures are usage-based estimates, not verified charges or retained cash." in markdown
    assert "This is usage-based estimation, not a statement of retained cash" in markdown
    assert "This is retained-dollars accounting" not in markdown
    assert "Estimated savings: $0.0185" in markdown
    assert "Imported-log source floor: 0.00%" in markdown
    assert "Declared receipt floor: 32.43%" in markdown
    assert "not actual charges or retained cash" in markdown


@pytest.fixture
def plain_sample(tmp_path):
    from kry import kry_mint as km

    records = [json.loads(line) for line in (ROOT / "examples/sample_usage_log.jsonl").read_text().splitlines()]
    usage = tmp_path / "usage.jsonl"
    usage.write_text("".join(json.dumps(row) + "\n" for row in records))
    att = tmp_path / "attestation.json"
    load("kry_savings_report")._mint_and_attest(records, str(att))
    return load("kry_verified_artifact"), usage, att, km._MINT_LOG_PATH


def test_plain_log_bundle_keeps_source_floor_zero_and_needs_no_t1(plain_sample, tmp_path):
    art, usage, att, log = plain_sample
    bundle = tmp_path / "bundle"
    report = art.write_bundle(bundle, str(usage), attestation=str(att), mint_log=str(log))
    assert report["ship_scope"] == "internal_or_demo_only"
    assert report["claim_allowed"]["external_verified_savings"] is False
    assert report["savings_report"]["veracity"]["veracity_floor"] == 0
    assert report["command_inputs"]["t1_manifest"] is None
    assert report["command_inputs"]["mint_log"] is None
    assert not (bundle / "t1_manifest.json").exists()
    assert not (bundle / log.name).exists()
    assert art.verify_artifact_file(bundle / "artifact.json")["ok"] is True


def test_explicit_empty_t1_manifest_request_remains_strict(plain_sample, tmp_path):
    art, _, _, log = plain_sample
    destination = tmp_path / "manifest.json"
    with pytest.raises(ValueError, match="at least one provider_metered"):
        art.write_t1_manifest(str(log), destination)
    assert not destination.exists()


def test_plain_log_with_oracle_cannot_claim_provider_reconciliation(plain_sample, tmp_path):
    art, usage, att, log = plain_sample
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]))
    report = art.write_bundle(tmp_path / "bundle", str(usage), attestation=str(att), mint_log=str(log),
                              provider_export=str(provider))
    assert report["ship_scope"] == "do_not_ship"
    assert report["claim_allowed"]["external_verified_savings"] is False
    assert report["research_assessment"]["reconcile"]["verdict"] == "RECONCILIATION_SOURCE_MISSING"


@pytest.mark.parametrize("content", ["not-json", '{"evidence_tier":"provider_metered","metered_tokens":null}'])
def test_bad_mint_input_is_not_treated_as_no_t1(plain_sample, tmp_path, content):
    art, usage, att, _ = plain_sample
    bad = tmp_path / "bad.jsonl"
    bad.write_text(content)
    with pytest.raises(ValueError):
        art.write_bundle(tmp_path / "bundle", str(usage), attestation=str(att), mint_log=str(bad))


def test_missing_mint_log_does_not_become_no_t1(plain_sample, tmp_path):
    art, usage, att, _ = plain_sample
    with pytest.raises(FileNotFoundError):
        art.write_bundle(tmp_path / "bundle", str(usage), attestation=str(att), mint_log=str(tmp_path / "missing"))


def test_invalid_window_is_still_rejected_for_t0_log(plain_sample, tmp_path):
    art, usage, att, log = plain_sample
    with pytest.raises(ValueError):
        art.write_bundle(tmp_path / "bundle", str(usage), attestation=str(att), mint_log=str(log), since=float("nan"))


def test_window_cannot_silently_remove_existing_t1(tmp_path):
    from test_verified_artifact import _mint_explicit_fixture
    from kry import kry_mint as km

    sr = load("kry_savings_report")
    records = sr._load_records(str(ROOT / "examples/sample_usage_log.jsonl"))
    att = tmp_path / "attestation.json"
    _mint_explicit_fixture(sr, records, att)
    art = load("kry_verified_artifact")
    with pytest.raises(ValueError, match="at least one provider_metered"):
        art.write_bundle(tmp_path / "bundle", str(ROOT / "examples/sample_usage_log.jsonl"),
                         attestation=str(att), mint_log=str(km._MINT_LOG_PATH), since=9999999999)
