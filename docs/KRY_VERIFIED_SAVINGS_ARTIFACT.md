# KRY Verified-Savings Artifact

This is the smallest packet KRY should show to a buyer, outside reviewer, or legal
reviewer without overstating what has been proven.

The packet is built by `scripts/kry_verified_artifact.py`. It does not create new
evidence. It composes the existing evidence and emits explicit gates:

- **Product gate:** the usage log has records, savings are positive, the public
 attestation verifies, and the attestation total/veracity floor/event counts match
 the savings report.
- **Science gate:** the corpus is operator-declared real, a structured corpus
 manifest is attached and hash-bound to the usage/provider files, that manifest
 carries a pre-registered `kry_validation_plan/v1` block, a provider export and
 provider-export provenance manifest are attached, the provider oracle is
 non-vacuous, independent agreement meets the 0.80 bar, and the readiness label
 can reach `production_ready`.
- **External-review gate:** outside verification, buyer feedback, and legal/claims
 review files are structured JSON evidence, hash-bound to the packet inputs and
 review-basis/toolchain configuration, and hashed into the packet.
- **Kill gate:** failed attestation, no positive savings, provider reconciliation
 discrepancy, assessment error, missing T1 reconciliation source with a supplied
 provider export, or independent agreement below the bar.

## T1 Manifest Privacy Boundary

The private mint log remains local. When `--bundle-dir` is used with `--mint-log`,
the builder derives `packet/t1_manifest.json` and does **not** copy
`kry_mint_log.jsonl` into the packet. `python3 scripts/kry_doctor.py --artifact
packet/artifact.json` fails the packet if private mint-log or ledger material is
later placed beside the shareable artifact, or if the declared usage/provider input
files contain prompt text, completion text, messages, content fields, request bodies,
response bodies, or raw payloads.

`t1_manifest.json` uses `schema = "kry_t1_reconciliation_manifest/v1"` and carries
only the fields needed for provider reconciliation:

```json
{
 "schema": "kry_t1_reconciliation_manifest/v1",
 "source_mint_log_sha256": "...",
 "receipt_count": 1,
 "receipts": [
 {
 "receipt_id": "KRY-...",
 "evidence_tier": "provider_metered",
 "receipt_hash": "...",
 "chain_hash": "...",
 "metered_tokens": [2000, 400],
 "ts": 1781000000.0
 }
 ]
}
```

No prompt text, receipt detail string, self-reported receipts, or broader ledger
history is included. `receipt_hash`, `chain_hash`, and T1 `metered_tokens` are
already public in the attestation; T1 receipt `ts` is also public, content-free
metadata needed to bind aggregate billing windows. Including them lets the packet prove the
provider-reconciled T1 rows are the same provider-metered links that contribute to
the public attestation. `source_mint_log_sha256` must be a 64-character lowercase
hex SHA-256 digest. `receipt_count` must be a JSON integer, not a boolean. T1 manifest `metered_tokens` must be JSON integer
`[prompt, completion]` pairs, not strings, booleans, floats, or longer arrays. New T1 receipts hash-bind `metered_tokens`
(`hash_version = 3`), and the artifact gate rejects a T1 manifest whose token counts
are malformed or differ from the attestation link. The artifact gate also rejects
a T1 manifest whose `ts` is missing, malformed, or differs from the attestation
link, so aggregate receipt-window filters cannot be backed by edited timestamps.
The stdlib attestation verifier rejects a
`provider_metered` link that does not expose valid `metered_tokens` and `ts`. The
saved artifact hashes the manifest and recomputes provider reconciliation from it,
so moving or tampering with the bundle still fails `--verify-artifact`.

For an external candidate, generate the manifest before requesting review evidence:

```bash
python3 scripts/kry_verified_artifact.py \
 --mint-log kry_data/kry_mint_log.jsonl \
 --write-t1-manifest t1_manifest.json
```

Passing science/review gates require `--t1-manifest`; a private mint log alone can
reconcile locally, but it is not the smallest shareable external packet. The
manifest writer refuses to write a live T1 manifest if the selected receipt set is
empty. The manifest must cover every `provider_metered` attestation link, so the
provider oracle denominator cannot be cherry-picked.

## Demo Packet

The sample log is synthetic. It may produce a mechanically valid product artifact,
but it must remain `internal_or_demo_only`.

