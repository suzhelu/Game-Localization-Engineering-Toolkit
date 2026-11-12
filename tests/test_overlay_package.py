from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from localization_toolkit.hashing import hash_file
from localization_toolkit.packages import (
    OverlayPackageError,
    OverlayPackageIntegrityError,
    OverlayPackagePlan,
    PackageFile,
    audit_overlay_package,
    build_overlay_package,
)
from localization_toolkit.packages.overlay import _zip_info


class OverlayPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="overlay package 中文 ")
        self.root = Path(self.temp.name)
        self.source = self.root / "accepted files"
        self.source.mkdir()
        self.first = self.source / "folder with spaces" / "α.bin"
        self.first.parent.mkdir()
        self.first.write_bytes("合成内容 A\n".encode("utf-8"))
        self.second = self.source / "menu.txt"
        self.second.write_bytes(b"synthetic menu\r\n")
        self.unlisted = self.source / "must-not-be-collected.bin"
        self.unlisted.write_bytes(b"not in explicit list")
        self.entries = [
            PackageFile("menu.txt", "Data/UI/menu.txt", hash_file(self.second)),
            PackageFile("folder with spaces/α.bin", "Data/Resources/α.bin", hash_file(self.first)),
        ]

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, name="demo", version="1.0", files=None, output="package-out"):
        return OverlayPackagePlan(
            source_root=self.source,
            output_directory=self.root / output,
            package_name=name,
            version=version,
            files=self.entries if files is None else files,
        )

    def _build(self, output="package-out", files=None):
        return build_overlay_package(self.plan(output=output, files=files))

    def _reseal(self, package_dir: Path) -> None:
        manifest_path = package_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        archive = package_dir / "package.zip"
        manifest["archive"]["size_bytes"] = archive.stat().st_size
        manifest["archive"]["sha256"] = hash_file(archive)
        payload = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        manifest_path.write_bytes(payload)
        manifest_sha = hashlib.sha256(payload).hexdigest().upper()
        (package_dir / "SHA256SUMS.txt").write_text(
            f"{manifest_sha}  manifest.json\n{manifest['archive']['sha256']}  package.zip\n",
            encoding="ascii",
            newline="\n",
        )

    def _write_custom_zip(self, package_dir: Path, names_and_bytes):
        archive_path = package_dir / "package.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, data in names_and_bytes:
                info = _zip_info(name, len(data))
                archive.writestr(info, data)
        self._reseal(package_dir)

    def test_build_and_audit_explicit_manifest_package(self):
        result = self._build()
        audit = audit_overlay_package(self.root / "package-out")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(audit["status"], "passed")
        self.assertEqual(audit["file_count"], 2)
        self.assertEqual([row["path"] for row in audit["files"]], ["Data/Resources/α.bin", "Data/UI/menu.txt"])
        self.assertEqual(hash_file(self.root / "package-out/package.zip"), result["archive_sha256"])

    def test_same_input_produces_byte_identical_package_artifacts(self):
        self._build(output="bundle-one")
        self._build(output="bundle-two")
        for name in ("package.zip", "manifest.json", "SHA256SUMS.txt"):
            self.assertEqual((self.root / "bundle-one" / name).read_bytes(), (self.root / "bundle-two" / name).read_bytes())

    def test_unlisted_source_files_are_never_collected(self):
        self._build()
        with zipfile.ZipFile(self.root / "package-out/package.zip") as archive:
            self.assertNotIn("must-not-be-collected.bin", archive.namelist())
            self.assertEqual(len(archive.namelist()), len(self.entries))
        self.assertTrue(self.unlisted.exists())

    def test_package_contains_only_artifacts_and_does_not_install(self):
        before_first = self.first.read_bytes()
        before_second = self.second.read_bytes()
        self._build()
        self.assertEqual({p.name for p in (self.root / "package-out").iterdir()}, {"package.zip", "manifest.json", "SHA256SUMS.txt"})
        self.assertEqual(self.first.read_bytes(), before_first)
        self.assertEqual(self.second.read_bytes(), before_second)
        self.assertFalse((self.root / "Data").exists())

    def test_output_is_not_overwritten(self):
        self._build()
        before = (self.root / "package-out/package.zip").read_bytes()
        with self.assertRaises(OverlayPackageError):
            self._build()
        self.assertEqual((self.root / "package-out/package.zip").read_bytes(), before)

    def test_missing_source_is_rejected(self):
        files = [PackageFile("missing.bin", "Data/missing.bin", "0" * 64)]
        with self.assertRaises(OverlayPackageError):
            self._build(files=files)

    def test_hash_mismatch_is_rejected(self):
        files = [PackageFile("menu.txt", "Data/menu.txt", "0" * 64)]
        with self.assertRaises(OverlayPackageError):
            self._build(files=files)

    def test_source_changed_during_staging_leaves_no_published_package(self):
        def corrupt_after_copy(source, destination, expected):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"not the approved content")
            raise OverlayPackageError("injected staged-content mismatch")

        with patch("localization_toolkit.packages.overlay._copy_verified", corrupt_after_copy):
            with self.assertRaises(OverlayPackageError):
                self._build()
        self.assertFalse((self.root / "package-out").exists())
        self.assertFalse(list(self.root.glob(".package-out.stage-*")))

    def test_archive_path_traversal_and_absolute_paths_are_rejected(self):
        for archive_path in ("../escape.txt", "/absolute.txt", "C:/drive.txt", "Data/../escape.txt"):
            with self.subTest(path=archive_path), self.assertRaises(OverlayPackageError):
                self._build(files=[PackageFile("menu.txt", archive_path, hash_file(self.second))])
            self.assertFalse((self.root / "package-out").exists())

    def test_source_path_traversal_is_rejected(self):
        with self.assertRaises(OverlayPackageError):
            self._build(files=[PackageFile("../outside.bin", "Data/outside.bin", "0" * 64)])

    def test_duplicate_archive_paths_are_rejected_case_insensitively(self):
        files = [
            PackageFile("menu.txt", "Data/Menu.txt", hash_file(self.second)),
            PackageFile("folder with spaces/α.bin", "data/menu.TXT", hash_file(self.first)),
        ]
        with self.assertRaises(OverlayPackageError):
            self._build(files=files)

    def test_archive_file_parent_collision_is_rejected(self):
        files = [
            PackageFile("menu.txt", "Data/item", hash_file(self.second)),
            PackageFile("folder with spaces/α.bin", "Data/item/child.bin", hash_file(self.first)),
        ]
        with self.assertRaises(OverlayPackageError):
            self._build(files=files)

    def test_corrupt_zip_is_rejected(self):
        self._build()
        path = self.root / "package-out/package.zip"
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 0x55
        path.write_bytes(data)
        with self.assertRaises(OverlayPackageIntegrityError):
            audit_overlay_package(self.root / "package-out")

    def test_missing_zip_member_is_rejected_after_outer_hashes_are_consistent(self):
        self._build()
        package_dir = self.root / "package-out"
        payload = [("Data/UI/menu.txt", self.second.read_bytes())]
        self._write_custom_zip(package_dir, payload)
        with self.assertRaises(OverlayPackageIntegrityError):
            audit_overlay_package(package_dir)

    def test_duplicate_zip_paths_are_rejected(self):
        self._build()
        package_dir = self.root / "package-out"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            self._write_custom_zip(package_dir, [("Data/UI/menu.txt", b"one"), ("Data/UI/menu.txt", b"two")])
        with self.assertRaises(OverlayPackageIntegrityError):
            audit_overlay_package(package_dir)

    def test_zip_member_order_must_match_canonical_manifest_order(self):
        self._build()
        package_dir = self.root / "package-out"
        self._write_custom_zip(package_dir, [
            ("Data/UI/menu.txt", self.second.read_bytes()),
            ("Data/Resources/α.bin", self.first.read_bytes()),
        ])
        with self.assertRaises(OverlayPackageIntegrityError):
            audit_overlay_package(package_dir)

    def test_path_traversal_zip_member_is_rejected(self):
        self._build()
        package_dir = self.root / "package-out"
        self._write_custom_zip(package_dir, [("../escape.txt", b"payload")])
        with self.assertRaises(OverlayPackageIntegrityError):
            audit_overlay_package(package_dir)

    def test_manifest_path_traversal_is_rejected(self):
        self._build()
        package_dir = self.root / "package-out"
        manifest_path = package_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["path"] = "../escape.txt"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._reseal(package_dir)
        with self.assertRaises(OverlayPackageIntegrityError):
            audit_overlay_package(package_dir)

    def test_manifest_duplicate_paths_are_rejected(self):
        self._build()
        package_dir = self.root / "package-out"
        manifest_path = package_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][1]["path"] = manifest["files"][0]["path"].swapcase()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._reseal(package_dir)
        with self.assertRaises(OverlayPackageIntegrityError):
            audit_overlay_package(package_dir)

    def test_checksum_sidecar_mismatch_is_rejected(self):
        self._build()
        path = self.root / "package-out/SHA256SUMS.txt"
        path.write_text("0" * 64 + "  package.zip\n", encoding="ascii")
        with self.assertRaises(OverlayPackageIntegrityError):
            audit_overlay_package(self.root / "package-out")

    def test_extra_package_directory_file_is_rejected(self):
        self._build()
        (self.root / "package-out/extra.tmp").write_bytes(b"extra")
        with self.assertRaises(OverlayPackageIntegrityError):
            audit_overlay_package(self.root / "package-out")

    def test_output_inside_source_root_is_rejected(self):
        plan = OverlayPackagePlan(self.source, self.source / "output", "demo", "1.0", self.entries)
        with self.assertRaises(OverlayPackageError):
            build_overlay_package(plan)

    def test_partial_zip_failure_cleans_staging_and_publishes_nothing(self):
        def partial_then_fail(payload_root, rows, archive_path):
            archive_path.write_bytes(b"partial ZIP")
            raise OverlayPackageError("injected ZIP write failure")

        with patch("localization_toolkit.packages.overlay._write_payload_zip", partial_then_fail):
            with self.assertRaises(OverlayPackageError):
                self._build()
        self.assertFalse((self.root / "package-out").exists())
        self.assertFalse(list(self.root.glob(".package-out.stage-*")))

    def test_duplicate_json_keys_are_rejected(self):
        self._build()
        path = self.root / "package-out/manifest.json"
        path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        with self.assertRaises(OverlayPackageIntegrityError):
            audit_overlay_package(self.root / "package-out")


if __name__ == "__main__":
    unittest.main()
