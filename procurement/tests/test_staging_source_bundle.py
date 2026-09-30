"""Tiny synthetic-bundle tests for the accepted source-store installer."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from procurement_os import staging_source_bundle as source_bundle
from procurement_os.staging_source_bundle import SourceBundleError


def _run(repository: Path, *arguments: str) -> bytes:
    environment = {
        "GIT_AUTHOR_EMAIL": "fixture@local.invalid",
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@local.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "HOME": str(repository.parent / "fixture-home"),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=repository,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return result.stdout


class _BundleFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.source_parent = root / "source"
        self.source_parent.mkdir(mode=0o700)
        self.repository = root / "repository"
        self.repository.mkdir(mode=0o700)
        _run(self.repository, "init", "--initial-branch=accepted")
        (self.repository / "alpha.txt").write_text("alpha\n", encoding="ascii")
        (self.repository / "seed.json").write_text('{"seed":true}\n', encoding="ascii")
        (self.repository / "seed.csv").write_text("id,value\n1,fixture\n", encoding="ascii")
        (self.repository / "policy.json").write_text(
            '{"policy":"fixture"}\n', encoding="ascii"
        )
        (self.repository / "joint.json").write_text(
            '{"joint":"fixture"}\n', encoding="ascii"
        )
        _run(
            self.repository,
            "add",
            "alpha.txt",
            "seed.json",
            "seed.csv",
            "policy.json",
            "joint.json",
        )
        _run(self.repository, "commit", "-m", "root")
        self.root_commit = _run(self.repository, "rev-parse", "HEAD").decode().strip()
        (self.repository / "tool.sh").write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        (self.repository / "tool.sh").chmod(0o755)
        _run(self.repository, "add", "tool.sh")
        _run(self.repository, "commit", "-m", "middle")
        self.parent = _run(self.repository, "rev-parse", "HEAD").decode().strip()
        (self.repository / "alpha.txt").write_text("alpha\nbeta\n", encoding="ascii")
        _run(self.repository, "add", "alpha.txt")
        _run(self.repository, "commit", "-m", "tip")
        self.tip = _run(self.repository, "rev-parse", "HEAD").decode().strip()
        self.bundle = self.source_parent / "fixture.bundle"
        _run(
            self.repository,
            "bundle",
            "create",
            str(self.bundle),
            "refs/heads/accepted",
        )
        self.bundle.chmod(0o600)
        self.contract = self._contract()

    def _contract(self) -> source_bundle._BundleContract:
        reachable = set(
            _run(
                self.repository,
                "rev-list",
                "--objects",
                "--no-object-names",
                self.tip,
            )
            .decode("ascii")
            .splitlines()
        )
        object_types: dict[str, str] = {}
        for oid in reachable:
            object_types[oid] = (
                _run(self.repository, "cat-file", "-t", oid).decode("ascii").strip()
            )
        object_hash = hashlib.sha256(
            "".join(f"{oid}\n" for oid in sorted(reachable)).encode("ascii")
        ).hexdigest()
        ls_tree = _run(
            self.repository, "ls-tree", "-r", "-z", "--full-tree", self.tip
        )
        entries = [entry for entry in ls_tree.split(b"\0") if entry]
        modes: Counter[str] = Counter()
        for entry in entries:
            metadata, _path = entry.split(b"\t", 1)
            mode, _type, _oid = metadata.decode("ascii").split(" ", 2)
            modes[mode] += 1
        milestone_pairs = tuple(
            (
                commit,
                _run(self.repository, "rev-parse", f"{commit}^{{tree}}").decode().strip(),
            )
            for commit in (self.root_commit, self.parent)
        )
        return source_bundle._BundleContract(
            filename=self.bundle.name,
            bundle_bytes=self.bundle.stat().st_size,
            bundle_sha256=hashlib.sha256(self.bundle.read_bytes()).hexdigest(),
            ref="refs/heads/accepted",
            tip=self.tip,
            tip_tree=_run(
                self.repository, "rev-parse", f"{self.tip}^{{tree}}"
            ).decode().strip(),
            parent=self.parent,
            parent_tree=_run(
                self.repository, "rev-parse", f"{self.parent}^{{tree}}"
            ).decode().strip(),
            object_count=len(reachable),
            object_set_sha256=object_hash,
            object_type_counts=dict(Counter(object_types.values())),
            commit_count=int(
                _run(self.repository, "rev-list", "--count", self.tip).decode()
            ),
            ls_tree_sha256=hashlib.sha256(ls_tree).hexdigest(),
            ls_tree_entries=len(entries),
            mode_counts=dict(modes),
            milestone_pairs=milestone_pairs,
            forbidden_object="f" * 40,
        )


class StagingSourceBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.fixture = _BundleFixture(self.root)
        self.staging = self.root / "staging"
        self.destination = self.root / "destination"
        self.staging.mkdir(mode=0o700)
        self.destination.mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _install(self):
        return source_bundle._build_unpublished_with_contract(
            self.fixture.bundle,
            repository_path=self.destination / "source.git",
            scratch_root=self.staging,
            owner_uid=os.getuid(),
            owner_gid=os.getgid(),
            source_uid=os.getuid(),
            source_gid=os.getgid(),
            contract=self.fixture.contract,
        )

    def test_production_contract_is_code_owned_and_exact(self) -> None:
        self.assertEqual(source_bundle.ACCEPTED_BUNDLE_BYTES, 2_863_932)
        self.assertEqual(
            source_bundle.ACCEPTED_BUNDLE_SHA256,
            "07009cb0442fc45b5a81da18f435f908e141fe2c8177ca1c2c455b9269d99079",
        )
        self.assertEqual(
            source_bundle.ACCEPTED_TIP,
            "608929ad00adfd2eefaab29449743c5a3f0f035e",
        )
        self.assertEqual(source_bundle.ACCEPTED_OBJECT_COUNT, 2_831)
        repository = Path("/private/research/source.git")
        command = source_bundle._git_base(Path("/usr/bin/git"), repository)
        self.assertIn(f"safe.directory={repository}", command)
        self.assertNotIn("safe.directory=*", command)

    def test_exact_bundle_installs_one_sealed_bare_store(self) -> None:
        before = (
            self.fixture.bundle.stat().st_ino,
            self.fixture.bundle.stat().st_mtime_ns,
            hashlib.sha256(self.fixture.bundle.read_bytes()).hexdigest(),
        )
        proof = self._install()
        store = self.destination / "source.git"
        self.assertEqual(proof.tip, self.fixture.tip)
        self.assertEqual(proof.object_count, self.fixture.contract.object_count)
        self.assertTrue(store.is_dir())
        self.assertFalse(any(path.name == "accepted.bundle" for path in store.rglob("*")))
        for parent, directories, files in os.walk(store):
            self.assertEqual(stat.S_IMODE(Path(parent).stat().st_mode), 0o500)
            for name in directories:
                self.assertEqual(
                    stat.S_IMODE((Path(parent) / name).stat().st_mode), 0o500
                )
            for name in files:
                self.assertEqual(
                    stat.S_IMODE((Path(parent) / name).stat().st_mode), 0o400
                )
        after = (
            self.fixture.bundle.stat().st_ino,
            self.fixture.bundle.stat().st_mtime_ns,
            hashlib.sha256(self.fixture.bundle.read_bytes()).hexdigest(),
        )
        self.assertEqual(before, after)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_hostile_inherited_git_configuration_is_ignored(self) -> None:
        hostile = self.root / "hostile.gitconfig"
        hostile.write_text(
            "[url \"ssh://attacker.invalid/\"]\n\tinsteadOf = file://\n",
            encoding="ascii",
        )
        with mock.patch.dict(
            os.environ,
            {
                "GIT_CONFIG_GLOBAL": str(hostile),
                "GIT_OBJECT_DIRECTORY": str(self.root / "objects"),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self.root / "alternates"),
                "GIT_SSH_COMMAND": "false",
                "LD_PRELOAD": str(self.root / "hostile.so"),
            },
            clear=False,
        ):
            proof = self._install()
        self.assertEqual(proof.tip, self.fixture.tip)

    def test_git_output_is_bounded_while_the_child_is_running(self) -> None:
        environment = {
            "HOME": str(self.root),
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
        }
        with mock.patch.object(source_bundle, "_MAX_GIT_OUTPUT_BYTES", 64):
            with self.assertRaisesRegex(SourceBundleError, "exceeded its bound"):
                source_bundle._run(
                    [
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.write('x' * 100000)",
                    ],
                    environment=environment,
                    cwd=self.root,
                )

    def test_tampered_or_truncated_bundle_fails_without_publication(self) -> None:
        for mutation in ("tamper", "truncate"):
            with self.subTest(mutation=mutation):
                raw = self.fixture.bundle.read_bytes()
                if mutation == "tamper":
                    changed = raw[:-1] + bytes([raw[-1] ^ 1])
                else:
                    changed = raw[:-1]
                self.fixture.bundle.write_bytes(changed)
                self.fixture.bundle.chmod(0o600)
                with self.assertRaises(SourceBundleError):
                    self._install()
                self.assertFalse((self.destination / "source.git").exists())
                self.assertEqual(list(self.staging.iterdir()), [])
                self.fixture.bundle.write_bytes(raw)
                self.fixture.bundle.chmod(0o600)

    def test_bundle_metadata_change_during_copy_fails_closed(self) -> None:
        real_read = source_bundle.os.read
        changed = False

        def mutate_after_read(descriptor: int, size: int) -> bytes:
            nonlocal changed
            raw = real_read(descriptor, size)
            if raw and not changed:
                self.fixture.bundle.chmod(0o400)
                changed = True
            return raw

        try:
            with mock.patch.object(source_bundle.os, "read", mutate_after_read):
                with self.assertRaises(SourceBundleError):
                    self._install()
        finally:
            self.fixture.bundle.chmod(0o600)
        self.assertTrue(changed)
        self.assertFalse((self.destination / "source.git").exists())
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_symlink_and_fifo_bundle_sources_fail_without_blocking(self) -> None:
        raw = self.fixture.bundle.read_bytes()
        alternate = self.fixture.source_parent / "alternate.bundle"
        alternate.write_bytes(raw)
        alternate.chmod(0o600)
        for kind in ("symlink", "fifo"):
            with self.subTest(kind=kind):
                self.fixture.bundle.unlink()
                if kind == "symlink":
                    self.fixture.bundle.symlink_to(alternate)
                else:
                    os.mkfifo(self.fixture.bundle, 0o600)
                with self.assertRaises(SourceBundleError):
                    self._install()
                self.fixture.bundle.unlink()
                self.fixture.bundle.write_bytes(raw)
                self.fixture.bundle.chmod(0o600)

    def test_prerequisite_bundle_and_extra_ref_header_fail_closed(self) -> None:
        thin = self.fixture.source_parent / "thin.bundle"
        _run(
            self.fixture.repository,
            "bundle",
            "create",
            str(thin),
            "refs/heads/accepted",
            f"^{self.fixture.parent}",
        )
        thin.chmod(0o600)
        thin_contract = replace(
            self.fixture.contract,
            filename=thin.name,
            bundle_bytes=thin.stat().st_size,
            bundle_sha256=hashlib.sha256(thin.read_bytes()).hexdigest(),
        )
        with self.assertRaisesRegex(SourceBundleError, "prerequisite"):
            source_bundle._parse_bundle_header(thin, thin_contract)

        _run(self.fixture.repository, "branch", "other", self.fixture.parent)
        multiple = self.fixture.source_parent / "multiple.bundle"
        _run(
            self.fixture.repository,
            "bundle",
            "create",
            str(multiple),
            "refs/heads/accepted",
            "refs/heads/other",
        )
        multiple.chmod(0o600)
        multiple_contract = replace(
            self.fixture.contract,
            filename=multiple.name,
            bundle_bytes=multiple.stat().st_size,
            bundle_sha256=hashlib.sha256(multiple.read_bytes()).hexdigest(),
        )
        with self.assertRaisesRegex(SourceBundleError, "ref inventory"):
            source_bundle._parse_bundle_header(multiple, multiple_contract)

    def test_wrong_object_or_tree_contract_fails_without_publication(self) -> None:
        for changed in (
            replace(
                self.fixture.contract,
                object_set_sha256="0" * 64,
            ),
            replace(
                self.fixture.contract,
                ls_tree_sha256="1" * 64,
            ),
        ):
            with self.subTest(field=changed):
                with self.assertRaises(SourceBundleError):
                    source_bundle._build_unpublished_with_contract(
                        self.fixture.bundle,
                        repository_path=self.destination / "source.git",
                        scratch_root=self.staging,
                        owner_uid=os.getuid(),
                        owner_gid=os.getgid(),
                        source_uid=os.getuid(),
                        source_gid=os.getgid(),
                        contract=changed,
                    )
                self.assertFalse((self.destination / "source.git").exists())
                self.assertEqual(list(self.staging.iterdir()), [])

    def test_existing_destination_is_not_replaced(self) -> None:
        store = self.destination / "source.git"
        store.mkdir(mode=0o700)
        marker = store / "marker"
        marker.write_bytes(b"preserve")
        with self.assertRaises(SourceBundleError):
            self._install()
        self.assertEqual(marker.read_bytes(), b"preserve")
        self.assertEqual(list(self.staging.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
