"""Root-only composition and lifecycle owner for the Railway staging service.

Ordinary startup is deliberately boring: it validates already-provisioned
inputs, creates only ephemeral runtime state, launches the gateway, proves the
synthetic worker's database readiness, and then supervises the fixed process
graph.  It never migrates, seeds, restores, replays research, or mutates the
accepted persistent research release.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import errno
import grp
import os
from pathlib import Path
import pwd
import secrets
import signal
import socket
import stat
import sys
import threading
from typing import Awaitable, Callable, Mapping, MutableMapping
from urllib.parse import urlparse

from .staging_access import OwnerPasswordAuthenticator, StagingAccessError
from .staging_config import (
    EXPECTED_APP_SERVICE_ID,
    EXPECTED_ENVIRONMENT_ID,
    EXPECTED_POSTGRES_SERVICE_ID,
    EXPECTED_PROJECT_ID,
    load_staging_config,
)
from .staging_management_keys import (
    derive_gateway_activation_key,
    derive_supervisor_control_key,
)
from .staging_process_contract import child_argv
from .staging_research_runtime import (
    SupervisedResearchRuntime,
    build_supervised_research_runtime,
)
from .staging_research_transfer import ACCEPTED_TARGET_WORKSPACE_ID
from .staging_research_validation import (
    RESEARCH_VALIDATION_IDENTITY,
    validate_staging_research_paths,
)
from .staging_supervisor import (
    ComponentIsolation,
    ControlStatus,
    FixedChildSpec,
    StagingChildSupervisor,
    StagingIsolationContract,
    StagingSupervisorError,
    SupervisorControlProtocol,
    SupervisorControlServer,
    WorkerKeyCopyContract,
    WorkerKeyLifecycle,
    bind_private_listener,
    write_private_key_copy,
)
from .staging_uds import PeerCredentials, SocketContract
from .staging_worker_activation import SupervisorActivationClient
from .synthetic_staging_database import (
    EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
    RUNTIME_LOGIN,
    SyntheticStagingDatabaseError,
    target_from_environment,
)


BOOTSTRAP_CONTRACT = "BUFFALO_RAILWAY_STAGING_BOOTSTRAP_V1"
RUNTIME_ROOT = Path("/run/buffalo-staging")
DATABASE_URL_ENV = "BUFFALO_STAGING_SYNTHETIC_DATABASE_URL"
DATABASE_PASSWORD_ENV = "BUFFALO_STAGING_SYNTHETIC_DATABASE_PASSWORD"
REPLICA_COUNT_ENV = "BUFFALO_STAGING_REPLICA_COUNT"


class StagingBootstrapError(RuntimeError):
    """The fixed root composition cannot prove a safe service state."""


@dataclass(frozen=True)
class ProcessAccount:
    name: str
    uid: int
    gid: int


SUPERVISOR_ACCOUNT = ProcessAccount("root", 0, 0)
GATEWAY_ACCOUNT = ProcessAccount("buffalo-gateway", 1101, 1201)
SYNTHETIC_ACCOUNT = ProcessAccount("buffalo-synthetic", 1102, 1202)
RESEARCH_ACCOUNT = ProcessAccount("buffalo-research", 1103, 1203)
PROCESS_ACCOUNTS = (
    SUPERVISOR_ACCOUNT,
    GATEWAY_ACCOUNT,
    SYNTHETIC_ACCOUNT,
    RESEARCH_ACCOUNT,
)

SYNTHETIC_SOCKET_GROUP = 2301
RESEARCH_SOCKET_GROUP = 2302
CONTROL_SOCKET_GROUP = 2303
SOCKET_GROUPS = {
    "buffalo-synthetic-socket": (
        SYNTHETIC_SOCKET_GROUP,
        frozenset({GATEWAY_ACCOUNT.name, SYNTHETIC_ACCOUNT.name}),
    ),
    "buffalo-research-socket": (
        RESEARCH_SOCKET_GROUP,
        frozenset({GATEWAY_ACCOUNT.name, RESEARCH_ACCOUNT.name}),
    ),
    "buffalo-control-socket": (
        CONTROL_SOCKET_GROUP,
        frozenset({GATEWAY_ACCOUNT.name}),
    ),
}

_REQUIRED_INPUT_NAMES = frozenset(
    {
        "BUFFALO_RUNTIME_MODE",
        "BUFFALO_STAGING_ENABLED",
        "BUFFALO_STAGING_EXPECTED_COMMIT",
        "BUFFALO_STAGING_EXTERNAL_HOST",
        "BUFFALO_STAGING_OWNER_VERIFIER",
        "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST",
        "BUFFALO_STAGING_POSTGRES_SERVICE_ID",
        "BUFFALO_STAGING_VOLUME_ROOT",
        DATABASE_URL_ENV,
        DATABASE_PASSWORD_ENV,
        REPLICA_COUNT_ENV,
        "PORT",
        "RAILWAY_ENVIRONMENT_ID",
        "RAILWAY_GIT_COMMIT_SHA",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_REPLICA_ID",
        "RAILWAY_SERVICE_ID",
    }
)
_LOCAL_INPUT_NAMES = frozenset(
    {"BUFFALO_STAGING_LOCAL_ACCEPTANCE", "BUFFALO_STAGING_OWNED_LOCAL_PORT"}
)
_FORBIDDEN_ROOT_NAMES = frozenset(
    {
        "DATABASE_URL",
        "DATABASE_PUBLIC_URL",
        "PGDATABASE",
        "PGHOST",
        "PGPASSFILE",
        "PGPASSWORD",
        "PGPORT",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGUSER",
        "PRICE_BOOK_REVIEW_TOKEN",
        "RECONCILIATION_REVIEW_TOKEN",
    }
)
_COMMON_PATH = "/usr/local/bin:/usr/bin:/bin"


@dataclass(frozen=True)
class StagingBootstrapInputs:
    expected_commit: str
    external_host: str
    owner_verifier: str = field(repr=False)
    port: str
    replica_id: str
    volume_root: Path
    database_url: str
    postgres_private_host: str
    local_port: str | None = None

    @property
    def external_origin(self) -> str:
        return f"https://{self.external_host}"


@dataclass(frozen=True)
class StagingBootstrapLayout:
    runtime_root: Path
    supervisor_root: Path
    gateway_root: Path
    synthetic_root: Path
    research_root: Path
    shared_socket_root: Path
    synthetic_socket_parent: Path
    research_socket_parent: Path
    control_socket_parent: Path
    synthetic_socket: Path
    research_socket: Path
    control_socket: Path
    shared_key_root: Path
    gateway_synthetic_keys: Path
    gateway_research_keys: Path
    gateway_control_keys: Path
    synthetic_keys: Path
    research_keys: Path
    control_key: Path
    pgpass: Path
    validation_parent: Path
    volume_root: Path
    synthetic_storage: Path
    research_release: Path
    research_payload: Path
    research_manifest: Path
    research_git_dir: Path
    research_workspace: Path
    transfer_root: Path

    @classmethod
    def build(
        cls,
        *,
        volume_root: Path,
        runtime_root: Path = RUNTIME_ROOT,
    ) -> "StagingBootstrapLayout":
        sockets = runtime_root / "sockets"
        keys = runtime_root / "keys"
        release = volume_root / "research-release"
        payload = release / "payload"
        return cls(
            runtime_root=runtime_root,
            supervisor_root=runtime_root / "supervisor",
            gateway_root=runtime_root / "gateway",
            synthetic_root=runtime_root / "synthetic",
            research_root=runtime_root / "research",
            shared_socket_root=sockets,
            synthetic_socket_parent=sockets / "synthetic",
            research_socket_parent=sockets / "research",
            control_socket_parent=sockets / "control",
            synthetic_socket=sockets / "synthetic" / "worker.sock",
            research_socket=sockets / "research" / "worker.sock",
            control_socket=sockets / "control" / "control.sock",
            shared_key_root=keys,
            gateway_synthetic_keys=keys / "gateway-synthetic",
            gateway_research_keys=keys / "gateway-research",
            gateway_control_keys=keys / "gateway-control",
            synthetic_keys=keys / "synthetic",
            research_keys=keys / "research",
            control_key=keys / "gateway-control" / "control.key",
            pgpass=runtime_root / "synthetic" / "private" / "pgpass",
            validation_parent=runtime_root
            / "supervisor"
            / "research-validation",
            volume_root=volume_root,
            synthetic_storage=volume_root / "synthetic",
            research_release=release,
            research_payload=payload,
            research_manifest=release / "deployment-inventory.json",
            research_git_dir=release / "source.git",
            research_workspace=payload
            / "private-research"
            / "workspaces"
            / ACCEPTED_TARGET_WORKSPACE_ID,
            transfer_root=volume_root / "transfer",
        )


@dataclass(frozen=True)
class _OwnedNode:
    path: Path
    device: int
    inode: int
    kind: str
    mode: int | None
    uid: int | None
    gid: int | None
    size: int | None = None


@dataclass
class BootstrapResources:
    """Exact inodes created by this boot and therefore safe to remove."""

    created_directories: list[_OwnedNode] = field(default_factory=list)
    created_files: list[_OwnedNode] = field(default_factory=list)
    created_sockets: list[_OwnedNode] = field(default_factory=list)
    listeners: list[socket.socket] = field(default_factory=list, repr=False)
    activation_client: SupervisorActivationClient | None = field(
        default=None, repr=False
    )
    gateway_activation_endpoint: socket.socket | None = field(
        default=None, repr=False
    )
    reserved_gateway_fd: int | None = field(default=None, repr=False)
    _cleaned: bool = False

    def reserve_closed_gateway_descriptor(self) -> None:
        endpoint = self.gateway_activation_endpoint
        if endpoint is None:
            raise StagingBootstrapError("gateway activation endpoint is unavailable")
        descriptor = endpoint.fileno()
        if descriptor < 3:
            raise StagingBootstrapError("gateway activation descriptor differs")
        endpoint.close()
        self.gateway_activation_endpoint = None
        temporary = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
        try:
            if temporary != descriptor:
                os.dup2(temporary, descriptor, inheritable=False)
                os.close(temporary)
            self.reserved_gateway_fd = descriptor
        except BaseException:
            if temporary != descriptor:
                try:
                    os.close(temporary)
                except OSError:
                    pass
            raise

    def cleanup(self) -> None:
        if self._cleaned:
            return
        failures: list[BaseException] = []
        if self.activation_client is not None:
            try:
                self.activation_client.close()
            except BaseException as exc:
                failures.append(exc)
            else:
                self.activation_client = None
        if self.gateway_activation_endpoint is not None:
            try:
                self.gateway_activation_endpoint.close()
            except BaseException as exc:
                failures.append(exc)
            else:
                self.gateway_activation_endpoint = None
        if self.reserved_gateway_fd is not None:
            try:
                os.close(self.reserved_gateway_fd)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    failures.append(exc)
                else:
                    self.reserved_gateway_fd = None
            else:
                self.reserved_gateway_fd = None
        remaining_listeners: list[socket.socket] = []
        for listener in reversed(self.listeners):
            try:
                listener.close()
            except BaseException as exc:
                failures.append(exc)
                remaining_listeners.append(listener)
        self.listeners = list(reversed(remaining_listeners))
        remaining_sockets: list[_OwnedNode] = []
        for node in reversed(self.created_sockets):
            if self.listeners:
                remaining_sockets.append(node)
                continue
            try:
                _unlink_owned_node(node)
            except BaseException as exc:
                failures.append(exc)
                remaining_sockets.append(node)
        self.created_sockets = list(reversed(remaining_sockets))
        remaining_files: list[_OwnedNode] = []
        for node in reversed(self.created_files):
            try:
                _unlink_owned_node(node)
            except BaseException as exc:
                failures.append(exc)
                remaining_files.append(node)
        self.created_files = list(reversed(remaining_files))
        remaining_directories: list[_OwnedNode] = []
        for node in reversed(self.created_directories):
            try:
                _rmdir_owned_node(node)
            except BaseException as exc:
                failures.append(exc)
                remaining_directories.append(node)
        self.created_directories = list(reversed(remaining_directories))
        self._cleaned = not failures
        if failures:
            raise StagingBootstrapError(
                "one or more bootstrap resources could not be cleaned"
            ) from failures[0]


@dataclass(frozen=True)
class BootstrapChildContracts:
    isolation: StagingIsolationContract
    gateway_spec: FixedChildSpec
    synthetic_spec: FixedChildSpec
    research_spec: FixedChildSpec
    socket_contracts: Mapping[str, SocketContract]


@dataclass
class StagingBootstrapComposition:
    inputs: StagingBootstrapInputs
    layout: StagingBootstrapLayout
    resources: BootstrapResources = field(repr=False)
    contracts: BootstrapChildContracts
    supervisor: StagingChildSupervisor = field(repr=False)
    management_key: bytes = field(repr=False)
    control_listener: socket.socket = field(repr=False)


def load_bootstrap_inputs(
    environment: Mapping[str, str],
) -> tuple[StagingBootstrapInputs, str]:
    """Select only reviewed root inputs and return the password separately."""

    names = {str(name) for name in environment}
    if not _REQUIRED_INPUT_NAMES.issubset(names):
        raise StagingBootstrapError("staging bootstrap input is incomplete")
    local = names & _LOCAL_INPUT_NAMES
    if local and local != _LOCAL_INPUT_NAMES:
        raise StagingBootstrapError("local staging authority is incomplete")
    if any(
        _root_entry_carries_unreviewed_authority(name, str(environment[name]))
        for name in names - _REQUIRED_INPUT_NAMES - _LOCAL_INPUT_NAMES
        if environment.get(name)
    ):
        raise StagingBootstrapError("production or ambient authority is present")
    selected: dict[str, str] = {}
    for name in sorted(_REQUIRED_INPUT_NAMES | local):
        value = environment.get(name)
        if not isinstance(value, str) or not value or "\x00" in value:
            raise StagingBootstrapError("staging bootstrap input is invalid")
        if name != DATABASE_PASSWORD_ENV and value != value.strip():
            raise StagingBootstrapError("staging bootstrap input is not canonical")
        selected[name] = value
    if (
        selected["BUFFALO_STAGING_ENABLED"] != "1"
        or selected["BUFFALO_RUNTIME_MODE"] != "SYNTHETIC_DEMO"
        or selected[REPLICA_COUNT_ENV] != "1"
        or selected["RAILWAY_GIT_COMMIT_SHA"]
        != selected["BUFFALO_STAGING_EXPECTED_COMMIT"]
        or len(selected["BUFFALO_STAGING_EXPECTED_COMMIT"]) != 40
        or any(
            character not in "0123456789abcdef"
            for character in selected["BUFFALO_STAGING_EXPECTED_COMMIT"]
        )
        or selected["RAILWAY_PROJECT_ID"] != EXPECTED_PROJECT_ID
        or selected["RAILWAY_ENVIRONMENT_ID"] != EXPECTED_ENVIRONMENT_ID
        or selected["RAILWAY_SERVICE_ID"] != EXPECTED_APP_SERVICE_ID
        or selected["BUFFALO_STAGING_POSTGRES_SERVICE_ID"]
        != EXPECTED_POSTGRES_SERVICE_ID
    ):
        raise StagingBootstrapError("staging bootstrap scope differs")
    password = selected.pop(DATABASE_PASSWORD_ENV)
    if len(password.encode("utf-8")) > 2_048 or any(
        character in password for character in ("\x00", "\r", "\n")
    ):
        raise StagingBootstrapError("synthetic runtime credential is invalid")
    try:
        OwnerPasswordAuthenticator(selected["BUFFALO_STAGING_OWNER_VERIFIER"])
    except StagingAccessError as exc:
        raise StagingBootstrapError("staging owner verifier is invalid") from exc
    volume_root = Path(selected["BUFFALO_STAGING_VOLUME_ROOT"])
    if (
        not volume_root.is_absolute()
        or volume_root == Path("/")
        or any(part in {"", ".", ".."} for part in volume_root.parts)
    ):
        raise StagingBootstrapError("staging volume root differs")
    inputs = StagingBootstrapInputs(
        expected_commit=selected["BUFFALO_STAGING_EXPECTED_COMMIT"],
        external_host=selected["BUFFALO_STAGING_EXTERNAL_HOST"],
        owner_verifier=selected["BUFFALO_STAGING_OWNER_VERIFIER"],
        port=selected["PORT"],
        replica_id=selected["RAILWAY_REPLICA_ID"],
        volume_root=volume_root,
        database_url=selected[DATABASE_URL_ENV],
        postgres_private_host=selected[
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST"
        ],
        local_port=selected.get("BUFFALO_STAGING_OWNED_LOCAL_PORT"),
    )
    # The exact child environment is constructed below, but reject a malformed
    # destination before touching the filesystem.
    target_environment = {
        "DATABASE_URL": inputs.database_url,
        "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": inputs.postgres_private_host,
        "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
        "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
        "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
        "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
    }
    if inputs.local_port is not None:
        target_environment.update(
            {
                "BUFFALO_STAGING_LOCAL_ACCEPTANCE": selected[
                    "BUFFALO_STAGING_LOCAL_ACCEPTANCE"
                ],
                "BUFFALO_STAGING_OWNED_LOCAL_PORT": inputs.local_port,
            }
        )
    try:
        target_from_environment(target_environment)
    except SyntheticStagingDatabaseError as exc:
        raise StagingBootstrapError("synthetic database target differs") from exc
    return inputs, password


def build_child_contracts(
    *,
    inputs: StagingBootstrapInputs,
    layout: StagingBootstrapLayout,
    python_executable: str,
    activation_fd: int,
    synthetic_listener_fd: int,
    research_listener_fd: int,
    supervisor_pid: int,
) -> BootstrapChildContracts:
    """Build the exact child inventory without reading ambient environment."""

    descriptors = (activation_fd, synthetic_listener_fd, research_listener_fd)
    if (
        not Path(python_executable).is_absolute()
        or type(supervisor_pid) is not int
        or supervisor_pid < 1
        or supervisor_pid != os.getpid()
        or any(type(value) is not int or value < 3 for value in descriptors)
        or len(set(descriptors)) != 3
    ):
        raise StagingBootstrapError("staging child descriptor contract differs")
    sockets: dict[str, SocketContract] = {
        "synthetic": SocketContract(
            path=layout.synthetic_socket,
            parent_uid=0,
            parent_gid=SYNTHETIC_SOCKET_GROUP,
            socket_uid=SYNTHETIC_ACCOUNT.uid,
            socket_gid=SYNTHETIC_SOCKET_GROUP,
        ),
        "research": SocketContract(
            path=layout.research_socket,
            parent_uid=0,
            parent_gid=RESEARCH_SOCKET_GROUP,
            socket_uid=RESEARCH_ACCOUNT.uid,
            socket_gid=RESEARCH_SOCKET_GROUP,
        ),
        "control": SocketContract(
            path=layout.control_socket,
            parent_uid=0,
            parent_gid=CONTROL_SOCKET_GROUP,
            socket_uid=0,
            socket_gid=CONTROL_SOCKET_GROUP,
        ),
    }
    common = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": _COMMON_PATH,
        "PYTHONUNBUFFERED": "1",
        "TZ": "UTC",
    }
    gateway_environment = {
        **common,
        "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
        "BUFFALO_STAGING_ACTIVATION_FD": str(activation_fd),
        "BUFFALO_STAGING_CONTROL_KEY_DIRECTORY_GID": str(GATEWAY_ACCOUNT.gid),
        "BUFFALO_STAGING_CONTROL_KEY_DIRECTORY_UID": "0",
        "BUFFALO_STAGING_CONTROL_KEY_FILE": str(layout.control_key),
        "BUFFALO_STAGING_CONTROL_SOCKET_GID": str(CONTROL_SOCKET_GROUP),
        "BUFFALO_STAGING_CONTROL_SOCKET_PATH": str(layout.control_socket),
        "BUFFALO_STAGING_ENABLED": "1",
        "BUFFALO_STAGING_EXPECTED_COMMIT": inputs.expected_commit,
        "BUFFALO_STAGING_EXTERNAL_HOST": inputs.external_host,
        "BUFFALO_STAGING_OWNER_VERIFIER": inputs.owner_verifier,
        "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
        "BUFFALO_STAGING_RESEARCH_KEY_FILE": str(
            layout.gateway_research_keys / "research-0.key"
        ),
        "BUFFALO_STAGING_RESEARCH_KEY_DIRECTORY_GID": str(GATEWAY_ACCOUNT.gid),
        "BUFFALO_STAGING_RESEARCH_KEY_DIRECTORY_UID": "0",
        "BUFFALO_STAGING_RESEARCH_SOCKET_GID": str(RESEARCH_SOCKET_GROUP),
        "BUFFALO_STAGING_RESEARCH_SOCKET_PATH": str(layout.research_socket),
        "BUFFALO_STAGING_RESEARCH_SOCKET_UID": str(RESEARCH_ACCOUNT.uid),
        "BUFFALO_STAGING_RUNTIME_ROOT": str(layout.gateway_root),
        "BUFFALO_STAGING_SYNTHETIC_KEY_FILE": str(
            layout.gateway_synthetic_keys / "synthetic-0.key"
        ),
        "BUFFALO_STAGING_SYNTHETIC_KEY_DIRECTORY_GID": str(GATEWAY_ACCOUNT.gid),
        "BUFFALO_STAGING_SYNTHETIC_KEY_DIRECTORY_UID": "0",
        "BUFFALO_STAGING_SYNTHETIC_SOCKET_GID": str(SYNTHETIC_SOCKET_GROUP),
        "BUFFALO_STAGING_SYNTHETIC_SOCKET_PATH": str(layout.synthetic_socket),
        "BUFFALO_STAGING_SYNTHETIC_SOCKET_UID": str(SYNTHETIC_ACCOUNT.uid),
        "BUFFALO_STAGING_SUPERVISOR_PID": str(supervisor_pid),
        "BUFFALO_STAGING_VOLUME_ROOT": str(layout.volume_root),
        "HOME": str(layout.gateway_root),
        "PORT": inputs.port,
        "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
        "RAILWAY_GIT_COMMIT_SHA": inputs.expected_commit,
        "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
        "RAILWAY_REPLICA_ID": inputs.replica_id,
        "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
        "TMPDIR": str(layout.gateway_root / "tmp"),
    }
    worker_common = {
        **common,
        "BUFFALO_STAGING_EXTERNAL_ORIGIN": inputs.external_origin,
        "BUFFALO_STAGING_GATEWAY_GID": str(GATEWAY_ACCOUNT.gid),
        "BUFFALO_STAGING_GATEWAY_PID": "1",
        "BUFFALO_STAGING_GATEWAY_UID": str(GATEWAY_ACCOUNT.uid),
        "BUFFALO_STAGING_KEY_GENERATION": "0",
    }
    synthetic_environment = {
        **worker_common,
        "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
        "BUFFALO_ENABLE_SYNTHETIC_DEVELOPMENT_FORECAST": "1",
        "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
        "BUFFALO_ENABLE_SYNTHETIC_PRICE_REPLACEMENT": "1",
        "BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1",
        "BUFFALO_STAGING_ASSERTION_KEY_FILE": str(
            layout.synthetic_keys / "synthetic-0.key"
        ),
        "BUFFALO_STAGING_KEY_DIRECTORY_GID": str(SYNTHETIC_ACCOUNT.gid),
        "BUFFALO_STAGING_KEY_DIRECTORY_UID": "0",
        "BUFFALO_STAGING_LISTEN_FD": str(synthetic_listener_fd),
        "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": inputs.postgres_private_host,
        "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
        "BUFFALO_STAGING_RUNTIME_ROOT": str(layout.synthetic_root),
        "BUFFALO_STAGING_SOCKET_GID": str(SYNTHETIC_SOCKET_GROUP),
        "BUFFALO_STAGING_SOCKET_PATH": str(layout.synthetic_socket),
        "BUFFALO_STAGING_WORKER_ROLE": "synthetic",
        "DATABASE_URL": inputs.database_url,
        "HOME": str(layout.synthetic_root),
        "PGPASSFILE": str(layout.pgpass),
        "PROCUREMENT_STORAGE_ROOT": str(layout.synthetic_storage),
        "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
        "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
        "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
        "TMPDIR": str(layout.synthetic_root / "tmp"),
    }
    if inputs.local_port is not None:
        synthetic_environment.update(
            {
                "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
                "BUFFALO_STAGING_OWNED_LOCAL_PORT": inputs.local_port,
            }
        )
    research_environment = {
        **worker_common,
        "BUFFALO_PRIVATE_RESEARCH_WORKSPACE": str(layout.research_workspace),
        "BUFFALO_RESEARCH_GIT_DIR": str(layout.research_git_dir),
        "BUFFALO_RESEARCH_MANIFEST": str(layout.research_manifest),
        "BUFFALO_RESEARCH_ROOT": str(layout.research_payload),
        "BUFFALO_STAGING_ASSERTION_KEY_FILE": str(
            layout.research_keys / "research-0.key"
        ),
        "BUFFALO_STAGING_KEY_DIRECTORY_GID": str(RESEARCH_ACCOUNT.gid),
        "BUFFALO_STAGING_KEY_DIRECTORY_UID": "0",
        "BUFFALO_STAGING_LISTEN_FD": str(research_listener_fd),
        "BUFFALO_STAGING_RUNTIME_ROOT": str(layout.research_root),
        "BUFFALO_STAGING_SOCKET_GID": str(RESEARCH_SOCKET_GROUP),
        "BUFFALO_STAGING_SOCKET_PATH": str(layout.research_socket),
        "BUFFALO_STAGING_WORKER_ROLE": "research",
        "HOME": str(layout.research_root),
        "TMPDIR": str(layout.research_root / "tmp"),
    }
    definitions = (
        (
            GATEWAY_ACCOUNT,
            tuple(sorted(SOCKET_GROUPS[name][0] for name in SOCKET_GROUPS)),
            (activation_fd,),
            gateway_environment,
        ),
        (
            SYNTHETIC_ACCOUNT,
            (SYNTHETIC_SOCKET_GROUP,),
            (synthetic_listener_fd,),
            synthetic_environment,
        ),
        (
            RESEARCH_ACCOUNT,
            (RESEARCH_SOCKET_GROUP,),
            (research_listener_fd,),
            research_environment,
        ),
    )
    specs: dict[str, FixedChildSpec] = {}
    components = [
        ComponentIsolation(
            name="supervisor",
            uid=0,
            gid=0,
            extra_groups=(),
            runtime_root=layout.supervisor_root,
        )
    ]
    roots = {
        "gateway": layout.gateway_root,
        "synthetic": layout.synthetic_root,
        "research": layout.research_root,
    }
    for account, groups, pass_fds, environment in definitions:
        role = account.name.removeprefix("buffalo-")
        argv = child_argv(python_executable=python_executable, role=role)
        spec = FixedChildSpec(
            name=role,
            argv=argv,
            environment=tuple(sorted(environment.items())),
            uid=account.uid,
            gid=account.gid,
            extra_groups=groups,
            pass_fds=pass_fds,
        )
        specs[role] = spec
        components.append(
            ComponentIsolation(
                name=role,
                uid=account.uid,
                gid=account.gid,
                extra_groups=groups,
                runtime_root=roots[role],
                child_argv=argv,
                child_environment_names=frozenset(environment),
                child_pass_fds=pass_fds,
            )
        )
    isolation = StagingIsolationContract(
        components=tuple(components),
        synthetic_socket_group=SYNTHETIC_SOCKET_GROUP,
        research_socket_group=RESEARCH_SOCKET_GROUP,
        control_socket_group=CONTROL_SOCKET_GROUP,
        shared_socket_root=layout.shared_socket_root,
        shared_socket_root_uid=0,
        shared_socket_root_gid=0,
        shared_key_root=layout.shared_key_root,
        shared_key_root_uid=0,
        shared_key_root_gid=0,
        socket_contracts=tuple(sockets.items()),
    )
    try:
        isolation.validate_children(
            (specs["gateway"], specs["synthetic"], specs["research"])
        )
        load_staging_config(dict(specs["gateway"].environment))
        target_from_environment(dict(specs["synthetic"].environment))
    except (ValueError, SyntheticStagingDatabaseError) as exc:
        raise StagingBootstrapError("staging child contract differs") from exc
    return BootstrapChildContracts(
        isolation=isolation,
        gateway_spec=specs["gateway"],
        synthetic_spec=specs["synthetic"],
        research_spec=specs["research"],
        socket_contracts=sockets,
    )


def prepare_bootstrap(
    *,
    inputs: StagingBootstrapInputs,
    database_password: str,
    python_executable: str = sys.executable,
    runtime_root: Path = RUNTIME_ROOT,
) -> StagingBootstrapComposition:
    """Create exact ephemeral state and the unstarted fixed composition."""

    _validate_root_identity_and_accounts(runtime_root=runtime_root)
    layout = StagingBootstrapLayout.build(
        volume_root=inputs.volume_root,
        runtime_root=runtime_root,
    )
    _validate_layout_root_separation(layout)
    resources = BootstrapResources()
    try:
        _validate_volume_mount(
            layout.volume_root,
            local_acceptance=inputs.local_port is not None,
        )
        _prepare_layout(layout, resources=resources)
        _write_pgpass(
            layout.pgpass,
            database_url=inputs.database_url,
            password=database_password,
            uid=SYNTHETIC_ACCOUNT.uid,
            gid=SYNTHETIC_ACCOUNT.gid,
            resources=resources,
        )
        management_key = secrets.token_bytes(32)
        if len(management_key) != 32:
            raise StagingBootstrapError("management key generator differs")
        _write_management_key(
            layout.control_key,
            key=management_key,
            uid=GATEWAY_ACCOUNT.uid,
            gid=GATEWAY_ACCOUNT.gid,
            parent_uid=0,
            parent_gid=GATEWAY_ACCOUNT.gid,
            resources=resources,
        )
        sockets = _socket_contracts_for_layout(layout)
        listeners = _bind_bootstrap_listeners(sockets, resources=resources)
        supervisor_channel, gateway_channel = socket.socketpair(
            socket.AF_UNIX, socket.SOCK_STREAM
        )
        resources.gateway_activation_endpoint = gateway_channel
        activation_client = SupervisorActivationClient(
            channel=supervisor_channel,
            key=derive_gateway_activation_key(management_key),
        )
        resources.activation_client = activation_client
        contracts = build_child_contracts(
            inputs=inputs,
            layout=layout,
            python_executable=python_executable,
            activation_fd=gateway_channel.fileno(),
            synthetic_listener_fd=listeners["synthetic"].fileno(),
            research_listener_fd=listeners["research"].fileno(),
            supervisor_pid=os.getpid(),
        )
        key_lifecycle = WorkerKeyLifecycle(
            tuple(
                WorkerKeyCopyContract(
                    role=role,
                    gateway_directory=(
                        layout.gateway_synthetic_keys
                        if role == "synthetic"
                        else layout.gateway_research_keys
                    ),
                    worker_directory=(
                        layout.synthetic_keys
                        if role == "synthetic"
                        else layout.research_keys
                    ),
                    gateway_uid=GATEWAY_ACCOUNT.uid,
                    gateway_gid=GATEWAY_ACCOUNT.gid,
                    worker_uid=(
                        SYNTHETIC_ACCOUNT.uid
                        if role == "synthetic"
                        else RESEARCH_ACCOUNT.uid
                    ),
                    worker_gid=(
                        SYNTHETIC_ACCOUNT.gid
                        if role == "synthetic"
                        else RESEARCH_ACCOUNT.gid
                    ),
                    gateway_parent_uid=0,
                    gateway_parent_gid=GATEWAY_ACCOUNT.gid,
                    worker_parent_uid=0,
                    worker_parent_gid=(
                        SYNTHETIC_ACCOUNT.gid
                        if role == "synthetic"
                        else RESEARCH_ACCOUNT.gid
                    ),
                )
                for role in ("synthetic", "research")
            )
        )

        def prepare_key(staged, _gateway) -> None:
            activation_client.prepare(
                role=staged.role,
                generation=staged.generation,
                worker_key=staged.key,
            )

        def commit_key(staged, _gateway, _worker) -> None:
            activation_client.commit(
                role=staged.role,
                generation=staged.generation,
            )

        def disable_key(staged) -> None:
            activation_client.disable(
                role=staged.role,
                generation=staged.generation,
            )

        supervisor = StagingChildSupervisor(
            isolation=contracts.isolation,
            gateway_spec=contracts.gateway_spec,
            key_lifecycle=key_lifecycle,
            worker_specs=(contracts.synthetic_spec, contracts.research_spec),
            worker_key_preparer=prepare_key,
            worker_key_committer=commit_key,
            worker_key_disabler=disable_key,
            research_validation_identity=RESEARCH_VALIDATION_IDENTITY,
            synthetic_validation_identity=EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
        )
        return StagingBootstrapComposition(
            inputs=inputs,
            layout=layout,
            resources=resources,
            contracts=contracts,
            supervisor=supervisor,
            management_key=management_key,
            control_listener=listeners["control"],
        )
    except BaseException as primary:
        try:
            resources.cleanup()
        except BaseException:
            raise StagingBootstrapError(
                "staging preparation and cleanup failed"
            ) from primary
        raise


class _ResearchHooksGate:
    """Prevent a public request from starting research before DB readiness."""

    def __init__(self, hooks) -> None:
        self.hooks = hooks
        self._open = False
        self._lock = threading.Lock()

    def open(self) -> None:
        with self._lock:
            self._open = True

    def start(self) -> ControlStatus:
        with self._lock:
            if not self._open:
                status = self.hooks.status()
                return ControlStatus("STOPPED", status.generation, 1)
        return self.hooks.start()

    def stop(self) -> ControlStatus:
        return self.hooks.stop()

    def status(self) -> ControlStatus:
        return self.hooks.status()


class _TrackedControlHandler:
    def __init__(self, server: SupervisorControlServer) -> None:
        self.server = server
        self.tasks: set[asyncio.Task[None]] = set()

    async def __call__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self.tasks.add(task)
        try:
            await self.server.handle(reader, writer)
        finally:
            if task is not None:
                self.tasks.discard(task)

    async def drain(self, *, timeout_seconds: float = 3.0) -> None:
        pending = tuple(task for task in self.tasks if not task.done())
        if not pending:
            return
        done, remaining = await asyncio.wait(pending, timeout=timeout_seconds)
        for task in done:
            try:
                task.result()
            except BaseException:
                pass
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)


async def run_composition(
    composition: StagingBootstrapComposition,
    *,
    research_runtime_factory: Callable[..., SupervisedResearchRuntime] = (
        build_supervised_research_runtime
    ),
    monitor_interval_seconds: float = 0.1,
) -> None:
    """Run the fixed topology until a signal or terminal component failure."""

    if (
        not isinstance(composition, StagingBootstrapComposition)
        or not 0.01 <= monitor_interval_seconds <= 1.0
    ):
        raise StagingBootstrapError("staging run configuration differs")
    supervisor = composition.supervisor
    resources = composition.resources
    control_server: asyncio.AbstractServer | None = None
    control_handler: _TrackedControlHandler | None = None
    research: SupervisedResearchRuntime | None = None
    runner_start_attempted = False
    runner_started = False
    stop_requested = threading.Event()
    fatal_requested = threading.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []

    def request_stop(signum: signal.Signals) -> None:
        stop_requested.set()
        supervisor.signal_handler(int(signum))

    def research_fatal() -> None:
        fatal_requested.set()
        supervisor.signal_handler(signal.SIGTERM)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            loop.add_signal_handler(signum, request_stop, signum)
            installed_signals.append(signum)
        # Fork/exec the fixed children only from the main event-loop thread.
        # Blocking readiness and teardown work moves off-loop separately.
        gateway = supervisor.start("gateway")
        resources.reserve_closed_gateway_descriptor()
        activation_client = resources.activation_client
        if activation_client is None:
            raise StagingBootstrapError("gateway activation client is unavailable")
        await asyncio.to_thread(
            activation_client.await_gateway_ready,
            expected_gateway_pid=gateway.identity.pid,
            timeout_seconds=30.0,
            should_abort=stop_requested.is_set,
            peer_is_live=lambda: (
                gateway.process.poll() is None and gateway.identity.is_live()
            ),
        )
        research = research_runtime_factory(
            supervisor=supervisor,
            release_path=composition.layout.research_release,
            validation_parent=composition.layout.validation_parent,
            destination_uid=RESEARCH_ACCOUNT.uid,
            destination_gid=RESEARCH_ACCOUNT.gid,
            supervisor_uid=SUPERVISOR_ACCOUNT.uid,
            supervisor_gid=SUPERVISOR_ACCOUNT.gid,
            fatal_handler=research_fatal,
        )
        hooks = _ResearchHooksGate(research.manager)
        protocol = SupervisorControlProtocol(
            key=derive_supervisor_control_key(composition.management_key),
            expected_peer=PeerCredentials(
                pid=gateway.identity.pid,
                uid=GATEWAY_ACCOUNT.uid,
                gid=GATEWAY_ACCOUNT.gid,
            ),
            hooks=hooks,
        )
        control_handler = _TrackedControlHandler(SupervisorControlServer(protocol))
        control_server = await asyncio.start_unix_server(
            control_handler,
            sock=composition.control_listener,
            start_serving=False,
        )
        runner_start_attempted = True
        research.runner.start()
        runner_started = True
        await control_server.start_serving()
        snapshot = supervisor.start_synthetic(0)
        await asyncio.to_thread(
            supervisor.await_synthetic_ready,
            snapshot.generation,
        )
        hooks.open()
        while True:
            research.runner.check()
            if fatal_requested.is_set():
                raise StagingBootstrapError("research lifecycle failed")
            if stop_requested.is_set():
                break
            crashed = await asyncio.to_thread(supervisor.reap_crashed)
            if crashed:
                raise StagingBootstrapError("mandatory staging child crashed")
            await asyncio.sleep(monitor_interval_seconds)
    finally:
        failures: list[BaseException] = []
        control_clean = True
        if control_server is not None:
            try:
                control_server.close()
            except BaseException as exc:
                failures.append(exc)
                control_clean = False
            try:
                await control_server.wait_closed()
            except BaseException as exc:
                failures.append(exc)
                control_clean = False
        if control_handler is not None:
            try:
                await control_handler.drain()
            except BaseException as exc:
                failures.append(exc)
                control_clean = False
        runner_clean = research is None or not runner_start_attempted
        if research is not None and runner_started:
            try:
                await asyncio.to_thread(research.runner.shutdown)
            except BaseException as exc:
                failures.append(exc)
            else:
                runner_clean = True
        supervisor_clean = False
        try:
            if stop_requested.is_set():
                await asyncio.to_thread(supervisor.process_pending_signal)
            else:
                await asyncio.to_thread(supervisor.shutdown)
        except BaseException as exc:
            failures.append(exc)
        else:
            supervisor_clean = True
        if supervisor_clean and runner_clean and control_clean:
            try:
                resources.cleanup()
            except BaseException as exc:
                failures.append(exc)
        # Keep the idempotent handlers installed throughout teardown so a
        # repeated termination request cannot interrupt owned-child cleanup.
        for signum in installed_signals:
            try:
                loop.remove_signal_handler(signum)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise StagingBootstrapError(
                "staging service cleanup did not complete"
            ) from failures[0]


def run_bootstrap(
    environment: MutableMapping[str, str],
    *,
    python_executable: str = sys.executable,
    runtime_root: Path = RUNTIME_ROOT,
) -> None:
    """Consume the one root credential and run the complete composition."""

    password: str | None = None
    try:
        inputs, password = load_bootstrap_inputs(environment)
    finally:
        # The child processes receive exact constructed dictionaries.  Remove
        # the root-only ingress value before any fork/exec can inherit it.
        environment.pop(DATABASE_PASSWORD_ENV, None)
    composition = prepare_bootstrap(
        inputs=inputs,
        database_password=password,
        python_executable=python_executable,
        runtime_root=runtime_root,
    )
    asyncio.run(run_composition(composition))


def main(
    arguments: list[str] | None = None,
    *,
    environment: MutableMapping[str, str] | None = None,
) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    target = os.environ if environment is None else environment
    if values:
        target.pop(DATABASE_PASSWORD_ENV, None)
        return 2
    try:
        run_bootstrap(target)
    except BaseException:
        # Never serialize a chained exception: it may contain a libpq error or
        # another dependency's rendering of a private path/credential.
        sys.stderr.write("Buffalo staging bootstrap failed\n")
        return 1
    return 0


def _validate_root_identity_and_accounts(*, runtime_root: Path) -> None:
    if (os.geteuid(), os.getegid()) != (0, 0):
        raise StagingBootstrapError("staging bootstrap must run as root")
    homes = {
        GATEWAY_ACCOUNT.name: runtime_root / "gateway",
        SYNTHETIC_ACCOUNT.name: runtime_root / "synthetic",
        RESEARCH_ACCOUNT.name: runtime_root / "research",
    }
    for account in PROCESS_ACCOUNTS[1:]:
        try:
            record = pwd.getpwnam(account.name)
        except KeyError as exc:
            raise StagingBootstrapError("staging process account is absent") from exc
        if (
            (record.pw_uid, record.pw_gid) != (account.uid, account.gid)
            or Path(record.pw_dir) != homes[account.name]
            or record.pw_shell not in {"/sbin/nologin", "/usr/sbin/nologin"}
        ):
            raise StagingBootstrapError("staging process account differs")
    try:
        passwd_inventory = tuple(pwd.getpwall())
        group_inventory = tuple(grp.getgrall())
    except OSError as exc:
        raise StagingBootstrapError("staging identity inventory is unavailable") from exc
    expected_users = {account.uid: account.name for account in PROCESS_ACCOUNTS}
    for uid, expected_name in expected_users.items():
        observed = tuple(
            record
            for record in passwd_inventory
            if record.pw_uid == uid or record.pw_name == expected_name
        )
        if (
            len(observed) != 1
            or observed[0].pw_uid != uid
            or observed[0].pw_name != expected_name
        ):
            raise StagingBootstrapError("staging process UID is not unique")
    expected_primary_gids = {
        account.gid: (
            account.name,
            frozenset({account.name}),
            frozenset(),
        )
        for account in PROCESS_ACCOUNTS
    }
    expected_groups = {
        **{
            gid: (name, frozenset(), members)
            for name, (gid, members) in SOCKET_GROUPS.items()
        },
        **expected_primary_gids,
    }
    for gid, (expected_name, primary_users, explicit_members) in expected_groups.items():
        observed = tuple(
            record
            for record in group_inventory
            if record.gr_gid == gid or record.gr_name == expected_name
        )
        if (
            len(observed) != 1
            or observed[0].gr_gid != gid
            or observed[0].gr_name != expected_name
        ):
            raise StagingBootstrapError("staging protected GID is not unique")
        if frozenset(observed[0].gr_mem) != explicit_members:
            raise StagingBootstrapError("staging protected group membership differs")
        effective_primary_users = frozenset(
            record.pw_name for record in passwd_inventory if record.pw_gid == gid
        )
        if effective_primary_users != primary_users:
            raise StagingBootstrapError("staging primary group membership differs")
    for name, (gid, members) in SOCKET_GROUPS.items():
        try:
            record = grp.getgrnam(name)
        except KeyError as exc:
            raise StagingBootstrapError("staging socket group is absent") from exc
        if record.gr_gid != gid or frozenset(record.gr_mem) != members:
            raise StagingBootstrapError("staging socket group differs")


def _prepare_layout(
    layout: StagingBootstrapLayout, *, resources: BootstrapResources
) -> None:
    _require_directory(
        layout.volume_root,
        mode=0o755,
        uid=0,
        gid=0,
        create=False,
        resources=resources,
    )
    if layout.transfer_root.exists() or layout.transfer_root.is_symlink():
        raise StagingBootstrapError("staging transfer root was not removed")
    _require_directory(
        layout.synthetic_storage,
        mode=0o700,
        uid=SYNTHETIC_ACCOUNT.uid,
        gid=SYNTHETIC_ACCOUNT.gid,
        create=True,
        resources=resources,
        persistent=True,
    )
    release_environment = {
        "BUFFALO_RESEARCH_ROOT": str(layout.research_payload),
        "BUFFALO_RESEARCH_MANIFEST": str(layout.research_manifest),
        "BUFFALO_RESEARCH_GIT_DIR": str(layout.research_git_dir),
        "BUFFALO_PRIVATE_RESEARCH_WORKSPACE": str(layout.research_workspace),
    }
    try:
        paths = validate_staging_research_paths(release_environment)
    except ValueError as exc:
        raise StagingBootstrapError("accepted research release is unavailable") from exc
    if paths.release != layout.research_release:
        raise StagingBootstrapError("accepted research release binding differs")
    _require_directory(
        layout.research_release,
        mode=0o710,
        uid=0,
        gid=RESEARCH_ACCOUNT.gid,
        create=False,
        resources=resources,
    )
    directories = (
        (layout.runtime_root, 0o755, 0, 0),
        (layout.supervisor_root, 0o700, 0, 0),
        (layout.gateway_root, 0o700, GATEWAY_ACCOUNT.uid, GATEWAY_ACCOUNT.gid),
        (
            layout.synthetic_root,
            0o700,
            SYNTHETIC_ACCOUNT.uid,
            SYNTHETIC_ACCOUNT.gid,
        ),
        (layout.research_root, 0o700, RESEARCH_ACCOUNT.uid, RESEARCH_ACCOUNT.gid),
        (layout.gateway_root / "tmp", 0o700, GATEWAY_ACCOUNT.uid, GATEWAY_ACCOUNT.gid),
        (
            layout.synthetic_root / "tmp",
            0o700,
            SYNTHETIC_ACCOUNT.uid,
            SYNTHETIC_ACCOUNT.gid,
        ),
        (
            layout.synthetic_root / "private",
            0o700,
            SYNTHETIC_ACCOUNT.uid,
            SYNTHETIC_ACCOUNT.gid,
        ),
        (
            layout.research_root / "tmp",
            0o700,
            RESEARCH_ACCOUNT.uid,
            RESEARCH_ACCOUNT.gid,
        ),
        (layout.validation_parent, 0o700, 0, 0),
        (layout.shared_socket_root, 0o755, 0, 0),
        (layout.synthetic_socket_parent, 0o750, 0, SYNTHETIC_SOCKET_GROUP),
        (layout.research_socket_parent, 0o750, 0, RESEARCH_SOCKET_GROUP),
        (layout.control_socket_parent, 0o750, 0, CONTROL_SOCKET_GROUP),
        (layout.shared_key_root, 0o755, 0, 0),
        (layout.gateway_synthetic_keys, 0o710, 0, GATEWAY_ACCOUNT.gid),
        (layout.gateway_research_keys, 0o710, 0, GATEWAY_ACCOUNT.gid),
        (layout.gateway_control_keys, 0o710, 0, GATEWAY_ACCOUNT.gid),
        (layout.synthetic_keys, 0o710, 0, SYNTHETIC_ACCOUNT.gid),
        (layout.research_keys, 0o710, 0, RESEARCH_ACCOUNT.gid),
    )
    for path, mode, uid, gid in directories:
        _require_directory(
            path,
            mode=mode,
            uid=uid,
            gid=gid,
            create=True,
            resources=resources,
        )
    _validate_ephemeral_membership(layout)


def _require_directory(
    path: Path,
    *,
    mode: int,
    uid: int,
    gid: int,
    create: bool,
    resources: BootstrapResources,
    persistent: bool = False,
) -> None:
    _require_canonical_path(path)
    created = False
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise StagingBootstrapError("required staging directory is absent") from None
        try:
            parent = path.parent.stat(follow_symlinks=False)
        except OSError as exc:
            raise StagingBootstrapError("staging directory parent is unavailable") from exc
        if not stat.S_ISDIR(parent.st_mode) or path.parent.is_symlink():
            raise StagingBootstrapError("staging directory parent differs")
        try:
            os.mkdir(path, mode)
            os.chown(path, uid, gid, follow_symlinks=False)
            os.chmod(path, mode, follow_symlinks=False)
        except BaseException:
            try:
                path.rmdir()
            except OSError:
                pass
            raise
        info = path.stat(follow_symlinks=False)
        created = True
    except OSError as exc:
        raise StagingBootstrapError("required staging directory is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != mode
        or (info.st_uid, info.st_gid) != (uid, gid)
    ):
        raise StagingBootstrapError("staging directory contract differs")
    if created and not persistent:
        resources.created_directories.append(
            _OwnedNode(
                path=path,
                device=info.st_dev,
                inode=info.st_ino,
                kind="directory",
                mode=mode,
                uid=uid,
                gid=gid,
            )
        )


def _write_pgpass(
    path: Path,
    *,
    database_url: str,
    password: str,
    uid: int,
    gid: int,
    resources: BootstrapResources,
) -> None:
    _capture_node(
        path.parent,
        kind="directory",
        mode=0o700,
        uid=uid,
        gid=gid,
    )
    parsed = urlparse(database_url)
    values = (
        parsed.hostname or "",
        str(parsed.port or 5432),
        parsed.path.removeprefix("/"),
        parsed.username or "",
        password,
    )
    if values[3] != RUNTIME_LOGIN or any(not value for value in values):
        raise StagingBootstrapError("synthetic credential binding differs")
    escape = lambda value: value.replace("\\", "\\\\").replace(":", "\\:")
    encoded = (":".join(escape(value) for value in values) + "\n").encode("utf-8")
    if not 0 < len(encoded) <= 4_096:
        raise StagingBootstrapError("synthetic credential record is invalid")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        try:
            created = os.fstat(descriptor)
        except BaseException:
            # O_EXCL created this pathname.  Preserve its identity even when
            # descriptor inspection itself fails so the caller's cleanup can
            # still remove only this inode.
            fallback = path.stat(follow_symlinks=False)
            _register_provisional_file(path, fallback, resources=resources)
            raise
        _register_provisional_file(path, created, resources=resources)
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written < 1:
                raise StagingBootstrapError("synthetic credential write failed")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, uid, gid)
        final = os.fstat(descriptor)
        if (
            not stat.S_ISREG(final.st_mode)
            or stat.S_IMODE(final.st_mode) != 0o600
            or (final.st_uid, final.st_gid) != (uid, gid)
            or final.st_nlink != 1
            or final.st_size != len(encoded)
        ):
            raise StagingBootstrapError("synthetic credential file differs")
        _finalize_owned_file(
            resources,
            _OwnedNode(
                path=path,
                device=final.st_dev,
                inode=final.st_ino,
                kind="file",
                mode=0o600,
                uid=uid,
                gid=gid,
                size=len(encoded),
            ),
        )
    finally:
        os.close(descriptor)


def _write_management_key(
    path: Path,
    *,
    key: bytes,
    uid: int,
    gid: int,
    parent_uid: int,
    parent_gid: int,
    resources: BootstrapResources,
) -> None:
    def register(identity: tuple[int, int]) -> None:
        resources.created_files.append(
            _OwnedNode(
                path=path,
                device=identity[0],
                inode=identity[1],
                kind="file",
                mode=None,
                uid=None,
                gid=None,
                size=None,
            )
        )

    write_private_key_copy(
        path,
        key=key,
        uid=uid,
        gid=gid,
        parent_uid=parent_uid,
        parent_gid=parent_gid,
        on_created=register,
    )
    _finalize_owned_file(
        resources,
        _capture_node(
            path,
            kind="file",
            mode=0o600,
            uid=uid,
            gid=gid,
            size=32,
        ),
    )


def _register_provisional_file(
    path: Path,
    info: os.stat_result,
    *,
    resources: BootstrapResources,
) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or any(node.path == path for node in resources.created_files)
    ):
        raise StagingBootstrapError("provisional bootstrap file differs")
    resources.created_files.append(
        _OwnedNode(
            path=path,
            device=info.st_dev,
            inode=info.st_ino,
            kind="file",
            mode=None,
            uid=None,
            gid=None,
            size=None,
        )
    )


def _finalize_owned_file(
    resources: BootstrapResources,
    final: _OwnedNode,
) -> None:
    for index, node in enumerate(resources.created_files):
        if (node.path, node.device, node.inode) == (
            final.path,
            final.device,
            final.inode,
        ):
            resources.created_files[index] = final
            return
    raise StagingBootstrapError("bootstrap file ownership was not registered")


def _socket_contracts_for_layout(
    layout: StagingBootstrapLayout,
) -> dict[str, SocketContract]:
    return {
        "synthetic": SocketContract(
            path=layout.synthetic_socket,
            parent_uid=0,
            parent_gid=SYNTHETIC_SOCKET_GROUP,
            socket_uid=SYNTHETIC_ACCOUNT.uid,
            socket_gid=SYNTHETIC_SOCKET_GROUP,
        ),
        "research": SocketContract(
            path=layout.research_socket,
            parent_uid=0,
            parent_gid=RESEARCH_SOCKET_GROUP,
            socket_uid=RESEARCH_ACCOUNT.uid,
            socket_gid=RESEARCH_SOCKET_GROUP,
        ),
        "control": SocketContract(
            path=layout.control_socket,
            parent_uid=0,
            parent_gid=CONTROL_SOCKET_GROUP,
            socket_uid=0,
            socket_gid=CONTROL_SOCKET_GROUP,
        ),
    }


def _bind_bootstrap_listeners(
    contracts: Mapping[str, SocketContract],
    *,
    resources: BootstrapResources,
) -> dict[str, socket.socket]:
    """Bind and register each listener before attempting the next one."""

    listeners: dict[str, socket.socket] = {}
    for role in ("synthetic", "research", "control"):
        try:
            contract = contracts[role]
        except KeyError as exc:
            raise StagingBootstrapError("staging listener inventory differs") from exc
        listener = bind_private_listener(contract)
        resources.listeners.append(listener)
        bound = contract.path.stat(follow_symlinks=False)
        provisional = _OwnedNode(
            path=contract.path,
            device=bound.st_dev,
            inode=bound.st_ino,
            kind="socket",
            mode=None,
            uid=None,
            gid=None,
        )
        resources.created_sockets.append(provisional)
        final = _capture_node(
            contract.path,
            kind="socket",
            mode=0o660,
            uid=contract.socket_uid,
            gid=contract.socket_gid,
        )
        if (final.device, final.inode) != (provisional.device, provisional.inode):
            raise StagingBootstrapError("staging listener identity changed")
        resources.created_sockets[-1] = final
        listeners[role] = listener
    return listeners


def _validate_layout_root_separation(layout: StagingBootstrapLayout) -> None:
    """Keep durable volume state disjoint from ephemeral credentials and IPC."""

    _require_canonical_path(layout.runtime_root)
    _require_canonical_path(layout.volume_root)
    if (
        layout.runtime_root == layout.volume_root
        or layout.runtime_root.is_relative_to(layout.volume_root)
        or layout.volume_root.is_relative_to(layout.runtime_root)
    ):
        raise StagingBootstrapError("staging runtime and volume roots overlap")


def _capture_node(
    path: Path,
    *,
    kind: str,
    mode: int,
    uid: int,
    gid: int,
    size: int | None = None,
) -> _OwnedNode:
    info = path.stat(follow_symlinks=False)
    expected_type = {
        "file": stat.S_ISREG,
        "socket": stat.S_ISSOCK,
        "directory": stat.S_ISDIR,
    }.get(kind)
    if (
        expected_type is None
        or path.is_symlink()
        or not expected_type(info.st_mode)
        or stat.S_IMODE(info.st_mode) != mode
        or (info.st_uid, info.st_gid) != (uid, gid)
        or (kind in {"file", "socket"} and info.st_nlink != 1)
        or (size is not None and info.st_size != size)
    ):
        raise StagingBootstrapError("owned bootstrap node differs")
    return _OwnedNode(
        path=path,
        device=info.st_dev,
        inode=info.st_ino,
        kind=kind,
        mode=mode,
        uid=uid,
        gid=gid,
        size=size,
    )


def _unlink_owned_node(node: _OwnedNode) -> None:
    try:
        info = node.path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    expected_type = {
        "file": stat.S_ISREG,
        "socket": stat.S_ISSOCK,
        "directory": stat.S_ISDIR,
    }.get(node.kind)
    if (
        expected_type is None
        or node.path.is_symlink()
        or not expected_type(info.st_mode)
        or (node.kind in {"file", "socket"} and info.st_nlink != 1)
        or (info.st_dev, info.st_ino) != (node.device, node.inode)
        or (node.mode is not None and stat.S_IMODE(info.st_mode) != node.mode)
        or (node.uid is not None and info.st_uid != node.uid)
        or (node.gid is not None and info.st_gid != node.gid)
        or (node.size is not None and info.st_size != node.size)
    ):
        raise StagingBootstrapError("bootstrap node identity changed")
    node.path.unlink()


def _rmdir_owned_node(node: _OwnedNode) -> None:
    try:
        current = _capture_node(
            node.path,
            kind="directory",
            mode=node.mode,
            uid=node.uid,
            gid=node.gid,
        )
    except FileNotFoundError:
        return
    if (current.device, current.inode) != (node.device, node.inode):
        raise StagingBootstrapError("bootstrap directory identity changed")
    try:
        node.path.rmdir()
    except OSError as exc:
        raise StagingBootstrapError("bootstrap directory is not empty") from exc


def _require_canonical_path(path: Path) -> None:
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise StagingBootstrapError("staging path is not canonical")
    current = Path(path.anchor)
    for part in path.parts[1:-1]:
        current /= part
        try:
            info = current.stat(follow_symlinks=False)
        except FileNotFoundError:
            # A later exact creator handles this component.  Existing ancestors
            # up to this point have still been checked without following links.
            return
        except OSError as exc:
            raise StagingBootstrapError("staging path ancestor is unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise StagingBootstrapError("staging path ancestor is a symlink")


def _root_entry_carries_unreviewed_authority(name: str, value: str) -> bool:
    upper = name.upper()
    sensitive_suffixes = (
        "_ACCESS_KEY",
        "_API_KEY",
        "_CREDENTIAL",
        "_CREDENTIALS",
        "_PRIVATE_KEY",
        "_PASSWORD",
        "_SECRET",
        "_TOKEN",
    )
    credentialed_url = False
    if upper.endswith("_URL"):
        try:
            parsed = urlparse(value)
            credentialed_url = (
                parsed.username is not None or parsed.password is not None
            )
        except ValueError:
            credentialed_url = True
    return bool(
        name in _FORBIDDEN_ROOT_NAMES
        or upper.startswith(("PG", "POSTGRES_", "SHOPIFY_"))
        or upper.endswith(sensitive_suffixes)
        or credentialed_url
        or (
            "DATABASE" in upper
            and upper.endswith(("_URL", "_USER", "_USERNAME", "_PASSWORD"))
        )
    )


def _validate_ephemeral_membership(layout: StagingBootstrapLayout) -> None:
    expected = {
        layout.runtime_root: {
            "gateway",
            "keys",
            "research",
            "sockets",
            "supervisor",
            "synthetic",
        },
        layout.supervisor_root: {"research-validation"},
        layout.gateway_root: {"tmp"},
        layout.synthetic_root: {"private", "tmp"},
        layout.research_root: {"tmp"},
        layout.validation_parent: set(),
        layout.gateway_root / "tmp": set(),
        layout.synthetic_root / "tmp": set(),
        layout.synthetic_root / "private": set(),
        layout.research_root / "tmp": set(),
        layout.shared_socket_root: {"control", "research", "synthetic"},
        layout.synthetic_socket_parent: set(),
        layout.research_socket_parent: set(),
        layout.control_socket_parent: set(),
        layout.shared_key_root: {
            "gateway-control",
            "gateway-research",
            "gateway-synthetic",
            "research",
            "synthetic",
        },
        layout.gateway_synthetic_keys: set(),
        layout.gateway_research_keys: set(),
        layout.gateway_control_keys: set(),
        layout.synthetic_keys: set(),
        layout.research_keys: set(),
    }
    for path, accepted in expected.items():
        try:
            observed = {entry.name for entry in path.iterdir()}
        except OSError as exc:
            raise StagingBootstrapError(
                "ephemeral staging inventory is unavailable"
            ) from exc
        if observed != accepted:
            raise StagingBootstrapError("ephemeral staging inventory differs")


def _decode_mountinfo_path(value: str) -> str:
    replacements = {
        "\\040": " ",
        "\\011": "\t",
        "\\012": "\n",
        "\\134": "\\",
    }
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "\\":
            if value[index] == "\x00":
                raise StagingBootstrapError("volume mount record is malformed")
            output.append(value[index])
            index += 1
            continue
        encoded = value[index : index + 4]
        try:
            output.append(replacements[encoded])
        except KeyError:
            raise StagingBootstrapError("volume mount record is malformed") from None
        index += 4
    return "".join(output)


def _validate_volume_mount(
    volume_root: Path,
    *,
    local_acceptance: bool,
    _mountinfo_path: Path = Path("/proc/self/mountinfo"),
) -> None:
    """Bind the configured volume to one writable kernel mount record."""

    _require_canonical_path(volume_root)
    try:
        raw = _mountinfo_path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise StagingBootstrapError("volume mount inventory is unavailable") from exc
    matches: list[tuple[set[str], str, set[str]]] = []
    for line in raw.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator < 6 or len(fields) < separator + 4:
            continue
        try:
            mountpoint = Path(_decode_mountinfo_path(fields[4]))
        except StagingBootstrapError:
            continue
        if mountpoint != volume_root:
            continue
        matches.append(
            (
                set(fields[5].split(",")),
                fields[separator + 1],
                set(fields[separator + 3].split(",")),
            )
        )
    if len(matches) != 1:
        raise StagingBootstrapError("staging volume is not one exact mount")
    mount_options, filesystem, super_options = matches[0]
    durable_filesystems = {"btrfs", "ext4", "xfs"}
    if (
        "rw" not in mount_options
        or "ro" in mount_options
        or "rw" not in super_options
        or "ro" in super_options
        or (not local_acceptance and filesystem not in durable_filesystems)
        or filesystem
        in {"cgroup", "cgroup2", "devpts", "proc", "sysfs", "tmpfs"}
    ):
        raise StagingBootstrapError("staging volume mount contract differs")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BOOTSTRAP_CONTRACT",
    "BootstrapChildContracts",
    "BootstrapResources",
    "CONTROL_SOCKET_GROUP",
    "DATABASE_PASSWORD_ENV",
    "DATABASE_URL_ENV",
    "GATEWAY_ACCOUNT",
    "PROCESS_ACCOUNTS",
    "RESEARCH_ACCOUNT",
    "RESEARCH_SOCKET_GROUP",
    "RUNTIME_ROOT",
    "SYNTHETIC_ACCOUNT",
    "SYNTHETIC_SOCKET_GROUP",
    "StagingBootstrapComposition",
    "StagingBootstrapError",
    "StagingBootstrapInputs",
    "StagingBootstrapLayout",
    "build_child_contracts",
    "load_bootstrap_inputs",
    "main",
    "prepare_bootstrap",
    "run_bootstrap",
    "run_composition",
]
