"""Fixed child entrypoints for the Railway staging process topology."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import socket
import stat
import struct
import sys

from uvicorn import Config, Server

from .staging_config import load_staging_config
from .staging_composition import load_staging_worker_boundary_config
from .staging_gateway import RegisteredRoutePolicy, StagingGateway
from .staging_management_keys import (
    derive_gateway_activation_key,
    derive_supervisor_control_key,
)
from .staging_process_contract import validated_process_environment
from .staging_research_readiness import (
    ResearchReadinessError,
    ResearchReadinessWriter,
)
from .staging_research_validation import StagingResearchValidationError
from .staging_supervisor import SupervisorControlClient
from .staging_uds import PeerCredentials, SocketContract
from .staging_worker_activation import (
    GatewayActivationProtocol,
    GatewayActivationServer,
)
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
    key_contracts = {
        role: (
            Path(environment[f"BUFFALO_STAGING_{role.upper()}_KEY_FILE"]),
            _canonical_integer(
                environment,
                f"BUFFALO_STAGING_{role.upper()}_KEY_DIRECTORY_UID",
                0,
            ),
            _canonical_integer(
                environment,
                f"BUFFALO_STAGING_{role.upper()}_KEY_DIRECTORY_GID",
                1,
            ),
        )
        for role in ("synthetic", "research")
    }
    if any(
        path.name != f"{role}-0.key"
        for role, (path, _parent_uid, _parent_gid) in key_contracts.items()
    ) or len({path.parent for path, _, _ in key_contracts.values()}) != 2:
        raise StagingProcessError("gateway worker key inventory differs")
    worker_keys = {
        role: _read_private_key(path, parent_uid=parent_uid, parent_gid=parent_gid)
        for role, (path, parent_uid, parent_gid) in key_contracts.items()
    }
    management_path = Path(environment["BUFFALO_STAGING_CONTROL_KEY_FILE"])
    if management_path.name != "control.key" or management_path.parent in {
        path.parent for path, _, _ in key_contracts.values()
    }:
        raise StagingProcessError("gateway management key binding differs")
    management_key = _read_private_key(
        management_path,
        parent_uid=_canonical_integer(
            environment,
            "BUFFALO_STAGING_CONTROL_KEY_DIRECTORY_UID",
            0,
        ),
        parent_gid=_canonical_integer(
            environment,
            "BUFFALO_STAGING_CONTROL_KEY_DIRECTORY_GID",
            1,
        ),
    )
    if any(
        secrets.compare_digest(management_key, worker_key)
        for worker_key in worker_keys.values()
    ):
        raise StagingProcessError("gateway management key is not independent")
    control_contract = SocketContract(
        path=Path(environment["BUFFALO_STAGING_CONTROL_SOCKET_PATH"]),
        parent_uid=0,
        parent_gid=_canonical_integer(
            environment, "BUFFALO_STAGING_CONTROL_SOCKET_GID", 1
        ),
        socket_uid=0,
        socket_gid=_canonical_integer(
            environment, "BUFFALO_STAGING_CONTROL_SOCKET_GID", 1
        ),
    )
    supervisor_peer = PeerCredentials(
        pid=_canonical_integer(
            environment, "BUFFALO_STAGING_SUPERVISOR_PID", 1
        ),
        uid=0,
        gid=0,
    )
    control_client = SupervisorControlClient(
        contract=control_contract,
        key=derive_supervisor_control_key(management_key),
        expected_supervisor_peer=supervisor_peer,
    )
    activation_fd = _canonical_integer(
        environment, "BUFFALO_STAGING_ACTIVATION_FD", 3
    )
    try:
        activation_channel = socket.socket(fileno=activation_fd)
    except OSError as exc:
        raise StagingProcessError("gateway activation descriptor is unavailable") from exc
    try:
        if _activation_peer_credentials(activation_channel) != supervisor_peer:
            raise StagingProcessError("gateway activation peer differs")
    except BaseException:
        activation_channel.close()
        raise
    server_holder: dict[str, Server] = {}

    def load_worker_key(role: str, generation: int) -> bytes:
        if (
            role not in key_contracts
            or type(generation) is not int
            or not 0 <= generation <= (2**63 - 1)
        ):
            raise StagingProcessError("gateway worker key binding differs")
        initial_path, parent_uid, parent_gid = key_contracts[role]
        path = initial_path.parent / f"{role}-{generation}.key"
        return _read_private_key(
            path,
            parent_uid=parent_uid,
            parent_gid=parent_gid,
        )

    def activation_factory(keyring):
        protocol = GatewayActivationProtocol(
            key=derive_gateway_activation_key(management_key),
            keyring=keyring,
            load_key=load_worker_key,
        )

        def fatal() -> None:
            for role in ("synthetic", "research"):
                try:
                    status = keyring.status(role)
                    keyring.disable(role=role, generation=status.generation)
                except ValueError:
                    pass
            server = server_holder.get("server")
            if server is not None:
                server.should_exit = True

        return GatewayActivationServer(
            channel=activation_channel,
            protocol=protocol,
            fatal_handler=fatal,
        )

    try:
        application = StagingGateway(
            config=config,
            route_policy=RegisteredRoutePolicy(),
            transport=UnixWorkerTransport(endpoints=endpoints),
            worker_keys=worker_keys,
            research_control=control_client,
            activation_service_factory=activation_factory,
        )
        server = Server(
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
        )
        server_holder["server"] = server
        server.run()
    finally:
        activation_channel.close()


def _run_worker(role: str, environment: dict[str, str]) -> None:
    listen_fd = _canonical_integer(environment, "BUFFALO_STAGING_LISTEN_FD", 3)
    socket_gid = _canonical_integer(environment, "BUFFALO_STAGING_SOCKET_GID", 1)
    path = Path(environment["BUFFALO_STAGING_SOCKET_PATH"])
    socket_contract = SocketContract(
        path=path,
        parent_uid=0,
        parent_gid=socket_gid,
        socket_uid=os.getuid(),
        socket_gid=socket_gid,
    )
    if role != "research":
        run_worker(
            worker_role=role,
            listen_fd=listen_fd,
            socket_contract=socket_contract,
        )
        return
    boundary = load_staging_worker_boundary_config("research")
    readiness_fd = _canonical_integer(
        environment, "BUFFALO_STAGING_RESEARCH_READINESS_FD", 3
    )
    generation = _canonical_integer(
        environment, "BUFFALO_STAGING_KEY_GENERATION", 0
    )
    try:
        writer = ResearchReadinessWriter(
            readiness_fd,
            key=boundary.assertion_key,
            generation=generation,
        )
    except ResearchReadinessError as exc:
        raise StagingProcessError("research readiness writer is unavailable") from exc

    def server_factory(config: Config) -> Server:
        return _ResearchReadinessServer(config, readiness=writer)

    try:
        run_worker(
            worker_role=role,
            listen_fd=listen_fd,
            socket_contract=socket_contract,
            server_factory=server_factory,
        )
    except BaseException:
        if not writer.closed:
            try:
                writer.emit_failure("ACTIVATION")
            except ResearchReadinessError:
                pass
        raise
    finally:
        if not writer.closed:
            try:
                writer.close()
            except ResearchReadinessError:
                pass


class _ResearchReadinessServer(Server):
    """Emit the proof only after lifespan and the Uvicorn accept loop start."""

    def __init__(self, config: Config, *, readiness: ResearchReadinessWriter) -> None:
        super().__init__(config)
        self._readiness = readiness

    async def startup(self, sockets=None) -> None:
        try:
            await super().startup(sockets=sockets)
        except SystemExit:
            failure = "ACTIVATION"
            try:
                from .private_research_app import staging_research_startup_failure

                failure = staging_research_startup_failure() or (
                    "SEMANTIC"
                    if getattr(self.lifespan, "should_exit", False)
                    else "ACTIVATION"
                )
            except BaseException:
                failure = "ACTIVATION"
            if not self._readiness.closed:
                try:
                    self._readiness.emit_failure(failure)
                except ResearchReadinessError:
                    pass
            raise
        except MemoryError:
            if not self._readiness.closed:
                try:
                    self._readiness.emit_failure("OOM")
                except ResearchReadinessError:
                    pass
            raise
        except BaseException:
            if not self._readiness.closed:
                try:
                    self._readiness.emit_failure("ACTIVATION")
                except ResearchReadinessError:
                    pass
            raise
        if self.should_exit or not self.started:
            if not self._readiness.closed:
                self._readiness.emit_failure("SEMANTIC")
            return
        try:
            from .private_research_app import staging_research_validation_identity

            validation_identity = staging_research_validation_identity()
            self._readiness.emit_ready(validation_identity)
        except MemoryError:
            self.should_exit = True
            if not self._readiness.closed:
                try:
                    self._readiness.emit_failure("OOM")
                except ResearchReadinessError:
                    pass
            return
        except StagingResearchValidationError:
            self.should_exit = True
            if not self._readiness.closed:
                try:
                    self._readiness.emit_failure("INTEGRITY")
                except ResearchReadinessError:
                    pass
            return
        except ResearchReadinessError:
            self.should_exit = True
            if not self._readiness.closed:
                try:
                    self._readiness.emit_failure("ACTIVATION")
                except ResearchReadinessError:
                    pass
            return


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
    _require_no_symlink_ancestors(path.parent)
    try:
        parent = path.parent.stat(follow_symlinks=False)
        before = path.stat(follow_symlinks=False)
    except OSError:
        raise StagingProcessError("gateway key parent is unavailable") from None
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o710
        or parent.st_uid != parent_uid
        or parent.st_gid != parent_gid
    ):
        raise StagingProcessError("gateway key parent contract differs")
    if (
        path.is_symlink()
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
    ):
        raise StagingProcessError("gateway key contract differs")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise StagingProcessError("gateway key is unavailable") from None
    try:
        info = os.fstat(descriptor)
        value = os.read(descriptor, 33)
        after = path.stat(follow_symlinks=False)
    except OSError:
        raise StagingProcessError("gateway key is unavailable") from None
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.getuid()
        or info.st_gid != os.getgid()
        or info.st_nlink != 1
        or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)
        or (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino)
        or _stable_file_metadata(before) != _stable_file_metadata(after)
        or _stable_file_metadata(info) != _stable_file_metadata(after)
        or len(value) != 32
    ):
        raise StagingProcessError("gateway key contract differs")
    return value


def _stable_file_metadata(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _require_no_symlink_ancestors(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.stat(follow_symlinks=False)
        except OSError:
            raise StagingProcessError("gateway key ancestor is unavailable") from None
        if stat.S_ISLNK(info.st_mode):
            raise StagingProcessError("gateway key ancestor is a symlink")


def _activation_peer_credentials(channel: socket.socket) -> PeerCredentials:
    try:
        raw = channel.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        pid, uid, gid = struct.unpack("3i", raw)
        return PeerCredentials(pid=pid, uid=uid, gid=gid)
    except (AttributeError, OSError, struct.error, ValueError):
        raise StagingProcessError(
            "gateway activation peer is unavailable"
        ) from None


if __name__ == "__main__":
    main()
