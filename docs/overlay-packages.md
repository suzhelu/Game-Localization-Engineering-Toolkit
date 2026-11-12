# Deterministic overlay packages

The package helper creates and audits a ZIP-based overlay release from a caller-supplied, hash-pinned file list. It does not scan for payload files, select game assets, install a package, or change a target directory.

## Build from an explicit list

`PackageFile` maps one source-root-relative file to one archive-root-relative path and requires its expected SHA-256. `OverlayPackagePlan` supplies the source root, a new output directory, package/version tokens, and the complete list. The builder rejects missing files, non-regular files, reparse points, source or archive path escapes, normalized duplicates, file/directory collisions, and hash mismatches. Unlisted source files are ignored.

```python
from pathlib import Path

from localization_toolkit.hashing import hash_file
from localization_toolkit.packages import (
    OverlayPackagePlan,
    PackageFile,
    build_overlay_package,
)

payload = Path("./approved-payload")
entries = [
    PackageFile(
        source_path="Data/UI/menu.bin",
        archive_path="Data/UI/menu.bin",
        expected_sha256=hash_file(payload / "Data/UI/menu.bin"),
    ),
]
result = build_overlay_package(OverlayPackagePlan(
    source_root=payload,
    output_directory=Path("./release-output"),
    package_name="sample-overlay",
    version="1.0",
    files=entries,
))
print(result["archive_sha256"])
```

The output directory must not exist and must be outside the source root. The implementation stages beside the final directory, verifies staged bytes and the finished package, and publishes by same-filesystem directory rename. On failure, it removes its staging directory. It refuses to overwrite an earlier package.

## Package format, manifest, and audit

The output directory contains exactly:

- `package.zip`: payload files at their declared relative paths.
- `manifest.json`: schema version, package/version, ZIP profile, archive size/hash, and ordered per-file path/size/hash records.
- `SHA256SUMS.txt`: SHA-256 lines for the manifest and ZIP. The checksum file does not hash itself.

Archive names use canonical POSIX relative paths, independent of the host's path separator. Entries are sorted by case-folded path then exact path; duplicate paths are rejected using Windows-style case-insensitive comparison. ZIP entries use `ZIP_STORED`, a fixed 1980-01-01 timestamp, Unix regular-file mode `0644`, and no comments or extra fields. This removes source timestamps, host paths, and compression-library output from the archive. The supported ZIP profile is deterministic for identical input bytes, paths, package metadata, and a compatible Python/zipfile implementation; byte identity across arbitrary future runtimes is not promised.

`audit_overlay_package(directory)` verifies the exact three-file release directory, strict manifest structure, checksum sidecar, ZIP digest and size, canonical ZIP metadata, exact member set, and every member's size and SHA-256. It reads members without extracting them. It rejects duplicate JSON keys, duplicate or unsafe archive paths, unexpected members, damaged archives, and excessive entry or uncompressed-size totals.

Current safety limits are 100,000 entries, 4 GiB per member, and 20 GiB total uncompressed data. A ZIP archive is an interchange artifact, not an installation instruction. Consumers must separately review its manifest and use an explicitly authorized installer or deployment workflow. This package module does not implement extraction or installation. In particular, do not treat a successful package audit as authorization to modify a game directory.

## Failure handling and limitations

The source file is hashed before staging and again while copying; staged content is independently checked before packaging. A source changed during the operation causes the build to fail. It does not lock the source tree against uncooperative concurrent writers, so callers should provide a quiescent, approved input set. The source tree is not recursively collected; only listed paths are opened.

The package directory must be published to a previously absent location. The helper does not coordinate multiple concurrent writers targeting the same output path. ZIP integrity checks are not a substitute for code signing, provenance review, malware scanning, rights review, or runtime testing. Manifest and checksum files provide integrity evidence, not authenticity.

## Provenance

This helper distills the explicit file mapping, per-file digest checks, release manifest, checksum list, and archive audit patterns from the project's actually used overlay-preparation and asset-audit workflow. Project paths, game files, translations, manifests, and archives were not copied. See [code-migration.md](code-migration.md) for the source/refactoring record.

Run the synthetic end-to-end example with `python examples/overlay_package/build_synthetic_package.py`. The example uses only temporary files created by the script.
