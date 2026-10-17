"""Read-only source binding for the approved hosted synthetic checkpoint.

This is a portable, non-authorizing component of Task 2. The eventual workflow
must assert trusted event/ref/SHA metadata before invoking repository code.
Neither this result nor an earlier job's result grants containment or release
authority. No GitHub API, network, credential, database, or payload is used.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess


REPOSITORY = "Vedang1998/Buffalo-Procurement-System"
REF = "refs/heads/codex/hosted-synthetic-acceptance"
BASELINE = "cb0969059b0b85241d611124910d691e49212881"
WORKFLOW = ".github/workflows/hosted-synthetic-acceptance.yml"
SOURCE_PATHS = (
    WORKFLOW,
    "procurement/tools/hosted_synthetic_source.py",
    "procurement/tools/run_local_staging_acceptance.py",
    "procurement/tools/run_local_staging_browser_worker.py",
    "procurement/pyproject.toml",
    "pyproject.toml",
    "uv.lock",
)
_OID = re.compile(r"[0-9a-f]{40}\Z")
_MAX_SOURCE_BYTES = 16 * 1024 * 1024


class HostedSourceError(RuntimeError):
    """A fixed diagnostic only; never include command output or environment."""


@dataclass(frozen=True)
class HostedSourceBinding:
    commit: str
    tree: str
    files: tuple[tuple[str, str], ...]

    @property
    def execution_authority(self) -> bool:
        return False


def _git(root: Path, *arguments: str, absent_ok: bool = False) -> bytes:
    # No inherited Git configuration, libpq variables, credentials, hooks, or
    # credential helper is needed for these strictly local read-only commands.
    environment = {
        "PATH": os.defpath,
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_ALLOW_PROTOCOL": "",
    }
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
             "-c", "core.untrackedCache=false", "-c", "protocol.allow=never",
             "-C", str(root), *arguments],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise HostedSourceError("source_git_unavailable") from None
    if result.returncode != 0:
        if absent_ok and result.returncode == 1 and not result.stdout:
            return b""
        raise HostedSourceError("source_git_refused")
    if len(result.stdout) > _MAX_SOURCE_BYTES:
        raise HostedSourceError("source_git_output_limit")
    return result.stdout


def _oid(raw: bytes) -> str:
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError:
        raise HostedSourceError("source_oid_invalid") from None
    if not value.endswith("\n") or _OID.fullmatch(value[:-1]) is None:
        raise HostedSourceError("source_oid_invalid")
    return value[:-1]


def _file_bytes(root: Path, relative: str) -> bytes:
    path = root / relative
    # Refuse symlinks at every component, including ignored replacements that
    # git status may hide when assume-unchanged/skip-worktree bits are set.
    if path.resolve() != path:
        raise HostedSourceError("source_path_indirection")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size > _MAX_SOURCE_BYTES):
                raise HostedSourceError("source_file_invalid")
            chunks = []
            remaining = before.st_size + 1
            while remaining:
                block = os.read(descriptor, min(remaining, 65536))
                if not block:
                    break
                chunks.append(block)
                remaining -= len(block)
            raw = b"".join(chunks)
            after = os.fstat(descriptor)
            named = path.lstat()
            identity = lambda info: (
                info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            )
            if (len(raw) != before.st_size or identity(before) != identity(after)
                    or identity(after) != identity(named)):
                raise HostedSourceError("source_file_changed")
            return raw
        finally:
            os.close(descriptor)
    except OSError:
        raise HostedSourceError("source_file_unavailable") from None


def _tree_records(root: Path) -> tuple[tuple[str, bytes, bytes], ...]:
    records = []
    for entry in _git(root, "ls-tree", "-r", "-z", "--full-tree", "HEAD").split(b"\0")[:-1]:
        try:
            header, name = entry.split(b"\t", 1)
            mode, kind, blob = header.split(b" ")
            relative = name.decode("utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError):
            raise HostedSourceError("source_blob_invalid") from None
        if (mode not in {b"100644", b"100755"} or kind != b"blob"
                or _OID.fullmatch(blob.decode("ascii", errors="replace")) is None
                or not relative or Path(relative).is_absolute()
                or any(part in {"", ".", ".."} for part in relative.split("/"))):
            raise HostedSourceError("source_blob_invalid")
        records.append((relative, mode, blob))
    if not set(SOURCE_PATHS).issubset(item[0] for item in records):
        raise HostedSourceError("source_blob_missing")
    return tuple(records)


def _check_index(root: Path, records: tuple[tuple[str, bytes, bytes], ...]) -> None:
    # `git status` can execute repository-local clean/process filters. Plumbing
    # plus raw blob verification avoids executing any configured filter.
    flags = _git(root, "ls-files", "-v", "-z").split(b"\0")[:-1]
    if any(not entry.startswith(b"H ") for entry in flags):
        raise HostedSourceError("source_index_flags_invalid")
    expected = b"".join(
        mode + b" " + blob + b" 0\t" + relative.encode("utf-8") + b"\0"
        for relative, mode, blob in sorted(records)
    )
    if (_git(root, "ls-files", "--stage", "-z") != expected
            or _git(root, "ls-files", "--others", "--exclude-standard", "-z")):
        raise HostedSourceError("source_checkout_dirty")


def _bind_files(root: Path, records: tuple[tuple[str, bytes, bytes], ...]) -> tuple[tuple[str, str], ...]:
    bindings = {}
    for relative, mode, blob in records:
        raw = _file_bytes(root, relative)
        expected_blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw)
        if expected_blob.hexdigest().encode("ascii") != blob:
            raise HostedSourceError("source_bytes_mismatch")
        executable = bool((root / relative).stat().st_mode & 0o111)
        if executable != (mode == b"100755"):
            raise HostedSourceError("source_mode_mismatch")
        if relative in SOURCE_PATHS:
            bindings[relative] = hashlib.sha256(raw).hexdigest()
    return tuple((path, bindings[path]) for path in SOURCE_PATHS)


def bind_hosted_source(
    checkout: Path, *, repository: str, event: str, ref: str, sha: str,
) -> HostedSourceBinding:
    """Bind one clean detached checkout; refuse absent/uncommitted workflow.

    Metadata must come from trusted workflow context, never a PR body or an
    artifact. This function performs no checkout, fetch, reset, or mutation.
    A caller must rebind in the same generation immediately before release.
    """
    if (repository != REPOSITORY or event != "push" or ref != REF
            or type(sha) is not str or _OID.fullmatch(sha) is None):
        raise HostedSourceError("source_event_invalid")
    root = checkout.absolute()
    if root.resolve() != root or not root.is_dir():
        raise HostedSourceError("source_root_invalid")
    root_identity = root.stat()
    if _git(root, "rev-parse", "--show-toplevel") != os.fsencode(root) + b"\n":
        raise HostedSourceError("source_root_mismatch")
    git_dir = root / ".git"
    if (git_dir.resolve() != git_dir or not git_dir.is_dir()
            or (git_dir / "info" / "grafts").exists()
            or (git_dir / "info" / "grafts").is_symlink()):
        raise HostedSourceError("source_git_layout_invalid")
    if _git(root, "symbolic-ref", "--quiet", "HEAD", absent_ok=True):
        raise HostedSourceError("source_checkout_not_detached")
    if _oid(_git(root, "rev-parse", "HEAD")) != sha:
        raise HostedSourceError("source_commit_mismatch")
    _git(root, "merge-base", "--is-ancestor", BASELINE, sha)
    tree = _oid(_git(root, "rev-parse", "HEAD^{tree}"))
    records = _tree_records(root)
    _check_index(root, records)
    bindings = _bind_files(root, records)
    # Repeat every tracked file, not only the hashes retained in the bounded
    # report. This is a snapshot, not an immutable runtime/projection lease.
    if _bind_files(root, records) != bindings:
        raise HostedSourceError("source_checkout_changed")
    _check_index(root, records)
    if (_oid(_git(root, "rev-parse", "HEAD")) != sha
            or _oid(_git(root, "rev-parse", "HEAD^{tree}")) != tree):
        raise HostedSourceError("source_checkout_changed")
    final_root = root.stat()
    if (root_identity.st_dev, root_identity.st_ino) != (final_root.st_dev, final_root.st_ino):
        raise HostedSourceError("source_root_changed")
    return HostedSourceBinding(sha, tree, bindings)