```bash
python3 scripts/kry_savings_report.py examples/sample_usage_log.jsonl --mint --attest att.json
python3 scripts/kry_verified_artifact.py examples/sample_usage_log.jsonl \
 --attestation att.json --mint-log kry_data/kry_mint_log.jsonl --bundle-dir packet
python3 scripts/kry_verified_artifact.py --verify-artifact packet/artifact.json
```

Expected scope:

```text
ship_scope = internal_or_demo_only
external_verified_savings = false
```

Render the buyer-facing report only after the artifact verifies:

```bash
python3 scripts/kry_finops_report.py packet/artifact.json
```

The report uses `schema = "kry_finops_report/v1"` internally. It shows retained
dollars, veracity floor, claim status, product/science/external-review/kill gate
status, claim-evidence manifest status and blocker counts, external blockers,
non-private evidence provenance references, validation-plan thresholds, and
buyer materiality threshold values when verified buyer feedback supplied them. It also shows T1
manifest-to-attestation binding counts when a shareable T1 manifest is present.
It refuses to render
a usable report from an artifact that fails `--verify-artifact`, and it keeps
`external_verified_savings` blocked in the human-facing output unless the
`claim_register` allows that claim. Missing buyer materiality renders as
unavailable rather than being inferred, and missing evidence provenance renders
as unavailable rather than inventing source references. The standalone report
command enforces the external packet surfaces too: an externally claimable artifact missing
`packet/reviewer_checklist.json` or a current `packet/finops_report.md` does not
get a usable buyer-facing report. Bundle mode writes the same derived report to
`packet/finops_report.md` during packet creation, after `packet/artifact.json`
verifies. The report also prints the packet `doctor_command`, so a buyer can run
the same packet-level checks as the reviewer checklist.

## External Candidate Packet

An externally credible candidate needs the same artifact plus the real evidence
files. The corpus flag is an operator declaration, not proof by itself; the packet
records that fact and requires the provider oracle and review files as separate
evidence.

```bash
python3 scripts/kry_verified_artifact.py usage.jsonl \
 --attestation attestation.json \
 --t1-manifest t1_manifest.json \
 --provider-export provider_usage.json \
 --provider-export-manifest provider_export_manifest.json \
 --corpus real \
 --corpus-manifest corpus_manifest.json \
 --outside-review outside_review.json \
 --buyer-feedback buyer_feedback.json \
 --legal-review legal_review.json \
 --bundle-dir packet
python3 scripts/kry_verified_artifact.py --verify-artifact packet/artifact.json
python3 scripts/kry_finops_report.py packet/artifact.json
```

Expected scope only when all gates pass:

```text
ship_scope = external_verified_savings_candidate
external_verified_savings = true
```

The CLI refuses to write an externally claimable candidate with bare `--out`;
use `--bundle-dir` so the artifact, relative inputs, reviewer checklist, FinOps
report, and packet privacy checks are generated together.

## Machine-Readable Claim Register

Every artifact includes `claim_register` with `schema = "kry_claim_register/v1"`.
It lists each public claim, its status (`allowed`, `blocked`, or `forbidden`), the
evidence fields that support it, and the gate blockers that prevent it. The register
covers internal efficiency use, external verified savings, provider reconciliation,
real-corpus validation, research-grade readiness, production-ready readiness,
external review completion, and the always-forbidden tradeable-token claim. The
legacy `claim_allowed` booleans are derived from this register.
The top-level `external_blockers` list mirrors product, science, external-review,
and kill-gate blockers for the external verified-savings claim.
The `real_corpus_validated` claim is blocked unless the real corpus manifest,
provider export manifest, validation plan, aligned collection window, and attested
T1 manifest binding all support it.

The `production_readiness_if_claimed` label can show the mechanical readiness
rubric result, but public readiness claims are stricter than the label alone:
the `research_grade_readiness` claim stays blocked unless product, science, and
kill gates support the packet, and the `production_ready` claim stays blocked
unless product, science, external-review, and kill gates all support it.

Every artifact also includes `claim_evidence_manifest` with
`schema = "kry_claim_evidence_manifest/v1"`. It mirrors each `claim_register`
entry and maps that claim to concrete artifact fields such as `/gates/science`,
`/review_evidence/outside_review`, `/review_evidence/buyer_feedback`,
`/review_evidence/legal_review`, `/research_assessment/reconcile`, and
`/production_readiness_if_claimed`. It also carries the packet verification command
and must bind to `artifact.json`, the artifact's current hash, and the artifact's
current `ship_scope`.
This lets a reviewer audit public claims by following machine-readable field
references instead of trusting prose.

