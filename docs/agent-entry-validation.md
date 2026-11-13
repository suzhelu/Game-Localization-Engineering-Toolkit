# Batch A validation and publication review

Date: 2026-11-13. Platform: Windows PowerShell; Python 3.10.11. Repository baseline: `a898251a0a6e4498df4850267bbef554ab8f177e`. Fetched origin/main and peeled v0.1.0 matched that baseline. Initial working tree was clean. Changes are isolated on local branch `docs/agent-entry-batch-a`; no push, tag or release is part of this batch.

## Executed validation

From the repository root, with `PYTHONPATH=src` and an explicitly selected Python 3.10 interpreter (no required py launcher):

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -q
```

The actual executable was the installed Python interpreter selected by full path; its private machine path is intentionally omitted. Baseline run: 179 tests, 12.444 seconds, OK. Updated run: 187 tests, 11.336 seconds, OK (0 failures/errors). Eight new tests in `tests/test_agent_docs.py` cover required files, a deliberately restricted valid YAML scalar frontmatter, local reference resolution, documented CLI commands against the real argparse parser, explicit safety clauses, limitations, seven real example paths and separation of proposals from implemented behavior. These tests detect contract drift; keyword checks are not proof that all prose is semantically correct, and the frontmatter test is not a general YAML parser.

All seven existing examples executed with the same interpreter and PYTHONPATH; each returned exit code 0:

| Command argument | Result |
|---|---|
| examples/verify_sample.py | passed |
| examples/bgi/translate_synthetic.py | passed |
| examples/deployment/transaction_demo.py | passed |
| examples/overlay_package/build_synthetic_package.py | passed |
| examples/translation/mock_batch.py | passed |
| examples/compilation/mock_build.py | passed |
| examples/cli/end_to_end.py | passed |

The final example executes initialization, inspection, extraction, Mock translation, explicit synthetic fixture review, compilation, verification, packaging and status. Deployment's example deploys/restores synthetic temporary files. No real game, online AI or game compiler was used. The BGI example's generated synthetic output is ignored by Git and is not part of this commit. New installation instructions were compared with the existing installation guide and parser; this batch did not repeat the earlier independent clean-machine install certification or test every Codex host's instruction discovery.

## Public-content and change review

Scanned 78 tracked or nonignored candidate files before final commit: no detected personal user/test-directory absolute path, API token/private-key pattern, binary/non-UTF8 file, or prohibited MG/EXE/DLL/archive/save extension. Inspected changed documents and the synthetic-only test. No game scripts, real translations, glossary entries or third-party compiler were copied. Synthetic examples already present remain synthetic; historical project names in provenance documentation are not game content. Pattern scanning is heuristic and cannot establish universal rights or secret absence.

Parsed all 30 Python module files for syntax/inventory. `git diff --name-only -- src pyproject.toml` was empty: runtime code and package interfaces are unchanged. `git diff --check` passed. The only existing-document correction beyond the README entry replaces the obsolete claim that no tagged release exists with the actual v0.1.0 preview status.

## Remaining boundaries

No new provider, adapter registry, storage locking, reviewer authentication, format detection, CLI error schema or game test capability was implemented. See [the audit](ai-readiness-audit.md) for evidence and Batch B proposals. Human approval remains required; static evidence is not runtime acceptance. This batch is ready for user review as a local documentation/test commit, not an authorization to publish or deploy.