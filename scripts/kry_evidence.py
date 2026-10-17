"""docs/evidence/ changes only when a run asks to replace it and measured something.

README and docs figures cite the committed results under docs/evidence/, and the research scripts
used to rewrite them on every run: an offline, keyless or --help run measured nothing, replaced the
committed rows with zeros or placeholders and exited 0 (the 0.7096 behind the README's 71%
prefix-cacheable figure became 0.0). A script now writes into a fresh run directory and hands its
files to publish(), which copies them over the committed ones only under --write-evidence, and
refuses, with exit status 1, a run that measured nothing or whose data pull failed.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parents[1] / "docs" / "evidence"
WRITE_HELP = ("replace the committed result under docs/evidence/ with this run's "
              "(refused when the run measured nothing)")


def run_dir(subdir: str) -> Path:
    """A fresh directory outside the checkout for one run's files."""
    return Path(tempfile.mkdtemp(prefix=f"kry_{subdir}_"))


def publish(files: dict[str, Path], subdir: str, *, rows: int, write: bool, failure: str = "") -> int:
    """Copy `files` ({committed name: this run's file}) into docs/evidence/<subdir>/ if asked.

    Returns the script's exit status: 1, with nothing copied, when the run measured no rows or its
    data pull failed; otherwise 0, copying only when `write` is set.
    """
    kept = ", ".join(str(path) for path in files.values())
    if failure or rows < 1:
        print(f"[evidence] {failure or 'the run measured nothing'}: docs/evidence/{subdir}/ is unchanged "
              f"(this run's files: {kept})", file=sys.stderr)
        return 1
    if not write:
        print(f"[evidence] this run's files: {kept}; docs/evidence/{subdir}/ is unchanged "
              "(--write-evidence replaces it)")
        return 0
    target = EVIDENCE / subdir
    target.mkdir(parents=True, exist_ok=True)
    for name, path in files.items():
        shutil.copyfile(path, target / name)
        print(f"[evidence] wrote docs/evidence/{subdir}/{name}")
    return 0
