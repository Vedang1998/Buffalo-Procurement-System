"""Private staging supervisor control and cleanup tests."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import struct
from dataclasses import replace

from procurement_os import staging_process, staging_supervisor
from procurement_os.staging_supervisor import (
    CONTROL_VERSION,
    ComponentIsolation,
    ControlRateLimiter,
    ControlStatus,
    FixedChildSpec,
    ProcessIdentity,
    StagingChildSupervisor,
    StagingIsolationContract,
    StagingSupervisorError,
    SupervisorControlClient,
    SupervisorControlProtocol,
    SupervisorControlServer,
    WorkerKeyCopyContract,
    WorkerKeyLifecycle,
    StagedWorkerKey,
    bind_private_listener,
    enable_child_subreaper,
    launch_fixed_child,
    mint_control_message,
    remove_owned_stale_socket,
    terminate_owned_process_group,
    write_private_key_copy,
)
from procurement_os.staging_process_contract import child_argv
from procurement_os.staging_worker_types import WorkerKeyring
from procurement_os.staging_worker_transport import PeerCredentials, SocketContract


KEY = bytes.fromhex("93" * 32)
PEER = PeerCredentials(pid=100, uid=101, gid=102)


def _isolation_fixture() -> tuple[
    StagingIsolationContract, tuple[FixedChildSpec, ...]
]:
    base = Path("/run/buffalo-staging-test")
    groups = (2301, 2302, 2303)
    definitions = {
        "gateway": {
            "uid": 1101,
            "gid": 1201,
            "groups": tuple(sorted(groups)),
            "fds": (),
            "environment": {
                "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
                "BUFFALO_STAGING_ENABLED": "1",
                "BUFFALO_STAGING_EXPECTED_COMMIT": "a" * 40,
                "BUFFALO_STAGING_EXTERNAL_HOST": "staging.example.test",
                "BUFFALO_STAGING_OWNER_VERIFIER": "private-phc",
                "BUFFALO_STAGING_POSTGRES_SERVICE_ID": "postgres-service",
                "BUFFALO_STAGING_RESEARCH_KEY_FILE": str(base / "keys" / "gateway-research" / "research-0.key"),
                "BUFFALO_STAGING_RESEARCH_KEY_DIRECTORY_GID": "1201",
                "BUFFALO_STAGING_RESEARCH_KEY_DIRECTORY_UID": "0",
                "BUFFALO_STAGING_RESEARCH_SOCKET_GID": "2302",
                "BUFFALO_STAGING_RESEARCH_SOCKET_PATH": str(base / "sockets" / "research" / "worker.sock"),
                "BUFFALO_STAGING_RESEARCH_SOCKET_UID": "1103",
                "BUFFALO_STAGING_RUNTIME_ROOT": str(base / "gateway"),
                "BUFFALO_STAGING_SYNTHETIC_KEY_FILE": str(base / "keys" / "gateway-synthetic" / "synthetic-0.key"),
                "BUFFALO_STAGING_SYNTHETIC_KEY_DIRECTORY_GID": "1201",
                "BUFFALO_STAGING_SYNTHETIC_KEY_DIRECTORY_UID": "0",
                "BUFFALO_STAGING_SYNTHETIC_SOCKET_GID": "2301",
                "BUFFALO_STAGING_SYNTHETIC_SOCKET_PATH": str(base / "sockets" / "synthetic" / "worker.sock"),
                "BUFFALO_STAGING_SYNTHETIC_SOCKET_UID": "1102",
                "BUFFALO_STAGING_VOLUME_ROOT": "/data",
                "HOME": str(base / "gateway"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin",
                "PORT": "8080",
                "PYTHONUNBUFFERED": "1",
                "RAILWAY_ENVIRONMENT_ID": "environment",
                "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
                "RAILWAY_PROJECT_ID": "project",
                "RAILWAY_REPLICA_ID": "replica",
                "RAILWAY_SERVICE_ID": "service",
                "TMPDIR": str(base / "gateway" / "tmp"),
                "TZ": "UTC",
            },
        },
        "synthetic": {
            "uid": 1102,
            "gid": 1202,
            "groups": (groups[0],),
            "fds": (11,),
            "environment": {
                "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
                "BUFFALO_STAGING_ASSERTION_KEY_FILE": str(base / "synthetic" / "worker.key"),
                "BUFFALO_STAGING_EXTERNAL_ORIGIN": "https://staging.example.test",
                "BUFFALO_STAGING_GATEWAY_GID": "1201",
                "BUFFALO_STAGING_GATEWAY_PID": "999",
                "BUFFALO_STAGING_GATEWAY_UID": "1101",
                "BUFFALO_STAGING_KEY_GENERATION": "0",
                "BUFFALO_STAGING_KEY_DIRECTORY_GID": "1202",
                "BUFFALO_STAGING_KEY_DIRECTORY_UID": "0",
                "BUFFALO_STAGING_LISTEN_FD": "11",
                "BUFFALO_STAGING_POSTGRES_SERVICE_ID": "postgres-service",
                "BUFFALO_STAGING_RUNTIME_ROOT": str(base / "synthetic"),
                "BUFFALO_STAGING_SOCKET_GID": "2301",
                "BUFFALO_STAGING_SOCKET_PATH": str(base / "sockets" / "synthetic" / "worker.sock"),
                "BUFFALO_STAGING_WORKER_ROLE": "synthetic",
                "DATABASE_URL": "postgresql://private-staging",
                "HOME": str(base / "synthetic"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin",
                "PGPASSFILE": str(base / "synthetic" / "pgpass"),
                "PROCUREMENT_STORAGE_ROOT": "/data/synthetic",
                "PYTHONUNBUFFERED": "1",
                "RAILWAY_ENVIRONMENT_ID": "environment",
                "RAILWAY_PROJECT_ID": "project",
                "RAILWAY_SERVICE_ID": "service",
                "TMPDIR": str(base / "synthetic" / "tmp"),
                "TZ": "UTC",
            },
        },
        "research": {
            "uid": 1103,
            "gid": 1203,
            "groups": (groups[1],),
            "fds": (12,),
            "environment": {
                "BUFFALO_PRIVATE_RESEARCH_WORKSPACE": "/private/workspace",
                "BUFFALO_RESEARCH_GIT_DIR": "/private/research.git",
                "BUFFALO_RESEARCH_MANIFEST": "/private/research-manifest.json",
                "BUFFALO_RESEARCH_ROOT": "/private/research",
                "BUFFALO_STAGING_ASSERTION_KEY_FILE": str(base / "research" / "worker.key"),
                "BUFFALO_STAGING_EXTERNAL_ORIGIN": "https://staging.example.test",
                "BUFFALO_STAGING_GATEWAY_GID": "1201",
                "BUFFALO_STAGING_GATEWAY_PID": "999",
                "BUFFALO_STAGING_GATEWAY_UID": "1101",
                "BUFFALO_STAGING_KEY_GENERATION": "0",
                "BUFFALO_STAGING_KEY_DIRECTORY_GID": "1203",
                "BUFFALO_STAGING_KEY_DIRECTORY_UID": "0",
                "BUFFALO_STAGING_LISTEN_FD": "12",
                "BUFFALO_STAGING_RUNTIME_ROOT": str(base / "research"),
                "BUFFALO_STAGING_SOCKET_GID": "2302",
                "BUFFALO_STAGING_SOCKET_PATH": str(base / "sockets" / "research" / "worker.sock"),
                "BUFFALO_STAGING_WORKER_ROLE": "research",
                "HOME": str(base / "research"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin",
                "PYTHONUNBUFFERED": "1",
                "TMPDIR": str(base / "research" / "tmp"),
                "TZ": "UTC",
            },
        },
    }
    components = [
        ComponentIsolation(
            name="supervisor",
            uid=0,
            gid=0,
            extra_groups=(),
            runtime_root=base / "supervisor",
        )
    ]
    specs: list[FixedChildSpec] = []
    for name in ("gateway", "synthetic", "research"):
        definition = definitions[name]
        argv = child_argv(python_executable=sys.executable, role=name)
        environment = tuple(sorted(definition["environment"].items()))
        components.append(
            ComponentIsolation(
                name=name,
                uid=definition["uid"],
                gid=definition["gid"],
                extra_groups=definition["groups"],
                runtime_root=base / name,
                child_argv=argv,
                child_environment_names=frozenset(definition["environment"]),
                child_pass_fds=definition["fds"],
            )
        )
        specs.append(
            FixedChildSpec(
                name=name,
                argv=argv,
                environment=environment,
                uid=definition["uid"],
                gid=definition["gid"],
                extra_groups=definition["groups"],
                pass_fds=definition["fds"],
            )
        )
    contracts = (
        (
            "synthetic",
            SocketContract(
                path=base / "sockets" / "synthetic" / "worker.sock",
                parent_uid=0,
                parent_gid=groups[0],
                socket_uid=1102,
                socket_gid=groups[0],
            ),
        ),
        (
            "research",
            SocketContract(
                path=base / "sockets" / "research" / "worker.sock",
                parent_uid=0,
                parent_gid=groups[1],
                socket_uid=1103,
                socket_gid=groups[1],
            ),
        ),
        (
            "control",
            SocketContract(
                path=base / "sockets" / "control" / "control.sock",
                parent_uid=0,
                parent_gid=groups[2],
                socket_uid=0,
                socket_gid=groups[2],
            ),
        ),
    )
    contract = StagingIsolationContract(
        components=tuple(components),
        synthetic_socket_group=groups[0],
        research_socket_group=groups[1],
        control_socket_group=groups[2],
        shared_socket_root=base / "sockets",
        shared_socket_root_uid=0,
        shared_socket_root_gid=0,
        shared_key_root=base / "keys",
        shared_key_root_uid=0,
        shared_key_root_gid=0,
        socket_contracts=contracts,
    )
    return contract, tuple(specs)


class _FakeKeyLifecycle:
    def __init__(self, specs: tuple[FixedChildSpec, ...]) -> None:
        values = {spec.name: dict(spec.environment) for spec in specs}
        gateway_root = Path("/run/buffalo-staging-test/keys")
        self.gateway_roots = {
            role: gateway_root / f"gateway-{role}"
            for role in ("synthetic", "research")
        }
        self.worker_roots = {
            role: gateway_root / role
            for role in ("synthetic", "research")
        }
        identities = {
            spec.name: (spec.uid, spec.gid) for spec in specs
        }
        self.contracts = {
            role: WorkerKeyCopyContract(
                role=role,
                gateway_directory=self.gateway_roots[role],
                worker_directory=self.worker_roots[role],
                gateway_uid=identities["gateway"][0],
                gateway_gid=identities["gateway"][1],
                worker_uid=identities[role][0],
                worker_gid=identities[role][1],
                gateway_parent_uid=0,
                gateway_parent_gid=identities["gateway"][1],
                worker_parent_uid=0,
                worker_parent_gid=identities[role][1],
            )
            for role in ("synthetic", "research")
        }
        self.generations = {"synthetic": -1, "research": -1}
        self.staged: dict[tuple[str, int], StagedWorkerKey] = {}

    def stage(self, role: str) -> StagedWorkerKey:
        generation = self.generations[role] + 1
        key = hashlib.sha256(f"{role}:{generation}".encode()).digest()
        value = StagedWorkerKey(
            role=role,
            generation=generation,
            gateway_path=self.gateway_roots[role] / f"{role}-{generation}.key",
            worker_path=self.worker_roots[role] / f"{role}-{generation}.key",
            key=key,
        )
        self.generations[role] = generation
        self.staged[(role, generation)] = value
        return value

    def destroy(self, staged: StagedWorkerKey) -> None:
        if self.staged.pop((staged.role, staged.generation), None) != staged:
            raise StagingSupervisorError("fake key is not owned")

    def destroy_all(self) -> None:
        self.staged.clear()


def _new_supervisor(**arguments) -> StagingChildSupervisor:
    with mock.patch(
        "procurement_os.staging_supervisor.os.geteuid", return_value=0
    ), mock.patch(
        "procurement_os.staging_supervisor.os.getegid", return_value=0
    ):
        return StagingChildSupervisor(**arguments)


class _Hooks:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def start(self) -> ControlStatus:
        self.calls.append("start")
        return ControlStatus("VALIDATING", 1, 5)

    def stop(self) -> ControlStatus:
        self.calls.append("stop")
        return ControlStatus("STOPPING", 1, 1)

    def status(self) -> ControlStatus:
        self.calls.append("status")
        return ControlStatus("READY", 1)


class SupervisorControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hooks = _Hooks()
        self.protocol = SupervisorControlProtocol(
            key=KEY,
            expected_peer=PEER,
            hooks=self.hooks,
            wall_clock=lambda: 1_000.0,
        )

    def test_exact_parameter_free_operations_dispatch(self):
        for index, operation in enumerate(
            ("research-start", "research-stop", "research-status"), start=1
        ):
            response = self.protocol.dispatch(
                mint_control_message(
                    key=KEY,
                    operation=operation,
                    issued_at=1_000.0,
                    nonce=f"{index:064x}",
                ),
                peer=PEER,
            )
            payload = json.loads(response)
            self.assertEqual(payload["version"], CONTROL_VERSION)
            self.assertEqual(payload["operation"], operation)
            self.assertEqual(
                set(payload),
                {"generation", "operation", "retry_after_seconds", "state", "version"},
            )
        self.assertEqual(self.hooks.calls, ["start", "stop", "status"])

    def test_extra_command_path_identity_environment_or_retry_is_refused(self):
        original = json.loads(
            mint_control_message(
                key=KEY,
                operation="research-start",
                issued_at=1_000.0,
                nonce="11" * 32,
            )
        )
        for name, value in (
            ("command", "/bin/sh"),
            ("path", "/tmp/other"),
            ("uid", 0),
            ("gid", 0),
            ("environment", {"DATABASE_URL": "hidden"}),
            ("destination", "other.sock"),
            ("retry_count", 99),
        ):
            changed = dict(original)
            changed[name] = value
            with self.subTest(name=name):
                with self.assertRaises(StagingSupervisorError):
                    self.protocol.dispatch(
                        json.dumps(changed).encode(), peer=PEER
                    )
        self.assertEqual(self.hooks.calls, [])

    def test_wrong_peer_key_expiry_and_replay_are_refused(self):
        message = mint_control_message(
            key=KEY,
            operation="research-status",
            issued_at=1_000.0,
            nonce="22" * 32,
        )
        with self.assertRaises(StagingSupervisorError):
            self.protocol.dispatch(
                message, peer=PeerCredentials(pid=999, uid=101, gid=102)
            )
        changed = bytearray(message)
        changed[-2] ^= 1
        with self.assertRaises(StagingSupervisorError):
            self.protocol.dispatch(bytes(changed), peer=PEER)
        self.protocol.dispatch(message, peer=PEER)
        with self.assertRaises(StagingSupervisorError):
            self.protocol.dispatch(message, peer=PEER)

    def test_control_rate_is_globally_bounded_and_recovers_after_window(self):
        now = [1_000.0]
        rate_now = [100.0]
        protocol = SupervisorControlProtocol(
            key=KEY,
            expected_peer=PEER,
            hooks=self.hooks,
            wall_clock=lambda: now[0],
            rate_clock=lambda: rate_now[0],
            rate_limiter=ControlRateLimiter(maximum=2, window_seconds=1),
        )
        for index in (1, 2):
            protocol.dispatch(
                mint_control_message(
                    key=KEY,
                    operation="research-status",
                    issued_at=1_000.0,
                    nonce=f"{index:064x}",
                ),
                peer=PEER,
            )
        with self.assertRaisesRegex(StagingSupervisorError, "rate limit"):
            protocol.dispatch(
                mint_control_message(
                    key=KEY,
                    operation="research-status",
                    issued_at=1_000.0,
                    nonce="03" * 32,
                ),
                peer=PEER,
            )
        now[0] = 1_001.0
        rate_now[0] = 101.0
        protocol.dispatch(
            mint_control_message(
                key=KEY,
                operation="research-status",
                issued_at=1_001.0,
                nonce="04" * 32,
            ),
            peer=PEER,
        )

    def test_control_rate_window_is_independent_of_wall_clock_rollback(self):
        wall_now = [1_000.0]
        rate_now = [100.0]
        protocol = SupervisorControlProtocol(
            key=KEY,
            expected_peer=PEER,
            hooks=self.hooks,
            wall_clock=lambda: wall_now[0],
            rate_clock=lambda: rate_now[0],
            rate_limiter=ControlRateLimiter(maximum=2, window_seconds=1),
        )
        for index in (1, 2):
            protocol.dispatch(
                mint_control_message(
                    key=KEY,
                    operation="research-status",
                    issued_at=wall_now[0],
                    nonce=f"{index:064x}",
                ),
                peer=PEER,
            )
        wall_now[0] = 900.0
        rate_now[0] = 101.0
        protocol.dispatch(
            mint_control_message(
                key=KEY,
                operation="research-status",
                issued_at=wall_now[0],
                nonce="03" * 32,
            ),
            peer=PEER,
        )


class SupervisorChildTests(unittest.TestCase):
    def test_process_isolation_binds_exact_identity_command_environment_and_fds(self):
        contract, specs = _isolation_fixture()
        contract.validate_children(specs)
        changed = replace(
            specs[0], argv=("/bin/sh", "-c", "unexpected")
        )
        with self.assertRaisesRegex(StagingSupervisorError, "command differs"):
            contract.validate_children((changed, specs[1], specs[2]))
        changed_environment = replace(
            specs[2],
            environment=tuple(
                sorted((*specs[2].environment, ("PGPASSWORD", "private")))
            ),
        )
        with self.assertRaisesRegex(
            StagingSupervisorError, "environment inventory differs"
        ):
            contract.validate_children(
                (specs[0], specs[1], changed_environment)
            )
        changed_fd = replace(specs[1], pass_fds=(99,))
        with self.assertRaisesRegex(StagingSupervisorError, "descriptors differ"):
            contract.validate_children((specs[0], changed_fd, specs[2]))

    def test_root_supervisor_and_unprivileged_distinct_children_are_required(self):
        contract, _ = _isolation_fixture()
        components = list(contract.components)
        components[0] = replace(components[0], uid=999)
        with self.assertRaisesRegex(StagingSupervisorError, "run as root"):
            replace(contract, components=tuple(components)).validate()
        components = list(contract.components)
        components[1] = replace(components[1], uid=0)
        with self.assertRaisesRegex(StagingSupervisorError, "unprivileged"):
            replace(contract, components=tuple(components)).validate()
        components = list(contract.components)
        components[1] = replace(components[1], extra_groups=(0, 2301, 2302, 2303))
        with self.assertRaisesRegex(StagingSupervisorError, "groups"):
            replace(contract, components=tuple(components)).validate()
        with self.assertRaisesRegex(StagingSupervisorError, "socket groups"):
            replace(contract, synthetic_socket_group=0).validate()

    def test_supervisor_process_must_actually_run_as_declared_root_identity(self):
        contract, specs = _isolation_fixture()
        with mock.patch(
            "procurement_os.staging_supervisor.os.geteuid", return_value=1000
        ), mock.patch(
            "procurement_os.staging_supervisor.os.getegid", return_value=1000
        ), self.assertRaisesRegex(StagingSupervisorError, "process identity"):
            StagingChildSupervisor(
                isolation=contract,
                gateway_spec=specs[0],
                key_lifecycle=_FakeKeyLifecycle(specs),
                worker_specs=(specs[1], specs[2]),
                worker_key_preparer=lambda _staged, _gateway: None,
                worker_key_committer=lambda _staged, _gateway, _worker: None,
                worker_key_disabler=lambda _staged: None,
            )

    def test_key_lifecycle_is_bound_to_exact_role_roots_and_identities(self):
        contract, specs = _isolation_fixture()
        lifecycle = _FakeKeyLifecycle(specs)
        lifecycle.contracts["research"] = replace(
            lifecycle.contracts["research"],
            worker_uid=specs[1].uid,
        )
        with self.assertRaisesRegex(StagingSupervisorError, "lifecycle binding"):
            _new_supervisor(
                isolation=contract,
                gateway_spec=specs[0],
                key_lifecycle=lifecycle,
                worker_specs=(specs[1], specs[2]),
                worker_key_preparer=lambda _staged, _gateway: None,
                worker_key_committer=lambda _staged, _gateway, _worker: None,
                worker_key_disabler=lambda _staged: None,
            )
        lifecycle = _FakeKeyLifecycle(specs)
        lifecycle.contracts["synthetic"] = replace(
            lifecycle.contracts["synthetic"],
            gateway_directory=Path("/unexpected/keys"),
        )
        with self.assertRaisesRegex(StagingSupervisorError, "lifecycle binding"):
            _new_supervisor(
                isolation=contract,
                gateway_spec=specs[0],
                key_lifecycle=lifecycle,
                worker_specs=(specs[1], specs[2]),
                worker_key_preparer=lambda _staged, _gateway: None,
                worker_key_committer=lambda _staged, _gateway, _worker: None,
                worker_key_disabler=lambda _staged: None,
            )

    def test_shared_socket_roots_are_outside_every_private_runtime_root(self):
        contract, _ = _isolation_fixture()
        with self.assertRaisesRegex(StagingSupervisorError, "shared socket root"):
            replace(contract, shared_socket_root_uid=1102).validate()
        research = next(
            component
            for component in contract.components
            if component.name == "research"
        )
        sockets = dict(contract.socket_contracts)
        original = sockets["research"]
        sockets["research"] = replace(
            original,
            path=research.runtime_root / "socket" / "worker.sock",
        )
        with self.assertRaisesRegex(StagingSupervisorError, "socket binding differs"):
            replace(contract, socket_contracts=tuple(sockets.items())).validate()

    def test_runtime_roots_must_exist_as_exact_private_owned_directories(self):
        contract, _ = _isolation_fixture()
        with self.assertRaisesRegex(StagingSupervisorError, "unavailable"):
            contract.validate_runtime_roots()

    def test_fixed_child_launch_has_no_shell_and_exact_identity(self):
        spec = FixedChildSpec(
            name="gateway",
            argv=("/usr/bin/python3", "-I", "worker.py"),
            environment=(("LANG", "C.UTF-8"), ("PATH", "/usr/bin")),
            uid=1001,
            gid=1002,
            extra_groups=(1003,),
            pass_fds=(7,),
        )
        with mock.patch("subprocess.Popen") as popen:
            launch_fixed_child(spec)
        _, kwargs = popen.call_args
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["start_new_session"], True)
        self.assertEqual(kwargs["user"], 1001)
        self.assertEqual(kwargs["group"], 1002)
        self.assertEqual(kwargs["extra_groups"], (1003,))
        self.assertEqual(kwargs["pass_fds"], (7,))
        self.assertEqual(kwargs["env"], {"LANG": "C.UTF-8", "PATH": "/usr/bin"})

    def test_child_spec_repr_never_contains_environment_values(self):
        _, specs = _isolation_fixture()
        rendered = repr(specs[0])
        self.assertNotIn("private-phc", rendered)
        self.assertNotIn("BUFFALO_STAGING_OWNER_VERIFIER", rendered)
        self.assertNotIn("postgresql://private-staging", repr(specs[1]))

    def test_fixed_role_entrypoint_accepts_the_exact_supervisor_environments(self):
        _, specs = _isolation_fixture()
        with mock.patch.dict(os.environ, dict(specs[0].environment), clear=True), mock.patch.object(
            staging_process, "_run_gateway"
        ) as run_gateway:
            self.assertEqual(staging_process.main(["gateway"]), 0)
            run_gateway.assert_called_once()
        with mock.patch.dict(os.environ, dict(specs[1].environment), clear=True), mock.patch.object(
            staging_process, "_run_worker"
        ) as run_worker:
            self.assertEqual(staging_process.main(["synthetic"]), 0)
            run_worker.assert_called_once()

    def test_fixed_role_entrypoint_refuses_root_child_identity(self):
        _, specs = _isolation_fixture()
        with mock.patch.dict(
            os.environ, dict(specs[0].environment), clear=True
        ), mock.patch.object(os, "geteuid", return_value=0), mock.patch.object(
            os, "getegid", return_value=0
        ), self.assertRaisesRegex(staging_process.StagingProcessError, "unprivileged"):
            staging_process.main(["gateway"])

    def test_worker_cannot_start_before_gateway_and_bad_filesystem_blocks_launch(self):
        contract, specs = _isolation_fixture()
        launcher = mock.Mock()
        supervisor = _new_supervisor(
            isolation=contract,
            gateway_spec=specs[0],
            key_lifecycle=_FakeKeyLifecycle(specs),
            worker_specs=(specs[1], specs[2]),
            worker_key_preparer=lambda _staged, _gateway: None,
            worker_key_committer=lambda _staged, _gateway, _worker: None,
            worker_key_disabler=lambda _staged: None,
            launcher=launcher,
        )
        with self.assertRaisesRegex(StagingSupervisorError, "live gateway"):
            supervisor.start("synthetic")
        with self.assertRaisesRegex(StagingSupervisorError, "unavailable"):
            supervisor.start("gateway")
        launcher.assert_not_called()

    def test_minimal_supervisor_starts_fixed_initial_set_and_signal_reaps_all(self):
        contract, specs = _isolation_fixture()
        launched: list[subprocess.Popen[bytes]] = []
        launched_roles: list[str] = []

        def launcher(spec: FixedChildSpec) -> subprocess.Popen[bytes]:
            launched_roles.append(spec.name)
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            launched.append(process)
            return process

        with mock.patch.object(
            StagingIsolationContract, "validate_runtime_roots"
        ), mock.patch.object(StagingIsolationContract, "validate_private_sockets"):
            supervisor = _new_supervisor(
                isolation=contract,
                gateway_spec=specs[0],
                key_lifecycle=_FakeKeyLifecycle(specs),
                worker_specs=(specs[1], specs[2]),
                worker_key_preparer=lambda _staged, _gateway: None,
                worker_key_committer=lambda _staged, _gateway, _worker: None,
                worker_key_disabler=lambda _staged: None,
                launcher=launcher,
            )
            try:
                supervisor.start_initial()
                self.assertEqual(set(supervisor.children), {"gateway", "synthetic"})
                self.assertEqual(launched_roles, ["gateway", "synthetic"])
                synthetic_environment = dict(supervisor.children["synthetic"].spec.environment)
                self.assertEqual(
                    synthetic_environment["BUFFALO_STAGING_GATEWAY_PID"],
                    str(supervisor.children["gateway"].identity.pid),
                )
                supervisor.signal_handler(signal.SIGTERM)
                self.assertEqual(set(supervisor.children), {"gateway", "synthetic"})
                self.assertTrue(supervisor.process_pending_signal())
                self.assertEqual(supervisor.children, {})
                self.assertTrue(all(process.poll() is not None for process in launched))
                self.assertFalse(supervisor.process_pending_signal())
            finally:
                for process in launched:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    def test_stop_failure_retains_ownership_and_shutdown_is_retryable(self):
        contract, specs = _isolation_fixture()
        launched: list[subprocess.Popen[bytes]] = []

        def launcher(_spec: FixedChildSpec) -> subprocess.Popen[bytes]:
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            launched.append(process)
            return process

        with mock.patch.object(
            StagingIsolationContract, "validate_runtime_roots"
        ), mock.patch.object(StagingIsolationContract, "validate_private_sockets"):
            supervisor = _new_supervisor(
                isolation=contract,
                gateway_spec=specs[0],
                key_lifecycle=_FakeKeyLifecycle(specs),
                worker_specs=(specs[1], specs[2]),
                worker_key_preparer=lambda _staged, _gateway: None,
                worker_key_committer=lambda _staged, _gateway, _worker: None,
                worker_key_disabler=lambda _staged: None,
                launcher=launcher,
            )
            supervisor.start_initial()
            supervisor.start("research")
            with mock.patch(
                "procurement_os.staging_supervisor.terminate_owned_process_group",
                side_effect=StagingSupervisorError("injected cleanup failure"),
            ):
                with self.assertRaises(StagingSupervisorError):
                    supervisor.stop("research")
            self.assertIn("research", supervisor.children)

            real_terminate = terminate_owned_process_group
            failed_once = False

            def fail_research_once(process, identity, *, timeout_seconds):
                nonlocal failed_once
                if process is supervisor.children["research"].process and not failed_once:
                    failed_once = True
                    raise StagingSupervisorError("injected cleanup failure")
                return real_terminate(
                    process, identity, timeout_seconds=timeout_seconds
                )

            with mock.patch(
                "procurement_os.staging_supervisor.terminate_owned_process_group",
                side_effect=fail_research_once,
            ):
                with self.assertRaisesRegex(StagingSupervisorError, "did not stop"):
                    supervisor.shutdown(timeout_seconds=2)
            self.assertIn("research", supervisor.children)
            self.assertIn("gateway", supervisor.children)
            self.assertNotIn("synthetic", supervisor.children)
            supervisor.shutdown(timeout_seconds=2)
            self.assertEqual(supervisor.children, {})
            with self.assertRaises(StagingSupervisorError):
                supervisor.start("gateway")
        for process in launched:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_pending_signal_blocks_commit_and_rearms_after_shutdown_failure(self):
        contract, specs = _isolation_fixture()
        launched: list[subprocess.Popen[bytes]] = []
        committer = mock.Mock()
        disable_calls = 0

        def launcher(_spec: FixedChildSpec) -> subprocess.Popen[bytes]:
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            launched.append(process)
            return process

        def disable_once(_staged: StagedWorkerKey) -> None:
            nonlocal disable_calls
            disable_calls += 1
            if disable_calls == 1:
                raise StagingSupervisorError("injected disable failure")

        with mock.patch.object(
            StagingIsolationContract, "validate_runtime_roots"
        ), mock.patch.object(StagingIsolationContract, "validate_private_sockets"):
            supervisor = _new_supervisor(
                isolation=contract,
                gateway_spec=specs[0],
                key_lifecycle=_FakeKeyLifecycle(specs),
                worker_specs=(specs[1], specs[2]),
                worker_key_preparer=lambda _staged, _gateway: None,
                worker_key_committer=committer,
                worker_key_disabler=disable_once,
                launcher=launcher,
            )
            try:
                supervisor.start_initial()
                supervisor.signal_handler(signal.SIGTERM)
                with self.assertRaisesRegex(
                    StagingSupervisorError, "start transition is invalid"
                ):
                    supervisor.start("research")
                with self.assertRaisesRegex(
                    StagingSupervisorError, "cannot be committed"
                ):
                    supervisor.commit_worker("synthetic")
                with self.assertRaisesRegex(
                    StagingSupervisorError, "did not stop"
                ):
                    supervisor.process_pending_signal()
                self.assertTrue(supervisor._signal_requested)
                self.assertIn("synthetic", supervisor.children)
                with self.assertRaisesRegex(
                    StagingSupervisorError, "cannot be committed"
                ):
                    supervisor.commit_worker("synthetic")
                self.assertTrue(supervisor.process_pending_signal())
                self.assertEqual(supervisor.children, {})
                committer.assert_not_called()
            finally:
                for process in launched:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    def test_unknown_commit_acknowledgement_forces_terminal_cleanup(self):
        contract, specs = _isolation_fixture()
        launched: list[subprocess.Popen[bytes]] = []
        disabled: list[tuple[str, int]] = []

        def launcher(_spec: FixedChildSpec) -> subprocess.Popen[bytes]:
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            launched.append(process)
            return process

        def unknown_commit(*_arguments) -> None:
            raise StagingSupervisorError("injected acknowledgement loss")

        with mock.patch.object(
            StagingIsolationContract, "validate_runtime_roots"
        ), mock.patch.object(StagingIsolationContract, "validate_private_sockets"):
            supervisor = _new_supervisor(
                isolation=contract,
                gateway_spec=specs[0],
                key_lifecycle=_FakeKeyLifecycle(specs),
                worker_specs=(specs[1], specs[2]),
                worker_key_preparer=lambda _staged, _gateway: None,
                worker_key_committer=unknown_commit,
                worker_key_disabler=lambda staged: disabled.append(
                    (staged.role, staged.generation)
                ),
                launcher=launcher,
            )
            try:
                supervisor.start_initial()
                with self.assertRaisesRegex(
                    StagingSupervisorError, "acknowledgement is unknown"
                ):
                    supervisor.commit_worker("synthetic")
                self.assertTrue(supervisor._shutdown_requested)
                self.assertIn("synthetic", supervisor.children)
                with self.assertRaisesRegex(
                    StagingSupervisorError, "cannot be committed"
                ):
                    supervisor.commit_worker("synthetic")
                supervisor.shutdown(timeout_seconds=2)
                self.assertEqual(disabled, [("synthetic", 0)])
                self.assertEqual(supervisor.children, {})
            finally:
                for process in launched:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    def test_failed_identity_cleanup_retains_worker_for_shutdown_retry(self):
        contract, specs = _isolation_fixture()
        launched: list[subprocess.Popen[bytes]] = []

        def launcher(_spec: FixedChildSpec) -> subprocess.Popen[bytes]:
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            launched.append(process)
            return process

        with mock.patch.object(
            StagingIsolationContract, "validate_runtime_roots"
        ), mock.patch.object(StagingIsolationContract, "validate_private_sockets"):
            supervisor = _new_supervisor(
                isolation=contract,
                gateway_spec=specs[0],
                key_lifecycle=_FakeKeyLifecycle(specs),
                worker_specs=(specs[1], specs[2]),
                worker_key_preparer=lambda _staged, _gateway: None,
                worker_key_committer=lambda _staged, _gateway, _worker: None,
                worker_key_disabler=lambda _staged: None,
                launcher=launcher,
            )
            try:
                supervisor.start("gateway")
                with mock.patch.object(
                    ProcessIdentity,
                    "capture",
                    side_effect=StagingSupervisorError("injected capture failure"),
                ), mock.patch(
                    "procurement_os.staging_supervisor._kill_uncaptured_process_group",
                    side_effect=StagingSupervisorError("injected cleanup failure"),
                ):
                    with self.assertRaisesRegex(
                        StagingSupervisorError, "identity and cleanup failed"
                    ):
                        supervisor.start("synthetic")
                self.assertIn("synthetic", supervisor._uncaptured_children)
                self.assertIn("synthetic", supervisor._active_worker_keys)
                self.assertIsNone(launched[-1].poll())
                supervisor.shutdown(timeout_seconds=2)
                self.assertEqual(supervisor.children, {})
                self.assertEqual(supervisor._uncaptured_children, {})
                self.assertEqual(supervisor._active_worker_keys, {})
                self.assertTrue(all(process.poll() is not None for process in launched))
            finally:
                for process in launched:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    def test_initial_start_retains_uncaptured_worker_and_key_after_double_failure(self):
        contract, specs = _isolation_fixture()
        launched: list[subprocess.Popen[bytes]] = []
        real_capture = ProcessIdentity.capture
        capture_calls = 0

        def launcher(_spec: FixedChildSpec) -> subprocess.Popen[bytes]:
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            launched.append(process)
            return process

        def fail_worker_capture(pid: int) -> ProcessIdentity:
            nonlocal capture_calls
            capture_calls += 1
            if capture_calls == 1:
                return real_capture(pid)
            raise StagingSupervisorError("injected worker capture failure")

        with mock.patch.object(
            StagingIsolationContract, "validate_runtime_roots"
        ), mock.patch.object(StagingIsolationContract, "validate_private_sockets"):
            supervisor = _new_supervisor(
                isolation=contract,
                gateway_spec=specs[0],
                key_lifecycle=_FakeKeyLifecycle(specs),
                worker_specs=(specs[1], specs[2]),
                worker_key_preparer=lambda _staged, _gateway: None,
                worker_key_committer=lambda _staged, _gateway, _worker: None,
                worker_key_disabler=lambda _staged: None,
                launcher=launcher,
            )
            try:
                with mock.patch.object(
                    ProcessIdentity, "capture", side_effect=fail_worker_capture
                ), mock.patch(
                    "procurement_os.staging_supervisor._kill_uncaptured_process_group",
                    side_effect=StagingSupervisorError("injected cleanup failure"),
                ):
                    with self.assertRaisesRegex(
                        StagingSupervisorError, "startup and cleanup failed"
                    ):
                        supervisor.start_initial()
                self.assertIn("gateway", supervisor.children)
                self.assertIn("synthetic", supervisor._uncaptured_children)
                self.assertIn("synthetic", supervisor._active_worker_keys)
                self.assertIsNone(launched[-1].poll())
                supervisor.shutdown(timeout_seconds=2)
                self.assertEqual(supervisor.children, {})
                self.assertEqual(supervisor._uncaptured_children, {})
                self.assertEqual(supervisor._active_worker_keys, {})
            finally:
                for process in launched:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    def test_gateway_crash_is_proved_gone_before_worker_key_cleanup(self):
        contract, specs = _isolation_fixture()
        launched: list[subprocess.Popen[bytes]] = []
        disabled: list[tuple[str, int]] = []

        def launcher(_spec: FixedChildSpec) -> subprocess.Popen[bytes]:
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            launched.append(process)
            return process

        with mock.patch.object(
            StagingIsolationContract, "validate_runtime_roots"
        ), mock.patch.object(StagingIsolationContract, "validate_private_sockets"):
            supervisor = _new_supervisor(
                isolation=contract,
                gateway_spec=specs[0],
                key_lifecycle=_FakeKeyLifecycle(specs),
                worker_specs=(specs[1], specs[2]),
                worker_key_preparer=lambda _staged, _gateway: None,
                worker_key_committer=lambda _staged, _gateway, _worker: None,
                worker_key_disabler=lambda staged: disabled.append(
                    (staged.role, staged.generation)
                ),
                launcher=launcher,
            )
            supervisor.start_initial()
            gateway = supervisor.children["gateway"].process
            gateway.kill()
            gateway.wait(timeout=2)
            self.assertEqual(supervisor.reap_crashed(), ("gateway",))
            self.assertEqual(supervisor.children, {})
            self.assertEqual(disabled, [])
            self.assertTrue(all(process.poll() is not None for process in launched))

    def test_shutdown_detects_dead_gateway_before_disabling_workers(self):
        contract, specs = _isolation_fixture()
        launched: list[subprocess.Popen[bytes]] = []
        disabled: list[tuple[str, int]] = []

        def launcher(_spec: FixedChildSpec) -> subprocess.Popen[bytes]:
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            launched.append(process)
            return process

        with mock.patch.object(
            StagingIsolationContract, "validate_runtime_roots"
        ), mock.patch.object(StagingIsolationContract, "validate_private_sockets"):
            supervisor = _new_supervisor(
                isolation=contract,
                gateway_spec=specs[0],
                key_lifecycle=_FakeKeyLifecycle(specs),
                worker_specs=(specs[1], specs[2]),
                worker_key_preparer=lambda _staged, _gateway: None,
                worker_key_committer=lambda _staged, _gateway, _worker: None,
                worker_key_disabler=lambda staged: disabled.append(
                    (staged.role, staged.generation)
                ),
                launcher=launcher,
            )
            supervisor.start_initial()
            gateway = supervisor.children["gateway"].process
            gateway.kill()
            gateway.wait(timeout=2)
            supervisor.shutdown(timeout_seconds=2)
            self.assertEqual(supervisor.children, {})
            self.assertEqual(disabled, [])
            self.assertTrue(all(process.poll() is not None for process in launched))

    def test_research_restart_prepares_commits_and_disables_fresh_key_generations(self):
        contract, specs = _isolation_fixture()
        launched: list[subprocess.Popen[bytes]] = []
        activated: list[tuple[str, int, Path]] = []
        committed: list[tuple[str, int]] = []
        deactivated: list[tuple[str, int]] = []
        events: list[str] = []

        def launcher(spec: FixedChildSpec) -> subprocess.Popen[bytes]:
            events.append(f"launch:{spec.name}")
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            launched.append(process)
            return process

        def activate(staged: StagedWorkerKey, _gateway) -> None:
            events.append(f"prepare:{staged.role}:{staged.generation}")
            activated.append((staged.role, staged.generation, staged.worker_path))

        def deactivate(staged: StagedWorkerKey) -> None:
            self.assertIsNone(supervisor.children[staged.role].process.poll())
            events.append(f"disable:{staged.role}:{staged.generation}")
            deactivated.append((staged.role, staged.generation))

        def commit(staged: StagedWorkerKey, _gateway, _worker) -> None:
            events.append(f"commit:{staged.role}:{staged.generation}")
            committed.append((staged.role, staged.generation))

        with mock.patch.object(
            StagingIsolationContract, "validate_runtime_roots"
        ), mock.patch.object(StagingIsolationContract, "validate_private_sockets"):
            supervisor = _new_supervisor(
                isolation=contract,
                gateway_spec=specs[0],
                key_lifecycle=_FakeKeyLifecycle(specs),
                worker_specs=(specs[1], specs[2]),
                worker_key_preparer=activate,
                worker_key_committer=commit,
                worker_key_disabler=deactivate,
                launcher=launcher,
            )
            try:
                supervisor.start("gateway")
                first = supervisor.start("research")
                supervisor.commit_worker("research")
                with self.assertRaisesRegex(StagingSupervisorError, "already committed"):
                    supervisor.commit_worker("research")
                supervisor.stop("research", timeout_seconds=2)
                second = supervisor.start("research")
                supervisor.commit_worker("research")
                self.assertNotEqual(
                    dict(first.spec.environment)["BUFFALO_STAGING_ASSERTION_KEY_FILE"],
                    dict(second.spec.environment)["BUFFALO_STAGING_ASSERTION_KEY_FILE"],
                )
                self.assertEqual(
                    [(role, generation) for role, generation, _ in activated],
                    [("research", 0), ("research", 1)],
                )
                self.assertEqual(
                    committed, [("research", 0), ("research", 1)]
                )
                self.assertEqual(
                    events[:7],
                    [
                        "launch:gateway",
                        "prepare:research:0",
                        "launch:research",
                        "commit:research:0",
                        "disable:research:0",
                        "prepare:research:1",
                        "launch:research",
                    ],
                )
                self.assertEqual(deactivated, [("research", 0)])
                supervisor.shutdown(timeout_seconds=2)
                self.assertEqual(
                    deactivated, [("research", 0), ("research", 1)]
                )
            finally:
                for process in launched:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    def test_real_owned_process_group_is_terminated_and_reaped(self):
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", "import time; time.sleep(60)"],
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        identity = ProcessIdentity.capture(process.pid)
        try:
            result = terminate_owned_process_group(
                process, identity, timeout_seconds=2
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        self.assertLess(result, 0)
        self.assertFalse(identity.is_live())

    def test_leader_exit_between_poll_and_identity_check_is_drained(self):
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", "import time; time.sleep(60)"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        identity = ProcessIdentity.capture(process.pid)

        def exit_during_identity_check(_identity: ProcessIdentity) -> bool:
            process.terminate()
            process.wait(timeout=2)
            return False

        try:
            with mock.patch.object(
                ProcessIdentity,
                "is_live",
                autospec=True,
                side_effect=exit_during_identity_check,
            ):
                result = terminate_owned_process_group(
                    process, identity, timeout_seconds=2
                )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        self.assertEqual(result, -signal.SIGTERM)
        self.assertFalse(identity.group_is_live())

    def test_sigterm_resistant_group_is_killed_and_reaped(self):
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import signal,time; signal.signal(signal.SIGTERM, lambda *_: None); print('ready',flush=True); time.sleep(60)",
            ],
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        identity = ProcessIdentity.capture(process.pid)
        self.assertIsNotNone(process.stdout)
        self.assertEqual(process.stdout.readline().strip(), "ready")
        try:
            result = terminate_owned_process_group(
                process, identity, timeout_seconds=0.1
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
        self.assertEqual(result, -signal.SIGKILL)
        self.assertFalse(identity.group_is_live())

    def test_crashed_leader_does_not_leave_a_descendant_running(self):
        enable_child_subreaper()
        script = (
            "import subprocess,sys,time;"
            "child=subprocess.Popen([sys.executable,'-I','-B','-c','import time;time.sleep(60)']);"
            "print(child.pid,flush=True);time.sleep(.2)"
        )
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", script],
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        identity = ProcessIdentity.capture(process.pid)
        self.assertIsNotNone(process.stdout)
        descendant_pid = int(process.stdout.readline().strip())
        process.wait(timeout=3)
        self.assertTrue(identity.group_is_live())
        terminate_owned_process_group(process, identity, timeout_seconds=2)
        self.assertFalse(identity.group_is_live())
        record = Path(f"/proc/{descendant_pid}/stat")
        self.assertFalse(record.exists())
        process.stdout.close()

    def test_stale_socket_requires_proof_the_owned_process_is_dead(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-stale-socket-") as raw:
            path = Path(raw) / "worker.sock"
            value = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(value.close)
            value.bind(str(path))
            current = ProcessIdentity.capture(os.getpid())
            with self.assertRaises(StagingSupervisorError):
                remove_owned_stale_socket(
                    SocketContract(
                        path=path,
                        parent_uid=os.getuid(),
                        parent_gid=os.getgid(),
                        socket_uid=os.getuid(),
                        socket_gid=os.getgid(),
                    ),
                    identity=current,
                )
            dead = ProcessIdentity(
                pid=2_000_000_000,
                start_ticks=1,
                process_group=2_000_000_000,
                session_id=2_000_000_000,
            )
            path.chmod(0o660)
            Path(raw).chmod(0o750)
            remove_owned_stale_socket(
                SocketContract(
                    path=path,
                    parent_uid=os.getuid(),
                    parent_gid=os.getgid(),
                    socket_uid=os.getuid(),
                    socket_gid=os.getgid(),
                ),
                identity=dead,
            )
            self.assertFalse(path.exists())

    def test_private_key_copies_are_exact_and_never_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-key-copy-") as raw:
            root = Path(raw)
            root.chmod(0o710)
            path = root / "worker.key"
            write_private_key_copy(
                path,
                key=KEY,
                uid=os.getuid(),
                gid=os.getgid(),
                parent_uid=os.getuid(),
                parent_gid=os.getgid(),
            )
            self.assertEqual(path.read_bytes(), KEY)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(StagingSupervisorError):
                write_private_key_copy(
                    path,
                    key=KEY,
                    uid=os.getuid(),
                    gid=os.getgid(),
                    parent_uid=os.getuid(),
                    parent_gid=os.getgid(),
                )

    def test_private_key_copy_handles_partial_writes(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-key-partial-") as raw:
            root = Path(raw)
            root.chmod(0o710)
            path = root / "worker.key"
            original_write = os.write

            def partial_write(descriptor, value):
                return original_write(descriptor, bytes(value[:5]))

            with mock.patch("os.write", side_effect=partial_write):
                write_private_key_copy(
                    path,
                    key=KEY,
                    uid=os.getuid(),
                    gid=os.getgid(),
                    parent_uid=os.getuid(),
                    parent_gid=os.getgid(),
                )
            self.assertEqual(path.read_bytes(), KEY)

    def test_worker_key_lifecycle_creates_two_private_copies_and_never_reuses(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-key-lifecycle-") as raw:
            root = Path(raw)
            directories = {
                name: root / name
                for name in (
                    "gateway-synthetic",
                    "worker-synthetic",
                    "gateway-research",
                    "worker-research",
                )
            }
            for directory in directories.values():
                directory.mkdir(mode=0o710)
            values = iter((KEY, bytes.fromhex("a4" * 32), KEY))
            lifecycle = WorkerKeyLifecycle(
                (
                    WorkerKeyCopyContract(
                        role="synthetic",
                        gateway_directory=directories["gateway-synthetic"],
                        worker_directory=directories["worker-synthetic"],
                        gateway_uid=os.getuid(),
                        gateway_gid=os.getgid(),
                        worker_uid=os.getuid(),
                        worker_gid=os.getgid(),
                        gateway_parent_uid=os.getuid(),
                        gateway_parent_gid=os.getgid(),
                        worker_parent_uid=os.getuid(),
                        worker_parent_gid=os.getgid(),
                    ),
                    WorkerKeyCopyContract(
                        role="research",
                        gateway_directory=directories["gateway-research"],
                        worker_directory=directories["worker-research"],
                        gateway_uid=os.getuid(),
                        gateway_gid=os.getgid(),
                        worker_uid=os.getuid(),
                        worker_gid=os.getgid(),
                        gateway_parent_uid=os.getuid(),
                        gateway_parent_gid=os.getgid(),
                        worker_parent_uid=os.getuid(),
                        worker_parent_gid=os.getgid(),
                    ),
                ),
                random_bytes=lambda _: next(values),
            )
            synthetic = lifecycle.stage("synthetic")
            research = lifecycle.stage("research")
            self.assertEqual((synthetic.generation, research.generation), (0, 0))
            for staged in (synthetic, research):
                self.assertEqual(staged.gateway_path.read_bytes(), staged.key)
                self.assertEqual(staged.worker_path.read_bytes(), staged.key)
                self.assertEqual(staged.gateway_path.stat().st_mode & 0o777, 0o600)
                self.assertNotEqual(staged.gateway_path.parent, staged.worker_path.parent)
            self.assertEqual(
                staging_process._read_private_key(
                    synthetic.gateway_path,
                    parent_uid=os.getuid(),
                    parent_gid=os.getgid(),
                ),
                synthetic.key,
            )
            with self.assertRaisesRegex(
                staging_process.StagingProcessError,
                "key parent contract differs",
            ):
                staging_process._read_private_key(
                    synthetic.gateway_path,
                    parent_uid=os.getuid() + 1,
                    parent_gid=os.getgid(),
                )
            lifecycle.destroy(synthetic)
            self.assertFalse(synthetic.gateway_path.exists())
            self.assertFalse(synthetic.worker_path.exists())
            with self.assertRaisesRegex(StagingSupervisorError, "reused"):
                lifecycle.stage("synthetic")
            lifecycle.destroy_all()
            self.assertFalse(research.gateway_path.exists())
            self.assertFalse(research.worker_path.exists())

    def test_key_lifecycle_generations_match_gateway_keyring_rotation(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-key-generations-") as raw:
            root = Path(raw)
            directories = {
                name: root / name
                for name in (
                    "gateway-synthetic",
                    "worker-synthetic",
                    "gateway-research",
                    "worker-research",
                )
            }
            for directory in directories.values():
                directory.mkdir(mode=0o710)
            contracts = (
                WorkerKeyCopyContract(
                    role="synthetic",
                    gateway_directory=directories["gateway-synthetic"],
                    worker_directory=directories["worker-synthetic"],
                    gateway_uid=os.getuid(),
                    gateway_gid=os.getgid(),
                    worker_uid=os.getuid(),
                    worker_gid=os.getgid(),
                    gateway_parent_uid=os.getuid(),
                    gateway_parent_gid=os.getgid(),
                    worker_parent_uid=os.getuid(),
                    worker_parent_gid=os.getgid(),
                ),
                WorkerKeyCopyContract(
                    role="research",
                    gateway_directory=directories["gateway-research"],
                    worker_directory=directories["worker-research"],
                    gateway_uid=os.getuid(),
                    gateway_gid=os.getgid(),
                    worker_uid=os.getuid(),
                    worker_gid=os.getgid(),
                    gateway_parent_uid=os.getuid(),
                    gateway_parent_gid=os.getgid(),
                    worker_parent_uid=os.getuid(),
                    worker_parent_gid=os.getgid(),
                ),
            )
            values = iter(
                (
                    bytes.fromhex("11" * 32),
                    bytes.fromhex("22" * 32),
                    bytes.fromhex("33" * 32),
                )
            )
            lifecycle = WorkerKeyLifecycle(
                contracts, random_bytes=lambda _: next(values)
            )
            synthetic = lifecycle.stage("synthetic")
            research = lifecycle.stage("research")
            keyring = WorkerKeyring(
                {"synthetic": synthetic.key, "research": research.key},
                active_roles=frozenset({"synthetic", "research"}),
            )
            replacement = lifecycle.stage("synthetic")
            self.assertEqual(replacement.generation, 1)
            keyring.disable(role="synthetic", generation=0)
            keyring.prepare(
                role="synthetic",
                key=replacement.key,
                generation=replacement.generation,
            )
            keyring.commit(role="synthetic", generation=replacement.generation)
            self.assertEqual(keyring.current("synthetic"), (replacement.key, 1))
            lifecycle.destroy_all()

    def test_partial_key_copy_failure_burns_value_without_advancing_generation(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-key-burn-") as raw:
            root = Path(raw)
            directories = [root / name for name in ("gs", "ws", "gr", "wr")]
            for directory in directories:
                directory.mkdir(mode=0o710)
            lifecycle = WorkerKeyLifecycle(
                (
                    WorkerKeyCopyContract(
                        "synthetic",
                        directories[0],
                        directories[1],
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                    ),
                    WorkerKeyCopyContract(
                        "research",
                        directories[2],
                        directories[3],
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                    ),
                ),
                random_bytes=lambda _: KEY,
            )
            original = write_private_key_copy
            calls = 0

            def fail_second(
                path,
                *,
                key,
                uid,
                gid,
                parent_uid,
                parent_gid,
                on_created=None,
            ):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise StagingSupervisorError("injected second-copy failure")
                return original(
                    path,
                    key=key,
                    uid=uid,
                    gid=gid,
                    parent_uid=parent_uid,
                    parent_gid=parent_gid,
                    on_created=on_created,
                )

            with mock.patch(
                "procurement_os.staging_supervisor.write_private_key_copy",
                side_effect=fail_second,
            ):
                with self.assertRaisesRegex(StagingSupervisorError, "second-copy"):
                    lifecycle.stage("synthetic")
            self.assertFalse(any(directories[0].iterdir()))
            with self.assertRaisesRegex(StagingSupervisorError, "reused"):
                lifecycle.stage("synthetic")

    def test_partial_key_cleanup_failure_remains_owned_and_retryable(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-key-retry-") as raw:
            root = Path(raw)
            directories = [root / name for name in ("gs", "ws", "gr", "wr")]
            for directory in directories:
                directory.mkdir(mode=0o710)
            lifecycle = WorkerKeyLifecycle(
                (
                    WorkerKeyCopyContract(
                        "synthetic",
                        directories[0],
                        directories[1],
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                    ),
                    WorkerKeyCopyContract(
                        "research",
                        directories[2],
                        directories[3],
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                    ),
                ),
                random_bytes=lambda _: KEY,
            )
            real_unlink = staging_supervisor._unlink_incomplete_private_key
            cleanup_calls = 0

            def write_partial(path, **kwargs):
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                    0o600,
                )
                try:
                    info = os.fstat(descriptor)
                    kwargs["on_created"]((info.st_dev, info.st_ino))
                    os.write(descriptor, KEY[:5])
                finally:
                    os.close(descriptor)
                raise StagingSupervisorError("injected partial write")

            def fail_first_cleanup(path, **kwargs):
                nonlocal cleanup_calls
                cleanup_calls += 1
                if cleanup_calls == 1:
                    raise StagingSupervisorError("injected unlink failure")
                return real_unlink(path, **kwargs)

            with mock.patch(
                "procurement_os.staging_supervisor.write_private_key_copy",
                side_effect=write_partial,
            ), mock.patch(
                "procurement_os.staging_supervisor._unlink_incomplete_private_key",
                side_effect=fail_first_cleanup,
            ):
                with self.assertRaisesRegex(
                    StagingSupervisorError, "staging and cleanup failed"
                ):
                    lifecycle.stage("synthetic")
            self.assertTrue(any(directories[0].iterdir()))
            self.assertTrue(lifecycle._staged)
            lifecycle.destroy_all()
            self.assertFalse(any(directories[0].iterdir()))
            self.assertFalse(lifecycle._staged)

    def test_key_stage_preserves_preexisting_matching_file(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-key-existing-") as raw:
            root = Path(raw)
            directories = [root / name for name in ("gs", "ws", "gr", "wr")]
            for directory in directories:
                directory.mkdir(mode=0o710)
            values = iter((bytes.fromhex("a5" * 32), bytes.fromhex("b6" * 32)))
            lifecycle = WorkerKeyLifecycle(
                (
                    WorkerKeyCopyContract(
                        "synthetic",
                        directories[0],
                        directories[1],
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                    ),
                    WorkerKeyCopyContract(
                        "research",
                        directories[2],
                        directories[3],
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                        os.getuid(),
                        os.getgid(),
                    ),
                ),
                random_bytes=lambda _: next(values),
            )
            occupied = directories[0] / "synthetic-0.key"
            occupied.write_bytes(KEY)
            occupied.chmod(0o600)
            with self.assertRaisesRegex(
                StagingSupervisorError, "already occupied"
            ):
                lifecycle.stage("synthetic")
            self.assertEqual(occupied.read_bytes(), KEY)
            self.assertFalse(lifecycle._staged)

            raced_content = bytes.fromhex("c7" * 32)

            def create_racing_file(path, **_kwargs):
                path.write_bytes(raced_content)
                path.chmod(0o600)
                raise StagingSupervisorError("injected O_EXCL race")

            with mock.patch(
                "procurement_os.staging_supervisor.write_private_key_copy",
                side_effect=create_racing_file,
            ):
                with self.assertRaisesRegex(StagingSupervisorError, "O_EXCL race"):
                    lifecycle.stage("research")
            raced = directories[2] / "research-0.key"
            self.assertEqual(raced.read_bytes(), raced_content)
            self.assertFalse(lifecycle._staged)

    def test_nested_key_directories_are_refused(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-key-nested-") as raw:
            root = Path(raw)
            directories = {
                "gs": root / "gateway",
                "ws": root / "gateway" / "nested",
                "gr": root / "gateway-research",
                "wr": root / "worker-research",
            }
            for directory in directories.values():
                directory.mkdir(mode=0o710, parents=True, exist_ok=True)
                directory.chmod(0o710)
            contracts = (
                WorkerKeyCopyContract(
                    "synthetic",
                    directories["gs"],
                    directories["ws"],
                    os.getuid(),
                    os.getgid(),
                    os.getuid(),
                    os.getgid(),
                    os.getuid(),
                    os.getgid(),
                    os.getuid(),
                    os.getgid(),
                ),
                WorkerKeyCopyContract(
                    "research",
                    directories["gr"],
                    directories["wr"],
                    os.getuid(),
                    os.getgid(),
                    os.getuid(),
                    os.getgid(),
                    os.getuid(),
                    os.getgid(),
                    os.getuid(),
                    os.getgid(),
                ),
            )
            with self.assertRaisesRegex(StagingSupervisorError, "not isolated"):
                WorkerKeyLifecycle(contracts)


class SupervisorControlSocketTests(unittest.IsolatedAsyncioTestCase):
    def test_private_listener_refuses_symlinked_ancestor_before_mutation(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-listener-ancestor-") as raw:
            root = Path(raw)
            target = root / "target"
            parent = target / "socket-parent"
            parent.mkdir(parents=True)
            parent.chmod(0o750)
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            contract = SocketContract(
                path=alias / "socket-parent" / "worker.sock",
                parent_uid=os.getuid(),
                parent_gid=os.getgid(),
                socket_uid=os.getuid(),
                socket_gid=os.getgid(),
            )
            with self.assertRaisesRegex(StagingSupervisorError, "ancestor is a symlink"):
                bind_private_listener(contract)
            self.assertFalse((parent / "worker.sock").exists())

    def test_failed_bind_does_not_unlink_path_created_by_other_actor(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-listener-race-") as raw:
            parent = Path(raw)
            parent.chmod(0o750)
            path = parent / "worker.sock"
            contract = SocketContract(
                path=path,
                parent_uid=os.getuid(),
                parent_gid=os.getgid(),
                socket_uid=os.getuid(),
                socket_gid=os.getgid(),
            )
            fake_listener = mock.Mock()

            def collide(_path: str) -> None:
                path.touch(mode=0o600)
                raise OSError("injected bind collision")

            fake_listener.bind.side_effect = collide
            with mock.patch(
                "procurement_os.staging_supervisor.socket.socket",
                return_value=fake_listener,
            ):
                with self.assertRaisesRegex(OSError, "injected bind collision"):
                    bind_private_listener(contract)
            self.assertTrue(path.exists())
            fake_listener.close.assert_called_once_with()

    async def test_real_unix_control_socket_uses_kernel_peer_and_one_frame(self):
        hooks = _Hooks()
        protocol = SupervisorControlProtocol(
            key=KEY,
            expected_peer=PeerCredentials(
                pid=os.getpid(), uid=os.getuid(), gid=os.getgid()
            ),
            hooks=hooks,
        )
        handler = SupervisorControlServer(protocol)
        temporary = tempfile.TemporaryDirectory(prefix="buffalo-control-uds-")
        self.addAsyncCleanup(asyncio.to_thread, temporary.cleanup)
        parent = Path(temporary.name)
        parent.chmod(0o750)
        path = parent / "control.sock"
        contract = SocketContract(
            path=path,
            parent_uid=os.getuid(),
            parent_gid=os.getgid(),
            socket_uid=os.getuid(),
            socket_gid=os.getgid(),
        )
        listener = bind_private_listener(contract)
        self.addCleanup(listener.close)
        server = await asyncio.start_unix_server(handler.handle, sock=listener)
        self.addAsyncCleanup(self._close_server, server)
        reader, writer = await asyncio.open_unix_connection(str(path))
        message = mint_control_message(
            key=KEY,
            operation="research-status",
            issued_at=float(int(time.time())),
        )
        writer.write(struct.pack("!I", len(message)) + message)
        await writer.drain()
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        response = json.loads(await reader.readexactly(size))
        self.assertEqual(response["state"], "READY")
        self.assertEqual(hooks.calls, ["status"])
        self.assertEqual(await reader.read(), b"")
        writer.close()
        await writer.wait_closed()

        client = SupervisorControlClient(
            contract=contract,
            key=KEY,
            expected_supervisor_peer=PeerCredentials(
                pid=os.getpid(), uid=os.getuid(), gid=os.getgid()
            ),
        )
        status = await client.request("research-status")
        self.assertEqual(status, ControlStatus("READY", 1))
        self.assertEqual(hooks.calls, ["status", "status"])

    @staticmethod
    async def _close_server(server: asyncio.AbstractServer) -> None:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
