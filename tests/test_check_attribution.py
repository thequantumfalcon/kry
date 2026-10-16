"""Regression for the attribution guard (H3 in the 2026-06-13 audit).

The guard (scripts/check_attribution.py) is the SOLE enforcement of the no-AI-attribution
rule (pre-commit hook + CI). Before the fix it missed the canonical markdown-link footer
form (the "[" after "with" broke the plain-substring match) and the bare robot emoji. These
tests pin both, AND that a legitimate product mention of the CLI's name is NOT false-flagged.
It also missed the ".mjs" extension, silently exempting the stranger-facing JS verifier; the
scan-coverage tests pin that too.

Inputs are built from split literals / escapes so THIS file does not itself contain a
contiguous banned marker (which would make the guard flag its own test).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_attribution.py"
_EMOJI = "\U0001F916"  # robot emoji, escape form so this file does not self-trip the guard


def _load():
    spec = importlib.util.spec_from_file_location("check_attribution_standalone", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _flagged(mod, text: str) -> bool:
    # mirrors check_attribution.main()'s exact rule: case-insensitive substring over BANNED
    low = text.lower()
    return any(marker.lower() in low for marker in mod.BANNED)


def test_canonical_claude_code_footer_is_caught():
    mod = _load()
    footer = _EMOJI + " Generated with [" + "Claude Code](https://claude.com/claude-code)"
    assert _flagged(mod, footer)


def test_bare_robot_emoji_is_caught():
    mod = _load()
    assert _flagged(mod, "some commit message " + _EMOJI + " trailing")


def test_commit_trailer_still_caught():
    mod = _load()
    trailer = "Co-" "Authored-By: " "Claude <" + "noreply@" + "anthropic.com>"
    assert _flagged(mod, trailer)


def test_legit_claude_code_product_mention_not_flagged():
    mod = _load()
    # "Claude Code" as a tool/product reference appears in real repo docs and must NOT trip the guard.
    assert not _flagged(mod, "We route coding traffic through the Claude Code CLI in these tests.")


def test_mjs_files_are_scanned():
    mod = _load()
    # The stranger-facing JS verifier (verifiers/js/*.mjs) ships as ES modules, so ".js"
    # alone exempted it from the scan in all three places the guard runs.
    assert ".mjs" in mod.TEXT_EXTENSIONS
    assert mod._is_text_candidate(Path("verifiers/js/verify.mjs"))


def test_marker_inside_an_mjs_file_is_reported(tmp_path, monkeypatch):
    mod = _load()
    target = tmp_path / "verify.mjs"
    target.write_text("// Generated with [" + "Claude Code](https://claude.com/claude-code)\n", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "_tracked_files", lambda: [target])
    assert mod.main() == 1


# --- commit scope -------------------------------------------------------------------------------
# The tracked-file scan never saw commit messages or commit identities: a co-author trailer, an AI
# coding session's trailer, or an AI tool's default git identity passed the hook and CI. These pin
# the --message (commit-msg hook) and --commits (CI) modes. Markers are split literals as above.
import os  # noqa: E402
import subprocess  # noqa: E402

_OWNER = ("Owner Example", "owner@example.invalid")
_AI_EMAIL = "noreply@" + "anthropic.com"


def _git_env(tmp_path, author=_OWNER, committer=_OWNER):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=str(tmp_path / "no-global-gitconfig"),
               GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1],
               GIT_COMMITTER_NAME=committer[0], GIT_COMMITTER_EMAIL=committer[1])
    return env


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=_git_env(tmp_path))
    return repo


def _commit(repo, tmp_path, message, author=_OWNER, committer=_OWNER):
    env = _git_env(tmp_path, author, committer)
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-q",
                    "--allow-empty", "--no-verify", "-m", message], check=True, env=env)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                   env=env, text=True).strip()


def _range_result(tmp_path, monkeypatch, message, author=_OWNER):
    mod = _load()
    repo = _repo(tmp_path)
    base = _commit(repo, tmp_path, "chore: base")
    _commit(repo, tmp_path, message, author=author)
    monkeypatch.setattr(mod, "ROOT", repo)
    for key, value in _git_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    return mod.main(["--commits", base + "..HEAD"])


def test_commits_mode_blocks_a_co_author_trailer(tmp_path, monkeypatch):
    msg = "fix: x\n\nCo-" "Authored-By: " "Claude Opus <" + _AI_EMAIL + ">\n"
    assert _range_result(tmp_path, monkeypatch, msg) == 1


def test_commits_mode_blocks_an_ai_session_trailer(tmp_path, monkeypatch):
    msg = "fix: x\n\nClaude" "-Session: https://claude.ai/" "code/session_0123\n"
    assert _range_result(tmp_path, monkeypatch, msg) == 1


def test_commits_mode_blocks_an_ai_default_author_identity(tmp_path, monkeypatch):
    # a clean message is not enough: an AI tool's default git identity makes it the commit author
    assert _range_result(tmp_path, monkeypatch, "fix: clean message",
                         author=("Claude", _AI_EMAIL)) == 1


def test_commits_mode_passes_a_clean_owner_commit(tmp_path, monkeypatch):
    msg = "fix: x\n\nSigned-off-by: Owner Example <owner@example.invalid>\n"
    assert _range_result(tmp_path, monkeypatch, msg) == 0


def test_message_mode_checks_text_and_pending_identity(tmp_path, monkeypatch):
    mod = _load()
    repo = _repo(tmp_path)
    monkeypatch.setattr(mod, "ROOT", repo)
    marker = "Co-" "Authored-By: " "Claude <" + _AI_EMAIL + ">"
    clean = tmp_path / "clean.txt"
    # text below git's scissors line is the verbose diff, which git always drops
    clean.write_text("fix: x\n# ------------------------ >8 ------------------------\n"
                     + marker + "\n", encoding="utf-8")
    trailer = tmp_path / "trailer.txt"
    trailer.write_text("fix: x\n\n" + marker + "\n", encoding="utf-8")
    commented = tmp_path / "commented.txt"
    # `git commit -m/-F` keeps "#" lines, so they are checked like any other line
    commented.write_text("fix: x\n\n# " + marker + "\n", encoding="utf-8")
    for key, value in _git_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    assert mod.main(["--message", str(clean)]) == 0
    assert mod.main(["--message", str(trailer)]) == 1
    assert mod.main(["--message", str(commented)]) == 1
    for key, value in _git_env(tmp_path, author=("Claude", _AI_EMAIL)).items():
        monkeypatch.setenv(key, value)
    assert mod.main(["--message", str(clean)]) == 1


def test_unknown_arguments_are_rejected_not_ignored():
    assert _load().main(["--bogus"]) == 2


def test_commit_msg_hook_runs_the_message_check():
    hook = Path(__file__).resolve().parents[1] / ".githooks" / "commit-msg"
    assert hook.is_file()
    assert "check_attribution.py --message" in hook.read_text(encoding="utf-8")


def test_ci_workflow_checks_pull_request_commits():
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "no-ai-attribution.yml")
    text = wf.read_text(encoding="utf-8")
    assert "--commits" in text and "fetch-depth: 0" in text
