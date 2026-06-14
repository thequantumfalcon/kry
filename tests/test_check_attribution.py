"""Regression for the attribution guard (H3 in the 2026-06-13 audit).

The guard (scripts/check_attribution.py) is the SOLE enforcement of the no-AI-attribution
rule (pre-commit hook + CI). Before the fix it missed the canonical markdown-link footer
form (the "[" after "with" broke the plain-substring match) and the bare robot emoji. These
tests pin both, AND that a legitimate product mention of the CLI's name is NOT false-flagged.

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
