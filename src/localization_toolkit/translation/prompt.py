"""Provider-independent, caller-configurable prompt templates."""
from __future__ import annotations

import hashlib
import json
import string
from dataclasses import dataclass
from typing import Mapping

from .glossary import Glossary
from .models import TranslationTask


_ALLOWED_FIELDS = {
    "slot_id", "source_language", "target_language", "source_text", "reference_text",
    "role", "category", "speaker", "context", "glossary_constraints", "special_rules",
}


@dataclass(frozen=True)
class PromptTemplate:
    template: str = (
        "Translate the primary source text from {source_language} into {target_language}.\n"
        "The reference translation is secondary and may be wrong. Preserve ambiguity, voice, "
        "placeholders, tags, escapes, and control markers. Do not add unstated facts.\n"
        "Role: {role}; category: {category}; speaker/context: {speaker} {context}\n"
        "Glossary constraints: {glossary_constraints}\nProject rules: {special_rules}\n"
        "ID: {slot_id}\nPrimary source:\n{source_text}\nReference:\n{reference_text}\n"
        "Return only the translation for this slot."
    )

    def __post_init__(self) -> None:
        if not isinstance(self.template, str) or not self.template:
            raise ValueError("Prompt template must be non-empty")
        try:
            parsed = list(string.Formatter().parse(self.template))
        except ValueError as exc:
            raise ValueError(f"Malformed prompt template: {exc}") from exc
        if any(conversion is not None or format_spec for _, _, format_spec, conversion in parsed):
            raise ValueError("Prompt fields do not support conversions or format specifications")
        fields = {name for _, name, _, _ in parsed if name}
        unknown = fields - _ALLOWED_FIELDS
        if unknown:
            raise ValueError(f"Unsupported prompt template fields: {sorted(unknown)}")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.template.encode("utf-8")).hexdigest().upper()

    def render(self, task: TranslationTask, glossary: Glossary, special_rules: str = "") -> str:
        context: Mapping[str, object] = json.loads(task.context_json)
        constraints = [
            {"source": entry.source, "target": entry.target, "required": entry.required, "note": entry.note}
            for entry in glossary.entries
            if entry.source.casefold() in task.source_text.casefold()
        ]
        values = {
            "slot_id": task.slot_id,
            "source_language": task.source_language,
            "target_language": task.target_language,
            "source_text": task.source_text,
            "reference_text": task.reference_text or "",
            "role": task.role,
            "category": task.category,
            "speaker": task.speaker_hint or "",
            "context": json.dumps(context, ensure_ascii=False, sort_keys=True),
            "glossary_constraints": json.dumps(constraints, ensure_ascii=False, sort_keys=True),
            "special_rules": special_rules,
        }
        return self.template.format_map(values)
