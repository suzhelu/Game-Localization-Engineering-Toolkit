"""Conservative BGI/C-like text-slot adapter based on the audited project lexer.

Only calls with an explicit argument schema are treated as translatable text.
Unknown calls are preserved and reported; string-bearing unknown calls block
writeback until a caller supplies an explicit non-text or text schema.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from ..models import (
    AdapterError,
    ControlLiteral,
    ExtractionResult,
    ParserIssue,
    TextCategory,
    TextSlot,
    UnsupportedStructureError,
)
from .registry import FormatProbe, ProbeStatus


@dataclass(frozen=True)
class Token:
    kind: str
    raw: str
    start: int
    end: int
    line: int
    column: int


@dataclass(frozen=True)
class Call:
    name: str
    token_index: int
    start: int
    end: int
    args: tuple[tuple[Token, ...], ...]
    ordinal: int


@dataclass(frozen=True)
class TextPair:
    source_argument: int
    target_argument: int
    role: str


@dataclass(frozen=True)
class CallSchema:
    arity: int
    text_pairs: tuple[TextPair, ...]


@dataclass(frozen=True)
class OpaqueCallSchema:
    """Explicit declaration that literal arguments are non-visible data."""

    arity: int
    non_text_string_arguments: frozenset[int]


_DEFAULT_SCHEMAS: dict[str, CallSchema] = {
    "OutputLine": CallSchema(5, (TextPair(0, 2, "name"), TextPair(1, 3, "body"))),
    "OutputLineAll": CallSchema(3, (TextPair(0, 1, "body"),)),
    "SavePoint": CallSchema(2, (TextPair(0, 1, "save-label"),)),
    "ModAddArtset": CallSchema(3, (TextPair(1, 0, "ui-name"),)),
    "ModAddBGMset": CallSchema(3, (TextPair(1, 0, "ui-name"),)),
    "ModAddSEset": CallSchema(3, (TextPair(1, 0, "ui-name"),)),
    "ModAddAudioset": CallSchema(8, (TextPair(2, 0, "ui-name"), TextPair(3, 1, "ui-description"))),
}

_ESCAPES = {
    "\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t",
    "b": "\b", "f": "\f", "v": "\v", "0": "\0",
}
_REVERSE_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t",
                    "\b": "\\b", "\f": "\\f", "\v": "\\v", "\0": "\\0"}
_DELIMITER_PAIRS = {"(": ")", "[": "]", "{": "}"}
_ROLE_CATEGORY = {
    "name": TextCategory.CHARACTER_NAME,
    "body": TextCategory.DIALOGUE,
    "choice": TextCategory.MENU,
    "save-label": TextCategory.OTHER_VISIBLE,
    "ui-name": TextCategory.UI,
    "ui-description": TextCategory.UI,
}
_DYNAMIC_REVIEW_COMMANDS = {"SetNameFormat", "ChapterPreview", "DrawFragment"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="strict")).hexdigest().upper()


def lex(source: str) -> tuple[Token, ...]:
    """Tokenize supported BGI source while preserving exact source offsets.

    Offsets are Python character offsets. Newlines are counted consistently for
    LF and CRLF; string/comment contents are never rewritten by the lexer.
    """
    if not isinstance(source, str):
        raise TypeError("BGI source must be str")
    tokens: list[Token] = []
    stack: list[Token] = []
    index, line, column = 0, 1, 1

    def advance(end: int) -> None:
        nonlocal index, line, column
        while index < end:
            char = source[index]
            index += 1
            if char == "\n":
                line += 1
                column = 1
            else:
                column += 1

    while index < len(source):
        char = source[index]
        if char.isspace() or char == "\ufeff":
            advance(index + 1)
            continue
        if source.startswith("//", index):
            end = source.find("\n", index)
            advance(len(source) if end < 0 else end)
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise UnsupportedStructureError(f"Unclosed block comment at line {line}")
            advance(end + 2)
            continue

        start, start_line, start_column = index, line, column
        if char in ('"', "'"):
            quote = char
            token_kind = "string" if quote == '"' else "char"
            cursor = index + 1
            while cursor < len(source):
                if source[cursor] == "\\":
                    cursor += 2
                    continue
                if source[cursor] == quote:
                    break
                cursor += 1
            if cursor >= len(source):
                raise UnsupportedStructureError(f"Unclosed string literal at line {start_line}")
            advance(cursor + 1)
        elif char.isascii() and (char.isalpha() or char in "_$"):
            token_kind = "id"
            cursor = index + 1
            while cursor < len(source) and source[cursor].isascii() and (source[cursor].isalnum() or source[cursor] in "_$"):
                cursor += 1
            advance(cursor)
        elif char.isascii() and char.isdigit():
            token_kind = "number"
            cursor = index + 1
            while cursor < len(source) and source[cursor].isascii() and (source[cursor].isalnum() or source[cursor] in "_."):
                cursor += 1
            advance(cursor)
        else:
            token_kind = "punct"
            advance(index + 1)

        token = Token(token_kind, source[start:index], start, index, start_line, start_column)
        tokens.append(token)
        if token_kind == "punct":
            if token.raw in _DELIMITER_PAIRS:
                stack.append(token)
            elif token.raw in _DELIMITER_PAIRS.values():
                if not stack or _DELIMITER_PAIRS[stack[-1].raw] != token.raw:
                    raise UnsupportedStructureError(f"Unbalanced delimiter {token.raw!r} at line {start_line}")
                stack.pop()
    if stack:
        raise UnsupportedStructureError(f"Unclosed delimiter {stack[-1].raw!r} at line {stack[-1].line}")
    return tuple(tokens)


def decode_literal(raw: str) -> str:
    if len(raw) < 2 or not raw.startswith('"') or not raw.endswith('"'):
        raise AdapterError("Expected a quoted string literal")
    body = raw[1:-1]
    out: list[str] = []
    index = 0
    while index < len(body):
        if body[index] == "\\" and index + 1 < len(body) and body[index + 1] in _ESCAPES:
            out.append(_ESCAPES[body[index + 1]])
            index += 2
        else:
            out.append(body[index])
            index += 1
    return "".join(out)


def encode_literal(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("Translation must be str")
    return '"' + "".join(_REVERSE_ESCAPES.get(char, char) for char in text) + '"'


def calls_from(tokens: Sequence[Token]) -> tuple[Call, ...]:
    calls: list[Call] = []
    for index, token in enumerate(tokens[:-1]):
        if token.kind != "id" or tokens[index + 1].raw != "(" or (index > 0 and tokens[index - 1].raw == "void"):
            continue
        open_index = index + 1
        depth: list[str] = [")"]
        args: list[tuple[Token, ...]] = []
        arg_start = open_index + 1
        cursor = arg_start
        while cursor < len(tokens):
            item = tokens[cursor]
            if item.kind == "punct" and item.raw in _DELIMITER_PAIRS:
                depth.append(_DELIMITER_PAIRS[item.raw])
            elif item.kind == "punct" and item.raw in _DELIMITER_PAIRS.values():
                if not depth or depth[-1] != item.raw:
                    raise UnsupportedStructureError(f"Mismatched call delimiter at line {item.line}")
                depth.pop()
                if not depth:
                    if cursor > arg_start:
                        args.append(tuple(tokens[arg_start:cursor]))
                    elif args:
                        raise UnsupportedStructureError(f"Empty trailing argument in {token.raw} at line {token.line}")
                    break
            elif item.kind == "punct" and item.raw == "," and len(depth) == 1:
                if cursor == arg_start:
                    raise UnsupportedStructureError(f"Empty argument in {token.raw} at line {token.line}")
                args.append(tuple(tokens[arg_start:cursor]))
                arg_start = cursor + 1
            cursor += 1
        if depth:
            raise UnsupportedStructureError(f"Unclosed call {token.raw} at line {token.line}")
        close_index = cursor
        calls.append(Call(token.raw, index, token.start, tokens[close_index].end, tuple(args), len(calls)))
    return tuple(calls)


def _literal(argument: Sequence[Token] | None) -> Token | None:
    return argument[0] if argument and len(argument) == 1 and argument[0].kind == "string" else None


def _is_null(argument: Sequence[Token] | None) -> bool:
    return bool(argument) and len(argument) == 1 and argument[0].raw == "NULL"


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]*>", "", text)


def _is_visible(text: str) -> bool:
    return bool(re.sub(r"[\s\u3000]", "", _strip_tags(text)))


def _protected_parts(text: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    # Preserve both the spelling and order of protected markers. Comparing
    # separate lists would allow a translator to move a placeholder across a
    # formatting tag while still appearing to retain both.
    pattern = re.compile(r"<[^>]*>|[\r\n\t\b\f\v\0]|\\[A-Za-z0-9]|\{\d+(?::[^}]*)?\}|%[sdif]")
    return tuple(match.group(0) for match in pattern.finditer(text)), (), ()


class BgiAdapter:
    """Explicit-schema adapter for the project's BGI text-call conventions.

    `opaque_commands` is opt-in: each entry declares a command's exact arity
    and which string-literal argument positions are known non-text data (such
    as resource identifiers). No string argument is assumed to be a resource
    or dialogue based only on its spelling.
    """

    adapter_id = "bgi"
    version = "1"
    formats = ("BGI/C-like source subset",)
    capabilities = frozenset({"extract", "writeback", "format_probe"})

    def __init__(
        self,
        *,
        schemas: Mapping[str, CallSchema] | None = None,
        opaque_commands: Mapping[str, OpaqueCallSchema] | None = None,
    ) -> None:
        self.schemas = dict(_DEFAULT_SCHEMAS if schemas is None else schemas)
        self.opaque_commands = dict(opaque_commands or {})

    @classmethod
    def from_options(cls, options: Mapping[str, object]) -> "BgiAdapter":
        allowed = {"schemas", "opaque_commands"}
        if set(options) - allowed:
            raise ValueError(f"Unsupported BGI adapter options: {sorted(set(options) - allowed)}")
        schemas: dict[str, CallSchema] | None = None
        opaque: dict[str, OpaqueCallSchema] = {}
        if "schemas" in options:
            rows = options["schemas"]
            if not isinstance(rows, dict):
                raise ValueError("game_specific.adapter_options.schemas must be an object")
            schemas = {}
            for command, row in rows.items():
                if (not isinstance(row, dict) or set(row) != {"arity", "text_pairs"}
                        or type(row["arity"]) is not int or not isinstance(row["text_pairs"], list)):
                    raise ValueError(f"Invalid schema configuration for {command}")
                pairs = []
                for pair in row["text_pairs"]:
                    if not isinstance(pair, dict) or set(pair) != {"source_argument", "target_argument", "role"}:
                        raise ValueError(f"Invalid text pair in schema {command}")
                    if (type(pair["source_argument"]) is not int or type(pair["target_argument"]) is not int
                            or not isinstance(pair["role"], str)):
                        raise ValueError(f"Invalid text pair values in schema {command}")
                    pairs.append(TextPair(pair["source_argument"], pair["target_argument"], pair["role"]))
                schemas[command] = CallSchema(row["arity"], tuple(pairs))
        if "opaque_commands" in options:
            rows = options["opaque_commands"]
            if not isinstance(rows, dict):
                raise ValueError("game_specific.adapter_options.opaque_commands must be an object")
            for command, row in rows.items():
                if (not isinstance(row, dict) or set(row) != {"arity", "non_text_string_arguments"}
                        or type(row["arity"]) is not int or not isinstance(row["non_text_string_arguments"], list)
                        or any(type(i) is not int for i in row["non_text_string_arguments"])):
                    raise ValueError(f"Invalid opaque command configuration for {command}")
                opaque[command] = OpaqueCallSchema(row["arity"], frozenset(row["non_text_string_arguments"]))
        return cls(schemas=schemas, opaque_commands=opaque or None)

    def probe(self, source: str) -> FormatProbe:
        """Inspect syntax only; never reads a file or evaluates BGI code."""
        try:
            calls = calls_from(lex(source))
        except (TypeError, UnsupportedStructureError) as exc:
            return FormatProbe(ProbeStatus.NEEDS_CONFIRMATION,
                               (f"Could not establish supported BGI syntax: {exc}",))
        recognized = sorted({call.name for call in calls if call.name in self.schemas})
        if recognized:
            return FormatProbe(ProbeStatus.SUPPORTED,
                               ("Recognized explicit BGI call schema(s): " + ", ".join(recognized),))
        if calls:
            return FormatProbe(ProbeStatus.NEEDS_CONFIRMATION,
                               ("Call syntax was found but no configured visible-text schema matched",))
        return FormatProbe(ProbeStatus.UNSUPPORTED, ("No configured BGI text-call evidence found",))

    def extract(self, source: str, script: str, reference_source: str | None = None) -> ExtractionResult:
        tokens = lex(source)
        calls = calls_from(tokens)
        reference_calls = None if reference_source is None else calls_from(lex(reference_source))
        if reference_calls is not None and (
            len(reference_calls) != len(calls)
            or any(a.name != b.name or len(a.args) != len(b.args) for a, b in zip(calls, reference_calls))
        ):
            raise UnsupportedStructureError(f"Reference call structure differs for {script}")

        slots: list[TextSlot] = []
        issues: list[ParserIssue] = []
        controls: list[ControlLiteral] = []
        slot_token_indices: dict[int, int] = {}
        accounted_string_indices: set[int] = set()

        # The audited project resolves the language-specific choice array only
        # for a declared char array guarded by the exact global-language branch.
        for select in (call for call in calls if call.name == "Select"):
            resolved = self._resolve_language_choices(tokens, calls, select, script, source)
            if resolved is None:
                issues.append(ParserIssue(script, tokens[select.token_index].line, tokens[select.token_index].column,
                                          select.name, "Select structure is not a proven literal language-choice array"))
                continue
            choice_slots, choice_token_indices, choice_accounted = resolved
            slots.extend(choice_slots)
            slot_token_indices.update(choice_token_indices)
            accounted_string_indices.update(choice_accounted)

        for call in calls:
            token = tokens[call.token_index]
            if call.name == "Select":
                continue
            schema = self.schemas.get(call.name)
            if schema is None:
                opaque = self.opaque_commands.get(call.name)
                if opaque is None:
                    string_args = tuple(i for i, arg in enumerate(call.args)
                                        if any(item.kind == "string" for item in arg))
                    string_starts = {item.start for arg in call.args for item in arg if item.kind == "string"}
                    accounted_string_indices.update(i for i, item in enumerate(tokens)
                                                    if item.kind == "string" and item.start in string_starts)
                    requires_review = call.name in _DYNAMIC_REVIEW_COMMANDS
                    reason = ("Dynamic/format/UI command requires explicit review"
                              if requires_review else
                              "Unknown call; explicit text or non-text argument schema required")
                    issues.append(ParserIssue(script, token.line, token.column, call.name,
                                              reason, string_args, bool(string_args) or requires_review))
                else:
                    if len(call.args) != opaque.arity:
                        issues.append(ParserIssue(script, token.line, token.column, call.name,
                                                  f"Opaque schema arity mismatch: {len(call.args)} != {opaque.arity}"))
                    else:
                        undeclared_strings = tuple(i for i, arg in enumerate(call.args)
                                                   if any(item.kind == "string" for item in arg)
                                                   and i not in opaque.non_text_string_arguments)
                        unexpected_literals = tuple(i for i in opaque.non_text_string_arguments
                                                    if i >= len(call.args) or not any(item.kind == "string" for item in call.args[i]))
                        if undeclared_strings or unexpected_literals:
                            issues.append(ParserIssue(script, token.line, token.column, call.name,
                                                      "Opaque string-argument declaration does not match source",
                                                      tuple(sorted(set(undeclared_strings + unexpected_literals)))))
                        else:
                            string_starts = {item.start for arg in call.args for item in arg if item.kind == "string"}
                            accounted_string_indices.update(i for i, item in enumerate(tokens)
                                                            if item.kind == "string" and item.start in string_starts)
                continue

            if len(call.args) != schema.arity:
                issues.append(ParserIssue(script, token.line, token.column, call.name,
                                          f"Call arity mismatch: {len(call.args)} != {schema.arity}"))
                continue
            paired_args = {index for pair in schema.text_pairs for index in (pair.source_argument, pair.target_argument)}
            for arg_index, arg in enumerate(call.args):
                if arg_index not in paired_args and any(item.kind == "string" for item in arg):
                    issues.append(ParserIssue(script, token.line, token.column, call.name,
                                              "String argument has no explicit role in this call schema", (arg_index,)))
                elif arg_index in paired_args:
                    string_starts = {item.start for item in arg if item.kind == "string"}
                    accounted_string_indices.update(i for i, item in enumerate(tokens)
                                                    if item.kind == "string" and item.start in string_starts)

            for pair in schema.text_pairs:
                source_literal = _literal(call.args[pair.source_argument])
                target_literal = _literal(call.args[pair.target_argument])
                if target_literal is None:
                    if _is_null(call.args[pair.target_argument]):
                        controls.append(ControlLiteral(script, token.line, token.column, call.name,
                                                       pair.target_argument, "NULL"))
                    else:
                        issues.append(ParserIssue(script, token.line, token.column, call.name,
                                                  "Target text argument is not a literal; no rewrite attempted",
                                                  (pair.target_argument,)))
                    continue
                target_text = decode_literal(target_literal.raw)
                if not _is_visible(target_text):
                    controls.append(ControlLiteral(script, target_literal.line, target_literal.column,
                                                   call.name, pair.target_argument, target_literal.raw))
                    continue
                if source_literal is None and not _is_null(call.args[pair.source_argument]):
                    issues.append(ParserIssue(script, token.line, token.column, call.name,
                                              "Source-language argument is not a literal; slot identity is ambiguous",
                                              (pair.source_argument,)))
                    continue
                original_text = decode_literal(source_literal.raw) if source_literal else ""
                reference_text = target_text
                if reference_calls is not None:
                    ref_call = reference_calls[call.ordinal]
                    ref_source = _literal(ref_call.args[pair.source_argument])
                    ref_target = _literal(ref_call.args[pair.target_argument])
                    if (ref_source.raw if ref_source else None) != (source_literal.raw if source_literal else None):
                        raise UnsupportedStructureError(f"Reference source-literal mismatch at {script}:{token.line}")
                    if ref_target:
                        reference_text = decode_literal(ref_target.raw)
                slot = self._make_slot(script, source, pair.role, call.name, call.ordinal,
                                       pair.target_argument, target_literal, original_text, target_text,
                                       reference_text, token_index=tokens.index(target_literal))
                slots.append(slot)
                slot_token_indices[slot.char_start] = tokens.index(target_literal)

        for token_index, item in enumerate(tokens):
            if item.kind == "string" and token_index not in accounted_string_indices:
                issues.append(ParserIssue(script, item.line, item.column, "<literal>",
                                          "String literal is outside a recognized text/resource argument schema"))

        slots.sort(key=lambda slot: slot.char_start)
        slots = self._attach_speaker_and_context(slots, calls)
        editable_indices = set(slot_token_indices.values())
        signature = self._structure_signature(tokens, editable_indices)
        return ExtractionResult(
            script=script,
            source=source,
            source_sha256=_sha(source),
            slots=tuple(slots),
            issues=tuple(issues),
            controls=tuple(controls),
            structure_signature=signature,
            adapter_payload={"tokens": tokens, "calls": calls, "slot_token_indices": slot_token_indices,
                             "schema_calls": tuple(call.name for call in calls)},
        )

    def write(self, parsed: ExtractionResult, translations: Mapping[str, str | Mapping[str, str]]) -> str:
        if parsed.unresolved_count:
            details = "; ".join(f"{issue.script}:{issue.line} {issue.command}: {issue.reason}"
                                 for issue in parsed.issues if issue.fatal_for_writeback)
            raise UnsupportedStructureError("Writeback blocked by unresolved structure: " + details)
        expected_ids = {slot.slot_id for slot in parsed.slots}
        actual_ids = set(translations)
        missing, extra = expected_ids - actual_ids, actual_ids - expected_ids
        if missing or extra:
            raise AdapterError(f"Translation key mismatch; missing={sorted(missing)}, extra={sorted(extra)}")

        payload = parsed.adapter_payload
        before_tokens: tuple[Token, ...] = payload["tokens"]
        patches: list[tuple[int, int, str]] = []
        slot_by_start = {slot.char_start: slot for slot in parsed.slots}
        for slot in parsed.slots:
            record = translations[slot.slot_id]
            text = record if isinstance(record, str) else record.get("text", record.get("zh"))
            if not isinstance(text, str) or not _is_visible(text):
                raise AdapterError(f"Empty or invalid translation for {slot.slot_id}")
            if _protected_parts(slot.reference_text or slot.current_text) != _protected_parts(text):
                raise AdapterError(f"Markup, control escapes, or placeholders changed for {slot.slot_id}")
            patches.append((slot.char_start, slot.char_end, encode_literal(text)))

        output = parsed.source
        for start, end, replacement in sorted(patches, reverse=True):
            output = output[:start] + replacement + output[end:]

        after_tokens = lex(output)
        if len(after_tokens) != len(before_tokens):
            raise AdapterError("Token count changed after writeback")
        editable_indices = set(payload["slot_token_indices"].values())
        for index, (before, after) in enumerate(zip(before_tokens, after_tokens)):
            if index in editable_indices:
                if before.kind != "string" or after.kind != "string":
                    raise AdapterError("A text slot no longer parses as a string literal")
            elif before.kind != after.kind or before.raw != after.raw:
                raise AdapterError(f"Non-target token changed near original line {before.line}")

        after_calls = calls_from(after_tokens)
        before_calls: tuple[Call, ...] = payload["calls"]
        if len(after_calls) != len(before_calls) or any(
            a.name != b.name or len(a.args) != len(b.args) for a, b in zip(before_calls, after_calls)
        ):
            raise AdapterError("Call count, order, or signature changed after writeback")
        if self._structure_signature(after_tokens, editable_indices) != parsed.structure_signature:
            raise AdapterError("Control structure signature changed after writeback")
        # Mask only authorized string tokens. This compares comments, whitespace,
        # resources, controls, and all non-target source characters exactly.
        before_mask = self._masked_source(parsed.source, before_tokens, editable_indices)
        after_mask = self._masked_source(output, after_tokens, editable_indices)
        if before_mask != after_mask:
            raise AdapterError("Non-target source region changed after writeback")
        return output

    @staticmethod
    def _make_slot(script: str, source: str, role: str, command: str, call_index: int,
                   argument_index: int, token: Token, original: str, current: str,
                   reference: str | None, *, token_index: int, item_index: int | None = None) -> TextSlot:
        category = _ROLE_CATEGORY.get(role, TextCategory.OTHER_VISIBLE)
        # Use the source-language text and structural address, never the
        # replaceable English/current text, so IDs survive translation edits.
        identity_text = (f"{script}\0{command}\0{call_index}\0{argument_index}\0{role}\0{original}"
                         if item_index is None else
                         f"{script}\0{command}\0{call_index}\0{argument_index}\0{item_index}\0{role}\0{original}")
        slot_id = _sha(identity_text)[:24]
        return TextSlot(slot_id, script, category, role, original, current, reference,
                        token.start, token.end, len(source[:token.start].encode("utf-8")),
                        token.line, token.column, command, call_index, argument_index,
                        item_index=item_index)

    @staticmethod
    def _structure_signature(tokens: Sequence[Token], editable_indices: set[int]) -> tuple[tuple[str, str], ...]:
        return tuple((token.kind, "<TARGET_STRING>" if i in editable_indices else token.raw)
                     for i, token in enumerate(tokens))

    @staticmethod
    def _masked_source(source: str, tokens: Sequence[Token], editable_indices: set[int]) -> str:
        result = source
        spans = [tokens[index] for index in editable_indices]
        for token in sorted(spans, key=lambda item: item.start, reverse=True):
            result = result[:token.start] + '"<TARGET_STRING>"' + result[token.end:]
        return result

    @staticmethod
    def _attach_speaker_and_context(slots: list[TextSlot], calls: Sequence[Call]) -> list[TextSlot]:
        speaker = ""
        by_call: dict[int, list[TextSlot]] = {}
        for slot in slots:
            by_call.setdefault(slot.call_index, []).append(slot)
        updates: dict[str, TextSlot] = {}
        # Process every call, not only calls which yielded slots: a message
        # clear/reset command changes the speaker context for later dialogue.
        for call in calls:
            if call.name == "ClearMessage":
                speaker = ""
            for slot in sorted(by_call.get(call.ordinal, ()), key=lambda item: item.char_start):
                if slot.role == "name":
                    speaker = _strip_tags(slot.original_text).strip()
                updates[slot.slot_id] = TextSlot(
                    slot.slot_id, slot.script, slot.category, slot.role, slot.original_text,
                    slot.current_text, slot.reference_text, slot.char_start, slot.char_end,
                    slot.byte_offset, slot.line, slot.column, slot.command, slot.call_index,
                    slot.argument_index, slot.item_index, speaker or None, slot.context)
        enriched = [updates[slot.slot_id] for slot in slots]
        for index, slot in enumerate(enriched):
            before = tuple({"role": item.role, "original_text": item.original_text,
                            "current_text": item.current_text}
                           for item in enriched[max(0, index - 2):index])
            after = tuple({"role": item.role, "original_text": item.original_text,
                           "current_text": item.current_text}
                          for item in enriched[index + 1:index + 3])
            enriched[index] = TextSlot(
                slot.slot_id, slot.script, slot.category, slot.role, slot.original_text,
                slot.current_text, slot.reference_text, slot.char_start, slot.char_end,
                slot.byte_offset, slot.line, slot.column, slot.command, slot.call_index,
                slot.argument_index, slot.item_index, slot.speaker_hint,
                {"previous": before, "next": after})
        return enriched

    def _resolve_language_choices(self, tokens: Sequence[Token], calls: Sequence[Call],
                                  select: Call, script: str, source: str
                                  ) -> tuple[list[TextSlot], dict[int, int], set[int]] | None:
        if (len(select.args) != 2 or len(select.args[0]) != 1 or select.args[0][0].kind != "number"
                or len(select.args[1]) != 1 or select.args[1][0].kind != "id"):
            return None
        count_raw = select.args[0][0].raw
        if not count_raw.isdecimal():
            return None
        count, variable = int(count_raw), select.args[1][0].raw
        if count < 1 or count > 100:
            return None
        select_token_index = select.token_index
        declarations = [i for i, token in enumerate(tokens[:select_token_index - 4])
                        if token.raw == "char" and tokens[i + 1].raw == variable
                        and tokens[i + 2].raw == "[" and tokens[i + 3].kind == "number"
                        and tokens[i + 3].raw.isdecimal() and int(tokens[i + 3].raw) == count
                        and tokens[i + 4].raw == "]"]
        if not declarations:
            return None
        declaration_index = declarations[-1]
        guards = [call for call in calls if call.name == "if"
                  and tokens[declaration_index].start < call.start < select.start
                  and call.args and "".join(token.raw for token in call.args[0]) == "GetGlobalFlag(GLanguage)==1"]
        if len(guards) != 1:
            return None
        guard = guards[0]
        begin = next((i for i, token in enumerate(tokens) if token.start >= guard.end), -1)
        en_block = self._read_block(tokens, begin)
        if en_block is None:
            return None
        en_values, after_en = en_block
        if after_en >= len(tokens) or tokens[after_en].raw != "else":
            return None
        jp_block = self._read_block(tokens, after_en + 1)
        if jp_block is None:
            return None
        jp_values, after_jp = jp_block
        if tokens[after_jp - 1].end > select.start:
            return None
        en_map, jp_map = self._read_assignments(en_values, variable), self._read_assignments(jp_values, variable)
        if en_map is None or jp_map is None or len(en_map) != count or len(jp_map) != count:
            return None
        if set(en_map) != set(range(count)) or set(jp_map) != set(range(count)):
            return None
        slots: list[TextSlot] = []
        token_indices: dict[int, int] = {}
        accounted: set[int] = set()
        for item_index in range(count):
            target, original = en_map[item_index], jp_map[item_index]
            accounted.add(tokens.index(target))
            accounted.add(tokens.index(original))
            current_text, original_text = decode_literal(target.raw), decode_literal(original.raw)
            if not _is_visible(current_text):
                return None
            slot = self._make_slot(script, source, "choice", select.name, select.ordinal, 1,
                                   target, original_text, current_text, current_text,
                                   token_index=tokens.index(target), item_index=item_index)
            # Correct byte offset using the actual source at the caller.
            slots.append(slot)
            token_indices[target.start] = tokens.index(target)
        return slots, token_indices, accounted

    @staticmethod
    def _read_block(tokens: Sequence[Token], start: int) -> tuple[tuple[Token, ...], int] | None:
        if start < 0 or start >= len(tokens) or tokens[start].raw != "{":
            return None
        depth = 1
        for index in range(start + 1, len(tokens)):
            token = tokens[index]
            if token.kind != "punct":
                continue
            if token.raw == "{":
                depth += 1
            elif token.raw == "}":
                depth -= 1
                if depth == 0:
                    return tuple(tokens[start + 1:index]), index + 1
        return None

    @staticmethod
    def _read_assignments(tokens: Sequence[Token], variable: str) -> dict[int, Token] | None:
        entries: dict[int, Token] = {}
        index = 0
        while index < len(tokens):
            part = tokens[index:index + 7]
            if (len(part) != 7 or part[0].raw != variable or part[1].raw != "["
                    or part[2].kind != "number" or not part[2].raw.isdecimal()
                    or part[3].raw != "]" or part[4].raw != "="
                    or part[5].kind != "string" or part[6].raw != ";"):
                return None
            item_index = int(part[2].raw)
            if item_index in entries:
                return None
            entries[item_index] = part[5]
            index += 7
        return entries