## Evidence Request Templates

Before the external evidence exists, generate non-passing request templates with the
hashes that reviewers/buyers/legal reviewers need to bind their response to the exact
inputs:

```bash
python3 scripts/kry_verified_artifact.py usage.jsonl \
 --attestation attestation.json \
 --t1-manifest t1_manifest.json \
 --provider-export provider_usage.json \
 --provider-export-manifest provider_export_manifest.json \
 --corpus real \
 --corpus-manifest corpus_manifest.json \
 --template-dir evidence_templates
```

This writes:

```text
evidence_templates/tool_manifest.json
evidence_templates/review_basis.json
evidence_templates/reviewer_checklist.json
evidence_templates/corpus_manifest.template.json
evidence_templates/provider_export_manifest.template.json
evidence_templates/outside_review.template.json
evidence_templates/buyer_feedback.template.json
evidence_templates/legal_review.template.json
evidence_templates/provider_export_request.md
evidence_templates/external_review_request.md
evidence_templates/buyer_feedback_request.md
evidence_templates/legal_review_request.md
```

`tool_manifest.json`, `review_basis.json`, and `reviewer_checklist.json` are
inspectable basis files. `reviewer_checklist.json` uses
`schema = "kry_reviewer_checklist/v1"`. They are not external evidence, but their
hashes and instructions tell reviewers what to verify and what values to copy into
their completed outside review, buyer feedback, and legal review JSON. The checklist
carries both `verify_command` and `doctor_command`; both must pass before handoff.
It also carries structured `buyer_local_privacy_boundary` and
`buyer_local_evidence_gates` lists so the packet-shaped handoff preserves the
threshold evidence target even when the Markdown request briefs are not nearby.
It carries `buyer_threshold_context_fields` so buyer feedback has to name the
actual reader, data path, baseline, authority, quality boundary, materiality
basis, and sample window without returning private prompts or completions.
It also carries `buyer_materiality_threshold`, requiring the completed buyer
feedback JSON to include `avoidable_spend_pct >= 10` or
`plausible_monthly_savings_usd >= 5000`.
It carries `required_kill_criteria` so reviewer handoff checks the same provider,
science, review, buyer-threshold, privacy, and revocation/voiding kill gates that
the validation plan must contain.
It carries `legal_claim_checks` for external claim text, retained-dollars wording,
credit/settlement/routing-permission wording, carbon wording, token disclaimers,
non-transferability, and recorded limitations.
The `*_request.md` briefs are also not evidence. They are sendable requests that name
the files to return, the schemas to complete, and the verifier/doctor commands the
outside reviewer, buyer, or legal reviewer should run before signing off.
The provider-export and buyer-feedback requests also carry the buyer-local evidence
boundary: do not return prompts, completions, raw messages, raw request bodies, or
raw response bodies; do return provider-authoritative cost/usage data,
request/gateway metadata, hashes, row counts, and gate statuses. They ask the buyer
path to preserve the real-evidence gates KRY needs later: a named proof-required
reader or intended user, provider bill/export/CUR plus request metadata for the same
window, an accepted measured/projected baseline before analysis, budget/customer/
procurement/board/audit/gainshare authority, a seven-day reconciliation target within
`<=2%` after approved exclusions, and materiality of `>=10%` avoidable spend or
`>=$5k/month` plausible realized savings.

Templates deliberately use `kry_corpus_manifest_template/v1`,
`kry_provider_export_manifest_template/v1`, and `kry_external_evidence_template/v1`.
They do **not** pass the packet gates. A reviewer must fill the TODO fields and
change the schema to the live evidence schema only after the provider provenance
statement, review, buyer feedback, or legal assessment has actually happened.
Any live-schema manifest or review evidence that still contains `TODO...`
placeholder text fails the packet gates.

## Review Basis Binding

The artifact includes a `tool_manifest` using `schema = "kry_tool_manifest/v1"`.
It hashes the source files that compute the packet gates, savings math, mint receipt
hashes, public attestation generation and verification, provider reconciliation, and
readiness label. It also hashes the reviewer-facing doctor and FinOps report tools
that outside reviewers are asked to run. A saved packet verified under different
local tool code fails `--verify-artifact` unless it is rebuilt and reviewed under
that toolchain.

The artifact emits a deterministic `review_basis` object using
`schema = "kry_review_basis/v1"`. Its `sha256` covers the packet input hashes plus
the gate settings that affect the review context:

