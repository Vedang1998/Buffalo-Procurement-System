#!/usr/bin/env python3
"""Run the bounded LOCAL Railway-staging acceptance composition.

The public orchestration is intentionally assembled from exact, separately
attested operators.  This module imports only the Python standard library at
startup so source, dependency, browser, and private-input trust can be proven
before any repository module or credential is loaded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Mapping
from uuid import UUID


ACCEPTANCE_CONTRACT = "BUFFALO_LOCAL_STAGING_ACCEPTANCE_V1"
BROWSER_WORKER_PROTOCOL = "BUFFALO_LOCAL_STAGING_BROWSER_WORKER_V1"
BROWSER_WORKER_HIDDEN_MODE = "--internal-browser-worker"
MATERIALIZER_ROLE = "research-materializer"
MATERIALIZER_VOLUME_ROLE = "research-volume"
MATERIALIZER_MODULE = "procurement_os.staging_research_materializer"

FROZEN_IMAGE_ID = (
    "sha256:64b2f821aaa12b2c4297f4d28e4f112d98698f204716f36ac81500974ebc0a6f"
)
FROZEN_SOURCE_COMMIT = "f9cd28f801c325cee5dbd22458b777fbc8bead77"
FROZEN_SOURCE_TREE = "2c7efd855001bbbe4a70defe07b6684e8c2d60a2"
FROZEN_IMAGE_ROOTFS_LAYERS = (
    "sha256:e48af84b2108a5d73effd9e16685b42ac33e7994e606398c46690918f5f3604a",
    "sha256:7716c346ed29fe1a805bca1a2ab6174bd3354cffa0e84a5e989a93f8ce5e1e44",
    "sha256:a2ac78ea6115362b78b98fb826f1d2f56a6fb53b14b6fd828b01ebb166e3c3d6",
    "sha256:bccbed27246a15486d39933a2a0ff8530d97da760aa254a82de8c488aac88d2e",
    "sha256:0015e37e84c4241937ad17b83108f28bc939ec5a7ee811b248f43e8fd950840e",
    "sha256:de8348500c5ea4e5c4ad4adfd58a9cd54a66da965560090d774faf890d52f11a",
    "sha256:f32260399152f2410ef15e897f07b7fa518f5580d39d8d95209291bfa7263b7f",
    "sha256:33ee1fde6ed7c7dd9b7b88e91a15bdab749519af8112d91bd28d90c0399c65aa",
    "sha256:7f9d9070986d81c19362946d938b224d1128dd0a6c60abafa76e99f6cec3d511",
    "sha256:f9d123bb5eaf89ad926929aabd29f0de3784b6d28d04d49fc3c47bdef4feeb00",
    "sha256:cbdbe6a410b580e2e8cb76cd9c1bca94323ff5b96f37db0141346edf26041af5",
    "sha256:7fd1b927b698e64c15496d0546924f56b86c149dfc196f753d7120df5d24d7ee",
    "sha256:aec8202091b4d02180bb1f1c663b795f1ccc88b08a4553178b8e77ec97790309",
    "sha256:d03224de5ae1f9f78f5107564c044aab2858aec3587a3d58b1352085fcf68e5f",
    "sha256:bd44ce5a1cc86e352b6483432ed34708645a6047786b8d6be1a0555bfa50838b",
    "sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef",
)

_DOCKER_EXECUTABLE = Path(
    "/nix/store/37rf2zl654djg7989yipq57d5pd195hi-docker-27.5.1/"
    "libexec/docker/docker"
)
_DOCKER_BYTES = 35_648_392
_DOCKER_SHA256 = "03f1d4e930931713fc9ae82302947e87f435bd81714201a225a6c237afd9baee"
_DOCKER_UID = 1000
_DOCKER_GID = 1000
_DOCKER_MODE = 0o555
_DOCKER_CLIENT_VERSION = "27.5.1"
_DOCKER_API_VERSION = "1.47"
_DOCKER_SOCKET = "/var/run/docker.sock"
_DOCKER_ZERO_TIME = "0001-01-01T00:00:00Z"
_DOCKER_METADATA_LIMIT = 256 * 1024
_DOCKER_ATTACH_LIMIT = 16 * 1024
_DOCKER_METADATA_TIMEOUT = 30.0
_BROWSER_REQUEST_LIMIT = 16 * 1024
_BROWSER_READY_LIMIT = 4 * 1024
_BROWSER_RESULT_LIMIT = 16 * 1024
_BROWSER_WORKER_MAX_FD = 1_048_575
_BROWSER_SECRET_BYTES = 43
_BROWSER_PROC_STAT_LIMIT = 16 * 1024
_BROWSER_PROC_VALUE_LIMIT = 64 * 1024
_BROWSER_CGROUP_FILE_LIMIT = 4_096
_BROWSER_CGROUP_PATH_LIMIT = 4_092
_BROWSER_CGROUP_COMPONENT_LIMIT = 255
_BROWSER_CGROUP_EVENTS_LIMIT = 64
_BROWSER_CGROUP_MEMBER_LIMIT = 4_096
_BROWSER_CGROUP_MEMBERS_BYTES_LIMIT = 45_056
_BROWSER_NSPID_LIMIT = 384
_BROWSER_PID_NAMESPACE_DEPTH_LIMIT = 32
_BROWSER_LINUX_PID_MAX = 2_147_483_647
_BROWSER_WORKER_ARGUMENT_LIMIT = 32
_BROWSER_WORKER_DESCRIPTOR_LIMIT = 16
_BROWSER_PYTHON_EXECUTABLE = Path(
    "/nix/store/qzc04a3npl70cyyy6flnnrb2ig3kayxm-python3-3.13.11/"
    "bin/python3.13"
)
_BROWSER_PYTHON_BYTES = 15_776
_BROWSER_PYTHON_SHA256 = (
    "bd5afcc703e9293ebea22ec05ad3a95f5b14ca6b65293a5f2969efe83148f565"
)
_BROWSER_PYTHON_UID = 1000
_BROWSER_PYTHON_GID = 1000
_BROWSER_PYTHON_MODE = 0o555
_BROWSER_WORKER_RUNNER_SOURCE = Path(__file__).resolve(strict=True).with_name(
    "run_local_staging_browser_worker.py"
)
_BROWSER_WORKER_RUNNER_BYTES = 664
_BROWSER_WORKER_RUNNER_SHA256 = (
    "f719af79822a3c2caa6b39bf4fdcda5aa0f39ceeced8f246fbcd154781047ff2"
)
_BROWSER_WORKER_RUNNER_MEMFD_NAME = "buffalo-local-staging-browser-worker"
_BROWSER_WORKER_RUNNER_MEMFD_TARGET = (
    f"/memfd:{_BROWSER_WORKER_RUNNER_MEMFD_NAME} (deleted)"
)
_BROWSER_WORKER_RUNNER_MODE = 0o400
_BROWSER_WORKER_RUNNER_SEALS = (
    fcntl.F_SEAL_SEAL
    | fcntl.F_SEAL_SHRINK
    | fcntl.F_SEAL_GROW
    | fcntl.F_SEAL_WRITE
)
_BROWSER_LIVE_PROCESS_STATES = frozenset({"R", "S"})
_BROWSER_PROCESS_STATES = frozenset(
    {"R", "S", "D", "T", "t", "W", "X", "x", "Z", "P", "I"}
)
_BROWSER_WORKER_ENVIRONMENT = (
    ("LANG", "C.UTF-8"),
    ("LC_ALL", "C.UTF-8"),
    ("TZ", "UTC"),
)
_BROWSER_HANDLE_TOKEN = object()
_SHA256_TEXT = re.compile(r"\A[0-9a-f]{64}\Z")
_GIT_OID_TEXT = re.compile(r"\A[0-9a-f]{40}\Z")
_CANONICAL_FD = re.compile(r"\A(?:[3-9]|[1-9][0-9]+)\Z")
_START_TICKS_TEXT = re.compile(r"\A[1-9][0-9]*\Z")
_BROWSER_PRODUCT_TEXT = re.compile(
    r"\A(?:Chrome|HeadlessChrome)/[0-9]+(?:\.[0-9]+)+\Z"
)
_VERSION_TEXT = re.compile(r"\A[0-9]+(?:\.[0-9]+)+\Z")
_BROWSER_SECRET_TEXT = re.compile(rb"\A[A-Za-z0-9_-]{43}\Z")
_OPERATOR_PROOF_CONTRACT = "BUFFALO_STOPPED_SERVICE_PRICE_STAGE_V1"
_OPERATOR_SOURCE_REF = "procurement/config/synthetic_price_replacement_book.csv"
_OPERATOR_SOURCE_BYTES = 1_590
_OPERATOR_RAW_SHA256 = (
    "00071443ea8c54b57fc6014c3b1daf204081714a2ff09b98bed6c56a0dd3862c"
)
_OPERATOR_TARGET_ATTESTATION_SHA256 = (
    "517843a848fd07e5fc62b9713279a8bcfc52a782890aee68c7d900dd3600d77a"
)
_OPERATOR_PROOF_KEYS = frozenset(
    {
        "contract",
        "source_ref",
        "source_bytes",
        "raw_sha256",
        "target_attestation_sha256",
        "batch_id",
        "status",
        "declaration_sha256",
        "validation_fingerprint",
        "proposed_scope_membership_sha256",
        "staging_rows_sha256",
        "validation_issues_sha256",
        "unchanged_database_sha256",
        "unchanged_storage_sha256",
        "idempotent_replay",
        "ambiguous_commit_recovered",
    }
)
_OPERATOR_PROOF_NON_HASH_KEYS = frozenset(
    {
        "contract",
        "source_ref",
        "source_bytes",
        "batch_id",
        "status",
        "idempotent_replay",
        "ambiguous_commit_recovered",
    }
)
_BROWSER_PHASE_CONTRACT = "BUFFALO_STAGING_PURCHASING_BROWSER_PHASE_V1"
_BROWSER_PHASE = "price-confirm"
_BROWSER_NODE_VERSION = "v24.13.0"
_BROWSER_ASSERTION_COUNT = 62
_BROWSER_ASSERTION_MANIFEST_SHA256 = (
    "64a5063520cbe378502b7930f8b51ba784b05c0e9c2ea986de9adeda991efb50"
)
_BROWSER_TARGET_SUMMARY_KEYS = frozenset(
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
_BROWSER_PROOF_KEYS = frozenset(
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

ACCEPTED_INVENTORY_BYTES = 49_648
ACCEPTED_INVENTORY_SHA256 = (
    "97fd10669302a2158882d08102b8d306b51b6b3c61bae6941ac957ef787238b2"
)
ACCEPTED_BUNDLE_BYTES = 2_863_932
ACCEPTED_BUNDLE_SHA256 = (
    "07009cb0f11c9d3a7425878622fbb8b6d081bb96e08bfe51f616e3608c7f82cc"
)

_IMAGE_ID = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_RUN_ID = re.compile(r"\A[0-9a-f]{32}\Z")
_SAFE_DOCKER_NAME = re.compile(r"\A[a-z0-9][a-z0-9_.-]{0,127}\Z")
_CHUNK_BYTES = 1024 * 1024
_MATERIALIZER_TMPFS = (
    "/run/buffalo-research-materializer:"
    "rw,nosuid,nodev,noexec,mode=0700,uid=0,gid=0,size=67108864"
)
_MATERIALIZER_CAPABILITIES = ("CHOWN", "DAC_READ_SEARCH", "FOWNER")
_MATERIALIZER_COMMAND = (
    "-g",
    "--",
    "/opt/buffalo-venv/bin/python",
    "-I",
    "-B",
    "-m",
    MATERIALIZER_MODULE,
)
_SERVICE_ENTRYPOINT = (
    "/usr/bin/tini",
    "-g",
    "--",
    "/opt/buffalo-venv/bin/python",
    "-I",
    "-B",
    "-m",
    "procurement_os.staging_bootstrap",
)
_IMAGE_ENVIRONMENT = (
    "PATH=/opt/buffalo-venv/bin:/usr/local/bin:/usr/bin:/bin",
    "GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305",
    "PYTHON_VERSION=3.13.11",
    "PYTHON_SHA256=16ede7bb7cdbfa895d11b0642fa0e523f291e6487194d53cf6d3b338c3a17ea2",
    "BUFFALO_STAGING_VOLUME_ROOT=/data",
    "LANG=C.UTF-8",
    "LC_ALL=C.UTF-8",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONUNBUFFERED=1",
    "TZ=UTC",
)


class LocalStagingAcceptanceError(RuntimeError):
    """The LOCAL acceptance composition differs from its frozen contract."""


@dataclass(frozen=True)
class MaterializerIngress:
    research_root: Path
    inventory_path: Path
    bundle_path: Path


@dataclass(frozen=True)
class MaterializerInvocation:
    image_id: str
    run_id: str
    volume_name: str
    container_name: str
    ingress: MaterializerIngress | None


@dataclass(frozen=True)
class MaterializerVolumeFingerprint:
    name: str
    driver: str
    scope: str
    labels: tuple[tuple[str, str], ...]
    options: None
    mountpoint: str
    created_at: str


@dataclass(frozen=True)
class MaterializerPhaseProof:
    contract: str
    role: str
    container_id: str
    immutable_envelope_sha256: str
    started_at: str
    finished_at: str


@dataclass(frozen=True)
class BrowserWorkerArguments:
    request_descriptor: int
    ready_descriptor: int
    secret_descriptor: int
    result_descriptor: int


@dataclass(frozen=True)
class BrowserWorkerRequest:
    protocol: str
    frame: str
    challenge: str
    run_id: str
    source_commit: str
    source_tree: str
    cdp_endpoint: str
    evidence_root: str
    operator_proof_json: bytes = field(repr=False)
    operator_batch_id: str
    operator_proof_sha256: str
    tls_certificate_sha256: str
    chromium_pid: int


@dataclass(frozen=True)
class BrowserWorkerReady:
    protocol: str
    frame: str
    challenge: str
    config_sha256: str
    worker_pid: int
    worker_start_ticks: int
    source_commit: str
    source_tree: str
    chromium_pid: int
    browser_start_time: str
    python_executable_sha256: str
    module_manifest_sha256: str
    driver_sha256: str
    node_sha256: str
    preflight_sha256: str


@dataclass(frozen=True)
class BrowserWorkerExpectedAttestation:
    challenge: str
    run_id: str
    source_commit: str
    source_tree: str
    cdp_endpoint: str
    evidence_root: str
    tls_certificate_sha256: str
    chromium_pid: int
    browser_start_time: str
    worker_pid: int
    worker_start_ticks: int
    python_executable_sha256: str
    module_manifest_sha256: str
    driver_sha256: str
    node_sha256: str
    preflight_sha256: str


@dataclass(frozen=True)
class BrowserWorkerResult:
    protocol: str
    frame: str
    challenge: str
    config_sha256: str
    ready_sha256: str
    worker_pid: int
    worker_start_ticks: int
    proof_json: bytes = field(repr=False)


@dataclass(frozen=True)
class BrowserWorkerProcessStat:
    pid: int
    state: str
    parent_pid: int
    process_group: int
    session_id: int
    start_ticks: int


@dataclass(frozen=True)
class BrowserCgroupEvents:
    populated: bool
    frozen: bool


@dataclass(frozen=True)
class BrowserContainmentTextEvidence:
    """Normalized text only; never launch or credential-release authority."""

    cgroup_path: str
    events: BrowserCgroupEvents
    process_ids: tuple[int, ...]
    thread_ids: tuple[int, ...]
    init_outer_pid: int
    worker_outer_pid: int
    init_namespace_pids: tuple[int, ...]
    worker_namespace_pids: tuple[int, ...]


@dataclass
class PinnedBrowserPythonExecutable:
    """Owned descriptor for the pinned CPython ELF, not its runtime closure."""

    descriptor: int
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int
    _owner_token: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor == -1 and self._owner_token is None:
            return
        if (
            self._owner_token is not _BROWSER_HANDLE_TOKEN
            or type(descriptor) is not int
            or descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python cleanup differs"
            )
        try:
            info = os.fstat(descriptor)
        except (OSError, OverflowError, ValueError, TypeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python cleanup differs"
            ) from None
        if (info.st_dev, info.st_ino) != (self.device, self.inode):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python cleanup differs"
            )
        self.descriptor = -1
        self._owner_token = None
        os.close(descriptor)

    def __enter__(self) -> PinnedBrowserPythonExecutable:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __copy__(self) -> PinnedBrowserPythonExecutable:
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python ownership differs"
        )

    def __deepcopy__(
        self,
        _: dict[int, object],
    ) -> PinnedBrowserPythonExecutable:
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python ownership differs"
        )


@dataclass
class PinnedBrowserWorkerRunner:
    """Owned sealed worker bytes; not a complete executable runtime."""

    descriptor: int
    device: int
    inode: int
    size: int
    sha256: str
    _owner_token: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor == -1 and self._owner_token is None:
            return
        if (
            self._owner_token is not _BROWSER_HANDLE_TOKEN
            or type(descriptor) is not int
            or descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser worker runner cleanup differs"
            )
        try:
            info = os.fstat(descriptor)
            seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
            target = os.readlink(f"/proc/self/fd/{descriptor}")
        except (OSError, OverflowError, ValueError, TypeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser worker runner cleanup differs"
            ) from None
        if (
            (info.st_dev, info.st_ino) != (self.device, self.inode)
            or seals != _BROWSER_WORKER_RUNNER_SEALS
            or target != _BROWSER_WORKER_RUNNER_MEMFD_TARGET
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser worker runner cleanup differs"
            )
        self.descriptor = -1
        self._owner_token = None
        os.close(descriptor)

    def __enter__(self) -> PinnedBrowserWorkerRunner:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __copy__(self) -> PinnedBrowserWorkerRunner:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner ownership differs"
        )

    def __deepcopy__(
        self,
        _: dict[int, object],
    ) -> PinnedBrowserWorkerRunner:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner ownership differs"
        )


@dataclass(frozen=True)
class BrowserWorkerLaunch:
    """Exact inert launch specification; it confers no execution authority."""

    command_line: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    cwd: Path
    pass_fds: tuple[int, ...]
    arguments: BrowserWorkerArguments


@dataclass
class ObservedBrowserWorker:
    """Owned pidfd for one exact preflight snapshot, not execution authority."""

    pidfd: int
    pidfd_device: int
    pidfd_inode: int
    process: BrowserWorkerProcessStat
    _owner_token: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def close(self) -> None:
        descriptor = self.pidfd
        if descriptor == -1 and self._owner_token is None:
            return
        if (
            self._owner_token is not _BROWSER_HANDLE_TOKEN
            or type(descriptor) is not int
            or descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process cleanup differs"
            )
        try:
            info, process_id, _, _ = _read_browser_pidfd_metadata(descriptor)
            if (
                (info.st_dev, info.st_ino)
                != (self.pidfd_device, self.pidfd_inode)
                or process_id not in {None, self.process.pid}
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance browser process differs"
                )
        except (
            LocalStagingAcceptanceError,
            OSError,
            OverflowError,
            ValueError,
            TypeError,
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process cleanup differs"
            ) from None
        self.pidfd = -1
        self._owner_token = None
        os.close(descriptor)

    def __enter__(self) -> ObservedBrowserWorker:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __copy__(self) -> ObservedBrowserWorker:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process ownership differs"
        )

    def __deepcopy__(
        self,
        _: dict[int, object],
    ) -> ObservedBrowserWorker:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process ownership differs"
        )


@dataclass(frozen=True)
class BrowserWorkerDescriptorExpectation:
    number: int
    target: str
    device: int
    inode: int
    mount_id: int
    position: int
    status_flags: int
    close_on_exec: bool


@dataclass(frozen=True)
class BrowserWorkerProcessExpectation:
    command_line: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    cwd: Path
    cwd_device: int
    cwd_inode: int
    descriptors: tuple[BrowserWorkerDescriptorExpectation, ...]
    user_id: int
    group_id: int
    supplementary_groups: tuple[int, ...]
    no_new_privileges: int
    seccomp_mode: int


@dataclass
class TrustedDockerClient:
    descriptor: int
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int

    def close(self) -> None:
        descriptor = self.descriptor
        self.descriptor = -1
        if descriptor >= 0:
            os.close(descriptor)

    def __enter__(self) -> TrustedDockerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def _sha256_descriptor(descriptor: int, *, expected_bytes: int) -> str:
    digest = hashlib.sha256()
    observed = 0
    while observed <= expected_bytes:
        block = os.pread(
            descriptor,
            min(_CHUNK_BYTES, expected_bytes + 1 - observed),
            observed,
        )
        if not block:
            break
        observed += len(block)
        digest.update(block)
    if observed != expected_bytes:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker client differs"
        )
    return digest.hexdigest()


def _validate_trusted_docker(client: TrustedDockerClient) -> None:
    try:
        descriptor_info = os.fstat(client.descriptor)
        named_info = client.path.stat(follow_symlinks=False)
    except (OSError, ValueError) as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker client is unavailable"
        ) from exc
    descriptor_identity = (
        descriptor_info.st_dev,
        descriptor_info.st_ino,
        descriptor_info.st_size,
        descriptor_info.st_mtime_ns,
    )
    if (
        client.path != _DOCKER_EXECUTABLE
        or client.path.is_symlink()
        or not stat.S_ISREG(descriptor_info.st_mode)
        or descriptor_info.st_nlink != 1
        or (descriptor_info.st_uid, descriptor_info.st_gid)
        != (_DOCKER_UID, _DOCKER_GID)
        or stat.S_IMODE(descriptor_info.st_mode) != _DOCKER_MODE
        or descriptor_info.st_size != _DOCKER_BYTES
        or descriptor_identity
        != (client.device, client.inode, client.size, client.mtime_ns)
        or (named_info.st_dev, named_info.st_ino)[:]
        != (client.device, client.inode)
        or _sha256_descriptor(
            client.descriptor,
            expected_bytes=_DOCKER_BYTES,
        )
        != _DOCKER_SHA256
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker client differs"
        )


def _validate_docker_socket() -> None:
    try:
        socket_info = Path(_DOCKER_SOCKET).stat(follow_symlinks=False)
    except OSError as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker daemon is unavailable"
        ) from exc
    if (
        not stat.S_ISSOCK(socket_info.st_mode)
        or (socket_info.st_uid, socket_info.st_gid) != (0, 1000)
        or stat.S_IMODE(socket_info.st_mode) != 0o660
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker daemon differs"
        )


def open_trusted_docker() -> TrustedDockerClient:
    """Open and bind the exact local Docker client before any private path exists."""

    descriptor = -1
    try:
        descriptor = os.open(
            _DOCKER_EXECUTABLE,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        info = os.fstat(descriptor)
        client = TrustedDockerClient(
            descriptor=descriptor,
            path=_DOCKER_EXECUTABLE,
            device=info.st_dev,
            inode=info.st_ino,
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
        )
        _validate_trusted_docker(client)
        return client
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _browser_python_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    observed = 0
    try:
        while observed <= _BROWSER_PYTHON_BYTES:
            block = os.pread(
                descriptor,
                min(
                    _CHUNK_BYTES,
                    _BROWSER_PYTHON_BYTES + 1 - observed,
                ),
                observed,
            )
            if not block:
                break
            observed += len(block)
            digest.update(block)
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python differs"
        ) from None
    if observed != _BROWSER_PYTHON_BYTES:
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python differs"
        )
    return digest.hexdigest()


def _validate_pinned_browser_python_executable(runtime: PinnedBrowserPythonExecutable) -> None:
    if (
        type(runtime) is not PinnedBrowserPythonExecutable
        or runtime._owner_token is not _BROWSER_HANDLE_TOKEN
        or type(runtime.descriptor) is not int
        or runtime.descriptor <= 2
        or runtime.descriptor > _BROWSER_WORKER_MAX_FD
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python differs"
        )
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        descriptor_info = os.fstat(runtime.descriptor)
        named_info = runtime.path.stat(follow_symlinks=False)
        descriptor_target = os.readlink(
            f"/proc/self/fd/{runtime.descriptor}"
        )
        descriptor_flags = fcntl.fcntl(runtime.descriptor, fcntl.F_GETFD)
        status_flags = fcntl.fcntl(runtime.descriptor, fcntl.F_GETFL)
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python differs"
        ) from None
    descriptor_identity = (
        descriptor_info.st_dev,
        descriptor_info.st_ino,
        descriptor_info.st_size,
        descriptor_info.st_mtime_ns,
    )
    named_identity = (
        named_info.st_dev,
        named_info.st_ino,
        named_info.st_size,
        named_info.st_mtime_ns,
    )
    if (
        runtime.path != _BROWSER_PYTHON_EXECUTABLE
        or (
            soft_limit != resource.RLIM_INFINITY
            and runtime.descriptor >= soft_limit
        )
        or runtime.path.is_symlink()
        or descriptor_target != str(_BROWSER_PYTHON_EXECUTABLE)
        or descriptor_flags & fcntl.FD_CLOEXEC == 0
        or os.get_inheritable(runtime.descriptor)
        or status_flags & os.O_ACCMODE != os.O_RDONLY
        or status_flags & os.O_NONBLOCK == 0
        or not stat.S_ISREG(descriptor_info.st_mode)
        or descriptor_info.st_nlink != 1
        or (descriptor_info.st_uid, descriptor_info.st_gid)
        != (_BROWSER_PYTHON_UID, _BROWSER_PYTHON_GID)
        or stat.S_IMODE(descriptor_info.st_mode) != _BROWSER_PYTHON_MODE
        or descriptor_info.st_size != _BROWSER_PYTHON_BYTES
        or descriptor_identity
        != (
            runtime.device,
            runtime.inode,
            runtime.size,
            runtime.mtime_ns,
        )
        or named_identity != descriptor_identity
        or _browser_python_sha256(runtime.descriptor)
        != _BROWSER_PYTHON_SHA256
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python differs"
        )


def open_pinned_browser_python_executable() -> PinnedBrowserPythonExecutable:
    """Pin the direct CPython ELF; runtime-closure attestation remains later."""

    descriptor = -1
    try:
        descriptor = os.open(
            _BROWSER_PYTHON_EXECUTABLE,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if (
            descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
            or (
                soft_limit != resource.RLIM_INFINITY
                and descriptor >= soft_limit
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python differs"
            )
        info = os.fstat(descriptor)
        runtime = PinnedBrowserPythonExecutable(
            descriptor=descriptor,
            path=_BROWSER_PYTHON_EXECUTABLE,
            device=info.st_dev,
            inode=info.st_ino,
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
        )
        runtime._owner_token = _BROWSER_HANDLE_TOKEN
        _validate_pinned_browser_python_executable(runtime)
        return runtime
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _read_browser_worker_runner_source() -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            _BROWSER_WORKER_RUNNER_SOURCE,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if (
            descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
            or (
                soft_limit != resource.RLIM_INFINITY
                and descriptor >= soft_limit
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser worker runner differs"
            )
        before = os.fstat(descriptor)
        named_before = _BROWSER_WORKER_RUNNER_SOURCE.stat(
            follow_symlinks=False
        )
        descriptor_target = os.readlink(f"/proc/self/fd/{descriptor}")
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        status_flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        selected = bytearray()
        while len(selected) <= _BROWSER_WORKER_RUNNER_BYTES:
            block = os.read(
                descriptor,
                _BROWSER_WORKER_RUNNER_BYTES + 1 - len(selected),
            )
            if not block:
                break
            selected.extend(block)
        after = os.fstat(descriptor)
        named_after = _BROWSER_WORKER_RUNNER_SOURCE.stat(
            follow_symlinks=False
        )
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    if (
        _BROWSER_WORKER_RUNNER_SOURCE.is_symlink()
        or descriptor_target != str(_BROWSER_WORKER_RUNNER_SOURCE)
        or descriptor_flags & fcntl.FD_CLOEXEC == 0
        or status_flags & os.O_ACCMODE != os.O_RDONLY
        or status_flags & os.O_NONBLOCK == 0
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or (before.st_uid, before.st_gid) != (os.geteuid(), os.getegid())
        or stat.S_IMODE(before.st_mode) != 0o644
        or identity
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        or identity
        != (
            named_before.st_dev,
            named_before.st_ino,
            named_before.st_size,
            named_before.st_mtime_ns,
        )
        or identity
        != (
            named_after.st_dev,
            named_after.st_ino,
            named_after.st_size,
            named_after.st_mtime_ns,
        )
        or len(selected) != _BROWSER_WORKER_RUNNER_BYTES
        or hashlib.sha256(selected).hexdigest()
        != _BROWSER_WORKER_RUNNER_SHA256
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        )
    return bytes(selected)


def _browser_worker_runner_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    selected = 0
    try:
        if os.lseek(descriptor, 0, os.SEEK_CUR) != 0:
            raise OSError
        while selected <= _BROWSER_WORKER_RUNNER_BYTES:
            block = os.read(
                descriptor,
                _BROWSER_WORKER_RUNNER_BYTES + 1 - selected,
            )
            if not block:
                break
            selected += len(block)
            digest.update(block)
        if os.lseek(descriptor, 0, os.SEEK_SET) != 0:
            raise OSError
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        ) from None
    if selected != _BROWSER_WORKER_RUNNER_BYTES:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        )
    return digest.hexdigest()


def _validate_pinned_browser_worker_runner(
    runner: PinnedBrowserWorkerRunner,
) -> None:
    if (
        type(runner) is not PinnedBrowserWorkerRunner
        or runner._owner_token is not _BROWSER_HANDLE_TOKEN
        or type(runner.descriptor) is not int
        or runner.descriptor <= 2
        or runner.descriptor > _BROWSER_WORKER_MAX_FD
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        )
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        info = os.fstat(runner.descriptor)
        target = os.readlink(f"/proc/self/fd/{runner.descriptor}")
        descriptor_flags = fcntl.fcntl(runner.descriptor, fcntl.F_GETFD)
        status_flags = fcntl.fcntl(runner.descriptor, fcntl.F_GETFL)
        seals = fcntl.fcntl(runner.descriptor, fcntl.F_GET_SEALS)
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        ) from None
    if (
        (
            soft_limit != resource.RLIM_INFINITY
            and runner.descriptor >= soft_limit
        )
        or target != _BROWSER_WORKER_RUNNER_MEMFD_TARGET
        or descriptor_flags & fcntl.FD_CLOEXEC == 0
        or os.get_inheritable(runner.descriptor)
        or status_flags & os.O_ACCMODE != os.O_RDONLY
        or status_flags & os.O_NONBLOCK != 0
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 0
        or (info.st_uid, info.st_gid) != (os.geteuid(), os.getegid())
        or stat.S_IMODE(info.st_mode) != _BROWSER_WORKER_RUNNER_MODE
        or seals != _BROWSER_WORKER_RUNNER_SEALS
        or info.st_size != _BROWSER_WORKER_RUNNER_BYTES
        or (info.st_dev, info.st_ino, info.st_size)
        != (runner.device, runner.inode, runner.size)
        or runner.sha256 != _BROWSER_WORKER_RUNNER_SHA256
        or _browser_worker_runner_sha256(runner.descriptor)
        != _BROWSER_WORKER_RUNNER_SHA256
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        )


def open_pinned_browser_worker_runner() -> PinnedBrowserWorkerRunner:
    """Copy exact public worker bytes into a sealed read-only descriptor."""

    content = _read_browser_worker_runner_source()
    staging_descriptor = -1
    sealed_descriptor = -1
    try:
        staging_descriptor = os.memfd_create(
            _BROWSER_WORKER_RUNNER_MEMFD_NAME,
            os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if (
            staging_descriptor <= 2
            or staging_descriptor > _BROWSER_WORKER_MAX_FD
            or (
                soft_limit != resource.RLIM_INFINITY
                and staging_descriptor >= soft_limit
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser worker runner differs"
            )
        offset = 0
        while offset < len(content):
            written = os.write(staging_descriptor, content[offset:])
            if written <= 0:
                raise OSError
            offset += written
        os.fchmod(staging_descriptor, _BROWSER_WORKER_RUNNER_MODE)
        if os.lseek(staging_descriptor, 0, os.SEEK_SET) != 0:
            raise OSError
        fcntl.fcntl(
            staging_descriptor,
            fcntl.F_ADD_SEALS,
            _BROWSER_WORKER_RUNNER_SEALS,
        )
        sealed_descriptor = os.open(
            f"/proc/self/fd/{staging_descriptor}",
            os.O_RDONLY | os.O_CLOEXEC,
        )
        if (
            sealed_descriptor <= 2
            or sealed_descriptor > _BROWSER_WORKER_MAX_FD
            or (
                soft_limit != resource.RLIM_INFINITY
                and sealed_descriptor >= soft_limit
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser worker runner differs"
            )
        os.close(staging_descriptor)
        staging_descriptor = -1
        info = os.fstat(sealed_descriptor)
        runner = PinnedBrowserWorkerRunner(
            descriptor=sealed_descriptor,
            device=info.st_dev,
            inode=info.st_ino,
            size=info.st_size,
            sha256=_BROWSER_WORKER_RUNNER_SHA256,
        )
        runner._owner_token = _BROWSER_HANDLE_TOKEN
        _validate_pinned_browser_worker_runner(runner)
        return runner
    except BaseException:
        if sealed_descriptor >= 0:
            os.close(sealed_descriptor)
        if staging_descriptor >= 0:
            os.close(staging_descriptor)
        raise


def build_browser_worker_launch(
    runtime: PinnedBrowserPythonExecutable,
    runner: PinnedBrowserWorkerRunner,
    arguments: BrowserWorkerArguments,
) -> BrowserWorkerLaunch:
    """Build an exact inert spec; spawning remains intentionally unavailable."""

    _validate_pinned_browser_python_executable(runtime)
    _validate_pinned_browser_worker_runner(runner)
    validated_arguments = validate_browser_worker_descriptors(arguments)
    channel_descriptors = (
        validated_arguments.request_descriptor,
        validated_arguments.ready_descriptor,
        validated_arguments.secret_descriptor,
        validated_arguments.result_descriptor,
    )
    inherited = (runtime.descriptor, runner.descriptor, *channel_descriptors)
    if len(set(inherited)) != 6:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker launch differs"
        )
    cwd = _BROWSER_WORKER_RUNNER_SOURCE.parents[2]
    try:
        cwd_info = cwd.stat(follow_symlinks=False)
    except OSError:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker launch differs"
        ) from None
    if cwd.is_symlink() or not stat.S_ISDIR(cwd_info.st_mode):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker launch differs"
        )
    command_line = (
        f"/proc/self/fd/{runtime.descriptor}",
        "-I",
        "-S",
        "-B",
        "-P",
        f"/proc/self/fd/{runner.descriptor}",
        BROWSER_WORKER_HIDDEN_MODE,
        *(str(value) for value in channel_descriptors),
    )
    return BrowserWorkerLaunch(
        command_line=command_line,
        environment=_BROWSER_WORKER_ENVIRONMENT,
        cwd=cwd,
        pass_fds=inherited,
        arguments=validated_arguments,
    )


def _parse_browser_containment_pid(value: bytes) -> int:
    if (
        type(value) is not bytes
        or not value
        or len(value) > 10
        or not value.isascii()
        or not value.isdigit()
        or value.startswith(b"0")
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    selected = int(value)
    if selected <= 0 or selected > _BROWSER_LINUX_PID_MAX:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    return selected


def parse_browser_cgroup_path(raw: bytes) -> str:
    """Parse one exact unified-cgroup membership record without filesystem I/O."""

    if (
        type(raw) is not bytes
        or len(raw) < 5
        or len(raw) > _BROWSER_CGROUP_FILE_LIMIT
        or not raw.endswith(b"\n")
        or raw.count(b"\n") != 1
        or b"\r" in raw
        or b"\0" in raw
        or not raw.startswith(b"0::")
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    path = raw[3:-1]
    if not path or len(path) > _BROWSER_CGROUP_PATH_LIMIT:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    if path == b"/":
        return "/"
    if not path.startswith(b"/") or path.endswith(b"/") or b"//" in path:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    components = path[1:].split(b"/")
    if any(
        not component
        or len(component) > _BROWSER_CGROUP_COMPONENT_LIMIT
        or component in {b".", b".."}
        or any(value < 0x21 or value > 0x7E for value in component)
        for component in components
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    try:
        return path.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        ) from None


def parse_browser_namespace_pids(raw: bytes) -> tuple[int, ...]:
    """Parse exactly one selected NSpid status record in kernel order."""

    prefix = b"NSpid:\t"
    if (
        type(raw) is not bytes
        or len(raw) <= len(prefix) + 1
        or len(raw) > _BROWSER_NSPID_LIMIT
        or not raw.startswith(prefix)
        or not raw.endswith(b"\n")
        or raw.count(b"\n") != 1
        or b"\r" in raw
        or b"\0" in raw
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    parts = raw[len(prefix) : -1].split(b"\t")
    if (
        not parts
        or len(parts) > _BROWSER_PID_NAMESPACE_DEPTH_LIMIT
        or any(not part for part in parts)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    return tuple(_parse_browser_containment_pid(part) for part in parts)


def parse_browser_cgroup_events(raw: bytes) -> BrowserCgroupEvents:
    """Parse the complete bounded cgroup.events projection."""

    if (
        type(raw) is not bytes
        or not raw
        or len(raw) > _BROWSER_CGROUP_EVENTS_LIMIT
        or not raw.endswith(b"\n")
        or b"\r" in raw
        or b"\0" in raw
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    selected: dict[bytes, bool] = {}
    for line in raw[:-1].split(b"\n"):
        if line.count(b" ") != 1:
            raise LocalStagingAcceptanceError(
                "local acceptance browser containment evidence differs"
            )
        key, value = line.split(b" ", 1)
        if key in selected or value not in {b"0", b"1"}:
            raise LocalStagingAcceptanceError(
                "local acceptance browser containment evidence differs"
            )
        selected[key] = value == b"1"
    if set(selected) != {b"populated", b"frozen"}:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    return BrowserCgroupEvents(
        populated=selected[b"populated"],
        frozen=selected[b"frozen"],
    )


def _parse_browser_cgroup_members(raw: bytes) -> tuple[int, ...]:
    if type(raw) is not bytes or len(raw) > _BROWSER_CGROUP_MEMBERS_BYTES_LIMIT:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    if not raw:
        return ()
    if not raw.endswith(b"\n") or b"\r" in raw or b"\0" in raw:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    parts = raw[:-1].split(b"\n")
    if (
        not parts
        or len(parts) > _BROWSER_CGROUP_MEMBER_LIMIT
        or any(not part for part in parts)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    members = tuple(_parse_browser_containment_pid(part) for part in parts)
    if len(set(members)) != len(members):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    return tuple(sorted(members))


def parse_browser_cgroup_processes(raw: bytes) -> tuple[int, ...]:
    """Normalize one bounded cgroup.procs snapshot."""

    return _parse_browser_cgroup_members(raw)


def parse_browser_cgroup_threads(raw: bytes) -> tuple[int, ...]:
    """Normalize one bounded cgroup.threads snapshot."""

    return _parse_browser_cgroup_members(raw)


def parse_browser_containment_text_evidence(
    *,
    expected_cgroup_path: str,
    expected_init_outer_pid: int,
    expected_worker_outer_pid: int,
    expected_frozen: bool,
    init_cgroup: bytes,
    worker_cgroup: bytes,
    init_nspid: bytes,
    worker_nspid: bytes,
    events: bytes,
    processes: bytes,
    threads: bytes,
) -> BrowserContainmentTextEvidence:
    """Normalize a two-process text snapshot without granting authority.

    This does not prove pidfd/start binding, PPID, namespace inodes, historical
    childlessness, mount isolation, launch ordering, or credential release.
    """

    if (
        type(expected_cgroup_path) is not str
        or not expected_cgroup_path
        or len(expected_cgroup_path) > _BROWSER_CGROUP_PATH_LIMIT
        or type(expected_init_outer_pid) is not int
        or type(expected_worker_outer_pid) is not int
        or type(expected_frozen) is not bool
        or expected_init_outer_pid <= 1
        or expected_worker_outer_pid <= 1
        or expected_init_outer_pid > _BROWSER_LINUX_PID_MAX
        or expected_worker_outer_pid > _BROWSER_LINUX_PID_MAX
        or expected_init_outer_pid == expected_worker_outer_pid
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    try:
        expected_raw = expected_cgroup_path.encode("ascii", errors="strict")
    except UnicodeEncodeError:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        ) from None
    if (
        parse_browser_cgroup_path(b"0::" + expected_raw + b"\n")
        != expected_cgroup_path
        or expected_cgroup_path == "/"
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    init_path = parse_browser_cgroup_path(init_cgroup)
    worker_path = parse_browser_cgroup_path(worker_cgroup)
    parsed_events = parse_browser_cgroup_events(events)
    process_ids = parse_browser_cgroup_processes(processes)
    thread_ids = parse_browser_cgroup_threads(threads)
    init_namespace_pids = parse_browser_namespace_pids(init_nspid)
    worker_namespace_pids = parse_browser_namespace_pids(worker_nspid)
    expected_members = tuple(
        sorted((expected_init_outer_pid, expected_worker_outer_pid))
    )
    if (
        init_path != expected_cgroup_path
        or worker_path != expected_cgroup_path
        or parsed_events
        != BrowserCgroupEvents(populated=True, frozen=expected_frozen)
        or process_ids != expected_members
        or thread_ids != expected_members
        or len(init_namespace_pids) < 2
        or len(init_namespace_pids) != len(worker_namespace_pids)
        or init_namespace_pids[0] != expected_init_outer_pid
        or worker_namespace_pids[0] != expected_worker_outer_pid
        or init_namespace_pids[-1] != 1
        or worker_namespace_pids[-1] != 2
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    return BrowserContainmentTextEvidence(
        cgroup_path=expected_cgroup_path,
        events=parsed_events,
        process_ids=process_ids,
        thread_ids=thread_ids,
        init_outer_pid=expected_init_outer_pid,
        worker_outer_pid=expected_worker_outer_pid,
        init_namespace_pids=init_namespace_pids,
        worker_namespace_pids=worker_namespace_pids,
    )


def _canonical_proc_integer(value: bytes, *, positive: bool) -> int:
    if (
        not value
        or len(value) > 20
        or not value.isascii()
        or not value.isdigit()
        or (len(value) > 1 and value.startswith(b"0"))
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    selected = int(value)
    if (
        selected > 18_446_744_073_709_551_615
        or (positive and selected <= 0)
        or (not positive and selected < 0)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    return selected


def _parse_browser_worker_proc_stat(
    raw: bytes,
    *,
    expected_pid: int,
) -> BrowserWorkerProcessStat:
    if (
        type(raw) is not bytes
        or not raw
        or len(raw) > _BROWSER_PROC_STAT_LIMIT
        or type(expected_pid) is not int
        or expected_pid <= 1
        or expected_pid > 2_147_483_647
        or raw.count(b"\n") != 1
        or not raw.endswith(b"\n")
        or b"\0" in raw
        or b"\r" in raw
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    selected = raw[:-1]
    prefix = str(expected_pid).encode("ascii") + b" ("
    closing = selected.rfind(b") ")
    if not selected.startswith(prefix) or closing < len(prefix):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    command_name = selected[len(prefix) : closing]
    fields = selected[closing + 2 :].split(b" ")
    if (
        not command_name
        or len(command_name) > 255
        or any(byte < 0x20 or byte > 0x7E for byte in command_name)
        or len(fields) < 20
        or any(not field for field in fields)
        or len(fields[0]) != 1
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    try:
        state = fields[0].decode("ascii", errors="strict")
    except UnicodeDecodeError:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    if state not in _BROWSER_PROCESS_STATES:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    return BrowserWorkerProcessStat(
        pid=expected_pid,
        state=state,
        parent_pid=_canonical_proc_integer(fields[1], positive=True),
        process_group=_canonical_proc_integer(fields[2], positive=True),
        session_id=_canonical_proc_integer(fields[3], positive=True),
        start_ticks=_canonical_proc_integer(fields[19], positive=True),
    )


def _read_browser_proc_value(
    path: str,
    *,
    maximum_bytes: int,
) -> bytes:
    if (
        not isinstance(path, str)
        or not path.startswith("/proc/")
        or type(maximum_bytes) is not int
        or maximum_bytes <= 0
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        observed = bytearray()
        while len(observed) <= maximum_bytes:
            try:
                block = os.read(
                    descriptor,
                    min(4096, maximum_bytes + 1 - len(observed)),
                )
            except InterruptedError:
                continue
            if not block:
                break
            observed.extend(block)
        if len(observed) > maximum_bytes:
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            )
        return bytes(observed)
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_browser_worker_proc_stat(pid: int) -> BrowserWorkerProcessStat:
    if type(pid) is not int or pid <= 1 or pid > 2_147_483_647:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    return _parse_browser_worker_proc_stat(
        _read_browser_proc_value(
            f"/proc/{pid}/stat",
            maximum_bytes=_BROWSER_PROC_STAT_LIMIT,
        ),
        expected_pid=pid,
    )


def _canonical_status_integers(
    value: str,
    *,
    count: int | None,
) -> tuple[int, ...]:
    parts = value.split()
    if count is not None and len(parts) != count:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    try:
        selected = tuple(
            _canonical_proc_integer(part.encode("ascii"), positive=False)
            for part in parts
        )
    except UnicodeEncodeError:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    return selected


def _parse_browser_worker_status(
    raw: bytes,
    *,
    process: BrowserWorkerProcessStat,
    expectation: BrowserWorkerProcessExpectation,
) -> None:
    if (
        type(raw) is not bytes
        or not raw
        or len(raw) > _BROWSER_PROC_VALUE_LIMIT
        or raw.count(b"\0")
        or raw.count(b"\r")
        or not raw.endswith(b"\n")
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    try:
        text = raw.decode("ascii", errors="strict")
        values: dict[str, str] = {}
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if not separator or not key or key in values:
                raise ValueError
            values[key] = value.strip()
        pid = _canonical_status_integers(values["Pid"], count=1)
        parent = _canonical_status_integers(values["PPid"], count=1)
        tracer = _canonical_status_integers(values["TracerPid"], count=1)
        threads = _canonical_status_integers(values["Threads"], count=1)
        user_ids = _canonical_status_integers(values["Uid"], count=4)
        group_ids = _canonical_status_integers(values["Gid"], count=4)
        groups = _canonical_status_integers(values["Groups"], count=None)
        namespace_pids = _canonical_status_integers(
            values["NSpid"],
            count=None,
        )
        no_new_privileges = _canonical_status_integers(
            values["NoNewPrivs"],
            count=1,
        )
        seccomp = _canonical_status_integers(values["Seccomp"], count=1)
    except (KeyError, UnicodeDecodeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    if (
        pid != (process.pid,)
        or parent != (process.parent_pid,)
        or tracer != (0,)
        or threads != (1,)
        or user_ids != (expectation.user_id,) * 4
        or group_ids != (expectation.group_id,) * 4
        or groups != expectation.supplementary_groups
        or not namespace_pids
        or namespace_pids[0] != process.pid
        or no_new_privileges != (expectation.no_new_privileges,)
        or seccomp != (expectation.seccomp_mode,)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )


def _validate_browser_worker_expectation(
    expectation: BrowserWorkerProcessExpectation,
) -> None:
    if type(expectation) is not BrowserWorkerProcessExpectation:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    command_line = expectation.command_line
    environment = expectation.environment
    descriptors = expectation.descriptors
    try:
        canonical_cwd = expectation.cwd.resolve(strict=True)
        cwd_info = expectation.cwd.stat(follow_symlinks=False)
    except (OSError, AttributeError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    if (
        type(command_line) is not tuple
        or not command_line
        or len(command_line) > _BROWSER_WORKER_ARGUMENT_LIMIT
        or any(
            type(value) is not str
            or not value
            or "\0" in value
            or len(value) > 4096
            or not value.isascii()
            for value in command_line
        )
        or sum(len(value) + 1 for value in command_line)
        > _BROWSER_PROC_VALUE_LIMIT
        or environment != _BROWSER_WORKER_ENVIRONMENT
        or type(descriptors) is not tuple
        or not descriptors
        or len(descriptors) > _BROWSER_WORKER_DESCRIPTOR_LIMIT
        or any(
            type(item) is not BrowserWorkerDescriptorExpectation
            for item in descriptors
        )
        or any(
            type(item.number) is not int
            or item.number < 0
            or item.number > _BROWSER_WORKER_MAX_FD
            or type(item.target) is not str
            or not item.target
            or "\0" in item.target
            or len(item.target) > 4096
            or not item.target.isascii()
            or type(item.device) is not int
            or type(item.inode) is not int
            or type(item.mount_id) is not int
            or type(item.position) is not int
            or item.device < 0
            or item.inode <= 0
            or item.mount_id <= 0
            or item.position < 0
            or type(item.status_flags) is not int
            or item.status_flags < 0
            or item.status_flags & os.O_CLOEXEC
            or item.status_flags & os.O_ACCMODE
            not in {os.O_RDONLY, os.O_WRONLY, os.O_RDWR}
            or type(item.close_on_exec) is not bool
            for item in descriptors
        )
        or tuple(sorted(descriptors, key=lambda item: item.number))
        != descriptors
        or len({item.number for item in descriptors}) != len(descriptors)
        or not {0, 1, 2}.issubset({item.number for item in descriptors})
        or expectation.cwd != canonical_cwd
        or expectation.cwd.is_symlink()
        or not stat.S_ISDIR(cwd_info.st_mode)
        or type(expectation.cwd_device) is not int
        or type(expectation.cwd_inode) is not int
        or expectation.cwd_device <= 0
        or expectation.cwd_inode <= 0
        or (cwd_info.st_dev, cwd_info.st_ino)
        != (expectation.cwd_device, expectation.cwd_inode)
        or type(expectation.user_id) is not int
        or type(expectation.group_id) is not int
        or expectation.user_id != os.geteuid()
        or expectation.user_id != os.getuid()
        or expectation.group_id != os.getegid()
        or expectation.group_id != os.getgid()
        or type(expectation.supplementary_groups) is not tuple
        or any(
            type(group) is not int or group < 0
            for group in expectation.supplementary_groups
        )
        or expectation.supplementary_groups != tuple(sorted(os.getgroups()))
        or type(expectation.no_new_privileges) is not int
        or type(expectation.seccomp_mode) is not int
        or expectation.no_new_privileges != 1
        or expectation.seccomp_mode != 2
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )


def _read_browser_worker_fd_metadata(
    pid: int,
    descriptor: int,
) -> tuple[int, int, int, int]:
    raw = _read_browser_proc_value(
        f"/proc/{pid}/fdinfo/{descriptor}",
        maximum_bytes=4096,
    )
    try:
        text = raw.decode("ascii", errors="strict")
        values: dict[str, str] = {}
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if not separator or not key or key in values:
                raise ValueError
            values[key] = value.strip()
        if set(values) != {"pos", "flags", "mnt_id", "ino"}:
            raise ValueError
        flags_text = values["flags"]
        if (
            re.fullmatch(r"[0-7]+", flags_text) is None
            or len(flags_text) > 20
        ):
            raise ValueError
        position = _canonical_proc_integer(
            values["pos"].encode("ascii"),
            positive=False,
        )
        mount_id = _canonical_proc_integer(
            values["mnt_id"].encode("ascii"),
            positive=True,
        )
        inode = _canonical_proc_integer(
            values["ino"].encode("ascii"),
            positive=True,
        )
        return position, int(flags_text, 8), mount_id, inode
    except (KeyError, UnicodeDecodeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None


def _validate_browser_worker_proc_snapshot(
    pid: int,
    runtime: PinnedBrowserPythonExecutable,
    expectation: BrowserWorkerProcessExpectation,
) -> BrowserWorkerProcessStat:
    _validate_browser_worker_expectation(expectation)
    process = _read_browser_worker_proc_stat(pid)
    if (
        process.state not in _BROWSER_LIVE_PROCESS_STATES
        or process.parent_pid != os.getpid()
        or process.process_group != pid
        or process.session_id != pid
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    _validate_browser_worker_executable(pid, runtime)
    _parse_browser_worker_status(
        _read_browser_proc_value(
            f"/proc/{pid}/status",
            maximum_bytes=_BROWSER_PROC_VALUE_LIMIT,
        ),
        process=process,
        expectation=expectation,
    )
    expected_command_line = b"\0".join(
        value.encode("ascii") for value in expectation.command_line
    ) + b"\0"
    expected_environment = b"".join(
        f"{key}={value}".encode("ascii") + b"\0"
        for key, value in expectation.environment
    )
    command_line = _read_browser_proc_value(
        f"/proc/{pid}/cmdline",
        maximum_bytes=_BROWSER_PROC_VALUE_LIMIT,
    )
    environment = _read_browser_proc_value(
        f"/proc/{pid}/environ",
        maximum_bytes=_BROWSER_PROC_VALUE_LIMIT,
    )
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
        cwd_info = os.stat(f"/proc/{pid}/cwd")
        expected_cwd_info = expectation.cwd.stat(follow_symlinks=False)
        descriptor_root = f"/proc/{pid}/fd"
        descriptor_names: list[str] = []
        with os.scandir(descriptor_root) as entries:
            for entry in entries:
                descriptor_names.append(entry.name)
                if len(descriptor_names) > len(expectation.descriptors):
                    raise ValueError
        observed_numbers = tuple(
            sorted(
                int(value)
                for value in descriptor_names
                if value.isascii() and value.isdigit()
            )
        )
        if len(observed_numbers) != len(descriptor_names):
            raise ValueError
        descriptor_values: list[BrowserWorkerDescriptorExpectation] = []
        for number in observed_numbers:
            descriptor_path = f"{descriptor_root}/{number}"
            descriptor_info = os.stat(descriptor_path)
            position, flags, mount_id, fdinfo_inode = (
                _read_browser_worker_fd_metadata(pid, number)
            )
            if fdinfo_inode != descriptor_info.st_ino:
                raise ValueError
            descriptor_values.append(
                BrowserWorkerDescriptorExpectation(
                    number=number,
                    target=os.readlink(descriptor_path),
                    device=descriptor_info.st_dev,
                    inode=descriptor_info.st_ino,
                    mount_id=mount_id,
                    position=position,
                    status_flags=flags & ~os.O_CLOEXEC,
                    close_on_exec=bool(flags & os.O_CLOEXEC),
                )
            )
        observed_descriptors = tuple(descriptor_values)
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    if (
        command_line != expected_command_line
        or environment != expected_environment
        or cwd != str(expectation.cwd)
        or (cwd_info.st_dev, cwd_info.st_ino)
        != (expectation.cwd_device, expectation.cwd_inode)
        or (expected_cwd_info.st_dev, expected_cwd_info.st_ino)
        != (expectation.cwd_device, expectation.cwd_inode)
        or observed_descriptors != expectation.descriptors
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    return process


def _stable_browser_process_identity(
    value: BrowserWorkerProcessStat,
) -> tuple[int, int, int, int, int]:
    return (
        value.pid,
        value.parent_pid,
        value.process_group,
        value.session_id,
        value.start_ticks,
    )


def _browser_pidfd_is_terminal(descriptor: int) -> bool:
    selector = selectors.DefaultSelector()
    try:
        selector.register(descriptor, selectors.EVENT_READ)
        return bool(selector.select(0))
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    finally:
        selector.close()


def _validate_browser_worker_executable(
    pid: int,
    runtime: PinnedBrowserPythonExecutable,
) -> None:
    _validate_pinned_browser_python_executable(runtime)
    try:
        executable = Path(f"/proc/{pid}/exe")
        info = executable.stat()
        target = os.readlink(executable)
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    if (
        target != str(_BROWSER_PYTHON_EXECUTABLE)
        or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        != (
            runtime.device,
            runtime.inode,
            runtime.size,
            runtime.mtime_ns,
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )


def _read_browser_pidfd_metadata(
    descriptor: int,
) -> tuple[os.stat_result, int | None, int, int]:
    if (
        type(descriptor) is not int
        or descriptor <= 2
        or descriptor > _BROWSER_WORKER_MAX_FD
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        info = os.fstat(descriptor)
        target = os.readlink(f"/proc/self/fd/{descriptor}")
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        status_flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        info_descriptor = os.open(
            f"/proc/self/fdinfo/{descriptor}",
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        try:
            raw_info = bytearray()
            while len(raw_info) <= 4096:
                block = os.read(info_descriptor, 4097 - len(raw_info))
                if not block:
                    break
                raw_info.extend(block)
        finally:
            os.close(info_descriptor)
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    try:
        text = bytes(raw_info).decode("ascii", errors="strict")
        pairs: dict[str, str] = {}
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if not separator or not key or key in pairs:
                raise ValueError
            pairs[key] = value.strip()
        if set(pairs) != {"pos", "flags", "mnt_id", "ino", "Pid", "NSpid"}:
            raise ValueError
        if (
            pairs["pos"] != "0"
            or re.fullmatch(r"[0-7]+", pairs["flags"]) is None
            or re.fullmatch(r"[1-9][0-9]*", pairs["mnt_id"]) is None
            or re.fullmatch(r"[1-9][0-9]*", pairs["ino"]) is None
            or int(pairs["flags"], 8)
            != status_flags | (
                os.O_CLOEXEC if descriptor_flags & fcntl.FD_CLOEXEC else 0
            )
            or int(pairs["ino"]) != info.st_ino
            or pairs["NSpid"] != pairs["Pid"]
        ):
            raise ValueError
        pid_value = pairs["Pid"]
        if pid_value == "-1":
            process_id = None
        elif re.fullmatch(r"[1-9][0-9]*", pid_value) is not None:
            process_id = int(pid_value)
        else:
            raise ValueError
    except (UnicodeDecodeError, KeyError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    if (
        (
            soft_limit != resource.RLIM_INFINITY
            and descriptor >= soft_limit
        )
        or target != "anon_inode:[pidfd]"
        or descriptor_flags & fcntl.FD_CLOEXEC == 0
        or os.get_inheritable(descriptor)
        or status_flags != os.O_RDWR
        or len(raw_info) > 4096
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    return info, process_id, descriptor_flags, status_flags


def _validate_browser_pidfd(
    observed: ObservedBrowserWorker,
) -> None:
    if (
        type(observed) is not ObservedBrowserWorker
        or observed._owner_token is not _BROWSER_HANDLE_TOKEN
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    info, process_id, _, _ = _read_browser_pidfd_metadata(observed.pidfd)
    if (
        process_id != observed.process.pid
        or (info.st_dev, info.st_ino)
        != (observed.pidfd_device, observed.pidfd_inode)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )


def observe_browser_worker_process(
    pid: int,
    runtime: PinnedBrowserPythonExecutable,
    *,
    expectation: BrowserWorkerProcessExpectation,
) -> ObservedBrowserWorker:
    if type(pid) is not int or pid <= 1 or pid > 2_147_483_647:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    pidfd = -1
    try:
        open_standard_descriptors: set[int] = set()
        for standard_descriptor in (0, 1, 2):
            try:
                os.fstat(standard_descriptor)
            except OSError:
                continue
            open_standard_descriptors.add(standard_descriptor)
        opener = getattr(os, "pidfd_open", None)
        if not callable(opener):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            )
        try:
            candidate_pidfd = opener(pid, 0)
        except (OSError, OverflowError, ValueError, TypeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            ) from None
        if (
            type(candidate_pidfd) is not int
            or candidate_pidfd <= 2
            or candidate_pidfd > _BROWSER_WORKER_MAX_FD
        ):
            if (
                type(candidate_pidfd) is int
                and candidate_pidfd >= 0
                and candidate_pidfd not in open_standard_descriptors
            ):
                try:
                    os.close(candidate_pidfd)
                except OSError:
                    pass
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            )
        pidfd = candidate_pidfd
        initial = _read_browser_worker_proc_stat(pid)
        if (
            initial.state not in _BROWSER_LIVE_PROCESS_STATES
            or initial.parent_pid != os.getpid()
            or initial.process_group != pid
            or initial.session_id != pid
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            )
        info = os.fstat(pidfd)
        observed = ObservedBrowserWorker(
            pidfd=pidfd,
            pidfd_device=info.st_dev,
            pidfd_inode=info.st_ino,
            process=initial,
        )
        observed._owner_token = _BROWSER_HANDLE_TOKEN
        _validate_browser_pidfd(observed)
        if _browser_pidfd_is_terminal(pidfd):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            )
        repeated = _validate_browser_worker_proc_snapshot(
            pid,
            runtime,
            expectation,
        )
        if (
            repeated.state not in _BROWSER_LIVE_PROCESS_STATES
            or _stable_browser_process_identity(repeated)
            != _stable_browser_process_identity(initial)
            or _browser_pidfd_is_terminal(pidfd)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            )
        return observed
    except BaseException:
        if pidfd >= 0:
            os.close(pidfd)
        raise


def require_browser_worker_process_live(
    observed: ObservedBrowserWorker,
    runtime: PinnedBrowserPythonExecutable,
    *,
    expectation: BrowserWorkerProcessExpectation,
) -> BrowserWorkerProcessStat:
    if type(observed) is not ObservedBrowserWorker:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    _validate_browser_pidfd(observed)
    if _browser_pidfd_is_terminal(observed.pidfd):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    repeated = _validate_browser_worker_proc_snapshot(
        observed.process.pid,
        runtime,
        expectation,
    )
    if (
        repeated.state not in _BROWSER_LIVE_PROCESS_STATES
        or _stable_browser_process_identity(repeated)
        != _stable_browser_process_identity(observed.process)
        or _browser_pidfd_is_terminal(observed.pidfd)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    return repeated


def _validate_docker_config_root(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        info = path.stat(follow_symlinks=False)
        members = tuple(path.iterdir())
    except OSError as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker configuration differs"
        ) from exc
    if (
        path != resolved
        or path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or (info.st_uid, info.st_gid) != (os.geteuid(), os.getegid())
        or stat.S_IMODE(info.st_mode) != 0o700
        or members
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker configuration differs"
        )
    return resolved


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        process.wait()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker command cleanup failed"
        ) from exc


def _run_bounded_process(
    arguments: tuple[str, ...],
    *,
    pass_fds: tuple[int, ...],
    environment: Mapping[str, str],
    cwd: Path,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
) -> BoundedProcessResult:
    if (
        not arguments
        or timeout_seconds <= 0
        or stdout_limit < 0
        or stderr_limit < 0
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker command differs"
        )
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    deadline = time.monotonic() + timeout_seconds
    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(environment),
            close_fds=True,
            pass_fds=pass_fds,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise LocalStagingAcceptanceError(
                "local acceptance Docker command differs"
            )
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LocalStagingAcceptanceError(
                    "local acceptance Docker command timed out"
                )
            events = selector.select(min(remaining, 0.25))
            if not events and process.poll() is not None:
                events = [
                    (key, selectors.EVENT_READ)
                    for key in tuple(selector.get_map().values())
                ]
            for key, _ in events:
                try:
                    block = os.read(key.fileobj.fileno(), 65_536)
                except BlockingIOError:
                    continue
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                selected = output[key.data]
                selected.extend(block)
                if len(selected) > limits[key.data]:
                    raise LocalStagingAcceptanceError(
                        "local acceptance Docker command output differs"
                    )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LocalStagingAcceptanceError(
                "local acceptance Docker command timed out"
            )
        returncode = process.wait(timeout=remaining)
        return BoundedProcessResult(
            returncode=returncode,
            stdout=bytes(output["stdout"]),
            stderr=bytes(output["stderr"]),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker command failed"
        ) from exc
    finally:
        selector.close()
        if process is not None and process.poll() is None:
            _kill_process_group(process)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def run_docker_command(
    client: TrustedDockerClient,
    config_root: Path,
    arguments: tuple[str, ...],
    *,
    timeout_seconds: float = _DOCKER_METADATA_TIMEOUT,
    stdout_limit: int = _DOCKER_METADATA_LIMIT,
    stderr_limit: int = _DOCKER_METADATA_LIMIT,
) -> BoundedProcessResult:
    """Run one local-daemon Docker command through the held exact executable."""

    _validate_trusted_docker(client)
    config = _validate_docker_config_root(config_root)
    executable = f"/proc/self/fd/{client.descriptor}"
    result = _run_bounded_process(
        (executable, *arguments),
        pass_fds=(client.descriptor,),
        environment={
            "DOCKER_API_VERSION": _DOCKER_API_VERSION,
            "DOCKER_CONFIG": str(config),
            "DOCKER_HOST": f"unix://{_DOCKER_SOCKET}",
            "HOME": str(config),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        },
        cwd=config,
        timeout_seconds=timeout_seconds,
        stdout_limit=stdout_limit,
        stderr_limit=stderr_limit,
    )
    _validate_trusted_docker(client)
    _validate_docker_config_root(config)
    return result


def run_docker_argv(
    client: TrustedDockerClient,
    config_root: Path,
    arguments: tuple[str, ...],
    *,
    timeout_seconds: float = _DOCKER_METADATA_TIMEOUT,
    stdout_limit: int = _DOCKER_METADATA_LIMIT,
    stderr_limit: int = _DOCKER_METADATA_LIMIT,
) -> BoundedProcessResult:
    expected_executable = f"/proc/self/fd/{client.descriptor}"
    if not arguments or arguments[0] != expected_executable:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker command differs"
        )
    return run_docker_command(
        client,
        config_root,
        arguments[1:],
        timeout_seconds=timeout_seconds,
        stdout_limit=stdout_limit,
        stderr_limit=stderr_limit,
    )


def _stable_file_sha256(
    path: Path,
    *,
    expected_bytes: int,
    expected_sha256: str,
    expected_uid: int,
    expected_gid: int,
) -> None:
    descriptor = -1
    observed = 0
    digest = hashlib.sha256()
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        before = os.fstat(descriptor)
        named_before = path.stat(follow_symlinks=False)
        if (
            path.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_uid, before.st_gid) != (expected_uid, expected_gid)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != expected_bytes
            or (before.st_dev, before.st_ino)
            != (named_before.st_dev, named_before.st_ino)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance ingress metadata differs"
            )
        while observed <= expected_bytes:
            block = os.read(
                descriptor,
                min(_CHUNK_BYTES, expected_bytes + 1 - observed),
            )
            if not block:
                break
            observed += len(block)
            digest.update(block)
        after = os.fstat(descriptor)
        named_after = path.stat(follow_symlinks=False)
    except LocalStagingAcceptanceError:
        raise
    except OSError as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance ingress is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        observed != expected_bytes
        or digest.hexdigest() != expected_sha256
        or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_size,
            before.st_mtime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_gid,
            after.st_size,
            after.st_mtime_ns,
        )
        or (after.st_dev, after.st_ino)
        != (named_after.st_dev, named_after.st_ino)
    ):
        raise LocalStagingAcceptanceError("local acceptance ingress changed")


def _canonical_mount_source(path: Path, *, directory: bool) -> Path:
    try:
        resolved = path.resolve(strict=True)
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance ingress is unavailable"
        ) from exc
    encoded = os.fsencode(path)
    if (
        not path.is_absolute()
        or path != resolved
        or path.is_symlink()
        or b"\x00" in encoded
        or b"\n" in encoded
        or b"\r" in encoded
        or b"," in encoded
        or b'"' in encoded
        or b"'" in encoded
        or b"\\" in encoded
        or (directory and not stat.S_ISDIR(info.st_mode))
        or (not directory and not stat.S_ISREG(info.st_mode))
    ):
        raise LocalStagingAcceptanceError("local acceptance ingress path differs")
    return resolved


def validate_materializer_ingress(
    *,
    research_root: str | Path,
    inventory_path: str | Path,
    bundle_path: str | Path,
) -> MaterializerIngress:
    """Bind the three read-only ingress mounts before Docker is invoked."""

    research = _canonical_mount_source(Path(research_root), directory=True)
    inventory = _canonical_mount_source(Path(inventory_path), directory=False)
    bundle = _canonical_mount_source(Path(bundle_path), directory=False)
    research_info = research.stat(follow_symlinks=False)
    if (
        (research_info.st_uid, research_info.st_gid) != (1000, 1000)
        or stat.S_IMODE(research_info.st_mode) != 0o700
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance research ingress differs"
        )
    _stable_file_sha256(
        inventory,
        expected_bytes=ACCEPTED_INVENTORY_BYTES,
        expected_sha256=ACCEPTED_INVENTORY_SHA256,
        expected_uid=1000,
        expected_gid=1000,
    )
    _stable_file_sha256(
        bundle,
        expected_bytes=ACCEPTED_BUNDLE_BYTES,
        expected_sha256=ACCEPTED_BUNDLE_SHA256,
        expected_uid=1000,
        expected_gid=1000,
    )
    return MaterializerIngress(research, inventory, bundle)


def _volume_name(run_id: str) -> str:
    return f"buffalo-staging-acceptance-{run_id}"


def _container_name(run_id: str, *, replay: bool) -> str:
    phase = "research-replay" if replay else "research-materialize"
    return f"buffalo-staging-{phase}-{run_id}"


def materializer_invocation(
    *,
    image_id: str,
    run_id: str,
    ingress: MaterializerIngress | None,
) -> MaterializerInvocation:
    if image_id != FROZEN_IMAGE_ID or _RUN_ID.fullmatch(run_id) is None:
        raise LocalStagingAcceptanceError(
            "local acceptance materializer identity differs"
        )
    volume_name = _volume_name(run_id)
    container_name = _container_name(run_id, replay=ingress is None)
    if (
        _SAFE_DOCKER_NAME.fullmatch(volume_name) is None
        or _SAFE_DOCKER_NAME.fullmatch(container_name) is None
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer name differs"
        )
    if ingress is not None:
        observed = validate_materializer_ingress(
            research_root=ingress.research_root,
            inventory_path=ingress.inventory_path,
            bundle_path=ingress.bundle_path,
        )
        if observed != ingress:
            raise LocalStagingAcceptanceError(
                "local acceptance materializer ingress differs"
            )
    return MaterializerInvocation(
        image_id=image_id,
        run_id=run_id,
        volume_name=volume_name,
        container_name=container_name,
        ingress=ingress,
    )


def _bind_mount(source: Path, destination: str) -> str:
    canonical = _canonical_mount_source(
        source,
        directory=source == source.resolve(strict=True) and source.is_dir(),
    )
    return (
        f"type=bind,src={canonical},dst={destination},"
        "readonly,bind-propagation=rprivate"
    )


def build_materializer_create_argv(
    *,
    docker_client: TrustedDockerClient,
    invocation: MaterializerInvocation,
) -> tuple[str, ...]:
    """Return the exact inspectable `docker create` argv for one phase."""

    _validate_trusted_docker(docker_client)
    if (
        invocation.image_id != FROZEN_IMAGE_ID
        or _RUN_ID.fullmatch(invocation.run_id) is None
        or invocation.volume_name != _volume_name(invocation.run_id)
        or invocation.container_name
        != _container_name(invocation.run_id, replay=invocation.ingress is None)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer command differs"
        )
    if invocation.ingress is not None:
        observed = validate_materializer_ingress(
            research_root=invocation.ingress.research_root,
            inventory_path=invocation.ingress.inventory_path,
            bundle_path=invocation.ingress.bundle_path,
        )
        if observed != invocation.ingress:
            raise LocalStagingAcceptanceError(
                "local acceptance materializer ingress differs"
            )

    arguments = [
        f"/proc/self/fd/{docker_client.descriptor}",
        "create",
        "--platform",
        "linux/amd64",
        "--pull",
        "never",
        "--runtime",
        "runc",
        "--user",
        "0:0",
        "--workdir",
        "/app",
        "--read-only",
        "--network",
        "none",
        "--ipc",
        "none",
        "--cgroupns",
        "private",
        "--pids-limit",
        "64",
        "--memory",
        "1073741824",
        "--memory-swap",
        "1073741824",
        "--cpus",
        "2",
        "--security-opt",
        "no-new-privileges=true",
        "--cap-drop",
        "ALL",
    ]
    for capability in _MATERIALIZER_CAPABILITIES:
        arguments.extend(("--cap-add", capability))
    arguments.extend(
        (
            "--restart",
            "no",
            "--no-healthcheck",
            "--log-driver",
            "none",
            "--name",
            invocation.container_name,
            "--label",
            f"buffalo.contract={ACCEPTANCE_CONTRACT}",
            "--label",
            f"buffalo.run={invocation.run_id}",
            "--label",
            f"buffalo.role={MATERIALIZER_ROLE}",
            "--mount",
            (
                f"type=volume,src={invocation.volume_name},dst=/data,"
                "volume-nocopy"
            ),
        )
    )
    if invocation.ingress is not None:
        arguments.extend(
            (
                "--mount",
                _bind_mount(
                    invocation.ingress.research_root,
                    "/mnt/buffalo-accepted-research",
                ),
                "--mount",
                _bind_mount(
                    invocation.ingress.inventory_path,
                    "/mnt/deployment-inventory.json",
                ),
                "--mount",
                _bind_mount(
                    invocation.ingress.bundle_path,
                    (
                        "/mnt/buffalo-procurement-os-accepted-source-"
                        "608929ad.bundle"
                    ),
                ),
            )
        )
    arguments.extend(
        (
            "--tmpfs",
            _MATERIALIZER_TMPFS,
            "--entrypoint",
            "/usr/bin/tini",
            invocation.image_id,
            *_MATERIALIZER_COMMAND,
        )
    )
    return tuple(arguments)


def build_materializer_volume_create_argv(
    *,
    docker_client: TrustedDockerClient,
    invocation: MaterializerInvocation,
) -> tuple[str, ...]:
    _validate_trusted_docker(docker_client)
    if (
        invocation.image_id != FROZEN_IMAGE_ID
        or invocation.volume_name != _volume_name(invocation.run_id)
        or _RUN_ID.fullmatch(invocation.run_id) is None
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer volume command differs"
        )
    return (
        f"/proc/self/fd/{docker_client.descriptor}",
        "volume",
        "create",
        "--driver",
        "local",
        "--label",
        f"buffalo.contract={ACCEPTANCE_CONTRACT}",
        "--label",
        f"buffalo.run={invocation.run_id}",
        "--label",
        f"buffalo.role={MATERIALIZER_VOLUME_ROLE}",
        "--name",
        invocation.volume_name,
    )


def _reject_duplicate_json_pairs(
    values: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise LocalStagingAcceptanceError(
                "local acceptance Docker response differs"
            )
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    raise LocalStagingAcceptanceError(
        "local acceptance Docker response differs"
    )


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker response differs"
        )
    return parsed


def _reject_protocol_float(_: str) -> None:
    raise LocalStagingAcceptanceError(
        "local acceptance browser frame differs"
    )


def _assert_protocol_json_value(value: object) -> None:
    if value is None or isinstance(value, (str, bool)) or type(value) is int:
        return
    if isinstance(value, list):
        for selected in value:
            _assert_protocol_json_value(selected)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise LocalStagingAcceptanceError(
                "local acceptance browser frame differs"
            )
        for selected in value.values():
            _assert_protocol_json_value(selected)
        return
    raise LocalStagingAcceptanceError(
        "local acceptance browser frame differs"
    )


def _canonical_protocol_json(value: Mapping[str, Any]) -> bytes:
    try:
        _assert_protocol_json_value(value)
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        ) from None


def encode_browser_worker_frame(
    value: Mapping[str, Any],
    *,
    maximum_bytes: int,
) -> bytes:
    if (
        type(value) is not dict
        or type(maximum_bytes) is not int
        or maximum_bytes <= 0
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        )
    body = _canonical_protocol_json(value)
    if not body or len(body) > maximum_bytes:
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        )
    return len(body).to_bytes(4, "big") + body


def decode_browser_worker_frame(
    raw: bytes,
    *,
    maximum_bytes: int,
) -> Mapping[str, Any]:
    if (
        type(maximum_bytes) is not int
        or maximum_bytes <= 0
        or len(raw) < 5
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        )
    declared = int.from_bytes(raw[:4], "big")
    body = raw[4:]
    if declared <= 0 or declared > maximum_bytes or len(body) != declared:
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        )
    try:
        text = body.decode("ascii", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_reject_protocol_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        ) from None
    if not isinstance(value, dict) or _canonical_protocol_json(value) != body:
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        )
    return value


def _require_browser_channel_deadline(deadline: float) -> None:
    if (
        isinstance(deadline, bool)
        or not isinstance(deadline, (int, float))
        or not math.isfinite(float(deadline))
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser channel differs"
        )


def _browser_pipe_identity(descriptor: int, access: int) -> tuple[int, int]:
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if (
            type(descriptor) is not int
            or descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
            or (soft_limit != resource.RLIM_INFINITY and descriptor >= soft_limit)
        ):
            raise OSError
        info = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        descriptor_target = os.readlink(f"/proc/self/fd/{descriptor}")
        if (
            not stat.S_ISFIFO(info.st_mode)
            or flags != access
            or descriptor_flags & fcntl.FD_CLOEXEC == 0
            or os.get_inheritable(descriptor)
            or descriptor_target != f"pipe:[{info.st_ino}]"
        ):
            raise OSError
        return info.st_dev, info.st_ino
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker descriptors differ"
        ) from None


def _wait_browser_channel(
    descriptor: int,
    event: int,
    deadline: float,
) -> None:
    while True:
        remaining = float(deadline) - time.monotonic()
        if remaining <= 0:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            )
        selector = selectors.DefaultSelector()
        try:
            selector.register(descriptor, event)
            try:
                ready = selector.select(remaining)
            except InterruptedError:
                continue
        except (OSError, ValueError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            ) from None
        finally:
            selector.close()
        if ready:
            return


def _read_exact_browser_pipe(
    descriptor: int,
    count: int,
    deadline: float,
) -> bytes:
    if type(count) is not int or count < 0:
        raise LocalStagingAcceptanceError(
            "local acceptance browser channel differs"
        )
    observed = bytearray()
    while len(observed) < count:
        _wait_browser_channel(
            descriptor,
            selectors.EVENT_READ,
            deadline,
        )
        try:
            block = os.read(descriptor, count - len(observed))
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            ) from None
        if not block:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            )
        observed.extend(block)
    return bytes(observed)


def _require_browser_pipe_eof(descriptor: int, deadline: float) -> None:
    while True:
        _wait_browser_channel(
            descriptor,
            selectors.EVENT_READ,
            deadline,
        )
        try:
            trailing = os.read(descriptor, 1)
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            ) from None
        if trailing:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            )
        return


def read_browser_worker_frame(
    descriptor: int,
    *,
    maximum_bytes: int,
    deadline: float,
) -> tuple[Mapping[str, Any], bytes]:
    try:
        _require_browser_channel_deadline(deadline)
        if type(maximum_bytes) is not int or maximum_bytes <= 0:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            )
        _browser_pipe_identity(descriptor, os.O_RDONLY)
        os.set_blocking(descriptor, False)
        header = _read_exact_browser_pipe(descriptor, 4, deadline)
        declared = int.from_bytes(header, "big")
        if declared <= 0 or declared > maximum_bytes:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            )
        body = _read_exact_browser_pipe(descriptor, declared, deadline)
        _require_browser_pipe_eof(descriptor, deadline)
        framed = header + body
        value = decode_browser_worker_frame(
            framed,
            maximum_bytes=maximum_bytes,
        )
        _validate_browser_worker_wire_snapshot(dict(value))
        return value, framed
    except LocalStagingAcceptanceError:
        raise
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser channel differs"
        ) from None
    finally:
        try:
            os.close(descriptor)
        except (OSError, TypeError):
            pass


def _write_exact_browser_pipe(
    descriptor: int,
    value: bytes | bytearray | memoryview,
    deadline: float,
) -> None:
    selected = memoryview(value)
    written = 0
    while written < len(selected):
        _wait_browser_channel(
            descriptor,
            selectors.EVENT_WRITE,
            deadline,
        )
        try:
            count = os.write(descriptor, selected[written:])
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            ) from None
        if count <= 0:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            )
        written += count


def write_browser_worker_frame(
    descriptor: int,
    value: Mapping[str, Any],
    *,
    maximum_bytes: int,
    deadline: float,
) -> bytes:
    try:
        _require_browser_channel_deadline(deadline)
        framed = encode_browser_worker_frame(value, maximum_bytes=maximum_bytes)
        snapshot = decode_browser_worker_frame(
            framed,
            maximum_bytes=maximum_bytes,
        )
        _validate_browser_worker_wire_snapshot(dict(snapshot))
        _browser_pipe_identity(descriptor, os.O_WRONLY)
        os.set_blocking(descriptor, False)
        _write_exact_browser_pipe(descriptor, framed, deadline)
        return framed
    except LocalStagingAcceptanceError:
        raise
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser channel differs"
        ) from None
    finally:
        try:
            os.close(descriptor)
        except (OSError, TypeError):
            pass


def read_browser_worker_secret(
    descriptor: int,
    *,
    deadline: float,
) -> bytearray:
    selected = bytearray(_BROWSER_SECRET_BYTES)
    trailing = bytearray(1)
    observed = 0
    succeeded = False
    try:
        _require_browser_channel_deadline(deadline)
        _browser_pipe_identity(descriptor, os.O_RDONLY)
        os.set_blocking(descriptor, False)
        while observed < _BROWSER_SECRET_BYTES:
            _wait_browser_channel(
                descriptor,
                selectors.EVENT_READ,
                deadline,
            )
            try:
                count = os.readv(descriptor, [memoryview(selected)[observed:]])
            except (BlockingIOError, InterruptedError):
                continue
            if count <= 0:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser credential differs"
                )
            observed += count
        while True:
            _wait_browser_channel(
                descriptor,
                selectors.EVENT_READ,
                deadline,
            )
            try:
                trailing_count = os.readv(descriptor, [trailing])
            except (BlockingIOError, InterruptedError):
                continue
            if trailing_count != 0:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser credential differs"
                )
            break
        if _BROWSER_SECRET_TEXT.fullmatch(selected) is None:
            raise LocalStagingAcceptanceError(
                "local acceptance browser credential differs"
            )
        succeeded = True
        return selected
    except LocalStagingAcceptanceError:
        raise
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser credential differs"
        ) from None
    finally:
        if not succeeded:
            for index in range(len(selected)):
                selected[index] = 0
        trailing[0] = 0
        try:
            os.close(descriptor)
        except (OSError, TypeError):
            pass


def write_browser_worker_secret(
    descriptor: int,
    secret: bytearray,
    *,
    deadline: float,
) -> None:
    try:
        _require_browser_channel_deadline(deadline)
        if (
            type(secret) is not bytearray
            or len(secret) != _BROWSER_SECRET_BYTES
            or _BROWSER_SECRET_TEXT.fullmatch(secret) is None
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser credential differs"
            )
        _browser_pipe_identity(descriptor, os.O_WRONLY)
        os.set_blocking(descriptor, False)
        _write_exact_browser_pipe(descriptor, secret, deadline)
    except LocalStagingAcceptanceError:
        raise
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser credential differs"
        ) from None
    finally:
        try:
            if isinstance(secret, bytearray):
                bytearray.__setitem__(
                    secret,
                    slice(None),
                    b"\0" * bytearray.__len__(secret),
                )
        except BaseException:
            pass
        finally:
            try:
                os.close(descriptor)
            except (OSError, TypeError):
                pass


def _require_exact_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )


def _require_exact_text(
    value: object,
    *,
    pattern: re.Pattern[str] | None = None,
    maximum: int = 1024,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in value)
        or (pattern is not None and pattern.fullmatch(value) is None)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    return value


def _require_positive_integer(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    return value


def _snapshot_browser_worker_value(
    value: Mapping[str, Any],
    *,
    maximum_bytes: int,
) -> tuple[dict[str, Any], bytes]:
    framed = encode_browser_worker_frame(value, maximum_bytes=maximum_bytes)
    decoded = decode_browser_worker_frame(framed, maximum_bytes=maximum_bytes)
    return dict(decoded), framed


def _validate_operator_proof_snapshot(
    value: object,
) -> tuple[bytes, str, str]:
    if type(value) is not dict or set(value) != _OPERATOR_PROOF_KEYS:
        raise LocalStagingAcceptanceError(
            "local acceptance browser operator proof differs"
        )
    batch_value = value.get("batch_id")
    try:
        batch_id = str(UUID(batch_value)) if isinstance(batch_value, str) else ""
    except (ValueError, TypeError, AttributeError):
        batch_id = ""
    hash_names = _OPERATOR_PROOF_KEYS - _OPERATOR_PROOF_NON_HASH_KEYS
    if (
        value.get("contract") != _OPERATOR_PROOF_CONTRACT
        or value.get("source_ref") != _OPERATOR_SOURCE_REF
        or value.get("source_bytes") != _OPERATOR_SOURCE_BYTES
        or value.get("raw_sha256") != _OPERATOR_RAW_SHA256
        or value.get("target_attestation_sha256")
        != _OPERATOR_TARGET_ATTESTATION_SHA256
        or value.get("status") != "VALIDATED"
        or batch_value != batch_id
        or any(
            not isinstance(value.get(name), str)
            or _SHA256_TEXT.fullmatch(value[name]) is None
            for name in hash_names
        )
        or type(value.get("idempotent_replay")) is not bool
        or type(value.get("ambiguous_commit_recovered")) is not bool
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser operator proof differs"
        )
    canonical = _canonical_protocol_json(value)
    return canonical, batch_id, hashlib.sha256(canonical).hexdigest()


def _validate_target_summary(value: object) -> None:
    if (
        type(value) is not dict
        or set(value) != _BROWSER_TARGET_SUMMARY_KEYS
        or any(type(item) is not int or item < 0 for item in value.values())
        or value["tracked"] != 5
        or value["guarded"] != 5
        or value["active_guarded"] != 5
        or value["inert"] != 0
        or value["tracked"] != value["guarded"] + value["inert"]
        or any(
            value[key] != 0
            for key in (
                "live_detached",
                "unsupported",
                "unattached",
                "unguarded",
                "unresumed",
            )
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser result proof differs"
        )


def _validate_browser_proof_snapshot(value: object) -> bytes:
    if type(value) is not dict or set(value) != _BROWSER_PROOF_KEYS:
        raise LocalStagingAcceptanceError(
            "local acceptance browser result proof differs"
        )
    batch_value = value.get("batch_id")
    try:
        batch_id = str(UUID(batch_value)) if isinstance(batch_value, str) else ""
    except (ValueError, TypeError, AttributeError):
        batch_id = ""
    hash_names = (
        "assertion_manifest_sha256",
        "confirmation_preview_sha256",
        "driver_sha256",
        "node_sha256",
        "operator_proof_sha256",
        "raw_sha256",
        "screenshot_sha256",
        "tls_certificate_sha256",
    )
    browser_start_time = value.get("browser_start_time")
    if (
        value.get("contract") != _BROWSER_PHASE_CONTRACT
        or value.get("phase") != _BROWSER_PHASE
        or batch_value != batch_id
        or value.get("status_before") != "VALIDATED"
        or value.get("operational_status_before") != "VALIDATED"
        or value.get("status_after") != "VERIFIED_FUTURE"
        or value.get("operational_status_after") != "VERIFIED_FUTURE"
        or value.get("temporal_basis") != "REGISTERED_OBSERVATION"
        or value.get("raw_bytes") != _OPERATOR_SOURCE_BYTES
        or value.get("raw_sha256") != _OPERATOR_RAW_SHA256
        or value.get("assertion_count") != _BROWSER_ASSERTION_COUNT
        or value.get("assertion_manifest_sha256")
        != _BROWSER_ASSERTION_MANIFEST_SHA256
        or value.get("node_version") != _BROWSER_NODE_VERSION
        or not isinstance(value.get("browser_product"), str)
        or _BROWSER_PRODUCT_TEXT.fullmatch(value["browser_product"]) is None
        or not isinstance(value.get("browser_protocol_version"), str)
        or _VERSION_TEXT.fullmatch(value["browser_protocol_version"]) is None
        or not isinstance(value.get("browser_js_version"), str)
        or _VERSION_TEXT.fullmatch(value["browser_js_version"]) is None
        or any(
            not isinstance(value.get(name), str)
            or _SHA256_TEXT.fullmatch(value[name]) is None
            for name in hash_names
        )
        or not isinstance(value.get("source_commit"), str)
        or _GIT_OID_TEXT.fullmatch(value["source_commit"]) is None
        or not isinstance(value.get("source_tree"), str)
        or _GIT_OID_TEXT.fullmatch(value["source_tree"]) is None
        or type(value.get("browser_pid")) is not int
        or value["browser_pid"] <= 1
        or not isinstance(browser_start_time, str)
        or not browser_start_time.isascii()
        or not browser_start_time.isdigit()
        or browser_start_time.startswith("0")
        or type(value.get("screenshot_bytes")) is not int
        or not 8 <= value["screenshot_bytes"] <= 10 * 1024 * 1024
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser result proof differs"
        )
    _validate_target_summary(value.get("target_summary"))
    return _canonical_protocol_json(value)


def _validate_evidence_root_text(value: object) -> str:
    selected = _require_exact_text(value, maximum=4096)
    path = Path(selected)
    if (
        selected == "/"
        or selected.startswith("//")
        or not path.is_absolute()
        or any(part in {".", ".."} for part in path.parts)
        or os.path.normpath(selected) != selected
        or str(path) != selected
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    return selected


def _validate_browser_worker_request_snapshot(
    value: dict[str, Any],
) -> BrowserWorkerRequest:
    expected = {
        "protocol",
        "frame",
        "challenge",
        "run_id",
        "source_commit",
        "source_tree",
        "cdp_endpoint",
        "evidence_root",
        "operator_proof",
        "tls_certificate_sha256",
        "chromium_pid",
    }
    _require_exact_keys(value, expected)
    if value["protocol"] != BROWSER_WORKER_PROTOCOL or value["frame"] != "REQUEST":
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    challenge = _require_exact_text(value["challenge"], pattern=_SHA256_TEXT)
    run_id = _require_exact_text(value["run_id"], pattern=_RUN_ID)
    source_commit = _require_exact_text(value["source_commit"], pattern=_GIT_OID_TEXT)
    source_tree = _require_exact_text(value["source_tree"], pattern=_GIT_OID_TEXT)
    certificate = _require_exact_text(
        value["tls_certificate_sha256"],
        pattern=_SHA256_TEXT,
    )
    cdp_endpoint = _require_exact_text(value["cdp_endpoint"], maximum=64)
    endpoint = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", cdp_endpoint)
    if endpoint is None or int(endpoint.group(1)) > 65_535:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    evidence_root = _validate_evidence_root_text(value["evidence_root"])
    operator_proof_json, batch_id, operator_proof_sha256 = (
        _validate_operator_proof_snapshot(value["operator_proof"])
    )
    chromium_pid = _require_positive_integer(value["chromium_pid"])
    if chromium_pid <= 1:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    return BrowserWorkerRequest(
        protocol=BROWSER_WORKER_PROTOCOL,
        frame="REQUEST",
        challenge=challenge,
        run_id=run_id,
        source_commit=source_commit,
        source_tree=source_tree,
        cdp_endpoint=cdp_endpoint,
        evidence_root=evidence_root,
        operator_proof_json=operator_proof_json,
        operator_batch_id=batch_id,
        operator_proof_sha256=operator_proof_sha256,
        tls_certificate_sha256=certificate,
        chromium_pid=chromium_pid,
    )


def validate_browser_worker_request(
    value: Mapping[str, Any],
) -> BrowserWorkerRequest:
    snapshot, _ = _snapshot_browser_worker_value(
        value,
        maximum_bytes=_BROWSER_REQUEST_LIMIT,
    )
    return _validate_browser_worker_request_snapshot(snapshot)


def _validate_browser_worker_ready_snapshot(
    value: dict[str, Any],
) -> BrowserWorkerReady:
    expected = {
        "protocol",
        "frame",
        "challenge",
        "config_sha256",
        "worker_pid",
        "worker_start_ticks",
        "source_commit",
        "source_tree",
        "chromium_pid",
        "browser_start_time",
        "python_executable_sha256",
        "module_manifest_sha256",
        "driver_sha256",
        "node_sha256",
        "preflight_sha256",
    }
    _require_exact_keys(value, expected)
    if value["protocol"] != BROWSER_WORKER_PROTOCOL or value["frame"] != "READY":
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    worker_pid = _require_positive_integer(value["worker_pid"])
    chromium_pid = _require_positive_integer(value["chromium_pid"])
    browser_start_time = _require_exact_text(
        value["browser_start_time"],
        pattern=_START_TICKS_TEXT,
    )
    if worker_pid <= 1 or chromium_pid <= 1:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    return BrowserWorkerReady(
        protocol=BROWSER_WORKER_PROTOCOL,
        frame="READY",
        challenge=_require_exact_text(value["challenge"], pattern=_SHA256_TEXT),
        config_sha256=_require_exact_text(
            value["config_sha256"], pattern=_SHA256_TEXT
        ),
        worker_pid=worker_pid,
        worker_start_ticks=_require_positive_integer(value["worker_start_ticks"]),
        source_commit=_require_exact_text(
            value["source_commit"], pattern=_GIT_OID_TEXT
        ),
        source_tree=_require_exact_text(value["source_tree"], pattern=_GIT_OID_TEXT),
        chromium_pid=chromium_pid,
        browser_start_time=browser_start_time,
        python_executable_sha256=_require_exact_text(
            value["python_executable_sha256"], pattern=_SHA256_TEXT
        ),
        module_manifest_sha256=_require_exact_text(
            value["module_manifest_sha256"], pattern=_SHA256_TEXT
        ),
        driver_sha256=_require_exact_text(
            value["driver_sha256"], pattern=_SHA256_TEXT
        ),
        node_sha256=_require_exact_text(value["node_sha256"], pattern=_SHA256_TEXT),
        preflight_sha256=_require_exact_text(
            value["preflight_sha256"], pattern=_SHA256_TEXT
        ),
    )


def validate_browser_worker_ready(
    value: Mapping[str, Any],
) -> BrowserWorkerReady:
    snapshot, _ = _snapshot_browser_worker_value(
        value,
        maximum_bytes=_BROWSER_READY_LIMIT,
    )
    return _validate_browser_worker_ready_snapshot(snapshot)


def _validate_browser_worker_result_snapshot(
    value: dict[str, Any],
) -> BrowserWorkerResult:
    expected = {
        "protocol",
        "frame",
        "challenge",
        "config_sha256",
        "ready_sha256",
        "worker_pid",
        "worker_start_ticks",
        "proof",
    }
    _require_exact_keys(value, expected)
    if value["protocol"] != BROWSER_WORKER_PROTOCOL or value["frame"] != "RESULT":
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    proof_json = _validate_browser_proof_snapshot(value["proof"])
    worker_pid = _require_positive_integer(value["worker_pid"])
    if worker_pid <= 1:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    return BrowserWorkerResult(
        protocol=BROWSER_WORKER_PROTOCOL,
        frame="RESULT",
        challenge=_require_exact_text(value["challenge"], pattern=_SHA256_TEXT),
        config_sha256=_require_exact_text(
            value["config_sha256"], pattern=_SHA256_TEXT
        ),
        ready_sha256=_require_exact_text(
            value["ready_sha256"], pattern=_SHA256_TEXT
        ),
        worker_pid=worker_pid,
        worker_start_ticks=_require_positive_integer(value["worker_start_ticks"]),
        proof_json=proof_json,
    )


def _validate_browser_worker_wire_snapshot(value: dict[str, Any]) -> None:
    frame = value.get("frame")
    if frame == "REQUEST":
        _validate_browser_worker_request_snapshot(value)
    elif frame == "READY":
        _validate_browser_worker_ready_snapshot(value)
    elif frame == "RESULT":
        _validate_browser_worker_result_snapshot(value)
    else:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )


def validate_browser_worker_result(
    value: Mapping[str, Any],
) -> BrowserWorkerResult:
    snapshot, _ = _snapshot_browser_worker_value(
        value,
        maximum_bytes=_BROWSER_RESULT_LIMIT,
    )
    return _validate_browser_worker_result_snapshot(snapshot)


def browser_worker_request_sha256(value: Mapping[str, Any]) -> str:
    snapshot, framed = _snapshot_browser_worker_value(
        value,
        maximum_bytes=_BROWSER_REQUEST_LIMIT,
    )
    _validate_browser_worker_request_snapshot(snapshot)
    return hashlib.sha256(framed).hexdigest()


def browser_worker_ready_sha256(value: Mapping[str, Any]) -> str:
    snapshot, framed = _snapshot_browser_worker_value(
        value,
        maximum_bytes=_BROWSER_READY_LIMIT,
    )
    _validate_browser_worker_ready_snapshot(snapshot)
    return hashlib.sha256(framed).hexdigest()


def new_browser_worker_identifiers() -> tuple[str, str]:
    return os.urandom(32).hex(), os.urandom(16).hex()


def validate_browser_worker_ready_attestation(
    request: BrowserWorkerRequest,
    ready: BrowserWorkerReady,
    expected: BrowserWorkerExpectedAttestation,
) -> None:
    if type(expected) is not BrowserWorkerExpectedAttestation:
        raise LocalStagingAcceptanceError(
            "local acceptance browser attestation differs"
        )
    try:
        expected_challenge = _require_exact_text(
            expected.challenge,
            pattern=_SHA256_TEXT,
        )
        expected_run_id = _require_exact_text(expected.run_id, pattern=_RUN_ID)
        expected_source_commit = _require_exact_text(
            expected.source_commit,
            pattern=_GIT_OID_TEXT,
        )
        expected_source_tree = _require_exact_text(
            expected.source_tree,
            pattern=_GIT_OID_TEXT,
        )
        expected_cdp = _require_exact_text(expected.cdp_endpoint, maximum=64)
        expected_evidence = _validate_evidence_root_text(expected.evidence_root)
        expected_tls = _require_exact_text(
            expected.tls_certificate_sha256,
            pattern=_SHA256_TEXT,
        )
        expected_browser_pid = _require_positive_integer(expected.chromium_pid)
        expected_worker_pid = _require_positive_integer(expected.worker_pid)
        expected_worker_start = _require_positive_integer(
            expected.worker_start_ticks
        )
        hashes = (
            _require_exact_text(
                expected.python_executable_sha256,
                pattern=_SHA256_TEXT,
            ),
            _require_exact_text(expected.module_manifest_sha256, pattern=_SHA256_TEXT),
            _require_exact_text(expected.driver_sha256, pattern=_SHA256_TEXT),
            _require_exact_text(expected.node_sha256, pattern=_SHA256_TEXT),
            _require_exact_text(expected.preflight_sha256, pattern=_SHA256_TEXT),
        )
    except LocalStagingAcceptanceError:
        raise LocalStagingAcceptanceError(
            "local acceptance browser attestation differs"
        ) from None
    if (
        expected_browser_pid <= 1
        or expected_worker_pid <= 1
        or not isinstance(expected.browser_start_time, str)
        or not expected.browser_start_time.isascii()
        or not expected.browser_start_time.isdigit()
        or expected.browser_start_time.startswith("0")
        or request.challenge != expected_challenge
        or request.run_id != expected_run_id
        or request.source_commit != expected_source_commit
        or request.source_tree != expected_source_tree
        or request.cdp_endpoint != expected_cdp
        or request.evidence_root != expected_evidence
        or request.tls_certificate_sha256 != expected_tls
        or request.chromium_pid != expected_browser_pid
        or ready.worker_pid != expected_worker_pid
        or ready.worker_start_ticks != expected_worker_start
        or ready.source_commit != expected_source_commit
        or ready.source_tree != expected_source_tree
        or ready.chromium_pid != expected_browser_pid
        or ready.browser_start_time != expected.browser_start_time
        or (
            ready.python_executable_sha256,
            ready.module_manifest_sha256,
            ready.driver_sha256,
            ready.node_sha256,
            ready.preflight_sha256,
        )
        != hashes
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser attestation differs"
        )


def validate_browser_worker_transition(
    request_value: Mapping[str, Any],
    ready_value: Mapping[str, Any],
    result_value: Mapping[str, Any] | None = None,
    *,
    expected: BrowserWorkerExpectedAttestation,
) -> tuple[BrowserWorkerRequest, BrowserWorkerReady, BrowserWorkerResult | None]:
    request_snapshot, request_frame = _snapshot_browser_worker_value(
        request_value,
        maximum_bytes=_BROWSER_REQUEST_LIMIT,
    )
    ready_snapshot, ready_frame = _snapshot_browser_worker_value(
        ready_value,
        maximum_bytes=_BROWSER_READY_LIMIT,
    )
    request = _validate_browser_worker_request_snapshot(request_snapshot)
    ready = _validate_browser_worker_ready_snapshot(ready_snapshot)
    request_sha256 = hashlib.sha256(request_frame).hexdigest()
    ready_sha256 = hashlib.sha256(ready_frame).hexdigest()
    if (
        ready.challenge != request.challenge
        or ready.config_sha256 != request_sha256
        or ready.source_commit != request.source_commit
        or ready.source_tree != request.source_tree
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser transition differs"
        )
    validate_browser_worker_ready_attestation(request, ready, expected)
    result: BrowserWorkerResult | None = None
    if result_value is not None:
        result_snapshot, _ = _snapshot_browser_worker_value(
            result_value,
            maximum_bytes=_BROWSER_RESULT_LIMIT,
        )
        result = _validate_browser_worker_result_snapshot(result_snapshot)
        proof = json.loads(result.proof_json)
        if (
            result.challenge != request.challenge
            or result.config_sha256 != request_sha256
            or result.ready_sha256 != ready_sha256
            or result.worker_pid != ready.worker_pid
            or result.worker_start_ticks != ready.worker_start_ticks
            or proof["batch_id"] != request.operator_batch_id
            or proof["source_commit"] != request.source_commit
            or proof["source_tree"] != request.source_tree
            or proof["driver_sha256"] != ready.driver_sha256
            or proof["node_sha256"] != ready.node_sha256
            or proof["operator_proof_sha256"] != request.operator_proof_sha256
            or proof["browser_pid"] != request.chromium_pid
            or proof["browser_start_time"] != expected.browser_start_time
            or proof["tls_certificate_sha256"]
            != request.tls_certificate_sha256
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser transition differs"
            )
    return request, ready, result


def parse_browser_worker_arguments(
    arguments: list[str] | tuple[str, ...],
) -> BrowserWorkerArguments:
    if len(arguments) != 5 or arguments[0] != BROWSER_WORKER_HIDDEN_MODE:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker arguments differ"
        )
    raw_descriptors = arguments[1:]
    if any(
        not isinstance(value, str)
        or len(value) > 7
        or _CANONICAL_FD.fullmatch(value) is None
        for value in raw_descriptors
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker arguments differ"
        )
    descriptors = tuple(int(value) for value in raw_descriptors)
    if (
        len(set(descriptors)) != 4
        or any(value > _BROWSER_WORKER_MAX_FD for value in descriptors)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker arguments differ"
        )
    return BrowserWorkerArguments(*descriptors)


def validate_browser_worker_descriptors(
    arguments: BrowserWorkerArguments,
) -> BrowserWorkerArguments:
    if type(arguments) is not BrowserWorkerArguments:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker descriptors differ"
        )
    descriptors = (
        arguments.request_descriptor,
        arguments.ready_descriptor,
        arguments.secret_descriptor,
        arguments.result_descriptor,
    )
    expected_access = (
        os.O_RDONLY,
        os.O_WRONLY,
        os.O_RDONLY,
        os.O_WRONLY,
    )
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        identities: list[tuple[int, int]] = []
        for descriptor, access in zip(descriptors, expected_access, strict=True):
            if (
                type(descriptor) is not int
                or descriptor <= 2
                or descriptor > _BROWSER_WORKER_MAX_FD
                or (soft_limit != resource.RLIM_INFINITY and descriptor >= soft_limit)
            ):
                raise OSError
            info = os.fstat(descriptor)
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
            descriptor_target = os.readlink(f"/proc/self/fd/{descriptor}")
            if (
                not stat.S_ISFIFO(info.st_mode)
                or flags != access
                or descriptor_target != f"pipe:[{info.st_ino}]"
            ):
                raise OSError
            identities.append((info.st_dev, info.st_ino))
        if len(set(identities)) != 4:
            raise OSError
        for descriptor in descriptors:
            descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
            fcntl.fcntl(descriptor, fcntl.F_SETFD, descriptor_flags | fcntl.FD_CLOEXEC)
            os.set_inheritable(descriptor, False)
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker descriptors differ"
        ) from None
    return arguments


def parse_single_json_object(raw: bytes) -> Mapping[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker response differs"
        ) from None
    if not isinstance(value, dict):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker response differs"
        )
    return value


def parse_single_json_array(raw: bytes) -> list[Mapping[str, Any]]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker response differs"
        ) from None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker response differs"
        )
    return value


def parse_container_create_output(raw: bytes) -> str:
    if re.fullmatch(rb"[0-9a-f]{64}\n", raw) is None:
        raise LocalStagingAcceptanceError(
            "local acceptance materializer creation differs"
        )
    return raw[:-1].decode("ascii")


def parse_volume_create_output(raw: bytes, *, expected_name: str) -> None:
    if raw != f"{expected_name}\n".encode("ascii"):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer volume creation differs"
        )


def _parse_docker_timestamp(value: object, *, allow_zero: bool) -> int:
    if not isinstance(value, str) or (not allow_zero and value == _DOCKER_ZERO_TIME):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker timestamp differs"
        )
    match = re.fullmatch(
        r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})"
        r"(?:\.(\d{1,9}))?Z",
        value,
    )
    if match is None:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker timestamp differs"
        )
    try:
        year, month, day, hour, minute, second = (
            int(selected) for selected in match.groups()[:6]
        )
        parsed = datetime(year, month, day, hour, minute, second)
    except ValueError:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker timestamp differs"
        ) from None
    fractional = match.group(7) or ""
    nanoseconds = int(fractional.ljust(9, "0")) if fractional else 0
    whole_seconds = (
        (parsed.toordinal() - 1) * 86_400
        + hour * 3_600
        + minute * 60
        + second
    )
    return whole_seconds * 1_000_000_000 + nanoseconds


def validate_materializer_volume_inspect(
    value: Mapping[str, Any],
    *,
    invocation: MaterializerInvocation,
) -> MaterializerVolumeFingerprint:
    labels = {
        "buffalo.contract": ACCEPTANCE_CONTRACT,
        "buffalo.run": invocation.run_id,
        "buffalo.role": MATERIALIZER_VOLUME_ROLE,
    }
    expected_mountpoint = (
        f"/var/lib/docker/volumes/{invocation.volume_name}/_data"
    )
    try:
        if (
            invocation.image_id != FROZEN_IMAGE_ID
            or value["Name"] != invocation.volume_name
            or value["Driver"] != "local"
            or value["Scope"] != "local"
            or value["Labels"] != labels
            or value["Options"] is not None
            or value["Mountpoint"] != expected_mountpoint
            or set(value)
            != {
                "CreatedAt",
                "Driver",
                "Labels",
                "Mountpoint",
                "Name",
                "Options",
                "Scope",
            }
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance materializer volume differs"
            )
        _parse_docker_timestamp(value["CreatedAt"], allow_zero=False)
    except LocalStagingAcceptanceError:
        raise
    except (KeyError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer volume differs"
        ) from None
    return MaterializerVolumeFingerprint(
        name=value["Name"],
        driver=value["Driver"],
        scope=value["Scope"],
        labels=tuple(sorted(value["Labels"].items())),
        options=None,
        mountpoint=value["Mountpoint"],
        created_at=value["CreatedAt"],
    )


def validate_docker_version(value: Mapping[str, Any]) -> None:
    try:
        client = value["Client"]
        server = value["Server"]
        component_rows = server["Components"]
        components = {item["Name"]: item for item in component_rows}
        if (
            not isinstance(component_rows, list)
            or len(component_rows) != len(components)
            or set(components) != {"Engine", "containerd", "runc", "docker-init"}
            or client["Version"] != _DOCKER_CLIENT_VERSION
            or client["ApiVersion"] != _DOCKER_API_VERSION
            or client["Os"] != "linux"
            or client["Arch"] != "amd64"
            or server["Version"] != _DOCKER_CLIENT_VERSION
            or server["ApiVersion"] != _DOCKER_API_VERSION
            or server["Os"] != "linux"
            or server["Arch"] != "amd64"
            or components["Engine"]["Version"] != _DOCKER_CLIENT_VERSION
            or components["containerd"]["Version"] != "v2.1.4"
            or components["runc"]["Version"] != "1.2.4"
            or components["docker-init"]["Version"] != "0.19.0"
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance Docker version differs"
            )
    except LocalStagingAcceptanceError:
        raise
    except (KeyError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker version differs"
        ) from None


def validate_docker_info(value: Mapping[str, Any]) -> None:
    try:
        cpu_count = value["NCPU"]
        memory_bytes = value["MemTotal"]
        runtimes = value["Runtimes"]
        if not isinstance(runtimes, dict) or set(runtimes) != {
            "io.containerd.runc.v2",
            "runc",
        }:
            raise LocalStagingAcceptanceError(
                "local acceptance Docker daemon differs"
            )
        for runtime in runtimes.values():
            if (
                not isinstance(runtime, dict)
                or runtime.get("path") != "runc"
                or set(runtime.get("status", {}))
                != {"org.opencontainers.runtime-spec.features"}
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance Docker daemon differs"
                )
            features = parse_single_json_object(
                runtime["status"][
                    "org.opencontainers.runtime-spec.features"
                ].encode("utf-8")
            )
            if (
                features["linux"]["cgroup"]["v2"] is not True
                or features["linux"]["seccomp"]["enabled"] is not True
                or features["annotations"]["org.opencontainers.runc.version"]
                != "1.2.4"
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance Docker daemon differs"
                )
        if (
            value["ServerVersion"] != _DOCKER_CLIENT_VERSION
            or value["Driver"] != "overlay2"
            or value["CgroupVersion"] != "2"
            or value["CgroupDriver"] != "cgroupfs"
            or value["OSType"] != "linux"
            or value["Architecture"] != "x86_64"
            or value["DefaultRuntime"] != "runc"
            or value["MemoryLimit"] is not True
            or value["SwapLimit"] is not True
            or value["PidsLimit"] is not True
            or type(cpu_count) is not int
            or cpu_count < 2
            or type(memory_bytes) is not int
            or memory_bytes < 1_073_741_824
            or value["DockerRootDir"] != "/var/lib/docker"
            or value["SecurityOptions"]
            != ["name=seccomp,profile=builtin", "name=cgroupns"]
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance Docker daemon differs"
            )
    except LocalStagingAcceptanceError:
        raise
    except (KeyError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker daemon differs"
        ) from None


def validate_frozen_image_inspect(
    value: Mapping[str, Any],
    *,
    image_id: str,
) -> None:
    """Prove the materializer image is the exact frozen service image."""

    try:
        config = value["Config"]
        rootfs = value["RootFS"]
        if (
            image_id != FROZEN_IMAGE_ID
            or value["Id"] != FROZEN_IMAGE_ID
            or value["Architecture"] != "amd64"
            or value["Os"] != "linux"
            or value["RepoDigests"] != []
            or config["User"] != "0:0"
            or tuple(config["Entrypoint"]) != _SERVICE_ENTRYPOINT
            or config["Cmd"] is not None
            or config["WorkingDir"] != "/app"
            or config["StopSignal"] != "SIGTERM"
            or tuple(config["Env"]) != _IMAGE_ENVIRONMENT
            or config.get("Volumes") is not None
            or config.get("ExposedPorts") is not None
            or config.get("Labels") is not None
            or rootfs["Type"] != "layers"
            or tuple(rootfs["Layers"]) != FROZEN_IMAGE_ROOTFS_LAYERS
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance image identity differs"
            )
    except (KeyError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance image identity differs"
        ) from None


def _expected_materializer_mounts(
    invocation: MaterializerInvocation,
) -> list[dict[str, Any]]:
    mounts: list[dict[str, Any]] = [
        {
            "Type": "volume",
            "Source": invocation.volume_name,
            "Target": "/data",
            "VolumeOptions": {"NoCopy": True, "DriverConfig": {}},
        }
    ]
    if invocation.ingress is not None:
        mounts.extend(
            (
                {
                    "Type": "bind",
                    "Source": str(invocation.ingress.research_root),
                    "Target": "/mnt/buffalo-accepted-research",
                    "ReadOnly": True,
                    "BindOptions": {"Propagation": "rprivate"},
                },
                {
                    "Type": "bind",
                    "Source": str(invocation.ingress.inventory_path),
                    "Target": "/mnt/deployment-inventory.json",
                    "ReadOnly": True,
                    "BindOptions": {"Propagation": "rprivate"},
                },
                {
                    "Type": "bind",
                    "Source": str(invocation.ingress.bundle_path),
                    "Target": (
                        "/mnt/buffalo-procurement-os-accepted-source-"
                        "608929ad.bundle"
                    ),
                    "ReadOnly": True,
                    "BindOptions": {"Propagation": "rprivate"},
                },
            )
        )
    return mounts


def validate_materializer_container_inspect(
    value: Mapping[str, Any],
    *,
    invocation: MaterializerInvocation,
    expected_state: str = "created",
) -> str:
    """Validate the complete security envelope before start or cleanup."""

    if expected_state not in {"created", "running", "exited"}:
        raise LocalStagingAcceptanceError(
            "local acceptance materializer state differs"
        )

    labels = {
        "buffalo.contract": ACCEPTANCE_CONTRACT,
        "buffalo.run": invocation.run_id,
        "buffalo.role": MATERIALIZER_ROLE,
    }
    expected_mounts = _expected_materializer_mounts(invocation)
    try:
        container_id = value["Id"]
        state = value["State"]
        host = value["HostConfig"]
        config = value["Config"]
        created_at = _parse_docker_timestamp(value["Created"], allow_zero=False)
        started_at = _parse_docker_timestamp(state["StartedAt"], allow_zero=True)
        finished_at = _parse_docker_timestamp(state["FinishedAt"], allow_zero=True)
        if expected_state == "created":
            state_matches = (
                type(state["Pid"]) is int
                and state["Pid"] == 0
                and type(state["ExitCode"]) is int
                and state["ExitCode"] == 0
                and state["StartedAt"] == _DOCKER_ZERO_TIME
                and state["FinishedAt"] == _DOCKER_ZERO_TIME
            )
        elif expected_state == "running":
            state_matches = (
                type(state["Pid"]) is int
                and state["Pid"] > 0
                and type(state["ExitCode"]) is int
                and state["ExitCode"] == 0
                and state["StartedAt"] != _DOCKER_ZERO_TIME
                and state["FinishedAt"] == _DOCKER_ZERO_TIME
                and created_at <= started_at
            )
        else:
            state_matches = (
                type(state["Pid"]) is int
                and state["Pid"] == 0
                and type(state["ExitCode"]) is int
                and state["ExitCode"] == 0
                and state["StartedAt"] != _DOCKER_ZERO_TIME
                and state["FinishedAt"] != _DOCKER_ZERO_TIME
                and created_at <= started_at <= finished_at
            )
        if expected_state == "created":
            runtime_paths_match = (
                value["ResolvConfPath"] == ""
                and value["HostnamePath"] == ""
                and value["HostsPath"] == ""
            )
            expected_oom_kill_disable: object = False
        else:
            runtime_root = f"/var/lib/docker/containers/{container_id}"
            runtime_paths_match = (
                value["ResolvConfPath"] == f"{runtime_root}/resolv.conf"
                and value["HostnamePath"] == f"{runtime_root}/hostname"
                and value["HostsPath"] == f"{runtime_root}/hosts"
            )
            expected_oom_kill_disable = None
        if (
            not isinstance(container_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
            or value["Image"] != invocation.image_id
            or value["Platform"] != "linux"
            or value["Path"] != "/usr/bin/tini"
            or tuple(value["Args"]) != _MATERIALIZER_COMMAND
            or value["Name"] != f"/{invocation.container_name}"
            or value["RestartCount"] != 0
            or value["LogPath"] != ""
            or not runtime_paths_match
            or state["Status"] != expected_state
            or state["Running"] is not (expected_state == "running")
            or not state_matches
            or state["Paused"] is not False
            or state["Restarting"] is not False
            or state["OOMKilled"] is not False
            or state["Dead"] is not False
            or state["Error"] != ""
            or host["LogConfig"] != {"Type": "none", "Config": {}}
            or host["NetworkMode"] != "none"
            or host["PortBindings"] != {}
            or host["RestartPolicy"]
            != {"Name": "no", "MaximumRetryCount": 0}
            or host["AutoRemove"] is not False
            or host["VolumesFrom"] is not None
            or host["Binds"] is not None
            or host["Links"] is not None
            or tuple(host["CapAdd"]) != _MATERIALIZER_CAPABILITIES
            or host["CapDrop"] != ["ALL"]
            or host["CgroupnsMode"] != "private"
            or host["Dns"] != []
            or host["DnsOptions"] != []
            or host["DnsSearch"] != []
            or host["ExtraHosts"] is not None
            or host["GroupAdd"] is not None
            or host["IpcMode"] != "none"
            or host["PidMode"] != ""
            or host["UTSMode"] != ""
            or host["UsernsMode"] != ""
            or host["Privileged"] is not False
            or host["PublishAllPorts"] is not False
            or host["ReadonlyRootfs"] is not True
            or host["SecurityOpt"] != ["no-new-privileges=true"]
            or host["Tmpfs"]
            != {
                "/run/buffalo-research-materializer": (
                    "rw,nosuid,nodev,noexec,mode=0700,uid=0,gid=0,"
                    "size=67108864"
                )
            }
            or host["Runtime"] != "runc"
            or host["Memory"] != 1_073_741_824
            or host["MemorySwap"] != 1_073_741_824
            or host["MemorySwappiness"] is not None
            or host["NanoCpus"] != 2_000_000_000
            or host["OomKillDisable"] is not expected_oom_kill_disable
            or host["PidsLimit"] != 64
            or host["Devices"] != []
            or host["DeviceRequests"] is not None
            or host["DeviceCgroupRules"] is not None
            or host["Ulimits"] != []
            or "Sysctls" in host
            or host["ShmSize"] != 67_108_864
            or host["MaskedPaths"]
            != [
                "/proc/asound",
                "/proc/acpi",
                "/proc/kcore",
                "/proc/keys",
                "/proc/latency_stats",
                "/proc/timer_list",
                "/proc/timer_stats",
                "/proc/sched_debug",
                "/proc/scsi",
                "/sys/firmware",
                "/sys/devices/virtual/powercap",
            ]
            or host["ReadonlyPaths"]
            != [
                "/proc/bus",
                "/proc/fs",
                "/proc/irq",
                "/proc/sys",
                "/proc/sysrq-trigger",
            ]
            or host["Mounts"] != expected_mounts
            or config["User"] != "0:0"
            or config["AttachStdin"] is not False
            or config["AttachStdout"] is not True
            or config["AttachStderr"] is not True
            or config["Tty"] is not False
            or config["OpenStdin"] is not False
            or tuple(config["Env"]) != _IMAGE_ENVIRONMENT
            or tuple(config["Cmd"]) != _MATERIALIZER_COMMAND
            or config["Healthcheck"] != {"Test": ["NONE"]}
            or config["Image"] != invocation.image_id
            or config.get("Volumes") is not None
            or config["WorkingDir"] != "/app"
            or config["Entrypoint"] != ["/usr/bin/tini"]
            or config["Labels"] != labels
            or config["StopSignal"] != "SIGTERM"
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance materializer container differs"
            )
        runtime_mounts = value["Mounts"]
        if (
            not isinstance(runtime_mounts, list)
            or len(runtime_mounts) != len(expected_mounts)
            or len({item["Destination"] for item in runtime_mounts})
            != len(runtime_mounts)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance materializer mounts differ"
            )
        observed_mounts = {item["Destination"]: item for item in runtime_mounts}
        expected_destinations = {item["Target"] for item in expected_mounts}
        if set(observed_mounts) != expected_destinations:
            raise LocalStagingAcceptanceError(
                "local acceptance materializer mounts differ"
            )
        data = observed_mounts["/data"]
        if (
            data["Type"] != "volume"
            or data["Name"] != invocation.volume_name
            or data["Driver"] != "local"
            or data["Source"]
            != f"/var/lib/docker/volumes/{invocation.volume_name}/_data"
            or data["Mode"] != "z"
            or data["RW"] is not True
            or data["Propagation"] != ""
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance materializer mounts differ"
            )
        for expected in expected_mounts[1:]:
            item = observed_mounts[expected["Target"]]
            if (
                item["Type"] != "bind"
                or item["Source"] != expected["Source"]
                or item["Mode"] != ""
                or item["RW"] is not False
                or item["Propagation"] != "rprivate"
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance materializer mounts differ"
                )
    except LocalStagingAcceptanceError:
        raise
    except (KeyError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer container differs"
        ) from None
    return container_id


def materializer_container_envelope_sha256(value: Mapping[str, Any]) -> str:
    """Hash every immutable container field used across start/exit."""

    keys = (
        "Id",
        "Created",
        "Path",
        "Args",
        "Image",
        "LogPath",
        "Name",
        "RestartCount",
        "Driver",
        "Platform",
        "MountLabel",
        "ProcessLabel",
        "AppArmorProfile",
        "ExecIDs",
        "HostConfig",
        "GraphDriver",
        "Mounts",
        "Config",
    )
    try:
        projection = {key: value[key] for key in keys}
        projection["HostConfig"] = {
            key: selected
            for key, selected in value["HostConfig"].items()
            if key != "OomKillDisable"
        }
        encoded = json.dumps(
            projection,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (KeyError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer envelope differs"
        ) from None
    return hashlib.sha256(encoded).hexdigest()


def _require_success(
    result: BoundedProcessResult,
    *,
    stdout: bytes | None = None,
    stderr: bytes = b"",
) -> None:
    if (
        result.returncode != 0
        or result.stderr != stderr
        or (stdout is not None and result.stdout != stdout)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker operation failed"
        )


def _docker_inspect_one(
    client: TrustedDockerClient,
    config_root: Path,
    resource: str,
    identity: str,
) -> Mapping[str, Any]:
    if resource not in {"container", "image", "volume"}:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker inspection differs"
        )
    result = run_docker_command(
        client,
        config_root,
        (resource, "inspect", identity),
    )
    _require_success(result)
    values = parse_single_json_array(result.stdout)
    if len(values) != 1:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker inspection differs"
        )
    return values[0]


def _docker_name_inventory(
    client: TrustedDockerClient,
    config_root: Path,
    *,
    resource: str,
) -> tuple[tuple[str, str], ...]:
    if resource == "container":
        arguments = (
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--format",
            "{{.ID}}\t{{.Names}}",
        )
        pattern = re.compile(r"\A([0-9a-f]{64})\t([a-zA-Z0-9][a-zA-Z0-9_.-]*)\Z")
    elif resource == "volume":
        arguments = ("volume", "ls", "--format", "{{.Name}}")
        pattern = re.compile(r"\A()([a-zA-Z0-9][a-zA-Z0-9_.-]*)\Z")
    else:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker inventory differs"
        )
    result = run_docker_command(client, config_root, arguments)
    _require_success(result)
    try:
        text = result.stdout.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker inventory differs"
        ) from None
    observed: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = pattern.fullmatch(line)
        if match is None:
            raise LocalStagingAcceptanceError(
                "local acceptance Docker inventory differs"
            )
        observed.append((match.group(1), match.group(2)))
    if len(observed) != len(set(observed)):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker inventory differs"
        )
    return tuple(observed)


def require_docker_name_absent(
    client: TrustedDockerClient,
    config_root: Path,
    *,
    resource: str,
    name: str,
) -> None:
    if _SAFE_DOCKER_NAME.fullmatch(name) is None:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker name differs"
        )
    inventory = _docker_name_inventory(
        client,
        config_root,
        resource=resource,
    )
    if any(observed_name == name for _, observed_name in inventory):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker name is already present"
        )


def attest_docker_materializer_runtime(
    client: TrustedDockerClient,
    config_root: Path,
) -> None:
    _validate_docker_socket()
    version = run_docker_command(
        client,
        config_root,
        ("version", "--format", "{{json .}}"),
    )
    _require_success(version)
    validate_docker_version(parse_single_json_object(version.stdout))
    info = run_docker_command(
        client,
        config_root,
        ("info", "--format", "{{json .}}"),
    )
    _require_success(info)
    validate_docker_info(parse_single_json_object(info.stdout))
    image = _docker_inspect_one(
        client,
        config_root,
        "image",
        FROZEN_IMAGE_ID,
    )
    validate_frozen_image_inspect(image, image_id=FROZEN_IMAGE_ID)


def create_materializer_volume(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
) -> MaterializerVolumeFingerprint:
    require_docker_name_absent(
        client,
        config_root,
        resource="volume",
        name=invocation.volume_name,
    )
    arguments = build_materializer_volume_create_argv(
        docker_client=client,
        invocation=invocation,
    )
    try:
        result = run_docker_argv(client, config_root, arguments)
        _require_success(result)
        parse_volume_create_output(
            result.stdout,
            expected_name=invocation.volume_name,
        )
    except LocalStagingAcceptanceError:
        # An interrupted client can leave a committed daemon operation.  Only
        # the exact code-owned volume is recoverable; foreign state is retained.
        try:
            observed = _docker_inspect_one(
                client,
                config_root,
                "volume",
                invocation.volume_name,
            )
            return validate_materializer_volume_inspect(
                observed,
                invocation=invocation,
            )
        except LocalStagingAcceptanceError:
            raise LocalStagingAcceptanceError(
                "local acceptance materializer volume creation failed"
            ) from None
    observed = _docker_inspect_one(
        client,
        config_root,
        "volume",
        invocation.volume_name,
    )
    return validate_materializer_volume_inspect(observed, invocation=invocation)


def reattest_materializer_volume(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
    fingerprint: MaterializerVolumeFingerprint,
) -> None:
    observed = _docker_inspect_one(
        client,
        config_root,
        "volume",
        invocation.volume_name,
    )
    if (
        validate_materializer_volume_inspect(observed, invocation=invocation)
        != fingerprint
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer volume changed"
        )


def _recover_materializer_container(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
) -> tuple[str, Mapping[str, Any]]:
    observed = _docker_inspect_one(
        client,
        config_root,
        "container",
        invocation.container_name,
    )
    container_id = validate_materializer_container_inspect(
        observed,
        invocation=invocation,
        expected_state="created",
    )
    materializer_container_envelope_sha256(observed)
    return container_id, observed


def create_materializer_container(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
    fingerprint: MaterializerVolumeFingerprint,
) -> tuple[str, Mapping[str, Any]]:
    reattest_materializer_volume(
        client,
        config_root,
        invocation,
        fingerprint,
    )
    require_docker_name_absent(
        client,
        config_root,
        resource="container",
        name=invocation.container_name,
    )
    arguments = build_materializer_create_argv(
        docker_client=client,
        invocation=invocation,
    )
    try:
        result = run_docker_argv(client, config_root, arguments)
        _require_success(result)
        container_id = parse_container_create_output(result.stdout)
        observed = _docker_inspect_one(
            client,
            config_root,
            "container",
            container_id,
        )
        if (
            validate_materializer_container_inspect(
                observed,
                invocation=invocation,
                expected_state="created",
            )
            != container_id
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance materializer creation differs"
            )
        materializer_container_envelope_sha256(observed)
        return container_id, observed
    except LocalStagingAcceptanceError:
        try:
            return _recover_materializer_container(
                client,
                config_root,
                invocation,
            )
        except LocalStagingAcceptanceError:
            raise LocalStagingAcceptanceError(
                "local acceptance materializer creation failed"
            ) from None


def _container_owned_for_cleanup(
    value: Mapping[str, Any],
    *,
    invocation: MaterializerInvocation,
    container_id: str,
    envelope_sha256: str,
) -> bool:
    try:
        return (
            value["Id"] == container_id
            and value["Image"] == FROZEN_IMAGE_ID
            and value["Name"] == f"/{invocation.container_name}"
            and value["Config"]["Labels"]
            == {
                "buffalo.contract": ACCEPTANCE_CONTRACT,
                "buffalo.run": invocation.run_id,
                "buffalo.role": MATERIALIZER_ROLE,
            }
            and materializer_container_envelope_sha256(value) == envelope_sha256
        )
    except (KeyError, TypeError, LocalStagingAcceptanceError):
        return False


def remove_owned_materializer_container(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
    *,
    container_id: str,
    envelope_sha256: str,
) -> None:
    observed = _docker_inspect_one(
        client,
        config_root,
        "container",
        container_id,
    )
    if not _container_owned_for_cleanup(
        observed,
        invocation=invocation,
        container_id=container_id,
        envelope_sha256=envelope_sha256,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer cleanup ownership differs"
        )
    arguments = ["container", "rm"]
    if observed["State"]["Running"] is True:
        arguments.append("--force")
    arguments.append(container_id)
    result = run_docker_command(client, config_root, tuple(arguments))
    _require_success(result, stdout=f"{container_id}\n".encode("ascii"))
    require_docker_name_absent(
        client,
        config_root,
        resource="container",
        name=invocation.container_name,
    )


def run_materializer_phase(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
    fingerprint: MaterializerVolumeFingerprint,
    *,
    timeout_seconds: float,
) -> MaterializerPhaseProof:
    container_id = ""
    envelope_sha256 = ""
    try:
        container_id, created = create_materializer_container(
            client,
            config_root,
            invocation,
            fingerprint,
        )
        envelope_sha256 = materializer_container_envelope_sha256(created)
        result = run_docker_command(
            client,
            config_root,
            ("container", "start", "--attach", container_id),
            timeout_seconds=timeout_seconds,
            stdout_limit=_DOCKER_ATTACH_LIMIT,
            stderr_limit=_DOCKER_ATTACH_LIMIT,
        )
        _require_success(result, stdout=b"", stderr=b"")
        exited = _docker_inspect_one(
            client,
            config_root,
            "container",
            container_id,
        )
        validate_materializer_container_inspect(
            exited,
            invocation=invocation,
            expected_state="exited",
        )
        if materializer_container_envelope_sha256(exited) != envelope_sha256:
            raise LocalStagingAcceptanceError(
                "local acceptance materializer envelope changed"
            )
        reattest_materializer_volume(
            client,
            config_root,
            invocation,
            fingerprint,
        )
        return MaterializerPhaseProof(
            contract=ACCEPTANCE_CONTRACT,
            role=MATERIALIZER_ROLE,
            container_id=container_id,
            immutable_envelope_sha256=envelope_sha256,
            started_at=exited["State"]["StartedAt"],
            finished_at=exited["State"]["FinishedAt"],
        )
    finally:
        if container_id and envelope_sha256:
            remove_owned_materializer_container(
                client,
                config_root,
                invocation,
                container_id=container_id,
                envelope_sha256=envelope_sha256,
            )


def remove_owned_materializer_volume(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
    fingerprint: MaterializerVolumeFingerprint,
) -> None:
    reattest_materializer_volume(
        client,
        config_root,
        invocation,
        fingerprint,
    )
    references = run_docker_command(
        client,
        config_root,
        (
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--filter",
            f"volume={invocation.volume_name}",
            "--format",
            "{{.ID}}\t{{.Names}}",
        ),
    )
    _require_success(references, stdout=b"")
    removed = run_docker_command(
        client,
        config_root,
        ("volume", "rm", invocation.volume_name),
    )
    _require_success(
        removed,
        stdout=f"{invocation.volume_name}\n".encode("ascii"),
    )
    require_docker_name_absent(
        client,
        config_root,
        resource="volume",
        name=invocation.volume_name,
    )


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    if values:
        return 2
    sys.stderr.write("Buffalo LOCAL staging acceptance is not yet executable\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ACCEPTANCE_CONTRACT",
    "BROWSER_WORKER_HIDDEN_MODE",
    "BROWSER_WORKER_PROTOCOL",
    "BrowserCgroupEvents",
    "BrowserContainmentTextEvidence",
    "BrowserWorkerArguments",
    "BrowserWorkerDescriptorExpectation",
    "BrowserWorkerExpectedAttestation",
    "BrowserWorkerLaunch",
    "BrowserWorkerProcessExpectation",
    "BrowserWorkerProcessStat",
    "BrowserWorkerReady",
    "BrowserWorkerRequest",
    "BrowserWorkerResult",
    "FROZEN_IMAGE_ID",
    "FROZEN_IMAGE_ROOTFS_LAYERS",
    "FROZEN_SOURCE_COMMIT",
    "FROZEN_SOURCE_TREE",
    "LocalStagingAcceptanceError",
    "MaterializerIngress",
    "MaterializerInvocation",
    "MaterializerPhaseProof",
    "MaterializerVolumeFingerprint",
    "ObservedBrowserWorker",
    "PinnedBrowserPythonExecutable",
    "PinnedBrowserWorkerRunner",
    "attest_docker_materializer_runtime",
    "build_materializer_create_argv",
    "build_materializer_volume_create_argv",
    "browser_worker_ready_sha256",
    "browser_worker_request_sha256",
    "build_browser_worker_launch",
    "create_materializer_container",
    "create_materializer_volume",
    "decode_browser_worker_frame",
    "encode_browser_worker_frame",
    "materializer_invocation",
    "new_browser_worker_identifiers",
    "observe_browser_worker_process",
    "open_trusted_docker",
    "open_pinned_browser_python_executable",
    "open_pinned_browser_worker_runner",
    "parse_browser_worker_arguments",
    "parse_browser_cgroup_events",
    "parse_browser_cgroup_path",
    "parse_browser_cgroup_processes",
    "parse_browser_cgroup_threads",
    "parse_browser_containment_text_evidence",
    "parse_browser_namespace_pids",
    "read_browser_worker_frame",
    "read_browser_worker_secret",
    "remove_owned_materializer_container",
    "remove_owned_materializer_volume",
    "require_browser_worker_process_live",
    "run_materializer_phase",
    "validate_browser_worker_ready",
    "validate_browser_worker_ready_attestation",
    "validate_browser_worker_request",
    "validate_browser_worker_result",
    "validate_browser_worker_transition",
    "validate_browser_worker_descriptors",
    "validate_materializer_ingress",
    "write_browser_worker_frame",
    "write_browser_worker_secret",
]
