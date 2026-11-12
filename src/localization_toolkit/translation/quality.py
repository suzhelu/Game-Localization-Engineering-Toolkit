"""Deterministic structural checks; these do not judge translation meaning."""
from __future__ import annotations

import re
from collections import Counter

from .glossary import Glossary
from .models import TranslationIssue, TranslationTask


_TAGS = re.compile(r"<[^>]*>")
_PLACEHOLDERS = re.compile(r"\{(?:[A-Za-z_][A-Za-z0-9_]*|\d+)(?::[^{}]*)?\}|%(?:\d+\$)?[sdif]|%%")
_CONTROLS = re.compile(r"[\r\n\t\b\f\v\0]|\\(?:[A-Za-z0-9]|\\|['\"])")


def _combined_counter(primary: str, reference: str | None, pattern: re.Pattern[str]) -> Counter[str]:
    a = Counter(pattern.findall(primary))
    b = Counter(pattern.findall(reference or ""))
    return a | b


def _is_subsequence(expected: list[str], actual: list[str]) -> bool:
    cursor = iter(actual)
    return all(any(candidate == item for candidate in cursor) for item in expected)


def validate_target(task: TranslationTask, target_text: object, glossary: Glossary) -> tuple[TranslationIssue, ...]:
    issues: list[TranslationIssue] = []
    if not isinstance(target_text, str) or not target_text.strip():
        return (TranslationIssue("empty_target", "Target text must be a non-empty string"),)
    expected_tags = _combined_counter(task.source_text, task.reference_text, _TAGS)
    actual_tags = Counter(_TAGS.findall(target_text))
    source_tags = _TAGS.findall(task.source_text)
    reference_tags = _TAGS.findall(task.reference_text or "")
    target_tags = _TAGS.findall(target_text)
    if (expected_tags != actual_tags or not _is_subsequence(source_tags, target_tags)
            or not _is_subsequence(reference_tags, target_tags)):
        issues.append(TranslationIssue("format_tags", "Formatting tags do not match source/reference markers"))
    expected_placeholders = _combined_counter(task.source_text, task.reference_text, _PLACEHOLDERS)
    actual_placeholders = Counter(_PLACEHOLDERS.findall(target_text))
    if expected_placeholders != actual_placeholders:
        issues.append(TranslationIssue("placeholders", "Placeholder multiset does not match source/reference"))
    expected_escapes = _combined_counter(task.source_text, task.reference_text, _CONTROLS)
    actual_escapes = Counter(_CONTROLS.findall(target_text))
    source_escapes = _CONTROLS.findall(task.source_text)
    reference_escapes = _CONTROLS.findall(task.reference_text or "")
    target_escapes = _CONTROLS.findall(target_text)
    if (expected_escapes != actual_escapes or not _is_subsequence(source_escapes, target_escapes)
            or not _is_subsequence(reference_escapes, target_escapes)):
        issues.append(TranslationIssue("control_escapes", "Escaped control markers do not match source/reference"))
    for entry in glossary.entries:
        if entry.required and entry.source.casefold() in task.source_text.casefold() and entry.target not in target_text:
            issues.append(TranslationIssue("required_term", f"Required glossary target is missing for source term {entry.source!r}"))
    return tuple(issues)