```json
{
 "schema": "kry_review_basis/v1",
 "inputs": {
 "usage_log_sha256": "...",
 "attestation_sha256": "...",
 "provider_export_sha256": "...",
 "provider_export_manifest_sha256": "...",
 "corpus_manifest_sha256": "...",
 "t1_manifest_sha256": "...",
 "tool_manifest_sha256": "..."
 },
 "config": {
 "corpus": "real",
 "mode": "per-request",
 "tolerance": 0,
 "tolerance_pct": 2.0,
 "since": null,
 "until": null,
 "replay_pass_rate": 1.0
 },
 "sha256": "..."
}
```

Every outside review, buyer feedback, and legal review file must include
`tool_manifest_sha256` and `review_basis_sha256` inside `artifact_inputs`. This
prevents a review of one packet, toolchain, or tolerance setting from being reused
for a different final artifact.

## Non-Negotiable Rule

A polished artifact with missing provider export, missing provider-export provenance
manifest, synthetic/internal corpus, no outside review, no buyer feedback, or no
legal review is not an external savings claim. It can be useful as an internal or
demo artifact, but the packet must say so.

## Corpus Manifest Schema

The `--corpus real` flag is not evidence by itself. A passing science gate also
requires `--corpus-manifest`, using `schema = "kry_corpus_manifest/v1"`:

The bundled `examples/sample_usage_log.jsonl` is always synthetic. The science gate
blocks that exact file content from supporting `--corpus real`, even if it is copied
to another path and paired with otherwise valid-looking provider or review files.
The usage log copied into a public bundle must also stay content-free: token counts,
model/routing metadata, cache/holdout flags, and request-class labels are allowed,
but prompt text, completion text, messages, content fields, request bodies, response
bodies, or raw payloads fail `usage_log_public_packet_safe` and block the external claim.

```json
{
 "schema": "kry_corpus_manifest/v1",
 "corpus": "real",
 "date": "2026-06-09",
 "source": "provider gateway / billing export / production traffic window",
 "source_reference": "usage export id / log bundle id / report id / signed note id",
 "non_synthetic": true,
 "record_count": 48,
 "collection_window": {
 "since": "2026-06-09T00:00:00Z",
 "until": "2026-06-09T01:00:00Z"
 },
 "validation_plan": {
 "schema": "kry_validation_plan/v1",
 "registered_date": "2026-06-09",
 "provider": "OpenRouter",
 "reconciliation_mode": "per-request",
 "tolerance": 0,
 "tolerance_pct": 2.0,
 "min_provider_records": 1,
 "min_usage_records": 48,
 "min_independent_agreement": 0.8,
 "collection_window": {
 "since": "2026-06-09T00:00:00Z",
 "until": "2026-06-09T01:00:00Z"
 },
 "outside_review_required": true,
 "buyer_feedback_required": true,
 "legal_review_required": true,
 "kill_criteria": [
 "provider reconciliation discrepancy",
 "independent agreement below bar",
 "missing outside review, buyer feedback, or legal review",
 "quality or SLO regression in counted savings",
 "buyer materiality or reliance threshold not met",
 "private data exposure in public packet",
 "invalid, revoked, or voided mint discovered after publication"
 ]
 },
 "artifact_inputs": {
 "usage_log_sha256": "...",
 "provider_export_sha256": "...",
 "provider_export_manifest_sha256": "...",
 "t1_manifest_sha256": "..."
 }
}
```

The manifest fails if it is missing, not `real`, synthetic, has a blank or copied option-list source or source reference,
missing an ISO-8601 date, has a future date, missing or malformed collection window,
uses JSON booleans or non-finite numeric strings as collection-window bounds, or has a zero-length/reversed collection window,
hash-bound to different usage/provider/provenance/T1 inputs, if `record_count`
is not a JSON integer, if `record_count` does not match the normalized usage log
record count. The corpus manifest fails if it does not align with the provider export manifest window. The nested `validation_plan` must use
`schema = "kry_validation_plan/v1"`, have a non-future `registered_date` on or
before the collection-window start and the corpus/provider evidence dates, name the same provider and
reconciliation mode, match the corpus/provider collection window and gate
tolerance settings, set minimum provider/usage counts and the independent
agreement bar, require outside review/buyer/legal evidence, and list explicit
kill criteria with no `TODO` placeholder entries. The required kill criteria
cover provider mismatch, independent-agreement failure, missing review/buyer/legal
evidence, quality/SLO regression, buyer materiality or reliance failure,
private-data exposure, and invalid/revoked/voided mints. This is the packet's
pre-registration guard against picking the
window, tolerance, or sample-size floor after seeing the provider result. When
`registered_date` includes a time, same-day timestamps after the collection-window
start fail; date-only values are treated as the start of that UTC day.

