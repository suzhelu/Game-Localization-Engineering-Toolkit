"""Tiny line-based synthetic adapter for the toolkit adapter contract."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from localization_toolkit.adapters.registry import FormatProbe, ProbeStatus
from localization_toolkit.models import AdapterError, ExtractionResult, TextCategory, TextSlot, UnsupportedStructureError

HEADER = "SYNTHETIC-TEXT-V1"
_ROW = re.compile(r"TEXT\t([A-Za-z0-9_-]+)\t(.*?)(?:\r?\n)?\Z")


class SyntheticTextAdapter:
    adapter_id = "synthetic-text-v1"
    version = "1"
    formats = ("synthetic line-based fixture only",)
    capabilities = frozenset({"extract", "writeback", "format_probe"})

    def probe(self, source: str) -> FormatProbe:
        if not isinstance(source, str):
            return FormatProbe(ProbeStatus.NEEDS_CONFIRMATION, ("Input was not decoded text",))
        lines = source.splitlines()
        if lines and lines[0] == HEADER:
            return FormatProbe(ProbeStatus.SUPPORTED, ("Exact synthetic fixture header matched",))
        if "SYNTHETIC-TEXT" in source:
            return FormatProbe(ProbeStatus.NEEDS_CONFIRMATION, ("Synthetic marker present but header did not match",))
        return FormatProbe(ProbeStatus.UNSUPPORTED, ("Synthetic fixture marker not present",))

    def extract(self, source: str, script: str, reference_source: str | None = None) -> ExtractionResult:
        if self.probe(source).status != ProbeStatus.SUPPORTED:
            raise UnsupportedStructureError("Unsupported synthetic fixture header")
        lines = source.splitlines(keepends=True)
        slots: list[TextSlot] = []
        spans: dict[str, tuple[int, int]] = {}
        keys: set[str] = set()
        cursor = len(lines[0])
        for line_number, line in enumerate(lines[1:], start=2):
            if not line.rstrip("\r\n"):
                cursor += len(line)
                continue
            match = _ROW.fullmatch(line)
            if not match:
                raise UnsupportedStructureError(f"Unrecognized synthetic row at line {line_number}")
            key, encoded = match.groups()
            if key in keys:
                raise UnsupportedStructureError(f"Duplicate synthetic slot key at line {line_number}")
            keys.add(key)
            try:
                text = json.loads(encoded)
            except json.JSONDecodeError as exc:
                raise UnsupportedStructureError(f"Invalid JSON text at line {line_number}") from exc
            if not isinstance(text, str):
                raise UnsupportedStructureError(f"Synthetic text must be a JSON string at line {line_number}")
            start = cursor + len("TEXT\t" + key + "\t")
            end = start + len(encoded)
            slot_id = hashlib.sha256(f"{script}\0{key}".encode("utf-8")).hexdigest()[:24]
            slots.append(TextSlot(slot_id, script, TextCategory.OTHER_VISIBLE, "synthetic-text",
                                  text, text, None, start, end,
                                  len(source[:start].encode("utf-8")), line_number,
                                  len("TEXT\t" + key + "\t") + 1, "TEXT", len(slots), 2))
            spans[slot_id] = (start, end)
            cursor += len(line)
        signature = tuple((slot.slot_id, slot.line, slot.command) for slot in slots)
        return ExtractionResult(script, source, hashlib.sha256(source.encode("utf-8")).hexdigest().upper(),
                                tuple(slots), (), (), signature, {"spans": spans})

    def write(self, parsed: ExtractionResult, translations: Mapping[str, Any]) -> str:
        if parsed.unresolved_count:
            raise AdapterError("Unresolved synthetic structure blocks writeback")
        if set(translations) != {slot.slot_id for slot in parsed.slots}:
            raise AdapterError("Translation keys must exactly match synthetic slots")
        output = parsed.source
        for slot in reversed(parsed.slots):
            target = translations[slot.slot_id]
            if not isinstance(target, str) or not target.strip():
                raise AdapterError(f"Invalid translated text for {slot.slot_id}")
            start, end = parsed.adapter_payload["spans"][slot.slot_id]
            replacement = parsed.source[start:end] if target == slot.current_text else json.dumps(target, ensure_ascii=False)
            output = output[:start] + replacement + output[end:]
        checked = self.extract(output, parsed.script)
        if checked.structure_signature != parsed.structure_signature:
            raise AdapterError("Synthetic structure changed during writeback")
        return output


def register(registry) -> None:
    """Example entry-point hook; call only after reviewing this synthetic code."""
    from localization_toolkit.adapters.registry import AdapterDescriptor
    registry.register(AdapterDescriptor(SyntheticTextAdapter.adapter_id, SyntheticTextAdapter.version,
                                        SyntheticTextAdapter.formats, SyntheticTextAdapter.capabilities,
                                        "Synthetic test format only"),
                      lambda options: SyntheticTextAdapter())
