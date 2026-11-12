# Source and migration notes

## Audited source components

- Windows path normalization, reparse-point checks, tree snapshots, and whitelist comparisons were adapted from the reviewed `path_guard_batch25.ps1` asset.
- Streaming SHA-256 and file-tree fingerprint behavior were adapted from the reviewed `lib/project.mjs` asset.
- Manifest generation and validation consolidate patterns found in the reviewed project manifest and hash-audit helpers. The example manifest contains only synthetic data.

The Python modules in this package are new, standard-library implementations of those reviewed behaviors. They are not copied source files and do not include project-specific logic.

## Refactoring performed

- Removed project roots, drive-specific settings, game names, source fingerprints, and translation content.
- Replaced project-specific deployment inputs with explicit caller-supplied roots and relative paths.
- Added Windows case-folded comparison keys, parent-traversal rejection, reparse-point checks, streaming SHA-256, duplicate-target detection, and atomic manifest writes.
- Added synthetic temporary-directory tests for path handling, whitelist changes/additions/removals, hashes, missing files, changed content, and duplicate manifest entries.

## BGI text-slot adapter migration

The adapter in `src/localization_toolkit/adapters/bgi.py` is a Python adaptation of the actual-used `lib/script.mjs` lexer, call scanner, explicit text-call schemas, language-choice-array resolver, slot extraction, protected-markup checks, and conservative writeback validation. The source asset was identified as A01 in the project's local code-asset inventory and cross-checked against its parser tests. Its implementation behavior—not project fixtures or text literals—was carried over and isolated behind a BGI adapter. The local-only inventory and source tree are not part of this repository.

The generic slot/result types live in `src/localization_toolkit/models.py`. The adapter retains the reviewed project conventions for `OutputLine`, `OutputLineAll`, `SavePoint`, `ModAddArtset`, `ModAddBGMset`, `ModAddSEset`, `ModAddAudioset`, and the narrowly recognized `Select` language-array form. Calls beyond that proven subset require an explicit adapter schema. Unknown calls with string literals are reported and block writeback; no literal is guessed to be dialogue or a resource. New tests use synthetic strings only. The proprietary BGI compiler and all game TXT/MG files were excluded.

## Excluded material

No original Japanese/English scripts, translated text, compiled game files, fonts, game resources, compiler assemblies, API keys, deployment manifests, or private backup records were copied. The game compiler and third-party assemblies are not included.

## Limitations and licensing

These checks do not prevent time-of-check/time-of-use races if another process changes the tree after validation. This package is not a deployment engine. Review source licensing and repository policy before public release; choose and add a license before treating the repository as an open-source release.
