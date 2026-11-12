"""Deterministic ZIP overlay packaging from an explicit, hash-pinned file list.

This module creates and audits package artifacts only. It never installs files
into a game or other target directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..hashing import hash_file
from ..manifest import ManifestError, build_manifest, validate_manifest, write_manifest
from ..path_safety import PathSafetyError, assert_no_reparse_tree, normalize_target

PACKAGE_SCHEMA_VERSION = 1
ZIP_PROFILE = "localization-overlay-zip-v1"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_PACKAGE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_MAX_ARCHIVE_ENTRIES = 100_000
_MAX_ARCHIVE_MEMBER_BYTES = 4 * 1024 * 1024 * 1024
_MAX_ARCHIVE_TOTAL_BYTES = 20 * 1024 * 1024 * 1024


class OverlayPackageError(RuntimeError):
    """A packaging precondition or output operation failed."""


class OverlayPackageIntegrityError(OverlayPackageError):
    """A package, manifest, checksum list, or ZIP member is invalid."""


@dataclass(frozen=True)
class PackageFile:
    """One explicitly authorized source-to-archive mapping."""

    source_path: str
    archive_path: str
    expected_sha256: str


@dataclass(frozen=True)
class OverlayPackagePlan:
    """Inputs for one deterministic overlay package.

    Paths in `files` are relative to `source_root`; archive paths are POSIX
    relative paths to be written at the ZIP root. Every source hash is required.
    `output_directory` must not exist and must be outside `source_root`.
    """

    source_root: os.PathLike[str] | str
    output_directory: os.PathLike[str] | str
    package_name: str
    version: str
    files: Sequence[PackageFile]


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise OverlayPackageError(f"{label} must be a 64-character SHA-256 hex digest")
    return value.upper()


def _relative_posix(raw: object, label: str) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw or raw.startswith("/"):
        raise OverlayPackageError(f"{label} must be a non-empty POSIX relative path")
    if len(raw) > 1 and raw[1] == ":":
        raise OverlayPackageError(f"{label} must not contain a drive prefix")
    return raw


def _directory(path: os.PathLike[str] | str, label: str) -> Path:
    candidate = Path(os.path.abspath(os.fspath(path)))
    if not candidate.is_dir():
        raise OverlayPackageError(f"{label} must be an existing directory")
    try:
        normalize_target(candidate, "__overlay_package_root_probe__")
    except PathSafetyError as exc:
        raise OverlayPackageError(f"{label} crosses an unsafe path component: {exc}") from exc
    return candidate


def _overlaps(left: Path, right: Path) -> bool:
    a = os.path.normcase(os.path.normpath(os.fspath(left)))
    b = os.path.normcase(os.path.normpath(os.fspath(right)))
    try:
        return os.path.commonpath((a, b)) in (a, b)
    except ValueError:
        return False


def _safe_package_paths(source_root: Path, payload_root: Path, files: Sequence[PackageFile]) -> list[dict[str, Any]]:
    if not files:
        raise OverlayPackageError("Explicit package file list must not be empty")
    source_keys: set[str] = set()
    archive_keys: set[str] = set()
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(files):
        if not isinstance(item, PackageFile):
            raise OverlayPackageError(f"File entry {index} is not a PackageFile")
        source_raw = _relative_posix(item.source_path, f"source_path for entry {index}")
        archive_raw = _relative_posix(item.archive_path, f"archive_path for entry {index}")
        try:
            source = normalize_target(source_root, source_raw)
            archive = normalize_target(payload_root, archive_raw)
        except PathSafetyError as exc:
            raise OverlayPackageError(f"Unsafe path in entry {index}: {exc}") from exc
        if source.relative_path != source_raw or archive.relative_path != archive_raw:
            raise OverlayPackageError(f"Paths must use canonical spelling and separators: entry {index}")
        if source.key in source_keys:
            raise OverlayPackageError(f"Duplicate normalized source path: {source_raw}")
        if archive.key in archive_keys:
            raise OverlayPackageError(f"Duplicate normalized archive path: {archive_raw}")
        source_keys.add(source.key)
        archive_keys.add(archive.key)
        digest = _digest(item.expected_sha256, f"expected_sha256 for {source_raw}")
        try:
            info = source.full_path.lstat()
        except OSError as exc:
            raise OverlayPackageError(f"Missing or unreadable source file {source_raw}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise OverlayPackageError(f"Source is not a regular file: {source_raw}")
        if hash_file(source.full_path) != digest:
            raise OverlayPackageError(f"Source SHA-256 mismatch: {source_raw}")
        rows.append({
            "source_path": source.relative_path,
            "archive_path": archive.relative_path,
            "expected_sha256": digest,
            "source_size_bytes": info.st_size,
        })

    # Reject file/directory prefix collisions before touching staged payloads.
    archive_path_keys = {row["archive_path"].casefold() for row in rows}
    for key in archive_path_keys:
        parts = key.split("/")
        for end in range(1, len(parts)):
            if "/".join(parts[:end]) in archive_path_keys:
                raise OverlayPackageError(f"Archive path is both a file and a parent directory: {key}")
    rows.sort(key=lambda row: (row["archive_path"].casefold(), row["archive_path"]))
    return rows


def _copy_verified(source: Path, destination: Path, expected_sha256: str) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("rb") as src, destination.open("xb") as dst:
            while True:
                block = src.read(1024 * 1024)
                if not block:
                    break
                dst.write(block)
                digest.update(block)
                size += len(block)
            dst.flush()
            os.fsync(dst.fileno())
    except OSError as exc:
        raise OverlayPackageError(f"Could not stage source file: {source.name}: {exc}") from exc
    actual = digest.hexdigest().upper()
    if actual != expected_sha256:
        raise OverlayPackageError(f"Source changed while staging: {source.name}")
    if hash_file(destination) != expected_sha256:
        raise OverlayPackageError(f"Staged file hash mismatch: {destination.name}")
    return size, actual


def _zip_info(name: str, size: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.internal_attr = 0
    info.extra = b""
    info.comment = b""
    info.file_size = size
    return info


def _write_payload_zip(payload_root: Path, rows: Sequence[Mapping[str, Any]], archive_path: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_STORED, strict_timestamps=True) as archive:
            archive.comment = b""
            for row in rows:
                relative = str(row["path"])
                source = normalize_target(payload_root, relative).full_path
                info = _zip_info(relative, int(row["size_bytes"]))
                with source.open("rb") as input_stream, archive.open(info, mode="w", force_zip64=False) as output_stream:
                    shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        with archive_path.open("rb+") as stream:
            os.fsync(stream.fileno())
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise OverlayPackageError(f"ZIP creation failed: {exc}") from exc


def _read_json_no_duplicate_keys(path: Path) -> dict[str, Any]:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise OverlayPackageIntegrityError(f"Duplicate JSON key: {key}")
            output[key] = value
        return output

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OverlayPackageIntegrityError(f"Cannot read release manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise OverlayPackageIntegrityError("Release manifest root must be an object")
    return value


def _validate_manifest_shape(manifest: Mapping[str, Any], package_root: Path) -> list[dict[str, Any]]:
    if set(manifest) != {"schema_version", "package", "version", "zip_profile", "archive", "files"}:
        raise OverlayPackageIntegrityError("Release manifest contains missing or unexpected top-level fields")
    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION or manifest.get("zip_profile") != ZIP_PROFILE:
        raise OverlayPackageIntegrityError("Unsupported overlay package manifest/profile")
    if not isinstance(manifest.get("package"), str) or not _PACKAGE_TOKEN.fullmatch(manifest["package"]):
        raise OverlayPackageIntegrityError("Invalid package name in release manifest")
    if not isinstance(manifest.get("version"), str) or not _PACKAGE_TOKEN.fullmatch(manifest["version"]):
        raise OverlayPackageIntegrityError("Invalid package version in release manifest")
    archive = manifest.get("archive")
    if not isinstance(archive, dict) or set(archive) != {"path", "size_bytes", "sha256"} or archive.get("path") != "package.zip":
        raise OverlayPackageIntegrityError("Release manifest archive record is invalid")
    try:
        archive_hash = _digest(archive.get("sha256"), "archive.sha256")
    except OverlayPackageError as exc:
        raise OverlayPackageIntegrityError(str(exc)) from exc
    archive_size = archive.get("size_bytes")
    if isinstance(archive_size, bool) or not isinstance(archive_size, int) or archive_size < 0:
        raise OverlayPackageIntegrityError("Invalid archive size in release manifest")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > _MAX_ARCHIVE_ENTRIES:
        raise OverlayPackageIntegrityError("Release manifest file list is empty or exceeds the entry limit")
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    total = 0
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise OverlayPackageIntegrityError(f"Release manifest entry {index} is not an object")
        if set(item) != {"path", "size_bytes", "sha256"}:
            raise OverlayPackageIntegrityError(f"Release manifest entry {index} has unexpected fields")
        try:
            raw = _relative_posix(item.get("path"), f"manifest path {index}")
            normalized = normalize_target(package_root, raw)
            digest = _digest(item.get("sha256"), f"manifest sha256 {index}")
        except (OverlayPackageError, PathSafetyError) as exc:
            raise OverlayPackageIntegrityError(f"Invalid release manifest entry {index}: {exc}") from exc
        if raw != normalized.relative_path:
            raise OverlayPackageIntegrityError(f"Non-canonical manifest path: {raw}")
        if normalized.key in seen:
            raise OverlayPackageIntegrityError(f"Duplicate normalized manifest path: {raw}")
        seen.add(normalized.key)
        size = item.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > _MAX_ARCHIVE_MEMBER_BYTES:
            raise OverlayPackageIntegrityError(f"Invalid or excessive member size: {raw}")
        total += size
        if total > _MAX_ARCHIVE_TOTAL_BYTES:
            raise OverlayPackageIntegrityError("Release manifest exceeds total uncompressed size limit")
        rows.append({"path": raw, "size_bytes": size, "sha256": digest})
    keys = {row["path"].casefold() for row in rows}
    for key in keys:
        parts = key.split("/")
        for end in range(1, len(parts)):
            if "/".join(parts[:end]) in keys:
                raise OverlayPackageIntegrityError(f"Manifest path is also a parent file: {key}")
    expected_order = sorted(rows, key=lambda row: (row["path"].casefold(), row["path"]))
    if rows != expected_order:
        raise OverlayPackageIntegrityError("Manifest file list is not in canonical order")
    return rows


def _verify_zip_payload(archive_path: Path, package_root: Path, expected_rows: Sequence[Mapping[str, Any]]) -> None:
    expected = {str(row["path"]).casefold(): row for row in expected_rows}
    seen: set[str] = set()
    total_size = 0
    try:
        with zipfile.ZipFile(archive_path, mode="r") as archive:
            if archive.comment:
                raise OverlayPackageIntegrityError("ZIP archive comment is not permitted by the canonical profile")
            infos = archive.infolist()
            if len(infos) != len(expected) or len(infos) > _MAX_ARCHIVE_ENTRIES:
                raise OverlayPackageIntegrityError("ZIP member count does not match the explicit release manifest")
            expected_order = [str(row["path"]) for row in expected_rows]
            if [info.filename for info in infos] != expected_order:
                raise OverlayPackageIntegrityError("ZIP members are not in canonical manifest order")
            for info in infos:
                raw = _relative_posix(info.filename, "ZIP member path")
                try:
                    normalized = normalize_target(package_root, raw)
                except PathSafetyError as exc:
                    raise OverlayPackageIntegrityError(f"Unsafe ZIP member path {raw!r}: {exc}") from exc
                if normalized.relative_path != raw:
                    raise OverlayPackageIntegrityError(f"ZIP member path is not canonical: {raw!r}")
                if info.is_dir():
                    raise OverlayPackageIntegrityError(f"Directory entries are not allowed in the overlay ZIP: {raw}")
                if info.flag_bits & 0x1:
                    raise OverlayPackageIntegrityError(f"Encrypted ZIP member is not allowed: {raw}")
                expected_flags = 0x800 if any(ord(char) > 0x7F for char in raw) else 0
                if info.flag_bits != expected_flags:
                    raise OverlayPackageIntegrityError(f"Non-canonical ZIP flags for {raw}")
                if info.compress_type != zipfile.ZIP_STORED:
                    raise OverlayPackageIntegrityError(f"Unsupported compression method for {raw}")
                if info.date_time != FIXED_ZIP_TIME or info.create_system != 3 or info.extra or info.comment:
                    raise OverlayPackageIntegrityError(f"Non-canonical ZIP metadata for {raw}")
                if info.external_attr >> 16 != (stat.S_IFREG | 0o644):
                    raise OverlayPackageIntegrityError(f"Non-regular or non-canonical ZIP file mode: {raw}")
                key = normalized.key
                if key in seen:
                    raise OverlayPackageIntegrityError(f"Duplicate normalized ZIP member: {raw}")
                seen.add(key)
                row = expected.get(key)
                if row is None:
                    raise OverlayPackageIntegrityError(f"Unexpected ZIP member not in release manifest: {raw}")
                if info.file_size != row["size_bytes"] or info.file_size > _MAX_ARCHIVE_MEMBER_BYTES:
                    raise OverlayPackageIntegrityError(f"ZIP member size mismatch or limit exceeded: {raw}")
                total_size += info.file_size
                if total_size > _MAX_ARCHIVE_TOTAL_BYTES:
                    raise OverlayPackageIntegrityError("ZIP exceeds total uncompressed size limit")
                digest = hashlib.sha256()
                size = 0
                try:
                    with archive.open(info, "r") as member:
                        for block in iter(lambda: member.read(1024 * 1024), b""):
                            digest.update(block)
                            size += len(block)
                except (OSError, EOFError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise OverlayPackageIntegrityError(f"Cannot read ZIP member {raw}: {exc}") from exc
                if size != row["size_bytes"] or digest.hexdigest().upper() != row["sha256"]:
                    raise OverlayPackageIntegrityError(f"ZIP member content hash/size mismatch: {raw}")
            if seen != set(expected):
                missing = sorted(set(expected) - seen)
                raise OverlayPackageIntegrityError(f"ZIP is missing manifest members: {missing}")
    except zipfile.BadZipFile as exc:
        raise OverlayPackageIntegrityError(f"ZIP archive is damaged: {exc}") from exc


def _manifest_bytes(package: str, version: str, archive_hash: str, archive_size: int,
                    file_rows: Sequence[Mapping[str, Any]]) -> bytes:
    manifest = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package": package,
        "version": version,
        "zip_profile": ZIP_PROFILE,
        "archive": {"path": "package.zip", "size_bytes": archive_size, "sha256": archive_hash},
        "files": [dict(row) for row in file_rows],
    }
    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _checksums_bytes(zip_hash: str, manifest_hash: str) -> bytes:
    return f"{manifest_hash}  manifest.json\n{zip_hash}  package.zip\n".encode("ascii")


def build_overlay_package(plan: OverlayPackagePlan) -> dict[str, Any]:
    """Build a deterministic package directory from only the supplied entries.

    The package is assembled in a private sibling staging directory, fully
    verified, and then renamed into the previously absent output directory.
    A failed build removes staging and does not publish a partial package.
    """
    if not _PACKAGE_TOKEN.fullmatch(plan.package_name) or not _PACKAGE_TOKEN.fullmatch(plan.version):
        raise OverlayPackageError("Package name and version must use letters, digits, dot, underscore, or hyphen")
    source_root = _directory(plan.source_root, "Source root")
    output = Path(os.path.abspath(os.fspath(plan.output_directory)))
    parent = _directory(output.parent, "Output parent")
    try:
        normalize_target(parent, output.name)
    except PathSafetyError as exc:
        raise OverlayPackageError(f"Unsafe output directory: {exc}") from exc
    if _overlaps(source_root, output):
        raise OverlayPackageError("Package output directory must be outside the source root")
    if output.exists():
        raise OverlayPackageError("Output directory already exists; refusing to overwrite a prior package")

    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=parent))
    try:
        payload_root = stage / "payload"
        payload_root.mkdir()
        rows = _safe_package_paths(source_root, payload_root, plan.files)
        staged_rows: list[dict[str, Any]] = []
        for row in rows:
            source = normalize_target(source_root, row["source_path"]).full_path
            destination = normalize_target(payload_root, row["archive_path"]).full_path
            size, digest = _copy_verified(source, destination, row["expected_sha256"])
            staged_rows.append({"path": row["archive_path"], "size_bytes": size, "sha256": digest})

        staged_manifest = build_manifest(payload_root, [row["path"] for row in staged_rows])
        manifest_issues = validate_manifest(staged_manifest, payload_root, verify_files=True)
        if manifest_issues:
            raise OverlayPackageIntegrityError("Staged payload manifest failed validation: " + "; ".join(manifest_issues))
        if staged_manifest["files"] != staged_rows:
            # Build-manifest ordering is the same canonical order; this guards
            # against changes in the shared manifest helper's contract.
            raise OverlayPackageIntegrityError("Staged manifest differs from the explicit package list")

        archive_path = stage / "package.zip"
        _write_payload_zip(payload_root, staged_rows, archive_path)
        _verify_zip_payload(archive_path, stage, staged_rows)
        zip_hash = hash_file(archive_path)
        zip_size = archive_path.stat().st_size
        manifest_data = _manifest_bytes(plan.package_name, plan.version, zip_hash, zip_size, staged_rows)
        manifest_path = stage / "manifest.json"
        manifest_path.write_bytes(manifest_data)
        manifest_hash = hashlib.sha256(manifest_data).hexdigest().upper()
        (stage / "SHA256SUMS.txt").write_bytes(_checksums_bytes(zip_hash, manifest_hash))
        # Remove staging payload tree; the package is self-contained in ZIP and sidecars.
        shutil.rmtree(payload_root)

        audit_overlay_package(stage)
        if output.exists():
            raise OverlayPackageError("Output directory appeared during build; refusing to overwrite it")
        os.rename(stage, output)
        stage = Path()
        return {
            "status": "passed",
            "package_directory": os.fspath(output),
            "archive_path": os.fspath(output / "package.zip"),
            "manifest_path": os.fspath(output / "manifest.json"),
            "checksums_path": os.fspath(output / "SHA256SUMS.txt"),
            "package_name": plan.package_name,
            "version": plan.version,
            "file_count": len(staged_rows),
            "archive_size_bytes": zip_size,
            "archive_sha256": zip_hash,
            "manifest_sha256": manifest_hash,
        }
    except BaseException:
        if stage != Path() and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise


def audit_overlay_package(package_directory: os.PathLike[str] | str) -> dict[str, Any]:
    """Validate sidecars, canonical ZIP metadata, exact member list, sizes and hashes.

    This function does not extract files and does not install the package.
    """
    root = _directory(package_directory, "Package directory")
    try:
        assert_no_reparse_tree(root)
    except PathSafetyError as exc:
        raise OverlayPackageIntegrityError(f"Reparse point in package: {exc}") from exc
    expected_names = {"package.zip", "manifest.json", "SHA256SUMS.txt"}
    actual_names = {entry.name for entry in root.iterdir()}
    if actual_names != expected_names or any(not (root / name).is_file() for name in expected_names):
        raise OverlayPackageIntegrityError("Package directory must contain exactly package.zip, manifest.json, and SHA256SUMS.txt")
    manifest = _read_json_no_duplicate_keys(root / "manifest.json")
    rows = _validate_manifest_shape(manifest, root)
    archive_record = manifest["archive"]
    archive_path = root / "package.zip"
    if archive_path.stat().st_size != archive_record["size_bytes"] or hash_file(archive_path) != archive_record["sha256"].upper():
        raise OverlayPackageIntegrityError("ZIP file size or SHA-256 does not match release manifest")
    _verify_zip_payload(archive_path, root, rows)
    manifest_hash = hash_file(root / "manifest.json")
    expected_sums = _checksums_bytes(archive_record["sha256"].upper(), manifest_hash)
    try:
        actual_sums = (root / "SHA256SUMS.txt").read_bytes()
    except OSError as exc:
        raise OverlayPackageIntegrityError(f"Cannot read SHA256SUMS.txt: {exc}") from exc
    if actual_sums != expected_sums:
        raise OverlayPackageIntegrityError("SHA256SUMS.txt is malformed or does not match package artifacts")
    return {
        "status": "passed",
        "package": manifest["package"],
        "version": manifest["version"],
        "file_count": len(rows),
        "archive_size_bytes": archive_record["size_bytes"],
        "archive_sha256": archive_record["sha256"].upper(),
        "manifest_sha256": manifest_hash,
        "files": rows,
    }
