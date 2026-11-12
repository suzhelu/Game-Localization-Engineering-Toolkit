"""Build and audit a small overlay package using synthetic files only."""
from __future__ import annotations

import tempfile
from pathlib import Path

from localization_toolkit.hashing import hash_file
from localization_toolkit.packages import (
    OverlayPackagePlan,
    PackageFile,
    audit_overlay_package,
    build_overlay_package,
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="localization-overlay-demo-") as temporary:
        root = Path(temporary)
        source = root / "accepted-payload"
        source.mkdir()
        (source / "Data").mkdir()
        (source / "Data" / "example.bin").write_bytes(b"synthetic overlay payload\n")
        (source / "menu.txt").write_bytes(b"Synthetic menu text\r\n")
        output = root / "release-package"
        entries = [
            PackageFile("Data/example.bin", "Data/example.bin", hash_file(source / "Data/example.bin")),
            PackageFile("menu.txt", "Data/UI/menu.txt", hash_file(source / "menu.txt")),
        ]
        result = build_overlay_package(OverlayPackagePlan(
            source_root=source,
            output_directory=output,
            package_name="synthetic-overlay",
            version="1.0",
            files=entries,
        ))
        audit = audit_overlay_package(output)
        assert result["status"] == "passed" and audit["status"] == "passed"
        print(f"Synthetic package verified: {audit['file_count']} payload files")
        print(f"ZIP SHA-256: {audit['archive_sha256']}")
        print(f"Manifest: {output / 'manifest.json'}")


if __name__ == "__main__":
    main()
