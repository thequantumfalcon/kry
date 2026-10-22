#!/usr/bin/env python3
"""Check offline that CI locks retain the authoritative direct dependency pins."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _pins(text: str) -> list[str]:
    return [line.split("#", 1)[0].strip() for line in text.splitlines()
            if line.split("#", 1)[0].strip()]


def check_locks(root: Path = ROOT) -> list[str]:
    with (root / "pyproject.toml").open("rb") as handle:
        dev = tomllib.load(handle)["project"]["optional-dependencies"]["dev"]
    integration = _pins((root / ".github/litellm-requirements.in").read_text(encoding="utf-8"))
    crypto = _pins((root / ".github/crypto-requirements.in").read_text(encoding="utf-8"))
    errors = []
    for filename, direct in (
        ("dev-requirements.txt", dev),
        ("litellm-requirements.txt", [*dev, *integration]),
        ("crypto-requirements.txt", [*dev, *crypto]),
    ):
        text = (root / ".github" / filename).read_text(encoding="utf-8")
        records = _pins(text.replace("\\\n", " "))
        locked = []
        for record in records:
            pin = record.split(";", 1)[0].split()[0]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+", pin):
                errors.append(f"{filename}: expected an exact package pin: {pin}")
            if not re.search(r"--hash=sha256:[0-9a-f]{64}(?:\s|$)", record):
                errors.append(f"{filename}: missing SHA-256 hash for {pin}")
            locked.append((pin, ";" in record.split("--hash", 1)[0]))
        for pin in direct:
            if (pin, False) not in locked:
                errors.append(f"{filename}: missing unconditional direct pin {pin}; regenerate the lock")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    errors = check_locks()
    for error in errors:
        print(error)
    if not errors:
        print("Dependency lock inputs: OK")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
