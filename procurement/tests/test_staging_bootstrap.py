"""Root staging composition, startup barrier, and cleanup tests."""
from __future__ import annotations

import asyncio
from pathlib import Path
import os
from types import SimpleNamespace
import stat
import tempfile
import unittest
from unittest import mock

from procurement_os import staging_bootstrap as bootstrap
from procurement_os.staging_access import generate_owner_credential
from procurement_os.staging_config import (
    EXPECTED_APP_SERVICE_ID,
    EXPECTED_ENVIRONMENT_ID,
    EXPECTED_POSTGRES_SERVICE_ID,
    EXPECTED_PROJECT_ID,
)
from procurement_os.staging_process_contract import (
    GATEWAY_ENVIRONMENT_NAMES,
    OPTIONAL_CERTIFICATE_ENVIRONMENT_NAMES,
    WORKER_ENVIRONMENT_NAMES,
)
from procurement_os.staging_supervisor import ControlStatus


class StagingBootstrapContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_verifier = generate_owner_credential().verifier

    def _environment(self) -> dict[str, str]:
        return {
            "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
            "BUFFALO_STAGING_ENABLED": "1",
            "BUFFALO_STAGING_EXPECTED_COMMIT": "a" * 40,
            "BUFFALO_STAGING_EXTERNAL_HOST": "staging.example.test",
            "BUFFALO_STAGING_OWNER_VERIFIER": self.owner_verifier,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "postgres.railway.internal",
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
            "BUFFALO_STAGING_REPLICA_COUNT": "1",
            "BUFFALO_STAGING_SYNTHETIC_DATABASE_URL": (
                "postgresql://buffalo_synthetic_runtime@"
                "postgres.railway.internal/buffalo_synthetic_staging_demo"
            ),
            "BUFFALO_STAGING_SYNTHETIC_DATABASE_PASSWORD": "fixture:secret\\value",
            "BUFFALO_STAGING_VOLUME_ROOT": "/data",
            "PORT": "8080",
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_REPLICA_ID": "replica-01",
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
        }

    def _inputs(self) -> bootstrap.StagingBootstrapInputs:
        return bootstrap.load_bootstrap_inputs(self._environment())[0]

    def test_root_inputs_are_selected_without_secret_rendering(self):
        environment = {
            **self._environment(),
            "UNRELATED_RAILWAY_METADATA": "ignored",
        }
        inputs, password = bootstrap.load_bootstrap_inputs(environment)
        self.assertEqual(password, "fixture:secret\\value")
        self.assertEqual(inputs.database_url, environment[bootstrap.DATABASE_URL_ENV])
        self.assertNotIn(password, repr(inputs))
        self.assertNotIn(self.owner_verifier, repr(inputs))
        self.assertNotIn("UNRELATED_RAILWAY_METADATA", repr(inputs))
        consumed = self._environment()

        def refuse_prepare(**arguments):
            self.assertEqual(
                arguments["database_password"], "fixture:secret\\value"
            )
            self.assertNotIn(bootstrap.DATABASE_PASSWORD_ENV, consumed)
            raise RuntimeError("fixture stop")

        with mock.patch.object(
            bootstrap, "prepare_bootstrap", side_effect=refuse_prepare
        ), self.assertRaisesRegex(RuntimeError, "fixture stop"):
            bootstrap.run_bootstrap(consumed)
        self.assertNotIn(bootstrap.DATABASE_PASSWORD_ENV, consumed)

    def test_root_inputs_reject_ambient_authority_and_scope_drift(self):
        changes = (
            ("SHOPIFY_ACCESS_TOKEN", "not-accepted"),
            ("DATABASE_URL", "postgresql://ambient"),
            ("REDIS_URL", "redis://user:credential@example.invalid/0"),
            ("UNRELATED_SERVICE_SECRET", "not-accepted"),
            ("BUFFALO_STAGING_REPLICA_COUNT", "2"),
            ("RAILWAY_GIT_COMMIT_SHA", "b" * 40),
            ("RAILWAY_PROJECT_ID", "wrong-project"),
        )
        for name, value in changes:
            with self.subTest(name=name), self.assertRaises(
                bootstrap.StagingBootstrapError
            ):
                bootstrap.load_bootstrap_inputs(
                    {**self._environment(), name: value}
                )

    def test_local_database_authority_is_exact_and_paired(self):
        remote = self._environment()
        local_url = (
            "postgresql://buffalo_synthetic_runtime@127.0.0.1:55432/"
            "buffalo_synthetic_staging_demo"
        )
        local = {
            **remote,
            bootstrap.DATABASE_URL_ENV: local_url,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": "55432",
        }
        inputs, _ = bootstrap.load_bootstrap_inputs(local)
        self.assertEqual(inputs.local_port, "55432")
        for missing in (
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE",
            "BUFFALO_STAGING_OWNED_LOCAL_PORT",
        ):
            changed = dict(local)
            changed.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(
                bootstrap.StagingBootstrapError
            ):
                bootstrap.load_bootstrap_inputs(changed)

    def test_layout_is_fixed_and_separates_ephemeral_and_persistent_roots(self):
        layout = bootstrap.StagingBootstrapLayout.build(
            volume_root=Path("/data"),
            runtime_root=Path("/run/buffalo-staging"),
        )
        self.assertEqual(
            layout.synthetic_socket,
            Path("/run/buffalo-staging/sockets/synthetic/worker.sock"),
        )
        self.assertEqual(
            layout.pgpass,
            Path("/run/buffalo-staging/synthetic/private/pgpass"),
        )
        self.assertEqual(layout.synthetic_storage, Path("/data/synthetic"))
        self.assertEqual(layout.research_release, Path("/data/research-release"))
        self.assertEqual(layout.transfer_root, Path("/data/transfer"))
        runtime_paths = {
            layout.supervisor_root,
            layout.gateway_root,
            layout.synthetic_root,
            layout.research_root,
        }
        self.assertEqual(len(runtime_paths), 4)
        self.assertTrue(
            all(not path.is_relative_to(layout.volume_root) for path in runtime_paths)
        )
        for volume_root in (
            Path("/run"),
            Path("/run/buffalo-staging"),
            Path("/run/buffalo-staging/durable"),
        ):
            with self.subTest(volume_root=volume_root), self.assertRaisesRegex(
                bootstrap.StagingBootstrapError, "roots overlap"
            ):
                bootstrap._validate_layout_root_separation(
                    bootstrap.StagingBootstrapLayout.build(
                        volume_root=volume_root,
                        runtime_root=Path("/run/buffalo-staging"),
                    )
                )
        with tempfile.TemporaryDirectory(prefix="buffalo-mountinfo-") as raw:
            mountinfo = Path(raw) / "mountinfo"
            mountinfo.write_text(
                "42 31 8:1 / /data rw,relatime - ext4 /dev/volume rw\n",
                encoding="ascii",
            )
            bootstrap._validate_volume_mount(
                Path("/data"),
                local_acceptance=False,
                _mountinfo_path=mountinfo,
            )
            mountinfo.write_text(
                "42 31 0:9 / /data rw,relatime - overlay overlay rw\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                bootstrap.StagingBootstrapError, "mount contract differs"
            ):
                bootstrap._validate_volume_mount(
                    Path("/data"),
                    local_acceptance=False,
                    _mountinfo_path=mountinfo,
                )
            bootstrap._validate_volume_mount(
                Path("/data"),
                local_acceptance=True,
                _mountinfo_path=mountinfo,
            )

    def test_child_contracts_bind_exact_identities_groups_fds_and_environments(self):
        layout = bootstrap.StagingBootstrapLayout.build(volume_root=Path("/data"))
        contracts = bootstrap.build_child_contracts(
            inputs=self._inputs(),
            layout=layout,
            python_executable="/usr/bin/python3",
            activation_fd=10,
            synthetic_listener_fd=11,
            research_listener_fd=12,
            supervisor_pid=os.getpid(),
        )
        specs = {
            spec.name: spec
            for spec in (
                contracts.gateway_spec,
                contracts.synthetic_spec,
                contracts.research_spec,
            )
        }
        self.assertEqual(specs["gateway"].uid, 1101)
        self.assertEqual(specs["gateway"].extra_groups, (2301, 2302, 2303))
        self.assertEqual(specs["synthetic"].extra_groups, (2301,))
        self.assertEqual(specs["research"].extra_groups, (2302,))
        self.assertEqual(specs["gateway"].pass_fds, (10,))
        self.assertEqual(specs["synthetic"].pass_fds, (11,))
        self.assertEqual(specs["research"].pass_fds, (12,))
        environments = {
            role: dict(spec.environment) for role, spec in specs.items()
        }
        self.assertEqual(
            set(environments["gateway"]),
            GATEWAY_ENVIRONMENT_NAMES - OPTIONAL_CERTIFICATE_ENVIRONMENT_NAMES,
        )
        # The image uses its fixed system trust store, not ambient paths.
        self.assertEqual(
            set(environments["synthetic"]),
            WORKER_ENVIRONMENT_NAMES["synthetic"]
            - {
                "BUFFALO_STAGING_SYNTHETIC_READINESS_FD",
                "BUFFALO_STAGING_LOCAL_ACCEPTANCE",
                "BUFFALO_STAGING_OWNED_LOCAL_PORT",
            }
            - OPTIONAL_CERTIFICATE_ENVIRONMENT_NAMES,
        )
        self.assertEqual(
            set(environments["research"]),
            WORKER_ENVIRONMENT_NAMES["research"]
            - {"BUFFALO_STAGING_RESEARCH_READINESS_FD"}
            - OPTIONAL_CERTIFICATE_ENVIRONMENT_NAMES,
        )
        self.assertIn("DATABASE_URL", environments["synthetic"])
        self.assertNotIn("DATABASE_URL", environments["gateway"])
        self.assertNotIn("DATABASE_URL", environments["research"])
        self.assertIn("BUFFALO_RESEARCH_ROOT", environments["research"])
        self.assertNotIn("BUFFALO_RESEARCH_ROOT", environments["synthetic"])
        self.assertTrue(
            all(
                bootstrap.DATABASE_PASSWORD_ENV not in environment
                and "fixture:secret" not in "\n".join(environment.values())
                for environment in environments.values()
            )
        )
        contracts.isolation.validate_children(tuple(specs.values()))

    def test_child_descriptor_reuse_and_foreign_supervisor_pid_are_refused(self):
        arguments = {
            "inputs": self._inputs(),
            "layout": bootstrap.StagingBootstrapLayout.build(
                volume_root=Path("/data")
            ),
            "python_executable": "/usr/bin/python3",
            "activation_fd": 10,
            "synthetic_listener_fd": 11,
            "research_listener_fd": 12,
            "supervisor_pid": os.getpid(),
        }
        for changes in (
            {"research_listener_fd": 11},
            {"activation_fd": 2},
            {"supervisor_pid": os.getpid() + 1},
        ):
            with self.subTest(changes=changes), self.assertRaises(
                bootstrap.StagingBootstrapError
            ):
                bootstrap.build_child_contracts(**{**arguments, **changes})

    def test_pgpass_is_one_escaped_private_record_and_cleanup_is_exact(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-bootstrap-pgpass-") as raw:
            parent = Path(raw) / "private"
            parent.mkdir(mode=0o700)
            parent.chmod(0o700)
            path = parent / "pgpass"
            resources = bootstrap.BootstrapResources()
            bootstrap._write_pgpass(
                path,
                database_url=(
                    "postgresql://buffalo_synthetic_runtime@127.0.0.1:55432/"
                    "buffalo_synthetic_staging_demo"
                ),
                password="fixture:secret\\value",
                uid=os.getuid(),
                gid=os.getgid(),
                resources=resources,
            )
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "127.0.0.1:55432:buffalo_synthetic_staging_demo:"
                "buffalo_synthetic_runtime:fixture\\:secret\\\\value\n",
            )
            info = path.stat(follow_symlinks=False)
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            self.assertEqual(info.st_nlink, 1)
            resources.cleanup()
            self.assertFalse(path.exists())

    def test_pgpass_post_create_failures_remain_exactly_cleanup_owned(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-bootstrap-pgfail-") as raw:
            parent = Path(raw) / "private"
            parent.mkdir(mode=0o700)
            parent.chmod(0o700)
            original_close = os.close

            def close_then_fail(descriptor: int) -> None:
                original_close(descriptor)
                raise OSError("fixture close failure")

            for case in ("close", "fstat", "finalize"):
                with self.subTest(case=case):
                    path = parent / f"pgpass-{case}"
                    resources = bootstrap.BootstrapResources()
                    patches = {
                        "close": mock.patch.object(
                            bootstrap.os, "close", side_effect=close_then_fail
                        ),
                        "fstat": mock.patch.object(
                            bootstrap.os, "fstat", side_effect=OSError("fixture fstat")
                        ),
                        "finalize": mock.patch.object(
                            bootstrap,
                            "_finalize_owned_file",
                            side_effect=RuntimeError("fixture finalize"),
                        ),
                    }
                    with patches[case], self.assertRaises(BaseException):
                        bootstrap._write_pgpass(
                            path,
                            database_url=(
                                "postgresql://buffalo_synthetic_runtime@"
                                "127.0.0.1:55432/"
                                "buffalo_synthetic_staging_demo"
                            ),
                            password="fixture-secret",
                            uid=os.getuid(),
                            gid=os.getgid(),
                            resources=resources,
                        )
                    self.assertEqual(len(resources.created_files), 1)
                    resources.cleanup()
                    self.assertFalse(path.exists())

    def test_management_key_post_create_failures_never_strand_the_key(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-bootstrap-keyfail-") as raw:
            parent = Path(raw) / "keys"
            parent.mkdir(mode=0o710)
            parent.chmod(0o710)
            original_close = os.close

            def close_then_fail(descriptor: int) -> None:
                original_close(descriptor)
                raise OSError("fixture close failure")

            for case in ("close", "fstat", "capture"):
                with self.subTest(case=case):
                    path = parent / f"control-{case}.key"
                    resources = bootstrap.BootstrapResources()
                    patches = {
                        "close": mock.patch.object(
                            bootstrap.os, "close", side_effect=close_then_fail
                        ),
                        "fstat": mock.patch.object(
                            bootstrap.os, "fstat", side_effect=OSError("fixture fstat")
                        ),
                        "capture": mock.patch.object(
                            bootstrap,
                            "_capture_node",
                            side_effect=RuntimeError("fixture capture"),
                        ),
                    }
                    with patches[case], self.assertRaises(BaseException):
                        bootstrap._write_management_key(
                            path,
                            key=b"m" * 32,
                            uid=os.getuid(),
                            gid=os.getgid(),
                            parent_uid=os.getuid(),
                            parent_gid=os.getgid(),
                            resources=resources,
                        )
                    self.assertEqual(len(resources.created_files), 1)
                    resources.cleanup()
                    self.assertFalse(path.exists())

    def test_exact_inode_cleanup_preserves_a_replacement(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-bootstrap-owned-") as raw:
            path = Path(raw) / "owned"
            path.write_bytes(b"first")
            path.chmod(0o600)
            node = bootstrap._capture_node(
                path,
                kind="file",
                mode=0o600,
                uid=os.getuid(),
                gid=os.getgid(),
                size=5,
            )
            path.unlink()
            path.write_bytes(b"other")
            path.chmod(0o600)
            resources = bootstrap.BootstrapResources(created_files=[node])
            with self.assertRaisesRegex(
                bootstrap.StagingBootstrapError, "could not be cleaned"
            ):
                resources.cleanup()
            self.assertEqual(path.read_bytes(), b"other")
            self.assertEqual(resources.created_files, [node])
            self.assertFalse(resources._cleaned)

    def test_listener_bind_failure_cleans_every_prior_socket(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-bootstrap-listener-") as raw:
            root = Path(raw)
            contracts: dict[str, object] = {}
            for role in ("synthetic", "research", "control"):
                parent = root / role
                parent.mkdir(mode=0o750)
                contracts[role] = bootstrap.SocketContract(
                    path=parent / "worker.sock",
                    parent_uid=os.getuid(),
                    parent_gid=os.getgid(),
                    socket_uid=os.getuid(),
                    socket_gid=os.getgid(),
                )
            resources = bootstrap.BootstrapResources()
            original_bind = bootstrap.bind_private_listener
            calls = 0

            def fail_second(contract):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("fixture listener bind failure")
                return original_bind(contract)

            with mock.patch.object(
                bootstrap, "bind_private_listener", side_effect=fail_second
            ), self.assertRaisesRegex(RuntimeError, "listener bind failure"):
                bootstrap._bind_bootstrap_listeners(
                    contracts,
                    resources=resources,
                )
            self.assertTrue(contracts["synthetic"].path.exists())
            resources.cleanup()
            self.assertFalse(contracts["synthetic"].path.exists())
            self.assertEqual(resources.listeners, [])
            self.assertEqual(resources.created_sockets, [])

            capture_resources = bootstrap.BootstrapResources()
            with mock.patch.object(
                bootstrap,
                "_capture_node",
                side_effect=RuntimeError("fixture listener capture failure"),
            ), self.assertRaisesRegex(RuntimeError, "listener capture failure"):
                bootstrap._bind_bootstrap_listeners(
                    contracts,
                    resources=capture_resources,
                )
            self.assertTrue(contracts["synthetic"].path.exists())
            capture_resources.cleanup()
            self.assertFalse(contracts["synthetic"].path.exists())

    def test_root_account_and_socket_group_inventory_is_exact(self):
        runtime = Path("/run/buffalo-staging")
        users = {
            account.name: SimpleNamespace(
                pw_uid=account.uid,
                pw_gid=account.gid,
                pw_dir=str(runtime / account.name.removeprefix("buffalo-")),
                pw_shell="/usr/sbin/nologin",
            )
            for account in bootstrap.PROCESS_ACCOUNTS[1:]
        }
        root_user = SimpleNamespace(
            pw_name="root",
            pw_uid=0,
            pw_gid=0,
            pw_dir="/root",
            pw_shell="/bin/sh",
        )
        for name, record in users.items():
            record.pw_name = name
        groups = {
            name: SimpleNamespace(gr_gid=gid, gr_mem=sorted(members))
            for name, (gid, members) in bootstrap.SOCKET_GROUPS.items()
        }
        primary_groups = {
            account.name: SimpleNamespace(
                gr_name=account.name,
                gr_gid=account.gid,
                gr_mem=[],
            )
            for account in bootstrap.PROCESS_ACCOUNTS
        }
        for name, record in groups.items():
            record.gr_name = name
        group_inventory = [*primary_groups.values(), *groups.values()]
        passwd_inventory = [root_user, *users.values()]
        with mock.patch.object(bootstrap.os, "geteuid", return_value=0), mock.patch.object(
            bootstrap.os, "getegid", return_value=0
        ), mock.patch.object(
            bootstrap.pwd, "getpwnam", side_effect=users.__getitem__
        ), mock.patch.object(
            bootstrap.pwd, "getpwall", return_value=passwd_inventory
        ), mock.patch.object(
            bootstrap.grp, "getgrnam", side_effect=groups.__getitem__
        ), mock.patch.object(
            bootstrap.grp, "getgrall", return_value=group_inventory
        ):
            bootstrap._validate_root_identity_and_accounts(runtime_root=runtime)
            groups["buffalo-control-socket"].gr_mem.append("buffalo-research")
            with self.assertRaisesRegex(
                bootstrap.StagingBootstrapError, "group membership differs"
            ):
                bootstrap._validate_root_identity_and_accounts(runtime_root=runtime)

            groups["buffalo-control-socket"].gr_mem.pop()
            duplicate_uid = SimpleNamespace(
                pw_name="foreign-user",
                pw_uid=bootstrap.SYNTHETIC_ACCOUNT.uid,
                pw_gid=9999,
            )
            passwd_inventory.append(duplicate_uid)
            with self.assertRaisesRegex(
                bootstrap.StagingBootstrapError, "UID is not unique"
            ):
                bootstrap._validate_root_identity_and_accounts(runtime_root=runtime)
            passwd_inventory.pop()

            primary_socket_member = SimpleNamespace(
                pw_name="foreign-user",
                pw_uid=9999,
                pw_gid=bootstrap.SYNTHETIC_SOCKET_GROUP,
            )
            passwd_inventory.append(primary_socket_member)
            with self.assertRaisesRegex(
                bootstrap.StagingBootstrapError, "primary group membership differs"
            ):
                bootstrap._validate_root_identity_and_accounts(runtime_root=runtime)
            passwd_inventory.pop()

            alias = SimpleNamespace(
                gr_name="socket-alias",
                gr_gid=bootstrap.RESEARCH_SOCKET_GROUP,
                gr_mem=[],
            )
            group_inventory.append(alias)
            with self.assertRaisesRegex(
                bootstrap.StagingBootstrapError, "GID is not unique"
            ):
                bootstrap._validate_root_identity_and_accounts(runtime_root=runtime)

    def test_research_control_gate_cannot_start_before_synthetic_ready(self):
        class Hooks:
            def __init__(self) -> None:
                self.starts = 0

            def status(self) -> ControlStatus:
                return ControlStatus("STOPPED", 4)

            def start(self) -> ControlStatus:
                self.starts += 1
                return ControlStatus("VALIDATING", 5, 1)

            def stop(self) -> ControlStatus:
                return ControlStatus("STOPPED", 5)

        hooks = Hooks()
        gate = bootstrap._ResearchHooksGate(hooks)
        self.assertEqual(gate.start(), ControlStatus("STOPPED", 4, 1))
        self.assertEqual(hooks.starts, 0)
        gate.open()
        self.assertEqual(gate.start(), ControlStatus("VALIDATING", 5, 1))
        self.assertEqual(hooks.starts, 1)


class _FakeControlServer:
    def __init__(self, events: list[str], *, close_failure: bool = False) -> None:
        self.events = events
        self.close_failure = close_failure

    async def start_serving(self) -> None:
        self.events.append("control-start")

    def close(self) -> None:
        self.events.append("control-close")
        if self.close_failure:
            raise RuntimeError("fixture control close failure")

    async def wait_closed(self) -> None:
        self.events.append("control-closed")


class _FakeManager:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def start(self) -> ControlStatus:
        self.events.append("research-start")
        return ControlStatus("VALIDATING", 1, 1)

    def stop(self) -> ControlStatus:
        return ControlStatus("STOPPED", 1)

    def status(self) -> ControlStatus:
        return ControlStatus("STOPPED", 0)


class _FakeRunner:
    def __init__(
        self,
        events: list[str],
        *,
        start_failure: bool = False,
        shutdown_failure: bool = False,
        shutdown_callback=None,
    ) -> None:
        self.events = events
        self.start_failure = start_failure
        self.shutdown_failure = shutdown_failure
        self.shutdown_callback = shutdown_callback

    def start(self) -> None:
        self.events.append("runner-start")
        if self.start_failure:
            raise RuntimeError("fixture runner partial startup failure")

    def check(self) -> None:
        self.events.append("runner-check")

    def shutdown(self) -> None:
        self.events.append("runner-shutdown")
        if self.shutdown_callback is not None:
            self.shutdown_callback()
        if self.shutdown_failure:
            raise RuntimeError("fixture runner shutdown failure")


class _FakeResources:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.readiness_callback = None
        self.activation_client = SimpleNamespace(
            await_gateway_ready=self._await_gateway_ready
        )

    def _await_gateway_ready(self, **arguments) -> None:
        self.events.append("gateway-ready")
        if self.readiness_callback is not None:
            self.readiness_callback()
        if arguments["should_abort"]() or not arguments["peer_is_live"]():
            raise RuntimeError("fixture gateway readiness interrupted")

    def reserve_closed_gateway_descriptor(self) -> None:
        self.events.append("activation-reserved")

    def cleanup(self) -> None:
        self.events.append("resources-cleanup")


class _FakeSupervisor:
    def __init__(
        self, events: list[str], *, crash: bool, shutdown_failure: bool = False
    ) -> None:
        self.events = events
        self.crash = crash
        self.shutdown_failure = shutdown_failure
        self.signal_callback = None
        self.gateway_live = True

    def start(self, role: str):
        self.events.append(f"start-{role}")
        return SimpleNamespace(
            process=SimpleNamespace(poll=lambda: None),
            identity=SimpleNamespace(pid=4321, is_live=lambda: self.gateway_live),
        )

    def start_synthetic(self, generation: int):
        self.events.append("start-synthetic")
        return SimpleNamespace(generation=generation)

    def await_synthetic_ready(self, generation: int):
        self.events.append("synthetic-ready")
        return SimpleNamespace(generation=generation, ready=True)

    def reap_crashed(self):
        self.events.append("reap")
        if self.signal_callback is not None:
            callback = self.signal_callback
            self.signal_callback = None
            callback()
        return ("gateway",) if self.crash else ()

    def signal_handler(self, signum: int, _frame=None) -> None:
        self.events.append(f"signal-{signum}")

    def process_pending_signal(self) -> bool:
        self.events.append("signal-drained")
        return True

    def shutdown(self) -> None:
        self.events.append("supervisor-shutdown")
        if self.shutdown_failure:
            raise RuntimeError("fixture shutdown failure")


class StagingBootstrapLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def _composition(
        self,
        events: list[str],
        *,
        crash: bool,
        shutdown_failure: bool = False,
    ):
        supervisor = _FakeSupervisor(
            events,
            crash=crash,
            shutdown_failure=shutdown_failure,
        )
        composition = bootstrap.StagingBootstrapComposition(
            inputs=SimpleNamespace(),
            layout=SimpleNamespace(
                research_release=Path("/data/research-release"),
                validation_parent=Path("/run/buffalo-staging/supervisor/validation"),
            ),
            resources=_FakeResources(events),
            contracts=SimpleNamespace(),
            supervisor=supervisor,
            management_key=b"m" * 32,
            control_listener=SimpleNamespace(),
        )
        runtime = SimpleNamespace(
            manager=_FakeManager(events),
            runner=_FakeRunner(events),
        )
        return composition, supervisor, runtime

    async def test_mandatory_child_crash_is_terminal_and_reverse_cleanup_runs(self):
        events: list[str] = []
        composition, _supervisor, runtime = self._composition(events, crash=True)

        async def start_server(*_args, **_kwargs):
            return _FakeControlServer(events)

        with mock.patch.object(
            bootstrap.asyncio, "start_unix_server", side_effect=start_server
        ):
            with self.assertRaisesRegex(
                bootstrap.StagingBootstrapError, "mandatory staging child crashed"
            ):
                await bootstrap.run_composition(
                    composition,
                    research_runtime_factory=lambda **_kwargs: runtime,
                    monitor_interval_seconds=0.01,
                )
        self.assertLess(events.index("start-gateway"), events.index("runner-start"))
        self.assertLess(events.index("gateway-ready"), events.index("runner-start"))
        self.assertLess(events.index("runner-start"), events.index("start-synthetic"))
        self.assertLess(events.index("start-synthetic"), events.index("synthetic-ready"))
        self.assertLess(events.index("control-close"), events.index("runner-shutdown"))
        self.assertLess(events.index("runner-shutdown"), events.index("supervisor-shutdown"))
        self.assertLess(events.index("supervisor-shutdown"), events.index("resources-cleanup"))

        retained_events: list[str] = []
        retained, _supervisor, retained_runtime = self._composition(
            retained_events,
            crash=True,
            shutdown_failure=True,
        )

        async def retained_server(*_args, **_kwargs):
            return _FakeControlServer(retained_events)

        with mock.patch.object(
            bootstrap.asyncio,
            "start_unix_server",
            side_effect=retained_server,
        ):
            with self.assertRaisesRegex(
                bootstrap.StagingBootstrapError,
                "cleanup did not complete",
            ):
                await bootstrap.run_composition(
                    retained,
                    research_runtime_factory=lambda **_kwargs: retained_runtime,
                    monitor_interval_seconds=0.01,
                )
        self.assertNotIn("resources-cleanup", retained_events)

    async def test_signal_after_readiness_drains_through_ordered_shutdown(self):
        events: list[str] = []
        composition, supervisor, runtime = self._composition(events, crash=False)
        loop = asyncio.get_running_loop()
        handlers: dict[int, tuple[object, tuple[object, ...]]] = {}

        def add_handler(signum, callback, *arguments):
            handlers[int(signum)] = (callback, arguments)

        async def start_server(*_args, **_kwargs):
            return _FakeControlServer(events)

        def trigger_signal() -> None:
            callback, arguments = handlers[int(bootstrap.signal.SIGTERM)]
            callback(*arguments)

        supervisor.signal_callback = trigger_signal
        with mock.patch.object(loop, "add_signal_handler", side_effect=add_handler), mock.patch.object(
            loop, "remove_signal_handler", return_value=True
        ), mock.patch.object(
            bootstrap.asyncio, "start_unix_server", side_effect=start_server
        ):
            await bootstrap.run_composition(
                composition,
                research_runtime_factory=lambda **_kwargs: runtime,
                monitor_interval_seconds=0.01,
            )
        self.assertIn(f"signal-{int(bootstrap.signal.SIGTERM)}", events)
        self.assertIn("signal-drained", events)
        self.assertNotIn("supervisor-shutdown", events)
        self.assertLess(events.index("runner-shutdown"), events.index("signal-drained"))
        self.assertLess(events.index("signal-drained"), events.index("resources-cleanup"))

    async def test_repeated_signal_during_runner_shutdown_cannot_interrupt_cleanup(self):
        events: list[str] = []
        composition, supervisor, runtime = self._composition(events, crash=False)
        loop = asyncio.get_running_loop()
        handlers: dict[int, tuple[object, tuple[object, ...]]] = {}

        def add_handler(signum, callback, *arguments):
            handlers[int(signum)] = (callback, arguments)

        def emit_sigterm() -> None:
            callback, arguments = handlers[int(bootstrap.signal.SIGTERM)]
            callback(*arguments)

        runtime.runner = _FakeRunner(events, shutdown_callback=emit_sigterm)
        supervisor.signal_callback = emit_sigterm

        async def start_server(*_args, **_kwargs):
            return _FakeControlServer(events)

        with mock.patch.object(
            loop, "add_signal_handler", side_effect=add_handler
        ), mock.patch.object(
            loop,
            "remove_signal_handler",
            side_effect=lambda signum: events.append(f"remove-{int(signum)}") or True,
        ), mock.patch.object(
            bootstrap.asyncio, "start_unix_server", side_effect=start_server
        ):
            await bootstrap.run_composition(
                composition,
                research_runtime_factory=lambda **_kwargs: runtime,
                monitor_interval_seconds=0.01,
            )
        self.assertEqual(
            events.count(f"signal-{int(bootstrap.signal.SIGTERM)}"),
            2,
        )
        self.assertLess(
            events.index("resources-cleanup"),
            events.index(f"remove-{int(bootstrap.signal.SIGINT)}"),
        )

    async def test_gateway_death_or_signal_during_ready_barrier_starts_no_worker(self):
        for reason in ("death", "signal"):
            with self.subTest(reason=reason):
                events: list[str] = []
                composition, supervisor, runtime = self._composition(
                    events, crash=False
                )
                loop = asyncio.get_running_loop()
                handlers: dict[int, tuple[object, tuple[object, ...]]] = {}

                def add_handler(signum, callback, *arguments):
                    handlers[int(signum)] = (callback, arguments)

                if reason == "death":
                    supervisor.gateway_live = False
                else:
                    def emit_sigterm() -> None:
                        callback, arguments = handlers[int(bootstrap.signal.SIGTERM)]
                        callback(*arguments)

                    composition.resources.readiness_callback = emit_sigterm

                with mock.patch.object(
                    loop, "add_signal_handler", side_effect=add_handler
                ), mock.patch.object(
                    loop, "remove_signal_handler", return_value=True
                ):
                    with self.assertRaisesRegex(
                        RuntimeError, "gateway readiness interrupted"
                    ):
                        await bootstrap.run_composition(
                            composition,
                            research_runtime_factory=lambda **_kwargs: runtime,
                            monitor_interval_seconds=0.01,
                        )
                self.assertNotIn("runner-start", events)
                self.assertNotIn("start-synthetic", events)
                self.assertIn("resources-cleanup", events)
                if reason == "signal":
                    self.assertIn("signal-drained", events)
                else:
                    self.assertIn("supervisor-shutdown", events)

    async def test_signal_handler_removal_failure_cannot_skip_terminal_cleanup(self):
        events: list[str] = []
        composition, _supervisor, runtime = self._composition(events, crash=True)
        loop = asyncio.get_running_loop()

        async def start_server(*_args, **_kwargs):
            return _FakeControlServer(events)

        with mock.patch.object(
            loop, "add_signal_handler", return_value=None
        ), mock.patch.object(
            loop,
            "remove_signal_handler",
            side_effect=RuntimeError("fixture signal removal failure"),
        ), mock.patch.object(
            bootstrap.asyncio, "start_unix_server", side_effect=start_server
        ):
            with self.assertRaisesRegex(
                bootstrap.StagingBootstrapError, "cleanup did not complete"
            ):
                await bootstrap.run_composition(
                    composition,
                    research_runtime_factory=lambda **_kwargs: runtime,
                    monitor_interval_seconds=0.01,
                )
        self.assertIn("control-close", events)
        self.assertIn("runner-shutdown", events)
        self.assertIn("supervisor-shutdown", events)
        self.assertIn("resources-cleanup", events)

    async def test_control_server_close_failure_cannot_skip_terminal_cleanup(self):
        events: list[str] = []
        composition, _supervisor, runtime = self._composition(events, crash=True)
        loop = asyncio.get_running_loop()

        async def start_server(*_args, **_kwargs):
            return _FakeControlServer(events, close_failure=True)

        with mock.patch.object(
            loop, "add_signal_handler", return_value=None
        ), mock.patch.object(
            loop, "remove_signal_handler", return_value=True
        ), mock.patch.object(
            bootstrap.asyncio, "start_unix_server", side_effect=start_server
        ):
            with self.assertRaisesRegex(
                bootstrap.StagingBootstrapError, "cleanup did not complete"
            ):
                await bootstrap.run_composition(
                    composition,
                    research_runtime_factory=lambda **_kwargs: runtime,
                    monitor_interval_seconds=0.01,
                )
        self.assertIn("control-closed", events)
        self.assertIn("runner-shutdown", events)
        self.assertIn("supervisor-shutdown", events)
        self.assertNotIn("resources-cleanup", events)

    async def test_runner_shutdown_uncertainty_retains_runtime_resources(self):
        events: list[str] = []
        composition, _supervisor, runtime = self._composition(events, crash=True)
        runtime.runner = _FakeRunner(events, shutdown_failure=True)
        loop = asyncio.get_running_loop()

        async def start_server(*_args, **_kwargs):
            return _FakeControlServer(events)

        with mock.patch.object(
            loop, "add_signal_handler", return_value=None
        ), mock.patch.object(
            loop, "remove_signal_handler", return_value=True
        ), mock.patch.object(
            bootstrap.asyncio, "start_unix_server", side_effect=start_server
        ):
            with self.assertRaisesRegex(
                bootstrap.StagingBootstrapError, "cleanup did not complete"
            ):
                await bootstrap.run_composition(
                    composition,
                    research_runtime_factory=lambda **_kwargs: runtime,
                    monitor_interval_seconds=0.01,
                )
        self.assertIn("runner-shutdown", events)
        self.assertIn("supervisor-shutdown", events)
        self.assertNotIn("resources-cleanup", events)

    async def test_runner_start_uncertainty_retains_runtime_resources(self):
        events: list[str] = []
        composition, _supervisor, runtime = self._composition(events, crash=False)
        runtime.runner = _FakeRunner(events, start_failure=True)
        loop = asyncio.get_running_loop()

        async def start_server(*_args, **_kwargs):
            return _FakeControlServer(events)

        with mock.patch.object(
            loop, "add_signal_handler", return_value=None
        ), mock.patch.object(
            loop, "remove_signal_handler", return_value=True
        ), mock.patch.object(
            bootstrap.asyncio, "start_unix_server", side_effect=start_server
        ):
            with self.assertRaisesRegex(RuntimeError, "partial startup failure"):
                await bootstrap.run_composition(
                    composition,
                    research_runtime_factory=lambda **_kwargs: runtime,
                    monitor_interval_seconds=0.01,
                )
        self.assertIn("control-close", events)
        self.assertIn("supervisor-shutdown", events)
        self.assertNotIn("runner-shutdown", events)
        self.assertNotIn("resources-cleanup", events)


if __name__ == "__main__":
    unittest.main()
