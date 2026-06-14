"""Buyer-facing retained-dollars report must stay tied to verified artifacts."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FINOPS = ROOT / "scripts" / "kry_finops_report.py"
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


def _minted_artifact(monkeypatch, tmp_path):
    log = _isolated_mint(monkeypatch, tmp_path)
    sr = _load(SAVINGS_REPORT, "kry_savings_report_for_finops")
    art = _load(ARTIFACT, "kry_verified_artifact_for_finops")
    usage_log = _non_bundled_usage_log(tmp_path)
    records = [json.loads(line) for line in usage_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    return art, usage_log, att_path, log


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
    record_count = sum(1 for line in Path(usage_log).read_text(encoding="utf-8").splitlines() if line.strip())
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


def test_finops_report_blocks_external_claim_for_demo_artifact(monkeypatch, tmp_path):
    art, usage_log, att_path, log = _minted_artifact(monkeypatch, tmp_path)
    finops = _load(FINOPS, "kry_finops_report_demo")
    packet = art.build_artifact(str(usage_log), attestation=str(att_path), mint_log=str(log))
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = finops.build_report(artifact_path)
    md = finops.render_markdown(report)

    assert report["ok"] is True
    assert report["ship_scope"] == "internal_or_demo_only"
    assert report["external_verified_savings"]["status"] == "blocked"
    assert report["gate_summary"]["product"]["status"] == "PASS"
    assert report["gate_summary"]["science"]["status"] == "FAIL"
    assert report["gate_summary"]["external_review"]["status"] == "FAIL"
    assert report["gate_summary"]["kill"]["status"] == "CLEAR"
    assert report["gate_summary"]["kill"]["triggers"] == []
    assert report["claim_evidence_manifest"]["schema"] == "kry_claim_evidence_manifest/v1"
    assert report["claim_evidence_manifest"]["ship_scope"] == "internal_or_demo_only"
    assert report["claim_evidence_manifest"]["artifact_hash"] == report["artifact_hash"]
    assert report["claim_evidence_manifest"]["claim_count"] == 8
    manifest_claims = {
        claim["id"]: claim
        for claim in report["claim_evidence_manifest"]["claims"]
    }
    assert manifest_claims["internal_efficiency_artifact"]["status"] == "allowed"
    assert manifest_claims["external_verified_savings"]["status"] == "blocked"
    assert manifest_claims["external_verified_savings"]["blocker_count"] > 0
    assert manifest_claims["tradeable_token"]["status"] == "forbidden"
    assert report["validation_plan"]["schema"] is None
    assert report["validation_plan"]["provider"] is None
    assert report["t1_attestation_binding"]["schema"] is None
    assert report["t1_attestation_binding"]["matched_links"] is None
    assert report["evidence_provenance"]["provider_export"]["reference"] is None
    assert report["evidence_provenance"]["outside_review"]["verdict"] is None
    assert report["buyer_materiality"]["avoidable_spend_pct"] is None
    assert report["buyer_materiality"]["plausible_monthly_savings_usd"] is None
    assert report["verification"]["doctor_command"].endswith(f"--artifact {artifact_path}")
    assert "External verified-savings claim: BLOCKED" in md
    assert "science:provider_export_supplied" in md
    assert "## Gate Summary" in md
    assert "- Product gate: PASS" in md
    assert "- Science gate: FAIL" in md
    assert "- External-review gate: FAIL" in md
    assert "- Kill gate: CLEAR" in md
    assert "- Kill triggers: none" in md
    assert "## Claim Evidence Manifest" in md
    assert "- Schema: kry_claim_evidence_manifest/v1" in md
    assert f"- Artifact binding: artifact.json @ {report['artifact_hash']}" in md
    assert "- Ship scope: internal_or_demo_only" in md
    assert "- Claims: 8 total; " in md
    assert "- external_verified_savings: blocked; evidence refs 5; blockers " in md
    assert "- tradeable_token: forbidden; evidence refs 2; blockers 0" in md
    assert "## Validation Plan" in md
    assert "- Schema: unavailable" in md
    assert "- Provider: unavailable" in md
    assert "- Collection window: unavailable" in md
    assert "- Tolerance: unavailable records; aggregate tolerance unavailable" in md
    assert "- Minimum independent agreement: unavailable" in md
    assert "## T1 Attestation Binding" in md
    assert "- Schema: unavailable" in md
    assert "- Source mint log SHA-256: unavailable" in md
    assert "- Manifest receipts: unavailable" in md
    assert "- Attestation provider-metered links: unavailable" in md
    assert "- Matched links: unavailable" in md
    assert "## Buyer Materiality" in md
    assert "- Avoidable spend threshold: unavailable / 10.00%" in md
    assert "- Monthly savings threshold: unavailable / $5,000.0000" in md
    assert "## Evidence Provenance" in md
    assert "- Provider export: unavailable; reference unavailable; date unavailable" in md
    assert "- Outside review: unavailable; reference unavailable; date unavailable; verdict unavailable" in md
    assert "Doctor command:" in md
    assert "ALLOWED AS CANDIDATE" not in md
    assert "not a tradeable token" in md


def test_finops_report_allows_only_candidate_language_for_full_packet(monkeypatch, tmp_path):
    art, usage_log, att_path, log = _minted_artifact(monkeypatch, tmp_path)
    finops = _load(FINOPS, "kry_finops_report_external")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(tmp_path, art, usage_log, provider, provider_export_manifest, t1_manifest)
    outside, buyer, legal = _review_files(tmp_path, art, usage_log, att_path, provider, corpus_manifest,
                                          provider_export_manifest, t1_manifest)
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
    artifact_path = bundle / "artifact.json"

    report = finops.build_report(artifact_path)
    md = finops.render_markdown(report)

    assert report["ok"] is True
    assert report["ship_scope"] == "external_verified_savings_candidate"
    assert report["gate_summary"]["product"]["status"] == "PASS"
    assert report["gate_summary"]["science"]["status"] == "PASS"
    assert report["gate_summary"]["external_review"]["status"] == "PASS"
    assert report["gate_summary"]["kill"]["status"] == "CLEAR"
    assert report["gate_summary"]["kill"]["triggers"] == []
    assert report["claim_evidence_manifest"]["schema"] == "kry_claim_evidence_manifest/v1"
    assert report["claim_evidence_manifest"]["ship_scope"] == "external_verified_savings_candidate"
    assert report["claim_evidence_manifest"]["artifact_hash"] == report["artifact_hash"]
    assert report["claim_evidence_manifest"]["claim_count"] == 8
    manifest_claims = {
        claim["id"]: claim
        for claim in report["claim_evidence_manifest"]["claims"]
    }
    assert manifest_claims["external_verified_savings"]["status"] == "allowed"
    assert manifest_claims["external_verified_savings"]["blocker_count"] == 0
    assert manifest_claims["tradeable_token"]["status"] == "forbidden"
    assert report["validation_plan"]["schema"] == "kry_validation_plan/v1"
    assert report["validation_plan"]["registered_date"] == "2026-06-09"
    assert report["validation_plan"]["provider"] == "OpenRouter"
    assert report["validation_plan"]["reconciliation_mode"] == "per-request"
    assert report["validation_plan"]["tolerance"] == 0
    assert report["validation_plan"]["tolerance_pct"] == 5.0
    assert report["validation_plan"]["min_provider_records"] == 1
    assert report["validation_plan"]["min_usage_records"] == 48
    assert report["validation_plan"]["min_independent_agreement"] == 0.8
    assert report["t1_attestation_binding"]["schema"] == "kry_t1_reconciliation_manifest/v1"
    assert report["t1_attestation_binding"]["source_mint_log_sha256"]
    assert report["t1_attestation_binding"]["manifest_receipts"] == 1
    assert report["t1_attestation_binding"]["attestation_provider_metered_links"] == 1
    assert report["t1_attestation_binding"]["matched_links"] == 1
    assert report["evidence_provenance"]["provider_export"]["subject"] == "OpenRouter"
    assert report["evidence_provenance"]["provider_export"]["reference"] == "export-ref-2026-06-09"
    assert report["evidence_provenance"]["corpus_manifest"]["reference"] == "usage-export-ref-2026-06-09"
    assert report["evidence_provenance"]["outside_review"]["verdict"] == "verified"
    assert report["evidence_provenance"]["buyer_feedback"]["subject"] == "ExampleCo platform buyer"
    assert report["evidence_provenance"]["legal_review"]["verdict"] == "approved_with_limits"
    assert report["buyer_materiality"]["avoidable_spend_pct"] == 12.5
    assert report["buyer_materiality"]["plausible_monthly_savings_usd"] == 6000.0
    assert "External verified-savings claim: ALLOWED AS CANDIDATE" in md
    assert "External verified-savings claim: ALLOWED\n" not in md
    assert "## Gate Summary" in md
    assert "- Product gate: PASS" in md
    assert "- Science gate: PASS" in md
    assert "- External-review gate: PASS" in md
    assert "- Kill gate: CLEAR" in md
    assert "- Kill triggers: none" in md
    assert "## Claim Evidence Manifest" in md
    assert "- Ship scope: external_verified_savings_candidate" in md
    assert "- Claims: 8 total; 7 allowed; 0 blocked; 1 forbidden; 0 blockers" in md
    assert "- external_verified_savings: allowed; evidence refs 5; blockers 0" in md
    assert "## Validation Plan" in md
    assert "- Schema: kry_validation_plan/v1" in md
    assert "- Registered date: 2026-06-09" in md
    assert "- Provider: OpenRouter" in md
    assert "- Reconciliation mode: per-request" in md
    assert "- Collection window: 2026-06-09T00:00:00Z to 2026-06-09T01:00:00Z" in md
    assert "- Tolerance: 0 records; aggregate tolerance 5.00%" in md
    assert "- Minimum provider records: 1" in md
    assert "- Minimum usage records: 48" in md
    assert "- Minimum independent agreement: 80.00%" in md
    assert "## T1 Attestation Binding" in md
    assert "- Schema: kry_t1_reconciliation_manifest/v1" in md
    assert "- Source mint log SHA-256: " in md
    assert "- Manifest receipts: 1" in md
    assert "- Attestation provider-metered links: 1" in md
    assert "- Matched links: 1" in md
    assert "## Buyer Materiality" in md
    assert "- Avoidable spend threshold: 12.50% / 10.00%" in md
    assert "- Monthly savings threshold: $6,000.0000 / $5,000.0000" in md
    assert "## Evidence Provenance" in md
    assert "- Provider export: OpenRouter via provider generation API export; reference export-ref-2026-06-09; date 2026-06-09" in md
    assert "- Corpus manifest: real via real provider gateway export; reference usage-export-ref-2026-06-09; date 2026-06-09" in md
    assert "- Outside review: independent reviewer via signed reviewer note; reference review-note-2026-06-09; date 2026-06-09; verdict verified" in md
    assert "- Buyer feedback: ExampleCo platform buyer via buyer call notes; reference buyer-note-2026-06-09; date 2026-06-09; verdict qualified_interest" in md
    assert "- Legal review: claims counsel via claims counsel memo; reference legal-memo-2026-06-09; date 2026-06-09; verdict approved_with_limits" in md
    assert f"python3 scripts/kry_doctor.py --artifact {artifact_path}" in md
    assert "Tradeable token: forbidden" in md


def test_finops_report_refuses_external_packet_missing_reviewer_surface(monkeypatch, tmp_path):
    art, usage_log, att_path, log = _minted_artifact(monkeypatch, tmp_path)
    finops = _load(FINOPS, "kry_finops_report_missing_reviewer_surface")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(tmp_path, art, usage_log, provider, provider_export_manifest, t1_manifest)
    outside, buyer, legal = _review_files(tmp_path, art, usage_log, att_path, provider, corpus_manifest,
                                          provider_export_manifest, t1_manifest)
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
    (bundle / "reviewer_checklist.json").unlink()

    report = finops.build_report(bundle / "artifact.json")
    md = finops.render_markdown(report)

    assert report["ok"] is False
    assert "reviewer_checklist.json missing from packet; bundle mode should generate it" in report["errors"]
    assert "Do not use this report" in md
    assert "ALLOWED AS CANDIDATE" not in md


def test_finops_report_refuses_unverified_artifact(monkeypatch, tmp_path):
    art, usage_log, att_path, log = _minted_artifact(monkeypatch, tmp_path)
    finops = _load(FINOPS, "kry_finops_report_tampered")
    packet = art.build_artifact(str(usage_log), attestation=str(att_path), mint_log=str(log))
    packet["ship_scope"] = "external_verified_savings_candidate"
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = finops.build_report(artifact_path)
    md = finops.render_markdown(report)

    assert report["ok"] is False
    assert "artifact_hash mismatch" in report["errors"]
    assert "Do not use this report" in md


def test_finops_report_rejects_nonstandard_json_constants(tmp_path):
    finops = _load(FINOPS, "kry_finops_report_strict_json")
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text('{"schema":"kry_verified_savings_artifact/v1","bad":NaN}\n', encoding="utf-8")

    report = finops.build_report(artifact_path)
    md = finops.render_markdown(report)

    assert report["ok"] is False
    assert report["errors"] == ["artifact unreadable: non-standard JSON constant rejected: NaN"]
    assert "Artifact verification failed" in md
    with pytest.raises(ValueError, match="Out of range float values"):
        finops._json_pretty({"bad": float("nan")})
