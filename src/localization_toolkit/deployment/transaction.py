"""Fail-closed multi-file deployment with independent backups and recovery journal.

This is not a general-purpose installer. It applies an explicit mapping of
already-built payload files to an existing target tree, and only when a fixed
whole-tree snapshot and every per-file before/after digest match.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..hashing import hash_file
from ..manifest import ManifestError, build_manifest, load_manifest, validate_manifest, write_manifest
from ..path_safety import (
    PathSafetyError,
    assert_no_reparse_tree,
    compare_snapshots,
    normalize_target,
    snapshot_tree,
)

JOURNAL_SCHEMA_VERSION = 1
_HEX = frozenset("0123456789abcdefABCDEF")
_ACTIVE_STATES = {"applying", "rollback_in_progress"}
_KNOWN_HASH_STATES = {"before", "candidate"}
_VALID_STATES = {
    "preparing", "prepared", "applying", "rollback_in_progress", "deployed",
    "rolled_back", "aborted", "preparation_failed", "manual_recovery_required",
}


class DeploymentError(RuntimeError):
    """Deployment failed, with the transaction retained for audit or recovery."""


class PreflightError(DeploymentError):
    """A safety precondition failed before any target file was replaced."""


class ManualRecoveryRequired(DeploymentError):
    """The observed state is not provably safe for automated recovery."""


class TransactionStateError(DeploymentError):
    """The journal is malformed, unsupported, or in an invalid state."""


@dataclass(frozen=True)
class DeploymentTarget:
    """One explicit payload-to-target mapping and its required hashes."""

    source_path: str
    target_path: str
    before_sha256: str
    candidate_sha256: str


@dataclass(frozen=True)
class DeploymentPlan:
    """Inputs for one deployment.

    `fixed_snapshot` must be captured before the deployment window. The caller
    supplies an observed version from its own reliable version probe; this
    module compares it with `expected_version` but does not guess game versions.
    """

    source_root: os.PathLike[str] | str
    target_root: os.PathLike[str] | str
    transaction_root: os.PathLike[str] | str
    expected_version: str
    targets: Sequence[DeploymentTarget]
    fixed_snapshot: Mapping[str, Any]


def _valid_sha(value: str, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in _HEX for char in value):
        raise PreflightError(f"{label} must be a 64-character SHA-256 hex digest")
    return value.upper()


def _root_fingerprint(root: Path) -> str:
    canonical = os.path.normcase(os.path.normpath(os.fspath(root.resolve(strict=True))))
    return hashlib.sha256(canonical.encode("utf-8", errors="strict")).hexdigest().upper()


def capture_fixed_snapshot(root: os.PathLike[str] | str) -> dict[str, Any]:
    """Capture a whole-tree snapshot bound to this canonical root location."""
    root_path = Path(root).resolve(strict=True)
    assert_no_reparse_tree(root_path)
    return {
        "schema_version": 1,
        "root_fingerprint": _root_fingerprint(root_path),
        "files": snapshot_tree(root_path),
    }


def _inside(candidate: Path, root: Path) -> bool:
    candidate_text = os.path.normcase(os.path.normpath(os.fspath(candidate)))
    root_text = os.path.normcase(os.path.normpath(os.fspath(root)))
    try:
        return os.path.commonpath([candidate_text, root_text]) == root_text
    except ValueError:
        return False


def _overlap(left: Path, right: Path) -> bool:
    return _inside(left, right) or _inside(right, left)


def _canonical_directory(path: os.PathLike[str] | str, label: str) -> Path:
    result = Path(os.path.abspath(os.fspath(path)))
    if not result.is_dir():
        raise PreflightError(f"{label} is not an existing directory")
    try:
        # Validate the caller's original path before resolving it; resolving
        # first would erase evidence that the supplied root was a junction.
        normalize_target(result, "__localization_toolkit_root_probe__")
    except PathSafetyError as exc:
        raise PreflightError(f"{label} crosses a reparse point or is unsafe: {exc}") from exc
    return result


def _target_file(root: Path, raw: str) -> Path:
    normalized = normalize_target(root, raw)
    path = normalized.full_path
    try:
        info = path.lstat()
    except OSError as exc:
        raise PreflightError(f"Cannot inspect target {normalized.relative_path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PreflightError(f"Target is not a regular file: {normalized.relative_path}")
    return path


def _candidate_file(root: Path, raw: str) -> Path:
    normalized = normalize_target(root, raw)
    path = normalized.full_path
    try:
        info = path.lstat()
    except OSError as exc:
        raise PreflightError(f"Cannot inspect candidate {normalized.relative_path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PreflightError(f"Candidate is not a regular file: {normalized.relative_path}")
    return path


def _verify_fixed_baseline(root: Path, fixed_snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    _validate_snapshot_binding(root, fixed_snapshot, error_type=PreflightError)
    before = fixed_snapshot["files"]
    current = snapshot_tree(root)
    try:
        diff = compare_snapshots(before, current, root=root, authorized_paths=())
    except PathSafetyError as exc:
        raise PreflightError(f"Fixed snapshot cannot be normalized under target root: {exc}") from exc
    if diff["added"] or diff["removed"] or diff["changed"]:
        raise PreflightError(
            "Target tree differs from its fixed snapshot: "
            f"added={len(diff['added'])}, removed={len(diff['removed'])}, changed={len(diff['changed'])}"
        )
    return current


def _validate_snapshot_binding(root: Path, fixed_snapshot: Mapping[str, Any], *, error_type: type[DeploymentError]) -> None:
    if fixed_snapshot.get("schema_version") != 1 or not isinstance(fixed_snapshot.get("files"), list):
        raise error_type("Fixed snapshot schema is invalid")
    if fixed_snapshot.get("root_fingerprint") != _root_fingerprint(root):
        raise error_type("Fixed snapshot belongs to a different canonical target root")
    # Snapshots emitted by snapshot_tree are always root-relative POSIX paths.
    # Reject absolute or differently rooted records instead of letting the
    # general-purpose manifest normalizer reinterpret them.
    for index, row in enumerate(fixed_snapshot["files"]):
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise error_type(f"Fixed snapshot entry {index} has no relative path")
        try:
            normalized = normalize_target(root, row["path"])
        except PathSafetyError as exc:
            raise error_type(f"Fixed snapshot path {row.get('path')!r} is unsafe: {exc}") from exc
        if row["path"] != normalized.relative_path:
            raise error_type(f"Fixed snapshot path is not canonical and root-relative: {row['path']!r}")
        size = row.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise error_type(f"Fixed snapshot size is invalid for {row['path']!r}")


def _validate_plan(plan: DeploymentPlan, observed_version: str, *, transaction_must_not_exist: bool) -> tuple[Path, Path, Path, list[dict[str, Any]]]:
    if not isinstance(plan.expected_version, str) or not plan.expected_version:
        raise PreflightError("expected_version must be a non-empty version identifier")
    if observed_version != plan.expected_version:
        raise PreflightError(f"Target version mismatch: expected {plan.expected_version!r}, observed {observed_version!r}")
    source_root = _canonical_directory(plan.source_root, "Source root")
    target_root = _canonical_directory(plan.target_root, "Target root")
    assert_no_reparse_tree(source_root)
    assert_no_reparse_tree(target_root)
    if _overlap(source_root, target_root):
        raise PreflightError("Source and target roots must be disjoint")

    transaction_raw = Path(plan.transaction_root)
    transaction_parent = _canonical_directory(transaction_raw.parent, "Transaction parent")
    normalized_txn = normalize_target(transaction_parent, transaction_raw.name)
    transaction_path = normalized_txn.full_path
    if _overlap(transaction_path, target_root) or _overlap(transaction_path, source_root):
        raise PreflightError("Transaction/backup directory must be outside source and target roots")
    if transaction_must_not_exist and transaction_path.exists():
        raise PreflightError("Transaction directory already exists; refusing reuse or overwrite")

    if not plan.targets:
        raise PreflightError("Deployment whitelist must contain at least one target")
    target_keys: set[str] = set()
    checked: list[dict[str, Any]] = []
    for index, target in enumerate(plan.targets):
        if not isinstance(target, DeploymentTarget):
            raise PreflightError(f"Target entry {index} is not a DeploymentTarget")
        try:
            destination = normalize_target(target_root, target.target_path)
            source = normalize_target(source_root, target.source_path)
        except PathSafetyError as exc:
            raise PreflightError(f"Unsafe path in target entry {index}: {exc}") from exc
        if destination.key in target_keys:
            raise PreflightError(f"Duplicate normalized deployment target: {target.target_path}")
        target_keys.add(destination.key)
        before_hash = _valid_sha(target.before_sha256, f"before_sha256 for {target.target_path}")
        candidate_hash = _valid_sha(target.candidate_sha256, f"candidate_sha256 for {target.target_path}")
        dest_path = _target_file(target_root, target.target_path)
        source_path = _candidate_file(source_root, target.source_path)
        if hash_file(dest_path) != before_hash:
            raise PreflightError(f"Original target hash mismatch: {target.target_path}")
        if hash_file(source_path) != candidate_hash:
            raise PreflightError(f"Candidate hash mismatch: {target.source_path}")
        checked.append({
            "index": index,
            "source_path": source.relative_path,
            "target_path": destination.relative_path,
            "before_sha256": before_hash,
            "candidate_sha256": candidate_hash,
            "before_mode": stat.S_IMODE(dest_path.stat().st_mode),
            "backup_path": f"backup/{index:06d}.bin",
            "stage_path": f"staging/{index:06d}.candidate",
            "state": "pending",
        })
    baseline = _verify_fixed_baseline(target_root, plan.fixed_snapshot)
    # Same-volume atomic replacement is required; fail before creating a
    # transaction or backup if any target parent lives on another filesystem.
    transaction_device = os.stat(transaction_parent).st_dev
    for item in checked:
        dest = _target_file(target_root, item["target_path"])
        if os.stat(dest.parent).st_dev != transaction_device:
            raise PreflightError(f"Transaction staging and target are not on the same filesystem: {item['target_path']}")
    return source_root, target_root, transaction_path, baseline


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_fsync(source: Path, destination: Path, *, mode: int | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        if mode is not None:
            os.chmod(destination, stat.S_IMODE(mode))
        output_stream.flush()
        os.fsync(output_stream.fileno())


def _replace_atomically(source: Path, destination: Path) -> None:
    """Replace one target from a verified sibling-filesystem staging file."""
    if os.stat(source).st_dev != os.stat(destination.parent).st_dev:
        raise DeploymentError("Atomic replacement refused across filesystems")
    os.replace(source, destination)
    _fsync_directory(destination.parent)


def _append_event(journal: dict[str, Any], event: str, **details: Any) -> None:
    journal["events"].append({"at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **details})


def _save_journal(transaction_root: Path, journal: Mapping[str, Any]) -> None:
    _write_json_atomic(transaction_root / "transaction.json", journal)


def _read_journal(transaction_root: Path) -> dict[str, Any]:
    try:
        assert_no_reparse_tree(transaction_root)
        with (transaction_root / "transaction.json").open("r", encoding="utf-8") as stream:
            journal = json.load(stream)
    except (OSError, json.JSONDecodeError, PathSafetyError) as exc:
        raise TransactionStateError(f"Cannot read a trustworthy transaction journal: {exc}") from exc
    if not isinstance(journal, dict) or journal.get("schema_version") != JOURNAL_SCHEMA_VERSION:
        raise TransactionStateError("Unsupported or malformed transaction journal")
    if not isinstance(journal.get("targets"), list) or not isinstance(journal.get("events"), list):
        raise TransactionStateError("Transaction journal is missing required arrays")
    if journal.get("state") not in _VALID_STATES or not isinstance(journal.get("transaction_id"), str):
        raise TransactionStateError("Transaction journal has an unknown state or identifier")
    if not isinstance(journal.get("fixed_snapshot"), dict):
        raise TransactionStateError("Transaction journal has no fixed snapshot")
    seen_targets: set[str] = set()
    seen_indexes: set[int] = set()
    for index, item in enumerate(journal["targets"]):
        if not isinstance(item, dict):
            raise TransactionStateError(f"Transaction target {index} is not an object")
        for field in ("target_path", "source_path", "before_sha256", "candidate_sha256", "backup_path", "stage_path"):
            if not isinstance(item.get(field), str) or not item[field]:
                raise TransactionStateError(f"Transaction target {index} has invalid {field}")
        item_index = item.get("index")
        if isinstance(item_index, bool) or not isinstance(item_index, int) or item_index < 0 or item_index in seen_indexes:
            raise TransactionStateError(f"Transaction target {index} has invalid or repeated index")
        seen_indexes.add(item_index)
        key = item["target_path"].replace("\\", "/").casefold()
        if key in seen_targets:
            raise TransactionStateError(f"Transaction journal repeats target path {item['target_path']!r}")
        seen_targets.add(key)
        try:
            _valid_sha(item["before_sha256"], "journal before hash")
            _valid_sha(item["candidate_sha256"], "journal candidate hash")
        except PreflightError as exc:
            raise TransactionStateError(str(exc)) from exc
    if any(not isinstance(event, dict) for event in journal["events"]):
        raise TransactionStateError("Transaction journal contains a malformed event")
    return journal


def _journal_target(root: Path, item: Mapping[str, Any]) -> Path:
    try:
        path = normalize_target(root, str(item["target_path"])).full_path
    except (KeyError, PathSafetyError) as exc:
        raise TransactionStateError(f"Unsafe target path in transaction journal: {exc}") from exc
    try:
        info = path.lstat()
    except OSError as exc:
        raise ManualRecoveryRequired(f"Cannot inspect target during recovery: {item.get('target_path')}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ManualRecoveryRequired(f"Target is no longer a regular file: {item.get('target_path')}")
    return path


def _transaction_outside_target(transaction_root: Path, target_root: Path) -> None:
    txn = transaction_root.resolve(strict=True)
    target = target_root.resolve(strict=True)
    if _overlap(txn, target):
        raise TransactionStateError("Transaction directory overlaps the target tree")


def _backup_manifest(transaction_root: Path) -> dict[str, Any]:
    manifest_path = transaction_root / "backup_manifest.json"
    try:
        manifest = load_manifest(manifest_path)
        issues = validate_manifest(manifest, transaction_root / "backup", verify_files=True)
    except (OSError, ManifestError, PathSafetyError) as exc:
        raise ManualRecoveryRequired(f"Original backup manifest is invalid: {exc}") from exc
    if issues:
        raise ManualRecoveryRequired("Original backup integrity check failed: " + "; ".join(issues))
    return manifest


def _validate_transaction_roots(target_root: Path, transaction_root: Path, journal: Mapping[str, Any]) -> None:
    _transaction_outside_target(transaction_root, target_root)
    expected = journal.get("target_root_fingerprint")
    if expected != _root_fingerprint(target_root):
        raise TransactionStateError("Transaction belongs to a different target root")
    if journal.get("transaction_id") != transaction_root.name:
        raise TransactionStateError("Transaction directory name does not match its journal identifier")


def _preflight_recovery(target_root: Path, transaction_root: Path, journal: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Path], dict[str, str]]:
    _validate_transaction_roots(target_root, transaction_root, journal)
    fixed = journal.get("fixed_snapshot")
    if not isinstance(fixed, dict):
        raise TransactionStateError("Fixed snapshot in journal is malformed")
    _validate_snapshot_binding(target_root, fixed, error_type=TransactionStateError)
    backup_manifest = _backup_manifest(transaction_root)
    backup_records = {str(record["path"]).casefold(): record for record in backup_manifest["files"]}
    backup_paths: dict[str, Path] = {}
    states: dict[str, str] = {}
    authorized = [str(item["target_path"]) for item in journal["targets"]]
    before = journal["fixed_snapshot"]["files"]
    current = snapshot_tree(target_root)
    try:
        diff = compare_snapshots(before, current, root=target_root, authorized_paths=authorized)
    except (PathSafetyError, KeyError) as exc:
        raise ManualRecoveryRequired(f"Cannot compare the current tree against its fixed snapshot: {exc}") from exc
    if diff["added"] or diff["removed"] or diff["outside_whitelist"]:
        raise ManualRecoveryRequired(
            "Unexpected tree changes prevent automated recovery: "
            f"added={len(diff['added'])}, removed={len(diff['removed'])}, outside={len(diff['outside_whitelist'])}"
        )

    for item in journal["targets"]:
        target_path = _journal_target(target_root, item)
        actual = hash_file(target_path)
        before_hash = _valid_sha(item.get("before_sha256"), "journal before hash")
        candidate_hash = _valid_sha(item.get("candidate_sha256"), "journal candidate hash")
        if actual == before_hash:
            states[item["target_path"].casefold()] = "before"
        elif actual == candidate_hash:
            states[item["target_path"].casefold()] = "candidate"
        else:
            raise ManualRecoveryRequired(
                f"Unknown target hash; refusing automated overwrite: {item['target_path']} ({actual})"
            )

        backup_rel = str(item.get("backup_path", ""))
        try:
            backup_normalized = normalize_target(transaction_root / "backup", backup_rel)
        except PathSafetyError as exc:
            raise ManualRecoveryRequired(f"Unsafe backup path for {item['target_path']}: {exc}") from exc
        manifest_record = backup_records.get(backup_normalized.relative_path.casefold())
        if not manifest_record or manifest_record["sha256"].upper() != before_hash:
            raise ManualRecoveryRequired(f"Backup manifest does not prove the original hash: {item['target_path']}")
        backup_path = backup_normalized.full_path
        if hash_file(backup_path) != before_hash:
            raise ManualRecoveryRequired(f"Original backup is corrupt: {item['target_path']}")
        backup_paths[item["target_path"].casefold()] = backup_path

    return journal["targets"], backup_paths, states


def _set_manual(journal: dict[str, Any], transaction_root: Path, message: str) -> ManualRecoveryRequired:
    journal["state"] = "manual_recovery_required"
    journal["error"] = message
    _append_event(journal, "manual_recovery_required", message=message)
    _save_journal(transaction_root, journal)
    return ManualRecoveryRequired(message)


def _rollback_known_state(target_root: Path, transaction_root: Path, journal: dict[str, Any]) -> dict[str, Any]:
    try:
        targets, backups, states = _preflight_recovery(target_root, transaction_root, journal)
    except ManualRecoveryRequired as exc:
        raise _set_manual(journal, transaction_root, str(exc)) from exc

    journal["state"] = "rollback_in_progress"
    _append_event(journal, "rollback_started")
    _save_journal(transaction_root, journal)
    try:
        for item in targets:
            key = item["target_path"].casefold()
            if states[key] == "before":
                item["state"] = "restored_or_unchanged"
                _append_event(journal, "rollback_noop_original_already_present", target=item["target_path"])
                _save_journal(transaction_root, journal)
                continue
            destination = _journal_target(target_root, item)
            actual = hash_file(destination)
            if actual != item["candidate_sha256"].upper():
                raise ManualRecoveryRequired(f"Target changed during rollback precheck: {item['target_path']}")
            restore_stage = transaction_root / "staging" / (str(item["index"]).zfill(6) + ".restore")
            if restore_stage.exists():
                if hash_file(restore_stage) != item["before_sha256"].upper():
                    raise ManualRecoveryRequired(f"Existing restore stage is not verified: {item['target_path']}")
            else:
                _copy_fsync(backups[key], restore_stage, mode=int(item.get("before_mode", 0o644)))
            if hash_file(restore_stage) != item["before_sha256"].upper():
                raise ManualRecoveryRequired(f"Restore stage hash mismatch: {item['target_path']}")
            item["state"] = "restore_intent"
            journal["current_intent"] = {"operation": "restore", "target_path": item["target_path"]}
            _append_event(journal, "restore_intent", target=item["target_path"])
            _save_journal(transaction_root, journal)
            _replace_atomically(restore_stage, destination)
            if hash_file(destination) != item["before_sha256"].upper():
                raise ManualRecoveryRequired(f"Restored target hash mismatch: {item['target_path']}")
            item["state"] = "restored"
            journal["current_intent"] = None
            _append_event(journal, "target_restored", target=item["target_path"])
            _save_journal(transaction_root, journal)

        final = snapshot_tree(target_root)
        diff = compare_snapshots(journal["fixed_snapshot"]["files"], final, root=target_root, authorized_paths=())
        if diff["added"] or diff["removed"] or diff["changed"]:
            raise ManualRecoveryRequired(
                "Rollback finished file replacements but the full tree is not back at baseline: "
                f"added={len(diff['added'])}, removed={len(diff['removed'])}, changed={len(diff['changed'])}"
            )
    except ManualRecoveryRequired as exc:
        raise _set_manual(journal, transaction_root, str(exc)) from exc
    except Exception as exc:
        journal["state"] = "manual_recovery_required"
        journal["error"] = f"Rollback interrupted: {type(exc).__name__}: {exc}"
        _append_event(journal, "rollback_interrupted", message=journal["error"])
        _save_journal(transaction_root, journal)
        raise ManualRecoveryRequired(journal["error"]) from exc

    journal["state"] = "rolled_back"
    journal["current_intent"] = None
    journal["error"] = None
    _append_event(journal, "rollback_verified_against_fixed_snapshot")
    _save_journal(transaction_root, journal)
    return {"transaction_id": journal["transaction_id"], "state": journal["state"], "target_count": len(targets)}


def deploy(plan: DeploymentPlan, *, observed_version: str) -> dict[str, Any]:
    """Back up, verify, replace, audit, and automatically roll back on failure."""
    source_root, target_root, transaction_root, baseline = _validate_plan(
        plan, observed_version, transaction_must_not_exist=True
    )
    try:
        transaction_root.mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        raise PreflightError(f"Cannot create a new transaction directory: {exc}") from exc

    tx_targets: list[dict[str, Any]] = []
    for index, target in enumerate(plan.targets):
        source = normalize_target(source_root, target.source_path)
        destination = normalize_target(target_root, target.target_path)
        tx_targets.append({
            "index": index,
            "source_path": source.relative_path,
            "target_path": destination.relative_path,
            "before_sha256": target.before_sha256.upper(),
            "candidate_sha256": target.candidate_sha256.upper(),
            "before_mode": stat.S_IMODE(_target_file(target_root, target.target_path).stat().st_mode),
            "backup_path": f"{index:06d}.bin",
            "stage_path": f"{index:06d}.candidate",
            "state": "pending",
        })
    journal: dict[str, Any] = {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "transaction_id": transaction_root.name,
        "state": "preparing",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "expected_version": plan.expected_version,
        "observed_version": observed_version,
        "target_root_fingerprint": _root_fingerprint(target_root),
        "fixed_snapshot": dict(plan.fixed_snapshot),
        "targets": tx_targets,
        "current_intent": None,
        "events": [],
        "error": None,
    }
    _append_event(journal, "transaction_created", target_count=len(tx_targets))
    _save_journal(transaction_root, journal)
    try:
        (transaction_root / "backup").mkdir()
        (transaction_root / "staging").mkdir()
        backup_relpaths: list[str] = []
        candidate_relpaths: list[str] = []
        for item in tx_targets:
            source = _candidate_file(source_root, item["source_path"])
            destination = _target_file(target_root, item["target_path"])
            if hash_file(destination) != item["before_sha256"]:
                raise PreflightError(f"Original target changed before backup: {item['target_path']}")
            backup_path = transaction_root / "backup" / item["backup_path"]
            stage_path = transaction_root / "staging" / item["stage_path"]
            _copy_fsync(destination, backup_path, mode=item["before_mode"])
            if hash_file(backup_path) != item["before_sha256"]:
                raise PreflightError(f"Original backup failed hash verification: {item['target_path']}")
            _copy_fsync(source, stage_path, mode=item["before_mode"])
            if hash_file(stage_path) != item["candidate_sha256"]:
                raise PreflightError(f"Candidate staging failed hash verification: {item['target_path']}")
            backup_relpaths.append(item["backup_path"])
            candidate_relpaths.append(item["stage_path"])

        backup_manifest = build_manifest(
            transaction_root / "backup",
            backup_relpaths,
            metadata={"kind": "independent_original_backup", "transaction_id": transaction_root.name},
        )
        write_manifest(transaction_root / "backup_manifest.json", backup_manifest)
        if validate_manifest(backup_manifest, transaction_root / "backup"):
            raise PreflightError("Independent backup manifest verification failed")
        candidate_manifest = build_manifest(
            transaction_root / "staging",
            candidate_relpaths,
            metadata={"kind": "verified_candidate_staging", "transaction_id": transaction_root.name},
        )
        write_manifest(transaction_root / "candidate_manifest.json", candidate_manifest)
        if validate_manifest(candidate_manifest, transaction_root / "staging"):
            raise PreflightError("Candidate staging manifest verification failed")

        # Recheck the fixed tree after backup/staging and before the first write.
        _verify_fixed_baseline(target_root, plan.fixed_snapshot)
        _backup_manifest(transaction_root)
        for item in tx_targets:
            if hash_file(_target_file(target_root, item["target_path"])) != item["before_sha256"]:
                raise PreflightError(f"Target changed while backup was prepared: {item['target_path']}")
        journal["state"] = "prepared"
        _append_event(journal, "backup_and_staging_verified", backup_count=len(tx_targets))
        _save_journal(transaction_root, journal)
        journal["state"] = "applying"
        _append_event(journal, "apply_started")
        _save_journal(transaction_root, journal)

        for item in tx_targets:
            destination = _target_file(target_root, item["target_path"])
            stage_path = transaction_root / "staging" / item["stage_path"]
            if hash_file(destination) != item["before_sha256"]:
                raise PreflightError(f"Last-moment original hash mismatch: {item['target_path']}")
            if hash_file(stage_path) != item["candidate_sha256"]:
                raise PreflightError(f"Staged candidate changed: {item['target_path']}")
            item["state"] = "replace_intent"
            journal["current_intent"] = {"operation": "replace", "target_path": item["target_path"]}
            _append_event(journal, "replace_intent", target=item["target_path"])
            _save_journal(transaction_root, journal)
            _replace_atomically(stage_path, destination)
            if hash_file(destination) != item["candidate_sha256"]:
                raise ManualRecoveryRequired(f"Post-replace candidate hash mismatch: {item['target_path']}")
            item["state"] = "replaced"
            journal["current_intent"] = None
            _append_event(journal, "target_replaced_and_verified", target=item["target_path"])
            _save_journal(transaction_root, journal)

        after = snapshot_tree(target_root)
        target_paths = [item["target_path"] for item in tx_targets]
        diff = compare_snapshots(baseline, after, root=target_root, authorized_paths=target_paths)
        if diff["added"] or diff["removed"] or diff["outside_whitelist"]:
            raise DeploymentError(
                "Post-deployment full-tree whitelist audit failed: "
                f"added={len(diff['added'])}, removed={len(diff['removed'])}, outside={len(diff['outside_whitelist'])}"
            )
        for item in tx_targets:
            if hash_file(_target_file(target_root, item["target_path"])) != item["candidate_sha256"]:
                raise DeploymentError(f"Post-deployment target hash mismatch: {item['target_path']}")
        journal["state"] = "deployed"
        journal["current_intent"] = None
        journal["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        journal["directory_diff"] = diff
        _append_event(journal, "deployment_verified_and_committed")
        _save_journal(transaction_root, journal)
        return {"transaction_id": journal["transaction_id"], "state": journal["state"],
                "target_count": len(tx_targets), "directory_diff": diff}
    except Exception as exc:
        journal["error"] = f"{type(exc).__name__}: {exc}"
        _append_event(journal, "deployment_exception", message=journal["error"])
        if journal["state"] in {"preparing", "prepared"}:
            journal["state"] = "preparation_failed"
            _save_journal(transaction_root, journal)
            raise DeploymentError(f"Deployment stopped before target writes; transaction retained at {transaction_root}") from exc
        journal["state"] = "rollback_in_progress"
        _save_journal(transaction_root, journal)
        try:
            result = _rollback_known_state(target_root, transaction_root, journal)
            raise DeploymentError(
                f"Deployment failed and all known target changes were rolled back: {exc}; transaction={result['transaction_id']}"
            ) from exc
        except ManualRecoveryRequired as recovery_exc:
            raise DeploymentError(
                f"Deployment failed and automatic recovery stopped for manual review: {recovery_exc}; transaction_root={transaction_root}"
            ) from exc


def inspect_transaction(target_root: os.PathLike[str] | str, transaction_root: os.PathLike[str] | str) -> dict[str, Any]:
    """Read and summarize a journal without modifying files."""
    root = _canonical_directory(target_root, "Target root")
    transaction = _canonical_directory(transaction_root, "Transaction root")
    journal = _read_journal(transaction)
    _validate_transaction_roots(root, transaction, journal)
    return {key: journal.get(key) for key in (
        "transaction_id", "state", "created_utc", "finished_utc", "expected_version",
        "observed_version", "targets", "current_intent", "error", "events", "directory_diff",
    )}


def recover_transaction(target_root: os.PathLike[str] | str, transaction_root: os.PathLike[str] | str) -> dict[str, Any]:
    """Inspect durable state and safely roll back a prepared/interrupted apply.

    A committed `deployed` transaction is only verified; use
    `rollback_transaction()` for an intentional rollback of a committed install.
    Unknown hashes, corrupted backups, root mismatch, or outside-tree changes
    leave the journal in `manual_recovery_required` and perform no overwrite.
    """
    root = _canonical_directory(target_root, "Target root")
    transaction = _canonical_directory(transaction_root, "Transaction root")
    journal = _read_journal(transaction)
    _validate_transaction_roots(root, transaction, journal)
    state = journal.get("state")
    if state == "deployed":
        _verify_deployed_state(root, transaction, journal)
        return {"transaction_id": journal["transaction_id"], "state": "deployed", "action": "verified_no_change"}
    if state in {"rolled_back", "aborted", "preparation_failed"}:
        _verify_original_state(root, journal)
        return {"transaction_id": journal["transaction_id"], "state": state, "action": "verified_no_change"}
    if state == "manual_recovery_required":
        raise ManualRecoveryRequired("Journal already requires manual recovery; automatic overwrite is disabled")
    if state not in {"preparing", "prepared", "applying", "rollback_in_progress"}:
        raise TransactionStateError(f"State {state!r} is not automatically recoverable")
    if state in {"preparing", "prepared"}:
        try:
            _verify_original_state(root, journal)
        except ManualRecoveryRequired as exc:
            raise _set_manual(journal, transaction, str(exc)) from exc
        journal["state"] = "aborted"
        journal["error"] = "Interrupted before first target replacement"
        _append_event(journal, "preparation_interrupted_no_target_writes_verified")
        _save_journal(transaction, journal)
        return {"transaction_id": journal["transaction_id"], "state": "aborted", "action": "no_target_changes"}
    return _rollback_known_state(root, transaction, journal)


def rollback_transaction(target_root: os.PathLike[str] | str, transaction_root: os.PathLike[str] | str) -> dict[str, Any]:
    """Explicitly restore originals from a verified backup without clobbering unknown bytes."""
    root = _canonical_directory(target_root, "Target root")
    transaction = _canonical_directory(transaction_root, "Transaction root")
    journal = _read_journal(transaction)
    _validate_transaction_roots(root, transaction, journal)
    if journal.get("state") == "rolled_back":
        _verify_original_state(root, journal)
        return {"transaction_id": journal["transaction_id"], "state": "rolled_back", "action": "verified_no_change"}
    if journal.get("state") not in {"deployed", "applying", "rollback_in_progress"}:
        raise TransactionStateError(f"Cannot explicitly roll back transaction in state {journal.get('state')!r}")
    return _rollback_known_state(root, transaction, journal)


def _verify_original_state(target_root: Path, journal: Mapping[str, Any]) -> None:
    try:
        diff = compare_snapshots(journal["fixed_snapshot"]["files"], snapshot_tree(target_root), root=target_root, authorized_paths=())
    except (PathSafetyError, KeyError) as exc:
        raise ManualRecoveryRequired(f"Cannot prove original snapshot: {exc}") from exc
    if diff["added"] or diff["removed"] or diff["changed"]:
        raise ManualRecoveryRequired(
            f"Target tree does not match the original snapshot: added={len(diff['added'])}, removed={len(diff['removed'])}, changed={len(diff['changed'])}"
        )


def _verify_deployed_state(target_root: Path, transaction_root: Path, journal: Mapping[str, Any]) -> None:
    _backup_manifest(transaction_root)
    authorized = [item["target_path"] for item in journal["targets"]]
    try:
        diff = compare_snapshots(journal["fixed_snapshot"]["files"], snapshot_tree(target_root),
                                root=target_root, authorized_paths=authorized)
    except (PathSafetyError, KeyError) as exc:
        raise ManualRecoveryRequired(f"Cannot verify committed full-tree state: {exc}") from exc
    if diff["added"] or diff["removed"] or diff["outside_whitelist"]:
        raise ManualRecoveryRequired("Committed target has non-whitelisted tree changes")
    for item in journal["targets"]:
        target = _journal_target(target_root, item)
        if hash_file(target) != item["candidate_sha256"].upper():
            raise ManualRecoveryRequired(f"Committed target hash changed: {item['target_path']}")
