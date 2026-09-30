"""Hardened reconstruction of the exact accepted source bundle.

Production expectations are code-owned.  Complete-release assembly never
accepts a caller-supplied hash, ref, object count, or lineage expectation.
Synthetic tests use the private contract seam with tiny generated bundles only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from typing import Mapping, Sequence

from .staging_research_transfer import (
    ResearchTransferError,
    _file_identity,
    _validate_private_parent,
)


ACCEPTED_BUNDLE_FILENAME = "buffalo-procurement-os-accepted-source-608929ad.bundle"
ACCEPTED_BUNDLE_BYTES = 2_863_932
ACCEPTED_BUNDLE_SHA256 = (
    "07009cb0442fc45b5a81da18f435f908e141fe2c8177ca1c2c455b9269d99079"
)
ACCEPTED_REF = "refs/heads/codex/private-v3-memory-remediation"
ACCEPTED_TIP = "608929ad00adfd2eefaab29449743c5a3f0f035e"
ACCEPTED_TREE = "68a72c84d26dc15e0e0c8075dd2c1c6bc46d00b8"
ACCEPTED_PARENT = "223c0ae6ba89248223cc9093579e195295b7024e"
ACCEPTED_PARENT_TREE = "3daaa79a1d622fa99a8625df9e044303b35f9119"
ACCEPTED_OBJECT_COUNT = 2_831
ACCEPTED_OBJECT_SET_SHA256 = (
    "63a229b5c3a6fc6ecdf24cd2c0d79b982482823573251e44200531082948893b"
)
ACCEPTED_OBJECT_TYPE_COUNTS = {"blob": 1_232, "commit": 303, "tree": 1_296}
ACCEPTED_COMMIT_COUNT = 303
ACCEPTED_LS_TREE_SHA256 = (
    "5efe948332c007a3c74332ab5382679f64c6f95a6e31bdcd3a040dfd02253f14"
)
ACCEPTED_LS_TREE_ENTRIES = 396
ACCEPTED_MODE_COUNTS = {"100644": 388, "100755": 8}
FORBIDDEN_DOCUMENTATION_OBJECT = "794e92f478d6e1a2e6286c186005c26b788a1a10"

_MILESTONE_PAIRS = (
    (
        "008f8b5f22184a2e43247636e1b7cb4ba3523696",
        "855e3d73ada509327d7cfa02f50da9260912ad07",
    ),
    (
        "a438a69cb05dd4f7dfb990e14de33f208dab7ccf",
        "2dacc029f088a7d44feb537d9784dc9f6a6924dd",
    ),
    (
        "62383f48aae9bbd401754bba543659c2b6543023",
        "686f8e5744f4f40bd916fb8f29c502a36508ca0b",
    ),
    (
        "d41a886aa84ccddba8e097aa9d9b47c8f6ad15cf",
        "8f9653813993355d8a696db724ebc2228a18eeef",
    ),
    (ACCEPTED_PARENT, ACCEPTED_PARENT_TREE),
)

_MAX_HEADER_BYTES = 64 * 1024
_MAX_GIT_OUTPUT_BYTES = 16 * 1024 * 1024
_GIT_TIMEOUT_SECONDS = 180
_CHUNK_BYTES = 1024 * 1024
_PACK_FILE = re.compile(r"pack-[0-9a-f]{40}\.(?:idx|pack)")
_ALLOWED_CONFIG = {
    "core.bare": ("true",),
    "core.filemode": ("true",),
    "core.repositoryformatversion": ("0",),
}


class SourceBundleError(ValueError):
    """The source bundle or reconstructed bare store differs."""


@dataclass(frozen=True)
class _BundleContract:
    filename: str
    bundle_bytes: int
    bundle_sha256: str
    ref: str
    tip: str
    tip_tree: str
    parent: str
    parent_tree: str
    object_count: int
    object_set_sha256: str
    object_type_counts: Mapping[str, int]
    commit_count: int
    ls_tree_sha256: str
    ls_tree_entries: int
    mode_counts: Mapping[str, int]
    milestone_pairs: tuple[tuple[str, str], ...]
    forbidden_object: str


_ACCEPTED_CONTRACT = _BundleContract(
    filename=ACCEPTED_BUNDLE_FILENAME,
    bundle_bytes=ACCEPTED_BUNDLE_BYTES,
    bundle_sha256=ACCEPTED_BUNDLE_SHA256,
    ref=ACCEPTED_REF,
    tip=ACCEPTED_TIP,
    tip_tree=ACCEPTED_TREE,
    parent=ACCEPTED_PARENT,
    parent_tree=ACCEPTED_PARENT_TREE,
    object_count=ACCEPTED_OBJECT_COUNT,
    object_set_sha256=ACCEPTED_OBJECT_SET_SHA256,
    object_type_counts=ACCEPTED_OBJECT_TYPE_COUNTS,
    commit_count=ACCEPTED_COMMIT_COUNT,
    ls_tree_sha256=ACCEPTED_LS_TREE_SHA256,
    ls_tree_entries=ACCEPTED_LS_TREE_ENTRIES,
    mode_counts=ACCEPTED_MODE_COUNTS,
    milestone_pairs=_MILESTONE_PAIRS,
    forbidden_object=FORBIDDEN_DOCUMENTATION_OBJECT,
)


@dataclass(frozen=True)
class SourceStoreProof:
    ref: str
    tip: str
    tree: str
    parent: str
    parent_tree: str
    object_count: int
    object_set_sha256: str
    object_type_counts: Mapping[str, int]
    commit_count: int
    ls_tree_sha256: str
    ls_tree_entries: int
    mode_counts: Mapping[str, int]


def _trusted_git(path: str | Path = "/usr/bin/git") -> Path:
    executable = Path(path)
    try:
        info = executable.stat(follow_symlinks=False)
        resolved = executable.resolve(strict=True)
    except OSError as exc:
        raise SourceBundleError("trusted Git executable is unavailable") from exc
    if (
        not executable.is_absolute()
        or executable != resolved
        or executable.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
        or not os.access(executable, os.X_OK)
    ):
        raise SourceBundleError("trusted Git executable contract differs")
    return executable


def _git_environment(root: Path) -> dict[str, str]:
    home = root / "home"
    xdg = home / ".config"
    home.mkdir(mode=0o700)
    xdg.mkdir(mode=0o700)
    return {
        "GIT_ASKPASS": "/bin/false",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "XDG_CONFIG_HOME": str(xdg),
    }


def _git_base(executable: Path, repository: Path | None = None) -> list[str]:
    command = [
        str(executable),
        "--no-replace-objects",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.file.allow=always",
        "-c",
        "protocol.ext.allow=never",
        "-c",
        "protocol.git.allow=never",
        "-c",
        "protocol.http.allow=never",
        "-c",
        "protocol.https.allow=never",
        "-c",
        "protocol.ssh.allow=never",
        "-c",
        "maintenance.auto=false",
        "-c",
        "gc.auto=0",
        "-c",
        "fetch.writeCommitGraph=false",
        "-c",
        "fetch.fsckObjects=true",
        "-c",
        "transfer.fsckObjects=true",
    ]
    if repository is not None:
        command.extend(("-c", f"safe.directory={repository}", "--git-dir", str(repository)))
    return command


def _run(
    command: Sequence[str],
    *,
    environment: Mapping[str, str],
    cwd: Path,
    check: bool = True,
    timeout: int = _GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[bytes]:
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            raise OSError("bounded Git pipes are unavailable")
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        overflow = threading.Event()
        read_errors: list[BaseException] = []

        def drain(stream, chunks: list[bytes]) -> None:
            observed = 0
            try:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    observed += len(chunk)
                    if observed > _MAX_GIT_OUTPUT_BYTES:
                        overflow.set()
                        try:
                            process.kill()
                        except OSError:
                            pass
                        break
                    chunks.append(chunk)
            except BaseException as exc:  # pragma: no cover - defensive pipe guard
                read_errors.append(exc)
                try:
                    process.kill()
                except OSError:
                    pass

        readers = (
            threading.Thread(
                target=drain, args=(process.stdout, stdout_chunks), daemon=True
            ),
            threading.Thread(
                target=drain, args=(process.stderr, stderr_chunks), daemon=True
            ),
        )
        for reader in readers:
            reader.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        finally:
            for reader in readers:
                reader.join()
            for stream in (process.stdout, process.stderr):
                try:
                    stream.close()
                except OSError:
                    pass
        if read_errors:
            raise OSError("bounded Git pipe read failed") from read_errors[0]
        if overflow.is_set():
            raise SourceBundleError("trusted Git diagnostic exceeded its bound")
        result = subprocess.CompletedProcess(
            args=list(command),
            returncode=returncode,
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
        )
    except SourceBundleError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise SourceBundleError("trusted Git command failed") from exc
    if check and result.returncode != 0:
        raise SourceBundleError("trusted Git command refused the source store")
    return result


def _copy_bundle_from_descriptor(
    source: Path,
    destination: Path,
    *,
    contract: _BundleContract,
    source_uid: int,
    source_gid: int,
) -> None:
    if source.name != contract.filename or not source.is_absolute():
        raise SourceBundleError("source bundle path differs")
    try:
        source_descriptor = os.open(
            source,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
        )
    except OSError as exc:
        raise SourceBundleError("source bundle is unavailable") from exc
    destination_descriptor: int | None = None
    try:
        before = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != source_uid
            or before.st_gid != source_gid
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size != contract.bundle_bytes
        ):
            raise SourceBundleError("source bundle metadata differs")
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(source_descriptor, _CHUNK_BYTES)
            if not chunk:
                break
            observed += len(chunk)
            if observed > contract.bundle_bytes:
                raise SourceBundleError("source bundle size differs")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                view = view[written:]
        os.fsync(destination_descriptor)
        after = os.fstat(source_descriptor)
        if (
            _file_identity(before) != _file_identity(after)
            or observed != contract.bundle_bytes
            or digest.hexdigest() != contract.bundle_sha256
        ):
            raise SourceBundleError("source bundle bytes differ")
    except OSError as exc:
        raise SourceBundleError("source bundle copy failed") from exc
    finally:
        os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)


def _parse_bundle_header(path: Path, contract: _BundleContract) -> None:
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        )
        try:
            raw = os.read(descriptor, _MAX_HEADER_BYTES + 6)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise SourceBundleError("staged bundle header is unavailable") from exc
    boundary = raw.find(b"\n\n")
    if (
        boundary < 0
        or boundary + 2 > _MAX_HEADER_BYTES
        or raw[boundary + 2 : boundary + 6] != b"PACK"
    ):
        raise SourceBundleError("bundle v2 header boundary differs")
    lines = raw[:boundary].splitlines()
    if not lines or lines[0] != b"# v2 git bundle":
        raise SourceBundleError("bundle version differs")
    references: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line or line.startswith((b"-", b"@")):
            raise SourceBundleError("bundle prerequisite or capability differs")
        try:
            oid_raw, ref_raw = line.split(b" ", 1)
            oid = oid_raw.decode("ascii")
            ref = ref_raw.decode("ascii")
        except (ValueError, UnicodeError):
            raise SourceBundleError("bundle header record differs") from None
        references.append((oid, ref))
    if references != [(contract.tip, contract.ref)]:
        raise SourceBundleError("bundle ref inventory differs")


def _initialize_store(
    *,
    executable: Path,
    environment: Mapping[str, str],
    stage: Path,
    repository: Path,
    bundle: Path,
    contract: _BundleContract,
) -> None:
    template = stage / "empty-template"
    template.mkdir(mode=0o700)
    _run(
        [
            str(executable),
            "init",
            "--bare",
            "--object-format=sha1",
            "--initial-branch=verification-placeholder",
            f"--template={template}",
            str(repository),
        ],
        environment=environment,
        cwd=stage,
    )
    _run(
        [*_git_base(executable, repository), "bundle", "verify", str(bundle)],
        environment=environment,
        cwd=stage,
    )
    heads = _run(
        [*_git_base(executable, repository), "bundle", "list-heads", str(bundle)],
        environment=environment,
        cwd=stage,
    ).stdout.decode("ascii", "strict").splitlines()
    if heads != [f"{contract.tip} {contract.ref}"]:
        raise SourceBundleError("bundle head listing differs")
    _run(
        [
            *_git_base(executable, repository),
            "fetch",
            "--atomic",
            "--no-tags",
            "--no-recurse-submodules",
            "--no-write-fetch-head",
            "--no-auto-maintenance",
            "--no-auto-gc",
            "--no-write-commit-graph",
            str(bundle),
            f"{contract.ref}:{contract.ref}",
        ],
        environment=environment,
        cwd=stage,
    )
    _run(
        [*_git_base(executable, repository), "symbolic-ref", "HEAD", contract.ref],
        environment=environment,
        cwd=stage,
    )
    # Reverse indexes are an optional Git implementation detail.  Removing
    # them from this newly built, unpublished store leaves one deterministic
    # minimal administrative layout that can be revalidated exactly.
    pack_directory = repository / "objects" / "pack"
    for reverse_index in pack_directory.glob("pack-*.rev"):
        reverse_index.unlink()


def _text(
    executable: Path,
    repository: Path,
    environment: Mapping[str, str],
    cwd: Path,
    *arguments: str,
) -> str:
    raw = _run(
        [*_git_base(executable, repository), *arguments],
        environment=environment,
        cwd=cwd,
    ).stdout
    try:
        return raw.decode("ascii").strip()
    except UnicodeError as exc:
        raise SourceBundleError("Git metadata is not ASCII") from exc


def _validate_config(
    executable: Path,
    repository: Path,
    environment: Mapping[str, str],
    cwd: Path,
) -> None:
    raw = _run(
        [
            *_git_base(executable, repository),
            "config",
            "--local",
            "--null",
            "--list",
        ],
        environment=environment,
        cwd=cwd,
    ).stdout
    observed: dict[str, list[str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            name_raw, value_raw = record.split(b"\n", 1)
            name = name_raw.decode("utf-8").casefold()
            value = value_raw.decode("utf-8")
        except (ValueError, UnicodeError):
            raise SourceBundleError("source store config is malformed") from None
        observed.setdefault(name, []).append(value)
    normalized = {key: tuple(values) for key, values in observed.items()}
    if normalized != _ALLOWED_CONFIG:
        raise SourceBundleError("source store config differs")


def _validate_repository_state(
    repository: Path, contract: _BundleContract
) -> None:
    forbidden = (
        repository / "FETCH_HEAD",
        repository / "ORIG_HEAD",
        repository / "shallow",
        repository / "info" / "grafts",
        repository / "objects" / "info" / "alternates",
        repository / "logs",
    )
    if any(path.exists() or path.is_symlink() for path in forbidden):
        raise SourceBundleError("source store contains forbidden Git state")
    hooks = repository / "hooks"
    if hooks.exists() and any(hooks.iterdir()):
        raise SourceBundleError("source store contains hooks")
    observed_directories: set[str] = set()
    observed_files: set[str] = set()
    observed_bytes = 0
    for parent, directories, files in os.walk(repository, followlinks=False):
        parent_path = Path(parent)
        if parent_path.is_symlink():
            raise SourceBundleError("source store contains a symlink")
        for name in directories:
            path = parent_path / name
            if path.is_symlink():
                raise SourceBundleError("source store contains a symlink")
            observed_directories.add(path.relative_to(repository).as_posix())
        for name in files:
            path = parent_path / name
            info = path.stat(follow_symlinks=False)
            if path.is_symlink() or not stat.S_ISREG(info.st_mode):
                raise SourceBundleError("source store contains a non-regular file")
            if info.st_nlink != 1:
                raise SourceBundleError("source store file link count differs")
            if name.endswith(".promisor") or name.endswith(".lock"):
                raise SourceBundleError("source store contains forbidden object state")
            relative = path.relative_to(repository).as_posix()
            observed_files.add(relative)
            observed_bytes += info.st_size
            if (
                (relative in {"HEAD", "config", contract.ref} and info.st_size > 64 * 1024)
                or (relative.endswith(".pack") and info.st_size > contract.bundle_bytes)
                or (relative.endswith(".idx") and info.st_size > _MAX_GIT_OUTPUT_BYTES)
                or observed_bytes > contract.bundle_bytes + _MAX_GIT_OUTPUT_BYTES
            ):
                raise SourceBundleError("source store file size bound differs")

    ref = PurePosixPath(contract.ref)
    if ref.parts[:2] != ("refs", "heads"):
        raise SourceBundleError("source store ref path differs")
    expected_directories = {
        "objects",
        "objects/info",
        "objects/pack",
        "refs",
        "refs/heads",
        "refs/tags",
    }
    parent = ref.parent
    while str(parent) not in {".", "refs/heads"}:
        expected_directories.add(str(parent))
        parent = parent.parent
    fixed_files = {"HEAD", "config", str(ref)}
    pack_files = observed_files - fixed_files
    if (
        observed_directories != expected_directories
        or len(pack_files) != 2
        or any(
            not path.startswith("objects/pack/")
            or _PACK_FILE.fullmatch(PurePosixPath(path).name) is None
            for path in pack_files
        )
    ):
        raise SourceBundleError("source store filesystem layout differs")
    stems = {PurePosixPath(path).stem for path in pack_files}
    suffixes = {PurePosixPath(path).suffix for path in pack_files}
    if len(stems) != 1 or suffixes != {".idx", ".pack"}:
        raise SourceBundleError("source store pack layout differs")


def _validate_store_with_contract(
    repository: Path,
    *,
    contract: _BundleContract,
    executable: Path,
    environment: Mapping[str, str],
    cwd: Path,
    sealed: bool,
    owner_uid: int,
    owner_gid: int,
    sealed_directory_mode: int = 0o500,
    sealed_file_mode: int = 0o400,
) -> SourceStoreProof:
    _validate_repository_state(repository, contract)
    if sealed:
        for parent, directories, files in os.walk(repository, followlinks=False):
            parent_path = Path(parent)
            parent_info = parent_path.stat(follow_symlinks=False)
            if (
                parent_info.st_uid != owner_uid
                or parent_info.st_gid != owner_gid
                or stat.S_IMODE(parent_info.st_mode) != sealed_directory_mode
            ):
                raise SourceBundleError("sealed source store directory differs")
            for name in directories:
                info = (parent_path / name).stat(follow_symlinks=False)
                if (
                    info.st_uid != owner_uid
                    or info.st_gid != owner_gid
                    or stat.S_IMODE(info.st_mode) != sealed_directory_mode
                ):
                    raise SourceBundleError("sealed source store directory differs")
            for name in files:
                info = (parent_path / name).stat(follow_symlinks=False)
                if (
                    info.st_uid != owner_uid
                    or info.st_gid != owner_gid
                    or stat.S_IMODE(info.st_mode) != sealed_file_mode
                ):
                    raise SourceBundleError("sealed source store file differs")
    if (
        _text(
            executable,
            repository,
            environment,
            cwd,
            "rev-parse",
            "--show-object-format",
        )
        != "sha1"
    ):
        raise SourceBundleError("source store object format differs")
    head = _text(executable, repository, environment, cwd, "symbolic-ref", "HEAD")
    if head != contract.ref:
        raise SourceBundleError("source store HEAD differs")
    refs_raw = _run(
        [
            *_git_base(executable, repository),
            "for-each-ref",
            "--format=%(refname)%00%(objectname)",
        ],
        environment=environment,
        cwd=cwd,
    ).stdout
    try:
        refs = [tuple(line.split("\0", 1)) for line in refs_raw.decode("ascii").splitlines()]
    except UnicodeError as exc:
        raise SourceBundleError("source store ref inventory is invalid") from exc
    if refs != [(contract.ref, contract.tip)]:
        raise SourceBundleError("source store ref inventory differs")
    _validate_config(executable, repository, environment, cwd)
    _run(
        [
            *_git_base(executable, repository),
            "fsck",
            "--full",
            "--strict",
            "--no-reflogs",
            "--no-dangling",
            "--no-progress",
            contract.tip,
        ],
        environment=environment,
        cwd=cwd,
    )
    reachable = _text(
        executable,
        repository,
        environment,
        cwd,
        "rev-list",
        "--objects",
        "--no-object-names",
        contract.tip,
    ).splitlines()
    reachable_set = set(reachable)
    if len(reachable) != len(reachable_set):
        raise SourceBundleError("reachable object inventory contains duplicates")
    object_lines = _text(
        executable,
        repository,
        environment,
        cwd,
        "cat-file",
        "--batch-all-objects",
        "--batch-check=%(objectname) %(objecttype)",
    ).splitlines()
    objects: dict[str, str] = {}
    for line in object_lines:
        try:
            oid, object_type = line.split(" ", 1)
        except ValueError:
            raise SourceBundleError("source store object inventory is malformed") from None
        if oid in objects:
            raise SourceBundleError("source store object inventory contains duplicates")
        objects[oid] = object_type
    if set(objects) != reachable_set:
        raise SourceBundleError("source store contains hidden or missing objects")
    object_digest = hashlib.sha256(
        "".join(f"{oid}\n" for oid in sorted(reachable_set)).encode("ascii")
    ).hexdigest()
    type_counts = dict(Counter(objects.values()))
    if (
        len(objects) != contract.object_count
        or object_digest != contract.object_set_sha256
        or type_counts != dict(contract.object_type_counts)
    ):
        raise SourceBundleError("source store object commitment differs")
    commit_count = int(
        _text(
            executable,
            repository,
            environment,
            cwd,
            "rev-list",
            "--count",
            contract.tip,
        )
    )
    tree = _text(
        executable, repository, environment, cwd, "rev-parse", f"{contract.tip}^{{tree}}"
    )
    parents = _text(
        executable,
        repository,
        environment,
        cwd,
        "show",
        "-s",
        "--format=%P",
        contract.tip,
    ).split()
    parent_tree = _text(
        executable, repository, environment, cwd, "rev-parse", f"{contract.parent}^{{tree}}"
    )
    if (
        commit_count != contract.commit_count
        or tree != contract.tip_tree
        or parents != [contract.parent]
        or parent_tree != contract.parent_tree
    ):
        raise SourceBundleError("source store lineage differs")
    for commit, expected_tree in contract.milestone_pairs:
        if (
            _text(executable, repository, environment, cwd, "cat-file", "-t", commit)
            != "commit"
            or _text(
                executable, repository, environment, cwd, "rev-parse", f"{commit}^{{tree}}"
            )
            != expected_tree
        ):
            raise SourceBundleError("source store milestone pair differs")
        ancestor = _run(
            [
                *_git_base(executable, repository),
                "merge-base",
                "--is-ancestor",
                commit,
                contract.tip,
            ],
            environment=environment,
            cwd=cwd,
            check=False,
        )
        if ancestor.returncode != 0:
            raise SourceBundleError("source store milestone ancestry differs")
    forbidden = _run(
        [
            *_git_base(executable, repository),
            "cat-file",
            "-e",
            f"{contract.forbidden_object}^{{object}}",
        ],
        environment=environment,
        cwd=cwd,
        check=False,
    )
    if forbidden.returncode == 0:
        raise SourceBundleError("forbidden source object is present")
    ls_tree = _run(
        [
            *_git_base(executable, repository),
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            contract.tip,
        ],
        environment=environment,
        cwd=cwd,
    ).stdout
    entries = [entry for entry in ls_tree.split(b"\0") if entry]
    modes: Counter[str] = Counter()
    for entry in entries:
        try:
            metadata, _path = entry.split(b"\t", 1)
            mode_raw, type_raw, _oid = metadata.split(b" ", 2)
            mode = mode_raw.decode("ascii")
            object_type = type_raw.decode("ascii")
        except (ValueError, UnicodeError):
            raise SourceBundleError("tip tree inventory is malformed") from None
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise SourceBundleError("tip tree contains a forbidden entry type")
        modes[mode] += 1
    ls_tree_digest = hashlib.sha256(ls_tree).hexdigest()
    if (
        len(entries) != contract.ls_tree_entries
        or ls_tree_digest != contract.ls_tree_sha256
        or dict(modes) != dict(contract.mode_counts)
    ):
        raise SourceBundleError("tip tree commitment differs")
    return SourceStoreProof(
        ref=contract.ref,
        tip=contract.tip,
        tree=tree,
        parent=contract.parent,
        parent_tree=parent_tree,
        object_count=len(objects),
        object_set_sha256=object_digest,
        object_type_counts=type_counts,
        commit_count=commit_count,
        ls_tree_sha256=ls_tree_digest,
        ls_tree_entries=len(entries),
        mode_counts=dict(modes),
    )


def _seal_store(
    repository: Path,
    *,
    owner_uid: int,
    owner_gid: int,
    directory_mode: int = 0o500,
    file_mode: int = 0o400,
) -> None:
    paths: list[Path] = []
    for parent, directories, files in os.walk(repository, topdown=False, followlinks=False):
        parent_path = Path(parent)
        paths.extend(parent_path / name for name in files)
        paths.extend(parent_path / name for name in directories)
    for path in paths:
        info = path.stat(follow_symlinks=False)
        if path.is_symlink():
            raise SourceBundleError("source store contains a symlink")
        mode = directory_mode if stat.S_ISDIR(info.st_mode) else file_mode
        os.chmod(path, mode, follow_symlinks=False)
        os.chown(path, owner_uid, owner_gid, follow_symlinks=False)
    os.chmod(repository, directory_mode)
    os.chown(repository, owner_uid, owner_gid)


def _build_unpublished_with_contract(
    bundle_path: str | Path,
    *,
    repository_path: str | Path,
    scratch_root: str | Path,
    owner_uid: int,
    owner_gid: int,
    source_uid: int,
    source_gid: int,
    contract: _BundleContract,
    git_executable: str | Path = "/usr/bin/git",
    sealed_directory_mode: int = 0o500,
    sealed_file_mode: int = 0o400,
) -> SourceStoreProof:
    """Build a sealed store inside an unpublished, supervisor-owned release."""

    executable = _trusted_git(git_executable)
    source = Path(bundle_path)
    repository = Path(repository_path)
    scratch = Path(scratch_root)
    if not source.is_absolute() or not repository.is_absolute() or not scratch.is_absolute():
        raise SourceBundleError("source store build path differs")
    _validate_private_parent(
        source.parent, expected_uid=source_uid, expected_gid=source_gid
    )
    _validate_private_parent(
        scratch, expected_uid=os.geteuid(), expected_gid=os.getegid()
    )
    _validate_private_parent(
        repository.parent, expected_uid=os.geteuid(), expected_gid=os.getegid()
    )
    if repository.exists() or repository.is_symlink():
        raise SourceBundleError("source store destination already exists")
    runtime = Path(tempfile.mkdtemp(prefix=".git-runtime.", dir=scratch))
    staged_bundle = runtime / "accepted.bundle"
    completed = False
    try:
        os.chmod(runtime, 0o700)
        environment = _git_environment(runtime)
        _copy_bundle_from_descriptor(
            source,
            staged_bundle,
            contract=contract,
            source_uid=source_uid,
            source_gid=source_gid,
        )
        _parse_bundle_header(staged_bundle, contract)
        _initialize_store(
            executable=executable,
            environment=environment,
            stage=runtime,
            repository=repository,
            bundle=staged_bundle,
            contract=contract,
        )
        proof = _validate_store_with_contract(
            repository,
            contract=contract,
            executable=executable,
            environment=environment,
            cwd=runtime,
            sealed=False,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
        )
        staged_bundle.unlink()
        _seal_store(
            repository,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            directory_mode=sealed_directory_mode,
            file_mode=sealed_file_mode,
        )
        _validate_store_with_contract(
            repository,
            contract=contract,
            executable=executable,
            environment=environment,
            cwd=runtime,
            sealed=True,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            sealed_directory_mode=sealed_directory_mode,
            sealed_file_mode=sealed_file_mode,
        )
        completed = True
        return proof
    except ResearchTransferError as exc:
        raise SourceBundleError(str(exc)) from exc
    finally:
        if runtime.exists():
            _remove_private_tree(runtime)
        if not completed and repository.exists():
            _remove_private_tree(repository)


def validate_accepted_source_store(
    store_path: str | Path,
    *,
    owner_uid: int,
    owner_gid: int,
) -> SourceStoreProof:
    """Revalidate an already sealed accepted bare store without mutating it."""

    repository = Path(store_path)
    if not repository.is_absolute() or repository.name != "source.git":
        raise SourceBundleError("source store path differs")
    try:
        resolved = repository.resolve(strict=True)
    except OSError as exc:
        raise SourceBundleError("source store is unavailable") from exc
    if repository != resolved or repository.is_symlink():
        raise SourceBundleError("source store path differs")
    temporary = Path(tempfile.mkdtemp(prefix=".source-store-validation."))
    try:
        os.chmod(temporary, 0o700)
        environment = _git_environment(temporary)
        return _validate_store_with_contract(
            repository,
            contract=_ACCEPTED_CONTRACT,
            executable=_trusted_git(),
            environment=environment,
            cwd=temporary,
            sealed=True,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            sealed_directory_mode=0o550,
            sealed_file_mode=0o440,
        )
    finally:
        _remove_private_tree(temporary)


def _remove_private_tree(path: Path) -> None:
    if not path.exists():
        return
    for parent, directories, _files in os.walk(path, topdown=False, followlinks=False):
        parent_path = Path(parent)
        for name in directories:
            child = parent_path / name
            if not child.is_symlink():
                os.chmod(child, 0o700)
        if not parent_path.is_symlink():
            os.chmod(parent_path, 0o700)
    shutil.rmtree(path)


__all__ = [
    "ACCEPTED_BUNDLE_BYTES",
    "ACCEPTED_BUNDLE_FILENAME",
    "ACCEPTED_BUNDLE_SHA256",
    "ACCEPTED_OBJECT_COUNT",
    "ACCEPTED_OBJECT_SET_SHA256",
    "ACCEPTED_REF",
    "ACCEPTED_TIP",
    "SourceBundleError",
    "SourceStoreProof",
    "validate_accepted_source_store",
]
