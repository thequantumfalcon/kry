"""The importable package's version must match the one pyproject.toml publishes."""
from __future__ import annotations

import tomllib
from pathlib import Path

import kry

ROOT = Path(__file__).resolve().parents[1]


def _declared_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def test_import_exposes_the_version_pyproject_declares():
    # `kry.__version__` is a literal so it survives a bare source checkout, where there is no
    # .dist-info for importlib.metadata to read. That makes it a second copy of the release
    # version, so pin it to pyproject here — a hand-copied duplicate drifts otherwise.
    assert kry.__version__ == _declared_version()
