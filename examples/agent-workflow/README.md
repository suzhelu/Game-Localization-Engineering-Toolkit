# Synthetic Agent workflow

Run from the repository root after installing the package:

```powershell
python examples/agent-workflow/end_to_end.py
```

The demonstration creates a temporary project and removes it when complete. To retain reports and package artifacts for inspection, provide a new or empty directory under the operating system temporary directory; paths resolving outside that temporary root are rejected:

```powershell
python examples/agent-workflow/end_to_end.py --workspace "$env:TEMP\localize-agent-workflow-demo"
```

It invokes the existing `localize` CLI entry point from `init` through `status`, uses the registered BGI adapter on a synthetic line, exports/imports a prewritten synthetic translation, checks that compile is blocked before review, and then uses an explicitly labeled test-only approval before the Mock Compiler and package workflow. The generated analysis keeps the engine unknown and Stage 9 `runtime_unverified`.

The reviewer marker is test scaffolding, not a human approval. No online model, real compiler, real game resource, game directory, or deployment target is used. The project retains `.localize/state.json`, generated analysis and final reports, a package manifest and checksums, and `reports/recovery-notes.md` when `--workspace` is supplied. Mock compilation and static checks do not establish runtime compatibility.
