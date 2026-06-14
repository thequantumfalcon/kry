"""Public-facing claim drift checks.

These are not documentation style tests. They guard the stranger-facing evidence
surface against stale verification instructions and over-strong proof language.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_uses_current_plain_pytest_command():
    readme = _read("README.md")
    readiness = _read("docs/KRY_READINESS.md")
    assert "python3 -m pytest tests/ -q" in readme
    assert "PYTHONPATH=src" not in readme
    assert "python scripts/kry_" not in readiness
    assert "python3 scripts/kry_research_grade.py" in readiness


def test_lab_reproducibility_paths_use_python3_and_avoid_stale_counts():
    harness = _read("lab/reproduce.sh")
    playbook = _read("lab/PLAYBOOK.md")
    results_template = _read("lab/RESULTS_TEMPLATE.md")
    assert 'PYTHON="${PYTHON:-python3}"' in harness
    assert "python -m pytest" not in harness
    assert "python3 -m pytest tests/ -q" in playbook
    assert "133 passed" not in playbook
    assert "148-test suite" not in playbook
    assert "133 passed" not in results_template


def test_live_public_docs_do_not_advertise_stale_test_counts():
    live_docs = {
        "README.md": _read("README.md"),
        "docs/KRY_READINESS.md": _read("docs/KRY_READINESS.md"),
        "docs/KRY_VERIFIED_SAVINGS_ARTIFACT.md": _read("docs/KRY_VERIFIED_SAVINGS_ARTIFACT.md"),
    }
    stale_claims = (
        "127 tests",
        "172 stdlib",
        "172 stdlib pass",
        "215 tests",
        "215 with optional",
    )
    for path, text in live_docs.items():
        for claim in stale_claims:
            assert claim not in text, f"{path} contains stale public claim: {claim}"


def test_readme_labels_the_sample_log_as_synthetic():
    readme = _read("README.md")
    assert "examples/sample_usage_log.jsonl is synthetic" in readme


def test_readme_separates_runtime_dependencies_from_test_tooling():
    readme = _read("README.md")
    assert "No runtime package dependencies" in readme
    assert "Tests use `pytest`" in readme
    assert "no third-party packages" not in readme


def test_readme_does_not_call_visualization_the_proof():
    readme = _read("README.md")
    assert "the animation is the proof" not in readme
    assert "the proof is the attestation plus verifier" in readme


def test_public_docs_name_the_verified_artifact_gate():
    readme = _read("README.md")
    doc = _read("docs/KRY_VERIFIED_SAVINGS_ARTIFACT.md")
    assert "scripts/kry_doctor.py" in readme
    assert "scripts/kry_doctor.py" in doc
    assert "scripts/kry_finops_report.py" in readme
    assert "scripts/kry_finops_report.py" in doc
    assert "doctor command buyers should run" in readme
    assert "--artifact packet/artifact.json" in readme
    assert "--artifact packet/artifact.json" in doc
    assert "scripts/kry_verified_artifact.py" in readme
    assert "scripts/kry_verified_artifact.py" in doc
    assert "--bundle-dir packet" in readme
    assert "--template-dir" in readme
    assert "evidence_templates" in readme
    assert "--write-t1-manifest t1_manifest.json" in readme
    assert "--write-t1-manifest t1_manifest.json" in doc
    assert "--write-provider-export-manifest provider_export_manifest.json" in doc
    assert "--write-corpus-manifest corpus_manifest.json" in doc
    assert "--write-provider-export-manifest" in readme
    assert "--write-corpus-manifest" in readme
    assert "This fills mechanical fields" in doc
    assert "is not outside review, buyer feedback, or legal approval" in doc
    assert "refuses blank, `TODO`, generic, or copied option-list provenance fields" in doc
    assert "provenance or identity fields with newlines or control\ncharacters" in doc
    assert "reused `evidence_reference` values across outside-review, buyer-feedback, and\nlegal-review channels" in doc
    assert "reused named actors across those three channels" in doc
    assert "zero provider records before writing the manifest" in doc
    assert "zero provider token totals before writing the manifest" in doc
    assert "the bundled sample cannot satisfy --corpus real" in readme
    assert "blocks that exact file content from supporting `--corpus real`" in doc
    assert "--verify-artifact packet/artifact.json" in readme
    assert "--verify-artifact packet/artifact.json" in doc
    assert "artifact_hash" in doc
    assert "does five checks" in doc
    assert "reviewer_checklist.json` or `finops_report.md` packet surface is missing" in doc
    assert "both `reviewer_checklist.json` and `finops_report.md`" in doc
    assert "private runtime or mint-log files" in doc
    assert "extra unbound directory or regular file" in doc
    assert "non-regular packet entry" in doc
    assert "Aggregate-mode provider exports must be built with explicit `--since` and\n`--until` receipt filters" in doc
    assert "inline raw payload text, such as `prompt: ...`, `messages: ...`, or\n`raw_response=...`" in doc
    assert "symlink" in doc
    assert "artifact entrypoint must be" in doc
    assert "absolute" in doc
    assert "private `mint_log`" in doc
    assert "relative `command_inputs`" in doc
    assert "kry_t1_reconciliation_manifest/v1" in doc
    assert "kry_provider_export_manifest/v1" in doc
    assert "provider_export_manifest.template.json" in doc
    assert "tool_manifest.json" in doc
    assert "review_basis.json" in doc
    assert "reviewer_checklist.json" in doc
    assert "external_review_request.md" in doc
    assert "buyer_feedback_request.md" in doc
    assert "legal_review_request.md" in doc
    assert "hash-bound request briefs" in readme
    assert "The `*_request.md` briefs are also not evidence" in doc
    assert "verify_command" in doc
    assert "doctor_command" in doc
    assert "packet-level checks" in doc
    assert "both must pass before handoff" in doc
    assert "finops_report.md" in doc
    assert "kry_reviewer_checklist/v1" in doc
    assert "buyer_local_privacy_boundary" in doc
    assert "buyer_local_evidence_gates" in doc
    assert "kry_finops_report/v1" in doc
    assert "product/science/external-review/kill gate\nstatus" in doc
    assert "claim-evidence manifest status and blocker counts" in doc
    assert "validation-plan thresholds" in doc
    assert "T1\nmanifest-to-attestation binding counts" in doc
    assert "saved-packet/checklist/report verification" in readme
    assert "fails if artifact ship_scope is do_not_ship" in readme
    assert "treats externally claimable artifacts as packet-shaped handoffs" in readme
    assert "packet-shaped artifact is missing its checklist or report" in readme
    assert "command_inputs depend on absolute local paths" in readme
    assert "artifact-specific external_evidence_status" in readme
    assert "private mint-log or ledger material" in readme
    assert "symlinks appear in the shareable packet" in readme
    assert "unbound directories, files, or non-regular entries" in readme
    assert "artifact-specific" in doc
    assert "upstream truth" in doc
    assert "packet/reviewer_checklist.json" in doc
    assert "required_kill_criteria" in doc
    assert "packet/finops_report.md" in doc
    assert "Any externally claimable artifact is treated" in doc
    assert "requires packet-shaped artifacts" in doc
    assert "fails valid artifacts whose `ship_scope` is `do_not_ship`" in doc
    assert "match fresh renders" in doc
    assert "command_inputs` use relative paths" in doc
    assert "stay inside the packet" in doc
    assert "private mint-log or ledger files are absent" in doc
    assert "unbound directories or files are absent" in doc
    assert "t1_manifest_sha256" in doc
    assert "provider_export_manifest_sha256" in doc
    assert "kry_tool_manifest/v1" in doc
    assert "tool_manifest_sha256" in doc
    assert "mint receipt" in doc
    assert "public attestation generation" in doc
    assert "event counts match" in doc
    assert "match `--since`/`--until`" in doc
    assert "does not align with the provider export manifest window" in doc
    assert "missing or malformed collection window" in doc
    assert "malformed, boolean-bounded, non-finite, zero-length, or reversed collection window" in doc
    assert "missing an ISO-8601 date" in doc
    assert "review dates before the corpus/provider manifest dates" in doc
    assert "same-day review timestamps before a timestamped corpus/provider manifest" in doc
    assert "date` fields must be ISO-8601 strings, not Unix epochs" in doc
    assert "has a future date" in doc
    assert "future-dated reviews" in doc
    assert "same-day future review timestamps" in doc
    assert "source_reference" in doc
    assert "blank or copied option-list source or source reference" in doc
    assert "export_reference" in doc
    assert "blank or copied option-list provider, export source, or export reference" in doc
    assert "source_mint_log_sha256` must be a 64-character lowercase" in doc
    assert "receipt_count` must be a JSON integer, not a boolean" in doc
    assert "refuses to write a live T1 manifest if the selected receipt set is" in doc
    assert "record_count` is not a JSON integer" in doc
    assert "provider_record_count` is not a JSON integer" in doc
    assert "provider_record_count` is not greater than zero" in doc
    assert "zero-length" in doc
    assert "JSON booleans or non-finite numeric strings as collection-window bounds" in doc
    assert "non-finite numeric strings as collection-window bounds" in doc
    assert "Artifact numeric config values must be finite before public JSON is written" in doc
    assert "`tolerance_pct`, `replay_pass_rate`, receipt filters, and validation-plan\nagreement floors reject `NaN` and `Infinity`" in doc
    assert "Generated artifact, template, bundle, public attestation, and verifier JSON uses\nstrict serialization and parsing" in doc
    assert "Usage logs, T1 manifests, OpenRouter fetch responses, provider exports, and\nresearch-grade provider-export reads also reject non-standard JSON constants" in doc
    assert "TEE/SNP/TLSNotary verifier evidence inputs reject non-standard JSON constants,\nnon-integer provider token counts, and non-finite savings bases before minting" in doc
    assert "Lab router, truth, and measured-energy artifacts use the same strict boundary:\nnon-standard JSON constants and non-finite measured values fail before proof\nreports are generated" in doc
    assert "provider token count" in doc
    assert "normalized provider\nexport token total is not greater than zero" in doc
    assert "blank reviewer or buyer identity fields" in doc
    assert "single flat usage record" in doc
    assert "provider_record_count = 1" in doc
    assert "Provider token counts must be non-negative JSON integers" in doc
    assert "kry_review_basis/v1" in doc
    assert "review_basis_sha256" in doc
    assert "kry_validation_plan/v1" in doc
    assert "registered_date" in doc
    assert "before the collection-window start" in doc
    assert "same-day timestamps after the collection-window" in doc
    assert "pre-registration guard" in doc
    assert "kill criteria with no `TODO` placeholder entries" in doc
    assert "buyer materiality or reliance threshold not met" in doc
    assert "invalid, revoked, or voided mint discovered after publication" in doc
    assert "revocation_or_void_status_checked" in doc
    assert "no_invalid_revoked_or_voided_mints_known" in doc
    assert "/review_evidence/legal_review/summary/external_claim_allowed" in doc
    assert "/review_evidence/legal_review/summary/tradeable_token_disclaimed" in doc
    assert "window, tolerance, or sample-size floor" in doc
    assert "hash_version = 3" in doc
    assert "must be JSON integer" in doc
    assert "not strings, booleans, floats, or longer arrays" in doc
    assert "are malformed or differ from the attestation link" in doc
    assert "T1 receipt `ts` is also public, content-free\nmetadata needed to bind aggregate billing windows" in doc
    assert "whose `ts` is missing, malformed, or differs from the attestation\nlink" in doc
    assert "does **not** copy" in doc
    assert "fails the packet if private mint-log or ledger material" in doc
    assert "private mint log stays local" in readme
    assert "kry_external_evidence_template/v1" in doc
    assert "They do **not** pass the packet gates" in doc
    assert "do not return prompts, completions, raw messages" in doc
    assert "outside-review, buyer-feedback, or legal-review JSON evidence" in doc
    assert "private prompt/message/content/request/response fields" in doc
    assert "public attestation/T1/provider-export/corpus manifest JSON" in doc
    assert "public manifest JSON, and review evidence" in doc
    assert "generic placeholders such as `N/A`, `none`" in doc
    assert "provider-authoritative cost/usage data" in doc
    assert "a named proof-required" in doc
    assert "an accepted measured/projected baseline before analysis" in doc
    assert "`<=2%` after approved exclusions" in doc
    assert "`>=10%` avoidable spend" in doc
    assert "Any live-schema manifest or review evidence that still contains `TODO...`" in doc
    assert "unreplaced `TODO` placeholders" in doc
    assert "copied placeholder option lists" in doc
    assert "URL / file id / note id" in doc
    assert "internal_or_demo_only" in doc
    assert "external_verified_savings_candidate" in doc
    assert "claim_register" in doc
    assert "kry_claim_register/v1" in doc
    assert "external_blockers` list mirrors product, science, external-review" in doc
    assert "real corpus manifest" in doc
    assert "attested" in doc
    assert "claim_evidence_manifest" in doc
    assert "kry_claim_evidence_manifest/v1" in doc
    assert "must bind to `artifact.json`, the artifact's current hash, and the artifact's\ncurrent `ship_scope`" in doc
    assert "/review_evidence/outside_review" in doc
    assert "/review_evidence/buyer_feedback" in doc
    assert "/review_evidence/legal_review" in doc
    assert "evidence_source" in doc
    assert "evidence_reference" in doc
    assert "reviewed_claims" in doc
    assert '["external_verified_savings", "tradeable_token"]' in doc
    assert "reviewer_artifact_checks" in doc
    assert "verify_artifact_command_run" in doc
    assert "doctor_command_run" in doc
    assert "outside-review artifact-check fields not set to `true`" in doc
    assert "reviewer_command_outputs" in doc
    assert "verify_artifact_error_count" in doc
    assert "doctor_fail_count" in doc
    assert "outside-review command-output flags not set to `true`" in doc
    assert "JSON number `0`" in doc
    assert '"buyer": "name / organization / counterparty id"' in doc
    assert "buyer_local_evidence_gates" in doc
    assert "proof_required_reader_named" in doc
    assert "seven_day_window_or_data_supplied" in doc
    assert "buyer-local gate fields not set to `true`" in doc
    assert "buyer_threshold_context" in doc
    assert "buyer_threshold_context_fields" in doc
    assert "provider_or_bill_data_source" in doc
    assert "baseline_reference" in doc
    assert "buyer threshold-context fields" in doc
    assert "legal_claim_checks" in doc
    assert "legal_limitations" in doc
    assert "external_claim_text_checked" in doc
    assert "carbon_language_checked" in doc
    assert "credit/settlement/routing-permission wording" in doc
    assert "token disclaimers" in doc
    assert "missing `legal_limitations`" in doc
    assert "legal claim-check fields not set to `true`" in doc
    assert "missing provenance source/reference" in doc
    assert "missing `reviewed_claims`" in doc
    assert "entries that are not `claim_register` IDs" in doc
    assert "kry_finops_report/v1" in doc
    assert "retained" in doc
    assert "It refuses to render" in doc
    assert "machine-readable field" in doc
    assert "machine-readable artifact field reference resolves inside the packet" in doc
    assert "tradeable-token claim" in doc
    assert "research_grade_readiness` claim stays blocked unless product" in doc
    assert "production_ready` claim stays blocked" in doc
    assert "claim-register checks" in doc


def test_verified_artifact_docs_require_structured_json_evidence():
    readme = _read("README.md")
    doc = _read("docs/KRY_VERIFIED_SAVINGS_ARTIFACT.md")
    assert "kry_corpus_manifest/v1" in doc
    assert "kry_external_evidence/v1" in doc
    assert "--corpus-manifest corpus_manifest.json" in readme
    assert "--provider-export-manifest provider_export_manifest.json" in readme
    assert "outside_review.json" in readme
    assert "buyer_feedback.json" in readme
    assert "legal_review.json" in readme
    assert "outside_review.md" not in doc
    assert "buyer_feedback.md" not in doc
    assert "legal_review.md" not in doc


def test_public_docs_describe_t1_metered_token_binding():
    readme = _read("README.md")
    veracity = _read("docs/KRY_VERACITY_BINDING.md")
    artifact = _read("docs/KRY_VERIFIED_SAVINGS_ARTIFACT.md")
    assert "hash-bind their `metered_tokens`" in readme
    assert "hash-bind their `metered_tokens`" in veracity
    assert "T1 `metered_tokens`" in artifact
    assert "stdlib attestation verifier rejects" in artifact
    assert "must be JSON integers, not strings, booleans, or floats" in veracity
