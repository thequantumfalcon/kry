"""Exercise the real tag guards before release gating and publication."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
GUARDS = re.findall(
    r"      - name: Validate tag format\n        run: \|\n((?:          [^\n]*\n|\n)+)",
    (ROOT / ".github/workflows/release.yml").read_text(),
)


@pytest.mark.parametrize("guard_index", [0, 1], ids=["gate", "publish"])
@pytest.mark.parametrize("tag,accepted", [
    (f"v{VERSION}", True),
    (f"v{VERSION}0", False),
    (VERSION, False),
    (f"v{VERSION}-rc.1", False),
])
def test_release_tag_guard(guard_index, tag, accepted):
    assert len(GUARDS) == 2, "both release jobs must validate the tag"
    env = dict(os.environ, GITHUB_REF_NAME=tag)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        ["bash", "-c", textwrap.dedent(GUARDS[guard_index])],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False,
    )
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
