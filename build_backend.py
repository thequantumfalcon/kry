"""Minimal stdlib-only build backend for KRY.

The project intentionally keeps runtime dependencies at zero. This backend lets
`pip install .` and `pip install -e .` work in a fresh checkout without asking
pip to download setuptools just to expose the `src/kry` package.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import os
import pathlib
import tarfile
import time
import tomllib
import zipfile
from email.generator import Generator
from email.message import EmailMessage
from email.policy import EmailPolicy


ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "src"
LICENSE = ROOT / "LICENSE.md"

# P1: core metadata wants raw, single-line UTF-8 headers. EmailMessage's default policy
# RFC2047-encodes any non-ASCII (the em-dash in `description`) and folds at 78 columns, which
# emitted a two-line `Summary:` that twine rejects with "'summary' must be a single line".
# utf8=True writes the bytes through unencoded; max_line_length=0 disables folding entirely.
METADATA_POLICY = EmailPolicy(utf8=True, max_line_length=0)

# PEP 621 string form: the readme's content type is implied by its suffix.
DESCRIPTION_TYPES = {".md": "text/markdown", ".rst": "text/x-rst", ".txt": "text/plain"}

# Everything an sdist consumer needs to rebuild the wheel from the tarball alone.
SDIST_CONTENTS = ("pyproject.toml", "build_backend.py", "README.md", "LICENSE.md", "src")

ZIP_EPOCH = 315532800   # 1980-01-01T00:00:00Z — ZIP cannot encode timestamps before this


def _project() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def _dist_name(name: str) -> str:
    return name.replace("-", "_")


def _dist_info_name() -> str:
    project = _project()
    return f"{_dist_name(str(project['name']))}-{project['version']}.dist-info"


def _metadata() -> bytes:
    project = _project()
    message = EmailMessage(policy=METADATA_POLICY)
    message["Metadata-Version"] = "2.4"      # License-File is only valid from 2.4 onward
    message["Name"] = str(project["name"])
    message["Version"] = str(project["version"])
    # P1: collapse any newline in the source description — a Summary that spans lines is invalid
    # metadata whether the break came from folding or from the TOML.
    message["Summary"] = " ".join(str(project["description"]).split())
    message["Requires-Python"] = str(project["requires-python"])

    license_info = project.get("license")
    if isinstance(license_info, dict) and "text" in license_info:
        message["License"] = str(license_info["text"])
    # P3: Apache-2.0 section 4(a) obliges us to hand recipients the license, so the wheel carries
    # the text and the metadata points at it. Paths are relative to `<dist-info>/licenses/`.
    message["License-File"] = LICENSE.name

    keywords = project.get("keywords")
    if isinstance(keywords, list):
        message["Keywords"] = ",".join(str(keyword) for keyword in keywords)

    classifiers = project.get("classifiers")
    if isinstance(classifiers, list):
        for classifier in classifiers:
            message["Classifier"] = str(classifier)

    urls = project.get("urls")
    if isinstance(urls, dict):
        for label, url in urls.items():
            message["Project-URL"] = f"{label}, {url}"

    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for extra, requirements in optional.items():
            message["Provides-Extra"] = str(extra)
            if isinstance(requirements, list):
                for requirement in requirements:
                    message["Requires-Dist"] = f"{requirement} ; extra == '{extra}'"

    readme = project.get("readme")
    if isinstance(readme, str):
        message["Description-Content-Type"] = DESCRIPTION_TYPES[pathlib.Path(readme).suffix]
        message.set_payload((ROOT / readme).read_text(encoding="utf-8"))

    # BytesGenerator would re-encode the body as base64 MIME; flatten to text and encode once so
    # the Description lands as the raw UTF-8 markdown the spec asks for.
    buffer = io.StringIO()
    Generator(buffer, policy=METADATA_POLICY).flatten(message)
    return buffer.getvalue().encode("utf-8")


def _wheel() -> bytes:
    return (
        "Wheel-Version: 1.0\n"
        "Generator: kry-build-backend\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    ).encode()


def _hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _record_entry(path: str, data: bytes) -> str:
    return f"{path},sha256={_hash(data)},{len(data)}\n"


def _write_metadata_dir(base: pathlib.Path) -> str:
    dist_info = base / _dist_info_name()
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_bytes(_metadata())
    (dist_info / "WHEEL").write_bytes(_wheel())
    return dist_info.name


def _collect(tree: pathlib.Path, base: pathlib.Path) -> list[tuple[str, bytes]]:
    """Files under `tree`, keyed relative to `base`, with local tool droppings excluded.

    The exclusion is load-bearing, not hygiene: a checkout that has been type-checked carries a
    ~9 MB `src/kry/.mypy_cache/` that a bare rglob would otherwise publish to PyPI.
    """
    files: list[tuple[str, bytes]] = []
    for path in sorted(tree.rglob("*")):
        if not path.is_file() or path.suffix == ".pyc":
            continue
        relative = path.relative_to(base)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        files.append((relative.as_posix(), path.read_bytes()))
    return files


def _package_files() -> list[tuple[str, bytes]]:
    # P5: collect everything, not just *.py — the `py.typed` marker is package DATA, and a typed
    # package that never ships its marker is invisible to type checkers downstream.
    return _collect(SRC / "kry", SRC)


def _wheel_files(editable: bool) -> list[tuple[str, bytes]]:
    dist_info = _dist_info_name()
    files = [
        (f"{dist_info}/METADATA", _metadata()),
        (f"{dist_info}/WHEEL", _wheel()),
        (f"{dist_info}/licenses/{LICENSE.name}", LICENSE.read_bytes()),
    ]
    if editable:
        # P6: derive the path filename from the distribution so it cannot go stale again the way
        # `kry_token_editable.pth` did when the distribution was renamed to kry-attest.
        pth = f"{_dist_name(str(_project()['name']))}_editable.pth"
        files.append((pth, f"{SRC}\n".encode()))
    else:
        files.extend(_package_files())
    return files


def _sdist_files() -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for entry in SDIST_CONTENTS:
        path = ROOT / entry
        if path.is_dir():
            files.extend(_collect(path, ROOT))
        else:
            files.append((entry, path.read_bytes()))
    return sorted(files)


def _timestamp() -> int:
    """SOURCE_DATE_EPOCH, clamped to the 1980 ZIP floor, shared by both archive builders."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            candidate = int(epoch)
            if time.gmtime(candidate)[0] >= 1980:     # ZIP cannot encode timestamps before 1980
                return candidate
        except (ValueError, OSError):
            pass
    return ZIP_EPOCH


