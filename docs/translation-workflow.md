# AI-assisted translation workflow

This module builds on the repository's generic `TextSlot` model and the BGI adapter's proven source/reference pairing, task IDs, source fingerprints, protected-format checks, local provider boundary, retry queue, and safe writer. It is independent of a game and model vendor. The included `MockProvider` is local-only; no HTTP client, credentials, endpoint, or online model integration is included.

## Separation of project data

The source project used a 104-entry bilingual glossary to guide translation and a separate 96-record compiled-script dictionary used by the game runtime for compiled-file metadata. These served different purposes. This workflow consumes only a caller-supplied `Glossary` of source/target terminology rules. It does not read, modify, or interpret compiler dictionaries. No project glossary entries or compiled-index records are distributed here.

External glossary schemas are not guessed. Convert records explicitly with `Glossary.from_records(..., source_field=..., target_field=...)`, or use `Glossary.load()` with the toolkit's small `{"version": ..., "entries": [{"source": ..., "target": ..., "required": ..., "note": ...}]}` format. Conflicting mappings for the same case-folded source term are rejected.

## Create and run tasks

`build_tasks()` accepts extracted `TextSlot` objects. `original_text` is the primary source; `reference_text` (or the slot's current text when no separate reference is present) is auxiliary. Task identity binds the slot ID, structural location, source and reference fingerprints, role/category, speaker/context fingerprint, languages, prompt template, glossary/rules fingerprint, and rule version. Identical source text in distinct slots remains distinct because slot IDs and call locations are part of task identity.

`PromptTemplate` supports configurable fields for source/target language, primary and reference text, role/category, speaker, serialized context, relevant glossary constraints, special project rules, and slot ID. The provider-neutral `AIProvider` protocol receives a batch ID, task objects, and rendered prompts, then returns `ProviderResult` items keyed by slot ID.

```python
from pathlib import Path

from localization_toolkit.adapters.bgi import BgiAdapter
from localization_toolkit.translation import (
    Glossary, MockProvider, PromptTemplate, ProviderResult,
    TranslationStore, run_batches,
)

source = 'OutputLineAll("Synthetic source", "Synthetic reference", 0);'
extraction = BgiAdapter().extract(source, "synthetic.bgi")
provider = MockProvider(lambda request: [
    ProviderResult(task.slot_id, "合成译文") for task in request.tasks
])
summary = run_batches(
    extraction.slots,
    glossary=Glossary("demo-glossary-v1", ()),
    prompt_template=PromptTemplate(),
    provider=provider,
    store=TranslationStore(Path("work/translation-state.json")),
    source_language="en",
    target_language="zh-Hans",
    rule_version="demo-rules-v1",
    batch_size=8,
    special_rules="Do not add information absent from the primary source.",
)
print(summary["pending_review"])
```

The example at `examples/translation/mock_batch.py` uses synthetic text and a temporary state file. Provider implementations are synchronous adapters in this version; an online integration would be a separate caller-supplied project component and must manage its own consent, credentials, limits, and data handling.

## State, retries, and staleness

`TranslationStore` writes an atomic local JSON state file. Each record retains batch ID, task/slot ID, primary-source SHA-256, structural-location and context fingerprints, glossary/rule and prompt fingerprints, target text, status, validation or provider-error reasons, provider label, processing timestamp, and optional reviewer/note. The store is not encrypted and is intended for a controlled local workspace; do not commit it if it contains private or copyrighted text.

Valid provider output is saved as `pending_review`, never as approved. Structurally unsafe output is `needs_review`; failed calls are `provider_error`; explicitly accepted/rejected results are `approved`/`rejected`. Re-running the same tasks skips current pending-review and approved records. Failed or structurally invalid records can be retried. When source text, reference, structural location, role/context, prompt, glossary, or rule version changes, an older matching-location record is marked `stale` and cannot be reused for writeback.

Batch writes happen after each provider response, so completed batches survive process interruption. The state file currently assumes one writer at a time; concurrent workflow processes sharing one store are unsupported.

## Structural checks and human review

The result validator checks exact slot-ID coverage, unknown/duplicate/missing IDs, text type/non-emptiness, placeholder multisets, ordered formatting-tag and control-marker preservation, required glossary targets, and source/rule association. A result with a structural warning cannot be approved through the normal API. A structurally clean result only becomes `pending_review`: a human must call `approve_translation()` with an explicit reviewer identity after assessing meaning and style.

Semantic accuracy, omissions not exposed by structural markers, character voice, ambiguity, cultural references, and invented story facts cannot be reliably certified by this module. The human reviewer must compare the primary source, reference, context, and translation. A reviewer declaration is an audit record, not a machine proof of linguistic correctness.

### Reviewer checklist

- Read the primary source first; use the reference translation only to cross-check.
- Confirm negation, conditionals, tense, speaker, relationships, quantities, and cause/effect.
- Preserve ambiguity, pauses, emotional intensity, and character voice; do not invent actions, motives, or facts.
- Check terms against the current approved glossary and note any context-sensitive exceptions.
- Read neighboring slots together and verify that text has not shifted across slot IDs.
- Confirm names and any protected tags/markers are in the correct slot and speaker context.
- Record unresolved questions instead of silently resolving them by inference.

`write_approved()` rebuilds task fingerprints from the current extraction and rules, requires every slot to have a matching explicit approval, validates markers again, then invokes the supplied adapter's `write()` method. The adapter's unresolved-structure checks remain authoritative; this module does not provide a raw text replacement path or bypass BGI safety checks. No file is written by `write_approved()` itself: it returns the adapter-produced candidate string for the caller to stage and review.

## Scope and limitations

- No AI service provider, network call, translation-quality model, automatic semantic approval, or real translation is implemented.
- Marker recognition is conservative and configurable support may be needed for project-specific placeholder syntaxes.
- The glossary checker enforces only explicitly required source/target entries; it cannot infer term sense from context.
- A local JSON store has no locking, encryption, multi-user merge, or remote synchronization.
- Safe script writeback is delegated to the existing format adapter. This module is not a script compiler, deployment tool, or installer.

Run the synthetic example with `python examples/translation/mock_batch.py`.
