"""Windows-oriented path normalization and whitelist snapshot comparison."""
from __future__ import annotations
import os
import ntpath
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

class PathSafetyError(ValueError):
    """Raised when a path escapes a root or crosses a reparse point."""

@dataclass(frozen=True)
class NormalizedPath:
    full_path: Path
    relative_path: str
    key: str

def _slash(path: str) -> str:
    return path.replace("\\", "/").rstrip("/")

def _is_reparse(info: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & flag)

def _absolute(path: os.PathLike[str] | str, base: Path | None = None) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (base if base is not None else Path.cwd()) / candidate
    return Path(os.path.abspath(os.fspath(candidate)))

def _check_root(root: os.PathLike[str] | str) -> Path:
    root_path = _absolute(root)
    # Check every existing component: checking only the final root misses a
    # junction or symlink in one of its ancestors.
    chain = [*reversed(root_path.parents), root_path]
    for index, component in enumerate(chain):
        try:
            info = component.lstat()
        except FileNotFoundError as exc:
            raise PathSafetyError(f"Root component does not exist: {component}") from exc
        if _is_reparse(info):
            raise PathSafetyError(f"Reparse point in authorized root: {component}")
        if index < len(chain) - 1 and not stat.S_ISDIR(info.st_mode):
            raise PathSafetyError(f"Root component is not a directory: {component}")
    if not stat.S_ISDIR(chain[-1].lstat().st_mode):
        raise PathSafetyError(f"Root is not a directory: {root_path}")
    return root_path

def _reject_parent_components(raw: str) -> None:
    if any(part == ".." for part in raw.replace("\\", "/").split("/")):
        raise PathSafetyError(f"Parent traversal is forbidden: {raw}")

def _validate_windows_relative(relative: str) -> None:
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for part in relative.replace("\\", "/").split("/"):
        if not part or part in {".", ".."}:
            raise PathSafetyError(f"Invalid path component: {part!r}")
        if ":" in part:
            raise PathSafetyError(f"Alternate data stream or drive syntax is forbidden: {part}")
        if part.endswith((" ", ".")):
            raise PathSafetyError(f"Windows path components may not end in a space or period: {part!r}")
        device_name = part.split(".", 1)[0].upper()
        if device_name in reserved:
            raise PathSafetyError(f"Reserved Windows device name is forbidden: {part}")

def _validate_raw_windows_components(raw: str) -> None:
    drive, tail = ntpath.splitdrive(raw.replace("/", "\\"))
    if drive and not ntpath.isabs(raw.replace("/", "\\")):
        raise PathSafetyError(f"Drive-relative paths are not accepted: {raw}")
    for part in tail.split("\\"):
        if part and part != "." and part.endswith((" ", ".")):
            raise PathSafetyError(f"Windows path components may not end in a space or period: {part!r}")

def _check_existing_chain(root: Path, relative: str) -> None:
    current = root
    for part in relative.split("/"):
        if not part or part == ".":
            continue
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        if _is_reparse(info):
            raise PathSafetyError(f"Reparse point in authorized path: {current}")

def normalize_target(root: os.PathLike[str] | str, candidate: os.PathLike[str] | str, *, base: os.PathLike[str] | str | None = None) -> NormalizedPath:
    """Normalize a relative or absolute target and prove it is beneath root.

    Parent traversal is rejected even if normalization would land back inside
    root. Existing symlinks/reparse points in the target chain are refused.
    The root must exist; the leaf target may be absent.
    """
    raw = os.fspath(candidate)
    if not isinstance(raw, str) or not raw:
        raise PathSafetyError("Target path must be a non-empty string")
    windows_raw = raw.replace("/", "\\")
    if windows_raw.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        raise PathSafetyError("Windows device-namespace paths are not accepted")
    _validate_raw_windows_components(raw)
    _reject_parent_components(raw)
    root_path = _check_root(root)
    base_path = _absolute(base, root_path) if base is not None else root_path
    candidate_path = _absolute(raw, base_path)
    root_text = _slash(os.path.normpath(os.fspath(root_path)))
    candidate_text = _slash(os.path.normpath(os.fspath(candidate_path)))
    root_key, candidate_key = root_text.casefold(), candidate_text.casefold()
    if not candidate_key.startswith(root_key + "/"):
        raise PathSafetyError(f"Target is outside authorized root: {raw}")
    relative = candidate_text[len(root_text) + 1:]
    if not relative or relative == ".":
        raise PathSafetyError("The root directory itself is not a file target")
    _validate_windows_relative(relative)
    _check_existing_chain(root_path, relative)
    return NormalizedPath(candidate_path, relative, relative.casefold())

