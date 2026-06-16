"""Health checks for the local reviewer/verifier surface."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "scripts" / "kry_doctor.py"
SAVINGS_REPORT = ROOT / "scripts" / "kry_savings_report.py"
ARTIFACT = ROOT / "scripts" / "kry_verified_artifact.py"
SAMPLE = ROOT / "examples" / "sample_usage_log.jsonl"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _buyer_local_gates():
    return {
        "proof_required_reader_named": True,
        "provider_or_bill_data_named": True,
        "request_or_gateway_logs_named": True,
        "baseline_accepted": True,
        "authority_named": True,
        "quality_or_slo_named": True,
        "materiality_named": True,
        "seven_day_window_or_data_supplied": True,
    }


def _buyer_threshold_context():
    return {
        "proof_required_reader": "finance stakeholder for verified savings evidence",
        "provider_or_bill_data_source": "provider billing export for the reviewed window",
        "request_or_gateway_metadata_source": "gateway request metadata hash set without prompts or completions",
        "baseline_reference": "accepted measured/projected baseline memo",
        "authority_basis": "budget owner can approve a paid proof sprint",
        "quality_or_slo_boundary": "no SLO regression accepted for counted savings",
        "materiality_basis": ">=10% avoidable spend or >=$5k/month path",
        "sample_window": "seven-day sample window tied to provider export",
    }


def _buyer_materiality():
    return {
        "avoidable_spend_pct": 12.5,
        "plausible_monthly_savings_usd": 6000.0,
    }


def _outside_review_checks():
    return {
        "verify_artifact_command_run": True,
        "doctor_command_run": True,
        "claim_register_checked": True,
        "claim_evidence_manifest_checked": True,
        "finops_report_checked": True,
        "hash_bindings_checked": True,
        "template_schema_absent": True,
        "no_private_packet_material": True,
        "revocation_or_void_status_checked": True,
    }


def _outside_review_outputs():
    return {
        "verify_artifact_ok": True,
        "verify_artifact_error_count": 0,
        "doctor_fail_count": 0,
        "finops_report_rendered": True,
        "claim_register_external_verified_savings_allowed": True,
        "claim_register_tradeable_token_forbidden": True,
        "claim_evidence_manifest_complete": True,
        "no_invalid_revoked_or_voided_mints_known": True,
    }


def _legal_claim_checks():
    return {
        "external_claim_text_checked": True,
        "retained_dollars_language_checked": True,
        "credit_settlement_language_checked": True,
        "routing_permission_language_checked": True,
        "carbon_language_checked": True,
        "tradeable_token_disclaimer_checked": True,
        "non_transferable_scope_checked": True,
        "legal_limitations_recorded": True,
    }


def _non_bundled_usage_log(tmp_path: Path) -> Path:
    records = [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]
    records[0]["id"] = f"non-bundled-{records[0]['id']}"
    path = tmp_path / "usage.jsonl"
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n", encoding="utf-8")
    return path


def _isolated_mint(monkeypatch, tmp_path):
    import kry.kry_attest as ka
    import kry.kry_mint as km
    import kry.kry_token as kt

    log = tmp_path / "mint.jsonl"
    monkeypatch.setattr(km, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(ka, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(kt, "_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(km, "_DECAY_STATE_PATH", tmp_path / "decay.json")
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = "0" * 64
    km._evidence_mints = {}
    km._decay_loaded = True
    kt._ledger_instance = kt.KRYLedger()
    return log


def _record_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _provider_export_manifest(tmp_path, art, provider, t1_manifest):
    manifest = tmp_path / "provider_export_manifest.json"
    manifest.write_text(json.dumps({
        "schema": "kry_provider_export_manifest/v1",
        "provider": "OpenRouter",
        "export_source": "provider generation API export",
        "export_reference": "export-ref-2026-06-09",
        "date": "2026-06-09",
        "non_synthetic": True,
        "reconciliation_mode": "per-request",
        "provider_record_count": 1,
        "collection_window": {"since": "2026-06-09T00:00:00Z", "until": "2026-06-09T01:00:00Z"},
        "artifact_inputs": {
            "provider_export_sha256": art._hash_file(str(provider))["sha256"],
            "t1_manifest_sha256": art._hash_file(str(t1_manifest))["sha256"],
        },
    }), encoding="utf-8")
    return manifest


def _corpus_manifest(tmp_path, art, usage_log, provider, provider_export_manifest, t1_manifest):
    manifest = tmp_path / "corpus_manifest.json"
    window = {"since": "2026-06-09T00:00:00Z", "until": "2026-06-09T01:00:00Z"}
    record_count = _record_count(usage_log)
    manifest.write_text(json.dumps({
        "schema": "kry_corpus_manifest/v1",
        "corpus": "real",
        "date": "2026-06-09",
        "source": "real provider gateway export",
        "source_reference": "usage-export-ref-2026-06-09",
        "non_synthetic": True,
        "record_count": record_count,
        "collection_window": window,
        "validation_plan": {
            "schema": "kry_validation_plan/v1",
            "registered_date": "2026-06-09",
            "provider": "OpenRouter",
            "reconciliation_mode": "per-request",
            "tolerance": 0,
            "tolerance_pct": 5.0,
            "min_provider_records": 1,
            "min_usage_records": record_count,
            "min_independent_agreement": 0.8,
            "collection_window": window,
            "outside_review_required": True,
            "buyer_feedback_required": True,
            "legal_review_required": True,
            "kill_criteria": list(art.REQUIRED_VALIDATION_KILL_CRITERIA),
        },
        "artifact_inputs": {
            "usage_log_sha256": art._hash_file(str(usage_log))["sha256"],
            "provider_export_sha256": art._hash_file(str(provider))["sha256"],
            "provider_export_manifest_sha256": art._hash_file(str(provider_export_manifest))["sha256"],
            "t1_manifest_sha256": art._hash_file(str(t1_manifest))["sha256"],
        },
    }), encoding="utf-8")
    return manifest


def _review_files(tmp_path, art, usage_log, att_path, provider, corpus_manifest, provider_export_manifest, t1_manifest):
    input_hashes = {
        "usage_log": art._hash_file(str(usage_log)),
        "attestation": art._hash_file(str(att_path)),
        "provider_export": art._hash_file(str(provider)),
        "corpus_manifest": art._hash_file(str(corpus_manifest)),
        "t1_manifest": art._hash_file(str(t1_manifest)),
        "provider_export_manifest": art._hash_file(str(provider_export_manifest)),
        "tool_manifest": {"sha256": art._tool_manifest()["sha256"]},
    }
    review_basis = art._review_basis(
        input_hashes,
        corpus="real",
        mode="per-request",
        tolerance=0,
        tolerance_pct=5.0,
        since=None,
        until=None,
        replay_pass_rate=1.0,
    )
    bindings = {
        "usage_log_sha256": input_hashes["usage_log"]["sha256"],
        "attestation_sha256": input_hashes["attestation"]["sha256"],
        "provider_export_sha256": input_hashes["provider_export"]["sha256"],
        "corpus_manifest_sha256": input_hashes["corpus_manifest"]["sha256"],
        "t1_manifest_sha256": input_hashes["t1_manifest"]["sha256"],
        "provider_export_manifest_sha256": input_hashes["provider_export_manifest"]["sha256"],
        "tool_manifest_sha256": input_hashes["tool_manifest"]["sha256"],
        "review_basis_sha256": review_basis["sha256"],
    }
    outside = tmp_path / "outside_review.json"
    buyer = tmp_path / "buyer_feedback.json"
    legal = tmp_path / "legal_review.json"
    outside.write_text(json.dumps({
        "schema": "kry_external_evidence/v1",
        "kind": "outside_review",
        "date": "2026-06-09",
        "evidence_source": "signed reviewer note",
        "evidence_reference": "review-note-2026-06-09",
        "reviewer": "independent reviewer",
        "independent": True,
        "verdict": "verified",
        "reviewer_artifact_checks": _outside_review_checks(),
        "reviewer_command_outputs": _outside_review_outputs(),
        "reviewed_claims": ["external_verified_savings"],
        "artifact_inputs": bindings,
    }), encoding="utf-8")
    buyer.write_text(json.dumps({
        "schema": "kry_external_evidence/v1",
        "kind": "buyer_feedback",
        "date": "2026-06-09",
        "evidence_source": "buyer call notes",
        "evidence_reference": "buyer-note-2026-06-09",
        "buyer": "ExampleCo platform buyer",
        "buyer_role": "AI FinOps buyer",
        "verdict": "qualified_interest",
        "buyer_local_evidence_gates": _buyer_local_gates(),
        "buyer_threshold_context": _buyer_threshold_context(),
        "buyer_materiality": _buyer_materiality(),
        "reviewed_claims": ["external_verified_savings"],
        "artifact_inputs": bindings,
    }), encoding="utf-8")
    legal.write_text(json.dumps({
        "schema": "kry_external_evidence/v1",
        "kind": "legal_review",
        "date": "2026-06-09",
        "evidence_source": "claims counsel memo",
        "evidence_reference": "legal-memo-2026-06-09",
        "reviewer": "claims counsel",
        "verdict": "approved_with_limits",
        "external_claim_allowed": True,
        "tradeable_token_disclaimed": True,
        "legal_limitations": ["external use limited to claim-register wording and current artifact ship_scope"],
        "legal_claim_checks": _legal_claim_checks(),
        "reviewed_claims": ["external_verified_savings", "tradeable_token"],
        "artifact_inputs": bindings,
    }), encoding="utf-8")
    return outside, buyer, legal


def test_doctor_reports_local_surface_without_failing():
    doctor = _load(DOCTOR, "kry_doctor_static")

    result = doctor.run_checks(ROOT)

    assert result["schema"] == "kry_doctor/v1"
    assert result["summary"]["fail"] == 0
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["python_version"]["status"] == "PASS"
    assert checks["pytest_pythonpath"]["status"] == "PASS"
    assert checks["stdlib_verifier_independent"]["status"] == "PASS"
    assert checks["verified_artifact_surface"]["status"] == "PASS"
    assert checks["external_evidence_status"]["status"] == "WARN"
    assert "not inspected" in checks["external_evidence_status"]["detail"]
    assert "real provider export" in checks["external_evidence_status"]["detail"]


def test_doctor_can_verify_saved_artifact(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_test")
    doctor = _load(DOCTOR, "kry_doctor_artifact")
    records = [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    packet = art.build_artifact(str(SAMPLE), attestation=str(att_path), mint_log=str(log))
    out = tmp_path / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = doctor.run_checks(ROOT, artifact=str(out), trust_local_inputs=True)

    assert result["summary"]["fail"] == 0
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "PASS"
    assert "ship_scope=internal_or_demo_only" in checks["artifact_verification"]["detail"]
    assert checks["artifact_ship_scope_status"]["status"] == "WARN"
    assert "internal_or_demo_only" in checks["artifact_ship_scope_status"]["detail"]
    assert checks["packet_report_current"]["status"] == "WARN"
    assert "finops_report.md missing" in checks["packet_report_current"]["detail"]
    assert checks["packet_checklist_current"]["status"] == "WARN"
    assert "reviewer_checklist.json missing" in checks["packet_checklist_current"]["detail"]
    assert checks["external_evidence_status"]["status"] == "WARN"
    assert "artifact is not externally claimable" in checks["external_evidence_status"]["detail"]
    assert "science:provider_export_supplied" in checks["external_evidence_status"]["detail"]


def test_doctor_fails_valid_do_not_ship_artifact(tmp_path):
    art = _load(ARTIFACT, "kry_verified_artifact_for_do_not_ship_doctor_test")
    doctor = _load(DOCTOR, "kry_doctor_do_not_ship_artifact")
    packet = art.build_artifact(str(SAMPLE), attestation=None, mint_log=None)
    out = tmp_path / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = doctor.run_checks(ROOT, artifact=str(out), trust_local_inputs=True)

    assert result["summary"]["fail"] == 1
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "PASS"
    assert "ship_scope=do_not_ship" in checks["artifact_verification"]["detail"]
    assert checks["artifact_ship_scope_status"]["status"] == "FAIL"
    assert "do not hand off" in checks["artifact_ship_scope_status"]["detail"]


def test_doctor_reports_external_candidate_from_verified_claim_register(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_external")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_external")
    doctor = _load(DOCTOR, "kry_doctor_external")
    usage_log = _non_bundled_usage_log(tmp_path)
    records = [json.loads(line) for line in usage_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(tmp_path, art, usage_log, provider, provider_export_manifest, t1_manifest)
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, provider_export_manifest, t1_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        t1_manifest=str(t1_manifest),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )

    result = doctor.run_checks(ROOT, artifact=str(bundle / "artifact.json"))

    assert result["summary"]["fail"] == 0
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "PASS"
    assert "ship_scope=external_verified_savings_candidate" in checks["artifact_verification"]["detail"]
    assert checks["artifact_ship_scope_status"]["status"] == "PASS"
    assert "external_verified_savings_candidate" in checks["artifact_ship_scope_status"]["detail"]
    assert checks["packet_report_current"]["status"] == "PASS"
    assert checks["packet_checklist_current"]["status"] == "PASS"
    assert checks["packet_privacy_boundary"]["status"] == "PASS"
    assert checks["packet_input_portability"]["status"] == "PASS"
    assert checks["external_evidence_status"]["status"] == "PASS"
    assert "claim_register allows external_verified_savings" in checks["external_evidence_status"]["detail"]
    assert "does not certify upstream evidence truth" in checks["external_evidence_status"]["detail"]


def test_doctor_fails_external_candidate_without_portable_packet(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_external_bare")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_external_bare")
    doctor = _load(DOCTOR, "kry_doctor_external_bare")
    usage_log = _non_bundled_usage_log(tmp_path)
    records = [json.loads(line) for line in usage_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(tmp_path, art, usage_log, provider, provider_export_manifest, t1_manifest)
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, provider_export_manifest, t1_manifest,
    )
    packet = art.build_artifact(
        str(usage_log),
        attestation=str(att_path),
        t1_manifest=str(t1_manifest),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    artifact_dir = tmp_path / "artifact_only"
    artifact_dir.mkdir()
    out = artifact_dir / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = doctor.run_checks(ROOT, artifact=str(out))

    assert result["summary"]["fail"] == 4
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "FAIL"
    assert "command_inputs.usage_log must be relative" in checks["artifact_verification"]["detail"]
    assert "artifact_ship_scope_status" not in checks
    assert checks["packet_report_current"]["status"] == "FAIL"
    assert "finops_report.md missing from packet" in checks["packet_report_current"]["detail"]
    assert checks["packet_checklist_current"]["status"] == "FAIL"
    assert "reviewer_checklist.json missing from packet" in checks["packet_checklist_current"]["detail"]
    assert checks["packet_privacy_boundary"]["status"] == "PASS"
    assert checks["packet_input_portability"]["status"] == "FAIL"
    assert "usage_log is absolute" in checks["packet_input_portability"]["detail"]
    assert checks["external_evidence_status"]["status"] == "WARN"
    assert "artifact did not verify" in checks["external_evidence_status"]["detail"]


def test_doctor_detects_stale_packet_report(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_bundle")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_bundle")
    doctor = _load(DOCTOR, "kry_doctor_packet_report")
    records = [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    bundle = tmp_path / "packet"
    art.write_bundle(bundle, str(SAMPLE), attestation=str(att_path), mint_log=str(log))

    result = doctor.run_checks(ROOT, artifact=str(bundle / "artifact.json"))

    assert result["summary"]["fail"] == 0
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "PASS"
    assert checks["packet_report_current"]["status"] == "PASS"
    assert checks["packet_checklist_current"]["status"] == "PASS"
    assert checks["packet_privacy_boundary"]["status"] == "PASS"
    assert checks["packet_input_portability"]["status"] == "PASS"

    (bundle / "finops_report.md").write_text(
        "# KRY Retained-Dollars Report\n\nExternal verified-savings claim: ALLOWED AS CANDIDATE\n"
    , encoding="utf-8")

    result = doctor.run_checks(ROOT, artifact=str(bundle / "artifact.json"))

    assert result["summary"]["fail"] == 1
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "PASS"
    assert checks["packet_report_current"]["status"] == "FAIL"
    assert checks["packet_checklist_current"]["status"] == "PASS"
    assert checks["packet_privacy_boundary"]["status"] == "PASS"
    assert checks["packet_input_portability"]["status"] == "PASS"
    assert "does not match" in checks["packet_report_current"]["detail"]


def test_doctor_fails_packet_missing_derived_surfaces(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_missing_surfaces")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_missing_surfaces")
    doctor = _load(DOCTOR, "kry_doctor_packet_missing_surfaces")
    records = [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    bundle = tmp_path / "packet"
    art.write_bundle(bundle, str(SAMPLE), attestation=str(att_path), mint_log=str(log))
    (bundle / "finops_report.md").unlink()
    (bundle / "reviewer_checklist.json").unlink()

    result = doctor.run_checks(ROOT, artifact=str(bundle / "artifact.json"))

    assert result["summary"]["fail"] == 2
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "PASS"
    assert checks["packet_report_current"]["status"] == "FAIL"
    assert checks["packet_checklist_current"]["status"] == "FAIL"
    assert checks["packet_privacy_boundary"]["status"] == "PASS"
    assert checks["packet_input_portability"]["status"] == "PASS"
    assert "finops_report.md missing from packet" in checks["packet_report_current"]["detail"]
    assert "reviewer_checklist.json missing from packet" in checks["packet_checklist_current"]["detail"]


def test_doctor_detects_stale_packet_checklist(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_checklist")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_checklist")
    doctor = _load(DOCTOR, "kry_doctor_packet_checklist")
    records = [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    bundle = tmp_path / "packet"
    art.write_bundle(bundle, str(SAMPLE), attestation=str(att_path), mint_log=str(log))

    checklist_path = bundle / "reviewer_checklist.json"
    checklist = json.loads(checklist_path.read_text(encoding="utf-8"))
    checklist["claim_checks"][0]["required_status_for_external_claim"] = "blocked"
    checklist_path.write_text(json.dumps(checklist, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = doctor.run_checks(ROOT, artifact=str(bundle / "artifact.json"))

    assert result["summary"]["fail"] == 1
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "PASS"
    assert checks["packet_report_current"]["status"] == "PASS"
    assert checks["packet_checklist_current"]["status"] == "FAIL"
    assert checks["packet_privacy_boundary"]["status"] == "PASS"
    assert checks["packet_input_portability"]["status"] == "PASS"
    assert "reviewer_checklist.json does not match" in checks["packet_checklist_current"]["detail"]


def test_doctor_detects_private_mint_log_in_packet(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_privacy")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_privacy")
    doctor = _load(DOCTOR, "kry_doctor_packet_privacy")
    records = [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    bundle = tmp_path / "packet"
    art.write_bundle(bundle, str(SAMPLE), attestation=str(att_path), mint_log=str(log))
    (bundle / "kry_mint_log.jsonl").write_text(log.read_text(), encoding="utf-8")

    result = doctor.run_checks(ROOT, artifact=str(bundle / "artifact.json"))

    assert result["summary"]["fail"] == 1
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "PASS"
    assert checks["packet_report_current"]["status"] == "PASS"
    assert checks["packet_checklist_current"]["status"] == "PASS"
    assert checks["packet_privacy_boundary"]["status"] == "FAIL"
    assert checks["packet_input_portability"]["status"] == "PASS"
    assert "private ledger/mint-log material" in checks["packet_privacy_boundary"]["detail"]
    assert "kry_mint_log.jsonl" in checks["packet_privacy_boundary"]["detail"]


def test_doctor_detects_symlink_in_packet(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_symlink")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_symlink")
    doctor = _load(DOCTOR, "kry_doctor_packet_symlink")
    records = [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    bundle = tmp_path / "packet"
    art.write_bundle(bundle, str(SAMPLE), attestation=str(att_path), mint_log=str(log))
    try:
        (bundle / "linked_report.md").symlink_to("finops_report.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this filesystem")

    result = doctor.run_checks(ROOT, artifact=str(bundle / "artifact.json"))

    assert result["summary"]["fail"] == 1
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "PASS"
    assert checks["packet_report_current"]["status"] == "PASS"
    assert checks["packet_checklist_current"]["status"] == "PASS"
    assert checks["packet_privacy_boundary"]["status"] == "FAIL"
    assert checks["packet_input_portability"]["status"] == "PASS"
    assert "symlink present in packet" in checks["packet_privacy_boundary"]["detail"]
    assert "linked_report.md" in checks["packet_privacy_boundary"]["detail"]


def test_doctor_detects_unbound_directory_in_packet(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_unbound_directory")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_unbound_directory")
    doctor = _load(DOCTOR, "kry_doctor_packet_unbound_directory")
    records = [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    bundle = tmp_path / "packet"
    art.write_bundle(bundle, str(SAMPLE), attestation=str(att_path), mint_log=str(log))
    (bundle / "extra_evidence").mkdir()

    result = doctor.run_checks(ROOT, artifact=str(bundle / "artifact.json"))

    assert result["summary"]["fail"] == 1
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "PASS"
    assert checks["packet_report_current"]["status"] == "PASS"
    assert checks["packet_checklist_current"]["status"] == "PASS"
    assert checks["packet_privacy_boundary"]["status"] == "FAIL"
    assert checks["packet_input_portability"]["status"] == "PASS"
    assert "unbound directory present in packet" in checks["packet_privacy_boundary"]["detail"]
    assert "extra_evidence" in checks["packet_privacy_boundary"]["detail"]


def test_doctor_detects_private_usage_content_in_packet(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_usage_privacy")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_usage_privacy")
    doctor = _load(DOCTOR, "kry_doctor_packet_usage_privacy")
    usage_log = _non_bundled_usage_log(tmp_path)
    records = [json.loads(line) for line in usage_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(tmp_path, art, usage_log, provider, provider_export_manifest, t1_manifest)
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, provider_export_manifest, t1_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        t1_manifest=str(t1_manifest),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    bundled_usage = bundle / "usage_log.jsonl"
    bundled_records = [json.loads(line) for line in bundled_usage.read_text(encoding="utf-8").splitlines() if line.strip()]
    bundled_records[0]["request_body"] = {
        "messages": [{"role": "user", "content": "private prompt text"}],
    }
    bundled_usage.write_text("\n".join(json.dumps(record, sort_keys=True) for record in bundled_records) + "\n", encoding="utf-8")

    result = doctor.run_checks(ROOT, artifact=str(bundle / "artifact.json"))

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["packet_privacy_boundary"]["status"] == "FAIL"
    assert "private prompt/message/raw-body material" in checks["packet_privacy_boundary"]["detail"]
    assert "usage log contains private field $[0].request_body" in checks["packet_privacy_boundary"]["detail"]


def test_doctor_detects_private_provider_content_in_packet(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_provider_privacy")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_provider_privacy")
    doctor = _load(DOCTOR, "kry_doctor_packet_provider_privacy")
    usage_log = _non_bundled_usage_log(tmp_path)
    records = [json.loads(line) for line in usage_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(tmp_path, art, usage_log, provider, provider_export_manifest, t1_manifest)
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, provider_export_manifest, t1_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        t1_manifest=str(t1_manifest),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    (bundle / "provider_export.json").write_text(json.dumps([{
        "prompt_tokens": 2000,
        "completion_tokens": 400,
        "raw_response": {"content": "private response text"},
    }]), encoding="utf-8")

    result = doctor.run_checks(ROOT, artifact=str(bundle / "artifact.json"))

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["packet_privacy_boundary"]["status"] == "FAIL"
    assert "private prompt/message/raw-body material" in checks["packet_privacy_boundary"]["detail"]
    assert "provider export contains private field $[0].raw_response" in checks["packet_privacy_boundary"]["detail"]


def test_doctor_detects_private_public_manifest_content_in_packet(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_manifest_privacy")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_manifest_privacy")
    doctor = _load(DOCTOR, "kry_doctor_packet_manifest_privacy")
    usage_log = _non_bundled_usage_log(tmp_path)
    records = [json.loads(line) for line in usage_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(tmp_path, art, usage_log, provider, provider_export_manifest, t1_manifest)
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, provider_export_manifest, t1_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        t1_manifest=str(t1_manifest),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    corpus_path = bundle / "corpus_manifest.json"
    corpus_data = json.loads(corpus_path.read_text(encoding="utf-8"))
    corpus_data["raw_response_body"] = {"content": "private corpus note content"}
    corpus_path.write_text(json.dumps(corpus_data), encoding="utf-8")

    result = doctor.run_checks(ROOT, artifact=str(bundle / "artifact.json"))

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["packet_privacy_boundary"]["status"] == "FAIL"
    assert "private prompt/message/raw-body material" in checks["packet_privacy_boundary"]["detail"]
    assert "corpus_manifest contains private field $.raw_response_body" in (
        checks["packet_privacy_boundary"]["detail"]
    )


def test_doctor_detects_private_review_content_in_packet(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_review_privacy")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_review_privacy")
    doctor = _load(DOCTOR, "kry_doctor_packet_review_privacy")
    usage_log = _non_bundled_usage_log(tmp_path)
    records = [json.loads(line) for line in usage_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(tmp_path, art, usage_log, provider, provider_export_manifest, t1_manifest)
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, provider_export_manifest, t1_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        t1_manifest=str(t1_manifest),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    buyer_path = bundle / "buyer_feedback.json"
    buyer_data = json.loads(buyer_path.read_text(encoding="utf-8"))
    buyer_data["raw_response_body"] = {"content": "private buyer note content"}
    buyer_path.write_text(json.dumps(buyer_data), encoding="utf-8")

    result = doctor.run_checks(ROOT, artifact=str(bundle / "artifact.json"))

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["packet_privacy_boundary"]["status"] == "FAIL"
    assert "private prompt/message/raw-body material" in checks["packet_privacy_boundary"]["detail"]
    assert "buyer_feedback evidence contains private field $.raw_response_body" in (
        checks["packet_privacy_boundary"]["detail"]
    )


def test_doctor_detects_nonportable_packet_absolute_inputs(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_doctor_nonportable")
    art = _load(ARTIFACT, "kry_verified_artifact_for_doctor_nonportable")
    doctor = _load(DOCTOR, "kry_doctor_packet_nonportable")
    records = [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    packet = art.build_artifact(str(SAMPLE), attestation=str(att_path), mint_log=str(log))
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    out = packet_dir / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checklist_inputs = dict(packet["review_basis"]["inputs"])
    checklist_inputs["review_basis_sha256"] = packet["review_basis"]["sha256"]
    checklist = art._reviewer_checklist(
        checklist_inputs,
        artifact_path="artifact.json",
        artifact_hash=packet["artifact_hash"],
    )
    (packet_dir / "reviewer_checklist.json").write_text(json.dumps(checklist, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finops = _load(ROOT / "scripts" / "kry_finops_report.py", "kry_finops_report_for_doctor_nonportable")
    report = finops.build_report(out, display_artifact_path=out.name, trust_local_inputs=True)
    (packet_dir / "finops_report.md").write_text(finops.render_markdown(report), encoding="utf-8")

    result = doctor.run_checks(ROOT, artifact=str(out), trust_local_inputs=True)

    assert result["summary"]["fail"] == 1
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "PASS"
    assert checks["packet_report_current"]["status"] == "PASS"
    assert checks["packet_checklist_current"]["status"] == "PASS"
    assert checks["packet_privacy_boundary"]["status"] == "PASS"
    assert checks["packet_input_portability"]["status"] == "FAIL"
    assert "usage_log is absolute" in checks["packet_input_portability"]["detail"]


def test_doctor_returns_nonzero_for_missing_artifact(tmp_path):
    doctor = _load(DOCTOR, "kry_doctor_missing_artifact")
    missing = tmp_path / "missing.json"

    result = doctor.run_checks(ROOT, artifact=str(missing))

    assert result["summary"]["fail"] == 1
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "FAIL"
    assert str(missing) in checks["artifact_verification"]["detail"]


def test_doctor_rejects_nonstandard_json_artifact(tmp_path):
    doctor = _load(DOCTOR, "kry_doctor_strict_json_artifact")
    artifact = tmp_path / "artifact.json"
    artifact.write_text('{"schema":"kry_verified_savings_artifact/v1","bad":NaN}\n', encoding="utf-8")

    result = doctor.run_checks(ROOT, artifact=str(artifact))

    assert result["summary"]["fail"] >= 1
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["artifact_verification"]["status"] == "FAIL"
    assert "non-standard JSON constant rejected: NaN" in checks["artifact_verification"]["detail"]
    assert doctor._load_artifact_json(artifact) is None
    with pytest.raises(ValueError, match="Out of range float values"):
        doctor._json_pretty({"bad": float("nan")})
