"""Portable JSON manifests for file payloads."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from typing import Iterable, Mapping
from .hashing import hash_file
from .path_safety import PathSafetyError, normalize_target

SCHEMA_VERSION = 1

class ManifestError(ValueError):
    """Raised for invalid manifests or payload verification failures."""

def build_manifest(root: os.PathLike[str] | str, paths: Iterable[os.PathLike[str] | str], *, metadata: Mapping[str, object] | None = None) -> dict[str, object]:
    root_path = Path(root)
    files, seen = [], set()
    for item in paths:
        normalized = normalize_target(root_path, item)
        if normalized.key in seen:
            raise ManifestError(f"Duplicate target path: {normalized.relative_path}")
        seen.add(normalized.key)
        if not normalized.full_path.is_file():
            raise ManifestError(f"Target is missing or not a file: {normalized.relative_path}")
        try:
            digest = hash_file(normalized.full_path)
        except (OSError, ValueError) as exc:
            raise ManifestError(f"Cannot hash {normalized.relative_path}: {exc}") from exc
        info = normalized.full_path.stat()
        files.append({"path": normalized.relative_path, "size_bytes": info.st_size, "sha256": digest})
    files.sort(key=lambda item: str(item["path"]).casefold())
    return {"schema_version": SCHEMA_VERSION, "metadata": dict(metadata or {}), "files": files}

def validate_manifest(manifest: Mapping[str, object], root: os.PathLike[str] | str, *, verify_files: bool = True) -> list[str]:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(f"Unsupported schema_version: {manifest.get('schema_version')!r}")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ManifestError("Manifest files must be a list")
    if not isinstance(manifest.get("metadata", {}), dict):
        raise ManifestError("Manifest metadata must be an object")
    seen, issues = set(), []
    for index, record in enumerate(files):
        if not isinstance(record, dict):
            raise ManifestError(f"File record {index} must be an object")
        raw_path = record.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ManifestError(f"File record {index} has no path")
        if Path(raw_path).is_absolute() or (len(raw_path) > 1 and raw_path[1] == ":") or raw_path.startswith(("/", "\\")):
            raise ManifestError(f"Manifest path must be relative: {raw_path}")
        try:
            normalized = normalize_target(root, raw_path)
        except PathSafetyError as exc:
            raise ManifestError(f"Unsafe manifest path {raw_path}: {exc}") from exc
        if normalized.key in seen:
            raise ManifestError(f"Duplicate target path: {raw_path}")
        seen.add(normalized.key)
        digest, size = record.get("sha256"), record.get("size_bytes")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
            raise ManifestError(f"Invalid SHA-256 for {raw_path}")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ManifestError(f"Invalid size_bytes for {raw_path}")
        if not verify_files:
            continue
        if not normalized.full_path.is_file():
            issues.append(f"missing: {normalized.relative_path}")
            continue
        info = normalized.full_path.stat()
        if info.st_size != size:
            issues.append(f"size_mismatch: {normalized.relative_path}")
        if hash_file(normalized.full_path).casefold() != digest.casefold():
            issues.append(f"sha256_mismatch: {normalized.relative_path}")
    return issues

def write_manifest(path: os.PathLike[str] | str, manifest: Mapping[str, object]) -> None:
    """Atomically replace a JSON manifest using a temporary sibling file."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise

def load_manifest(path: os.PathLike[str] | str) -> dict[str, object]:
    with Path(path).open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ManifestError("Manifest root must be an object")
    return value