def _build(wheel_directory: str, editable: bool) -> str:
    wheel_dir = pathlib.Path(wheel_directory)
    wheel_dir.mkdir(parents=True, exist_ok=True)

    project = _project()
    filename = f"{_dist_name(str(project['name']))}-{project['version']}-py3-none-any.whl"
    output = wheel_dir / filename
    files = _wheel_files(editable)

    record_path = f"{_dist_info_name()}/RECORD"
    record = "".join(_record_entry(path, data) for path, data in files)
    record += f"{record_path},,\n"

    # L6: byte-reproducible wheel — stamp every entry with SOURCE_DATE_EPOCH (else the 1980 zip
    # epoch) instead of the current wall clock, and pin perms to writestr()'s 0o600 default. The
    # RECORD hashes cover file DATA, not zip metadata, so this does not change them.
    dt = time.gmtime(_timestamp())[:6]
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for path, data in files:
            info = zipfile.ZipInfo(path, date_time=dt)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16          # match ZipFile.writestr(str)'s default perms
            wheel.writestr(info, data)
        rec_info = zipfile.ZipInfo(record_path, date_time=dt)
        rec_info.compress_type = zipfile.ZIP_DEFLATED
        rec_info.external_attr = 0o600 << 16
        wheel.writestr(rec_info, record.encode())

    return filename


def get_requires_for_build_wheel(config_settings: dict[str, object] | None = None) -> list[str]:
    return []


def get_requires_for_build_editable(config_settings: dict[str, object] | None = None) -> list[str]:
    return []


def get_requires_for_build_sdist(config_settings: dict[str, object] | None = None) -> list[str]:
    return []


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, object] | None = None,
) -> str:
    return _write_metadata_dir(pathlib.Path(metadata_directory))


def prepare_metadata_for_build_editable(
    metadata_directory: str,
    config_settings: dict[str, object] | None = None,
) -> str:
    return _write_metadata_dir(pathlib.Path(metadata_directory))


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    return _build(wheel_directory, editable=False)


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    return _build(wheel_directory, editable=True)


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, object] | None = None,
) -> str:
    # P4: without this hook `python -m build` aborts before it starts, so no source distribution
    # could ever be produced. The tarball carries exactly what rebuilding the wheel needs.
    sdist_dir = pathlib.Path(sdist_directory)
    sdist_dir.mkdir(parents=True, exist_ok=True)

    project = _project()
    prefix = f"{_dist_name(str(project['name']))}-{project['version']}"
    filename = f"{prefix}.tar.gz"
    files = [("PKG-INFO", _metadata()), *_sdist_files()]

    # L6 again: gzip stamps its own mtime and tar stamps per-member mtimes, so both are pinned to
    # SOURCE_DATE_EPOCH — otherwise the sdist would be the one non-reproducible release artifact.
    stamp = _timestamp()
    with (sdist_dir / filename).open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=stamp) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as sdist:
                for path, data in files:
                    info = tarfile.TarInfo(f"{prefix}/{path}")
                    info.size = len(data)
                    info.mtime = stamp
                    info.mode = 0o644
                    sdist.addfile(info, io.BytesIO(data))

    return filename
