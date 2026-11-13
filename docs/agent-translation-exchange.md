# Offline Agent translation exchange

This feature exports current toolkit translation tasks as UTF-8 JSONL and imports agent-produced text into the existing `TranslationStore`. It does not call an online model or approve translations.

## CLI

After `localize extract`, export tasks with `localize translate-export --project PROJECT exchange/tasks-000.jsonl --limit 50 --offset 0`. Continue with a new output filename and offset 50, 100, and so on. The output is project-relative and created exclusively; an existing file is never overwritten. Omit `--limit` or pass zero to export all eligible tasks. Current pending-review and approved records are excluded.

Return a JSONL file containing exactly these fields per result: `schema_version` (integer 1), `task_id`, `slot_id`, `source_fingerprint` (SHA-256 of the primary source text), `task_fingerprint` (the deterministic task ID, which binds source, context, rules, prompt, languages and location), and `translated_text`. Import with `localize translate-import --project PROJECT exchange/results.jsonl`. Results are structurally checked with the existing placeholder, formatting-tag, control-escape and glossary validation and are written as `pending_review` only. An import file cannot choose a status.

Task export records additionally include source text, languages, context JSON, reference text, role and category. Each record starts with `schema_version: 1`. Output order is stable by slot ID. Re-exporting to the same path fails instead of replacing the prior batch.

## Atomicity, conflicts and recovery

The importer parses and validates the entire file before writing. It captures a store SHA-256 before validation; `save_many` acquires the store lock, checks that fingerprint, reloads the latest JSON state under the lock, merges the complete batch, writes a same-directory temporary file, flushes it, then atomically replaces the state file. A bad row leaves the state unchanged. A concurrent writer changes the fingerprint and causes this import to fail with `TRANSLATION_EXCHANGE_INVALID`; rerun from a fresh export/state. Repeated identical pending results are accepted as the same pending value; a different result for an existing task or any already-reviewed task is a conflict.

All public store writes (`upsert`, `save_many`) use the same OS lock file. Windows uses a byte-range lock through `msvcrt`; POSIX uses `flock`. Locks are advisory, have a five-second default timeout, and are released by the operating system when a process exits. The `.lock` file is intentionally persistent: deleting and recreating it could split lock ownership. This is intended for local filesystems. Network shares, unusual filesystem semantics, power loss, hardware failure and concurrent writers that bypass `TranslationStore` are not certified. Atomic rename protects against ordinary replacement errors; it is not a general database transaction or durability guarantee.

An imported record remains `pending_review`. Use `localize review` with an identified human reviewer before compile. The CLI does not auto-review, compile or deploy during import.

## Synthetic example and schema fixture

Run `python examples/agent-translation/exchange_demo.py` from the repository root with `PYTHONPATH=src`. It creates a temporary synthetic BGI-like input, exports one task, fills a result through a local mock step, imports it, checks QA, explicitly marks the fixture as reviewed, and exercises the adapter write path. The reviewer identity is synthetic and demonstrates API mechanics only; it is not approval for real text. `examples/agent-translation/result.schema-example.jsonl` illustrates the six-field result shape and contains dummy fingerprints, so it is not importable into a project.

## Error behavior and limits

CLI failures return exit code 2 and JSON on stderr with `schema_version`, error code, message and next action. The task exchange schema is version 1; unknown fields and versions fail closed. This performs structural QA, not semantic translation review. Never treat a successful import as approval or proof of linguistic correctness.
