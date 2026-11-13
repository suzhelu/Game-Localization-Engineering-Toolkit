---
name: game-localization
description: Guide evidence-based localization planning and guarded toolkit workflows for explicitly authorized scripts, using supported adapters and human review.
---

# Game localization workflow

Use for toolkit-based extraction, translation preparation, validation and release planning. This skill does not identify arbitrary engines or supply online AI, game resources or compilers. Read [repository instructions](../../AGENTS.md) first. Other Agent hosts can manually load this file; automatic discovery is not verified.

## Prerequisites

Confirm repository revision, clean/dirty state, user-authorized inputs and legal tool availability. Never assume BGI. Unknown formats require read-only analysis first. Prepare an isolated project; original game directories must remain read-only.

## Stages and outputs

1. Identify supported syntax and inputs. Produce an analysis report with hashes, evidence, unresolved structures and adapter decision. Read [analysis reference](references/analysis.md).
2. Configure explicit inputs and extract slots. Preserve slot IDs, controls and extraction diagnostics. Read the existing BGI and CLI documents linked in the reference only when this stage applies.
3. Run Mock batches only to exercise plumbing. Mock translation is not real translation. Preserve task fingerprints and pending records; human review cannot be skipped. Do not impersonate a reviewer.
   For offline Agent exchange, use `localize translate-export --project PROJECT exchange/tasks.jsonl`, return schema-version-1 JSONL, then `localize translate-import --project PROJECT exchange/results.jsonl`. Imports remain `pending_review`; never approve on behalf of the user. Read [Agent translation exchange](../../docs/agent-translation-exchange.md) for fields, fingerprint checks, batch validation and lock boundaries.
4. After explicit review, build and verify in isolation using the existing compiler interface. Static validation is not in-game acceptance. Record exit codes, evidence levels, artifact hashes and limitations.
5. Package explicit verified files. Deployment and recovery are separate authorized operations. Read [release reference](references/release.md) only when requested.

Stop the affected operation on unsupported syntax, stale inputs, failed validation, unknown target hashes or ambiguous recovery state. Preserve checkpoints and actual validation evidence. Never edit state to manufacture success. Do not upload game resources, secrets or unauthorized tools; do not bypass DRM, encryption protections or anti-cheat mechanisms.
