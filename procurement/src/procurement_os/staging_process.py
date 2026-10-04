"""Fixed child entrypoints for the Railway staging process topology."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import secrets
import socket
import stat
import struct
import sys
from typing import TYPE_CHECKING
from urllib.parse import urlparse

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
from .staging_synthetic_readiness import SyntheticReadinessWriter
from .staging_research_validation import StagingResearchValidationError
from .staging_supervisor import SupervisorControlClient
from .staging_uds import PeerCredentials, SocketContract
from .staging_worker_activation import (
    GatewayActivationProtocol,
    GatewayActivationServer,
)
from .staging_worker_transport import UnixWorkerTransport, run_worker

if TYPE_CHECKING:
    from .synthetic_staging_database import SyntheticStagingTarget


class StagingProcessError(ValueError):
    """A fixed child process cannot prove its launch contract."""


class SyntheticCredentialError(StagingProcessError):
    """The synthetic worker's private libpq credential file differs."""


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
    boundary = load_staging_worker_boundary_config(role)
    readiness_name = f"BUFFALO_STAGING_{role.upper()}_READINESS_FD"
    readiness_fd = _canonical_integer(environment, readiness_name, 3)
    generation = _canonical_integer(
        environment, "BUFFALO_STAGING_KEY_GENERATION", 0
    )
    try:
        writer = (
            ResearchReadinessWriter(
                readiness_fd,
                key=boundary.assertion_key,
                generation=generation,
            )
            if role == "research"
            else SyntheticReadinessWriter(
                readiness_fd,
                key=boundary.assertion_key,
                generation=generation,
            )
        )
    except ResearchReadinessError as exc:
        raise StagingProcessError(
            f"{role} readiness writer is unavailable"
        ) from exc

    def server_factory(config: Config) -> Server:
        if role == "research":
            return _ResearchReadinessServer(config, readiness=writer)
        return _SyntheticReadinessServer(
            config,
            readiness=writer,
            environment=environment,
        )

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


class _SyntheticReadinessServer(Server):
    """Emit READY only after Uvicorn and an exact runtime-role DB attestation."""

    def __init__(
        self,
        config: Config,
        *,
        readiness: SyntheticReadinessWriter,
        environment: dict[str, str],
    ) -> None:
        super().__init__(config)
        self._readiness = readiness
        self._environment = dict(environment)

    def _attest_database(self) -> str:
        import psycopg

        from .synthetic_staging_database import (
            attest_runtime_connection,
            target_from_environment,
        )

        target = target_from_environment(self._environment)
        passfile = _validated_synthetic_pgpass(self._environment, target)
        with psycopg.connect(
            self._environment["DATABASE_URL"],
            connect_timeout=5,
            autocommit=False,
            passfile=passfile,
        ) as conn:
            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            conn.execute("SET LOCAL statement_timeout = '10000ms'")
            conn.execute("SET LOCAL lock_timeout = '2000ms'")
            conn.execute("SET LOCAL idle_in_transaction_session_timeout = '15000ms'")
            identity = attest_runtime_connection(conn, target)
            conn.rollback()
        return identity

    async def startup(self, sockets=None) -> None:
        try:
            await super().startup(sockets=sockets)
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
                self._readiness.emit_failure("ACTIVATION")
            return
        try:
            validation_identity = await asyncio.to_thread(self._attest_database)
            if self.should_exit or not self.started:
                if not self._readiness.closed:
                    self._readiness.emit_failure("ACTIVATION")
                return
            self._readiness.emit_ready(validation_identity)
        except asyncio.CancelledError:
            self.should_exit = True
            if not self._readiness.closed:
                try:
                    self._readiness.emit_failure("ACTIVATION")
                except ResearchReadinessError:
                    pass
            raise
        except MemoryError:
            self.should_exit = True
            if not self._readiness.closed:
                try:
                    self._readiness.emit_failure("OOM")
                except ResearchReadinessError:
                    pass
        except BaseException as exc:
            self.should_exit = True
            failure = "ACTIVATION"
            try:
                import psycopg

                from .synthetic_staging_database import (
                    SyntheticStagingDatabaseError,
                )

                if isinstance(exc, psycopg.OperationalError):
                    failure = "DATABASE"
                elif isinstance(
                    exc,
                    (
                        SyntheticCredentialError,
                        SyntheticStagingDatabaseError,
                        psycopg.Error,
                    ),
                ):
                    failure = "INTEGRITY"
            except BaseException:
                failure = "ACTIVATION"
            if not self._readiness.closed:
                try:
                    self._readiness.emit_failure(failure)
                except ResearchReadinessError:
                    pass


