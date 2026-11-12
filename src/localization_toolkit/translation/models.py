"""Provider-neutral translation workflow data types."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from ..models import TextSlot


class TranslationStatus(str, Enum):
    PROVIDER_ERROR = "provider_error"
    NEEDS_REVIEW = "needs_review"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"


@dataclass(frozen=True)
class TranslationTask:
    task_id: str
    slot_id: str
    script: str
    role: str
    category: str
    source_text: str
    reference_text: str | None
    source_sha256: str
    location_fingerprint: str
    context_json: str
    context_sha256: str
    rules_fingerprint: str
    prompt_fingerprint: str
    source_language: str
    target_language: str
    rule_version: str
    speaker_hint: str | None


@dataclass(frozen=True)
class ProviderResult:
    slot_id: str
    target_text: str


@dataclass(frozen=True)
class TranslationRequest:
    batch_id: str
    tasks: Sequence[TranslationTask]
    prompts: Mapping[str, str]


@dataclass(frozen=True)
class TranslationIssue:
    code: str
    message: str
    blocking: bool = True


@dataclass(frozen=True)
class TranslationRecord:
    batch_id: str
    task_id: str
    slot_id: str
    source_sha256: str
    location_fingerprint: str
    context_sha256: str
    rules_fingerprint: str
    prompt_fingerprint: str
    rule_version: str
    target_text: str | None
    status: TranslationStatus
    issues: tuple[TranslationIssue, ...]
    provider: str
    processed_at: str
    reviewer: str | None = None
    review_note: str | None = None


def slot_source_text(slot: TextSlot) -> str:
    """Use the declared primary-language original; current/reference is auxiliary."""
    return slot.original_text
