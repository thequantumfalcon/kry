"""Packaging smoke tests for the stdlib-only build backend."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _copy_minimal_checkout(tmp_path: Path) -> Path:
    src = tmp_path / "checkout"
    (src / "src").mkdir(parents=True)
    shutil.copy2(ROOT / "pyproject.toml", src / "pyproject.toml")
    shutil.copy2(ROOT / "build_backend.py", src / "build_backend.py")
    # README/LICENSE are no longer optional: METADATA embeds the readme as its Description and
    # the wheel carries the license text, so a checkout without them cannot build.
    shutil.copy2(ROOT / "README.md", src / "README.md")
    shutil.copy2(ROOT / "LICENSE.md", src / "LICENSE.md")
    shutil.copytree(ROOT / "src" / "kry", src / "src" / "kry")
    return src


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _clean_python_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def _run(cmd: list[str], *, cwd: Path | None = None):
    subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        env=_clean_python_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _install_and_import(tmp_path: Path, *, editable: bool):
    checkout = _copy_minimal_checkout(tmp_path)
    venv = tmp_path / ("venv-editable" if editable else "venv-wheel")
    _run([sys.executable, "-m", "venv", str(venv)])
    py = _venv_python(venv)
    install_cmd = [str(py), "-m", "pip", "install", "--no-index"]
    if editable:
        install_cmd.append("-e")
    install_cmd.append(str(checkout))
    _run(install_cmd)
    out = subprocess.check_output(
        [str(py), "-c", "import kry; print(kry.__file__)"],
        env=_clean_python_env(),
        text=True,
    ).strip()
    return checkout, Path(out)


def _backend():
    """Load the repo's real backend module without putting ROOT on sys.path."""
    spec = importlib.util.spec_from_file_location("kry_build_backend", ROOT / "build_backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _built_wheel(tmp_path: Path, *, editable: bool = False) -> zipfile.ZipFile:
    backend = _backend()
    out = tmp_path / ("editable" if editable else "wheel")
    out.mkdir(parents=True, exist_ok=True)
    build = backend.build_editable if editable else backend.build_wheel
    return zipfile.ZipFile(out / build(str(out)))


def _dist_info(archive: zipfile.ZipFile) -> str:
    return next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA")).rsplit("/", 1)[0]


def _metadata(archive: zipfile.ZipFile) -> tuple[list[str], str]:
    """(header lines, description body) of the wheel's METADATA."""
    text = archive.read(f"{_dist_info(archive)}/METADATA").decode("utf-8")
    headers, _, body = text.partition("\n\n")
    return headers.splitlines(), body


def test_editable_install_uses_source_tree_without_index(tmp_path):
    checkout, imported = _install_and_import(tmp_path, editable=True)
    assert str(imported).startswith(str(checkout / "src" / "kry"))


def test_wheel_install_copies_package_without_index(tmp_path):
    checkout, imported = _install_and_import(tmp_path, editable=False)
    assert "site-packages" in str(imported)
    assert not str(imported).startswith(str(checkout))


def test_metadata_summary_is_a_single_raw_utf8_line(tmp_path):
    # P1: EmailMessage's default policy RFC2047-encoded the em-dash in `description` and folded
    # the result, so METADATA carried a two-line `Summary: KRY =?utf-8?b?4oCU?= ...` that twine
    # rejects with "'summary' must be a single line". Read from pyproject rather than restating
    # the description here — a hardcoded copy would just go stale.
    with (ROOT / "pyproject.toml").open("rb") as handle:
        description = tomllib.load(handle)["project"]["description"]
    headers, _ = _metadata(_built_wheel(tmp_path))
    assert [line for line in headers if line.startswith("Summary:")] == [f"Summary: {description}"]
    assert "=?utf-8?" not in "\n".join(headers)          # no encoded words anywhere
    assert not any(line[:1].isspace() for line in headers)   # no folded continuation lines


def test_metadata_carries_the_fields_a_published_package_needs(tmp_path):
    headers, body = _metadata(_built_wheel(tmp_path))
    assert "Metadata-Version: 2.4" in headers            # License-File is invalid before 2.4
    assert "Description-Content-Type: text/markdown" in headers
    assert "Classifier: License :: OSI Approved :: Apache Software License" in headers
    assert "Classifier: Operating System :: OS Independent" in headers
    assert "Classifier: Programming Language :: Python :: 3.11" in headers
    assert "Classifier: Typing :: Typed" in headers
    assert any(line.startswith("Classifier: Development Status ::") for line in headers)
    assert "Project-URL: Homepage, https://github.com/thequantumfalcon/kry" in headers
    assert any(line.startswith("Project-URL: Issues, ") for line in headers)
    # keywords reached METADATA nowhere before; they are comma-joined per core metadata
    assert any(line.startswith("Keywords: ") and "proof-of-efficiency" in line for line in headers)
    assert body == (ROOT / "README.md").read_text(encoding="utf-8")


def test_wheel_ships_the_license_text(tmp_path):
    # P3: Apache-2.0 section 4(a) obliges us to give recipients the license.
    archive = _built_wheel(tmp_path)
    packaged = f"{_dist_info(archive)}/licenses/LICENSE.md"
    assert packaged in archive.namelist()
    assert archive.read(packaged) == (ROOT / "LICENSE.md").read_bytes()
    headers, _ = _metadata(archive)
    assert "License-File: LICENSE.md" in headers


def test_wheel_ships_package_data_but_no_tool_droppings(tmp_path):
    names = _built_wheel(tmp_path).namelist()
    assert "kry/py.typed" in names                       # P5: marker is data, not a *.py file
    # a type-checked checkout carries a multi-megabyte src/kry/.mypy_cache that must never publish
    assert not any(n.endswith(".pyc") for n in names)
    assert not any("__pycache__" in n or "/." in n for n in names)


def test_editable_path_file_is_named_for_the_distribution(tmp_path):
    # P6: the name lagged the kry-token -> kry-attest rename.
    names = _built_wheel(tmp_path, editable=True).namelist()
    assert [n for n in names if n.endswith(".pth")] == ["kry_attest_editable.pth"]


def test_sdist_is_a_valid_tarball_that_rebuilds_the_wheel(tmp_path):
    # P4: with no build_sdist hook `python -m build` aborted outright.
    backend = _backend()
    out = tmp_path / "sdist"
    out.mkdir()
    filename = backend.build_sdist(str(out))
    prefix = filename[: -len(".tar.gz")]

    with tarfile.open(out / filename) as tar:
        payload = {member.name: tar.extractfile(member).read() for member in tar.getmembers()}
    assert all(name.startswith(f"{prefix}/") for name in payload)
    for required in ("PKG-INFO", "pyproject.toml", "build_backend.py", "README.md",
                     "LICENSE.md", "src/kry/__init__.py", "src/kry/py.typed"):
        assert f"{prefix}/{required}" in payload

    # unpacked by hand rather than extractall(): tarfile's extraction `filter` is not available
    # on every 3.11 patch release this package claims to support.
    extracted = tmp_path / "extracted"
    for name, data in payload.items():
        target = extracted / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _run(
        [sys.executable, "-c", "import build_backend; build_backend.build_wheel('dist')"],
        cwd=extracted / prefix,
    )
    rebuilt = zipfile.ZipFile(extracted / prefix / "dist" / f"{prefix}-py3-none-any.whl")
    assert "kry/py.typed" in rebuilt.namelist()