You can generate the live provider/corpus manifests after collecting the real
provider export and T1 manifest:

```bash
python3 scripts/kry_verified_artifact.py usage.jsonl \
 --provider-export provider_usage.json \
 --t1-manifest t1_manifest.json \
 --provider OpenRouter \
 --export-source "provider generation API export" \
 --export-reference "export-ref-2026-06-09" \
 --corpus-source "real provider gateway export" \
 --corpus-reference "usage-export-ref-2026-06-09" \
 --evidence-date 2026-06-09 \
 --window-since 2026-06-09T00:00:00Z \
 --window-until 2026-06-09T01:00:00Z \
 --write-provider-export-manifest provider_export_manifest.json \
 --write-corpus-manifest corpus_manifest.json
```

This fills mechanical fields: input hashes, provider record count, normalized usage
record count, collection window, and `kry_validation_plan/v1`. The operator still
has to supply the provider name, source, source reference, export reference, evidence date, and window, and the result
is not outside review, buyer feedback, or legal approval.
Artifact numeric config values must be finite before public JSON is written:
`tolerance_pct`, `replay_pass_rate`, receipt filters, and validation-plan
agreement floors reject `NaN` and `Infinity`.
For aggregate-mode external candidate packets, `tolerance_pct` must also be
`<=2.0`; looser aggregate reconciliation can be useful for operator diagnostics,
but it does not satisfy the buyer threshold gate.
Generated artifact, template, bundle, public attestation, and verifier JSON uses
strict serialization and parsing, so nested `NaN`, `Infinity`, and `-Infinity`
values fail instead of becoming public packet bytes.
Usage logs, T1 manifests, OpenRouter fetch responses, provider exports, and
research-grade provider-export reads also reject non-standard JSON constants
before savings, reconciliation, or readiness-gate decisions are computed.
TEE/SNP/TLSNotary verifier evidence inputs reject non-standard JSON constants,
non-integer provider token counts, and non-finite savings bases before minting.
Lab router, truth, and measured-energy artifacts use the same strict boundary:
non-standard JSON constants and non-finite measured values fail before proof
reports are generated.
The generation path is also a live-evidence gate: it refuses blank, `TODO`, generic, or copied option-list provenance fields and malformed dates/windows before writing either live manifest. It is also a privacy gate:
`--write-provider-export-manifest`
refuses provider exports that contain prompt text, message content, request/response
bodies, raw provider payloads, or zero provider records before writing the manifest.
It also refuses zero provider token totals before writing the manifest, and
`--write-corpus-manifest` applies the same check to both the usage log and
provider export before writing the corpus manifest.

## Provider Export Manifest Schema

A passing science gate also requires `--provider-export-manifest`, using
`schema = "kry_provider_export_manifest/v1"`:

```json
{
 "schema": "kry_provider_export_manifest/v1",
 "provider": "OpenRouter",
 "export_source": "provider generation API export",
 "export_reference": "export-ref-2026-06-09",
 "date": "2026-06-09",
 "non_synthetic": true,
 "reconciliation_mode": "per-request",
 "provider_record_count": 48,
 "collection_window": {
 "since": "2026-06-09T00:00:00Z",
 "until": "2026-06-09T01:00:00Z"
 },
 "artifact_inputs": {
 "provider_export_sha256": "...",
 "t1_manifest_sha256": "..."
 }
}
```

The manifest fails if it is missing, not live schema, synthetic, has a blank or copied option-list provider, export source, or export reference, missing an ISO-8601 date, has a future date, hash-bound to different provider/T1
files, has the wrong reconciliation mode, lacks a collection window, has a
malformed, boolean-bounded, non-finite, zero-length, or reversed collection window, has a collection window that does not match `--since`/`--until` when those
reconciliation filters are used, if `provider_record_count` is not a JSON integer, if
`provider_record_count` is not greater than zero, or if `provider_record_count`
does not match the provider export file. It also fails if the normalized provider
export token total is not greater than zero.

For per-request reconciliation, the provider export may be a JSON list, a
`{"data": [...]}` or `{"records": [...]}` envelope, or a single flat usage record
such as `{"prompt_tokens": 2000, "completion_tokens": 400}`. A single flat usage
record counts as `provider_record_count = 1`.

