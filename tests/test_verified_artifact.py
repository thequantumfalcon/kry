"""Verified-savings artifact gate.

This pins the smallest buyer/reviewer packet: savings report + public attestation
+ provider oracle + review evidence, with explicit product/science/kill gates.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_ARTIFACT = _ROOT / "scripts" / "kry_verified_artifact.py"
_SR = _ROOT / "scripts" / "kry_savings_report.py"
_SAMPLE = _ROOT / "examples" / "sample_usage_log.jsonl"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_date_errors_rejects_same_day_future_timestamp(monkeypatch):
    art = _load(_ARTIFACT, "kry_verified_artifact_date_helper_future_timestamp")

    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
            return current if tz is not None else current.replace(tzinfo=None)

        @classmethod
        def fromisoformat(cls, text):
            return datetime.fromisoformat(text)

    monkeypatch.setattr(art, "datetime", FrozenDateTime)

    assert art._date_errors("2026-06-09") == []
    assert art._date_errors("2026-06-09T12:00:00Z") == []
    assert "date must not be in the future" in art._date_errors("2026-06-09T12:00:01Z")
    assert "date must not be in the future" in art._date_errors("2026-06-10")


def _non_bundled_usage_log(tmp_path: Path) -> Path:
    records = [json.loads(ln) for ln in _SAMPLE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    records[0]["id"] = f"non-bundled-{records[0]['id']}"
    path = tmp_path / "usage.jsonl"
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def minted_sample(tmp_path, monkeypatch):
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

    usage_log = _non_bundled_usage_log(tmp_path)
    sr = _load(_SR, "kry_savings_report_for_artifact_test")
    records = [json.loads(ln) for ln in usage_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    return usage_log, att_path, log


def _bindings(
    art,
    usage_log,
    att_path,
    provider,
    corpus_manifest=None,
    t1_manifest=None,
    provider_export_manifest=None,
    *,
    corpus="real",
    mode="per-request",
    tolerance=0,
    tolerance_pct=5.0,
    since=None,
    until=None,
    replay_pass_rate=1.0,
):
    input_hashes = {
        "usage_log": art._hash_file(str(usage_log)),
        "attestation": art._hash_file(str(att_path)),
        "provider_export": art._hash_file(str(provider)),
        "corpus_manifest": art._hash_file(str(corpus_manifest)) if corpus_manifest is not None else None,
        "t1_manifest": art._hash_file(str(t1_manifest)) if t1_manifest is not None else None,
        "provider_export_manifest": (
            art._hash_file(str(provider_export_manifest)) if provider_export_manifest is not None else None
        ),
        "tool_manifest": {"sha256": art._tool_manifest()["sha256"]},
    }
    review_basis = art._review_basis(
        input_hashes,
        corpus=corpus,
        mode=mode,
        tolerance=tolerance,
        tolerance_pct=tolerance_pct,
        since=since,
        until=until,
        replay_pass_rate=replay_pass_rate,
    )
    return {
        "usage_log_sha256": input_hashes["usage_log"]["sha256"],
        "attestation_sha256": input_hashes["attestation"]["sha256"],
        "provider_export_sha256": input_hashes["provider_export"]["sha256"],
        "corpus_manifest_sha256": (
            input_hashes["corpus_manifest"]["sha256"] if input_hashes["corpus_manifest"] else None
        ),
        "t1_manifest_sha256": input_hashes["t1_manifest"]["sha256"] if input_hashes["t1_manifest"] else None,
        "provider_export_manifest_sha256": (
            input_hashes["provider_export_manifest"]["sha256"] if input_hashes["provider_export_manifest"] else None
        ),
        "tool_manifest_sha256": input_hashes["tool_manifest"]["sha256"],
        "review_basis_sha256": review_basis["sha256"],
    }


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


def _corpus_manifest(
    tmp_path,
    art,
    usage_log,
    provider,
    record_count,
    t1_manifest=None,
    provider_export_manifest=None,
    collection_window=None,
    date="2026-06-09",
    source="real provider gateway export",
    source_reference="usage-export-ref-2026-06-09",
    provider_name="OpenRouter",
    mode="per-request",
    tolerance=0,
    tolerance_pct=5.0,
    min_provider_records=1,
    min_independent_agreement=0.8,
):
    manifest = tmp_path / "corpus_manifest.json"
    window = collection_window or {
        "since": "2026-06-09T00:00:00Z",
        "until": "2026-06-09T01:00:00Z",
    }
    artifact_inputs = {
        "usage_log_sha256": art._hash_file(str(usage_log))["sha256"],
        "provider_export_sha256": art._hash_file(str(provider))["sha256"],
    }
    if t1_manifest is not None:
        artifact_inputs["t1_manifest_sha256"] = art._hash_file(str(t1_manifest))["sha256"]
    if provider_export_manifest is not None:
        artifact_inputs["provider_export_manifest_sha256"] = art._hash_file(str(provider_export_manifest))["sha256"]
    manifest.write_text(json.dumps({
        "schema": "kry_corpus_manifest/v1",
        "corpus": "real",
        "date": date,
        "source": source,
        "source_reference": source_reference,
        "non_synthetic": True,
        "record_count": record_count,
        "collection_window": window,
        "validation_plan": {
            "schema": "kry_validation_plan/v1",
            "registered_date": date,
            "provider": provider_name,
            "reconciliation_mode": mode,
            "tolerance": tolerance,
            "tolerance_pct": tolerance_pct,
            "min_provider_records": min_provider_records,
            "min_usage_records": record_count,
            "min_independent_agreement": min_independent_agreement,
            "collection_window": window,
            "outside_review_required": True,
            "buyer_feedback_required": True,
            "legal_review_required": True,
            "kill_criteria": list(art.REQUIRED_VALIDATION_KILL_CRITERIA),
        },
        "artifact_inputs": artifact_inputs,
    }), encoding="utf-8")
    return manifest


def _provider_export_manifest(tmp_path, art, provider, t1_manifest, *, mode="per-request", count=1,
                              date="2026-06-09", provider_name="OpenRouter",
                              export_source="provider generation API export",
                              export_reference="export-ref-2026-06-09",
                              collection_window=None):
    manifest = tmp_path / "provider_export_manifest.json"
    window = collection_window or {
        "since": "2026-06-09T00:00:00Z",
        "until": "2026-06-09T01:00:00Z",
    }
    manifest.write_text(json.dumps({
        "schema": "kry_provider_export_manifest/v1",
        "provider": provider_name,
        "export_source": export_source,
        "export_reference": export_reference,
        "date": date,
        "non_synthetic": True,
        "reconciliation_mode": mode,
        "provider_record_count": count,
        "collection_window": window,
        "artifact_inputs": {
            "provider_export_sha256": art._hash_file(str(provider))["sha256"],
            "t1_manifest_sha256": art._hash_file(str(t1_manifest))["sha256"],
        },
    }), encoding="utf-8")
    return manifest


def _record_count(path):
    return sum(1 for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip())


def _claims(packet):
    return {claim["id"]: claim for claim in packet["claim_register"]["claims"]}


def _manifest_claims(packet):
    return {claim["id"]: claim for claim in packet["claim_evidence_manifest"]["claims"]}


def _manifest_fields(manifest_claim, key="evidence"):
    fields = []
    for item in manifest_claim[key]:
        fields.extend(item["artifact_fields"])
    return fields


def _review_files(
    tmp_path,
    art,
    usage_log,
    att_path,
    provider,
    corpus_manifest,
    t1_manifest=None,
    provider_export_manifest=None,
    *,
    corpus="real",
    mode="per-request",
    tolerance=0,
    tolerance_pct=5.0,
    since=None,
    until=None,
    replay_pass_rate=1.0,
):
    bindings = _bindings(
        art,
        usage_log,
        att_path,
        provider,
        corpus_manifest,
        t1_manifest,
        provider_export_manifest,
        corpus=corpus,
        mode=mode,
        tolerance=tolerance,
        tolerance_pct=tolerance_pct,
        since=since,
        until=until,
        replay_pass_rate=replay_pass_rate,
    )
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


def _placeholder_review_files(tmp_path):
    outside = tmp_path / "outside_review.md"
    buyer = tmp_path / "buyer_feedback.md"
    legal = tmp_path / "legal_review.md"
    outside.write_text("verified: checked attestation and provider export\n", encoding="utf-8")
    buyer.write_text("buyer: would evaluate a real-savings packet\n", encoding="utf-8")
    legal.write_text("legal: claims reviewed for closed-loop non-transferable posture\n", encoding="utf-8")
    return outside, buyer, legal


def test_synthetic_packet_stays_internal_demo_only(minted_sample):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_internal")

    packet = art.build_artifact(str(usage_log), attestation=str(att_path), mint_log=str(log))

    assert packet["gates"]["product"]["status"] == "PASS"
    assert packet["gates"]["science"]["status"] == "FAIL"
    assert packet["gates"]["kill"]["status"] == "CLEAR"
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert packet["claim_allowed"]["internal_efficiency_artifact"] is True
    assert packet["claim_allowed"]["external_verified_savings"] is False
    claims = _claims(packet)
    assert packet["claim_register"]["schema"] == "kry_claim_register/v1"
    assert claims["internal_efficiency_artifact"]["status"] == "allowed"
    assert claims["external_verified_savings"]["status"] == "blocked"
    assert claims["provider_reconciled"]["status"] == "blocked"
    assert claims["real_corpus_validated"]["status"] == "blocked"
    assert claims["external_review_complete"]["status"] == "blocked"
    assert claims["tradeable_token"]["status"] == "forbidden"
    legal_summary = packet["review_evidence"]["legal_review"]["summary"]
    assert legal_summary["kind"] == "legal_review"
    assert legal_summary["external_claim_allowed"] is None
    assert legal_summary["tradeable_token_disclaimed"] is None
    assert packet["claim_evidence_manifest"]["schema"] == "kry_claim_evidence_manifest/v1"
    assert packet["claim_evidence_manifest"]["artifact"]["path"] == "artifact.json"
    assert packet["claim_evidence_manifest"]["artifact"]["artifact_hash"] == packet["artifact_hash"]
    assert packet["claim_evidence_manifest"]["verify_command"].endswith("--verify-artifact artifact.json")
    manifest_claims = _manifest_claims(packet)
    assert set(manifest_claims) == set(claims)
    assert manifest_claims["external_verified_savings"]["status"] == claims["external_verified_savings"]["status"]
    external_fields = _manifest_fields(manifest_claims["external_verified_savings"])
    assert "/gates/product" in external_fields
    assert "/gates/science" in external_fields
    assert "/gates/external_review" in external_fields
    external_blocker_fields = _manifest_fields(manifest_claims["external_verified_savings"], key="blockers")
    assert "/gates/science" in external_blocker_fields
    assert "/gates/external_review" in external_blocker_fields
    tradeable_fields = _manifest_fields(manifest_claims["tradeable_token"])
    assert "/review_evidence/legal_review/summary/tradeable_token_disclaimed" in tradeable_fields
    assert "science:provider_export_supplied" in packet["external_blockers"]
    assert "science:corpus_declared_real" in packet["external_blockers"]


def test_bundled_sample_cannot_be_declared_real_for_external_candidate(tmp_path, monkeypatch):
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

    sr = _load(_SR, "kry_savings_report_for_bundled_sample_block")
    art = _load(_ARTIFACT, "kry_verified_artifact_bundled_sample_block")
    records = [json.loads(ln) for ln in _SAMPLE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    att_path = tmp_path / "attestation.json"
    sr._mint_and_attest(records, str(att_path))
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, _SAMPLE, provider, record_count=_record_count(_SAMPLE),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, _SAMPLE, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )

    packet = art.build_artifact(
        str(_SAMPLE),
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

    sample_check = [c for c in packet["gates"]["science"]["checks"]
                    if c["name"] == "real_corpus_not_bundled_sample"][0]
    assert sample_check["pass"] is False
    assert "sample_usage_log.jsonl is synthetic" in sample_check["detail"]
    assert packet["gates"]["science"]["status"] == "FAIL"
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert _claims(packet)["external_verified_savings"]["status"] == "blocked"
    assert "science:real_corpus_not_bundled_sample" in packet["external_blockers"]


def test_full_packet_can_be_external_verified_candidate(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_external")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )

    bundle = tmp_path / "packet"
    packet = art.write_bundle(
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

    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert packet["research_assessment"]["t1_receipts"] == 1
    assert packet["research_assessment"]["independent_agreement"] == 1.0
    assert packet["production_readiness_if_claimed"]["label"] == "production_ready"
    assert packet["gates"]["product"]["status"] == "PASS"
    assert packet["gates"]["science"]["status"] == "PASS"
    assert packet["gates"]["external_review"]["status"] == "PASS"
    assert packet["gates"]["kill"]["status"] == "CLEAR"
    legal_summary = packet["review_evidence"]["legal_review"]["summary"]
    assert legal_summary["external_claim_allowed"] is True
    assert legal_summary["tradeable_token_disclaimed"] is True
    assert packet["validation_plan"]["ok"] is True
    assert packet["validation_plan"]["summary"]["schema"] == "kry_validation_plan/v1"
    assert packet["validation_plan"]["summary"]["provider"] == "OpenRouter"
    assert packet["tool_manifest"]["schema"] == "kry_tool_manifest/v1"
    assert packet["inputs"]["tool_manifest"]["sha256"] == packet["tool_manifest"]["sha256"]
    assert packet["review_basis"]["inputs"]["tool_manifest_sha256"] == packet["tool_manifest"]["sha256"]
    assert packet["review_basis"]["schema"] == "kry_review_basis/v1"
    assert packet["inputs"]["review_basis"]["sha256"] == packet["review_basis"]["sha256"]
    assert packet["review_basis"]["config"]["corpus"] == "real"
    assert packet["ship_scope"] == "external_verified_savings_candidate"
    assert packet["claim_allowed"]["external_verified_savings"] is True
    claims = _claims(packet)
    assert claims["external_verified_savings"]["status"] == "allowed"
    assert claims["provider_reconciled"]["status"] == "allowed"
    assert claims["real_corpus_validated"]["status"] == "allowed"
    assert claims["research_grade_readiness"]["status"] == "allowed"
    assert claims["production_ready"]["status"] == "allowed"
    assert claims["external_review_complete"]["status"] == "allowed"
    assert claims["tradeable_token"]["status"] == "forbidden"
    manifest_claims = _manifest_claims(packet)
    assert manifest_claims["production_ready"]["status"] == "allowed"
    assert manifest_claims["production_ready"]["blockers"] == []
    production_fields = _manifest_fields(manifest_claims["production_ready"])
    assert "/production_readiness_if_claimed" in production_fields
    assert "/gates/product" in production_fields
    assert "/gates/science" in production_fields
    assert "/gates/external_review" in production_fields
    external_review_fields = _manifest_fields(manifest_claims["external_review_complete"])
    assert "/review_evidence/outside_review" in external_review_fields
    assert "/review_evidence/buyer_feedback" in external_review_fields
    assert "/review_evidence/legal_review" in external_review_fields
    assert "/gates/external_review" in external_review_fields
    external_savings_fields = _manifest_fields(manifest_claims["external_verified_savings"])
    assert "/review_evidence/legal_review/summary/external_claim_allowed" in external_savings_fields
    assert manifest_claims["tradeable_token"]["status"] == "forbidden"
    tradeable_fields = _manifest_fields(manifest_claims["tradeable_token"])
    assert "/review_evidence/legal_review/summary/tradeable_token_disclaimed" in tradeable_fields


def test_cli_requires_bundle_dir_for_external_candidate_output(minted_sample, tmp_path, capsys):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_cli_requires_bundle")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    out = tmp_path / "artifact.json"

    result = art.main([
        str(usage_log),
        "--attestation", str(att_path),
        "--t1-manifest", str(t1_manifest),
        "--provider-export", str(provider),
        "--provider-export-manifest", str(provider_export_manifest),
        "--corpus", "real",
        "--corpus-manifest", str(corpus_manifest),
        "--outside-review", str(outside),
        "--buyer-feedback", str(buyer),
        "--legal-review", str(legal),
        "--out", str(out),
    ])

    captured = capsys.readouterr()
    assert result == 1
    assert "external verified-savings candidates must be written with --bundle-dir" in captured.err
    assert "reviewer_checklist.json" in captured.err
    assert "finops_report.md" in captured.err
    assert captured.out == ""
    assert not out.exists()


def test_generated_provider_and_corpus_manifests_can_feed_external_candidate(minted_sample, tmp_path, capsys):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_generated_manifests")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_manifest = tmp_path / "provider_export_manifest.json"
    corpus_manifest = tmp_path / "corpus_manifest.json"

    assert art.main([
        str(usage_log),
        "--provider-export", str(provider),
        "--t1-manifest", str(t1_manifest),
        "--provider", "OpenRouter",
        "--export-source", "provider generation API export",
        "--export-reference", "export-ref-2026-06-09",
        "--corpus-source", "real provider gateway export",
        "--corpus-reference", "usage-export-ref-2026-06-09",
        "--evidence-date", "2026-06-09",
        "--window-since", "2026-06-09T00:00:00Z",
        "--window-until", "2026-06-09T01:00:00Z",
        "--write-provider-export-manifest", str(provider_manifest),
        "--write-corpus-manifest", str(corpus_manifest),
    ]) == 0
    cli_output = capsys.readouterr().out
    assert "kry_evidence_manifest_generation/v1" in cli_output
    # the path is JSON-escaped in the CLI output; on Windows that doubles the backslashes, so accept
    # the raw path OR its JSON-escaped form (a no-op on POSIX, where paths use "/").
    assert str(provider_manifest) in cli_output or json.dumps(str(provider_manifest))[1:-1] in cli_output
    assert str(corpus_manifest) in cli_output or json.dumps(str(corpus_manifest))[1:-1] in cli_output

    provider_data = json.loads(provider_manifest.read_text(encoding="utf-8"))
    corpus_data = json.loads(corpus_manifest.read_text(encoding="utf-8"))
    assert provider_data["schema"] == "kry_provider_export_manifest/v1"
    assert provider_data["export_reference"] == "export-ref-2026-06-09"
    assert provider_data["provider_record_count"] == 1
    assert provider_data["artifact_inputs"]["provider_export_sha256"] == art._hash_file(str(provider))["sha256"]
    assert corpus_data["schema"] == "kry_corpus_manifest/v1"
    assert corpus_data["source_reference"] == "usage-export-ref-2026-06-09"
    assert corpus_data["record_count"] == _record_count(usage_log)
    assert corpus_data["validation_plan"]["min_provider_records"] == 1
    assert corpus_data["validation_plan"]["min_usage_records"] == _record_count(usage_log)
    assert corpus_data["artifact_inputs"]["provider_export_manifest_sha256"] == art._hash_file(str(provider_manifest))["sha256"]

    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_manifest,
    )
    bundle = tmp_path / "packet"
    packet = art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        t1_manifest=str(t1_manifest),
        provider_export=str(provider),
        provider_export_manifest=str(provider_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )

    assert packet["gates"]["science"]["status"] == "PASS"
    assert packet["validation_plan"]["ok"] is True
    assert packet["ship_scope"] == "external_verified_savings_candidate"


def test_external_review_evidence_must_include_source_and_reference(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_review_evidence_provenance")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    data = json.loads(outside.read_text(encoding="utf-8"))
    data.pop("evidence_source")
    data["evidence_reference"] = ""
    outside.write_text(json.dumps(data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert outside_check["pass"] is False
    assert "evidence_source missing" in outside_check["detail"]
    assert "evidence_reference missing" in outside_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert _claims(packet)["external_verified_savings"]["status"] == "blocked"


def test_external_review_evidence_must_replace_todo_placeholders(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_review_evidence_todo")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    data = json.loads(outside.read_text(encoding="utf-8"))
    data["evidence_source"] = "signed reviewer note TODO_source"
    data["evidence_reference"] = "review-note TODO_URL_OR_FILE_ID"
    data["reviewer"] = "independent reviewer TODO_org"
    outside.write_text(json.dumps(data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert outside_check["pass"] is False
    assert "evidence_source must replace TODO placeholder" in outside_check["detail"]
    assert "evidence_reference must replace TODO placeholder" in outside_check["detail"]
    assert "reviewer must replace TODO placeholder" in outside_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_external_review_evidence_must_replace_option_list_placeholders(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_review_evidence_option_placeholders")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    outside_data = json.loads(outside.read_text(encoding="utf-8"))
    outside_data["evidence_source"] = "signed reviewer note / issue / email / review packet"
    outside_data["evidence_reference"] = "URL / file id / note id"
    outside_data["reviewer"] = "name / org / role"
    outside.write_text(json.dumps(outside_data), encoding="utf-8")
    buyer_data = json.loads(buyer.read_text(encoding="utf-8"))
    buyer_data["buyer"] = "name / organization / counterparty id"
    buyer_data["buyer_role"] = "AI FinOps / platform / infra buyer"
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")
    legal_data = json.loads(legal.read_text(encoding="utf-8"))
    legal_data["reviewer"] = "claims counsel / legal reviewer"
    legal.write_text(json.dumps(legal_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    buyer_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "buyer_feedback_valid"][0]
    legal_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "legal_review_valid"][0]
    assert outside_check["pass"] is False
    assert buyer_check["pass"] is False
    assert legal_check["pass"] is False
    assert "evidence_source must be a concrete value" in outside_check["detail"]
    assert "evidence_reference must be a concrete value" in outside_check["detail"]
    assert "reviewer must be a concrete value" in outside_check["detail"]
    assert "buyer must be a concrete value" in buyer_check["detail"]
    assert "buyer_role must be a concrete value" in buyer_check["detail"]
    assert "reviewer must be a concrete value" in legal_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_external_review_evidence_must_replace_generic_placeholders(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_review_evidence_generic_placeholders")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    outside_data = json.loads(outside.read_text(encoding="utf-8"))
    outside_data["evidence_source"] = "N/A"
    outside_data["evidence_reference"] = "unknown"
    outside_data["reviewer"] = "TBD"
    outside.write_text(json.dumps(outside_data), encoding="utf-8")
    buyer_data = json.loads(buyer.read_text(encoding="utf-8"))
    buyer_data["buyer"] = "none"
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    buyer_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "buyer_feedback_valid"][0]
    assert outside_check["pass"] is False
    assert buyer_check["pass"] is False
    assert "evidence_source must be a concrete value, not a generic placeholder" in outside_check["detail"]
    assert "evidence_reference must be a concrete value, not a generic placeholder" in outside_check["detail"]
    assert "reviewer must be a concrete value, not a generic placeholder" in outside_check["detail"]
    assert "buyer must be a concrete value, not a generic placeholder" in buyer_check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert "external_review:buyer_feedback_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_external_review_evidence_rejects_multiline_provenance_fields(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_review_evidence_multiline_provenance")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    outside_data = json.loads(outside.read_text(encoding="utf-8"))
    outside_data["evidence_source"] = "signed reviewer note\nextra unbound note"
    outside_data["evidence_reference"] = "review-note-2026-06-09\tprivate-tab"
    outside_data["reviewer"] = "independent reviewer\nExample Org"
    outside.write_text(json.dumps(outside_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert outside_check["pass"] is False
    assert "evidence_source must be a single-line concrete value" in outside_check["detail"]
    assert "evidence_reference must be a single-line concrete value" in outside_check["detail"]
    assert "reviewer must be a single-line concrete value" in outside_check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_external_review_evidence_must_name_reviewed_claims(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_review_evidence_claim_scope")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    outside_data = json.loads(outside.read_text(encoding="utf-8"))
    outside_data.pop("reviewed_claims")
    outside.write_text(json.dumps(outside_data), encoding="utf-8")
    buyer_data = json.loads(buyer.read_text(encoding="utf-8"))
    buyer_data["reviewed_claims"] = []
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")
    legal_data = json.loads(legal.read_text(encoding="utf-8"))
    legal_data["reviewed_claims"] = ["external_verified_savings"]
    legal.write_text(json.dumps(legal_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    buyer_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "buyer_feedback_valid"][0]
    legal_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "legal_review_valid"][0]
    assert outside_check["pass"] is False
    assert buyer_check["pass"] is False
    assert legal_check["pass"] is False
    assert "reviewed_claims must be a non-empty list of strings" in outside_check["detail"]
    assert "reviewed_claims must be a non-empty list of strings" in buyer_check["detail"]
    assert "reviewed_claims must include tradeable_token" in legal_check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert "external_review:buyer_feedback_valid" in packet["external_blockers"]
    assert "external_review:legal_review_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_outside_review_must_name_artifact_checks(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_outside_review_check_scope")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    outside_data = json.loads(outside.read_text(encoding="utf-8"))
    outside_data.pop("reviewer_artifact_checks")
    outside.write_text(json.dumps(outside_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert outside_check["pass"] is False
    assert "reviewer_artifact_checks object missing" in outside_check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"

    outside_data["reviewer_artifact_checks"] = _outside_review_checks()
    outside_data["reviewer_artifact_checks"]["doctor_command_run"] = False
    outside.write_text(json.dumps(outside_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert outside_check["pass"] is False
    assert "reviewer_artifact_checks.doctor_command_run must be true" in outside_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_outside_review_must_record_command_outputs(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_outside_review_command_outputs")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    outside_data = json.loads(outside.read_text(encoding="utf-8"))
    outside_data.pop("reviewer_command_outputs")
    outside.write_text(json.dumps(outside_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert outside_check["pass"] is False
    assert "reviewer_command_outputs object missing" in outside_check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"

    outside_data["reviewer_command_outputs"] = _outside_review_outputs()
    outside_data["reviewer_command_outputs"]["verify_artifact_error_count"] = False
    outside.write_text(json.dumps(outside_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert outside_check["pass"] is False
    assert "reviewer_command_outputs.verify_artifact_error_count must be 0" in outside_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"

    outside_data["reviewer_command_outputs"] = _outside_review_outputs()
    outside_data["reviewer_command_outputs"]["doctor_fail_count"] = 1
    outside.write_text(json.dumps(outside_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert outside_check["pass"] is False
    assert "reviewer_command_outputs.doctor_fail_count must be 0" in outside_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_outside_review_must_record_revocation_status(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_outside_review_revocation_status")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    outside_data = json.loads(outside.read_text(encoding="utf-8"))
    outside_data["reviewer_artifact_checks"].pop("revocation_or_void_status_checked")
    outside.write_text(json.dumps(outside_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert outside_check["pass"] is False
    assert "reviewer_artifact_checks.revocation_or_void_status_checked must be true" in outside_check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert _claims(packet)["external_verified_savings"]["status"] == "blocked"
    assert packet["ship_scope"] == "internal_or_demo_only"

    outside_data["reviewer_artifact_checks"] = _outside_review_checks()
    outside_data["reviewer_command_outputs"]["no_invalid_revoked_or_voided_mints_known"] = False
    outside.write_text(json.dumps(outside_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert outside_check["pass"] is False
    assert "reviewer_command_outputs.no_invalid_revoked_or_voided_mints_known must be true" in outside_check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert _claims(packet)["external_verified_savings"]["status"] == "blocked"
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_buyer_feedback_must_name_buyer_local_evidence_gates(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_buyer_feedback_gate_scope")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    buyer_data = json.loads(buyer.read_text(encoding="utf-8"))
    buyer_data.pop("buyer_local_evidence_gates")
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")

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

    buyer_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "buyer_feedback_valid"][0]
    assert buyer_check["pass"] is False
    assert "buyer_local_evidence_gates object missing" in buyer_check["detail"]
    assert "external_review:buyer_feedback_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"

    buyer_data["buyer_local_evidence_gates"] = _buyer_local_gates()
    buyer_data["buyer_local_evidence_gates"]["baseline_accepted"] = False
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")

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

    buyer_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "buyer_feedback_valid"][0]
    assert buyer_check["pass"] is False
    assert "buyer_local_evidence_gates.baseline_accepted must be true" in buyer_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_buyer_feedback_must_record_threshold_context(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_buyer_feedback_threshold_context")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    buyer_data = json.loads(buyer.read_text(encoding="utf-8"))
    buyer_data.pop("buyer_threshold_context")
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")

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

    buyer_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "buyer_feedback_valid"][0]
    assert buyer_check["pass"] is False
    assert "buyer_threshold_context object missing" in buyer_check["detail"]
    assert "external_review:buyer_feedback_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"

    buyer_data["buyer_threshold_context"] = _buyer_threshold_context()
    buyer_data["buyer_threshold_context"]["baseline_reference"] = "TODO_baseline memo"
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")

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

    buyer_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "buyer_feedback_valid"][0]
    assert buyer_check["pass"] is False
    assert "buyer_threshold_context.baseline_reference must replace TODO placeholder" in buyer_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_buyer_feedback_must_record_machine_readable_materiality(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_buyer_materiality")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    buyer_data = json.loads(buyer.read_text(encoding="utf-8"))
    buyer_data.pop("buyer_materiality")
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")

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

    buyer_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "buyer_feedback_valid"][0]
    assert buyer_check["pass"] is False
    assert "buyer_materiality object missing" in buyer_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"

    buyer_data["buyer_materiality"] = {
        "avoidable_spend_pct": 9.99,
        "plausible_monthly_savings_usd": 4999.99,
    }
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")

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

    buyer_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "buyer_feedback_valid"][0]
    assert buyer_check["pass"] is False
    assert "buyer_materiality must meet avoidable_spend_pct >= 10" in buyer_check["detail"]
    assert "plausible_monthly_savings_usd >= 5000" in buyer_check["detail"]
    assert "external_review:buyer_feedback_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_legal_review_must_name_claim_checks(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_legal_review_check_scope")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    legal_data = json.loads(legal.read_text(encoding="utf-8"))
    legal_data.pop("legal_claim_checks")
    legal.write_text(json.dumps(legal_data), encoding="utf-8")

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

    legal_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "legal_review_valid"][0]
    assert legal_check["pass"] is False
    assert "legal_claim_checks object missing" in legal_check["detail"]
    assert "external_review:legal_review_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"

    legal_data["legal_claim_checks"] = _legal_claim_checks()
    legal_data["legal_claim_checks"]["carbon_language_checked"] = False
    legal.write_text(json.dumps(legal_data), encoding="utf-8")

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

    legal_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "legal_review_valid"][0]
    assert legal_check["pass"] is False
    assert "legal_claim_checks.carbon_language_checked must be true" in legal_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_legal_review_must_record_limitations(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_legal_review_limitations")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    legal_data = json.loads(legal.read_text(encoding="utf-8"))
    legal_data.pop("legal_limitations")
    legal.write_text(json.dumps(legal_data), encoding="utf-8")

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

    legal_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "legal_review_valid"][0]
    assert legal_check["pass"] is False
    assert "legal_limitations must be a non-empty list of strings" in legal_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"

    legal_data["legal_limitations"] = ["TODO_limits from counsel"]
    legal.write_text(json.dumps(legal_data), encoding="utf-8")

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

    legal_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "legal_review_valid"][0]
    assert legal_check["pass"] is False
    assert "legal_limitations[0] must replace TODO placeholder" in legal_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"

    legal_data["legal_limitations"] = ["none"]
    legal.write_text(json.dumps(legal_data), encoding="utf-8")

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

    legal_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "legal_review_valid"][0]
    assert legal_check["pass"] is False
    assert "legal_limitations[0] must be a concrete value, not a generic placeholder" in legal_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_review_evidence_rejects_private_packet_material(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_review_evidence_privacy")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    buyer_data = json.loads(buyer.read_text(encoding="utf-8"))
    buyer_data["raw_request_body"] = {
        "messages": [{"role": "user", "content": "private prompt text"}],
    }
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")

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

    buyer_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "buyer_feedback_valid"][0]
    assert buyer_check["pass"] is False
    assert "buyer_feedback evidence contains private field $.raw_request_body" in buyer_check["detail"]
    assert "buyer_feedback evidence must exclude prompts" in buyer_check["detail"]
    assert "external_review:buyer_feedback_valid" in packet["external_blockers"]
    assert _claims(packet)["external_verified_savings"]["status"] == "blocked"
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_external_review_evidence_reviewed_claims_must_be_known_claim_ids(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_unknown_reviewed_claims")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    outside_data = json.loads(outside.read_text(encoding="utf-8"))
    outside_data["reviewed_claims"] = ["external_verified_savings", "future_revenue_claim"]
    outside.write_text(json.dumps(outside_data), encoding="utf-8")
    buyer_data = json.loads(buyer.read_text(encoding="utf-8"))
    buyer_data["reviewed_claims"] = ["external_verified_savings", "market_ready_claim"]
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")
    legal_data = json.loads(legal.read_text(encoding="utf-8"))
    legal_data["reviewed_claims"] = ["external_verified_savings", "tradeable_token", "securities_safe"]
    legal.write_text(json.dumps(legal_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    buyer_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "buyer_feedback_valid"][0]
    legal_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "legal_review_valid"][0]
    assert outside_check["pass"] is False
    assert buyer_check["pass"] is False
    assert legal_check["pass"] is False
    assert "reviewed_claims[1] unknown claim id future_revenue_claim" in outside_check["detail"]
    assert "reviewed_claims[1] unknown claim id market_ready_claim" in buyer_check["detail"]
    assert "reviewed_claims[2] unknown claim id securities_safe" in legal_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_manifest_generation_missing_provider_export_fails_without_traceback(minted_sample, tmp_path, capsys):
    usage_log, _att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_manifest_generation_error")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)

    result = art.main([
        str(usage_log),
        "--provider-export", str(tmp_path / "missing_provider.json"),
        "--t1-manifest", str(t1_manifest),
        "--provider", "OpenRouter",
        "--export-source", "provider generation API export",
        "--export-reference", "export-ref-2026-06-09",
        "--evidence-date", "2026-06-09",
        "--window-since", "2026-06-09T00:00:00Z",
        "--window-until", "2026-06-09T01:00:00Z",
        "--write-provider-export-manifest", str(tmp_path / "provider_export_manifest.json"),
    ])

    captured = capsys.readouterr()
    assert result == 1
    assert "provider export manifest generation failed" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_provider_export_manifest_generation_rejects_private_content(minted_sample, tmp_path, capsys):
    usage_log, _att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_manifest_generation_private_provider")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{
        "prompt_tokens": 2000,
        "completion_tokens": 400,
        "raw_response": {"content": "private response text"},
    }]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_manifest = tmp_path / "provider_export_manifest.json"

    result = art.main([
        str(usage_log),
        "--provider-export", str(provider),
        "--t1-manifest", str(t1_manifest),
        "--provider", "OpenRouter",
        "--export-source", "provider generation API export",
        "--export-reference", "export-ref-2026-06-09",
        "--evidence-date", "2026-06-09",
        "--window-since", "2026-06-09T00:00:00Z",
        "--window-until", "2026-06-09T01:00:00Z",
        "--write-provider-export-manifest", str(provider_manifest),
    ])

    captured = capsys.readouterr()
    assert result == 1
    assert "provider export manifest generation failed" in captured.err
    assert "provider export privacy check failed" in captured.err
    assert "provider export contains private field $[0].raw_response" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert not provider_manifest.exists()


def test_provider_export_manifest_generation_rejects_empty_provider_export(minted_sample, tmp_path, capsys):
    usage_log, _att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_manifest_generation_empty_provider")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_manifest = tmp_path / "provider_export_manifest.json"

    result = art.main([
        str(usage_log),
        "--provider-export", str(provider),
        "--t1-manifest", str(t1_manifest),
        "--provider", "OpenRouter",
        "--export-source", "provider generation API export",
        "--export-reference", "export-ref-2026-06-09",
        "--evidence-date", "2026-06-09",
        "--window-since", "2026-06-09T00:00:00Z",
        "--window-until", "2026-06-09T01:00:00Z",
        "--write-provider-export-manifest", str(provider_manifest),
    ])

    captured = capsys.readouterr()
    assert result == 1
    assert "provider export manifest generation failed" in captured.err
    assert "provider export must contain at least one provider record" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert not provider_manifest.exists()


def test_provider_export_manifest_generation_rejects_zero_token_provider_export(minted_sample, tmp_path, capsys):
    usage_log, _att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_manifest_generation_zero_token_provider")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 0, "completion_tokens": 0}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_manifest = tmp_path / "provider_export_manifest.json"

    result = art.main([
        str(usage_log),
        "--provider-export", str(provider),
        "--t1-manifest", str(t1_manifest),
        "--provider", "OpenRouter",
        "--export-source", "provider generation API export",
        "--export-reference", "export-ref-2026-06-09",
        "--evidence-date", "2026-06-09",
        "--window-since", "2026-06-09T00:00:00Z",
        "--window-until", "2026-06-09T01:00:00Z",
        "--write-provider-export-manifest", str(provider_manifest),
    ])

    captured = capsys.readouterr()
    assert result == 1
    assert "provider export manifest generation failed" in captured.err
    assert "provider export must contain positive provider token counts" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert not provider_manifest.exists()


def test_provider_export_manifest_generation_rejects_placeholder_provenance(minted_sample, tmp_path, capsys):
    usage_log, _att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_manifest_generation_placeholder_provider")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_manifest = tmp_path / "provider_export_manifest.json"

    result = art.main([
        str(usage_log),
        "--provider-export", str(provider),
        "--t1-manifest", str(t1_manifest),
        "--provider", "OpenRouter TODO_provider",
        "--export-source", "N/A",
        "--export-reference", "URL / file id / note id",
        "--evidence-date", "2026-06-09",
        "--window-since", "2026-06-09T00:00:00Z",
        "--window-until", "2026-06-09T01:00:00Z",
        "--write-provider-export-manifest", str(provider_manifest),
    ])

    captured = capsys.readouterr()
    assert result == 1
    assert "provider export manifest generation failed" in captured.err
    assert "provider export manifest evidence fields invalid" in captured.err
    assert "provider must replace TODO placeholder" in captured.err
    assert "export_source must be a concrete value, not a generic placeholder" in captured.err
    assert "export_reference must be a concrete value, not a placeholder option list" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert not provider_manifest.exists()


def test_corpus_manifest_generation_rejects_private_usage_log(minted_sample, tmp_path, capsys):
    usage_log, _att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_manifest_generation_private_usage")
    private_usage = tmp_path / "private_usage.jsonl"
    records = [json.loads(line) for line in usage_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    records[0]["request_body"] = {
        "messages": [{"role": "user", "content": "private prompt text"}],
    }
    private_usage.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n", encoding="utf-8")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = tmp_path / "corpus_manifest.json"

    result = art.main([
        str(private_usage),
        "--provider-export", str(provider),
        "--provider-export-manifest", str(provider_manifest),
        "--t1-manifest", str(t1_manifest),
        "--provider", "OpenRouter",
        "--corpus-source", "real provider gateway export",
        "--corpus-reference", "usage-export-ref-2026-06-09",
        "--evidence-date", "2026-06-09",
        "--window-since", "2026-06-09T00:00:00Z",
        "--window-until", "2026-06-09T01:00:00Z",
        "--write-corpus-manifest", str(corpus_manifest),
    ])

    captured = capsys.readouterr()
    assert result == 1
    assert "corpus manifest generation failed" in captured.err
    assert "usage log privacy check failed" in captured.err
    assert "usage log contains private field $[0].request_body" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert not corpus_manifest.exists()


def test_corpus_manifest_generation_rejects_placeholder_provenance(minted_sample, tmp_path, capsys):
    usage_log, _att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_manifest_generation_placeholder_corpus")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = tmp_path / "corpus_manifest.json"

    result = art.main([
        str(usage_log),
        "--provider-export", str(provider),
        "--provider-export-manifest", str(provider_manifest),
        "--t1-manifest", str(t1_manifest),
        "--provider", "TBD",
        "--corpus-source", "provider gateway / billing export / production traffic window",
        "--corpus-reference", "none",
        "--evidence-date", "2026-06-09",
        "--window-since", "2026-06-09T00:00:00Z",
        "--window-until", "2026-06-09T01:00:00Z",
        "--write-corpus-manifest", str(corpus_manifest),
    ])

    captured = capsys.readouterr()
    assert result == 1
    assert "corpus manifest generation failed" in captured.err
    assert "corpus manifest evidence fields invalid" in captured.err
    assert "provider must be a concrete value, not a generic placeholder" in captured.err
    assert "source must be a concrete value, not a placeholder option list" in captured.err
    assert "source_reference must be a concrete value, not a generic placeholder" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert not corpus_manifest.exists()


def test_real_corpus_without_validation_plan_cannot_pass_science_gate(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_missing_validation_plan")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    corpus_data = json.loads(corpus_manifest.read_text(encoding="utf-8"))
    corpus_data.pop("validation_plan")
    corpus_manifest.write_text(json.dumps(corpus_data), encoding="utf-8")
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    plan_check = [c for c in packet["gates"]["science"]["checks"]
                  if c["name"] == "validation_plan_valid"][0]
    assert plan_check["pass"] is False
    assert "validation_plan object missing" in plan_check["detail"]
    assert "science:validation_plan_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert _claims(packet)["real_corpus_validated"]["status"] == "blocked"


def test_full_packet_accepts_single_flat_provider_usage_record(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_flat_provider_record")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps({"prompt_tokens": 2000, "completion_tokens": 400}), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest, count=1)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert packet["research_assessment"]["reconcile"]["provider_records"] == 1
    assert packet["provider_export_manifest"]["summary"]["provider_record_count"] == 1
    assert packet["gates"]["science"]["status"] == "PASS"
    assert packet["ship_scope"] == "external_verified_savings_candidate"


def test_review_evidence_cannot_predate_reviewed_manifests(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_stale_review_date")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    data = json.loads(outside.read_text(encoding="utf-8"))
    data["date"] = "2026-06-08"
    outside.write_text(json.dumps(data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert packet["gates"]["science"]["status"] == "PASS"
    assert outside_check["pass"] is False
    assert "date must be on or after reviewed evidence date 2026-06-09" in outside_check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_review_evidence_timestamp_cannot_predate_same_day_manifest_timestamp(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_same_day_stale_review_timestamp")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(
        tmp_path, art, provider, t1_manifest, date="2026-06-09T01:00:00Z",
    )
    corpus_manifest = _corpus_manifest(
        tmp_path,
        art,
        usage_log,
        provider,
        record_count=_record_count(usage_log),
        t1_manifest=t1_manifest,
        provider_export_manifest=provider_export_manifest,
        date="2026-06-09T01:00:00Z",
    )
    corpus_data = json.loads(corpus_manifest.read_text(encoding="utf-8"))
    corpus_data["validation_plan"]["registered_date"] = "2026-06-08T23:59:00Z"
    corpus_manifest.write_text(json.dumps(corpus_data), encoding="utf-8")
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    for path in (outside, buyer, legal):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["date"] = "2026-06-09T00:30:00Z"
        path.write_text(json.dumps(data), encoding="utf-8")

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

    assert packet["gates"]["science"]["status"] == "PASS"
    for name in ("outside_review_valid", "buyer_feedback_valid", "legal_review_valid"):
        check = [c for c in packet["gates"]["external_review"]["checks"]
                 if c["name"] == name][0]
        assert check["pass"] is False
        assert "date must be on or after reviewed evidence date 2026-06-09T01:00:00Z" in check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert "external_review:buyer_feedback_valid" in packet["external_blockers"]
    assert "external_review:legal_review_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_review_evidence_date_must_be_iso_string(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_review_numeric_date")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    data = json.loads(outside.read_text(encoding="utf-8"))
    data["date"] = 1781000000
    outside.write_text(json.dumps(data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert packet["gates"]["science"]["status"] == "PASS"
    assert outside_check["pass"] is False
    assert "date must be ISO-8601" in outside_check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_review_evidence_date_must_not_be_future(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_review_future_date")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    data = json.loads(outside.read_text(encoding="utf-8"))
    data["date"] = "2999-01-01"
    outside.write_text(json.dumps(data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert packet["gates"]["science"]["status"] == "PASS"
    assert outside_check["pass"] is False
    assert "date must not be in the future" in outside_check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_review_evidence_same_day_future_timestamp_is_rejected(minted_sample, tmp_path, monkeypatch):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_review_same_day_future_timestamp")

    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
            return current if tz is not None else current.replace(tzinfo=None)

        @classmethod
        def fromisoformat(cls, text):
            return datetime.fromisoformat(text)

    monkeypatch.setattr(art, "datetime", FrozenDateTime)
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    data = json.loads(outside.read_text(encoding="utf-8"))
    data["date"] = "2026-06-09T12:00:01Z"
    outside.write_text(json.dumps(data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert packet["gates"]["science"]["status"] == "PASS"
    assert outside_check["pass"] is False
    assert "date must not be in the future" in outside_check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_review_evidence_identity_fields_must_be_nonblank(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_blank_review_identity")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    outside_data = json.loads(outside.read_text(encoding="utf-8"))
    outside_data["reviewer"] = "   "
    outside.write_text(json.dumps(outside_data), encoding="utf-8")
    buyer_data = json.loads(buyer.read_text(encoding="utf-8"))
    buyer_data["buyer"] = "   "
    buyer_data["buyer_role"] = "   "
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")
    legal_data = json.loads(legal.read_text(encoding="utf-8"))
    legal_data["reviewer"] = 123
    legal.write_text(json.dumps(legal_data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    buyer_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "buyer_feedback_valid"][0]
    legal_check = [c for c in packet["gates"]["external_review"]["checks"]
                   if c["name"] == "legal_review_valid"][0]
    assert packet["gates"]["science"]["status"] == "PASS"
    assert "reviewer missing" in outside_check["detail"]
    assert "buyer missing" in buyer_check["detail"]
    assert "buyer_role missing" in buyer_check["detail"]
    assert "reviewer missing" in legal_check["detail"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]
    assert "external_review:buyer_feedback_valid" in packet["external_blockers"]
    assert "external_review:legal_review_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_tool_manifest_covers_proof_generation_and_verification_code(minted_sample):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_tool_manifest_scope")

    packet = art.build_artifact(str(usage_log), attestation=str(att_path), mint_log=str(log))

    paths = {entry["path"] for entry in packet["tool_manifest"]["files"]}
    assert {
        "scripts/kry_verified_artifact.py",
        "scripts/kry_doctor.py",
        "scripts/kry_finops_report.py",
        "scripts/kry_savings_report.py",
        "scripts/kry_verify.py",
        "scripts/kry_research_grade.py",
        "scripts/kry_reconcile.py",
        "src/kry/kry_mint.py",
        "src/kry/kry_attest.py",
        "src/kry/kry_token.py",
    } <= paths
    assert packet["review_basis"]["inputs"]["tool_manifest_sha256"] == packet["tool_manifest"]["sha256"]


def test_attestation_event_counts_must_match_savings_report(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_event_count_binding")
    tampered_att = tmp_path / "attestation_tampered.json"
    att = json.loads(att_path.read_text(encoding="utf-8"))
    att["links"][0]["event_type"] = "short_circuit"
    att["event_type_counts"] = {
        "cache_hit": att["event_type_counts"]["cache_hit"] - 1,
        "short_circuit": att["event_type_counts"].get("short_circuit", 0) + 1,
    }
    att["attestation_hash"] = art.kry_verify._attestation_hash(att)
    assert art.kry_verify.verify_attestation(att)[0]
    tampered_att.write_text(json.dumps(att, indent=2, sort_keys=True), encoding="utf-8")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, tampered_att, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )

    packet = art.build_artifact(
        str(usage_log),
        attestation=str(tampered_att),
        t1_manifest=str(t1_manifest),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )

    product_check = [c for c in packet["gates"]["product"]["checks"]
                     if c["name"] == "attestation_matches_report_event_counts"][0]
    assert packet["attestation_verification"]["ok"] is True
    assert product_check["pass"] is False
    assert "short_circuit" in product_check["detail"]
    assert "product:attestation_matches_report_event_counts" in packet["external_blockers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_private_log_without_t1_manifest_cannot_pass_science_gate(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_private_log_no_t1_manifest")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    corpus_manifest = _corpus_manifest(tmp_path, art, usage_log, provider, record_count=_record_count(usage_log))
    outside, buyer, legal = _review_files(tmp_path, art, usage_log, att_path, provider, corpus_manifest)

    packet = art.build_artifact(
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )

    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert packet["gates"]["science"]["status"] == "FAIL"
    assert packet["gates"]["external_review"]["status"] == "PASS"
    assert "science:t1_manifest_supplied" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_missing_provider_export_manifest_cannot_pass_science_gate(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_missing_provider_manifest")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    corpus_manifest = _corpus_manifest(tmp_path, art, usage_log, provider, record_count=_record_count(usage_log))
    outside, buyer, legal = _review_files(tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest)

    packet = art.build_artifact(
        str(usage_log),
        attestation=str(att_path),
        t1_manifest=str(t1_manifest),
        provider_export=str(provider),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )

    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert packet["gates"]["science"]["status"] == "FAIL"
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_corpus_manifest_must_bind_provider_provenance(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_corpus_missing_provider_binding")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    corpus_check = [c for c in packet["gates"]["science"]["checks"]
                    if c["name"] == "corpus_manifest_valid"][0]
    assert corpus_check["pass"] is False
    assert "provider_export_manifest_sha256 mismatch" in corpus_check["detail"]
    assert "science:corpus_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_full_packet_can_use_t1_manifest_instead_of_private_log(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_external_t1_manifest")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    assert packet["research_assessment"]["reconciliation_source"] == "t1_manifest"
    assert packet["research_assessment"]["mint_log"] is None
    assert packet["research_assessment"]["t1_manifest"]["path"] == str(t1_manifest)
    assert packet["gates"]["science"]["status"] == "PASS"
    assert packet["ship_scope"] == "external_verified_savings_candidate"


def test_saved_packet_verifies_by_recomputing_inputs(minted_sample, tmp_path, capsys):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_verify_clean")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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
    out = bundle / "artifact.json"

    result = art.verify_artifact_file(str(out))

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["ship_scope"] == "external_verified_savings_candidate"
    assert _claims(result)["external_verified_savings"]["status"] == "allowed"
    assert art.main(["--verify-artifact", str(out)]) == 0
    assert '"ok": true' in capsys.readouterr().out


def test_external_candidate_requires_portable_command_inputs(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_nonportable_external")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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
    out = tmp_path / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")

    result = art.verify_artifact_file(str(out))

    assert result["ok"] is False
    assert "command_inputs.usage_log must be relative, got absolute path" in result["errors"]
    assert "command_inputs.t1_manifest must be relative, got absolute path" in result["errors"]


def test_external_candidate_rejects_private_mint_log_command_input(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_private_log_external")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    packet_dir = tmp_path / "leaky_packet"
    packet_dir.mkdir()
    copies = {
        "usage_log.jsonl": usage_log,
        "attestation.json": att_path,
        "mint_log.jsonl": log,
        "t1_manifest.json": t1_manifest,
        "provider_export.json": provider,
        "provider_export_manifest.json": provider_export_manifest,
        "corpus_manifest.json": corpus_manifest,
        "outside_review.json": outside,
        "buyer_feedback.json": buyer,
        "legal_review.json": legal,
    }
    for name, source in copies.items():
        (packet_dir / name).write_bytes(Path(source).read_bytes())
    packet = art.build_artifact(
        "usage_log.jsonl",
        attestation="attestation.json",
        mint_log="mint_log.jsonl",
        t1_manifest="t1_manifest.json",
        provider_export="provider_export.json",
        provider_export_manifest="provider_export_manifest.json",
        corpus="real",
        corpus_manifest="corpus_manifest.json",
        outside_review="outside_review.json",
        buyer_feedback="buyer_feedback.json",
        legal_review="legal_review.json",
        base_dir=packet_dir,
    )
    out = packet_dir / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")

    result = art.verify_artifact_file(str(out))

    assert result["ok"] is False
    assert "command_inputs.mint_log must be absent for external candidates; use t1_manifest" in result["errors"]


def test_external_candidate_requires_packet_checklist_and_report(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_missing_packet_surfaces")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    packet_dir = tmp_path / "bare_packet"
    packet_dir.mkdir()
    copies = {
        "usage_log.jsonl": usage_log,
        "attestation.json": att_path,
        "t1_manifest.json": t1_manifest,
        "provider_export.json": provider,
        "provider_export_manifest.json": provider_export_manifest,
        "corpus_manifest.json": corpus_manifest,
        "outside_review.json": outside,
        "buyer_feedback.json": buyer,
        "legal_review.json": legal,
    }
    for name, source in copies.items():
        (packet_dir / name).write_bytes(Path(source).read_bytes())
    packet = art.build_artifact(
        "usage_log.jsonl",
        attestation="attestation.json",
        t1_manifest="t1_manifest.json",
        provider_export="provider_export.json",
        provider_export_manifest="provider_export_manifest.json",
        corpus="real",
        corpus_manifest="corpus_manifest.json",
        outside_review="outside_review.json",
        buyer_feedback="buyer_feedback.json",
        legal_review="legal_review.json",
        base_dir=packet_dir,
    )
    out = packet_dir / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")

    result = art.verify_artifact_file(str(out))

    assert packet["ship_scope"] == "external_verified_savings_candidate"
    assert result["ok"] is False
    assert "finops_report.md missing from packet; bundle mode should generate it" in result["errors"]
    assert "reviewer_checklist.json missing from packet; bundle mode should generate it" in result["errors"]


def test_external_candidate_verifier_rejects_stale_packet_checklist(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_stale_packet_checklist")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    checklist_path = bundle / "reviewer_checklist.json"
    checklist = json.loads(checklist_path.read_text(encoding="utf-8"))
    checklist["artifact"]["artifact_hash"] = "0" * 64
    checklist_path.write_text(json.dumps(checklist, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = art.verify_artifact_file(str(bundle / "artifact.json"))

    assert result["ok"] is False
    assert "reviewer_checklist.json does not match artifact.json" in result["errors"]


def test_external_candidate_verifier_rejects_nonstandard_packet_checklist_json(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_nonstandard_packet_checklist_json")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    (bundle / "reviewer_checklist.json").write_text('{"schema":"kry_reviewer_checklist/v1","bad":NaN}\n', encoding="utf-8")

    result = art.verify_artifact_file(str(bundle / "artifact.json"))

    assert result["ok"] is False
    assert any(
        "cannot read reviewer_checklist.json: non-standard JSON constant rejected: NaN" in err
        for err in result["errors"]
    )


def test_external_candidate_verifier_rejects_stale_packet_report(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_stale_packet_report")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    report_path = bundle / "finops_report.md"
    report_path.write_text(report_path.read_text().replace(
        "External verified-savings candidate",
        "External verified-savings claim approved",
    ), encoding="utf-8")

    result = art.verify_artifact_file(str(bundle / "artifact.json"))

    assert result["ok"] is False
    assert "finops_report.md does not match artifact.json" in result["errors"]


def test_external_candidate_verifier_rejects_private_packet_runtime_file(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_private_packet_runtime_file")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    (bundle / "mint.jsonl").write_text(log.read_text(), encoding="utf-8")

    result = art.verify_artifact_file(str(bundle / "artifact.json"))

    assert result["ok"] is False
    assert "private ledger/mint-log material present in packet: mint.jsonl" in result["errors"]


def test_external_candidate_verifier_rejects_unbound_packet_file(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_unbound_packet_file")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    (bundle / "notes.txt").write_text("unverified packet note\n", encoding="utf-8")

    result = art.verify_artifact_file(str(bundle / "artifact.json"))

    assert result["ok"] is False
    assert "packet contains unbound file: notes.txt" in result["errors"]


def test_external_candidate_verifier_rejects_unbound_packet_directory(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_unbound_packet_directory")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    (bundle / "extra_evidence").mkdir()

    result = art.verify_artifact_file(str(bundle / "artifact.json"))

    assert result["ok"] is False
    assert "packet contains unbound directory: extra_evidence" in result["errors"]


def test_external_candidate_verifier_allows_declared_input_parent_directory(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_declared_input_parent_directory")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    packet_dir = tmp_path / "packet"
    inputs_dir = packet_dir / "inputs"
    inputs_dir.mkdir(parents=True)
    copies = {
        "usage_log.jsonl": usage_log,
        "attestation.json": att_path,
        "t1_manifest.json": t1_manifest,
        "provider_export.json": provider,
        "provider_export_manifest.json": provider_export_manifest,
        "corpus_manifest.json": corpus_manifest,
        "outside_review.json": outside,
        "buyer_feedback.json": buyer,
        "legal_review.json": legal,
    }
    for name, source in copies.items():
        (inputs_dir / name).write_bytes(Path(source).read_bytes())
    packet = art.build_artifact(
        "inputs/usage_log.jsonl",
        attestation="inputs/attestation.json",
        t1_manifest="inputs/t1_manifest.json",
        provider_export="inputs/provider_export.json",
        provider_export_manifest="inputs/provider_export_manifest.json",
        corpus="real",
        corpus_manifest="inputs/corpus_manifest.json",
        outside_review="inputs/outside_review.json",
        buyer_feedback="inputs/buyer_feedback.json",
        legal_review="inputs/legal_review.json",
        base_dir=packet_dir,
    )
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
    report_text = art._render_finops_report(out)
    (packet_dir / "finops_report.md").write_text(report_text + ("" if report_text.endswith("\n") else "\n"), encoding="utf-8")

    result = art.verify_artifact_file(str(out))

    assert result["ok"] is True
    assert result["errors"] == []


def test_external_candidate_verifier_rejects_packet_symlink(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_packet_symlink")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    try:
        (bundle / "linked_report.md").symlink_to("finops_report.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this filesystem")

    result = art.verify_artifact_file(str(bundle / "artifact.json"))

    assert result["ok"] is False
    assert "packet contains symlink: linked_report.md" in result["errors"]


def test_external_candidate_verifier_requires_canonical_artifact_name(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_canonical_artifact_name")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    packet = art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    renamed = bundle / "renamed.json"
    (bundle / "artifact.json").rename(renamed)
    checklist_inputs = dict(packet["review_basis"]["inputs"])
    checklist_inputs["review_basis_sha256"] = packet["review_basis"]["sha256"]
    checklist = art._reviewer_checklist(
        checklist_inputs,
        artifact_path="renamed.json",
        artifact_hash=packet["artifact_hash"],
    )
    (bundle / "reviewer_checklist.json").write_text(json.dumps(checklist, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_text = art._render_finops_report(renamed)
    (bundle / "finops_report.md").write_text(report_text + ("" if report_text.endswith("\n") else "\n"), encoding="utf-8")

    result = art.verify_artifact_file(str(renamed))

    assert result["ok"] is False
    assert "externally claimable packet artifact must be named artifact.json" in result["errors"]


def test_saved_packet_rejects_broken_claim_evidence_manifest(minted_sample, tmp_path, monkeypatch):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_broken_claim_manifest")
    monkeypatch.setitem(
        art._CLAIM_EVIDENCE_FIELDS,
        "science_gate",
        ["/gates/science", "/missing/reviewer/path"],
    )
    packet = art.build_artifact(str(usage_log), attestation=str(att_path), mint_log=str(log))
    out = tmp_path / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")

    result = art.verify_artifact_file(str(out))

    assert result["ok"] is False
    assert (
        "claim_evidence_manifest external_verified_savings "
        "evidence:science_gate field missing: /missing/reviewer/path"
    ) in result["errors"]


def test_saved_packet_rejects_stale_claim_evidence_manifest_metadata(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_claim_manifest_metadata")
    packet = art.build_artifact(str(usage_log), attestation=str(att_path), mint_log=str(log))
    packet["claim_evidence_manifest"]["artifact"]["path"] = "other-artifact.json"
    packet["claim_evidence_manifest"]["artifact"]["artifact_hash"] = "0" * 64
    packet["claim_evidence_manifest"]["ship_scope"] = "external_verified_savings_candidate"
    packet["artifact_hash"] = art._artifact_hash(packet)
    out = tmp_path / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")

    result = art.verify_artifact_file(str(out))

    assert result["ok"] is False
    assert "claim_evidence_manifest artifact path mismatch" in result["errors"]
    assert "claim_evidence_manifest artifact hash mismatch" in result["errors"]
    assert "claim_evidence_manifest ship_scope mismatch" in result["errors"]


def test_saved_packet_rejects_stale_claim_evidence_manifest_hash_only(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_claim_manifest_hash")
    packet = art.build_artifact(str(usage_log), attestation=str(att_path), mint_log=str(log))
    packet["claim_evidence_manifest"]["artifact"]["artifact_hash"] = "0" * 64
    out = tmp_path / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")

    result = art.verify_artifact_file(str(out))

    assert result["ok"] is False
    assert "claim_evidence_manifest artifact hash mismatch" in result["errors"]


def test_saved_packet_rejects_toolchain_drift(minted_sample, tmp_path, monkeypatch):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_toolchain_drift")
    packet = art.build_artifact(str(usage_log), attestation=str(att_path), mint_log=str(log))
    out = tmp_path / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
    original_manifest = art._tool_manifest()

    def drifted_manifest():
        data = json.loads(json.dumps(original_manifest))
        data["files"][0]["sha256"] = "0" * 64
        data["sha256"] = "1" * 64
        return data

    monkeypatch.setattr(art, "_tool_manifest", drifted_manifest)

    result = art.verify_artifact_file(str(out))

    assert result["ok"] is False
    assert "artifact body does not match recomputed gates" in result["errors"]


def test_bundle_uses_relative_paths_and_verifies_from_any_cwd(minted_sample, tmp_path, monkeypatch):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_bundle_clean")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"

    packet = art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    artifact_path = bundle / "artifact.json"
    saved = json.loads(artifact_path.read_text(encoding="utf-8"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = art.verify_artifact_file(str(artifact_path))

    assert packet["ship_scope"] == "external_verified_savings_candidate"
    assert saved["command_inputs"]["usage_log"] == "usage_log.jsonl"
    assert saved["command_inputs"]["mint_log"] is None
    assert saved["command_inputs"]["t1_manifest"] == "t1_manifest.json"
    assert saved["command_inputs"]["provider_export_manifest"] == "provider_export_manifest.json"
    assert saved["inputs"]["usage_log"]["path"] == "usage_log.jsonl"
    assert saved["inputs"]["t1_manifest"]["path"] == "t1_manifest.json"
    assert saved["inputs"]["provider_export_manifest"]["path"] == "provider_export_manifest.json"
    assert saved["research_assessment"]["reconciliation_source"] == "t1_manifest"
    assert (bundle / "provider_export.json").exists()
    assert (bundle / "provider_export_manifest.json").exists()
    assert (bundle / "t1_manifest.json").exists()
    assert (bundle / "reviewer_checklist.json").exists()
    assert (bundle / "finops_report.md").exists()
    checklist = json.loads((bundle / "reviewer_checklist.json").read_text(encoding="utf-8"))
    manifest = json.loads((bundle / "artifact.json").read_text(encoding="utf-8"))["claim_evidence_manifest"]
    assert checklist["schema"] == "kry_reviewer_checklist/v1"
    assert checklist["artifact"]["path"] == "artifact.json"
    assert checklist["artifact"]["artifact_hash"] == packet["artifact_hash"]
    assert manifest["artifact"]["path"] == "artifact.json"
    assert manifest["artifact"]["artifact_hash"] == packet["artifact_hash"]
    assert checklist["basis"]["review_basis_sha256"] == packet["review_basis"]["sha256"]
    assert checklist["verify_command"].endswith("--verify-artifact artifact.json")
    assert checklist["doctor_command"].endswith("--artifact artifact.json")
    assert "Run doctor_command and require fail=0 before handing the packet to a reviewer." in checklist["review_steps"]
    assert checklist["buyer_local_privacy_boundary"][0].startswith("Do not return prompts")
    assert "Provider bill, provider usage export, or AWS CUR plus gateway/request metadata for the same window." in (
        checklist["buyer_local_evidence_gates"]
    )
    assert any("<=2%" in gate for gate in checklist["buyer_local_evidence_gates"])
    assert any(">=$5k/month" in gate for gate in checklist["buyer_local_evidence_gates"])
    assert checklist["derived_artifacts"] == [
        {"file": "finops_report.md", "schema": "kry_finops_report/v1", "source": "artifact.json"},
    ]
    report = (bundle / "finops_report.md").read_text(encoding="utf-8")
    assert "# KRY Retained-Dollars Report" in report
    assert "External verified-savings claim: ALLOWED AS CANDIDATE" in report
    assert "- Tradeable token: forbidden" in report
    assert "--verify-artifact artifact.json" in report
    assert "--artifact artifact.json" in report
    assert str(tmp_path) not in report
    assert not (bundle / "mint_log.jsonl").exists()
    manifest = json.loads((bundle / "t1_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "kry_t1_reconciliation_manifest/v1"
    assert manifest["receipt_count"] == 1
    assert set(manifest["receipts"][0]) <= {
        "receipt_id", "evidence_tier", "receipt_hash", "chain_hash", "metered_tokens", "ts",
    }
    assert manifest["receipts"][0]["receipt_hash"]
    assert manifest["receipts"][0]["chain_hash"]
    assert manifest["receipts"][0]["metered_tokens"] == [2000, 400]
    assert result["ok"] is True


def test_bundle_refuses_dirty_existing_packet_directory(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_bundle_dirty")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    bundle.mkdir()
    (bundle / "notes.txt").write_text("stale packet note\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        art.write_bundle(
            bundle,
            str(usage_log),
            attestation=str(att_path),
            mint_log=str(log),
            provider_export=str(provider),
            provider_export_manifest=str(provider_export_manifest),
            corpus="real",
            corpus_manifest=str(corpus_manifest),
            outside_review=str(outside),
            buyer_feedback=str(buyer),
            legal_review=str(legal),
        )

    assert "bundle verification failed" in str(exc.value)
    assert "packet contains unbound file: notes.txt" in str(exc.value)


def test_cli_bundle_refuses_dirty_existing_packet_directory_without_traceback(minted_sample, tmp_path, capsys):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_cli_dirty_bundle")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    bundle.mkdir()
    (bundle / "notes.txt").write_text("stale packet note\n", encoding="utf-8")

    result = art.main([
        str(usage_log),
        "--attestation", str(att_path),
        "--mint-log", str(log),
        "--provider-export", str(provider),
        "--provider-export-manifest", str(provider_export_manifest),
        "--corpus", "real",
        "--corpus-manifest", str(corpus_manifest),
        "--outside-review", str(outside),
        "--buyer-feedback", str(buyer),
        "--legal-review", str(legal),
        "--bundle-dir", str(bundle),
    ])

    captured = capsys.readouterr()
    assert result == 1
    assert "bundle generation failed: bundle verification failed" in captured.err
    assert "packet contains unbound file: notes.txt" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_bundle_refuses_private_usage_log_before_copying_inputs(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_bundle_private_usage")
    private_usage = tmp_path / "private_usage.jsonl"
    records = [json.loads(ln) for ln in usage_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    records[0]["request_body"] = {
        "messages": [{"role": "user", "content": "private prompt text"}],
    }
    private_usage.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n", encoding="utf-8")
    bundle = tmp_path / "packet"

    with pytest.raises(ValueError) as exc:
        art.write_bundle(bundle, str(private_usage), attestation=str(att_path), mint_log=str(log))

    assert "bundle input privacy check failed" in str(exc.value)
    assert "usage log contains private field $[0].request_body" in str(exc.value)
    assert not bundle.exists()


def test_bundle_refuses_private_provider_export_before_copying_inputs(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_bundle_private_provider")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{
        "prompt_tokens": 2000,
        "completion_tokens": 400,
        "raw_response": {"content": "private response text"},
    }]), encoding="utf-8")
    bundle = tmp_path / "packet"

    with pytest.raises(ValueError) as exc:
        art.write_bundle(
            bundle,
            str(usage_log),
            attestation=str(att_path),
            mint_log=str(log),
            provider_export=str(provider),
        )

    assert "bundle input privacy check failed" in str(exc.value)
    assert "provider export contains private field $[0].raw_response" in str(exc.value)
    assert not bundle.exists()


def test_bundle_refuses_private_review_evidence_before_copying_inputs(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_bundle_private_review_evidence")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    outside_data = json.loads(outside.read_text(encoding="utf-8"))
    outside_data["messages"] = [{"role": "user", "content": "private review packet text"}]
    outside.write_text(json.dumps(outside_data), encoding="utf-8")
    bundle = tmp_path / "packet"

    with pytest.raises(ValueError) as exc:
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

    assert "bundle input privacy check failed" in str(exc.value)
    assert "outside_review evidence contains private field $.messages" in str(exc.value)
    assert not bundle.exists()


def test_bundle_refuses_private_public_manifest_before_copying_inputs(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_bundle_private_public_manifest")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    corpus_data = json.loads(corpus_manifest.read_text(encoding="utf-8"))
    corpus_data["raw_response_body"] = {"content": "private corpus note content"}
    corpus_manifest.write_text(json.dumps(corpus_data), encoding="utf-8")
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"

    with pytest.raises(ValueError) as exc:
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

    assert "bundle input privacy check failed" in str(exc.value)
    assert "corpus_manifest contains private field $.raw_response_body" in str(exc.value)
    assert not bundle.exists()


def test_bundle_verifier_rejects_tampered_included_input(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_bundle_tamper")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    (bundle / "provider_export.json").write_text(json.dumps([{"prompt_tokens": 1, "completion_tokens": 1}]), encoding="utf-8")

    result = art.verify_artifact_file(str(bundle / "artifact.json"))

    assert result["ok"] is False
    assert "artifact body does not match recomputed gates" in result["errors"]


def test_bundle_verifier_rejects_private_public_manifest_content(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_bundle_manifest_privacy")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
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

    result = art.verify_artifact_file(str(bundle / "artifact.json"))

    assert result["ok"] is False
    assert "corpus_manifest contains private field $.raw_response_body" in result["errors"]
    assert (
        "corpus_manifest must exclude prompts, completions, messages, content, "
        "and raw request/response bodies"
    ) in result["errors"]


def test_bundle_verifier_rejects_tampered_t1_manifest(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_bundle_t1_tamper")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "prebundle_t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    bundle = tmp_path / "packet"
    art.write_bundle(
        bundle,
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        provider_export_manifest=str(provider_export_manifest),
        corpus="real",
        corpus_manifest=str(corpus_manifest),
        outside_review=str(outside),
        buyer_feedback=str(buyer),
        legal_review=str(legal),
    )
    manifest_path = bundle / "t1_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["receipts"][0]["metered_tokens"] = [1, 1]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = art.verify_artifact_file(str(bundle / "artifact.json"))

    assert result["ok"] is False
    assert "artifact body does not match recomputed gates" in result["errors"]


def test_t1_manifest_must_match_attestation_links(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_forged_t1_manifest")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    data = json.loads(t1_manifest.read_text(encoding="utf-8"))
    data["receipts"][0]["receipt_hash"] = "0" * 64
    t1_manifest.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    binding_check = [c for c in packet["gates"]["science"]["checks"]
                     if c["name"] == "t1_manifest_matches_attestation"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert binding_check["pass"] is False
    assert "not present in provider_metered attestation links" in binding_check["detail"]
    assert "t1_manifest_not_attested" in packet["gates"]["kill"]["triggers"]
    assert packet["ship_scope"] == "do_not_ship"
    assert packet["production_readiness_if_claimed"]["label"] == "research_grade"
    assert _claims(packet)["provider_reconciled"]["status"] == "blocked"
    assert "science:t1_manifest_matches_attestation" in _claims(packet)["provider_reconciled"]["blockers"]
    assert _claims(packet)["real_corpus_validated"]["status"] == "blocked"
    assert "science:t1_manifest_matches_attestation" in _claims(packet)["real_corpus_validated"]["blockers"]


def test_t1_manifest_metered_tokens_must_match_attestation(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_t1_metered_tamper")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 1, "completion_tokens": 1}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    data = json.loads(t1_manifest.read_text(encoding="utf-8"))
    data["receipts"][0]["metered_tokens"] = [1, 1]
    t1_manifest.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    binding_check = [c for c in packet["gates"]["science"]["checks"]
                     if c["name"] == "t1_manifest_matches_attestation"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert binding_check["pass"] is False
    assert "metered_tokens differ from attestation link" in binding_check["detail"]
    assert "t1_manifest_not_attested" in packet["gates"]["kill"]["triggers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_t1_manifest_timestamp_must_match_attestation(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_t1_ts_tamper")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    data = json.loads(t1_manifest.read_text(encoding="utf-8"))
    data["receipts"][0]["ts"] = data["receipts"][0]["ts"] + 3600
    t1_manifest.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    binding_check = [c for c in packet["gates"]["science"]["checks"]
                     if c["name"] == "t1_manifest_matches_attestation"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert binding_check["pass"] is False
    assert "ts differs from attestation link" in binding_check["detail"]
    assert "t1_manifest_not_attested" in packet["gates"]["kill"]["triggers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_malformed_t1_manifest_metered_tokens_fail_gate_without_crashing(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_t1_metered_malformed")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    data = json.loads(t1_manifest.read_text(encoding="utf-8"))
    data["receipts"][0]["metered_tokens"] = ["not-an-int", 400]
    t1_manifest.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    binding_check = [c for c in packet["gates"]["science"]["checks"]
                     if c["name"] == "t1_manifest_matches_attestation"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "ASSESSMENT_ERROR"
    assert binding_check["pass"] is False
    assert "metered_tokens must be integers" in binding_check["detail"]
    assert "t1_manifest_not_attested" in packet["gates"]["kill"]["triggers"]
    assert "reconciliation_assessment_error" in packet["gates"]["kill"]["triggers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_boolean_t1_manifest_metered_tokens_fail_gate_without_coercion(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_t1_metered_bool")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 1, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    data = json.loads(t1_manifest.read_text(encoding="utf-8"))
    data["receipts"][0]["metered_tokens"] = [True, 400]
    t1_manifest.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    binding_check = [c for c in packet["gates"]["science"]["checks"]
                     if c["name"] == "t1_manifest_matches_attestation"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "ASSESSMENT_ERROR"
    assert binding_check["pass"] is False
    assert "metered_tokens must be integers" in binding_check["detail"]
    assert "t1_manifest_not_attested" in packet["gates"]["kill"]["triggers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_t1_manifest_must_cover_all_attested_t1_links(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_omitted_t1_manifest")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    data = json.loads(t1_manifest.read_text(encoding="utf-8"))
    data["receipt_count"] = 0
    data["receipts"] = []
    t1_manifest.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest, count=0)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    binding_check = [c for c in packet["gates"]["science"]["checks"]
                     if c["name"] == "t1_manifest_matches_attestation"][0]
    assert packet["research_assessment"]["t1_receipts"] == 0
    assert binding_check["pass"] is False
    assert "omits 1 provider_metered attestation link" in binding_check["detail"]
    assert "t1_manifest_not_attested" in packet["gates"]["kill"]["triggers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_t1_manifest_receipt_count_must_be_json_integer(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_t1_receipt_count_bool")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    data = json.loads(t1_manifest.read_text(encoding="utf-8"))
    data["receipt_count"] = True
    t1_manifest.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    binding_check = [c for c in packet["gates"]["science"]["checks"]
                     if c["name"] == "t1_manifest_matches_attestation"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert binding_check["pass"] is False
    assert "receipt_count must be a non-negative integer" in binding_check["detail"]
    assert "t1_manifest_not_attested" in packet["gates"]["kill"]["triggers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_t1_manifest_source_hash_must_be_valid_sha256(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_t1_source_hash")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    data = json.loads(t1_manifest.read_text(encoding="utf-8"))
    data["source_mint_log_sha256"] = "TODO_SHA256"
    t1_manifest.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    binding_check = [c for c in packet["gates"]["science"]["checks"]
                     if c["name"] == "t1_manifest_matches_attestation"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert binding_check["pass"] is False
    assert "source_mint_log_sha256 must be 64 lowercase hex characters" in binding_check["detail"]
    assert "t1_manifest_not_attested" in packet["gates"]["kill"]["triggers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_evidence_templates_are_hash_bound_but_non_passing(minted_sample, tmp_path, capsys):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_templates")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    assert art.main([
        "--mint-log", str(log),
        "--write-t1-manifest", str(t1_manifest),
    ]) == 0
    assert "t1_manifest" in capsys.readouterr().out
    template_dir = tmp_path / "templates"

    result = art.write_evidence_templates(
        template_dir,
        str(usage_log),
        attestation=str(att_path),
        provider_export=str(provider),
        t1_manifest=str(t1_manifest),
        corpus="real",
    )

    assert len(result["written"]) == 5
    assert len(result["basis_files"]) == 3
    assert len(result["request_files"]) == 4
    provider_template = json.loads((template_dir / "provider_export_manifest.template.json").read_text(encoding="utf-8"))
    corpus = json.loads((template_dir / "corpus_manifest.template.json").read_text(encoding="utf-8"))
    outside = json.loads((template_dir / "outside_review.template.json").read_text(encoding="utf-8"))
    buyer = json.loads((template_dir / "buyer_feedback.template.json").read_text(encoding="utf-8"))
    provider_request = (template_dir / "provider_export_request.md").read_text(encoding="utf-8")
    outside_request = (template_dir / "external_review_request.md").read_text(encoding="utf-8")
    buyer_request = (template_dir / "buyer_feedback_request.md").read_text(encoding="utf-8")
    legal_request = (template_dir / "legal_review_request.md").read_text(encoding="utf-8")
    tool_manifest = json.loads((template_dir / "tool_manifest.json").read_text(encoding="utf-8"))
    review_basis = json.loads((template_dir / "review_basis.json").read_text(encoding="utf-8"))
    checklist = json.loads((template_dir / "reviewer_checklist.json").read_text(encoding="utf-8"))
    assert provider_template["schema"] == "kry_provider_export_manifest_template/v1"
    assert corpus["schema"] == "kry_corpus_manifest_template/v1"
    assert corpus["validation_plan"]["schema"] == "kry_validation_plan/v1"
    assert corpus["validation_plan"]["registered_date"].endswith("BEFORE_OR_ON_COLLECTION_START")
    assert corpus["validation_plan"]["min_independent_agreement"] == art.kry_capabilities.INDEPENDENT_AGREEMENT_BAR
    assert corpus["validation_plan"]["outside_review_required"] is True
    assert corpus["validation_plan"]["kill_criteria"] == list(art.REQUIRED_VALIDATION_KILL_CRITERIA)
    assert corpus["source_reference"].startswith("TODO_")
    assert provider_template["export_reference"].startswith("TODO_")
    assert outside["schema"] == "kry_external_evidence_template/v1"
    assert outside["evidence_source"].startswith("TODO_")
    assert outside["evidence_reference"].startswith("TODO_")
    assert outside["reviewer_artifact_checks"]["verify_artifact_command_run"] == "TODO_TRUE_OR_FALSE"
    assert outside["reviewer_artifact_checks"]["doctor_command_run"] == "TODO_TRUE_OR_FALSE"
    assert outside["reviewer_artifact_checks"]["revocation_or_void_status_checked"] == "TODO_TRUE_OR_FALSE"
    assert outside["reviewer_command_outputs"]["verify_artifact_ok"] == "TODO_TRUE_OR_FALSE"
    assert outside["reviewer_command_outputs"]["verify_artifact_error_count"] == "TODO_ZERO"
    assert outside["reviewer_command_outputs"]["doctor_fail_count"] == "TODO_ZERO"
    assert outside["reviewer_command_outputs"]["no_invalid_revoked_or_voided_mints_known"] == "TODO_TRUE_OR_FALSE"
    assert outside["reviewed_claims"] == ["external_verified_savings"]
    assert buyer["buyer"].startswith("TODO_")
    assert buyer["buyer_local_evidence_gates"]["proof_required_reader_named"] == "TODO_TRUE_OR_FALSE"
    assert buyer["buyer_local_evidence_gates"]["baseline_accepted"] == "TODO_TRUE_OR_FALSE"
    assert buyer["buyer_threshold_context"]["proof_required_reader"].startswith("TODO_")
    assert buyer["buyer_threshold_context"]["baseline_reference"].startswith("TODO_")
    assert buyer["buyer_materiality"]["avoidable_spend_pct"] == "TODO_NUMBER_OR_OMIT"
    assert buyer["buyer_materiality"]["plausible_monthly_savings_usd"] == "TODO_NUMBER_OR_OMIT"
    assert buyer["reviewed_claims"] == ["external_verified_savings"]
    legal = json.loads((template_dir / "legal_review.template.json").read_text(encoding="utf-8"))
    assert legal["reviewed_claims"] == ["external_verified_savings", "tradeable_token"]
    assert legal["legal_limitations"][0].startswith("TODO_")
    assert legal["legal_claim_checks"]["external_claim_text_checked"] == "TODO_TRUE_OR_FALSE"
    assert legal["legal_claim_checks"]["carbon_language_checked"] == "TODO_TRUE_OR_FALSE"
    assert checklist["schema"] == "kry_reviewer_checklist/v1"
    assert checklist["basis"]["tool_manifest_sha256"] == result["tool_manifest"]["sha256"]
    assert checklist["basis"]["review_basis_sha256"] == result["review_basis"]["sha256"]
    assert checklist["artifact"]["artifact_hash"] is None
    assert checklist["verify_command"].endswith("--verify-artifact packet/artifact.json")
    assert checklist["doctor_command"].endswith("--artifact packet/artifact.json")
    assert "Run doctor_command and require fail=0 before handing the packet to a reviewer." in checklist["review_steps"]
    assert checklist["buyer_local_privacy_boundary"][0].startswith("Do not return prompts")
    assert "Named proof-required reader or intended user for the savings evidence." in (
        checklist["buyer_local_evidence_gates"]
    )
    assert any("provider-authoritative cost within <=2%" in gate for gate in checklist["buyer_local_evidence_gates"])
    assert any("Concrete proof-required reader" in field
               for field in checklist["buyer_threshold_context_fields"])
    assert any("Request/gateway metadata reference without prompts" in field
               for field in checklist["buyer_threshold_context_fields"])
    assert checklist["buyer_materiality_threshold"] == {
        "avoidable_spend_pct_min": 10.0,
        "plausible_monthly_savings_usd_min": 5000.0,
        "rule": "at least one threshold must be met",
    }
    assert checklist["required_kill_criteria"] == list(art.REQUIRED_VALIDATION_KILL_CRITERIA)
    assert any("External claim text matches the claim register" in check
               for check in checklist["legal_claim_checks"])
    assert any("Credit, settlement, and routing-permission wording" in check
               for check in checklist["legal_claim_checks"])
    assert checklist["derived_artifacts"] == [
        {"file": "finops_report.md", "schema": "kry_finops_report/v1", "source": "packet/artifact.json"},
    ]
    assert any(c["claim_id"] == "tradeable_token" and c["required_status"] == "forbidden"
               for c in checklist["claim_checks"])
    assert tool_manifest == result["tool_manifest"]
    assert review_basis == result["review_basis"]
    assert result["tool_manifest"]["schema"] == "kry_tool_manifest/v1"
    assert result["review_basis"]["schema"] == "kry_review_basis/v1"
    assert result["review_basis"]["config"]["corpus"] == "real"
    assert result["review_basis"]["inputs"]["tool_manifest_sha256"] == result["tool_manifest"]["sha256"]
    assert "This request is not evidence by itself" in provider_request
    assert "kry_provider_export_manifest/v1" in provider_request
    assert "export_reference" in provider_request
    assert "Do not return prompts, completions" in provider_request
    assert "Provider bill, provider usage export, or AWS CUR plus gateway/request metadata" in provider_request
    assert "Accepted measured/projected baseline before analysis" in provider_request
    assert "Materiality target: >=10% avoidable spend or >=$5k/month" in provider_request
    assert "This request is not review evidence" in outside_request
    assert "python3 scripts/kry_verified_artifact.py --verify-artifact packet/artifact.json" in outside_request
    assert "python3 scripts/kry_doctor.py --artifact packet/artifact.json" in outside_request
    assert "kry_external_evidence/v1" in outside_request
    assert "evidence_source" in outside_request
    assert "evidence_reference" in outside_request
    assert "reviewer_artifact_checks` with every verifier/checklist flag set to `true" in outside_request
    assert "reviewer_command_outputs" in outside_request
    assert "error/fail counts set to JSON number `0`" in outside_request
    assert "no_invalid_revoked_or_voided_mints_known=true" in outside_request
    assert "reviewed_claims` must include `external_verified_savings" in outside_request
    assert "This request is not buyer feedback" in buyer_request
    assert "qualified_interest" in buyer_request
    assert "buyer`, `buyer_role`" in buyer_request
    assert "Named proof-required reader or intended user" in buyer_request
    assert "Budget authority, customer authority, procurement authority" in buyer_request
    assert "Seven-day sample target that can reconcile provider-authoritative cost within <=2%" in buyer_request
    assert "buyer_local_evidence_gates` with every threshold flag set to `true" in buyer_request
    assert "buyer_threshold_context" in buyer_request
    assert "concrete buyer-local reader" in buyer_request
    assert "buyer_materiality" in buyer_request
    assert "plausible_monthly_savings_usd >= 5000" in buyer_request
    assert "reviewed_claims` must include `external_verified_savings" in buyer_request
    assert "This request is not legal review" in legal_request
    assert "tradeable_token_disclaimed" in legal_request
    assert "legal_limitations" in legal_request
    assert "legal_claim_checks" in legal_request
    assert "Credit, settlement, and routing-permission wording" in legal_request
    assert "Carbon or environmental wording" in legal_request
    assert "includes `external_verified_savings` and `tradeable_token" in legal_request
    assert provider_template["artifact_inputs"]["provider_export_sha256"] == art._hash_file(str(provider))["sha256"]
    assert provider_template["artifact_inputs"]["t1_manifest_sha256"] == art._hash_file(str(t1_manifest))["sha256"]
    assert corpus["artifact_inputs"]["usage_log_sha256"] == art._hash_file(str(usage_log))["sha256"]
    assert outside["artifact_inputs"]["attestation_sha256"] == art._hash_file(str(att_path))["sha256"]
    assert outside["artifact_inputs"]["t1_manifest_sha256"] == art._hash_file(str(t1_manifest))["sha256"]
    assert outside["artifact_inputs"]["tool_manifest_sha256"] == result["tool_manifest"]["sha256"]
    assert outside["artifact_inputs"]["review_basis_sha256"] == result["review_basis"]["sha256"]
    assert art.main([
        str(usage_log),
        "--attestation", str(att_path),
        "--provider-export", str(provider),
        "--t1-manifest", str(t1_manifest),
        "--corpus", "real",
        "--template-dir", str(tmp_path / "cli_templates"),
    ]) == 0
    cli_output = capsys.readouterr().out
    assert "outside_review.template.json" in cli_output
    assert "external_review_request.md" in cli_output

    packet = art.build_artifact(
        str(usage_log),
        attestation=str(att_path),
        t1_manifest=str(t1_manifest),
        provider_export=str(provider),
        provider_export_manifest=str(template_dir / "provider_export_manifest.template.json"),
        corpus="real",
        corpus_manifest=str(template_dir / "corpus_manifest.template.json"),
        outside_review=str(template_dir / "outside_review.template.json"),
        buyer_feedback=str(template_dir / "buyer_feedback.template.json"),
        legal_review=str(template_dir / "legal_review.template.json"),
    )
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert packet["gates"]["science"]["status"] == "FAIL"
    assert packet["gates"]["external_review"]["status"] == "FAIL"
    assert "science:corpus_manifest_valid" in packet["external_blockers"]
    assert "external_review:outside_review_valid" in packet["external_blockers"]


def test_t1_manifest_generation_rejects_empty_receipt_set(minted_sample, tmp_path, capsys):
    _usage_log, _att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_empty_t1_manifest_generation")
    t1_manifest = tmp_path / "empty_t1_manifest.json"

    result = art.main([
        "--mint-log", str(log),
        "--since", "9999999999",
        "--write-t1-manifest", str(t1_manifest),
    ])

    captured = capsys.readouterr()
    assert result == 1
    assert "T1 manifest generation failed" in captured.err
    assert "requires at least one provider_metered receipt" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert not t1_manifest.exists()


def test_packet_hash_catches_stale_saved_edit(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_stale_edit")
    packet = art.build_artifact(str(usage_log), attestation=str(att_path), mint_log=str(log))
    packet["ship_scope"] = "external_verified_savings_candidate"
    out = tmp_path / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")

    result = art.verify_artifact_file(str(out))

    assert result["ok"] is False
    assert "artifact_hash mismatch" in result["errors"]


def test_recompute_catches_tampered_packet_even_with_updated_hash(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_recompute_tamper")
    packet = art.build_artifact(str(usage_log), attestation=str(att_path), mint_log=str(log))
    packet["ship_scope"] = "external_verified_savings_candidate"
    packet["claim_allowed"]["external_verified_savings"] = True
    packet["artifact_hash"] = art._artifact_hash(packet)
    out = tmp_path / "artifact.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")

    result = art.verify_artifact_file(str(out))

    assert result["ok"] is False
    assert "artifact body does not match recomputed gates" in result["errors"]


def test_real_flag_without_manifest_does_not_pass_science_gate(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_missing_corpus_manifest")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")

    packet = art.build_artifact(
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        corpus="real",
    )

    assert packet["gates"]["science"]["status"] == "FAIL"
    assert "science:corpus_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_placeholder_review_files_do_not_pass_external_gate(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_placeholder_reviews")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _placeholder_review_files(tmp_path)

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

    assert packet["gates"]["science"]["status"] == "PASS"
    assert packet["gates"]["external_review"]["status"] == "FAIL"
    assert packet["production_readiness_if_claimed"]["label"] == "production_ready"
    assert _claims(packet)["research_grade_readiness"]["status"] == "allowed"
    assert _claims(packet)["production_ready"]["status"] == "blocked"
    assert "external_review:outside_review_valid" in _claims(packet)["production_ready"]["blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert packet["claim_allowed"]["external_verified_savings"] is False
    assert "external_review:outside_review_valid" in packet["external_blockers"]


def test_hash_mismatched_review_evidence_does_not_pass(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_mismatched_review")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    data = json.loads(outside.read_text(encoding="utf-8"))
    data["artifact_inputs"]["attestation_sha256"] = "0" * 64
    outside.write_text(json.dumps(data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert outside_check["pass"] is False
    assert "attestation_sha256 mismatch" in outside_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_review_evidence_must_bind_t1_manifest(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_missing_t1_review_binding")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path,
        art,
        usage_log,
        att_path,
        provider,
        corpus_manifest,
        t1_manifest=None,
        provider_export_manifest=provider_export_manifest,
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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert packet["gates"]["science"]["status"] == "PASS"
    assert outside_check["pass"] is False
    assert "t1_manifest_sha256 mismatch" in outside_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_review_evidence_must_bind_gate_config(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_review_basis_mismatch")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
        tolerance=1,
    )
    outside, buyer, legal = _review_files(
        tmp_path,
        art,
        usage_log,
        att_path,
        provider,
        corpus_manifest,
        t1_manifest,
        provider_export_manifest,
        tolerance=0,
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
        tolerance=1,
    )

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert packet["gates"]["science"]["status"] == "PASS"
    assert outside_check["pass"] is False
    assert "review_basis_sha256 mismatch" in outside_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_review_evidence_must_bind_tool_manifest(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_tool_manifest_review_binding")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    data = json.loads(outside.read_text(encoding="utf-8"))
    data["artifact_inputs"]["tool_manifest_sha256"] = "0" * 64
    outside.write_text(json.dumps(data), encoding="utf-8")

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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert packet["gates"]["science"]["status"] == "PASS"
    assert outside_check["pass"] is False
    assert "tool_manifest_sha256 mismatch" in outside_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_review_evidence_channels_must_be_distinct(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_review_channel_separation")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )
    buyer_data = json.loads(buyer.read_text(encoding="utf-8"))
    buyer_data["evidence_reference"] = "review-note-2026-06-09"
    buyer_data["buyer"] = "independent reviewer"
    buyer.write_text(json.dumps(buyer_data), encoding="utf-8")
    legal_data = json.loads(legal.read_text(encoding="utf-8"))
    legal_data["evidence_reference"] = "review-note-2026-06-09"
    legal_data["reviewer"] = "independent reviewer"
    legal.write_text(json.dumps(legal_data), encoding="utf-8")

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

    checks = {
        check["name"]: check
        for check in packet["gates"]["external_review"]["checks"]
    }
    assert checks["outside_review_valid"]["pass"] is True
    assert checks["buyer_feedback_valid"]["pass"] is True
    assert checks["legal_review_valid"]["pass"] is True
    assert checks["review_channels_distinct"]["pass"] is False
    assert "must use distinct evidence_reference values" in checks["review_channels_distinct"]["detail"]
    assert "must name distinct actors" in checks["review_channels_distinct"]["detail"]
    assert "external_review:review_channels_distinct" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_provider_export_manifest_count_must_match_export(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_provider_count_mismatch")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest, count=2)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert provider_check["pass"] is False
    assert "provider_record_count must match provider export (1)" in provider_check["detail"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert packet["production_readiness_if_claimed"]["label"] == "research_grade"
    assert _claims(packet)["research_grade_readiness"]["status"] == "blocked"
    assert "science:provider_export_manifest_valid" in _claims(packet)["research_grade_readiness"]["blockers"]
    assert _claims(packet)["real_corpus_validated"]["status"] == "blocked"


def test_provider_export_manifest_count_must_be_json_integer(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_provider_count_bool")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest, count=True)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert provider_check["pass"] is False
    assert "provider_record_count must be a non-negative integer" in provider_check["detail"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_provider_export_manifest_count_must_be_positive(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_provider_count_zero")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest, count=0)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
        min_provider_records=0,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "DISCREPANCY"
    assert provider_check["pass"] is False
    assert "provider_record_count must be greater than 0" in provider_check["detail"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_provider_export_manifest_token_total_must_be_positive(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_provider_token_total_zero")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 0, "completion_tokens": 0}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest, count=1)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "DISCREPANCY"
    assert provider_check["pass"] is False
    assert "provider export token total must be greater than 0" in provider_check["detail"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_usage_log_with_private_content_fields_blocks_public_claim(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_usage_log_private_fields")
    private_usage = tmp_path / "private_usage.jsonl"
    records = [json.loads(ln) for ln in usage_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    records[0]["request_body"] = {
        "messages": [{"role": "user", "content": "private prompt text"}],
    }
    private_usage.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n", encoding="utf-8")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, private_usage, provider, record_count=_record_count(private_usage),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, private_usage, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
    )

    packet = art.build_artifact(
        str(private_usage),
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

    usage_check = [c for c in packet["gates"]["science"]["checks"]
                   if c["name"] == "usage_log_public_packet_safe"][0]
    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert packet["gates"]["product"]["status"] == "PASS"
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert provider_check["pass"] is True
    assert usage_check["pass"] is False
    assert "usage log contains private field $[0].request_body" in usage_check["detail"]
    assert "usage log contains private field $[0].request_body.messages" in usage_check["detail"]
    assert "usage log contains private field $[0].request_body.messages[0].content" in usage_check["detail"]
    assert "usage log must exclude prompts, completions, messages, content" in usage_check["detail"]
    assert "science:usage_log_public_packet_safe" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert _claims(packet)["real_corpus_validated"]["status"] == "blocked"


def test_provider_export_with_private_content_fields_blocks_public_claim(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_provider_private_fields")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{
        "prompt_tokens": 2000,
        "completion_tokens": 400,
        "request_body": {
            "messages": [{"role": "user", "content": "private prompt text"}],
        },
    }]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert provider_check["pass"] is False
    assert "provider export contains private field $[0].request_body" in provider_check["detail"]
    assert "provider export contains private field $[0].request_body.messages" in provider_check["detail"]
    assert "provider export contains private field $[0].request_body.messages[0].content" in provider_check["detail"]
    assert "provider export must exclude prompts, completions, messages, content" in provider_check["detail"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert _claims(packet)["real_corpus_validated"]["status"] == "blocked"


def test_provider_export_with_private_string_value_blocks_public_claim(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_provider_private_string")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{
        "prompt_tokens": 2000,
        "completion_tokens": 400,
        "metadata": {
            "note": "prompt: private customer question",
        },
    }]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert provider_check["pass"] is False
    assert "provider export contains private string value $[0].metadata.note" in provider_check["detail"]
    assert "provider export must exclude prompts, completions, messages, content" in provider_check["detail"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert _claims(packet)["real_corpus_validated"]["status"] == "blocked"


def test_malformed_provider_export_tokens_fail_without_coercion(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_provider_token_bool")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": True, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest, count=1)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "ASSESSMENT_ERROR"
    assert provider_check["pass"] is False
    assert "provider export count unavailable: prompt_tokens must be a non-negative JSON integer" in provider_check["detail"]
    assert "reconciliation_assessment_error" in packet["gates"]["kill"]["triggers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_provider_export_manifest_date_must_be_iso_8601(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_provider_date_invalid")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(
        tmp_path, art, provider, t1_manifest, date="not-a-date",
    )
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert provider_check["pass"] is False
    assert "date must be ISO-8601" in provider_check["detail"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_provider_export_manifest_date_must_be_iso_string_not_epoch(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_provider_numeric_date")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(
        tmp_path, art, provider, t1_manifest, date=1781000000,
    )
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert provider_check["pass"] is False
    assert "date must be ISO-8601" in provider_check["detail"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_manifest_provenance_fields_must_be_nonblank(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_blank_manifest_provenance")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(
        tmp_path,
        art,
        provider,
        t1_manifest,
        provider_name="   ",
        export_source="   ",
        export_reference="   ",
    )
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
        source="   ",
        source_reference="   ",
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    corpus_check = [c for c in packet["gates"]["science"]["checks"]
                    if c["name"] == "corpus_manifest_valid"][0]
    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert packet["gates"]["product"]["status"] == "PASS"
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert "source missing" in corpus_check["detail"]
    assert "source_reference missing" in corpus_check["detail"]
    assert "provider missing" in provider_check["detail"]
    assert "export_source missing" in provider_check["detail"]
    assert "export_reference missing" in provider_check["detail"]
    assert "science:corpus_manifest_valid" in packet["external_blockers"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_manifest_provenance_fields_must_replace_todo_placeholders(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_todo_manifest_provenance")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(
        tmp_path,
        art,
        provider,
        t1_manifest,
        provider_name="OpenRouter TODO_provider",
        export_source="provider API endpoint TODO_export",
        export_reference="export ref TODO_reference",
    )
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
        source="real provider gateway export TODO_source",
        source_reference="usage export TODO_reference",
        provider_name="OpenRouter TODO_provider",
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    corpus_check = [c for c in packet["gates"]["science"]["checks"]
                    if c["name"] == "corpus_manifest_valid"][0]
    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    plan_check = [c for c in packet["gates"]["science"]["checks"]
                  if c["name"] == "validation_plan_valid"][0]
    assert "source must replace TODO placeholder" in corpus_check["detail"]
    assert "source_reference must replace TODO placeholder" in corpus_check["detail"]
    assert "provider must replace TODO placeholder" in provider_check["detail"]
    assert "export_source must replace TODO placeholder" in provider_check["detail"]
    assert "export_reference must replace TODO placeholder" in provider_check["detail"]
    assert "validation_plan.provider must replace TODO placeholder" in plan_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_manifest_provenance_fields_must_replace_option_list_placeholders(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_manifest_option_placeholders")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(
        tmp_path,
        art,
        provider,
        t1_manifest,
        export_source="provider generation API export / console report",
        export_reference="export id / report id / console URL / signed note id",
    )
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
        source="provider gateway / billing export / production traffic window",
        source_reference="usage export id / log bundle id / report id / signed note id",
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    corpus_check = [c for c in packet["gates"]["science"]["checks"]
                    if c["name"] == "corpus_manifest_valid"][0]
    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert corpus_check["pass"] is False
    assert provider_check["pass"] is False
    assert "source must be a concrete value" in corpus_check["detail"]
    assert "source_reference must be a concrete value" in corpus_check["detail"]
    assert "export_source must be a concrete value" in provider_check["detail"]
    assert "export_reference must be a concrete value" in provider_check["detail"]
    assert "science:corpus_manifest_valid" in packet["external_blockers"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_validation_plan_kill_criteria_must_replace_todo_placeholders(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_todo_kill_criteria")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    corpus_data = json.loads(corpus_manifest.read_text(encoding="utf-8"))
    corpus_data["validation_plan"]["kill_criteria"] = [
        "provider reconciliation discrepancy",
        "TODO_missing external review evidence",
    ]
    corpus_manifest.write_text(json.dumps(corpus_data), encoding="utf-8")
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    plan_check = [c for c in packet["gates"]["science"]["checks"]
                  if c["name"] == "validation_plan_valid"][0]
    assert plan_check["pass"] is False
    assert "validation_plan.kill_criteria[1] must replace TODO placeholder" in plan_check["detail"]
    assert "science:validation_plan_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_validation_plan_must_include_required_kill_criteria(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_required_kill_criteria")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    corpus_data = json.loads(corpus_manifest.read_text(encoding="utf-8"))
    missing = "buyer materiality or reliance threshold not met"
    corpus_data["validation_plan"]["kill_criteria"] = [
        item for item in corpus_data["validation_plan"]["kill_criteria"] if item != missing
    ]
    corpus_manifest.write_text(json.dumps(corpus_data), encoding="utf-8")
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    plan_check = [c for c in packet["gates"]["science"]["checks"]
                  if c["name"] == "validation_plan_valid"][0]
    assert plan_check["pass"] is False
    assert "validation_plan.kill_criteria must include buyer materiality or reliance threshold not met" in (
        plan_check["detail"]
    )
    assert "science:validation_plan_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_validation_plan_registered_date_must_not_postdate_collection_start(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_posthoc_validation_plan")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(
        tmp_path, art, provider, t1_manifest, date="2026-06-10",
    )
    corpus_manifest = _corpus_manifest(
        tmp_path,
        art,
        usage_log,
        provider,
        record_count=_record_count(usage_log),
        t1_manifest=t1_manifest,
        provider_export_manifest=provider_export_manifest,
        date="2026-06-10",
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    plan_check = [c for c in packet["gates"]["science"]["checks"]
                  if c["name"] == "validation_plan_valid"][0]
    assert plan_check["pass"] is False
    assert "registered_date must be on or before collection window start" in plan_check["detail"]
    assert "science:validation_plan_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert _claims(packet)["real_corpus_validated"]["status"] == "blocked"


def test_validation_plan_registered_timestamp_must_not_postdate_same_day_collection_start(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_same_day_posthoc_validation_plan")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path,
        art,
        usage_log,
        provider,
        record_count=_record_count(usage_log),
        t1_manifest=t1_manifest,
        provider_export_manifest=provider_export_manifest,
    )
    data = json.loads(corpus_manifest.read_text(encoding="utf-8"))
    data["validation_plan"]["registered_date"] = "2026-06-09T00:30:00Z"
    corpus_manifest.write_text(json.dumps(data), encoding="utf-8")
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    plan_check = [c for c in packet["gates"]["science"]["checks"]
                  if c["name"] == "validation_plan_valid"][0]
    assert plan_check["pass"] is False
    assert "registered_date must be on or before collection window start 2026-06-09T00:00:00Z" in (
        plan_check["detail"]
    )
    assert "science:validation_plan_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_validation_plan_registered_date_must_not_postdate_epoch_collection_start(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_posthoc_epoch_validation_plan")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    window = {
        "since": art._window_bound_to_epoch("2026-06-09T00:00:00Z"),
        "until": art._window_bound_to_epoch("2026-06-09T01:00:00Z"),
    }
    provider_export_manifest = _provider_export_manifest(
        tmp_path, art, provider, t1_manifest, date="2026-06-10", collection_window=window,
    )
    corpus_manifest = _corpus_manifest(
        tmp_path,
        art,
        usage_log,
        provider,
        record_count=_record_count(usage_log),
        t1_manifest=t1_manifest,
        provider_export_manifest=provider_export_manifest,
        date="2026-06-10",
        collection_window=window,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    plan_check = [c for c in packet["gates"]["science"]["checks"]
                  if c["name"] == "validation_plan_valid"][0]
    assert plan_check["pass"] is False
    assert "registered_date must be on or before collection window start" in plan_check["detail"]
    assert "science:validation_plan_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_manifest_dates_must_not_be_future(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_future_manifest_dates")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(
        tmp_path, art, provider, t1_manifest, date="2999-01-01",
    )
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
        date="2999-01-01",
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    corpus_check = [c for c in packet["gates"]["science"]["checks"]
                    if c["name"] == "corpus_manifest_valid"][0]
    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert corpus_check["pass"] is False
    assert provider_check["pass"] is False
    assert "date must not be in the future" in corpus_check["detail"]
    assert "date must not be in the future" in provider_check["detail"]
    assert "science:corpus_manifest_valid" in packet["external_blockers"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_provider_export_manifest_window_must_match_reconciliation_window(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_provider_window_mismatch")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path,
        art,
        usage_log,
        att_path,
        provider,
        corpus_manifest,
        t1_manifest,
        provider_export_manifest,
        since=0.0,
        until=9999999999.0,
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
        since=0.0,
        until=9999999999.0,
    )

    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert provider_check["pass"] is False
    assert "collection_window.since must match --since" in provider_check["detail"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_aggregate_mode_requires_receipt_window_filters(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_aggregate_window_required")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest, mode="aggregate")
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest, mode="aggregate",
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
        mode="aggregate",
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
        mode="aggregate",
    )

    aggregate_check = [c for c in packet["gates"]["science"]["checks"]
                       if c["name"] == "aggregate_reconciliation_window_applied"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "ASSESSMENT_ERROR"
    assert any("aggregate mode requires --since and --until receipt filters" in reason
               for reason in packet["research_assessment"]["reasons"])
    assert aggregate_check["pass"] is False
    assert "aggregate mode requires --since and --until" in aggregate_check["detail"]
    assert "science:aggregate_reconciliation_window_applied" in packet["external_blockers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_aggregate_mode_can_pass_with_receipt_window_filters(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_aggregate_window_applied")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    t1_data = json.loads(t1_manifest.read_text(encoding="utf-8"))
    receipt_ts = t1_data["receipts"][0]["ts"]
    start = receipt_ts - 60
    end = receipt_ts + 60
    window = {"since": start, "until": end}
    provider_export_manifest = _provider_export_manifest(
        tmp_path, art, provider, t1_manifest, mode="aggregate", collection_window=window,
    )
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest, mode="aggregate",
        collection_window=window, tolerance_pct=2.0,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
        mode="aggregate", tolerance_pct=2.0, since=start, until=end,
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
        mode="aggregate",
        tolerance_pct=2.0,
        since=start,
        until=end,
    )

    aggregate_check = [c for c in packet["gates"]["science"]["checks"]
                       if c["name"] == "aggregate_reconciliation_window_applied"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert aggregate_check["pass"] is True
    assert packet["gates"]["science"]["status"] == "PASS"
    assert packet["ship_scope"] == "external_verified_savings_candidate"


def test_aggregate_external_candidate_requires_two_percent_tolerance(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_aggregate_tolerance_threshold")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    t1_data = json.loads(t1_manifest.read_text(encoding="utf-8"))
    receipt_ts = t1_data["receipts"][0]["ts"]
    start = receipt_ts - 60
    end = receipt_ts + 60
    window = {"since": start, "until": end}
    provider_export_manifest = _provider_export_manifest(
        tmp_path, art, provider, t1_manifest, mode="aggregate", collection_window=window,
    )
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest, mode="aggregate",
        collection_window=window, tolerance_pct=5.0,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
        mode="aggregate", tolerance_pct=5.0, since=start, until=end,
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
        mode="aggregate",
        tolerance_pct=5.0,
        since=start,
        until=end,
    )

    tolerance_check = [c for c in packet["gates"]["science"]["checks"]
                       if c["name"] == "aggregate_reconciliation_tolerance_within_threshold"][0]
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert tolerance_check["pass"] is False
    assert "tolerance_pct must be <= 2" in tolerance_check["detail"]
    assert packet["validation_plan"]["ok"] is False
    assert "validation_plan_valid" in packet["gates"]["science"]["failed"]
    assert "science:aggregate_reconciliation_tolerance_within_threshold" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_collection_windows_must_have_parseable_bounds_and_order(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_malformed_windows")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    provider_data = json.loads(provider_export_manifest.read_text(encoding="utf-8"))
    provider_data["collection_window"] = {
        "since": "2026-06-09T02:00:00Z",
        "until": "2026-06-09T01:00:00Z",
    }
    provider_export_manifest.write_text(json.dumps(provider_data), encoding="utf-8")
    corpus_manifest = _corpus_manifest(
        tmp_path,
        art,
        usage_log,
        provider,
        record_count=_record_count(usage_log),
        t1_manifest=t1_manifest,
        provider_export_manifest=provider_export_manifest,
        collection_window={"since": "not-a-date", "until": "2026-06-09T01:00:00Z"},
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    corpus_check = [c for c in packet["gates"]["science"]["checks"]
                    if c["name"] == "corpus_manifest_valid"][0]
    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    plan_check = [c for c in packet["gates"]["science"]["checks"]
                  if c["name"] == "validation_plan_valid"][0]
    assert "collection_window.since must be numeric epoch or ISO-8601" in corpus_check["detail"]
    assert "collection_window.since must be before collection_window.until" in provider_check["detail"]
    assert "validation_plan.collection_window.since must be numeric epoch or ISO-8601" in plan_check["detail"]
    assert "science:corpus_manifest_valid" in packet["external_blockers"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert "science:validation_plan_valid" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_collection_windows_must_have_positive_duration(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_zero_duration_windows")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    window = {"since": "2026-06-09T00:00:00Z", "until": "2026-06-09T00:00:00Z"}
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(
        tmp_path, art, provider, t1_manifest, collection_window=window,
    )
    corpus_manifest = _corpus_manifest(
        tmp_path,
        art,
        usage_log,
        provider,
        record_count=_record_count(usage_log),
        t1_manifest=t1_manifest,
        provider_export_manifest=provider_export_manifest,
        collection_window=window,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    corpus_check = [c for c in packet["gates"]["science"]["checks"]
                    if c["name"] == "corpus_manifest_valid"][0]
    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    plan_check = [c for c in packet["gates"]["science"]["checks"]
                  if c["name"] == "validation_plan_valid"][0]
    assert "collection_window.since must be before collection_window.until" in corpus_check["detail"]
    assert "collection_window.since must be before collection_window.until" in provider_check["detail"]
    assert "validation_plan.collection_window.since must be before validation_plan.collection_window.until" in plan_check["detail"]
    assert packet["gates"]["science"]["status"] == "FAIL"
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert "science:corpus_manifest_valid" in packet["external_blockers"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert "science:validation_plan_valid" in packet["external_blockers"]


def test_collection_window_bounds_must_not_be_booleans(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_boolean_windows")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    window = {"since": False, "until": True}
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(
        tmp_path,
        art,
        provider,
        t1_manifest,
        collection_window=window,
        date="1970-01-01",
    )
    corpus_manifest = _corpus_manifest(
        tmp_path,
        art,
        usage_log,
        provider,
        record_count=_record_count(usage_log),
        t1_manifest=t1_manifest,
        provider_export_manifest=provider_export_manifest,
        collection_window=window,
        date="1970-01-01",
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    corpus_check = [c for c in packet["gates"]["science"]["checks"]
                    if c["name"] == "corpus_manifest_valid"][0]
    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    plan_check = [c for c in packet["gates"]["science"]["checks"]
                  if c["name"] == "validation_plan_valid"][0]
    assert "collection_window.since must be numeric epoch or ISO-8601" in corpus_check["detail"]
    assert "collection_window.until must be numeric epoch or ISO-8601" in provider_check["detail"]
    assert "validation_plan.collection_window.since must be numeric epoch or ISO-8601" in plan_check["detail"]
    assert art._aggregate_reconciliation_window_errors("aggregate", False, True) == [
        "aggregate mode requires numeric --since and --until receipt filters"
    ]
    assert packet["gates"]["science"]["status"] == "FAIL"
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert "science:corpus_manifest_valid" in packet["external_blockers"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert "science:validation_plan_valid" in packet["external_blockers"]


def test_collection_window_bounds_must_be_finite(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_nonfinite_windows")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    window = {"since": "nan", "until": "inf"}
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(
        tmp_path, art, provider, t1_manifest, collection_window=window,
    )
    corpus_manifest = _corpus_manifest(
        tmp_path,
        art,
        usage_log,
        provider,
        record_count=_record_count(usage_log),
        t1_manifest=t1_manifest,
        provider_export_manifest=provider_export_manifest,
        collection_window=window,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    corpus_check = [c for c in packet["gates"]["science"]["checks"]
                    if c["name"] == "corpus_manifest_valid"][0]
    provider_check = [c for c in packet["gates"]["science"]["checks"]
                      if c["name"] == "provider_export_manifest_valid"][0]
    plan_check = [c for c in packet["gates"]["science"]["checks"]
                  if c["name"] == "validation_plan_valid"][0]
    assert "collection_window.since must be numeric epoch or ISO-8601" in corpus_check["detail"]
    assert "collection_window.until must be numeric epoch or ISO-8601" in provider_check["detail"]
    assert "validation_plan.collection_window.since must be numeric epoch or ISO-8601" in plan_check["detail"]
    assert art._window_bound_to_epoch("nan") is None
    assert art._window_bound_to_epoch("inf") is None
    assert art._window_bound_to_epoch("-inf") is None
    assert packet["gates"]["science"]["status"] == "FAIL"
    assert packet["ship_scope"] == "internal_or_demo_only"
    assert "science:corpus_manifest_valid" in packet["external_blockers"]
    assert "science:provider_export_manifest_valid" in packet["external_blockers"]
    assert "science:validation_plan_valid" in packet["external_blockers"]


def test_artifact_numeric_config_must_be_finite_before_public_json(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_nonfinite_config")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    window = {"since": "2026-06-09T00:00:00Z", "until": "2026-06-09T01:00:00Z"}

    with pytest.raises(ValueError, match="replay_pass_rate must be a finite number"):
        art.build_artifact(
            str(usage_log),
            attestation=str(att_path),
            t1_manifest=str(t1_manifest),
            provider_export=str(provider),
            provider_export_manifest=str(provider_export_manifest),
            tolerance_pct=5.0,
            replay_pass_rate=float("nan"),
        )

    with pytest.raises(ValueError, match="tolerance_pct must be a finite number"):
        art.build_artifact(
            str(usage_log),
            attestation=str(att_path),
            t1_manifest=str(t1_manifest),
            provider_export=str(provider),
            provider_export_manifest=str(provider_export_manifest),
            tolerance_pct=float("inf"),
        )

    with pytest.raises(ValueError, match="since must be a finite number"):
        art.build_artifact(
            str(usage_log),
            attestation=str(att_path),
            t1_manifest=str(t1_manifest),
            provider_export=str(provider),
            provider_export_manifest=str(provider_export_manifest),
            since=float("inf"),
        )

    with pytest.raises(ValueError, match="min_independent_agreement must be a finite number"):
        art.write_corpus_manifest(
            tmp_path / "corpus_manifest.json",
            str(usage_log),
            provider_export=str(provider),
            provider_export_manifest=str(provider_export_manifest),
            t1_manifest=str(t1_manifest),
            provider="OpenRouter",
            source="real provider gateway export",
            source_reference="usage-export-ref-2026-06-09",
            date="2026-06-09",
            collection_window=window,
            min_independent_agreement=float("nan"),
        )


def test_public_json_boundary_rejects_nonstandard_numbers(tmp_path):
    art = _load(_ARTIFACT, "kry_verified_artifact_strict_json")

    with pytest.raises(ValueError, match="Out of range float values"):
        art._json_pretty({"bad": float("nan")})

    with pytest.raises(ValueError, match="Out of range float values"):
        art._artifact_hash({
            "schema": "kry_verified_savings_artifact/v1",
            "artifact_hash": "",
            "bad": float("inf"),
        })

    bad_artifact = tmp_path / "artifact.json"
    bad_artifact.write_text('{"schema":"kry_verified_savings_artifact/v1","bad":NaN}\n', encoding="utf-8")

    result = art.verify_artifact_file(bad_artifact, require_packet_surfaces=False)

    assert result["ok"] is False
    assert result["errors"] == ["artifact unreadable: non-standard JSON constant rejected: NaN"]


def test_corpus_and_provider_collection_windows_must_align(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_corpus_provider_window_mismatch")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path,
        art,
        usage_log,
        provider,
        record_count=_record_count(usage_log),
        t1_manifest=t1_manifest,
        provider_export_manifest=provider_export_manifest,
        collection_window={"since": "2026-06-09T02:00:00Z", "until": "2026-06-09T03:00:00Z"},
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest,
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

    window_check = [c for c in packet["gates"]["science"]["checks"]
                    if c["name"] == "corpus_provider_windows_align"][0]
    assert packet["gates"]["product"]["status"] == "PASS"
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert window_check["pass"] is False
    assert "corpus window" in window_check["detail"]
    assert "science:corpus_provider_windows_align" in packet["external_blockers"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_review_evidence_must_bind_provider_export_manifest(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_missing_provider_review_binding")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    t1_manifest = tmp_path / "t1_manifest.json"
    art.write_t1_manifest(str(log), t1_manifest)
    provider_export_manifest = _provider_export_manifest(tmp_path, art, provider, t1_manifest)
    corpus_manifest = _corpus_manifest(
        tmp_path, art, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=t1_manifest, provider_export_manifest=provider_export_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, art, usage_log, att_path, provider, corpus_manifest, t1_manifest, provider_export_manifest=None,
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

    outside_check = [c for c in packet["gates"]["external_review"]["checks"]
                     if c["name"] == "outside_review_valid"][0]
    assert packet["gates"]["science"]["status"] == "PASS"
    assert outside_check["pass"] is False
    assert "provider_export_manifest_sha256 mismatch" in outside_check["detail"]
    assert packet["ship_scope"] == "internal_or_demo_only"


def test_provider_discrepancy_triggers_kill_gate(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_discrepancy")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 1, "completion_tokens": 1}]), encoding="utf-8")

    packet = art.build_artifact(
        str(usage_log),
        attestation=str(att_path),
        mint_log=str(log),
        provider_export=str(provider),
        corpus="real",
    )

    assert packet["research_assessment"]["reconcile"]["verdict"] == "DISCREPANCY"
    assert "reconciliation_discrepancy" in packet["gates"]["kill"]["triggers"]
    assert "independent_agreement_below_bar" in packet["gates"]["kill"]["triggers"]
    assert "kill:reconciliation_discrepancy" in packet["external_blockers"]
    assert "kill:independent_agreement_below_bar" in packet["external_blockers"]
    assert packet["ship_scope"] == "do_not_ship"


def test_tampered_attestation_triggers_kill_gate(minted_sample, tmp_path):
    usage_log, att_path, log = minted_sample
    art = _load(_ARTIFACT, "kry_verified_artifact_tamper")
    tampered = tmp_path / "tampered.json"
    att = json.loads(att_path.read_text(encoding="utf-8"))
    att["total_kry"] += 1
    tampered.write_text(json.dumps(att), encoding="utf-8")

    packet = art.build_artifact(str(usage_log), attestation=str(tampered), mint_log=str(log))

    assert packet["gates"]["product"]["status"] == "FAIL"
    assert "attestation_failed" in packet["gates"]["kill"]["triggers"]
    assert "product:attestation_verifies" in packet["external_blockers"]
    assert "kill:attestation_failed" in packet["external_blockers"]
    assert packet["ship_scope"] == "do_not_ship"
