import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_rejects_internal_symlink_components(self):
        self.store.put_bytes("target/existing.txt", b"safe")
        root = Path(self.tmp.name) / "root"
        (root / "alias").symlink_to(root / "target", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            self.store.put_bytes_once("alias/new.txt", b"unsafe alias write")
        self.assertFalse((root / "target" / "new.txt").exists())

    def test_construction_and_read_paths_do_not_create_storage_root(self):
        root = Path(self.tmp.name) / "read-only-root"
        store = LocalFilesystemStorage(root)
        self.assertFalse(root.exists())
        self.assertFalse(store.exists("missing.csv"))
        self.assertEqual(store.list_keys(), [])
        self.assertFalse(root.exists())

    def test_failed_atomic_replace_preserves_prior_object_and_removes_temporary_file(self):
        self.store.put_bytes("packets/review.zip", b"complete-prior-object")
        with patch("procurement_os.storage.os.replace", side_effect=OSError("synthetic replace failure")):
            with self.assertRaisesRegex(OSError, "synthetic replace failure"):
                self.store.put_bytes("packets/review.zip", b"partial-new-object")
        self.assertEqual(
            self.store.get_bytes("packets/review.zip"), b"complete-prior-object"
        )
        self.assertEqual(
            self.store.list_keys("packets/"), ["packets/review.zip"]
        )

    def test_put_bytes_once_publishes_complete_bytes_and_refuses_replacement(self):
        self.store.put_bytes_once("intakes/blob", b"first-complete-object")
        with self.assertRaises(FileExistsError):
            self.store.put_bytes_once("intakes/blob", b"different-object")
        self.assertEqual(
            self.store.get_bytes("intakes/blob"), b"first-complete-object"
        )
        self.assertEqual(self.store.list_keys("intakes/"), ["intakes/blob"])

    def test_put_bytes_once_link_failure_leaves_no_partial_object_or_temporary_file(self):
        with patch(
            "procurement_os.storage.os.link",
            side_effect=OSError("synthetic no-replace publication failure"),
        ):
            with self.assertRaisesRegex(
                OSError, "synthetic no-replace publication failure"
            ):
                self.store.put_bytes_once("intakes/blob", b"complete-object")
        self.assertFalse(self.store.exists("intakes/blob"))
        self.assertEqual(self.store.list_keys("intakes/"), [])

    def test_put_bytes_once_stages_outside_the_published_object_directory(self):
        observed: dict[str, Path] = {}
        original = __import__("os").link

        def inspect_link(source, target, **kwargs):
            observed["source"] = Path(source)
            observed["target"] = Path(target)
            return original(source, target, **kwargs)

        with patch("procurement_os.storage.os.link", side_effect=inspect_link):
            self.store.put_bytes_once("sealed/workspace/object.json", b"sealed\n")
        self.assertEqual(observed["source"].parent.name, ".immutable-staging")
        self.assertEqual(observed["target"].parent.name, "workspace")
        self.assertNotEqual(observed["source"].parent, observed["target"].parent)

    def test_put_bytes_once_refuses_symlinked_staging_and_hides_orphans(self):
        root = Path(self.tmp.name) / "unsafe-root"
        root.mkdir(mode=0o700)
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir(mode=0o700)
        (root / ".immutable-staging").symlink_to(outside, target_is_directory=True)
        store = LocalFilesystemStorage(root)
        with self.assertRaisesRegex(PermissionError, "staging directory is unsafe"):
            store.put_bytes_once("sealed/object", b"private")
        self.assertEqual(list(outside.iterdir()), [])

        (root / ".immutable-staging").unlink()
        staging = root / ".immutable-staging"
        staging.mkdir(mode=0o700)
        (staging / "orphan.tmp").write_bytes(b"orphan")
        self.assertEqual(store.list_keys(), [])


if __name__ == "__main__":
    unittest.main()
