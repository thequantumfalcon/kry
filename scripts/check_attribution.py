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


def main() -> int:
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
    raise SystemExit(main())
