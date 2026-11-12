# CLI project workflow

The `localize` command is a thin project runner over the toolkit's existing BGI adapter, translation workflow, compiler build API, manifest checks, overlay packager, and deployment transaction API. It does not implement a second parser, translator, compiler, or installer.

## Support boundary

This release supports the repository's conservative BGI text-slot adapter only. Translation uses a local Mock Provider; no online provider is wired in. Compilation defaults to a synthetic Mock Compiler. A caller may configure an authorized local compiler as an explicit executable plus argument arrays, but this CLI does not claim compatibility with an arbitrary game compiler. The `mgsc-v1` validator checks only the documented finite outer structure and treats payload bytes as opaque. Neither a successful compiler exit code nor static validation establishes that a game runs correctly.

All configured project paths are relative to the project directory and pass through the existing path-safety helper. Game-specific call schemas live under `game_specific.adapter_options`; common languages, batching, rules, compiler, validation, output, packaging, and deployment settings remain separate. No game-specific paths, character names, glossary terms, or story data are included in the template.

## Windows PowerShell quick start

From the repository root, create a dedicated environment with an installed Python 3.10+ interpreter. If `python` is available on `PATH`:

```powershell
python -m venv .venv
```

If Python is not on `PATH`, invoke the installed interpreter by its local executable path instead of `python`. Install the project and run its entry point from the environment explicitly; this avoids relying on activation policy or a globally installed `localize` command:

```powershell
& .\.venv\Scripts\python.exe -m pip install -e .
& .\.venv\Scripts\localize.exe init .\demo-project --name "Synthetic Demo"
& .\.venv\Scripts\localize.exe inspect --project .\demo-project
```

Edit `localize.json` and add explicit script input/output mappings. Put source files, optional reference files, glossary JSON, and rules inside the project directory. The initialized settings use BGI, Mock translation, Mock compilation, mandatory human approval, an explicit packaging list, and disabled deployment.

The normal sequence is:

```powershell
& .\.venv\Scripts\localize.exe extract --project .\demo-project
& .\.venv\Scripts\localize.exe translate --project .\demo-project
& .\.venv\Scripts\localize.exe review --project .\demo-project
& .\.venv\Scripts\localize.exe review --project .\demo-project --approve-slot "SLOT-ID" --reviewer "your-review-id"
& .\.venv\Scripts\localize.exe compile --project .\demo-project
& .\.venv\Scripts\localize.exe verify --project .\demo-project
& .\.venv\Scripts\localize.exe package --project .\demo-project
& .\.venv\Scripts\localize.exe verify --project .\demo-project
& .\.venv\Scripts\localize.exe status --project .\demo-project
```

`review` without an action lists current source, candidate, validation issues, and approval state. Approval/rejection is explicit per slot and requires a reviewer identifier. The Mock Provider never approves. Compilation fails closed until every current slot has an approved result, and it stages content through the existing adapter's safe writeback gate.

