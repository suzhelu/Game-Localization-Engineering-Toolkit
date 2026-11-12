# Game Localization Engineering Toolkit

Small Python utilities for localization workflows. This extraction contains path safety, SHA-256 hashing, tree snapshots, whitelist comparison, manifest generation and verification, a conservative adapter for a documented subset of BGI text-slot calls, and a fail-closed transaction primitive for explicitly allowlisted existing files. It does not contain game files, actual translations, or a BGI compiler.

## Requirements

- Python 3.10 or newer.
- Runtime code uses only the Python standard library. One synthetic .NET binding regression invokes host PowerShell when available and is skipped when PowerShell is unavailable.
- Building/installing a wheel uses the setuptools build backend declared in pyproject.toml.

Run tests from this directory:

    python -m unittest discover -s tests -v

Install for local development:

    python -m pip install -e .

Verify the synthetic example:

    python examples/verify_sample.py
    python examples/bgi/translate_synthetic.py
    python examples/deployment/transaction_demo.py
    python examples/overlay_package/build_synthetic_package.py
    python examples/translation/mock_batch.py
    python examples/compilation/mock_build.py

For BGI slot extraction and its strict writeback boundary, see [bgi-adapter.md](bgi-adapter.md).
For backup, journaling, crash recovery and limitations, see [deployment-transactions.md](deployment-transactions.md).
For explicit-list ZIP overlay packaging and release auditing, see [overlay-packages.md](overlay-packages.md). Packaging never installs files into a target directory.
For resumable provider-neutral translation batches and the required human-review gate, see [translation-workflow.md](translation-workflow.md). The sample provider is local and does not call an AI service.
For compiler command configuration, reviewed-source gates, MGSC envelope validation, evidence levels, and build publication limits, see [compilation-adapters.md](compilation-adapters.md). The example uses only a Mock Compiler; no real compiler is bundled or called.

## Basic use

```python
from pathlib import Path
from localization_toolkit import build_manifest, validate_manifest, write_manifest

payload_root = Path("./payload")
manifest = build_manifest(payload_root, ["files/example.bin"], metadata={"name": "demo"})
write_manifest("./manifest.json", manifest)
issues = validate_manifest(manifest, payload_root)
if issues:
    raise RuntimeError("Payload check failed: " + "; ".join(issues))
```

For an allowlisted tree comparison, call `snapshot_tree(root)` before and after a change, then pass both snapshots and explicit relative `authorized_paths` to `compare_snapshots`. Inspect `added`, `removed`, `changed`, and `outside_whitelist`; the helper reports differences and never applies changes.

Path comparison keys are case-insensitive to match Windows deployment semantics. On non-Windows hosts this is a deliberate logical comparison rule; actual filesystem case behavior can differ.

## Safety scope

The deployment transaction helper changes only existing files in an explicit allowlist. It refuses unknown hashes, corrupt backups, root mismatches, and outside-tree changes. Crash recovery is limited to the documented states and supported local filesystems; see its dedicated guide before use. It is not a turnkey installer and does not preserve every platform-specific metadata type. The BGI adapter only recognizes explicit schemas and does not compile scripts or validate game-build compatibility, language quality, or runtime layout. A successful manifest check proves listed bytes match recorded sizes and SHA-256 values; it does not prove translation correctness. Quiesce other writers during filesystem operations.

This is a local development toolkit, not a tagged release. Review the repository license scope and the documented deployment limits before integrating it into a project.
