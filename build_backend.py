"""Minimal stdlib-only build backend for KRY.

The project intentionally keeps runtime dependencies at zero. This backend lets
`pip install .` and `pip install -e .` work in a fresh checkout without asking
pip to download setuptools just to expose the `src/kry` package.
"""

from __future__ import annotations

import base64
import hashlib
import pathlib
import tomllib
import zipfile
from email.message import EmailMessage


ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "src"


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
    message = EmailMessage()
    message["Metadata-Version"] = "2.1"
    message["Name"] = str(project["name"])
    message["Version"] = str(project["version"])
    message["Summary"] = str(project["description"])
    message["Requires-Python"] = str(project["requires-python"])

    license_info = project.get("license")
    if isinstance(license_info, dict) and "text" in license_info:
        message["License"] = str(license_info["text"])

    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for extra, requirements in optional.items():
            message["Provides-Extra"] = str(extra)
            if isinstance(requirements, list):
                for requirement in requirements:
                    message["Requires-Dist"] = f"{requirement} ; extra == '{extra}'"

    return message.as_bytes()


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


def _package_files() -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for path in sorted((SRC / "kry").rglob("*.py")):
        archive_path = path.relative_to(SRC).as_posix()
        files.append((archive_path, path.read_bytes()))
    return files


def _wheel_files(editable: bool) -> list[tuple[str, bytes]]:
    dist_info = _dist_info_name()
    files = [
        (f"{dist_info}/METADATA", _metadata()),
        (f"{dist_info}/WHEEL", _wheel()),
    ]
    if editable:
        files.append(("kry_token_editable.pth", f"{SRC}\n".encode()))
    else:
        files.extend(_package_files())
    return files


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

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for path, data in files:
            wheel.writestr(path, data)
        wheel.writestr(record_path, record.encode())

    return filename


def get_requires_for_build_wheel(config_settings: dict[str, object] | None = None) -> list[str]:
    return []


def get_requires_for_build_editable(config_settings: dict[str, object] | None = None) -> list[str]:
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
