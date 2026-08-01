"""Importing a kry module must not touch the filesystem.

Each module binds its persistence paths at import time from ``_kry_data_dir()``. That
helper used to ``mkdir`` the data directory, so merely importing (``import kry.kry_mint``,
``--help``, a doc build, a test collector) created ``kry_data/`` in whatever directory the
caller happened to be in — and raised PermissionError outright under a read-only cwd.
Path CONSTRUCTION is now separate from directory CREATION: the constants are pure Paths and
the writers mkdir lazily.

These tests spawn SUBPROCESSES on purpose. The import side-effect happens exactly once per
interpreter, and the suite has already imported every module by the time any test body runs,
so an in-process assertion could never observe it.

The lazy-creation tests below are the ones that catch a regression. conftest points
KRY_DATA_DIR at pytest's ``tmp_path``, which ALREADY EXISTS — so the rest of the suite stays
green even if a writer forgets to create the directory. These drive the writers against a
data dir that does NOT exist.

NOTE: ``cross_process_lock(p)`` opens ``<p>.lock`` INSIDE the data dir, so a writer must
create the directory BEFORE it takes the lock, not merely before it writes.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = str(Path(__file__).resolve().parents[1] / "src")

# The modules that bind persistence paths at import time.
_MODULES = [
    "kry.kry_mint",
    "kry.kry_token",
    "kry.kry_baseline",
    "kry.kry_pending",
    "kry.kry_sanctions",
    "kry.kry_referee",
    "kry.kry_settlement",
]

# One representative writer per module, to prove the data dir is created lazily by the code
# paths that actually write (and before the lock each of them takes).
_WRITERS = {
    "kry.kry_mint": "import kry.kry_mint as m; assert m.mint('cache_hit', 1000.0, evidence='e',"
                    " avoided_model='opus') is not None",
    "kry.kry_token": "import kry.kry_token as t; t.earn(1000.0, 'cache_hit', 'd',"
                     " avoided_model='opus')",
    "kry.kry_baseline": "import kry.kry_baseline as b; b.observe_holdout('cls', True);"
                        " b.observe_treated('cls', 2)",
    "kry.kry_pending": "import kry.kry_pending as p;"
                       " p.record_pending({'event_type': 'cache_hit', 'tokens_saved': 500.0})",
    "kry.kry_sanctions": "import kry.kry_sanctions as s; s.record_reconciliation('party', True)",
    "kry.kry_referee": "import kry.kry_referee as r; r.is_sanctioned('g', 'rule')",
    "kry.kry_settlement": "import kry.kry_settlement as s; s.compact_registry(keep_recent=2)",
}


def _run(code: str, cwd: Path, data_dir: Path | None):
    """Run `code` in a fresh interpreter. KRY_DATA_DIR is UNSET when data_dir is None, so the
    module falls back to its ./kry_data default relative to `cwd` (the import-purity case)."""
    env = {**os.environ, "PYTHONPATH": _SRC}
    env.pop("KRY_DATA_DIR", None)          # conftest sets it in THIS process; the child must not inherit
    if data_dir is not None:
        env["KRY_DATA_DIR"] = str(data_dir)
    return subprocess.run([sys.executable, "-c", code], cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("modname", _MODULES)
def test_import_creates_no_data_dir(modname, tmp_path):
    """Importing must not create kry_data/ in the caller's current directory."""
    p = _run(f"import {modname}", cwd=tmp_path, data_dir=None)
    assert p.returncode == 0, p.stderr
    assert not (tmp_path / "kry_data").exists(), (
        f"importing {modname} created kry_data/ in the caller's cwd")
    assert list(tmp_path.iterdir()) == [], f"importing {modname} wrote {list(tmp_path.iterdir())}"


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX directory permissions")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root ignores the read-only bit")
@pytest.mark.parametrize("modname", _MODULES)
def test_import_succeeds_in_read_only_cwd(modname, tmp_path):
    """A read-only working directory must not make the import raise (it raised PermissionError
    from the mkdir inside _kry_data_dir())."""
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o500)
    try:
        p = _run(f"import {modname}", cwd=ro, data_dir=None)
    finally:
        os.chmod(ro, 0o700)   # always restore so tmp_path cleanup can remove it
    assert p.returncode == 0, f"{modname} failed to import in a read-only cwd:\n{p.stderr}"
    assert not (ro / "kry_data").exists()


def test_mint_creates_the_data_dir(tmp_path):
    """The other half of the contract: a real mint() DOES create the data dir and the log."""
    data_dir = tmp_path / "nested" / "kry_data"        # deliberately not created
    p = _run(_WRITERS["kry.kry_mint"], cwd=tmp_path, data_dir=data_dir)
    assert p.returncode == 0, p.stderr
    assert data_dir.is_dir(), "mint() did not create the data dir"
    assert (data_dir / "kry_mint_log.jsonl").exists(), "mint() wrote no mint log"


@pytest.mark.parametrize("modname", sorted(_WRITERS))
def test_writer_creates_data_dir_lazily(modname, tmp_path):
    """Every module's write path must create the data dir itself — including before it takes
    the cross-process lock, which opens `<path>.lock` inside that same directory."""
    data_dir = tmp_path / "nested" / "kry_data"        # deliberately not created
    p = _run(_WRITERS[modname], cwd=tmp_path, data_dir=data_dir)
    assert p.returncode == 0, f"{modname} writer failed against a missing data dir:\n{p.stderr}"
    assert data_dir.is_dir(), f"{modname} writer did not create the data dir"
