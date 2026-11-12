"""Dependency-free helpers and format adapters for localization workflows."""

from .models import (
    AdapterError,
    ControlLiteral,
    ExtractionResult,
    ParserIssue,
    TextCategory,
    TextSlot,
    UnsupportedStructureError,
)
from .hashing import hash_file
from .manifest import ManifestError, build_manifest, load_manifest, validate_manifest, write_manifest
from .path_safety import PathSafetyError, compare_snapshots, normalize_target, snapshot_tree

__all__ = [
    "AdapterError", "ControlLiteral", "ExtractionResult", "ManifestError", "ParserIssue",
    "PathSafetyError", "TextCategory", "TextSlot", "UnsupportedStructureError",
    "build_manifest", "compare_snapshots", "hash_file", "load_manifest", "normalize_target",
    "snapshot_tree", "validate_manifest", "write_manifest",
]
