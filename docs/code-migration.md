# Source and migration notes

## Audited source components

- Windows path normalization, reparse-point checks, tree snapshots, and whitelist comparisons were adapted from the reviewed `path_guard_batch25.ps1` asset.
- Streaming SHA-256 and file-tree fingerprint behavior were adapted from the reviewed `lib/project.mjs` asset.
- Manifest generation and validation consolidate patterns found in the reviewed project manifest and hash-audit helpers. The example manifest contains only synthetic data.
- The deployment transaction module refactors the actually used B25 backup, deploy, rollback, and path-guard behavior. It carries forward fixed whole-tree snapshots, explicit allowlists, per-target original/candidate digests, independently verified backup manifests, a durable per-file journal, atomic same-filesystem replacement, post-deploy tree comparison, and hash-classified recovery.
- B25 failure reviews informed regression coverage for the empty `.NET File.Replace` backup argument, mismatched snapshot/whitelist path roots, and rollback after partial replacement. The generic implementation uses standard-library `os.replace` with verified staged files and separate independent backups rather than copying the PowerShell call site.
- The overlay-package helper distills the actually used Steam bundle preparation, explicit payload mapping, source-hash audit, release manifest, SHA-256 sidecar, and final archive checks. It does not carry over the project's fixed file list, game-specific compatibility rules, translations, or installation behavior. Payload selection is explicit at the API boundary; package creation and installation remain separate operations.
- The translation workflow adapts behaviors from the actually used `cli.mjs`, `batch_cli.mjs` family, `lib/script.mjs`, and batch import/cache records: primary-language source with secondary reference; slot-ID keyed JSON batches through an explicit local provider command; source/glossary fingerprints; bounded batch/retry policies; resumable cache records; protected markers and required-term checks; and safe adapter-mediated candidate writing. Prompt contents and project acceptance wording were generalized, not copied verbatim. No provider endpoint or online API code was migrated.
- The 104-entry bilingual project glossary and 96-record compiled-script dictionary were audited as separate data types. Only a configurable glossary interface was implemented; neither project file or any term values were copied. The compiler dictionary is outside the translation module's scope.

The Python modules in this package are new, standard-library implementations of those reviewed behaviors. They are not copied source files and do not include project-specific logic.

## Refactoring performed

- Removed project roots, drive-specific settings, game names, source fingerprints, and translation content.
- Replaced project-specific deployment inputs with explicit caller-supplied roots and relative paths.
- Replaced project paths and authorization details with a Python API that requires caller-provided version tokens, fixed snapshots, and exact per-file hashes. Transaction evidence is stored outside both payload and target roots.
- Added real subprocess termination tests: child processes perform an actual atomic replacement and exit immediately without cleanup; another process resumes from the journal. A separate test kills a process during rollback and resumes it again.
- Added Windows case-folded comparison keys, parent-traversal rejection, reparse-point checks, streaming SHA-256, duplicate-target detection, and atomic manifest writes.
- Added synthetic temporary-directory tests for path handling, whitelist changes/additions/removals, hashes, missing files, changed content, and duplicate manifest entries.

## BGI text-slot adapter migration

The adapter in `src/localization_toolkit/adapters/bgi.py` is a Python adaptation of the actual-used `lib/script.mjs` lexer, call scanner, explicit text-call schemas, language-choice-array resolver, slot extraction, protected-markup checks, and conservative writeback validation. The source asset was identified as A01 in the project's local code-asset inventory and cross-checked against its parser tests. Its implementation behavior—not project fixtures or text literals—was carried over and isolated behind a BGI adapter. The local-only inventory and source tree are not part of this repository.

The generic slot/result types live in `src/localization_toolkit/models.py`. The adapter retains the reviewed project conventions for `OutputLine`, `OutputLineAll`, `SavePoint`, `ModAddArtset`, `ModAddBGMset`, `ModAddSEset`, `ModAddAudioset`, and the narrowly recognized `Select` language-array form. Calls beyond that proven subset require an explicit adapter schema. Unknown calls with string literals are reported and block writeback; no literal is guessed to be dialogue or a resource. New tests use synthetic strings only. The proprietary BGI compiler and all game TXT/MG files were excluded.

## Excluded material

No original Japanese/English scripts, translated text, compiled game files, fonts, game resources, compiler assemblies, API keys, deployment manifests, or private backup records were copied. The game compiler and third-party assemblies are not included.

## Limitations and licensing

The path and manifest helpers do not prevent time-of-check/time-of-use races by themselves. The deployment primitive adds repeated checks and durable recovery evidence but still requires a quiescent target and has the limitations documented in `deployment-transactions.md`. Review source licensing and repository policy before public release.
