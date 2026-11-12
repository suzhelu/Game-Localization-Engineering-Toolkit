"""Strict, portable project configuration for the localization CLI."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from ..path_safety import PathSafetyError, normalize_target


class ProjectConfigError(ValueError):
    pass


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectConfigError(f"Duplicate configuration key: {key}")
        result[key] = value
    return result


class Project:
    CONFIG_NAME = "localize.json"

    def __init__(self, root: Path, config: dict[str, Any], config_bytes: bytes):
        self.root = root
        self.config = config
        self.config_bytes = config_bytes
        self.validate()

    @classmethod
    def load(cls, root: str | Path) -> "Project":
        project_root = Path(root).absolute()
        if not project_root.is_dir():
            raise ProjectConfigError(f"Project directory does not exist: {project_root}")
        try:
            config_path = normalize_target(project_root, cls.CONFIG_NAME).full_path
            config_bytes = config_path.read_bytes()
            config = json.loads(config_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs)
        except (OSError, UnicodeError, json.JSONDecodeError, PathSafetyError) as exc:
            raise ProjectConfigError(f"Cannot read {cls.CONFIG_NAME}: {exc}") from exc
        if not isinstance(config, dict):
            raise ProjectConfigError("Project configuration root must be an object")
        return cls(project_root, config, config_bytes)

    def validate(self) -> None:
        expected = {"schema_version", "project", "general", "game_specific", "inputs", "translation", "compiler", "validation", "paths", "package", "deployment"}
        if set(self.config) != expected or type(self.config.get("schema_version")) is not int or self.config["schema_version"] != 1:
            raise ProjectConfigError("Unsupported configuration schema or unexpected top-level fields")
        project = self.config["project"]
        if not isinstance(project, dict) or set(project) != {"name"} or not _nonempty(project.get("name")):
            raise ProjectConfigError("project.name is required")
        general = self.config["general"]
        required_general = {"adapter", "source_language", "target_language", "batch_size", "rule_version", "prompt_template"}
        if not isinstance(general, dict) or set(general) != required_general:
            raise ProjectConfigError("general must define adapter, languages, batch_size, rule_version, and prompt_template")
        for key in ("adapter", "source_language", "target_language", "rule_version", "prompt_template"):
            if not _nonempty(general.get(key)):
                raise ProjectConfigError(f"general.{key} must be a non-empty string")
        if general["adapter"] != "bgi":
            raise ProjectConfigError(f"Unknown text adapter {general['adapter']!r}; available adapters: bgi")
        if type(general["batch_size"]) is not int or general["batch_size"] < 1:
            raise ProjectConfigError("general.batch_size must be a positive integer")
        game_specific = self.config["game_specific"]
        if not isinstance(game_specific, dict) or set(game_specific) != {"adapter_options"} or not isinstance(game_specific["adapter_options"], dict):
            raise ProjectConfigError("game_specific must contain an adapter_options object")
        inputs = self.config["inputs"]
        if not isinstance(inputs, dict) or set(inputs) != {"scripts"} or not isinstance(inputs["scripts"], list):
            raise ProjectConfigError("inputs.scripts must be a list")
        script_ids: set[str] = set()
        for row in inputs["scripts"]:
            if not isinstance(row, dict) or not set(row).issubset({"id", "source", "reference", "output", "baseline_mg"}) or not {"id", "source", "output"}.issubset(row):
                raise ProjectConfigError("Each script needs id, source, output, and optional reference")
            if not _nonempty(row["id"]) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", row["id"]) or row["id"] in {".", ".."} or row["id"].casefold() in script_ids:
                raise ProjectConfigError("Script IDs must be safe path tokens and unique")
            script_ids.add(row["id"].casefold())
            for key in ("source", "output", "reference", "baseline_mg"):
                if key in row and row[key] is not None:
                    self.path(row[key])
        translation = self.config["translation"]
        if not isinstance(translation, dict) or set(translation) != {"provider", "mock_prefix"}:
            raise ProjectConfigError("translation must define provider and mock_prefix")
        if translation["provider"] != "mock":
            raise ProjectConfigError("This CLI release supports only provider='mock'; online providers are not wired in")
        if not isinstance(translation["mock_prefix"], str):
            raise ProjectConfigError("translation.mock_prefix must be a string")
        compiler = self.config["compiler"]
        if not isinstance(compiler, dict) or set(compiler) != {"kind", "id", "version", "executable", "arguments", "version_arguments", "cwd", "timeout_seconds"}:
            raise ProjectConfigError("compiler configuration has an invalid shape")
        if compiler["kind"] not in {"mock", "command"}:
            raise ProjectConfigError("compiler.kind must be 'mock' or 'command'")
        if not _nonempty(compiler["id"]) or not _nonempty(compiler["version"]):
            raise ProjectConfigError("compiler.id and compiler.version are required")
        if isinstance(compiler["timeout_seconds"], bool) or not isinstance(compiler["timeout_seconds"], (int, float)) or compiler["timeout_seconds"] <= 0:
            raise ProjectConfigError("compiler.timeout_seconds must be positive")
        if compiler["kind"] == "command":
            if not _nonempty(compiler["executable"]) or not _string_list(compiler["arguments"]) or not _string_list(compiler["version_arguments"]):
                raise ProjectConfigError("Command compiler requires an executable and explicit argv lists")
            if compiler["cwd"] is not None:
                self.path(compiler["cwd"])
        validation = self.config["validation"]
        validation_keys = {"compiled_format", "require_human_review", "require_resolved_structure", "original_roundtrip_required"}
        if not isinstance(validation, dict) or set(validation) != validation_keys:
            raise ProjectConfigError("validation has an invalid shape")
        if validation["compiled_format"] != "mgsc-v1":
            raise ProjectConfigError("Only the audited read-only validator 'mgsc-v1' is supported by this CLI")
        if validation["require_human_review"] is not True or validation["require_resolved_structure"] is not True:
            raise ProjectConfigError("Human review and resolved-structure gates cannot be disabled")
        if type(validation["original_roundtrip_required"]) is not bool:
            raise ProjectConfigError("validation.original_roundtrip_required must be boolean")
        paths = self.config["paths"]
        if not isinstance(paths, dict) or set(paths) != {"glossary", "rules", "state_dir", "build_dir", "package_dir"}:
            raise ProjectConfigError("paths has an invalid shape")
        for value in paths.values():
            self.path(value)
        package = self.config["package"]
        if not isinstance(package, dict) or set(package) != {"name", "version", "files"} or not _nonempty(package["name"]) or not _nonempty(package["version"]) or not isinstance(package["files"], list):
            raise ProjectConfigError("package requires name, version, and explicit files list")
        package_targets: set[str] = set()
        for row in package["files"]:
            if not isinstance(row, dict) or set(row) != {"build_path", "archive_path"}:
                raise ProjectConfigError("Each package file needs build_path and archive_path")
            self.path(row["build_path"])
            archive_path = row["archive_path"]
            parts = PurePosixPath(archive_path).parts if isinstance(archive_path, str) else ()
            if (not isinstance(archive_path, str) or not archive_path or archive_path.startswith("/")
                    or "\\" in archive_path or not parts or any(part in {"", ".", ".."} for part in parts)):
                raise ProjectConfigError("Package archive_path must be a POSIX relative path")
            normalized_archive = "/".join(parts).casefold()
            if normalized_archive in package_targets:
                raise ProjectConfigError("Package archive paths must be unique")
            package_targets.add(normalized_archive)
        deployment = self.config["deployment"]
        if not isinstance(deployment, dict) or set(deployment) != {"enabled", "target_root", "transaction_root", "expected_version", "fixed_snapshot", "targets"}:
            raise ProjectConfigError("deployment has an invalid shape")
        if type(deployment["enabled"]) is not bool or not isinstance(deployment["targets"], list):
            raise ProjectConfigError("deployment.enabled must be boolean and targets a list")
        if deployment["enabled"]:
            for key in ("target_root", "transaction_root", "expected_version", "fixed_snapshot"):
                if not _nonempty(deployment.get(key)):
                    raise ProjectConfigError(f"Enabled deployment requires deployment.{key}")
            self.path(deployment["fixed_snapshot"])
            if not Path(deployment["target_root"]).is_absolute():
                raise ProjectConfigError("deployment.target_root must be an explicitly configured absolute path")
            if not Path(deployment["transaction_root"]).is_absolute():
                raise ProjectConfigError("deployment.transaction_root must be an explicitly configured absolute path")
            target_paths: set[str] = set()
            for row in deployment["targets"]:
                if not isinstance(row, dict) or set(row) != {"build_path", "target_path", "before_sha256", "candidate_sha256"}:
                    raise ProjectConfigError("Each deployment target requires build_path, target_path, before_sha256, and candidate_sha256")
                if not all(_nonempty(row[key]) for key in ("build_path", "target_path", "before_sha256", "candidate_sha256")):
                    raise ProjectConfigError("Deployment target paths and hashes must be non-empty")
                self.path(row["build_path"])
                target_key = row["target_path"].replace("/", "\\").casefold()
                if target_key in target_paths:
                    raise ProjectConfigError("Deployment target paths must be unique")
                target_paths.add(target_key)
                if any(len(row[key]) != 64 or any(char not in "0123456789abcdefABCDEF" for char in row[key]) for key in ("before_sha256", "candidate_sha256")):
                    raise ProjectConfigError("Deployment target hashes must be SHA-256 hex strings")

    def path(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ProjectConfigError("Project-owned paths must be non-empty relative paths")
        try:
            return normalize_target(self.root, relative).full_path
        except PathSafetyError as exc:
            raise ProjectConfigError(f"Unsafe project path {relative!r}: {exc}") from exc

    def fingerprint(self) -> str:
        values: dict[str, str] = {"localize.json": hashlib.sha256(self.config_bytes).hexdigest().upper()}
        for label in (self.config["paths"]["glossary"], self.config["paths"]["rules"]):
            path = self.path(label)
            values[label] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        for row in self.config["inputs"]["scripts"]:
            for key in ("source", "reference", "baseline_mg"):
                if row.get(key):
                    path = self.path(row[key])
                    values[row[key]] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest().upper()

    def script_path(self, row: dict[str, Any], key: str) -> Path:
        return self.path(row[key])


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and "\0" not in item for item in value)
