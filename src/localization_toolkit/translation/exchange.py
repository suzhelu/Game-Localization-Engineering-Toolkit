"""Offline JSONL exchange for reviewed translation tasks."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

from .glossary import Glossary
from .models import TranslationIssue, TranslationRecord, TranslationStatus, TranslationTask
from .quality import validate_target
from .store import TranslationStore, TranslationStoreError
from .workflow import _now

SCHEMA_VERSION = 1
EXCHANGE_ERROR = "TRANSLATION_EXCHANGE_INVALID"


class ExchangeError(ValueError):
    code = EXCHANGE_ERROR


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ExchangeError(f"Duplicate JSON field: {key}")
        value[key] = item
    return value


def export_jsonl(tasks: Sequence[TranslationTask], path: str | Path, *, batch_size: int = 0,
                 offset: int = 0) -> int:
    """Create a UTF-8 JSONL task file without overwriting an existing file."""
    if (isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 0
            or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0):
        raise ExchangeError("batch_size and offset must be non-negative integers")
    ordered = sorted(tasks, key=lambda task: task.slot_id)
    ordered = ordered[offset:]
    if batch_size:
        ordered = ordered[:batch_size]
    rows = []
    for task in ordered:
        rows.append({"schema_version": SCHEMA_VERSION, "task_id": task.task_id,
                     "slot_id": task.slot_id, "source_text": task.source_text,
                     "source_language": task.source_language, "target_language": task.target_language,
                     "source_fingerprint": task.source_sha256,
                     "task_fingerprint": task.task_id, "context": task.context_json,
                     "reference_text": task.reference_text, "role": task.role,
                     "category": task.category})
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                       for row in rows).encode("utf-8")
    created = False
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise ExchangeError(f"Export target already exists: {target}") from exc
    except OSError as exc:
        if created:
            try: target.unlink()
            except OSError: pass
        raise ExchangeError(f"Could not write complete export: {exc}") from exc
    return len(rows)


def import_jsonl(path: str | Path, tasks: Sequence[TranslationTask], *,
                 glossary: Glossary, store: TranslationStore, batch_id: str = "agent-import") -> dict[str, int]:
    """Validate the whole file before one locked, optimistic store commit."""
    expected = {task.task_id: task for task in tasks}
    if len(expected) != len(tasks) or len({task.slot_id for task in tasks}) != len(tasks):
        raise ExchangeError("Current task set contains duplicate task or slot IDs")
    snapshot = store.snapshot_fingerprint()
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ExchangeError(f"Cannot read UTF-8 JSONL: {exc}") from exc
    seen: set[str] = set()
    seen_slots: set[str] = set()
    records: list[TranslationRecord] = []
    if not lines or any(not line.strip() for line in lines):
        raise ExchangeError("Import JSONL must contain non-empty records without blank lines")
    for line_number, line in enumerate(lines, 1):
        try:
            row = json.loads(line, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, ExchangeError) as exc:
            if isinstance(exc, ExchangeError):
                raise
            raise ExchangeError(f"Invalid JSON on line {line_number}") from exc
        fields = {"schema_version", "task_id", "slot_id", "source_fingerprint", "task_fingerprint", "translated_text"}
        if (not isinstance(row, dict) or set(row) != fields
                or type(row.get("schema_version")) is not int or row["schema_version"] != SCHEMA_VERSION):
            raise ExchangeError(f"Unsupported schema or fields on line {line_number}")
        task_id = row["task_id"]
        if not isinstance(task_id, str) or task_id not in expected:
            raise ExchangeError(f"Unknown task ID on line {line_number}")
        if task_id in seen:
            raise ExchangeError(f"Duplicate task ID on line {line_number}")
        seen.add(task_id)
        task = expected[task_id]
        if task.slot_id in seen_slots:
            raise ExchangeError(f"Duplicate slot ID on line {line_number}")
        seen_slots.add(task.slot_id)
        if (row["slot_id"] != task.slot_id or row["source_fingerprint"] != task.source_sha256
                or row["task_fingerprint"] != task.task_id):
            raise ExchangeError(f"Stale task/source fingerprint on line {line_number}")
        target = row["translated_text"]
        issues = validate_target(task, target, glossary)
        if issues:
            raise ExchangeError(f"Translation failed structural QA on line {line_number}: {issues[0].code}")
        records.append(TranslationRecord(
            batch_id=batch_id, task_id=task.task_id, slot_id=task.slot_id,
            source_sha256=task.source_sha256, location_fingerprint=task.location_fingerprint,
            context_sha256=task.context_sha256, rules_fingerprint=task.rules_fingerprint,
            prompt_fingerprint=task.prompt_fingerprint, rule_version=task.rule_version,
            target_text=target, status=TranslationStatus.PENDING_REVIEW, issues=(),
            provider="agent-jsonl", processed_at=_now()))
    # State is re-read under the store lock. Identical pending records are idempotent;
    # changed or already-reviewed records are conflicts, never overwritten.
    current = {record.task_id: record for record in store.records()}
    for record in records:
        old = current.get(record.task_id)
        if old and (old.status != TranslationStatus.PENDING_REVIEW or old.target_text != record.target_text
                    or old.source_sha256 != record.source_sha256
                    or old.location_fingerprint != record.location_fingerprint
                    or old.context_sha256 != record.context_sha256
                    or old.rules_fingerprint != record.rules_fingerprint
                    or old.prompt_fingerprint != record.prompt_fingerprint):
            raise ExchangeError(f"Conflicting repeated import for task {record.task_id}")
    try:
        store.save_many(records, expected_fingerprint=snapshot)
    except TranslationStoreError as exc:
        raise ExchangeError(f"Atomic import rejected: {exc}") from exc
    return {"schema_version": SCHEMA_VERSION, "imported": len(records), "pending_review": len(records)}
