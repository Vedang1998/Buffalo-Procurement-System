"""Focused contract tests for the LOCAL staging acceptance supervisor."""

from __future__ import annotations

import ast
import copy
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

import run_local_staging_acceptance as acceptance


IMAGE_ID = acceptance.FROZEN_IMAGE_ID
RUN_ID = "b" * 32
CONTAINER_ID = "c" * 64


def _image_inspect() -> dict[str, object]:
    return {
        "Id": IMAGE_ID,
        "RepoDigests": [],
        "Architecture": "amd64",
        "Os": "linux",
        "Config": {
            "User": "0:0",
            "Entrypoint": list(acceptance._SERVICE_ENTRYPOINT),
            "Cmd": None,
            "WorkingDir": "/app",
            "StopSignal": "SIGTERM",
            "Env": list(acceptance._IMAGE_ENVIRONMENT),
            "Volumes": None,
            "ExposedPorts": None,
            "Labels": None,
        },
        "RootFS": {
            "Type": "layers",
            "Layers": list(acceptance.FROZEN_IMAGE_ROOTFS_LAYERS),
        },
    }


def _docker_version() -> dict[str, object]:
    return {
        "Client": {
            "Version": "27.5.1",
            "ApiVersion": "1.47",
            "Os": "linux",
            "Arch": "amd64",
        },
        "Server": {
            "Version": "27.5.1",
            "ApiVersion": "1.47",
            "Os": "linux",
            "Arch": "amd64",
            "Components": [
                {"Name": "Engine", "Version": "27.5.1"},
                {"Name": "containerd", "Version": "v2.1.4"},
                {"Name": "runc", "Version": "1.2.4"},
                {"Name": "docker-init", "Version": "0.19.0"},
            ],
        },
    }


