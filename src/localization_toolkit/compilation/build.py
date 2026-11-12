"""Auditable compilation, evidence separation, and no-overwrite publication."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..hashing import hash_file
from ..manifest import build_manifest, write_manifest
from ..path_safety import PathSafetyError, assert_no_reparse_tree, normalize_target
from .models import (
    ArtifactValidator,
    BuildMode,
    BuildPlan,
    BuildReport,
    BuildTarget,
    BuildItemResult,
    CompilerAdapter,
    CompilerRequest,
    CompilerVersion,
    EvidenceLevel,
)


class CompilationError(RuntimeError):
    pass


class _Blocked(Exception):
    pass


def _sha_ok(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)


def _jsonable(value: object) -> object:
    if hasattr(value, "value"):
        return getattr(value, "value")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def _report_dict(report: BuildReport) -> dict[str, object]:
    return _jsonable(asdict(report))  # type: ignore[return-value]


def _verify_output_whitelist(artifact_root: Path, expected_paths: Sequence[str]) -> None:
    try:
        assert_no_reparse_tree(artifact_root)
    except PathSafetyError as exc:
        raise _Blocked(f"Compiler output tree contains an unsafe reparse point: {exc}") from exc
    actual = {path.relative_to(artifact_root).as_posix().casefold()
              for path in artifact_root.rglob("*") if path.is_file()}
    expected = {path.replace("\\", "/").casefold() for path in expected_paths}
    if actual != expected:
        extra = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise _Blocked(f"Compiler output whitelist mismatch; extra={extra}, missing={missing}")
    expected_directories = {
        "/".join(path.split("/")[:index]).casefold()
        for path in expected
        for index in range(1, len(path.split("/")))
    }
    actual_directories = {path.relative_to(artifact_root).as_posix().casefold()
                          for path in artifact_root.rglob("*") if path.is_dir()}
    if actual_directories != expected_directories:
        extra = sorted(actual_directories - expected_directories)
        missing = sorted(expected_directories - actual_directories)
        raise _Blocked(f"Compiler output directory whitelist mismatch; extra={extra}, missing={missing}")


def _write_report_once(path: Path, report: BuildReport) -> None:
    path = Path(path)
    if path.exists():
        raise CompilationError(f"Refusing to overwrite build report: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(_report_dict(report), stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_plan(plan: BuildPlan, validators: Sequence[ArtifactValidator]) -> tuple[Path, Path, dict[str, str], dict[str, str]]:
    input_root = Path(plan.input_root).absolute()
    if not input_root.is_dir():
        raise _Blocked("input_root must be an existing directory")
    if not plan.targets:
        raise _Blocked("At least one explicit build target is required")
    if not validators:
        raise _Blocked("At least one explicit output-format validator is required")
    if not plan.expected_compiler_version or not plan.expected_compiler_version.strip():
        raise _Blocked("An expected compiler version is required")
    if isinstance(plan.timeout_seconds, bool) or plan.timeout_seconds <= 0:
        raise _Blocked("timeout_seconds must be positive")

    output = Path(plan.output_directory).absolute()
    parent = output.parent
    if not parent.is_dir():
        raise _Blocked("Output parent must already exist")
    if output.exists():
        raise _Blocked("Output directory already exists; builds never overwrite artifacts")
    try:
        assert_no_reparse_tree(parent)
        normalize_target(parent, output.name)
    except PathSafetyError as exc:
        raise _Blocked(f"Unsafe output location: {exc}") from exc

    source_records: set[str] = set()
    output_keys: set[str] = set()
    output_relatives: dict[str, str] = {}
    target_hashes: dict[str, str] = {}
    for target in plan.targets:
        source = target.source
        if source.purpose != target.mode:
            raise _Blocked(f"Build mode does not match review evidence for {source.script_id}")
        if source.unresolved_count != 0 or not source.structure_fingerprint:
            raise _Blocked(f"Source structure evidence is incomplete for {source.script_id}")
        if not _sha_ok(source.sha256) or not _sha_ok(source.source_sha256):
            raise _Blocked(f"Source fingerprint is invalid for {source.script_id}")
        try:
            normalized_source = normalize_target(input_root, source.relative_path)
        except PathSafetyError as exc:
            raise _Blocked(f"Unsafe source path for {source.script_id}: {exc}") from exc
        if not normalized_source.full_path.is_file():
            raise _Blocked(f"Compilation source is missing: {source.relative_path}")
        actual_input_hash = hash_file(normalized_source.full_path)
        if actual_input_hash.casefold() != source.sha256.casefold():
            raise _Blocked(f"Staged source changed after review: {source.relative_path}")
        if normalized_source.key in source_records:
            raise _Blocked(f"Duplicate build source: {source.relative_path}")
        source_records.add(normalized_source.key)
        if target.mode == BuildMode.TRANSLATED:
            if not source.slot_ids or not source.reviewer_ids or source.rules_fingerprint is None:
                raise _Blocked(f"Human approval evidence is incomplete for {source.script_id}")
        elif target.mode == BuildMode.ORIGINAL_ROUNDTRIP:
            if not _sha_ok(target.expected_artifact_sha256):
                raise _Blocked(f"Original roundtrip requires an independently recorded baseline artifact hash: {source.script_id}")
        else:
            raise _Blocked(f"Unsupported build mode: {target.mode}")
        try:
            # Reuse the shared Windows path policy before invoking any compiler.
            # The normalized key also detects slash and case aliases.
            normalized_output = normalize_target(input_root, target.output_relative_path)
            output_key = normalized_output.key
            if output_key in output_keys:
                raise _Blocked(f"Duplicate output path: {target.output_relative_path}")
            output_keys.add(output_key)
            output_relatives[output_key] = normalized_output.relative_path
        except PathSafetyError as exc:
            raise _Blocked(f"Unsafe output path: {exc}") from exc
        if target.expected_artifact_sha256 is not None and not _sha_ok(target.expected_artifact_sha256):
            raise _Blocked(f"Invalid expected output hash for {target.output_relative_path}")
        if target.expected_artifact_sha256 is not None:
            target_hashes[output_key] = target.expected_artifact_sha256.upper()

    if plan.expected_artifact_index is not None:
        index_keys: dict[str, str] = {}
        for path, digest in plan.expected_artifact_index.items():
            try:
                key = normalize_target(input_root, path).key
            except PathSafetyError as exc:
                raise _Blocked(f"Unsafe artifact index path {path!r}: {exc}") from exc
            if key in index_keys or not _sha_ok(digest):
                raise _Blocked("Artifact index contains duplicate paths or invalid hashes")
            index_keys[key] = digest.upper()
        if set(index_keys) != output_keys:
            raise _Blocked("Expected artifact index paths do not exactly match the explicit build targets")
        for key, digest in index_keys.items():
            if key in target_hashes and target_hashes[key] != digest:
                raise _Blocked("Expected artifact index conflicts with a target hash")
        target_hashes.update(index_keys)

    return input_root, output, target_hashes, output_relatives


def build(
    plan: BuildPlan,
    *,
    compiler: CompilerAdapter,
    validators: Sequence[ArtifactValidator],
) -> BuildReport:
    """Compile explicit reviewed inputs into a new sibling directory.

    Compiler exit, format validation, original byte roundtrip, and translated
    source structure are recorded as separate evidence. A successful build is
    not a claim of successful game runtime behavior.
    """
    build_id = uuid.uuid4().hex
    results: list[BuildItemResult] = []
    version: CompilerVersion | None = None
    temporary: Path | None = None
    residual_temporary: str | None = None
    final_output: Path | None = None
    error: str | None = None
    status = "blocked"
    try:
        input_root, output, expected_outputs, output_relatives = _validate_plan(plan, validators)
        final_output = output
        try:
            version = compiler.get_version(plan.timeout_seconds)
        except Exception as exc:
            raise _Blocked(f"Compiler version probe failed: {exc}") from exc
        if version.version != plan.expected_compiler_version:
            raise _Blocked(f"Compiler version changed: expected {plan.expected_compiler_version!r}, got {version.version!r}")

        temporary = Path(tempfile.mkdtemp(prefix=f".locbuild-{build_id}-", dir=output.parent))
        marker = {"schema_version": 1, "build_id": build_id, "status": "building"}
        (temporary / ".localization-build-temp.json").write_text(
            json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        artifact_root = temporary / "artifacts"
        artifact_root.mkdir()

        for target in plan.targets:
            source = target.source
            output_key = normalize_target(input_root, target.output_relative_path).key
            normalized_output_relative = output_relatives[output_key]
            source_path = normalize_target(input_root, source.relative_path).full_path
            output_path = normalize_target(artifact_root, normalized_output_relative).full_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            evidence = {
                EvidenceLevel.INPUT_STRUCTURE.value: "passed",
                EvidenceLevel.HUMAN_APPROVAL.value: "passed" if target.mode == BuildMode.TRANSLATED else "not_applicable_control_source",
                EvidenceLevel.COMPILER_EXIT.value: "not_run",
                EvidenceLevel.OUTPUT_FORMAT.value: "not_run",
                EvidenceLevel.OUTPUT_HASH.value: "not_run",
                EvidenceLevel.ORIGINAL_ROUNDTRIP.value: "not_applicable",
                EvidenceLevel.TRANSLATED_STRUCTURE.value: "passed" if target.mode == BuildMode.TRANSLATED else "not_applicable",
                EvidenceLevel.RUNTIME.value: "not_verified",
            }
            execution = compiler.compile(CompilerRequest(source_path, output_path), plan.timeout_seconds)
            item_error = None
            validation_data: dict[str, object] | None = None
            output_hash: str | None = None
            if execution.timed_out:
                item_error = "compiler timed out"
            elif execution.exit_code != 0:
                item_error = f"compiler exited with code {execution.exit_code}"
            else:
                evidence[EvidenceLevel.COMPILER_EXIT.value] = "passed"
                if not output_path.is_file():
                    item_error = "compiler reported success but output file is missing"
                else:
                    output_hash = hash_file(output_path)
                    evidence[EvidenceLevel.OUTPUT_HASH.value] = "passed"
                    try:
                        validations = {validator.validator_id: validator.validate(output_path)
                                       for validator in validators}
                        validation_data = {"validators": validations}
                        evidence[EvidenceLevel.OUTPUT_FORMAT.value] = "passed"
                    except Exception as exc:
                        item_error = f"artifact validation failed: {type(exc).__name__}: {exc}"
                    pinned_hash = expected_outputs.get(target.output_relative_path.replace("\\", "/").casefold())
                    if pinned_hash and output_hash.casefold() != pinned_hash.casefold():
                        if plan.expected_artifact_index is not None:
                            item_error = "compiled artifact hash differs from the explicit artifact index"
                        else:
                            item_error = "compiled output hash differs from the target's expected hash"
                    if target.mode == BuildMode.ORIGINAL_ROUNDTRIP:
                        if output_hash.casefold() != str(pinned_hash).casefold():
                            item_error = "original source recompilation is not byte-identical to the supplied baseline artifact"
                        else:
                            evidence[EvidenceLevel.ORIGINAL_ROUNDTRIP.value] = "passed"
            result = BuildItemResult(
                source.relative_path, normalized_output_relative, target.mode.value,
                source.sha256, output_hash, execution, validation_data, evidence, item_error,
            )
            results.append(result)
            if item_error:
                raise _Blocked(f"{source.script_id}: {item_error}")

        artifact_paths = [item.output_relative_path for item in results]
        _verify_output_whitelist(artifact_root, artifact_paths)
        manifest = build_manifest(artifact_root, artifact_paths,
                                  metadata={"build_id": build_id, "compiler_id": version.compiler_id,
                                            "compiler_version": version.version})
        if expected_outputs:
            observed = {str(row["path"]).casefold(): str(row["sha256"]).upper() for row in manifest["files"]}
            if any(observed.get(path) != digest for path, digest in expected_outputs.items()):
                raise _Blocked("Compiled artifacts do not match the explicit artifact index")
        write_manifest(temporary / "artifact_manifest.json", manifest)
        report = BuildReport(build_id, "succeeded", version, tuple(results), str(output), None, None)
        (temporary / "build_report.json").write_text(
            json.dumps(_report_dict(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")
        marker["status"] = "validated"
        (temporary / ".localization-build-temp.json").write_text(
            json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        # The temporary directory is a sibling, so rename stays on one filesystem.
        if output.exists():
            raise _Blocked("Output directory appeared during build; refusing publication")
        os.rename(temporary, output)
        temporary = None
        status = "succeeded"
        report = BuildReport(build_id, status, version, tuple(results), str(output), None, None)
        return report
    except _Blocked as exc:
        error = str(exc)
        status = "blocked" if not results else "failed"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        status = "failed"
    finally:
        if temporary is not None:
            try:
                shutil.rmtree(temporary, ignore_errors=False)
            except OSError as exc:
                residual_temporary = str(temporary)
                status = "failed"
                error = f"{error or 'build stopped'}; temporary cleanup failed: {exc}"

    report = BuildReport(build_id, status, version, tuple(results), None, residual_temporary, error)
    if plan.report_path is not None:
        _write_report_once(Path(plan.report_path), report)
    return report


def cleanup_abandoned_build(build_parent: str | Path, temporary_directory: str | Path, build_id: str) -> None:
    """Explicitly remove a marked stale temp tree after operator inspection.

    It never runs automatically. This is a cleanup aid for an interrupted
    process, not crash recovery or proof that compiler child processes stopped.
    """
    parent = Path(build_parent).absolute()
    if not parent.is_dir():
        raise CompilationError("Build parent must exist")
    candidate = Path(temporary_directory).absolute()
    if candidate.parent != parent or not candidate.name.startswith(".locbuild-"):
        raise CompilationError("Abandoned build path is not a direct marked child of build_parent")
    try:
        marker_path = normalize_target(parent, candidate.name).full_path / ".localization-build-temp.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, PathSafetyError) as exc:
        raise CompilationError(f"Cannot verify abandoned build marker: {exc}") from exc
    if (marker.get("schema_version") != 1 or marker.get("build_id") != build_id
            or marker.get("status") not in {"building", "validated"}):
        raise CompilationError("Abandoned build marker does not match requested build ID")
    assert_no_reparse_tree(candidate)
    shutil.rmtree(candidate)
