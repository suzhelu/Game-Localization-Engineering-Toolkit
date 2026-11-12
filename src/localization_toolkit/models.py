"""Game-independent text-slot and parser result models."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence


class TextCategory(str, Enum):
    DIALOGUE = "dialogue"
    CHARACTER_NAME = "character_name"
    MENU = "menu"
    UI = "ui"
    OTHER_VISIBLE = "other_visible"


@dataclass(frozen=True)
class TextSlot:
    """A translatable string with stable identity and original-source location."""

    slot_id: str
    script: str
    category: TextCategory
    role: str
    original_text: str
    current_text: str
    reference_text: str | None
    char_start: int
    char_end: int
    byte_offset: int
    line: int
    column: int
    command: str
    call_index: int
    argument_index: int
    item_index: int | None = None
    speaker_hint: str | None = None
    context: Mapping[str, Sequence[Mapping[str, str]]] = field(default_factory=dict)


@dataclass(frozen=True)
class ParserIssue:
    """A structure that was preserved but could not be classified safely."""

    script: str
    line: int
    column: int
    command: str
    reason: str
    argument_indices: tuple[int, ...] = ()
    fatal_for_writeback: bool = True


@dataclass(frozen=True)
class ControlLiteral:
    """A literal retained as control data rather than exposed text."""

    script: str
    line: int
    column: int
    command: str
    argument_index: int
    raw_literal: str


@dataclass(frozen=True)
class ExtractionResult:
    """Game-independent result envelope; adapters own the opaque payload."""

    script: str
    source: str
    source_sha256: str
    slots: tuple[TextSlot, ...]
    issues: tuple[ParserIssue, ...]
    controls: tuple[ControlLiteral, ...]
    structure_signature: tuple[Any, ...]
    adapter_payload: Any = field(repr=False, compare=False)

    @property
    def unresolved_count(self) -> int:
        return sum(issue.fatal_for_writeback for issue in self.issues)


class TextAdapter(Protocol):
    """Protocol for format-specific extractors and safe writers."""

    def extract(self, source: str, script: str, reference_source: str | None = None) -> ExtractionResult: ...

    def write(self, parsed: ExtractionResult, translations: Mapping[str, str | Mapping[str, str]]) -> str: ...


class AdapterError(ValueError):
    """Raised when an adapter cannot prove a safe parse or writeback."""


class UnsupportedStructureError(AdapterError):
    """Raised when a structure is not covered by an explicit adapter rule."""
