# FAQ

## Can I use the toolkit immediately after downloading it?

Install Python 3.10+ and the package in a virtual environment first. The repository has synthetic examples; real projects still need explicit inputs, a supported adapter, and any required lawful external compiler.

## Is Python required?

Yes. The current package requires Python 3.10 or newer. On Windows, `py` is optional; a selected `python.exe` can create the virtual environment.

## Must I configure an OpenAI API key?

No. The CLI currently connects a local Mock Provider and offline Agent JSONL exchange. It does not call online AI services. Mock output is not a real translation.

## How does Codex use the toolkit?

Open the repository or an isolated project workspace, have Codex read `AGENTS.md`, and follow the linked [AI task contract](ai-task-contract.md). Other Agent products can manually load the [Skill](../skills/game-localization/SKILL.md); automatic discovery by every host is not verified.

## Why can it not identify and localize every game automatically?

Game formats, text encodings, control structures, compilers, fonts, and runtime behavior differ. The registry currently includes the conservative BGI adapter only; unknown formats need read-only analysis and a purpose-built tested adapter.

## How do I add a new game adapter?

Follow [adapter development](adapter-development.md) and begin with synthetic samples. Do not claim real game support until the adapter's declared scope, unknown-structure handling, and round-trip behavior have evidence.

## Why is human review required?

Structural checks cannot reliably assess meaning, voice, context, or ambiguity. Imported/provider output remains pending review, and the compiler gate rejects unreviewed text.

## Does a successful compile prove the game works?

No. Mock compilation tests workflow plumbing. Static format checks are limited, including MGSC v1 envelope checks; runtime acceptance requires testing the actual intended game build.

## What happens after failure, and how do I recover?

Inspect `localize status` and operation/transaction records first. Use the documented deployment recovery command only after inspecting transaction state and confirming the exact transaction. Unknown hashes, damaged backups, or ambiguous state require stopping for manual investigation.

## Will the toolkit modify my original Steam game?

Not by default. Keep original and Steam directories read-only. Deployment is disabled in new projects and requires explicit configuration, preview, exact confirmations, and the existing transaction safeguards.

## Can I use another AI Agent?

Yes, if it can read and write files and run commands within an authorized isolated workspace. Use the documented JSONL exchange when helpful. Follow `AGENTS.md` and the Skill; do not assume every host automatically discovers the Skill.
