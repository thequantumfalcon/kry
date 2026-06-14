#!/usr/bin/env python3
"""Render a buyer-facing retained-dollars report from a verified KRY artifact."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_TOOL = ROOT / "scripts" / "kry_verified_artifact.py"
SCHEMA = "kry_finops_report/v1"


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_load(f):
    return json.load(f, parse_constant=_reject_json_constant)


def _json_dumps(data: object, **kwargs) -> str:
    kwargs.setdefault("allow_nan", False)
    return json.dumps(data, **kwargs)


def _json_pretty(data: object) -> str:
    return _json_dumps(data, indent=2, sort_keys=True)


def _load_artifact_tool():
    spec = importlib.util.spec_from_file_location("kry_verified_artifact_for_finops", ARTIFACT_TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/kry_verified_artifact.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_json(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = _json_load(f)
    if not isinstance(data, dict):
        raise ValueError("artifact JSON must be an object")
    return data


def _claims(artifact: dict) -> dict:
    register = artifact.get("claim_register") or {}
    return {
        claim.get("id"): claim
        for claim in register.get("claims", [])
        if isinstance(claim, dict)
    }


def _summary(block) -> dict:
    if isinstance(block, dict) and isinstance(block.get("summary"), dict):
        return block["summary"]
    return {}


def _clean_text(value) -> str:
    if isinstance(value, str) and value.strip():
        return " ".join(value.strip().split())
    return "unavailable"


def _money(value) -> str:
    try:
        return f"${float(value):,.4f}"
    except (TypeError, ValueError):
        return "$0.0000"


def _kry(value) -> str:
    try:
        return f"{float(value):,.2f} KRY"
    except (TypeError, ValueError):
        return "0.00 KRY"


def _pct(value) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "0.00%"


def _materiality_pct(value) -> str:
    try:
        return f"{float(value):,.2f}%"
    except (TypeError, ValueError):
        return "unavailable"


def _materiality_money(value) -> str:
    try:
        return f"${float(value):,.4f}"
    except (TypeError, ValueError):
        return "unavailable"


def _integer_text(value) -> str:
    if isinstance(value, bool):
        return "unavailable"
    if isinstance(value, int):
        return f"{value:,}"
    return "unavailable"


def _number_pct(value, *, fraction: bool = False) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "unavailable"
    if not math.isfinite(numeric):
        return "unavailable"
    if fraction:
        numeric *= 100
    return f"{numeric:,.2f}%"


def _window_text(value) -> str:
    if not isinstance(value, dict):
        return "unavailable"
    since = _clean_text(value.get("since"))
    until = _clean_text(value.get("until"))
    if since == "unavailable" or until == "unavailable":
        return "unavailable"
    return f"{since} to {until}"


def _provenance_subject(subject, source) -> str:
    subject_text = _clean_text(subject)
    source_text = _clean_text(source)
    if subject_text == "unavailable":
        return source_text
    if source_text == "unavailable" or source_text == subject_text:
        return subject_text
    return f"{subject_text} via {source_text}"


def _provenance_line(label: str, item: dict, *, include_verdict: bool = False) -> str:
    line = (
        f"- {label}: {_provenance_subject(item.get('subject'), item.get('source'))}; "
        f"reference {_clean_text(item.get('reference'))}; "
        f"date {_clean_text(item.get('date'))}"
    )
    if include_verdict:
        line += f"; verdict {_clean_text(item.get('verdict'))}"
    return line


def _gate_status(gates: dict, name: str) -> str:
    gate = gates.get(name)
    if not isinstance(gate, dict):
        return "unavailable"
    return _clean_text(gate.get("status"))


def _gate_triggers(gates: dict, name: str) -> list[str]:
    gate = gates.get(name)
    if not isinstance(gate, dict):
        return []
    triggers = gate.get("triggers")
    if isinstance(triggers, list):
        return [str(trigger) for trigger in triggers]
    return []


def _claim_evidence_summary(manifest: dict) -> dict:
    artifact = manifest.get("artifact")
    if not isinstance(artifact, dict):
        artifact = {}
    claims = manifest.get("claims")
    if not isinstance(claims, list):
        claims = []
    claim_rows = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        evidence = claim.get("evidence")
        blockers = claim.get("blockers")
        claim_rows.append({
            "id": claim.get("id"),
            "status": claim.get("status"),
            "evidence_count": len(evidence) if isinstance(evidence, list) else 0,
            "blocker_count": len(blockers) if isinstance(blockers, list) else 0,
        })
    status_counts: dict[str, int] = {}
    for claim in claim_rows:
        status = _clean_text(claim.get("status"))
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "schema": manifest.get("schema"),
        "ship_scope": manifest.get("ship_scope"),
        "artifact_path": artifact.get("path"),
        "artifact_hash": artifact.get("artifact_hash"),
        "claim_count": len(claim_rows),
        "blocker_count": sum(claim["blocker_count"] for claim in claim_rows),
        "status_counts": status_counts,
        "claims": claim_rows,
    }


def build_report(
    artifact_path: str | Path,
    *,
    display_artifact_path: str | Path | None = None,
    require_packet_surfaces: bool = True,
) -> dict:
    tool = _load_artifact_tool()
    display_path = str(display_artifact_path or artifact_path)
    verification = tool.verify_artifact_file(
        str(artifact_path),
        require_packet_surfaces=require_packet_surfaces,
    )
    if not verification.get("ok"):
        return {
            "schema": SCHEMA,
            "ok": False,
            "artifact": display_path,
            "verification": verification,
            "errors": verification.get("errors") or ["artifact verification failed"],
        }

    artifact = _load_json(artifact_path)
    claims = _claims(artifact)
    savings = artifact.get("savings_report") or {}
    veracity = savings.get("veracity") or {}
    external_claim = claims.get("external_verified_savings") or {}
    internal_claim = claims.get("internal_efficiency_artifact") or {}
    tradeable_claim = claims.get("tradeable_token") or {}
    review_evidence = artifact.get("review_evidence") or {}
    buyer_feedback = review_evidence.get("buyer_feedback") or {}
    buyer_summary = buyer_feedback.get("summary") or {}
    buyer_materiality = buyer_summary.get("buyer_materiality")
    if not isinstance(buyer_materiality, dict):
        buyer_materiality = {}
    claim_evidence_manifest = artifact.get("claim_evidence_manifest")
    if not isinstance(claim_evidence_manifest, dict):
        claim_evidence_manifest = {}
    provider_summary = _summary(artifact.get("provider_export_manifest"))
    corpus_summary = _summary(artifact.get("corpus_manifest"))
    validation_plan = _summary(artifact.get("validation_plan"))
    corpus_validation_plan = corpus_summary.get("validation_plan")
    if not isinstance(corpus_validation_plan, dict):
        corpus_validation_plan = {}
    t1_binding_summary = _summary(artifact.get("t1_manifest_attestation_binding"))
    gates = artifact.get("gates")
    if not isinstance(gates, dict):
        gates = {}
    outside_summary = _summary(review_evidence.get("outside_review"))
    legal_summary = _summary(review_evidence.get("legal_review"))
    scope = artifact.get("ship_scope")
    external_allowed = external_claim.get("status") == "allowed"
    if external_allowed:
        headline = "External verified-savings candidate"
    elif internal_claim.get("status") == "allowed":
        headline = "Internal/demo efficiency artifact"
    else:
        headline = "Do not ship"

    return {
        "schema": SCHEMA,
        "ok": True,
        "artifact": display_path,
        "artifact_hash": artifact.get("artifact_hash"),
        "verification": {
            "ok": True,
            "verify_command": f"python3 scripts/kry_verified_artifact.py --verify-artifact {display_path}",
            "doctor_command": f"python3 scripts/kry_doctor.py --artifact {display_path}",
        },
        "headline": headline,
        "ship_scope": scope,
        "retained_dollars": savings.get("saved_usd", 0.0),
        "saved_kry": savings.get("saved_kry", 0.0),
        "spend_usd": savings.get("spend_usd", 0.0),
        "spend_kry": savings.get("spend_kry", 0.0),
        "efficiency_ratio": savings.get("efficiency_ratio", 0.0),
        "veracity_floor": veracity.get("veracity_floor", 0.0),
        "veracity_breakdown": {
            "self_reported_kry": veracity.get("self_reported_kry", 0.0),
            "holdout_validated_kry": veracity.get("holdout_validated_kry", 0.0),
            "provider_metered_kry": veracity.get("provider_metered_kry", 0.0),
        },
        "external_verified_savings": {
            "status": external_claim.get("status", "blocked"),
            "statement": external_claim.get("statement"),
            "blockers": external_claim.get("blockers") or [],
        },
        "provider_reconciled": (claims.get("provider_reconciled") or {}).get("status", "blocked"),
        "real_corpus_validated": (claims.get("real_corpus_validated") or {}).get("status", "blocked"),
        "external_review_complete": (claims.get("external_review_complete") or {}).get("status", "blocked"),
        "claim_evidence_manifest": _claim_evidence_summary(claim_evidence_manifest),
        "gate_summary": {
            "product": {"status": _gate_status(gates, "product")},
            "science": {"status": _gate_status(gates, "science")},
            "external_review": {"status": _gate_status(gates, "external_review")},
            "kill": {
                "status": _gate_status(gates, "kill"),
                "triggers": _gate_triggers(gates, "kill"),
            },
        },
        "validation_plan": {
            "schema": validation_plan.get("schema") or corpus_validation_plan.get("schema"),
            "registered_date": validation_plan.get("registered_date") or corpus_validation_plan.get("registered_date"),
            "provider": validation_plan.get("provider") or corpus_validation_plan.get("provider"),
            "reconciliation_mode": (
                validation_plan.get("reconciliation_mode") or corpus_validation_plan.get("reconciliation_mode")
            ),
            "tolerance": corpus_validation_plan.get("tolerance"),
            "tolerance_pct": corpus_validation_plan.get("tolerance_pct"),
            "collection_window": validation_plan.get("collection_window") or corpus_validation_plan.get("collection_window"),
            "min_provider_records": (
                validation_plan.get("min_provider_records") or corpus_validation_plan.get("min_provider_records")
            ),
            "min_usage_records": validation_plan.get("min_usage_records") or corpus_validation_plan.get("min_usage_records"),
            "min_independent_agreement": (
                validation_plan.get("min_independent_agreement")
                or corpus_validation_plan.get("min_independent_agreement")
            ),
        },
        "t1_attestation_binding": {
            "schema": t1_binding_summary.get("schema"),
            "source_mint_log_sha256": t1_binding_summary.get("source_mint_log_sha256"),
            "manifest_receipts": t1_binding_summary.get("manifest_receipts"),
            "attestation_provider_metered_links": t1_binding_summary.get("attestation_provider_metered_links"),
            "matched_links": t1_binding_summary.get("matched_links"),
        },
        "evidence_provenance": {
            "provider_export": {
                "subject": provider_summary.get("provider"),
                "source": provider_summary.get("export_source"),
                "reference": provider_summary.get("export_reference"),
                "date": provider_summary.get("date"),
            },
            "corpus_manifest": {
                "subject": corpus_summary.get("corpus"),
                "source": corpus_summary.get("source"),
                "reference": corpus_summary.get("source_reference"),
                "date": corpus_summary.get("date"),
            },
            "outside_review": {
                "subject": outside_summary.get("reviewer"),
                "source": outside_summary.get("evidence_source"),
                "reference": outside_summary.get("evidence_reference"),
                "date": outside_summary.get("date"),
                "verdict": outside_summary.get("verdict"),
            },
            "buyer_feedback": {
                "subject": buyer_summary.get("buyer"),
                "source": buyer_summary.get("evidence_source"),
                "reference": buyer_summary.get("evidence_reference"),
                "date": buyer_summary.get("date"),
                "verdict": buyer_summary.get("verdict"),
            },
            "legal_review": {
                "subject": legal_summary.get("reviewer"),
                "source": legal_summary.get("evidence_source"),
                "reference": legal_summary.get("evidence_reference"),
                "date": legal_summary.get("date"),
                "verdict": legal_summary.get("verdict"),
            },
        },
        "buyer_materiality": {
            "avoidable_spend_pct": buyer_materiality.get("avoidable_spend_pct"),
            "avoidable_spend_pct_min": getattr(tool, "BUYER_MATERIALITY_AVOIDABLE_SPEND_PCT_BAR", 10.0),
            "plausible_monthly_savings_usd": buyer_materiality.get("plausible_monthly_savings_usd"),
            "plausible_monthly_savings_usd_min": getattr(
                tool,
                "BUYER_MATERIALITY_MONTHLY_SAVINGS_USD_BAR",
                5000.0,
            ),
        },
        "tradeable_token": {
            "status": tradeable_claim.get("status", "forbidden"),
            "statement": tradeable_claim.get("statement", "KRY is not a tradeable token."),
        },
    }


def render_markdown(report: dict) -> str:
    if not report.get("ok"):
        errors = "\n".join(f"- {err}" for err in report.get("errors", []))
        return "\n".join([
            "# KRY Retained-Dollars Report",
            "",
            "Artifact verification failed. Do not use this report.",
            "",
            errors,
            "",
        ])

    external = report["external_verified_savings"]
    blockers = external.get("blockers") or []
    blocker_lines = "\n".join(f"- {b}" for b in blockers) if blockers else "- none"
    status = external.get("status", "blocked").upper()
    if status == "ALLOWED":
        claim_line = "External verified-savings claim: ALLOWED AS CANDIDATE"
    else:
        claim_line = f"External verified-savings claim: {status}"
    materiality = report.get("buyer_materiality") or {}
    provenance = report.get("evidence_provenance") or {}
    validation = report.get("validation_plan") or {}
    t1_binding = report.get("t1_attestation_binding") or {}
    gates = report.get("gate_summary") or {}
    kill_gate = gates.get("kill") or {}
    claim_manifest = report.get("claim_evidence_manifest") or {}
    status_counts = claim_manifest.get("status_counts") or {}
    claim_rows = claim_manifest.get("claims") or []
    claim_summary = (
        f"{_integer_text(claim_manifest.get('claim_count'))} total; "
        f"{_integer_text(status_counts.get('allowed', 0))} allowed; "
        f"{_integer_text(status_counts.get('blocked', 0))} blocked; "
        f"{_integer_text(status_counts.get('forbidden', 0))} forbidden; "
        f"{_integer_text(claim_manifest.get('blocker_count'))} blockers"
    )
    claim_lines = [
        f"- {_clean_text(claim.get('id'))}: {_clean_text(claim.get('status'))}; "
        f"evidence refs {_integer_text(claim.get('evidence_count'))}; "
        f"blockers {_integer_text(claim.get('blocker_count'))}"
        for claim in claim_rows
        if isinstance(claim, dict)
    ] or ["- unavailable"]

    lines = [
        "# KRY Retained-Dollars Report",
        "",
        f"Status: {report['headline']}",
        f"Ship scope: `{report['ship_scope']}`",
        f"Artifact hash: `{report['artifact_hash']}`",
        "",
        "## Retained Dollars",
        "",
        f"- Retained dollars: {_money(report['retained_dollars'])}",
        f"- Saved amount: {_kry(report['saved_kry'])}",
        f"- Real spend: {_money(report['spend_usd'])} ({_kry(report['spend_kry'])})",
        f"- Efficiency ratio: {_pct(report['efficiency_ratio'])}",
        "",
        "## Veracity",
        "",
        f"- Veracity floor: {_pct(report['veracity_floor'])}",
        f"- Self-reported: {_kry(report['veracity_breakdown']['self_reported_kry'])}",
        f"- Holdout-validated: {_kry(report['veracity_breakdown']['holdout_validated_kry'])}",
        f"- Provider-metered: {_kry(report['veracity_breakdown']['provider_metered_kry'])}",
        "",
        "## Claim Status",
        "",
        f"- {claim_line}",
        f"- Provider reconciled: {report['provider_reconciled']}",
        f"- Real corpus validated: {report['real_corpus_validated']}",
        f"- External review complete: {report['external_review_complete']}",
        f"- Tradeable token: {report['tradeable_token']['status']}",
        "",
        "External blockers:",
        blocker_lines,
        "",
        "## Gate Summary",
        "",
        f"- Product gate: {_clean_text((gates.get('product') or {}).get('status'))}",
        f"- Science gate: {_clean_text((gates.get('science') or {}).get('status'))}",
        f"- External-review gate: {_clean_text((gates.get('external_review') or {}).get('status'))}",
        f"- Kill gate: {_clean_text(kill_gate.get('status'))}",
        "- Kill triggers: "
        + (", ".join(str(trigger) for trigger in kill_gate.get("triggers", [])) or "none"),
        "",
        "## Claim Evidence Manifest",
        "",
        f"- Schema: {_clean_text(claim_manifest.get('schema'))}",
        f"- Artifact binding: {_clean_text(claim_manifest.get('artifact_path'))} @ "
        f"{_clean_text(claim_manifest.get('artifact_hash'))}",
        f"- Ship scope: {_clean_text(claim_manifest.get('ship_scope'))}",
        f"- Claims: {claim_summary}",
        *claim_lines,
        "",
        "## Validation Plan",
        "",
        f"- Schema: {_clean_text(validation.get('schema'))}",
        f"- Registered date: {_clean_text(validation.get('registered_date'))}",
        f"- Provider: {_clean_text(validation.get('provider'))}",
        f"- Reconciliation mode: {_clean_text(validation.get('reconciliation_mode'))}",
        f"- Collection window: {_window_text(validation.get('collection_window'))}",
        f"- Tolerance: {_integer_text(validation.get('tolerance'))} records; aggregate tolerance "
        f"{_number_pct(validation.get('tolerance_pct'))}",
        f"- Minimum provider records: {_integer_text(validation.get('min_provider_records'))}",
        f"- Minimum usage records: {_integer_text(validation.get('min_usage_records'))}",
        f"- Minimum independent agreement: {_number_pct(validation.get('min_independent_agreement'), fraction=True)}",
        "",
        "## T1 Attestation Binding",
        "",
        f"- Schema: {_clean_text(t1_binding.get('schema'))}",
        f"- Source mint log SHA-256: {_clean_text(t1_binding.get('source_mint_log_sha256'))}",
        f"- Manifest receipts: {_integer_text(t1_binding.get('manifest_receipts'))}",
        f"- Attestation provider-metered links: {_integer_text(t1_binding.get('attestation_provider_metered_links'))}",
        f"- Matched links: {_integer_text(t1_binding.get('matched_links'))}",
        "",
        "## Buyer Materiality",
        "",
        "- Avoidable spend threshold: "
        f"{_materiality_pct(materiality.get('avoidable_spend_pct'))} / "
        f"{_materiality_pct(materiality.get('avoidable_spend_pct_min'))}",
        "- Monthly savings threshold: "
        f"{_materiality_money(materiality.get('plausible_monthly_savings_usd'))} / "
        f"{_materiality_money(materiality.get('plausible_monthly_savings_usd_min'))}",
        "",
        "## Evidence Provenance",
        "",
        _provenance_line("Provider export", provenance.get("provider_export") or {}),
        _provenance_line("Corpus manifest", provenance.get("corpus_manifest") or {}),
        _provenance_line("Outside review", provenance.get("outside_review") or {}, include_verdict=True),
        _provenance_line("Buyer feedback", provenance.get("buyer_feedback") or {}, include_verdict=True),
        _provenance_line("Legal review", provenance.get("legal_review") or {}, include_verdict=True),
        "",
        "## Verification",
        "",
        f"- Verified command: `{report['verification']['verify_command']}`",
        f"- Doctor command: `{report['verification']['doctor_command']}`",
        "- This is retained-dollars accounting, not a tradeable token or exchange instrument.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render a KRY FinOps retained-dollars report from artifact.json")
    p.add_argument("artifact", help="verified-savings artifact JSON")
    p.add_argument("--json", action="store_true", help="emit kry_finops_report/v1 JSON")
    p.add_argument("--out", default=None, help="write report to this path instead of stdout")
    args = p.parse_args(argv)

    report = build_report(args.artifact)
    text = _json_pretty(report) if args.json else render_markdown(report)
    if args.out:
        Path(args.out).write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
    else:
        print(text)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