def _split_pgpass_record(record: str) -> tuple[str, str, str, str, str]:
    fields: list[str] = []
    value: list[str] = []
    escaped = False
    for character in record:
        if escaped:
            if character not in {":", "\\"}:
                raise SyntheticCredentialError(
                    "synthetic credential record is malformed"
                )
            value.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(value))
            value = []
        else:
            value.append(character)
    if escaped:
        raise SyntheticCredentialError("synthetic credential record is malformed")
    fields.append("".join(value))
    if len(fields) != 5:
        raise SyntheticCredentialError("synthetic credential record is malformed")
    return tuple(fields)  # type: ignore[return-value]


def _validated_synthetic_pgpass(
    environment: dict[str, str],
    target: "SyntheticStagingTarget",
) -> str:
    """Prove one exact runtime credential in a private role-local file."""

    runtime_root = Path(environment.get("BUFFALO_STAGING_RUNTIME_ROOT", ""))
    passfile = Path(environment.get("PGPASSFILE", ""))
    if (
        not runtime_root.is_absolute()
        or not passfile.is_absolute()
        or passfile == runtime_root
        or not passfile.is_relative_to(runtime_root)
        or any(part in {"", ".", ".."} for part in passfile.parts)
    ):
        raise SyntheticCredentialError("synthetic credential path differs")
    uid = os.getuid()
    gid = os.getgid()
    current = passfile.parent
    while True:
        try:
            info = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise SyntheticCredentialError(
                "synthetic credential directory is unavailable"
            ) from exc
        if (
            current.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != uid
            or info.st_gid != gid
        ):
            raise SyntheticCredentialError(
                "synthetic credential directory differs"
            )
        if current == runtime_root:
            break
        current = current.parent
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(passfile, flags)
    except OSError as exc:
        raise SyntheticCredentialError(
            "synthetic credential file is unavailable"
        ) from exc
    try:
        info = os.fstat(descriptor)
        link_info = passfile.stat(follow_symlinks=False)
        if (
            passfile.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != uid
            or info.st_gid != gid
            or info.st_nlink != 1
            or not 0 < info.st_size <= 4_096
            or (info.st_dev, info.st_ino) != (link_info.st_dev, link_info.st_ino)
        ):
            raise SyntheticCredentialError("synthetic credential file differs")
        raw_record = os.read(descriptor, 4_097)
        final_info = os.fstat(descriptor)
        final_link_info = passfile.stat(follow_symlinks=False)
        if (
            len(raw_record) != info.st_size
            or final_info.st_size != info.st_size
            or (final_info.st_dev, final_info.st_ino)
            != (info.st_dev, info.st_ino)
            or (final_link_info.st_dev, final_link_info.st_ino)
            != (info.st_dev, info.st_ino)
        ):
            raise SyntheticCredentialError("synthetic credential file differs")
        try:
            text = raw_record.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SyntheticCredentialError(
                "synthetic credential record is malformed"
            ) from exc
        if not text.endswith("\n") or text.count("\n") != 1 or "\r" in text:
            raise SyntheticCredentialError(
                "synthetic credential record is malformed"
            )
        fields = _split_pgpass_record(text[:-1])
        parsed = urlparse(target.database_url)
        expected = (
            parsed.hostname or "",
            str(parsed.port or 5432),
            parsed.path.removeprefix("/"),
            parsed.username or "",
        )
        if (
            fields[:4] != expected
            or not fields[4]
            or any(value == "*" for value in fields[:4])
        ):
            raise SyntheticCredentialError(
                "synthetic credential record differs"
            )
    finally:
        os.close(descriptor)
    return str(passfile)


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
