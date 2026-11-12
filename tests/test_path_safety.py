import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from localization_toolkit.path_safety import PathSafetyError, compare_snapshots, normalize_target, snapshot_tree


class PathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "Game Root 中文"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_normal_relative_path(self):
        result = normalize_target(self.root, "Data/script.mg")
        self.assertEqual(result.relative_path, "Data/script.mg")
        self.assertEqual(result.full_path, self.root / "Data" / "script.mg")

    def test_absolute_path_converts_to_relative(self):
        absolute = self.root / "Data" / "file with spaces.txt"
        self.assertEqual(normalize_target(self.root, absolute).relative_path, "Data/file with spaces.txt")

    def test_unicode_and_spaces(self):
        self.assertEqual(normalize_target(self.root, "汉化 文件/脚本.txt").relative_path, "汉化 文件/脚本.txt")

    def test_windows_case_insensitive_key(self):
        upper = normalize_target(self.root, "Data/Script.MG")
        lower = normalize_target(self.root, "data/script.mg")
        self.assertEqual(upper.key, lower.key)

    def test_parent_traversal_rejected(self):
        with self.assertRaises(PathSafetyError):
            normalize_target(self.root, "folder/../file.txt")

    def test_windows_special_path_forms_rejected(self):
        for raw in ("folder/file.txt:stream", "folder/trailing.", "folder/trailing ", "folder/NUL.txt", "\\\\?\\device-path"):
            with self.subTest(path=raw), self.assertRaises(PathSafetyError):
                normalize_target(self.root, raw)

    def test_absolute_escape_rejected(self):
        outside = Path(self.temp.name) / "outside.txt"
        with self.assertRaises(PathSafetyError):
            normalize_target(self.root, outside)

    def test_symlink_escape_rejected(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.root / "linked"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            if os.name != "nt":
                self.skipTest(f"Symlink creation unavailable on this host: {exc}")
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, text=True, errors="replace")
            if result.returncode != 0:
                self.skipTest(f"Symlink and junction creation unavailable: {result.stderr or result.stdout}")
        with self.assertRaises(PathSafetyError):
            normalize_target(self.root, "linked/secret.txt")
        with self.assertRaises(PathSafetyError):
            snapshot_tree(self.root)

    def test_reparse_point_tree_rejected(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.root / "linked-dir"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            if os.name != "nt":
                self.skipTest(f"Symlink creation unavailable on this host: {exc}")
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, text=True, errors="replace")
            if result.returncode != 0:
                self.skipTest(f"Symlink and junction creation unavailable: {result.stderr or result.stdout}")
        with self.assertRaises(PathSafetyError):
            snapshot_tree(self.root)

    def test_reparse_point_in_root_ancestor_rejected(self):
        outside = Path(self.temp.name) / "actual-root"
        outside.mkdir()
        alias = Path(self.temp.name) / "root-alias"
        try:
            os.symlink(outside, alias, target_is_directory=True)
        except (OSError, NotImplementedError):
            if os.name != "nt":
                self.skipTest("Symlink creation unavailable on this host")
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(outside)], capture_output=True, text=True, errors="replace")
            if result.returncode != 0:
                self.skipTest(f"Symlink and junction creation unavailable: {result.stderr or result.stdout}")
        nested_root = alias / "nested"
        nested_root.mkdir()
        with self.assertRaises(PathSafetyError):
            normalize_target(nested_root, "file.txt")

    def test_whitelist_changed_added_removed_detection(self):
        (self.root / "allowed.txt").write_text("before", encoding="utf-8")
        (self.root / "removed.txt").write_text("remove", encoding="utf-8")
        before = snapshot_tree(self.root)
        (self.root / "allowed.txt").write_text("after", encoding="utf-8")
        (self.root / "added.txt").write_text("new", encoding="utf-8")
        (self.root / "removed.txt").unlink()
        after = snapshot_tree(self.root)
        diff = compare_snapshots(before, after, root=self.root, authorized_paths=["allowed.txt"])
        self.assertEqual([x["path"] for x in diff["changed"]], ["allowed.txt"])
        self.assertEqual([x["path"] for x in diff["added"]], ["added.txt"])
        self.assertEqual([x["path"] for x in diff["removed"]], ["removed.txt"])
        self.assertEqual({(x["kind"], x["path"]) for x in diff["outside_whitelist"]}, {("added", "added.txt"), ("removed", "removed.txt")})

    def test_whitelist_external_change_reported(self):
        (self.root / "other.txt").write_text("before", encoding="utf-8")
        before = snapshot_tree(self.root)
        (self.root / "other.txt").write_text("after", encoding="utf-8")
        diff = compare_snapshots(before, snapshot_tree(self.root), root=self.root)
        self.assertEqual([x["path"] for x in diff["outside_whitelist"]], ["other.txt"])

    def test_whitelisted_addition_and_removal_are_authorized(self):
        (self.root / "old.txt").write_text("old", encoding="utf-8")
        before = snapshot_tree(self.root)
        (self.root / "old.txt").unlink()
        (self.root / "new.txt").write_text("new", encoding="utf-8")
        after = snapshot_tree(self.root)
        diff = compare_snapshots(before, after, root=self.root, authorized_paths=["old.txt", "new.txt"])
        self.assertEqual(diff["outside_whitelist"], [])
        self.assertTrue(diff["added"][0]["authorized"])
        self.assertTrue(diff["removed"][0]["authorized"])


if __name__ == "__main__":
    unittest.main()
