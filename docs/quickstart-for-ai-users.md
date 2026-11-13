# AI user quick start (Windows PowerShell)

This toolkit is not one-click localization for arbitrary games. Only the BGI text adapter and local Mock translation provider are currently wired into the CLI. Mock translation is not real translation. Human review cannot be skipped. Static validation is not in-game acceptance.

## Install in a separate workspace

Install Python 3.10+ from [python.org](https://www.python.org/downloads/windows/) if absent. Open PowerShell in a clone of this repository, outside any game installation. Check Python with `python --version`; a Microsoft Store alias is not proof of a usable interpreter. The `py` launcher is optional. If python is unavailable, select the installed executable explicitly:

```powershell
$pythonExe = Read-Host 'Full path to an installed Python 3.10+ python.exe'
& $pythonExe -c "import sys; assert sys.version_info >= (3, 10)"
if ($LASTEXITCODE -ne 0) { throw 'A working Python 3.10+ is required' }
& $pythonExe -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
& .\.venv\Scripts\python.exe -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw 'Installation failed' }
& .\.venv\Scripts\localize.exe --help
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Explicit virtual-environment executable paths avoid activation-policy changes. For automatic interpreter probing and the optional launcher fallback, use the existing [installation guide](python-toolkit-quickstart.md). Check each native command's exit code; PowerShell does not automatically stop on nonzero exits.

## Start an Agent with bounded access

Open the repository folder in your installed Codex application and start a chat there. Ask it to read [AGENTS.md](../AGENTS.md) before acting. Codex documents repository instructions in its [official AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md). Installing/authenticating Codex is separate from installing this Python package. Other agents can manually load [the skill](../skills/game-localization/SKILL.md); automatic discovery across hosts has not been tested.

Give the Agent the private path of a legally held **test copy**, requested source/target languages, and an explicit read-only scope. Do not put your private path or game contents into this repository. Original game directories must remain read-only. An example request is: "Read AGENTS.md. Analyze only the explicitly named scripts in my test copy; do not modify them. Report format evidence, unsupported structures, hashes and approval requirements in a separate project directory. Do not assume BGI or translate yet."

Unknown formats require read-only analysis first. `localize inspect` is not engine detection and does not generate a full game analysis report automatically.

## First synthetic project and analysis report

```powershell
& .\.venv\Scripts\localize.exe init ./work/agent-demo --name AgentDemo
```

Use the generated `localize.json` as the schema reference. Keep deployment disabled and compiler kind mock. Add only an authorized source copied into the separate project's input folder, never the game directory itself. For the synthetic tutorial use `OutputLineAll("Synthetic source", "Synthetic reference", 0);` in `input/demo.txt`, set inputs.scripts to `[{"id":"demo","source":"input/demo.txt","output":"scripts/demo.mg"}]`, and package.files to `[{"build_path":"scripts/demo.mg","archive_path":"scripts/demo.mg"}]`. Preserve all other generated keys. Save JSON/source as UTF-8 (without BOM for JSON); do not rely on Windows PowerShell's default text encoding.

```powershell
$report = & .\.venv\Scripts\localize.exe inspect --project ./work/agent-demo
if ($LASTEXITCODE -ne 0) { throw 'Inspect failed' }
$report | Out-File ./work/agent-demo/inspect-report.json -Encoding utf8
& .\.venv\Scripts\localize.exe extract --project ./work/agent-demo
& .\.venv\Scripts\localize.exe status --project ./work/agent-demo
```

Out-File may add a UTF-8 BOM on Windows PowerShell 5.1; this report is evidence, not CLI configuration input. Ask the Agent to combine it with extraction issues into an analysis report using [the report checklist](../skills/game-localization/references/analysis.md). Record actual counts, hashes, unresolved syntax and next decisions; never invent reachability or successful runtime tests. Stop before real translation until format and scope are confirmed.

## Continue only within supported scope

Follow [the command guide](cli-workflow.md) for translate, review, compile, verify and package. Mock produces test output; a human must explicitly review real translation before safe writeback. Never fabricate reviewer identity. A real compiler must be legally held, configured and version-checked by its owner. MGSC v1 validation checks only the envelope, not its payload or game behavior. Packaging uses explicit files and does not install them. Deployment and recovery need separate authorization and confirmations; leave them disabled in this quick start.

Run all seven existing synthetic examples with the venv interpreter:

```powershell
& .\.venv\Scripts\python.exe examples/verify_sample.py
& .\.venv\Scripts\python.exe examples/bgi/translate_synthetic.py
& .\.venv\Scripts\python.exe examples/deployment/transaction_demo.py
& .\.venv\Scripts\python.exe examples/overlay_package/build_synthetic_package.py
& .\.venv\Scripts\python.exe examples/translation/mock_batch.py
& .\.venv\Scripts\python.exe examples/compilation/mock_build.py
& .\.venv\Scripts\python.exe examples/cli/end_to_end.py
```

The BGI example writes an ignored synthetic output beside itself; most others use temporary directories. The end-to-end example supplies fixture approvals only. None establishes real game acceptance. Preserve game-resource, secret, third-party-license and DRM boundaries from AGENTS.md.