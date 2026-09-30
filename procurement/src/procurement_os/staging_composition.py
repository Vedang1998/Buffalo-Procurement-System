"""Mutually exclusive local or Railway-staging worker access composition."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import stat
from typing import Any
from urllib.parse import urlsplit

from .staging_process_contract import (
    WORKER_BOUNDARY_ENVIRONMENT_NAMES,
    StagingProcessContractError,
    validated_process_environment,
)
from .staging_routes import validate_worker_application_routes
from .staging_worker_transport import (
    PeerCredentials,
    StagingInternalAccessMiddleware,
    StagingWorkerBoundaryError,
)


STAGING_WORKER_ROLE_ENV = "BUFFALO_STAGING_WORKER_ROLE"
_KEY_FILE_ENV = "BUFFALO_STAGING_ASSERTION_KEY_FILE"
_ORIGIN_ENV = "BUFFALO_STAGING_EXTERNAL_ORIGIN"
_GATEWAY_PID_ENV = "BUFFALO_STAGING_GATEWAY_PID"
_GATEWAY_UID_ENV = "BUFFALO_STAGING_GATEWAY_UID"
_GATEWAY_GID_ENV = "BUFFALO_STAGING_GATEWAY_GID"


@dataclass(frozen=True)
class StagingWorkerBoundaryConfig:
    worker_role: str
    assertion_key: bytes = field(repr=False)
    external_origin: str
    gateway_peer: PeerCredentials
    key_file: Path


def install_access_boundary(
    app: Any,
    *,
    local_middleware: type,
    expected_worker_role: str,
) -> str:
    """Install exactly one access boundary after the app route table exists."""

    selected = os.getenv(STAGING_WORKER_ROLE_ENV, "")
    if selected == "":
        if any(
            name in os.environ
            for name in WORKER_BOUNDARY_ENVIRONMENT_NAMES - {STAGING_WORKER_ROLE_ENV}
        ):
            raise StagingWorkerBoundaryError(
                "partial staging worker composition is not accepted"
            )
        app.add_middleware(local_middleware)
        return "LOCAL"
    if selected != expected_worker_role:
        raise StagingWorkerBoundaryError("staging worker composition role differs")
    config = load_staging_worker_boundary_config(expected_worker_role)
    validate_worker_application_routes(
        worker_role=expected_worker_role,
        routes=app.routes,
    )
    app.add_middleware(
        StagingInternalAccessMiddleware,
        worker_role=config.worker_role,
        assertion_key=config.assertion_key,
        expected_origin=config.external_origin,
        expected_gateway_peer=config.gateway_peer,
    )
    return "RAILWAY_STAGING_INTERNAL"


def load_staging_worker_boundary_config(
    expected_worker_role: str,
) -> StagingWorkerBoundaryConfig:
    role = os.getenv(STAGING_WORKER_ROLE_ENV, "")
    if expected_worker_role not in {"synthetic", "research"} or role != expected_worker_role:
        raise StagingWorkerBoundaryError("staging worker role differs")
    try:
        environment = validated_process_environment(role=role, environ=os.environ)
    except StagingProcessContractError as exc:
        raise StagingWorkerBoundaryError(str(exc)) from exc
    origin = environment[_ORIGIN_ENV]
    try:
        parsed = urlsplit(origin)
        parsed_port = parsed.port
    except ValueError as exc:
        raise StagingWorkerBoundaryError("staging external origin is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed_port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or origin.endswith("/")
        or origin != f"https://{parsed.hostname}"
    ):
        raise StagingWorkerBoundaryError("staging external origin is invalid")
    key_value = environment[_KEY_FILE_ENV]
    key_file = Path(key_value) if key_value else Path()
    assertion_key = _read_worker_key(
        key_file,
        parent_uid=_canonical_integer(
            environment, "BUFFALO_STAGING_KEY_DIRECTORY_UID", minimum=0
        ),
        parent_gid=_canonical_integer(
            environment, "BUFFALO_STAGING_KEY_DIRECTORY_GID", minimum=1
        ),
    )
    gateway_peer = PeerCredentials(
        pid=_canonical_integer(environment, _GATEWAY_PID_ENV, minimum=1),
        uid=_canonical_integer(environment, _GATEWAY_UID_ENV, minimum=0),
        gid=_canonical_integer(environment, _GATEWAY_GID_ENV, minimum=0),
    )
    return StagingWorkerBoundaryConfig(
        worker_role=role,
        assertion_key=assertion_key,
        external_origin=origin,
        gateway_peer=gateway_peer,
        key_file=key_file,
    )


def _canonical_integer(
    environment: dict[str, str], name: str, *, minimum: int
) -> int:
    value = environment.get(name, "")
    if not value or not value.isdecimal() or (value != "0" and value.startswith("0")):
        raise StagingWorkerBoundaryError(f"{name} is invalid")
    result = int(value)
    if result < minimum:
        raise StagingWorkerBoundaryError(f"{name} is invalid")
    return result


def _read_worker_key(
    path: Path, *, parent_uid: int, parent_gid: int
) -> bytes:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise StagingWorkerBoundaryError("staging worker key path is invalid")
    try:
        parent = path.parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise StagingWorkerBoundaryError("staging worker key parent is unavailable") from exc
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o710
        or parent.st_uid != parent_uid
        or parent.st_gid != parent_gid
    ):
        raise StagingWorkerBoundaryError("staging worker key parent contract differs")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StagingWorkerBoundaryError("staging worker key is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        link_info = path.stat(follow_symlinks=False)
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
            or info.st_gid != os.getgid()
            or info.st_size != 32
            or (info.st_dev, info.st_ino) != (link_info.st_dev, link_info.st_ino)
        ):
            raise StagingWorkerBoundaryError("staging worker key contract differs")
        chunks = bytearray()
        while len(chunks) < 33:
            chunk = os.read(descriptor, 33 - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
        value = bytes(chunks)
        if len(value) != 32:
            raise StagingWorkerBoundaryError("staging worker key length differs")
        return value
    finally:
        os.close(descriptor)
