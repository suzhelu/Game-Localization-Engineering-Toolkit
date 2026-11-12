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

## Excluded material

No original Japanese/English scripts, translated text, compiled game files, fonts, game resources, compiler assemblies, API keys, deployment manifests, or private backup records were copied. The game compiler and third-party assemblies are not included.

## Limitations and licensing

These checks do not prevent time-of-check/time-of-use races if another process changes the tree after validation. This package is not a deployment engine. Review source licensing and repository policy before public release; choose and add a license before treating the repository as an open-source release.
