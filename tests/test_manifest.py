import tempfile
import unittest
from pathlib import Path

from localization_toolkit.manifest import ManifestError, build_manifest, load_manifest, validate_manifest, write_manifest


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "payload"
        self.root.mkdir()
        (self.root / "子目录").mkdir()
        (self.root / "子目录" / "sample.txt").write_text("synthetic payload\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_build_and_validate_manifest(self):
        manifest = build_manifest(self.root, ["子目录/sample.txt"], metadata={"purpose": "test"})
        self.assertEqual(validate_manifest(manifest, self.root), [])

    def test_write_and_load_manifest(self):
        manifest = build_manifest(self.root, ["子目录/sample.txt"])
        target = Path(self.temp.name) / "manifest.json"
        write_manifest(target, manifest)
        self.assertEqual(load_manifest(target), manifest)

    def test_duplicate_targets_rejected_when_building(self):
        with self.assertRaises(ManifestError):
            build_manifest(self.root, ["子目录/sample.txt", "子目录\\sample.txt"])

    def test_duplicate_manifest_entries_rejected(self):
        manifest = build_manifest(self.root, ["子目录/sample.txt"])
        manifest["files"].append(dict(manifest["files"][0]))
        with self.assertRaises(ManifestError):
            validate_manifest(manifest, self.root)

    def test_missing_file_reported(self):
        manifest = build_manifest(self.root, ["子目录/sample.txt"])
        (self.root / "子目录" / "sample.txt").unlink()
        self.assertIn("missing: 子目录/sample.txt", validate_manifest(manifest, self.root))

    def test_changed_content_reports_size_and_hash(self):
        manifest = build_manifest(self.root, ["子目录/sample.txt"])
        (self.root / "子目录" / "sample.txt").write_text("changed and longer\n", encoding="utf-8")
        issues = validate_manifest(manifest, self.root)
        self.assertIn("size_mismatch: 子目录/sample.txt", issues)
        self.assertIn("sha256_mismatch: 子目录/sample.txt", issues)

    def test_absolute_and_parent_paths_rejected(self):
        manifest = build_manifest(self.root, ["子目录/sample.txt"])
        manifest["files"][0]["path"] = str(self.root / "子目录" / "sample.txt")
        with self.assertRaises(ManifestError):
            validate_manifest(manifest, self.root)
        manifest["files"][0]["path"] = "../outside.txt"
        with self.assertRaises(ManifestError):
            validate_manifest(manifest, self.root)


if __name__ == "__main__":
    unittest.main()