To run the complete synthetic demonstration without installing the CLI:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location).Path 'src')
& .\.venv\Scripts\python.exe .\examples\cli\end_to_end.py
```

The example creates its project in a temporary directory, explicitly approves its synthetic slot, builds with the Mock Compiler, verifies outputs, creates a synthetic overlay ZIP, and removes the temporary tree on exit.

## Commands

| Command | Behavior |
| --- | --- |
| `init [PATH] --name NAME` | Creates a project skeleton; refuses to overwrite `localize.json`. |
| `inspect --project PATH` | Validates config and reports configured input presence and fingerprint. |
| `extract --project PATH` | Extracts supported text slots and records source/structure fingerprints. Unknown or unresolved structures block downstream writing. |
| `translate --project PATH` | Runs resumable local Mock batches; records remain pending human review. |
| `review [--approve-slot ID\|--reject-slot ID] --reviewer ID` | Lists results or explicitly records one human decision. |
| `compile --project PATH` | Requires fresh extraction/translation and approval for every slot; uses the configured compiler adapter and finite validators. |
| `verify --project PATH` | Checks the current build manifest, hashes, and current package if present. |
| `package --project PATH` | Packages only the explicit `package.files` list from the latest build. It does not scan the build tree or install files. |
| `deploy --project PATH` | Without `--apply`, performs a read-only precheck and prints a plan. Apply requires an enabled explicit target, fixed snapshot, hashes, `--apply`, exact `--confirm-project`, and `--observed-version`; it calls only the existing transaction API. |
| `status --project PATH` | Shows stage freshness, recent operation records, transactions, failure details, and recommended next steps. |
| `recover --project PATH` | Inspects configured transaction state. Recovery requires `--apply --confirm-transaction TRANSACTION_ID` and is handled by the existing recovery API. |

An initialized project has deployment disabled and no target path. Do not point it at a production installation. The deployment option accepts an operator-supplied observed version token; this is an attestation, not an independently implemented game-version detector. Review the printed plan, fixed snapshot, exact targets, and transaction state before any write. Recovery is not attempted automatically when transaction state is ambiguous.

## State and freshness

The private `.localize/state.json` records operation IDs, command names, start/end times, status, configuration fingerprint, errors, summaries, stage fingerprints, artifacts, and transaction references. `.gitignore` excludes this local state. Translation records are stored separately through the existing resumable translation store.

Fingerprints include the project configuration, source and reference files, glossary, rules, and optional original baseline files. Translation task identity also includes its context and applicable rules. A changed source/configuration/glossary/rule makes prior stages stale; a changed compiler version/configuration makes build state stale. `status` reports the first recommended next command. Re-run upstream steps instead of manually editing state to force reuse.

Operation records are local audit aids, not signed or tamper-proof evidence. An abrupt process death may leave an operation marked `running`; inspect its files and rerun the failed stage only after checking that stage's documented state.

## Configuration

See [`../examples/cli/project-config.example.json`](../examples/cli/project-config.example.json). Important sections:

- `general`: adapter, source/target language, batch size, rule version, prompt template.
- `game_specific.adapter_options`: only explicit BGI schema information for a caller-owned game.
- `inputs.scripts`: explicit IDs, source paths, optional reference/baseline paths, and output paths.
- `translation`: currently `provider: "mock"` only.
- `compiler`: Mock Compiler or explicit local executable and argv lists, declared version, working directory, and timeout.
- `validation`: finite `mgsc-v1` validation and non-disableable human-review/structure gates.
- `paths`: glossary, rules, local state, build and release directories.
- `package.files`: explicit build-to-archive path mapping; no directory collection.
- `deployment`: disabled by default; explicit target root, transaction root, fixed snapshot, expected version, and before/candidate hashes required to enable.

The historical project's 104-entry translation glossary and 96-entry compiled-script dictionary serve different purposes. This CLI accepts a configurable translation glossary. It does not generate, edit, or infer any game's compiled dictionary from glossary data.

## Failure behavior and limitations

Commands return `0` on completed action, `2` on configuration, validation, or workflow failure, and argparse's standard nonzero result for invalid command syntax. A failed stage is recorded with an error when an operation has begun. No stage reports success merely because a provider returned data or a process exited zero: output presence, manifests/hashes, format validators, freshness, and review gates are checked separately.

This CLI does not provide online AI, semantic correctness proofs, automatic reviewer approval, game-specific dynamic-menu discovery, arbitrary-script parsing, full BGI compilation, or runtime testing. BGI unknown structures remain unresolved and block safe writeback. Real projects still need a verified adapter schema, licensed local compiler, legal baselines where byte-roundtrip evidence is required, project-specific human review, and in-game acceptance.

Package creation is separate from installation. Deployment is an explicit, separately authorized operation using the toolkit's existing transaction module; this CLI is not a general installer. ZIP checksum verification is not a signature, and operation logs are not cryptographic attestations.

## Architecture

`config.py` strictly validates the versioned project shape and safe project-relative paths. `state.py` atomically persists local stage and operation records. `workflow.py` composes the public APIs already present in `adapters.bgi`, `translation`, `compilation`, `manifest`, `packages`, `path_safety`, and `deployment`. `__main__.py` and the `localize` entry point provide JSON-oriented command output and concise errors. Synthetic regression tests and the end-to-end example use only generated text and temporary directories.