Provider token counts must be non-negative JSON integers, not strings, booleans, or floats,
and the export must include at least one positive provider token count.
Aggregate-mode provider exports must be built with explicit `--since` and
`--until` receipt filters so the T1 reconciliation source is clipped to the same
billed window declared by the provider and corpus manifests.
Aggregate-mode external verified-savings candidates must use `tolerance_pct <= 2.0`;
if billing/unit drift needs more slack than that, the packet remains internal or
diagnostic until the window, units, or exclusions are tightened.
The provider export must also stay inside the public-packet privacy boundary: token
counts and provider metadata are allowed, but fields such as `prompt`, `completion`,
`messages`, `content`, `request_body`, `response_body`, `raw_request`, or
`raw_response` fail `provider_export_manifest_valid` and block the external claim.
Generic provider-export metadata fields also fail if their string values look like
inline raw payload text, such as `prompt: ...`, `messages: ...`, or
`raw_response=...`.

## Review Evidence JSON Schemas

All three evidence files use `schema = "kry_external_evidence/v1"` and must include
`artifact_inputs` binding the evidence to the exact packet inputs and review basis.
All manifest and review `date` fields must be ISO-8601 strings, not Unix epochs.

```json
{
 "artifact_inputs": {
 "usage_log_sha256": "...",
 "attestation_sha256": "...",
 "provider_export_sha256": "...",
 "provider_export_manifest_sha256": "...",
 "corpus_manifest_sha256": "...",
 "t1_manifest_sha256": "...",
 "tool_manifest_sha256": "...",
 "review_basis_sha256": "..."
 }
}
```

Outside review:

```json
{
 "schema": "kry_external_evidence/v1",
 "kind": "outside_review",
 "date": "2026-06-09",
 "evidence_source": "signed reviewer note / issue / email / review packet",
 "evidence_reference": "URL / file id / note id",
 "reviewer": "name / org / role",
 "independent": true,
 "verdict": "verified",
 "reviewer_artifact_checks": {
 "verify_artifact_command_run": true,
 "doctor_command_run": true,
 "claim_register_checked": true,
 "claim_evidence_manifest_checked": true,
 "finops_report_checked": true,
 "hash_bindings_checked": true,
 "template_schema_absent": true,
 "no_private_packet_material": true,
 "revocation_or_void_status_checked": true
 },
 "reviewer_command_outputs": {
 "verify_artifact_ok": true,
 "verify_artifact_error_count": 0,
 "doctor_fail_count": 0,
 "finops_report_rendered": true,
 "claim_register_external_verified_savings_allowed": true,
 "claim_register_tradeable_token_forbidden": true,
 "claim_evidence_manifest_complete": true,
 "no_invalid_revoked_or_voided_mints_known": true
 },
 "reviewed_claims": ["external_verified_savings"],
 "artifact_inputs": {
 "usage_log_sha256": "...",
 "attestation_sha256": "...",
 "provider_export_sha256": "...",
 "provider_export_manifest_sha256": "...",
 "corpus_manifest_sha256": "...",
 "t1_manifest_sha256": "...",
 "tool_manifest_sha256": "...",
 "review_basis_sha256": "..."
 }
}
```

Buyer feedback:

```json
{
 "schema": "kry_external_evidence/v1",
 "kind": "buyer_feedback",
 "date": "2026-06-09",
 "evidence_source": "buyer email / call notes / CRM note / LOI / paid trial record",
 "evidence_reference": "URL / file id / note id",
 "buyer": "name / organization / counterparty id",
 "buyer_role": "AI FinOps / platform / infra buyer",
 "verdict": "qualified_interest",
 "buyer_local_evidence_gates": {
 "proof_required_reader_named": true,
 "provider_or_bill_data_named": true,
 "request_or_gateway_logs_named": true,
 "baseline_accepted": true,
 "authority_named": true,
 "quality_or_slo_named": true,
 "materiality_named": true,
 "seven_day_window_or_data_supplied": true
 },
 "buyer_threshold_context": {
 "proof_required_reader": "finance / customer / procurement / audit / board / gainshare reader",
 "provider_or_bill_data_source": "provider bill / usage export / AWS CUR reference",
 "request_or_gateway_metadata_source": "gateway/request metadata reference without prompts or completions",
 "baseline_reference": "accepted measured/projected baseline reference",
 "authority_basis": "budget / customer / procurement / board / audit / gainshare authority",
 "quality_or_slo_boundary": "quality/SLO boundary for usable savings",
 "materiality_basis": ">=10% avoidable spend or >=$5k/month path",
 "sample_window": "seven-day or supplied sample window reference"
 },
 "buyer_materiality": {
 "avoidable_spend_pct": 12.5,
 "plausible_monthly_savings_usd": 6000.0
 },
 "reviewed_claims": ["external_verified_savings"],
 "artifact_inputs": {
 "usage_log_sha256": "...",
 "attestation_sha256": "...",
 "provider_export_sha256": "...",
 "provider_export_manifest_sha256": "...",
 "corpus_manifest_sha256": "...",
 "t1_manifest_sha256": "...",
 "tool_manifest_sha256": "...",
 "review_basis_sha256": "..."
 }
}
```

