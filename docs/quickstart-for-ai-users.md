# AI quick start (Windows PowerShell)

This toolkit is not one-click localization for arbitrary games. The CLI currently connects the conservative BGI text adapter, a local Mock translation provider, and a Mock Compiler by default. No online AI provider or real game compiler is bundled. Human review cannot be skipped. Static validation is not in-game acceptance.

## Install from a clean Windows environment

Install Git and Python 3.10+ if needed. The `py` launcher is optional. In PowerShell, clone the repository and enter it:

```powershell
git clone https://github.com/suzhelu/Game-Localization-Engineering-Toolkit.git
Set-Location Game-Localization-Engineering-Toolkit
```

Check whether a usable Python is on PATH. If `python` is absent or opens a Store page, set `$pythonExe` to the full path of an installed Python 3.10+ executable (for example, from the official python.org installer):

```powershell
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) { $pythonExe = $pythonCommand.Source } else { $pythonExe = Read-Host 'Enter the full path to Python 3.10+ python.exe' }
& $pythonExe --version
if ($LASTEXITCODE -ne 0) { throw 'Python could not be started' }
& $pythonExe -c "import sys; assert sys.version_info >= (3, 10), sys.version"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 or newer is required' }
& $pythonExe -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip setup failed' }
& .\.venv\Scripts\python.exe -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw 'Toolkit installation failed' }
& .\.venv\Scripts\localize.exe --help
if ($LASTEXITCODE -ne 0) { throw 'CLI executable is unavailable' }
```

This uses the virtual environment's executables directly and does not require changing PowerShell execution policy or activating the environment. Run tests from the repository root:

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
```

## Start Codex or another Agent

Open the cloned repository in the Codex desktop application and start a chat there. Ask the Agent to read `AGENTS.md` and `skills/game-localization/SKILL.md`. The Skill is a manually loadable entry for other Agent products; automatic discovery across hosts has not been tested. Give the Agent an explicit, read-only analysis scope and a separate workspace path for any legally held test-copy files. Never point it at a Steam/original installation for write operations.

For other Agent products, provide these repository-relative instruction paths directly. Do not place personal absolute paths, game files, or secrets in the repository.

## Run the complete synthetic workflow

The example exercises actual existing CLI commands with a generated fixture, a prewritten synthetic translation, and Mock compilation. It deliberately simulates the review action only for test plumbing; it is not a real reviewer approval.

```powershell
$demo = Join-Path $env:TEMP ("localize-agent-workflow-" + [guid]::NewGuid().ToString('N'))
& .\.venv\Scripts\python.exe examples/agent-workflow/end_to_end.py --workspace $demo
if ($LASTEXITCODE -ne 0) { throw 'Synthetic workflow failed' }
Get-Content (Join-Path $demo 'synthetic-project\reports\game-analysis.md')
Get-Content (Join-Path $demo 'synthetic-project\reports\final-report.json')
```

The report and overlay package remain under the new temporary demo directory. It checks that compilation is blocked while translations are pending review, then demonstrates the test-only review gate, Mock Compiler, static checks, and package manifest. It leaves Stage 9 as `runtime_unverified` and deployment disabled. Review the detailed [synthetic workflow notes](../examples/agent-workflow/README.md).

To list available adapters and inspect one input without writing it, use `localize adapters` and `localize probe PATH --adapter bgi`. A probe is syntax evidence for that input only; it is not engine detection or permission to write back.

Run the established synthetic examples and the new end-to-end workflow with the virtual-environment interpreter:

```powershell
& .\.venv\Scripts\python.exe examples/verify_sample.py
& .\.venv\Scripts\python.exe examples/bgi/translate_synthetic.py
& .\.venv\Scripts\python.exe examples/deployment/transaction_demo.py
& .\.venv\Scripts\python.exe examples/overlay_package/build_synthetic_package.py
& .\.venv\Scripts\python.exe examples/translation/mock_batch.py
& .\.venv\Scripts\python.exe examples/compilation/mock_build.py
& .\.venv\Scripts\python.exe examples/cli/end_to_end.py
& .\.venv\Scripts\python.exe examples/agent-translation/exchange_demo.py
& .\.venv\Scripts\python.exe examples/game-analysis/analysis_demo.py
& .\.venv\Scripts\python.exe examples/agent-workflow/end_to_end.py
```

The BGI example writes an ignored synthetic output beside itself; others use temporary directories by default. The new workflow uses a prewritten synthetic translation and a test-only approval; neither is a real AI translation nor a human approval. None of these examples establishes real game acceptance.

## Real project boundaries

Before touching a new title, follow the [game analysis workflow](game-analysis-workflow.md) and [11-stage task contract](ai-task-contract.md). Do not infer engine support from an extension. Only the registered BGI adapter is implemented for real script syntax; unsupported structures must remain untouched. Mock translation is not real translation; imported text stays pending human review. A real compiler must be legally held and explicitly configured by its owner. MGSC v1 checking is limited to verified envelope/table boundaries. Packaging uses an explicit file list and does not install files. Deployment is a separate, explicitly authorized operation with target checks and recovery safeguards. Stage 9 requires actual in-game observation; no synthetic example proves compatibility.

For command details see [CLI workflow](cli-workflow.md), [compatibility](compatibility.md), and [FAQ](faq.md).

## Basic CLI commands

From a configured isolated project, CLI examples include:

```powershell
localize init ./work/demo --name SyntheticDemo
localize inspect --project ./work/demo
localize extract --project ./work/demo
localize status --project ./work/demo
```

`init` creates a project skeleton; the remaining commands require a valid `localize.json` with explicit inputs where applicable. These examples do not imply that the tool detects arbitrary game engines.
