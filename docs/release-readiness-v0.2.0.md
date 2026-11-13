# v0.2.0 Preview Candidate — Release Readiness Audit

**Audit result: READY for a bounded preview release, subject to the support limits below.** This means the documented synthetic/tooling workflows are suitable for preview distribution. It is not a claim of arbitrary-game support, security certification, or real-game runtime compatibility. No tag, GitHub Release, or remote push was created as part of this audit.

## Candidate scope

- Candidate package version: `0.2.0rc1` (PEP 440); user-facing preview label may be `v0.2.0-rc`.
- Scope: batches A–E, with existing interfaces retained: Agent entry and analysis protocol; adapter registry and conservative BGI text-slot adapter; offline translation exchange and review gates; compiler adapter and limited static checks; deployment/recovery transactions; explicit overlay packaging; unified CLI and synthetic end-to-end workflow.
- Excluded: online AI service, bundled third-party compiler, game resources, ready-made game patch, automatic support for arbitrary engines, and any assurance that a game will run correctly.

## Baseline and fresh-environment evidence

Audit began at Batch E commit `8d2ecf9fcdebceb84099078276161b2eb628b9dd`, on a clean `feat/agent-e2e-batch-e` worktree. Work was isolated on local branch `release/ai-native-v0.2.0-rc`.

A new Python 3.10.11 virtual environment was created under a unique system temporary directory on the same Windows machine. The repository was installed editable from that checkout; no previously installed toolkit package was used. This is a fresh environment reproduction on the current Windows host, **not** a clean Windows device, a clean OS image, or a separately provisioned machine certification.

Commands executed in the fresh venv (repository root; paths shown relative to the checkout):

```powershell
& .venv\Scripts\python.exe -m pip install -e .
& .venv\Scripts\localize.exe --help
& .venv\Scripts\localize.exe adapters
& .venv\Scripts\python.exe -m unittest discover -s tests -q
& .venv\Scripts\python.exe -m unittest examples.adapters.template.test_adapter -q
```

Final rerun: main suite **240 tests passed** (19.448 seconds), adapter-template suite **5 tests passed**, and all **10 synthetic examples exited 0**. Python bytecode compilation passed. The candidate was reinstalled editable in the same isolated venv and reported package version `0.2.0rc1`; `localize --help` and `localize adapters` both exited 0, reporting only the built-in BGI adapter and no discovered external plugins.

The ten examples are: `examples/verify_sample.py`, `examples/bgi/translate_synthetic.py`, `examples/deployment/transaction_demo.py`, `examples/overlay_package/build_synthetic_package.py`, `examples/translation/mock_batch.py`, `examples/compilation/mock_build.py`, `examples/cli/end_to_end.py`, `examples/agent-translation/exchange_demo.py`, `examples/game-analysis/analysis_demo.py`, and `examples/agent-workflow/end_to_end.py`. They use synthetic fixtures and temporary output. The Agent workflow uses a test-only review action; it is not real human approval. Stage 9 remains `runtime_unverified`.

## Batch A–E audit matrix

Statuses: **PASS** = directly checked in this audit by implementation review and/or named automated evidence; **FAIL** = a known requirement is violated; **NOT_TESTED** = no sufficient evidence in this audit; **NOT_APPLICABLE** = outside this candidate's promised scope. `NOT_TESTED` is never counted as `PASS`.

