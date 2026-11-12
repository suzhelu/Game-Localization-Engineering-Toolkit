import tempfile
import unittest
from pathlib import Path

from localization_toolkit.hashing import hash_file


class HashingTests(unittest.TestCase):
    def test_sha256_known_vector(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.bin"
            path.write_bytes(b"abc")
            self.assertEqual(hash_file(path), "BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD")

    def test_content_change_changes_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "change.bin"
            path.write_bytes(b"before")
            before = hash_file(path)
            path.write_bytes(b"after")
            self.assertNotEqual(before, hash_file(path))

    def test_rejects_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                hash_file(temp)


if __name__ == "__main__":
    unittest.main()
