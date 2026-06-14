"""Static malicious packet fixtures stay wired to verifier checks."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "scripts" / "kry_verified_artifact.py"
FIXTURES = ROOT / "tests" / "fixtures" / "malicious_packets"


def _load_artifact_tool():
    spec = importlib.util.spec_from_file_location("kry_verified_artifact_malicious_fixtures", ARTIFACT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_malicious_fixture_manifest_names_required_attack_classes():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in manifest["cases"]}
    assert set(cases) == {
        "absolute_command_inputs",
        "stale_artifact_hash",
        "hidden_mint_log",
        "raw_prompt_usage_log",
        "raw_response_provider_export",
        "symlink_packet_entry",
    }


def test_malicious_fixture_inputs_trip_verifier_boundaries():
    art = _load_artifact_tool()

    absolute = json.loads((FIXTURES / "absolute_command_inputs.json").read_text(encoding="utf-8"))
    absolute_errors = art._command_input_portability_errors(FIXTURES / "artifact.json", absolute)
    # The malicious absolute path is rejected on every OS: POSIX flags it "absolute"; Windows — where a
    # leading-"/" path is not is_absolute() — flags it as escaping the packet directory. Both correctly
    # reject the same input; only the message differs by platform.
    assert any("usage_log" in e and ("absolute" in e or "escapes" in e) for e in absolute_errors), absolute_errors

    stale = json.loads((FIXTURES / "stale_artifact_hash.json").read_text(encoding="utf-8"))
    assert stale["artifact_hash"] != art._artifact_hash(stale)

    usage_records = [
        json.loads(line)
        for line in (FIXTURES / "raw_prompt_usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert "usage log contains private field $[0].request_body" in art._usage_log_privacy_errors(usage_records)

    provider_errors = art._provider_export_privacy_errors(FIXTURES / "provider_export_raw_response.json")
    assert "provider export contains private field $[0].raw_response" in provider_errors