def _docker_info() -> dict[str, object]:
    features = json.dumps(
        {
            "linux": {
                "cgroup": {"v2": True},
                "seccomp": {"enabled": True},
            },
            "annotations": {"org.opencontainers.runc.version": "1.2.4"},
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "ServerVersion": "27.5.1",
        "Driver": "overlay2",
        "CgroupVersion": "2",
        "CgroupDriver": "cgroupfs",
        "OSType": "linux",
        "Architecture": "x86_64",
        "DefaultRuntime": "runc",
        "Runtimes": {
            "io.containerd.runc.v2": {
                "path": "runc",
                "status": {"org.opencontainers.runtime-spec.features": features},
            },
            "runc": {
                "path": "runc",
                "status": {"org.opencontainers.runtime-spec.features": features},
            },
        },
        "MemoryLimit": True,
        "SwapLimit": True,
        "PidsLimit": True,
        "NCPU": 4,
        "MemTotal": 8_000_000_000,
        "DockerRootDir": "/var/lib/docker",
        "SecurityOptions": ["name=seccomp,profile=builtin", "name=cgroupns"],
    }


def _operator_proof() -> dict[str, object]:
    return {
        "contract": "BUFFALO_STOPPED_SERVICE_PRICE_STAGE_V1",
        "source_ref": "procurement/config/synthetic_price_replacement_book.csv",
        "source_bytes": 1_590,
        "raw_sha256": (
            "00071443ea8c54b57fc6014c3b1daf204081714a2ff09b98bed6c56a0dd3862c"
        ),
        "target_attestation_sha256": (
            "517843a848fd07e5fc62b9713279a8bcfc52a782890aee68c7d900dd3600d77a"
        ),
        "batch_id": "11111111-1111-4111-8111-111111111111",
        "status": "VALIDATED",
        "declaration_sha256": "a" * 64,
        "validation_fingerprint": "b" * 64,
        "proposed_scope_membership_sha256": "c" * 64,
        "staging_rows_sha256": "d" * 64,
        "validation_issues_sha256": "e" * 64,
        "unchanged_database_sha256": "f" * 64,
        "unchanged_storage_sha256": "0" * 64,
        "idempotent_replay": False,
        "ambiguous_commit_recovered": False,
    }


def _browser_request() -> dict[str, object]:
    return {
        "protocol": acceptance.BROWSER_WORKER_PROTOCOL,
        "frame": "REQUEST",
        "challenge": "1" * 64,
        "run_id": "2" * 32,
        "source_commit": "3" * 40,
        "source_tree": "4" * 40,
        "cdp_endpoint": "http://127.0.0.1:9222",
        "evidence_root": "/private/runtime/evidence/price-confirm",
        "operator_proof": _operator_proof(),
        "tls_certificate_sha256": "5" * 64,
        "chromium_pid": 12345,
    }


def _browser_ready(request: dict[str, object]) -> dict[str, object]:
    return {
        "protocol": acceptance.BROWSER_WORKER_PROTOCOL,
        "frame": "READY",
        "challenge": request["challenge"],
        "config_sha256": acceptance.browser_worker_request_sha256(request),
        "worker_pid": 23456,
        "worker_start_ticks": 34567,
        "source_commit": request["source_commit"],
        "source_tree": request["source_tree"],
        "chromium_pid": request["chromium_pid"],
        "browser_start_time": "45678",
        "python_executable_sha256": "6" * 64,
        "module_manifest_sha256": "7" * 64,
        "driver_sha256": "8" * 64,
        "node_sha256": "9" * 64,
        "preflight_sha256": "a" * 64,
    }


def _browser_result(
    request: dict[str, object],
    ready: dict[str, object],
) -> dict[str, object]:
    return {
        "protocol": acceptance.BROWSER_WORKER_PROTOCOL,
        "frame": "RESULT",
        "challenge": request["challenge"],
        "config_sha256": ready["config_sha256"],
        "ready_sha256": acceptance.browser_worker_ready_sha256(ready),
        "worker_pid": ready["worker_pid"],
        "worker_start_ticks": ready["worker_start_ticks"],
        "proof": {
            "assertion_count": 62,
            "assertion_manifest_sha256": (
                "64a5063520cbe378502b7930f8b51ba784b05c0e9c2ea986de9adeda991efb50"
            ),
            "batch_id": request["operator_proof"]["batch_id"],
            "browser_js_version": "15.0.0.0",
            "browser_pid": request["chromium_pid"],
            "browser_product": "HeadlessChrome/152.0.7977.64",
            "browser_protocol_version": "1.3",
            "browser_start_time": "45678",
            "confirmation_preview_sha256": "1" * 64,
            "contract": "BUFFALO_STAGING_PURCHASING_BROWSER_PHASE_V1",
            "driver_sha256": ready["driver_sha256"],
            "node_sha256": ready["node_sha256"],
            "node_version": "v24.13.0",
            "operational_status_after": "VERIFIED_FUTURE",
            "operational_status_before": "VALIDATED",
            "operator_proof_sha256": acceptance.validate_browser_worker_request(
                request
            ).operator_proof_sha256,
            "phase": "price-confirm",
            "raw_bytes": 1_590,
            "raw_sha256": (
                "00071443ea8c54b57fc6014c3b1daf204081714a2ff09b98bed6c56a0dd3862c"
            ),
            "screenshot_bytes": 128,
            "screenshot_sha256": "2" * 64,
            "source_commit": request["source_commit"],
            "source_tree": request["source_tree"],
            "status_after": "VERIFIED_FUTURE",
            "status_before": "VALIDATED",
            "target_summary": {
                "active_guarded": 5,
                "guarded": 5,
                "inert": 0,
                "live_detached": 0,
                "tracked": 5,
                "unattached": 0,
                "unguarded": 0,
                "unresumed": 0,
                "unsupported": 0,
            },
            "temporal_basis": "REGISTERED_OBSERVATION",
            "tls_certificate_sha256": request["tls_certificate_sha256"],
        },
    }


def _browser_expected(
    request: dict[str, object],
    ready: dict[str, object],
) -> acceptance.BrowserWorkerExpectedAttestation:
    return acceptance.BrowserWorkerExpectedAttestation(
        challenge=request["challenge"],
        run_id=request["run_id"],
        source_commit=request["source_commit"],
        source_tree=request["source_tree"],
        cdp_endpoint=request["cdp_endpoint"],
        evidence_root=request["evidence_root"],
        tls_certificate_sha256=request["tls_certificate_sha256"],
        chromium_pid=request["chromium_pid"],
        browser_start_time="45678",
        worker_pid=ready["worker_pid"],
        worker_start_ticks=ready["worker_start_ticks"],
        python_executable_sha256=ready["python_executable_sha256"],
        module_manifest_sha256=ready["module_manifest_sha256"],
        driver_sha256=ready["driver_sha256"],
        node_sha256=ready["node_sha256"],
        preflight_sha256=ready["preflight_sha256"],
    )


def _container_inspect(
    invocation: acceptance.MaterializerInvocation,
) -> dict[str, object]:
    host_mounts = acceptance._expected_materializer_mounts(invocation)
    observed_mounts = [
        {
            "Type": "volume",
            "Name": invocation.volume_name,
            "Source": f"/var/lib/docker/volumes/{invocation.volume_name}/_data",
            "Destination": "/data",
            "Driver": "local",
            "Mode": "z",
            "RW": True,
            "Propagation": "",
        }
    ]
    for item in host_mounts[1:]:
        observed_mounts.append(
            {
                "Type": "bind",
                "Source": item["Source"],
                "Destination": item["Target"],
                "Mode": "",
                "RW": False,
                "Propagation": "rprivate",
            }
        )
    return {
        "Id": CONTAINER_ID,
        "Created": "2026-10-05T12:00:00.000000000Z",
        "Path": "/usr/bin/tini",
        "Args": list(acceptance._MATERIALIZER_COMMAND),
        "State": {
            "Status": "created",
            "Running": False,
            "Paused": False,
            "Restarting": False,
            "OOMKilled": False,
            "Dead": False,
            "Pid": 0,
            "ExitCode": 0,
            "Error": "",
            "StartedAt": acceptance._DOCKER_ZERO_TIME,
            "FinishedAt": acceptance._DOCKER_ZERO_TIME,
        },
        "Image": invocation.image_id,
        "ResolvConfPath": "",
        "HostnamePath": "",
        "HostsPath": "",
        "LogPath": "",
        "Name": f"/{invocation.container_name}",
        "RestartCount": 0,
        "Driver": "overlay2",
        "Platform": "linux",
        "MountLabel": "",
        "ProcessLabel": "",
        "AppArmorProfile": "",
        "ExecIDs": None,
        "GraphDriver": {"Data": {}, "Name": "overlay2"},
        "HostConfig": {
            "LogConfig": {"Type": "none", "Config": {}},
            "NetworkMode": "none",
            "PortBindings": {},
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "AutoRemove": False,
            "VolumesFrom": None,
            "Binds": None,
            "Links": None,
            "CapAdd": list(acceptance._MATERIALIZER_CAPABILITIES),
            "CapDrop": ["ALL"],
            "CgroupnsMode": "private",
            "Dns": [],
            "DnsOptions": [],
            "DnsSearch": [],
            "ExtraHosts": None,
            "GroupAdd": None,
            "IpcMode": "none",
            "PidMode": "",
            "UTSMode": "",
            "UsernsMode": "",
            "Privileged": False,
            "PublishAllPorts": False,
            "ReadonlyRootfs": True,
            "SecurityOpt": ["no-new-privileges=true"],
            "Tmpfs": {
                "/run/buffalo-research-materializer": (
                    "rw,nosuid,nodev,noexec,mode=0700,uid=0,gid=0,"
                    "size=67108864"
                )
            },
            "Runtime": "runc",
            "Memory": 1_073_741_824,
            "MemorySwap": 1_073_741_824,
            "MemorySwappiness": None,
            "NanoCpus": 2_000_000_000,
            "OomKillDisable": False,
            "PidsLimit": 64,
            "Devices": [],
            "DeviceRequests": None,
            "DeviceCgroupRules": None,
            "Ulimits": [],
            "ShmSize": 67_108_864,
            "MaskedPaths": [
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
            ],
            "ReadonlyPaths": [
                "/proc/bus",
                "/proc/fs",
                "/proc/irq",
                "/proc/sys",
                "/proc/sysrq-trigger",
            ],
            "Mounts": host_mounts,
        },
        "Mounts": observed_mounts,
        "Config": {
            "User": "0:0",
            "AttachStdin": False,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
            "OpenStdin": False,
            "Env": list(acceptance._IMAGE_ENVIRONMENT),
            "Cmd": list(acceptance._MATERIALIZER_COMMAND),
            "Healthcheck": {"Test": ["NONE"]},
            "Image": invocation.image_id,
            "Volumes": None,
            "WorkingDir": "/app",
            "Entrypoint": ["/usr/bin/tini"],
            "Labels": {
                "buffalo.contract": acceptance.ACCEPTANCE_CONTRACT,
                "buffalo.run": invocation.run_id,
                "buffalo.role": acceptance.MATERIALIZER_ROLE,
            },
            "StopSignal": "SIGTERM",
        },
    }


def _volume_inspect(
    invocation: acceptance.MaterializerInvocation,
) -> dict[str, object]:
    return {
        "CreatedAt": "2026-10-05T12:00:00Z",
        "Driver": "local",
        "Labels": {
            "buffalo.contract": acceptance.ACCEPTANCE_CONTRACT,
            "buffalo.run": invocation.run_id,
            "buffalo.role": acceptance.MATERIALIZER_VOLUME_ROLE,
        },
        "Mountpoint": (
            f"/var/lib/docker/volumes/{invocation.volume_name}/_data"
        ),
        "Name": invocation.volume_name,
        "Options": None,
        "Scope": "local",
    }


def _exited_container(
    invocation: acceptance.MaterializerInvocation,
) -> dict[str, object]:
    value = _container_inspect(invocation)
    value["State"].update(
        {
            "Status": "exited",
            "Running": False,
            "Pid": 0,
            "ExitCode": 0,
            "StartedAt": "2026-10-05T12:00:01.000000000Z",
            "FinishedAt": "2026-10-05T12:00:02.000000000Z",
        }
    )
    value["ResolvConfPath"] = (
        f"/var/lib/docker/containers/{CONTAINER_ID}/resolv.conf"
    )
    value["HostnamePath"] = (
        f"/var/lib/docker/containers/{CONTAINER_ID}/hostname"
    )
    value["HostsPath"] = f"/var/lib/docker/containers/{CONTAINER_ID}/hosts"
    value["HostConfig"]["OomKillDisable"] = None
    return value


class RunLocalStagingAcceptanceTests(unittest.TestCase):
    @contextmanager
    def _docker(self, root: Path):
        executable = root / "docker"
        executable.write_bytes(b"synthetic fixed Docker client")
        executable.chmod(0o555)
        raw = executable.read_bytes()
        with patch.multiple(
            acceptance,
            _DOCKER_EXECUTABLE=executable,
            _DOCKER_BYTES=len(raw),
            _DOCKER_SHA256=hashlib.sha256(raw).hexdigest(),
            _DOCKER_UID=os.geteuid(),
            _DOCKER_GID=os.getegid(),
        ):
            with acceptance.open_trusted_docker() as client:
                yield client

    def _ingress(self, root: Path) -> acceptance.MaterializerIngress:
        research = root / "research"
        inventory = root / "deployment-inventory.json"
        bundle = root / "accepted.bundle"
        research.mkdir(mode=0o700)
        inventory.write_bytes(b"inventory")
        bundle.write_bytes(b"bundle")
        inventory.chmod(0o600)
        bundle.chmod(0o600)
        return acceptance.MaterializerIngress(research, inventory, bundle)

    def test_public_entrypoint_is_stdlib_first_and_not_partially_executable(self):
        source = Path(acceptance.__file__).read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(item.name.partition(".")[0] for item in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module.partition(".")[0])
        self.assertTrue(imported.issubset(sys.stdlib_module_names), imported)
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(acceptance.main([]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "Buffalo LOCAL staging acceptance is not yet executable\n",
        )
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(acceptance.main(["unexpected"]), 2)

    def test_stable_ingress_file_refuses_alias_metadata_and_content_drift(self):
        raw = b"exact public test ingress"
        digest = hashlib.sha256(raw).hexdigest()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_bytes(raw)
            target.chmod(0o600)
            acceptance._stable_file_sha256(
                target,
                expected_bytes=len(raw),
                expected_sha256=digest,
                expected_uid=os.geteuid(),
                expected_gid=os.getegid(),
            )
            alias = root / "alias"
            for case in ("mode", "hardlink", "symlink", "bytes"):
                with self.subTest(case=case):
                    if case == "mode":
                        target.chmod(0o640)
                        selected = target
                    elif case == "hardlink":
                        os.link(target, alias)
                        selected = target
                    elif case == "symlink":
                        alias.symlink_to(target)
                        selected = alias
                    else:
                        target.write_bytes(raw + b"x")
                        selected = target
                    with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                        acceptance._stable_file_sha256(
                            selected,
                            expected_bytes=len(raw),
                            expected_sha256=digest,
                            expected_uid=os.geteuid(),
                            expected_gid=os.getegid(),
                        )
                    if alias.exists() or alias.is_symlink():
                        alias.unlink()
                    target.write_bytes(raw)
                    target.chmod(0o600)

    def test_materializer_identity_is_full_digest_and_names_are_code_owned(self):
        self.assertEqual(
            acceptance.FROZEN_IMAGE_ID,
            "sha256:64b2f821aaa12b2c4297f4d28e4f112d98698f204716f36ac81500974ebc0a6f",
        )
        self.assertEqual(
            acceptance.FROZEN_SOURCE_COMMIT,
            "f9cd28f801c325cee5dbd22458b777fbc8bead77",
        )
        self.assertEqual(
            acceptance.FROZEN_SOURCE_TREE,
            "2c7efd855001bbbe4a70defe07b6684e8c2d60a2",
        )
        self.assertEqual(
            hashlib.sha256(
                "\n".join(acceptance.FROZEN_IMAGE_ROOTFS_LAYERS).encode("ascii")
            ).hexdigest(),
            "703579ecb36c94220c35b319f63173991ee3ec88a89a27b6f0fe29c389309547",
        )
        self.assertEqual(
            hashlib.sha256(
                "\n".join(acceptance._IMAGE_ENVIRONMENT).encode("ascii")
            ).hexdigest(),
            "01d4c219d2f9e7848d296af9d7e74055ddd663f6de84f25b6c5594096a468226",
        )
        self.assertEqual(
            acceptance._MATERIALIZER_CAPABILITIES,
            ("CHOWN", "DAC_READ_SEARCH", "FOWNER"),
        )
        self.assertEqual(
            acceptance._MATERIALIZER_COMMAND,
            (
                "-g",
                "--",
                "/opt/buffalo-venv/bin/python",
                "-I",
                "-B",
                "-m",
                "procurement_os.staging_research_materializer",
            ),
        )
        with TemporaryDirectory() as temporary:
            ingress = self._ingress(Path(temporary))
            with patch.object(
                acceptance,
                "validate_materializer_ingress",
                return_value=ingress,
            ):
                initial = acceptance.materializer_invocation(
                    image_id=IMAGE_ID,
                    run_id=RUN_ID,
                    ingress=ingress,
                )
                replay = acceptance.materializer_invocation(
                    image_id=IMAGE_ID,
                    run_id=RUN_ID,
                    ingress=None,
                )
            self.assertEqual(
                initial.volume_name,
                f"buffalo-staging-acceptance-{RUN_ID}",
            )
            self.assertEqual(
                initial.container_name,
                f"buffalo-staging-research-materialize-{RUN_ID}",
            )
            self.assertEqual(
                replay.container_name,
                f"buffalo-staging-research-replay-{RUN_ID}",
            )
            for bad_image in (
                "a" * 64,
                "sha256:" + "a" * 63,
                "sha256:" + "e" * 64,
                "candidate:latest",
            ):
                with self.subTest(bad_image=bad_image), self.assertRaises(
                    acceptance.LocalStagingAcceptanceError
                ):
                    acceptance.materializer_invocation(
                        image_id=bad_image,
                        run_id=RUN_ID,
                        ingress=None,
                    )
            for bad_run in ("b" * 31, "B" * 32, "../foreign", "b" * 33):
                with self.subTest(bad_run=bad_run), self.assertRaises(
                    acceptance.LocalStagingAcceptanceError
                ):
                    acceptance.materializer_invocation(
                        image_id=IMAGE_ID,
                        run_id=bad_run,
                        ingress=None,
                    )

    def test_initial_materializer_create_argv_is_exact_and_contains_no_env(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            ingress = self._ingress(root)
            with self._docker(root) as docker, patch.object(
                acceptance,
                "validate_materializer_ingress",
                return_value=ingress,
            ):
                invocation = acceptance.materializer_invocation(
                    image_id=IMAGE_ID,
                    run_id=RUN_ID,
                    ingress=ingress,
                )
                docker_descriptor = docker.descriptor
                arguments = acceptance.build_materializer_create_argv(
                    docker_client=docker,
                    invocation=invocation,
                )
            self.assertEqual(
                arguments[:8],
                (
                    f"/proc/self/fd/{docker_descriptor}",
                    "create",
                    "--platform",
                    "linux/amd64",
                    "--pull",
                    "never",
                    "--runtime",
                    "runc",
                ),
            )
            self.assertEqual(
                arguments[-10:],
                (
                    "--entrypoint",
                    "/usr/bin/tini",
                    IMAGE_ID,
                    "-g",
                    "--",
                    "/opt/buffalo-venv/bin/python",
                    "-I",
                    "-B",
                    "-m",
                    acceptance.MATERIALIZER_MODULE,
                ),
            )
            self.assertEqual(arguments.count("--mount"), 4)
            self.assertEqual(arguments.count("--cap-add"), 3)
            self.assertEqual(
                tuple(
                    arguments[index + 1]
                    for index, value in enumerate(arguments)
                    if value == "--cap-add"
                ),
                ("CHOWN", "DAC_READ_SEARCH", "FOWNER"),
            )
            for flag, expected in (
                ("--pids-limit", "64"),
                ("--memory", "1073741824"),
                ("--memory-swap", "1073741824"),
                ("--cpus", "2"),
            ):
                self.assertEqual(arguments[arguments.index(flag) + 1], expected)
            self.assertNotIn("--env", arguments)
            self.assertNotIn("--env-file", arguments)
            self.assertNotIn("--privileged", arguments)
            self.assertNotIn("--pid", arguments)
            joined = "\n".join(arguments)
            for expected in (
                "--read-only",
                "type=volume,src=buffalo-staging-acceptance-",
                "dst=/mnt/buffalo-accepted-research,readonly",
                "dst=/mnt/deployment-inventory.json,readonly",
                "dst=/mnt/buffalo-procurement-os-accepted-source-608929ad.bundle",
                acceptance._MATERIALIZER_TMPFS,
            ):
                self.assertIn(expected, joined)

    def test_replay_uses_same_envelope_without_any_ingress_mount(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            invocation = acceptance.materializer_invocation(
                image_id=IMAGE_ID,
                run_id=RUN_ID,
                ingress=None,
            )
            with self._docker(root) as docker:
                arguments = acceptance.build_materializer_create_argv(
                    docker_client=docker,
                    invocation=invocation,
                )
            self.assertEqual(arguments.count("--mount"), 1)
            joined = "\n".join(arguments)
            self.assertNotIn("/mnt/buffalo-accepted-research", joined)
            self.assertNotIn("/mnt/deployment-inventory.json", joined)
            self.assertNotIn("accepted-source-608929ad.bundle", joined)
            self.assertIn("--network\nnone", joined)
            self.assertIn("--ipc\nnone", joined)
            self.assertIn("--cgroupns\nprivate", joined)
            self.assertIn("--log-driver\nnone", joined)

    def test_image_and_container_inspect_are_exact_and_fail_on_extra_authority(self):
        acceptance.validate_docker_version(_docker_version())
        acceptance.validate_docker_info(_docker_info())
        acceptance.validate_frozen_image_inspect(
            _image_inspect(),
            image_id=IMAGE_ID,
        )
        with TemporaryDirectory() as temporary:
            ingress = self._ingress(Path(temporary))
            with patch.object(
                acceptance,
                "validate_materializer_ingress",
                return_value=ingress,
            ):
                invocation = acceptance.materializer_invocation(
                    image_id=IMAGE_ID,
                    run_id=RUN_ID,
                    ingress=ingress,
                )
            baseline = _container_inspect(invocation)
            self.assertEqual(
                acceptance.validate_materializer_container_inspect(
                    baseline,
                    invocation=invocation,
                ),
                CONTAINER_ID,
            )
            mutations = (
                (
                    "extra capability",
                    lambda value: value["HostConfig"]["CapAdd"].append("SYS_ADMIN"),
                ),
                (
                    "extra environment",
                    lambda value: value["Config"]["Env"].append("SECRET=value"),
                ),
                ("oom", lambda value: value["State"].update(OOMKilled=True)),
                (
                    "extra label",
                    lambda value: value["Config"]["Labels"].update(
                        unexpected="value"
                    ),
                ),
                (
                    "privileged",
                    lambda value: value["HostConfig"].update(Privileged=True),
                ),
                (
                    "writable root",
                    lambda value: value["HostConfig"].update(ReadonlyRootfs=False),
                ),
                (
                    "host network",
                    lambda value: value["HostConfig"].update(NetworkMode="host"),
                ),
                (
                    "missing cap drop",
                    lambda value: value["HostConfig"].update(CapDrop=[]),
                ),
                (
                    "missing no-new-privileges",
                    lambda value: value["HostConfig"].update(SecurityOpt=[]),
                ),
                (
                    "unbounded memory",
                    lambda value: value["HostConfig"].update(Memory=0),
                ),
                (
                    "unbounded swap",
                    lambda value: value["HostConfig"].update(MemorySwap=0),
                ),
                (
                    "unbounded pids",
                    lambda value: value["HostConfig"].update(PidsLimit=0),
                ),
                (
                    "writable data mismatch",
                    lambda value: value["Mounts"][0].update(RW=False),
                ),
                (
                    "mount destination drift",
                    lambda value: value["Mounts"][0].update(
                        Destination="/foreign"
                    ),
                ),
                (
                    "command drift",
                    lambda value: value["Config"].update(Cmd=["foreign"]),
                ),
                (
                    "user drift",
                    lambda value: value["Config"].update(User="1103:1203"),
                ),
            )
            for label, mutate in mutations:
                with self.subTest(label=label):
                    changed = copy.deepcopy(baseline)
                    mutate(changed)
                    with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                        acceptance.validate_materializer_container_inspect(
                            changed,
                            invocation=invocation,
                        )
        for field, value in (("NCPU", float("nan")), ("MemTotal", float("inf"))):
            with self.subTest(field=field):
                changed_info = _docker_info()
                changed_info[field] = value
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.validate_docker_info(changed_info)
        changed_runtime = _docker_info()
        changed_runtime["Runtimes"]["runc"]["path"] = "foreign-shim"
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.validate_docker_info(changed_runtime)
        changed_version = _docker_version()
        changed_version["Server"]["Components"].append(
            {"Name": "runc", "Version": "1.2.4"}
        )
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.validate_docker_version(changed_version)

    def test_directly_forged_invocation_and_unsafe_mount_path_are_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            forged = acceptance.MaterializerInvocation(
                image_id=IMAGE_ID,
                run_id=RUN_ID,
                volume_name="foreign-volume",
                container_name=f"buffalo-staging-research-replay-{RUN_ID}",
                ingress=None,
            )
            with self._docker(root) as docker, self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance.build_materializer_create_argv(
                    docker_client=docker,
                    invocation=forged,
                )
            comma = root / "unsafe,path"
            comma.mkdir()
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance._canonical_mount_source(comma, directory=True)

    def test_trusted_docker_client_binds_exact_path_bytes_and_inode(self):
        self.assertEqual(
            acceptance._DOCKER_EXECUTABLE,
            Path(
                "/nix/store/37rf2zl654djg7989yipq57d5pd195hi-docker-27.5.1/"
                "libexec/docker/docker"
            ),
        )
        self.assertEqual(acceptance._DOCKER_BYTES, 35_648_392)
        self.assertEqual(
            acceptance._DOCKER_SHA256,
            "03f1d4e930931713fc9ae82302947e87f435bd81714201a225a6c237afd9baee",
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._docker(root) as client:
                acceptance._validate_trusted_docker(client)
                client.path.chmod(0o755)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance._validate_trusted_docker(client)
                client.path.chmod(0o555)
                acceptance._validate_trusted_docker(client)
            self.assertEqual(client.descriptor, -1)

            target = root / "target"
            alias = root / "alias"
            target.write_bytes(b"hard-linked Docker client")
            target.chmod(0o555)
            os.link(target, alias)
            with patch.multiple(
                acceptance,
                _DOCKER_EXECUTABLE=target,
                _DOCKER_BYTES=target.stat().st_size,
                _DOCKER_SHA256=hashlib.sha256(target.read_bytes()).hexdigest(),
                _DOCKER_UID=os.geteuid(),
                _DOCKER_GID=os.getegid(),
            ), self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance.open_trusted_docker()

    def test_volume_creation_and_inspection_are_exact_and_labeled(self):
        invocation = acceptance.materializer_invocation(
            image_id=IMAGE_ID,
            run_id=RUN_ID,
            ingress=None,
        )
        baseline = _volume_inspect(invocation)
        fingerprint = acceptance.validate_materializer_volume_inspect(
            baseline,
            invocation=invocation,
        )
        self.assertEqual(fingerprint.name, invocation.volume_name)
        self.assertIsNone(fingerprint.options)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._docker(root) as client:
                arguments = acceptance.build_materializer_volume_create_argv(
                    docker_client=client,
                    invocation=invocation,
                )
            self.assertEqual(arguments[1:5], ("volume", "create", "--driver", "local"))
            self.assertEqual(arguments.count("--label"), 3)
            self.assertEqual(arguments[-2:], ("--name", invocation.volume_name))
        acceptance.parse_volume_create_output(
            f"{invocation.volume_name}\n".encode("ascii"),
            expected_name=invocation.volume_name,
        )
        for label, mutate in (
            ("foreign label", lambda value: value["Labels"].update(role="foreign")),
            ("driver option", lambda value: value.__setitem__("Options", {})),
            ("foreign mount", lambda value: value.__setitem__("Mountpoint", "/tmp/x")),
            ("extra field", lambda value: value.__setitem__("Status", {})),
        ):
            with self.subTest(label=label):
                changed = copy.deepcopy(baseline)
                mutate(changed)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.validate_materializer_volume_inspect(
                        changed,
                        invocation=invocation,
                    )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._docker(root) as client, patch.object(
                acceptance,
                "require_docker_name_absent",
            ), patch.object(
                acceptance,
                "run_docker_argv",
                side_effect=acceptance.LocalStagingAcceptanceError("ambiguous"),
            ), patch.object(
                acceptance,
                "_docker_inspect_one",
                return_value=baseline,
            ):
                self.assertEqual(
                    acceptance.create_materializer_volume(
                        client,
                        root,
                        invocation,
                    ),
                    fingerprint,
                )

                created = _container_inspect(invocation)
                with patch.object(
                    acceptance,
                    "reattest_materializer_volume",
                ), patch.object(
                    acceptance,
                    "_docker_inspect_one",
                    return_value=created,
                ):
                    recovered_id, recovered = (
                        acceptance.create_materializer_container(
                            client,
                            root,
                            invocation,
                            fingerprint,
                        )
                    )
                self.assertEqual(recovered_id, CONTAINER_ID)
                self.assertIs(recovered, created)

    def test_container_state_requires_clean_exit_and_unique_exact_mounts(self):
        invocation = acceptance.materializer_invocation(
            image_id=IMAGE_ID,
            run_id=RUN_ID,
            ingress=None,
        )
        created = _container_inspect(invocation)
        exited = _exited_container(invocation)
        self.assertEqual(
            acceptance.validate_materializer_container_inspect(
                exited,
                invocation=invocation,
                expected_state="exited",
            ),
            CONTAINER_ID,
        )
        self.assertEqual(
            acceptance.materializer_container_envelope_sha256(created),
            acceptance.materializer_container_envelope_sha256(exited),
        )
        for label, mutate in (
            ("nonzero exit", lambda value: value["State"].update(ExitCode=137)),
            ("residual pid", lambda value: value["State"].update(Pid=123)),
            ("boolean exit", lambda value: value["State"].update(ExitCode=False)),
            ("oom", lambda value: value["State"].update(OOMKilled=True)),
            (
                "reversed timestamps",
                lambda value: value["State"].update(
                    FinishedAt="2026-10-05T11:59:59Z"
                ),
            ),
            (
                "nanosecond reversal",
                lambda value: value["State"].update(
                    StartedAt="2026-10-05T12:00:02.000000002Z",
                    FinishedAt="2026-10-05T12:00:02.000000001Z",
                ),
            ),
            (
                "noncanonical offset",
                lambda value: value["State"].update(
                    FinishedAt="2026-10-05T12:00:02+00:00"
                ),
            ),
            (
                "duplicate destination",
                lambda value: value["Mounts"].append(
                    copy.deepcopy(value["Mounts"][0])
                ),
            ),
        ):
            with self.subTest(label=label):
                changed = copy.deepcopy(exited)
                mutate(changed)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.validate_materializer_container_inspect(
                        changed,
                        invocation=invocation,
                        expected_state="exited",
                    )

    def test_bounded_process_refuses_output_overflow_and_timeout(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = acceptance._run_bounded_process(
                (sys.executable, "-c", "print('ok')"),
                pass_fds=(),
                environment={"LANG": "C.UTF-8"},
                cwd=root,
                timeout_seconds=2.0,
                stdout_limit=3,
                stderr_limit=0,
            )
            self.assertEqual(result, acceptance.BoundedProcessResult(0, b"ok\n", b""))
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance._run_bounded_process(
                    (sys.executable, "-c", "print('x' * 1000)"),
                    pass_fds=(),
                    environment={"LANG": "C.UTF-8"},
                    cwd=root,
                    timeout_seconds=2.0,
                    stdout_limit=16,
                    stderr_limit=0,
                )
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance._run_bounded_process(
                    (sys.executable, "-c", "import time; time.sleep(5)"),
                    pass_fds=(),
                    environment={"LANG": "C.UTF-8"},
                    cwd=root,
                    timeout_seconds=0.05,
                    stdout_limit=0,
                    stderr_limit=0,
                )

    def test_json_and_creation_outputs_reject_ambiguity(self):
        self.assertEqual(
            acceptance.parse_container_create_output(
                f"{CONTAINER_ID}\n".encode("ascii")
            ),
            CONTAINER_ID,
        )
        self.assertEqual(
            acceptance.parse_single_json_object(b'{"exact":true}'),
            {"exact": True},
        )
        for raw in (
            CONTAINER_ID.encode("ascii"),
            f"{CONTAINER_ID}\nextra\n".encode("ascii"),
            b'{"a":1,"a":2}',
            b'{"x":NaN}',
            b'{"x":1e999}',
            b'{} {}',
            b'[]',
        ):
            with self.subTest(raw=raw), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                if raw.startswith(b"{") or raw.startswith(b"["):
                    acceptance.parse_single_json_object(raw)
                else:
                    acceptance.parse_container_create_output(raw)

    def test_owned_name_collision_is_refused_even_with_matching_labels(self):
        invocation = acceptance.materializer_invocation(
            image_id=IMAGE_ID,
            run_id=RUN_ID,
            ingress=None,
        )
        with patch.object(
            acceptance,
            "_docker_name_inventory",
            return_value=(("", invocation.volume_name),),
        ), self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.require_docker_name_absent(
                object(),
                Path("/unused"),
                resource="volume",
                name=invocation.volume_name,
            )
        with patch.object(
            acceptance,
            "_docker_name_inventory",
            return_value=((CONTAINER_ID, invocation.container_name),),
        ), self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.require_docker_name_absent(
                object(),
                Path("/unused"),
                resource="container",
                name=invocation.container_name,
            )

    def test_materializer_phase_rejects_failed_exit_and_runs_owned_cleanup(self):
        invocation = acceptance.materializer_invocation(
            image_id=IMAGE_ID,
            run_id=RUN_ID,
            ingress=None,
        )
        created = _container_inspect(invocation)
        envelope = acceptance.materializer_container_envelope_sha256(created)
        fingerprint = acceptance.validate_materializer_volume_inspect(
            _volume_inspect(invocation),
            invocation=invocation,
        )
        client = object()
        with patch.object(
            acceptance,
            "create_materializer_container",
            return_value=(CONTAINER_ID, created),
        ), patch.object(
            acceptance,
            "run_docker_command",
            return_value=acceptance.BoundedProcessResult(
                1,
                b"",
                b"Buffalo research materialization failed\n",
            ),
        ), patch.object(
            acceptance,
            "remove_owned_materializer_container",
        ) as cleanup, self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.run_materializer_phase(
                client,
                Path("/unused"),
                invocation,
                fingerprint,
                timeout_seconds=30.0,
            )
        cleanup.assert_called_once_with(
            client,
            Path("/unused"),
            invocation,
            container_id=CONTAINER_ID,
            envelope_sha256=envelope,
        )

    def test_browser_worker_frame_is_bounded_canonical_and_exact(self):
        value = {"a": 1, "nested": [True, None, "text"]}
        framed = acceptance.encode_browser_worker_frame(
            value,
            maximum_bytes=128,
        )
        self.assertEqual(
            int.from_bytes(framed[:4], "big"),
            len(framed) - 4,
        )
        self.assertEqual(
            acceptance.decode_browser_worker_frame(
                framed,
                maximum_bytes=128,
            ),
            value,
        )
        noncanonical = b'{"nested": [true,null,"text"], "a":1}'
        duplicate = b'{"a":1,"a":1}'
        cases = (
            b"",
            b"\x00\x00\x00\x00",
            framed[:-1],
            framed + b"x",
            len(noncanonical).to_bytes(4, "big") + noncanonical,
            len(duplicate).to_bytes(4, "big") + duplicate,
            b"\x00\x00\x00\x09{\"x\":NaN}",
            b"\x00\x00\x00\x0a{\"x\":1.25}",
        )
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance.decode_browser_worker_frame(
                    raw,
                    maximum_bytes=128,
                )
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.encode_browser_worker_frame(
                {"float": 1.25},
                maximum_bytes=128,
            )
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.decode_browser_worker_frame(
                framed,
                maximum_bytes=len(framed) - 5,
            )
        for non_object in ([], 1, "object"):
            with self.subTest(non_object=non_object), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance.encode_browser_worker_frame(
                    non_object,
                    maximum_bytes=128,
                )
        for invalid_maximum in (True, 1.5, float("inf")):
            with self.subTest(
                invalid_maximum=invalid_maximum
            ), self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance.encode_browser_worker_frame(
                    value,
                    maximum_bytes=invalid_maximum,
                )
            with self.subTest(
                invalid_decode_maximum=invalid_maximum
            ), self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance.decode_browser_worker_frame(
                    framed,
                    maximum_bytes=invalid_maximum,
                )

    def test_browser_worker_frame_pipe_io_is_bounded_one_shot_and_deadlined(self):
        value = _browser_request()
        maximum = acceptance._BROWSER_REQUEST_LIMIT
        framed = acceptance.encode_browser_worker_frame(
            value,
            maximum_bytes=maximum,
        )

        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)

        def fragmented_writer() -> None:
            try:
                for start, stop in ((0, 1), (1, 4), (4, 19), (19, len(framed))):
                    os.write(write_descriptor, framed[start:stop])
                    time.sleep(0.005)
            finally:
                os.close(write_descriptor)

        writer = threading.Thread(target=fragmented_writer)
        writer.start()
        observed, observed_frame = acceptance.read_browser_worker_frame(
            read_descriptor,
            maximum_bytes=maximum,
            deadline=time.monotonic() + 1.0,
        )
        writer.join(timeout=1.0)
        self.assertFalse(writer.is_alive())
        self.assertEqual(observed, value)
        self.assertEqual(observed_frame, framed)
        with self.assertRaises(OSError):
            os.fstat(read_descriptor)

        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        reader_result: list[tuple[dict[str, object], bytes]] = []

        def bounded_reader() -> None:
            selected, raw = acceptance.read_browser_worker_frame(
                read_descriptor,
                maximum_bytes=maximum,
                deadline=time.monotonic() + 1.0,
            )
            reader_result.append((dict(selected), raw))

        reader = threading.Thread(target=bounded_reader)
        reader.start()
        written = acceptance.write_browser_worker_frame(
            write_descriptor,
            value,
            maximum_bytes=maximum,
            deadline=time.monotonic() + 1.0,
        )
        reader.join(timeout=1.0)
        self.assertFalse(reader.is_alive())
        self.assertEqual(written, framed)
        self.assertEqual(reader_result, [(value, framed)])

        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        reader_result.clear()
        reader = threading.Thread(target=bounded_reader)
        reader.start()
        real_write = os.write
        write_calls = 0

        def partial_write(descriptor: int, payload: object) -> int:
            nonlocal write_calls
            write_calls += 1
            if write_calls == 1:
                raise InterruptedError
            selected = memoryview(payload)
            return real_write(descriptor, selected[: min(17, len(selected))])

        with patch.object(acceptance.os, "write", side_effect=partial_write):
            self.assertEqual(
                acceptance.write_browser_worker_frame(
                    write_descriptor,
                    value,
                    maximum_bytes=maximum,
                    deadline=time.monotonic() + 1.0,
                ),
                framed,
            )
        reader.join(timeout=1.0)
        self.assertFalse(reader.is_alive())
        self.assertGreater(write_calls, 2)
        self.assertEqual(reader_result, [(value, framed)])

        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        with patch.object(acceptance.os, "write", return_value=0):
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance.write_browser_worker_frame(
                    write_descriptor,
                    value,
                    maximum_bytes=maximum,
                    deadline=time.monotonic() + 0.5,
                )
        os.close(read_descriptor)

        malformed_cases = (
            framed[:-1],
            framed + b"x",
            b"\x00\x00\x00\x09{\"x\":NaN}",
        )
        for raw in malformed_cases:
            with self.subTest(raw=raw):
                read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
                os.write(write_descriptor, raw)
                os.close(write_descriptor)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.read_browser_worker_frame(
                        read_descriptor,
                        maximum_bytes=maximum,
                        deadline=time.monotonic() + 0.5,
                    )
                with self.assertRaises(OSError):
                    os.fstat(read_descriptor)

        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        os.write(write_descriptor, (maximum + 1).to_bytes(4, "big"))
        started = time.monotonic()
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.read_browser_worker_frame(
                read_descriptor,
                maximum_bytes=maximum,
                deadline=time.monotonic() + 1.0,
            )
        self.assertLess(time.monotonic() - started, 0.5)
        os.close(write_descriptor)

        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        os.write(write_descriptor, framed)
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.read_browser_worker_frame(
                read_descriptor,
                maximum_bytes=maximum,
                deadline=time.monotonic() + 0.05,
            )
        os.close(write_descriptor)

    def test_browser_worker_secret_pipe_is_exact_one_shot_and_scrubbed(self):
        secret = bytearray(b"S" * 43)
        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        acceptance.write_browser_worker_secret(
            write_descriptor,
            secret,
            deadline=time.monotonic() + 1.0,
        )
        self.assertEqual(secret, bytearray(43))
        observed = acceptance.read_browser_worker_secret(
            read_descriptor,
            deadline=time.monotonic() + 1.0,
        )
        self.assertEqual(observed, bytearray(b"S" * 43))
        for index in range(len(observed)):
            observed[index] = 0

        retained: list[bytearray] = []
        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        os.write(write_descriptor, b"S" * 10)

        def interrupting_readv(_: int, buffers: list[object]) -> int:
            target = memoryview(buffers[0])
            target[:10] = b"S" * 10
            retained.append(target.obj)
            raise KeyboardInterrupt

        with patch.object(
            acceptance.os,
            "readv",
            side_effect=interrupting_readv,
        ), self.assertRaises(KeyboardInterrupt):
            acceptance.read_browser_worker_secret(
                read_descriptor,
                deadline=time.monotonic() + 0.5,
            )
        self.assertEqual(retained, [bytearray(43)])
        with self.assertRaises(OSError):
            os.fstat(read_descriptor)
        os.close(write_descriptor)

        for payload in (
            b"",
            b"S" * 42,
            b"S" * 44,
            b"S" * 42 + b"\n",
            b"S" * 42 + b"!",
            b"\xff" * 43,
        ):
            with self.subTest(payload_bytes=len(payload)):
                read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
                if payload:
                    os.write(write_descriptor, payload)
                os.close(write_descriptor)
                with self.assertRaises(
                    acceptance.LocalStagingAcceptanceError
                ) as captured:
                    acceptance.read_browser_worker_secret(
                        read_descriptor,
                        deadline=time.monotonic() + 0.5,
                    )
                self.assertNotIn("S" * 8, str(captured.exception))
                with self.assertRaises(OSError):
                    os.fstat(read_descriptor)

        invalid = bytearray(b"short")
        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.write_browser_worker_secret(
                write_descriptor,
                invalid,
                deadline=time.monotonic() + 0.5,
            )
        self.assertEqual(invalid, bytearray(len(invalid)))
        with self.assertRaises(OSError):
            os.fstat(write_descriptor)
        os.close(read_descriptor)

        class MutableSecret(bytearray):
            pass

        subclass_secret = MutableSecret(b"U" * 43)
        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.write_browser_worker_secret(
                write_descriptor,
                subclass_secret,
                deadline=time.monotonic() + 0.5,
            )
        self.assertEqual(subclass_secret, bytearray(43))
        with self.assertRaises(OSError):
            os.fstat(write_descriptor)
        os.close(read_descriptor)

        for wrong_type in (b"S" * 43, None):
            with self.subTest(wrong_type=type(wrong_type).__name__):
                read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.write_browser_worker_secret(
                        write_descriptor,
                        wrong_type,
                        deadline=time.monotonic() + 0.5,
                    )
                with self.assertRaises(OSError):
                    os.fstat(write_descriptor)
                os.close(read_descriptor)

        interrupted = bytearray(b"I" * 43)
        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        with patch.object(
            acceptance.os,
            "write",
            side_effect=KeyboardInterrupt,
        ), self.assertRaises(KeyboardInterrupt):
            acceptance.write_browser_worker_secret(
                write_descriptor,
                interrupted,
                deadline=time.monotonic() + 0.5,
            )
        self.assertEqual(interrupted, bytearray(43))
        with self.assertRaises(OSError):
            os.fstat(write_descriptor)
        os.close(read_descriptor)

        with TemporaryDirectory() as temporary:
            retained = Path(temporary) / "must-remain-empty"
            regular_descriptor = os.open(
                retained,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                0o600,
            )
            rejected = bytearray(b"R" * 43)
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance.write_browser_worker_secret(
                    regular_descriptor,
                    rejected,
                    deadline=time.monotonic() + 0.5,
                )
            self.assertEqual(rejected, bytearray(43))
            self.assertEqual(retained.read_bytes(), b"")

        stalled_secret = bytearray(b"T" * 43)
        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        os.write(write_descriptor, stalled_secret)
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.read_browser_worker_secret(
                read_descriptor,
                deadline=time.monotonic() + 0.05,
            )
        os.close(write_descriptor)
        for index in range(len(stalled_secret)):
            stalled_secret[index] = 0

    def test_browser_request_schema_contains_no_credential_and_fails_closed(self):
        from procurement_os.synthetic_staging_price_stage import (
            _validate_operator_proof,
        )

        request = _browser_request()
        observed = acceptance.validate_browser_worker_request(request)
        self.assertEqual(
            _validate_operator_proof(copy.deepcopy(request["operator_proof"])),
            request["operator_proof"],
        )
        self.assertEqual(observed.run_id, "2" * 32)
        frozen_proof = observed.operator_proof_json
        request["operator_proof"]["status"] = "MUTATED"
        self.assertEqual(observed.operator_proof_json, frozen_proof)
        request = _browser_request()
        self.assertEqual(
            len(acceptance.browser_worker_request_sha256(request)),
            64,
        )
        with patch.object(
            acceptance.os,
            "urandom",
            side_effect=(b"\x12" * 32, b"\x34" * 16),
        ):
            self.assertEqual(
                acceptance.new_browser_worker_identifiers(),
                ("12" * 32, "34" * 16),
            )
        sentinel = "S" * 43
        encoded = acceptance.encode_browser_worker_frame(
            request,
            maximum_bytes=acceptance._BROWSER_REQUEST_LIMIT,
        )
        self.assertNotIn(sentinel.encode("ascii"), encoded)
        mutations = (
            lambda value: value.update(secret=sentinel),
            lambda value: value.pop("source_tree"),
            lambda value: value.update(protocol="foreign"),
            lambda value: value.update(cdp_endpoint="https://evil.example"),
            lambda value: value.update(evidence_root="relative"),
            lambda value: value.update(evidence_root="/"),
            lambda value: value.update(chromium_pid=True),
            lambda value: value.update(operator_proof={}),
            lambda value: value["operator_proof"].update(password=sentinel),
            lambda value: value["operator_proof"].update(status="MUTATED"),
        )
        for mutate in mutations:
            changed = copy.deepcopy(request)
            mutate(changed)
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance.validate_browser_worker_request(changed)

    def test_browser_ready_and_result_are_bound_to_the_exact_request(self):
        from audit_staging_purchasing_browser import _validate_phase_proof

        request = _browser_request()
        ready = _browser_ready(request)
        result = _browser_result(request, ready)
        expected = _browser_expected(request, ready)
        proof = result["proof"]
        self.assertEqual(
            _validate_phase_proof(
                copy.deepcopy(proof),
                batch_id=proof["batch_id"],
                expected_source_commit=proof["source_commit"],
                expected_source_tree=proof["source_tree"],
                driver_sha256=proof["driver_sha256"],
                node_sha256=proof["node_sha256"],
                node_version=proof["node_version"],
                operator_proof_sha256=proof["operator_proof_sha256"],
                browser_pid=proof["browser_pid"],
                browser_start_time=proof["browser_start_time"],
                tls_certificate_sha256=proof["tls_certificate_sha256"],
                screenshot_bytes=proof["screenshot_bytes"],
                screenshot_sha256=proof["screenshot_sha256"],
            ),
            proof,
        )
        validated = acceptance.validate_browser_worker_transition(
            request,
            ready,
            result,
            expected=expected,
        )
        self.assertIsNone(
            acceptance.validate_browser_worker_transition(
                request,
                ready,
                expected=expected,
            )[2]
        )
        self.assertEqual(validated[0].challenge, validated[1].challenge)
        self.assertEqual(validated[1].worker_pid, validated[2].worker_pid)
        sentinel = b"S" * 43
        for value, maximum in (
            (ready, acceptance._BROWSER_READY_LIMIT),
            (result, acceptance._BROWSER_RESULT_LIMIT),
        ):
            self.assertNotIn(
                sentinel,
                acceptance.encode_browser_worker_frame(
                    value,
                    maximum_bytes=maximum,
                ),
            )
        for label, target, field, replacement in (
            ("challenge", ready, "challenge", "f" * 64),
            ("config", ready, "config_sha256", "e" * 64),
            ("source", ready, "source_tree", "d" * 40),
            ("browser pid", ready, "chromium_pid", 99999),
            ("browser start", ready, "browser_start_time", "45679"),
            ("python", ready, "python_executable_sha256", "f" * 64),
            ("modules", ready, "module_manifest_sha256", "f" * 64),
            ("driver", ready, "driver_sha256", "f" * 64),
            ("node", ready, "node_sha256", "f" * 64),
            ("preflight", ready, "preflight_sha256", "f" * 64),
            ("result pid", result, "worker_pid", 99999),
            ("result start", result, "worker_start_ticks", 99999),
        ):
            with self.subTest(label=label):
                changed_ready = copy.deepcopy(ready)
                changed_result = copy.deepcopy(result)
                if target is ready:
                    changed_ready[field] = replacement
                else:
                    changed_result[field] = replacement
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.validate_browser_worker_transition(
                        request,
                        changed_ready,
                        changed_result,
                        expected=expected,
                    )
        for label, mutate in (
            (
                "nested result credential",
                lambda value: value["proof"].update(cookie="private"),
            ),
            (
                "ready digest",
                lambda value: value.update(ready_sha256="0" * 64),
            ),
            (
                "operator binding",
                lambda value: value["proof"].update(operator_proof_sha256="0" * 64),
            ),
            (
                "browser start",
                lambda value: value["proof"].update(browser_start_time="45679"),
            ),
        ):
            with self.subTest(label=label):
                changed_result = copy.deepcopy(result)
                mutate(changed_result)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.validate_browser_worker_transition(
                        request,
                        ready,
                        changed_result,
                        expected=expected,
                    )
        for field in (
            "challenge",
            "run_id",
            "source_commit",
            "source_tree",
            "cdp_endpoint",
            "evidence_root",
            "tls_certificate_sha256",
            "chromium_pid",
            "browser_start_time",
            "worker_pid",
            "worker_start_ticks",
            "python_executable_sha256",
            "module_manifest_sha256",
            "driver_sha256",
            "node_sha256",
            "preflight_sha256",
        ):
            with self.subTest(expected_attestation=field):
                changed = copy.copy(expected)
                replacements = {
                    "challenge": "e" * 64,
                    "run_id": "e" * 32,
                    "source_commit": "e" * 40,
                    "source_tree": "e" * 40,
                    "cdp_endpoint": "http://127.0.0.1:9223",
                    "evidence_root": "/private/runtime/evidence/foreign",
                    "tls_certificate_sha256": "e" * 64,
                    "chromium_pid": 99999,
                    "browser_start_time": "45679",
                    "worker_pid": 99999,
                    "worker_start_ticks": 99999,
                }
                replacement = replacements.get(field, "f" * 64)
                object.__setattr__(changed, field, replacement)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.validate_browser_worker_transition(
                        request,
                        ready,
                        result,
                        expected=changed,
                    )
                if field == "browser_start_time":
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        acceptance.validate_browser_worker_transition(
                            request,
                            ready,
                            expected=changed,
                        )

    def test_hidden_browser_worker_fd_arguments_are_exact_but_not_dispatched(self):
        self.assertEqual(
            acceptance.BROWSER_WORKER_PROTOCOL,
            "BUFFALO_LOCAL_STAGING_BROWSER_WORKER_V1",
        )
        self.assertEqual(
            acceptance.BROWSER_WORKER_HIDDEN_MODE,
            "--internal-browser-worker",
        )
        self.assertEqual(
            (
                acceptance._BROWSER_REQUEST_LIMIT,
                acceptance._BROWSER_READY_LIMIT,
                acceptance._BROWSER_RESULT_LIMIT,
            ),
            (16_384, 4_096, 16_384),
        )
        arguments = [acceptance.BROWSER_WORKER_HIDDEN_MODE, "3", "4", "10", "11"]
        self.assertEqual(
            acceptance.parse_browser_worker_arguments(arguments),
            acceptance.BrowserWorkerArguments(3, 4, 10, 11),
        )
        for changed in (
            arguments[:-1],
            ["--foreign", *arguments[1:]],
            [acceptance.BROWSER_WORKER_HIDDEN_MODE, "0", "4", "10", "11"],
            [acceptance.BROWSER_WORKER_HIDDEN_MODE, "03", "4", "10", "11"],
            [acceptance.BROWSER_WORKER_HIDDEN_MODE, "+3", "4", "10", "11"],
            [
                acceptance.BROWSER_WORKER_HIDDEN_MODE,
                "1048576",
                "4",
                "10",
                "11",
            ],
            [acceptance.BROWSER_WORKER_HIDDEN_MODE, "3", "3", "10", "11"],
            [
                acceptance.BROWSER_WORKER_HIDDEN_MODE,
                "9" * 5_000,
                "4",
                "10",
                "11",
            ],
        ):
            with self.subTest(changed=changed), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance.parse_browser_worker_arguments(changed)
        descriptors: list[int] = []
        try:
            request_read, request_write = os.pipe2(os.O_CLOEXEC)
            ready_read, ready_write = os.pipe2(os.O_CLOEXEC)
            secret_read, secret_write = os.pipe2(os.O_CLOEXEC)
            result_read, result_write = os.pipe2(os.O_CLOEXEC)
            descriptors.extend(
                (
                    request_read,
                    request_write,
                    ready_read,
                    ready_write,
                    secret_read,
                    secret_write,
                    result_read,
                    result_write,
                )
            )
            exact = acceptance.BrowserWorkerArguments(
                request_read,
                ready_write,
                secret_read,
                result_write,
            )
            self.assertEqual(
                acceptance.validate_browser_worker_descriptors(exact),
                exact,
            )
            self.assertTrue(
                all(
                    not os.get_inheritable(descriptor)
                    for descriptor in (
                        request_read,
                        ready_write,
                        secret_read,
                        result_write,
                    )
                )
            )
            duplicate = os.dup(request_read)
            descriptors.append(duplicate)
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance.validate_browser_worker_descriptors(
                    acceptance.BrowserWorkerArguments(
                        request_read,
                        ready_write,
                        duplicate,
                        result_write,
                    )
                )
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance.validate_browser_worker_descriptors(
                    acceptance.BrowserWorkerArguments(
                        request_write,
                        ready_write,
                        secret_read,
                        result_write,
                    )
                )
            nonblocking_read, nonblocking_write = os.pipe2(
                os.O_CLOEXEC | os.O_NONBLOCK
            )
            descriptors.extend((nonblocking_read, nonblocking_write))
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance.validate_browser_worker_descriptors(
                    acceptance.BrowserWorkerArguments(
                        nonblocking_read,
                        ready_write,
                        secret_read,
                        result_write,
                    )
                )
            with TemporaryDirectory() as temporary:
                fifo_descriptors: list[int] = []
                for index in range(4):
                    fifo = Path(temporary) / f"channel-{index}"
                    os.mkfifo(fifo, 0o600)
                    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
                    writer = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                    acceptance.fcntl.fcntl(reader, acceptance.fcntl.F_SETFL, 0)
                    acceptance.fcntl.fcntl(
                        writer,
                        acceptance.fcntl.F_SETFL,
                        os.O_WRONLY,
                    )
                    fifo_descriptors.extend((reader, writer))
                descriptors.extend(fifo_descriptors)
                named = acceptance.BrowserWorkerArguments(
                    fifo_descriptors[0],
                    fifo_descriptors[3],
                    fifo_descriptors[4],
                    fifo_descriptors[7],
                )
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.validate_browser_worker_descriptors(named)
        finally:
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(acceptance.main(arguments), 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
