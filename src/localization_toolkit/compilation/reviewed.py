"""Create compiler inputs only through approved adapter writeback or a control capture."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..models import ExtractionResult, TextAdapter
from ..path_safety import PathSafetyError, normalize_target
from ..translation import Glossary, PromptTemplate, TranslationStore, build_tasks, write_approved
from .models import BuildMode, ReviewedSource


class ReviewedSourceError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _structure_fingerprint(extraction: ExtractionResult) -> str:
    try:
        encoded = json.dumps(extraction.structure_signature, ensure_ascii=False,
                             separators=(",", ":"), default=repr).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ReviewedSourceError("Adapter structure signature cannot be fingerprinted") from exc
    return _sha(encoded)


def _safe_new_file(root: Path, relative: str, text: str) -> tuple[Path, str, str]:
    try:
        normalized = normalize_target(root, relative)
    except PathSafetyError as exc:
        raise ReviewedSourceError(f"Unsafe staged source path: {exc}") from exc
    normalized.full_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_target(root, relative)
    payload = text.encode("utf-8", errors="strict")
    try:
        with normalized.full_path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise ReviewedSourceError(f"Refusing to overwrite staged input: {relative}") from exc
    return normalized.full_path, _sha(payload), normalized.relative_path


def prepare_reviewed_source(
    adapter: TextAdapter,
    extraction: ExtractionResult,
    *,
    input_root: str | Path,
    relative_path: str,
    glossary: Glossary,
    prompt_template: PromptTemplate,
    store: TranslationStore,
    source_language: str,
    target_language: str,
    rule_version: str,
    special_rules: str = "",
    reference_source: str | None = None,
) -> ReviewedSource:
    """Write an approved candidate through the existing adapter, then re-parse it.

    `write_approved` is the sole translation writeback path. The candidate is
    created exclusively under `input_root`, and only after review fingerprints
    match the supplied extraction and current glossary/rules.
    """
    if extraction.unresolved_count:
        raise ReviewedSourceError("Unresolved source structures block compilation")
    try:
        candidate = write_approved(
            adapter, extraction, glossary=glossary, prompt_template=prompt_template,
            store=store, source_language=source_language, target_language=target_language,
            rule_version=rule_version, special_rules=special_rules,
        )
        checked = adapter.extract(candidate, extraction.script, reference_source)
    except Exception as exc:
        raise ReviewedSourceError(f"Approved adapter writeback failed: {exc}") from exc
    expected_ids = tuple(slot.slot_id for slot in extraction.slots)
    actual_ids = tuple(slot.slot_id for slot in checked.slots)
    if checked.unresolved_count or checked.structure_signature != extraction.structure_signature:
        raise ReviewedSourceError("Translated source has unresolved structure or changed control signature")
    if actual_ids != expected_ids:
        raise ReviewedSourceError("Translated source changed text-slot count, order, or IDs")
    tasks = build_tasks(
        extraction.slots, glossary=glossary, prompt_template=prompt_template,
        source_language=source_language, target_language=target_language,
        rule_version=rule_version, special_rules=special_rules,
    )
    reviewers: set[str] = set()
    for task in tasks:
        record = store.get(task.task_id)
        if record is None or record.reviewer is None:
            raise ReviewedSourceError(f"Missing reviewer evidence for slot {task.slot_id}")
        reviewers.add(record.reviewer)
    root = Path(input_root).absolute()
    path, digest, normalized_relative = _safe_new_file(root, relative_path, candidate)
    return ReviewedSource(
        relative_path=normalized_relative, script_id=extraction.script,
        sha256=digest, purpose=BuildMode.TRANSLATED,
        structure_fingerprint=_structure_fingerprint(extraction),
        source_sha256=extraction.source_sha256, slot_ids=expected_ids,
        unresolved_count=checked.unresolved_count, reviewer_ids=tuple(sorted(reviewers)),
        rules_fingerprint=tasks[0].rules_fingerprint if tasks else None,
    )


def capture_original_source(
    adapter: TextAdapter,
    source_text: str,
    *,
    script_id: str,
    input_root: str | Path,
    relative_path: str,
) -> ReviewedSource:
    """Stage a legal local original for control compilation/roundtrip evidence."""
    try:
        extraction = adapter.extract(source_text, script_id)
    except Exception as exc:
        raise ReviewedSourceError(f"Original source could not be structurally extracted: {exc}") from exc
    if extraction.unresolved_count:
        raise ReviewedSourceError("Unresolved original structures block control compilation")
    root = Path(input_root).absolute()
    path, digest, normalized_relative = _safe_new_file(root, relative_path, source_text)
    if digest != extraction.source_sha256:
        path.unlink(missing_ok=True)
        raise ReviewedSourceError("Staged original hash does not match extraction")
    return ReviewedSource(
        relative_path=normalized_relative, script_id=script_id,
        sha256=digest, purpose=BuildMode.ORIGINAL_ROUNDTRIP,
        structure_fingerprint=_structure_fingerprint(extraction),
        source_sha256=extraction.source_sha256,
        slot_ids=tuple(slot.slot_id for slot in extraction.slots),
        unresolved_count=0, reviewer_ids=(), rules_fingerprint=None,
    )
