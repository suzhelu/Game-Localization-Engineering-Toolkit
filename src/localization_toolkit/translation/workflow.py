"""Batch, resume, review, and adapter-gated writeback orchestration."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Iterable, Sequence

from ..models import ExtractionResult, TextSlot
from .glossary import Glossary
from .models import (
    ProviderResult,
    TranslationIssue,
    TranslationRecord,
    TranslationRequest,
    TranslationStatus,
    TranslationTask,
    slot_source_text,
)
from .prompt import PromptTemplate
from .provider import AIProvider
from .quality import validate_target
from .store import TranslationStore


class TranslationWorkflowError(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_tasks(
    slots: Sequence[TextSlot],
    *,
    glossary: Glossary,
    prompt_template: PromptTemplate,
    source_language: str,
    target_language: str,
    rule_version: str,
    special_rules: str = "",
) -> tuple[TranslationTask, ...]:
    if (not isinstance(source_language, str) or not source_language.strip()
            or not isinstance(target_language, str) or not target_language.strip()
            or not isinstance(rule_version, str) or not rule_version.strip()
            or not isinstance(special_rules, str)):
        raise TranslationWorkflowError("Source language, target language, and rule version are required")
    ids = [slot.slot_id for slot in slots]
    if len(ids) != len(set(ids)):
        raise TranslationWorkflowError("Input contains duplicate text-slot IDs")
    rules_fingerprint = _sha(_canonical({
        "rule_version": rule_version,
        "glossary_sha256": glossary.fingerprint,
        "special_rules": special_rules,
        "quality_policy": "structural-validation-v1",
    }))
    tasks: list[TranslationTask] = []
    for slot in slots:
        source = slot_source_text(slot)
        if not source:
            raise TranslationWorkflowError(f"Slot {slot.slot_id} has empty primary source text")
        context_payload = {
            "speaker_hint": slot.speaker_hint,
            "context": slot.context,
        }
        try:
            context_json = json.dumps(context_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            context_bytes = context_json.encode("utf-8", errors="strict")
            source_bytes = source.encode("utf-8", errors="strict")
        except (TypeError, ValueError) as exc:
            raise TranslationWorkflowError(f"Slot {slot.slot_id} has non-serializable context") from exc
        except UnicodeError as exc:
            raise TranslationWorkflowError(f"Slot {slot.slot_id} contains invalid Unicode") from exc
        context_sha = _sha(context_bytes)
        source_sha = _sha(source_bytes)
        location_fingerprint = _sha(_canonical({
            "script": slot.script, "command": slot.command, "call_index": slot.call_index,
            "argument_index": slot.argument_index, "item_index": slot.item_index,
            "role": slot.role,
        }))
        reference = slot.reference_text if slot.reference_text is not None else slot.current_text
        if reference is not None:
            try:
                reference_bytes = reference.encode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise TranslationWorkflowError(f"Slot {slot.slot_id} reference contains invalid Unicode") from exc
        else:
            reference_bytes = b""
        identity = {
            "slot_id": slot.slot_id,
            "location_fingerprint": location_fingerprint,
            "script": slot.script,
            "role": slot.role,
            "category": slot.category.value,
            "speaker_hint": slot.speaker_hint,
            "source_sha256": source_sha,
            "reference_sha256": _sha(reference_bytes),
            "context_sha256": context_sha,
            "rules_fingerprint": rules_fingerprint,
            "prompt_fingerprint": prompt_template.fingerprint,
            "source_language": source_language,
            "target_language": target_language,
        }
        task_id = _sha(_canonical(identity))[:32]
        tasks.append(TranslationTask(
            task_id=task_id, slot_id=slot.slot_id, script=slot.script, role=slot.role,
            category=slot.category.value, source_text=source, reference_text=reference,
            source_sha256=source_sha, location_fingerprint=location_fingerprint,
            context_json=context_json, context_sha256=context_sha,
            rules_fingerprint=rules_fingerprint, prompt_fingerprint=prompt_template.fingerprint,
            source_language=source_language, target_language=target_language,
            rule_version=rule_version, speaker_hint=slot.speaker_hint,
        ))
    return tuple(tasks)


def _record(
    task: TranslationTask,
    batch_id: str,
    *,
    target_text: str | None,
    status: TranslationStatus,
    issues: Iterable[TranslationIssue],
    provider: str,
) -> TranslationRecord:
    return TranslationRecord(
        batch_id=batch_id, task_id=task.task_id, slot_id=task.slot_id,
        source_sha256=task.source_sha256, location_fingerprint=task.location_fingerprint,
        context_sha256=task.context_sha256,
        rules_fingerprint=task.rules_fingerprint, prompt_fingerprint=task.prompt_fingerprint,
        rule_version=task.rule_version, target_text=target_text, status=status,
        issues=tuple(issues), provider=provider, processed_at=_now(),
    )


def run_batches(
    slots: Sequence[TextSlot],
    *,
    glossary: Glossary,
    prompt_template: PromptTemplate,
    provider: AIProvider,
    store: TranslationStore,
    source_language: str,
    target_language: str,
    rule_version: str,
    batch_size: int = 8,
    special_rules: str = "",
) -> dict[str, int]:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise TranslationWorkflowError("batch_size must be a positive integer")
    tasks = build_tasks(
        slots, glossary=glossary, prompt_template=prompt_template,
        source_language=source_language, target_language=target_language,
        rule_version=rule_version, special_rules=special_rules,
    )
    stale_count = 0
    for task in tasks:
        for old in store.stale_records(task.location_fingerprint, task.task_id):
            store.upsert(replace(
                old, status=TranslationStatus.STALE,
                issues=old.issues + (TranslationIssue("stale_fingerprint", "Source, context, prompt, or rules changed; translation cannot be reused"),),
                processed_at=_now(),
            ))
            stale_count += 1
    pending: list[TranslationTask] = []
    for task in tasks:
        existing = store.get(task.task_id)
        if existing and existing.status in {TranslationStatus.PENDING_REVIEW, TranslationStatus.APPROVED}:
            association_matches = (
                existing.slot_id == task.slot_id
                and existing.source_sha256 == task.source_sha256
                and existing.location_fingerprint == task.location_fingerprint
                and existing.context_sha256 == task.context_sha256
                and existing.rules_fingerprint == task.rules_fingerprint
                and existing.prompt_fingerprint == task.prompt_fingerprint
                and existing.rule_version == task.rule_version
            )
            cached_issues = validate_target(task, existing.target_text, glossary)
            if not association_matches or cached_issues or (existing.status == TranslationStatus.APPROVED and not existing.reviewer):
                issues = list(existing.issues)
                issues.append(TranslationIssue("cached_record_invalid", "Cached review record failed current association or structural checks"))
                issues.extend(cached_issues)
                store.upsert(replace(existing, status=TranslationStatus.NEEDS_REVIEW,
                                     issues=tuple(issues), processed_at=_now()))
                continue
            if not existing.issues:
                continue
            store.upsert(replace(existing, status=TranslationStatus.NEEDS_REVIEW, processed_at=_now()))
            continue
        pending.append(task)

    batches = [pending[index:index + batch_size] for index in range(0, len(pending), batch_size)]
    for batch_number, batch in enumerate(batches):
        batch_id = _sha(_canonical({
            "number": batch_number,
            "task_ids": [task.task_id for task in batch],
            "batch_size": batch_size,
        }))[:24]
        prompts = {task.slot_id: prompt_template.render(task, glossary, special_rules) for task in batch}
        request = TranslationRequest(batch_id, tuple(batch), prompts)
        try:
            response = provider.translate(request)
            if isinstance(response, (str, bytes)) or not isinstance(response, Sequence):
                raise TypeError("Provider must return a sequence of ProviderResult records")
            if any(not isinstance(item, ProviderResult) for item in response):
                raise TypeError("Provider response contains a non-ProviderResult item")
        except Exception as exc:
            reason = str(exc)[:1000] or type(exc).__name__
            store.save_many(_record(
                task, batch_id, target_text=None, status=TranslationStatus.PROVIDER_ERROR,
                issues=(TranslationIssue("provider_error", reason),), provider=getattr(provider, "name", type(provider).__name__),
            ) for task in batch)
            continue

        malformed = [item for item in response if not isinstance(item.slot_id, str) or not isinstance(item.target_text, str)]
        if malformed:
            store.save_many(_record(
                task, batch_id, target_text=None, status=TranslationStatus.NEEDS_REVIEW,
                issues=(TranslationIssue("invalid_provider_result", "Provider slot IDs and translations must be strings"),),
                provider=getattr(provider, "name", type(provider).__name__),
            ) for task in batch)
            continue

        by_id: dict[str, list[ProviderResult]] = {}
        for item in response:
            by_id.setdefault(item.slot_id, []).append(item)
        expected_ids = {task.slot_id for task in batch}
        unknown = set(by_id) - expected_ids
        records: list[TranslationRecord] = []
        for task in batch:
            found = by_id.get(task.slot_id, [])
            issues: list[TranslationIssue] = []
            if unknown:
                issues.append(TranslationIssue("unexpected_slot_id", "Provider returned one or more unknown slot IDs"))
            if not found:
                issues.append(TranslationIssue("missing_result", "Provider omitted this slot ID"))
            elif len(found) != 1:
                issues.append(TranslationIssue("duplicate_result", "Provider returned this slot ID more than once"))
            target_text = found[0].target_text if len(found) == 1 else None
            if len(found) == 1:
                issues.extend(validate_target(task, target_text, glossary))
            status = TranslationStatus.NEEDS_REVIEW if issues else TranslationStatus.PENDING_REVIEW
            records.append(_record(
                task, batch_id, target_text=target_text, status=status, issues=issues,
                provider=getattr(provider, "name", type(provider).__name__),
            ))
        store.save_many(records)

    records = [store.get(task.task_id) for task in tasks]
    return {
        "task_count": len(tasks),
        "provider_calls": len(batches),
        "pending_review": sum(record is not None and record.status == TranslationStatus.PENDING_REVIEW for record in records),
        "approved": sum(record is not None and record.status == TranslationStatus.APPROVED for record in records),
        "needs_review": sum(record is not None and record.status == TranslationStatus.NEEDS_REVIEW for record in records),
        "provider_error": sum(record is not None and record.status == TranslationStatus.PROVIDER_ERROR for record in records),
        "missing": sum(record is None for record in records),
        "stale_records": stale_count,
    }


def approve_translation(
    task: TranslationTask,
    *,
    store: TranslationStore,
    glossary: Glossary,
    reviewer: str,
    review_note: str = "",
) -> TranslationRecord:
    if not reviewer.strip():
        raise TranslationWorkflowError("An explicit reviewer name/identifier is required")
    record = store.get(task.task_id)
    if record is None or record.status != TranslationStatus.PENDING_REVIEW:
        raise TranslationWorkflowError("Only a current structurally valid pending-review record can be approved")
    if (record.slot_id != task.slot_id or record.source_sha256 != task.source_sha256
            or record.location_fingerprint != task.location_fingerprint
            or record.context_sha256 != task.context_sha256
            or record.rules_fingerprint != task.rules_fingerprint
            or record.prompt_fingerprint != task.prompt_fingerprint
            or record.rule_version != task.rule_version):
        raise TranslationWorkflowError("Translation is stale or no longer bound to this task")
    if record.issues:
        raise TranslationWorkflowError("A record with validation/error issues cannot be approved")
    issues = validate_target(task, record.target_text, glossary)
    if issues:
        raise TranslationWorkflowError("Translation has blocking structural issues and cannot be approved")
    approved = replace(record, status=TranslationStatus.APPROVED, reviewer=reviewer.strip(),
                       review_note=review_note, processed_at=_now())
    store.upsert(approved)
    return approved


def reject_translation(task: TranslationTask, *, store: TranslationStore, reviewer: str, reason: str) -> TranslationRecord:
    if not reviewer.strip() or not reason.strip():
        raise TranslationWorkflowError("Rejecting a result requires reviewer and reason")
    record = store.get(task.task_id)
    if record is None or record.status not in {TranslationStatus.PENDING_REVIEW, TranslationStatus.NEEDS_REVIEW}:
        raise TranslationWorkflowError("No reviewable result exists for this task")
    rejected = replace(record, status=TranslationStatus.REJECTED, reviewer=reviewer.strip(),
                       review_note=reason.strip(), processed_at=_now())
    store.upsert(rejected)
    return rejected


def write_approved(
    adapter: object,
    extraction: ExtractionResult,
    *,
    glossary: Glossary,
    prompt_template: PromptTemplate,
    store: TranslationStore,
    source_language: str,
    target_language: str,
    rule_version: str,
    special_rules: str = "",
) -> str:
    if extraction.unresolved_count:
        raise TranslationWorkflowError("Adapter extraction contains unresolved structures; writeback is blocked")
    tasks = build_tasks(
        extraction.slots, glossary=glossary, prompt_template=prompt_template,
        source_language=source_language, target_language=target_language,
        rule_version=rule_version, special_rules=special_rules,
    )
    translations: dict[str, str] = {}
    for task in tasks:
        record = store.get(task.task_id)
        if record is None or record.status != TranslationStatus.APPROVED:
            raise TranslationWorkflowError(f"Slot {task.slot_id} is not explicitly approved against current inputs")
        if (record.source_sha256 != task.source_sha256 or record.location_fingerprint != task.location_fingerprint
                or record.context_sha256 != task.context_sha256
                or record.rules_fingerprint != task.rules_fingerprint
                or record.prompt_fingerprint != task.prompt_fingerprint
                or record.rule_version != task.rule_version or record.target_text is None):
            raise TranslationWorkflowError(f"Slot {task.slot_id} has stale or incomplete approval metadata")
        if not record.reviewer or record.issues:
            raise TranslationWorkflowError(f"Slot {task.slot_id} lacks a clean reviewer approval record")
        issues = validate_target(task, record.target_text, glossary)
        if issues:
            raise TranslationWorkflowError(f"Approved slot {task.slot_id} no longer passes structural checks")
        translations[task.slot_id] = record.target_text
    writer = getattr(adapter, "write", None)
    if not callable(writer):
        raise TranslationWorkflowError("A compatible safe text adapter with write() is required")
    try:
        return writer(extraction, translations)
    except Exception as exc:
        raise TranslationWorkflowError(f"Adapter rejected safe writeback: {exc}") from exc