def assert_no_reparse_tree(root: os.PathLike[str] | str) -> None:
    """Reject a root containing any symbolic link or Windows reparse point."""
    root_path = _check_root(root)
    for current, dirnames, filenames in os.walk(root_path, followlinks=False):
        for name in [*dirnames, *filenames]:
            entry = Path(current) / name
            try:
                info = entry.lstat()
            except OSError as exc:
                raise PathSafetyError(f"Cannot inspect tree entry: {entry}") from exc
            if _is_reparse(info):
                raise PathSafetyError(f"Reparse point found in tree: {entry}")

def snapshot_tree(root: os.PathLike[str] | str) -> list[dict[str, object]]:
    """Return sorted file records containing relative path, size, and SHA-256."""
    from .hashing import hash_file
    root_path = _check_root(root)
    assert_no_reparse_tree(root_path)
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for current, dirnames, filenames in os.walk(root_path, followlinks=False):
        dirnames.sort(key=str.casefold)
        filenames.sort(key=str.casefold)
        for name in filenames:
            file_path = Path(current) / name
            relative = file_path.relative_to(root_path).as_posix()
            key = relative.casefold()
            if key in seen:
                raise PathSafetyError(f"Duplicate path under Windows comparison rules: {relative}")
            seen.add(key)
            info = file_path.stat()
            rows.append({"path": relative, "size_bytes": info.st_size, "sha256": hash_file(file_path)})
    return sorted(rows, key=lambda row: str(row["path"]).casefold())

def _snapshot_map(rows: Sequence[Mapping[str, object]], root: Path, label: str) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        raw_path = row.get("path")
        if not isinstance(raw_path, str):
            raise PathSafetyError(f"{label} entry has no string path")
        normalized = normalize_target(root, raw_path)
        if normalized.key in result:
            raise PathSafetyError(f"Duplicate {label} path: {raw_path}")
        digest = row.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
            raise PathSafetyError(f"Invalid {label} SHA-256 for {raw_path}")
        result[normalized.key] = {"path": normalized.relative_path, "sha256": digest.upper()}
    return result

def compare_snapshots(before: Sequence[Mapping[str, object]], after: Sequence[Mapping[str, object]], *, root: os.PathLike[str] | str, authorized_paths: Iterable[os.PathLike[str] | str] = ()) -> dict[str, object]:
    """Compare snapshots and identify changes outside a normalized whitelist."""
    root_path = _check_root(root)
    old, new = _snapshot_map(before, root_path, "before snapshot"), _snapshot_map(after, root_path, "after snapshot")
    allowed: set[str] = set()
    for item in authorized_paths:
        normalized = normalize_target(root_path, item)
        if normalized.key in allowed:
            raise PathSafetyError(f"Duplicate normalized whitelist path: {normalized.relative_path}")
        allowed.add(normalized.key)
    added = [{**new[key], "authorized": key in allowed} for key in sorted(new.keys() - old.keys())]
    removed = [{**old[key], "authorized": key in allowed} for key in sorted(old.keys() - new.keys())]
    changed, outside = [], []
    outside.extend({"kind": "added", **item} for item in added if not item["authorized"])
    outside.extend({"kind": "removed", **item} for item in removed if not item["authorized"])
    for key in sorted(old.keys() & new.keys()):
        if old[key]["sha256"] != new[key]["sha256"]:
            item = {"path": new[key]["path"], "before_sha256": old[key]["sha256"], "after_sha256": new[key]["sha256"], "authorized": key in allowed}
            changed.append(item)
            if key not in allowed:
                outside.append({"kind": "changed", **item})
    return {"before_count": len(old), "after_count": len(new), "added": added, "removed": removed, "changed": changed, "outside_whitelist": outside}
