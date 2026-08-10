import tempfile
import unittest
from pathlib import Path

from procurement_os.storage import LocalFilesystemStorage


class TestLocalFilesystemStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LocalFilesystemStorage(Path(self.tmp.name) / "root")

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        self.store.put_bytes("books/june.pdf", b"data")
        self.assertEqual(self.store.get_bytes("books/june.pdf"), b"data")
        self.assertTrue(self.store.exists("books/june.pdf"))
        self.assertEqual(self.store.list_keys("books/"), ["books/june.pdf"])

    def test_rejects_parent_traversal(self):
        with self.assertRaises(ValueError):
            self.store.put_bytes("../escape.txt", b"x")

    def test_rejects_sibling_prefix_traversal(self):
        # 'root-evil' shares the textual prefix of 'root' — must still be rejected.
        with self.assertRaises(ValueError):
            self.store.put_bytes("../root-evil/escape.txt", b"x")

    def test_rejects_absolute_key(self):
        with self.assertRaises(ValueError):
            self.store.get_bytes("/etc/passwd")

    def test_rejects_nested_traversal(self):
        with self.assertRaises(ValueError):
            self.store.put_bytes("a/../../escape.txt", b"x")


if __name__ == "__main__":
    unittest.main()