| Area | Status | Evidence and boundary |
|---|---|---|
| A — Agent entry, safety instructions, local links, command references | PASS | `AGENTS.md`, `skills/game-localization/SKILL.md`, Quick Start and docs-link/CLI contract tests. Other Agent hosts' automatic Skill discovery is not assumed. |
| B — Adapter registry, unknown-ID rejection, read-only probe, ambiguity | PASS | Registry/probe unit tests and five adapter-template tests; template is synthetic. Loading a selected third-party Python plugin executes its code in-process and requires operator trust. |
| B — BGI behavior against a real game's complete source corpus | NOT_TESTED | The BGI adapter is a conservative parser subset. This audit did not re-run a real game corpus or compiler. Unknown/dynamic structures must block writeback. |
| C — JSONL schema, source/task freshness, batch validation, pending-review gate | PASS | Translation exchange tests and synthetic exchange example. Imported results cannot self-approve. Structural checks do not prove translation meaning. |
| C — cooperating local process locking and conflict handling | PASS | TranslationStore uses a shared OS lock and optimistic fingerprint; Batch C tests include process contention and failure cases. Network shares and writers bypassing the store are not covered. |
| D — analysis evidence levels, report invariants, 11-stage contract | PASS | Schema and documentation contract tests plus synthetic analysis example. Schema validation checks shape, not whether evidence claims are true. |
| E — end-to-end workflow invokes existing CLI paths, synthetic isolation, review gate, package audit | PASS | E2E test/example use generated temporary project data and existing CLI entry points; test reviewer is simulated; deployment stays disabled. |
| Compiler workflow and Mock execution | PASS | Unit tests and synthetic compiler example. A Mock proves plumbing only. |
| MGSC v1 validation and real compiler interoperability | NOT_TESTED | Validator checks only the documented envelope/table/bounds; payload remains opaque. No real compiler or real game file was used. |
| Deployment/recovery against a real game tree | NOT_TESTED | Transaction tests use temporary synthetic trees. No real game directory was read or modified for deployment. |
| Actual target game runtime, fonts, scene flow, saves, or Steam launch | NOT_TESTED | No game was launched. Static evidence cannot satisfy Stage 9. |
| Online AI provider, arbitrary engine detection, arbitrary compiler support | NOT_APPLICABLE | These are explicitly excluded from the product scope; no online provider is implemented. |
| PyPI publishing and third-party redistribution review | NOT_APPLICABLE | No package upload or release asset publication was performed. A later release owner must review licensing and distribution scope. |

## Security and publication review

**PASS for the bounded repository preview after the final scans below; not a security certification.** Source review and targeted tests cover root-contained path handling, reparse-point checks where supported, ZIP traversal/duplicate names, explicit package allowlists, hashes, reviewed-source gates, compiler argv invocation without a shell, transaction snapshots/backups/journals, lock conflicts, and fail-closed recovery for unknown state. `git diff --check`, document-link validation, CLI help, bytecode syntax checks, and a content inventory were included in the final run.

Residual risks:

- **Medium:** third-party adapter plugins execute Python code in-process when explicitly loaded. Install only trusted plugins.
- **Medium:** the reviewer identity recorded by the CLI is an audit field, not authenticated identity; operators must not let an Agent impersonate a human approver.
- **Medium:** TranslationStore locking protects cooperating toolkit writers on supported local filesystems only. External writers, network-share behavior, abrupt hardware/storage failure, and durability across every power-loss scenario are not guaranteed.
- **Low/Medium:** hashes detect content changes but are not signatures or provenance authentication. Release consumers should obtain the package from a trusted channel and verify checksums where supplied.
- **Medium:** BGI, compiler, layout, and runtime compatibility are limited to observed implementation evidence; unknown structures stop safe writeback rather than being guessed.

No game scripts, complete translations, MG files, game binaries, compiler binaries, secrets, or personal absolute paths are required by the candidate. Automated secret/path scans are heuristic and are supplemented by review of changed files; they cannot prove the absence of every sensitive datum or resolve third-party licensing questions universally.

## Documentation corrections in this audit

The Batch A readiness audit is now explicitly labeled a historical snapshot so its then-correct claims (for example, no adapter registry or no TranslationStore lock) are not mistaken for current behavior. The adapter-development and translation-workflow guides now describe the current advisory OS-lock/fingerprint behavior and its limits. The package version is set to `0.2.0rc1`; `CHANGELOG.md` records candidate scope and exclusions.

## Final verification record

| Check | Result |
|---|---|
| Full repository unittest suite | **PASS — 240/240** |
| Adapter template tests | **PASS — 5/5** |
| Ten synthetic examples | **PASS — 10/10, exit code 0** |
| CLI install/help/adapter listing | **PASS** in the isolated Python 3.10.11 venv |
| Python syntax, docs links, `git diff --check` | **PASS**; doc-link checks are part of the 240-test suite |
| Public-content scan and changed-file review | **PASS with heuristic limits stated above** |
| New Windows device / clean OS image | **NOT_TESTED** |
| Real game compile and runtime | **NOT_TESTED** |

## Release decision

**READY for a v0.2.0 preview that preserves the stated boundaries.** Do not advertise universal game support, online AI translation, real compiler success, or in-game acceptance. Before any actual release, the release owner should review the final commit, licensing/distribution scope and generated release notes. Real-game users still need a supported or separately developed adapter, authorized tools, isolated files, human translation review, and runtime testing of the exact game build.
