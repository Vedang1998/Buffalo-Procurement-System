#!/usr/bin/env python3
"""Run the bounded LOCAL Railway-staging acceptance composition.

The public orchestration is intentionally assembled from exact, separately
attested operators.  This module imports only the Python standard library at
startup so source, dependency, browser, and private-input trust can be proven
before any repository module or credential is loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Mapping


ACCEPTANCE_CONTRACT = "BUFFALO_LOCAL_STAGING_ACCEPTANCE_V1"
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
    "FROZEN_IMAGE_ID",
    "FROZEN_IMAGE_ROOTFS_LAYERS",
    "FROZEN_SOURCE_COMMIT",
    "FROZEN_SOURCE_TREE",
    "LocalStagingAcceptanceError",
    "MaterializerIngress",
    "MaterializerInvocation",
    "MaterializerPhaseProof",
    "MaterializerVolumeFingerprint",
    "attest_docker_materializer_runtime",
    "build_materializer_create_argv",
    "build_materializer_volume_create_argv",
    "create_materializer_container",
    "create_materializer_volume",
    "materializer_invocation",
    "open_trusted_docker",
    "remove_owned_materializer_container",
    "remove_owned_materializer_volume",
    "run_materializer_phase",
    "validate_materializer_ingress",
]
