"""Atomic local JSON persistence for resumable translation records."""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from .models import TranslationIssue, TranslationRecord, TranslationStatus, TranslationTask


class TranslationStoreError(RuntimeError):
    pass


def _unique_pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise TranslationStoreError(f"Duplicate JSON key: {key}")
        output[key] = value
    return output


class TranslationStore:
    SCHEMA_VERSION = 1

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.lock_timeout_seconds = 5.0

    @contextmanager
    def _exclusive(self):
        """OS-managed exclusive lock; the OS releases it if a writer exits."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        stream = open(lock_path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                # Never read the byte-range-locked file: Windows may deny that
                # read while another process holds the lock. Appending a byte
                # only when metadata says it is empty makes offset zero lockable.
                if lock_path.stat().st_size == 0:
                    stream.write(b"0"); stream.flush()
                deadline = time.monotonic() + self.lock_timeout_seconds
                while True:
                    try:
                        stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1); break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TranslationStoreError("Translation store is locked by another writer")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                deadline = time.monotonic() + self.lock_timeout_seconds
                while True:
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB); break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TranslationStoreError("Translation store is locked by another writer")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def snapshot_fingerprint(self) -> str:
        import hashlib
        return hashlib.sha256(self.path.read_bytes() if self.path.exists() else b"{}").hexdigest().upper()

    def _read(self) -> dict[str, TranslationRecord]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
            if not isinstance(raw, dict) or raw.get("schema_version") != self.SCHEMA_VERSION or not isinstance(raw.get("records"), list):
                raise TranslationStoreError("Invalid translation store structure")
            records: dict[str, TranslationRecord] = {}
            for row in raw["records"]:
                record = _record_from_mapping(row)
                if record.task_id in records:
                    raise TranslationStoreError(f"Duplicate stored task ID: {record.task_id}")
                records[record.task_id] = record
            return records
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            if isinstance(exc, TranslationStoreError):
                raise
            raise TranslationStoreError(f"Cannot load translation store: {exc}") from exc

    def records(self) -> tuple[TranslationRecord, ...]:
        return tuple(self._read().values())

    def get(self, task_id: str) -> TranslationRecord | None:
        return self._read().get(task_id)

    def upsert(self, record: TranslationRecord, *, expected_current: TranslationRecord | None = None) -> None:
        with self._exclusive():
            records = self._read()
            current = records.get(record.task_id)
            if current != expected_current:
                raise TranslationStoreError("Translation record changed concurrently; reload before updating")
            records[record.task_id] = record
            self._write(records.values())

    def save_many(self, records_to_save: Iterable[TranslationRecord], *, expected_fingerprint: str | None = None,
                  allow_replace: bool = False) -> None:
        if allow_replace and expected_fingerprint is None:
            raise TranslationStoreError("Replacing existing records requires an expected store fingerprint")
        rows = tuple(records_to_save)
        with self._exclusive():
            if expected_fingerprint is not None and self.snapshot_fingerprint() != expected_fingerprint:
                raise TranslationStoreError("Translation store changed during validation; retry from a fresh snapshot")
            records = self._read()
            if not allow_replace:
                for row in rows:
                    current = records.get(row.task_id)
                    if current is not None:
                        same_result = (
                            current.status == row.status and current.target_text == row.target_text
                            and current.source_sha256 == row.source_sha256
                            and current.location_fingerprint == row.location_fingerprint
                            and current.context_sha256 == row.context_sha256
                            and current.rules_fingerprint == row.rules_fingerprint
                            and current.prompt_fingerprint == row.prompt_fingerprint
                        )
                        if not same_result:
                            raise TranslationStoreError("Conflicting translation record; refusing to overwrite existing state")
            for record in rows:
                current = records.get(record.task_id)
                if current is None or allow_replace:
                    records[record.task_id] = record
            self._write(records.values())

    def _write(self, records: Iterable[TranslationRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = [asdict(record) for record in sorted(records, key=lambda item: item.task_id)]
        for row in rows:
            row["status"] = row["status"].value if isinstance(row["status"], TranslationStatus) else row["status"]
        data = (json.dumps({"schema_version": self.SCHEMA_VERSION, "records": rows}, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def stale_records(self, location_fingerprint: str, current_task_id: str) -> tuple[TranslationRecord, ...]:
        return tuple(record for record in self._read().values()
                     if record.location_fingerprint == location_fingerprint and record.task_id != current_task_id
                     and record.status != TranslationStatus.STALE)


def _record_from_mapping(row: Any) -> TranslationRecord:
    if not isinstance(row, dict):
        raise TranslationStoreError("Stored record must be an object")
    expected_fields = {
        "batch_id", "task_id", "slot_id", "source_sha256", "location_fingerprint",
        "context_sha256", "rules_fingerprint", "prompt_fingerprint", "rule_version",
        "target_text", "status", "issues", "provider", "processed_at", "reviewer", "review_note",
    }
    if set(row) != expected_fields:
        raise TranslationStoreError("Stored record has missing or unexpected fields")
    try:
        issues = tuple(TranslationIssue(**item) for item in row["issues"])
        return TranslationRecord(
            batch_id=row["batch_id"], task_id=row["task_id"], slot_id=row["slot_id"],
            source_sha256=row["source_sha256"], context_sha256=row["context_sha256"],
            location_fingerprint=row["location_fingerprint"],
            rules_fingerprint=row["rules_fingerprint"], prompt_fingerprint=row["prompt_fingerprint"],
            rule_version=row["rule_version"], target_text=row["target_text"],
            status=TranslationStatus(row["status"]), issues=issues, provider=row["provider"],
            processed_at=row["processed_at"], reviewer=row.get("reviewer"), review_note=row.get("review_note"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TranslationStoreError(f"Malformed stored translation record: {exc}") from exc
