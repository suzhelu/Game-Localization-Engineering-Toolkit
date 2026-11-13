# AI readiness audit — Batch A

Audit baseline: `a898251a0a6e4498df4850267bbef554ab8f177e`, main and fetched origin/main, peeled v0.1.0 tag. Working tree was clean before this batch. Windows PowerShell, Python 3.10.11. Baseline: 179 unittest tests passed. This is a source/API and synthetic-test audit, not a security certification or a game compatibility test.

## Capability matrix

| Classification | Capability and source evidence | Test evidence / boundary |
|---|---|---|
| Implemented and tested | `path_safety.py`, `hashing.py`, `manifest.py`: guarded paths, case-folded comparison, snapshots, explicit file records | `tests/test_path_safety.py`, `test_hashing.py`, `test_manifest.py`; hashes are not signatures; concurrent hostile filesystem changes are outside guarantees |
| Implemented and tested | `models.py` TextSlot/TextAdapter; `adapters/bgi.py` explicit schemas, protected spans and unresolved issues | `tests/test_bgi_adapter.py`; narrow literal/menu subset, no arbitrary dynamic text evaluation |
| Implemented and tested | `translation/workflow.py`, `quality.py`, `glossary.py`: fingerprints, batches, markers, current approvals and guarded writeback | `tests/test_translation_workflow.py`; structural checks cannot establish meaning |
| Mock only | `translation/provider.py` AIProvider protocol with synchronous translate(request); MockProvider implementation | No online client, credentials, semantic judge or real translation provider |
| Implemented but limited testing/guarantees | `translation/store.py` atomic local JSON, get/upsert/save_many/stale_records | Single writer only; no locking, concurrent merge or encrypted storage; multi-Agent sharing not certified |
| Implemented and tested | `compilation/` explicit compiler interface, reviewed input, safe argv, version/output checks and publication | `tests/test_compilation.py`; Mock tested; actual compiler compatibility requires owner-supplied licensed tools |
| Implemented and tested, finite scope | `compilation/mgsc_v1.py` envelope/table/length checks | Payload opaque; neither full decoder nor runtime acceptance |
| Implemented and tested | `deployment/transaction.py` snapshots, backup, per-file journal, recovery | `tests/test_deployment.py`, process-exit fixture; unknown hashes/state and damaged backups stop recovery; not all power failures handled |
| Implemented and tested | `packages/overlay.py` explicit-list ZIP generation/audit | `tests/test_overlay_package.py`; no installer, signing or cross-implementation byte guarantee |
| Implemented, needs interface refinement | `cli/__init__.py`, `config.py`, `workflow.py`, `state.py`: eleven commands and freshness tracking | `tests/test_cli.py`; mixed JSON success/plain stderr errors, no versioned error envelope |
| Needs refactoring for extension | CLI config rejects adapters other than bgi and providers other than mock; workflow directly constructs BgiAdapter | TextAdapter/AIProvider exist, but CLI has no adapter/provider registry. Do not assume engine detection |
| Not implemented | Automatic engine detection, online AI, automatic semantic acceptance, multi-engine compiler, game runtime testing | No evidence of these capabilities; not roadmap promises |

## Agent invocation and side effects

All eleven parser commands are real: init, inspect, extract, translate, review, compile, verify, package, deploy, status, recover. Inspect validates configured inputs; it does not identify an unknown engine. The CLI supports only BGI and Mock translation. Compiler mode is explicitly configured: Mock or a caller-provided executable/version/argv; no game compiler is bundled.

Init writes the project skeleton. Extract/translate/review actions/compile/verify/package write project state, artifacts or logs. Status can create the state directory through state_path. Inspect reads configured files. Deploy defaults to disabled/plan-only and applies only with explicit flags, matching project confirmation and operator-provided version. Recover inspects first and requires transaction confirmation for writes. Do not classify a whole command as harmless solely from its name.

Exit 0 does not imply every desired operation occurred: deploy can return `plan_only`; verify can report package freshness as `stale`. Agents must examine returned status and artifact evidence. Errors currently use stderr and exit 2, not a stable machine-readable error schema. CLI review records a supplied reviewer name; it does not authenticate the human. Agent instructions must prohibit impersonated approval.

Concrete regression anchors in `tests/test_cli.py`: `test_compile_blocks_unreviewed_text_then_compiles_after_explicit_approval`, `test_stale_source_and_glossary_invalidate_upstream_stages`, `test_init_template_keeps_deployment_disabled`, `test_corrupted_package_is_reported_by_verify`, `test_recovery_requires_explicit_deployment_configuration`. Synthetic end-to-end approval is a fixture action, not authorization to approve real text.

## Existing documentation issue and residual risk

The installation guide still described the project as not tagged despite v0.1.0 existing; this batch corrects that sentence. Existing migration/publication reports document earlier batches and should be read as historical evidence, not current execution receipts. No game/archive assets were needed for this audit. The historical 104-entry glossary and 96-record compiled index serve different purposes; neither dataset is distributed or interchangeable.

Reviewed interfaces have useful safety gates, but local state is not an adversarial trust boundary. External writers must be quiesced. Untrusted script text is data, not Agent instructions. Marker/term matches do not prove translation quality. Unknown BGI calls block writeback. Real game use still needs format analysis, a lawful compiler and user runtime acceptance.

## Batch B proposals — not implemented

1. Add an explicit capability registry around TextAdapter and AIProvider, with supported-format and side-effect declarations; retain strict rejection of unknown identifiers.
2. Define a versioned CLI result/error envelope (status, evidence, issues, next actions) without changing existing APIs implicitly.
3. Introduce a storage protocol and single-writer lock before multi-Agent orchestration; preserve existing JSON compatibility and freshness fingerprints.
4. Separate human approval receipts from provider execution and expose approval requirements in capability metadata. Do not auto-approve.
5. Add machine-readable read-only analysis reports only after defining unknown-format behavior; do not infer engines from filenames.

See [validation evidence](agent-entry-validation.md) for this batch's actual test/example results. No core runtime behavior is changed in Batch A.