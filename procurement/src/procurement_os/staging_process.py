"""Fixed child entrypoints for the Railway staging process topology."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

from uvicorn import Config, Server

from .staging_config import load_staging_config
from .staging_gateway import RegisteredRoutePolicy, StagingGateway
from .staging_process_contract import validated_process_environment
from .staging_uds import SocketContract
from .staging_worker_transport import UnixWorkerTransport, run_worker


class StagingProcessError(ValueError):
    """A fixed child process cannot prove its launch contract."""


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    if len(values) != 1 or values[0] not in {"gateway", "synthetic", "research"}:
        raise StagingProcessError("exactly one staging process role is required")
    if os.geteuid() == 0 or os.getegid() == 0:
        raise StagingProcessError("staging child must run unprivileged")
    role = values[0]
    environment = validated_process_environment(role=role, environ=os.environ)
    if role == "gateway":
        _run_gateway(environment)
    else:
        _run_worker(role, environment)
    return 0


def _run_gateway(environment: dict[str, str]) -> None:
    config = load_staging_config(environment)
    endpoints = {
        role: _gateway_socket_contract(environment, role)
        for role in ("synthetic", "research")
    }
    worker_keys = {
        role: _read_private_key(
            Path(environment[f"BUFFALO_STAGING_{role.upper()}_KEY_FILE"]),
            parent_uid=_canonical_integer(
                environment,
                f"BUFFALO_STAGING_{role.upper()}_KEY_DIRECTORY_UID",
                0,
            ),
            parent_gid=_canonical_integer(
                environment,
                f"BUFFALO_STAGING_{role.upper()}_KEY_DIRECTORY_GID",
                1,
            ),
        )
        for role in ("synthetic", "research")
    }
    application = StagingGateway(
        config=config,
        route_policy=RegisteredRoutePolicy(),
        transport=UnixWorkerTransport(endpoints=endpoints),
        worker_keys=worker_keys,
    )
    Server(
        Config(
            application,
            host="0.0.0.0",
            port=config.port,
            proxy_headers=False,
            access_log=False,
            server_header=False,
            date_header=False,
            ws="none",
            lifespan="on",
            workers=1,
        )
    ).run()


def _run_worker(role: str, environment: dict[str, str]) -> None:
    listen_fd = _canonical_integer(environment, "BUFFALO_STAGING_LISTEN_FD", 3)
    socket_gid = _canonical_integer(environment, "BUFFALO_STAGING_SOCKET_GID", 1)
    path = Path(environment["BUFFALO_STAGING_SOCKET_PATH"])
    run_worker(
        worker_role=role,
        listen_fd=listen_fd,
        socket_contract=SocketContract(
            path=path,
            parent_uid=0,
            parent_gid=socket_gid,
            socket_uid=os.getuid(),
            socket_gid=socket_gid,
        ),
    )


def _gateway_socket_contract(
    environment: dict[str, str], role: str
) -> SocketContract:
    prefix = f"BUFFALO_STAGING_{role.upper()}_SOCKET"
    return SocketContract(
        path=Path(environment[f"{prefix}_PATH"]),
        parent_uid=0,
        parent_gid=_canonical_integer(environment, f"{prefix}_GID", 1),
        socket_uid=_canonical_integer(environment, f"{prefix}_UID", 1),
        socket_gid=_canonical_integer(environment, f"{prefix}_GID", 1),
    )


def _canonical_integer(
    environment: dict[str, str], name: str, minimum: int
) -> int:
    value = environment.get(name, "")
    if not value.isdecimal() or (value != "0" and value.startswith("0")):
        raise StagingProcessError("staging integer contract differs")
    result = int(value)
    if result < minimum:
        raise StagingProcessError("staging integer contract differs")
    return result


def _read_private_key(
    path: Path, *, parent_uid: int, parent_gid: int
) -> bytes:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise StagingProcessError("gateway key path is invalid")
    try:
        parent = path.parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise StagingProcessError("gateway key parent is unavailable") from exc
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o710
        or parent.st_uid != parent_uid
        or parent.st_gid != parent_gid
    ):
        raise StagingProcessError("gateway key parent contract differs")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StagingProcessError("gateway key is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        value = os.read(descriptor, 33)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.getuid()
        or info.st_gid != os.getgid()
        or len(value) != 32
    ):
        raise StagingProcessError("gateway key contract differs")
    return value


if __name__ == "__main__":
    main()
