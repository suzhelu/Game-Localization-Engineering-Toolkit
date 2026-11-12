# Game Localization Engineering Toolkit

Small Python utilities for localization workflows. This extraction contains path safety, SHA-256 hashing, tree snapshots, whitelist comparison, manifest generation and verification, and a conservative adapter for a documented subset of BGI text-slot calls. It does not contain game files, actual translations, a BGI compiler, deployment, or rollback code.

## Requirements

- Python 3.10 or newer.
- Runtime and tests use only the Python standard library.
- Building/installing a wheel uses the setuptools build backend declared in pyproject.toml.

Run tests from this directory:

    python -m unittest discover -s tests -v

Install for local development:

    python -m pip install -e .

Verify the synthetic example:

    python examples/verify_sample.py
    python examples/bgi/translate_synthetic.py

For BGI slot extraction and its strict writeback boundary, see [bgi-adapter.md](bgi-adapter.md).

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

The path and manifest helpers inspect files but do not deploy, delete, or restore them. The BGI adapter only recognizes explicit schemas and does not compile scripts or validate game-build compatibility, language quality, or runtime layout. A successful manifest check proves listed bytes match recorded sizes and SHA-256 values; it does not prove translation correctness. Avoid concurrent filesystem changes during checks.

This is an initial extraction, not a tagged release. Choose and review a license before publication.