Legal/claims review:

```json
{
 "schema": "kry_external_evidence/v1",
 "kind": "legal_review",
 "date": "2026-06-09",
 "evidence_source": "counsel memo / legal ticket / signed note",
 "evidence_reference": "URL / file id / note id",
 "reviewer": "claims counsel / legal reviewer",
 "verdict": "approved_with_limits",
 "external_claim_allowed": true,
 "tradeable_token_disclaimed": true,
 "legal_limitations": [
 "external use limited to claim-register wording and current artifact ship_scope"
 ],
 "legal_claim_checks": {
 "external_claim_text_checked": true,
 "retained_dollars_language_checked": true,
 "credit_settlement_language_checked": true,
 "routing_permission_language_checked": true,
 "carbon_language_checked": true,
 "tradeable_token_disclaimer_checked": true,
 "non_transferable_scope_checked": true,
 "legal_limitations_recorded": true
 },
 "reviewed_claims": ["external_verified_savings", "tradeable_token"],
 "artifact_inputs": {
 "usage_log_sha256": "...",
 "attestation_sha256": "...",
 "provider_export_sha256": "...",
 "provider_export_manifest_sha256": "...",
 "corpus_manifest_sha256": "...",
 "t1_manifest_sha256": "...",
 "tool_manifest_sha256": "...",
 "review_basis_sha256": "..."
 }
}
```

The saved artifact exposes the legal review's `external_claim_allowed` and
`tradeable_token_disclaimed` values in
`/review_evidence/legal_review/summary`. The claim evidence manifest points the
external verified-savings claim at
`/review_evidence/legal_review/summary/external_claim_allowed` and the forbidden
tradeable-token claim at
`/review_evidence/legal_review/summary/tradeable_token_disclaimed`, so reviewers
can audit the exact legal facts without reopening the private source file.

Accepted verdicts are deliberately narrow:

- `outside_review`: `pass`, `verified`, or `accepted`
- `buyer_feedback`: `qualified_interest`, `pilot`, `paid_trial`, `pass`, or `accepted`
- `legal_review`: `approved`, `approved_with_limits`, or `pass`

Plain Markdown notes, blank files, wrong `kind`, missing provenance source/reference
fields, unreplaced `TODO` placeholders, copied placeholder option lists such as
`URL / file id / note id`, generic placeholders such as `N/A`, `none`,
`unknown`, or `TBD`, provenance or identity fields with newlines or control
characters, missing or non-ISO-8601 dates, review dates before the corpus/provider manifest dates, same-day review timestamps before a timestamped corpus/provider manifest, missing independence flags, future-dated reviews,
same-day future review timestamps,
reused `evidence_reference` values across outside-review, buyer-feedback, and
legal-review channels, reused named actors across those three channels,
private prompt/message/content/request/response fields in any outside-review,
buyer-feedback, or legal-review JSON evidence,
blank reviewer or buyer identity fields, missing `reviewer_artifact_checks`,
outside-review artifact-check fields not set to `true`, missing `reviewer_command_outputs`,
outside-review command-output flags not set to `true` or error/fail counts not set to JSON number `0`,
missing revocation/void status review or a non-true
`no_invalid_revoked_or_voided_mints_known` result,
missing `buyer_local_evidence_gates`,
buyer-local gate fields not set to `true`, missing `buyer_threshold_context`,
buyer threshold-context fields that are blank, TODO placeholders, or copied option lists,
missing `buyer_materiality`, non-finite or negative buyer materiality numbers,
or buyer materiality below both `>=10%` avoidable spend and `>=$5k/month`
plausible monthly savings,
missing `reviewed_claims`, `reviewed_claims`
entries that are not `claim_register` IDs, missing legal disclaimers,
missing `legal_limitations`, missing `legal_claim_checks`,
legal limitation entries that are generic placeholders instead of concrete
approved limits such as `none beyond claim register`,
legal claim-check fields not set to `true`, or hash
mismatches fail the external-review gate.

