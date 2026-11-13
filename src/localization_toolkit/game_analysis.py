"""Small dependency-free validator for structured game-analysis evidence reports."""
from __future__ import annotations

from typing import Any, Mapping

SCHEMA_VERSION = 1
EVIDENCE_LEVELS = {"confirmed", "inferred", "unknown"}
STAGE_STATES = {"not_started", "in_progress", "passed", "failed", "blocked", "skipped", "runtime_unverified"}
STAGES = tuple(f"stage_{index}" for index in range(11))


class GameAnalysisReportError(ValueError):
    pass


def _require_text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise GameAnalysisReportError(f"{label} must be a non-empty string")


def _valid_level(value: object) -> bool:
    return isinstance(value, str) and value in EVIDENCE_LEVELS


def validate_game_analysis_report(report: Mapping[str, Any]) -> None:
    """Validate schema v1 and guard evidence/runtime status invariants.

    This checks shape and declared evidence only; it cannot prove that evidence
    is truthful or that a game is compatible.
    """
    required = {"schema_version", "game", "platform", "engine", "evidence", "resource_candidates",
                "encoding", "localization_support", "font_support", "compiler_requirements",
                "reusable_components", "risks", "unknowns", "next_actions", "stages", "runtime_evidence"}
    if not isinstance(report, Mapping) or set(report) != required:
        raise GameAnalysisReportError("Report fields do not match game-analysis schema v1")
    if type(report["schema_version"]) is not int or report["schema_version"] != SCHEMA_VERSION:
        raise GameAnalysisReportError("Unsupported game-analysis schema_version")
    _require_text(report["game"], "game")
    _require_text(report["platform"], "platform")
    engine = report["engine"]
    if not isinstance(engine, Mapping) or set(engine) != {"name", "evidence_level"}:
        raise GameAnalysisReportError("engine requires name and evidence_level")
    _require_text(engine["name"], "engine.name")
    if not _valid_level(engine["evidence_level"]):
        raise GameAnalysisReportError("Invalid engine evidence_level")
    if engine["name"] in {"unknown", ""} and engine["evidence_level"] == "confirmed":
        raise GameAnalysisReportError("Unknown engine cannot be marked confirmed")
    if not isinstance(report["evidence"], list):
        raise GameAnalysisReportError("evidence must be a list")
    for item in report["evidence"]:
        if not isinstance(item, Mapping) or set(item) != {"claim", "level", "source"}:
            raise GameAnalysisReportError("Each evidence item requires claim, level, and source")
        _require_text(item["claim"], "evidence.claim"); _require_text(item["source"], "evidence.source")
        if not _valid_level(item["level"]):
            raise GameAnalysisReportError("Invalid evidence level")
    for key in ("resource_candidates", "reusable_components", "risks", "unknowns", "next_actions", "runtime_evidence"):
        if not isinstance(report[key], list):
            raise GameAnalysisReportError(f"{key} must be a list")
    for item in report["resource_candidates"]:
        if not isinstance(item, Mapping) or not _valid_level(item.get("evidence_level")):
            raise GameAnalysisReportError("Each resource candidate requires an evidence_level")
    for key in ("reusable_components", "risks", "unknowns", "next_actions"):
        if any(not isinstance(item, str) or not item.strip() for item in report[key]):
            raise GameAnalysisReportError(f"{key} entries must be non-empty strings")
    for key in ("encoding", "localization_support", "font_support", "compiler_requirements"):
        value = report[key]
        if not isinstance(value, Mapping) or not _valid_level(value.get("evidence_level")):
            raise GameAnalysisReportError(f"{key} requires a valid evidence_level")
    stages = report["stages"]
    if not isinstance(stages, Mapping) or set(stages) != set(STAGES):
        raise GameAnalysisReportError("stages must define stage_0 through stage_10")
    if any(not isinstance(value, str) or value not in STAGE_STATES for value in stages.values()):
        raise GameAnalysisReportError("Unknown stage state")
    runtime = report["runtime_evidence"]
    if not runtime and stages["stage_9"] != "runtime_unverified":
        raise GameAnalysisReportError("Without runtime evidence Stage 9 must remain runtime_unverified")
    if runtime and stages["stage_9"] == "runtime_unverified":
        raise GameAnalysisReportError("Stage 9 state contradicts supplied runtime evidence; review manually")
    if stages["stage_9"] in {"passed", "failed"}:
        if not runtime:
            raise GameAnalysisReportError("A completed runtime Stage 9 requires runtime_evidence")
        for item in runtime:
            if not isinstance(item, Mapping) or not {"type", "source", "observed_at"}.issubset(item):
                raise GameAnalysisReportError("Runtime evidence requires type, source, and observed_at")
            for field in ("type", "source", "observed_at"):
                _require_text(item[field], f"runtime_evidence.{field}")
