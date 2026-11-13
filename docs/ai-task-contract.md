# AI localization task contract

This contract coordinates an Agent and a human operator. It is not an autonomous one-click translator. Record each stage's inputs, preconditions, operations, outputs, evidence, result state, failures, and whether human confirmation was required. Preserve checkpoints and stop safely when a gate fails.

## Stage states

Use: `not_started`, `in_progress`, `passed`, `failed`, `blocked`, `skipped`, `runtime_unverified`. A skipped stage needs a reason. `passed` requires stage-specific evidence; a Mock result is evidence only of the Mock path. Stage 9 stays `runtime_unverified` until a person observes the actual game running and records build, environment, procedure, outcome and date. Do not infer runtime success from compilation or static validation.

## Stages 0–10

| Stage | Inputs / preconditions | Allowed work and outputs | Pass evidence / failure and stop rule | Human confirmation / proof boundary |
|---|---|---|---|---|
| 0 Environment check | User request, repository revision, OS, permissions | Record branch/HEAD, clean state, Python/tools and authorized workspace | Reproducible environment record; stop on dirty/unexpected workspace or unauthorized path | Confirm target copy and scope; CLI can report versions/status, operator confirms authorization |
| 1 Game analysis | Exact build/platform and read-only inventory permission | Follow [analysis workflow](game-analysis-workflow.md); produce evidence report | Validate report schema; unknowns remain explicit; stop on protected/ambiguous format | Human decides investigation scope; static evidence only |
| 2 Minimal feasibility | Analysis identifies a lawful candidate and reversible sample | Test one synthetic or explicitly authorized sample, parser round-trip and control preservation | Compare source/output structure and hashes; stop on unknown structures or mismatch | Human approves sample access; CLI can record checks, not certify compatibility |
| 3 Chinese display | Authorized disposable runtime/sample and chosen font route | Test smallest display surface and capture observed result | Record glyphs, wrapping, encoding; stop if unreadable or risk to originals | Requires real runtime observation; font file presence alone is not proof |
| 4 Bulk extraction | Adapter and sample checks passed; explicit input list | Extract to isolated workspace with IDs, hashes and diagnostics | IDs/counts stable, unresolved items accounted for; stop on parser warnings | Human approves scale-up; CLI verifies its parser evidence |
| 5 AI translation | Current task fingerprints, rules/glossary, provider policy | Mock or explicitly configured provider; exchange JSONL when useful | Record provider, task IDs, errors and freshness; Mock is not a translation result | Online calls require separate user authorization; semantic meaning cannot be CLI-proven |
| 6 QA and human review | Current imported/provider records | Structural QA, consistency checks, human approve/reject with identity | Only current valid approved slots pass; unresolved items block | Real translation requires human review; CLI checks record state, not reviewer judgment quality |
| 7 Compile/rebuild | Reviewed current text and a legally usable configured compiler | Build only in isolated output paths; record compiler version/logs | Exit code plus output existence/hash/format evidence; failure stops downstream | Human supplies/authorizes compiler; Mock success is not real compiler evidence |
| 8 Static verification | Build artifacts and pinned inputs | Check structures, manifests, hashes and comparison evidence | All required static gates pass; any mismatch blocks package/deploy | CLI can prove only implemented checks; not gameplay behavior |
| 9 In-game acceptance | Static gates passed and explicit test copy authorized | Operator runs game normally; record scenario and observations | `passed` only with concrete runtime evidence; otherwise `runtime_unverified` | Always requires actual runtime evidence; no automation claim from static checks |
| 10 Package/release | Approved scope, verified outputs, rights review | Package explicit allowlisted files and audit manifest/checksums | Package audit passes; any provenance/rights uncertainty blocks release | Human confirms distribution scope and release; package tool does not grant rights |

## Non-negotiable gates

- Unknown formats do not enter bulk writeback. Probe is read-only evidence, not authorization.
- No unreviewed or stale translation enters compilation. Do not bypass the reviewed adapter write path.
- Mock translation is not real translation; Mock compiler is not a real build.
- Static validation is not runtime acceptance. Without runtime evidence Stage 9 remains `runtime_unverified`.
- Compiler failure blocks deployment; static failure blocks compatibility or release claims.
- Any real game-directory write requires explicit user authorization for the exact target and the existing backup/transaction safeguards. Default original and Steam directories remain read-only.
- The user may stop at any stage. Preserve reports, hashes, logs and recovery data; never manufacture a passed state.

The current CLI covers project extraction, Mock translation, review, compile, verify, package, status, deployment preview and guarded recovery for configured adapters. It does not automatically analyze arbitrary games, identify every engine, invoke online AI, or prove game compatibility. Consult each tool's module guide for its bounded checks.
