#!/usr/bin/env python3
"""Fail if tracked files contain disallowed attribution markers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Each marker is split across adjacent string literals so this guard file does not
# trip on its own source. Matching is case-insensitive (see main()).
BANNED = [
    "Co-" "Authored-By:" " Claude",
    "Co-" "Authored-By:" " Codex",
    "Claude" " (Anthropic)",
    "Codex" " (Anthropic)",
    "Generated with " "Claude",
    "Generated with " "Codex",
    "Generated with " "[Claude",   # markdown-link footer: the "[" after "with" defeated the plain marker
    "Generated with " "[Codex",
    "\U0001F916",                  # the robot-emoji attribution marker (escape form, so this file does not self-trip)
    "Senior Research Partner:" " Claude",
    "Senior Research Partner:" " Codex",
    "noreply@" "anthropic.com",
    "noreply@" "openai.com",
]
TEXT_EXTENSIONS = {
    ".cff",
    ".cfg",
    ".cmd",
    ".css",
    ".diff",
    ".gradle",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsonl",
    ".kt",
    ".md",
    ".mjs",   # the stranger-facing JS verifier ships as ES modules; ".js" alone left it unscanned
    ".patch",
    ".ps1",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".txt",
    ".xml",
    ".yml",
    ".yaml",
}


# A file may opt OUT of the scan ONLY by carrying this exact, greppable sentinel — declaring that it
# QUOTES the banned markers as policy examples (audit reports, cross-verify evidence, review/plan docs),
# which is discussion, not attribution. Per-FILE and auditable (grep the sentinel string to list every
# exempt file) — there is NO blanket directory bypass a shipped artifact could hide behind. The sentinel
# is built from split literals here so this guard does not self-exempt (it must still scan its own source).
_ALLOW_SENTINEL = "attribution-check: " "allow-quoted-markers"


def _tracked_files() -> list[Path]:
    try:
        raw = subprocess.check_output(
            ["git", "ls-files"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        skip = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__", "kry_data"}
        return [
            path
            for path in sorted(ROOT.rglob("*"))
            if path.is_file() and not any(part in skip for part in path.relative_to(ROOT).parts)
        ]
    return [ROOT / line.decode() for line in raw.splitlines()]


def _is_text_candidate(path: Path) -> bool:
    if path.suffix in TEXT_EXTENSIONS:
        return True
    return path.name in {"Dockerfile", "LICENSE", "README"}


def _markers_in(text: str) -> list[str]:
    lowered = text.lower()
    return [marker for marker in BANNED if marker.lower() in lowered]


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True,
                                   encoding="utf-8", errors="replace")


# COMMIT-SCOPE: the tracked-file scan below never saw commit messages or commit
# identities, so a co-author trailer or an AI tool's default git identity passed
# both the hook and CI while CONTRIBUTING.md said such a commit would be blocked.
# --message (commit-msg hook) and --commits (CI) apply the same BANNED markers to
# the message text plus the author and committer identity lines, together with
# COMMIT_BANNED: trailers an AI coding session appends that the file markers miss.
COMMIT_BANNED = [
    "Claude" "-Session:",
    "claude.ai/" "code/session",
    "Codex" "-Session:",
]


def _commit_markers_in(text: str) -> list[str]:
    lowered = text.lower()
    return _markers_in(text) + [m for m in COMMIT_BANNED if m.lower() in lowered]


_SCISSORS = "# ------------------------ >8 ------------------------"


def _check_message(path: str) -> list[str]:
    # Comment lines are checked too: `git commit -m/-F` keeps them in the commit. Only the
    # verbose diff below git's scissors line is dropped, since git always removes it.
    with open(path, encoding="utf-8", errors="replace") as handle:
        message = handle.read().split(_SCISSORS, 1)[0]
    identity = _git("var", "GIT_AUTHOR_IDENT") + _git("var", "GIT_COMMITTER_IDENT")
    return [f"commit message or identity: contains disallowed attribution marker ({m!r})"
            for m in _commit_markers_in(message + "\n" + identity)]


def _check_commits(rev_range: str) -> list[str]:
    findings: list[str] = []
    for sha in _git("rev-list", rev_range).split():
        record = _git("show", "-s", "--format=%an <%ae>%n%cn <%ce>%n%B", sha)
        findings.extend(f"commit {sha[:10]}: contains disallowed attribution marker ({m!r})"
                        for m in _commit_markers_in(record))
    return findings


_USAGE = "usage: check_attribution.py [--message FILE | --commits RANGE]"


def main(argv: list[str] | None = None) -> int:
    args = [] if argv is None else list(argv)   # no arguments: the tracked-file scan, unchanged
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        print("\nNo arguments: scan tracked files. --message: a commit message plus the git identity "
              "(commit-msg hook). --commits: every commit in a revision range (CI).")
        return 0
    if args[:1] in (["--message"], ["--commits"]) and len(args) == 2:
        findings = (_check_message(args[1]) if args[0] == "--message"
                    else _check_commits(args[1]))
        for finding in findings:
            print(finding, file=sys.stderr)
        return 1 if findings else 0
    if args:
        print(_USAGE, file=sys.stderr)
        return 2
    findings: list[str] = []
    for path in _tracked_files():
        if not path.exists() or not path.is_file() or not _is_text_candidate(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(ROOT)
        lowered = text.lower()
        if _ALLOW_SENTINEL in lowered:
            continue   # file explicitly opts out: it QUOTES banned markers as policy examples
        for marker in BANNED:
            if marker.lower() in lowered:
                findings.append(f"{rel}: contains disallowed attribution marker ({marker!r})")

    if findings:
        for finding in findings:
            print(finding, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):  # a cp1252 Windows console cannot encode the output glyphs
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    raise SystemExit(main(sys.argv[1:]))
