"""Small, versioned glossary model. This is separate from compiler indexes."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class GlossaryError(ValueError):
    pass


@dataclass(frozen=True)
class GlossaryEntry:
    source: str
    target: str
    required: bool = True
    note: str = ""


@dataclass(frozen=True)
class Glossary:
    version: str
    entries: tuple[GlossaryEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise GlossaryError("Glossary version must be a non-empty string")
        seen: dict[str, GlossaryEntry] = {}
        for entry in self.entries:
            if (not isinstance(entry, GlossaryEntry) or not isinstance(entry.source, str)
                    or not isinstance(entry.target, str) or not isinstance(entry.note, str)
                    or type(entry.required) is not bool or not entry.source.strip() or not entry.target.strip()):
                raise GlossaryError("Glossary entries require non-empty source and target strings")
            key = entry.source.casefold()
            previous = seen.get(key)
            if previous and previous.target != entry.target:
                raise GlossaryError(f"Conflicting glossary targets for source term: {entry.source}")
            if previous and previous != entry:
                raise GlossaryError(f"Duplicate glossary source term: {entry.source}")
            seen[key] = entry

    @classmethod
    def from_mapping(cls, value: Any) -> "Glossary":
        if not isinstance(value, dict) or set(value) != {"version", "entries"}:
            raise GlossaryError("Glossary must contain exactly version and entries")
        rows = value["entries"]
        if not isinstance(rows, list):
            raise GlossaryError("Glossary entries must be a list")
        entries: list[GlossaryEntry] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not set(row).issubset({"source", "target", "required", "note"}):
                raise GlossaryError(f"Invalid glossary entry {index}")
            if not {"source", "target"}.issubset(row):
                raise GlossaryError(f"Glossary entry {index} needs source and target")
            if not isinstance(row["source"], str) or not isinstance(row["target"], str):
                raise GlossaryError(f"Glossary entry {index} source/target must be strings")
            required = row.get("required", True)
            note = row.get("note", "")
            if type(required) is not bool or not isinstance(note, str):
                raise GlossaryError(f"Invalid glossary entry options at {index}")
            entries.append(GlossaryEntry(row["source"], row["target"], required, note))
        return cls(value["version"], tuple(entries))

    @classmethod
    def from_records(
        cls, version: str, records: Iterable[dict[str, Any]], *,
        source_field: str, target_field: str, required_field: str | None = None,
        note_field: str | None = None,
    ) -> "Glossary":
        """Adapt an external glossary by explicit field names; no format is guessed."""
        entries: list[GlossaryEntry] = []
        for index, row in enumerate(records):
            if not isinstance(row, dict) or source_field not in row or target_field not in row:
                raise GlossaryError(f"Glossary record {index} lacks configured source/target fields")
            required = row.get(required_field, True) if required_field else True
            note = row.get(note_field, "") if note_field else ""
            entries.append(GlossaryEntry(row[source_field], row[target_field], required, note))
        return cls(version, tuple(entries))

    @classmethod
    def load(cls, path: str | Path) -> "Glossary":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise GlossaryError(f"Cannot read glossary: {exc}") from exc
        return cls.from_mapping(value)

    def canonical_bytes(self) -> bytes:
        value = {"version": self.version, "entries": [
            {"source": entry.source, "target": entry.target,
             "required": entry.required, "note": entry.note}
            for entry in sorted(self.entries, key=lambda item: (item.source.casefold(), item.source, item.target))
        ]}
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest().upper()


def _unique_pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GlossaryError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result
