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


if __name__ == "__main__":
    unittest.main()
