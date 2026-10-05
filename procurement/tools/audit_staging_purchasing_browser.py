#!/usr/bin/env python3
"""Run one bounded Chromium phase against the real staging gateway.

The caller owns Chromium, TLS, and service lifecycle.  This module supplies the
small reusable boundary that drives CDP without putting the owner passphrase in
argv, the environment, evidence, or output.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import threading
from typing import Any, Mapping, NamedTuple
from urllib.parse import urlsplit
from uuid import UUID

BROWSER_PHASE_CONTRACT = "BUFFALO_STAGING_PURCHASING_BROWSER_PHASE_V1"
PRICE_CONFIRM_PHASE = "price-confirm"
LOCAL_TLS_HOST = "staging.example.test"
LOCAL_TLS_ORIGIN = f"https://{LOCAL_TLS_HOST}"
REGISTERED_OPERATOR_BOOK_BYTES = 1590
REGISTERED_OPERATOR_BOOK_SHA256 = (
    "00071443ea8c54b57fc6014c3b1daf204081714a2ff09b98bed6c56a0dd3862c"
)
REPO_ROOT = Path(__file__).resolve().parents[2]
_DRIVER = Path(__file__).with_suffix(".mjs")
_TRUSTED_GIT = Path("/usr/bin/git")
_EXPECTED_NODE_EXECUTABLE = Path(
    "/nix/store/9cyx2v23dip6p9q98384k9v06c96qskb-nodejs-24.13.0/bin/node"
)
_EXPECTED_NODE_VERSION = "v24.13.0"
_EXPECTED_NODE_SHA256 = (
    "f360fb1d3e009edb293084a528ef5b96c3f8ec1167d0634f8975b0ecda4adb28"
)
_EXPECTED_CHROMIUM_EXECUTABLE = Path(
    "/repl/ctls/bvajdx8xxf05dqrzwxgrlaxv0ryigzix-chromium-unwrapped-152.0.7977.64/"
    "libexec/chromium/chromium"
)
_HOST_RESOLVER_RULES = (
    f"--host-resolver-rules=MAP {LOCAL_TLS_HOST} 127.0.0.1, MAP * 0.0.0.0"
)
_REQUIRED_CHROMIUM_ARGUMENTS = frozenset(
    {
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-gpu",
        "--disable-sync",
        "--dns-prefetch-disable",
        "--enable-automation",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--headless=new",
        _HOST_RESOLVER_RULES,
        "--incognito",
        "--metrics-recording-only",
        "--no-default-browser-check",
        "--no-first-run",
        "--proxy-bypass-list=*",
        "--proxy-server=direct://",
    }
)
_EXPECTED_CHROMIUM_STATIC_ENVIRONMENT = {
    "CHROME_DEVEL_SANDBOX": (
        "/repl/ctls/257kyf3i1cjkbi03nhsrq59r4j1cf12c-"
        "chromium-152.0.7977.64-sandbox/bin/__chromium-suid-sandbox"
    ),
    "CHROME_WRAPPER": "chromium",
    "FONTCONFIG_FILE": "/repl/ctls/gma8hbn7cqzrh4c1k4y1jhmfrz5f942w-fonts.conf",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "LD_LIBRARY_PATH": (
        "/repl/ctls/6bx68shkhjj08kl26aslrlnc9f21ma0h-libva-2.24.1/lib:"
        "/repl/ctls/ihhpc5fqgwym99dadasw034k9qq76k9s-pipewire-1.6.8/lib:"
        "/repl/ctls/vjcwhp0milwa1jqy1inpjxrsh8hjrgwc-wayland-1.26.0/lib:"
        "/repl/ctls/fl089m700c7zxqwgly18jyx0fs4lgbbj-gtk+3-3.24.52/lib:"
        "/repl/ctls/4j905cjgk8idkzg4szhyryzjx2q1b6br-gtk4-4.22.4/lib:"
        "/repl/ctls/adhjdycq6m1n6ljzm9ifp843y2my6zd8-krb5-1.22.2-lib/lib"
    ),
    "LD_PRELOAD": "",
    "PATH": (
        "/repl/tools/bin:/usr/bin:/bin:"
        "/repl/ctls/cadc1hk45xzbwcxrlpfrl89912i5fq76-xdg-utils-1.2.1/bin"
    ),
    "SHLVL": "0",
    "XDG_DATA_DIRS": (
        "/repl/ctls/ns769gwc5z8a1vlkiyz3s1c77g97yksc-cups-2.4.19/share:"
        "/repl/ctls/fl089m700c7zxqwgly18jyx0fs4lgbbj-gtk+3-3.24.52/share:"
        "/repl/ctls/4j905cjgk8idkzg4szhyryzjx2q1b6br-gtk4-4.22.4/share:"
        "/repl/ctls/vq0h5xk0sx57s22djpsq25ny813w3sbi-adwaita-icon-theme-50.0/share:"
        "/repl/ctls/1azcjkmc5ba1srfp9agqx1k074vxhcxs-hicolor-icon-theme-0.18/share:"
        "/repl/ctls/w7z6y1f2gnm6qx8gpkxzvbnxsw3b3svl-"
        "gsettings-desktop-schemas-50.1/share/gsettings-schemas/"
        "gsettings-desktop-schemas-50.1:"
        "/repl/ctls/fl089m700c7zxqwgly18jyx0fs4lgbbj-gtk+3-3.24.52/share/"
        "gsettings-schemas/gtk+3-3.24.52:"
        "/repl/ctls/4j905cjgk8idkzg4szhyryzjx2q1b6br-gtk4-4.22.4/share/"
        "gsettings-schemas/gtk4-4.22.4"
    ),
}
_EXPECTED_CHROMIUM_ENVIRONMENT_KEYS = frozenset(
    {*_EXPECTED_CHROMIUM_STATIC_ENVIRONMENT, "HOME", "PWD", "TMPDIR"}
)
_MAX_LOG_BYTES = 64 * 1024
_MAX_PROOF_BYTES = 8 * 1024
_MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024
_PASSPHRASE = re.compile(r"\A[A-Za-z0-9_-]{43}\Z")
_GIT_ID = re.compile(r"\A[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_BROWSER_PRODUCT = re.compile(r"\A(?:Chrome|HeadlessChrome)/[0-9]+(?:\.[0-9]+)+\Z")
_EXPECTED_ASSERTION_COUNT = 62
_EXPECTED_ASSERTION_MANIFEST_SHA256 = (
    "64a5063520cbe378502b7930f8b51ba784b05c0e9c2ea986de9adeda991efb50"
)
_TARGET_SUMMARY_KEYS = frozenset(
    {
        "active_guarded",
        "guarded",
        "inert",
        "live_detached",
        "tracked",
        "unattached",
        "unguarded",
        "unresumed",
        "unsupported",
    }
)
_PROOF_KEYS = frozenset(
    {
        "assertion_count",
        "assertion_manifest_sha256",
        "batch_id",
        "browser_js_version",
        "browser_pid",
        "browser_product",
        "browser_protocol_version",
        "browser_start_time",
        "confirmation_preview_sha256",
        "contract",
        "driver_sha256",
        "node_sha256",
        "node_version",
        "operational_status_after",
        "operational_status_before",
        "operator_proof_sha256",
        "phase",
        "raw_bytes",
        "raw_sha256",
        "screenshot_bytes",
        "screenshot_sha256",
        "source_commit",
        "source_tree",
        "status_after",
        "status_before",
        "target_summary",
        "temporal_basis",
        "tls_certificate_sha256",
    }
)


class StagingBrowserAuditError(RuntimeError):
    """The staging browser phase or its proof differs from the contract."""


class _StagingBrowserInterrupted(BaseException):
    pass


class _TrustedNode(NamedTuple):
    path: Path
    version: str
    sha256: str
    descriptor: int
    identity: tuple[int, int, int, int]


def _validate_bound_operator_proof(value: Mapping[str, object]) -> dict[str, object]:
    try:
        from procurement_os.synthetic_price_replacement_contract import (
            REGISTERED_OPERATOR_BOOK_BYTES as canonical_bytes,
            REGISTERED_OPERATOR_BOOK_SHA256 as canonical_sha256,
        )
        from procurement_os.synthetic_staging_price_stage import (
            SyntheticStagingPriceStageError,
            _validate_operator_proof,
        )
    except Exception as exc:
        raise StagingBrowserAuditError(
            "staging operator proof dependency differs"
        ) from exc
    if (
        canonical_bytes != REGISTERED_OPERATOR_BOOK_BYTES
        or canonical_sha256 != REGISTERED_OPERATOR_BOOK_SHA256
    ):
        raise StagingBrowserAuditError("staging operator proof dependency differs")
    try:
        return _validate_operator_proof(dict(value))
    except SyntheticStagingPriceStageError as exc:
        raise StagingBrowserAuditError("staging operator proof differs") from exc


def _read_bound_driver() -> tuple[bytes, int, str]:
    source = -1
    try:
        source = os.open(
            _DRIVER,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(source)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size < 1
            or before.st_size > 256 * 1024
        ):
            raise StagingBrowserAuditError("staging browser driver differs")
        digest = hashlib.sha256()
        content = bytearray()
        observed = 0
        while True:
            block = os.read(source, 64 * 1024)
            if not block:
                break
            observed += len(block)
            if observed > 256 * 1024:
                raise StagingBrowserAuditError("staging browser driver differs")
            digest.update(block)
            content.extend(block)
        after = os.fstat(source)
        if (
            observed != before.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise StagingBrowserAuditError("staging browser driver differs")
        bytes(content).decode("utf-8", "strict")
        return bytes(content), observed, digest.hexdigest()
    except (StagingBrowserAuditError, UnicodeError):
        raise StagingBrowserAuditError("staging browser driver differs") from None
    except OSError as exc:
        raise StagingBrowserAuditError(
            "staging browser driver is unavailable"
        ) from exc
    finally:
        if source >= 0:
            os.close(source)


def _sha256_descriptor(
    descriptor: int,
    *,
    maximum_bytes: int,
    difference_message: str,
) -> tuple[int, str, tuple[int, int, int, int]]:
    digest = hashlib.sha256()
    observed = 0
    try:
        before = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            block = os.read(
                descriptor,
                min(1024 * 1024, maximum_bytes + 1 - observed),
            )
            if not block:
                break
            observed += len(block)
            if observed > maximum_bytes:
                raise StagingBrowserAuditError(difference_message)
            digest.update(block)
        after = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        raise StagingBrowserAuditError(difference_message) from exc
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if (
        not stat.S_ISREG(before.st_mode)
        or identity
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or observed != before.st_size
    ):
        raise StagingBrowserAuditError(difference_message)
    return observed, digest.hexdigest(), identity


def _sha256_file(
    path: Path,
    *,
    maximum_bytes: int,
    difference_message: str = "staging browser driver differs",
) -> tuple[int, str]:
    digest = hashlib.sha256()
    observed = 0
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StagingBrowserAuditError(difference_message) from exc
    try:
        before = os.fstat(descriptor)
        while True:
            block = os.read(
                descriptor,
                min(1024 * 1024, maximum_bytes + 1 - observed),
            )
            if not block:
                break
            observed += len(block)
            if observed > maximum_bytes:
                raise StagingBrowserAuditError("staging browser evidence exceeds limit")
            digest.update(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or observed != before.st_size
    ):
        raise StagingBrowserAuditError(difference_message)
    return observed, digest.hexdigest()


def _git_stdout(repo_root: Path, *arguments: str) -> bytes:
    try:
        info = _TRUSTED_GIT.stat(follow_symlinks=False)
    except OSError as exc:
        raise StagingBrowserAuditError(
            "staging browser source identity is unavailable"
        ) from exc
    if (
        not _TRUSTED_GIT.is_absolute()
        or _TRUSTED_GIT.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o022
        or not os.access(_TRUSTED_GIT, os.X_OK)
    ):
        raise StagingBrowserAuditError("staging browser Git identity differs")
    environment = {
        "GIT_ASKPASS": "/bin/false",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": str(_TRUSTED_GIT.parent),
        "XDG_CONFIG_HOME": "/nonexistent",
    }
    try:
        completed = subprocess.run(
            (
                str(_TRUSTED_GIT),
                "--no-replace-objects",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "protocol.allow=never",
                "-C",
                str(repo_root),
                *arguments,
            ),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StagingBrowserAuditError(
            "staging browser source identity is unavailable"
        ) from exc
    if completed.returncode != 0:
        raise StagingBrowserAuditError(
            "staging browser source identity is unavailable"
        )
    return completed.stdout


def _git_blob_oid(value: bytes) -> str:
    header = f"blob {len(value)}\0".encode("ascii")
    return hashlib.sha1(header + value, usedforsecurity=False).hexdigest()


def _verify_driver_snapshot(*, commit: str, content: bytes) -> None:
    relative = _DRIVER.relative_to(REPO_ROOT).as_posix()
    entry = _git_stdout(
        REPO_ROOT,
        "ls-tree",
        "-z",
        commit,
        "--",
        relative,
    )
    try:
        metadata, raw_path = entry.removesuffix(b"\0").split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split(" ")
        observed_path = raw_path.decode("utf-8", "strict")
    except (ValueError, UnicodeError) as exc:
        raise StagingBrowserAuditError("staging browser driver differs") from exc
    if (
        mode != "100644"
        or object_type != "blob"
        or observed_path != relative
        or object_id != _git_blob_oid(content)
    ):
        raise StagingBrowserAuditError("staging browser driver differs")


def _attest_tracked_worktree(repo_root: Path, *, commit: str) -> None:
    raw_tree = _git_stdout(
        repo_root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        commit,
    )
    raw_index = _git_stdout(repo_root, "ls-files", "--stage", "-v", "-z")
    tree: dict[str, tuple[str, str]] = {}
    for raw in raw_tree.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise StagingBrowserAuditError(
                "staging browser source tree is malformed"
            ) from exc
        if object_type != "blob" or mode not in {"100644", "100755", "120000"}:
            raise StagingBrowserAuditError("staging browser source tree differs")
        tree[relative] = (mode, object_id)

    index: dict[str, tuple[str, str]] = {}
    for raw in raw_index.split(b"\0"):
        if not raw:
            continue
        try:
            tagged_metadata, raw_path = raw.split(b"\t", 1)
            tag, metadata = tagged_metadata[:1], tagged_metadata[2:]
            mode, object_id, stage = metadata.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise StagingBrowserAuditError(
                "staging browser source index is malformed"
            ) from exc
        if tag != b"H" or stage != "0":
            raise StagingBrowserAuditError(
                "staging browser source index contains non-normal flags"
            )
        index[relative] = (mode, object_id)
    if index != tree:
        raise StagingBrowserAuditError("staging browser source index differs")

    for relative, (mode, object_id) in tree.items():
        candidate = repo_root / relative
        if not candidate.is_relative_to(repo_root):
            raise StagingBrowserAuditError("staging browser source path differs")
        try:
            info = candidate.stat(follow_symlinks=False)
            if mode == "120000":
                if not stat.S_ISLNK(info.st_mode):
                    raise StagingBrowserAuditError(
                        "staging browser source path differs"
                    )
                content = os.fsencode(os.readlink(candidate))
            else:
                if candidate.is_symlink() or not stat.S_ISREG(info.st_mode):
                    raise StagingBrowserAuditError(
                        "staging browser source path differs"
                    )
                if bool(stat.S_IMODE(info.st_mode) & 0o111) != (mode == "100755"):
                    raise StagingBrowserAuditError(
                        "staging browser source mode differs"
                    )
                content = candidate.read_bytes()
        except OSError as exc:
            raise StagingBrowserAuditError(
                "staging browser source path is unavailable"
            ) from exc
        if _git_blob_oid(content) != object_id:
            raise StagingBrowserAuditError("staging browser source bytes differ")


def _source_identity(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    try:
        root = repo_root.resolve(strict=True)
        git_root = Path(
            _git_stdout(root, "rev-parse", "--show-toplevel")
            .decode("utf-8", "strict")
            .strip()
        ).resolve(strict=True)
        untracked = _git_stdout(
            root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        ignored_executables = _git_stdout(
            root,
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
            "--",
            "procurement/src",
            "procurement/tools",
        )
        commit = _git_stdout(
            root, "rev-parse", "--verify", "HEAD^{commit}"
        ).decode("ascii").strip()
        tree = _git_stdout(root, "rev-parse", "--verify", "HEAD^{tree}").decode(
            "ascii"
        ).strip()
        commit_type = _git_stdout(root, "cat-file", "-t", commit).decode(
            "ascii"
        ).strip()
        tree_type = _git_stdout(root, "cat-file", "-t", tree).decode(
            "ascii"
        ).strip()
        resolved_tree = _git_stdout(
            root, "rev-parse", "--verify", f"{commit}^{{tree}}"
        ).decode("ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise StagingBrowserAuditError(
            "staging browser source identity is unavailable"
        ) from exc
    if (
        git_root != root
        or untracked
        or any(
            "__pycache__" in Path(item).parts
            or Path(item).suffix in {".pyc", ".pyo"}
            for item in ignored_executables.decode("utf-8", "strict").split("\0")
            if item
        )
        or _GIT_ID.fullmatch(commit) is None
        or _GIT_ID.fullmatch(tree) is None
        or commit_type != "commit"
        or tree_type != "tree"
        or resolved_tree != tree
    ):
        raise StagingBrowserAuditError("staging browser source identity differs")
    _attest_tracked_worktree(root, commit=commit)
    if (
        _git_stdout(
            root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        or any(
            "__pycache__" in Path(item).parts
            or Path(item).suffix in {".pyc", ".pyo"}
            for item in _git_stdout(
                root,
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "-z",
                "--",
                "procurement/src",
                "procurement/tools",
            )
            .decode("utf-8", "strict")
            .split("\0")
            if item
        )
        or _git_stdout(root, "rev-parse", "--verify", "HEAD^{commit}")
        .decode("ascii")
        .strip()
        != commit
        or _git_stdout(root, "rev-parse", "--verify", "HEAD^{tree}")
        .decode("ascii")
        .strip()
        != tree
    ):
        raise StagingBrowserAuditError("staging browser source changed")
    return {"commit": commit, "tree": tree}


def _validate_local_tls_resolution() -> None:
    try:
        addresses = {
            ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(
                LOCAL_TLS_HOST,
                443,
                type=socket.SOCK_STREAM,
            )
        }
    except (OSError, ValueError) as exc:
        raise StagingBrowserAuditError(
            "staging browser TLS endpoint is unavailable"
        ) from exc
    if not addresses or any(not address.is_loopback for address in addresses):
        raise StagingBrowserAuditError("staging browser TLS endpoint differs")


def _validate_trusted_node(node: Path) -> _TrustedNode:
    descriptor = -1
    try:
        resolved = node.resolve(strict=True)
        descriptor = os.open(
            resolved,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        info = os.fstat(descriptor)
    except OSError as exc:
        raise StagingBrowserAuditError(
            "staging browser Node identity is unavailable"
        ) from exc
    if (
        node != resolved
        or node.is_symlink()
        or resolved != _EXPECTED_NODE_EXECUTABLE
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o222
        or not os.access(resolved, os.X_OK)
    ):
        os.close(descriptor)
        raise StagingBrowserAuditError("staging browser Node identity differs")
    try:
        _, digest, identity = _sha256_descriptor(
            descriptor,
            maximum_bytes=128 * 1024 * 1024,
            difference_message="staging browser Node identity differs",
        )
        if digest != _EXPECTED_NODE_SHA256:
            raise StagingBrowserAuditError("staging browser Node identity differs")
        completed = subprocess.run(
            (f"/proc/self/fd/{descriptor}", "--version"),
            env={"LANG": "C", "LC_ALL": "C", "PATH": str(resolved.parent)},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            pass_fds=(descriptor,),
            timeout=10,
        )
        version = completed.stdout.decode("ascii", "strict").strip()
        if completed.returncode != 0 or version != _EXPECTED_NODE_VERSION:
            raise StagingBrowserAuditError("staging browser Node identity differs")
        return _TrustedNode(resolved, version, digest, descriptor, identity)
    except StagingBrowserAuditError:
        os.close(descriptor)
        raise
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        os.close(descriptor)
        raise StagingBrowserAuditError(
            "staging browser Node identity is unavailable"
        ) from exc


def _validate_node_process(process_id: int, trusted: _TrustedNode) -> None:
    executable = Path("/proc") / str(process_id) / "exe"
    descriptor = -1
    try:
        resolved = executable.resolve(strict=True)
        descriptor = os.open(executable, os.O_RDONLY | os.O_CLOEXEC)
        status_lines = (
            (Path("/proc") / str(process_id) / "status")
            .read_text(encoding="ascii")
            .splitlines()
        )
        uid_line = next(line for line in status_lines if line.startswith("Uid:"))
        uids = tuple(int(value) for value in uid_line.split()[1:])
        _, digest, identity = _sha256_descriptor(
            descriptor,
            maximum_bytes=128 * 1024 * 1024,
            difference_message="staging browser Node process identity differs",
        )
    except (OSError, StopIteration, ValueError) as exc:
        raise StagingBrowserAuditError(
            "staging browser Node process identity is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        resolved != trusted.path
        or uids != (os.getuid(), os.geteuid(), os.geteuid(), os.geteuid())
        or digest != trusted.sha256
        or identity != trusted.identity
    ):
        raise StagingBrowserAuditError(
            "staging browser Node process identity differs"
        )


def _has_exact_argument(
    arguments: tuple[str, ...],
    *,
    prefix: str,
    expected: str,
) -> bool:
    return tuple(item for item in arguments if item.startswith(prefix)) == (expected,)


def _validate_chromium_environment(raw: bytes, *, profile: Path) -> None:
    entries: dict[str, str] = {}
    try:
        parts = raw.split(b"\0")
        if not parts or parts[-1] != b"":
            raise ValueError
        for item in parts[:-1]:
            name, separator, value = item.partition(b"=")
            decoded_name = name.decode("ascii", "strict")
            decoded_value = value.decode("utf-8", "strict")
            if (
                separator != b"="
                or not decoded_name
                or decoded_name in entries
                or re.fullmatch(r"[A-Z][A-Z0-9_]*", decoded_name) is None
            ):
                raise ValueError
            entries[decoded_name] = decoded_value
        home = Path(entries["HOME"])
        temporary = Path(entries["TMPDIR"])
        working = Path(entries["PWD"])
        home_info = home.stat(follow_symlinks=False)
        resolved_home = home.resolve(strict=True)
        resolved_profile_parent = profile.parent.resolve(strict=True)
        resolved_working = working.resolve(strict=True)
    except (KeyError, OSError, UnicodeError, ValueError) as exc:
        raise StagingBrowserAuditError(
            "staging browser process environment differs"
        ) from exc
    if (
        set(entries) != _EXPECTED_CHROMIUM_ENVIRONMENT_KEYS
        or any(
            entries.get(name) != expected
            for name, expected in _EXPECTED_CHROMIUM_STATIC_ENVIRONMENT.items()
        )
        or not home.is_absolute()
        or home.is_symlink()
        or not stat.S_ISDIR(home_info.st_mode)
        or home_info.st_uid != os.geteuid()
        or stat.S_IMODE(home_info.st_mode) != 0o700
        or temporary != home
        or resolved_profile_parent != resolved_home
        or resolved_working != REPO_ROOT.resolve(strict=True)
    ):
        raise StagingBrowserAuditError(
            "staging browser process environment differs"
        )


def _validate_owned_chromium(*, cdp_port: int, chromium_pid: int) -> str:
    if type(chromium_pid) is not int or chromium_pid <= 1:
        raise StagingBrowserAuditError("staging browser process identity differs")
    process_root = Path("/proc") / str(chromium_pid)
    try:
        status_lines = (process_root / "status").read_text(encoding="ascii").splitlines()
        uid_line = next(line for line in status_lines if line.startswith("Uid:"))
        uids = tuple(int(value) for value in uid_line.split()[1:])
        command = (process_root / "cmdline").read_bytes().split(b"\0")
        environment = (process_root / "environ").read_bytes()
        executable = (process_root / "exe").resolve(strict=True)
        executable_info = executable.stat(follow_symlinks=False)
        stat_tail = (process_root / "stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
        start_time = stat_tail[19]
        socket_inodes: set[str] = set()
        for entry in (process_root / "fd").iterdir():
            try:
                target = os.readlink(entry)
            except FileNotFoundError:
                continue
            match = re.fullmatch(r"socket:\[([0-9]+)\]", target)
            if match is not None:
                socket_inodes.add(match.group(1))
        listener_inodes: set[str] = set()
        for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
            if not table.exists():
                continue
            for line in table.read_text(encoding="ascii").splitlines()[1:]:
                fields = line.split()
                if len(fields) >= 10 and fields[3] == "0A":
                    host_hex, port_hex = fields[1].split(":", 1)
                    if int(port_hex, 16) == cdp_port and host_hex in {
                        "0100007F",
                        "00000000000000000000000001000000",
                    }:
                        listener_inodes.add(fields[9])
    except (OSError, StopIteration, ValueError, IndexError) as exc:
        raise StagingBrowserAuditError(
            "staging browser process identity is unavailable"
        ) from exc
    try:
        decoded_command = tuple(
            value.decode("utf-8", "strict") for value in command if value
        )
    except UnicodeError as exc:
        raise StagingBrowserAuditError(
            "staging browser process identity differs"
        ) from exc
    try:
        profile_arguments = tuple(
            value for value in decoded_command if value.startswith("--user-data-dir=")
        )
        if len(profile_arguments) != 1:
            raise ValueError
        profile = Path(profile_arguments[0].split("=", 1)[1])
        profile_info = profile.stat(follow_symlinks=False)
    except (OSError, ValueError) as exc:
        raise StagingBrowserAuditError(
            "staging browser process identity differs"
        ) from exc
    _validate_chromium_environment(environment, profile=profile)
    expected_arguments = {
        *_REQUIRED_CHROMIUM_ARGUMENTS,
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={cdp_port}",
        profile_arguments[0],
        "about:blank",
    }
    if (
        uids != (os.getuid(), os.geteuid(), os.geteuid(), os.geteuid())
        or executable != _EXPECTED_CHROMIUM_EXECUTABLE
        or not stat.S_ISREG(executable_info.st_mode)
        or executable_info.st_nlink != 1
        or stat.S_IMODE(executable_info.st_mode) & 0o222
        or not _REQUIRED_CHROMIUM_ARGUMENTS.issubset(decoded_command)
        or not _has_exact_argument(
            decoded_command,
            prefix="--remote-debugging-port=",
            expected=f"--remote-debugging-port={cdp_port}",
        )
        or not _has_exact_argument(
            decoded_command,
            prefix="--remote-debugging-address=",
            expected="--remote-debugging-address=127.0.0.1",
        )
        or not _has_exact_argument(
            decoded_command,
            prefix="--host-resolver-rules=",
            expected=_HOST_RESOLVER_RULES,
        )
        or not _has_exact_argument(
            decoded_command,
            prefix="--proxy-server=",
            expected="--proxy-server=direct://",
        )
        or not _has_exact_argument(
            decoded_command,
            prefix="--proxy-bypass-list=",
            expected="--proxy-bypass-list=*",
        )
        or any(
            value == "--proxy-auto-detect"
            or value == "--no-proxy-server"
            or value.startswith("--proxy-pac-url=")
            for value in decoded_command
        )
        or not profile.is_absolute()
        or profile.is_symlink()
        or not stat.S_ISDIR(profile_info.st_mode)
        or profile_info.st_uid != os.geteuid()
        or stat.S_IMODE(profile_info.st_mode) & 0o077
        or decoded_command.count("about:blank") != 1
        or len(decoded_command[1:]) != len(expected_arguments)
        or set(decoded_command[1:]) != expected_arguments
        or not socket_inodes.intersection(listener_inodes)
        or not start_time.isdigit()
    ):
        raise StagingBrowserAuditError("staging browser process identity differs")
    return start_time


def _validate_inputs(
    *,
    node: Path,
    base_url: str,
    cdp_endpoint: str,
    evidence_root: Path,
    proof_path: Path,
    batch_id: str,
    expected_source_commit: str,
    passphrase: str,
) -> str:
    try:
        canonical_batch_id = str(UUID(batch_id))
        base = urlsplit(base_url)
        cdp = urlsplit(cdp_endpoint)
        base_port = base.port
        cdp_port = cdp.port
        node_info = node.stat(follow_symlinks=False)
        root_info = evidence_root.stat(follow_symlinks=False)
        driver_info = _DRIVER.stat(follow_symlinks=False)
        root_members = tuple(evidence_root.iterdir())
    except (OSError, ValueError) as exc:
        raise StagingBrowserAuditError("staging browser input differs") from exc
    if (
        batch_id != canonical_batch_id
        or _GIT_ID.fullmatch(expected_source_commit) is None
        or _PASSPHRASE.fullmatch(passphrase) is None
        or base_url != LOCAL_TLS_ORIGIN
        or base.scheme != "https"
        or base.hostname != LOCAL_TLS_HOST
        or base_port is not None
        or base.username is not None
        or base.password is not None
        or base.path
        or base.query
        or base.fragment
        or base_url != f"https://{base.hostname}"
        or cdp.scheme != "http"
        or cdp.hostname != "127.0.0.1"
        or cdp_port is None
        or cdp.username is not None
        or cdp.password is not None
        or cdp.path
        or cdp.query
        or cdp.fragment
        or cdp_endpoint != f"http://127.0.0.1:{cdp_port}"
        or node.is_symlink()
        or not stat.S_ISREG(node_info.st_mode)
        or node_info.st_mode & 0o022
        or _DRIVER.is_symlink()
        or not stat.S_ISREG(driver_info.st_mode)
        or driver_info.st_mode & 0o022
        or evidence_root.is_symlink()
        or not stat.S_ISDIR(root_info.st_mode)
        or stat.S_IMODE(root_info.st_mode) != 0o700
        or root_info.st_uid != os.geteuid()
        or proof_path.parent != evidence_root
        or proof_path.name != "staging-price-confirmation-proof.json"
        or root_members
    ):
        raise StagingBrowserAuditError("staging browser input differs")
    return canonical_batch_id


def _validate_phase_proof(
    value: object,
    *,
    batch_id: str,
    expected_source_commit: str,
    expected_source_tree: str,
    driver_sha256: str,
    node_sha256: str,
    node_version: str,
    operator_proof_sha256: str,
    browser_pid: int,
    browser_start_time: str,
    tls_certificate_sha256: str,
    screenshot_bytes: int,
    screenshot_sha256: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _PROOF_KEYS:
        raise StagingBrowserAuditError("staging browser proof differs")
    target_summary = value.get("target_summary")
    if (
        value.get("contract") != BROWSER_PHASE_CONTRACT
        or value.get("phase") != PRICE_CONFIRM_PHASE
        or value.get("batch_id") != batch_id
        or value.get("source_commit") != expected_source_commit
        or value.get("source_tree") != expected_source_tree
        or value.get("driver_sha256") != driver_sha256
        or value.get("node_sha256") != node_sha256
        or value.get("node_version") != node_version
        or value.get("operator_proof_sha256") != operator_proof_sha256
        or value.get("browser_pid") != browser_pid
        or value.get("browser_start_time") != browser_start_time
        or value.get("tls_certificate_sha256") != tls_certificate_sha256
        or value.get("screenshot_bytes") != screenshot_bytes
        or value.get("screenshot_sha256") != screenshot_sha256
        or value.get("status_before") != "VALIDATED"
        or value.get("operational_status_before") != "VALIDATED"
        or value.get("status_after") != "VERIFIED_FUTURE"
        or value.get("operational_status_after") != "VERIFIED_FUTURE"
        or value.get("temporal_basis") != "REGISTERED_OBSERVATION"
        or value.get("raw_bytes") != REGISTERED_OPERATOR_BOOK_BYTES
        or value.get("raw_sha256") != REGISTERED_OPERATOR_BOOK_SHA256
        or value.get("assertion_count") != _EXPECTED_ASSERTION_COUNT
        or value.get("assertion_manifest_sha256")
        != _EXPECTED_ASSERTION_MANIFEST_SHA256
        or not isinstance(value.get("browser_product"), str)
        or _BROWSER_PRODUCT.fullmatch(value["browser_product"]) is None
        or not isinstance(value.get("browser_protocol_version"), str)
        or re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", value["browser_protocol_version"])
        is None
        or not isinstance(value.get("browser_js_version"), str)
        or re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", value["browser_js_version"])
        is None
        or not isinstance(value.get("confirmation_preview_sha256"), str)
        or _SHA256.fullmatch(value["confirmation_preview_sha256"]) is None
        or not isinstance(target_summary, dict)
        or set(target_summary) != _TARGET_SUMMARY_KEYS
        or any(type(item) is not int or item < 0 for item in target_summary.values())
        or target_summary["tracked"] != 5
        or target_summary["guarded"] != 5
        or target_summary["active_guarded"] != 5
        or target_summary["inert"] != 0
        or target_summary["tracked"]
        != target_summary["guarded"] + target_summary["inert"]
        or any(
            target_summary[key] != 0
            for key in (
                "live_detached",
                "unsupported",
                "unattached",
                "unguarded",
                "unresumed",
            )
        )
    ):
        raise StagingBrowserAuditError("staging browser proof differs")
    return dict(value)


def _read_evidence_file(
    path: Path,
    *,
    maximum_bytes: int,
    allow_empty: bool = False,
) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or info.st_size > maximum_bytes
                or (not allow_empty and info.st_size < 1)
            ):
                raise StagingBrowserAuditError("staging browser evidence differs")
            value = bytearray()
            while len(value) <= maximum_bytes:
                block = os.read(
                    descriptor,
                    min(64 * 1024, maximum_bytes + 1 - len(value)),
                )
                if not block:
                    break
                value.extend(block)
            if len(value) != info.st_size or len(value) > maximum_bytes:
                raise StagingBrowserAuditError("staging browser evidence differs")
            return bytes(value)
        finally:
            os.close(descriptor)
    except StagingBrowserAuditError:
        raise
    except OSError as exc:
        raise StagingBrowserAuditError("staging browser evidence differs") from exc


def _purge_if_credential_present(
    root: Path,
    *,
    root_identity: tuple[int, int],
    credential: bytes,
) -> bool:
    if not credential:
        return False
    try:
        info = root.stat(follow_symlinks=False)
        if root.is_symlink() or (info.st_dev, info.st_ino) != root_identity:
            raise StagingBrowserAuditError(
                "staging browser evidence cleanup identity differs"
            )
        contaminated = False
        for directory, names, files in os.walk(root, topdown=True, followlinks=False):
            for name in (*names, *files):
                candidate = Path(directory) / name
                relative = str(candidate.relative_to(root)).encode(
                    "utf-8", "surrogateescape"
                )
                if credential in relative:
                    contaminated = True
                candidate_info = candidate.stat(follow_symlinks=False)
                if stat.S_ISLNK(candidate_info.st_mode) and credential in os.fsencode(
                    os.readlink(candidate)
                ):
                    contaminated = True
            for name in files:
                candidate = Path(directory) / name
                candidate_info = candidate.stat(follow_symlinks=False)
                if not stat.S_ISREG(candidate_info.st_mode):
                    continue
                descriptor = os.open(
                    candidate,
                    os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                )
                try:
                    overlap = b""
                    while True:
                        block = os.read(descriptor, 64 * 1024)
                        if not block:
                            break
                        combined = overlap + block
                        if credential in combined:
                            contaminated = True
                            break
                        overlap = combined[-max(0, len(credential) - 1) :]
                finally:
                    os.close(descriptor)
                if contaminated:
                    break
            if contaminated:
                break
        if contaminated:
            shutil.rmtree(root)
            root.mkdir(mode=0o700)
            recreated = root.stat(follow_symlinks=False)
            if (
                root.is_symlink()
                or not stat.S_ISDIR(recreated.st_mode)
                or stat.S_IMODE(recreated.st_mode) != 0o700
                or recreated.st_uid != os.geteuid()
                or tuple(root.iterdir())
            ):
                raise StagingBrowserAuditError(
                    "staging browser evidence cleanup differs"
                )
        return contaminated
    except StagingBrowserAuditError:
        raise
    except OSError as exc:
        raise StagingBrowserAuditError(
            "staging browser evidence cleanup differs"
        ) from exc


def run_price_confirmation_phase(
    *,
    node: str | Path,
    base_url: str,
    cdp_endpoint: str,
    evidence_root: str | Path,
    operator_proof: Mapping[str, object],
    expected_source_commit: str,
    expected_tls_certificate_sha256: str,
    chromium_pid: int,
    passphrase: str,
    timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    """Drive login and exact VALIDATED -> VERIFIED_FUTURE confirmation."""

    node_path = Path(node)
    root = Path(evidence_root)
    proof_path = root / "staging-price-confirmation-proof.json"
    screenshot_path = root / "staging-price-confirmed-test-data.png"
    source_identity = _source_identity()
    if source_identity["commit"] != expected_source_commit:
        raise StagingBrowserAuditError("staging browser source identity differs")
    if _SHA256.fullmatch(expected_tls_certificate_sha256) is None:
        raise StagingBrowserAuditError("staging browser TLS identity differs")
    validated_operator_proof = _validate_bound_operator_proof(operator_proof)
    canonical_operator_proof = json.dumps(
        validated_operator_proof,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    operator_proof_sha256 = hashlib.sha256(canonical_operator_proof).hexdigest()
    batch_id = str(validated_operator_proof["batch_id"])
    driver_source, driver_bytes, driver_sha256 = _read_bound_driver()
    _verify_driver_snapshot(commit=source_identity["commit"], content=driver_source)
    driver_text = driver_source.decode("utf-8", "strict")
    if not driver_text.startswith("#!/usr/bin/env node\n"):
        raise StagingBrowserAuditError("staging browser driver differs")
    driver_evaluation = (
        "globalThis.__BUFFALO_STAGING_BROWSER_DRIVER_SHA256__="
        f"{json.dumps(driver_sha256)};"
        f"\n//{driver_text[2:]}"
    )
    canonical_batch_id = _validate_inputs(
        node=node_path,
        base_url=base_url,
        cdp_endpoint=cdp_endpoint,
        evidence_root=root,
        proof_path=proof_path,
        batch_id=batch_id,
        expected_source_commit=expected_source_commit,
        passphrase=passphrase,
    )
    trusted_node = _validate_trusted_node(node_path)
    node_path = trusted_node.path
    node_version = trusted_node.version
    node_sha256 = trusted_node.sha256
    node_fd = trusted_node.descriptor
    try:
        _validate_local_tls_resolution()
        cdp_port = urlsplit(cdp_endpoint).port
        if cdp_port is None:
            raise StagingBrowserAuditError("staging browser CDP identity differs")
        browser_start_time = _validate_owned_chromium(
            cdp_port=cdp_port,
            chromium_pid=chromium_pid,
        )
        root_info = root.stat(follow_symlinks=False)
        root_identity = (root_info.st_dev, root_info.st_ino)
        log_path = root / "staging-price-confirmation-browser.log"
        if threading.current_thread() is not threading.main_thread():
            raise StagingBrowserAuditError("staging browser runner thread differs")
    except BaseException:
        os.close(node_fd)
        raise
    secret = bytearray(passphrase.encode("ascii"))
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    process: subprocess.Popen[bytes] | None = None
    interrupted_by: int | None = None
    cleanup_in_progress = False
    old_handlers: dict[int, Any] = {}

    def interrupt(signum: int, _frame: object) -> None:
        nonlocal interrupted_by
        if interrupted_by is not None:
            return
        interrupted_by = signum
        if cleanup_in_progress:
            return
        if process is not None:
            raise _StagingBrowserInterrupted

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        old_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupt)
    try:
        descriptor = os.open(
            log_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as log:
            if interrupted_by is not None:
                raise _StagingBrowserInterrupted
            process = subprocess.Popen(
                (
                    f"/proc/self/fd/{node_fd}",
                    "--input-type=module",
                    "--eval",
                    driver_evaluation,
                    "buffalo-staging-browser-driver.mjs",
                    PRICE_CONFIRM_PHASE,
                    base_url,
                    cdp_endpoint,
                    str(root),
                    str(proof_path),
                    canonical_batch_id,
                    expected_source_commit,
                    source_identity["tree"],
                    driver_sha256,
                    node_sha256,
                    node_version,
                    operator_proof_sha256,
                    expected_tls_certificate_sha256,
                    str(chromium_pid),
                    browser_start_time,
                    str(read_fd),
                ),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
                pass_fds=(node_fd, read_fd),
                start_new_session=True,
                env={"LANG": "C.UTF-8", "PATH": str(node_path.parent)},
                umask=0o077,
            )
            if interrupted_by is not None:
                raise _StagingBrowserInterrupted
            _validate_node_process(process.pid, trusted_node)
            os.close(node_fd)
            node_fd = -1
            remaining = memoryview(secret)
            while remaining:
                written = os.write(write_fd, remaining)
                if written < 1:
                    raise StagingBrowserAuditError(
                        "staging browser credential channel is unavailable"
                    )
                remaining = remaining[written:]
            os.close(write_fd)
            write_fd = -1
            os.close(read_fd)
            read_fd = -1
            try:
                return_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
                raise StagingBrowserAuditError("staging browser phase timed out") from None
        if return_code != 0:
            raise StagingBrowserAuditError("staging browser phase failed")
        if {item.name for item in root.iterdir()} != {
            log_path.name,
            proof_path.name,
            screenshot_path.name,
        }:
            raise StagingBrowserAuditError("staging browser evidence differs")
        log_bytes = _read_evidence_file(
            log_path,
            maximum_bytes=_MAX_LOG_BYTES,
            allow_empty=True,
        )
        proof_bytes = _read_evidence_file(
            proof_path,
            maximum_bytes=_MAX_PROOF_BYTES,
        )
        screenshot_bytes_value = _read_evidence_file(
            screenshot_path,
            maximum_bytes=_MAX_SCREENSHOT_BYTES,
        )
        if bytes(secret) in log_bytes or bytes(secret) in proof_bytes:
            raise StagingBrowserAuditError("staging browser evidence contains credential")
        try:
            value = json.loads(proof_bytes)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise StagingBrowserAuditError("staging browser proof differs") from exc
        if not screenshot_bytes_value.startswith(b"\x89PNG\r\n\x1a\n"):
            raise StagingBrowserAuditError("staging browser proof differs")
        screenshot_sha256 = hashlib.sha256(screenshot_bytes_value).hexdigest()
        if (
            _source_identity() != source_identity
            or _validate_owned_chromium(
                cdp_port=cdp_port,
                chromium_pid=chromium_pid,
            )
            != browser_start_time
        ):
            raise StagingBrowserAuditError("staging browser terminal identity differs")
        _validate_local_tls_resolution()
        return _validate_phase_proof(
            value,
            batch_id=canonical_batch_id,
            expected_source_commit=expected_source_commit,
            expected_source_tree=source_identity["tree"],
            driver_sha256=driver_sha256,
            node_sha256=node_sha256,
            node_version=node_version,
            operator_proof_sha256=operator_proof_sha256,
            browser_pid=chromium_pid,
            browser_start_time=browser_start_time,
            tls_certificate_sha256=expected_tls_certificate_sha256,
            screenshot_bytes=len(screenshot_bytes_value),
            screenshot_sha256=screenshot_sha256,
        )
    except _StagingBrowserInterrupted:
        raise StagingBrowserAuditError("staging browser phase was interrupted") from None
    except StagingBrowserAuditError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise StagingBrowserAuditError("staging browser phase failed") from exc
    finally:
        cleanup_in_progress = True
        cleanup_error: StagingBrowserAuditError | None = None
        contaminated = False
        child_is_terminal = process is None
        if process is not None:
            try:
                child_is_terminal = process.poll() is not None
            except OSError:
                child_is_terminal = False
        if process is not None and not child_is_terminal:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                process.wait(timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                child_is_terminal = process.poll() is not None
            except OSError:
                child_is_terminal = False
        if process is not None and not child_is_terminal:
            cleanup_error = StagingBrowserAuditError(
                "staging browser child cleanup differs"
            )
        if child_is_terminal:
            try:
                contaminated = _purge_if_credential_present(
                    root,
                    root_identity=root_identity,
                    credential=bytes(secret),
                )
            except StagingBrowserAuditError as exc:
                cleanup_error = exc
        for descriptor in (node_fd, read_fd, write_fd):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        for index in range(len(secret)):
            secret[index] = 0
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        if cleanup_error is not None:
            raise cleanup_error
        if contaminated:
            raise StagingBrowserAuditError(
                "staging browser evidence contains credential"
            )
        if interrupted_by is not None:
            raise StagingBrowserAuditError(
                "staging browser phase was interrupted"
            )


__all__ = [
    "BROWSER_PHASE_CONTRACT",
    "LOCAL_TLS_HOST",
    "LOCAL_TLS_ORIGIN",
    "PRICE_CONFIRM_PHASE",
    "StagingBrowserAuditError",
    "_validate_phase_proof",
    "run_price_confirmation_phase",
]
