"""Release proofs must identify and travel with the source they tested."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import kry_release_verify as gate


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Thomas Albrecht")
    git("config", "user.email", "241560317+thequantumfalcon@users.noreply.github.com")
    git("config", "commit.gpgsign", "false")
    (tmp_path / "source.txt").write_text("original\n")
    git("add", "source.txt")
    git("commit", "-qm", "Synthetic release fixture")
    git("tag", "v9.9.9")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "_verify_install", lambda mode, _: {"name": f"install:{mode}", "ok": True})
    monkeypatch.setattr(gate, "_prepare_dev_tools", lambda _: (
        {"name": "dev-tooling", "ok": True}, ["ruff"], [sys.executable, "-m", "pytest"]))
    monkeypatch.setattr(gate, "_packet_gate", lambda _: {"name": "packet_gate", "ok": True})
    monkeypatch.setattr(gate, "_run", lambda name, cmd, **kw: {
        "name": name, "ok": True, "returncode": 0, "output": ""})
    return tmp_path, git


def test_report_records_git_identity_python_and_clean_state(checkout):
    _, git = checkout
    report = gate.run_release_gate(full=True)
    assert report["schema"] == "kry_release_verify/v2"
    source = report["source_before"]
    assert source["commit"] == git("rev-parse", "HEAD")
    assert source["tree"] == git("rev-parse", "HEAD^{tree}")
    assert source["tags"] == ["v9.9.9"]
    assert source["tracked_dirty"] is False
    assert source == report["source_after"]
    assert report["python"] == sys.version
    assert report["ok"] is True


@pytest.mark.parametrize("change", ["content", "commit", "tag"])
def test_source_change_during_gate_fails(checkout, monkeypatch, change):
    root, git = checkout

    def run(name, cmd, **kw):
        if name == "pytest":
            if change == "tag":
                git("tag", "v9.9.8")
            else:
                (root / "source.txt").write_text("changed\n")
                if change == "commit":
                    git("add", "source.txt")
                    git("commit", "-qm", "Changed synthetic source")
        return {"name": name, "ok": True, "returncode": 0, "output": ""}

    monkeypatch.setattr(gate, "_run", run)
    report = gate.run_release_gate(full=True)
    assert report["ok"] is False
    assert next(c for c in report["checks"] if c["name"] == "source-stable")["ok"] is False


def test_missing_git_identity_fails_gate(checkout):
    root, _ = checkout
    # Move the fixture's git directory; the source remains readable but unidentifiable.
    (root / ".git").rename(root / "retained-git")
    assert gate.run_release_gate(full=True)["ok"] is False


def test_clean_full_proof_accepted_by_cli(checkout, capsys):
    root, _ = checkout
    path = root / "release-verify.json"
    path.write_text(json.dumps(gate.run_release_gate(full=True)))
    assert gate.main(["--verify-proof", str(path), "--expected-tag", "v9.9.9"]) == 0
    assert "PASS" in capsys.readouterr().out


@pytest.mark.parametrize("defect", [
    "old_schema", "smoke", "failed", "empty_checks", "missing_check", "duplicate_check",
    "failed_check", "numeric_ok", "missing_python", "dirty", "changed_after", "wrong_commit",
    "wrong_tree", "missing_tag", "current_dirty", "moved_tag", "malformed", "non_object",
])
def test_publication_refuses_invalid_or_mismatched_proof(checkout, defect):
    root, git = checkout
    report = copy.deepcopy(gate.run_release_gate(full=True))
    if defect == "old_schema":
        report["schema"] = "kry_release_verify/v1"
    elif defect == "smoke":
        report["full"] = False
    elif defect == "failed":
        report["ok"] = False
    elif defect == "empty_checks":
        report["checks"] = []
    elif defect == "missing_check":
        report["checks"].pop()
    elif defect == "duplicate_check":
        report["checks"].append(report["checks"][0])
    elif defect == "failed_check":
        report["checks"][0]["ok"] = False
    elif defect == "numeric_ok":
        report["checks"][0]["ok"] = 1
    elif defect == "missing_python":
        report.pop("python", None)
    elif defect == "dirty":
        for name in ("source_before", "source_after"):
            report.setdefault(name, {})["tracked_dirty"] = True
    elif defect == "changed_after":
        report.setdefault("source_after", {})["commit"] = "0" * 40
    elif defect in ("wrong_commit", "wrong_tree", "missing_tag"):
        field = {"wrong_commit": "commit", "wrong_tree": "tree", "missing_tag": "tags"}[defect]
        for name in ("source_before", "source_after"):
            report.setdefault(name, {})[field] = [] if field == "tags" else "0" * 40
    elif defect == "current_dirty":
        (root / "source.txt").write_text("changed after verification\n")
    elif defect == "moved_tag":
        git("tag", "-d", "v9.9.9")
    path = root / "release-verify.json"
    path.write_text("{" if defect == "malformed" else "[]" if defect == "non_object" else json.dumps(report))
    # An explicit failure code, not argparse's usage error, must reject the supplied proof.
    assert gate.main(["--verify-proof", str(path), "--expected-tag", "v9.9.9"]) == 1


def test_published_proof_is_checked_checksummed_and_attested():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/release.yml").read_text()
    verify = workflow.index('--verify-proof release-verify.json --expected-tag "${GITHUB_REF_NAME}"')
    build = workflow.index("python -m build --no-isolation --wheel")
    copy_proof = workflow.index("cp release-verify.json dist/release-verify.json")
    checksum = workflow.index("sha256sum ./* > checksums.txt")
    assert verify < build < copy_proof < checksum
    subjects = workflow.split("subject-path: |", 1)[1].split("- name:", 1)[0]
    assert "dist/release-verify.json" in subjects
    assert "dist/checksums.txt dist/release-verify.json" in workflow