## Verifying A Saved Packet

`--bundle-dir packet` copies the shareable inputs into one directory under stable
names, derives `t1_manifest.json` from the private mint log when needed, and writes
`packet/artifact.json` with relative `command_inputs`. It also writes
`packet/reviewer_checklist.json`, a non-evidence checklist that points at the artifact
hash, review basis, required evidence files, derived `packet/finops_report.md`, and
claim-register checks. It includes both the artifact `verify_command` and the packet
`doctor_command`. Before copying inputs, bundle mode rejects usage logs, provider
exports, public attestation/T1/provider-export/corpus manifest JSON, plus
outside-review, buyer-feedback, or legal-review JSON evidence, that contain prompt
text, completion text, messages, content fields, request or response bodies, or
raw payloads. Bundle mode verifies the completed packet before returning;
pre-existing unbound files or directories in the target directory cause generation to
fail. That directory is the smallest shippable packet.
`--verify-artifact packet/artifact.json` resolves relative paths
against the artifact's directory, so the bundle can be moved and verified from another
working directory. For externally claimable packets, the artifact entrypoint must be
named `artifact.json` so the embedded claim-evidence manifest, reviewer checklist,
and commands all point at the same file.

`artifact.json` contains an `artifact_hash` and the `command_inputs` used to build it.
`--verify-artifact` does five checks:

1. Recomputes the canonical hash of the saved JSON with `artifact_hash` blanked.
2. Re-runs the packet builder from `command_inputs` and compares the recomputed
 packet body with the saved packet body.
3. Validates that `claim_evidence_manifest` mirrors `claim_register`, binds to
 `artifact.json` and the artifact's `ship_scope`, and that every
 machine-readable artifact field reference resolves inside the packet.
4. Rejects externally claimable artifacts whose `command_inputs` use absolute
 paths, escape the packet directory, or still reference a private `mint_log`
 instead of the shareable `t1_manifest`.
5. Rejects externally claimable artifacts whose adjacent
 `reviewer_checklist.json` or `finops_report.md` packet surface is missing,
 and verifies that both `reviewer_checklist.json` and `finops_report.md`
 match `artifact.json`. It also rejects externally claimable packets that
 contain private runtime or mint-log files such as `mint.jsonl`,
 `kry_mint_log*`, `ledger.json`, `decay.json`, or `kry_data/`; any
 symlink; any extra unbound directory or regular file not named by
 `artifact.json`'s relative `command_inputs` or derived packet surfaces; or
 any other non-regular packet entry.

This catches both ordinary edits, where the hash is stale, and stronger edits where
someone changes `ship_scope` or gate fields and recalculates the packet hash. A saved
packet is only meaningful if this command returns `ok: true`. If any copied input file
inside the bundle is edited or removed, the recomputed packet no longer matches and
verification fails.

Run the local doctor before handing the packet to a reviewer:

```bash
python3 scripts/kry_doctor.py --artifact packet/artifact.json
```

It checks Python/config/docs/verifier readiness and re-runs saved-packet verification.
It fails valid artifacts whose `ship_scope` is `do_not_ship`, warns on
`internal_or_demo_only`, and passes only the ship-scope check for
`external_verified_savings_candidate`. Any externally claimable artifact is treated
as a packet-shaped handoff. It requires packet-shaped artifacts to include
`packet/reviewer_checklist.json` and `packet/finops_report.md`, and checks that they
match fresh renders from `packet/artifact.json`. It also confirms packet
`command_inputs` use relative paths that stay inside the packet, that private
mint-log or ledger files are absent from the shareable packet, that symlinks and
unbound directories or files are absent from the shareable packet, and that
prompt/message or raw-body material is absent from the declared usage/provider
inputs, public manifest JSON, and review evidence. It specifically checks that
private mint-log or ledger files are absent from the shareable packet.
With `--artifact`, its `external_evidence_status` is artifact-specific: blocked packets report their
`claim_register` blockers; externally claimable candidates report that the verified
`claim_register` allows `external_verified_savings`. That passing doctor check
confirms local bindings and report freshness, not the upstream truth of the provider
export, reviewer judgment, buyer feedback, or legal review.
