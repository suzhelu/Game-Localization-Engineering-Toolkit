# Unknown-game analysis workflow

Use this protocol before selecting a parser or changing files. It is evidence collection, not automatic engine identification. Work on an isolated copy or metadata-only inventory; keep the original game directory read-only. Never execute an unknown game file to identify its format. Do not infer compatibility from an extension alone.

## Required questions

Record game title/build/platform, operating system/runtime, engine evidence, resource-tree shape, candidate text files and exact locations, encoding/container/control evidence, official language support and language switching, font/Chinese display evidence, compiler or rebuild requirements, integrity protections, reusable toolkit components, unresolved risks, and a smallest reversible feasibility test. Check legal tool access and project authorization before any read beyond permitted inventory. Do not bypass DRM, encryption, anti-cheat or platform controls.

Every claim receives one evidence level: `confirmed` means a cited file observation or completed test directly supports the claim; `inferred` means a hypothesis from indirect features and must name its basis; `unknown` means not examined or inconclusive. Include evidence source and reproducible observation. A filename, extension, marketing page or model guess alone is not confirmation. If signatures conflict, report ambiguity and stop format-specific write operations.

## Report schema

Schema v1 is checked by `localization_toolkit.game_analysis.validate_game_analysis_report`. It is a shape/invariant check, not a truth oracle. Required top-level keys: `schema_version`, `game`, `platform`, `engine`, `evidence`, `resource_candidates`, `encoding`, `localization_support`, `font_support`, `compiler_requirements`, `reusable_components`, `risks`, `unknowns`, `next_actions`, `stages`, and `runtime_evidence`. Evidence levels are restricted to confirmed/inferred/unknown. Stage keys are `stage_0` through `stage_10` and use the states in [the task contract](ai-task-contract.md). Stage 9 cannot be `passed` without concrete runtime evidence.

Copy the synthetic template in `examples/game-analysis/reports/` into the target project's private reports directory. Do not commit reports containing real game inventories, paths, scripts, extracted strings, or proprietary metadata. The validator does not check that a source citation is honest, and does not declare any engine supported.

## Investigation sequence

1. Identify exact game build, platform, OS and authorized workspace; hash files before inspection.
2. Inventory names, sizes and directory relationships without executing files. Note potentially protected or proprietary content.
3. Select a few candidates based on evidence, inspect bytes read-only, determine encoding/container boundaries and control markers. An adapter probe is advisory only.
4. Check official localization/language settings and fonts from user-accessible documentation and synthetic/minimal display tests where authorized.
5. Confirm available, legally usable compiler/rebuilder and verify its version/configuration. Mock compiler results only prove toolkit plumbing.
6. Pick one low-risk sample. Require strict extraction/writeback round trip and byte/structure comparison before considering scale-up.
7. Record what remains unknown, stop conditions and a reversible test plan. Require human approval before bulk extraction, translation or any deployment.

Possible outcomes: supported existing adapter, adapter extension needed, unsupported/unknown, or blocked by rights/protection/environment. Do not force every investigation into a positive compatibility result.
