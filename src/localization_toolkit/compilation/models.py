"""Provider-neutral compiler and auditable build records."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, Sequence


class BuildMode(str, Enum):
    TRANSLATED = "translated"
    ORIGINAL_ROUNDTRIP = "original_roundtrip"


class EvidenceLevel(str, Enum):
    INPUT_STRUCTURE = "input_structure"
    HUMAN_APPROVAL = "human_approval"
    COMPILER_EXIT = "compiler_exit"
    OUTPUT_FORMAT = "output_format"
    OUTPUT_HASH = "output_hash"
    ORIGINAL_ROUNDTRIP = "original_roundtrip"
    TRANSLATED_STRUCTURE = "translated_structure"
    RUNTIME = "runtime"


@dataclass(frozen=True)
class CompilerVersion:
    compiler_id: str
    version: str


@dataclass(frozen=True)
class CompilerRequest:
    input_path: Path
    output_path: Path


@dataclass(frozen=True)
class CompilerExecution:
    command_label: str
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    duration_seconds: float


class CompilerAdapter(Protocol):
    """An installed compiler, configured by the caller; no compiler is bundled."""

    @property
    def compiler_id(self) -> str: ...

    def get_version(self, timeout_seconds: float) -> CompilerVersion: ...

    def compile(self, request: CompilerRequest, timeout_seconds: float) -> CompilerExecution: ...


class ArtifactValidator(Protocol):
    """Read-only format validator for a specific, explicitly supported format."""

    @property
    def validator_id(self) -> str: ...

    def validate(self, path: Path) -> dict[str, object]: ...


@dataclass(frozen=True)
class ReviewedSource:
    """A staged source paired with adapter structure and review evidence."""

    relative_path: str
    script_id: str
    sha256: str
    purpose: BuildMode
    structure_fingerprint: str
    source_sha256: str
    slot_ids: tuple[str, ...]
    unresolved_count: int
    reviewer_ids: tuple[str, ...]
    rules_fingerprint: str | None


@dataclass(frozen=True)
class BuildTarget:
    source: ReviewedSource
    output_relative_path: str
    mode: BuildMode
    expected_artifact_sha256: str | None = None


@dataclass(frozen=True)
class BuildPlan:
    input_root: Path
    output_directory: Path
    targets: Sequence[BuildTarget]
    expected_compiler_version: str
    timeout_seconds: float = 120.0
    expected_artifact_index: dict[str, str] | None = None
    report_path: Path | None = None


@dataclass(frozen=True)
class BuildItemResult:
    input_relative_path: str
    output_relative_path: str
    mode: str
    input_sha256: str
    output_sha256: str | None
    execution: CompilerExecution | None
    validation: dict[str, object] | None
    evidence: dict[str, str]
    error: str | None = None


@dataclass(frozen=True)
class BuildReport:
    build_id: str
    status: str
    compiler: CompilerVersion | None
    items: tuple[BuildItemResult, ...]
    output_directory: str | None
    temporary_directory: str | None
    error: str | None
