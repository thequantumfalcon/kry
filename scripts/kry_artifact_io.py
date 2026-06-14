"""Shared strict JSON and hash helpers for KRY artifact tooling."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _resolve_path(path: str | Path | None, base_dir: str | Path | None = None) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if base_dir and not p.is_absolute():
        return Path(base_dir) / p
    return p


def _load_json(path: str | Path, base_dir: str | Path | None = None) -> object:
    with open(_resolve_path(path, base_dir), encoding="utf-8") as handle:
        return json.load(handle, parse_constant=_reject_json_constant)


def _json_dumps(data: object, **kwargs) -> str:
    kwargs.setdefault("allow_nan", False)
    return json.dumps(data, **kwargs)


def _json_pretty(data: object) -> str:
    return _json_dumps(data, indent=2, sort_keys=True) + "\n"


def _json_canonical(data: object) -> str:
    return _json_dumps(data, sort_keys=True, separators=(",", ":"))


def _json_clean(data: object) -> object:
    return json.loads(_json_dumps(data, sort_keys=True))


def _artifact_hash_body(data: dict) -> dict:
    clean = _json_clean(data)
    clean["artifact_hash"] = ""
    manifest = clean.get("claim_evidence_manifest")
    if isinstance(manifest, dict):
        artifact = manifest.get("artifact")
        if isinstance(artifact, dict) and "artifact_hash" in artifact:
            artifact["artifact_hash"] = ""
    return clean


def _artifact_hash(data: dict) -> str:
    clean = _artifact_hash_body(data)
    payload = _json_canonical(clean)
    return hashlib.sha256(payload.encode()).hexdigest()


def _artifact_compare_body(data: dict) -> dict:
    return _artifact_hash_body(data)


def _hash_file(path: str | Path | None, base_dir: str | Path | None = None) -> dict | None:
    p = _resolve_path(path, base_dir)
    if p is None:
        return None
    if not p.exists():
        return None
    data = p.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
