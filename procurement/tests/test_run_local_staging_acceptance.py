"""Focused contract tests for the LOCAL staging acceptance supervisor."""

from __future__ import annotations

import ast
import base64
import copy
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
import fcntl
import hashlib
import io
from io import StringIO
import json
import os
from pathlib import Path
import selectors
import stat
import sys
import tarfile
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

import run_local_staging_acceptance as acceptance
import run_local_staging_browser_worker as browser_worker


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


def _browser_dependency_record_hash(raw: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return "sha256=" + encoded.rstrip(b"=").decode("ascii")


def _browser_dependency_fixture_members() -> dict[str, dict[str, object]]:
    members: dict[str, dict[str, object]] = {}
    for name, version in (("alpha", "1.0"), ("beta", "2.0")):
        dist_info = f"{name}-{version}.dist-info"
        module_path = f"{name}.py"
        metadata_path = f"{dist_info}/METADATA"
        record_path = f"{dist_info}/RECORD"
        module = f"VALUE = {version!r}\n".encode("ascii")
        metadata = (
            "Metadata-Version: 2.4\n"
            f"Name: {name}\n"
            f"Version: {version}\n\n"
        ).encode("ascii")
        record = (
            f"{module_path},{_browser_dependency_record_hash(module)},"
            f"{len(module)}\n"
            f"{metadata_path},{_browser_dependency_record_hash(metadata)},"
            f"{len(metadata)}\n"
            f"{record_path},,\n"
        ).encode("ascii")
        if name == "alpha":
            script = b"alpha-script"
            record += (
                "../../../bin/alpha,"
                f"{_browser_dependency_record_hash(script)},{len(script)}\n"
            ).encode("ascii")
        members[dist_info] = {
            "kind": "D",
            "mode": 0o755,
            "uid": 0,
            "gid": 0,
            "content": b"",
        }
        for path, content in (
            (module_path, module),
            (metadata_path, metadata),
            (record_path, record),
        ):
            members[path] = {
                "kind": "F",
                "mode": 0o644,
                "uid": 0,
                "gid": 0,
                "content": content,
            }
    members["_virtualenv.pth"] = {
        "kind": "F",
        "mode": 0o644,
        "uid": 0,
        "gid": 0,
        "content": b"import _virtualenv",
    }
    return members


def _browser_dependency_fixture_archive(
    members: dict[str, dict[str, object]],
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        root.uid = 0
        root.gid = 0
        root.mtime = 0
        archive.addfile(root)
        for path, value in sorted(members.items()):
            item = tarfile.TarInfo(f"./{path}")
            item.mode = value["mode"]
            item.uid = value["uid"]
            item.gid = value["gid"]
            item.mtime = 0
            if value["kind"] == "D":
                item.type = tarfile.DIRTYPE
                archive.addfile(item)
            elif value["kind"] == "L":
                item.type = tarfile.SYMTYPE
                item.linkname = value["target"]
                archive.addfile(item)
            else:
                content = value["content"]
                item.type = tarfile.REGTYPE
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
    return output.getvalue()


def _browser_dependency_fixture_parameters(
    members: dict[str, dict[str, object]],
) -> dict[str, object]:
    digest = hashlib.sha256(acceptance._BROWSER_DEPENDENCY_TREE_DOMAIN)
    files = 0
    directories = 0
    regular_bytes = 0
    excluded = {"_virtualenv.pth"}
    for path, value in sorted(members.items()):
        path_bytes = path.encode("ascii")
        content = value["content"]
        if value["kind"] == "D":
            kind = b"D"
            size = 0
            payload = b""
            directories += 1
        else:
            kind = b"F"
            size = len(content)
            payload = hashlib.sha256(content).digest()
            regular_bytes += size
            files += 1
        if path in excluded:
            regular_bytes -= size
            files -= 1
            continue
        digest.update(kind)
        digest.update(len(path_bytes).to_bytes(4, "big"))
        digest.update(path_bytes)
        digest.update(value["mode"].to_bytes(4, "big"))
        digest.update(size.to_bytes(8, "big"))
        digest.update(len(payload).to_bytes(4, "big"))
        digest.update(payload)
    return {
        "source_image_id": IMAGE_ID,
        "expected_sha256": digest.hexdigest(),
        "expected_distributions": (
            ("alpha", "1.0", "alpha-1.0.dist-info"),
            ("beta", "2.0", "beta-2.0.dist-info"),
        ),
        "expected_extras": (
            (
                "_virtualenv.pth",
                len(members["_virtualenv.pth"]["content"]),
                hashlib.sha256(
                    members["_virtualenv.pth"]["content"]
                ).hexdigest(),
            ),
        ),
        "expected_external_records": (
            (
                "alpha-1.0.dist-info/RECORD",
                "../../../bin/alpha",
                _browser_dependency_record_hash(b"alpha-script"),
                str(len(b"alpha-script")),
            ),
        ),
        "expected_entries": len(members) - len(excluded),
        "expected_regular_files": files,
        "expected_directories": directories,
        "expected_regular_bytes": regular_bytes,
        "expected_source_entries": len(members),
        "expected_source_regular_files": files + len(excluded),
        "expected_source_directories": directories,
        "expected_source_regular_bytes": regular_bytes
        + sum(len(members[path]["content"]) for path in excluded),
        "expected_record_rows": 7,
    }


def _browser_dependency_layer_fixture(
    members: dict[str, dict[str, object]],
    *,
    whiteouts: tuple[str, ...] = (),
    root_whiteouts: tuple[str, ...] = (),
    include_target_ancestors: bool = True,
    application_members: dict[str, dict[str, object]] | None = None,
    application_root_mode: int = 0o755,
    application_root_uid: int = 0,
    application_root_gid: int = 0,
) -> bytes:
    output = io.BytesIO()
    target = acceptance._BROWSER_DEPENDENCY_IMAGE_PATH.lstrip("/")
    ancestors = tuple(
        "/".join(target.split("/")[:index])
        for index in range(1, len(target.split("/")) + 1)
    )
    with tarfile.open(
        fileobj=output,
        mode="w",
        format=tarfile.USTAR_FORMAT,
    ) as archive:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        root.uid = 0
        root.gid = 0
        root.mtime = 0
        archive.addfile(root)
        for path in root_whiteouts:
            item = tarfile.TarInfo(path)
            item.type = tarfile.REGTYPE
            item.mode = 0o000
            item.uid = 0
            item.gid = 0
            item.mtime = 0
            item.size = 0
            archive.addfile(item, io.BytesIO())
        for path in ancestors if include_target_ancestors else ():
            item = tarfile.TarInfo(path)
            item.type = tarfile.DIRTYPE
            item.mode = 0o755
            item.uid = 0
            item.gid = 0
            item.mtime = 0
            archive.addfile(item)
        for path in whiteouts:
            item = tarfile.TarInfo(f"{target}/{path}")
            item.type = tarfile.REGTYPE
            item.mode = 0o000
            item.uid = 0
            item.gid = 0
            item.mtime = 0
            item.size = 0
            archive.addfile(item, io.BytesIO())
        for path, value in sorted(members.items()):
            item = tarfile.TarInfo(f"{target}/{path}")
            item.mode = value["mode"]
            item.uid = value.get("uid", 0)
            item.gid = value.get("gid", 0)
            item.mtime = 0
            if value["kind"] == "D":
                item.type = tarfile.DIRTYPE
                archive.addfile(item)
            elif value["kind"] == "L":
                item.type = tarfile.SYMTYPE
                item.linkname = value["target"]
                archive.addfile(item)
            else:
                content = value["content"]
                item.type = tarfile.REGTYPE
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
        if application_members is not None:
            application_root = tarfile.TarInfo("app")
            application_root.type = tarfile.DIRTYPE
            application_root.mode = application_root_mode
            application_root.uid = application_root_uid
            application_root.gid = application_root_gid
            application_root.mtime = 0
            archive.addfile(application_root)
            for path, value in sorted(application_members.items()):
                item = tarfile.TarInfo(f"app/{path}")
                item.mode = value["mode"]
                item.uid = value.get("uid", 0)
                item.gid = value.get("gid", 0)
                item.mtime = 0
                if value["kind"] == "D":
                    item.type = tarfile.DIRTYPE
                    archive.addfile(item)
                elif value["kind"] == "L":
                    item.type = tarfile.SYMTYPE
                    item.linkname = value["target"]
                    archive.addfile(item)
                else:
                    content = value["content"]
                    item.type = tarfile.REGTYPE
                    item.size = len(content)
                    archive.addfile(item, io.BytesIO(content))
    return output.getvalue()


def _browser_dependency_image_export_fixture(
    layers: tuple[bytes, ...],
    *,
    manifest_layers: tuple[str, ...] | None = None,
) -> tuple[bytes, dict[str, object]]:
    layer_ids = tuple(f"sha256:{hashlib.sha256(value).hexdigest()}" for value in layers)
    layer_sizes = tuple(len(value) for value in layers)
    config = json.dumps(
        {
            "architecture": "amd64",
            "config": {},
            "created": "2026-10-06T00:00:00Z",
            "history": [],
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": list(layer_ids)},
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    image_id = f"sha256:{hashlib.sha256(config).hexdigest()}"
    selected_manifest_layers = manifest_layers or layer_ids
    layer_descriptors = [
        {
            "mediaType": "application/vnd.oci.image.layer.v1.tar",
            "digest": value,
            "size": layer_sizes[layer_ids.index(value)],
        }
        for value in selected_manifest_layers
    ]
    oci_manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": image_id,
                "size": len(config),
            },
            "layers": layer_descriptors,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    oci_digest = hashlib.sha256(oci_manifest).hexdigest()
    layer_sources = {
        value: {
            "mediaType": "application/vnd.oci.image.layer.v1.tar",
            "digest": value,
            "size": layer_sizes[index],
        }
        for index, value in enumerate(layer_ids)
    }
    docker_manifest = (
        json.dumps(
            [
                {
                    "Config": f"blobs/sha256/{image_id[7:]}",
                    "RepoTags": None,
                    "Layers": [
                        f"blobs/sha256/{value[7:]}"
                        for value in selected_manifest_layers
                    ],
                    "LayerSources": layer_sources,
                }
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    index = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": f"sha256:{oci_digest}",
                    "size": len(oci_manifest),
                }
            ],
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    blobs = {
        **{value[7:]: layers[index] for index, value in enumerate(layer_ids)},
        image_id[7:]: config,
        oci_digest: oci_manifest,
    }
    output = io.BytesIO()
    with tarfile.open(
        fileobj=output,
        mode="w",
        format=tarfile.USTAR_FORMAT,
    ) as archive:
        for path in ("blobs", "blobs/sha256"):
            item = tarfile.TarInfo(path)
            item.type = tarfile.DIRTYPE
            item.mode = 0o755
            item.uid = 0
            item.gid = 0
            item.mtime = 0
            archive.addfile(item)
        for digest, content in sorted(blobs.items()):
            item = tarfile.TarInfo(f"blobs/sha256/{digest}")
            item.type = tarfile.REGTYPE
            item.mode = 0o644
            item.uid = 0
            item.gid = 0
            item.mtime = 0
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
        for path, content in (
            ("index.json", index),
            ("manifest.json", docker_manifest),
            ("oci-layout", b'{"imageLayoutVersion": "1.0.0"}'),
        ):
            item = tarfile.TarInfo(path)
            item.type = tarfile.REGTYPE
            item.mode = 0o644
            item.uid = 0
            item.gid = 0
            item.mtime = 0
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
    semantic_counts = []
    for layer in layers:
        with tarfile.open(fileobj=io.BytesIO(layer), mode="r:") as archive:
            semantic_counts.append(len(archive.getmembers()))
    return output.getvalue(), {
        "source_image_id": image_id,
        "expected_image_id": image_id,
        "expected_layers": layer_ids,
        "expected_layer_sizes": layer_sizes,
        "expected_physical_members": tuple(semantic_counts),
        "expected_semantic_members": tuple(semantic_counts),
        "expected_legacy_blobs": (),
        "expected_oci_manifest_sha256": oci_digest,
        "expected_config_bytes": len(config),
        "expected_oci_manifest_bytes": len(oci_manifest),
        "expected_export_members": len(blobs) + 5,
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


def _browser_descriptor_expectation(
    source_descriptor: int,
    *,
    number: int | None = None,
    target: str | None = None,
    close_on_exec: bool,
) -> acceptance.BrowserWorkerDescriptorExpectation:
    info = os.fstat(source_descriptor)
    position, flags, mount_id, fdinfo_inode = (
        acceptance._read_browser_worker_fd_metadata(
            os.getpid(),
            source_descriptor,
        )
    )
    if fdinfo_inode != info.st_ino:
        raise AssertionError("descriptor fixture identity differs")
    return acceptance.BrowserWorkerDescriptorExpectation(
        number=source_descriptor if number is None else number,
        target=(
            os.readlink(f"/proc/self/fd/{source_descriptor}")
            if target is None
            else target
        ),
        device=info.st_dev,
        inode=info.st_ino,
        mount_id=mount_id,
        position=position,
        status_flags=flags & ~os.O_CLOEXEC,
        close_on_exec=close_on_exec,
    )


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

    def test_browser_worker_runner_is_separate_pinned_sealed_and_inert(self):
        source_path = Path(browser_worker.__file__).resolve(strict=True)
        self.assertEqual(source_path, acceptance._BROWSER_WORKER_RUNNER_SOURCE)
        self.assertNotEqual(source_path, Path(acceptance.__file__).resolve())
        source = source_path.read_bytes()
        self.assertEqual(len(source), 664)
        self.assertEqual(
            hashlib.sha256(source).hexdigest(),
            "f719af79822a3c2caa6b39bf4fdcda5aa0f39ceeced8f246fbcd154781047ff2",
        )
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source.decode("utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(
                    item.name.partition(".")[0] for item in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module.partition(".")[0])
        self.assertTrue(imported.issubset(sys.stdlib_module_names), imported)
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(browser_worker.main([]), 2)
            self.assertEqual(
                browser_worker.main(
                    [acceptance.BROWSER_WORKER_HIDDEN_MODE, "3", "4", "5", "6"]
                ),
                2,
            )
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))

        with acceptance.open_pinned_browser_worker_runner() as runner:
            info = os.fstat(runner.descriptor)
            self.assertEqual(
                os.readlink(f"/proc/self/fd/{runner.descriptor}"),
                "/memfd:buffalo-local-staging-browser-worker (deleted)",
            )
            self.assertEqual(info.st_nlink, 0)
            self.assertEqual(info.st_size, 664)
            self.assertEqual(info.st_mode & 0o777, 0o400)
            self.assertEqual(
                acceptance.fcntl.fcntl(
                    runner.descriptor,
                    acceptance.fcntl.F_GET_SEALS,
                ),
                acceptance._BROWSER_WORKER_RUNNER_SEALS,
            )
            self.assertEqual(
                acceptance._browser_worker_runner_sha256(runner.descriptor),
                acceptance._BROWSER_WORKER_RUNNER_SHA256,
            )
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                copy.copy(runner)
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                copy.deepcopy(runner)
            os.set_inheritable(runner.descriptor, True)
            try:
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance._validate_pinned_browser_worker_runner(runner)
            finally:
                os.set_inheritable(runner.descriptor, False)
            acceptance._validate_pinned_browser_worker_runner(runner)

        for case in ("mode", "hardlink", "symlink", "bytes"):
            with self.subTest(case=case), TemporaryDirectory() as temporary:
                root = Path(temporary)
                candidate = root / "runner.py"
                candidate.write_bytes(source)
                candidate.chmod(0o644)
                selected = candidate
                if case == "mode":
                    candidate.chmod(0o600)
                elif case == "hardlink":
                    selected = root / "runner-alias.py"
                    os.link(candidate, selected)
                elif case == "symlink":
                    selected = root / "runner-alias.py"
                    selected.symlink_to(candidate)
                elif case == "bytes":
                    candidate.write_bytes(b"X" + source[1:])
                with (
                    patch.object(
                        acceptance,
                        "_BROWSER_WORKER_RUNNER_SOURCE",
                        selected,
                    ),
                    self.assertRaises(acceptance.LocalStagingAcceptanceError),
                ):
                    acceptance.open_pinned_browser_worker_runner()

        stale = acceptance.open_pinned_browser_worker_runner()
        stale_descriptor = stale.descriptor
        os.close(stale_descriptor)
        replacement = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
        try:
            if replacement != stale_descriptor:
                os.dup2(replacement, stale_descriptor, inheritable=False)
                os.close(replacement)
                replacement = stale_descriptor
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                stale.close()
            os.fstat(replacement)
        finally:
            os.close(replacement)
            stale.descriptor = -1
            stale._owner_token = None

    def test_browser_worker_launch_is_exact_code_owned_and_inert(self):
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
            arguments = acceptance.BrowserWorkerArguments(
                request_read,
                ready_write,
                secret_read,
                result_write,
            )
            with (
                acceptance.open_pinned_browser_python_executable() as runtime,
                acceptance.open_pinned_browser_worker_runner() as runner,
                patch.object(acceptance.subprocess, "Popen") as spawn,
                patch.object(acceptance, "write_browser_worker_frame") as frame,
                patch.object(acceptance, "write_browser_worker_secret") as secret,
            ):
                launch = acceptance.build_browser_worker_launch(
                    runtime,
                    runner,
                    arguments,
                )
                expected_command = (
                    f"/proc/self/fd/{runtime.descriptor}",
                    "-I",
                    "-S",
                    "-B",
                    "-P",
                    f"/proc/self/fd/{runner.descriptor}",
                    "--internal-browser-worker",
                    str(request_read),
                    str(ready_write),
                    str(secret_read),
                    str(result_write),
                )
                self.assertEqual(launch.command_line, expected_command)
                self.assertEqual(
                    launch.environment,
                    (("LANG", "C.UTF-8"), ("LC_ALL", "C.UTF-8"), ("TZ", "UTC")),
                )
                self.assertEqual(
                    launch.cwd,
                    Path(acceptance.__file__).resolve().parents[2],
                )
                self.assertEqual(
                    launch.pass_fds,
                    (
                        runtime.descriptor,
                        runner.descriptor,
                        request_read,
                        ready_write,
                        secret_read,
                        result_write,
                    ),
                )
                self.assertEqual(launch.arguments, arguments)
                rendered = repr(launch)
                self.assertNotIn(
                    str(acceptance._BROWSER_WORKER_RUNNER_SOURCE),
                    rendered,
                )
                self.assertNotIn("owner-passphrase", rendered)
                spawn.assert_not_called()
                frame.assert_not_called()
                secret.assert_not_called()

                os.lseek(runner.descriptor, 1, os.SEEK_SET)
                try:
                    with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                        acceptance.build_browser_worker_launch(
                            runtime,
                            runner,
                            arguments,
                        )
                finally:
                    os.lseek(runner.descriptor, 0, os.SEEK_SET)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.build_browser_worker_launch(
                        runtime,
                        runner,
                        replace(arguments, request_descriptor=runtime.descriptor),
                    )
        finally:
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def test_browser_python_runtime_source_is_exact_but_non_authorizing(self):
        with (
            patch.object(acceptance.subprocess, "Popen") as spawned,
            patch.object(acceptance, "write_browser_worker_frame") as frame,
            patch.object(acceptance, "write_browser_worker_secret") as secret,
        ):
            observed = acceptance._observe_browser_python_runtime_source()
        self.assertEqual(
            observed,
            acceptance._BrowserPythonRuntimeObservation(
                startup_sha256=(
                    "5f0b49198f006b36808508d976d2b9e35410839e0f34700a208877d815b10934"
                ),
                stdlib_sha256=(
                    "91e877d25cd89b60c1125fbaca143c88ef9d7a06c019ab86de658d9f9f4e4600"
                ),
                stdlib_entries=3_251,
                stdlib_regular_files=3_137,
                stdlib_directories=113,
                stdlib_symlinks=1,
                stdlib_regular_bytes=102_170_195,
                stdlib_zip_absent=True,
                native_sha256=(
                    "f05a1558c91a1a8979a5f9251fc52bc3b0063d1dcfa99661457c54fadf7bab0b"
                ),
                native_entries=304,
                native_regular_files=287,
                native_directories=4,
                native_symlinks=13,
                native_regular_bytes=21_772_212,
                gconv_cache_absent=True,
                locale_archive_absent=True,
                all_source_mounts_read_only=False,
                execution_authority=False,
            ),
        )
        spawned.assert_not_called()
        frame.assert_not_called()
        secret.assert_not_called()

    def test_browser_python_runtime_source_observation_rejects_drift(self):
        def manifest_sha256(
            domain: bytes,
            records: tuple[tuple[bytes, bytes, int, int, bytes], ...],
        ) -> str:
            digest = hashlib.sha256(domain)
            for kind, path, mode, size, payload in sorted(
                records,
                key=lambda item: item[1],
            ):
                digest.update(kind)
                digest.update(len(path).to_bytes(4, "big"))
                digest.update(path)
                digest.update(mode.to_bytes(4, "big"))
                digest.update(size.to_bytes(8, "big"))
                digest.update(len(payload).to_bytes(4, "big"))
                digest.update(payload)
            return digest.hexdigest()

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_one = root / "runtime-one"
            runtime_two = root / "runtime-two"
            runtime_link = root / "runtime-link"
            one_bytes = b"runtime-one"
            two_bytes = b"runtime-two"
            runtime_one.write_bytes(one_bytes)
            runtime_two.write_bytes(two_bytes)
            runtime_one.chmod(0o600)
            runtime_two.chmod(0o600)
            runtime_link.symlink_to(runtime_one)
            link_target = os.fsencode(str(runtime_one))
            user_id = os.geteuid()
            group_id = os.getegid()
            startup_expectations = (
                acceptance._BrowserRuntimeFileExpectation(
                    runtime_one,
                    "F",
                    len(one_bytes),
                    0o600,
                    hashlib.sha256(one_bytes).hexdigest(),
                    user_id,
                    group_id,
                ),
                acceptance._BrowserRuntimeFileExpectation(
                    runtime_two,
                    "F",
                    len(two_bytes),
                    0o600,
                    hashlib.sha256(two_bytes).hexdigest(),
                    user_id,
                    group_id,
                ),
                acceptance._BrowserRuntimeFileExpectation(
                    runtime_link,
                    "L",
                    len(link_target),
                    0o777,
                    os.fsdecode(link_target),
                    user_id,
                    group_id,
                ),
            )
            startup_records = (
                (
                    b"F",
                    os.fsencode(runtime_one),
                    0o600,
                    len(one_bytes),
                    hashlib.sha256(one_bytes).digest(),
                ),
                (
                    b"F",
                    os.fsencode(runtime_two),
                    0o600,
                    len(two_bytes),
                    hashlib.sha256(two_bytes).digest(),
                ),
                (
                    b"L",
                    os.fsencode(runtime_link),
                    0o777,
                    len(link_target),
                    link_target,
                ),
            )
            startup_sha256 = manifest_sha256(
                b"BUFFALO_LOCAL_BROWSER_PYTHON_STARTUP_FILES_V1\0",
                startup_records,
            )

            def observe_startup() -> acceptance._BrowserRuntimeStartupObservation:
                return acceptance._observe_browser_runtime_startup_files(
                    startup_expectations,
                    expected_sha256=startup_sha256,
                )

            startup = observe_startup()
            self.assertEqual(startup.sha256, startup_sha256)
            self.assertFalse(startup.all_source_mounts_read_only)

            with self.subTest(startup="same-size content"):
                runtime_one.write_bytes(b"Runtime-one")
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    observe_startup()
                runtime_one.write_bytes(one_bytes)
            with self.subTest(startup="mode"):
                runtime_one.chmod(0o640)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    observe_startup()
                runtime_one.chmod(0o600)
            with self.subTest(startup="hardlink"):
                alias = root / "runtime-hardlink"
                os.link(runtime_one, alias)
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        observe_startup()
                finally:
                    alias.unlink()
            with self.subTest(startup="symlink target"):
                runtime_link.unlink()
                runtime_link.symlink_to(runtime_two)
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        observe_startup()
                finally:
                    runtime_link.unlink()
                    runtime_link.symlink_to(runtime_one)

            stdlib = root / "stdlib"
            child = stdlib / "sub"
            stdlib.mkdir(mode=0o700)
            child.mkdir(mode=0o700)
            file_a = stdlib / "a.txt"
            file_b = child / "b.bin"
            tree_link = stdlib / "link"
            file_a.write_bytes(b"alpha")
            file_b.write_bytes(b"beta")
            file_a.chmod(0o600)
            file_b.chmod(0o600)
            tree_link.symlink_to("sub/b.bin")
            absent_zip = root / "python.zip"
            tree_records = (
                (
                    b"F",
                    b"a.txt",
                    0o600,
                    5,
                    hashlib.sha256(b"alpha").digest(),
                ),
                (b"L", b"link", 0o777, 9, b"sub/b.bin"),
                (b"D", b"sub", 0o700, 0, b""),
                (
                    b"F",
                    b"sub/b.bin",
                    0o600,
                    4,
                    hashlib.sha256(b"beta").digest(),
                ),
            )
            tree_sha256 = manifest_sha256(
                b"BUFFALO_LOCAL_BROWSER_PYTHON_STDLIB_TREE_V1\0",
                tree_records,
            )

            def observe_tree() -> acceptance._BrowserStdlibObservation:
                return acceptance._observe_browser_stdlib_tree(
                    stdlib,
                    absent_zip,
                    expected_sha256=tree_sha256,
                    expected_entries=4,
                    expected_regular_files=2,
                    expected_directories=1,
                    expected_symlinks=1,
                    expected_regular_bytes=9,
                    expected_user_id=user_id,
                    expected_group_id=group_id,
                    expected_root_mode=0o700,
                )

            tree = observe_tree()
            self.assertEqual(
                tree,
                acceptance._BrowserStdlibObservation(
                    sha256=tree_sha256,
                    entries=4,
                    regular_files=2,
                    directories=1,
                    symlinks=1,
                    regular_bytes=9,
                    all_source_mounts_read_only=False,
                ),
            )

            with self.subTest(stdlib="same-size content"):
                file_a.write_bytes(b"ALPHA")
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    observe_tree()
                file_a.write_bytes(b"alpha")
            with self.subTest(stdlib="mode"):
                file_a.chmod(0o640)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    observe_tree()
                file_a.chmod(0o600)
            with self.subTest(stdlib="hardlink"):
                alias = stdlib / "alias"
                os.link(file_a, alias)
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        observe_tree()
                finally:
                    alias.unlink()
            with self.subTest(stdlib="symlink target"):
                tree_link.unlink()
                tree_link.symlink_to("a.txt")
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        observe_tree()
                finally:
                    tree_link.unlink()
                    tree_link.symlink_to("sub/b.bin")
            with self.subTest(stdlib="missing entry"):
                saved_a = root / "saved-a.txt"
                file_a.rename(saved_a)
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        observe_tree()
                finally:
                    saved_a.rename(file_a)
            with self.subTest(stdlib="extra entry"):
                extra = stdlib / "extra"
                extra.write_bytes(b"extra")
                extra.chmod(0o600)
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        observe_tree()
                finally:
                    extra.unlink()
            with self.subTest(stdlib="type change"):
                file_a.unlink()
                file_a.mkdir(mode=0o700)
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        observe_tree()
                finally:
                    file_a.rmdir()
                    file_a.write_bytes(b"alpha")
                    file_a.chmod(0o600)
            with self.subTest(stdlib="zip appearance"):
                absent_zip.write_bytes(b"zip")
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        observe_tree()
                finally:
                    absent_zip.unlink()
            with self.subTest(stdlib="root mode"):
                stdlib.chmod(0o750)
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        observe_tree()
                finally:
                    stdlib.chmod(0o700)

    def test_browser_dependency_image_save_command_is_exact_and_nonmutating(self):
        self.assertEqual(
            tuple(
                (
                    str(
                        path.relative_to(
                            acceptance._BROWSER_DEPENDENCY_REPOSITORY_ROOT
                        )
                    ),
                    size,
                    digest,
                )
                for path, size, digest in acceptance._BROWSER_DEPENDENCY_BUILD_FILES
            ),
            (
                (
                    "uv.lock",
                    34_616,
                    "f8613b17cb90ca5e3070e13c47cd6d53a60a8c314257d5588986cf92d0717be9",
                ),
                (
                    "pyproject.toml",
                    299,
                    "c83fa94b31129a28040b199c4fdb6902287646bf306129066eadcc810fe1d346",
                ),
                (
                    "Dockerfile",
                    4_295,
                    "744c0aa01c187eb0faefa9dfd1a0d6533ab1e794e452b843b3268f4162cc5573",
                ),
                (
                    "procurement/tools/audit_staging_purchasing_browser.py",
                    53_889,
                    "ce106b21b789980886cbadff90e422a7c4b118734c996790c181d754c61c6a86",
                ),
                (
                    "procurement/tools/audit_staging_purchasing_browser.mjs",
                    58_550,
                    "951da82b06202a77a25fa3f219197860cfb24c653a23de551d2b2e242603811c",
                ),
            ),
        )
        with TemporaryDirectory() as temporary:
            with self._docker(Path(temporary)) as docker:
                descriptor = docker.descriptor
                arguments = acceptance._build_browser_dependency_image_save_argv(
                    docker_client=docker,
                )
        self.assertEqual(
            arguments,
            (
                f"/proc/self/fd/{descriptor}",
                "image",
                "save",
                IMAGE_ID,
            ),
        )
        self.assertFalse(
            {
                "create",
                "cp",
                "run",
                "start",
                "exec",
                "events",
                "rm",
            }
            & set(arguments)
        )
        acceptance._validate_browser_dependency_run_id(RUN_ID)
        for invalid in ("", "B" * 32, "b" * 31, "../escape", None):
            with self.subTest(run_id=invalid):
                with self.assertRaises(
                    acceptance.LocalStagingAcceptanceError
                ):
                    acceptance._validate_browser_dependency_run_id(invalid)

    def test_browser_dependency_image_export_is_exact_and_bounded(self):
        application = {
            "source": {
                "kind": "D",
                "mode": 0o755,
                "content": b"",
            },
            "source/main.py": {
                "kind": "F",
                "mode": 0o644,
                "content": b"APP = 1\n",
            },
        }
        layer = _browser_dependency_layer_fixture(
            {
                "package": {
                    "kind": "D",
                    "mode": 0o755,
                    "content": b"",
                },
                "package/value.py": {
                    "kind": "F",
                    "mode": 0o644,
                    "content": b"VALUE = 1\n",
                },
            },
            application_members=application,
        )
        image, parameters = _browser_dependency_image_export_fixture((layer,))
        application_digest = hashlib.sha256(
            acceptance._BROWSER_APPLICATION_TREE_DOMAIN
        )
        for path, value in sorted(application.items()):
            content = value["content"]
            kind = value["kind"].encode("ascii")
            payload = (
                hashlib.sha256(content).digest()
                if value["kind"] == "F"
                else b""
            )
            acceptance._update_browser_runtime_manifest(
                application_digest,
                kind=kind,
                path=path.encode("ascii"),
                mode=value["mode"],
                size=len(content),
                payload=payload,
            )
        parameters.update(
            expected_application_sha256=application_digest.hexdigest(),
            expected_application_entries=2,
            expected_application_files=1,
            expected_application_directories=1,
            expected_application_bytes=len(b"APP = 1\n"),
            expected_application_layer=hashlib.sha256(layer).hexdigest(),
        )
        observation = acceptance._BrowserDependencySourceObservation(
            image_id=parameters["source_image_id"],
            tree_sha256="0" * 64,
            entries=2,
            regular_files=1,
            directories=1,
            symlinks=0,
            regular_bytes=len(b"VALUE = 1\n"),
            source_entries=2,
            source_regular_files=1,
            source_directories=1,
            source_regular_bytes=len(b"VALUE = 1\n"),
            record_rows=0,
            distributions=(),
            excluded_source_files=(),
            execution_authority=False,
        )
        with patch.object(
            acceptance,
            "_observe_browser_dependency_archive",
            return_value=observation,
        ) as observed_tree:
            snapshot = acceptance._parse_browser_dependency_image_export(
                image,
                **parameters,
            )
        self.assertEqual(snapshot.observation, observation)
        self.assertEqual(
            tuple(value.path for value in snapshot.source_entries),
            ("package", "package/value.py"),
        )
        self.assertEqual(snapshot.source_entries, snapshot.selected_entries)
        self.assertEqual(
            tuple(value.path for value in snapshot.application_entries),
            ("source", "source/main.py"),
        )
        self.assertEqual(
            snapshot.dependency_root,
            acceptance._BrowserDependencyTreeEntry(
                "",
                "D",
                0o755,
                0,
                0,
                snapshot.rootfs_layers[-1][7:],
                b"",
            ),
        )
        self.assertEqual(snapshot.application_root, snapshot.dependency_root)
        self.assertEqual(
            snapshot.application_tree_sha256,
            application_digest.hexdigest(),
        )
        self.assertFalse(snapshot.execution_authority)
        observed_tree.assert_called_once()

        layer_two = _browser_dependency_layer_fixture(
            {
                "second.py": {
                    "kind": "F",
                    "mode": 0o644,
                    "content": b"SECOND = 2\n",
                }
            }
        )
        ordered_ids = tuple(
            f"sha256:{hashlib.sha256(value).hexdigest()}"
            for value in (layer, layer_two)
        )
        wrong_order, wrong_parameters = (
            _browser_dependency_image_export_fixture(
                (layer, layer_two),
                manifest_layers=tuple(reversed(ordered_ids)),
            )
        )
        invalid_archives = (
            ("truncated", image[:-tarfile.BLOCKSIZE]),
            (
                "layout drift",
                image.replace(b'"1.0.0"', b'"1.0.1"', 1),
            ),
            ("trailing data", image + b"x" * tarfile.BLOCKSIZE),
        )
        for label, selected in invalid_archives:
            with self.subTest(image=label):
                with self.assertRaises(
                    acceptance.LocalStagingAcceptanceError
                ):
                    acceptance._parse_browser_dependency_image_export(
                        selected,
                        **parameters,
                    )
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._parse_browser_dependency_image_export(
                wrong_order,
                **wrong_parameters,
            )
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._parse_browser_dependency_image_export(
                image,
                **{
                    **parameters,
                    "source_image_id": "sha256:" + "f" * 64,
                },
            )

    def test_browser_dependency_layers_apply_whiteouts_safely(self):
        first = _browser_dependency_layer_fixture(
            {
                "gone.py": {
                    "kind": "F",
                    "mode": 0o644,
                    "content": b"gone\n",
                },
                "package": {
                    "kind": "D",
                    "mode": 0o755,
                    "content": b"",
                },
                "package/old.py": {
                    "kind": "F",
                    "mode": 0o644,
                    "content": b"old\n",
                },
            }
        )
        second = _browser_dependency_layer_fixture(
            {
                "package": {
                    "kind": "D",
                    "mode": 0o755,
                    "content": b"",
                },
                "package/new.py": {
                    "kind": "F",
                    "mode": 0o644,
                    "content": b"new\n",
                },
                "same.py": {
                    "kind": "F",
                    "mode": 0o644,
                    "content": b"replacement\n",
                },
            },
            whiteouts=(
                ".wh.gone.py",
                "package/.wh..wh..opq",
                ".wh.same.py",
            ),
        )

        def apply(layer, entries, root):
            digest = hashlib.sha256(layer).hexdigest()
            with tarfile.open(fileobj=io.BytesIO(layer), mode="r:") as archive:
                members = len(archive.getmembers())
            return acceptance._apply_browser_dependency_layer(
                entries,
                root,
                layer,
                layer_sha256=digest,
                expected_bytes=len(layer),
                expected_physical_members=members,
                expected_semantic_members=members,
            )

        entries, root = apply(first, {}, None)
        root_opaque = _browser_dependency_layer_fixture(
            {},
            root_whiteouts=(".wh..wh..opq",),
            include_target_ancestors=False,
        )
        opaque_entries, opaque_root = apply(
            root_opaque,
            dict(entries),
            root,
        )
        self.assertEqual(opaque_entries, {})
        self.assertIsNone(opaque_root)
        entries, root = apply(second, entries, root)
        self.assertIsNotNone(root)
        self.assertEqual(
            tuple(entries),
            ("package", "package/new.py", "same.py"),
        )
        self.assertEqual(entries["same.py"].content, b"replacement\n")
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._apply_browser_dependency_layer(
                {},
                None,
                None,
                layer_sha256="0" * 64,
                expected_bytes=0,
                expected_physical_members=0,
                expected_semantic_members=0,
            )

        invalid_layers = (
            _browser_dependency_layer_fixture(
                {
                    ".wh.bad": {
                        "kind": "F",
                        "mode": 0o000,
                        "content": b"not-empty",
                    }
                }
            ),
            _browser_dependency_layer_fixture(
                {
                    "linked": {
                        "kind": "L",
                        "mode": 0o777,
                        "target": "/escape",
                        "content": b"",
                    }
                }
            ),
            _browser_dependency_layer_fixture(
                {
                    "../escape": {
                        "kind": "F",
                        "mode": 0o644,
                        "content": b"escape",
                    }
                }
            ),
        )
        for selected in invalid_layers:
            with self.subTest(layer=hashlib.sha256(selected).hexdigest()):
                with self.assertRaises(
                    acceptance.LocalStagingAcceptanceError
                ):
                    apply(selected, {}, None)


    def test_browser_dependency_archive_is_bounded_owned_and_canonical(self):
        members = _browser_dependency_fixture_members()
        parameters = _browser_dependency_fixture_parameters(members)
        observed = acceptance._observe_browser_dependency_archive(
            _browser_dependency_fixture_archive(members),
            **parameters,
        )
        self.assertEqual(observed.tree_sha256, parameters["expected_sha256"])
        self.assertEqual(observed.entries, 8)
        self.assertEqual(observed.regular_files, 6)
        self.assertEqual(observed.directories, 2)
        self.assertEqual(observed.source_entries, 9)
        self.assertEqual(observed.record_rows, 7)
        self.assertEqual(
            observed.excluded_source_files[0][0],
            "_virtualenv.pth",
        )
        self.assertFalse(observed.execution_authority)

        simple_mutations = (
            (
                "same-size content",
                lambda value: value["alpha.py"].update(
                    content=b"VALUE = '9.9'\n"
                ),
            ),
            ("mode", lambda value: value["alpha.py"].update(mode=0o755)),
            ("owner", lambda value: value["alpha.py"].update(uid=1)),
            ("missing", lambda value: value.pop("alpha.py")),
            (
                "extra",
                lambda value: value.update(
                    {
                        "extra.py": {
                            "kind": "F",
                            "mode": 0o644,
                            "uid": 0,
                            "gid": 0,
                            "content": b"extra\n",
                        }
                    }
                ),
            ),
            (
                "type",
                lambda value: value["alpha.py"].update(
                    kind="D", mode=0o755, content=b""
                ),
            ),
        )
        for label, mutate in simple_mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(members)
                mutate(changed)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance._observe_browser_dependency_archive(
                        _browser_dependency_fixture_archive(changed),
                        **parameters,
                    )

        for path in ("bad\nname", "bad\tname", "bad\rname", "bad\x7fname"):
            with self.subTest(path=path), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance._canonical_browser_dependency_relative_path(path)

        alias = copy.deepcopy(members)
        alias_path = "alpha-1.0.dist-info/RECORD"
        alias_record = bytearray(alias[alias_path]["content"])
        marker = alias_record.index(b"sha256=") + len(b"sha256=")
        last = marker + 42
        self.assertIn(alias_record[last], b"AEIMQUYcgkosw048")
        alias_record[last] += 1
        alias[alias_path]["content"] = bytes(alias_record)

        unicode_size = copy.deepcopy(members)
        unicode_path = "alpha-1.0.dist-info/RECORD"
        unicode_lines = unicode_size[unicode_path]["content"].decode().splitlines()
        fields = unicode_lines[0].split(",")
        fields[2] = fields[2].translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
        unicode_lines[0] = ",".join(fields)
        unicode_size[unicode_path]["content"] = (
            "\n".join(unicode_lines) + "\n"
        ).encode("utf-8")

        cross_owned = copy.deepcopy(members)
        alpha_record = "alpha-1.0.dist-info/RECORD"
        beta_record = "beta-2.0.dist-info/RECORD"
        alpha_lines = cross_owned[alpha_record]["content"].splitlines()
        beta_lines = cross_owned[beta_record]["content"].splitlines()
        alpha_lines[1], beta_lines[1] = beta_lines[1], alpha_lines[1]
        cross_owned[alpha_record]["content"] = b"\n".join(alpha_lines) + b"\n"
        cross_owned[beta_record]["content"] = b"\n".join(beta_lines) + b"\n"

        cross_self = copy.deepcopy(members)
        alpha_lines = cross_self[alpha_record]["content"].splitlines()
        beta_lines = cross_self[beta_record]["content"].splitlines()
        alpha_lines[2], beta_lines[2] = beta_lines[2], alpha_lines[2]
        cross_self[alpha_record]["content"] = b"\n".join(alpha_lines) + b"\n"
        cross_self[beta_record]["content"] = b"\n".join(beta_lines) + b"\n"

        duplicate_metadata = copy.deepcopy(members)
        metadata_path = "alpha-1.0.dist-info/METADATA"
        duplicate_metadata[metadata_path]["content"] = (
            b"Metadata-Version: 2.4\n"
            + duplicate_metadata[metadata_path]["content"]
        )

        forbidden_pth = copy.deepcopy(members)
        forbidden_pth["alpha.pth"] = forbidden_pth.pop("alpha.py")
        forbidden_pth[alpha_record]["content"] = forbidden_pth[alpha_record][
            "content"
        ].replace(b"alpha.py,", b"alpha.pth,")

        for label, changed in (
            ("base64 pad bits", alias),
            ("unicode size", unicode_size),
            ("cross-owned metadata", cross_owned),
            ("cross-owned self", cross_self),
            ("duplicate metadata version", duplicate_metadata),
            ("selected pth", forbidden_pth),
        ):
            with self.subTest(label=label), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance._observe_browser_dependency_archive(
                    _browser_dependency_fixture_archive(changed),
                    **_browser_dependency_fixture_parameters(changed),
                )

        too_many = io.BytesIO()
        with tarfile.open(fileobj=too_many, mode="w") as archive:
            for index in range(acceptance._BROWSER_DEPENDENCY_ARCHIVE_MEMBERS + 1):
                item = tarfile.TarInfo("." if index == 0 else f"./d{index}")
                item.type = tarfile.DIRTYPE
                item.mode = 0o755
                archive.addfile(item)
        huge = tarfile.TarInfo("./huge")
        huge.type = tarfile.REGTYPE
        huge.mode = 0o644
        huge.size = 2**63
        malformed_huge = huge.tobuf(format=tarfile.GNU_FORMAT) + b"\0" * 1024
        long_name = tarfile.TarInfo("././@LongLink")
        long_name.type = tarfile.GNUTYPE_LONGNAME
        long_name.mode = 0o644
        long_name.size = 2
        long_name_block = (
            long_name.tobuf(format=tarfile.GNU_FORMAT)
            + b"x\0"
            + b"\0" * (tarfile.BLOCKSIZE - 2)
        )
        hidden_members = (
            long_name_block
            * (acceptance._BROWSER_DEPENDENCY_ARCHIVE_MEMBERS + 1)
            + b"\0" * (2 * tarfile.BLOCKSIZE)
        )
        for label, raw in (
            ("member ceiling", too_many.getvalue()),
            ("base256 overflow", malformed_huge),
            ("hidden GNU member ceiling", hidden_members),
        ):
            with self.subTest(label=label), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance._observe_browser_dependency_archive(
                    raw,
                    source_image_id=IMAGE_ID,
                )

    def test_browser_dependency_image_observation_fails_closed_without_daemon_residue(
        self,
    ):
        sentinel = acceptance._BrowserDependencyImageSnapshot(
            image_id=IMAGE_ID,
            rootfs_layers=acceptance.FROZEN_IMAGE_ROOTFS_LAYERS,
            source_entries=(),
            selected_entries=(),
            observation=acceptance._BrowserDependencySourceObservation(
                image_id=IMAGE_ID,
                tree_sha256="0" * 64,
                entries=0,
                regular_files=0,
                directories=0,
                symlinks=0,
                regular_bytes=0,
                source_entries=0,
                source_regular_files=0,
                source_directories=0,
                source_regular_bytes=0,
                record_rows=0,
                distributions=(),
                excluded_source_files=(),
                execution_authority=False,
            ),
            execution_authority=False,
        )
        client = object()
        call_ledger = []
        lifecycle_ledger = []

        source_descriptor = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
        try:
            with (
                patch.object(
                    acceptance,
                    "_BROWSER_DEPENDENCY_BUILD_FILES",
                    ((Path("/unused"), 0, "0" * 64),),
                ),
                patch.object(
                    acceptance.os,
                    "open",
                    return_value=source_descriptor,
                ),
                patch.object(
                    acceptance.os,
                    "fstat",
                    side_effect=KeyboardInterrupt(),
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                acceptance._open_browser_dependency_build_sources()
            with self.assertRaises(OSError):
                fcntl.fcntl(source_descriptor, fcntl.F_GETFD)
            source_descriptor = -1
        finally:
            if source_descriptor >= 0:
                os.close(source_descriptor)

        def docker_argv(_client, _root, arguments, **kwargs):
            call_ledger.append((arguments, kwargs))
            return acceptance.BoundedProcessResult(0, b"image-export", b"")

        with (
            patch.object(
                acceptance,
                "_open_browser_dependency_build_sources",
                side_effect=lambda: (
                    lifecycle_ledger.append("open") or ("held",)
                ),
            ),
            patch.object(
                acceptance,
                "_close_browser_dependency_build_sources",
                side_effect=lambda _sources: lifecycle_ledger.append("close"),
            ) as closed,
            patch.object(
                acceptance,
                "_validate_browser_dependency_build_sources",
                side_effect=lambda _sources: lifecycle_ledger.append("validate"),
            ) as sources,
            patch.object(
                acceptance,
                "_read_browser_dependency_audit_entries",
                side_effect=lambda _sources: (
                    lifecycle_ledger.append("audit") or ()
                ),
            ) as audits,
            patch.object(
                acceptance,
                "attest_docker_materializer_runtime",
                side_effect=lambda _client, _root: lifecycle_ledger.append(
                    "attest"
                ),
            ) as attested,
            patch.object(
                acceptance,
                "_build_browser_dependency_image_save_argv",
                return_value=("/proc/self/fd/99", "image", "save", IMAGE_ID),
            ),
            patch.object(
                acceptance,
                "run_docker_argv",
                side_effect=docker_argv,
            ),
            patch.object(
                acceptance,
                "_parse_browser_dependency_image_export",
                side_effect=lambda _raw: (
                    lifecycle_ledger.append("parse") or sentinel
                ),
            ) as parsed,
        ):
            result = acceptance._observe_frozen_browser_dependency_source(
                client,
                Path("/unused"),
                run_id=RUN_ID,
            )
        self.assertEqual(result, sentinel)
        attested.assert_called_once_with(client, Path("/unused"))
        self.assertEqual(
            call_ledger,
            [
                (
                    ("/proc/self/fd/99", "image", "save", IMAGE_ID),
                    {
                        "timeout_seconds": (
                            acceptance._BROWSER_DEPENDENCY_IMAGE_EXPORT_TIMEOUT
                        ),
                        "stdout_limit": (
                            acceptance._BROWSER_DEPENDENCY_IMAGE_EXPORT_LIMIT
                        ),
                        "stderr_limit": acceptance._DOCKER_METADATA_LIMIT,
                    },
                )
            ],
        )
        self.assertEqual(sources.call_count, 2)
        audits.assert_called_once_with(("held",))
        parsed.assert_called_once_with(b"image-export")
        closed.assert_called_once_with(("held",))
        self.assertEqual(
            lifecycle_ledger,
            [
                "open",
                "attest",
                "validate",
                "audit",
                "parse",
                "validate",
                "close",
            ],
        )

        for failure in (
            acceptance.LocalStagingAcceptanceError("timeout"),
            KeyboardInterrupt(),
        ):
            with self.subTest(failure=type(failure).__name__):
                with (
                    patch.object(
                        acceptance,
                        "_open_browser_dependency_build_sources",
                        return_value=("held",),
                    ),
                    patch.object(
                        acceptance,
                        "_close_browser_dependency_build_sources",
                    ) as failure_closed,
                    patch.object(
                        acceptance,
                        "attest_docker_materializer_runtime",
                    ),
                    patch.object(
                        acceptance,
                        "_build_browser_dependency_image_save_argv",
                        return_value=(
                            "/proc/self/fd/99",
                            "image",
                            "save",
                            IMAGE_ID,
                        ),
                    ),
                    patch.object(
                        acceptance,
                        "run_docker_argv",
                        side_effect=failure,
                    ),
                    patch.object(
                        acceptance,
                        "_parse_browser_dependency_image_export",
                    ) as failure_parser,
                    self.assertRaises(type(failure)),
                ):
                    acceptance._observe_frozen_browser_dependency_source(
                        client,
                        Path("/unused"),
                        run_id=RUN_ID,
                    )
                failure_parser.assert_not_called()
                failure_closed.assert_called_once_with(("held",))

        with TemporaryDirectory() as temporary:
            source_root = Path(temporary)
            source_rows = []
            for name, content in (
                ("uv.lock", b"lock"),
                ("pyproject.toml", b"project"),
                ("Dockerfile", b"docker"),
            ):
                path = source_root / name
                path.write_bytes(content)
                path.chmod(0o644)
                source_rows.append(
                    (path, len(content), hashlib.sha256(content).hexdigest())
                )
            descriptors_before = set(os.listdir("/proc/self/fd"))
            source_rows[-1] = (
                source_rows[-1][0],
                source_rows[-1][1],
                "0" * 64,
            )
            with (
                patch.object(
                    acceptance,
                    "_BROWSER_DEPENDENCY_BUILD_FILES",
                    tuple(source_rows),
                ),
                patch.object(
                    acceptance,
                    "attest_docker_materializer_runtime",
                ) as no_docker,
                self.assertRaises(acceptance.LocalStagingAcceptanceError),
            ):
                acceptance._observe_frozen_browser_dependency_source(
                    client,
                    Path("/unused"),
                    run_id=RUN_ID,
                )
            no_docker.assert_not_called()
            self.assertEqual(
                set(os.listdir("/proc/self/fd")),
                descriptors_before,
            )

            source_rows[-1] = (
                source_rows[-1][0],
                source_rows[-1][1],
                hashlib.sha256(b"docker").hexdigest(),
            )
            for drift_phase in ("after-export", "during-parser"):
                with self.subTest(drift_phase=drift_phase):
                    source_rows[-1][0].write_bytes(b"docker")
                    parser = unittest.mock.Mock(return_value=sentinel)

                    def exported(*_args, **_kwargs):
                        if drift_phase == "after-export":
                            source_rows[-1][0].write_bytes(b"change")
                        return acceptance.BoundedProcessResult(
                            0,
                            b"image-export",
                            b"",
                        )

                    def parsed(raw):
                        if drift_phase == "during-parser":
                            source_rows[-1][0].write_bytes(b"change")
                        return parser(raw)

                    with (
                        patch.object(
                            acceptance,
                            "_BROWSER_DEPENDENCY_BUILD_FILES",
                            tuple(source_rows),
                        ),
                        patch.object(
                            acceptance,
                            "attest_docker_materializer_runtime",
                        ),
                        patch.object(
                            acceptance,
                            "_build_browser_dependency_image_save_argv",
                            return_value=(
                                "/proc/self/fd/99",
                                "image",
                                "save",
                                IMAGE_ID,
                            ),
                        ),
                        patch.object(
                            acceptance,
                            "run_docker_argv",
                            side_effect=exported,
                        ),
                        patch.object(
                            acceptance,
                            "_parse_browser_dependency_image_export",
                            side_effect=parsed,
                        ),
                        patch.object(
                            acceptance,
                            "_read_browser_dependency_audit_entries",
                            return_value=(),
                        ),
                        self.assertRaises(
                            acceptance.LocalStagingAcceptanceError
                        ),
                    ):
                        acceptance._observe_frozen_browser_dependency_source(
                            client,
                            Path("/unused"),
                            run_id=RUN_ID,
                        )
                    if drift_phase == "after-export":
                        parser.assert_not_called()
                    else:
                        parser.assert_called_once_with(b"image-export")

    def test_browser_application_tree_is_exact_owned_and_provenance_bound(self):
        layer = "a" * 64
        entries = {
            "package": acceptance._BrowserDependencyTreeEntry(
                "package", "D", 0o755, 0, 0, layer, b""
            ),
            "package/main.py": acceptance._BrowserDependencyTreeEntry(
                "package/main.py", "F", 0o644, 0, 0, layer, b"APP = 1\n"
            ),
        }
        digest = hashlib.sha256(acceptance._BROWSER_APPLICATION_TREE_DOMAIN)
        for entry in entries.values():
            acceptance._update_browser_runtime_manifest(
                digest,
                kind=entry.kind.encode("ascii"),
                path=entry.path.encode("ascii"),
                mode=entry.mode,
                size=len(entry.content),
                payload=(
                    hashlib.sha256(entry.content).digest()
                    if entry.kind == "F"
                    else b""
                ),
            )
        expected = digest.hexdigest()

        def observe(selected):
            return acceptance._browser_application_tree_sha256(
                selected,
                expected_sha256=expected,
                expected_entries=2,
                expected_files=1,
                expected_directories=1,
                expected_bytes=len(b"APP = 1\n"),
                expected_layer=layer,
            )

        self.assertEqual(observe(entries), expected)
        mutations = (
            ("owner", replace(entries["package/main.py"], user_id=1)),
            ("group", replace(entries["package/main.py"], group_id=1)),
            ("layer", replace(entries["package/main.py"], layer_sha256="b" * 64)),
            ("content", replace(entries["package/main.py"], content=b"APP = 2\n")),
            ("content type", replace(entries["package/main.py"], content="x")),
            ("kind type", replace(entries["package/main.py"], kind=[])),
        )
        for label, changed in mutations:
            with self.subTest(label=label):
                selected = dict(entries)
                selected["package/main.py"] = changed
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    observe(selected)
        malformed_entries = (
            replace(entries["package/main.py"], path=[]),
            replace(entries["package/main.py"], kind=[]),
            replace(entries["package/main.py"], content=None),
            replace(entries["package"], content=None),
        )
        for malformed in malformed_entries:
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance._validate_browser_dependency_tree_entry_shape(
                    malformed,
                    allow_empty_path=malformed.path == "",
                )

        invalid_root_layer = _browser_dependency_layer_fixture(
            {"value.py": {"kind": "F", "mode": 0o644, "content": b"x"}},
            application_members={
                "package": {"kind": "D", "mode": 0o755, "content": b""},
                "package/main.py": {
                    "kind": "F",
                    "mode": 0o644,
                    "content": b"APP = 1\n",
                },
            },
            application_root_mode=0o700,
        )
        image, parameters = _browser_dependency_image_export_fixture(
            (invalid_root_layer,)
        )
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._parse_browser_dependency_image_export(
                image,
                **{
                    **parameters,
                    "expected_application_sha256": expected,
                    "expected_application_entries": 2,
                    "expected_application_files": 1,
                    "expected_application_directories": 1,
                    "expected_application_bytes": len(b"APP = 1\n"),
                    "expected_application_layer": hashlib.sha256(
                        invalid_root_layer
                    ).hexdigest(),
                },
            )

    def test_browser_audit_sources_are_copied_from_held_descriptors(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            contents = (
                ("uv.lock", b"lock"),
                ("pyproject.toml", b"project"),
                ("Dockerfile", b"docker"),
                (
                    "procurement/tools/audit_staging_purchasing_browser.py",
                    b"print('audit')\n",
                ),
                (
                    "procurement/tools/audit_staging_purchasing_browser.mjs",
                    b"console.log('audit');\n",
                ),
            )
            rows = []
            for relative, content in contents:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                path.chmod(0o644)
                rows.append((path, len(content), hashlib.sha256(content).hexdigest()))
            with (
                patch.object(
                    acceptance,
                    "_BROWSER_DEPENDENCY_REPOSITORY_ROOT",
                    root,
                ),
                patch.object(
                    acceptance,
                    "_BROWSER_DEPENDENCY_BUILD_FILES",
                    tuple(rows),
                ),
            ):
                sources = acceptance._open_browser_dependency_build_sources()
                try:
                    observed = acceptance._read_browser_dependency_audit_entries(
                        sources
                    )
                    self.assertEqual(
                        tuple(entry.path for entry in observed),
                        tuple(value[0] for value in contents[-2:]),
                    )
                    self.assertEqual(
                        tuple(entry.content for entry in observed),
                        tuple(value[1] for value in contents[-2:]),
                    )
                    self.assertTrue(
                        all(
                            entry.mode == 0o644
                            and entry.layer_sha256
                            == hashlib.sha256(entry.content).hexdigest()
                            for entry in observed
                        )
                    )
                    changed = rows[-1][0]
                    displaced = changed.with_suffix(".saved")
                    changed.rename(displaced)
                    changed.write_bytes(contents[-1][1])
                    changed.chmod(0o644)
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        acceptance._read_browser_dependency_audit_entries(
                            sources
                        )
                finally:
                    acceptance._close_browser_dependency_build_sources(sources)

    def test_browser_python_runtime_snapshot_retains_exact_bytes(self):
        snapshot = acceptance._snapshot_browser_python_runtime_source()
        acceptance._validate_exact_browser_python_runtime_snapshot(snapshot)
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._validate_exact_browser_python_runtime_snapshot(
                replace(snapshot, entries=None)
            )
        self.assertFalse(snapshot.execution_authority)
        self.assertEqual(snapshot.observation.stdlib_entries, 3_251)
        self.assertEqual(snapshot.observation.native_entries, 304)
        self.assertEqual(len(snapshot.entries), 3_564)
        by_path = {entry.path: entry for entry in snapshot.entries}
        python = by_path[str(acceptance._BROWSER_PYTHON_EXECUTABLE)]
        self.assertEqual(
            hashlib.sha256(python.content).hexdigest(),
            acceptance._BROWSER_PYTHON_SHA256,
        )
        stdlib_link = by_path[
            str(acceptance._BROWSER_RUNTIME_STDLIB_ROOT / "site-packages")
            + "/_sysconfigdata__linux_x86_64-linux-gnu.py"
        ]
        self.assertEqual(
            stdlib_link.content,
            b"../_sysconfigdata__linux_x86_64-linux-gnu.py",
        )
        self.assertNotIn(str(acceptance._BROWSER_RUNTIME_STDLIB_ZIP), by_path)
        self.assertNotIn(str(acceptance._BROWSER_RUNTIME_GCONV_CACHE), by_path)
        self.assertNotIn(str(acceptance._BROWSER_RUNTIME_LOCALE_ARCHIVE), by_path)
        self.assertEqual(
            acceptance._browser_native_runtime_sha256(
                tuple(
                    entry
                    for entry in snapshot.entries
                    if entry.provenance.startswith("native-runtime:")
                )
            ),
            acceptance._BROWSER_RUNTIME_NATIVE_SHA256,
        )
        native_path = str(
            acceptance._BROWSER_RUNTIME_NATIVE_FILES[0].path
        )
        native_entry = by_path[native_path]
        changed_native = tuple(
            replace(entry, content=entry.content + b"x")
            if entry.path == native_path
            else entry
            for entry in snapshot.entries
        )
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._validate_exact_browser_python_runtime_snapshot(
                replace(snapshot, entries=changed_native)
            )
        self.assertEqual(by_path[native_path], native_entry)
        self.assertTrue(
            all(
                entry.provenance.startswith(
                    (
                        "python-startup:",
                        "python-stdlib:",
                        "native-runtime:",
                    )
                )
                for entry in snapshot.entries
            )
        )

    def test_browser_runtime_bundle_namespace_is_exact_and_provenance_bound(self):
        E = acceptance._BrowserRuntimeBundleEntry
        runtime = acceptance._BrowserPythonRuntimeSnapshot(
            entries=(
                E(
                    "/nix/runtime",
                    "F",
                    0o555,
                    1000,
                    1000,
                    "python-startup:" + "1" * 64,
                    b"runtime",
                ),
            ),
            observation=acceptance._BrowserPythonRuntimeObservation(
                startup_sha256=acceptance._BROWSER_RUNTIME_STARTUP_SHA256,
                stdlib_sha256=acceptance._BROWSER_RUNTIME_STDLIB_SHA256,
                stdlib_entries=acceptance._BROWSER_RUNTIME_STDLIB_ENTRIES,
                stdlib_regular_files=acceptance._BROWSER_RUNTIME_STDLIB_FILES,
                stdlib_directories=acceptance._BROWSER_RUNTIME_STDLIB_DIRECTORIES,
                stdlib_symlinks=acceptance._BROWSER_RUNTIME_STDLIB_SYMLINKS,
                stdlib_regular_bytes=acceptance._BROWSER_RUNTIME_STDLIB_BYTES,
                stdlib_zip_absent=True,
                native_sha256=acceptance._BROWSER_RUNTIME_NATIVE_SHA256,
                native_entries=acceptance._BROWSER_RUNTIME_NATIVE_ENTRIES,
                native_regular_files=(
                    acceptance._BROWSER_RUNTIME_NATIVE_REGULAR_FILES
                ),
                native_directories=(
                    acceptance._BROWSER_RUNTIME_NATIVE_DIRECTORIES
                ),
                native_symlinks=acceptance._BROWSER_RUNTIME_NATIVE_SYMLINKS,
                native_regular_bytes=acceptance._BROWSER_RUNTIME_NATIVE_BYTES,
                gconv_cache_absent=True,
                locale_archive_absent=True,
                all_source_mounts_read_only=False,
                execution_authority=False,
            ),
            execution_authority=False,
        )
        tree_entry = acceptance._BrowserDependencyTreeEntry
        application = tree_entry("pkg", "D", 0o755, 0, 0, "2" * 64, b"")
        dependency_entry = tree_entry(
            "dep.py", "F", 0o644, 0, 0, "3" * 64, b"dep"
        )
        audits = (
            tree_entry(
                "procurement/tools/audit.py",
                "F",
                0o644,
                1000,
                1000,
                "4" * 64,
                b"audit",
            ),
            tree_entry(
                "procurement/tools/audit.mjs",
                "F",
                0o644,
                1000,
                1000,
                "5" * 64,
                b"audit-js",
            ),
        )
        dependency = acceptance._BrowserDependencyImageSnapshot(
            image_id=IMAGE_ID,
            rootfs_layers=(),
            source_entries=(),
            selected_entries=(dependency_entry,),
            observation=unittest.mock.sentinel.observation,
            execution_authority=False,
            application_entries=(application,),
            application_tree_sha256="6" * 64,
            audit_entries=audits,
            dependency_root=tree_entry(
                "", "D", 0o755, 0, 0, "3" * 64, b""
            ),
            application_root=tree_entry(
                "", "D", 0o755, 0, 0, "2" * 64, b""
            ),
        )
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._validate_exact_browser_dependency_snapshot(
                replace(
                    dependency,
                    image_id=acceptance.FROZEN_IMAGE_ID,
                    rootfs_layers=acceptance.FROZEN_IMAGE_ROOTFS_LAYERS,
                    application_tree_sha256=(
                        acceptance._BROWSER_APPLICATION_TREE_SHA256
                    ),
                    source_entries=None,
                )
            )
        with patch.object(
            acceptance,
            "_validate_exact_browser_dependency_snapshot",
        ), patch.object(
            acceptance,
            "_validate_exact_browser_python_runtime_snapshot",
        ):
            entries = acceptance._collect_browser_runtime_bundle_entries(
                runtime,
                dependency,
            )
        self.assertEqual(
            tuple(entry.path for entry in entries),
            (
                "/nix",
                "/nix/runtime",
                "/runtime",
                "/runtime/pkg",
                "/runtime/procurement",
                "/runtime/procurement/tools",
                "/runtime/procurement/tools/audit.mjs",
                "/runtime/procurement/tools/audit.py",
                "/runtime/site-packages",
                "/runtime/site-packages/dep.py",
            ),
        )
        self.assertEqual(
            tuple(entry.provenance.split(":", 1)[0] for entry in entries),
            (
                "bundle-parent",
                "python-startup",
                "application-layer",
                "application-layer",
                "bundle-parent",
                "bundle-parent",
                "audit-source",
                "audit-source",
                "dependency-layer",
                "dependency-layer",
            ),
        )
        collision = replace(
            dependency,
            audit_entries=(
                replace(audits[0], path="pkg"),
                audits[1],
            ),
        )
        with (
            patch.object(
                acceptance,
                "_validate_exact_browser_dependency_snapshot",
            ),
            patch.object(
                acceptance,
                "_validate_exact_browser_python_runtime_snapshot",
            ),
            self.assertRaises(acceptance.LocalStagingAcceptanceError),
        ):
            acceptance._collect_browser_runtime_bundle_entries(
                runtime,
                collision,
            )
        provenance = hashlib.sha256(
            acceptance._BROWSER_DEPENDENCY_LAYER_PROVENANCE_DOMAIN
        )
        path = dependency_entry.path.encode("ascii")
        provenance.update(len(path).to_bytes(4, "big"))
        provenance.update(path)
        provenance.update(bytes.fromhex(dependency_entry.layer_sha256))
        self.assertEqual(
            acceptance._browser_dependency_layer_provenance_sha256(
                (dependency_entry,),
                expected_sha256=provenance.hexdigest(),
            ),
            provenance.hexdigest(),
        )
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._browser_dependency_layer_provenance_sha256(
                (replace(dependency_entry, layer_sha256="7" * 64),),
                expected_sha256=provenance.hexdigest(),
            )

    def test_browser_runtime_bundle_encoding_is_canonical_and_bounded(self):
        E = acceptance._BrowserRuntimeBundleEntry
        entries = (
            E(
                "/runtime",
                "D",
                0o755,
                0,
                0,
                "application-layer:" + "1" * 64,
                b"",
            ),
            E(
                "/runtime/a",
                "F",
                0o644,
                0,
                0,
                "application-layer:" + "2" * 64,
                b"alpha",
            ),
            E(
                "/runtime/link",
                "L",
                0o777,
                0,
                0,
                "application-layer:" + "3" * 64,
                b"/runtime/a",
            ),
        )
        encoded = acceptance._encode_browser_runtime_bundle(entries)
        shuffled = acceptance._encode_browser_runtime_bundle(tuple(reversed(entries)))
        self.assertEqual(encoded, shuffled)
        parsed, manifest_sha256, bundle_sha256, regular_bytes = (
            acceptance._parse_browser_runtime_bundle(encoded[0])
        )
        self.assertEqual(parsed, entries)
        self.assertEqual(manifest_sha256, encoded[1])
        self.assertEqual(bundle_sha256, encoded[2])
        self.assertEqual(regular_bytes, 5)
        for label, raw in (
            ("truncated", encoded[0][:-1]),
            ("trailing", encoded[0] + b"x"),
            (
                "manifest drift",
                encoded[0][:8]
                + encoded[0][8:].replace(b'"kind":"F"', b'"kind":"X"', 1),
            ),
        ):
            with self.subTest(label=label), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance._parse_browser_runtime_bundle(raw)
        with (
            patch.object(acceptance, "_BROWSER_RUNTIME_BUNDLE_ENTRY_LIMIT", 2),
            self.assertRaises(acceptance.LocalStagingAcceptanceError),
        ):
            acceptance._encode_browser_runtime_bundle(entries)
        with (
            patch.object(acceptance, "_BROWSER_RUNTIME_BUNDLE_BYTES_LIMIT", 4),
            self.assertRaises(acceptance.LocalStagingAcceptanceError),
        ):
            acceptance._encode_browser_runtime_bundle(entries)
        invalid_entries = (
            ("non-text path", (entries[0], replace(entries[1], path=1))),
            (
                "oversized component",
                (entries[0], replace(entries[1], path="/runtime/" + "x" * 256)),
            ),
            (
                "symlink cycle",
                (
                    entries[0],
                    replace(
                        entries[2],
                        path="/runtime/left",
                        content=b"/runtime/right",
                    ),
                    replace(
                        entries[2],
                        path="/runtime/right",
                        content=b"/runtime/left",
                    ),
                ),
            ),
        )
        for label, selected in invalid_entries:
            with self.subTest(label=label), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance._encode_browser_runtime_bundle(selected)
        deep_manifest = (
            b'{"entries":'
            + b"[" * 2_000
            + b"0"
            + b"]" * 2_000
            + b',"protocol":"'
            + acceptance._BROWSER_RUNTIME_BUNDLE_PROTOCOL.encode("ascii")
            + b'"}'
        )
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._parse_browser_runtime_bundle(
                len(deep_manifest).to_bytes(8, "big") + deep_manifest
            )

    def test_pinned_browser_runtime_bundle_is_sealed_owned_and_inert(self):
        E = acceptance._BrowserRuntimeBundleEntry
        entries = (
            E(
                "/runtime",
                "D",
                0o555,
                0,
                0,
                "bundle-parent:"
                + acceptance._BROWSER_RUNTIME_PARENT_POLICY_SHA256,
                b"",
            ),
            E(
                "/runtime/a",
                "F",
                0o644,
                0,
                0,
                "application-layer:" + "1" * 64,
                b"alpha",
            ),
        )
        with (
            patch.object(acceptance.subprocess, "Popen") as spawned,
            patch.object(acceptance, "write_browser_worker_frame") as frame,
            patch.object(acceptance, "write_browser_worker_secret") as secret,
        ):
            with acceptance._seal_browser_runtime_bundle(entries) as bundle:
                acceptance._validate_pinned_browser_runtime_bundle(bundle)
                with self.assertRaises(
                    acceptance.LocalStagingAcceptanceError
                ):
                    acceptance._validate_frozen_browser_runtime_bundle(bundle)
                info = os.fstat(bundle.descriptor)
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o400)
                self.assertEqual(info.st_nlink, 0)
                self.assertEqual(
                    fcntl.fcntl(bundle.descriptor, fcntl.F_GET_SEALS),
                    acceptance._BROWSER_RUNTIME_BUNDLE_SEALS,
                )
                self.assertFalse(bundle.execution_authority)
                with self.assertRaises(OSError):
                    os.write(bundle.descriptor, b"x")
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    copy.copy(bundle)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    copy.deepcopy(bundle)
        spawned.assert_not_called()
        frame.assert_not_called()
        secret.assert_not_called()

        bundle = acceptance._seal_browser_runtime_bundle(entries)
        descriptor = bundle.descriptor
        try:
            with (
                patch.object(
                    acceptance.os,
                    "close",
                    side_effect=KeyboardInterrupt(),
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                bundle.close()
            self.assertEqual(bundle.descriptor, descriptor)
            self.assertIs(bundle._owner_token, acceptance._BROWSER_HANDLE_TOKEN)
            self.assertGreaterEqual(fcntl.fcntl(descriptor, fcntl.F_GETFD), 0)
        finally:
            bundle.close()

        raw, manifest_sha256, bundle_sha256, regular_bytes = (
            acceptance._encode_browser_runtime_bundle(entries)
        )
        with (
            patch.object(
                acceptance,
                "_BROWSER_RUNTIME_BUNDLE_EXPECTED_ENTRIES",
                len(entries),
            ),
            patch.object(
                acceptance,
                "_BROWSER_RUNTIME_BUNDLE_EXPECTED_REGULAR_BYTES",
                regular_bytes,
            ),
            patch.object(
                acceptance,
                "_BROWSER_RUNTIME_BUNDLE_EXPECTED_BYTES",
                len(raw),
            ),
            patch.object(
                acceptance,
                "_BROWSER_RUNTIME_BUNDLE_EXPECTED_MANIFEST_SHA256",
                manifest_sha256,
            ),
            patch.object(
                acceptance,
                "_BROWSER_RUNTIME_BUNDLE_EXPECTED_SHA256",
                bundle_sha256,
            ),
            patch.object(
                acceptance,
                "_snapshot_browser_python_runtime_source",
                return_value=unittest.mock.sentinel.runtime,
            ),
            patch.object(
                acceptance,
                "_observe_frozen_browser_dependency_source",
                return_value=unittest.mock.sentinel.dependency,
            ),
            patch.object(
                acceptance,
                "_collect_browser_runtime_bundle_entries",
                return_value=entries,
            ),
        ):
            with acceptance._open_frozen_browser_runtime_bundle(
                object(),
                Path("/unused"),
                run_id=RUN_ID,
            ) as exact_bundle:
                acceptance._validate_frozen_browser_runtime_bundle(exact_bundle)
                self.assertIs(
                    exact_bundle._source_token,
                    acceptance._BROWSER_FROZEN_RUNTIME_BUNDLE_TOKEN,
                )

    def test_browser_runtime_materialization_failure_closes_descriptors(self):
        E = acceptance._BrowserRuntimeBundleEntry
        entries = (
            E(
                "/runtime",
                "D",
                0o555,
                0,
                0,
                "bundle-parent:"
                + acceptance._BROWSER_RUNTIME_PARENT_POLICY_SHA256,
                b"",
            ),
            E(
                "/runtime/a",
                "F",
                0o644,
                0,
                0,
                "application-layer:" + "1" * 64,
                b"alpha",
            ),
        )
        injections = (
            (acceptance.os, "write"),
            (acceptance.os, "fchmod"),
            (acceptance.fcntl, "fcntl"),
            (acceptance.os, "open"),
            (acceptance, "_validate_pinned_browser_runtime_bundle"),
        )
        for owner, name in injections:
            with self.subTest(point=name):
                before = set(os.listdir("/proc/self/fd"))
                with (
                    patch.object(owner, name, side_effect=KeyboardInterrupt()),
                    patch.object(acceptance.subprocess, "Popen") as spawned,
                    patch.object(
                        acceptance,
                        "write_browser_worker_secret",
                    ) as secret,
                    self.assertRaises(KeyboardInterrupt),
                ):
                    acceptance._seal_browser_runtime_bundle(entries)
                self.assertEqual(set(os.listdir("/proc/self/fd")), before)
                spawned.assert_not_called()
                secret.assert_not_called()

        before = set(os.listdir("/proc/self/fd"))
        with (
            patch.object(acceptance, "_BROWSER_WORKER_MAX_FD", 2),
            self.assertRaises(acceptance.LocalStagingAcceptanceError),
        ):
            acceptance._seal_browser_runtime_bundle(entries)
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)

        result_read, result_write = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(result_read)
            os.close(0)
            outcome = b"0"
            try:
                acceptance._seal_browser_runtime_bundle(entries)
            except acceptance.LocalStagingAcceptanceError:
                try:
                    fcntl.fcntl(0, fcntl.F_GETFD)
                except OSError:
                    outcome = b"1"
            except BaseException:
                outcome = b"E"
            try:
                os.write(result_write, outcome)
            finally:
                os._exit(0)
        os.close(result_write)
        try:
            outcome = os.read(result_read, 2)
        finally:
            os.close(result_read)
        _, child_status = os.waitpid(child, 0)
        self.assertEqual(child_status, 0)
        self.assertEqual(outcome, b"1")

        before = set(os.listdir("/proc/self/fd"))
        real_close = acceptance.os.close
        reused_descriptor = -1
        close_calls = []

        def close_then_reuse(descriptor):
            nonlocal reused_descriptor
            close_calls.append(descriptor)
            if reused_descriptor == -1:
                real_close(descriptor)
                reused_descriptor = os.open(
                    "/dev/null",
                    os.O_RDONLY | os.O_CLOEXEC,
                )
                self.assertEqual(reused_descriptor, descriptor)
                raise KeyboardInterrupt
            real_close(descriptor)

        try:
            with (
                patch.object(
                    acceptance.os,
                    "close",
                    side_effect=close_then_reuse,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                acceptance._seal_browser_runtime_bundle(entries)
            self.assertGreaterEqual(
                fcntl.fcntl(reused_descriptor, fcntl.F_GETFD),
                0,
            )
            self.assertEqual(len(close_calls), 2)
        finally:
            if reused_descriptor >= 0:
                real_close(reused_descriptor)
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)

        real_fstat = acceptance.os.fstat
        for failed_fstat_call in (1, 2):
            with self.subTest(failed_fstat_call=failed_fstat_call):
                before = set(os.listdir("/proc/self/fd"))
                foreign_descriptor = -1
                fstat_calls = 0

                def fstat_then_reuse(descriptor):
                    nonlocal foreign_descriptor, fstat_calls
                    fstat_calls += 1
                    if fstat_calls == failed_fstat_call:
                        real_close(descriptor)
                        foreign_descriptor = os.memfd_create(
                            acceptance._BROWSER_RUNTIME_BUNDLE_MEMFD_NAME_PREFIX,
                            os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
                        )
                        self.assertEqual(foreign_descriptor, descriptor)
                        raise KeyboardInterrupt
                    return real_fstat(descriptor)

                try:
                    with (
                        patch.object(
                            acceptance.os,
                            "fstat",
                            side_effect=fstat_then_reuse,
                        ),
                        self.assertRaises(KeyboardInterrupt),
                    ):
                        acceptance._seal_browser_runtime_bundle(entries)
                    self.assertGreaterEqual(
                        fcntl.fcntl(
                            foreign_descriptor,
                            fcntl.F_GETFD,
                        ),
                        0,
                    )
                finally:
                    if foreign_descriptor >= 0:
                        real_close(foreign_descriptor)
                self.assertEqual(set(os.listdir("/proc/self/fd")), before)

        before = set(os.listdir("/proc/self/fd"))
        fstat_calls = 0

        def interrupt_first_fstat(descriptor):
            nonlocal fstat_calls
            fstat_calls += 1
            if fstat_calls == 1:
                raise KeyboardInterrupt
            return real_fstat(descriptor)

        with (
            patch.object(
                acceptance.os,
                "fstat",
                side_effect=interrupt_first_fstat,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            acceptance._seal_browser_runtime_bundle(entries)
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)


    def test_browser_containment_cgroup_and_nspid_text_is_canonical(self):
        self.assertEqual(
            acceptance.parse_browser_cgroup_path(
                b"0::/system.slice/default.scope/buffalo-run_01\n"
            ),
            "/system.slice/default.scope/buffalo-run_01",
        )
        self.assertEqual(acceptance.parse_browser_cgroup_path(b"0::/\n"), "/")
        self.assertEqual(
            acceptance.parse_browser_namespace_pids(
                b"NSpid:\t120001\t41\t1\n"
            ),
            (120001, 41, 1),
        )
        invalid_cgroups = (
            b"",
            b"0::/x",
            b"0::/x\r\n",
            b"0::/x\0\n",
            b"1:name=/x\n",
            b"0::relative\n",
            b"0::/x//y\n",
            b"0::/x/\n",
            b"0::/./x\n",
            b"0::/../x\n",
            b"0::/white space\n",
            b"0::/x\n0::/y\n",
            b"0::/" + b"a" * 256 + b"\n",
            b"0::/" + b"a/" * 2046 + b"a\n",
            b"0::/\xff\n",
        )
        for raw in invalid_cgroups:
            with self.subTest(cgroup=raw[:40]), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance.parse_browser_cgroup_path(raw)
        invalid_nspids = (
            b"",
            b"NSpid:\t1",
            b"NSpid: 1\n",
            b"NSpid:\t0\n",
            b"NSpid:\t01\n",
            b"NSpid:\t+1\n",
            b"NSpid:\t-1\n",
            b"NSpid:\t2147483648\n",
            b"NSpid:\t1 2\n",
            b"NSpid:\t1\t\n",
            b"NSpid:\t1\r\n",
            b"NSpid:\t1\0\n",
            b"NSpid:\t1\nNSpid:\t2\n",
            b"NSpid:\t" + b"\t".join([b"1"] * 33) + b"\n",
        )
        for raw in invalid_nspids:
            with self.subTest(nspid=raw[:40]), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance.parse_browser_namespace_pids(raw)

    def test_browser_containment_events_and_members_are_exact(self):
        expected_events = acceptance.BrowserCgroupEvents(
            populated=True,
            frozen=False,
        )
        self.assertEqual(
            acceptance.parse_browser_cgroup_events(
                b"populated 1\nfrozen 0\n"
            ),
            expected_events,
        )
        self.assertEqual(
            acceptance.parse_browser_cgroup_events(
                b"frozen 0\npopulated 1\n"
            ),
            expected_events,
        )
        self.assertEqual(acceptance.parse_browser_cgroup_processes(b""), ())
        self.assertEqual(
            acceptance.parse_browser_cgroup_processes(b"12\n3\n9\n"),
            (3, 9, 12),
        )
        self.assertEqual(
            acceptance.parse_browser_cgroup_threads(b"12\n3\n9\n"),
            (3, 9, 12),
        )
        invalid_events = (
            b"",
            b"populated 1\n",
            b"populated 1\nfrozen 0",
            b"populated 1\nfrozen 0\nextra 0\n",
            b"populated 1\npopulated 1\nfrozen 0\n",
            b"populated 2\nfrozen 0\n",
            b"populated 1 \nfrozen 0\n",
            b"populated\t1\nfrozen 0\n",
            b"populated 1\nfrozen 0\r\n",
            b"populated 1\nfrozen 0\0\n",
        )
        for raw in invalid_events:
            with self.subTest(events=raw), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance.parse_browser_cgroup_events(raw)
        invalid_members = (
            b"1",
            b"1\r\n",
            b"1\0\n",
            b"0\n",
            b"01\n",
            b"+1\n",
            b"-1\n",
            b"2147483648\n",
            b"1 2\n",
            b"1\n1\n",
            b"1\n\n",
            b"1\n" * 4_097,
            b"2147483647\n" * 4_097,
        )
        for raw in invalid_members:
            with self.subTest(members=raw[:40]), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance.parse_browser_cgroup_processes(raw)

    def test_browser_containment_text_evidence_requires_pid1_pid2_topology(self):
        values: dict[str, object] = {
            "expected_cgroup_path": "/buffalo.acceptance/run-01/worker",
            "expected_init_outer_pid": 120001,
            "expected_worker_outer_pid": 120002,
            "expected_frozen": False,
            "init_cgroup": b"0::/buffalo.acceptance/run-01/worker\n",
            "worker_cgroup": b"0::/buffalo.acceptance/run-01/worker\n",
            "init_nspid": b"NSpid:\t120001\t1\n",
            "worker_nspid": b"NSpid:\t120002\t2\n",
            "events": b"populated 1\nfrozen 0\n",
            "processes": b"120002\n120001\n",
            "threads": b"120001\n120002\n",
        }
        expected = acceptance.BrowserContainmentTextEvidence(
            cgroup_path="/buffalo.acceptance/run-01/worker",
            events=acceptance.BrowserCgroupEvents(
                populated=True,
                frozen=False,
            ),
            process_ids=(120001, 120002),
            thread_ids=(120001, 120002),
            init_outer_pid=120001,
            worker_outer_pid=120002,
            init_namespace_pids=(120001, 1),
            worker_namespace_pids=(120002, 2),
        )
        with (
            patch.object(acceptance.os, "open") as opened,
            patch.object(acceptance.os, "write") as wrote,
            patch.object(acceptance.subprocess, "Popen") as spawned,
            patch.object(acceptance, "write_browser_worker_secret") as secret,
        ):
            self.assertEqual(
                acceptance.parse_browser_containment_text_evidence(**values),
                expected,
            )
        opened.assert_not_called()
        wrote.assert_not_called()
        spawned.assert_not_called()
        secret.assert_not_called()

        mutations: tuple[tuple[str, object], ...] = (
            ("expected_cgroup_path", "/"),
            ("expected_cgroup_path", "/" + "a" * 4_093),
            ("expected_init_outer_pid", True),
            ("expected_worker_outer_pid", 120001),
            ("expected_frozen", 0),
            ("init_cgroup", b"0::/buffalo.acceptance/other\n"),
            ("worker_cgroup", b"0::/buffalo.acceptance/other\n"),
            ("events", b"populated 0\nfrozen 0\n"),
            ("events", b"populated 1\nfrozen 1\n"),
            ("processes", b"120001\n"),
            ("processes", b"120001\n120002\n120003\n"),
            ("threads", b"120001\n"),
            ("init_nspid", b"NSpid:\t120001\t2\n"),
            ("worker_nspid", b"NSpid:\t120002\t1\n"),
            ("worker_nspid", b"NSpid:\t120002\t55\t2\n"),
        )
        for key, changed in mutations:
            case = dict(values)
            case[key] = changed
            with self.subTest(key=key, changed=changed), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance.parse_browser_containment_text_evidence(**case)

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
            pid_path = root / "bounded-process.pid"
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance._run_bounded_process(
                    (
                        sys.executable,
                        "-c",
                        (
                            "import os,pathlib,time;"
                            f"pathlib.Path({str(pid_path)!r}).write_text("
                            "str(os.getpid()), encoding='ascii');"
                            "time.sleep(5)"
                        ),
                    ),
                    pass_fds=(),
                    environment={"LANG": "C.UTF-8"},
                    cwd=root,
                    timeout_seconds=0.2,
                    stdout_limit=0,
                    stderr_limit=0,
                )
            process_id = int(pid_path.read_text(encoding="ascii"))
            self.assertFalse(Path(f"/proc/{process_id}").exists())

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

    def test_browser_worker_proc_stat_is_bounded_and_unambiguous(self):
        pid = 23456
        fields = (
            ["S", "123", "23456", "23456"]
            + ["0"] * 15
            + ["987654"]
            + ["0"] * 30
        )
        raw = (
            f"{pid} (worker ) with spaces) {' '.join(fields)}\n".encode(
                "ascii"
            )
        )
        self.assertEqual(
            acceptance._parse_browser_worker_proc_stat(
                raw,
                expected_pid=pid,
            ),
            acceptance.BrowserWorkerProcessStat(
                pid=pid,
                state="S",
                parent_pid=123,
                process_group=pid,
                session_id=pid,
                start_ticks=987654,
            ),
        )

        def changed_field(index: int, value: str) -> bytes:
            selected = list(fields)
            selected[index] = value
            return f"{pid} (worker) {' '.join(selected)}\n".encode("ascii")

        malformed = (
            b"",
            raw[:-1],
            raw + b"\n",
            raw.replace(b"worker", b"work\0er"),
            raw.replace(str(pid).encode("ascii"), b"99999", 1),
            f"{pid} worker {' '.join(fields)}\n".encode("ascii"),
            f"{pid} (worker) S 1 2\n".encode("ascii"),
            changed_field(0, "?"),
            changed_field(1, "0"),
            changed_field(1, "9" * 5_000),
            changed_field(2, "023456"),
            changed_field(3, "-1"),
            changed_field(19, "0"),
            (
                f"{pid} (worker) ".encode("ascii")
                + b"S "
                + b"1 " * acceptance._BROWSER_PROC_STAT_LIMIT
                + b"\n"
            ),
        )
        for selected in malformed:
            with self.subTest(raw=selected[:64]), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance._parse_browser_worker_proc_stat(
                    selected,
                    expected_pid=pid,
                )

    def test_browser_worker_process_is_bound_to_direct_python_and_pidfd(self):
        self.assertEqual(
            str(acceptance._BROWSER_PYTHON_EXECUTABLE),
            "/nix/store/qzc04a3npl70cyyy6flnnrb2ig3kayxm-"
            "python3-3.13.11/bin/python3.13",
        )
        self.assertEqual(acceptance._BROWSER_PYTHON_BYTES, 15_776)
        self.assertEqual(
            acceptance._BROWSER_PYTHON_SHA256,
            "bd5afcc703e9293ebea22ec05ad3a95f5b14ca6b65293a5f2969efe83148f565",
        )
        with (
            patch.object(acceptance.os, "open", return_value=0),
            patch.object(acceptance.os, "close") as close_low_descriptor,
            self.assertRaises(acceptance.LocalStagingAcceptanceError),
        ):
            acceptance.open_pinned_browser_python_executable()
        close_low_descriptor.assert_called_once_with(0)
        for invalid_pid in (True, 1, 2_147_483_648, 10**100):
            with self.subTest(invalid_pid=invalid_pid), self.assertRaises(
                acceptance.LocalStagingAcceptanceError
            ):
                acceptance.observe_browser_worker_process(
                    invalid_pid,
                    None,
                    expectation=None,
                )
        process = None
        observed = None
        ready_read = ready_write = gate_read = gate_write = -1
        held_descriptor = -1
        with (
            acceptance.open_pinned_browser_python_executable() as runtime,
            TemporaryDirectory() as descriptor_temporary,
        ):
            held_path = Path(descriptor_temporary) / "held-input"
            held_path.write_bytes(b"fixed held descriptor fixture")
            held_descriptor = os.open(
                held_path,
                os.O_RDONLY | os.O_CLOEXEC,
            )
            try:
                ready_read, ready_write = os.pipe2(os.O_CLOEXEC)
                gate_read, gate_write = os.pipe2(os.O_CLOEXEC)
                code = (
                    "import os,sys;"
                    "tuple(os.set_inheritable(int(value),False) "
                    "for value in sys.argv[1:5]);"
                    "os.write(int(sys.argv[1]),b'R');"
                    "assert os.read(int(sys.argv[2]),1)==b'P';"
                    "held=int(sys.argv[4]);"
                    "os.lseek(held,1,os.SEEK_SET);"
                    "os.write(int(sys.argv[1]),b'P');"
                    "assert os.read(int(sys.argv[2]),1)==b'D';"
                    "os.close(held);"
                    "replacement=os.open(sys.argv[5],os.O_RDONLY|os.O_CLOEXEC);"
                    "(os.dup2(replacement,held,inheritable=False),"
                    "os.close(replacement)) if replacement!=held else None;"
                    "os.set_inheritable(held,False);"
                    "os.write(int(sys.argv[1]),b'D');"
                    "os.read(int(sys.argv[2]),1)"
                )
                worker_argv = (
                    f"/proc/self/fd/{runtime.descriptor}",
                    "-I",
                    "-S",
                    "-B",
                    "-P",
                    "-c",
                    code,
                    str(ready_write),
                    str(gate_read),
                    str(runtime.descriptor),
                    str(held_descriptor),
                    str(held_path),
                )
                standard_probe = os.open(
                    "/dev/null",
                    os.O_RDWR | os.O_CLOEXEC,
                )
                try:
                    standard_descriptors = tuple(
                        _browser_descriptor_expectation(
                            standard_probe,
                            number=number,
                            target="/dev/null",
                            close_on_exec=False,
                        )
                        for number in (0, 1, 2)
                    )
                finally:
                    os.close(standard_probe)
                expected_descriptors = tuple(
                    sorted(
                        (
                            *standard_descriptors,
                            _browser_descriptor_expectation(
                                runtime.descriptor,
                                close_on_exec=True,
                            ),
                            _browser_descriptor_expectation(
                                ready_write,
                                close_on_exec=True,
                            ),
                            _browser_descriptor_expectation(
                                gate_read,
                                close_on_exec=True,
                            ),
                            _browser_descriptor_expectation(
                                held_descriptor,
                                close_on_exec=True,
                            ),
                        ),
                        key=lambda item: item.number,
                    )
                )
                expectation = acceptance.BrowserWorkerProcessExpectation(
                    command_line=worker_argv,
                    environment=acceptance._BROWSER_WORKER_ENVIRONMENT,
                    cwd=Path.cwd(),
                    cwd_device=Path.cwd().stat().st_dev,
                    cwd_inode=Path.cwd().stat().st_ino,
                    descriptors=expected_descriptors,
                    user_id=os.geteuid(),
                    group_id=os.getegid(),
                    supplementary_groups=tuple(sorted(os.getgroups())),
                    no_new_privileges=1,
                    seccomp_mode=2,
                )
                process = acceptance.subprocess.Popen(
                    worker_argv,
                    stdin=acceptance.subprocess.DEVNULL,
                    stdout=acceptance.subprocess.DEVNULL,
                    stderr=acceptance.subprocess.DEVNULL,
                    close_fds=True,
                    pass_fds=(
                        runtime.descriptor,
                        ready_write,
                        gate_read,
                        held_descriptor,
                    ),
                    start_new_session=True,
                    shell=False,
                    env=dict(acceptance._BROWSER_WORKER_ENVIRONMENT),
                )
                child_gate_descriptor = gate_read
                os.close(ready_write)
                ready_write = -1
                os.close(gate_read)
                gate_read = -1
                selector = selectors.DefaultSelector()
                try:
                    selector.register(ready_read, selectors.EVENT_READ)
                    self.assertTrue(selector.select(5.0))
                finally:
                    selector.close()
                self.assertEqual(os.read(ready_read, 1), b"R")

                with patch.object(acceptance.os, "pidfd_open", None):
                    with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                        acceptance.observe_browser_worker_process(
                            process.pid,
                            runtime,
                            expectation=expectation,
                        )
                standard_input = os.fstat(0)
                with patch.object(
                    acceptance.os,
                    "pidfd_open",
                    return_value=False,
                ):
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        acceptance.observe_browser_worker_process(
                            process.pid,
                            runtime,
                            expectation=expectation,
                        )
                with patch.object(
                    acceptance.os,
                    "pidfd_open",
                    return_value=0,
                ):
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        acceptance.observe_browser_worker_process(
                            process.pid,
                            runtime,
                            expectation=expectation,
                        )
                self.assertEqual(os.fstat(0), standard_input)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.observe_browser_worker_process(
                        process.pid,
                        runtime,
                        expectation=replace(
                            expectation,
                            command_line=expectation.command_line
                            + ("unexpected",),
                        ),
                    )
                observed = acceptance.observe_browser_worker_process(
                    process.pid,
                    runtime,
                    expectation=expectation,
                )
                current = acceptance.require_browser_worker_process_live(
                    observed,
                    runtime,
                    expectation=expectation,
                )
                self.assertEqual(current.pid, process.pid)
                self.assertEqual(current.start_ticks, observed.process.start_ticks)
                expectation_mutations = (
                    replace(
                        expectation,
                        environment=expectation.environment
                        + (("FORBIDDEN_MARKER", "present"),),
                    ),
                    replace(
                        expectation,
                        descriptors=expectation.descriptors[:-1],
                    ),
                    replace(
                        expectation,
                        descriptors=tuple(
                            replace(
                                item,
                                status_flags=(
                                    item.status_flags & ~os.O_ACCMODE
                                )
                                | os.O_WRONLY,
                            )
                            if item.number == child_gate_descriptor
                            else item
                            for item in expectation.descriptors
                        ),
                    ),
                    replace(
                        expectation,
                        descriptors=tuple(
                            replace(
                                item,
                                status_flags=item.status_flags | os.O_NONBLOCK,
                            )
                            if item.number == child_gate_descriptor
                            else item
                            for item in expectation.descriptors
                        ),
                    ),
                    replace(
                        expectation,
                        descriptors=tuple(
                            replace(item, inode=item.inode + 1)
                            if item.number == child_gate_descriptor
                            else item
                            for item in expectation.descriptors
                        ),
                    ),
                    replace(
                        expectation,
                        descriptors=tuple(
                            replace(item, position=item.position + 1)
                            if item.number == child_gate_descriptor
                            else item
                            for item in expectation.descriptors
                        ),
                    ),
                    replace(expectation, cwd=Path("/")),
                    replace(expectation, cwd_inode=expectation.cwd_inode + 1),
                    replace(expectation, user_id=expectation.user_id + 1),
                )
                for changed in expectation_mutations:
                    with self.subTest(changed=changed), self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        acceptance.require_browser_worker_process_live(
                            observed,
                            runtime,
                            expectation=changed,
                        )

                original_process = observed.process
                observed.process = replace(
                    observed.process,
                    start_ticks=observed.process.start_ticks + 1,
                )
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        acceptance.require_browser_worker_process_live(
                            observed,
                            runtime,
                            expectation=expectation,
                        )
                finally:
                    observed.process = original_process

                os.kill(process.pid, acceptance.signal.SIGSTOP)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if acceptance._read_browser_worker_proc_stat(
                        process.pid
                    ).state in {"T", "t"}:
                        break
                    time.sleep(0.01)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.require_browser_worker_process_live(
                        observed,
                        runtime,
                        expectation=expectation,
                    )
                os.kill(process.pid, acceptance.signal.SIGCONT)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if acceptance._read_browser_worker_proc_stat(
                        process.pid
                    ).state in {"R", "S"}:
                        break
                    time.sleep(0.01)
                acceptance.require_browser_worker_process_live(
                    observed,
                    runtime,
                    expectation=expectation,
                )

                original_pidfd_inode = observed.pidfd_inode
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    copy.copy(observed)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    copy.deepcopy(observed)
                original_pidfd = observed.pidfd
                observed.pidfd = 10**100
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        observed.close()
                finally:
                    observed.pidfd = original_pidfd
                observed.pidfd_inode += 1
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        observed.close()
                finally:
                    observed.pidfd_inode = original_pidfd_inode

                os.write(gate_write, b"P")
                selector = selectors.DefaultSelector()
                try:
                    selector.register(ready_read, selectors.EVENT_READ)
                    self.assertTrue(selector.select(5.0))
                finally:
                    selector.close()
                self.assertEqual(os.read(ready_read, 1), b"P")
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.require_browser_worker_process_live(
                        observed,
                        runtime,
                        expectation=expectation,
                    )
                os.lseek(held_descriptor, 0, os.SEEK_SET)
                acceptance.require_browser_worker_process_live(
                    observed,
                    runtime,
                    expectation=expectation,
                )

                displaced_held_path = Path(descriptor_temporary) / "displaced"
                held_path.rename(displaced_held_path)
                held_path.write_bytes(b"fixed held descriptor fixture")
                self.assertNotEqual(
                    held_path.stat().st_ino,
                    os.fstat(held_descriptor).st_ino,
                )
                os.write(gate_write, b"D")
                selector = selectors.DefaultSelector()
                try:
                    selector.register(ready_read, selectors.EVENT_READ)
                    self.assertTrue(selector.select(5.0))
                finally:
                    selector.close()
                self.assertEqual(os.read(ready_read, 1), b"D")
                os.close(ready_read)
                ready_read = -1
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.require_browser_worker_process_live(
                        observed,
                        runtime,
                        expectation=expectation,
                    )
                os.write(gate_write, b"X")
                os.close(gate_write)
                gate_write = -1
                self.assertEqual(process.wait(timeout=5.0), 0)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance.require_browser_worker_process_live(
                        observed,
                        runtime,
                        expectation=expectation,
                    )
                replacement_process = acceptance.subprocess.Popen(
                    (
                        str(acceptance._BROWSER_PYTHON_EXECUTABLE),
                        "-I",
                        "-S",
                        "-B",
                        "-c",
                        "import time;time.sleep(5)",
                    ),
                    stdin=acceptance.subprocess.DEVNULL,
                    stdout=acceptance.subprocess.DEVNULL,
                    stderr=acceptance.subprocess.DEVNULL,
                    close_fds=True,
                    start_new_session=True,
                    shell=False,
                    env=dict(acceptance._BROWSER_WORKER_ENVIRONMENT),
                )
                stale_descriptor = observed.pidfd
                os.close(stale_descriptor)
                replacement_pidfd = os.pidfd_open(replacement_process.pid)
                try:
                    if replacement_pidfd != stale_descriptor:
                        os.dup2(replacement_pidfd, stale_descriptor)
                        os.close(replacement_pidfd)
                        replacement_pidfd = stale_descriptor
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        observed.close()
                finally:
                    os.close(replacement_pidfd)
                    observed.pidfd = -1
                    observed._owner_token = None
                    replacement_process.kill()
                    replacement_process.wait(timeout=5.0)
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5.0)
                if observed is not None:
                    observed.close()
                for descriptor in (
                    ready_read,
                    ready_write,
                    gate_read,
                    gate_write,
                ):
                    if descriptor >= 0:
                        os.close(descriptor)
                if held_descriptor >= 0:
                    os.close(held_descriptor)
                    held_descriptor = -1

            def assert_actual_drift_rejected(
                *,
                extra_environment: bool = False,
                nonblocking_ready: bool = False,
                extra_descriptor: bool = False,
            ) -> None:
                dirty_process = None
                dirty_ready_read = dirty_ready_write = -1
                dirty_gate_read = dirty_gate_write = -1
                dirty_extra_read = dirty_extra_write = -1
                try:
                    dirty_ready_read, dirty_ready_write = os.pipe2(
                        os.O_CLOEXEC
                    )
                    dirty_gate_read, dirty_gate_write = os.pipe2(
                        os.O_CLOEXEC
                    )
                    if extra_descriptor:
                        dirty_extra_read, dirty_extra_write = os.pipe2(
                            os.O_CLOEXEC
                        )
                    toggle = (
                        "fcntl.fcntl(int(sys.argv[1]),fcntl.F_SETFL,"
                        "fcntl.fcntl(int(sys.argv[1]),fcntl.F_GETFL)"
                        "|os.O_NONBLOCK);"
                        if nonblocking_ready
                        else ""
                    )
                    dirty_code = (
                        "import fcntl,os,sys;"
                        "tuple(os.set_inheritable(int(value),False) "
                        "for value in sys.argv[1:]);"
                        + toggle
                        + "os.write(int(sys.argv[1]),b'R');"
                        "os.read(int(sys.argv[2]),1)"
                    )
                    dirty_argv = (
                        f"/proc/self/fd/{runtime.descriptor}",
                        "-I",
                        "-S",
                        "-B",
                        "-P",
                        "-c",
                        dirty_code,
                        str(dirty_ready_write),
                        str(dirty_gate_read),
                        str(runtime.descriptor),
                    ) + (
                        (str(dirty_extra_read),)
                        if extra_descriptor
                        else ()
                    )
                    dirty_descriptors = tuple(
                        sorted(
                            (
                                *standard_descriptors,
                                _browser_descriptor_expectation(
                                    runtime.descriptor,
                                    close_on_exec=True,
                                ),
                                _browser_descriptor_expectation(
                                    dirty_ready_write,
                                    close_on_exec=True,
                                ),
                                _browser_descriptor_expectation(
                                    dirty_gate_read,
                                    close_on_exec=True,
                                ),
                            ),
                            key=lambda item: item.number,
                        )
                    )
                    dirty_expectation = replace(
                        expectation,
                        command_line=dirty_argv,
                        descriptors=dirty_descriptors,
                    )
                    dirty_environment = dict(
                        acceptance._BROWSER_WORKER_ENVIRONMENT
                    )
                    if extra_environment:
                        dirty_environment["FORBIDDEN_MARKER"] = "present"
                    pass_fds = (
                        runtime.descriptor,
                        dirty_ready_write,
                        dirty_gate_read,
                    ) + (
                        (dirty_extra_read,) if extra_descriptor else ()
                    )
                    dirty_process = acceptance.subprocess.Popen(
                        dirty_argv,
                        stdin=acceptance.subprocess.DEVNULL,
                        stdout=acceptance.subprocess.DEVNULL,
                        stderr=acceptance.subprocess.DEVNULL,
                        close_fds=True,
                        pass_fds=pass_fds,
                        start_new_session=True,
                        shell=False,
                        env=dirty_environment,
                    )
                    os.close(dirty_ready_write)
                    dirty_ready_write = -1
                    os.close(dirty_gate_read)
                    dirty_gate_read = -1
                    if dirty_extra_read >= 0:
                        os.close(dirty_extra_read)
                        dirty_extra_read = -1
                    selector = selectors.DefaultSelector()
                    try:
                        selector.register(
                            dirty_ready_read,
                            selectors.EVENT_READ,
                        )
                        self.assertTrue(selector.select(5.0))
                    finally:
                        selector.close()
                    self.assertEqual(os.read(dirty_ready_read, 1), b"R")
                    os.close(dirty_ready_read)
                    dirty_ready_read = -1
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        acceptance.observe_browser_worker_process(
                            dirty_process.pid,
                            runtime,
                            expectation=dirty_expectation,
                        )
                    os.write(dirty_gate_write, b"X")
                    os.close(dirty_gate_write)
                    dirty_gate_write = -1
                    self.assertEqual(dirty_process.wait(timeout=5.0), 0)
                finally:
                    if dirty_process is not None and dirty_process.poll() is None:
                        dirty_process.kill()
                        dirty_process.wait(timeout=5.0)
                    for descriptor in (
                        dirty_ready_read,
                        dirty_ready_write,
                        dirty_gate_read,
                        dirty_gate_write,
                        dirty_extra_read,
                        dirty_extra_write,
                    ):
                        if descriptor >= 0:
                            os.close(descriptor)

            for drift in (
                {"extra_environment": True},
                {"nonblocking_ready": True},
                {"extra_descriptor": True},
            ):
                with self.subTest(actual_drift=drift):
                    assert_actual_drift_rejected(**drift)

            cwd_process = None
            cwd_observed = None
            cwd_ready_read = cwd_ready_write = -1
            cwd_gate_read = cwd_gate_write = -1
            with TemporaryDirectory() as temporary:
                cwd_root = Path(temporary)
                cwd_path = cwd_root / "worker"
                cwd_path.mkdir(mode=0o700)
                original_cwd = cwd_path.stat()
                try:
                    cwd_ready_read, cwd_ready_write = os.pipe2(os.O_CLOEXEC)
                    cwd_gate_read, cwd_gate_write = os.pipe2(os.O_CLOEXEC)
                    cwd_code = (
                        "import os,sys;"
                        "tuple(os.set_inheritable(int(value),False) "
                        "for value in sys.argv[1:4]);"
                        "os.write(int(sys.argv[1]),b'R');"
                        "assert os.read(int(sys.argv[2]),1)==b'C';"
                        "os.chdir(sys.argv[4]);"
                        "os.write(int(sys.argv[1]),b'C');"
                        "os.read(int(sys.argv[2]),1)"
                    )
                    cwd_argv = (
                        f"/proc/self/fd/{runtime.descriptor}",
                        "-I",
                        "-S",
                        "-B",
                        "-P",
                        "-c",
                        cwd_code,
                        str(cwd_ready_write),
                        str(cwd_gate_read),
                        str(runtime.descriptor),
                        str(cwd_path),
                    )
                    cwd_descriptors = tuple(
                        sorted(
                            (
                                *standard_descriptors,
                                _browser_descriptor_expectation(
                                    runtime.descriptor,
                                    close_on_exec=True,
                                ),
                                _browser_descriptor_expectation(
                                    cwd_ready_write,
                                    close_on_exec=True,
                                ),
                                _browser_descriptor_expectation(
                                    cwd_gate_read,
                                    close_on_exec=True,
                                ),
                            ),
                            key=lambda item: item.number,
                        )
                    )
                    cwd_expectation = replace(
                        expectation,
                        command_line=cwd_argv,
                        cwd=cwd_path,
                        cwd_device=original_cwd.st_dev,
                        cwd_inode=original_cwd.st_ino,
                        descriptors=cwd_descriptors,
                    )
                    cwd_process = acceptance.subprocess.Popen(
                        cwd_argv,
                        stdin=acceptance.subprocess.DEVNULL,
                        stdout=acceptance.subprocess.DEVNULL,
                        stderr=acceptance.subprocess.DEVNULL,
                        close_fds=True,
                        pass_fds=(
                            runtime.descriptor,
                            cwd_ready_write,
                            cwd_gate_read,
                        ),
                        start_new_session=True,
                        shell=False,
                        cwd=cwd_path,
                        env=dict(acceptance._BROWSER_WORKER_ENVIRONMENT),
                    )
                    os.close(cwd_ready_write)
                    cwd_ready_write = -1
                    os.close(cwd_gate_read)
                    cwd_gate_read = -1
                    selector = selectors.DefaultSelector()
                    try:
                        selector.register(cwd_ready_read, selectors.EVENT_READ)
                        self.assertTrue(selector.select(5.0))
                    finally:
                        selector.close()
                    self.assertEqual(os.read(cwd_ready_read, 1), b"R")
                    cwd_observed = acceptance.observe_browser_worker_process(
                        cwd_process.pid,
                        runtime,
                        expectation=cwd_expectation,
                    )

                    displaced_cwd = cwd_root / "displaced"
                    cwd_path.rename(displaced_cwd)
                    cwd_path.mkdir(mode=0o700)
                    self.assertNotEqual(
                        cwd_path.stat().st_ino,
                        original_cwd.st_ino,
                    )
                    os.write(cwd_gate_write, b"C")
                    selector = selectors.DefaultSelector()
                    try:
                        selector.register(cwd_ready_read, selectors.EVENT_READ)
                        self.assertTrue(selector.select(5.0))
                    finally:
                        selector.close()
                    self.assertEqual(os.read(cwd_ready_read, 1), b"C")
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        acceptance.require_browser_worker_process_live(
                            cwd_observed,
                            runtime,
                            expectation=cwd_expectation,
                        )
                    os.write(cwd_gate_write, b"X")
                    os.close(cwd_gate_write)
                    cwd_gate_write = -1
                    self.assertEqual(cwd_process.wait(timeout=5.0), 0)
                finally:
                    if cwd_process is not None and cwd_process.poll() is None:
                        cwd_process.kill()
                        cwd_process.wait(timeout=5.0)
                    if cwd_observed is not None:
                        cwd_observed.close()
                    for descriptor in (
                        cwd_ready_read,
                        cwd_ready_write,
                        cwd_gate_read,
                        cwd_gate_write,
                    ):
                        if descriptor >= 0:
                            os.close(descriptor)

        standard_input = os.fstat(0)
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.ObservedBrowserWorker(
                pidfd=0,
                pidfd_device=0,
                pidfd_inode=0,
                process=acceptance.BrowserWorkerProcessStat(
                    pid=123,
                    state="S",
                    parent_pid=1,
                    process_group=123,
                    session_id=123,
                    start_ticks=1,
                ),
            ).close()
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance.PinnedBrowserPythonExecutable(
                descriptor=0,
                path=acceptance._BROWSER_PYTHON_EXECUTABLE,
                device=0,
                inode=0,
                size=0,
                mtime_ns=0,
            ).close()
        self.assertEqual(os.fstat(0), standard_input)

        with TemporaryDirectory() as temporary:
            original = acceptance._BROWSER_PYTHON_EXECUTABLE.read_bytes()
            root = Path(temporary)
            candidate = root / "python"
            candidate.write_bytes(original)
            candidate.chmod(0o555)
            with patch.multiple(
                acceptance,
                _BROWSER_PYTHON_EXECUTABLE=candidate,
                _BROWSER_PYTHON_BYTES=len(original),
                _BROWSER_PYTHON_SHA256=hashlib.sha256(original).hexdigest(),
                _BROWSER_PYTHON_UID=os.geteuid(),
                _BROWSER_PYTHON_GID=os.getegid(),
            ):
                held = acceptance.open_pinned_browser_python_executable()
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    copy.copy(held)
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    copy.deepcopy(held)
                original_descriptor = held.descriptor
                held.descriptor = 10**100
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        acceptance._validate_pinned_browser_python_executable(
                            held
                        )
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        held.close()
                finally:
                    held.descriptor = original_descriptor
                displaced = root / "displaced"
                candidate.rename(displaced)
                candidate.write_bytes(original)
                candidate.chmod(0o555)
                try:
                    with self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ):
                        acceptance._validate_pinned_browser_python_executable(held)
                finally:
                    held.close()

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

    def test_browser_projection_policy_environment_manifest_and_negative_paths_are_exact(self):
        policy = acceptance._browser_projection_policy_entries()
        self.assertEqual(len(policy), 13)
        self.assertEqual(
            sum(len(entry.content) for entry in policy if entry.kind == "F"),
            181,
        )
        self.assertEqual(
            acceptance._browser_projection_policy_sha256(
                policy,
                expected_sha256=acceptance._BROWSER_PROJECTION_POLICY_SHA256,
            ),
            acceptance._BROWSER_PROJECTION_POLICY_SHA256,
        )
        self.assertEqual(
            tuple(sorted(acceptance._BROWSER_PROJECTION_ENVIRONMENT)),
            acceptance._BROWSER_PROJECTION_ENVIRONMENT,
        )
        self.assertTrue(
            all(
                not acceptance._BROWSER_PROJECTION_FORBIDDEN_ENVIRONMENT.fullmatch(
                    name
                )
                for name, _ in acceptance._BROWSER_PROJECTION_ENVIRONMENT
            )
        )
        self.assertIn("/etc/ld-nix.so.preload", acceptance._BROWSER_PROJECTION_NEGATIVE_PATHS)
        self.assertIn(
            str(acceptance._BROWSER_RUNTIME_LOCALE_ARCHIVE),
            acceptance._BROWSER_PROJECTION_NEGATIVE_PATHS,
        )
        self.assertIn("/runtime/.pythonlibs", acceptance._BROWSER_PROJECTION_NEGATIVE_PATHS)
        changed = tuple(
            replace(entry, content=entry.content + b"x")
            if entry.kind == "F"
            else entry
            for entry in policy
        )
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._browser_projection_policy_sha256(
                changed,
                expected_sha256=acceptance._BROWSER_PROJECTION_POLICY_SHA256,
            )
        changed_environment = tuple(
            (name, "/etc/passwd" if name == "SSL_CERT_FILE" else value)
            for name, value in acceptance._BROWSER_PROJECTION_ENVIRONMENT
        )
        with patch.object(
            acceptance,
            "_BROWSER_PROJECTION_ENVIRONMENT",
            changed_environment,
        ), self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._browser_projection_policy_entries()
        with patch.object(
            acceptance,
            "_BROWSER_PROJECTION_NEGATIVE_PATHS",
            acceptance._BROWSER_PROJECTION_NEGATIVE_PATHS[1:],
        ), self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._browser_projection_policy_entries()
        with patch.object(
            acceptance,
            "_BROWSER_PROJECTION_POLICY_ENTRIES",
            acceptance._BROWSER_PROJECTION_POLICY_ENTRIES
            + (acceptance._BROWSER_PROJECTION_POLICY_ENTRIES[-1],),
        ), self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._browser_projection_policy_entries()

    def test_browser_projection_materialization_binds_owned_root_and_is_read_only(self):
        generation = "d" * 64
        entry = acceptance._BrowserRuntimeBundleEntry(
            "/",
            "D",
            0o555,
            0,
            0,
            "projection-policy:" + "1" * 64,
            b"",
        )
        bundle = unittest.mock.Mock(
            descriptor=91,
            size=99,
            sha256="2" * 64,
            manifest_sha256="3" * 64,
            regular_bytes=0,
        )
        observation = unittest.mock.sentinel.projection
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / (
                acceptance._BROWSER_PROJECTION_ROOT_PREFIX + generation
            )
            root.mkdir(mode=0o700)
            info = root.stat(follow_symlinks=False)

            def detached_mount() -> tuple[int, int, int]:
                return (
                    os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC),
                    os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC),
                    os.open(
                        root,
                        os.O_RDONLY
                        | os.O_CLOEXEC
                        | os.O_DIRECTORY
                        | os.O_NOFOLLOW,
                    ),
                )

            with (
                patch.object(acceptance, "_validate_frozen_browser_runtime_bundle"),
                patch.object(
                    acceptance,
                    "_read_browser_runtime_bundle_descriptor",
                    return_value=b"bundle",
                ),
                patch.object(
                    acceptance,
                    "_parse_browser_runtime_bundle",
                    return_value=(
                        (entry,),
                        bundle.manifest_sha256,
                        bundle.sha256,
                        bundle.regular_bytes,
                    ),
                ),
                patch.object(
                    acceptance,
                    "_browser_projection_entries",
                    return_value=(entry,),
                ),
                patch.object(acceptance, "_browser_mount") as mounted,
                patch.object(
                    acceptance,
                    "_open_detached_browser_projection_mount",
                    side_effect=detached_mount,
                ),
                patch.object(
                    acceptance,
                    "_seal_detached_browser_projection_mount",
                ) as sealed,
                patch.object(
                    acceptance,
                    "_attach_detached_browser_projection_mount",
                ) as attached,
                patch.object(
                    acceptance,
                    "_browser_fstatfs_magic",
                    return_value=acceptance._BROWSER_TMPFS_MAGIC,
                ),
                patch.object(
                    acceptance,
                    "_materialize_browser_projection_entries",
                ) as materialized,
                patch.object(
                    acceptance,
                    "_validate_materialized_browser_projection",
                    return_value=observation,
                ) as validated,
                patch.object(acceptance, "_browser_unmount") as unmounted,
            ):
                returned, descriptor = (
                    acceptance._materialize_browser_runtime_projection(
                        bundle,
                        root,
                        generation=generation,
                        expected_root_device=info.st_dev,
                        expected_root_inode=info.st_ino,
                    )
                )
                self.assertIs(returned, observation)
                os.close(descriptor)
                self.assertEqual(mounted.call_count, 1)
                self.assertEqual(
                    mounted.call_args_list[-1].args[3],
                    acceptance._BROWSER_MS_REC
                    | acceptance._BROWSER_MS_PRIVATE,
                )
                materialized.assert_called_once()
                validated.assert_called_once()
                sealed.assert_called_once()
                attached.assert_called_once()
                unmounted.assert_not_called()
                mounted.reset_mock()
                with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                    acceptance._materialize_browser_runtime_projection(
                        bundle,
                        root,
                        generation=generation,
                        expected_root_device=info.st_dev,
                        expected_root_inode=info.st_ino + 1,
                    )
                mounted.assert_not_called()
            with (
                patch.object(acceptance, "_validate_frozen_browser_runtime_bundle"),
                patch.object(
                    acceptance,
                    "_read_browser_runtime_bundle_descriptor",
                    return_value=b"bundle",
                ),
                patch.object(
                    acceptance,
                    "_parse_browser_runtime_bundle",
                    return_value=(
                        (entry,),
                        bundle.manifest_sha256,
                        bundle.sha256,
                        bundle.regular_bytes,
                    ),
                ),
                patch.object(
                    acceptance,
                    "_browser_projection_entries",
                    return_value=(entry,),
                ),
                patch.object(acceptance, "_browser_mount"),
                patch.object(
                    acceptance,
                    "_open_detached_browser_projection_mount",
                    side_effect=detached_mount,
                ),
                patch.object(
                    acceptance,
                    "_seal_detached_browser_projection_mount",
                ),
                patch.object(
                    acceptance,
                    "_attach_detached_browser_projection_mount",
                ),
                patch.object(
                    acceptance,
                    "_browser_fstatfs_magic",
                    return_value=acceptance._BROWSER_TMPFS_MAGIC,
                ),
                patch.object(
                    acceptance,
                    "_materialize_browser_projection_entries",
                    side_effect=KeyboardInterrupt,
                ),
                patch.object(acceptance, "_browser_unmount") as unmounted,
                self.assertRaises(KeyboardInterrupt),
            ):
                acceptance._materialize_browser_runtime_projection(
                    bundle,
                    root,
                    generation=generation,
                    expected_root_device=info.st_dev,
                    expected_root_inode=info.st_ino,
                )
            unmounted.assert_not_called()
            descriptor_baseline = set(os.listdir("/proc/self/fd"))
            def queue_projection_interrupt(*_: object) -> None:
                acceptance.signal.raise_signal(acceptance.signal.SIGINT)

            with (
                patch.object(
                    acceptance,
                    "_validate_frozen_browser_runtime_bundle",
                ),
                patch.object(
                    acceptance,
                    "_read_browser_runtime_bundle_descriptor",
                    return_value=b"bundle",
                ),
                patch.object(
                    acceptance,
                    "_parse_browser_runtime_bundle",
                    return_value=(
                        (entry,),
                        bundle.manifest_sha256,
                        bundle.sha256,
                        bundle.regular_bytes,
                    ),
                ),
                patch.object(
                    acceptance,
                    "_browser_projection_entries",
                    return_value=(entry,),
                ),
                patch.object(acceptance, "_browser_mount"),
                patch.object(
                    acceptance,
                    "_open_detached_browser_projection_mount",
                    side_effect=detached_mount,
                ),
                patch.object(
                    acceptance,
                    "_seal_detached_browser_projection_mount",
                ),
                patch.object(
                    acceptance,
                    "_attach_detached_browser_projection_mount",
                ),
                patch.object(
                    acceptance,
                    "_browser_fstatfs_magic",
                    return_value=acceptance._BROWSER_TMPFS_MAGIC,
                ),
                patch.object(
                    acceptance,
                    "_materialize_browser_projection_entries",
                    side_effect=queue_projection_interrupt,
                ),
                patch.object(
                    acceptance,
                    "_validate_materialized_browser_projection",
                    return_value=observation,
                ),
                patch.object(acceptance, "_browser_unmount") as unmounted,
                self.assertRaises(KeyboardInterrupt),
            ):
                acceptance._materialize_browser_runtime_projection(
                    bundle,
                    root,
                    generation=generation,
                    expected_root_device=info.st_dev,
                    expected_root_inode=info.st_ino,
                )
            unmounted.assert_called_once()
            self.assertEqual(
                descriptor_baseline,
                set(os.listdir("/proc/self/fd")),
            )
            descriptor_baseline = set(os.listdir("/proc/self/fd"))
            with (
                patch.object(
                    acceptance,
                    "_validate_frozen_browser_runtime_bundle",
                ),
                patch.object(
                    acceptance,
                    "_read_browser_runtime_bundle_descriptor",
                    return_value=b"bundle",
                ),
                patch.object(
                    acceptance,
                    "_parse_browser_runtime_bundle",
                    return_value=(
                        (entry,),
                        bundle.manifest_sha256,
                        bundle.sha256,
                        bundle.regular_bytes,
                    ),
                ),
                patch.object(
                    acceptance,
                    "_browser_projection_entries",
                    return_value=(entry,),
                ),
                patch.object(acceptance, "_browser_mount"),
                patch.object(
                    acceptance,
                    "_open_detached_browser_projection_mount",
                    side_effect=detached_mount,
                ),
                patch.object(
                    acceptance,
                    "_seal_detached_browser_projection_mount",
                ),
                patch.object(
                    acceptance,
                    "_attach_detached_browser_projection_mount",
                    side_effect=KeyboardInterrupt,
                ),
                patch.object(
                    acceptance,
                    "_browser_fstatfs_magic",
                    return_value=acceptance._BROWSER_TMPFS_MAGIC,
                ),
                patch.object(
                    acceptance,
                    "_materialize_browser_projection_entries",
                ),
                patch.object(
                    acceptance,
                    "_validate_materialized_browser_projection",
                    return_value=observation,
                ),
                patch.object(acceptance, "_browser_unmount") as unmounted,
                self.assertRaises(KeyboardInterrupt),
            ):
                acceptance._materialize_browser_runtime_projection(
                    bundle,
                    root,
                    generation=generation,
                    expected_root_device=info.st_dev,
                    expected_root_inode=info.st_ino,
                )
            unmounted.assert_called_once()
            self.assertEqual(
                descriptor_baseline,
                set(os.listdir("/proc/self/fd")),
            )
            descriptor_baseline = set(os.listdir("/proc/self/fd"))
            with (
                patch.object(
                    acceptance,
                    "_validate_frozen_browser_runtime_bundle",
                ),
                patch.object(
                    acceptance,
                    "_read_browser_runtime_bundle_descriptor",
                    side_effect=KeyboardInterrupt,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                acceptance._materialize_browser_runtime_projection(
                    bundle,
                    root,
                    generation=generation,
                    expected_root_device=info.st_dev,
                    expected_root_inode=info.st_ino,
                )
            self.assertEqual(
                descriptor_baseline,
                set(os.listdir("/proc/self/fd")),
            )
            displaced = root.with_name(root.name + "-displaced")
            def swap_root_during_attach(*_: object) -> None:
                root.rename(displaced)
                root.mkdir(mode=0o700)

            try:
                with (
                    patch.object(
                        acceptance,
                        "_validate_frozen_browser_runtime_bundle",
                    ),
                    patch.object(
                        acceptance,
                        "_read_browser_runtime_bundle_descriptor",
                        return_value=b"bundle",
                    ),
                    patch.object(
                        acceptance,
                        "_parse_browser_runtime_bundle",
                        return_value=(
                            (entry,),
                            bundle.manifest_sha256,
                            bundle.sha256,
                            bundle.regular_bytes,
                        ),
                    ),
                    patch.object(
                        acceptance,
                        "_browser_projection_entries",
                        return_value=(entry,),
                    ),
                    patch.object(
                        acceptance,
                        "_browser_mount",
                    ),
                    patch.object(
                        acceptance,
                        "_open_detached_browser_projection_mount",
                        side_effect=detached_mount,
                    ),
                    patch.object(
                        acceptance,
                        "_seal_detached_browser_projection_mount",
                    ),
                    patch.object(
                        acceptance,
                        "_attach_detached_browser_projection_mount",
                        side_effect=swap_root_during_attach,
                    ),
                    patch.object(
                        acceptance,
                        "_materialize_browser_projection_entries",
                    ),
                    patch.object(
                        acceptance,
                        "_validate_materialized_browser_projection",
                        return_value=observation,
                    ),
                    patch.object(
                        acceptance,
                        "_browser_fstatfs_magic",
                        return_value=acceptance._BROWSER_TMPFS_MAGIC,
                    ),
                    patch.object(acceptance, "_browser_unmount"),
                    self.assertRaises(
                        acceptance.LocalStagingAcceptanceError
                    ),
                ):
                    acceptance._materialize_browser_runtime_projection(
                        bundle,
                        root,
                        generation=generation,
                        expected_root_device=info.st_dev,
                        expected_root_inode=info.st_ino,
                    )
            finally:
                if root.exists():
                    root.rmdir()
                if displaced.exists():
                    displaced.rename(root)

    def test_browser_clone3_and_private_proc_policy_fail_closed(self):
        for selected_errno in (acceptance.errno.ENOSYS, acceptance.errno.EPERM):
            with patch.object(
                acceptance,
                "_browser_syscall",
                side_effect=OSError(selected_errno, "denied"),
            ):
                self.assertEqual(
                    acceptance._probe_browser_clone3_policy(),
                    selected_errno,
                )
        with patch.object(
            acceptance,
            "_browser_syscall",
            side_effect=OSError(acceptance.errno.EINVAL, "reachable"),
        ), self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._probe_browser_clone3_policy()
        with (
            patch.object(
                acceptance,
                "_browser_syscall",
                side_effect=[
                    41,
                    0,
                    OSError(acceptance.errno.EPERM, "denied"),
                ],
            ),
            patch.object(
                acceptance,
                "_browser_mount",
                side_effect=OSError(acceptance.errno.EPERM, "denied"),
            ),
            patch.object(acceptance.os, "close") as closed,
        ):
            self.assertEqual(
                acceptance._attempt_private_browser_procfs(),
                (
                    acceptance._BROWSER_PRIVATE_PROC_BLOCKER_STAGE,
                    acceptance.errno.EPERM,
                ),
            )
            closed.assert_called_once_with(41)
        with (
            patch.object(
                acceptance,
                "_browser_syscall",
                side_effect=[41, 0, 42, 0],
            ),
            patch.object(acceptance.os, "close") as closed,
        ):
            self.assertEqual(
                acceptance._attempt_private_browser_procfs(),
                ("mounted", 0),
            )
            self.assertEqual(
                [call.args[0] for call in closed.call_args_list],
                [42, 41],
            )

    def test_browser_private_proc_guardian_is_stopped_mapped_and_residue_free(self):
        generation = hashlib.sha256(b"focused-private-proc-guardian").hexdigest()
        before_descriptors = set(os.listdir("/proc/self/fd"))
        guardian = acceptance._start_stopped_browser_private_proc_guardian(
            generation=generation
        )
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            copy.copy(guardian)
        with self.assertRaises(acceptance.LocalStagingAcceptanceError):
            copy.deepcopy(guardian)
        process_id = guardian.process_id
        try:
            self.assertEqual(
                acceptance._continue_browser_private_proc_guardian(
                    guardian,
                    generation=generation,
                ),
                (
                    acceptance._BROWSER_PRIVATE_PROC_BLOCKER_STAGE,
                    acceptance._BROWSER_PRIVATE_PROC_BLOCKER_ERRNO,
                ),
            )
        finally:
            acceptance._close_browser_prerequisite_guardian_lease(guardian)
        self.assertFalse(Path(f"/proc/{process_id}").exists())
        self.assertEqual(before_descriptors, set(os.listdir("/proc/self/fd")))

        guardian = acceptance._start_stopped_browser_private_proc_guardian(
            generation=generation
        )
        process_id = guardian.process_id
        real_metadata = acceptance._read_browser_pidfd_metadata
        metadata_calls = 0

        def interrupted_metadata(descriptor: int):
            nonlocal metadata_calls
            metadata_calls += 1
            if metadata_calls == 1:
                raise KeyboardInterrupt
            return real_metadata(descriptor)

        with patch.object(
            acceptance,
            "_read_browser_pidfd_metadata",
            side_effect=interrupted_metadata,
        ), self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._close_browser_prerequisite_guardian_lease(guardian)
        self.assertIsNone(guardian._owner_token)
        self.assertEqual(
            (guardian.pidfd, guardian.gate_write, guardian.status_read),
            (-1, -1, -1),
        )
        self.assertFalse(Path(f"/proc/{process_id}").exists())

        guardian = acceptance._start_stopped_browser_private_proc_guardian(
            generation=generation
        )
        process_id = guardian.process_id
        selected_pidfd = guardian.pidfd
        real_close = acceptance.os.close
        close_calls = 0

        def interrupted_pidfd_close(descriptor: int) -> None:
            nonlocal close_calls
            if descriptor == selected_pidfd and close_calls == 0:
                close_calls += 1
                raise KeyboardInterrupt
            real_close(descriptor)

        with patch.object(
            acceptance.os,
            "close",
            side_effect=interrupted_pidfd_close,
        ), self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._close_browser_prerequisite_guardian_lease(guardian)
        self.assertIsNone(guardian._owner_token)
        self.assertEqual(
            (guardian.pidfd, guardian.gate_write, guardian.status_read),
            (-1, -1, -1),
        )
        self.assertFalse(Path(f"/proc/{process_id}").exists())

        guardian = acceptance._start_stopped_browser_private_proc_guardian(
            generation=generation
        )
        process_id = guardian.process_id
        real_pipe_validation = (
            acceptance._validate_browser_guardian_pipe_handle
        )
        pipe_validation_calls = 0

        def interrupted_pipe_validation(*args, **kwargs):
            nonlocal pipe_validation_calls
            pipe_validation_calls += 1
            if pipe_validation_calls == 1:
                raise KeyboardInterrupt
            return real_pipe_validation(*args, **kwargs)

        with patch.object(
            acceptance,
            "_validate_browser_guardian_pipe_handle",
            side_effect=interrupted_pipe_validation,
        ), self.assertRaises(acceptance.LocalStagingAcceptanceError):
            acceptance._close_browser_prerequisite_guardian_lease(guardian)
        self.assertIsNone(guardian._owner_token)
        self.assertEqual(
            (guardian.pidfd, guardian.gate_write, guardian.status_read),
            (-1, -1, -1),
        )
        self.assertFalse(Path(f"/proc/{process_id}").exists())

        guardian = acceptance._start_stopped_browser_private_proc_guardian(
            generation=generation
        )
        owned_gate_duplicate = os.dup(guardian.gate_write)
        foreign_read, foreign_write = os.pipe2(os.O_CLOEXEC)
        try:
            os.close(guardian.gate_write)
            os.dup2(foreign_write, guardian.gate_write, inheritable=False)
            if foreign_write != guardian.gate_write:
                os.close(foreign_write)
                foreign_write = -1
            foreign_identity = os.fstat(guardian.gate_write)
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance._close_browser_prerequisite_guardian_lease(
                    guardian
                )
            repeated_foreign = os.fstat(guardian.gate_write)
            self.assertEqual(
                (repeated_foreign.st_dev, repeated_foreign.st_ino),
                (foreign_identity.st_dev, foreign_identity.st_ino),
            )
        finally:
            for descriptor in (
                owned_gate_duplicate,
                foreign_read,
                foreign_write,
                guardian.gate_write,
            ):
                if descriptor < 0:
                    continue
                try:
                    os.close(descriptor)
                except OSError:
                    pass

        guardian = acceptance._start_stopped_browser_private_proc_guardian(
            generation=generation
        )
        owned_pidfd_duplicate = os.dup(guardian.pidfd)
        foreign_process = os.fork()
        if foreign_process == 0:
            time.sleep(60.0)
            os._exit(0)
        foreign_pidfd = os.pidfd_open(foreign_process, 0)
        try:
            os.close(guardian.pidfd)
            os.dup2(foreign_pidfd, guardian.pidfd, inheritable=False)
            if foreign_pidfd != guardian.pidfd:
                os.close(foreign_pidfd)
                foreign_pidfd = -1
            with self.assertRaises(acceptance.LocalStagingAcceptanceError):
                acceptance._close_browser_prerequisite_guardian_lease(
                    guardian
                )
            self.assertFalse(
                acceptance._browser_pidfd_is_terminal(guardian.pidfd)
            )
        finally:
            try:
                acceptance.signal.pidfd_send_signal(
                    owned_pidfd_duplicate,
                    acceptance.signal.SIGKILL,
                )
            except ProcessLookupError:
                pass
            acceptance._wait_browser_child_bounded(
                guardian.process_id,
                deadline=time.monotonic() + 5.0,
            )
            try:
                acceptance.signal.pidfd_send_signal(
                    guardian.pidfd,
                    acceptance.signal.SIGKILL,
                )
            except ProcessLookupError:
                pass
            os.waitpid(foreign_process, 0)
            for descriptor in (
                owned_pidfd_duplicate,
                foreign_pidfd,
                guardian.pidfd,
            ):
                if descriptor < 0:
                    continue
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        forked: list[int] = []
        real_fork = os.fork
        real_wait = acceptance._wait_browser_child_bounded
        wait_calls = 0

        def recording_fork() -> int:
            selected = real_fork()
            if selected > 1:
                forked.append(selected)
            return selected

        def interrupted_wait(process_id: int, *, deadline: float) -> int:
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls == 1:
                raise acceptance.LocalStagingAcceptanceError(
                    "injected first wait failure"
                )
            return real_wait(process_id, deadline=deadline)

        with (
            patch.object(acceptance.os, "fork", side_effect=recording_fork),
            patch.object(
                acceptance,
                "_read_browser_guardian_frame",
                side_effect=acceptance.LocalStagingAcceptanceError(
                    "injected post-stop failure"
                ),
            ),
            patch.object(
                acceptance,
                "_wait_browser_child_bounded",
                side_effect=interrupted_wait,
            ),
            self.assertRaises(acceptance.LocalStagingAcceptanceError),
        ):
            acceptance._start_stopped_browser_private_proc_guardian(
                generation=generation
            )
        self.assertEqual(len(forked), 1)
        self.assertFalse(Path(f"/proc/{forked[0]}").exists())
        self.assertEqual(before_descriptors, set(os.listdir("/proc/self/fd")))

        forked.clear()

        def interrupt_after_fork() -> int:
            selected = real_fork()
            if selected > 1:
                forked.append(selected)
                acceptance.signal.raise_signal(acceptance.signal.SIGINT)
            return selected

        try:
            with (
                patch.object(
                    acceptance.os,
                    "fork",
                    side_effect=interrupt_after_fork,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                acceptance._start_stopped_browser_private_proc_guardian(
                    generation=generation
                )
            self.assertEqual(len(forked), 1)
            self.assertFalse(Path(f"/proc/{forked[0]}").exists())
        finally:
            if forked and Path(f"/proc/{forked[0]}").exists():
                try:
                    os.kill(forked[0], acceptance.signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(forked[0], 0)
                except ChildProcessError:
                    pass
        self.assertEqual(before_descriptors, set(os.listdir("/proc/self/fd")))

        acquired: list[acceptance._BrowserGuardianLease] = []
        real_start = acceptance._start_stopped_browser_private_proc_guardian

        def interrupt_after_guardian_acquisition(**kwargs):
            selected = real_start(**kwargs)
            acquired.append(selected)
            acceptance.signal.raise_signal(acceptance.signal.SIGINT)
            return selected

        try:
            with (
                patch.object(
                    acceptance,
                    "_validate_frozen_browser_runtime_bundle",
                ),
                patch.object(
                    acceptance,
                    "_validate_browser_prerequisite_supervisor_environment",
                ),
                patch.object(
                    acceptance,
                    "_probe_browser_clone3_policy",
                    return_value=acceptance.errno.ENOSYS,
                ),
                patch.object(
                    acceptance,
                    "_start_stopped_browser_private_proc_guardian",
                    side_effect=interrupt_after_guardian_acquisition,
                ),
                patch.object(
                    acceptance,
                    "_continue_browser_private_proc_guardian",
                    return_value=(
                        acceptance._BROWSER_PRIVATE_PROC_BLOCKER_STAGE,
                        acceptance._BROWSER_PRIVATE_PROC_BLOCKER_ERRNO,
                    ),
                ) as continued,
                self.assertRaises(KeyboardInterrupt),
            ):
                acceptance._observe_browser_containment_blocker(
                    unittest.mock.sentinel.bundle,
                    generation=generation,
                )
            continued.assert_called_once_with(
                acquired[0],
                generation=generation,
            )
            self.assertEqual(len(acquired), 1)
            self.assertIsNone(acquired[0]._owner_token)
            self.assertEqual(
                (
                    acquired[0].pidfd,
                    acquired[0].gate_write,
                    acquired[0].status_read,
                ),
                (-1, -1, -1),
            )
            self.assertFalse(
                Path(f"/proc/{acquired[0].process_id}").exists()
            )
        finally:
            if acquired and acquired[0]._owner_token is not None:
                acceptance._close_browser_prerequisite_guardian_lease(
                    acquired[0]
                )
        self.assertEqual(before_descriptors, set(os.listdir("/proc/self/fd")))

        real_close = acceptance._close_browser_prerequisite_guardian_lease
        for permanent_failure in (False, True):
            failed_acquisitions: list[
                acceptance._BrowserGuardianLease
            ] = []
            cleanup_calls = 0

            def interrupt_before_cleanup(**kwargs):
                selected = real_start(**kwargs)
                failed_acquisitions.append(selected)
                acceptance.signal.raise_signal(acceptance.signal.SIGINT)
                return selected

            def fail_cleanup(
                selected: acceptance._BrowserGuardianLease,
            ) -> None:
                nonlocal cleanup_calls
                cleanup_calls += 1
                if permanent_failure or cleanup_calls == 1:
                    raise acceptance.LocalStagingAcceptanceError(
                        "injected cleanup failure"
                    )
                real_close(selected)

            try:
                with (
                    self.subTest(permanent_failure=permanent_failure),
                    patch.object(
                        acceptance,
                        "_validate_frozen_browser_runtime_bundle",
                    ),
                    patch.object(
                        acceptance,
                        "_validate_browser_prerequisite_supervisor_environment",
                    ),
                    patch.object(
                        acceptance,
                        "_probe_browser_clone3_policy",
                        return_value=acceptance.errno.ENOSYS,
                    ),
                    patch.object(
                        acceptance,
                        "_start_stopped_browser_private_proc_guardian",
                        side_effect=interrupt_before_cleanup,
                    ),
                    patch.object(
                        acceptance,
                        "_continue_browser_private_proc_guardian",
                        return_value=(
                            acceptance._BROWSER_PRIVATE_PROC_BLOCKER_STAGE,
                            acceptance._BROWSER_PRIVATE_PROC_BLOCKER_ERRNO,
                        ),
                    ) as continued,
                    patch.object(
                        acceptance,
                        "_close_browser_prerequisite_guardian_lease",
                        side_effect=fail_cleanup,
                    ),
                    self.assertRaisesRegex(
                        acceptance.LocalStagingAcceptanceError,
                        "containment cleanup failed",
                    ),
                ):
                    acceptance._observe_browser_containment_blocker(
                        unittest.mock.sentinel.bundle,
                        generation=generation,
                    )
                self.assertEqual(cleanup_calls, 2)
                self.assertEqual(len(failed_acquisitions), 1)
                continued.assert_called_once_with(
                    failed_acquisitions[0],
                    generation=generation,
                )
                if permanent_failure:
                    self.assertIsNotNone(
                        failed_acquisitions[0]._owner_token
                    )
                    self.assertTrue(
                        Path(
                            f"/proc/{failed_acquisitions[0].process_id}"
                        ).exists()
                    )
                else:
                    self.assertIsNone(failed_acquisitions[0]._owner_token)
                    self.assertEqual(
                        (
                            failed_acquisitions[0].pidfd,
                            failed_acquisitions[0].gate_write,
                            failed_acquisitions[0].status_read,
                        ),
                        (-1, -1, -1),
                    )
                    self.assertFalse(
                        Path(
                            f"/proc/{failed_acquisitions[0].process_id}"
                        ).exists()
                    )
            finally:
                if (
                    failed_acquisitions
                    and failed_acquisitions[0]._owner_token is not None
                ):
                    real_close(failed_acquisitions[0])
            self.assertEqual(
                before_descriptors,
                set(os.listdir("/proc/self/fd")),
            )

        real_prctl = acceptance._browser_prctl

        def hold_before_init_hardening(option: int, value: int) -> None:
            if (
                option == acceptance._BROWSER_PR_SET_PDEATHSIG
                and value == int(acceptance.signal.SIGKILL)
                and os.getpid() == 1
                and os.getppid() == 0
            ):
                time.sleep(60.0)
            real_prctl(option, value)

        continuation_errors: list[BaseException] = []
        inner_pid = 0
        inner_pidfd = -1
        with patch.object(
            acceptance,
            "_browser_prctl",
            side_effect=hold_before_init_hardening,
        ):
            guardian = acceptance._start_stopped_browser_private_proc_guardian(
                generation=generation
            )

            def continue_guardian() -> None:
                try:
                    acceptance._continue_browser_private_proc_guardian(
                        guardian,
                        generation=generation,
                    )
                except BaseException as exc:
                    continuation_errors.append(exc)

            continuation = threading.Thread(target=continue_guardian)
            continuation.start()
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and inner_pid == 0:
                for candidate in Path("/proc").iterdir():
                    if not candidate.name.isascii() or not candidate.name.isdigit():
                        continue
                    selected_pid = int(candidate.name)
                    if selected_pid <= 1 or selected_pid == guardian.process_id:
                        continue
                    try:
                        observed = acceptance._read_browser_worker_proc_stat(
                            selected_pid
                        )
                    except acceptance.LocalStagingAcceptanceError:
                        continue
                    if observed.parent_pid == guardian.process_id:
                        inner_pid = selected_pid
                        break
                if inner_pid == 0:
                    time.sleep(0.01)
            self.assertGreater(inner_pid, 1)
            inner_pidfd = os.pidfd_open(inner_pid, 0)
            acceptance._close_browser_prerequisite_guardian_lease(guardian)
            self.assertTrue(
                acceptance._browser_pidfd_is_terminal(inner_pidfd)
            )
            continuation.join(5.0)
            self.assertFalse(continuation.is_alive())
        try:
            self.assertTrue(continuation_errors)
        finally:
            if inner_pidfd >= 0:
                os.close(inner_pidfd)
        self.assertEqual(before_descriptors, set(os.listdir("/proc/self/fd")))

    def test_browser_containment_blocker_withholds_provider_on_every_outcome(self):
        bundle = unittest.mock.sentinel.bundle
        guardian = unittest.mock.Mock(process_id=12345, process_start_ticks=67890)
        generation = "e" * 64
        safe_environment = b"LD_LIBRARY_PATH=/nix/store/safe/lib\0"
        safe_environment_sha256 = hashlib.sha256(safe_environment).hexdigest()
        with (
            patch.object(
                acceptance,
                "_BROWSER_SUPERVISOR_ENVIRONMENT_BYTES",
                len(safe_environment),
            ),
            patch.object(
                acceptance,
                "_BROWSER_SUPERVISOR_ENVIRONMENT_SHA256",
                safe_environment_sha256,
            ),
        ):
            self.assertEqual(
                acceptance._validate_browser_prerequisite_supervisor_environment_raw(
                    safe_environment
                ),
                safe_environment_sha256,
            )
            for changed in (
                bytearray(safe_environment),
                safe_environment[:-1],
                safe_environment + b"EXTRA=value\0",
                b"SECRET=value\0",
            ):
                with self.subTest(environment=changed), self.assertRaises(
                    acceptance.LocalStagingAcceptanceError
                ):
                    acceptance._validate_browser_prerequisite_supervisor_environment_raw(
                        changed
                    )
            with patch.object(
                acceptance,
                "_read_browser_proc_value",
                return_value=safe_environment,
            ) as read_environment:
                self.assertEqual(
                    acceptance._validate_browser_prerequisite_supervisor_environment(),
                    safe_environment_sha256,
                )
            read_environment.assert_called_once_with(
                "/proc/self/environ",
                maximum_bytes=len(safe_environment),
            )
        with (
            patch.object(acceptance, "_validate_frozen_browser_runtime_bundle"),
            patch.object(
                acceptance,
                "_validate_browser_prerequisite_supervisor_environment",
                side_effect=acceptance.LocalStagingAcceptanceError(
                    "unsafe initial environment"
                ),
            ),
            patch.object(acceptance, "_probe_browser_clone3_policy") as clone3,
            patch.object(
                acceptance,
                "_start_stopped_browser_private_proc_guardian",
            ) as started,
            self.assertRaises(acceptance.LocalStagingAcceptanceError),
        ):
            acceptance._observe_browser_containment_blocker(
                bundle,
                generation=generation,
            )
        clone3.assert_not_called()
        started.assert_not_called()
        with (
            patch.object(acceptance, "_validate_frozen_browser_runtime_bundle"),
            patch.object(
                acceptance,
                "_validate_browser_prerequisite_supervisor_environment",
            ),
            patch.object(
                acceptance,
                "_probe_browser_clone3_policy",
                return_value=acceptance.errno.ENOSYS,
            ),
            patch.object(
                acceptance,
                "_start_stopped_browser_private_proc_guardian",
                return_value=guardian,
            ),
            patch.object(
                acceptance,
                "_continue_browser_private_proc_guardian",
                return_value=(
                    acceptance._BROWSER_PRIVATE_PROC_BLOCKER_STAGE,
                    acceptance.errno.EPERM,
                ),
            ),
            patch.object(
                acceptance,
                "_close_browser_prerequisite_guardian_lease",
            ) as cleaned,
        ):
            observed = acceptance._observe_browser_containment_blocker(
                bundle,
                generation=generation,
            )
        self.assertEqual(observed.projection_status, "NOT_RUN")
        self.assertEqual(observed.cgroup_status, "NOT_RUN")
        self.assertEqual(observed.credential_release_status, "WITHHELD")
        self.assertEqual(observed.sentinel_provider_calls, 0)
        self.assertEqual(observed.payload_processes, 0)
        self.assertFalse(observed.execution_authority)
        cleaned.assert_called_once_with(guardian)
        for failure in (
            ("fsopen", acceptance.errno.EPERM),
            ("fsmount", acceptance.errno.EINVAL),
            KeyboardInterrupt(),
        ):
            with (
                patch.object(
                    acceptance,
                    "_validate_frozen_browser_runtime_bundle",
                ),
                patch.object(
                    acceptance,
                    "_validate_browser_prerequisite_supervisor_environment",
                ),
                patch.object(
                    acceptance,
                    "_probe_browser_clone3_policy",
                    return_value=acceptance.errno.ENOSYS,
                ),
                patch.object(
                    acceptance,
                    "_start_stopped_browser_private_proc_guardian",
                    return_value=guardian,
                ),
                patch.object(
                    acceptance,
                    "_continue_browser_private_proc_guardian",
                    side_effect=(
                        failure
                        if isinstance(failure, BaseException)
                        else None
                    ),
                    return_value=(
                        failure
                        if isinstance(failure, tuple)
                        else unittest.mock.DEFAULT
                    ),
                ),
                patch.object(
                    acceptance,
                    "_close_browser_prerequisite_guardian_lease",
                ) as cleaned,
            ):
                with self.assertRaises(
                    KeyboardInterrupt
                    if isinstance(failure, KeyboardInterrupt)
                    else acceptance.LocalStagingAcceptanceError
                ):
                    acceptance._observe_browser_containment_blocker(
                        bundle,
                        generation=generation,
                    )
            cleaned.assert_called_once_with(guardian)

    def test_browser_fabricated_sentinel_release_surface_is_absent_at_blocker(self):
        self.assertFalse(hasattr(acceptance, "_BrowserReleaseProof"))
        self.assertFalse(
            hasattr(acceptance, "_release_browser_fabricated_sentinel")
        )
        module = ast.parse(Path(acceptance.__file__).read_text(encoding="utf-8"))
        observer = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_observe_browser_containment_blocker"
        )
        names = {
            selected.id
            for selected in ast.walk(observer)
            if isinstance(selected, ast.Name)
        }
        attributes = {
            selected.attr
            for selected in ast.walk(observer)
            if isinstance(selected, ast.Attribute)
        }
        self.assertNotIn("provider", names)
        self.assertNotIn("secret", names)
        self.assertNotIn("write_browser_worker_secret", attributes)
        self.assertNotIn("_release_browser_fabricated_sentinel", names)

    def test_browser_private_proc_guardian_dies_with_supervisor_without_residue(self):
        before_roots = tuple(sorted(Path("/tmp").glob("buffalo-browser-projection-*")))
        cgroup_parent = Path("/sys/fs/cgroup/system.slice")
        before_cgroups = tuple(sorted(cgroup_parent.glob("buffalo-browser-*")))
        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        supervisor = os.fork()
        if supervisor == 0:
            try:
                os.close(read_descriptor)
                generation = hashlib.sha256(
                    b"focused-supervisor-interruption"
                ).hexdigest()

                real_prctl = acceptance._browser_prctl

                def hold_before_first_init_hardening(
                    option: int,
                    value: int,
                ) -> None:
                    if (
                        option == acceptance._BROWSER_PR_SET_PDEATHSIG
                        and value == int(acceptance.signal.SIGKILL)
                        and os.getpid() == 1
                        and os.getppid() == 0
                    ):
                        time.sleep(60.0)
                    real_prctl(option, value)

                with patch.object(
                    acceptance,
                    "_browser_prctl",
                    side_effect=hold_before_first_init_hardening,
                ):
                    guardian = (
                        acceptance._start_stopped_browser_private_proc_guardian(
                            generation=generation
                        )
                    )
                message = (
                    f"{guardian.process_id} {guardian.process_start_ticks}\n"
                ).encode("ascii")
                os.write(write_descriptor, message)
                with patch.object(
                    acceptance,
                    "_browser_prctl",
                    side_effect=hold_before_first_init_hardening,
                ):
                    acceptance._continue_browser_private_proc_guardian(
                        guardian,
                        generation=generation,
                    )
            finally:
                os._exit(0)
        os.close(write_descriptor)
        guardian_pid = guardian_start = inner_pid = inner_start = 0
        guardian_pidfd = inner_pidfd = -1
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(read_descriptor, selectors.EVENT_READ)
                self.assertTrue(selector.select(5.0))
            raw = os.read(read_descriptor, 128)
            guardian_pid, guardian_start = (
                int(value) for value in raw.strip().split(b" ")
            )
            guardian_pidfd = os.pidfd_open(guardian_pid, 0)
            guardian_info, guardian_bound, _, _ = (
                acceptance._read_browser_pidfd_metadata(guardian_pidfd)
            )
            self.assertEqual(guardian_bound, guardian_pid)
            self.assertGreater(guardian_info.st_ino, 0)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and inner_pid == 0:
                for candidate in Path("/proc").iterdir():
                    if not candidate.name.isascii() or not candidate.name.isdigit():
                        continue
                    selected_pid = int(candidate.name)
                    if selected_pid <= 1 or selected_pid == guardian_pid:
                        continue
                    try:
                        observed = acceptance._read_browser_worker_proc_stat(
                            selected_pid
                        )
                    except acceptance.LocalStagingAcceptanceError:
                        continue
                    if observed.parent_pid == guardian_pid:
                        inner_pid = selected_pid
                        inner_start = observed.start_ticks
                        break
                if inner_pid == 0:
                    time.sleep(0.01)
            self.assertGreater(inner_pid, 1)
            inner_pidfd = os.pidfd_open(inner_pid, 0)
            self.assertEqual(
                os.readlink(f"/proc/self/fd/{inner_pidfd}"),
                "anon_inode:[pidfd]",
            )
            self.assertFalse(
                acceptance._browser_pidfd_is_terminal(inner_pidfd)
            )
            self.assertEqual(
                acceptance._read_browser_worker_proc_stat(
                    inner_pid
                ).start_ticks,
                inner_start,
            )
            os.kill(supervisor, acceptance.signal.SIGKILL)
            waited, status = os.waitpid(supervisor, 0)
            self.assertEqual(waited, supervisor)
            self.assertTrue(os.WIFSIGNALED(status))
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if (
                    acceptance._browser_pidfd_is_terminal(guardian_pidfd)
                    and acceptance._browser_pidfd_is_terminal(inner_pidfd)
                ):
                    break
                time.sleep(0.01)
            else:
                self.fail(
                    "guardian or namespace init survived supervisor SIGKILL"
                )
            self.assertLessEqual(
                acceptance._reap_browser_process_group_children(
                    guardian_pid
                ),
                2,
            )
            for process_id, start_ticks in (
                (guardian_pid, guardian_start),
                (inner_pid, inner_start),
            ):
                try:
                    current = acceptance._read_browser_worker_proc_stat(
                        process_id
                    )
                except acceptance.LocalStagingAcceptanceError:
                    continue
                if current.start_ticks == start_ticks:
                    self.assertIn(
                        current.state,
                        acceptance._BROWSER_TERMINAL_PROCESS_STATES,
                    )
        finally:
            os.close(read_descriptor)
            for descriptor in (guardian_pidfd, inner_pidfd):
                if descriptor < 0:
                    continue
                try:
                    if not acceptance._browser_pidfd_is_terminal(descriptor):
                        acceptance.signal.pidfd_send_signal(
                            descriptor,
                            acceptance.signal.SIGKILL,
                        )
                except (OSError, acceptance.LocalStagingAcceptanceError):
                    pass
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                os.kill(supervisor, acceptance.signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(supervisor, 0)
            except ChildProcessError:
                pass
        self.assertEqual(
            before_roots,
            tuple(sorted(Path("/tmp").glob("buffalo-browser-projection-*"))),
        )
        self.assertEqual(
            before_cgroups,
            tuple(sorted(cgroup_parent.glob("buffalo-browser-*"))),
        )


if __name__ == "__main__":
    unittest.main()
