#!/usr/bin/env python3
"""Run the bounded LOCAL Railway-staging acceptance composition.

The public orchestration is intentionally assembled from exact, separately
attested operators.  This module imports only the Python standard library at
startup so source, dependency, browser, and private-input trust can be proven
before any repository module or credential is loaded.
"""

from __future__ import annotations

import base64
import ctypes
import csv
from dataclasses import dataclass, field, replace
from datetime import datetime
from email.parser import BytesParser
from email.policy import compat32
import errno
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import posixpath
import re
import resource
import selectors
import signal
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any, Mapping
from uuid import UUID


ACCEPTANCE_CONTRACT = "BUFFALO_LOCAL_STAGING_ACCEPTANCE_V1"
BROWSER_WORKER_PROTOCOL = "BUFFALO_LOCAL_STAGING_BROWSER_WORKER_V1"
BROWSER_WORKER_HIDDEN_MODE = "--internal-browser-worker"
MATERIALIZER_ROLE = "research-materializer"
MATERIALIZER_VOLUME_ROLE = "research-volume"
MATERIALIZER_MODULE = "procurement_os.staging_research_materializer"

FROZEN_IMAGE_ID = (
    "sha256:64b2f821aaa12b2c4297f4d28e4f112d98698f204716f36ac81500974ebc0a6f"
)
FROZEN_SOURCE_COMMIT = "f9cd28f801c325cee5dbd22458b777fbc8bead77"
FROZEN_SOURCE_TREE = "2c7efd855001bbbe4a70defe07b6684e8c2d60a2"
FROZEN_IMAGE_ROOTFS_LAYERS = (
    "sha256:e48af84b2108a5d73effd9e16685b42ac33e7994e606398c46690918f5f3604a",
    "sha256:7716c346ed29fe1a805bca1a2ab6174bd3354cffa0e84a5e989a93f8ce5e1e44",
    "sha256:a2ac78ea6115362b78b98fb826f1d2f56a6fb53b14b6fd828b01ebb166e3c3d6",
    "sha256:bccbed27246a15486d39933a2a0ff8530d97da760aa254a82de8c488aac88d2e",
    "sha256:0015e37e84c4241937ad17b83108f28bc939ec5a7ee811b248f43e8fd950840e",
    "sha256:de8348500c5ea4e5c4ad4adfd58a9cd54a66da965560090d774faf890d52f11a",
    "sha256:f32260399152f2410ef15e897f07b7fa518f5580d39d8d95209291bfa7263b7f",
    "sha256:33ee1fde6ed7c7dd9b7b88e91a15bdab749519af8112d91bd28d90c0399c65aa",
    "sha256:7f9d9070986d81c19362946d938b224d1128dd0a6c60abafa76e99f6cec3d511",
    "sha256:f9d123bb5eaf89ad926929aabd29f0de3784b6d28d04d49fc3c47bdef4feeb00",
    "sha256:cbdbe6a410b580e2e8cb76cd9c1bca94323ff5b96f37db0141346edf26041af5",
    "sha256:7fd1b927b698e64c15496d0546924f56b86c149dfc196f753d7120df5d24d7ee",
    "sha256:aec8202091b4d02180bb1f1c663b795f1ccc88b08a4553178b8e77ec97790309",
    "sha256:d03224de5ae1f9f78f5107564c044aab2858aec3587a3d58b1352085fcf68e5f",
    "sha256:bd44ce5a1cc86e352b6483432ed34708645a6047786b8d6be1a0555bfa50838b",
    "sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef",
)

_DOCKER_EXECUTABLE = Path(
    "/nix/store/37rf2zl654djg7989yipq57d5pd195hi-docker-27.5.1/"
    "libexec/docker/docker"
)
_DOCKER_BYTES = 35_648_392
_DOCKER_SHA256 = "03f1d4e930931713fc9ae82302947e87f435bd81714201a225a6c237afd9baee"
_DOCKER_UID = 1000
_DOCKER_GID = 1000
_DOCKER_MODE = 0o555
_DOCKER_CLIENT_VERSION = "27.5.1"
_DOCKER_API_VERSION = "1.47"
_DOCKER_SOCKET = "/var/run/docker.sock"
_DOCKER_ZERO_TIME = "0001-01-01T00:00:00Z"
_DOCKER_METADATA_LIMIT = 256 * 1024
_DOCKER_ATTACH_LIMIT = 16 * 1024
_DOCKER_METADATA_TIMEOUT = 30.0
_BROWSER_REQUEST_LIMIT = 16 * 1024
_BROWSER_READY_LIMIT = 4 * 1024
_BROWSER_RESULT_LIMIT = 16 * 1024
_BROWSER_WORKER_MAX_FD = 1_048_575
_BROWSER_SECRET_BYTES = 43
_BROWSER_PROC_STAT_LIMIT = 16 * 1024
_BROWSER_PROC_VALUE_LIMIT = 64 * 1024
_BROWSER_CGROUP_FILE_LIMIT = 4_096
_BROWSER_CGROUP_PATH_LIMIT = 4_092
_BROWSER_CGROUP_COMPONENT_LIMIT = 255
_BROWSER_CGROUP_EVENTS_LIMIT = 64
_BROWSER_CGROUP_MEMBER_LIMIT = 4_096
_BROWSER_CGROUP_MEMBERS_BYTES_LIMIT = 45_056
_BROWSER_NSPID_LIMIT = 384
_BROWSER_PID_NAMESPACE_DEPTH_LIMIT = 32
_BROWSER_LINUX_PID_MAX = 2_147_483_647
_BROWSER_WORKER_ARGUMENT_LIMIT = 32
_BROWSER_WORKER_DESCRIPTOR_LIMIT = 16
_BROWSER_PYTHON_EXECUTABLE = Path(
    "/nix/store/qzc04a3npl70cyyy6flnnrb2ig3kayxm-python3-3.13.11/"
    "bin/python3.13"
)
_BROWSER_PYTHON_BYTES = 15_776
_BROWSER_PYTHON_SHA256 = (
    "bd5afcc703e9293ebea22ec05ad3a95f5b14ca6b65293a5f2969efe83148f565"
)
_BROWSER_PYTHON_UID = 1000
_BROWSER_PYTHON_GID = 1000
_BROWSER_PYTHON_MODE = 0o555
_BROWSER_RUNTIME_STARTUP_DOMAIN = (
    b"BUFFALO_LOCAL_BROWSER_PYTHON_STARTUP_FILES_V1\0"
)
_BROWSER_RUNTIME_STARTUP_SHA256 = (
    "5f0b49198f006b36808508d976d2b9e35410839e0f34700a208877d815b10934"
)
_BROWSER_RUNTIME_STDLIB_DOMAIN = (
    b"BUFFALO_LOCAL_BROWSER_PYTHON_STDLIB_TREE_V1\0"
)
_BROWSER_RUNTIME_STDLIB_SHA256 = (
    "91e877d25cd89b60c1125fbaca143c88ef9d7a06c019ab86de658d9f9f4e4600"
)
_BROWSER_RUNTIME_STDLIB_ROOT = Path(
    "/nix/store/qzc04a3npl70cyyy6flnnrb2ig3kayxm-python3-3.13.11/"
    "lib/python3.13"
)
_BROWSER_RUNTIME_STDLIB_ZIP = Path(
    "/nix/store/qzc04a3npl70cyyy6flnnrb2ig3kayxm-python3-3.13.11/"
    "lib/python313.zip"
)
_BROWSER_RUNTIME_STDLIB_ENTRIES = 3_251
_BROWSER_RUNTIME_STDLIB_FILES = 3_137
_BROWSER_RUNTIME_STDLIB_DIRECTORIES = 113
_BROWSER_RUNTIME_STDLIB_SYMLINKS = 1
_BROWSER_RUNTIME_STDLIB_BYTES = 102_170_195
_BROWSER_RUNTIME_NATIVE_DOMAIN = (
    b"BUFFALO_LOCAL_BROWSER_NATIVE_RUNTIME_TREE_V1\0"
)
_BROWSER_RUNTIME_NATIVE_SHA256 = (
    "f05a1558c91a1a8979a5f9251fc52bc3b0063d1dcfa99661457c54fadf7bab0b"
)
_BROWSER_RUNTIME_NATIVE_ENTRIES = 304
_BROWSER_RUNTIME_NATIVE_REGULAR_FILES = 287
_BROWSER_RUNTIME_NATIVE_DIRECTORIES = 4
_BROWSER_RUNTIME_NATIVE_SYMLINKS = 13
_BROWSER_RUNTIME_NATIVE_BYTES = 21_772_212
_BROWSER_RUNTIME_GCONV_DOMAIN = (
    b"BUFFALO_LOCAL_BROWSER_GLIBC_GCONV_TREE_V1\0"
)
_BROWSER_RUNTIME_GCONV_SHA256 = (
    "8f79a850c7b218e482ca3b6d34bd1cc3d12a4bbe0db4039c2c05aa51f83473c8"
)
_BROWSER_RUNTIME_GCONV_ROOT = Path(
    "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
    "lib/gconv"
)
_BROWSER_RUNTIME_GCONV_CACHE = _BROWSER_RUNTIME_GCONV_ROOT / "gconv-modules.cache"
_BROWSER_RUNTIME_GCONV_ENTRIES = 256
_BROWSER_RUNTIME_GCONV_FILES = 255
_BROWSER_RUNTIME_GCONV_DIRECTORIES = 1
_BROWSER_RUNTIME_GCONV_BYTES = 8_441_954
_BROWSER_RUNTIME_LOCALE_DOMAIN = (
    b"BUFFALO_LOCAL_BROWSER_GLIBC_LOCALE_TREE_V1\0"
)
_BROWSER_RUNTIME_LOCALE_SHA256 = (
    "784e2ff45a2b677d7eaed6d3fd702b999776128ab1c785adfc440d63f6e60136"
)
_BROWSER_RUNTIME_LOCALE_ROOT = Path(
    "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
    "lib/locale/C.utf8"
)
_BROWSER_RUNTIME_LOCALE_ARCHIVE = _BROWSER_RUNTIME_LOCALE_ROOT.parent / "locale-archive"
_BROWSER_RUNTIME_LOCALE_ENTRIES = 13
_BROWSER_RUNTIME_LOCALE_FILES = 12
_BROWSER_RUNTIME_LOCALE_DIRECTORIES = 1
_BROWSER_RUNTIME_LOCALE_BYTES = 366_145
_BROWSER_RUNTIME_STARTUP_FILE_BYTES_LIMIT = 8 * 1024 * 1024
_BROWSER_RUNTIME_TREE_DEPTH_LIMIT = 64
_BROWSER_RUNTIME_PATH_BYTES_LIMIT = 4_096
_BROWSER_RUNTIME_COMPONENT_BYTES_LIMIT = 255
_BROWSER_DEPENDENCY_IMAGE_PATH = (
    "/opt/buffalo-venv/lib/python3.13/site-packages"
)
_BROWSER_DEPENDENCY_IMAGE_EXPORT_TIMEOUT = 180.0
_BROWSER_DEPENDENCY_IMAGE_EXPORT_LIMIT = 320 * 1024 * 1024
_BROWSER_DEPENDENCY_IMAGE_EXPORT_MEMBERS = 39
_BROWSER_DEPENDENCY_OCI_MANIFEST_SHA256 = (
    "cc82cb64523bae47b90388f9494ee7790faaa639937a1ba3d3edabc32e212cdd"
)
_BROWSER_DEPENDENCY_OCI_MANIFEST_BYTES = 2_684
_BROWSER_DEPENDENCY_IMAGE_CONFIG_BYTES = 12_140
_BROWSER_DEPENDENCY_LAYER_SIZES = (
    77_885_440,
    9_570_304,
    37_720_064,
    5_120,
    88_640_000,
    18_944,
    32_407_552,
    6_656,
    4_045_824,
    43_008,
    562_176,
    442_880,
    73_216,
    156_160,
    37_718_528,
    1_024,
)
_BROWSER_DEPENDENCY_LAYER_PHYSICAL_MEMBERS = (
    4_225,
    544,
    1_851,
    8,
    2_520,
    14,
    864,
    10,
    99,
    13,
    21,
    4,
    4,
    4,
    996,
    0,
)
_BROWSER_DEPENDENCY_LAYER_SEMANTIC_MEMBERS = (
    4_225,
    541,
    1_851,
    8,
    2_520,
    14,
    864,
    10,
    99,
    13,
    21,
    4,
    4,
    4,
    996,
    0,
)
_BROWSER_DEPENDENCY_LEGACY_BLOBS = (
    ("c086ef2f0a2f60a300313b55570800fb1555b466df9c1e3ba7e4268ea7b1f720", 401),
    ("bbeec3a6422b87810663983b1326f1145eaa0d42ad6ad1f637e1a8c70dbf826f", 477),
    ("66d444cf5bf3f2431e36d6dbb5fee8970df9c18eb65c3fead4deae9c553d4cda", 477),
    ("03c23b71e27e5ac9b9782aef78630275f7ad4ca136d2f3e05732acbad56c5ec0", 477),
    ("84e41f5d96a177d52fc77f1c7e0c4fdd85570684b9c0237101b2db0d08f7c894", 477),
    ("2ea2b5a8a6a8071a5ab7bbf4b99caa2604ec463e61b985f8b655ff5541ca7845", 477),
    ("9a70daafb7eb3821f87d359d9cd3eff4e012f4a0a284b271def50f99ca3dfd32", 477),
    ("3dfa2e9fedbfc404310880c9750381b9d02e2c70b2ae6d66f72ea6e46010ba29", 477),
    ("329dc754b1ec90b0410fb10a939844eb237dcb1db7db558f41a6ac5d75f66282", 477),
    ("69724b5310e2a959cddff3f802601d6cc10ac1d5e6354d1310a0415719429749", 477),
    ("24f7527a1a16938b56d3ec297491f474a2bd3552757dcca965f9c17e906653a9", 477),
    ("cd2b4b44c4bc82cef2ce165f90a118151fd19fe12cb3b86bdda531bcc0d0cfbc", 477),
    ("fa2f5a2b618f27ecaa8907b1a7e1c70615567bdae40872aeb3a8768969237ab7", 477),
    ("0680c95f0293b5acefb778862ff0d859907ce0f3902588255057f746589da087", 477),
    ("0e2f1197578857abb1d50b4ee2191a73ab64a9178f5a7c5653e8bfcd9d9a9d5c", 477),
    ("628ed475a6fa79189fb357db0305277a3def050086b55ffe91ce07c14e29baa6", 1_254),
)
_BROWSER_DEPENDENCY_TREE_DOMAIN = (
    b"BUFFALO_LOCAL_BROWSER_PYTHON_DEPENDENCY_TREE_V1\0"
)
_BROWSER_DEPENDENCY_TREE_SHA256 = (
    "3c20c381aacf01fd0de297286ce26aad1a2a13d538a0dcbc8eca8d30e81e22a3"
)
_BROWSER_DEPENDENCY_LAYER_PROVENANCE_DOMAIN = (
    b"BUFFALO_LOCAL_BROWSER_DEPENDENCY_LAYER_PROVENANCE_V1\0"
)
_BROWSER_DEPENDENCY_LAYER_PROVENANCE_SHA256 = (
    "63fe97d19516e501d82b05366631cef7c334be2b08b46a1fa339ed69d5d37190"
)
_BROWSER_DEPENDENCY_TREE_ENTRIES = 833
_BROWSER_DEPENDENCY_TREE_FILES = 726
_BROWSER_DEPENDENCY_TREE_DIRECTORIES = 107
_BROWSER_DEPENDENCY_TREE_SYMLINKS = 0
_BROWSER_DEPENDENCY_TREE_BYTES = 31_726_568
_BROWSER_DEPENDENCY_SOURCE_ENTRIES = 836
_BROWSER_DEPENDENCY_SOURCE_FILES = 729
_BROWSER_DEPENDENCY_SOURCE_DIRECTORIES = 107
_BROWSER_DEPENDENCY_SOURCE_BYTES = 31_731_853
_BROWSER_DEPENDENCY_ARCHIVE_LIMIT = 40 * 1024 * 1024
_BROWSER_DEPENDENCY_ARCHIVE_MEMBERS = 837
_BROWSER_DEPENDENCY_PATH_BYTES_LIMIT = 4_096
_BROWSER_DEPENDENCY_PATH_DEPTH_LIMIT = 64
_BROWSER_DEPENDENCY_RECORD_ROWS = 731
_BROWSER_APPLICATION_TREE_DOMAIN = b"BUFFALO_LOCAL_BROWSER_SOURCE_TREE_V1\0"
_BROWSER_APPLICATION_TREE_SHA256 = (
    "b3f52e90e8b47d79f9c198b89d194d9d04a4d745e12ec9a39c7a2e513ef58aca"
)
_BROWSER_APPLICATION_TREE_ENTRIES = 133
_BROWSER_APPLICATION_TREE_FILES = 124
_BROWSER_APPLICATION_TREE_DIRECTORIES = 9
_BROWSER_APPLICATION_TREE_SYMLINKS = 0
_BROWSER_APPLICATION_TREE_BYTES = 5_209_228
_BROWSER_APPLICATION_ROOT_MODE = 0o755
_BROWSER_APPLICATION_ROOT_UID = 0
_BROWSER_APPLICATION_ROOT_GID = 0
_BROWSER_APPLICATION_WINNING_LAYER = (
    "bd44ce5a1cc86e352b6483432ed34708645a6047786b8d6be1a0555bfa50838b"
)
_BROWSER_DEPENDENCY_DISTRIBUTIONS = (
    ("annotated-doc", "0.0.5", "annotated_doc-0.0.5.dist-info"),
    ("annotated-types", "0.8.0", "annotated_types-0.8.0.dist-info"),
    ("anyio", "4.14.2", "anyio-4.14.2.dist-info"),
    ("argon2-cffi", "25.1.0", "argon2_cffi-25.1.0.dist-info"),
    (
        "argon2-cffi-bindings",
        "26.1.0",
        "argon2_cffi_bindings-26.1.0.dist-info",
    ),
    ("certifi", "2026.7.22", "certifi-2026.7.22.dist-info"),
    ("cffi", "2.1.1", "cffi-2.1.1.dist-info"),
    ("click", "8.4.2", "click-8.4.2.dist-info"),
    ("fastapi", "0.141.1", "fastapi-0.141.1.dist-info"),
    ("h11", "0.16.0", "h11-0.16.0.dist-info"),
    ("httpcore", "1.0.9", "httpcore-1.0.9.dist-info"),
    ("httpx", "0.28.1", "httpx-0.28.1.dist-info"),
    ("idna", "3.18", "idna-3.18.dist-info"),
    ("psycopg", "3.3.4", "psycopg-3.3.4.dist-info"),
    ("psycopg-binary", "3.3.4", "psycopg_binary-3.3.4.dist-info"),
    ("pycparser", "3.0", "pycparser-3.0.dist-info"),
    ("pydantic", "2.13.4", "pydantic-2.13.4.dist-info"),
    ("pydantic-core", "2.46.4", "pydantic_core-2.46.4.dist-info"),
    (
        "python-multipart",
        "0.0.32",
        "python_multipart-0.0.32.dist-info",
    ),
    ("starlette", "1.6.0", "starlette-1.6.0.dist-info"),
    (
        "typing-extensions",
        "4.16.0",
        "typing_extensions-4.16.0.dist-info",
    ),
    (
        "typing-inspection",
        "0.4.2",
        "typing_inspection-0.4.2.dist-info",
    ),
    ("uvicorn", "0.52.1", "uvicorn-0.52.1.dist-info"),
)
_BROWSER_DEPENDENCY_SOURCE_EXTRAS = (
    (
        "_virtualenv.pth",
        18,
        "69ac3d8f27e679c81b94ab30b3b56e9cd138219b1ba94a1fa3606d5a76a1433d",
    ),
    (
        "_virtualenv.py",
        5_246,
        "cfb3db86aaa53bb62b5ff764970bec2d71c9228590a0ebec57f6ec926cc0bf1a",
    ),
    (
        "buffalo-procurement-os.pth",
        21,
        "7c5c32236433b1a27f630d96b784222762668ec4f91122f95e3e7b06c237e038",
    ),
)
_BROWSER_DEPENDENCY_EXTERNAL_RECORDS = (
    (
        "cffi-2.1.1.dist-info/RECORD",
        "../../../bin/cffi-gen-src",
        "sha256=y9V31-hejqtKxp1K7o92KPXgJNgartBxou8zqXQAgP4",
        "310",
    ),
    (
        "fastapi-0.141.1.dist-info/RECORD",
        "../../../bin/fastapi",
        "sha256=bHMlmwvdHYHrqO3YaVRuVgM9K8ZjlU2n7fkA_w1pP94",
        "305",
    ),
    (
        "httpx-0.28.1.dist-info/RECORD",
        "../../../bin/httpx",
        "sha256=IxjXEJ-SoJehyXYT38CK5TSa7ZHT3IGKchHp3iHMWJQ",
        "299",
    ),
    (
        "idna-3.18.dist-info/RECORD",
        "../../../bin/idna",
        "sha256=OTMRIDQz6Dg2vcB33Lj6ZWMg9ODVRO5YDJgeY4PHMZY",
        "302",
    ),
    (
        "uvicorn-0.52.1.dist-info/RECORD",
        "../../../bin/uvicorn",
        "sha256=0zhvJFoWpEOPv41MwU26lSu1dPEGaYdfu6YHG3Xv1VM",
        "306",
    ),
)
_FROZEN_UV_LOCK_BYTES = 34_616
_FROZEN_UV_LOCK_SHA256 = (
    "f8613b17cb90ca5e3070e13c47cd6d53a60a8c314257d5588986cf92d0717be9"
)
_FROZEN_PYPROJECT_BYTES = 299
_FROZEN_PYPROJECT_SHA256 = (
    "c83fa94b31129a28040b199c4fdb6902287646bf306129066eadcc810fe1d346"
)
_FROZEN_DOCKERFILE_BYTES = 4_295
_FROZEN_DOCKERFILE_SHA256 = (
    "744c0aa01c187eb0faefa9dfd1a0d6533ab1e794e452b843b3268f4162cc5573"
)
_BROWSER_DEPENDENCY_REPOSITORY_ROOT = Path(__file__).resolve(
    strict=True
).parents[2]
_BROWSER_DEPENDENCY_BUILD_FILES = (
    (
        _BROWSER_DEPENDENCY_REPOSITORY_ROOT / "uv.lock",
        _FROZEN_UV_LOCK_BYTES,
        _FROZEN_UV_LOCK_SHA256,
    ),
    (
        _BROWSER_DEPENDENCY_REPOSITORY_ROOT / "pyproject.toml",
        _FROZEN_PYPROJECT_BYTES,
        _FROZEN_PYPROJECT_SHA256,
    ),
    (
        _BROWSER_DEPENDENCY_REPOSITORY_ROOT / "Dockerfile",
        _FROZEN_DOCKERFILE_BYTES,
        _FROZEN_DOCKERFILE_SHA256,
    ),
    (
        _BROWSER_DEPENDENCY_REPOSITORY_ROOT
        / "procurement/tools/audit_staging_purchasing_browser.py",
        53_889,
        "ce106b21b789980886cbadff90e422a7c4b118734c996790c181d754c61c6a86",
    ),
    (
        _BROWSER_DEPENDENCY_REPOSITORY_ROOT
        / "procurement/tools/audit_staging_purchasing_browser.mjs",
        58_550,
        "951da82b06202a77a25fa3f219197860cfb24c653a23de551d2b2e242603811c",
    ),
)
_BROWSER_WORKER_RUNNER_SOURCE = Path(__file__).resolve(strict=True).with_name(
    "run_local_staging_browser_worker.py"
)
_BROWSER_WORKER_RUNNER_BYTES = 664
_BROWSER_WORKER_RUNNER_SHA256 = (
    "f719af79822a3c2caa6b39bf4fdcda5aa0f39ceeced8f246fbcd154781047ff2"
)
_BROWSER_WORKER_RUNNER_MEMFD_NAME = "buffalo-local-staging-browser-worker"
_BROWSER_WORKER_RUNNER_MEMFD_TARGET = (
    f"/memfd:{_BROWSER_WORKER_RUNNER_MEMFD_NAME} (deleted)"
)
_BROWSER_WORKER_RUNNER_MODE = 0o400
_BROWSER_WORKER_RUNNER_SEALS = (
    fcntl.F_SEAL_SEAL
    | fcntl.F_SEAL_SHRINK
    | fcntl.F_SEAL_GROW
    | fcntl.F_SEAL_WRITE
)
_BROWSER_RUNTIME_BUNDLE_MAGIC = b"BUFFALO_LOCAL_BROWSER_RUNTIME_BUNDLE_V1\0"
_BROWSER_RUNTIME_BUNDLE_PROTOCOL = "BUFFALO_LOCAL_BROWSER_RUNTIME_BUNDLE_V1"
_BROWSER_RUNTIME_BUNDLE_MEMFD_NAME_PREFIX = "buffalo-local-browser-runtime"
_BROWSER_RUNTIME_BUNDLE_MEMFD_TARGET = re.compile(
    r"\A/memfd:buffalo-local-browser-runtime-[0-9a-f]{32} \(deleted\)\Z"
)
_BROWSER_RUNTIME_BUNDLE_MODE = 0o400
_BROWSER_RUNTIME_BUNDLE_SEALS = _BROWSER_WORKER_RUNNER_SEALS
_BROWSER_RUNTIME_BUNDLE_ENTRY_LIMIT = 5_000
_BROWSER_RUNTIME_BUNDLE_MANIFEST_LIMIT = 4 * 1024 * 1024
_BROWSER_RUNTIME_BUNDLE_BYTES_LIMIT = 512 * 1024 * 1024
_BROWSER_RUNTIME_BUNDLE_EXPECTED_ENTRIES = 4_575
_BROWSER_RUNTIME_BUNDLE_EXPECTED_REGULAR_BYTES = 171_833_618
_BROWSER_RUNTIME_BUNDLE_EXPECTED_BYTES = 173_499_779
_BROWSER_RUNTIME_BUNDLE_EXPECTED_MANIFEST_SHA256 = (
    "1a176afff9196463367f3201f6e012ac874937a788abb325d9297ef890cd5edf"
)
_BROWSER_RUNTIME_BUNDLE_EXPECTED_SHA256 = (
    "ff46c1cc615659888aeb9c01ad52aef00b45c595d87491646c00454cbb2a858c"
)
_BROWSER_RUNTIME_PARENT_POLICY_SHA256 = (
    "3a8831475b2d6b539f5de30f25dd35ea4d2869ca8c750465487ac160fe91ee17"
)
_BROWSER_PROJECTION_PROTOCOL = "BUFFALO_LOCAL_BROWSER_PROJECTION_V1"
_BROWSER_PROJECTION_DOMAIN = b"BUFFALO_LOCAL_BROWSER_PROJECTION_V1\0"
_BROWSER_PROJECTION_POLICY_DOMAIN = (
    b"BUFFALO_LOCAL_BROWSER_PROJECTION_POLICY_V2\0"
)
_BROWSER_PROJECTION_POLICY_ENTRIES_DOMAIN = (
    b"BUFFALO_LOCAL_BROWSER_PROJECTION_POLICY_ENTRIES_V1\0"
)
_BROWSER_PROJECTION_POLICY_SHA256 = (
    "b31f20961531fa21ec8056de9e2f6100b3fe4b55ab906fd170243f1a432a66f1"
)
_BROWSER_PROJECTION_POLICY_ID_SHA256 = (
    "c526a64e7489481b8183f8199af3a02f8f04b7dfef973968087c09c108b6b6f3"
)
_BROWSER_PROJECTION_EXPECTED_ENTRIES = 4_588
_BROWSER_PROJECTION_EXPECTED_REGULAR_BYTES = 171_833_799
_BROWSER_PROJECTION_EXPECTED_SHA256 = (
    "1c142df8df4b6e9a775062c8fe718ff265025049835c4d69338d4e9b543fdbad"
)
_BROWSER_PROJECTION_ROOT_PREFIX = "buffalo-browser-projection-"
_BROWSER_PROJECTION_TMPFS_BYTES = 256 * 1024 * 1024
_BROWSER_PROJECTION_TMPFS_INODES = 6_000
_BROWSER_PROJECTION_STATUS_LIMIT = 64 * 1024
_BROWSER_CONTAINMENT_PROTOCOL = "BUFFALO_LOCAL_BROWSER_CONTAINMENT_V1"
_BROWSER_CONTAINMENT_TIMEOUT_SECONDS = 120.0
_BROWSER_GUARDIAN_CLEANUP_TIMEOUT_SECONDS = 5.0
_BROWSER_PRIVATE_PROC_BLOCKER_STAGE = "fsmount+legacy-mount"
_BROWSER_PRIVATE_PROC_BLOCKER_ERRNO = errno.EPERM
_BROWSER_CLONE3_SYSCALL = 435
_BROWSER_FSOPEN_SYSCALL = 430
_BROWSER_FSCONFIG_SYSCALL = 431
_BROWSER_FSMOUNT_SYSCALL = 432
_BROWSER_MOVE_MOUNT_SYSCALL = 429
_BROWSER_PIVOT_ROOT_SYSCALL = 155
_BROWSER_FSOPEN_CLOEXEC = 1
_BROWSER_FSCONFIG_CMD_CREATE = 6
_BROWSER_FSCONFIG_SET_STRING = 1
_BROWSER_FSMOUNT_CLOEXEC = 1
_BROWSER_MOVE_MOUNT_F_EMPTY_PATH = 0x00000004
_BROWSER_MOVE_MOUNT_T_EMPTY_PATH = 0x00000040
_BROWSER_MOUNT_SETATTR_SYSCALL = 442
_BROWSER_AT_EMPTY_PATH = 0x00001000
_BROWSER_MOUNT_ATTR_RDONLY = 0x00000001
_BROWSER_MOUNT_ATTR_NOSUID = 0x00000002
_BROWSER_MOUNT_ATTR_NODEV = 0x00000004
_BROWSER_AT_FDCWD = -100
_BROWSER_PR_SET_PDEATHSIG = 1
_BROWSER_PR_SET_DUMPABLE = 4
_BROWSER_PR_SET_NO_NEW_PRIVS = 38
_BROWSER_MS_RDONLY = 1
_BROWSER_MS_NOSUID = 2
_BROWSER_MS_NODEV = 4
_BROWSER_MS_NOEXEC = 8
_BROWSER_MS_REMOUNT = 32
_BROWSER_MS_REC = 16_384
_BROWSER_MS_PRIVATE = 1 << 18
_BROWSER_MNT_DETACH = 2
_BROWSER_TMPFS_MAGIC = 0x01021994
_BROWSER_PROJECTION_MASKED_SIGNALS = frozenset(
    signal.valid_signals() - {signal.SIGKILL, signal.SIGSTOP}
)
_BROWSER_LIVE_PROCESS_STATES = frozenset({"R", "S"})
_BROWSER_TERMINAL_PROCESS_STATES = frozenset({"X", "x", "Z"})
_BROWSER_PROCESS_STATES = frozenset(
    {"R", "S", "D", "T", "t", "W", "X", "x", "Z", "P", "I"}
)
_BROWSER_WORKER_ENVIRONMENT = (
    ("LANG", "C.UTF-8"),
    ("LC_ALL", "C.UTF-8"),
    ("TZ", "UTC"),
)
_BROWSER_HANDLE_TOKEN = object()
_BROWSER_FROZEN_RUNTIME_BUNDLE_TOKEN = object()
_BROWSER_GUARDIAN_LEASE_TOKEN = object()
_SHA256_TEXT = re.compile(r"\A[0-9a-f]{64}\Z")
_GIT_OID_TEXT = re.compile(r"\A[0-9a-f]{40}\Z")
_CANONICAL_FD = re.compile(r"\A(?:[3-9]|[1-9][0-9]+)\Z")
_START_TICKS_TEXT = re.compile(r"\A[1-9][0-9]*\Z")
_BROWSER_PRODUCT_TEXT = re.compile(
    r"\A(?:Chrome|HeadlessChrome)/[0-9]+(?:\.[0-9]+)+\Z"
)
_VERSION_TEXT = re.compile(r"\A[0-9]+(?:\.[0-9]+)+\Z")
_BROWSER_SECRET_TEXT = re.compile(rb"\A[A-Za-z0-9_-]{43}\Z")
_OPERATOR_PROOF_CONTRACT = "BUFFALO_STOPPED_SERVICE_PRICE_STAGE_V1"
_OPERATOR_SOURCE_REF = "procurement/config/synthetic_price_replacement_book.csv"
_OPERATOR_SOURCE_BYTES = 1_590
_OPERATOR_RAW_SHA256 = (
    "00071443ea8c54b57fc6014c3b1daf204081714a2ff09b98bed6c56a0dd3862c"
)
_OPERATOR_TARGET_ATTESTATION_SHA256 = (
    "517843a848fd07e5fc62b9713279a8bcfc52a782890aee68c7d900dd3600d77a"
)
_OPERATOR_PROOF_KEYS = frozenset(
    {
        "contract",
        "source_ref",
        "source_bytes",
        "raw_sha256",
        "target_attestation_sha256",
        "batch_id",
        "status",
        "declaration_sha256",
        "validation_fingerprint",
        "proposed_scope_membership_sha256",
        "staging_rows_sha256",
        "validation_issues_sha256",
        "unchanged_database_sha256",
        "unchanged_storage_sha256",
        "idempotent_replay",
        "ambiguous_commit_recovered",
    }
)
_OPERATOR_PROOF_NON_HASH_KEYS = frozenset(
    {
        "contract",
        "source_ref",
        "source_bytes",
        "batch_id",
        "status",
        "idempotent_replay",
        "ambiguous_commit_recovered",
    }
)
_BROWSER_PHASE_CONTRACT = "BUFFALO_STAGING_PURCHASING_BROWSER_PHASE_V1"
_BROWSER_PHASE = "price-confirm"
_BROWSER_NODE_VERSION = "v24.13.0"
_BROWSER_ASSERTION_COUNT = 62
_BROWSER_ASSERTION_MANIFEST_SHA256 = (
    "64a5063520cbe378502b7930f8b51ba784b05c0e9c2ea986de9adeda991efb50"
)
_BROWSER_TARGET_SUMMARY_KEYS = frozenset(
    {
        "active_guarded",
        "guarded",
        "inert",
        "live_detached",
        "tracked",
        "unattached",
        "unguarded",
        "unresumed",
        "unsupported",
    }
)
_BROWSER_PROOF_KEYS = frozenset(
    {
        "assertion_count",
        "assertion_manifest_sha256",
        "batch_id",
        "browser_js_version",
        "browser_pid",
        "browser_product",
        "browser_protocol_version",
        "browser_start_time",
        "confirmation_preview_sha256",
        "contract",
        "driver_sha256",
        "node_sha256",
        "node_version",
        "operational_status_after",
        "operational_status_before",
        "operator_proof_sha256",
        "phase",
        "raw_bytes",
        "raw_sha256",
        "screenshot_bytes",
        "screenshot_sha256",
        "source_commit",
        "source_tree",
        "status_after",
        "status_before",
        "target_summary",
        "temporal_basis",
        "tls_certificate_sha256",
    }
)

ACCEPTED_INVENTORY_BYTES = 49_648
ACCEPTED_INVENTORY_SHA256 = (
    "97fd10669302a2158882d08102b8d306b51b6b3c61bae6941ac957ef787238b2"
)
ACCEPTED_BUNDLE_BYTES = 2_863_932
ACCEPTED_BUNDLE_SHA256 = (
    "07009cb0f11c9d3a7425878622fbb8b6d081bb96e08bfe51f616e3608c7f82cc"
)

_IMAGE_ID = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_RUN_ID = re.compile(r"\A[0-9a-f]{32}\Z")
_SAFE_DOCKER_NAME = re.compile(r"\A[a-z0-9][a-z0-9_.-]{0,127}\Z")
_CHUNK_BYTES = 1024 * 1024
_MATERIALIZER_TMPFS = (
    "/run/buffalo-research-materializer:"
    "rw,nosuid,nodev,noexec,mode=0700,uid=0,gid=0,size=67108864"
)
_MATERIALIZER_CAPABILITIES = ("CHOWN", "DAC_READ_SEARCH", "FOWNER")
_MATERIALIZER_COMMAND = (
    "-g",
    "--",
    "/opt/buffalo-venv/bin/python",
    "-I",
    "-B",
    "-m",
    MATERIALIZER_MODULE,
)
_SERVICE_ENTRYPOINT = (
    "/usr/bin/tini",
    "-g",
    "--",
    "/opt/buffalo-venv/bin/python",
    "-I",
    "-B",
    "-m",
    "procurement_os.staging_bootstrap",
)
_IMAGE_ENVIRONMENT = (
    "PATH=/opt/buffalo-venv/bin:/usr/local/bin:/usr/bin:/bin",
    "GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305",
    "PYTHON_VERSION=3.13.11",
    "PYTHON_SHA256=16ede7bb7cdbfa895d11b0642fa0e523f291e6487194d53cf6d3b338c3a17ea2",
    "BUFFALO_STAGING_VOLUME_ROOT=/data",
    "LANG=C.UTF-8",
    "LC_ALL=C.UTF-8",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONUNBUFFERED=1",
    "TZ=UTC",
)


class LocalStagingAcceptanceError(RuntimeError):
    """The LOCAL acceptance composition differs from its frozen contract."""


@dataclass(frozen=True)
class MaterializerIngress:
    research_root: Path
    inventory_path: Path
    bundle_path: Path


@dataclass(frozen=True)
class MaterializerInvocation:
    image_id: str
    run_id: str
    volume_name: str
    container_name: str
    ingress: MaterializerIngress | None


@dataclass(frozen=True)
class MaterializerVolumeFingerprint:
    name: str
    driver: str
    scope: str
    labels: tuple[tuple[str, str], ...]
    options: None
    mountpoint: str
    created_at: str


@dataclass(frozen=True)
class MaterializerPhaseProof:
    contract: str
    role: str
    container_id: str
    immutable_envelope_sha256: str
    started_at: str
    finished_at: str


@dataclass(frozen=True)
class BrowserWorkerArguments:
    request_descriptor: int
    ready_descriptor: int
    secret_descriptor: int
    result_descriptor: int


@dataclass(frozen=True)
class BrowserWorkerRequest:
    protocol: str
    frame: str
    challenge: str
    run_id: str
    source_commit: str
    source_tree: str
    cdp_endpoint: str
    evidence_root: str
    operator_proof_json: bytes = field(repr=False)
    operator_batch_id: str
    operator_proof_sha256: str
    tls_certificate_sha256: str
    chromium_pid: int


@dataclass(frozen=True)
class BrowserWorkerReady:
    protocol: str
    frame: str
    challenge: str
    config_sha256: str
    worker_pid: int
    worker_start_ticks: int
    source_commit: str
    source_tree: str
    chromium_pid: int
    browser_start_time: str
    python_executable_sha256: str
    module_manifest_sha256: str
    driver_sha256: str
    node_sha256: str
    preflight_sha256: str


@dataclass(frozen=True)
class BrowserWorkerExpectedAttestation:
    challenge: str
    run_id: str
    source_commit: str
    source_tree: str
    cdp_endpoint: str
    evidence_root: str
    tls_certificate_sha256: str
    chromium_pid: int
    browser_start_time: str
    worker_pid: int
    worker_start_ticks: int
    python_executable_sha256: str
    module_manifest_sha256: str
    driver_sha256: str
    node_sha256: str
    preflight_sha256: str


@dataclass(frozen=True)
class BrowserWorkerResult:
    protocol: str
    frame: str
    challenge: str
    config_sha256: str
    ready_sha256: str
    worker_pid: int
    worker_start_ticks: int
    proof_json: bytes = field(repr=False)


@dataclass(frozen=True)
class BrowserWorkerProcessStat:
    pid: int
    state: str
    parent_pid: int
    process_group: int
    session_id: int
    start_ticks: int


@dataclass(frozen=True)
class BrowserCgroupEvents:
    populated: bool
    frozen: bool


@dataclass(frozen=True)
class BrowserContainmentTextEvidence:
    """Normalized text only; never launch or credential-release authority."""

    cgroup_path: str
    events: BrowserCgroupEvents
    process_ids: tuple[int, ...]
    thread_ids: tuple[int, ...]
    init_outer_pid: int
    worker_outer_pid: int
    init_namespace_pids: tuple[int, ...]
    worker_namespace_pids: tuple[int, ...]


@dataclass(frozen=True)
class _BrowserRuntimeFileExpectation:
    path: Path
    kind: str
    size: int
    mode: int
    payload: str
    user_id: int = 1000
    group_id: int = 1000


@dataclass(frozen=True)
class _BrowserRuntimeStartupObservation:
    sha256: str
    all_source_mounts_read_only: bool


@dataclass(frozen=True)
class _BrowserStdlibObservation:
    sha256: str
    entries: int
    regular_files: int
    directories: int
    symlinks: int
    regular_bytes: int
    all_source_mounts_read_only: bool


@dataclass(frozen=True)
class _BrowserPythonRuntimeObservation:
    startup_sha256: str
    stdlib_sha256: str
    stdlib_entries: int
    stdlib_regular_files: int
    stdlib_directories: int
    stdlib_symlinks: int
    stdlib_regular_bytes: int
    stdlib_zip_absent: bool
    native_sha256: str
    native_entries: int
    native_regular_files: int
    native_directories: int
    native_symlinks: int
    native_regular_bytes: int
    gconv_cache_absent: bool
    locale_archive_absent: bool
    all_source_mounts_read_only: bool
    execution_authority: bool


@dataclass(frozen=True)
class _BrowserPythonRuntimeSnapshot:
    entries: tuple[_BrowserRuntimeBundleEntry, ...]
    observation: _BrowserPythonRuntimeObservation
    execution_authority: bool


@dataclass(frozen=True)
class _BrowserDependencySourceObservation:
    image_id: str
    tree_sha256: str
    entries: int
    regular_files: int
    directories: int
    symlinks: int
    regular_bytes: int
    source_entries: int
    source_regular_files: int
    source_directories: int
    source_regular_bytes: int
    record_rows: int
    distributions: tuple[tuple[str, str, str], ...]
    excluded_source_files: tuple[tuple[str, str], ...]
    execution_authority: bool


@dataclass(frozen=True)
class _BrowserDependencyTreeEntry:
    path: str
    kind: str
    mode: int
    user_id: int
    group_id: int
    layer_sha256: str
    content: bytes


@dataclass(frozen=True)
class _BrowserDependencyImageSnapshot:
    image_id: str
    rootfs_layers: tuple[str, ...]
    source_entries: tuple[_BrowserDependencyTreeEntry, ...]
    selected_entries: tuple[_BrowserDependencyTreeEntry, ...]
    observation: _BrowserDependencySourceObservation
    execution_authority: bool
    application_entries: tuple[_BrowserDependencyTreeEntry, ...] = ()
    application_tree_sha256: str = ""
    audit_entries: tuple[_BrowserDependencyTreeEntry, ...] = ()
    dependency_root: _BrowserDependencyTreeEntry | None = None
    application_root: _BrowserDependencyTreeEntry | None = None


@dataclass(frozen=True)
class _BrowserRuntimeBundleEntry:
    path: str
    kind: str
    mode: int
    user_id: int
    group_id: int
    provenance: str
    content: bytes


@dataclass
class _PinnedBrowserRuntimeBundle:
    """Owned sealed runtime bytes; not yet an executable projection."""

    descriptor: int
    target: str
    device: int
    inode: int
    size: int
    sha256: str
    manifest_sha256: str
    entries: int
    regular_bytes: int
    execution_authority: bool
    _owner_token: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _source_token: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def close(self) -> None:
        descriptor = self.descriptor
        if (
            descriptor == -1
            and self._owner_token is None
            and self._source_token is None
        ):
            return
        if (
            self._owner_token is not _BROWSER_HANDLE_TOKEN
            or type(descriptor) is not int
            or descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
            or type(self.target) is not str
            or _BROWSER_RUNTIME_BUNDLE_MEMFD_TARGET.fullmatch(self.target)
            is None
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle cleanup differs"
            )
        try:
            info = os.fstat(descriptor)
            seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
            target = os.readlink(f"/proc/self/fd/{descriptor}")
        except (OSError, OverflowError, ValueError, TypeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle cleanup differs"
            ) from None
        if (
            (info.st_dev, info.st_ino) != (self.device, self.inode)
            or info.st_size != self.size
            or seals != _BROWSER_RUNTIME_BUNDLE_SEALS
            or target != self.target
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle cleanup differs"
            )
        os.close(descriptor)
        self.descriptor = -1
        self._owner_token = None
        self._source_token = None

    def __enter__(self) -> _PinnedBrowserRuntimeBundle:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __copy__(self) -> _PinnedBrowserRuntimeBundle:
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle ownership differs"
        )

    def __deepcopy__(
        self,
        _: dict[int, object],
    ) -> _PinnedBrowserRuntimeBundle:
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle ownership differs"
        )


@dataclass(frozen=True)
class _BrowserProjectionPolicyEntry:
    path: str
    kind: str
    mode: int
    content: bytes


@dataclass(frozen=True)
class _BrowserProjectionObservation:
    protocol: str
    generation: str
    source_bundle_sha256: str
    source_manifest_sha256: str
    policy_sha256: str
    projection_sha256: str
    entries: int
    regular_bytes: int
    mount_id: int
    mount_read_only: bool
    mount_nosuid: bool
    mount_nodev: bool
    environment: tuple[tuple[str, str], ...]
    execution_authority: bool


@dataclass(frozen=True)
class _BrowserContainmentBlockerObservation:
    protocol: str
    generation: str
    clone3_errno: int
    guardian_pid: int
    guardian_start_ticks: int
    cgroup_path: str
    projection_status: str
    cgroup_status: str
    credential_release_status: str
    projection_sha256: str
    projection_entries: int
    proc_stage: str
    proc_errno: int
    sentinel_provider_calls: int
    payload_processes: int
    cleanup_complete: bool
    execution_authority: bool


@dataclass
class _BrowserGuardianLease:
    process_id: int
    process_start_ticks: int
    pidfd: int
    pidfd_device: int
    pidfd_inode: int
    gate_write: int
    gate_device: int
    gate_inode: int
    status_read: int
    status_device: int
    status_inode: int
    _owner_token: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __copy__(self) -> _BrowserGuardianLease:
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite ownership differs"
        )

    def __deepcopy__(
        self,
        _: dict[int, object],
    ) -> _BrowserGuardianLease:
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite ownership differs"
        )


_BROWSER_PROJECTION_POLICY_ENTRIES = (
    _BrowserProjectionPolicyEntry("/", "D", 0o555, b""),
    _BrowserProjectionPolicyEntry("/.oldroot", "D", 0o555, b""),
    _BrowserProjectionPolicyEntry("/dev", "D", 0o555, b""),
    _BrowserProjectionPolicyEntry("/etc", "D", 0o555, b""),
    _BrowserProjectionPolicyEntry("/etc/gai.conf", "F", 0o444, b""),
    _BrowserProjectionPolicyEntry("/etc/group", "F", 0o444, b"root:x:0:\n"),
    _BrowserProjectionPolicyEntry("/etc/host.conf", "F", 0o444, b""),
    _BrowserProjectionPolicyEntry(
        "/etc/hosts",
        "F",
        0o444,
        b"127.0.0.1 localhost staging.example.test\n::1 localhost\n",
    ),
    _BrowserProjectionPolicyEntry(
        "/etc/nsswitch.conf",
        "F",
        0o444,
        b"passwd: files\ngroup: files\nhosts: files\nnetworks: files\n",
    ),
    _BrowserProjectionPolicyEntry(
        "/etc/passwd",
        "F",
        0o444,
        b"root:x:0:0:Buffalo contained browser:/runtime:/sbin/nologin\n",
    ),
    _BrowserProjectionPolicyEntry("/etc/resolv.conf", "F", 0o444, b""),
    _BrowserProjectionPolicyEntry("/proc", "D", 0o555, b""),
    _BrowserProjectionPolicyEntry("/tmp", "D", 0o1777, b""),
)
_BROWSER_PROJECTION_ENVIRONMENT = (
    (
        "GCONV_PATH",
        "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
        "lib/gconv",
    ),
    ("LANG", "C.UTF-8"),
    ("LC_ALL", "C.UTF-8"),
    (
        "LOCPATH",
        "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
        "lib/locale",
    ),
    (
        "OPENSSL_CONF",
        "/nix/store/rfm5m2l26lqkskcvxn5bm5xqh6c8wqr5-openssl-3.6.0/"
        "etc/ssl/openssl.cnf",
    ),
    (
        "SSL_CERT_FILE",
        "/runtime/site-packages/certifi/cacert.pem",
    ),
    ("TZ", "UTC"),
)
_BROWSER_PROJECTION_NEGATIVE_PATHS = (
    "/etc/ld-nix.so.preload",
    "/etc/ld.so.preload",
    "/home",
    "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
    "etc/ld.so.cache",
    str(_BROWSER_RUNTIME_GCONV_CACHE),
    str(_BROWSER_RUNTIME_LOCALE_ARCHIVE),
    str(_BROWSER_RUNTIME_STDLIB_ZIP),
    "/nix/store/rfm5m2l26lqkskcvxn5bm5xqh6c8wqr5-openssl-3.6.0/"
    "lib/engines-3",
    "/nix/store/rfm5m2l26lqkskcvxn5bm5xqh6c8wqr5-openssl-3.6.0/"
    "lib/ossl-modules",
    "/root",
    "/runtime/.pythonlibs",
    "/runtime/site-packages/_virtualenv.pth",
    "/runtime/site-packages/_virtualenv.py",
    "/runtime/site-packages/buffalo-procurement-os.pth",
    "/runtime/site-packages/sitecustomize.py",
    "/runtime/site-packages/usercustomize.py",
)
_BROWSER_PROJECTION_FORBIDDEN_ENVIRONMENT = re.compile(
    r"\A(?:LD_.*|GLIBC_TUNABLES|LOCALE_ARCHIVE|NIX_PATH|PYTHONHOME|"
    r"PYTHONPATH|PYTHONUSERBASE|OPENSSL_ENGINES|OPENSSL_MODULES|"
    r"REPLIT_LD_.*|REPLIT_NIX_.*)\Z"
)


@dataclass(frozen=True)
class _BrowserDependencyBuildSource:
    path: Path
    descriptor: int
    device: int
    inode: int
    mode: int
    user_id: int
    group_id: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int
    sha256: str


_BROWSER_RUNTIME_STARTUP_FILES = (
    _BrowserRuntimeFileExpectation(
        path=_BROWSER_PYTHON_EXECUTABLE,
        kind="F",
        size=15_776,
        mode=0o555,
        payload=(
            "bd5afcc703e9293ebea22ec05ad3a95f5b14ca6b65293a5f2969efe83148f565"
        ),
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
            "lib/ld-linux-x86-64.so.2"
        ),
        kind="F",
        size=253_696,
        mode=0o555,
        payload=(
            "1e08370bba3ee9e4f97bb0500d1f32afb0417babca6ae455c6458b9a3edb86a8"
        ),
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/qzc04a3npl70cyyy6flnnrb2ig3kayxm-python3-3.13.11/"
            "lib/libpython3.13.so.1.0"
        ),
        kind="F",
        size=6_918_296,
        mode=0o555,
        payload=(
            "c18948facb3a9ad3f737bd49838dfce75b9a7a0125b11f778b742bd3a2baa78e"
        ),
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
            "lib/libdl.so.2"
        ),
        kind="F",
        size=15_688,
        mode=0o555,
        payload=(
            "0b410a4dc1e19583f1bbd192ff5d09de4208715a04d6d11c28fdbda01e1c6bb0"
        ),
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
            "lib/libm.so.6"
        ),
        kind="F",
        size=1_029_504,
        mode=0o555,
        payload=(
            "2e1c8e9e8d5fbefde85eb1d0750375da636ea65aa918b7fdcc328f07f3e6c01e"
        ),
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
            "lib/libc.so.6"
        ),
        kind="F",
        size=2_413_048,
        mode=0o555,
        payload=(
            "29ed835214dc8bc811e10f384ac5428148418bdc7d93f43e5f3a6880bd6dc903"
        ),
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/xc0ga87wdclrx54qjaryahkkmkmqi9qz-gcc-15.2.0-lib/"
            "lib/libgcc_s.so.1"
        ),
        kind="L",
        size=79,
        mode=0o777,
        payload=(
            "/nix/store/b7kx9bkjsma9wslr1cg0316m5jy450l8-gcc-15.2.0-libgcc/"
            "lib/libgcc_s.so.1"
        ),
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/b7kx9bkjsma9wslr1cg0316m5jy450l8-gcc-15.2.0-libgcc/"
            "lib/libgcc_s.so.1"
        ),
        kind="F",
        size=196_968,
        mode=0o444,
        payload=(
            "528a3ea63aa4c25bf9ba5cd8d14c8bdeab3c5aaea16dc9cee00d50fdd8565a53"
        ),
    ),
)

_BROWSER_RUNTIME_NATIVE_FILES = (
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/0c22zivvf0yspdvr2960rvmjgiwwi5wm-bzip2-1.0.8/"
            "lib/libbz2.so.1"
        ),
        kind="L",
        size=15,
        mode=0o777,
        payload="libbz2.so.1.0.8",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/0c22zivvf0yspdvr2960rvmjgiwwi5wm-bzip2-1.0.8/"
            "lib/libbz2.so.1.0.8"
        ),
        kind="F",
        size=86_664,
        mode=0o555,
        payload="bd157c9fb07c6e6ee99a63002e3ccd25fbc0df4f353ae00b9bfe40a5f6c96b16",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/1z031fwbsc8jhmm7i39r6z6m1xn7rbza-libffi-3.5.2/"
            "lib/libffi.so.8"
        ),
        kind="L",
        size=15,
        mode=0o777,
        payload="libffi.so.8.2.0",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/1z031fwbsc8jhmm7i39r6z6m1xn7rbza-libffi-3.5.2/"
            "lib/libffi.so.8.2.0"
        ),
        kind="F",
        size=71_536,
        mode=0o555,
        payload="1a11928dabba924f5a360b8e3763831ff8a0e584e05922efe7a42c951865a1a6",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/b32rwnzms52mwv98hkbadpl1mamzpfvx-xz-5.8.1/"
            "lib/liblzma.so.5"
        ),
        kind="L",
        size=16,
        mode=0o777,
        payload="liblzma.so.5.8.1",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/b32rwnzms52mwv98hkbadpl1mamzpfvx-xz-5.8.1/"
            "lib/liblzma.so.5.8.1"
        ),
        kind="F",
        size=223_824,
        mode=0o555,
        payload="efd53f325a0e0fea9b7a22cf60d73334a9f8d97872a7c5a3fc280936cbe96eb7",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/c2qsgf2832zi4n29gfkqgkjpvmbmxam6-zlib-1.3.1/"
            "lib/libz.so.1"
        ),
        kind="L",
        size=13,
        mode=0o777,
        payload="libz.so.1.3.1",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/c2qsgf2832zi4n29gfkqgkjpvmbmxam6-zlib-1.3.1/"
            "lib/libz.so.1.3.1"
        ),
        kind="F",
        size=128_576,
        mode=0o555,
        payload="9dafd654792106dc7b496ef621b1f64e27ab9925793301355b235884756bb715",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/ddranxbikfg4jkf3n1m0a0h9qqxj1vf0-ncurses-6.5/"
            "lib/libncursesw.so.6"
        ),
        kind="L",
        size=18,
        mode=0o777,
        payload="libncursesw.so.6.5",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/ddranxbikfg4jkf3n1m0a0h9qqxj1vf0-ncurses-6.5/"
            "lib/libncursesw.so.6.5"
        ),
        kind="F",
        size=542_656,
        mode=0o555,
        payload="be8af6c64a4cfcce88a4ac09cd0afc1f601fd41364aea8c109cb7758be5679cf",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/ddranxbikfg4jkf3n1m0a0h9qqxj1vf0-ncurses-6.5/"
            "lib/libpanelw.so.6"
        ),
        kind="L",
        size=16,
        mode=0o777,
        payload="libpanelw.so.6.5",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/ddranxbikfg4jkf3n1m0a0h9qqxj1vf0-ncurses-6.5/"
            "lib/libpanelw.so.6.5"
        ),
        kind="F",
        size=25_904,
        mode=0o555,
        payload="8b8c409d7864909674777ac228eda120891f13f8f991d08765b154c164c11e86",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/f6zwd0xdld51287as0sv79kbaf2pcayh-sqlite-3.51.1/"
            "lib/libsqlite3.so"
        ),
        kind="L",
        size=20,
        mode=0o777,
        payload="libsqlite3.so.3.51.1",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/f6zwd0xdld51287as0sv79kbaf2pcayh-sqlite-3.51.1/"
            "lib/libsqlite3.so.3.51.1"
        ),
        kind="F",
        size=1_804_080,
        mode=0o555,
        payload="52ff381e54f66524dbf7921fa34cdde78efa606ba64095e436f32b21d3e4dc93",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/rh4shf5y1assm86qggc8p10ffk4svfg7-gdbm-1.26-lib/"
            "lib/libgdbm.so.6"
        ),
        kind="L",
        size=16,
        mode=0o777,
        payload="libgdbm.so.6.0.0",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/rh4shf5y1assm86qggc8p10ffk4svfg7-gdbm-1.26-lib/"
            "lib/libgdbm.so.6.0.0"
        ),
        kind="F",
        size=90_272,
        mode=0o555,
        payload="3e148aee701e46c9b42d901efdfa82e661a33c8baee232d9e5729003220744a5",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/rh4shf5y1assm86qggc8p10ffk4svfg7-gdbm-1.26-lib/"
            "lib/libgdbm_compat.so.4"
        ),
        kind="L",
        size=23,
        mode=0o777,
        payload="libgdbm_compat.so.4.0.0",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/rh4shf5y1assm86qggc8p10ffk4svfg7-gdbm-1.26-lib/"
            "lib/libgdbm_compat.so.4.0.0"
        ),
        kind="F",
        size=20_976,
        mode=0o555,
        payload="21a1242f8fd81b9d494849837c71b674b4de7460738d61ff05dc09b481ba81d6",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/sr4cnxyzx24ylxygfk7d81hy4791l8gm-expat-2.7.3/"
            "lib/libexpat.so.1"
        ),
        kind="L",
        size=18,
        mode=0o777,
        payload="libexpat.so.1.11.1",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/sr4cnxyzx24ylxygfk7d81hy4791l8gm-expat-2.7.3/"
            "lib/libexpat.so.1.11.1"
        ),
        kind="F",
        size=208_288,
        mode=0o555,
        payload="c8661e70f2bd506cc47ec446feea56151173be2a3bf9eef92f18018e8023d312",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/vksayvpb9qm9h32k0qqg67nclrq8sf79-readline-8.3p1/"
            "lib/libreadline.so.8"
        ),
        kind="L",
        size=18,
        mode=0o777,
        payload="libreadline.so.8.3",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/vksayvpb9qm9h32k0qqg67nclrq8sf79-readline-8.3p1/"
            "lib/libreadline.so.8.3"
        ),
        kind="F",
        size=441_776,
        mode=0o555,
        payload="1ef7621ebcf3c9a6a917cf9641652eac1d76faf7f7de7a55d6d728924d1d1eda",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/vl3j31vls196i0ay8xw37h4prjmdyqgc-mpdecimal-4.0.1/"
            "lib/libmpdec.so.4"
        ),
        kind="L",
        size=17,
        mode=0o777,
        payload="libmpdec.so.4.0.1",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/vl3j31vls196i0ay8xw37h4prjmdyqgc-mpdecimal-4.0.1/"
            "lib/libmpdec.so.4.0.1"
        ),
        kind="F",
        size=222_600,
        mode=0o555,
        payload="31d2b33ce092df21288cfcd9b33776e31d8cf65e02ea4562fd41eee4a9461955",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/xbydf9b2lx6jziwi6z85g0bny5331dim-util-linux-minimal-"
            "2.41.2-lib/lib/libuuid.so.1"
        ),
        kind="L",
        size=16,
        mode=0o777,
        payload="libuuid.so.1.3.0",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/xbydf9b2lx6jziwi6z85g0bny5331dim-util-linux-minimal-"
            "2.41.2-lib/lib/libuuid.so.1.3.0"
        ),
        kind="F",
        size=40_672,
        mode=0o555,
        payload="b88af218b0bf10d58995c20156fbe703bbadddc8b821facc2da3daf4d8f24926",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
            "lib/libpthread.so.0"
        ),
        kind="F",
        size=16_536,
        mode=0o555,
        payload="a4150ff6ddfe86384f38fc78d181fb1c0c77b413a172d4fff10176b4084dec43",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
            "lib/libresolv.so.2"
        ),
        kind="F",
        size=77_152,
        mode=0o555,
        payload="2a52e8de1bd98665a08f66bba19269949003a4b020338836129afd023047dc59",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
            "lib/librt.so.1"
        ),
        kind="F",
        size=16_352,
        mode=0o555,
        payload="573b665c223e81106e9ff7e07335e425b0e6ca5d24e733e0721f60d878576a0c",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/rfm5m2l26lqkskcvxn5bm5xqh6c8wqr5-openssl-3.6.0/"
            "lib/libcrypto.so.3"
        ),
        kind="F",
        size=7_606_376,
        mode=0o555,
        payload="9c298d15748740096a2cc79d9fae4edd66c368a9c054c4eeeeb823160b71c0fc",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/rfm5m2l26lqkskcvxn5bm5xqh6c8wqr5-openssl-3.6.0/"
            "lib/libssl.so.3"
        ),
        kind="F",
        size=1_324_464,
        mode=0o555,
        payload="27b3b1454ba2d358dc4a74ab3cbbba81b045ede81e215c3dac92e45d89bfff2a",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/j193mfi0f921y0kfs8vjc1znnr45ispv-glibc-2.40-66/"
            "share/locale/locale.alias"
        ),
        kind="F",
        size=2_998,
        mode=0o444,
        payload="e55e2a18d3e320e27dda8672a394e30de0dbf901a051fd369e06a0d66b234752",
    ),
    _BrowserRuntimeFileExpectation(
        path=Path(
            "/nix/store/rfm5m2l26lqkskcvxn5bm5xqh6c8wqr5-openssl-3.6.0/"
            "etc/ssl/openssl.cnf"
        ),
        kind="F",
        size=12_411,
        mode=0o444,
        payload="a65a2cb9f4ee8ffdc7ef4f0ac600c0bdafb95b7b1ab457188ac610a62f5ad6b3",
    ),
)


@dataclass
class PinnedBrowserPythonExecutable:
    """Owned descriptor for the pinned CPython ELF, not its runtime closure."""

    descriptor: int
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int
    _owner_token: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor == -1 and self._owner_token is None:
            return
        if (
            self._owner_token is not _BROWSER_HANDLE_TOKEN
            or type(descriptor) is not int
            or descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python cleanup differs"
            )
        try:
            info = os.fstat(descriptor)
        except (OSError, OverflowError, ValueError, TypeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python cleanup differs"
            ) from None
        if (info.st_dev, info.st_ino) != (self.device, self.inode):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python cleanup differs"
            )
        self.descriptor = -1
        self._owner_token = None
        os.close(descriptor)

    def __enter__(self) -> PinnedBrowserPythonExecutable:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __copy__(self) -> PinnedBrowserPythonExecutable:
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python ownership differs"
        )

    def __deepcopy__(
        self,
        _: dict[int, object],
    ) -> PinnedBrowserPythonExecutable:
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python ownership differs"
        )


@dataclass
class PinnedBrowserWorkerRunner:
    """Owned sealed worker bytes; not a complete executable runtime."""

    descriptor: int
    device: int
    inode: int
    size: int
    sha256: str
    _owner_token: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor == -1 and self._owner_token is None:
            return
        if (
            self._owner_token is not _BROWSER_HANDLE_TOKEN
            or type(descriptor) is not int
            or descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser worker runner cleanup differs"
            )
        try:
            info = os.fstat(descriptor)
            seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
            target = os.readlink(f"/proc/self/fd/{descriptor}")
        except (OSError, OverflowError, ValueError, TypeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser worker runner cleanup differs"
            ) from None
        if (
            (info.st_dev, info.st_ino) != (self.device, self.inode)
            or seals != _BROWSER_WORKER_RUNNER_SEALS
            or target != _BROWSER_WORKER_RUNNER_MEMFD_TARGET
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser worker runner cleanup differs"
            )
        self.descriptor = -1
        self._owner_token = None
        os.close(descriptor)

    def __enter__(self) -> PinnedBrowserWorkerRunner:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __copy__(self) -> PinnedBrowserWorkerRunner:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner ownership differs"
        )

    def __deepcopy__(
        self,
        _: dict[int, object],
    ) -> PinnedBrowserWorkerRunner:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner ownership differs"
        )


@dataclass(frozen=True)
class BrowserWorkerLaunch:
    """Exact inert launch specification; it confers no execution authority."""

    command_line: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    cwd: Path
    pass_fds: tuple[int, ...]
    arguments: BrowserWorkerArguments


@dataclass
class ObservedBrowserWorker:
    """Owned pidfd for one exact preflight snapshot, not execution authority."""

    pidfd: int
    pidfd_device: int
    pidfd_inode: int
    process: BrowserWorkerProcessStat
    _owner_token: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def close(self) -> None:
        descriptor = self.pidfd
        if descriptor == -1 and self._owner_token is None:
            return
        if (
            self._owner_token is not _BROWSER_HANDLE_TOKEN
            or type(descriptor) is not int
            or descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process cleanup differs"
            )
        try:
            info, process_id, _, _ = _read_browser_pidfd_metadata(descriptor)
            if (
                (info.st_dev, info.st_ino)
                != (self.pidfd_device, self.pidfd_inode)
                or process_id not in {None, self.process.pid}
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance browser process differs"
                )
        except (
            LocalStagingAcceptanceError,
            OSError,
            OverflowError,
            ValueError,
            TypeError,
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process cleanup differs"
            ) from None
        self.pidfd = -1
        self._owner_token = None
        os.close(descriptor)

    def __enter__(self) -> ObservedBrowserWorker:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __copy__(self) -> ObservedBrowserWorker:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process ownership differs"
        )

    def __deepcopy__(
        self,
        _: dict[int, object],
    ) -> ObservedBrowserWorker:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process ownership differs"
        )


@dataclass(frozen=True)
class BrowserWorkerDescriptorExpectation:
    number: int
    target: str
    device: int
    inode: int
    mount_id: int
    position: int
    status_flags: int
    close_on_exec: bool


@dataclass(frozen=True)
class BrowserWorkerProcessExpectation:
    command_line: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    cwd: Path
    cwd_device: int
    cwd_inode: int
    descriptors: tuple[BrowserWorkerDescriptorExpectation, ...]
    user_id: int
    group_id: int
    supplementary_groups: tuple[int, ...]
    no_new_privileges: int
    seccomp_mode: int


@dataclass
class TrustedDockerClient:
    descriptor: int
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int

    def close(self) -> None:
        descriptor = self.descriptor
        self.descriptor = -1
        if descriptor >= 0:
            os.close(descriptor)

    def __enter__(self) -> TrustedDockerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def _sha256_descriptor(descriptor: int, *, expected_bytes: int) -> str:
    digest = hashlib.sha256()
    observed = 0
    while observed <= expected_bytes:
        block = os.pread(
            descriptor,
            min(_CHUNK_BYTES, expected_bytes + 1 - observed),
            observed,
        )
        if not block:
            break
        observed += len(block)
        digest.update(block)
    if observed != expected_bytes:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker client differs"
        )
    return digest.hexdigest()


def _validate_trusted_docker(client: TrustedDockerClient) -> None:
    try:
        descriptor_info = os.fstat(client.descriptor)
        named_info = client.path.stat(follow_symlinks=False)
    except (OSError, ValueError) as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker client is unavailable"
        ) from exc
    descriptor_identity = (
        descriptor_info.st_dev,
        descriptor_info.st_ino,
        descriptor_info.st_size,
        descriptor_info.st_mtime_ns,
    )
    if (
        client.path != _DOCKER_EXECUTABLE
        or client.path.is_symlink()
        or not stat.S_ISREG(descriptor_info.st_mode)
        or descriptor_info.st_nlink != 1
        or (descriptor_info.st_uid, descriptor_info.st_gid)
        != (_DOCKER_UID, _DOCKER_GID)
        or stat.S_IMODE(descriptor_info.st_mode) != _DOCKER_MODE
        or descriptor_info.st_size != _DOCKER_BYTES
        or descriptor_identity
        != (client.device, client.inode, client.size, client.mtime_ns)
        or (named_info.st_dev, named_info.st_ino)[:]
        != (client.device, client.inode)
        or _sha256_descriptor(
            client.descriptor,
            expected_bytes=_DOCKER_BYTES,
        )
        != _DOCKER_SHA256
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker client differs"
        )


def _validate_docker_socket() -> None:
    try:
        socket_info = Path(_DOCKER_SOCKET).stat(follow_symlinks=False)
    except OSError as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker daemon is unavailable"
        ) from exc
    if (
        not stat.S_ISSOCK(socket_info.st_mode)
        or (socket_info.st_uid, socket_info.st_gid) != (0, 1000)
        or stat.S_IMODE(socket_info.st_mode) != 0o660
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker daemon differs"
        )


def open_trusted_docker() -> TrustedDockerClient:
    """Open and bind the exact local Docker client before any private path exists."""

    descriptor = -1
    try:
        descriptor = os.open(
            _DOCKER_EXECUTABLE,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        info = os.fstat(descriptor)
        client = TrustedDockerClient(
            descriptor=descriptor,
            path=_DOCKER_EXECUTABLE,
            device=info.st_dev,
            inode=info.st_ino,
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
        )
        _validate_trusted_docker(client)
        return client
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _browser_python_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    observed = 0
    try:
        while observed <= _BROWSER_PYTHON_BYTES:
            block = os.pread(
                descriptor,
                min(
                    _CHUNK_BYTES,
                    _BROWSER_PYTHON_BYTES + 1 - observed,
                ),
                observed,
            )
            if not block:
                break
            observed += len(block)
            digest.update(block)
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python differs"
        ) from None
    if observed != _BROWSER_PYTHON_BYTES:
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python differs"
        )
    return digest.hexdigest()


def _validate_pinned_browser_python_executable(runtime: PinnedBrowserPythonExecutable) -> None:
    if (
        type(runtime) is not PinnedBrowserPythonExecutable
        or runtime._owner_token is not _BROWSER_HANDLE_TOKEN
        or type(runtime.descriptor) is not int
        or runtime.descriptor <= 2
        or runtime.descriptor > _BROWSER_WORKER_MAX_FD
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python differs"
        )
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        descriptor_info = os.fstat(runtime.descriptor)
        named_info = runtime.path.stat(follow_symlinks=False)
        descriptor_target = os.readlink(
            f"/proc/self/fd/{runtime.descriptor}"
        )
        descriptor_flags = fcntl.fcntl(runtime.descriptor, fcntl.F_GETFD)
        status_flags = fcntl.fcntl(runtime.descriptor, fcntl.F_GETFL)
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python differs"
        ) from None
    descriptor_identity = (
        descriptor_info.st_dev,
        descriptor_info.st_ino,
        descriptor_info.st_size,
        descriptor_info.st_mtime_ns,
    )
    named_identity = (
        named_info.st_dev,
        named_info.st_ino,
        named_info.st_size,
        named_info.st_mtime_ns,
    )
    if (
        runtime.path != _BROWSER_PYTHON_EXECUTABLE
        or (
            soft_limit != resource.RLIM_INFINITY
            and runtime.descriptor >= soft_limit
        )
        or runtime.path.is_symlink()
        or descriptor_target != str(_BROWSER_PYTHON_EXECUTABLE)
        or descriptor_flags & fcntl.FD_CLOEXEC == 0
        or os.get_inheritable(runtime.descriptor)
        or status_flags & os.O_ACCMODE != os.O_RDONLY
        or status_flags & os.O_NONBLOCK == 0
        or not stat.S_ISREG(descriptor_info.st_mode)
        or descriptor_info.st_nlink != 1
        or (descriptor_info.st_uid, descriptor_info.st_gid)
        != (_BROWSER_PYTHON_UID, _BROWSER_PYTHON_GID)
        or stat.S_IMODE(descriptor_info.st_mode) != _BROWSER_PYTHON_MODE
        or descriptor_info.st_size != _BROWSER_PYTHON_BYTES
        or descriptor_identity
        != (
            runtime.device,
            runtime.inode,
            runtime.size,
            runtime.mtime_ns,
        )
        or named_identity != descriptor_identity
        or _browser_python_sha256(runtime.descriptor)
        != _BROWSER_PYTHON_SHA256
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python differs"
        )


def open_pinned_browser_python_executable() -> PinnedBrowserPythonExecutable:
    """Pin the direct CPython ELF; runtime-closure attestation remains later."""

    descriptor = -1
    try:
        descriptor = os.open(
            _BROWSER_PYTHON_EXECUTABLE,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if (
            descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
            or (
                soft_limit != resource.RLIM_INFINITY
                and descriptor >= soft_limit
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python differs"
            )
        info = os.fstat(descriptor)
        runtime = PinnedBrowserPythonExecutable(
            descriptor=descriptor,
            path=_BROWSER_PYTHON_EXECUTABLE,
            device=info.st_dev,
            inode=info.st_ino,
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
        )
        runtime._owner_token = _BROWSER_HANDLE_TOKEN
        _validate_pinned_browser_python_executable(runtime)
        return runtime
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _browser_runtime_identity(info: os.stat_result) -> tuple[int, ...]:
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


def _update_browser_runtime_manifest(
    digest: Any,
    *,
    kind: bytes,
    path: bytes,
    mode: int,
    size: int,
    payload: bytes,
) -> None:
    if (
        type(kind) is not bytes
        or kind not in {b"F", b"D", b"L"}
        or type(path) is not bytes
        or not path
        or len(path) > _BROWSER_RUNTIME_PATH_BYTES_LIMIT
        or b"\0" in path
        or type(mode) is not int
        or mode < 0
        or mode > 0o7777
        or type(size) is not int
        or size < 0
        or size > 18_446_744_073_709_551_615
        or type(payload) is not bytes
        or len(payload) > _BROWSER_RUNTIME_PATH_BYTES_LIMIT
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    digest.update(kind)
    digest.update(len(path).to_bytes(4, "big"))
    digest.update(path)
    digest.update(mode.to_bytes(4, "big"))
    digest.update(size.to_bytes(8, "big"))
    digest.update(len(payload).to_bytes(4, "big"))
    digest.update(payload)


def _observe_exact_browser_runtime_file(
    expectation: _BrowserRuntimeFileExpectation,
) -> tuple[bytes, bytes, int, int, bytes, bytes, bool]:
    if (
        type(expectation) is not _BrowserRuntimeFileExpectation
        or not isinstance(expectation.path, Path)
        or not expectation.path.is_absolute()
        or type(expectation.kind) is not str
        or expectation.kind not in {"F", "L"}
        or type(expectation.size) is not int
        or expectation.size < 0
        or expectation.size > _BROWSER_RUNTIME_STARTUP_FILE_BYTES_LIMIT
        or type(expectation.mode) is not int
        or expectation.mode < 0
        or expectation.mode > 0o7777
        or type(expectation.payload) is not str
        or type(expectation.user_id) is not int
        or expectation.user_id < 0
        or expectation.user_id > 4_294_967_295
        or type(expectation.group_id) is not int
        or expectation.group_id < 0
        or expectation.group_id > 4_294_967_295
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    path_bytes = os.fsencode(expectation.path)
    if (
        not path_bytes.startswith(b"/")
        or len(path_bytes) > _BROWSER_RUNTIME_PATH_BYTES_LIMIT
        or b"\0" in path_bytes
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    if expectation.kind == "L":
        parent_descriptor = -1
        try:
            if expectation.path.name in {"", ".", ".."}:
                raise OSError
            named_parent_before = expectation.path.parent.lstat()
            named_link_before = expectation.path.lstat()
            named_target_before = os.fsencode(os.readlink(expectation.path))
            parent_descriptor = os.open(
                expectation.path.parent,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY,
            )
            if (
                parent_descriptor <= 2
                or parent_descriptor > _BROWSER_WORKER_MAX_FD
            ):
                raise OSError
            parent_before = os.fstat(parent_descriptor)
            before = os.stat(
                expectation.path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            target_before = os.fsencode(
                os.readlink(expectation.path.name, dir_fd=parent_descriptor)
            )
            after = os.stat(
                expectation.path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            target_after = os.fsencode(
                os.readlink(expectation.path.name, dir_fd=parent_descriptor)
            )
            parent_after = os.fstat(parent_descriptor)
            read_only = bool(
                os.fstatvfs(parent_descriptor).f_flag & os.ST_RDONLY
            )
            named_link_after = expectation.path.lstat()
            named_target_after = os.fsencode(os.readlink(expectation.path))
            named_parent_after = expectation.path.parent.lstat()
            expected_target = expectation.payload.encode("ascii", errors="strict")
        except (OSError, OverflowError, ValueError, TypeError, UnicodeEncodeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python runtime source differs"
            ) from None
        finally:
            if parent_descriptor >= 0:
                os.close(parent_descriptor)
        if (
            not stat.S_ISDIR(parent_before.st_mode)
            or _browser_runtime_identity(parent_before)
            != _browser_runtime_identity(named_parent_before)
            or _browser_runtime_identity(parent_before)
            != _browser_runtime_identity(parent_after)
            or _browser_runtime_identity(parent_before)
            != _browser_runtime_identity(named_parent_after)
            or not stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or (before.st_uid, before.st_gid)
            != (expectation.user_id, expectation.group_id)
            or stat.S_IMODE(before.st_mode) != expectation.mode
            or before.st_size != expectation.size
            or _browser_runtime_identity(before)
            != _browser_runtime_identity(after)
            or _browser_runtime_identity(before)
            != _browser_runtime_identity(named_link_before)
            or _browser_runtime_identity(before)
            != _browser_runtime_identity(named_link_after)
            or target_before != target_after
            or target_before != named_target_before
            or target_before != named_target_after
            or target_before != expected_target
            or len(target_before) != expectation.size
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python runtime source differs"
            )
        return (
            b"L",
            path_bytes,
            expectation.mode,
            expectation.size,
            target_before,
            target_before,
            read_only,
        )

    if _SHA256_TEXT.fullmatch(expectation.payload) is None:
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    descriptor = -1
    try:
        named_before = expectation.path.lstat()
        descriptor = os.open(
            expectation.path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        if descriptor <= 2 or descriptor > _BROWSER_WORKER_MAX_FD:
            raise OSError
        before = os.fstat(descriptor)
        descriptor_target = os.readlink(f"/proc/self/fd/{descriptor}")
        digest = hashlib.sha256()
        content = bytearray()
        observed = 0
        while observed <= expectation.size:
            block = os.read(
                descriptor,
                min(1024 * 1024, expectation.size + 1 - observed),
            )
            if not block:
                break
            observed += len(block)
            digest.update(block)
            content.extend(block)
        after = os.fstat(descriptor)
        named_after = expectation.path.lstat()
        read_only = bool(os.fstatvfs(descriptor).f_flag & os.ST_RDONLY)
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        descriptor_target != str(expectation.path)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or (before.st_uid, before.st_gid)
        != (expectation.user_id, expectation.group_id)
        or stat.S_IMODE(before.st_mode) != expectation.mode
        or before.st_size != expectation.size
        or observed != expectation.size
        or _browser_runtime_identity(before) != _browser_runtime_identity(after)
        or _browser_runtime_identity(before)
        != _browser_runtime_identity(named_before)
        or _browser_runtime_identity(before)
        != _browser_runtime_identity(named_after)
        or digest.hexdigest() != expectation.payload
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    return (
        b"F",
        path_bytes,
        expectation.mode,
        expectation.size,
        digest.digest(),
        bytes(content),
        read_only,
    )


def _observe_browser_runtime_startup_files(
    expectations: tuple[_BrowserRuntimeFileExpectation, ...],
    *,
    expected_sha256: str,
    retained_entries: list[_BrowserRuntimeBundleEntry] | None = None,
) -> _BrowserRuntimeStartupObservation:
    if (
        type(expectations) is not tuple
        or not expectations
        or len(expectations) > len(_BROWSER_RUNTIME_STARTUP_FILES)
        or type(expected_sha256) is not str
        or _SHA256_TEXT.fullmatch(expected_sha256) is None
        or (
            retained_entries is not None
            and type(retained_entries) is not list
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    records = tuple(
        _observe_exact_browser_runtime_file(expectation)
        for expectation in expectations
    )
    paths = tuple(record[1] for record in records)
    if len(set(paths)) != len(paths):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    digest = hashlib.sha256(_BROWSER_RUNTIME_STARTUP_DOMAIN)
    for kind, path, mode, size, payload, _, _ in sorted(
        records,
        key=lambda item: item[1],
    ):
        _update_browser_runtime_manifest(
            digest,
            kind=kind,
            path=path,
            mode=mode,
            size=size,
            payload=payload,
        )
    observed = digest.hexdigest()
    if observed != expected_sha256:
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    if retained_entries is not None:
        retained_entries.extend(
            _BrowserRuntimeBundleEntry(
                path=os.fsdecode(path),
                kind=kind.decode("ascii"),
                mode=mode,
                user_id=expectation.user_id,
                group_id=expectation.group_id,
                provenance=f"python-startup:{observed}",
                content=content,
            )
            for expectation, (kind, path, mode, _, _, content, _) in sorted(
                zip(expectations, records, strict=True),
                key=lambda item: item[1][1],
            )
        )
    return _BrowserRuntimeStartupObservation(
        sha256=observed,
        all_source_mounts_read_only=all(record[6] for record in records),
    )


def _require_browser_runtime_path_absent(path: Path) -> bool:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path.name in {"", ".", ".."}
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    parent_descriptor = -1
    try:
        named_parent_before = path.parent.lstat()
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python runtime source differs"
            )
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY,
        )
        if parent_descriptor <= 2 or parent_descriptor > _BROWSER_WORKER_MAX_FD:
            raise OSError
        before = os.fstat(parent_descriptor)
        try:
            os.stat(
                path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python runtime source differs"
            )
        read_only = bool(os.fstatvfs(parent_descriptor).f_flag & os.ST_RDONLY)
        after = os.fstat(parent_descriptor)
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python runtime source differs"
            )
        named_parent_after = path.parent.lstat()
    except LocalStagingAcceptanceError:
        raise
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        ) from None
    finally:
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    if (
        not stat.S_ISDIR(before.st_mode)
        or _browser_runtime_identity(before)
        != _browser_runtime_identity(named_parent_before)
        or _browser_runtime_identity(before) != _browser_runtime_identity(after)
        or _browser_runtime_identity(before)
        != _browser_runtime_identity(named_parent_after)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    return read_only


def _observe_browser_stdlib_tree(
    root: Path,
    absent_zip: Path,
    *,
    expected_sha256: str,
    expected_entries: int,
    expected_regular_files: int,
    expected_directories: int,
    expected_symlinks: int,
    expected_regular_bytes: int,
    expected_user_id: int,
    expected_group_id: int,
    expected_root_mode: int,
    manifest_domain: bytes = _BROWSER_RUNTIME_STDLIB_DOMAIN,
    provenance: str | None = None,
    retained_entries: list[_BrowserRuntimeBundleEntry] | None = None,
) -> _BrowserStdlibObservation:
    integer_values = (
        expected_entries,
        expected_regular_files,
        expected_directories,
        expected_symlinks,
        expected_regular_bytes,
        expected_user_id,
        expected_group_id,
        expected_root_mode,
    )
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or not isinstance(absent_zip, Path)
        or not absent_zip.is_absolute()
        or (
            absent_zip.parent != root.parent
            and absent_zip.parent != root
        )
        or type(expected_sha256) is not str
        or _SHA256_TEXT.fullmatch(expected_sha256) is None
        or type(manifest_domain) is not bytes
        or not manifest_domain
        or len(manifest_domain) > 128
        or (provenance is not None and type(provenance) is not str)
        or any(type(value) is not int or value < 0 for value in integer_values)
        or expected_entries <= 0
        or expected_entries > _BROWSER_RUNTIME_STDLIB_ENTRIES
        or expected_regular_files > expected_entries
        or expected_directories > expected_entries
        or expected_symlinks > expected_entries
        or (
            expected_regular_files
            + expected_directories
            + expected_symlinks
            != expected_entries
        )
        or expected_regular_bytes > _BROWSER_RUNTIME_STDLIB_BYTES
        or expected_user_id > 4_294_967_295
        or expected_group_id > 4_294_967_295
        or expected_root_mode > 0o7777
        or (
            retained_entries is not None
            and type(retained_entries) is not list
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    all_source_mounts_read_only = _require_browser_runtime_path_absent(
        absent_zip
    )
    root_descriptor = -1
    records: list[tuple[bytes, bytes, int, int, bytes, bytes]] = []
    regular_files = 0
    directories = 0
    symlinks = 0
    regular_bytes = 0

    def observe_mount(descriptor: int) -> None:
        nonlocal all_source_mounts_read_only
        try:
            read_only = bool(os.fstatvfs(descriptor).f_flag & os.ST_RDONLY)
        except (OSError, OverflowError, ValueError, TypeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python runtime source differs"
            ) from None
        all_source_mounts_read_only = (
            all_source_mounts_read_only and read_only
        )

    def require_identity(
        observed: os.stat_result,
        expected: os.stat_result,
    ) -> None:
        if _browser_runtime_identity(observed) != _browser_runtime_identity(expected):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python runtime source differs"
            )

    def observe_regular(
        parent_descriptor: int,
        name: str,
        named_before: os.stat_result,
    ) -> tuple[int, bytes, bytes]:
        nonlocal regular_bytes
        descriptor = -1
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent_descriptor,
            )
            if descriptor <= 2 or descriptor > _BROWSER_WORKER_MAX_FD:
                raise OSError
            before = os.fstat(descriptor)
            observe_mount(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or (before.st_uid, before.st_gid)
                != (expected_user_id, expected_group_id)
                or before.st_size > expected_regular_bytes - regular_bytes
            ):
                raise OSError
            digest = hashlib.sha256()
            content = bytearray()
            observed = 0
            while observed <= before.st_size:
                block = os.read(
                    descriptor,
                    min(1024 * 1024, before.st_size + 1 - observed),
                )
                if not block:
                    break
                observed += len(block)
                digest.update(block)
                content.extend(block)
            after = os.fstat(descriptor)
            named_after = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except (OSError, OverflowError, ValueError, TypeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python runtime source differs"
            ) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if observed != before.st_size:
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python runtime source differs"
            )
        require_identity(before, named_before)
        require_identity(before, after)
        require_identity(before, named_after)
        regular_bytes += observed
        return observed, digest.digest(), bytes(content)

    def walk(
        directory_descriptor: int,
        prefix: bytes,
        expected_directory: os.stat_result,
        depth: int,
    ) -> None:
        nonlocal regular_files, directories, symlinks
        if depth > _BROWSER_RUNTIME_TREE_DEPTH_LIMIT:
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python runtime source differs"
            )
        before = os.fstat(directory_descriptor)
        observe_mount(directory_descriptor)
        require_identity(before, expected_directory)
        if (
            not stat.S_ISDIR(before.st_mode)
            or (before.st_uid, before.st_gid)
            != (expected_user_id, expected_group_id)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python runtime source differs"
            )
        entries: list[tuple[bytes, str]] = []
        try:
            with os.scandir(directory_descriptor) as iterator:
                for entry in iterator:
                    raw_name = os.fsencode(entry.name)
                    if (
                        not raw_name
                        or raw_name in {b".", b".."}
                        or b"/" in raw_name
                        or b"\0" in raw_name
                        or len(entries) >= expected_entries
                    ):
                        raise LocalStagingAcceptanceError(
                            "local acceptance browser Python runtime source differs"
                        )
                    entries.append((raw_name, entry.name))
        except OSError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser Python runtime source differs"
            ) from None
        for raw_name, name in sorted(entries):
            relative = raw_name if not prefix else prefix + b"/" + raw_name
            if (
                len(relative) > _BROWSER_RUNTIME_PATH_BYTES_LIMIT
                or len(records) >= expected_entries
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance browser Python runtime source differs"
                )
            try:
                named_before = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser Python runtime source differs"
                ) from None
            mode = stat.S_IMODE(named_before.st_mode)
            if stat.S_ISREG(named_before.st_mode):
                size, payload, content = observe_regular(
                    directory_descriptor,
                    name,
                    named_before,
                )
                regular_files += 1
                records.append(
                    (relative, b"F", mode, size, payload, content)
                )
            elif stat.S_ISDIR(named_before.st_mode):
                child_descriptor = -1
                try:
                    child_descriptor = os.open(
                        name,
                        os.O_RDONLY
                        | os.O_CLOEXEC
                        | os.O_NOFOLLOW
                        | os.O_DIRECTORY,
                        dir_fd=directory_descriptor,
                    )
                    if (
                        child_descriptor <= 2
                        or child_descriptor > _BROWSER_WORKER_MAX_FD
                    ):
                        raise OSError
                    directories += 1
                    records.append((relative, b"D", mode, 0, b"", b""))
                    walk(
                        child_descriptor,
                        relative,
                        named_before,
                        depth + 1,
                    )
                    named_after = os.stat(
                        name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                    require_identity(named_after, named_before)
                except OSError:
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser Python runtime source differs"
                    ) from None
                finally:
                    if child_descriptor >= 0:
                        os.close(child_descriptor)
            elif stat.S_ISLNK(named_before.st_mode):
                try:
                    target_before = os.fsencode(
                        os.readlink(name, dir_fd=directory_descriptor)
                    )
                    named_after = os.stat(
                        name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                    target_after = os.fsencode(
                        os.readlink(name, dir_fd=directory_descriptor)
                    )
                except OSError:
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser Python runtime source differs"
                    ) from None
                if (
                    named_before.st_nlink != 1
                    or (named_before.st_uid, named_before.st_gid)
                    != (expected_user_id, expected_group_id)
                    or named_before.st_size != len(target_before)
                    or target_before != target_after
                ):
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser Python runtime source differs"
                    )
                require_identity(named_after, named_before)
                symlinks += 1
                records.append(
                    (
                        relative,
                        b"L",
                        mode,
                        len(target_before),
                        target_before,
                        target_before,
                    )
                )
            else:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser Python runtime source differs"
                )
        after = os.fstat(directory_descriptor)
        require_identity(after, before)

    try:
        named_root_before = root.lstat()
        root_descriptor = os.open(
            root,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY,
        )
        if root_descriptor <= 2 or root_descriptor > _BROWSER_WORKER_MAX_FD:
            raise OSError
        descriptor_root = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(descriptor_root.st_mode)
            or (descriptor_root.st_uid, descriptor_root.st_gid)
            != (expected_user_id, expected_group_id)
            or stat.S_IMODE(descriptor_root.st_mode) != expected_root_mode
        ):
            raise OSError
        walk(root_descriptor, b"", descriptor_root, 0)
        descriptor_root_after = os.fstat(root_descriptor)
        named_root_after = root.lstat()
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        ) from None
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)
    require_identity(descriptor_root, named_root_before)
    require_identity(descriptor_root, descriptor_root_after)
    require_identity(descriptor_root, named_root_after)
    zip_parent_read_only_after = _require_browser_runtime_path_absent(
        absent_zip
    )
    all_source_mounts_read_only = (
        all_source_mounts_read_only and zip_parent_read_only_after
    )
    if (
        len(records) != expected_entries
        or regular_files != expected_regular_files
        or directories != expected_directories
        or symlinks != expected_symlinks
        or regular_bytes != expected_regular_bytes
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    digest = hashlib.sha256(manifest_domain)
    for path, kind, mode, size, payload, _ in sorted(records):
        _update_browser_runtime_manifest(
            digest,
            kind=kind,
            path=path,
            mode=mode,
            size=size,
            payload=payload,
        )
    observed_sha256 = digest.hexdigest()
    if observed_sha256 != expected_sha256:
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    if retained_entries is not None:
        selected_provenance = (
            f"python-stdlib:{observed_sha256}"
            if provenance is None
            else provenance
        )
        retained_entries.append(
            _BrowserRuntimeBundleEntry(
                path=str(root),
                kind="D",
                mode=expected_root_mode,
                user_id=expected_user_id,
                group_id=expected_group_id,
                provenance=selected_provenance,
                content=b"",
            )
        )
        retained_entries.extend(
            _BrowserRuntimeBundleEntry(
                path=str(root / os.fsdecode(path)),
                kind=kind.decode("ascii"),
                mode=mode,
                user_id=expected_user_id,
                group_id=expected_group_id,
                provenance=selected_provenance,
                content=content,
            )
            for path, kind, mode, _, _, content in sorted(records)
        )
    return _BrowserStdlibObservation(
        sha256=observed_sha256,
        entries=len(records),
        regular_files=regular_files,
        directories=directories,
        symlinks=symlinks,
        regular_bytes=regular_bytes,
        all_source_mounts_read_only=all_source_mounts_read_only,
    )


def _browser_native_runtime_sha256(
    entries: tuple[_BrowserRuntimeBundleEntry, ...],
) -> str:
    if (
        type(entries) is not tuple
        or len(entries) != _BROWSER_RUNTIME_NATIVE_ENTRIES
        or any(type(entry) is not _BrowserRuntimeBundleEntry for entry in entries)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser native runtime source differs"
        )
    explicit_paths = {
        str(expectation.path) for expectation in _BROWSER_RUNTIME_NATIVE_FILES
    }
    allowed_roots = (
        str(_BROWSER_RUNTIME_GCONV_ROOT),
        str(_BROWSER_RUNTIME_LOCALE_ROOT),
    )
    digest = hashlib.sha256(_BROWSER_RUNTIME_NATIVE_DOMAIN)
    regular_files = 0
    directories = 0
    symlinks = 0
    regular_bytes = 0
    paths: set[str] = set()
    for entry in sorted(entries, key=lambda item: item.path):
        if (
            type(entry.path) is not str
            or not entry.path.startswith("/")
            or entry.path in paths
            or (
                entry.path not in explicit_paths
                and not any(
                    entry.path == root or entry.path.startswith(f"{root}/")
                    for root in allowed_roots
                )
            )
            or entry.provenance
            != f"native-runtime:{_BROWSER_RUNTIME_NATIVE_SHA256}"
            or (entry.user_id, entry.group_id)
            != (_BROWSER_PYTHON_UID, _BROWSER_PYTHON_GID)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser native runtime source differs"
            )
        paths.add(entry.path)
        if entry.kind == "F":
            regular_files += 1
            regular_bytes += len(entry.content)
            payload = hashlib.sha256(entry.content).digest()
        elif entry.kind == "D":
            directories += 1
            if entry.content:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser native runtime source differs"
                )
            payload = b""
        elif entry.kind == "L":
            symlinks += 1
            payload = entry.content
        else:
            raise LocalStagingAcceptanceError(
                "local acceptance browser native runtime source differs"
            )
        _update_browser_runtime_manifest(
            digest,
            kind=entry.kind.encode("ascii"),
            path=entry.path.encode("ascii"),
            mode=entry.mode,
            size=len(entry.content),
            payload=payload,
        )
    observed = digest.hexdigest()
    if (
        regular_files != _BROWSER_RUNTIME_NATIVE_REGULAR_FILES
        or directories != _BROWSER_RUNTIME_NATIVE_DIRECTORIES
        or symlinks != _BROWSER_RUNTIME_NATIVE_SYMLINKS
        or regular_bytes != _BROWSER_RUNTIME_NATIVE_BYTES
        or observed != _BROWSER_RUNTIME_NATIVE_SHA256
        or str(_BROWSER_RUNTIME_GCONV_CACHE) in paths
        or str(_BROWSER_RUNTIME_LOCALE_ARCHIVE) in paths
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser native runtime source differs"
        )
    for expectation in _BROWSER_RUNTIME_NATIVE_FILES:
        selected = next(
            (entry for entry in entries if entry.path == str(expectation.path)),
            None,
        )
        if (
            selected is None
            or selected.kind != expectation.kind
            or selected.mode != expectation.mode
            or (selected.user_id, selected.group_id)
            != (expectation.user_id, expectation.group_id)
            or len(selected.content) != expectation.size
            or (
                selected.kind == "F"
                and hashlib.sha256(selected.content).hexdigest()
                != expectation.payload
            )
            or (
                selected.kind == "L"
                and selected.content
                != expectation.payload.encode("ascii", errors="strict")
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser native runtime source differs"
            )
    return observed


def _observe_browser_native_runtime_source(
    retained_entries: list[_BrowserRuntimeBundleEntry],
) -> bool:
    if type(retained_entries) is not list:
        raise LocalStagingAcceptanceError(
            "local acceptance browser native runtime source differs"
        )
    provenance = f"native-runtime:{_BROWSER_RUNTIME_NATIVE_SHA256}"
    read_only: list[bool] = []
    for expectation in _BROWSER_RUNTIME_NATIVE_FILES:
        kind, path, mode, _, _, content, source_read_only = (
            _observe_exact_browser_runtime_file(expectation)
        )
        retained_entries.append(
            _BrowserRuntimeBundleEntry(
                path=os.fsdecode(path),
                kind=kind.decode("ascii"),
                mode=mode,
                user_id=expectation.user_id,
                group_id=expectation.group_id,
                provenance=provenance,
                content=content,
            )
        )
        read_only.append(source_read_only)
    gconv = _observe_browser_stdlib_tree(
        _BROWSER_RUNTIME_GCONV_ROOT,
        _BROWSER_RUNTIME_GCONV_CACHE,
        expected_sha256=_BROWSER_RUNTIME_GCONV_SHA256,
        expected_entries=_BROWSER_RUNTIME_GCONV_ENTRIES,
        expected_regular_files=_BROWSER_RUNTIME_GCONV_FILES,
        expected_directories=_BROWSER_RUNTIME_GCONV_DIRECTORIES,
        expected_symlinks=0,
        expected_regular_bytes=_BROWSER_RUNTIME_GCONV_BYTES,
        expected_user_id=_BROWSER_PYTHON_UID,
        expected_group_id=_BROWSER_PYTHON_GID,
        expected_root_mode=0o555,
        manifest_domain=_BROWSER_RUNTIME_GCONV_DOMAIN,
        provenance=provenance,
        retained_entries=retained_entries,
    )
    locale = _observe_browser_stdlib_tree(
        _BROWSER_RUNTIME_LOCALE_ROOT,
        _BROWSER_RUNTIME_LOCALE_ARCHIVE,
        expected_sha256=_BROWSER_RUNTIME_LOCALE_SHA256,
        expected_entries=_BROWSER_RUNTIME_LOCALE_ENTRIES,
        expected_regular_files=_BROWSER_RUNTIME_LOCALE_FILES,
        expected_directories=_BROWSER_RUNTIME_LOCALE_DIRECTORIES,
        expected_symlinks=0,
        expected_regular_bytes=_BROWSER_RUNTIME_LOCALE_BYTES,
        expected_user_id=_BROWSER_PYTHON_UID,
        expected_group_id=_BROWSER_PYTHON_GID,
        expected_root_mode=0o555,
        manifest_domain=_BROWSER_RUNTIME_LOCALE_DOMAIN,
        provenance=provenance,
        retained_entries=retained_entries,
    )
    native_entries = tuple(
        entry for entry in retained_entries if entry.provenance == provenance
    )
    _browser_native_runtime_sha256(native_entries)
    return all(read_only) and gconv.all_source_mounts_read_only and locale.all_source_mounts_read_only


def _snapshot_browser_python_runtime_source() -> _BrowserPythonRuntimeSnapshot:
    """Retain exact validated runtime bytes without granting execution authority."""

    retained_entries: list[_BrowserRuntimeBundleEntry] = []
    startup = _observe_browser_runtime_startup_files(
        _BROWSER_RUNTIME_STARTUP_FILES,
        expected_sha256=_BROWSER_RUNTIME_STARTUP_SHA256,
        retained_entries=retained_entries,
    )
    stdlib = _observe_browser_stdlib_tree(
        _BROWSER_RUNTIME_STDLIB_ROOT,
        _BROWSER_RUNTIME_STDLIB_ZIP,
        expected_sha256=_BROWSER_RUNTIME_STDLIB_SHA256,
        expected_entries=_BROWSER_RUNTIME_STDLIB_ENTRIES,
        expected_regular_files=_BROWSER_RUNTIME_STDLIB_FILES,
        expected_directories=_BROWSER_RUNTIME_STDLIB_DIRECTORIES,
        expected_symlinks=_BROWSER_RUNTIME_STDLIB_SYMLINKS,
        expected_regular_bytes=_BROWSER_RUNTIME_STDLIB_BYTES,
        expected_user_id=_BROWSER_PYTHON_UID,
        expected_group_id=_BROWSER_PYTHON_GID,
        expected_root_mode=0o555,
        retained_entries=retained_entries,
    )
    native_read_only = _observe_browser_native_runtime_source(
        retained_entries
    )
    observation = _BrowserPythonRuntimeObservation(
        startup_sha256=startup.sha256,
        stdlib_sha256=stdlib.sha256,
        stdlib_entries=stdlib.entries,
        stdlib_regular_files=stdlib.regular_files,
        stdlib_directories=stdlib.directories,
        stdlib_symlinks=stdlib.symlinks,
        stdlib_regular_bytes=stdlib.regular_bytes,
        stdlib_zip_absent=True,
        native_sha256=_BROWSER_RUNTIME_NATIVE_SHA256,
        native_entries=_BROWSER_RUNTIME_NATIVE_ENTRIES,
        native_regular_files=_BROWSER_RUNTIME_NATIVE_REGULAR_FILES,
        native_directories=_BROWSER_RUNTIME_NATIVE_DIRECTORIES,
        native_symlinks=_BROWSER_RUNTIME_NATIVE_SYMLINKS,
        native_regular_bytes=_BROWSER_RUNTIME_NATIVE_BYTES,
        gconv_cache_absent=True,
        locale_archive_absent=True,
        all_source_mounts_read_only=(
            startup.all_source_mounts_read_only
            and stdlib.all_source_mounts_read_only
            and native_read_only
        ),
        execution_authority=False,
    )
    paths = tuple(entry.path for entry in retained_entries)
    if (
        len(paths) != len(set(paths))
        or any(not path.startswith("/") for path in paths)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser Python runtime source differs"
        )
    return _BrowserPythonRuntimeSnapshot(
        entries=tuple(sorted(retained_entries, key=lambda entry: entry.path)),
        observation=observation,
        execution_authority=False,
    )


def _observe_browser_python_runtime_source() -> _BrowserPythonRuntimeObservation:
    """Observe exact named source bytes without granting execution authority."""

    return _snapshot_browser_python_runtime_source().observation


def _canonical_browser_distribution_name(value: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 128
        or not value.isascii()
        or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", value)
        is None
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    selected = re.sub(r"[-_.]+", "-", value).lower()
    if not selected or len(selected) > 128:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    return selected


def _canonical_browser_dependency_relative_path(value: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > _BROWSER_DEPENDENCY_PATH_BYTES_LIMIT
        or not value.isascii()
        or value.startswith("/")
        or value.endswith("/")
        or "//" in value
        or "\\" in value
        or "\0" in value
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    components = value.split("/")
    if (
        len(components) > _BROWSER_DEPENDENCY_PATH_DEPTH_LIMIT
        or any(
            not component
            or component in {".", ".."}
            or len(component.encode("ascii")) > 255
            for component in components
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    return value


def _canonical_browser_dependency_tar_path(value: str) -> str | None:
    if value == ".":
        return None
    if type(value) is not str or not value.startswith("./"):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    return _canonical_browser_dependency_relative_path(value[2:])


def _parse_browser_dependency_metadata(
    raw: bytes,
    *,
    expected_name: str,
    expected_version: str,
) -> None:
    if (
        type(raw) is not bytes
        or not raw
        or len(raw) > 1024 * 1024
        or b"\0" in raw
        or type(expected_name) is not str
        or type(expected_version) is not str
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    try:
        message = BytesParser(policy=compat32).parsebytes(raw, headersonly=True)
        metadata_versions = message.get_all("Metadata-Version", failobj=[])
        names = message.get_all("Name", failobj=[])
        versions = message.get_all("Version", failobj=[])
    except (TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        ) from None
    if (
        message.defects
        or len(metadata_versions) != 1
        or type(metadata_versions[0]) is not str
        or re.fullmatch(r"2\.[1-4]", metadata_versions[0]) is None
        or len(names) != 1
        or len(versions) != 1
        or type(names[0]) is not str
        or type(versions[0]) is not str
        or names[0] != names[0].strip()
        or versions[0] != versions[0].strip()
        or _canonical_browser_distribution_name(names[0]) != expected_name
        or versions[0] != expected_version
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )


def _decode_browser_dependency_record_hash(value: str) -> bytes:
    prefix = "sha256="
    encoded = value[len(prefix) :] if isinstance(value, str) else ""
    if (
        type(value) is not str
        or not value.startswith(prefix)
        or re.fullmatch(r"[A-Za-z0-9_-]{43}", encoded) is None
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    try:
        selected = base64.urlsafe_b64decode(encoded + "=")
    except (ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        ) from None
    canonical = base64.urlsafe_b64encode(selected).rstrip(b"=").decode("ascii")
    if len(selected) != hashlib.sha256().digest_size or canonical != encoded:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    return selected


def _require_browser_dependency_archive_member_bound(raw: bytes) -> None:
    """Count physical tar headers before tarfile can recurse through extensions."""

    offset = 0
    members = 0
    try:
        while True:
            if offset + (2 * tarfile.BLOCKSIZE) > len(raw):
                raise ValueError
            header = raw[offset : offset + tarfile.BLOCKSIZE]
            if header == b"\0" * tarfile.BLOCKSIZE:
                trailer = raw[
                    offset + tarfile.BLOCKSIZE :
                    offset + (2 * tarfile.BLOCKSIZE)
                ]
                if trailer != b"\0" * tarfile.BLOCKSIZE or any(
                    raw[offset + (2 * tarfile.BLOCKSIZE) :]
                ):
                    raise ValueError
                return
            members += 1
            if members > _BROWSER_DEPENDENCY_ARCHIVE_MEMBERS:
                raise ValueError
            member = tarfile.TarInfo.frombuf(
                header,
                encoding="utf-8",
                errors="surrogateescape",
            )
            if type(member.size) is not int or member.size < 0:
                raise ValueError
            payload_blocks = (
                member.size + tarfile.BLOCKSIZE - 1
            ) // tarfile.BLOCKSIZE
            selected = offset + tarfile.BLOCKSIZE * (1 + payload_blocks)
            if selected <= offset or selected > len(raw):
                raise ValueError
            offset = selected
    except LocalStagingAcceptanceError:
        raise
    except (
        tarfile.TarError,
        OSError,
        EOFError,
        IndexError,
        OverflowError,
        RecursionError,
        ValueError,
        TypeError,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        ) from None


def _observe_browser_dependency_archive(
    raw: bytes,
    *,
    source_image_id: str,
    expected_sha256: str = _BROWSER_DEPENDENCY_TREE_SHA256,
    expected_distributions: tuple[tuple[str, str, str], ...] = (
        _BROWSER_DEPENDENCY_DISTRIBUTIONS
    ),
    expected_extras: tuple[tuple[str, int, str], ...] = (
        _BROWSER_DEPENDENCY_SOURCE_EXTRAS
    ),
    expected_external_records: tuple[tuple[str, str, str, str], ...] = (
        _BROWSER_DEPENDENCY_EXTERNAL_RECORDS
    ),
    expected_entries: int = _BROWSER_DEPENDENCY_TREE_ENTRIES,
    expected_regular_files: int = _BROWSER_DEPENDENCY_TREE_FILES,
    expected_directories: int = _BROWSER_DEPENDENCY_TREE_DIRECTORIES,
    expected_regular_bytes: int = _BROWSER_DEPENDENCY_TREE_BYTES,
    expected_source_entries: int = _BROWSER_DEPENDENCY_SOURCE_ENTRIES,
    expected_source_regular_files: int = _BROWSER_DEPENDENCY_SOURCE_FILES,
    expected_source_directories: int = (
        _BROWSER_DEPENDENCY_SOURCE_DIRECTORIES
    ),
    expected_source_regular_bytes: int = _BROWSER_DEPENDENCY_SOURCE_BYTES,
    expected_record_rows: int = _BROWSER_DEPENDENCY_RECORD_ROWS,
) -> _BrowserDependencySourceObservation:
    integer_values = (
        expected_entries,
        expected_regular_files,
        expected_directories,
        expected_regular_bytes,
        expected_source_entries,
        expected_source_regular_files,
        expected_source_directories,
        expected_source_regular_bytes,
        expected_record_rows,
    )
    if (
        type(raw) is not bytes
        or not raw
        or len(raw) > _BROWSER_DEPENDENCY_ARCHIVE_LIMIT
        or len(raw) % tarfile.BLOCKSIZE != 0
        or source_image_id != FROZEN_IMAGE_ID
        or type(expected_sha256) is not str
        or _SHA256_TEXT.fullmatch(expected_sha256) is None
        or type(expected_distributions) is not tuple
        or not expected_distributions
        or type(expected_extras) is not tuple
        or type(expected_external_records) is not tuple
        or any(type(value) is not int or value < 0 for value in integer_values)
        or expected_entries
        != expected_regular_files + expected_directories
        or expected_source_entries
        != expected_source_regular_files + expected_source_directories
        or expected_source_entries > _BROWSER_DEPENDENCY_SOURCE_ENTRIES
        or expected_source_regular_bytes > _BROWSER_DEPENDENCY_SOURCE_BYTES
        or expected_record_rows > _BROWSER_DEPENDENCY_RECORD_ROWS
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    _require_browser_dependency_archive_member_bound(raw)
    expected_distribution_values: list[tuple[str, str, str]] = []
    for value in expected_distributions:
        if (
            type(value) is not tuple
            or len(value) != 3
            or any(type(item) is not str or not item for item in value)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency source differs"
            )
        name, version, dist_info = value
        if (
            _canonical_browser_distribution_name(name) != name
            or not version.isascii()
            or len(version) > 128
            or _canonical_browser_dependency_relative_path(dist_info)
            != dist_info
            or "/" in dist_info
            or not dist_info.endswith(".dist-info")
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency source differs"
            )
        expected_distribution_values.append(value)
    if (
        tuple(sorted(expected_distribution_values)) != expected_distributions
        or len({value[0] for value in expected_distributions})
        != len(expected_distributions)
        or len({value[2] for value in expected_distributions})
        != len(expected_distributions)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    extra_expectations: dict[str, tuple[int, str]] = {}
    for value in expected_extras:
        if (
            type(value) is not tuple
            or len(value) != 3
            or type(value[0]) is not str
            or type(value[1]) is not int
            or value[1] < 0
            or type(value[2]) is not str
            or _SHA256_TEXT.fullmatch(value[2]) is None
            or _canonical_browser_dependency_relative_path(value[0])
            != value[0]
            or value[0] in extra_expectations
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency source differs"
            )
        extra_expectations[value[0]] = (value[1], value[2])
    external_expectations: set[tuple[str, str, str, str]] = set()
    for value in expected_external_records:
        if (
            type(value) is not tuple
            or len(value) != 4
            or any(type(item) is not str or not item for item in value)
            or not value[0].endswith(".dist-info/RECORD")
            or not value[1].startswith("../../../bin/")
            or "/" in value[1][len("../../../bin/") :]
            or re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", value[3]) is None
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency source differs"
            )
        _canonical_browser_dependency_relative_path(value[0])
        _decode_browser_dependency_record_hash(value[2])
        external_expectations.add(value)
    if len(external_expectations) != len(expected_external_records):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )

    members: list[tarfile.TarInfo]
    archive_stream = io.BytesIO(raw)
    try:
        with tarfile.open(fileobj=archive_stream, mode="r:") as archive:
            if archive.pax_headers:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser dependency source differs"
                )
            members = []
            while True:
                member = archive.next()
                if member is None:
                    break
                if len(members) >= _BROWSER_DEPENDENCY_ARCHIVE_MEMBERS:
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser dependency source differs"
                    )
                members.append(member)
            archive_offset = archive.offset
            if (
                archive_offset + (2 * tarfile.BLOCKSIZE) > len(raw)
                or raw[
                    archive_offset : archive_offset + (2 * tarfile.BLOCKSIZE)
                ]
                != b"\0" * (2 * tarfile.BLOCKSIZE)
                or any(raw[archive_offset + (2 * tarfile.BLOCKSIZE) :])
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance browser dependency source differs"
                )

            directories: dict[str, int] = {}
            regular: dict[str, tuple[int, int, bytes]] = {}
            selected_content: dict[str, bytes] = {}
            identities: set[str] = set()
            root_seen = False
            total_regular_bytes = 0
            selected_paths = {
                f"{dist_info}/METADATA"
                for _, _, dist_info in expected_distributions
            } | {
                f"{dist_info}/RECORD"
                for _, _, dist_info in expected_distributions
            } | set(extra_expectations)
            for member in members:
                if member.pax_headers or member.sparse:
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser dependency source differs"
                    )
                path = _canonical_browser_dependency_tar_path(member.name)
                if path is None:
                    if (
                        root_seen
                        or member.type != tarfile.DIRTYPE
                        or member.mode != 0o755
                        or member.uid != 0
                        or member.gid != 0
                        or member.size != 0
                        or member.linkname
                    ):
                        raise LocalStagingAcceptanceError(
                            "local acceptance browser dependency source differs"
                        )
                    root_seen = True
                    continue
                folded = path.casefold()
                if folded in identities:
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser dependency source differs"
                    )
                identities.add(folded)
                if (
                    member.uid != 0
                    or member.gid != 0
                    or member.mode & ~0o777
                    or member.linkname
                ):
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser dependency source differs"
                    )
                if member.type == tarfile.DIRTYPE:
                    if member.mode != 0o755 or member.size != 0:
                        raise LocalStagingAcceptanceError(
                            "local acceptance browser dependency source differs"
                        )
                    directories[path] = member.mode
                elif member.type == tarfile.REGTYPE:
                    if member.mode not in {0o644, 0o755} or member.size < 0:
                        raise LocalStagingAcceptanceError(
                            "local acceptance browser dependency source differs"
                        )
                    total_regular_bytes += member.size
                    if total_regular_bytes > expected_source_regular_bytes:
                        raise LocalStagingAcceptanceError(
                            "local acceptance browser dependency source differs"
                        )
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise LocalStagingAcceptanceError(
                            "local acceptance browser dependency source differs"
                        )
                    content = stream.read(member.size + 1)
                    if len(content) != member.size:
                        raise LocalStagingAcceptanceError(
                            "local acceptance browser dependency source differs"
                        )
                    digest = hashlib.sha256(content).digest()
                    regular[path] = (member.mode, member.size, digest)
                    if path in selected_paths:
                        selected_content[path] = content
                else:
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser dependency source differs"
                    )
    except LocalStagingAcceptanceError:
        raise
    except (
        tarfile.TarError,
        OSError,
        EOFError,
        IndexError,
        OverflowError,
        RecursionError,
        ValueError,
        TypeError,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        ) from None

    if (
        not root_seen
        or len(directories) != expected_source_directories
        or len(regular) != expected_source_regular_files
        or len(directories) + len(regular) != expected_source_entries
        or total_regular_bytes != expected_source_regular_bytes
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    all_paths = set(directories) | set(regular)
    for path in all_paths:
        components = path.split("/")
        for index in range(1, len(components)):
            if "/".join(components[:index]) not in directories:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser dependency source differs"
                )
    observed_dist_info = tuple(
        sorted(path for path in directories if path.endswith(".dist-info"))
    )
    if observed_dist_info != tuple(
        sorted(value[2] for value in expected_distributions)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    for name, version, dist_info in expected_distributions:
        metadata_path = f"{dist_info}/METADATA"
        record_path = f"{dist_info}/RECORD"
        try:
            metadata = selected_content[metadata_path]
            selected_content[record_path]
        except KeyError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency source differs"
            ) from None
        _parse_browser_dependency_metadata(
            metadata,
            expected_name=name,
            expected_version=version,
        )

    excluded: list[tuple[str, str]] = []
    for path, (expected_size, expected_digest) in sorted(
        extra_expectations.items()
    ):
        try:
            mode, size, digest = regular[path]
            content = selected_content[path]
        except KeyError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency source differs"
            ) from None
        if (
            mode != 0o644
            or size != expected_size
            or len(content) != expected_size
            or digest.hex() != expected_digest
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency source differs"
            )
        excluded.append((path, expected_digest))

    owners: dict[str, str] = {}
    observed_external: set[tuple[str, str, str, str]] = set()
    record_rows = 0
    for _, _, dist_info in expected_distributions:
        record_path = f"{dist_info}/RECORD"
        metadata_path = f"{dist_info}/METADATA"
        content = selected_content[record_path]
        try:
            text = content.decode("utf-8", errors="strict")
            rows = csv.reader(io.StringIO(text, newline=""), strict=True)
            seen_rows: set[str] = set()
            owned_by_record: set[str] = set()
            for row in rows:
                record_rows += 1
                if len(row) != 3 or not row[0] or row[0] in seen_rows:
                    raise ValueError
                seen_rows.add(row[0])
                if row[0].startswith("../"):
                    selected_external = (record_path, *row)
                    if selected_external not in external_expectations:
                        raise ValueError
                    observed_external.add(selected_external)
                    continue
                path = _canonical_browser_dependency_relative_path(row[0])
                if path not in regular or path in extra_expectations:
                    raise ValueError
                if path in owners:
                    raise ValueError
                owners[path] = record_path
                owned_by_record.add(path)
                _, size, digest = regular[path]
                if path == record_path:
                    if row[1:] != ["", ""]:
                        raise ValueError
                else:
                    if (
                        _decode_browser_dependency_record_hash(row[1])
                        != digest
                        or re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", row[2])
                        is None
                        or int(row[2]) != size
                    ):
                        raise ValueError
            if {record_path, metadata_path} - owned_by_record:
                raise ValueError
        except (
            UnicodeDecodeError,
            csv.Error,
            ValueError,
            TypeError,
            LocalStagingAcceptanceError,
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency source differs"
            ) from None
    selected_regular = set(regular) - set(extra_expectations)
    if (
        record_rows != expected_record_rows
        or observed_external != external_expectations
        or set(owners) != selected_regular
        or any(
            path.endswith((".pth", ".egg-link", ".pyc"))
            or path.rsplit("/", 1)[-1]
            in {"sitecustomize.py", "usercustomize.py"}
            for path in selected_regular
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )

    records: list[tuple[bytes, bytes, int, int, bytes]] = []
    for path, mode in directories.items():
        records.append((b"D", path.encode("ascii"), mode, 0, b""))
    regular_bytes = 0
    for path in selected_regular:
        mode, size, digest = regular[path]
        regular_bytes += size
        records.append((b"F", path.encode("ascii"), mode, size, digest))
    if (
        len(records) != expected_entries
        or len(selected_regular) != expected_regular_files
        or len(directories) != expected_directories
        or regular_bytes != expected_regular_bytes
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    manifest = hashlib.sha256(_BROWSER_DEPENDENCY_TREE_DOMAIN)
    for kind, path, mode, size, payload in sorted(
        records,
        key=lambda item: item[1],
    ):
        _update_browser_runtime_manifest(
            manifest,
            kind=kind,
            path=path,
            mode=mode,
            size=size,
            payload=payload,
        )
    observed_sha256 = manifest.hexdigest()
    if observed_sha256 != expected_sha256:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency source differs"
        )
    return _BrowserDependencySourceObservation(
        image_id=source_image_id,
        tree_sha256=observed_sha256,
        entries=len(records),
        regular_files=len(selected_regular),
        directories=len(directories),
        symlinks=0,
        regular_bytes=regular_bytes,
        source_entries=len(directories) + len(regular),
        source_regular_files=len(regular),
        source_directories=len(directories),
        source_regular_bytes=total_regular_bytes,
        record_rows=record_rows,
        distributions=expected_distributions,
        excluded_source_files=tuple(excluded),
        execution_authority=False,
    )


def _scan_browser_tar_physical_members(
    raw: bytes,
    *,
    maximum_bytes: int,
    maximum_members: int,
) -> int:
    """Validate one bounded tar envelope before `tarfile` interprets it."""

    if (
        type(raw) is not bytes
        or not raw
        or type(maximum_bytes) is not int
        or maximum_bytes <= 0
        or len(raw) > maximum_bytes
        or len(raw) % tarfile.BLOCKSIZE != 0
        or type(maximum_members) is not int
        or maximum_members < 0
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency image differs"
        )
    offset = 0
    members = 0
    try:
        while True:
            if offset + (2 * tarfile.BLOCKSIZE) > len(raw):
                raise ValueError
            header = raw[offset : offset + tarfile.BLOCKSIZE]
            if header == b"\0" * tarfile.BLOCKSIZE:
                if (
                    raw[
                        offset + tarfile.BLOCKSIZE :
                        offset + (2 * tarfile.BLOCKSIZE)
                    ]
                    != b"\0" * tarfile.BLOCKSIZE
                    or any(raw[offset + (2 * tarfile.BLOCKSIZE) :])
                ):
                    raise ValueError
                return members
            members += 1
            if members > maximum_members:
                raise ValueError
            member = tarfile.TarInfo.frombuf(
                header,
                encoding="utf-8",
                errors="surrogateescape",
            )
            if type(member.size) is not int or member.size < 0:
                raise ValueError
            blocks = (
                member.size + tarfile.BLOCKSIZE - 1
            ) // tarfile.BLOCKSIZE
            selected = offset + tarfile.BLOCKSIZE * (1 + blocks)
            if selected <= offset or selected > len(raw):
                raise ValueError
            offset = selected
    except LocalStagingAcceptanceError:
        raise
    except (
        tarfile.TarError,
        OSError,
        EOFError,
        IndexError,
        OverflowError,
        RecursionError,
        ValueError,
        TypeError,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency image differs"
        ) from None


def _canonical_browser_image_layer_path(value: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8", errors="strict"))
        > _BROWSER_DEPENDENCY_PATH_BYTES_LIMIT
        or value.startswith(("/", "./"))
        or value.endswith("/")
        or "//" in value
        or "\\" in value
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency layer differs"
        )
    components = value.split("/")
    if (
        len(components) > _BROWSER_DEPENDENCY_PATH_DEPTH_LIMIT
        or any(
            component in {"", ".", ".."}
            or len(component.encode("utf-8", errors="strict")) > 255
            for component in components
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency layer differs"
        )
    return value


def _remove_browser_dependency_tree_path(
    entries: dict[str, _BrowserDependencyTreeEntry],
    path: str,
    *,
    descendants_only: bool,
) -> None:
    prefix = f"{path}/" if path else ""
    for selected in tuple(entries):
        if (
            (not descendants_only and selected == path)
            or (prefix and selected.startswith(prefix))
            or (not path and descendants_only)
        ):
            del entries[selected]


def _apply_browser_dependency_layer(
    entries: dict[str, _BrowserDependencyTreeEntry],
    root_entry: _BrowserDependencyTreeEntry | None,
    raw: bytes,
    *,
    layer_sha256: str,
    expected_bytes: int,
    expected_physical_members: int,
    expected_semantic_members: int,
    target_path: str = _BROWSER_DEPENDENCY_IMAGE_PATH,
    maximum_regular_bytes: int = _BROWSER_DEPENDENCY_SOURCE_BYTES,
) -> tuple[dict[str, _BrowserDependencyTreeEntry], _BrowserDependencyTreeEntry | None]:
    """Apply one authenticated OCI layer only to one selected subtree."""

    if (
        type(entries) is not dict
        or (
            root_entry is not None
            and type(root_entry) is not _BrowserDependencyTreeEntry
        )
        or type(layer_sha256) is not str
        or _SHA256_TEXT.fullmatch(layer_sha256) is None
        or type(expected_bytes) is not int
        or expected_bytes < 0
        or type(raw) is not bytes
        or len(raw) != expected_bytes
        or hashlib.sha256(raw).hexdigest() != layer_sha256
        or type(expected_physical_members) is not int
        or expected_physical_members < 0
        or type(expected_semantic_members) is not int
        or expected_semantic_members < 0
        or type(target_path) is not str
        or not target_path.startswith("/")
        or target_path.endswith("/")
        or "//" in target_path
        or type(maximum_regular_bytes) is not int
        or maximum_regular_bytes < 0
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency layer differs"
        )
    physical_members = _scan_browser_tar_physical_members(
        raw,
        maximum_bytes=expected_bytes,
        maximum_members=expected_physical_members,
    )
    if physical_members != expected_physical_members:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency layer differs"
        )

    target = _canonical_browser_image_layer_path(target_path.lstrip("/"))
    ancestors = tuple(
        "/".join(target.split("/")[:index])
        for index in range(1, len(target.split("/")))
    )
    prior_entries = dict(entries)
    prior_root = root_entry
    additions: list[tuple[str, tarfile.TarInfo, bytes]] = []
    whiteouts: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    seen_folded: set[str] = set()
    semantic_members = 0
    archive_stream = io.BytesIO(raw)
    try:
        with tarfile.open(fileobj=archive_stream, mode="r:") as archive:
            while True:
                member = archive.next()
                if member is None:
                    break
                semantic_members += 1
                if semantic_members > expected_semantic_members:
                    raise ValueError
                if member.name == ".":
                    if (
                        not member.isdir()
                        or member.linkname
                        or member.pax_headers
                        or getattr(member, "sparse", None)
                    ):
                        raise ValueError
                    continue
                path = _canonical_browser_image_layer_path(member.name)
                if path in seen_paths:
                    raise ValueError
                seen_paths.add(path)
                basename = path.rsplit("/", 1)[-1]
                parent = path.rsplit("/", 1)[0] if "/" in path else ""
                relevant_whiteout = (
                    basename.startswith(".wh.")
                    and (
                        parent == target
                        or parent.startswith(f"{target}/")
                        or parent in ancestors
                        or parent == ""
                    )
                )
                relevant_normal = (
                    path == target
                    or path.startswith(f"{target}/")
                    or path in ancestors
                )
                if relevant_whiteout or relevant_normal:
                    folded = path.casefold()
                    if folded in seen_folded:
                        raise ValueError
                    seen_folded.add(folded)
                if relevant_whiteout:
                    if (
                        not member.isreg()
                        or member.size != 0
                        or member.linkname
                        or member.pax_headers
                        or getattr(member, "sparse", None)
                    ):
                        raise ValueError
                    marker = basename[len(".wh.") :]
                    if marker == ".wh..opq":
                        whiteouts.append((parent, "opaque"))
                    elif marker and marker not in {".", ".."}:
                        whiteouts.append(
                            (
                                f"{parent}/{marker}" if parent else marker,
                                "remove",
                            )
                        )
                    else:
                        raise ValueError
                    continue
                if not relevant_normal:
                    continue
                if member.pax_headers or getattr(member, "sparse", None):
                    raise ValueError
                if path in ancestors:
                    if not member.isdir() or member.linkname:
                        raise ValueError
                    continue
                if path == target:
                    if not member.isdir() or member.linkname:
                        raise ValueError
                    additions.append(("", member, b""))
                    continue
                relative = path[len(target) + 1 :]
                relative = _canonical_browser_dependency_relative_path(relative)
                if member.isdir():
                    if member.linkname:
                        raise ValueError
                    additions.append((relative, member, b""))
                elif member.isreg():
                    if member.linkname or member.size > maximum_regular_bytes:
                        raise ValueError
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError
                    content = stream.read(member.size + 1)
                    if len(content) != member.size:
                        raise ValueError
                    additions.append((relative, member, content))
                else:
                    raise ValueError
            if semantic_members != expected_semantic_members:
                raise ValueError
    except (
        tarfile.TarError,
        OSError,
        EOFError,
        IndexError,
        OverflowError,
        RecursionError,
        ValueError,
        TypeError,
        LocalStagingAcceptanceError,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency layer differs"
        ) from None

    for path, operation in whiteouts:
        if operation == "opaque":
            if not path or path in ancestors:
                prior_entries.clear()
                prior_root = None
            elif path == target:
                prior_entries.clear()
            elif path.startswith(f"{target}/"):
                relative = path[len(target) + 1 :]
                _remove_browser_dependency_tree_path(
                    prior_entries,
                    relative,
                    descendants_only=True,
                )
            continue
        if path == target or path in ancestors:
            prior_entries.clear()
            prior_root = None
        elif path.startswith(f"{target}/"):
            relative = path[len(target) + 1 :]
            _remove_browser_dependency_tree_path(
                prior_entries,
                relative,
                descendants_only=False,
            )
    for relative, member, content in additions:
        selected = _BrowserDependencyTreeEntry(
            path=relative,
            kind="D" if member.isdir() else "F",
            mode=member.mode,
            user_id=member.uid,
            group_id=member.gid,
            layer_sha256=layer_sha256,
            content=content,
        )
        if not relative:
            prior_root = selected
            continue
        if selected.kind == "F":
            _remove_browser_dependency_tree_path(
                prior_entries,
                relative,
                descendants_only=False,
            )
        elif relative in prior_entries and prior_entries[relative].kind == "F":
            del prior_entries[relative]
        prior_entries[relative] = selected
    return prior_entries, prior_root


def _serialize_browser_dependency_tree(
    entries: Mapping[str, _BrowserDependencyTreeEntry],
    root_entry: _BrowserDependencyTreeEntry,
) -> bytes:
    if (
        type(entries) is not dict
        or type(root_entry) is not _BrowserDependencyTreeEntry
        or root_entry.path != ""
        or root_entry.kind != "D"
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency layer differs"
        )
    output = io.BytesIO()
    try:
        with tarfile.open(
            fileobj=output,
            mode="w",
            format=tarfile.USTAR_FORMAT,
        ) as archive:
            root = tarfile.TarInfo(".")
            root.type = tarfile.DIRTYPE
            root.mode = root_entry.mode
            root.uid = root_entry.user_id
            root.gid = root_entry.group_id
            root.mtime = 0
            archive.addfile(root)
            for path, entry in sorted(entries.items()):
                if entry.path != path:
                    raise ValueError
                item = tarfile.TarInfo(f"./{path}")
                item.mode = entry.mode
                item.uid = entry.user_id
                item.gid = entry.group_id
                item.mtime = 0
                if entry.kind == "D":
                    item.type = tarfile.DIRTYPE
                    archive.addfile(item)
                elif entry.kind == "F":
                    item.type = tarfile.REGTYPE
                    item.size = len(entry.content)
                    archive.addfile(item, io.BytesIO(entry.content))
                else:
                    raise ValueError
        selected = output.getvalue()
    except (OSError, OverflowError, tarfile.TarError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency layer differs"
        ) from None
    if len(selected) > _BROWSER_DEPENDENCY_ARCHIVE_LIMIT:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency layer differs"
        )
    return selected


def _browser_application_tree_sha256(
    entries: Mapping[str, _BrowserDependencyTreeEntry],
    *,
    expected_sha256: str = _BROWSER_APPLICATION_TREE_SHA256,
    expected_entries: int = _BROWSER_APPLICATION_TREE_ENTRIES,
    expected_files: int = _BROWSER_APPLICATION_TREE_FILES,
    expected_directories: int = _BROWSER_APPLICATION_TREE_DIRECTORIES,
    expected_bytes: int = _BROWSER_APPLICATION_TREE_BYTES,
    expected_layer: str = _BROWSER_APPLICATION_WINNING_LAYER,
) -> str:
    if (
        type(entries) is not dict
        or type(expected_sha256) is not str
        or _SHA256_TEXT.fullmatch(expected_sha256) is None
        or type(expected_entries) is not int
        or expected_entries < 0
        or type(expected_files) is not int
        or expected_files < 0
        or type(expected_directories) is not int
        or expected_directories < 0
        or expected_files + expected_directories != expected_entries
        or type(expected_bytes) is not int
        or expected_bytes < 0
        or type(expected_layer) is not str
        or _SHA256_TEXT.fullmatch(expected_layer) is None
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser application source differs"
        )
    records: list[tuple[bytes, bytes, int, int, bytes]] = []
    regular_files = 0
    directories = 0
    regular_bytes = 0
    for path, entry in entries.items():
        if (
            type(path) is not str
            or type(entry) is not _BrowserDependencyTreeEntry
            or entry.path != path
            or type(entry.kind) is not str
            or entry.kind not in {"F", "D"}
            or type(entry.content) is not bytes
            or (entry.user_id, entry.group_id)
            != (_BROWSER_APPLICATION_ROOT_UID, _BROWSER_APPLICATION_ROOT_GID)
            or entry.layer_sha256 != expected_layer
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser application source differs"
            )
        canonical = _canonical_browser_dependency_relative_path(path)
        if canonical != path:
            raise LocalStagingAcceptanceError(
                "local acceptance browser application source differs"
            )
        if entry.kind == "D":
            if entry.content:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser application source differs"
                )
            directories += 1
            records.append((b"D", path.encode("ascii"), entry.mode, 0, b""))
        else:
            regular_files += 1
            regular_bytes += len(entry.content)
            records.append(
                (
                    b"F",
                    path.encode("ascii"),
                    entry.mode,
                    len(entry.content),
                    hashlib.sha256(entry.content).digest(),
                )
            )
    if (
        len(records) != expected_entries
        or regular_files != expected_files
        or directories != expected_directories
        or regular_bytes != expected_bytes
        or _BROWSER_APPLICATION_TREE_SYMLINKS != 0
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser application source differs"
        )
    digest = hashlib.sha256(_BROWSER_APPLICATION_TREE_DOMAIN)
    for kind, path, mode, size, payload in sorted(
        records,
        key=lambda item: item[1],
    ):
        _update_browser_runtime_manifest(
            digest,
            kind=kind,
            path=path,
            mode=mode,
            size=size,
            payload=payload,
        )
    observed = digest.hexdigest()
    if observed != expected_sha256:
        raise LocalStagingAcceptanceError(
            "local acceptance browser application source differs"
        )
    return observed


def _browser_dependency_layer_provenance_sha256(
    entries: tuple[_BrowserDependencyTreeEntry, ...],
    *,
    expected_sha256: str = _BROWSER_DEPENDENCY_LAYER_PROVENANCE_SHA256,
) -> str:
    """Bind every selected dependency path to its exact winning image layer."""

    if (
        type(entries) is not tuple
        or not entries
        or type(expected_sha256) is not str
        or _SHA256_TEXT.fullmatch(expected_sha256) is None
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency provenance differs"
        )
    digest = hashlib.sha256(_BROWSER_DEPENDENCY_LAYER_PROVENANCE_DOMAIN)
    previous = ""
    for entry in entries:
        if (
            type(entry) is not _BrowserDependencyTreeEntry
            or _canonical_browser_dependency_relative_path(entry.path)
            != entry.path
            or entry.path <= previous
            or type(entry.layer_sha256) is not str
            or _SHA256_TEXT.fullmatch(entry.layer_sha256) is None
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency provenance differs"
            )
        path = entry.path.encode("ascii", errors="strict")
        digest.update(len(path).to_bytes(4, "big"))
        digest.update(path)
        digest.update(bytes.fromhex(entry.layer_sha256))
        previous = entry.path
    observed = digest.hexdigest()
    if observed != expected_sha256:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency provenance differs"
        )
    return observed


def _parse_browser_dependency_image_export(
    raw: bytes,
    *,
    source_image_id: str = FROZEN_IMAGE_ID,
    expected_image_id: str = FROZEN_IMAGE_ID,
    expected_layers: tuple[str, ...] = FROZEN_IMAGE_ROOTFS_LAYERS,
    expected_layer_sizes: tuple[int, ...] = _BROWSER_DEPENDENCY_LAYER_SIZES,
    expected_physical_members: tuple[int, ...] = (
        _BROWSER_DEPENDENCY_LAYER_PHYSICAL_MEMBERS
    ),
    expected_semantic_members: tuple[int, ...] = (
        _BROWSER_DEPENDENCY_LAYER_SEMANTIC_MEMBERS
    ),
    expected_legacy_blobs: tuple[tuple[str, int], ...] = (
        _BROWSER_DEPENDENCY_LEGACY_BLOBS
    ),
    expected_oci_manifest_sha256: str = (
        _BROWSER_DEPENDENCY_OCI_MANIFEST_SHA256
    ),
    expected_config_bytes: int = _BROWSER_DEPENDENCY_IMAGE_CONFIG_BYTES,
    expected_oci_manifest_bytes: int = (
        _BROWSER_DEPENDENCY_OCI_MANIFEST_BYTES
    ),
    expected_export_members: int = _BROWSER_DEPENDENCY_IMAGE_EXPORT_MEMBERS,
    expected_application_sha256: str = _BROWSER_APPLICATION_TREE_SHA256,
    expected_application_entries: int = _BROWSER_APPLICATION_TREE_ENTRIES,
    expected_application_files: int = _BROWSER_APPLICATION_TREE_FILES,
    expected_application_directories: int = (
        _BROWSER_APPLICATION_TREE_DIRECTORIES
    ),
    expected_application_bytes: int = _BROWSER_APPLICATION_TREE_BYTES,
    expected_application_layer: str = _BROWSER_APPLICATION_WINNING_LAYER,
) -> _BrowserDependencyImageSnapshot:
    """Validate a pinned `docker image save` stream and rebuild its subtree."""

    if (
        type(raw) is not bytes
        or not raw
        or len(raw) > _BROWSER_DEPENDENCY_IMAGE_EXPORT_LIMIT
        or type(source_image_id) is not str
        or source_image_id != expected_image_id
        or not source_image_id.startswith("sha256:")
        or _SHA256_TEXT.fullmatch(source_image_id[7:]) is None
        or type(expected_layers) is not tuple
        or not expected_layers
        or type(expected_layer_sizes) is not tuple
        or type(expected_physical_members) is not tuple
        or type(expected_semantic_members) is not tuple
        or not (
            len(expected_layers)
            == len(expected_layer_sizes)
            == len(expected_physical_members)
            == len(expected_semantic_members)
        )
        or any(
            type(value) is not str
            or not value.startswith("sha256:")
            or _SHA256_TEXT.fullmatch(value[7:]) is None
            for value in expected_layers
        )
        or any(type(value) is not int or value <= 0 for value in expected_layer_sizes)
        or any(type(value) is not int or value < 0 for value in expected_physical_members)
        or any(type(value) is not int or value < 0 for value in expected_semantic_members)
        or type(expected_legacy_blobs) is not tuple
        or type(expected_oci_manifest_sha256) is not str
        or _SHA256_TEXT.fullmatch(expected_oci_manifest_sha256) is None
        or type(expected_config_bytes) is not int
        or expected_config_bytes <= 0
        or type(expected_oci_manifest_bytes) is not int
        or expected_oci_manifest_bytes <= 0
        or type(expected_export_members) is not int
        or expected_export_members <= 0
        or type(expected_application_sha256) is not str
        or _SHA256_TEXT.fullmatch(expected_application_sha256) is None
        or type(expected_application_entries) is not int
        or expected_application_entries < 0
        or type(expected_application_files) is not int
        or expected_application_files < 0
        or type(expected_application_directories) is not int
        or expected_application_directories < 0
        or expected_application_files + expected_application_directories
        != expected_application_entries
        or type(expected_application_bytes) is not int
        or expected_application_bytes < 0
        or type(expected_application_layer) is not str
        or _SHA256_TEXT.fullmatch(expected_application_layer) is None
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency image differs"
        )
    physical = _scan_browser_tar_physical_members(
        raw,
        maximum_bytes=_BROWSER_DEPENDENCY_IMAGE_EXPORT_LIMIT,
        maximum_members=expected_export_members,
    )
    if physical != expected_export_members:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency image differs"
        )

    layer_sizes = dict(zip(expected_layers, expected_layer_sizes, strict=True))
    legacy_sizes: dict[str, int] = {}
    for value in expected_legacy_blobs:
        if (
            type(value) is not tuple
            or len(value) != 2
            or type(value[0]) is not str
            or _SHA256_TEXT.fullmatch(value[0]) is None
            or type(value[1]) is not int
            or value[1] <= 0
            or value[0] in legacy_sizes
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency image differs"
            )
        legacy_sizes[value[0]] = value[1]
    config_digest = source_image_id[7:]
    expected_blob_sizes = {
        value[7:]: layer_sizes[value] for value in expected_layers
    }
    expected_blob_sizes.update(legacy_sizes)
    expected_blob_sizes[config_digest] = expected_config_bytes
    expected_blob_sizes[expected_oci_manifest_sha256] = (
        expected_oci_manifest_bytes
    )
    if len(expected_blob_sizes) != len(expected_layers) + len(legacy_sizes) + 2:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency image differs"
        )
    expected_names = (
        "blobs",
        "blobs/sha256",
        *(f"blobs/sha256/{value}" for value in sorted(expected_blob_sizes)),
        "index.json",
        "manifest.json",
        "oci-layout",
    )
    if len(expected_names) != expected_export_members:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency image differs"
        )

    regular: dict[str, bytes] = {}
    archive_stream = io.BytesIO(raw)
    try:
        with tarfile.open(fileobj=archive_stream, mode="r:") as archive:
            observed_names: list[str] = []
            while True:
                member = archive.next()
                if member is None:
                    break
                observed_names.append(member.name)
                if (
                    member.name not in expected_names
                    or member.uid != 0
                    or member.gid != 0
                    or member.linkname
                    or member.pax_headers
                    or getattr(member, "sparse", None)
                ):
                    raise ValueError
                if member.name in {"blobs", "blobs/sha256"}:
                    if not member.isdir() or member.mode != 0o755 or member.size != 0:
                        raise ValueError
                    continue
                if not member.isreg() or member.mode != 0o644:
                    raise ValueError
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError
                content = stream.read(member.size + 1)
                if len(content) != member.size:
                    raise ValueError
                regular[member.name] = content
            if tuple(observed_names) != expected_names:
                raise ValueError
    except (
        tarfile.TarError,
        OSError,
        EOFError,
        IndexError,
        OverflowError,
        RecursionError,
        ValueError,
        TypeError,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency image differs"
        ) from None

    for digest, expected_size in expected_blob_sizes.items():
        content = regular[f"blobs/sha256/{digest}"]
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != digest:
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency image differs"
            )
    try:
        layout = parse_single_json_object(regular["oci-layout"])
        index = parse_single_json_object(regular["index.json"])
        docker_manifest = parse_single_json_array(regular["manifest.json"])
        oci_manifest = parse_single_json_object(
            regular[f"blobs/sha256/{expected_oci_manifest_sha256}"]
        )
        image_config = parse_single_json_object(
            regular[f"blobs/sha256/{config_digest}"]
        )
        layer_descriptors = tuple(
            {
                "mediaType": "application/vnd.oci.image.layer.v1.tar",
                "digest": value,
                "size": layer_sizes[value],
            }
            for value in expected_layers
        )
        layer_paths = tuple(f"blobs/sha256/{value[7:]}" for value in expected_layers)
        if (
            layout != {"imageLayoutVersion": "1.0.0"}
            or set(index) != {"schemaVersion", "mediaType", "manifests"}
            or type(index["schemaVersion"]) is not int
            or index["schemaVersion"] != 2
            or index["mediaType"] != "application/vnd.oci.image.index.v1+json"
            or index["manifests"]
            != [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": f"sha256:{expected_oci_manifest_sha256}",
                    "size": expected_oci_manifest_bytes,
                }
            ]
            or len(docker_manifest) != 1
            or set(docker_manifest[0])
            != {"Config", "RepoTags", "Layers", "LayerSources"}
            or docker_manifest[0]["Config"]
            != f"blobs/sha256/{config_digest}"
            or docker_manifest[0]["RepoTags"] is not None
            or tuple(docker_manifest[0]["Layers"]) != layer_paths
            or set(docker_manifest[0]["LayerSources"]) != set(expected_layers)
            or any(
                docker_manifest[0]["LayerSources"][value]
                != layer_descriptors[index_value]
                for index_value, value in enumerate(expected_layers)
            )
            or set(oci_manifest)
            != {"schemaVersion", "mediaType", "config", "layers"}
            or type(oci_manifest["schemaVersion"]) is not int
            or oci_manifest["schemaVersion"] != 2
            or oci_manifest["mediaType"]
            != "application/vnd.oci.image.manifest.v1+json"
            or oci_manifest["config"]
            != {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": source_image_id,
                "size": expected_config_bytes,
            }
            or tuple(oci_manifest["layers"]) != layer_descriptors
            or set(image_config)
            != {"architecture", "config", "created", "history", "os", "rootfs"}
            or image_config["architecture"] != "amd64"
            or image_config["os"] != "linux"
            or image_config["rootfs"]
            != {"type": "layers", "diff_ids": list(expected_layers)}
        ):
            raise ValueError
    except (
        KeyError,
        TypeError,
        ValueError,
        IndexError,
        LocalStagingAcceptanceError,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency image differs"
        ) from None

    entries: dict[str, _BrowserDependencyTreeEntry] = {}
    root_entry: _BrowserDependencyTreeEntry | None = None
    application_entries: dict[str, _BrowserDependencyTreeEntry] = {}
    application_root: _BrowserDependencyTreeEntry | None = None
    for index_value, layer in enumerate(expected_layers):
        layer_digest = layer[7:]
        layer_raw = regular[f"blobs/sha256/{layer_digest}"]
        entries, root_entry = _apply_browser_dependency_layer(
            entries,
            root_entry,
            layer_raw,
            layer_sha256=layer_digest,
            expected_bytes=expected_layer_sizes[index_value],
            expected_physical_members=expected_physical_members[index_value],
            expected_semantic_members=expected_semantic_members[index_value],
        )
        application_entries, application_root = (
            _apply_browser_dependency_layer(
                application_entries,
                application_root,
                layer_raw,
                layer_sha256=layer_digest,
                expected_bytes=expected_layer_sizes[index_value],
                expected_physical_members=expected_physical_members[index_value],
                expected_semantic_members=expected_semantic_members[index_value],
                target_path="/app",
                maximum_regular_bytes=expected_application_bytes,
            )
        )
    if root_entry is None:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency layer differs"
        )
    if (
        application_root is None
        or application_root.path != ""
        or application_root.kind != "D"
        or application_root.mode != _BROWSER_APPLICATION_ROOT_MODE
        or application_root.user_id != _BROWSER_APPLICATION_ROOT_UID
        or application_root.group_id != _BROWSER_APPLICATION_ROOT_GID
        or application_root.layer_sha256 != expected_application_layer
        or application_root.content
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser application source differs"
        )
    reconstructed = _serialize_browser_dependency_tree(entries, root_entry)
    observation = _observe_browser_dependency_archive(
        reconstructed,
        source_image_id=source_image_id,
    )
    source_entries = tuple(entries[path] for path in sorted(entries))
    excluded = {value[0] for value in _BROWSER_DEPENDENCY_SOURCE_EXTRAS}
    selected_entries = tuple(
        entry for entry in source_entries if entry.path not in excluded
    )
    if (
        len(source_entries) != observation.source_entries
        or len(selected_entries) != observation.entries
        or any(entry.path in excluded for entry in selected_entries)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency image differs"
        )
    application_source_entries = tuple(
        application_entries[path] for path in sorted(application_entries)
    )
    application_files = sum(
        entry.kind == "F" for entry in application_source_entries
    )
    application_directories = sum(
        entry.kind == "D" for entry in application_source_entries
    )
    application_bytes = sum(
        len(entry.content)
        for entry in application_source_entries
        if entry.kind == "F"
    )
    application_sha256 = _browser_application_tree_sha256(
        application_entries,
        expected_sha256=expected_application_sha256,
        expected_entries=expected_application_entries,
        expected_files=expected_application_files,
        expected_directories=expected_application_directories,
        expected_bytes=expected_application_bytes,
        expected_layer=expected_application_layer,
    )
    if (
        len(application_source_entries) != expected_application_entries
        or application_files != expected_application_files
        or application_directories != expected_application_directories
        or application_bytes != expected_application_bytes
        or application_sha256 != expected_application_sha256
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser application source differs"
        )
    return _BrowserDependencyImageSnapshot(
        image_id=source_image_id,
        rootfs_layers=expected_layers,
        source_entries=source_entries,
        selected_entries=selected_entries,
        observation=observation,
        execution_authority=False,
        application_entries=application_source_entries,
        application_tree_sha256=application_sha256,
        dependency_root=root_entry,
        application_root=application_root,
    )


def _browser_dependency_build_source_sha256(
    descriptor: int,
    *,
    expected_bytes: int,
) -> str:
    digest = hashlib.sha256()
    offset = 0
    try:
        while offset < expected_bytes:
            try:
                block = os.pread(
                    descriptor,
                    min(_CHUNK_BYTES, expected_bytes - offset),
                    offset,
                )
            except InterruptedError:
                continue
            if not block:
                break
            offset += len(block)
            digest.update(block)
        extra = os.pread(descriptor, 1, expected_bytes)
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency build source differs"
        ) from None
    if offset != expected_bytes or extra:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency build source differs"
        )
    return digest.hexdigest()


def _validate_browser_dependency_build_sources(
    sources: tuple[_BrowserDependencyBuildSource, ...],
) -> None:
    if (
        type(sources) is not tuple
        or len(sources) != len(_BROWSER_DEPENDENCY_BUILD_FILES)
        or any(type(source) is not _BrowserDependencyBuildSource for source in sources)
        or tuple(source.path for source in sources)
        != tuple(value[0] for value in _BROWSER_DEPENDENCY_BUILD_FILES)
        or len({source.descriptor for source in sources}) != len(sources)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency build source differs"
        )
    for source, (path, expected_bytes, expected_sha256) in zip(
        sources,
        _BROWSER_DEPENDENCY_BUILD_FILES,
        strict=True,
    ):
        try:
            soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
            before = os.fstat(source.descriptor)
            named_before = path.stat(follow_symlinks=False)
            target = os.readlink(f"/proc/self/fd/{source.descriptor}")
            descriptor_flags = fcntl.fcntl(source.descriptor, fcntl.F_GETFD)
            status_flags = fcntl.fcntl(source.descriptor, fcntl.F_GETFL)
            canonical = path.resolve(strict=True)
            observed_sha256 = _browser_dependency_build_source_sha256(
                source.descriptor,
                expected_bytes=expected_bytes,
            )
            after = os.fstat(source.descriptor)
            named_after = path.stat(follow_symlinks=False)
        except LocalStagingAcceptanceError:
            raise
        except (OSError, OverflowError, ValueError, TypeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency build source differs"
            ) from None
        identity = (
            source.device,
            source.inode,
            source.mode,
            source.user_id,
            source.group_id,
            source.links,
            source.size,
            source.modified_ns,
            source.changed_ns,
        )
        if (
            type(source.descriptor) is not int
            or source.descriptor <= 2
            or source.descriptor > _BROWSER_WORKER_MAX_FD
            or (
                soft_limit != resource.RLIM_INFINITY
                and source.descriptor >= soft_limit
            )
            or path != canonical
            or path.is_symlink()
            or target != str(path)
            or descriptor_flags & fcntl.FD_CLOEXEC == 0
            or os.get_inheritable(source.descriptor)
            or status_flags & os.O_ACCMODE != os.O_RDONLY
            or not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o644
            or (before.st_uid, before.st_gid) != (1000, 1000)
            or before.st_nlink != 1
            or before.st_size != expected_bytes
            or source.sha256 != expected_sha256
            or observed_sha256 != expected_sha256
            or identity
            != (
                before.st_dev,
                before.st_ino,
                stat.S_IMODE(before.st_mode),
                before.st_uid,
                before.st_gid,
                before.st_nlink,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            or identity
            != (
                after.st_dev,
                after.st_ino,
                stat.S_IMODE(after.st_mode),
                after.st_uid,
                after.st_gid,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            or identity
            != (
                named_before.st_dev,
                named_before.st_ino,
                stat.S_IMODE(named_before.st_mode),
                named_before.st_uid,
                named_before.st_gid,
                named_before.st_nlink,
                named_before.st_size,
                named_before.st_mtime_ns,
                named_before.st_ctime_ns,
            )
            or identity
            != (
                named_after.st_dev,
                named_after.st_ino,
                stat.S_IMODE(named_after.st_mode),
                named_after.st_uid,
                named_after.st_gid,
                named_after.st_nlink,
                named_after.st_size,
                named_after.st_mtime_ns,
                named_after.st_ctime_ns,
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser dependency build source differs"
            )


def _open_browser_dependency_build_sources(
) -> tuple[_BrowserDependencyBuildSource, ...]:
    selected: list[_BrowserDependencyBuildSource] = []
    descriptor = -1
    try:
        for path, expected_bytes, expected_sha256 in (
            _BROWSER_DEPENDENCY_BUILD_FILES
        ):
            descriptor = os.open(
                path,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
            info = os.fstat(descriptor)
            source = _BrowserDependencyBuildSource(
                path=path,
                descriptor=descriptor,
                device=info.st_dev,
                inode=info.st_ino,
                mode=stat.S_IMODE(info.st_mode),
                user_id=info.st_uid,
                group_id=info.st_gid,
                links=info.st_nlink,
                size=info.st_size,
                modified_ns=info.st_mtime_ns,
                changed_ns=info.st_ctime_ns,
                sha256=expected_sha256,
            )
            selected.append(source)
            descriptor = -1
        result = tuple(selected)
        _validate_browser_dependency_build_sources(result)
        return result
    except BaseException:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        for source in selected:
            try:
                os.close(source.descriptor)
            except OSError:
                pass
        raise


def _close_browser_dependency_build_sources(
    sources: tuple[_BrowserDependencyBuildSource, ...],
) -> None:
    failed = False
    for source in sources:
        try:
            os.close(source.descriptor)
        except (OSError, OverflowError, ValueError, TypeError):
            failed = True
    if failed:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency build source cleanup differs"
        )


def _read_browser_dependency_audit_entries(
    sources: tuple[_BrowserDependencyBuildSource, ...],
) -> tuple[_BrowserDependencyTreeEntry, ...]:
    """Copy both exact audit programs from their already-held descriptors."""

    _validate_browser_dependency_build_sources(sources)
    expected_paths = (
        "procurement/tools/audit_staging_purchasing_browser.py",
        "procurement/tools/audit_staging_purchasing_browser.mjs",
    )
    selected: list[_BrowserDependencyTreeEntry] = []
    for source, expected_path in zip(
        sources[-len(expected_paths):],
        expected_paths,
        strict=True,
    ):
        try:
            relative = source.path.relative_to(
                _BROWSER_DEPENDENCY_REPOSITORY_ROOT
            ).as_posix()
        except ValueError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser audit source differs"
            ) from None
        if relative != expected_path:
            raise LocalStagingAcceptanceError(
                "local acceptance browser audit source differs"
            )
        content = bytearray()
        offset = 0
        try:
            while offset < source.size:
                try:
                    block = os.pread(
                        source.descriptor,
                        min(_CHUNK_BYTES, source.size - offset),
                        offset,
                    )
                except InterruptedError:
                    continue
                if not block:
                    break
                content.extend(block)
                offset += len(block)
            extra = os.pread(source.descriptor, 1, source.size)
        except (OSError, OverflowError, ValueError, TypeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser audit source differs"
            ) from None
        copied = bytes(content)
        if (
            offset != source.size
            or extra
            or hashlib.sha256(copied).hexdigest() != source.sha256
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser audit source differs"
            )
        selected.append(
            _BrowserDependencyTreeEntry(
                path=relative,
                kind="F",
                mode=source.mode,
                user_id=source.user_id,
                group_id=source.group_id,
                layer_sha256=source.sha256,
                content=copied,
            )
        )
    _validate_browser_dependency_build_sources(sources)
    return tuple(selected)


def _validate_exact_browser_python_runtime_snapshot(
    snapshot: _BrowserPythonRuntimeSnapshot,
) -> None:
    expected_observation = _BrowserPythonRuntimeObservation(
        startup_sha256=_BROWSER_RUNTIME_STARTUP_SHA256,
        stdlib_sha256=_BROWSER_RUNTIME_STDLIB_SHA256,
        stdlib_entries=_BROWSER_RUNTIME_STDLIB_ENTRIES,
        stdlib_regular_files=_BROWSER_RUNTIME_STDLIB_FILES,
        stdlib_directories=_BROWSER_RUNTIME_STDLIB_DIRECTORIES,
        stdlib_symlinks=_BROWSER_RUNTIME_STDLIB_SYMLINKS,
        stdlib_regular_bytes=_BROWSER_RUNTIME_STDLIB_BYTES,
        stdlib_zip_absent=True,
        native_sha256=_BROWSER_RUNTIME_NATIVE_SHA256,
        native_entries=_BROWSER_RUNTIME_NATIVE_ENTRIES,
        native_regular_files=_BROWSER_RUNTIME_NATIVE_REGULAR_FILES,
        native_directories=_BROWSER_RUNTIME_NATIVE_DIRECTORIES,
        native_symlinks=_BROWSER_RUNTIME_NATIVE_SYMLINKS,
        native_regular_bytes=_BROWSER_RUNTIME_NATIVE_BYTES,
        gconv_cache_absent=True,
        locale_archive_absent=True,
        all_source_mounts_read_only=False,
        execution_authority=False,
    )
    if (
        type(snapshot) is not _BrowserPythonRuntimeSnapshot
        or snapshot.observation != expected_observation
        or snapshot.execution_authority is not False
        or type(snapshot.entries) is not tuple
        or len(snapshot.entries)
        != len(_BROWSER_RUNTIME_STARTUP_FILES)
        + _BROWSER_RUNTIME_STDLIB_ENTRIES
        + 1
        + _BROWSER_RUNTIME_NATIVE_ENTRIES
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    entries = _validate_browser_runtime_bundle_entries(
        snapshot.entries,
        require_parents=False,
    )
    by_path = {entry.path: entry for entry in entries}
    startup_paths: set[str] = set()
    for expectation in _BROWSER_RUNTIME_STARTUP_FILES:
        path = str(expectation.path)
        startup_paths.add(path)
        entry = by_path.get(path)
        if (
            entry is None
            or entry.kind != expectation.kind
            or entry.mode != expectation.mode
            or (entry.user_id, entry.group_id)
            != (expectation.user_id, expectation.group_id)
            or entry.provenance
            != f"python-startup:{_BROWSER_RUNTIME_STARTUP_SHA256}"
            or len(entry.content) != expectation.size
            or (
                expectation.kind == "F"
                and hashlib.sha256(entry.content).hexdigest()
                != expectation.payload
            )
            or (
                expectation.kind == "L"
                and entry.content
                != expectation.payload.encode("ascii", errors="strict")
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle differs"
            )
    root_path = str(_BROWSER_RUNTIME_STDLIB_ROOT)
    root = by_path.get(root_path)
    if (
        root is None
        or root.kind != "D"
        or root.mode != 0o555
        or (root.user_id, root.group_id)
        != (_BROWSER_PYTHON_UID, _BROWSER_PYTHON_GID)
        or root.provenance
        != f"python-stdlib:{_BROWSER_RUNTIME_STDLIB_SHA256}"
        or root.content
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    prefix = f"{root_path}/"
    stdlib = tuple(
        entry for entry in entries if entry.path.startswith(prefix)
    )
    native = tuple(
        entry
        for entry in entries
        if entry.provenance
        == f"native-runtime:{_BROWSER_RUNTIME_NATIVE_SHA256}"
    )
    if (
        len(stdlib) != _BROWSER_RUNTIME_STDLIB_ENTRIES
        or len(native) != _BROWSER_RUNTIME_NATIVE_ENTRIES
        or set(by_path)
        != (
            startup_paths
            | {root_path}
            | {entry.path for entry in stdlib}
            | {entry.path for entry in native}
        )
        or str(_BROWSER_RUNTIME_STDLIB_ZIP) in by_path
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    digest = hashlib.sha256(_BROWSER_RUNTIME_STDLIB_DOMAIN)
    regular_files = 0
    directories = 0
    symlinks = 0
    regular_bytes = 0
    for entry in stdlib:
        relative = entry.path[len(prefix) :]
        if (
            _canonical_browser_dependency_relative_path(relative) != relative
            or (entry.user_id, entry.group_id)
            != (_BROWSER_PYTHON_UID, _BROWSER_PYTHON_GID)
            or entry.provenance
            != f"python-stdlib:{_BROWSER_RUNTIME_STDLIB_SHA256}"
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle differs"
            )
        if entry.kind == "F":
            regular_files += 1
            regular_bytes += len(entry.content)
            payload = hashlib.sha256(entry.content).digest()
        elif entry.kind == "D":
            directories += 1
            payload = b""
        else:
            symlinks += 1
            payload = entry.content
        _update_browser_runtime_manifest(
            digest,
            kind=entry.kind.encode("ascii"),
            path=relative.encode("ascii"),
            mode=entry.mode,
            size=len(entry.content),
            payload=payload,
        )
    if (
        regular_files != _BROWSER_RUNTIME_STDLIB_FILES
        or directories != _BROWSER_RUNTIME_STDLIB_DIRECTORIES
        or symlinks != _BROWSER_RUNTIME_STDLIB_SYMLINKS
        or regular_bytes != _BROWSER_RUNTIME_STDLIB_BYTES
        or digest.hexdigest() != _BROWSER_RUNTIME_STDLIB_SHA256
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    _browser_native_runtime_sha256(native)


def _validate_browser_dependency_tree_entry_shape(
    entry: _BrowserDependencyTreeEntry,
    *,
    allow_empty_path: bool = False,
) -> None:
    if (
        type(entry) is not _BrowserDependencyTreeEntry
        or type(allow_empty_path) is not bool
        or type(entry.path) is not str
        or type(entry.kind) is not str
        or entry.kind not in {"F", "D"}
        or type(entry.mode) is not int
        or entry.mode < 0
        or entry.mode > 0o7777
        or type(entry.user_id) is not int
        or entry.user_id < 0
        or type(entry.group_id) is not int
        or entry.group_id < 0
        or type(entry.layer_sha256) is not str
        or _SHA256_TEXT.fullmatch(entry.layer_sha256) is None
        or type(entry.content) is not bytes
        or (entry.kind == "D" and entry.content != b"")
        or (
            entry.path == ""
            and (not allow_empty_path or entry.kind != "D")
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency snapshot differs"
        )
    if entry.path and (
        _canonical_browser_dependency_relative_path(entry.path)
        != entry.path
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency snapshot differs"
        )


def _validate_exact_browser_dependency_snapshot(
    snapshot: _BrowserDependencyImageSnapshot,
) -> None:
    if (
        type(snapshot) is not _BrowserDependencyImageSnapshot
        or snapshot.image_id != FROZEN_IMAGE_ID
        or snapshot.rootfs_layers != FROZEN_IMAGE_ROOTFS_LAYERS
        or snapshot.execution_authority is not False
        or snapshot.application_tree_sha256
        != _BROWSER_APPLICATION_TREE_SHA256
        or type(snapshot.source_entries) is not tuple
        or type(snapshot.selected_entries) is not tuple
        or type(snapshot.application_entries) is not tuple
        or type(snapshot.audit_entries) is not tuple
        or len(snapshot.source_entries) != _BROWSER_DEPENDENCY_SOURCE_ENTRIES
        or len(snapshot.selected_entries) != _BROWSER_DEPENDENCY_TREE_ENTRIES
        or len(snapshot.application_entries) != _BROWSER_APPLICATION_TREE_ENTRIES
        or len(snapshot.audit_entries) != 2
        or type(snapshot.dependency_root) is not _BrowserDependencyTreeEntry
        or type(snapshot.application_root) is not _BrowserDependencyTreeEntry
        or snapshot.observation
        != _BrowserDependencySourceObservation(
            image_id=FROZEN_IMAGE_ID,
            tree_sha256=_BROWSER_DEPENDENCY_TREE_SHA256,
            entries=_BROWSER_DEPENDENCY_TREE_ENTRIES,
            regular_files=_BROWSER_DEPENDENCY_TREE_FILES,
            directories=_BROWSER_DEPENDENCY_TREE_DIRECTORIES,
            symlinks=_BROWSER_DEPENDENCY_TREE_SYMLINKS,
            regular_bytes=_BROWSER_DEPENDENCY_TREE_BYTES,
            source_entries=_BROWSER_DEPENDENCY_SOURCE_ENTRIES,
            source_regular_files=_BROWSER_DEPENDENCY_SOURCE_FILES,
            source_directories=_BROWSER_DEPENDENCY_SOURCE_DIRECTORIES,
            source_regular_bytes=_BROWSER_DEPENDENCY_SOURCE_BYTES,
            record_rows=_BROWSER_DEPENDENCY_RECORD_ROWS,
            distributions=_BROWSER_DEPENDENCY_DISTRIBUTIONS,
            excluded_source_files=tuple(
                (path, digest)
                for path, _, digest in _BROWSER_DEPENDENCY_SOURCE_EXTRAS
            ),
            execution_authority=False,
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    for root in (snapshot.dependency_root, snapshot.application_root):
        _validate_browser_dependency_tree_entry_shape(
            root,
            allow_empty_path=True,
        )
        if (
            root.path != ""
            or root.kind != "D"
            or root.mode != 0o755
            or (root.user_id, root.group_id) != (0, 0)
            or root.layer_sha256 != _BROWSER_APPLICATION_WINNING_LAYER
            or root.content
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle differs"
            )
    for collection in (
        snapshot.source_entries,
        snapshot.selected_entries,
        snapshot.application_entries,
        snapshot.audit_entries,
    ):
        for entry in collection:
            _validate_browser_dependency_tree_entry_shape(entry)
    excluded = {value[0] for value in _BROWSER_DEPENDENCY_SOURCE_EXTRAS}
    try:
        source_by_path = {
            entry.path: entry for entry in snapshot.source_entries
        }
        selected_by_path = {
            entry.path: entry for entry in snapshot.selected_entries
        }
    except (AttributeError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        ) from None
    if (
        len(source_by_path) != len(snapshot.source_entries)
        or len(selected_by_path) != len(snapshot.selected_entries)
        or set(selected_by_path) != set(source_by_path) - excluded
        or any(source_by_path[path] != entry for path, entry in selected_by_path.items())
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    for path, size, expected_sha256 in _BROWSER_DEPENDENCY_SOURCE_EXTRAS:
        entry = source_by_path.get(path)
        if (
            entry is None
            or entry.kind != "F"
            or entry.mode != 0o644
            or (entry.user_id, entry.group_id) != (0, 0)
            or entry.layer_sha256 != _BROWSER_APPLICATION_WINNING_LAYER
            or len(entry.content) != size
            or hashlib.sha256(entry.content).hexdigest() != expected_sha256
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle differs"
            )
    digest = hashlib.sha256(_BROWSER_DEPENDENCY_TREE_DOMAIN)
    regular_files = 0
    directories = 0
    regular_bytes = 0
    for entry in snapshot.selected_entries:
        if (
            type(entry) is not _BrowserDependencyTreeEntry
            or entry.path in excluded
            or _canonical_browser_dependency_relative_path(entry.path)
            != entry.path
            or entry.kind not in {"F", "D"}
            or (entry.user_id, entry.group_id) != (0, 0)
            or entry.layer_sha256 != _BROWSER_APPLICATION_WINNING_LAYER
            or (entry.kind == "D" and entry.content)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle differs"
            )
        if entry.kind == "F":
            regular_files += 1
            regular_bytes += len(entry.content)
            payload = hashlib.sha256(entry.content).digest()
        else:
            directories += 1
            payload = b""
        _update_browser_runtime_manifest(
            digest,
            kind=entry.kind.encode("ascii"),
            path=entry.path.encode("ascii"),
            mode=entry.mode,
            size=len(entry.content),
            payload=payload,
        )
    if (
        regular_files != _BROWSER_DEPENDENCY_TREE_FILES
        or directories != _BROWSER_DEPENDENCY_TREE_DIRECTORIES
        or regular_bytes != _BROWSER_DEPENDENCY_TREE_BYTES
        or digest.hexdigest() != _BROWSER_DEPENDENCY_TREE_SHA256
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    _browser_dependency_layer_provenance_sha256(snapshot.selected_entries)
    _browser_application_tree_sha256(
        dict((entry.path, entry) for entry in snapshot.application_entries)
    )
    expected_audits = _BROWSER_DEPENDENCY_BUILD_FILES[-2:]
    for entry, (source_path, source_bytes, source_sha256) in zip(
        snapshot.audit_entries,
        expected_audits,
        strict=True,
    ):
        if (
            type(entry) is not _BrowserDependencyTreeEntry
            or entry.path
            != source_path.relative_to(
                _BROWSER_DEPENDENCY_REPOSITORY_ROOT
            ).as_posix()
            or entry.kind != "F"
            or entry.mode != 0o644
            or (entry.user_id, entry.group_id) != (1000, 1000)
            or entry.layer_sha256 != source_sha256
            or len(entry.content) != source_bytes
            or hashlib.sha256(entry.content).hexdigest() != source_sha256
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle differs"
            )


def _collect_browser_runtime_bundle_entries(
    runtime: _BrowserPythonRuntimeSnapshot,
    dependency: _BrowserDependencyImageSnapshot,
) -> tuple[_BrowserRuntimeBundleEntry, ...]:
    """Map all exact sources into one canonical future execution namespace."""

    _validate_exact_browser_python_runtime_snapshot(runtime)
    _validate_exact_browser_dependency_snapshot(dependency)
    selected: list[_BrowserRuntimeBundleEntry] = list(runtime.entries)
    selected.extend(
        (
            _BrowserRuntimeBundleEntry(
                path="/runtime",
                kind="D",
                mode=dependency.application_root.mode,
                user_id=dependency.application_root.user_id,
                group_id=dependency.application_root.group_id,
                provenance=(
                    "application-layer:"
                    + dependency.application_root.layer_sha256
                ),
                content=b"",
            ),
            _BrowserRuntimeBundleEntry(
                path="/runtime/site-packages",
                kind="D",
                mode=dependency.dependency_root.mode,
                user_id=dependency.dependency_root.user_id,
                group_id=dependency.dependency_root.group_id,
                provenance=(
                    "dependency-layer:"
                    + dependency.dependency_root.layer_sha256
                ),
                content=b"",
            ),
        )
    )
    selected.extend(
        _BrowserRuntimeBundleEntry(
            path=f"/runtime/{entry.path}",
            kind=entry.kind,
            mode=entry.mode,
            user_id=entry.user_id,
            group_id=entry.group_id,
            provenance=f"application-layer:{entry.layer_sha256}",
            content=entry.content,
        )
        for entry in dependency.application_entries
    )
    selected.extend(
        _BrowserRuntimeBundleEntry(
            path=f"/runtime/site-packages/{entry.path}",
            kind=entry.kind,
            mode=entry.mode,
            user_id=entry.user_id,
            group_id=entry.group_id,
            provenance=f"dependency-layer:{entry.layer_sha256}",
            content=entry.content,
        )
        for entry in dependency.selected_entries
    )
    selected.extend(
        _BrowserRuntimeBundleEntry(
            path=f"/runtime/{entry.path}",
            kind=entry.kind,
            mode=entry.mode,
            user_id=entry.user_id,
            group_id=entry.group_id,
            provenance=f"audit-source:{entry.layer_sha256}",
            content=entry.content,
        )
        for entry in dependency.audit_entries
    )
    return _complete_browser_runtime_bundle_parents(
        tuple(sorted(selected, key=lambda entry: entry.path))
    )


def _validate_browser_runtime_bundle_entries(
    entries: tuple[_BrowserRuntimeBundleEntry, ...],
    *,
    require_parents: bool = True,
) -> tuple[_BrowserRuntimeBundleEntry, ...]:
    """Validate one canonical, provenance-bound execution-tree payload."""

    if (
        type(entries) is not tuple
        or not entries
        or len(entries) > _BROWSER_RUNTIME_BUNDLE_ENTRY_LIMIT
        or any(type(entry) is not _BrowserRuntimeBundleEntry for entry in entries)
        or type(require_parents) is not bool
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    paths: dict[str, _BrowserRuntimeBundleEntry] = {}
    folded: set[str] = set()
    payload_bytes = 0
    provenance_pattern = re.compile(
        r"(?:python-startup|python-stdlib|native-runtime|dependency-layer|"
        r"application-layer|audit-source|bundle-parent):[0-9a-f]{64}"
    )
    for entry in entries:
        if (
            type(entry.path) is not str
            or type(entry.kind) is not str
            or type(entry.provenance) is not str
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle differs"
            )
        try:
            encoded_path = entry.path.encode("ascii", errors="strict")
            encoded_provenance = entry.provenance.encode(
                "ascii", errors="strict"
            )
        except (AttributeError, UnicodeEncodeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle differs"
            ) from None
        components = entry.path.split("/")
        if (
            not entry.path.startswith("/")
            or entry.path == "/"
            or entry.path.endswith("/")
            or "//" in entry.path
            or any(component in {"", ".", ".."} for component in components[1:])
            or len(encoded_path) > _BROWSER_RUNTIME_PATH_BYTES_LIMIT
            or len(components) - 1 > _BROWSER_RUNTIME_TREE_DEPTH_LIMIT
            or any(
                len(component.encode("ascii"))
                > _BROWSER_RUNTIME_COMPONENT_BYTES_LIMIT
                for component in components[1:]
            )
            or any(value < 0x20 or value == 0x7F for value in encoded_path)
            or entry.kind not in {"F", "D", "L"}
            or type(entry.mode) is not int
            or entry.mode < 0
            or entry.mode > 0o7777
            or entry.mode & 0o7000
            or type(entry.user_id) is not int
            or entry.user_id < 0
            or entry.user_id > 4_294_967_295
            or type(entry.group_id) is not int
            or entry.group_id < 0
            or entry.group_id > 4_294_967_295
            or provenance_pattern.fullmatch(entry.provenance) is None
            or len(encoded_provenance) > 128
            or type(entry.content) is not bytes
            or (entry.kind == "D" and entry.content)
            or (entry.kind == "L" and not entry.content)
            or len(entry.content) > _BROWSER_RUNTIME_BUNDLE_BYTES_LIMIT
            or entry.path in paths
            or entry.path.casefold() in folded
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle differs"
            )
        paths[entry.path] = entry
        folded.add(entry.path.casefold())
        payload_bytes += len(entry.content)
        if payload_bytes > _BROWSER_RUNTIME_BUNDLE_BYTES_LIMIT:
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle differs"
            )
    ordered = tuple(sorted(entries, key=lambda entry: entry.path))
    for path, entry in paths.items():
        components = path.split("/")[1:]
        for index in range(1, len(components)):
            ancestor = "/" + "/".join(components[:index])
            if (
                (ancestor in paths and paths[ancestor].kind != "D")
                or (require_parents and ancestor not in paths)
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance browser runtime bundle differs"
                )
        if entry.kind != "L":
            continue
        selected_path = path
        seen: set[str] = set()
        while paths[selected_path].kind == "L":
            if selected_path in seen:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser runtime bundle differs"
                )
            seen.add(selected_path)
            selected_entry = paths[selected_path]
            try:
                target = selected_entry.content.decode("ascii", errors="strict")
            except UnicodeDecodeError:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser runtime bundle differs"
                ) from None
            if (
                len(selected_entry.content) > _BROWSER_RUNTIME_PATH_BYTES_LIMIT
                or not target
                or "\0" in target
                or any(
                    ord(value) < 0x20 or ord(value) == 0x7F
                    for value in target
                )
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance browser runtime bundle differs"
                )
            selected_path = posixpath.normpath(
                target
                if target.startswith("/")
                else posixpath.join(posixpath.dirname(selected_path), target)
            )
            if not selected_path.startswith("/") or selected_path not in paths:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser runtime bundle differs"
                )
    return ordered


def _complete_browser_runtime_bundle_parents(
    entries: tuple[_BrowserRuntimeBundleEntry, ...],
) -> tuple[_BrowserRuntimeBundleEntry, ...]:
    selected = list(
        _validate_browser_runtime_bundle_entries(
            entries,
            require_parents=False,
        )
    )
    paths = {entry.path for entry in selected}
    missing: set[str] = set()
    for entry in selected:
        components = entry.path.split("/")[1:]
        for index in range(1, len(components)):
            ancestor = "/" + "/".join(components[:index])
            if ancestor not in paths:
                missing.add(ancestor)
    selected.extend(
        _BrowserRuntimeBundleEntry(
            path=path,
            kind="D",
            mode=0o555,
            user_id=0,
            group_id=0,
            provenance=(
                "bundle-parent:" + _BROWSER_RUNTIME_PARENT_POLICY_SHA256
            ),
            content=b"",
        )
        for path in sorted(missing)
    )
    return _validate_browser_runtime_bundle_entries(
        tuple(sorted(selected, key=lambda entry: entry.path))
    )


def _encode_browser_runtime_bundle(
    entries: tuple[_BrowserRuntimeBundleEntry, ...],
) -> tuple[bytes, str, str, int]:
    selected = _validate_browser_runtime_bundle_entries(entries)
    manifest_entries: list[dict[str, object]] = []
    payload_parts: list[bytes] = []
    offset = 0
    regular_bytes = 0
    for entry in selected:
        size = len(entry.content)
        manifest_entries.append(
            {
                "group_id": entry.group_id,
                "kind": entry.kind,
                "mode": entry.mode,
                "offset": offset,
                "path": entry.path,
                "provenance": entry.provenance,
                "sha256": hashlib.sha256(entry.content).hexdigest(),
                "size": size,
                "user_id": entry.user_id,
            }
        )
        payload_parts.append(entry.content)
        offset += size
        if entry.kind == "F":
            regular_bytes += size
    manifest = json.dumps(
        {
            "entries": manifest_entries,
            "protocol": _BROWSER_RUNTIME_BUNDLE_PROTOCOL,
        },
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    if (
        not manifest
        or len(manifest) > _BROWSER_RUNTIME_BUNDLE_MANIFEST_LIMIT
        or offset > _BROWSER_RUNTIME_BUNDLE_BYTES_LIMIT
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    payload = b"".join(payload_parts)
    raw = len(manifest).to_bytes(8, "big") + manifest + payload
    return (
        raw,
        hashlib.sha256(manifest).hexdigest(),
        hashlib.sha256(
            _BROWSER_RUNTIME_BUNDLE_MAGIC + manifest + payload
        ).hexdigest(),
        regular_bytes,
    )


def _parse_browser_runtime_bundle(
    raw: bytes,
) -> tuple[tuple[_BrowserRuntimeBundleEntry, ...], str, str, int]:
    if (
        type(raw) is not bytes
        or len(raw) < 9
        or len(raw)
        > 8
        + _BROWSER_RUNTIME_BUNDLE_MANIFEST_LIMIT
        + _BROWSER_RUNTIME_BUNDLE_BYTES_LIMIT
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    manifest_bytes = int.from_bytes(raw[:8], "big")
    if (
        manifest_bytes <= 0
        or manifest_bytes > _BROWSER_RUNTIME_BUNDLE_MANIFEST_LIMIT
        or 8 + manifest_bytes > len(raw)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    manifest_raw = raw[8 : 8 + manifest_bytes]
    payload = raw[8 + manifest_bytes :]
    try:
        manifest = parse_single_json_object(manifest_raw)
        canonical = json.dumps(
            manifest,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        rows = manifest["entries"]
        if (
            canonical != manifest_raw
            or set(manifest) != {"entries", "protocol"}
            or manifest["protocol"] != _BROWSER_RUNTIME_BUNDLE_PROTOCOL
            or type(rows) is not list
            or not rows
            or len(rows) > _BROWSER_RUNTIME_BUNDLE_ENTRY_LIMIT
        ):
            raise ValueError
        selected: list[_BrowserRuntimeBundleEntry] = []
        expected_offset = 0
        regular_bytes = 0
        expected_keys = {
            "group_id",
            "kind",
            "mode",
            "offset",
            "path",
            "provenance",
            "sha256",
            "size",
            "user_id",
        }
        for row in rows:
            if type(row) is not dict or set(row) != expected_keys:
                raise ValueError
            integer_values = (
                row["group_id"],
                row["mode"],
                row["offset"],
                row["size"],
                row["user_id"],
            )
            if (
                any(type(value) is not int or value < 0 for value in integer_values)
                or row["offset"] != expected_offset
                or row["size"] > len(payload) - expected_offset
                or type(row["sha256"]) is not str
                or _SHA256_TEXT.fullmatch(row["sha256"]) is None
            ):
                raise ValueError
            content = payload[
                expected_offset : expected_offset + row["size"]
            ]
            if hashlib.sha256(content).hexdigest() != row["sha256"]:
                raise ValueError
            entry = _BrowserRuntimeBundleEntry(
                path=row["path"],
                kind=row["kind"],
                mode=row["mode"],
                user_id=row["user_id"],
                group_id=row["group_id"],
                provenance=row["provenance"],
                content=content,
            )
            selected.append(entry)
            expected_offset += row["size"]
            if row["kind"] == "F":
                regular_bytes += row["size"]
        if expected_offset != len(payload):
            raise ValueError
        entries = _validate_browser_runtime_bundle_entries(tuple(selected))
        if tuple(selected) != entries:
            raise ValueError
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
        UnicodeDecodeError,
        LocalStagingAcceptanceError,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        ) from None
    return (
        entries,
        hashlib.sha256(manifest_raw).hexdigest(),
        hashlib.sha256(
            _BROWSER_RUNTIME_BUNDLE_MAGIC + manifest_raw + payload
        ).hexdigest(),
        regular_bytes,
    )


def _update_browser_projection_digest(
    digest: Any,
    entry: _BrowserRuntimeBundleEntry,
) -> None:
    try:
        path = entry.path.encode("ascii", errors="strict")
        provenance = entry.provenance.encode("ascii", errors="strict")
        if entry.kind == "F":
            payload = hashlib.sha256(entry.content).digest()
        elif entry.kind == "L":
            payload = entry.content
        elif entry.kind == "D":
            payload = b""
        else:
            raise ValueError
        digest.update(entry.kind.encode("ascii"))
        digest.update(len(path).to_bytes(4, "big"))
        digest.update(path)
        digest.update(entry.mode.to_bytes(4, "big"))
        digest.update(entry.user_id.to_bytes(4, "big"))
        digest.update(entry.group_id.to_bytes(4, "big"))
        digest.update(len(provenance).to_bytes(4, "big"))
        digest.update(provenance)
        digest.update(len(entry.content).to_bytes(8, "big"))
        digest.update(len(payload).to_bytes(4, "big"))
        digest.update(payload)
    except (AttributeError, OverflowError, TypeError, UnicodeEncodeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection differs"
        ) from None


def _browser_projection_manifest_sha256(
    entries: tuple[_BrowserRuntimeBundleEntry, ...],
    *,
    domain: bytes = _BROWSER_PROJECTION_DOMAIN,
    expected_sha256: str | None = None,
) -> str:
    if (
        type(entries) is not tuple
        or not entries
        or type(domain) is not bytes
        or not domain
        or (
            expected_sha256 is not None
            and (
                type(expected_sha256) is not str
                or _SHA256_TEXT.fullmatch(expected_sha256) is None
            )
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection differs"
        )
    digest = hashlib.sha256(domain)
    for entry in sorted(entries, key=lambda selected: selected.path):
        if type(entry) is not _BrowserRuntimeBundleEntry:
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection differs"
            )
        _update_browser_projection_digest(digest, entry)
    observed = digest.hexdigest()
    if expected_sha256 is not None and observed != expected_sha256:
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection differs"
        )
    return observed


def _browser_projection_policy_sha256(
    entries: tuple[_BrowserRuntimeBundleEntry, ...],
    *,
    expected_sha256: str | None = None,
) -> str:
    if (
        type(entries) is not tuple
        or not entries
        or type(_BROWSER_PROJECTION_ENVIRONMENT) is not tuple
        or type(_BROWSER_PROJECTION_NEGATIVE_PATHS) is not tuple
        or tuple(sorted(_BROWSER_PROJECTION_ENVIRONMENT))
        != _BROWSER_PROJECTION_ENVIRONMENT
        or tuple(sorted(_BROWSER_PROJECTION_NEGATIVE_PATHS))
        != _BROWSER_PROJECTION_NEGATIVE_PATHS
        or len(dict(_BROWSER_PROJECTION_ENVIRONMENT))
        != len(_BROWSER_PROJECTION_ENVIRONMENT)
        or len(set(_BROWSER_PROJECTION_NEGATIVE_PATHS))
        != len(_BROWSER_PROJECTION_NEGATIVE_PATHS)
        or (
            expected_sha256 is not None
            and (
                type(expected_sha256) is not str
                or _SHA256_TEXT.fullmatch(expected_sha256) is None
            )
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection policy differs"
        )
    try:
        environment = []
        for name, value in _BROWSER_PROJECTION_ENVIRONMENT:
            if (
                type(name) is not str
                or type(value) is not str
                or not name
                or "\0" in name
                or "\0" in value
                or _BROWSER_PROJECTION_FORBIDDEN_ENVIRONMENT.fullmatch(name)
            ):
                raise ValueError
            name.encode("ascii", errors="strict")
            value.encode("ascii", errors="strict")
            environment.append([name, value])
        negative_paths = []
        for path in _BROWSER_PROJECTION_NEGATIVE_PATHS:
            if (
                type(path) is not str
                or not path.startswith("/")
                or path == "/"
                or "\0" in path
            ):
                raise ValueError
            path.encode("ascii", errors="strict")
            negative_paths.append(path)
        entry_sha256 = _browser_projection_manifest_sha256(
            entries,
            domain=_BROWSER_PROJECTION_POLICY_ENTRIES_DOMAIN,
        )
        policy = {
            "entries_sha256": entry_sha256,
            "environment": environment,
            "mount": {
                "filesystem": "tmpfs",
                "flags": ["nodev", "nosuid", "readonly"],
                "maximum_bytes": _BROWSER_PROJECTION_TMPFS_BYTES,
                "maximum_inodes": _BROWSER_PROJECTION_TMPFS_INODES,
                "private_recursive": True,
            },
            "negative_paths": negative_paths,
            "projected_owner": {"gid": 0, "uid": 0},
            "source_metadata_preserved_in_manifest": True,
        }
        raw = json.dumps(
            policy,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (
        AttributeError,
        TypeError,
        UnicodeEncodeError,
        ValueError,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection policy differs"
        ) from None
    digest = hashlib.sha256(_BROWSER_PROJECTION_POLICY_DOMAIN)
    digest.update(len(raw).to_bytes(8, "big"))
    digest.update(raw)
    observed = digest.hexdigest()
    if expected_sha256 is not None and observed != expected_sha256:
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection policy differs"
        )
    return observed


def _browser_projection_policy_entries(
) -> tuple[_BrowserRuntimeBundleEntry, ...]:
    selected = tuple(
        _BrowserRuntimeBundleEntry(
            path=entry.path,
            kind=entry.kind,
            mode=entry.mode,
            user_id=0,
            group_id=0,
            provenance=(
                "projection-policy:" + _BROWSER_PROJECTION_POLICY_ID_SHA256
            ),
            content=entry.content,
        )
        for entry in _BROWSER_PROJECTION_POLICY_ENTRIES
    )
    if (
        tuple(entry.path for entry in selected)
        != tuple(sorted(entry.path for entry in selected))
        or len({entry.path for entry in selected}) != len(selected)
        or any(
            entry.kind not in {"F", "D"}
            or type(entry.mode) is not int
            or entry.mode < 0
            or entry.mode > 0o1777
            or entry.user_id != 0
            or entry.group_id != 0
            or type(entry.content) is not bytes
            or (entry.kind == "D" and entry.content)
            for entry in selected
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection policy differs"
        )
    _browser_projection_policy_sha256(
        selected,
        expected_sha256=_BROWSER_PROJECTION_POLICY_SHA256,
    )
    return selected


def _browser_projection_entries(
    source_entries: tuple[_BrowserRuntimeBundleEntry, ...],
) -> tuple[_BrowserRuntimeBundleEntry, ...]:
    source = _validate_browser_runtime_bundle_entries(source_entries)
    policy = _browser_projection_policy_entries()
    source_paths = {entry.path for entry in source}
    policy_paths = {entry.path for entry in policy}
    negative = set(_BROWSER_PROJECTION_NEGATIVE_PATHS)
    if (
        source_paths & policy_paths
        or source_paths & negative
        or policy_paths & negative
        or any("glibc-hwcaps" in entry.path.split("/") for entry in source)
        or "/runtime/site-packages/certifi/cacert.pem" not in source_paths
        or tuple(sorted(_BROWSER_PROJECTION_ENVIRONMENT))
        != _BROWSER_PROJECTION_ENVIRONMENT
        or len(dict(_BROWSER_PROJECTION_ENVIRONMENT))
        != len(_BROWSER_PROJECTION_ENVIRONMENT)
        or any(
            _BROWSER_PROJECTION_FORBIDDEN_ENVIRONMENT.fullmatch(name)
            for name, _ in _BROWSER_PROJECTION_ENVIRONMENT
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection differs"
        )
    combined = tuple(sorted(source + policy, key=lambda entry: entry.path))
    regular_bytes = sum(
        len(entry.content) for entry in combined if entry.kind == "F"
    )
    if (
        len(combined) != _BROWSER_PROJECTION_EXPECTED_ENTRIES
        or regular_bytes != _BROWSER_PROJECTION_EXPECTED_REGULAR_BYTES
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection differs"
        )
    _browser_projection_manifest_sha256(
        combined,
        expected_sha256=_BROWSER_PROJECTION_EXPECTED_SHA256,
    )
    return combined


def _browser_libc() -> Any:
    try:
        return ctypes.CDLL(None, use_errno=True)
    except (AttributeError, OSError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment syscall differs"
        ) from None


def _browser_mount(
    source: bytes | None,
    target: bytes,
    filesystem: bytes | None,
    flags: int,
    data: bytes | None,
) -> None:
    if (
        source is not None and type(source) is not bytes
    ) or type(target) is not bytes or not target or (
        filesystem is not None and type(filesystem) is not bytes
    ) or type(flags) is not int or flags < 0 or (
        data is not None and type(data) is not bytes
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment mount differs"
        )
    libc = _browser_libc()
    try:
        result = libc.mount(
            ctypes.c_char_p(source),
            ctypes.c_char_p(target),
            ctypes.c_char_p(filesystem),
            ctypes.c_ulong(flags),
            ctypes.c_char_p(data),
        )
    except (AttributeError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment mount differs"
        ) from None
    if result != 0:
        selected_errno = ctypes.get_errno()
        raise OSError(selected_errno, os.strerror(selected_errno))


def _browser_unmount(target: bytes, flags: int) -> None:
    if type(target) is not bytes or not target or type(flags) is not int:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment mount differs"
        )
    libc = _browser_libc()
    try:
        result = libc.umount2(ctypes.c_char_p(target), ctypes.c_int(flags))
    except (AttributeError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment mount differs"
        ) from None
    if result != 0:
        selected_errno = ctypes.get_errno()
        raise OSError(selected_errno, os.strerror(selected_errno))


def _browser_syscall(number: int, *arguments: object) -> int:
    if type(number) is not int or number <= 0:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment syscall differs"
        )
    libc = _browser_libc()
    try:
        result = int(libc.syscall(ctypes.c_long(number), *arguments))
    except (AttributeError, OverflowError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment syscall differs"
        ) from None
    if result < 0:
        selected_errno = ctypes.get_errno()
        raise OSError(selected_errno, os.strerror(selected_errno))
    return result


def _block_browser_projection_signals() -> frozenset[int]:
    try:
        if len(os.listdir("/proc/self/task")) != 1:
            raise OSError(errno.EBUSY, "projection process is multithreaded")
        previous = signal.pthread_sigmask(
            signal.SIG_BLOCK,
            _BROWSER_PROJECTION_MASKED_SIGNALS,
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection signal boundary differs"
        ) from None
    return frozenset(int(selected) for selected in previous)


def _restore_browser_projection_signals(previous: frozenset[int]) -> None:
    if (
        type(previous) is not frozenset
        or any(type(selected) is not int for selected in previous)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection signal boundary differs"
        )
    try:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection signal boundary differs"
        ) from None


def _open_detached_browser_projection_mount() -> tuple[int, int, int]:
    filesystem_descriptor = mount_descriptor = root_descriptor = -1
    try:
        previous_signals = _block_browser_projection_signals()
        try:
            filesystem_descriptor = _browser_syscall(
                _BROWSER_FSOPEN_SYSCALL,
                ctypes.c_char_p(b"tmpfs"),
                ctypes.c_uint(_BROWSER_FSOPEN_CLOEXEC),
            )
            for key, value in (
                (b"size", str(_BROWSER_PROJECTION_TMPFS_BYTES).encode("ascii")),
                (
                    b"nr_inodes",
                    str(_BROWSER_PROJECTION_TMPFS_INODES).encode("ascii"),
                ),
                (b"mode", b"0700"),
                (b"uid", b"0"),
                (b"gid", b"0"),
            ):
                _browser_syscall(
                    _BROWSER_FSCONFIG_SYSCALL,
                    ctypes.c_int(filesystem_descriptor),
                    ctypes.c_uint(_BROWSER_FSCONFIG_SET_STRING),
                    ctypes.c_char_p(key),
                    ctypes.c_char_p(value),
                    ctypes.c_int(0),
                )
            _browser_syscall(
                _BROWSER_FSCONFIG_SYSCALL,
                ctypes.c_int(filesystem_descriptor),
                ctypes.c_uint(_BROWSER_FSCONFIG_CMD_CREATE),
                ctypes.c_void_p(0),
                ctypes.c_void_p(0),
                ctypes.c_int(0),
            )
            mount_descriptor = _browser_syscall(
                _BROWSER_FSMOUNT_SYSCALL,
                ctypes.c_int(filesystem_descriptor),
                ctypes.c_uint(_BROWSER_FSMOUNT_CLOEXEC),
                ctypes.c_uint(0),
            )
            root_descriptor = os.open(
                ".",
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=mount_descriptor,
            )
        finally:
            _restore_browser_projection_signals(previous_signals)
        if (
            filesystem_descriptor <= 2
            or mount_descriptor <= 2
            or root_descriptor <= 2
            or _browser_fstatfs_magic(root_descriptor)
            != _BROWSER_TMPFS_MAGIC
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection mount differs"
            )
        return filesystem_descriptor, mount_descriptor, root_descriptor
    except BaseException as body_error:
        cleanup_errors: list[BaseException] = []
        descriptors = (
            root_descriptor,
            mount_descriptor,
            filesystem_descriptor,
        )
        root_descriptor = mount_descriptor = filesystem_descriptor = -1
        for descriptor in descriptors:
            if descriptor < 0:
                continue
            try:
                os.close(descriptor)
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection mount cleanup differs"
            ) from body_error
        raise


def _seal_detached_browser_projection_mount(mount_descriptor: int) -> None:
    class _MountAttributes(ctypes.Structure):
        _fields_ = (
            ("attr_set", ctypes.c_uint64),
            ("attr_clr", ctypes.c_uint64),
            ("propagation", ctypes.c_uint64),
            ("userns_fd", ctypes.c_uint64),
        )

    attributes = _MountAttributes(
        _BROWSER_MOUNT_ATTR_RDONLY
        | _BROWSER_MOUNT_ATTR_NOSUID
        | _BROWSER_MOUNT_ATTR_NODEV,
        0,
        0,
        0,
    )
    _browser_syscall(
        _BROWSER_MOUNT_SETATTR_SYSCALL,
        ctypes.c_int(mount_descriptor),
        ctypes.c_char_p(b""),
        ctypes.c_uint(_BROWSER_AT_EMPTY_PATH),
        ctypes.byref(attributes),
        ctypes.c_size_t(ctypes.sizeof(attributes)),
    )


def _attach_detached_browser_projection_mount(
    mount_descriptor: int,
    target_descriptor: int,
) -> None:
    _browser_syscall(
        _BROWSER_MOVE_MOUNT_SYSCALL,
        ctypes.c_int(mount_descriptor),
        ctypes.c_char_p(b""),
        ctypes.c_int(target_descriptor),
        ctypes.c_char_p(b""),
        ctypes.c_uint(
            _BROWSER_MOVE_MOUNT_F_EMPTY_PATH
            | _BROWSER_MOVE_MOUNT_T_EMPTY_PATH
        ),
    )


def _browser_prctl(option: int, value: int) -> None:
    if type(option) is not int or type(value) is not int:
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian hardening differs"
        )
    libc = _browser_libc()
    try:
        result = libc.prctl(
            ctypes.c_int(option),
            ctypes.c_ulong(value),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
        )
    except (AttributeError, OverflowError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian hardening differs"
        ) from None
    if result != 0:
        selected_errno = ctypes.get_errno()
        raise OSError(selected_errno, os.strerror(selected_errno))


def _browser_fstatfs_magic(descriptor: int) -> int:
    class _StatFs(ctypes.Structure):
        _fields_ = (
            ("f_type", ctypes.c_long),
            ("f_bsize", ctypes.c_long),
            ("f_blocks", ctypes.c_ulong),
            ("f_bfree", ctypes.c_ulong),
            ("f_bavail", ctypes.c_ulong),
            ("f_files", ctypes.c_ulong),
            ("f_ffree", ctypes.c_ulong),
            ("f_fsid", ctypes.c_int * 2),
            ("f_namelen", ctypes.c_long),
            ("f_frsize", ctypes.c_long),
            ("f_flags", ctypes.c_long),
            ("f_spare", ctypes.c_long * 4),
        )

    if type(descriptor) is not int or descriptor < 0:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment filesystem differs"
        )
    selected = _StatFs()
    libc = _browser_libc()
    try:
        result = libc.fstatfs(
            ctypes.c_int(descriptor),
            ctypes.byref(selected),
        )
    except (AttributeError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment filesystem differs"
        ) from None
    if result != 0:
        selected_errno = ctypes.get_errno()
        raise OSError(selected_errno, os.strerror(selected_errno))
    return int(selected.f_type) & 0xFFFFFFFFFFFFFFFF


def _write_browser_projection_file(
    root_descriptor: int,
    relative: str,
    content: bytes,
    mode: int,
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            relative,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | os.O_NOFOLLOW,
            0o600,
            dir_fd=root_descriptor,
        )
        offset = 0
        while offset < len(content):
            try:
                written = os.write(descriptor, content[offset:])
            except InterruptedError:
                continue
            if written <= 0:
                raise OSError(errno.EIO, "short projection write")
            offset += written
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, 0, 0)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != mode
            or (info.st_uid, info.st_gid) != (0, 0)
            or info.st_nlink != 1
            or info.st_size != len(content)
        ):
            raise OSError(errno.EIO, "projection file metadata differs")
    except (OSError, OverflowError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection write differs"
        ) from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _materialize_browser_projection_entries(
    root: Path,
    root_descriptor: int,
    entries: tuple[_BrowserRuntimeBundleEntry, ...],
) -> None:
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or type(root_descriptor) is not int
        or root_descriptor <= 2
        or type(entries) is not tuple
        or not entries
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection differs"
        )
    try:
        old_umask = os.umask(0o077)
        try:
            directories = tuple(
                sorted(
                    (entry for entry in entries if entry.kind == "D"),
                    key=lambda entry: (entry.path.count("/"), entry.path),
                )
            )
            for entry in directories:
                if entry.path == "/":
                    continue
                os.mkdir(entry.path[1:], 0o700, dir_fd=root_descriptor)
            for entry in entries:
                if entry.kind == "F":
                    _write_browser_projection_file(
                        root_descriptor,
                        entry.path[1:],
                        entry.content,
                        entry.mode,
                    )
                elif entry.kind == "L":
                    target = entry.content.decode("ascii", errors="strict")
                    os.symlink(
                        target,
                        entry.path[1:],
                        dir_fd=root_descriptor,
                    )
                    os.chown(
                        entry.path[1:],
                        0,
                        0,
                        dir_fd=root_descriptor,
                        follow_symlinks=False,
                    )
            for entry in reversed(directories):
                descriptor = (
                    os.dup(root_descriptor)
                    if entry.path == "/"
                    else os.open(
                        entry.path[1:],
                        os.O_RDONLY
                        | os.O_CLOEXEC
                        | os.O_DIRECTORY
                        | os.O_NOFOLLOW,
                        dir_fd=root_descriptor,
                    )
                )
                try:
                    os.fchmod(descriptor, entry.mode)
                    os.fchown(descriptor, 0, 0)
                finally:
                    os.close(descriptor)
        finally:
            os.umask(old_umask)
    except LocalStagingAcceptanceError:
        raise
    except (
        OSError,
        OverflowError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection materialization differs"
        ) from None


def _browser_projection_inventory(root: Path) -> tuple[str, ...]:
    selected: list[str] = ["/"]

    def visit(path: Path, relative: str, depth: int) -> None:
        if depth > _BROWSER_RUNTIME_TREE_DEPTH_LIMIT:
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection inventory differs"
            )
        try:
            with os.scandir(path) as iterator:
                members = sorted(iterator, key=lambda item: item.name)
        except OSError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection inventory differs"
            ) from None
        for member in members:
            if (
                not member.name
                or member.name in {".", ".."}
                or "/" in member.name
                or "\0" in member.name
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance browser projection inventory differs"
                )
            child_relative = (
                f"{relative}/{member.name}" if relative else f"/{member.name}"
            )
            selected.append(child_relative)
            try:
                if member.is_dir(follow_symlinks=False):
                    visit(Path(member.path), child_relative, depth + 1)
            except OSError:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser projection inventory differs"
                ) from None

    visit(root, "", 0)
    if len(selected) > _BROWSER_RUNTIME_BUNDLE_ENTRY_LIMIT:
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection inventory differs"
        )
    return tuple(selected)


def _validate_materialized_browser_projection(
    root: Path,
    root_descriptor: int,
    entries: tuple[_BrowserRuntimeBundleEntry, ...],
    *,
    generation: str,
    source_bundle_sha256: str,
    source_manifest_sha256: str,
) -> _BrowserProjectionObservation:
    expected = {entry.path: entry for entry in entries}
    inventory = _browser_projection_inventory(root)
    if len(inventory) != len(expected) or set(inventory) != set(expected):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection inventory differs"
        )
    regular_bytes = 0
    root_device = os.fstat(root_descriptor).st_dev
    for path in inventory:
        entry = expected[path]
        selected = root if path == "/" else root / path[1:]
        try:
            info = (
                os.fstat(root_descriptor)
                if path == "/"
                else selected.lstat()
            )
            if (
                info.st_dev != root_device
                or (info.st_uid, info.st_gid) != (0, 0)
                or stat.S_IMODE(info.st_mode) != entry.mode
            ):
                raise OSError
            if entry.kind == "D":
                if not stat.S_ISDIR(info.st_mode):
                    raise OSError
            elif entry.kind == "L":
                if (
                    not stat.S_ISLNK(info.st_mode)
                    or info.st_nlink != 1
                    or os.fsencode(os.readlink(selected)) != entry.content
                ):
                    raise OSError
            elif entry.kind == "F":
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_size != len(entry.content)
                ):
                    raise OSError
                descriptor = os.open(
                    path[1:],
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=root_descriptor,
                )
                try:
                    observed = bytearray()
                    offset = 0
                    while offset < len(entry.content):
                        block = os.pread(
                            descriptor,
                            min(_CHUNK_BYTES, len(entry.content) - offset),
                            offset,
                        )
                        if not block:
                            break
                        observed.extend(block)
                        offset += len(block)
                    if (
                        offset != len(entry.content)
                        or os.pread(descriptor, 1, offset)
                        or bytes(observed) != entry.content
                    ):
                        raise OSError
                finally:
                    os.close(descriptor)
                regular_bytes += len(entry.content)
            else:
                raise OSError
        except (OSError, OverflowError, TypeError, ValueError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection contents differ"
            ) from None
    for path in _BROWSER_PROJECTION_NEGATIVE_PATHS:
        try:
            os.lstat(root / path[1:])
        except FileNotFoundError:
            continue
        except OSError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection negative path differs"
            ) from None
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection negative path differs"
        )
    flags = os.fstatvfs(root_descriptor).f_flag
    if (
        _browser_fstatfs_magic(root_descriptor) != _BROWSER_TMPFS_MAGIC
        or flags & os.ST_RDONLY == 0
        or flags & os.ST_NOSUID == 0
        or flags & os.ST_NODEV == 0
        or flags & os.ST_NOEXEC != 0
        or regular_bytes != _BROWSER_PROJECTION_EXPECTED_REGULAR_BYTES
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection mount differs"
        )
    try:
        probe = os.open(
            ".write-probe",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
            dir_fd=root_descriptor,
        )
    except OSError as exc:
        if exc.errno != errno.EROFS:
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection is writable"
            ) from None
    else:
        os.close(probe)
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection is writable"
        )
    _, _, mount_id, _ = _read_browser_worker_fd_metadata(
        os.getpid(),
        root_descriptor,
    )
    projection_sha256 = _browser_projection_manifest_sha256(
        entries,
        expected_sha256=_BROWSER_PROJECTION_EXPECTED_SHA256,
    )
    return _BrowserProjectionObservation(
        protocol=_BROWSER_PROJECTION_PROTOCOL,
        generation=generation,
        source_bundle_sha256=source_bundle_sha256,
        source_manifest_sha256=source_manifest_sha256,
        policy_sha256=_BROWSER_PROJECTION_POLICY_SHA256,
        projection_sha256=projection_sha256,
        entries=len(entries),
        regular_bytes=regular_bytes,
        mount_id=mount_id,
        mount_read_only=True,
        mount_nosuid=True,
        mount_nodev=True,
        environment=_BROWSER_PROJECTION_ENVIRONMENT,
        execution_authority=False,
    )


def _materialize_browser_runtime_projection(
    bundle: _PinnedBrowserRuntimeBundle,
    root: Path,
    *,
    generation: str,
    expected_root_device: int,
    expected_root_inode: int,
) -> tuple[_BrowserProjectionObservation, int]:
    _validate_frozen_browser_runtime_bundle(bundle)
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or type(generation) is not str
        or _SHA256_TEXT.fullmatch(generation) is None
        or root.name != _BROWSER_PROJECTION_ROOT_PREFIX + generation
        or type(expected_root_device) is not int
        or type(expected_root_inode) is not int
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser projection root differs"
        )
    underlying_descriptor = filesystem_descriptor = -1
    mount_descriptor = root_descriptor = -1
    attach_attempted = False
    mounted_identity: tuple[int, int] | None = None
    previous_projection_signals = _block_browser_projection_signals()
    projection_signals_restored = False
    try:
        previous_signals = _block_browser_projection_signals()
        try:
            underlying_descriptor = os.open(
                root,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
        finally:
            _restore_browser_projection_signals(previous_signals)
        named_root = root.stat(follow_symlinks=False)
        held_root = os.fstat(underlying_descriptor)
        held_target = os.readlink(f"/proc/self/fd/{underlying_descriptor}")
        if (
            not stat.S_ISDIR(named_root.st_mode)
            or not stat.S_ISDIR(held_root.st_mode)
            or (named_root.st_dev, named_root.st_ino)
            != (expected_root_device, expected_root_inode)
            or (held_root.st_dev, held_root.st_ino)
            != (expected_root_device, expected_root_inode)
            or held_target != str(root)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection root differs"
            )
        raw = _read_browser_runtime_bundle_descriptor(
            bundle.descriptor,
            bundle.size,
        )
        source, manifest_sha256, bundle_sha256, regular_bytes = (
            _parse_browser_runtime_bundle(raw)
        )
        if (
            manifest_sha256 != bundle.manifest_sha256
            or bundle_sha256 != bundle.sha256
            or regular_bytes != bundle.regular_bytes
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection source differs"
            )
        entries = _browser_projection_entries(source)
        _browser_mount(
            None,
            b"/",
            None,
            _BROWSER_MS_REC | _BROWSER_MS_PRIVATE,
            None,
        )
        repeated_root = root.stat(follow_symlinks=False)
        repeated_held = os.fstat(underlying_descriptor)
        repeated_target = os.readlink(
            f"/proc/self/fd/{underlying_descriptor}"
        )
        if (
            (repeated_root.st_dev, repeated_root.st_ino)
            != (expected_root_device, expected_root_inode)
            or (repeated_held.st_dev, repeated_held.st_ino)
            != (expected_root_device, expected_root_inode)
            or repeated_target != str(root)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection root differs"
            )
        previous_signals = _block_browser_projection_signals()
        try:
            (
                filesystem_descriptor,
                mount_descriptor,
                root_descriptor,
            ) = _open_detached_browser_projection_mount()
        finally:
            _restore_browser_projection_signals(previous_signals)
        mounted_root = os.fstat(root_descriptor)
        mounted_identity = (mounted_root.st_dev, mounted_root.st_ino)
        if not stat.S_ISDIR(mounted_root.st_mode):
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection root differs"
            )
        projected_root = Path(f"/proc/self/fd/{root_descriptor}")
        _materialize_browser_projection_entries(
            projected_root,
            root_descriptor,
            entries,
        )
        _seal_detached_browser_projection_mount(mount_descriptor)
        observation = _validate_materialized_browser_projection(
            projected_root,
            root_descriptor,
            entries,
            generation=generation,
            source_bundle_sha256=bundle.sha256,
            source_manifest_sha256=bundle.manifest_sha256,
        )
        final_underlying_named = root.stat(follow_symlinks=False)
        final_underlying_held = os.fstat(underlying_descriptor)
        if (
            (final_underlying_named.st_dev, final_underlying_named.st_ino)
            != (expected_root_device, expected_root_inode)
            or (final_underlying_held.st_dev, final_underlying_held.st_ino)
            != (expected_root_device, expected_root_inode)
            or os.readlink(f"/proc/self/fd/{underlying_descriptor}")
            != str(root)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection root differs"
            )
        attach_attempted = True
        _attach_detached_browser_projection_mount(
            mount_descriptor,
            underlying_descriptor,
        )
        final_named = root.stat(follow_symlinks=False)
        final_held = os.fstat(root_descriptor)
        if (
            (final_named.st_dev, final_named.st_ino)
            != (final_held.st_dev, final_held.st_ino)
            or os.readlink(f"/proc/self/fd/{root_descriptor}") != str(root)
            or os.readlink(f"/proc/self/fd/{underlying_descriptor}")
            != str(root)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection root differs"
            )
        previous_signals = _block_browser_projection_signals()
        try:
            selected_descriptor = mount_descriptor
            mount_descriptor = -1
            os.close(selected_descriptor)
            selected_descriptor = filesystem_descriptor
            filesystem_descriptor = -1
            os.close(selected_descriptor)
            selected_descriptor = underlying_descriptor
            underlying_descriptor = -1
            os.close(selected_descriptor)
        finally:
            _restore_browser_projection_signals(previous_signals)
        _restore_browser_projection_signals(previous_projection_signals)
        projection_signals_restored = True
        return observation, root_descriptor
    except BaseException as body_error:
        cleanup_errors: list[BaseException] = []
        if attach_attempted:
            unmounted = False
            for _ in range(2):
                try:
                    if root_descriptor < 0 or mounted_identity is None:
                        raise LocalStagingAcceptanceError(
                            "local acceptance browser projection mount ownership differs"
                        )
                    held = os.fstat(root_descriptor)
                    if (
                        (held.st_dev, held.st_ino) != mounted_identity
                        or _browser_fstatfs_magic(root_descriptor)
                        != _BROWSER_TMPFS_MAGIC
                    ):
                        raise LocalStagingAcceptanceError(
                            "local acceptance browser projection mount ownership differs"
                        )
                    _browser_unmount(
                        f"/proc/self/fd/{root_descriptor}".encode("ascii"),
                        _BROWSER_MNT_DETACH,
                    )
                    unmounted = True
                    break
                except BaseException as exc:
                    cleanup_errors.append(exc)
                    detached = False
                    if isinstance(exc, OSError) and exc.errno == errno.EINVAL:
                        try:
                            named = root.stat(follow_symlinks=False)
                            held_target = os.fstat(underlying_descriptor)
                            detached = (
                                (named.st_dev, named.st_ino)
                                == (expected_root_device, expected_root_inode)
                                and (held_target.st_dev, held_target.st_ino)
                                == (expected_root_device, expected_root_inode)
                                and _browser_fstatfs_magic(
                                    underlying_descriptor
                                )
                                != _BROWSER_TMPFS_MAGIC
                            )
                        except BaseException:
                            detached = False
                    if detached:
                        unmounted = True
                        break
            if not unmounted:
                cleanup_errors.append(
                    LocalStagingAcceptanceError(
                        "local acceptance browser projection mount cleanup differs"
                    )
                )
        descriptors = (
            root_descriptor,
            mount_descriptor,
            filesystem_descriptor,
            underlying_descriptor,
        )
        root_descriptor = mount_descriptor = filesystem_descriptor = -1
        underlying_descriptor = -1
        for descriptor in descriptors:
            if descriptor < 0:
                continue
            try:
                os.close(descriptor)
            except BaseException as exc:
                cleanup_errors.append(exc)
        if not projection_signals_restored:
            try:
                _restore_browser_projection_signals(
                    previous_projection_signals
                )
            except BaseException as exc:
                projection_signals_restored = True
                cleanup_errors.append(exc)
            else:
                projection_signals_restored = True
        if cleanup_errors:
            raise LocalStagingAcceptanceError(
                "local acceptance browser projection and cleanup differ"
            ) from body_error
        raise


def _write_browser_guardian_frame(
    descriptor: int,
    value: Mapping[str, Any],
    *,
    deadline: float,
) -> None:
    _require_browser_channel_deadline(deadline)
    framed = encode_browser_worker_frame(
        value,
        maximum_bytes=_BROWSER_PROJECTION_STATUS_LIMIT,
    )
    _browser_guardian_pipe_identity(descriptor, os.O_WRONLY)
    os.set_blocking(descriptor, False)
    _write_exact_browser_pipe(descriptor, framed, deadline)


def _read_browser_guardian_frame(
    descriptor: int,
    *,
    deadline: float,
) -> Mapping[str, Any]:
    _require_browser_channel_deadline(deadline)
    _browser_guardian_pipe_identity(descriptor, os.O_RDONLY)
    os.set_blocking(descriptor, False)
    header = _read_exact_browser_pipe(descriptor, 4, deadline)
    declared = int.from_bytes(header, "big")
    if declared <= 0 or declared > _BROWSER_PROJECTION_STATUS_LIMIT:
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian frame differs"
        )
    body = _read_exact_browser_pipe(descriptor, declared, deadline)
    return decode_browser_worker_frame(
        header + body,
        maximum_bytes=_BROWSER_PROJECTION_STATUS_LIMIT,
    )


def _read_browser_guardian_armed_and_rearm(
    descriptor: int,
    supervisor_pidfd: int,
    *,
    expected_parent: int,
    expected_pidfd_device: int,
    expected_pidfd_inode: int,
    generation: str,
    deadline: float,
) -> None:
    _require_browser_channel_deadline(deadline)
    if (
        type(expected_parent) is not int
        or expected_parent <= 1
        or type(expected_pidfd_device) is not int
        or type(expected_pidfd_inode) is not int
        or type(generation) is not str
        or _SHA256_TEXT.fullmatch(generation) is None
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite parent differs"
        )
    _browser_guardian_pipe_identity(descriptor, os.O_RDONLY)
    pidfd_info, bound_pid, _, _ = _read_browser_pidfd_metadata(
        supervisor_pidfd
    )
    if (
        bound_pid != expected_parent
        or (pidfd_info.st_dev, pidfd_info.st_ino)
        != (expected_pidfd_device, expected_pidfd_inode)
        or os.getppid() != expected_parent
        or _browser_pidfd_is_terminal(supervisor_pidfd)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite parent changed"
        )
    os.set_blocking(descriptor, False)

    def read_exact(count: int) -> bytes:
        observed = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_READ, "frame")
            selector.register(
                supervisor_pidfd,
                selectors.EVENT_READ,
                "supervisor",
            )
            while len(observed) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser prerequisite parent changed"
                    )
                try:
                    ready = selector.select(remaining)
                except InterruptedError:
                    continue
                except (OSError, ValueError):
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser prerequisite parent changed"
                    ) from None
                if not ready or any(
                    key.data == "supervisor" for key, _ in ready
                ):
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser prerequisite parent changed"
                    )
                try:
                    block = os.read(descriptor, count - len(observed))
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser guardian frame differs"
                    ) from None
                if not block:
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser guardian frame differs"
                    )
                observed.extend(block)
        return bytes(observed)

    header = read_exact(4)
    declared = int.from_bytes(header, "big")
    if declared <= 0 or declared > _BROWSER_PROJECTION_STATUS_LIMIT:
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian frame differs"
        )
    armed = decode_browser_worker_frame(
        header + read_exact(declared),
        maximum_bytes=_BROWSER_PROJECTION_STATUS_LIMIT,
    )
    if armed != {
        "generation": generation,
        "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
        "stage": "ARMED",
    }:
        raise LocalStagingAcceptanceError(
            "local acceptance browser private proc init differs"
        )
    _browser_prctl(_BROWSER_PR_SET_PDEATHSIG, int(signal.SIGKILL))
    repeated_info, repeated_pid, _, _ = _read_browser_pidfd_metadata(
        supervisor_pidfd
    )
    if (
        repeated_pid != expected_parent
        or (repeated_info.st_dev, repeated_info.st_ino)
        != (expected_pidfd_device, expected_pidfd_inode)
        or os.getppid() != expected_parent
        or _browser_pidfd_is_terminal(supervisor_pidfd)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite parent changed"
        )


def _capture_browser_guardian_pipe_identity(
    descriptor: int,
    access: int,
) -> tuple[int, int, int, bool]:
    _browser_guardian_pipe_identity(descriptor, access)
    try:
        info = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        close_on_exec = bool(
            fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        )
    except (OSError, OverflowError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian pipe differs"
        ) from None
    return (
        info.st_dev,
        info.st_ino,
        flags & ~os.O_NONBLOCK,
        close_on_exec,
    )


def _write_browser_guardian_attested_frame(
    descriptor: int,
    value: Mapping[str, Any],
    identity: tuple[int, int, int, bool],
    *,
    deadline: float,
) -> None:
    _require_browser_channel_deadline(deadline)
    if (
        type(identity) is not tuple
        or len(identity) != 4
        or any(type(selected) is not int for selected in identity[:3])
        or type(identity[3]) is not bool
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian pipe differs"
        )
    try:
        info = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        close_on_exec = bool(
            fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        )
    except (OSError, OverflowError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian pipe differs"
        ) from None
    if (
        not stat.S_ISFIFO(info.st_mode)
        or (
            info.st_dev,
            info.st_ino,
            flags & ~os.O_NONBLOCK,
            close_on_exec,
        )
        != identity
        or identity[2] != os.O_WRONLY
        or identity[3] is not True
        or os.get_inheritable(descriptor)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian pipe differs"
        )
    framed = encode_browser_worker_frame(
        value,
        maximum_bytes=_BROWSER_PROJECTION_STATUS_LIMIT,
    )
    os.set_blocking(descriptor, False)
    _write_exact_browser_pipe(descriptor, framed, deadline)


def _browser_guardian_pipe_identity(descriptor: int, access: int) -> None:
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        info = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        target = os.readlink(f"/proc/self/fd/{descriptor}")
        if (
            type(descriptor) is not int
            or descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
            or (
                soft_limit != resource.RLIM_INFINITY
                and descriptor >= soft_limit
            )
            or access not in {os.O_RDONLY, os.O_WRONLY}
            or not stat.S_ISFIFO(info.st_mode)
            or flags not in {access, access | os.O_NONBLOCK}
            or descriptor_flags & fcntl.FD_CLOEXEC == 0
            or os.get_inheritable(descriptor)
            or target != f"pipe:[{info.st_ino}]"
        ):
            raise OSError
    except (OSError, OverflowError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian descriptors differ"
        ) from None


def _close_browser_guardian_descriptors(allowed: frozenset[int]) -> None:
    if (
        type(allowed) is not frozenset
        or any(type(value) is not int or value < 0 for value in allowed)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian descriptors differ"
        )
    try:
        observed = tuple(
            int(value)
            for value in os.listdir("/proc/self/fd")
            if value.isascii() and value.isdigit()
        )
    except (OSError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian descriptors differ"
        ) from None
    for descriptor in observed:
        if descriptor in allowed:
            continue
        try:
            os.close(descriptor)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser guardian descriptors differ"
                ) from None


def _normalize_browser_guardian_stdio() -> None:
    read_descriptor = write_descriptor = -1
    try:
        named = os.stat("/dev/null", follow_symlinks=False)
        read_descriptor = os.open(
            "/dev/null",
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        write_descriptor = os.open(
            "/dev/null",
            os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        read_info = os.fstat(read_descriptor)
        write_info = os.fstat(write_descriptor)
        if (
            not stat.S_ISCHR(named.st_mode)
            or not stat.S_ISCHR(read_info.st_mode)
            or not stat.S_ISCHR(write_info.st_mode)
            or (read_info.st_dev, read_info.st_ino, read_info.st_rdev)
            != (named.st_dev, named.st_ino, named.st_rdev)
            or (write_info.st_dev, write_info.st_ino, write_info.st_rdev)
            != (named.st_dev, named.st_ino, named.st_rdev)
        ):
            raise OSError(errno.EIO, "guardian null device differs")
        os.dup2(read_descriptor, 0, inheritable=False)
        os.dup2(write_descriptor, 1, inheritable=False)
        os.dup2(write_descriptor, 2, inheritable=False)
        for descriptor, access in (
            (0, os.O_RDONLY),
            (1, os.O_WRONLY),
            (2, os.O_WRONLY),
        ):
            info = os.fstat(descriptor)
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
            if (
                (info.st_dev, info.st_ino, info.st_rdev)
                != (named.st_dev, named.st_ino, named.st_rdev)
                or flags & os.O_ACCMODE != access
                or fcntl.fcntl(descriptor, fcntl.F_GETFD)
                & fcntl.FD_CLOEXEC
                == 0
                or os.get_inheritable(descriptor)
            ):
                raise OSError(errno.EIO, "guardian standard descriptor differs")
    except (OSError, OverflowError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian standard descriptors differ"
        ) from None
    finally:
        for descriptor in (read_descriptor, write_descriptor):
            if descriptor > 2:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _probe_browser_clone3_policy() -> int:
    try:
        _browser_syscall(
            _BROWSER_CLONE3_SYSCALL,
            ctypes.c_void_p(0),
            ctypes.c_size_t(0),
        )
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.EPERM}:
            return exc.errno
        raise LocalStagingAcceptanceError(
            "local acceptance browser clone3 is reachable without an approved path"
        ) from None
    raise LocalStagingAcceptanceError(
        "local acceptance browser clone3 probe unexpectedly created a process"
    )


def _write_browser_user_namespace_maps(
    process_id: int,
    *,
    pidfd: int,
    expected_start_ticks: int,
) -> None:
    if (
        type(process_id) is not int
        or process_id <= 1
        or type(pidfd) is not int
        or pidfd <= 2
        or type(expected_start_ticks) is not int
        or expected_start_ticks <= 0
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser user namespace differs"
        )
    proc_descriptor = -1
    selected = (
        ("setgroups", b"deny\n"),
        ("uid_map", f"0 {os.geteuid()} 1\n".encode("ascii")),
        ("gid_map", f"0 {os.getegid()} 1\n".encode("ascii")),
    )
    try:
        pidfd_info, bound_pid, _, _ = _read_browser_pidfd_metadata(pidfd)
        before = _read_browser_worker_proc_stat(process_id)
        proc_descriptor = os.open(
            f"/proc/{process_id}",
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        held = os.fstat(proc_descriptor)
        named = os.stat(
            f"/proc/{process_id}",
            follow_symlinks=False,
        )
        if (
            bound_pid != process_id
            or _browser_pidfd_is_terminal(pidfd)
            or before.start_ticks != expected_start_ticks
            or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino)
            or not stat.S_ISDIR(held.st_mode)
            or pidfd_info.st_nlink == 0
        ):
            raise OSError(errno.ESRCH, "namespace owner changed")
        for name, raw in selected:
            descriptor = -1
            try:
                descriptor = os.open(
                    name,
                    os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=proc_descriptor,
                )
                offset = 0
                while offset < len(raw):
                    written = os.write(descriptor, raw[offset:])
                    if written <= 0:
                        raise OSError(errno.EIO, "short namespace-map write")
                    offset += written
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        after = _read_browser_worker_proc_stat(process_id)
        if (
            after.start_ticks != expected_start_ticks
            or _browser_pidfd_is_terminal(pidfd)
        ):
            raise OSError(errno.ESRCH, "namespace owner changed")
    except (
        LocalStagingAcceptanceError,
        OSError,
        OverflowError,
        TypeError,
        ValueError,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser user namespace differs"
        ) from None
    finally:
        if proc_descriptor >= 0:
            try:
                os.close(proc_descriptor)
            except OSError:
                pass


def _attempt_private_browser_procfs() -> tuple[str, int]:
    filesystem_descriptor = -1
    mount_descriptor = -1
    try:
        try:
            filesystem_descriptor = _browser_syscall(
                _BROWSER_FSOPEN_SYSCALL,
                ctypes.c_char_p(b"proc"),
                ctypes.c_uint(_BROWSER_FSOPEN_CLOEXEC),
            )
        except OSError as exc:
            return "fsopen", exc.errno
        try:
            _browser_syscall(
                _BROWSER_FSCONFIG_SYSCALL,
                ctypes.c_int(filesystem_descriptor),
                ctypes.c_uint(_BROWSER_FSCONFIG_CMD_CREATE),
                ctypes.c_void_p(0),
                ctypes.c_void_p(0),
                ctypes.c_int(0),
            )
        except OSError as exc:
            return "fsconfig", exc.errno
        try:
            mount_descriptor = _browser_syscall(
                _BROWSER_FSMOUNT_SYSCALL,
                ctypes.c_int(filesystem_descriptor),
                ctypes.c_uint(_BROWSER_FSMOUNT_CLOEXEC),
                ctypes.c_uint(0),
            )
        except OSError as exc:
            fsmount_errno = exc.errno
            try:
                _browser_mount(
                    b"proc",
                    b"/proc",
                    b"proc",
                    _BROWSER_MS_NOSUID
                    | _BROWSER_MS_NODEV
                    | _BROWSER_MS_NOEXEC,
                    None,
                )
            except OSError as legacy_exc:
                if (
                    fsmount_errno == _BROWSER_PRIVATE_PROC_BLOCKER_ERRNO
                    and legacy_exc.errno
                    == _BROWSER_PRIVATE_PROC_BLOCKER_ERRNO
                ):
                    return (
                        _BROWSER_PRIVATE_PROC_BLOCKER_STAGE,
                        _BROWSER_PRIVATE_PROC_BLOCKER_ERRNO,
                    )
                return "legacy-mount", legacy_exc.errno
            try:
                _browser_unmount(b"/proc", _BROWSER_MNT_DETACH)
            except OSError:
                return "legacy-mount-cleanup", errno.EIO
            return "legacy-mounted", 0
        try:
            _browser_syscall(
                _BROWSER_MOVE_MOUNT_SYSCALL,
                ctypes.c_int(mount_descriptor),
                ctypes.c_char_p(b""),
                ctypes.c_int(_BROWSER_AT_FDCWD),
                ctypes.c_char_p(b"/proc"),
                ctypes.c_uint(_BROWSER_MOVE_MOUNT_F_EMPTY_PATH),
            )
        except OSError as exc:
            return "move_mount", exc.errno
        return "mounted", 0
    finally:
        for descriptor in (mount_descriptor, filesystem_descriptor):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _run_browser_private_proc_init(
    result_write: int,
    result_identity: tuple[int, int, int, bool],
    gate_read: int,
    guardian_pidfd: int,
    generation: str,
) -> None:
    deadline = time.monotonic() + _BROWSER_CONTAINMENT_TIMEOUT_SECONDS
    try:
        _browser_prctl(_BROWSER_PR_SET_PDEATHSIG, int(signal.SIGKILL))
        if (
            (os.getpid(), os.getppid()) != (1, 0)
            or _browser_pidfd_is_terminal(guardian_pidfd)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser private proc init differs"
            )
        os.unshare(os.CLONE_NEWNS)
        _browser_mount(
            None,
            b"/",
            None,
            _BROWSER_MS_REC | _BROWSER_MS_PRIVATE,
            None,
        )
        _close_browser_guardian_descriptors(
            frozenset(
                (0, 1, 2, result_write, gate_read, guardian_pidfd)
            )
        )
        if _browser_pidfd_is_terminal(guardian_pidfd):
            raise LocalStagingAcceptanceError(
                "local acceptance browser private proc guardian changed"
            )
        _write_browser_guardian_attested_frame(
            result_write,
            {
                "generation": generation,
                "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
                "stage": "ARMED",
            },
            result_identity,
            deadline=deadline,
        )
        armed = _read_browser_guardian_frame(
            gate_read,
            deadline=time.monotonic() + _BROWSER_CONTAINMENT_TIMEOUT_SECONDS,
        )
        if armed != {
            "action": "PROBE",
            "generation": generation,
            "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
        } or _browser_pidfd_is_terminal(guardian_pidfd):
            raise LocalStagingAcceptanceError(
                "local acceptance browser private proc guardian changed"
            )
        os.close(gate_read)
        gate_read = -1
        os.close(guardian_pidfd)
        guardian_pidfd = -1
        stage, selected_errno = _attempt_private_browser_procfs()
        _write_browser_guardian_attested_frame(
            result_write,
            {
                "errno": selected_errno,
                "generation": generation,
                "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
                "stage": stage,
            },
            result_identity,
            deadline=deadline,
        )
    except BaseException:
        try:
            _write_browser_guardian_attested_frame(
                result_write,
                {
                    "errno": errno.EPROTO,
                    "generation": generation,
                    "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
                    "stage": "inner-error",
                },
                result_identity,
                deadline=time.monotonic()
                + _BROWSER_GUARDIAN_CLEANUP_TIMEOUT_SECONDS,
            )
        except BaseException:
            pass
    finally:
        for descriptor in (gate_read, guardian_pidfd, result_write):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        os._exit(0)


def _wait_browser_child_bounded(process_id: int, *, deadline: float) -> int:
    if (
        type(process_id) is not int
        or process_id <= 1
        or type(deadline) is not float
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser child cleanup differs"
        )
    while time.monotonic() < deadline:
        try:
            waited, wait_status = os.waitpid(process_id, os.WNOHANG)
        except (ChildProcessError, OSError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser child cleanup differs"
            ) from None
        if waited == process_id:
            return wait_status
        if waited != 0:
            raise LocalStagingAcceptanceError(
                "local acceptance browser child cleanup differs"
            )
        time.sleep(0.005)
    raise LocalStagingAcceptanceError(
        "local acceptance browser child cleanup timed out"
    )


def _run_browser_private_proc_guardian(
    generation: str,
    expected_parent: int,
    gate_read: int,
    status_write: int,
) -> None:
    deadline = time.monotonic() + _BROWSER_CONTAINMENT_TIMEOUT_SECONDS
    stage = "hardening"
    guardian_pidfd = supervisor_pidfd = -1
    supervisor_pidfd_device = supervisor_pidfd_inode = -1
    parent_death_signal_armed = True
    result_read = result_write = child_gate_read = child_gate_write = -1
    child = -1
    try:
        for selected_signal in signal.valid_signals():
            if selected_signal in {signal.SIGKILL, signal.SIGSTOP}:
                continue
            try:
                signal.signal(selected_signal, signal.SIG_DFL)
            except (OSError, RuntimeError, ValueError):
                pass
        _browser_prctl(_BROWSER_PR_SET_PDEATHSIG, int(signal.SIGKILL))
        if os.getppid() != expected_parent:
            raise LocalStagingAcceptanceError(
                "local acceptance browser prerequisite parent changed"
            )
        _browser_prctl(_BROWSER_PR_SET_DUMPABLE, 0)
        _browser_prctl(_BROWSER_PR_SET_NO_NEW_PRIVS, 1)
        os.setsid()
        os.environ.clear()
        _normalize_browser_guardian_stdio()
        _close_browser_guardian_descriptors(
            frozenset((0, 1, 2, gate_read, status_write))
        )
        _write_browser_guardian_frame(
            status_write,
            {
                "descriptors": [
                    [descriptor, os.readlink(f"/proc/self/fd/{descriptor}")]
                    for descriptor in sorted((0, 1, 2, gate_read, status_write))
                ],
                "environment_entries": len(os.environ),
                "generation": generation,
                "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
                "stage": "HARDENED",
            },
            deadline=deadline,
        )
        os.kill(os.getpid(), signal.SIGSTOP)
        deadline = time.monotonic() + _BROWSER_CONTAINMENT_TIMEOUT_SECONDS
        start = _read_browser_guardian_frame(gate_read, deadline=deadline)
        if start != {
            "action": "START",
            "generation": generation,
            "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
        }:
            raise LocalStagingAcceptanceError(
                "local acceptance browser prerequisite generation differs"
            )
        stage = "userns"
        _browser_prctl(_BROWSER_PR_SET_DUMPABLE, 1)
        os.unshare(
            os.CLONE_NEWUSER
            | os.CLONE_NEWNS
            | os.CLONE_NEWPID
            | os.CLONE_NEWNET
        )
        _write_browser_guardian_frame(
            status_write,
            {
                "generation": generation,
                "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
                "stage": "USERNS",
            },
            deadline=deadline,
        )
        deadline = time.monotonic() + _BROWSER_CONTAINMENT_TIMEOUT_SECONDS
        mapped = _read_browser_guardian_frame(gate_read, deadline=deadline)
        if mapped != {
            "action": "MAPPED",
            "generation": generation,
            "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
        } or (os.geteuid(), os.getegid(), tuple(os.getgroups())) != (0, 0, ()):
            raise LocalStagingAcceptanceError(
                "local acceptance browser prerequisite mapping differs"
            )
        _browser_prctl(_BROWSER_PR_SET_DUMPABLE, 0)
        _browser_mount(
            None,
            b"/",
            None,
            _BROWSER_MS_REC | _BROWSER_MS_PRIVATE,
            None,
        )
        stage = "private-proc"
        guardian_pidfd = os.pidfd_open(os.getpid(), 0)
        if guardian_pidfd <= 2 or _browser_pidfd_is_terminal(guardian_pidfd):
            raise LocalStagingAcceptanceError(
                "local acceptance browser prerequisite pidfd differs"
            )
        result_read, result_write = os.pipe2(os.O_CLOEXEC)
        child_gate_read, child_gate_write = os.pipe2(os.O_CLOEXEC)
        result_identity = _capture_browser_guardian_pipe_identity(
            result_write,
            os.O_WRONLY,
        )
        supervisor_pidfd = os.pidfd_open(expected_parent, 0)
        supervisor_info, supervisor_bound_pid, _, _ = (
            _read_browser_pidfd_metadata(supervisor_pidfd)
        )
        supervisor_pidfd_device = supervisor_info.st_dev
        supervisor_pidfd_inode = supervisor_info.st_ino
        if (
            supervisor_pidfd <= 2
            or supervisor_bound_pid != expected_parent
            or os.getppid() != expected_parent
            or _browser_pidfd_is_terminal(supervisor_pidfd)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser prerequisite parent changed"
            )
        _browser_prctl(_BROWSER_PR_SET_PDEATHSIG, 0)
        parent_death_signal_armed = False
        if (
            os.getppid() != expected_parent
            or _browser_pidfd_is_terminal(supervisor_pidfd)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser prerequisite parent changed"
            )
        child = os.fork()
        if child == 0:
            try:
                _browser_prctl(
                    _BROWSER_PR_SET_PDEATHSIG,
                    int(signal.SIGKILL),
                )
                os.close(supervisor_pidfd)
                supervisor_pidfd = -1
                os.close(result_read)
                os.close(child_gate_write)
                _run_browser_private_proc_init(
                    result_write,
                    result_identity,
                    child_gate_read,
                    guardian_pidfd,
                    generation,
                )
            finally:
                os._exit(127)
        os.close(result_write)
        result_write = -1
        os.close(child_gate_read)
        child_gate_read = -1
        _read_browser_guardian_armed_and_rearm(
            result_read,
            supervisor_pidfd,
            expected_parent=expected_parent,
            expected_pidfd_device=supervisor_pidfd_device,
            expected_pidfd_inode=supervisor_pidfd_inode,
            generation=generation,
            deadline=time.monotonic() + _BROWSER_CONTAINMENT_TIMEOUT_SECONDS,
        )
        parent_death_signal_armed = True
        os.close(supervisor_pidfd)
        supervisor_pidfd = -1
        os.close(guardian_pidfd)
        guardian_pidfd = -1
        _write_browser_guardian_frame(
            child_gate_write,
            {
                "action": "PROBE",
                "generation": generation,
                "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
            },
            deadline=time.monotonic() + _BROWSER_CONTAINMENT_TIMEOUT_SECONDS,
        )
        os.close(child_gate_write)
        child_gate_write = -1
        result = _read_browser_guardian_frame(
            result_read,
            deadline=time.monotonic() + _BROWSER_CONTAINMENT_TIMEOUT_SECONDS,
        )
        os.close(result_read)
        result_read = -1
        wait_status = _wait_browser_child_bounded(
            child,
            deadline=time.monotonic()
            + _BROWSER_GUARDIAN_CLEANUP_TIMEOUT_SECONDS,
        )
        child = -1
        if not os.WIFEXITED(wait_status) or os.WEXITSTATUS(wait_status) != 0:
            raise LocalStagingAcceptanceError(
                "local acceptance browser private proc init differs"
            )
        if (
            type(result) is not dict
            or set(result) != {"errno", "generation", "protocol", "stage"}
            or result["generation"] != generation
            or result["protocol"] != _BROWSER_CONTAINMENT_PROTOCOL
            or type(result["errno"]) is not int
            or type(result["stage"]) is not str
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser private proc evidence differs"
            )
        _write_browser_guardian_frame(
            status_write,
            result,
            deadline=time.monotonic() + _BROWSER_CONTAINMENT_TIMEOUT_SECONDS,
        )
    except BaseException:
        if not parent_death_signal_armed:
            try:
                _browser_prctl(
                    _BROWSER_PR_SET_PDEATHSIG,
                    int(signal.SIGKILL),
                )
            except BaseException:
                pass
        if child > 1:
            try:
                os.kill(child, signal.SIGKILL)
            except OSError:
                pass
            try:
                _wait_browser_child_bounded(
                    child,
                    deadline=time.monotonic()
                    + _BROWSER_GUARDIAN_CLEANUP_TIMEOUT_SECONDS,
                )
            except LocalStagingAcceptanceError:
                pass
        try:
            _write_browser_guardian_frame(
                status_write,
                {
                    "errno": errno.EPROTO,
                    "generation": generation,
                    "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
                    "stage": "guardian-error-" + stage,
                },
                deadline=time.monotonic()
                + _BROWSER_GUARDIAN_CLEANUP_TIMEOUT_SECONDS,
            )
        except BaseException:
            pass
    finally:
        for descriptor in (
            guardian_pidfd,
            supervisor_pidfd,
            result_read,
            result_write,
            child_gate_read,
            child_gate_write,
            gate_read,
            status_write,
        ):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        os._exit(0)


def _read_browser_runtime_bundle_descriptor(
    descriptor: int,
    size: int,
) -> bytes:
    if (
        type(descriptor) is not int
        or descriptor <= 2
        or descriptor > _BROWSER_WORKER_MAX_FD
        or type(size) is not int
        or size < 9
        or size
        > 8
        + _BROWSER_RUNTIME_BUNDLE_MANIFEST_LIMIT
        + _BROWSER_RUNTIME_BUNDLE_BYTES_LIMIT
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    selected = bytearray()
    offset = 0
    try:
        while offset < size:
            try:
                block = os.pread(
                    descriptor,
                    min(_CHUNK_BYTES, size - offset),
                    offset,
                )
            except InterruptedError:
                continue
            if not block:
                break
            selected.extend(block)
            offset += len(block)
        extra = os.pread(descriptor, 1, size)
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        ) from None
    if offset != size or extra:
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    return bytes(selected)


def _validate_pinned_browser_runtime_bundle(
    bundle: _PinnedBrowserRuntimeBundle,
) -> None:
    if (
        type(bundle) is not _PinnedBrowserRuntimeBundle
        or bundle._owner_token is not _BROWSER_HANDLE_TOKEN
        or (
            bundle._source_token is not None
            and bundle._source_token is not _BROWSER_FROZEN_RUNTIME_BUNDLE_TOKEN
        )
        or type(bundle.descriptor) is not int
        or bundle.descriptor <= 2
        or bundle.descriptor > _BROWSER_WORKER_MAX_FD
        or type(bundle.target) is not str
        or _BROWSER_RUNTIME_BUNDLE_MEMFD_TARGET.fullmatch(bundle.target)
        is None
        or type(bundle.device) is not int
        or type(bundle.inode) is not int
        or type(bundle.size) is not int
        or type(bundle.sha256) is not str
        or _SHA256_TEXT.fullmatch(bundle.sha256) is None
        or type(bundle.manifest_sha256) is not str
        or _SHA256_TEXT.fullmatch(bundle.manifest_sha256) is None
        or type(bundle.entries) is not int
        or bundle.entries <= 0
        or bundle.entries > _BROWSER_RUNTIME_BUNDLE_ENTRY_LIMIT
        or type(bundle.regular_bytes) is not int
        or bundle.regular_bytes < 0
        or bundle.execution_authority is not False
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        info = os.fstat(bundle.descriptor)
        target = os.readlink(f"/proc/self/fd/{bundle.descriptor}")
        descriptor_flags = fcntl.fcntl(bundle.descriptor, fcntl.F_GETFD)
        status_flags = fcntl.fcntl(bundle.descriptor, fcntl.F_GETFL)
        seals = fcntl.fcntl(bundle.descriptor, fcntl.F_GET_SEALS)
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        ) from None
    if (
        (soft_limit != resource.RLIM_INFINITY and bundle.descriptor >= soft_limit)
        or target != bundle.target
        or descriptor_flags & fcntl.FD_CLOEXEC == 0
        or os.get_inheritable(bundle.descriptor)
        or status_flags & os.O_ACCMODE != os.O_RDONLY
        or status_flags & os.O_NONBLOCK != 0
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 0
        or (info.st_uid, info.st_gid) != (os.geteuid(), os.getegid())
        or stat.S_IMODE(info.st_mode) != _BROWSER_RUNTIME_BUNDLE_MODE
        or seals != _BROWSER_RUNTIME_BUNDLE_SEALS
        or (info.st_dev, info.st_ino, info.st_size)
        != (bundle.device, bundle.inode, bundle.size)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )
    raw = _read_browser_runtime_bundle_descriptor(
        bundle.descriptor,
        bundle.size,
    )
    entries, manifest_sha256, bundle_sha256, regular_bytes = (
        _parse_browser_runtime_bundle(raw)
    )
    if (
        len(entries) != bundle.entries
        or manifest_sha256 != bundle.manifest_sha256
        or bundle_sha256 != bundle.sha256
        or regular_bytes != bundle.regular_bytes
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle differs"
        )


def _validate_frozen_browser_runtime_bundle(
    bundle: _PinnedBrowserRuntimeBundle,
) -> None:
    """Require the exact frozen aggregate; still grants no execution authority."""

    _validate_pinned_browser_runtime_bundle(bundle)
    if (
        bundle._source_token is not _BROWSER_FROZEN_RUNTIME_BUNDLE_TOKEN
        or bundle.entries != _BROWSER_RUNTIME_BUNDLE_EXPECTED_ENTRIES
        or bundle.regular_bytes
        != _BROWSER_RUNTIME_BUNDLE_EXPECTED_REGULAR_BYTES
        or bundle.size != _BROWSER_RUNTIME_BUNDLE_EXPECTED_BYTES
        or bundle.manifest_sha256
        != _BROWSER_RUNTIME_BUNDLE_EXPECTED_MANIFEST_SHA256
        or bundle.sha256 != _BROWSER_RUNTIME_BUNDLE_EXPECTED_SHA256
        or bundle.execution_authority is not False
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance frozen browser runtime bundle differs"
        )


def _close_browser_runtime_bundle_descriptor(
    descriptor: int,
    expected_target: str,
    identity: tuple[int, int] | None,
) -> bool:
    """Close only the still-owned memfd number; never a reused descriptor."""

    if (
        type(descriptor) is not int
        or descriptor < 0
        or type(expected_target) is not str
        or _BROWSER_RUNTIME_BUNDLE_MEMFD_TARGET.fullmatch(expected_target)
        is None
        or (
            identity is not None
            and (
                type(identity) is not tuple
                or len(identity) != 2
                or any(type(value) is not int for value in identity)
            )
        )
    ):
        return False
    try:
        target = os.readlink(f"/proc/self/fd/{descriptor}")
        info = os.fstat(descriptor)
    except OSError as exc:
        if exc.errno == errno.EBADF or not os.path.exists(
            f"/proc/self/fd/{descriptor}"
        ):
            return False
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle cleanup differs"
        ) from None
    except (OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser runtime bundle cleanup differs"
        ) from None
    if (
        target != expected_target
        or (
            identity is not None
            and (info.st_dev, info.st_ino) != identity
        )
    ):
        return False
    os.close(descriptor)
    return True


def _seal_browser_runtime_bundle(
    entries: tuple[_BrowserRuntimeBundleEntry, ...],
) -> _PinnedBrowserRuntimeBundle:
    """Seal supplied bytes without granting frozen-source or execution authority."""

    raw, manifest_sha256, bundle_sha256, regular_bytes = (
        _encode_browser_runtime_bundle(entries)
    )
    staging_descriptor = -1
    sealed_descriptor = -1
    staging_identity: tuple[int, int] | None = None
    sealed_identity: tuple[int, int] | None = None
    instance_name = (
        f"{_BROWSER_RUNTIME_BUNDLE_MEMFD_NAME_PREFIX}-"
        f"{os.urandom(16).hex()}"
    )
    expected_target = f"/memfd:{instance_name} (deleted)"
    try:
        staging_descriptor = os.memfd_create(
            instance_name,
            os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )
        staging_info = os.fstat(staging_descriptor)
        staging_identity = (staging_info.st_dev, staging_info.st_ino)
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if (
            staging_descriptor <= 2
            or staging_descriptor > _BROWSER_WORKER_MAX_FD
            or (
                soft_limit != resource.RLIM_INFINITY
                and staging_descriptor >= soft_limit
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle differs"
            )
        offset = 0
        while offset < len(raw):
            try:
                written = os.write(staging_descriptor, raw[offset:])
            except InterruptedError:
                continue
            if written <= 0:
                raise OSError
            offset += written
        os.fchmod(staging_descriptor, _BROWSER_RUNTIME_BUNDLE_MODE)
        fcntl.fcntl(
            staging_descriptor,
            fcntl.F_ADD_SEALS,
            _BROWSER_RUNTIME_BUNDLE_SEALS,
        )
        sealed_descriptor = os.open(
            f"/proc/self/fd/{staging_descriptor}",
            os.O_RDONLY | os.O_CLOEXEC,
        )
        sealed_info = os.fstat(sealed_descriptor)
        sealed_identity = (sealed_info.st_dev, sealed_info.st_ino)
        if (
            sealed_descriptor <= 2
            or sealed_descriptor > _BROWSER_WORKER_MAX_FD
            or (
                soft_limit != resource.RLIM_INFINITY
                and sealed_descriptor >= soft_limit
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle differs"
            )
        if not _close_browser_runtime_bundle_descriptor(
            staging_descriptor,
            expected_target,
            staging_identity,
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser runtime bundle cleanup differs"
            )
        staging_descriptor = -1
        info = os.fstat(sealed_descriptor)
        bundle = _PinnedBrowserRuntimeBundle(
            descriptor=sealed_descriptor,
            target=expected_target,
            device=info.st_dev,
            inode=info.st_ino,
            size=info.st_size,
            sha256=bundle_sha256,
            manifest_sha256=manifest_sha256,
            entries=len(entries),
            regular_bytes=regular_bytes,
            execution_authority=False,
        )
        bundle._owner_token = _BROWSER_HANDLE_TOKEN
        _validate_pinned_browser_runtime_bundle(bundle)
        return bundle
    except BaseException:
        if sealed_descriptor >= 0:
            try:
                _close_browser_runtime_bundle_descriptor(
                    sealed_descriptor,
                    expected_target,
                    sealed_identity,
                )
            except BaseException:
                pass
        if staging_descriptor >= 0:
            try:
                _close_browser_runtime_bundle_descriptor(
                    staging_descriptor,
                    expected_target,
                    staging_identity,
                )
            except BaseException:
                pass
        raise


def _read_browser_worker_runner_source() -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            _BROWSER_WORKER_RUNNER_SOURCE,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if (
            descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
            or (
                soft_limit != resource.RLIM_INFINITY
                and descriptor >= soft_limit
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser worker runner differs"
            )
        before = os.fstat(descriptor)
        named_before = _BROWSER_WORKER_RUNNER_SOURCE.stat(
            follow_symlinks=False
        )
        descriptor_target = os.readlink(f"/proc/self/fd/{descriptor}")
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        status_flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        selected = bytearray()
        while len(selected) <= _BROWSER_WORKER_RUNNER_BYTES:
            block = os.read(
                descriptor,
                _BROWSER_WORKER_RUNNER_BYTES + 1 - len(selected),
            )
            if not block:
                break
            selected.extend(block)
        after = os.fstat(descriptor)
        named_after = _BROWSER_WORKER_RUNNER_SOURCE.stat(
            follow_symlinks=False
        )
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    if (
        _BROWSER_WORKER_RUNNER_SOURCE.is_symlink()
        or descriptor_target != str(_BROWSER_WORKER_RUNNER_SOURCE)
        or descriptor_flags & fcntl.FD_CLOEXEC == 0
        or status_flags & os.O_ACCMODE != os.O_RDONLY
        or status_flags & os.O_NONBLOCK == 0
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or (before.st_uid, before.st_gid) != (os.geteuid(), os.getegid())
        or stat.S_IMODE(before.st_mode) != 0o644
        or identity
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        or identity
        != (
            named_before.st_dev,
            named_before.st_ino,
            named_before.st_size,
            named_before.st_mtime_ns,
        )
        or identity
        != (
            named_after.st_dev,
            named_after.st_ino,
            named_after.st_size,
            named_after.st_mtime_ns,
        )
        or len(selected) != _BROWSER_WORKER_RUNNER_BYTES
        or hashlib.sha256(selected).hexdigest()
        != _BROWSER_WORKER_RUNNER_SHA256
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        )
    return bytes(selected)


def _browser_worker_runner_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    selected = 0
    try:
        if os.lseek(descriptor, 0, os.SEEK_CUR) != 0:
            raise OSError
        while selected <= _BROWSER_WORKER_RUNNER_BYTES:
            block = os.read(
                descriptor,
                _BROWSER_WORKER_RUNNER_BYTES + 1 - selected,
            )
            if not block:
                break
            selected += len(block)
            digest.update(block)
        if os.lseek(descriptor, 0, os.SEEK_SET) != 0:
            raise OSError
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        ) from None
    if selected != _BROWSER_WORKER_RUNNER_BYTES:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        )
    return digest.hexdigest()


def _validate_pinned_browser_worker_runner(
    runner: PinnedBrowserWorkerRunner,
) -> None:
    if (
        type(runner) is not PinnedBrowserWorkerRunner
        or runner._owner_token is not _BROWSER_HANDLE_TOKEN
        or type(runner.descriptor) is not int
        or runner.descriptor <= 2
        or runner.descriptor > _BROWSER_WORKER_MAX_FD
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        )
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        info = os.fstat(runner.descriptor)
        target = os.readlink(f"/proc/self/fd/{runner.descriptor}")
        descriptor_flags = fcntl.fcntl(runner.descriptor, fcntl.F_GETFD)
        status_flags = fcntl.fcntl(runner.descriptor, fcntl.F_GETFL)
        seals = fcntl.fcntl(runner.descriptor, fcntl.F_GET_SEALS)
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        ) from None
    if (
        (
            soft_limit != resource.RLIM_INFINITY
            and runner.descriptor >= soft_limit
        )
        or target != _BROWSER_WORKER_RUNNER_MEMFD_TARGET
        or descriptor_flags & fcntl.FD_CLOEXEC == 0
        or os.get_inheritable(runner.descriptor)
        or status_flags & os.O_ACCMODE != os.O_RDONLY
        or status_flags & os.O_NONBLOCK != 0
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 0
        or (info.st_uid, info.st_gid) != (os.geteuid(), os.getegid())
        or stat.S_IMODE(info.st_mode) != _BROWSER_WORKER_RUNNER_MODE
        or seals != _BROWSER_WORKER_RUNNER_SEALS
        or info.st_size != _BROWSER_WORKER_RUNNER_BYTES
        or (info.st_dev, info.st_ino, info.st_size)
        != (runner.device, runner.inode, runner.size)
        or runner.sha256 != _BROWSER_WORKER_RUNNER_SHA256
        or _browser_worker_runner_sha256(runner.descriptor)
        != _BROWSER_WORKER_RUNNER_SHA256
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker runner differs"
        )


def open_pinned_browser_worker_runner() -> PinnedBrowserWorkerRunner:
    """Copy exact public worker bytes into a sealed read-only descriptor."""

    content = _read_browser_worker_runner_source()
    staging_descriptor = -1
    sealed_descriptor = -1
    try:
        staging_descriptor = os.memfd_create(
            _BROWSER_WORKER_RUNNER_MEMFD_NAME,
            os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if (
            staging_descriptor <= 2
            or staging_descriptor > _BROWSER_WORKER_MAX_FD
            or (
                soft_limit != resource.RLIM_INFINITY
                and staging_descriptor >= soft_limit
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser worker runner differs"
            )
        offset = 0
        while offset < len(content):
            written = os.write(staging_descriptor, content[offset:])
            if written <= 0:
                raise OSError
            offset += written
        os.fchmod(staging_descriptor, _BROWSER_WORKER_RUNNER_MODE)
        if os.lseek(staging_descriptor, 0, os.SEEK_SET) != 0:
            raise OSError
        fcntl.fcntl(
            staging_descriptor,
            fcntl.F_ADD_SEALS,
            _BROWSER_WORKER_RUNNER_SEALS,
        )
        sealed_descriptor = os.open(
            f"/proc/self/fd/{staging_descriptor}",
            os.O_RDONLY | os.O_CLOEXEC,
        )
        if (
            sealed_descriptor <= 2
            or sealed_descriptor > _BROWSER_WORKER_MAX_FD
            or (
                soft_limit != resource.RLIM_INFINITY
                and sealed_descriptor >= soft_limit
            )
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser worker runner differs"
            )
        os.close(staging_descriptor)
        staging_descriptor = -1
        info = os.fstat(sealed_descriptor)
        runner = PinnedBrowserWorkerRunner(
            descriptor=sealed_descriptor,
            device=info.st_dev,
            inode=info.st_ino,
            size=info.st_size,
            sha256=_BROWSER_WORKER_RUNNER_SHA256,
        )
        runner._owner_token = _BROWSER_HANDLE_TOKEN
        _validate_pinned_browser_worker_runner(runner)
        return runner
    except BaseException:
        if sealed_descriptor >= 0:
            os.close(sealed_descriptor)
        if staging_descriptor >= 0:
            os.close(staging_descriptor)
        raise


def build_browser_worker_launch(
    runtime: PinnedBrowserPythonExecutable,
    runner: PinnedBrowserWorkerRunner,
    arguments: BrowserWorkerArguments,
) -> BrowserWorkerLaunch:
    """Build an exact inert spec; spawning remains intentionally unavailable."""

    _validate_pinned_browser_python_executable(runtime)
    _validate_pinned_browser_worker_runner(runner)
    validated_arguments = validate_browser_worker_descriptors(arguments)
    channel_descriptors = (
        validated_arguments.request_descriptor,
        validated_arguments.ready_descriptor,
        validated_arguments.secret_descriptor,
        validated_arguments.result_descriptor,
    )
    inherited = (runtime.descriptor, runner.descriptor, *channel_descriptors)
    if len(set(inherited)) != 6:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker launch differs"
        )
    cwd = _BROWSER_WORKER_RUNNER_SOURCE.parents[2]
    try:
        cwd_info = cwd.stat(follow_symlinks=False)
    except OSError:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker launch differs"
        ) from None
    if cwd.is_symlink() or not stat.S_ISDIR(cwd_info.st_mode):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker launch differs"
        )
    command_line = (
        f"/proc/self/fd/{runtime.descriptor}",
        "-I",
        "-S",
        "-B",
        "-P",
        f"/proc/self/fd/{runner.descriptor}",
        BROWSER_WORKER_HIDDEN_MODE,
        *(str(value) for value in channel_descriptors),
    )
    return BrowserWorkerLaunch(
        command_line=command_line,
        environment=_BROWSER_WORKER_ENVIRONMENT,
        cwd=cwd,
        pass_fds=inherited,
        arguments=validated_arguments,
    )


def _parse_browser_containment_pid(value: bytes) -> int:
    if (
        type(value) is not bytes
        or not value
        or len(value) > 10
        or not value.isascii()
        or not value.isdigit()
        or value.startswith(b"0")
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    selected = int(value)
    if selected <= 0 or selected > _BROWSER_LINUX_PID_MAX:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    return selected


def parse_browser_cgroup_path(raw: bytes) -> str:
    """Parse one exact unified-cgroup membership record without filesystem I/O."""

    if (
        type(raw) is not bytes
        or len(raw) < 5
        or len(raw) > _BROWSER_CGROUP_FILE_LIMIT
        or not raw.endswith(b"\n")
        or raw.count(b"\n") != 1
        or b"\r" in raw
        or b"\0" in raw
        or not raw.startswith(b"0::")
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    path = raw[3:-1]
    if not path or len(path) > _BROWSER_CGROUP_PATH_LIMIT:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    if path == b"/":
        return "/"
    if not path.startswith(b"/") or path.endswith(b"/") or b"//" in path:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    components = path[1:].split(b"/")
    if any(
        not component
        or len(component) > _BROWSER_CGROUP_COMPONENT_LIMIT
        or component in {b".", b".."}
        or any(value < 0x21 or value > 0x7E for value in component)
        for component in components
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    try:
        return path.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        ) from None


def parse_browser_namespace_pids(raw: bytes) -> tuple[int, ...]:
    """Parse exactly one selected NSpid status record in kernel order."""

    prefix = b"NSpid:\t"
    if (
        type(raw) is not bytes
        or len(raw) <= len(prefix) + 1
        or len(raw) > _BROWSER_NSPID_LIMIT
        or not raw.startswith(prefix)
        or not raw.endswith(b"\n")
        or raw.count(b"\n") != 1
        or b"\r" in raw
        or b"\0" in raw
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    parts = raw[len(prefix) : -1].split(b"\t")
    if (
        not parts
        or len(parts) > _BROWSER_PID_NAMESPACE_DEPTH_LIMIT
        or any(not part for part in parts)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    return tuple(_parse_browser_containment_pid(part) for part in parts)


def parse_browser_cgroup_events(raw: bytes) -> BrowserCgroupEvents:
    """Parse the complete bounded cgroup.events projection."""

    if (
        type(raw) is not bytes
        or not raw
        or len(raw) > _BROWSER_CGROUP_EVENTS_LIMIT
        or not raw.endswith(b"\n")
        or b"\r" in raw
        or b"\0" in raw
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    selected: dict[bytes, bool] = {}
    for line in raw[:-1].split(b"\n"):
        if line.count(b" ") != 1:
            raise LocalStagingAcceptanceError(
                "local acceptance browser containment evidence differs"
            )
        key, value = line.split(b" ", 1)
        if key in selected or value not in {b"0", b"1"}:
            raise LocalStagingAcceptanceError(
                "local acceptance browser containment evidence differs"
            )
        selected[key] = value == b"1"
    if set(selected) != {b"populated", b"frozen"}:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    return BrowserCgroupEvents(
        populated=selected[b"populated"],
        frozen=selected[b"frozen"],
    )


def _parse_browser_cgroup_members(raw: bytes) -> tuple[int, ...]:
    if type(raw) is not bytes or len(raw) > _BROWSER_CGROUP_MEMBERS_BYTES_LIMIT:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    if not raw:
        return ()
    if not raw.endswith(b"\n") or b"\r" in raw or b"\0" in raw:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    parts = raw[:-1].split(b"\n")
    if (
        not parts
        or len(parts) > _BROWSER_CGROUP_MEMBER_LIMIT
        or any(not part for part in parts)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    members = tuple(_parse_browser_containment_pid(part) for part in parts)
    if len(set(members)) != len(members):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    return tuple(sorted(members))


def parse_browser_cgroup_processes(raw: bytes) -> tuple[int, ...]:
    """Normalize one bounded cgroup.procs snapshot."""

    return _parse_browser_cgroup_members(raw)


def parse_browser_cgroup_threads(raw: bytes) -> tuple[int, ...]:
    """Normalize one bounded cgroup.threads snapshot."""

    return _parse_browser_cgroup_members(raw)


def parse_browser_containment_text_evidence(
    *,
    expected_cgroup_path: str,
    expected_init_outer_pid: int,
    expected_worker_outer_pid: int,
    expected_frozen: bool,
    init_cgroup: bytes,
    worker_cgroup: bytes,
    init_nspid: bytes,
    worker_nspid: bytes,
    events: bytes,
    processes: bytes,
    threads: bytes,
) -> BrowserContainmentTextEvidence:
    """Normalize a two-process text snapshot without granting authority.

    This does not prove pidfd/start binding, PPID, namespace inodes, historical
    childlessness, mount isolation, launch ordering, or credential release.
    """

    if (
        type(expected_cgroup_path) is not str
        or not expected_cgroup_path
        or len(expected_cgroup_path) > _BROWSER_CGROUP_PATH_LIMIT
        or type(expected_init_outer_pid) is not int
        or type(expected_worker_outer_pid) is not int
        or type(expected_frozen) is not bool
        or expected_init_outer_pid <= 1
        or expected_worker_outer_pid <= 1
        or expected_init_outer_pid > _BROWSER_LINUX_PID_MAX
        or expected_worker_outer_pid > _BROWSER_LINUX_PID_MAX
        or expected_init_outer_pid == expected_worker_outer_pid
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    try:
        expected_raw = expected_cgroup_path.encode("ascii", errors="strict")
    except UnicodeEncodeError:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        ) from None
    if (
        parse_browser_cgroup_path(b"0::" + expected_raw + b"\n")
        != expected_cgroup_path
        or expected_cgroup_path == "/"
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    init_path = parse_browser_cgroup_path(init_cgroup)
    worker_path = parse_browser_cgroup_path(worker_cgroup)
    parsed_events = parse_browser_cgroup_events(events)
    process_ids = parse_browser_cgroup_processes(processes)
    thread_ids = parse_browser_cgroup_threads(threads)
    init_namespace_pids = parse_browser_namespace_pids(init_nspid)
    worker_namespace_pids = parse_browser_namespace_pids(worker_nspid)
    expected_members = tuple(
        sorted((expected_init_outer_pid, expected_worker_outer_pid))
    )
    if (
        init_path != expected_cgroup_path
        or worker_path != expected_cgroup_path
        or parsed_events
        != BrowserCgroupEvents(populated=True, frozen=expected_frozen)
        or process_ids != expected_members
        or thread_ids != expected_members
        or len(init_namespace_pids) < 2
        or len(init_namespace_pids) != len(worker_namespace_pids)
        or init_namespace_pids[0] != expected_init_outer_pid
        or worker_namespace_pids[0] != expected_worker_outer_pid
        or init_namespace_pids[-1] != 1
        or worker_namespace_pids[-1] != 2
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment evidence differs"
        )
    return BrowserContainmentTextEvidence(
        cgroup_path=expected_cgroup_path,
        events=parsed_events,
        process_ids=process_ids,
        thread_ids=thread_ids,
        init_outer_pid=expected_init_outer_pid,
        worker_outer_pid=expected_worker_outer_pid,
        init_namespace_pids=init_namespace_pids,
        worker_namespace_pids=worker_namespace_pids,
    )


def _validate_browser_guardian_pipe_handle(
    descriptor: int,
    device: int,
    inode: int,
    access: int,
) -> None:
    try:
        info = os.fstat(descriptor)
        target = os.readlink(f"/proc/self/fd/{descriptor}")
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
    except (OSError, OverflowError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian pipe differs"
        ) from None
    if (
        type(descriptor) is not int
        or descriptor <= 2
        or type(device) is not int
        or type(inode) is not int
        or access not in {os.O_RDONLY, os.O_WRONLY}
        or not stat.S_ISFIFO(info.st_mode)
        or (info.st_dev, info.st_ino) != (device, inode)
        or target != f"pipe:[{inode}]"
        or flags not in {access, access | os.O_NONBLOCK}
        or descriptor_flags & fcntl.FD_CLOEXEC == 0
        or os.get_inheritable(descriptor)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser guardian pipe differs"
        )


def _validate_browser_prerequisite_guardian_lease(
    guardian: _BrowserGuardianLease,
    *,
    expected_state: str,
) -> BrowserWorkerProcessStat:
    if (
        type(guardian) is not _BrowserGuardianLease
        or guardian._owner_token is not _BROWSER_GUARDIAN_LEASE_TOKEN
        or type(guardian.process_id) is not int
        or guardian.process_id <= 1
        or type(guardian.process_start_ticks) is not int
        or guardian.process_start_ticks <= 0
        or type(guardian.pidfd) is not int
        or guardian.pidfd <= 2
        or type(guardian.pidfd_device) is not int
        or type(guardian.pidfd_inode) is not int
        or expected_state not in _BROWSER_PROCESS_STATES
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite lease differs"
        )
    info, process_id, _, _ = _read_browser_pidfd_metadata(guardian.pidfd)
    process = _read_browser_worker_proc_stat(guardian.process_id)
    if (
        process_id != guardian.process_id
        or (info.st_dev, info.st_ino)
        != (guardian.pidfd_device, guardian.pidfd_inode)
        or _browser_pidfd_is_terminal(guardian.pidfd)
        or process.parent_pid != os.getpid()
        or process.process_group != guardian.process_id
        or process.session_id != guardian.process_id
        or process.start_ticks != guardian.process_start_ticks
        or process.state != expected_state
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite lease differs"
        )
    _validate_browser_guardian_pipe_handle(
        guardian.gate_write,
        guardian.gate_device,
        guardian.gate_inode,
        os.O_WRONLY,
    )
    _validate_browser_guardian_pipe_handle(
        guardian.status_read,
        guardian.status_device,
        guardian.status_inode,
        os.O_RDONLY,
    )
    return process


def _reap_browser_process_group_children(process_group: int) -> int:
    if (
        type(process_group) is not int
        or process_group <= 1
        or process_group > _BROWSER_LINUX_PID_MAX
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite descendants differ"
        )
    reaped = 0
    while True:
        try:
            process_id, _ = os.waitpid(-process_group, os.WNOHANG)
        except ChildProcessError:
            return reaped
        except OSError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser prerequisite descendants differ"
            ) from None
        if process_id == 0:
            return reaped
        if process_id <= 1:
            raise LocalStagingAcceptanceError(
                "local acceptance browser prerequisite descendants differ"
            )
        reaped += 1


def _start_stopped_browser_private_proc_guardian(
    *,
    generation: str,
) -> _BrowserGuardianLease:
    if (
        type(generation) is not str
        or _SHA256_TEXT.fullmatch(generation) is None
        or len(os.listdir("/proc/self/task")) != 1
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite preflight differs"
        )
    gate_read = gate_write = status_read = status_write = -1
    child_gate_read = child_status_write = -1
    process_id = pidfd = -1
    try:
        gate_read, gate_write = os.pipe2(os.O_CLOEXEC)
        status_read, status_write = os.pipe2(os.O_CLOEXEC)
        child_gate_read = gate_read
        child_status_write = status_write
        expected_gate_target = os.readlink(f"/proc/self/fd/{gate_write}")
        expected_status_target = os.readlink(f"/proc/self/fd/{status_read}")
        supervisor_pid = os.getpid()
        process_id = os.fork()
        if process_id == 0:
            try:
                os.close(gate_write)
                os.close(status_read)
                _run_browser_private_proc_guardian(
                    generation,
                    supervisor_pid,
                    gate_read,
                    status_write,
                )
            finally:
                os._exit(127)
        os.close(gate_read)
        gate_read = -1
        os.close(status_write)
        status_write = -1
        deadline = time.monotonic() + _BROWSER_CONTAINMENT_TIMEOUT_SECONDS
        while True:
            if time.monotonic() >= deadline:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser prerequisite did not stop"
                )
            waited, wait_status = os.waitpid(
                process_id,
                os.WNOHANG | os.WUNTRACED,
            )
            if waited == process_id:
                if (
                    os.WIFSTOPPED(wait_status)
                    and os.WSTOPSIG(wait_status) == signal.SIGSTOP
                ):
                    break
                process_id = -1
                raise LocalStagingAcceptanceError(
                    "local acceptance browser prerequisite exited early"
                )
            time.sleep(0.005)
        hardened = _read_browser_guardian_frame(status_read, deadline=deadline)
        process = _read_browser_worker_proc_stat(process_id)
        if (
            process.state != "T"
            or process.parent_pid != os.getpid()
            or process.process_group != process_id
            or process.session_id != process_id
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser prerequisite identity differs"
            )
        pidfd = os.pidfd_open(process_id, 0)
        pidfd_info, bound_pid, _, _ = _read_browser_pidfd_metadata(pidfd)
        if bound_pid != process_id or _browser_pidfd_is_terminal(pidfd):
            raise LocalStagingAcceptanceError(
                "local acceptance browser prerequisite pidfd differs"
            )
        expected_targets = {
            0: "/dev/null",
            1: "/dev/null",
            2: "/dev/null",
            child_gate_read: expected_gate_target,
            child_status_write: expected_status_target,
        }
        if hardened != {
            "descriptors": [
                [number, target]
                for number, target in sorted(expected_targets.items())
            ],
            "environment_entries": 0,
            "generation": generation,
            "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
            "stage": "HARDENED",
        }:
            raise LocalStagingAcceptanceError(
                "local acceptance browser prerequisite hardening differs"
            )
        gate_info = os.fstat(gate_write)
        status_info = os.fstat(status_read)
        guardian = _BrowserGuardianLease(
            process_id=process_id,
            process_start_ticks=process.start_ticks,
            pidfd=pidfd,
            pidfd_device=pidfd_info.st_dev,
            pidfd_inode=pidfd_info.st_ino,
            gate_write=gate_write,
            gate_device=gate_info.st_dev,
            gate_inode=gate_info.st_ino,
            status_read=status_read,
            status_device=status_info.st_dev,
            status_inode=status_info.st_ino,
        )
        guardian._owner_token = _BROWSER_GUARDIAN_LEASE_TOKEN
        _validate_browser_prerequisite_guardian_lease(
            guardian,
            expected_state="T",
        )
        process_id = pidfd = gate_write = status_read = -1
        return guardian
    except BaseException as body_error:
        cleanup_errors: list[BaseException] = []
        if process_id > 1:
            cleanup_deadline = (
                time.monotonic()
                + _BROWSER_GUARDIAN_CLEANUP_TIMEOUT_SECONDS
            )
            try:
                if pidfd > 2:
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                else:
                    os.kill(process_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except BaseException as exc:
                cleanup_errors.append(exc)
            reaped = False
            for _ in range(2):
                try:
                    _wait_browser_child_bounded(
                        process_id,
                        deadline=cleanup_deadline,
                    )
                    reaped = True
                    break
                except BaseException as exc:
                    cleanup_errors.append(exc)
            if not reaped:
                cleanup_errors.append(
                    LocalStagingAcceptanceError(
                        "local acceptance browser prerequisite child remained"
                    )
                )
        for descriptor in (
            gate_read,
            gate_write,
            status_read,
            status_write,
            pidfd,
        ):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except BaseException as exc:
                    cleanup_errors.append(exc)
        if cleanup_errors:
            raise LocalStagingAcceptanceError(
                "local acceptance browser prerequisite start and cleanup failed"
            ) from body_error
        raise


def _continue_browser_private_proc_guardian(
    guardian: _BrowserGuardianLease,
    *,
    generation: str,
) -> tuple[str, int]:
    _validate_browser_prerequisite_guardian_lease(
        guardian,
        expected_state="T",
    )
    deadline = time.monotonic() + _BROWSER_CONTAINMENT_TIMEOUT_SECONDS
    _write_browser_guardian_frame(
        guardian.gate_write,
        {
            "action": "START",
            "generation": generation,
            "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
        },
        deadline=deadline,
    )
    signal.pidfd_send_signal(guardian.pidfd, signal.SIGCONT)
    user_namespace = _read_browser_guardian_frame(
        guardian.status_read,
        deadline=deadline,
    )
    if user_namespace != {
        "generation": generation,
        "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
        "stage": "USERNS",
    }:
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite user namespace differs"
        )
    repeated = _read_browser_worker_proc_stat(guardian.process_id)
    if (
        repeated.start_ticks != guardian.process_start_ticks
        or repeated.parent_pid != os.getpid()
        or repeated.state not in _BROWSER_LIVE_PROCESS_STATES
        or _browser_pidfd_is_terminal(guardian.pidfd)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite identity changed"
        )
    _write_browser_user_namespace_maps(
        guardian.process_id,
        pidfd=guardian.pidfd,
        expected_start_ticks=guardian.process_start_ticks,
    )
    deadline = time.monotonic() + _BROWSER_CONTAINMENT_TIMEOUT_SECONDS
    _write_browser_guardian_frame(
        guardian.gate_write,
        {
            "action": "MAPPED",
            "generation": generation,
            "protocol": _BROWSER_CONTAINMENT_PROTOCOL,
        },
        deadline=deadline,
    )
    blocker = _read_browser_guardian_frame(
        guardian.status_read,
        deadline=deadline,
    )
    if (
        type(blocker) is not dict
        or set(blocker) != {"errno", "generation", "protocol", "stage"}
        or blocker["generation"] != generation
        or blocker["protocol"] != _BROWSER_CONTAINMENT_PROTOCOL
        or type(blocker["stage"]) is not str
        or type(blocker["errno"]) is not int
        or blocker["errno"] < 0
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser private proc evidence differs"
        )
    _validate_browser_guardian_pipe_handle(
        guardian.gate_write,
        guardian.gate_device,
        guardian.gate_inode,
        os.O_WRONLY,
    )
    os.close(guardian.gate_write)
    guardian.gate_write = -1
    return blocker["stage"], blocker["errno"]


def _close_browser_prerequisite_guardian_lease(
    guardian: _BrowserGuardianLease,
) -> None:
    if (
        type(guardian) is not _BrowserGuardianLease
        or guardian._owner_token is not _BROWSER_GUARDIAN_LEASE_TOKEN
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite cleanup differs"
        )
    errors: list[BaseException] = []
    cleanup_deadline = (
        time.monotonic() + _BROWSER_GUARDIAN_CLEANUP_TIMEOUT_SECONDS
    )
    pidfd_bound = False
    for _ in range(2):
        try:
            pidfd_info, bound_pid, _, _ = _read_browser_pidfd_metadata(
                guardian.pidfd
            )
            if (
                (pidfd_info.st_dev, pidfd_info.st_ino)
                != (guardian.pidfd_device, guardian.pidfd_inode)
                or bound_pid not in {guardian.process_id, None}
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance browser prerequisite cleanup differs"
                )
            pidfd_bound = True
            break
        except BaseException as exc:
            errors.append(exc)
    reaped = False
    process_group_empty = False
    adopted_descendants = 0
    if pidfd_bound:
        process_group_bound = False
        for _ in range(2):
            try:
                process = _read_browser_worker_proc_stat(
                    guardian.process_id
                )
                if (
                    process.pid != guardian.process_id
                    or process.start_ticks != guardian.process_start_ticks
                    or process.process_group != guardian.process_id
                    or process.session_id != guardian.process_id
                ):
                    raise LocalStagingAcceptanceError(
                        "local acceptance browser prerequisite cleanup differs"
                    )
                process_group_bound = True
                break
            except BaseException as exc:
                errors.append(exc)
        if process_group_bound:
            for _ in range(2):
                try:
                    os.killpg(guardian.process_id, signal.SIGKILL)
                    break
                except ProcessLookupError:
                    break
                except BaseException as exc:
                    errors.append(exc)
        for _ in range(2):
            try:
                _wait_browser_child_bounded(
                    guardian.process_id,
                    deadline=cleanup_deadline,
                )
                reaped = True
                break
            except BaseException as exc:
                errors.append(exc)
        if reaped and process_group_bound:
            while time.monotonic() < cleanup_deadline:
                try:
                    adopted_descendants += (
                        _reap_browser_process_group_children(
                            guardian.process_id
                        )
                    )
                    os.killpg(guardian.process_id, 0)
                except ProcessLookupError:
                    process_group_empty = True
                    break
                except BaseException as exc:
                    errors.append(exc)
                    break
                time.sleep(0.005)
            if adopted_descendants > 1:
                errors.append(
                    LocalStagingAcceptanceError(
                        "local acceptance browser prerequisite descendants differ"
                    )
                )
            if not process_group_empty:
                errors.append(
                    LocalStagingAcceptanceError(
                        "local acceptance browser prerequisite descendants remained"
                    )
                )

    def close_pipe(
        field_name: str,
        device: int,
        inode: int,
        access: int,
    ) -> None:
        descriptor = getattr(guardian, field_name)
        if descriptor < 0:
            return
        validated = False
        for _ in range(2):
            try:
                _validate_browser_guardian_pipe_handle(
                    descriptor,
                    device,
                    inode,
                    access,
                )
                validated = True
                break
            except BaseException as exc:
                errors.append(exc)
        if not validated:
            return
        try:
            os.close(descriptor)
        except BaseException as exc:
            errors.append(exc)
            try:
                _validate_browser_guardian_pipe_handle(
                    descriptor,
                    device,
                    inode,
                    access,
                )
            except BaseException:
                setattr(guardian, field_name, -1)
                return
            try:
                os.close(descriptor)
            except BaseException as repeated_exc:
                errors.append(repeated_exc)
                try:
                    _validate_browser_guardian_pipe_handle(
                        descriptor,
                        device,
                        inode,
                        access,
                    )
                except BaseException:
                    setattr(guardian, field_name, -1)
                return
        setattr(guardian, field_name, -1)

    close_pipe(
        "gate_write",
        guardian.gate_device,
        guardian.gate_inode,
        os.O_WRONLY,
    )
    close_pipe(
        "status_read",
        guardian.status_device,
        guardian.status_inode,
        os.O_RDONLY,
    )
    if pidfd_bound and reaped:
        try:
            repeated_info, repeated_pid, _, _ = _read_browser_pidfd_metadata(
                guardian.pidfd
            )
            if (
                (repeated_info.st_dev, repeated_info.st_ino)
                != (guardian.pidfd_device, guardian.pidfd_inode)
                or repeated_pid not in {guardian.process_id, None}
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance browser prerequisite cleanup differs"
                )
            descriptor = guardian.pidfd
            try:
                os.close(descriptor)
            except BaseException as exc:
                errors.append(exc)
                try:
                    still_info, still_pid, _, _ = (
                        _read_browser_pidfd_metadata(descriptor)
                    )
                    if (
                        (still_info.st_dev, still_info.st_ino)
                        != (guardian.pidfd_device, guardian.pidfd_inode)
                        or still_pid not in {guardian.process_id, None}
                    ):
                        guardian.pidfd = -1
                except BaseException:
                    guardian.pidfd = -1
                if guardian.pidfd >= 0:
                    try:
                        os.close(descriptor)
                    except BaseException as repeated_exc:
                        errors.append(repeated_exc)
                        try:
                            still_info, still_pid, _, _ = (
                                _read_browser_pidfd_metadata(descriptor)
                            )
                            if (
                                (still_info.st_dev, still_info.st_ino)
                                != (
                                    guardian.pidfd_device,
                                    guardian.pidfd_inode,
                                )
                                or still_pid
                                not in {guardian.process_id, None}
                            ):
                                guardian.pidfd = -1
                        except BaseException:
                            guardian.pidfd = -1
                    else:
                        guardian.pidfd = -1
            else:
                guardian.pidfd = -1
        except BaseException as exc:
            errors.append(exc)
    complete = (
        reaped
        and process_group_empty
        and guardian.gate_write == -1
        and guardian.status_read == -1
        and guardian.pidfd == -1
    )
    if complete:
        guardian._owner_token = None
    if errors or not complete:
        raise LocalStagingAcceptanceError(
            "local acceptance browser prerequisite cleanup differs"
        ) from None


def _observe_browser_containment_blocker(
    bundle: _PinnedBrowserRuntimeBundle,
    *,
    generation: str,
) -> _BrowserContainmentBlockerObservation:
    _validate_frozen_browser_runtime_bundle(bundle)
    if (
        type(generation) is not str
        or _SHA256_TEXT.fullmatch(generation) is None
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment invocation differs"
        )
    clone3_errno = _probe_browser_clone3_policy()
    guardian: _BrowserGuardianLease | None = None
    proc_stage = ""
    proc_errno = 0
    guardian_pid = 0
    guardian_start_ticks = 0
    cleanup_complete = False
    body_error: BaseException | None = None
    try:
        guardian = _start_stopped_browser_private_proc_guardian(
            generation=generation,
        )
        guardian_pid = guardian.process_id
        guardian_start_ticks = guardian.process_start_ticks
        proc_stage, proc_errno = _continue_browser_private_proc_guardian(
            guardian,
            generation=generation,
        )
        if (
            proc_stage != _BROWSER_PRIVATE_PROC_BLOCKER_STAGE
            or proc_errno != _BROWSER_PRIVATE_PROC_BLOCKER_ERRNO
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance private proc blocker differs"
            )
    except BaseException as exc:
        body_error = exc
    cleanup_errors: list[BaseException] = []
    if guardian is not None:
        try:
            _close_browser_prerequisite_guardian_lease(guardian)
        except BaseException as exc:
            cleanup_errors.append(exc)
    cleanup_complete = not cleanup_errors
    if body_error is not None:
        if cleanup_errors:
            raise LocalStagingAcceptanceError(
                "local acceptance browser containment and cleanup failed"
            ) from body_error
        raise body_error
    if cleanup_errors:
        raise LocalStagingAcceptanceError(
            "local acceptance browser containment cleanup failed"
        ) from cleanup_errors[0]
    return _BrowserContainmentBlockerObservation(
        protocol=_BROWSER_CONTAINMENT_PROTOCOL,
        generation=generation,
        clone3_errno=clone3_errno,
        guardian_pid=guardian_pid,
        guardian_start_ticks=guardian_start_ticks,
        cgroup_path="",
        projection_status="NOT_RUN",
        cgroup_status="NOT_RUN",
        credential_release_status="WITHHELD",
        projection_sha256="",
        projection_entries=0,
        proc_stage=proc_stage,
        proc_errno=proc_errno,
        sentinel_provider_calls=0,
        payload_processes=0,
        cleanup_complete=cleanup_complete,
        execution_authority=False,
    )


def _canonical_proc_integer(value: bytes, *, positive: bool) -> int:
    if (
        not value
        or len(value) > 20
        or not value.isascii()
        or not value.isdigit()
        or (len(value) > 1 and value.startswith(b"0"))
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    selected = int(value)
    if (
        selected > 18_446_744_073_709_551_615
        or (positive and selected <= 0)
        or (not positive and selected < 0)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    return selected


def _parse_browser_worker_proc_stat(
    raw: bytes,
    *,
    expected_pid: int,
) -> BrowserWorkerProcessStat:
    if (
        type(raw) is not bytes
        or not raw
        or len(raw) > _BROWSER_PROC_STAT_LIMIT
        or type(expected_pid) is not int
        or expected_pid <= 1
        or expected_pid > 2_147_483_647
        or raw.count(b"\n") != 1
        or not raw.endswith(b"\n")
        or b"\0" in raw
        or b"\r" in raw
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    selected = raw[:-1]
    prefix = str(expected_pid).encode("ascii") + b" ("
    closing = selected.rfind(b") ")
    if not selected.startswith(prefix) or closing < len(prefix):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    command_name = selected[len(prefix) : closing]
    fields = selected[closing + 2 :].split(b" ")
    if (
        not command_name
        or len(command_name) > 255
        or any(byte < 0x20 or byte > 0x7E for byte in command_name)
        or len(fields) < 20
        or any(not field for field in fields)
        or len(fields[0]) != 1
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    try:
        state = fields[0].decode("ascii", errors="strict")
    except UnicodeDecodeError:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    if state not in _BROWSER_PROCESS_STATES:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    return BrowserWorkerProcessStat(
        pid=expected_pid,
        state=state,
        parent_pid=_canonical_proc_integer(fields[1], positive=True),
        process_group=_canonical_proc_integer(fields[2], positive=True),
        session_id=_canonical_proc_integer(fields[3], positive=True),
        start_ticks=_canonical_proc_integer(fields[19], positive=True),
    )


def _read_browser_proc_value(
    path: str,
    *,
    maximum_bytes: int,
) -> bytes:
    if (
        not isinstance(path, str)
        or not path.startswith("/proc/")
        or type(maximum_bytes) is not int
        or maximum_bytes <= 0
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        observed = bytearray()
        while len(observed) <= maximum_bytes:
            try:
                block = os.read(
                    descriptor,
                    min(4096, maximum_bytes + 1 - len(observed)),
                )
            except InterruptedError:
                continue
            if not block:
                break
            observed.extend(block)
        if len(observed) > maximum_bytes:
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            )
        return bytes(observed)
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_browser_worker_proc_stat(pid: int) -> BrowserWorkerProcessStat:
    if type(pid) is not int or pid <= 1 or pid > 2_147_483_647:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    return _parse_browser_worker_proc_stat(
        _read_browser_proc_value(
            f"/proc/{pid}/stat",
            maximum_bytes=_BROWSER_PROC_STAT_LIMIT,
        ),
        expected_pid=pid,
    )


def _canonical_status_integers(
    value: str,
    *,
    count: int | None,
) -> tuple[int, ...]:
    parts = value.split()
    if count is not None and len(parts) != count:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    try:
        selected = tuple(
            _canonical_proc_integer(part.encode("ascii"), positive=False)
            for part in parts
        )
    except UnicodeEncodeError:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    return selected


def _parse_browser_worker_status(
    raw: bytes,
    *,
    process: BrowserWorkerProcessStat,
    expectation: BrowserWorkerProcessExpectation,
) -> None:
    if (
        type(raw) is not bytes
        or not raw
        or len(raw) > _BROWSER_PROC_VALUE_LIMIT
        or raw.count(b"\0")
        or raw.count(b"\r")
        or not raw.endswith(b"\n")
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    try:
        text = raw.decode("ascii", errors="strict")
        values: dict[str, str] = {}
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if not separator or not key or key in values:
                raise ValueError
            values[key] = value.strip()
        pid = _canonical_status_integers(values["Pid"], count=1)
        parent = _canonical_status_integers(values["PPid"], count=1)
        tracer = _canonical_status_integers(values["TracerPid"], count=1)
        threads = _canonical_status_integers(values["Threads"], count=1)
        user_ids = _canonical_status_integers(values["Uid"], count=4)
        group_ids = _canonical_status_integers(values["Gid"], count=4)
        groups = _canonical_status_integers(values["Groups"], count=None)
        namespace_pids = _canonical_status_integers(
            values["NSpid"],
            count=None,
        )
        no_new_privileges = _canonical_status_integers(
            values["NoNewPrivs"],
            count=1,
        )
        seccomp = _canonical_status_integers(values["Seccomp"], count=1)
    except (KeyError, UnicodeDecodeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    if (
        pid != (process.pid,)
        or parent != (process.parent_pid,)
        or tracer != (0,)
        or threads != (1,)
        or user_ids != (expectation.user_id,) * 4
        or group_ids != (expectation.group_id,) * 4
        or groups != expectation.supplementary_groups
        or not namespace_pids
        or namespace_pids[0] != process.pid
        or no_new_privileges != (expectation.no_new_privileges,)
        or seccomp != (expectation.seccomp_mode,)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )


def _validate_browser_worker_expectation(
    expectation: BrowserWorkerProcessExpectation,
) -> None:
    if type(expectation) is not BrowserWorkerProcessExpectation:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    command_line = expectation.command_line
    environment = expectation.environment
    descriptors = expectation.descriptors
    try:
        canonical_cwd = expectation.cwd.resolve(strict=True)
        cwd_info = expectation.cwd.stat(follow_symlinks=False)
    except (OSError, AttributeError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    if (
        type(command_line) is not tuple
        or not command_line
        or len(command_line) > _BROWSER_WORKER_ARGUMENT_LIMIT
        or any(
            type(value) is not str
            or not value
            or "\0" in value
            or len(value) > 4096
            or not value.isascii()
            for value in command_line
        )
        or sum(len(value) + 1 for value in command_line)
        > _BROWSER_PROC_VALUE_LIMIT
        or environment != _BROWSER_WORKER_ENVIRONMENT
        or type(descriptors) is not tuple
        or not descriptors
        or len(descriptors) > _BROWSER_WORKER_DESCRIPTOR_LIMIT
        or any(
            type(item) is not BrowserWorkerDescriptorExpectation
            for item in descriptors
        )
        or any(
            type(item.number) is not int
            or item.number < 0
            or item.number > _BROWSER_WORKER_MAX_FD
            or type(item.target) is not str
            or not item.target
            or "\0" in item.target
            or len(item.target) > 4096
            or not item.target.isascii()
            or type(item.device) is not int
            or type(item.inode) is not int
            or type(item.mount_id) is not int
            or type(item.position) is not int
            or item.device < 0
            or item.inode <= 0
            or item.mount_id <= 0
            or item.position < 0
            or type(item.status_flags) is not int
            or item.status_flags < 0
            or item.status_flags & os.O_CLOEXEC
            or item.status_flags & os.O_ACCMODE
            not in {os.O_RDONLY, os.O_WRONLY, os.O_RDWR}
            or type(item.close_on_exec) is not bool
            for item in descriptors
        )
        or tuple(sorted(descriptors, key=lambda item: item.number))
        != descriptors
        or len({item.number for item in descriptors}) != len(descriptors)
        or not {0, 1, 2}.issubset({item.number for item in descriptors})
        or expectation.cwd != canonical_cwd
        or expectation.cwd.is_symlink()
        or not stat.S_ISDIR(cwd_info.st_mode)
        or type(expectation.cwd_device) is not int
        or type(expectation.cwd_inode) is not int
        or expectation.cwd_device <= 0
        or expectation.cwd_inode <= 0
        or (cwd_info.st_dev, cwd_info.st_ino)
        != (expectation.cwd_device, expectation.cwd_inode)
        or type(expectation.user_id) is not int
        or type(expectation.group_id) is not int
        or expectation.user_id != os.geteuid()
        or expectation.user_id != os.getuid()
        or expectation.group_id != os.getegid()
        or expectation.group_id != os.getgid()
        or type(expectation.supplementary_groups) is not tuple
        or any(
            type(group) is not int or group < 0
            for group in expectation.supplementary_groups
        )
        or expectation.supplementary_groups != tuple(sorted(os.getgroups()))
        or type(expectation.no_new_privileges) is not int
        or type(expectation.seccomp_mode) is not int
        or expectation.no_new_privileges != 1
        or expectation.seccomp_mode != 2
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )


def _read_browser_worker_fd_metadata(
    pid: int,
    descriptor: int,
) -> tuple[int, int, int, int]:
    raw = _read_browser_proc_value(
        f"/proc/{pid}/fdinfo/{descriptor}",
        maximum_bytes=4096,
    )
    try:
        text = raw.decode("ascii", errors="strict")
        values: dict[str, str] = {}
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if not separator or not key or key in values:
                raise ValueError
            values[key] = value.strip()
        if set(values) != {"pos", "flags", "mnt_id", "ino"}:
            raise ValueError
        flags_text = values["flags"]
        if (
            re.fullmatch(r"[0-7]+", flags_text) is None
            or len(flags_text) > 20
        ):
            raise ValueError
        position = _canonical_proc_integer(
            values["pos"].encode("ascii"),
            positive=False,
        )
        mount_id = _canonical_proc_integer(
            values["mnt_id"].encode("ascii"),
            positive=True,
        )
        inode = _canonical_proc_integer(
            values["ino"].encode("ascii"),
            positive=True,
        )
        return position, int(flags_text, 8), mount_id, inode
    except (KeyError, UnicodeDecodeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None


def _validate_browser_worker_proc_snapshot(
    pid: int,
    runtime: PinnedBrowserPythonExecutable,
    expectation: BrowserWorkerProcessExpectation,
) -> BrowserWorkerProcessStat:
    _validate_browser_worker_expectation(expectation)
    process = _read_browser_worker_proc_stat(pid)
    if (
        process.state not in _BROWSER_LIVE_PROCESS_STATES
        or process.parent_pid != os.getpid()
        or process.process_group != pid
        or process.session_id != pid
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    _validate_browser_worker_executable(pid, runtime)
    _parse_browser_worker_status(
        _read_browser_proc_value(
            f"/proc/{pid}/status",
            maximum_bytes=_BROWSER_PROC_VALUE_LIMIT,
        ),
        process=process,
        expectation=expectation,
    )
    expected_command_line = b"\0".join(
        value.encode("ascii") for value in expectation.command_line
    ) + b"\0"
    expected_environment = b"".join(
        f"{key}={value}".encode("ascii") + b"\0"
        for key, value in expectation.environment
    )
    command_line = _read_browser_proc_value(
        f"/proc/{pid}/cmdline",
        maximum_bytes=_BROWSER_PROC_VALUE_LIMIT,
    )
    environment = _read_browser_proc_value(
        f"/proc/{pid}/environ",
        maximum_bytes=_BROWSER_PROC_VALUE_LIMIT,
    )
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
        cwd_info = os.stat(f"/proc/{pid}/cwd")
        expected_cwd_info = expectation.cwd.stat(follow_symlinks=False)
        descriptor_root = f"/proc/{pid}/fd"
        descriptor_names: list[str] = []
        with os.scandir(descriptor_root) as entries:
            for entry in entries:
                descriptor_names.append(entry.name)
                if len(descriptor_names) > len(expectation.descriptors):
                    raise ValueError
        observed_numbers = tuple(
            sorted(
                int(value)
                for value in descriptor_names
                if value.isascii() and value.isdigit()
            )
        )
        if len(observed_numbers) != len(descriptor_names):
            raise ValueError
        descriptor_values: list[BrowserWorkerDescriptorExpectation] = []
        for number in observed_numbers:
            descriptor_path = f"{descriptor_root}/{number}"
            descriptor_info = os.stat(descriptor_path)
            position, flags, mount_id, fdinfo_inode = (
                _read_browser_worker_fd_metadata(pid, number)
            )
            if fdinfo_inode != descriptor_info.st_ino:
                raise ValueError
            descriptor_values.append(
                BrowserWorkerDescriptorExpectation(
                    number=number,
                    target=os.readlink(descriptor_path),
                    device=descriptor_info.st_dev,
                    inode=descriptor_info.st_ino,
                    mount_id=mount_id,
                    position=position,
                    status_flags=flags & ~os.O_CLOEXEC,
                    close_on_exec=bool(flags & os.O_CLOEXEC),
                )
            )
        observed_descriptors = tuple(descriptor_values)
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    if (
        command_line != expected_command_line
        or environment != expected_environment
        or cwd != str(expectation.cwd)
        or (cwd_info.st_dev, cwd_info.st_ino)
        != (expectation.cwd_device, expectation.cwd_inode)
        or (expected_cwd_info.st_dev, expected_cwd_info.st_ino)
        != (expectation.cwd_device, expectation.cwd_inode)
        or observed_descriptors != expectation.descriptors
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    return process


def _stable_browser_process_identity(
    value: BrowserWorkerProcessStat,
) -> tuple[int, int, int, int, int]:
    return (
        value.pid,
        value.parent_pid,
        value.process_group,
        value.session_id,
        value.start_ticks,
    )


def _browser_pidfd_is_terminal(descriptor: int) -> bool:
    selector = selectors.DefaultSelector()
    try:
        selector.register(descriptor, selectors.EVENT_READ)
        return bool(selector.select(0))
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    finally:
        selector.close()


def _validate_browser_worker_executable(
    pid: int,
    runtime: PinnedBrowserPythonExecutable,
) -> None:
    _validate_pinned_browser_python_executable(runtime)
    try:
        executable = Path(f"/proc/{pid}/exe")
        info = executable.stat()
        target = os.readlink(executable)
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    if (
        target != str(_BROWSER_PYTHON_EXECUTABLE)
        or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        != (
            runtime.device,
            runtime.inode,
            runtime.size,
            runtime.mtime_ns,
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )


def _read_browser_pidfd_metadata(
    descriptor: int,
) -> tuple[os.stat_result, int | None, int, int]:
    if (
        type(descriptor) is not int
        or descriptor <= 2
        or descriptor > _BROWSER_WORKER_MAX_FD
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        info = os.fstat(descriptor)
        target = os.readlink(f"/proc/self/fd/{descriptor}")
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        status_flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        info_descriptor = os.open(
            f"/proc/self/fdinfo/{descriptor}",
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        try:
            raw_info = bytearray()
            while len(raw_info) <= 4096:
                block = os.read(info_descriptor, 4097 - len(raw_info))
                if not block:
                    break
                raw_info.extend(block)
        finally:
            os.close(info_descriptor)
    except (OSError, OverflowError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    try:
        text = bytes(raw_info).decode("ascii", errors="strict")
        pairs: dict[str, str] = {}
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if not separator or not key or key in pairs:
                raise ValueError
            pairs[key] = value.strip()
        if set(pairs) != {"pos", "flags", "mnt_id", "ino", "Pid", "NSpid"}:
            raise ValueError
        if (
            pairs["pos"] != "0"
            or re.fullmatch(r"[0-7]+", pairs["flags"]) is None
            or re.fullmatch(r"[1-9][0-9]*", pairs["mnt_id"]) is None
            or re.fullmatch(r"[1-9][0-9]*", pairs["ino"]) is None
            or int(pairs["flags"], 8)
            != status_flags | (
                os.O_CLOEXEC if descriptor_flags & fcntl.FD_CLOEXEC else 0
            )
            or int(pairs["ino"]) != info.st_ino
            or pairs["NSpid"] != pairs["Pid"]
        ):
            raise ValueError
        pid_value = pairs["Pid"]
        if pid_value == "-1":
            process_id = None
        elif re.fullmatch(r"[1-9][0-9]*", pid_value) is not None:
            process_id = int(pid_value)
        else:
            raise ValueError
    except (UnicodeDecodeError, KeyError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        ) from None
    if (
        (
            soft_limit != resource.RLIM_INFINITY
            and descriptor >= soft_limit
        )
        or target != "anon_inode:[pidfd]"
        or descriptor_flags & fcntl.FD_CLOEXEC == 0
        or os.get_inheritable(descriptor)
        or status_flags != os.O_RDWR
        or len(raw_info) > 4096
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    return info, process_id, descriptor_flags, status_flags


def _validate_browser_pidfd(
    observed: ObservedBrowserWorker,
) -> None:
    if (
        type(observed) is not ObservedBrowserWorker
        or observed._owner_token is not _BROWSER_HANDLE_TOKEN
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    info, process_id, _, _ = _read_browser_pidfd_metadata(observed.pidfd)
    if (
        process_id != observed.process.pid
        or (info.st_dev, info.st_ino)
        != (observed.pidfd_device, observed.pidfd_inode)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )


def observe_browser_worker_process(
    pid: int,
    runtime: PinnedBrowserPythonExecutable,
    *,
    expectation: BrowserWorkerProcessExpectation,
) -> ObservedBrowserWorker:
    if type(pid) is not int or pid <= 1 or pid > 2_147_483_647:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    pidfd = -1
    try:
        open_standard_descriptors: set[int] = set()
        for standard_descriptor in (0, 1, 2):
            try:
                os.fstat(standard_descriptor)
            except OSError:
                continue
            open_standard_descriptors.add(standard_descriptor)
        opener = getattr(os, "pidfd_open", None)
        if not callable(opener):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            )
        try:
            candidate_pidfd = opener(pid, 0)
        except (OSError, OverflowError, ValueError, TypeError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            ) from None
        if (
            type(candidate_pidfd) is not int
            or candidate_pidfd <= 2
            or candidate_pidfd > _BROWSER_WORKER_MAX_FD
        ):
            if (
                type(candidate_pidfd) is int
                and candidate_pidfd >= 0
                and candidate_pidfd not in open_standard_descriptors
            ):
                try:
                    os.close(candidate_pidfd)
                except OSError:
                    pass
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            )
        pidfd = candidate_pidfd
        initial = _read_browser_worker_proc_stat(pid)
        if (
            initial.state not in _BROWSER_LIVE_PROCESS_STATES
            or initial.parent_pid != os.getpid()
            or initial.process_group != pid
            or initial.session_id != pid
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            )
        info = os.fstat(pidfd)
        observed = ObservedBrowserWorker(
            pidfd=pidfd,
            pidfd_device=info.st_dev,
            pidfd_inode=info.st_ino,
            process=initial,
        )
        observed._owner_token = _BROWSER_HANDLE_TOKEN
        _validate_browser_pidfd(observed)
        if _browser_pidfd_is_terminal(pidfd):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            )
        repeated = _validate_browser_worker_proc_snapshot(
            pid,
            runtime,
            expectation,
        )
        if (
            repeated.state not in _BROWSER_LIVE_PROCESS_STATES
            or _stable_browser_process_identity(repeated)
            != _stable_browser_process_identity(initial)
            or _browser_pidfd_is_terminal(pidfd)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser process differs"
            )
        return observed
    except BaseException:
        if pidfd >= 0:
            os.close(pidfd)
        raise


def require_browser_worker_process_live(
    observed: ObservedBrowserWorker,
    runtime: PinnedBrowserPythonExecutable,
    *,
    expectation: BrowserWorkerProcessExpectation,
) -> BrowserWorkerProcessStat:
    if type(observed) is not ObservedBrowserWorker:
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    _validate_browser_pidfd(observed)
    if _browser_pidfd_is_terminal(observed.pidfd):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    repeated = _validate_browser_worker_proc_snapshot(
        observed.process.pid,
        runtime,
        expectation,
    )
    if (
        repeated.state not in _BROWSER_LIVE_PROCESS_STATES
        or _stable_browser_process_identity(repeated)
        != _stable_browser_process_identity(observed.process)
        or _browser_pidfd_is_terminal(observed.pidfd)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser process differs"
        )
    return repeated


def _validate_docker_config_root(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        info = path.stat(follow_symlinks=False)
        members = tuple(path.iterdir())
    except OSError as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker configuration differs"
        ) from exc
    if (
        path != resolved
        or path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or (info.st_uid, info.st_gid) != (os.geteuid(), os.getegid())
        or stat.S_IMODE(info.st_mode) != 0o700
        or members
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker configuration differs"
        )
    return resolved


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        process.wait()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker command cleanup failed"
        ) from exc


def _run_bounded_process(
    arguments: tuple[str, ...],
    *,
    pass_fds: tuple[int, ...],
    environment: Mapping[str, str],
    cwd: Path,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
) -> BoundedProcessResult:
    if (
        not arguments
        or timeout_seconds <= 0
        or stdout_limit < 0
        or stderr_limit < 0
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker command differs"
        )
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    deadline = time.monotonic() + timeout_seconds
    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(environment),
            close_fds=True,
            pass_fds=pass_fds,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise LocalStagingAcceptanceError(
                "local acceptance Docker command differs"
            )
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LocalStagingAcceptanceError(
                    "local acceptance Docker command timed out"
                )
            events = selector.select(min(remaining, 0.25))
            if not events and process.poll() is not None:
                events = [
                    (key, selectors.EVENT_READ)
                    for key in tuple(selector.get_map().values())
                ]
            for key, _ in events:
                try:
                    block = os.read(key.fileobj.fileno(), 65_536)
                except BlockingIOError:
                    continue
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                selected = output[key.data]
                selected.extend(block)
                if len(selected) > limits[key.data]:
                    raise LocalStagingAcceptanceError(
                        "local acceptance Docker command output differs"
                    )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LocalStagingAcceptanceError(
                "local acceptance Docker command timed out"
            )
        returncode = process.wait(timeout=remaining)
        return BoundedProcessResult(
            returncode=returncode,
            stdout=bytes(output["stdout"]),
            stderr=bytes(output["stderr"]),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker command failed"
        ) from exc
    finally:
        selector.close()
        if process is not None and process.poll() is None:
            _kill_process_group(process)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def run_docker_command(
    client: TrustedDockerClient,
    config_root: Path,
    arguments: tuple[str, ...],
    *,
    timeout_seconds: float = _DOCKER_METADATA_TIMEOUT,
    stdout_limit: int = _DOCKER_METADATA_LIMIT,
    stderr_limit: int = _DOCKER_METADATA_LIMIT,
) -> BoundedProcessResult:
    """Run one local-daemon Docker command through the held exact executable."""

    _validate_trusted_docker(client)
    config = _validate_docker_config_root(config_root)
    executable = f"/proc/self/fd/{client.descriptor}"
    result = _run_bounded_process(
        (executable, *arguments),
        pass_fds=(client.descriptor,),
        environment={
            "DOCKER_API_VERSION": _DOCKER_API_VERSION,
            "DOCKER_CONFIG": str(config),
            "DOCKER_HOST": f"unix://{_DOCKER_SOCKET}",
            "HOME": str(config),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        },
        cwd=config,
        timeout_seconds=timeout_seconds,
        stdout_limit=stdout_limit,
        stderr_limit=stderr_limit,
    )
    _validate_trusted_docker(client)
    _validate_docker_config_root(config)
    return result


def run_docker_argv(
    client: TrustedDockerClient,
    config_root: Path,
    arguments: tuple[str, ...],
    *,
    timeout_seconds: float = _DOCKER_METADATA_TIMEOUT,
    stdout_limit: int = _DOCKER_METADATA_LIMIT,
    stderr_limit: int = _DOCKER_METADATA_LIMIT,
) -> BoundedProcessResult:
    expected_executable = f"/proc/self/fd/{client.descriptor}"
    if not arguments or arguments[0] != expected_executable:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker command differs"
        )
    return run_docker_command(
        client,
        config_root,
        arguments[1:],
        timeout_seconds=timeout_seconds,
        stdout_limit=stdout_limit,
        stderr_limit=stderr_limit,
    )


def _stable_file_sha256(
    path: Path,
    *,
    expected_bytes: int,
    expected_sha256: str,
    expected_uid: int,
    expected_gid: int,
) -> None:
    descriptor = -1
    observed = 0
    digest = hashlib.sha256()
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        before = os.fstat(descriptor)
        named_before = path.stat(follow_symlinks=False)
        if (
            path.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_uid, before.st_gid) != (expected_uid, expected_gid)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != expected_bytes
            or (before.st_dev, before.st_ino)
            != (named_before.st_dev, named_before.st_ino)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance ingress metadata differs"
            )
        while observed <= expected_bytes:
            block = os.read(
                descriptor,
                min(_CHUNK_BYTES, expected_bytes + 1 - observed),
            )
            if not block:
                break
            observed += len(block)
            digest.update(block)
        after = os.fstat(descriptor)
        named_after = path.stat(follow_symlinks=False)
    except LocalStagingAcceptanceError:
        raise
    except OSError as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance ingress is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        observed != expected_bytes
        or digest.hexdigest() != expected_sha256
        or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_size,
            before.st_mtime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_gid,
            after.st_size,
            after.st_mtime_ns,
        )
        or (after.st_dev, after.st_ino)
        != (named_after.st_dev, named_after.st_ino)
    ):
        raise LocalStagingAcceptanceError("local acceptance ingress changed")


def _canonical_mount_source(path: Path, *, directory: bool) -> Path:
    try:
        resolved = path.resolve(strict=True)
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise LocalStagingAcceptanceError(
            "local acceptance ingress is unavailable"
        ) from exc
    encoded = os.fsencode(path)
    if (
        not path.is_absolute()
        or path != resolved
        or path.is_symlink()
        or b"\x00" in encoded
        or b"\n" in encoded
        or b"\r" in encoded
        or b"," in encoded
        or b'"' in encoded
        or b"'" in encoded
        or b"\\" in encoded
        or (directory and not stat.S_ISDIR(info.st_mode))
        or (not directory and not stat.S_ISREG(info.st_mode))
    ):
        raise LocalStagingAcceptanceError("local acceptance ingress path differs")
    return resolved


def validate_materializer_ingress(
    *,
    research_root: str | Path,
    inventory_path: str | Path,
    bundle_path: str | Path,
) -> MaterializerIngress:
    """Bind the three read-only ingress mounts before Docker is invoked."""

    research = _canonical_mount_source(Path(research_root), directory=True)
    inventory = _canonical_mount_source(Path(inventory_path), directory=False)
    bundle = _canonical_mount_source(Path(bundle_path), directory=False)
    research_info = research.stat(follow_symlinks=False)
    if (
        (research_info.st_uid, research_info.st_gid) != (1000, 1000)
        or stat.S_IMODE(research_info.st_mode) != 0o700
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance research ingress differs"
        )
    _stable_file_sha256(
        inventory,
        expected_bytes=ACCEPTED_INVENTORY_BYTES,
        expected_sha256=ACCEPTED_INVENTORY_SHA256,
        expected_uid=1000,
        expected_gid=1000,
    )
    _stable_file_sha256(
        bundle,
        expected_bytes=ACCEPTED_BUNDLE_BYTES,
        expected_sha256=ACCEPTED_BUNDLE_SHA256,
        expected_uid=1000,
        expected_gid=1000,
    )
    return MaterializerIngress(research, inventory, bundle)


def _volume_name(run_id: str) -> str:
    return f"buffalo-staging-acceptance-{run_id}"


def _container_name(run_id: str, *, replay: bool) -> str:
    phase = "research-replay" if replay else "research-materialize"
    return f"buffalo-staging-{phase}-{run_id}"


def materializer_invocation(
    *,
    image_id: str,
    run_id: str,
    ingress: MaterializerIngress | None,
) -> MaterializerInvocation:
    if image_id != FROZEN_IMAGE_ID or _RUN_ID.fullmatch(run_id) is None:
        raise LocalStagingAcceptanceError(
            "local acceptance materializer identity differs"
        )
    volume_name = _volume_name(run_id)
    container_name = _container_name(run_id, replay=ingress is None)
    if (
        _SAFE_DOCKER_NAME.fullmatch(volume_name) is None
        or _SAFE_DOCKER_NAME.fullmatch(container_name) is None
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer name differs"
        )
    if ingress is not None:
        observed = validate_materializer_ingress(
            research_root=ingress.research_root,
            inventory_path=ingress.inventory_path,
            bundle_path=ingress.bundle_path,
        )
        if observed != ingress:
            raise LocalStagingAcceptanceError(
                "local acceptance materializer ingress differs"
            )
    return MaterializerInvocation(
        image_id=image_id,
        run_id=run_id,
        volume_name=volume_name,
        container_name=container_name,
        ingress=ingress,
    )


def _validate_browser_dependency_run_id(run_id: str) -> None:
    if type(run_id) is not str or _RUN_ID.fullmatch(run_id) is None:
        raise LocalStagingAcceptanceError(
            "local acceptance browser dependency identity differs"
        )


def _build_browser_dependency_image_save_argv(
    *,
    docker_client: TrustedDockerClient,
) -> tuple[str, ...]:
    """Build the sole non-mutating dependency-source Docker command."""

    _validate_trusted_docker(docker_client)
    return (
        f"/proc/self/fd/{docker_client.descriptor}",
        "image",
        "save",
        FROZEN_IMAGE_ID,
    )


def _bind_mount(source: Path, destination: str) -> str:
    canonical = _canonical_mount_source(
        source,
        directory=source == source.resolve(strict=True) and source.is_dir(),
    )
    return (
        f"type=bind,src={canonical},dst={destination},"
        "readonly,bind-propagation=rprivate"
    )


def build_materializer_create_argv(
    *,
    docker_client: TrustedDockerClient,
    invocation: MaterializerInvocation,
) -> tuple[str, ...]:
    """Return the exact inspectable `docker create` argv for one phase."""

    _validate_trusted_docker(docker_client)
    if (
        invocation.image_id != FROZEN_IMAGE_ID
        or _RUN_ID.fullmatch(invocation.run_id) is None
        or invocation.volume_name != _volume_name(invocation.run_id)
        or invocation.container_name
        != _container_name(invocation.run_id, replay=invocation.ingress is None)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer command differs"
        )
    if invocation.ingress is not None:
        observed = validate_materializer_ingress(
            research_root=invocation.ingress.research_root,
            inventory_path=invocation.ingress.inventory_path,
            bundle_path=invocation.ingress.bundle_path,
        )
        if observed != invocation.ingress:
            raise LocalStagingAcceptanceError(
                "local acceptance materializer ingress differs"
            )

    arguments = [
        f"/proc/self/fd/{docker_client.descriptor}",
        "create",
        "--platform",
        "linux/amd64",
        "--pull",
        "never",
        "--runtime",
        "runc",
        "--user",
        "0:0",
        "--workdir",
        "/app",
        "--read-only",
        "--network",
        "none",
        "--ipc",
        "none",
        "--cgroupns",
        "private",
        "--pids-limit",
        "64",
        "--memory",
        "1073741824",
        "--memory-swap",
        "1073741824",
        "--cpus",
        "2",
        "--security-opt",
        "no-new-privileges=true",
        "--cap-drop",
        "ALL",
    ]
    for capability in _MATERIALIZER_CAPABILITIES:
        arguments.extend(("--cap-add", capability))
    arguments.extend(
        (
            "--restart",
            "no",
            "--no-healthcheck",
            "--log-driver",
            "none",
            "--name",
            invocation.container_name,
            "--label",
            f"buffalo.contract={ACCEPTANCE_CONTRACT}",
            "--label",
            f"buffalo.run={invocation.run_id}",
            "--label",
            f"buffalo.role={MATERIALIZER_ROLE}",
            "--mount",
            (
                f"type=volume,src={invocation.volume_name},dst=/data,"
                "volume-nocopy"
            ),
        )
    )
    if invocation.ingress is not None:
        arguments.extend(
            (
                "--mount",
                _bind_mount(
                    invocation.ingress.research_root,
                    "/mnt/buffalo-accepted-research",
                ),
                "--mount",
                _bind_mount(
                    invocation.ingress.inventory_path,
                    "/mnt/deployment-inventory.json",
                ),
                "--mount",
                _bind_mount(
                    invocation.ingress.bundle_path,
                    (
                        "/mnt/buffalo-procurement-os-accepted-source-"
                        "608929ad.bundle"
                    ),
                ),
            )
        )
    arguments.extend(
        (
            "--tmpfs",
            _MATERIALIZER_TMPFS,
            "--entrypoint",
            "/usr/bin/tini",
            invocation.image_id,
            *_MATERIALIZER_COMMAND,
        )
    )
    return tuple(arguments)


def build_materializer_volume_create_argv(
    *,
    docker_client: TrustedDockerClient,
    invocation: MaterializerInvocation,
) -> tuple[str, ...]:
    _validate_trusted_docker(docker_client)
    if (
        invocation.image_id != FROZEN_IMAGE_ID
        or invocation.volume_name != _volume_name(invocation.run_id)
        or _RUN_ID.fullmatch(invocation.run_id) is None
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer volume command differs"
        )
    return (
        f"/proc/self/fd/{docker_client.descriptor}",
        "volume",
        "create",
        "--driver",
        "local",
        "--label",
        f"buffalo.contract={ACCEPTANCE_CONTRACT}",
        "--label",
        f"buffalo.run={invocation.run_id}",
        "--label",
        f"buffalo.role={MATERIALIZER_VOLUME_ROLE}",
        "--name",
        invocation.volume_name,
    )


def _reject_duplicate_json_pairs(
    values: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise LocalStagingAcceptanceError(
                "local acceptance Docker response differs"
            )
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    raise LocalStagingAcceptanceError(
        "local acceptance Docker response differs"
    )


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker response differs"
        )
    return parsed


def _reject_protocol_float(_: str) -> None:
    raise LocalStagingAcceptanceError(
        "local acceptance browser frame differs"
    )


def _assert_protocol_json_value(value: object) -> None:
    if value is None or isinstance(value, (str, bool)) or type(value) is int:
        return
    if isinstance(value, list):
        for selected in value:
            _assert_protocol_json_value(selected)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise LocalStagingAcceptanceError(
                "local acceptance browser frame differs"
            )
        for selected in value.values():
            _assert_protocol_json_value(selected)
        return
    raise LocalStagingAcceptanceError(
        "local acceptance browser frame differs"
    )


def _canonical_protocol_json(value: Mapping[str, Any]) -> bytes:
    try:
        _assert_protocol_json_value(value)
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        ) from None


def encode_browser_worker_frame(
    value: Mapping[str, Any],
    *,
    maximum_bytes: int,
) -> bytes:
    if (
        type(value) is not dict
        or type(maximum_bytes) is not int
        or maximum_bytes <= 0
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        )
    body = _canonical_protocol_json(value)
    if not body or len(body) > maximum_bytes:
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        )
    return len(body).to_bytes(4, "big") + body


def decode_browser_worker_frame(
    raw: bytes,
    *,
    maximum_bytes: int,
) -> Mapping[str, Any]:
    if (
        type(maximum_bytes) is not int
        or maximum_bytes <= 0
        or len(raw) < 5
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        )
    declared = int.from_bytes(raw[:4], "big")
    body = raw[4:]
    if declared <= 0 or declared > maximum_bytes or len(body) != declared:
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        )
    try:
        text = body.decode("ascii", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_reject_protocol_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        ) from None
    if not isinstance(value, dict) or _canonical_protocol_json(value) != body:
        raise LocalStagingAcceptanceError(
            "local acceptance browser frame differs"
        )
    return value


def _require_browser_channel_deadline(deadline: float) -> None:
    if (
        isinstance(deadline, bool)
        or not isinstance(deadline, (int, float))
        or not math.isfinite(float(deadline))
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser channel differs"
        )


def _browser_pipe_identity(descriptor: int, access: int) -> tuple[int, int]:
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if (
            type(descriptor) is not int
            or descriptor <= 2
            or descriptor > _BROWSER_WORKER_MAX_FD
            or (soft_limit != resource.RLIM_INFINITY and descriptor >= soft_limit)
        ):
            raise OSError
        info = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        descriptor_target = os.readlink(f"/proc/self/fd/{descriptor}")
        if (
            not stat.S_ISFIFO(info.st_mode)
            or flags != access
            or descriptor_flags & fcntl.FD_CLOEXEC == 0
            or os.get_inheritable(descriptor)
            or descriptor_target != f"pipe:[{info.st_ino}]"
        ):
            raise OSError
        return info.st_dev, info.st_ino
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker descriptors differ"
        ) from None


def _wait_browser_channel(
    descriptor: int,
    event: int,
    deadline: float,
) -> None:
    while True:
        remaining = float(deadline) - time.monotonic()
        if remaining <= 0:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            )
        selector = selectors.DefaultSelector()
        try:
            selector.register(descriptor, event)
            try:
                ready = selector.select(remaining)
            except InterruptedError:
                continue
        except (OSError, ValueError):
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            ) from None
        finally:
            selector.close()
        if ready:
            return


def _read_exact_browser_pipe(
    descriptor: int,
    count: int,
    deadline: float,
) -> bytes:
    if type(count) is not int or count < 0:
        raise LocalStagingAcceptanceError(
            "local acceptance browser channel differs"
        )
    observed = bytearray()
    while len(observed) < count:
        _wait_browser_channel(
            descriptor,
            selectors.EVENT_READ,
            deadline,
        )
        try:
            block = os.read(descriptor, count - len(observed))
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            ) from None
        if not block:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            )
        observed.extend(block)
    return bytes(observed)


def _require_browser_pipe_eof(descriptor: int, deadline: float) -> None:
    while True:
        _wait_browser_channel(
            descriptor,
            selectors.EVENT_READ,
            deadline,
        )
        try:
            trailing = os.read(descriptor, 1)
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            ) from None
        if trailing:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            )
        return


def read_browser_worker_frame(
    descriptor: int,
    *,
    maximum_bytes: int,
    deadline: float,
) -> tuple[Mapping[str, Any], bytes]:
    try:
        _require_browser_channel_deadline(deadline)
        if type(maximum_bytes) is not int or maximum_bytes <= 0:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            )
        _browser_pipe_identity(descriptor, os.O_RDONLY)
        os.set_blocking(descriptor, False)
        header = _read_exact_browser_pipe(descriptor, 4, deadline)
        declared = int.from_bytes(header, "big")
        if declared <= 0 or declared > maximum_bytes:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            )
        body = _read_exact_browser_pipe(descriptor, declared, deadline)
        _require_browser_pipe_eof(descriptor, deadline)
        framed = header + body
        value = decode_browser_worker_frame(
            framed,
            maximum_bytes=maximum_bytes,
        )
        _validate_browser_worker_wire_snapshot(dict(value))
        return value, framed
    except LocalStagingAcceptanceError:
        raise
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser channel differs"
        ) from None
    finally:
        try:
            os.close(descriptor)
        except (OSError, TypeError):
            pass


def _write_exact_browser_pipe(
    descriptor: int,
    value: bytes | bytearray | memoryview,
    deadline: float,
) -> None:
    selected = memoryview(value)
    written = 0
    while written < len(selected):
        _wait_browser_channel(
            descriptor,
            selectors.EVENT_WRITE,
            deadline,
        )
        try:
            count = os.write(descriptor, selected[written:])
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            ) from None
        if count <= 0:
            raise LocalStagingAcceptanceError(
                "local acceptance browser channel differs"
            )
        written += count


def write_browser_worker_frame(
    descriptor: int,
    value: Mapping[str, Any],
    *,
    maximum_bytes: int,
    deadline: float,
) -> bytes:
    try:
        _require_browser_channel_deadline(deadline)
        framed = encode_browser_worker_frame(value, maximum_bytes=maximum_bytes)
        snapshot = decode_browser_worker_frame(
            framed,
            maximum_bytes=maximum_bytes,
        )
        _validate_browser_worker_wire_snapshot(dict(snapshot))
        _browser_pipe_identity(descriptor, os.O_WRONLY)
        os.set_blocking(descriptor, False)
        _write_exact_browser_pipe(descriptor, framed, deadline)
        return framed
    except LocalStagingAcceptanceError:
        raise
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser channel differs"
        ) from None
    finally:
        try:
            os.close(descriptor)
        except (OSError, TypeError):
            pass


def read_browser_worker_secret(
    descriptor: int,
    *,
    deadline: float,
) -> bytearray:
    selected = bytearray(_BROWSER_SECRET_BYTES)
    trailing = bytearray(1)
    observed = 0
    succeeded = False
    try:
        _require_browser_channel_deadline(deadline)
        _browser_pipe_identity(descriptor, os.O_RDONLY)
        os.set_blocking(descriptor, False)
        while observed < _BROWSER_SECRET_BYTES:
            _wait_browser_channel(
                descriptor,
                selectors.EVENT_READ,
                deadline,
            )
            try:
                count = os.readv(descriptor, [memoryview(selected)[observed:]])
            except (BlockingIOError, InterruptedError):
                continue
            if count <= 0:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser credential differs"
                )
            observed += count
        while True:
            _wait_browser_channel(
                descriptor,
                selectors.EVENT_READ,
                deadline,
            )
            try:
                trailing_count = os.readv(descriptor, [trailing])
            except (BlockingIOError, InterruptedError):
                continue
            if trailing_count != 0:
                raise LocalStagingAcceptanceError(
                    "local acceptance browser credential differs"
                )
            break
        if _BROWSER_SECRET_TEXT.fullmatch(selected) is None:
            raise LocalStagingAcceptanceError(
                "local acceptance browser credential differs"
            )
        succeeded = True
        return selected
    except LocalStagingAcceptanceError:
        raise
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser credential differs"
        ) from None
    finally:
        if not succeeded:
            for index in range(len(selected)):
                selected[index] = 0
        trailing[0] = 0
        try:
            os.close(descriptor)
        except (OSError, TypeError):
            pass


def write_browser_worker_secret(
    descriptor: int,
    secret: bytearray,
    *,
    deadline: float,
) -> None:
    try:
        _require_browser_channel_deadline(deadline)
        if (
            type(secret) is not bytearray
            or len(secret) != _BROWSER_SECRET_BYTES
            or _BROWSER_SECRET_TEXT.fullmatch(secret) is None
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser credential differs"
            )
        _browser_pipe_identity(descriptor, os.O_WRONLY)
        os.set_blocking(descriptor, False)
        _write_exact_browser_pipe(descriptor, secret, deadline)
    except LocalStagingAcceptanceError:
        raise
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser credential differs"
        ) from None
    finally:
        try:
            if isinstance(secret, bytearray):
                bytearray.__setitem__(
                    secret,
                    slice(None),
                    b"\0" * bytearray.__len__(secret),
                )
        except BaseException:
            pass
        finally:
            try:
                os.close(descriptor)
            except (OSError, TypeError):
                pass


def _require_exact_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )


def _require_exact_text(
    value: object,
    *,
    pattern: re.Pattern[str] | None = None,
    maximum: int = 1024,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in value)
        or (pattern is not None and pattern.fullmatch(value) is None)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    return value


def _require_positive_integer(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    return value


def _snapshot_browser_worker_value(
    value: Mapping[str, Any],
    *,
    maximum_bytes: int,
) -> tuple[dict[str, Any], bytes]:
    framed = encode_browser_worker_frame(value, maximum_bytes=maximum_bytes)
    decoded = decode_browser_worker_frame(framed, maximum_bytes=maximum_bytes)
    return dict(decoded), framed


def _validate_operator_proof_snapshot(
    value: object,
) -> tuple[bytes, str, str]:
    if type(value) is not dict or set(value) != _OPERATOR_PROOF_KEYS:
        raise LocalStagingAcceptanceError(
            "local acceptance browser operator proof differs"
        )
    batch_value = value.get("batch_id")
    try:
        batch_id = str(UUID(batch_value)) if isinstance(batch_value, str) else ""
    except (ValueError, TypeError, AttributeError):
        batch_id = ""
    hash_names = _OPERATOR_PROOF_KEYS - _OPERATOR_PROOF_NON_HASH_KEYS
    if (
        value.get("contract") != _OPERATOR_PROOF_CONTRACT
        or value.get("source_ref") != _OPERATOR_SOURCE_REF
        or value.get("source_bytes") != _OPERATOR_SOURCE_BYTES
        or value.get("raw_sha256") != _OPERATOR_RAW_SHA256
        or value.get("target_attestation_sha256")
        != _OPERATOR_TARGET_ATTESTATION_SHA256
        or value.get("status") != "VALIDATED"
        or batch_value != batch_id
        or any(
            not isinstance(value.get(name), str)
            or _SHA256_TEXT.fullmatch(value[name]) is None
            for name in hash_names
        )
        or type(value.get("idempotent_replay")) is not bool
        or type(value.get("ambiguous_commit_recovered")) is not bool
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser operator proof differs"
        )
    canonical = _canonical_protocol_json(value)
    return canonical, batch_id, hashlib.sha256(canonical).hexdigest()


def _validate_target_summary(value: object) -> None:
    if (
        type(value) is not dict
        or set(value) != _BROWSER_TARGET_SUMMARY_KEYS
        or any(type(item) is not int or item < 0 for item in value.values())
        or value["tracked"] != 5
        or value["guarded"] != 5
        or value["active_guarded"] != 5
        or value["inert"] != 0
        or value["tracked"] != value["guarded"] + value["inert"]
        or any(
            value[key] != 0
            for key in (
                "live_detached",
                "unsupported",
                "unattached",
                "unguarded",
                "unresumed",
            )
        )
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser result proof differs"
        )


def _validate_browser_proof_snapshot(value: object) -> bytes:
    if type(value) is not dict or set(value) != _BROWSER_PROOF_KEYS:
        raise LocalStagingAcceptanceError(
            "local acceptance browser result proof differs"
        )
    batch_value = value.get("batch_id")
    try:
        batch_id = str(UUID(batch_value)) if isinstance(batch_value, str) else ""
    except (ValueError, TypeError, AttributeError):
        batch_id = ""
    hash_names = (
        "assertion_manifest_sha256",
        "confirmation_preview_sha256",
        "driver_sha256",
        "node_sha256",
        "operator_proof_sha256",
        "raw_sha256",
        "screenshot_sha256",
        "tls_certificate_sha256",
    )
    browser_start_time = value.get("browser_start_time")
    if (
        value.get("contract") != _BROWSER_PHASE_CONTRACT
        or value.get("phase") != _BROWSER_PHASE
        or batch_value != batch_id
        or value.get("status_before") != "VALIDATED"
        or value.get("operational_status_before") != "VALIDATED"
        or value.get("status_after") != "VERIFIED_FUTURE"
        or value.get("operational_status_after") != "VERIFIED_FUTURE"
        or value.get("temporal_basis") != "REGISTERED_OBSERVATION"
        or value.get("raw_bytes") != _OPERATOR_SOURCE_BYTES
        or value.get("raw_sha256") != _OPERATOR_RAW_SHA256
        or value.get("assertion_count") != _BROWSER_ASSERTION_COUNT
        or value.get("assertion_manifest_sha256")
        != _BROWSER_ASSERTION_MANIFEST_SHA256
        or value.get("node_version") != _BROWSER_NODE_VERSION
        or not isinstance(value.get("browser_product"), str)
        or _BROWSER_PRODUCT_TEXT.fullmatch(value["browser_product"]) is None
        or not isinstance(value.get("browser_protocol_version"), str)
        or _VERSION_TEXT.fullmatch(value["browser_protocol_version"]) is None
        or not isinstance(value.get("browser_js_version"), str)
        or _VERSION_TEXT.fullmatch(value["browser_js_version"]) is None
        or any(
            not isinstance(value.get(name), str)
            or _SHA256_TEXT.fullmatch(value[name]) is None
            for name in hash_names
        )
        or not isinstance(value.get("source_commit"), str)
        or _GIT_OID_TEXT.fullmatch(value["source_commit"]) is None
        or not isinstance(value.get("source_tree"), str)
        or _GIT_OID_TEXT.fullmatch(value["source_tree"]) is None
        or type(value.get("browser_pid")) is not int
        or value["browser_pid"] <= 1
        or not isinstance(browser_start_time, str)
        or not browser_start_time.isascii()
        or not browser_start_time.isdigit()
        or browser_start_time.startswith("0")
        or type(value.get("screenshot_bytes")) is not int
        or not 8 <= value["screenshot_bytes"] <= 10 * 1024 * 1024
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser result proof differs"
        )
    _validate_target_summary(value.get("target_summary"))
    return _canonical_protocol_json(value)


def _validate_evidence_root_text(value: object) -> str:
    selected = _require_exact_text(value, maximum=4096)
    path = Path(selected)
    if (
        selected == "/"
        or selected.startswith("//")
        or not path.is_absolute()
        or any(part in {".", ".."} for part in path.parts)
        or os.path.normpath(selected) != selected
        or str(path) != selected
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    return selected


def _validate_browser_worker_request_snapshot(
    value: dict[str, Any],
) -> BrowserWorkerRequest:
    expected = {
        "protocol",
        "frame",
        "challenge",
        "run_id",
        "source_commit",
        "source_tree",
        "cdp_endpoint",
        "evidence_root",
        "operator_proof",
        "tls_certificate_sha256",
        "chromium_pid",
    }
    _require_exact_keys(value, expected)
    if value["protocol"] != BROWSER_WORKER_PROTOCOL or value["frame"] != "REQUEST":
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    challenge = _require_exact_text(value["challenge"], pattern=_SHA256_TEXT)
    run_id = _require_exact_text(value["run_id"], pattern=_RUN_ID)
    source_commit = _require_exact_text(value["source_commit"], pattern=_GIT_OID_TEXT)
    source_tree = _require_exact_text(value["source_tree"], pattern=_GIT_OID_TEXT)
    certificate = _require_exact_text(
        value["tls_certificate_sha256"],
        pattern=_SHA256_TEXT,
    )
    cdp_endpoint = _require_exact_text(value["cdp_endpoint"], maximum=64)
    endpoint = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", cdp_endpoint)
    if endpoint is None or int(endpoint.group(1)) > 65_535:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    evidence_root = _validate_evidence_root_text(value["evidence_root"])
    operator_proof_json, batch_id, operator_proof_sha256 = (
        _validate_operator_proof_snapshot(value["operator_proof"])
    )
    chromium_pid = _require_positive_integer(value["chromium_pid"])
    if chromium_pid <= 1:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    return BrowserWorkerRequest(
        protocol=BROWSER_WORKER_PROTOCOL,
        frame="REQUEST",
        challenge=challenge,
        run_id=run_id,
        source_commit=source_commit,
        source_tree=source_tree,
        cdp_endpoint=cdp_endpoint,
        evidence_root=evidence_root,
        operator_proof_json=operator_proof_json,
        operator_batch_id=batch_id,
        operator_proof_sha256=operator_proof_sha256,
        tls_certificate_sha256=certificate,
        chromium_pid=chromium_pid,
    )


def validate_browser_worker_request(
    value: Mapping[str, Any],
) -> BrowserWorkerRequest:
    snapshot, _ = _snapshot_browser_worker_value(
        value,
        maximum_bytes=_BROWSER_REQUEST_LIMIT,
    )
    return _validate_browser_worker_request_snapshot(snapshot)


def _validate_browser_worker_ready_snapshot(
    value: dict[str, Any],
) -> BrowserWorkerReady:
    expected = {
        "protocol",
        "frame",
        "challenge",
        "config_sha256",
        "worker_pid",
        "worker_start_ticks",
        "source_commit",
        "source_tree",
        "chromium_pid",
        "browser_start_time",
        "python_executable_sha256",
        "module_manifest_sha256",
        "driver_sha256",
        "node_sha256",
        "preflight_sha256",
    }
    _require_exact_keys(value, expected)
    if value["protocol"] != BROWSER_WORKER_PROTOCOL or value["frame"] != "READY":
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    worker_pid = _require_positive_integer(value["worker_pid"])
    chromium_pid = _require_positive_integer(value["chromium_pid"])
    browser_start_time = _require_exact_text(
        value["browser_start_time"],
        pattern=_START_TICKS_TEXT,
    )
    if worker_pid <= 1 or chromium_pid <= 1:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    return BrowserWorkerReady(
        protocol=BROWSER_WORKER_PROTOCOL,
        frame="READY",
        challenge=_require_exact_text(value["challenge"], pattern=_SHA256_TEXT),
        config_sha256=_require_exact_text(
            value["config_sha256"], pattern=_SHA256_TEXT
        ),
        worker_pid=worker_pid,
        worker_start_ticks=_require_positive_integer(value["worker_start_ticks"]),
        source_commit=_require_exact_text(
            value["source_commit"], pattern=_GIT_OID_TEXT
        ),
        source_tree=_require_exact_text(value["source_tree"], pattern=_GIT_OID_TEXT),
        chromium_pid=chromium_pid,
        browser_start_time=browser_start_time,
        python_executable_sha256=_require_exact_text(
            value["python_executable_sha256"], pattern=_SHA256_TEXT
        ),
        module_manifest_sha256=_require_exact_text(
            value["module_manifest_sha256"], pattern=_SHA256_TEXT
        ),
        driver_sha256=_require_exact_text(
            value["driver_sha256"], pattern=_SHA256_TEXT
        ),
        node_sha256=_require_exact_text(value["node_sha256"], pattern=_SHA256_TEXT),
        preflight_sha256=_require_exact_text(
            value["preflight_sha256"], pattern=_SHA256_TEXT
        ),
    )


def validate_browser_worker_ready(
    value: Mapping[str, Any],
) -> BrowserWorkerReady:
    snapshot, _ = _snapshot_browser_worker_value(
        value,
        maximum_bytes=_BROWSER_READY_LIMIT,
    )
    return _validate_browser_worker_ready_snapshot(snapshot)


def _validate_browser_worker_result_snapshot(
    value: dict[str, Any],
) -> BrowserWorkerResult:
    expected = {
        "protocol",
        "frame",
        "challenge",
        "config_sha256",
        "ready_sha256",
        "worker_pid",
        "worker_start_ticks",
        "proof",
    }
    _require_exact_keys(value, expected)
    if value["protocol"] != BROWSER_WORKER_PROTOCOL or value["frame"] != "RESULT":
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    proof_json = _validate_browser_proof_snapshot(value["proof"])
    worker_pid = _require_positive_integer(value["worker_pid"])
    if worker_pid <= 1:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )
    return BrowserWorkerResult(
        protocol=BROWSER_WORKER_PROTOCOL,
        frame="RESULT",
        challenge=_require_exact_text(value["challenge"], pattern=_SHA256_TEXT),
        config_sha256=_require_exact_text(
            value["config_sha256"], pattern=_SHA256_TEXT
        ),
        ready_sha256=_require_exact_text(
            value["ready_sha256"], pattern=_SHA256_TEXT
        ),
        worker_pid=worker_pid,
        worker_start_ticks=_require_positive_integer(value["worker_start_ticks"]),
        proof_json=proof_json,
    )


def _validate_browser_worker_wire_snapshot(value: dict[str, Any]) -> None:
    frame = value.get("frame")
    if frame == "REQUEST":
        _validate_browser_worker_request_snapshot(value)
    elif frame == "READY":
        _validate_browser_worker_ready_snapshot(value)
    elif frame == "RESULT":
        _validate_browser_worker_result_snapshot(value)
    else:
        raise LocalStagingAcceptanceError(
            "local acceptance browser protocol differs"
        )


def validate_browser_worker_result(
    value: Mapping[str, Any],
) -> BrowserWorkerResult:
    snapshot, _ = _snapshot_browser_worker_value(
        value,
        maximum_bytes=_BROWSER_RESULT_LIMIT,
    )
    return _validate_browser_worker_result_snapshot(snapshot)


def browser_worker_request_sha256(value: Mapping[str, Any]) -> str:
    snapshot, framed = _snapshot_browser_worker_value(
        value,
        maximum_bytes=_BROWSER_REQUEST_LIMIT,
    )
    _validate_browser_worker_request_snapshot(snapshot)
    return hashlib.sha256(framed).hexdigest()


def browser_worker_ready_sha256(value: Mapping[str, Any]) -> str:
    snapshot, framed = _snapshot_browser_worker_value(
        value,
        maximum_bytes=_BROWSER_READY_LIMIT,
    )
    _validate_browser_worker_ready_snapshot(snapshot)
    return hashlib.sha256(framed).hexdigest()


def new_browser_worker_identifiers() -> tuple[str, str]:
    return os.urandom(32).hex(), os.urandom(16).hex()


def validate_browser_worker_ready_attestation(
    request: BrowserWorkerRequest,
    ready: BrowserWorkerReady,
    expected: BrowserWorkerExpectedAttestation,
) -> None:
    if type(expected) is not BrowserWorkerExpectedAttestation:
        raise LocalStagingAcceptanceError(
            "local acceptance browser attestation differs"
        )
    try:
        expected_challenge = _require_exact_text(
            expected.challenge,
            pattern=_SHA256_TEXT,
        )
        expected_run_id = _require_exact_text(expected.run_id, pattern=_RUN_ID)
        expected_source_commit = _require_exact_text(
            expected.source_commit,
            pattern=_GIT_OID_TEXT,
        )
        expected_source_tree = _require_exact_text(
            expected.source_tree,
            pattern=_GIT_OID_TEXT,
        )
        expected_cdp = _require_exact_text(expected.cdp_endpoint, maximum=64)
        expected_evidence = _validate_evidence_root_text(expected.evidence_root)
        expected_tls = _require_exact_text(
            expected.tls_certificate_sha256,
            pattern=_SHA256_TEXT,
        )
        expected_browser_pid = _require_positive_integer(expected.chromium_pid)
        expected_worker_pid = _require_positive_integer(expected.worker_pid)
        expected_worker_start = _require_positive_integer(
            expected.worker_start_ticks
        )
        hashes = (
            _require_exact_text(
                expected.python_executable_sha256,
                pattern=_SHA256_TEXT,
            ),
            _require_exact_text(expected.module_manifest_sha256, pattern=_SHA256_TEXT),
            _require_exact_text(expected.driver_sha256, pattern=_SHA256_TEXT),
            _require_exact_text(expected.node_sha256, pattern=_SHA256_TEXT),
            _require_exact_text(expected.preflight_sha256, pattern=_SHA256_TEXT),
        )
    except LocalStagingAcceptanceError:
        raise LocalStagingAcceptanceError(
            "local acceptance browser attestation differs"
        ) from None
    if (
        expected_browser_pid <= 1
        or expected_worker_pid <= 1
        or not isinstance(expected.browser_start_time, str)
        or not expected.browser_start_time.isascii()
        or not expected.browser_start_time.isdigit()
        or expected.browser_start_time.startswith("0")
        or request.challenge != expected_challenge
        or request.run_id != expected_run_id
        or request.source_commit != expected_source_commit
        or request.source_tree != expected_source_tree
        or request.cdp_endpoint != expected_cdp
        or request.evidence_root != expected_evidence
        or request.tls_certificate_sha256 != expected_tls
        or request.chromium_pid != expected_browser_pid
        or ready.worker_pid != expected_worker_pid
        or ready.worker_start_ticks != expected_worker_start
        or ready.source_commit != expected_source_commit
        or ready.source_tree != expected_source_tree
        or ready.chromium_pid != expected_browser_pid
        or ready.browser_start_time != expected.browser_start_time
        or (
            ready.python_executable_sha256,
            ready.module_manifest_sha256,
            ready.driver_sha256,
            ready.node_sha256,
            ready.preflight_sha256,
        )
        != hashes
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser attestation differs"
        )


def validate_browser_worker_transition(
    request_value: Mapping[str, Any],
    ready_value: Mapping[str, Any],
    result_value: Mapping[str, Any] | None = None,
    *,
    expected: BrowserWorkerExpectedAttestation,
) -> tuple[BrowserWorkerRequest, BrowserWorkerReady, BrowserWorkerResult | None]:
    request_snapshot, request_frame = _snapshot_browser_worker_value(
        request_value,
        maximum_bytes=_BROWSER_REQUEST_LIMIT,
    )
    ready_snapshot, ready_frame = _snapshot_browser_worker_value(
        ready_value,
        maximum_bytes=_BROWSER_READY_LIMIT,
    )
    request = _validate_browser_worker_request_snapshot(request_snapshot)
    ready = _validate_browser_worker_ready_snapshot(ready_snapshot)
    request_sha256 = hashlib.sha256(request_frame).hexdigest()
    ready_sha256 = hashlib.sha256(ready_frame).hexdigest()
    if (
        ready.challenge != request.challenge
        or ready.config_sha256 != request_sha256
        or ready.source_commit != request.source_commit
        or ready.source_tree != request.source_tree
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser transition differs"
        )
    validate_browser_worker_ready_attestation(request, ready, expected)
    result: BrowserWorkerResult | None = None
    if result_value is not None:
        result_snapshot, _ = _snapshot_browser_worker_value(
            result_value,
            maximum_bytes=_BROWSER_RESULT_LIMIT,
        )
        result = _validate_browser_worker_result_snapshot(result_snapshot)
        proof = json.loads(result.proof_json)
        if (
            result.challenge != request.challenge
            or result.config_sha256 != request_sha256
            or result.ready_sha256 != ready_sha256
            or result.worker_pid != ready.worker_pid
            or result.worker_start_ticks != ready.worker_start_ticks
            or proof["batch_id"] != request.operator_batch_id
            or proof["source_commit"] != request.source_commit
            or proof["source_tree"] != request.source_tree
            or proof["driver_sha256"] != ready.driver_sha256
            or proof["node_sha256"] != ready.node_sha256
            or proof["operator_proof_sha256"] != request.operator_proof_sha256
            or proof["browser_pid"] != request.chromium_pid
            or proof["browser_start_time"] != expected.browser_start_time
            or proof["tls_certificate_sha256"]
            != request.tls_certificate_sha256
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance browser transition differs"
            )
    return request, ready, result


def parse_browser_worker_arguments(
    arguments: list[str] | tuple[str, ...],
) -> BrowserWorkerArguments:
    if len(arguments) != 5 or arguments[0] != BROWSER_WORKER_HIDDEN_MODE:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker arguments differ"
        )
    raw_descriptors = arguments[1:]
    if any(
        not isinstance(value, str)
        or len(value) > 7
        or _CANONICAL_FD.fullmatch(value) is None
        for value in raw_descriptors
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker arguments differ"
        )
    descriptors = tuple(int(value) for value in raw_descriptors)
    if (
        len(set(descriptors)) != 4
        or any(value > _BROWSER_WORKER_MAX_FD for value in descriptors)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker arguments differ"
        )
    return BrowserWorkerArguments(*descriptors)


def validate_browser_worker_descriptors(
    arguments: BrowserWorkerArguments,
) -> BrowserWorkerArguments:
    if type(arguments) is not BrowserWorkerArguments:
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker descriptors differ"
        )
    descriptors = (
        arguments.request_descriptor,
        arguments.ready_descriptor,
        arguments.secret_descriptor,
        arguments.result_descriptor,
    )
    expected_access = (
        os.O_RDONLY,
        os.O_WRONLY,
        os.O_RDONLY,
        os.O_WRONLY,
    )
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        identities: list[tuple[int, int]] = []
        for descriptor, access in zip(descriptors, expected_access, strict=True):
            if (
                type(descriptor) is not int
                or descriptor <= 2
                or descriptor > _BROWSER_WORKER_MAX_FD
                or (soft_limit != resource.RLIM_INFINITY and descriptor >= soft_limit)
            ):
                raise OSError
            info = os.fstat(descriptor)
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
            descriptor_target = os.readlink(f"/proc/self/fd/{descriptor}")
            if (
                not stat.S_ISFIFO(info.st_mode)
                or flags != access
                or descriptor_target != f"pipe:[{info.st_ino}]"
            ):
                raise OSError
            identities.append((info.st_dev, info.st_ino))
        if len(set(identities)) != 4:
            raise OSError
        for descriptor in descriptors:
            descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
            fcntl.fcntl(descriptor, fcntl.F_SETFD, descriptor_flags | fcntl.FD_CLOEXEC)
            os.set_inheritable(descriptor, False)
    except (OSError, ValueError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance browser worker descriptors differ"
        ) from None
    return arguments


def parse_single_json_object(raw: bytes) -> Mapping[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker response differs"
        ) from None
    if not isinstance(value, dict):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker response differs"
        )
    return value


def parse_single_json_array(raw: bytes) -> list[Mapping[str, Any]]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker response differs"
        ) from None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker response differs"
        )
    return value


def parse_container_create_output(raw: bytes) -> str:
    if re.fullmatch(rb"[0-9a-f]{64}\n", raw) is None:
        raise LocalStagingAcceptanceError(
            "local acceptance materializer creation differs"
        )
    return raw[:-1].decode("ascii")


def parse_volume_create_output(raw: bytes, *, expected_name: str) -> None:
    if raw != f"{expected_name}\n".encode("ascii"):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer volume creation differs"
        )


def _parse_docker_timestamp(value: object, *, allow_zero: bool) -> int:
    if not isinstance(value, str) or (not allow_zero and value == _DOCKER_ZERO_TIME):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker timestamp differs"
        )
    match = re.fullmatch(
        r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})"
        r"(?:\.(\d{1,9}))?Z",
        value,
    )
    if match is None:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker timestamp differs"
        )
    try:
        year, month, day, hour, minute, second = (
            int(selected) for selected in match.groups()[:6]
        )
        parsed = datetime(year, month, day, hour, minute, second)
    except ValueError:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker timestamp differs"
        ) from None
    fractional = match.group(7) or ""
    nanoseconds = int(fractional.ljust(9, "0")) if fractional else 0
    whole_seconds = (
        (parsed.toordinal() - 1) * 86_400
        + hour * 3_600
        + minute * 60
        + second
    )
    return whole_seconds * 1_000_000_000 + nanoseconds


def validate_materializer_volume_inspect(
    value: Mapping[str, Any],
    *,
    invocation: MaterializerInvocation,
) -> MaterializerVolumeFingerprint:
    labels = {
        "buffalo.contract": ACCEPTANCE_CONTRACT,
        "buffalo.run": invocation.run_id,
        "buffalo.role": MATERIALIZER_VOLUME_ROLE,
    }
    expected_mountpoint = (
        f"/var/lib/docker/volumes/{invocation.volume_name}/_data"
    )
    try:
        if (
            invocation.image_id != FROZEN_IMAGE_ID
            or value["Name"] != invocation.volume_name
            or value["Driver"] != "local"
            or value["Scope"] != "local"
            or value["Labels"] != labels
            or value["Options"] is not None
            or value["Mountpoint"] != expected_mountpoint
            or set(value)
            != {
                "CreatedAt",
                "Driver",
                "Labels",
                "Mountpoint",
                "Name",
                "Options",
                "Scope",
            }
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance materializer volume differs"
            )
        _parse_docker_timestamp(value["CreatedAt"], allow_zero=False)
    except LocalStagingAcceptanceError:
        raise
    except (KeyError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer volume differs"
        ) from None
    return MaterializerVolumeFingerprint(
        name=value["Name"],
        driver=value["Driver"],
        scope=value["Scope"],
        labels=tuple(sorted(value["Labels"].items())),
        options=None,
        mountpoint=value["Mountpoint"],
        created_at=value["CreatedAt"],
    )


def validate_docker_version(value: Mapping[str, Any]) -> None:
    try:
        client = value["Client"]
        server = value["Server"]
        component_rows = server["Components"]
        components = {item["Name"]: item for item in component_rows}
        if (
            not isinstance(component_rows, list)
            or len(component_rows) != len(components)
            or set(components) != {"Engine", "containerd", "runc", "docker-init"}
            or client["Version"] != _DOCKER_CLIENT_VERSION
            or client["ApiVersion"] != _DOCKER_API_VERSION
            or client["Os"] != "linux"
            or client["Arch"] != "amd64"
            or server["Version"] != _DOCKER_CLIENT_VERSION
            or server["ApiVersion"] != _DOCKER_API_VERSION
            or server["Os"] != "linux"
            or server["Arch"] != "amd64"
            or components["Engine"]["Version"] != _DOCKER_CLIENT_VERSION
            or components["containerd"]["Version"] != "v2.1.4"
            or components["runc"]["Version"] != "1.2.4"
            or components["docker-init"]["Version"] != "0.19.0"
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance Docker version differs"
            )
    except LocalStagingAcceptanceError:
        raise
    except (KeyError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker version differs"
        ) from None


def validate_docker_info(value: Mapping[str, Any]) -> None:
    try:
        cpu_count = value["NCPU"]
        memory_bytes = value["MemTotal"]
        runtimes = value["Runtimes"]
        if not isinstance(runtimes, dict) or set(runtimes) != {
            "io.containerd.runc.v2",
            "runc",
        }:
            raise LocalStagingAcceptanceError(
                "local acceptance Docker daemon differs"
            )
        for runtime in runtimes.values():
            if (
                not isinstance(runtime, dict)
                or runtime.get("path") != "runc"
                or set(runtime.get("status", {}))
                != {"org.opencontainers.runtime-spec.features"}
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance Docker daemon differs"
                )
            features = parse_single_json_object(
                runtime["status"][
                    "org.opencontainers.runtime-spec.features"
                ].encode("utf-8")
            )
            if (
                features["linux"]["cgroup"]["v2"] is not True
                or features["linux"]["seccomp"]["enabled"] is not True
                or features["annotations"]["org.opencontainers.runc.version"]
                != "1.2.4"
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance Docker daemon differs"
                )
        if (
            value["ServerVersion"] != _DOCKER_CLIENT_VERSION
            or value["Driver"] != "overlay2"
            or value["CgroupVersion"] != "2"
            or value["CgroupDriver"] != "cgroupfs"
            or value["OSType"] != "linux"
            or value["Architecture"] != "x86_64"
            or value["DefaultRuntime"] != "runc"
            or value["MemoryLimit"] is not True
            or value["SwapLimit"] is not True
            or value["PidsLimit"] is not True
            or type(cpu_count) is not int
            or cpu_count < 2
            or type(memory_bytes) is not int
            or memory_bytes < 1_073_741_824
            or value["DockerRootDir"] != "/var/lib/docker"
            or value["SecurityOptions"]
            != ["name=seccomp,profile=builtin", "name=cgroupns"]
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance Docker daemon differs"
            )
    except LocalStagingAcceptanceError:
        raise
    except (KeyError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker daemon differs"
        ) from None


def validate_frozen_image_inspect(
    value: Mapping[str, Any],
    *,
    image_id: str,
) -> None:
    """Prove the materializer image is the exact frozen service image."""

    try:
        config = value["Config"]
        rootfs = value["RootFS"]
        if (
            image_id != FROZEN_IMAGE_ID
            or value["Id"] != FROZEN_IMAGE_ID
            or value["Architecture"] != "amd64"
            or value["Os"] != "linux"
            or value["RepoDigests"] != []
            or config["User"] != "0:0"
            or tuple(config["Entrypoint"]) != _SERVICE_ENTRYPOINT
            or config["Cmd"] is not None
            or config["WorkingDir"] != "/app"
            or config["StopSignal"] != "SIGTERM"
            or tuple(config["Env"]) != _IMAGE_ENVIRONMENT
            or config.get("Volumes") is not None
            or config.get("ExposedPorts") is not None
            or config.get("Labels") is not None
            or rootfs["Type"] != "layers"
            or tuple(rootfs["Layers"]) != FROZEN_IMAGE_ROOTFS_LAYERS
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance image identity differs"
            )
    except (KeyError, TypeError):
        raise LocalStagingAcceptanceError(
            "local acceptance image identity differs"
        ) from None


def _expected_materializer_mounts(
    invocation: MaterializerInvocation,
) -> list[dict[str, Any]]:
    mounts: list[dict[str, Any]] = [
        {
            "Type": "volume",
            "Source": invocation.volume_name,
            "Target": "/data",
            "VolumeOptions": {"NoCopy": True, "DriverConfig": {}},
        }
    ]
    if invocation.ingress is not None:
        mounts.extend(
            (
                {
                    "Type": "bind",
                    "Source": str(invocation.ingress.research_root),
                    "Target": "/mnt/buffalo-accepted-research",
                    "ReadOnly": True,
                    "BindOptions": {"Propagation": "rprivate"},
                },
                {
                    "Type": "bind",
                    "Source": str(invocation.ingress.inventory_path),
                    "Target": "/mnt/deployment-inventory.json",
                    "ReadOnly": True,
                    "BindOptions": {"Propagation": "rprivate"},
                },
                {
                    "Type": "bind",
                    "Source": str(invocation.ingress.bundle_path),
                    "Target": (
                        "/mnt/buffalo-procurement-os-accepted-source-"
                        "608929ad.bundle"
                    ),
                    "ReadOnly": True,
                    "BindOptions": {"Propagation": "rprivate"},
                },
            )
        )
    return mounts


def validate_materializer_container_inspect(
    value: Mapping[str, Any],
    *,
    invocation: MaterializerInvocation,
    expected_state: str = "created",
) -> str:
    """Validate the complete security envelope before start or cleanup."""

    if expected_state not in {"created", "running", "exited"}:
        raise LocalStagingAcceptanceError(
            "local acceptance materializer state differs"
        )

    labels = {
        "buffalo.contract": ACCEPTANCE_CONTRACT,
        "buffalo.run": invocation.run_id,
        "buffalo.role": MATERIALIZER_ROLE,
    }
    expected_mounts = _expected_materializer_mounts(invocation)
    try:
        container_id = value["Id"]
        state = value["State"]
        host = value["HostConfig"]
        config = value["Config"]
        created_at = _parse_docker_timestamp(value["Created"], allow_zero=False)
        started_at = _parse_docker_timestamp(state["StartedAt"], allow_zero=True)
        finished_at = _parse_docker_timestamp(state["FinishedAt"], allow_zero=True)
        if expected_state == "created":
            state_matches = (
                type(state["Pid"]) is int
                and state["Pid"] == 0
                and type(state["ExitCode"]) is int
                and state["ExitCode"] == 0
                and state["StartedAt"] == _DOCKER_ZERO_TIME
                and state["FinishedAt"] == _DOCKER_ZERO_TIME
            )
        elif expected_state == "running":
            state_matches = (
                type(state["Pid"]) is int
                and state["Pid"] > 0
                and type(state["ExitCode"]) is int
                and state["ExitCode"] == 0
                and state["StartedAt"] != _DOCKER_ZERO_TIME
                and state["FinishedAt"] == _DOCKER_ZERO_TIME
                and created_at <= started_at
            )
        else:
            state_matches = (
                type(state["Pid"]) is int
                and state["Pid"] == 0
                and type(state["ExitCode"]) is int
                and state["ExitCode"] == 0
                and state["StartedAt"] != _DOCKER_ZERO_TIME
                and state["FinishedAt"] != _DOCKER_ZERO_TIME
                and created_at <= started_at <= finished_at
            )
        if expected_state == "created":
            runtime_paths_match = (
                value["ResolvConfPath"] == ""
                and value["HostnamePath"] == ""
                and value["HostsPath"] == ""
            )
            expected_oom_kill_disable: object = False
        else:
            runtime_root = f"/var/lib/docker/containers/{container_id}"
            runtime_paths_match = (
                value["ResolvConfPath"] == f"{runtime_root}/resolv.conf"
                and value["HostnamePath"] == f"{runtime_root}/hostname"
                and value["HostsPath"] == f"{runtime_root}/hosts"
            )
            expected_oom_kill_disable = None
        if (
            not isinstance(container_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
            or value["Image"] != invocation.image_id
            or value["Platform"] != "linux"
            or value["Path"] != "/usr/bin/tini"
            or tuple(value["Args"]) != _MATERIALIZER_COMMAND
            or value["Name"] != f"/{invocation.container_name}"
            or value["RestartCount"] != 0
            or value["LogPath"] != ""
            or not runtime_paths_match
            or state["Status"] != expected_state
            or state["Running"] is not (expected_state == "running")
            or not state_matches
            or state["Paused"] is not False
            or state["Restarting"] is not False
            or state["OOMKilled"] is not False
            or state["Dead"] is not False
            or state["Error"] != ""
            or host["LogConfig"] != {"Type": "none", "Config": {}}
            or host["NetworkMode"] != "none"
            or host["PortBindings"] != {}
            or host["RestartPolicy"]
            != {"Name": "no", "MaximumRetryCount": 0}
            or host["AutoRemove"] is not False
            or host["VolumesFrom"] is not None
            or host["Binds"] is not None
            or host["Links"] is not None
            or tuple(host["CapAdd"]) != _MATERIALIZER_CAPABILITIES
            or host["CapDrop"] != ["ALL"]
            or host["CgroupnsMode"] != "private"
            or host["Dns"] != []
            or host["DnsOptions"] != []
            or host["DnsSearch"] != []
            or host["ExtraHosts"] is not None
            or host["GroupAdd"] is not None
            or host["IpcMode"] != "none"
            or host["PidMode"] != ""
            or host["UTSMode"] != ""
            or host["UsernsMode"] != ""
            or host["Privileged"] is not False
            or host["PublishAllPorts"] is not False
            or host["ReadonlyRootfs"] is not True
            or host["SecurityOpt"] != ["no-new-privileges=true"]
            or host["Tmpfs"]
            != {
                "/run/buffalo-research-materializer": (
                    "rw,nosuid,nodev,noexec,mode=0700,uid=0,gid=0,"
                    "size=67108864"
                )
            }
            or host["Runtime"] != "runc"
            or host["Memory"] != 1_073_741_824
            or host["MemorySwap"] != 1_073_741_824
            or host["MemorySwappiness"] is not None
            or host["NanoCpus"] != 2_000_000_000
            or host["OomKillDisable"] is not expected_oom_kill_disable
            or host["PidsLimit"] != 64
            or host["Devices"] != []
            or host["DeviceRequests"] is not None
            or host["DeviceCgroupRules"] is not None
            or host["Ulimits"] != []
            or "Sysctls" in host
            or host["ShmSize"] != 67_108_864
            or host["MaskedPaths"]
            != [
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
            ]
            or host["ReadonlyPaths"]
            != [
                "/proc/bus",
                "/proc/fs",
                "/proc/irq",
                "/proc/sys",
                "/proc/sysrq-trigger",
            ]
            or host["Mounts"] != expected_mounts
            or config["User"] != "0:0"
            or config["AttachStdin"] is not False
            or config["AttachStdout"] is not True
            or config["AttachStderr"] is not True
            or config["Tty"] is not False
            or config["OpenStdin"] is not False
            or tuple(config["Env"]) != _IMAGE_ENVIRONMENT
            or tuple(config["Cmd"]) != _MATERIALIZER_COMMAND
            or config["Healthcheck"] != {"Test": ["NONE"]}
            or config["Image"] != invocation.image_id
            or config.get("Volumes") is not None
            or config["WorkingDir"] != "/app"
            or config["Entrypoint"] != ["/usr/bin/tini"]
            or config["Labels"] != labels
            or config["StopSignal"] != "SIGTERM"
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance materializer container differs"
            )
        runtime_mounts = value["Mounts"]
        if (
            not isinstance(runtime_mounts, list)
            or len(runtime_mounts) != len(expected_mounts)
            or len({item["Destination"] for item in runtime_mounts})
            != len(runtime_mounts)
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance materializer mounts differ"
            )
        observed_mounts = {item["Destination"]: item for item in runtime_mounts}
        expected_destinations = {item["Target"] for item in expected_mounts}
        if set(observed_mounts) != expected_destinations:
            raise LocalStagingAcceptanceError(
                "local acceptance materializer mounts differ"
            )
        data = observed_mounts["/data"]
        if (
            data["Type"] != "volume"
            or data["Name"] != invocation.volume_name
            or data["Driver"] != "local"
            or data["Source"]
            != f"/var/lib/docker/volumes/{invocation.volume_name}/_data"
            or data["Mode"] != "z"
            or data["RW"] is not True
            or data["Propagation"] != ""
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance materializer mounts differ"
            )
        for expected in expected_mounts[1:]:
            item = observed_mounts[expected["Target"]]
            if (
                item["Type"] != "bind"
                or item["Source"] != expected["Source"]
                or item["Mode"] != ""
                or item["RW"] is not False
                or item["Propagation"] != "rprivate"
            ):
                raise LocalStagingAcceptanceError(
                    "local acceptance materializer mounts differ"
                )
    except LocalStagingAcceptanceError:
        raise
    except (KeyError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer container differs"
        ) from None
    return container_id


def materializer_container_envelope_sha256(value: Mapping[str, Any]) -> str:
    """Hash every immutable container field used across start/exit."""

    keys = (
        "Id",
        "Created",
        "Path",
        "Args",
        "Image",
        "LogPath",
        "Name",
        "RestartCount",
        "Driver",
        "Platform",
        "MountLabel",
        "ProcessLabel",
        "AppArmorProfile",
        "ExecIDs",
        "HostConfig",
        "GraphDriver",
        "Mounts",
        "Config",
    )
    try:
        projection = {key: value[key] for key in keys}
        projection["HostConfig"] = {
            key: selected
            for key, selected in value["HostConfig"].items()
            if key != "OomKillDisable"
        }
        encoded = json.dumps(
            projection,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (KeyError, TypeError, ValueError):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer envelope differs"
        ) from None
    return hashlib.sha256(encoded).hexdigest()


def _require_success(
    result: BoundedProcessResult,
    *,
    stdout: bytes | None = None,
    stderr: bytes = b"",
) -> None:
    if (
        result.returncode != 0
        or result.stderr != stderr
        or (stdout is not None and result.stdout != stdout)
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker operation failed"
        )


def _docker_inspect_one(
    client: TrustedDockerClient,
    config_root: Path,
    resource: str,
    identity: str,
) -> Mapping[str, Any]:
    if resource not in {"container", "image", "volume"}:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker inspection differs"
        )
    result = run_docker_command(
        client,
        config_root,
        (resource, "inspect", identity),
    )
    _require_success(result)
    values = parse_single_json_array(result.stdout)
    if len(values) != 1:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker inspection differs"
        )
    return values[0]


def _docker_name_inventory(
    client: TrustedDockerClient,
    config_root: Path,
    *,
    resource: str,
) -> tuple[tuple[str, str], ...]:
    if resource == "container":
        arguments = (
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--format",
            "{{.ID}}\t{{.Names}}",
        )
        pattern = re.compile(r"\A([0-9a-f]{64})\t([a-zA-Z0-9][a-zA-Z0-9_.-]*)\Z")
    elif resource == "volume":
        arguments = ("volume", "ls", "--format", "{{.Name}}")
        pattern = re.compile(r"\A()([a-zA-Z0-9][a-zA-Z0-9_.-]*)\Z")
    else:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker inventory differs"
        )
    result = run_docker_command(client, config_root, arguments)
    _require_success(result)
    try:
        text = result.stdout.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker inventory differs"
        ) from None
    observed: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = pattern.fullmatch(line)
        if match is None:
            raise LocalStagingAcceptanceError(
                "local acceptance Docker inventory differs"
            )
        observed.append((match.group(1), match.group(2)))
    if len(observed) != len(set(observed)):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker inventory differs"
        )
    return tuple(observed)


def require_docker_name_absent(
    client: TrustedDockerClient,
    config_root: Path,
    *,
    resource: str,
    name: str,
) -> None:
    if _SAFE_DOCKER_NAME.fullmatch(name) is None:
        raise LocalStagingAcceptanceError(
            "local acceptance Docker name differs"
        )
    inventory = _docker_name_inventory(
        client,
        config_root,
        resource=resource,
    )
    if any(observed_name == name for _, observed_name in inventory):
        raise LocalStagingAcceptanceError(
            "local acceptance Docker name is already present"
        )


def attest_docker_materializer_runtime(
    client: TrustedDockerClient,
    config_root: Path,
) -> None:
    _validate_docker_socket()
    version = run_docker_command(
        client,
        config_root,
        ("version", "--format", "{{json .}}"),
    )
    _require_success(version)
    validate_docker_version(parse_single_json_object(version.stdout))
    info = run_docker_command(
        client,
        config_root,
        ("info", "--format", "{{json .}}"),
    )
    _require_success(info)
    validate_docker_info(parse_single_json_object(info.stdout))
    image = _docker_inspect_one(
        client,
        config_root,
        "image",
        FROZEN_IMAGE_ID,
    )
    validate_frozen_image_inspect(image, image_id=FROZEN_IMAGE_ID)


def _observe_frozen_browser_dependency_source(
    client: TrustedDockerClient,
    config_root: Path,
    *,
    run_id: str,
) -> _BrowserDependencyImageSnapshot:
    """Read the exact frozen image without creating any daemon resource."""

    _validate_browser_dependency_run_id(run_id)
    build_sources = _open_browser_dependency_build_sources()
    try:
        attest_docker_materializer_runtime(client, config_root)
        arguments = _build_browser_dependency_image_save_argv(
            docker_client=client,
        )
        exported = run_docker_argv(
            client,
            config_root,
            arguments,
            timeout_seconds=_BROWSER_DEPENDENCY_IMAGE_EXPORT_TIMEOUT,
            stdout_limit=_BROWSER_DEPENDENCY_IMAGE_EXPORT_LIMIT,
            stderr_limit=_DOCKER_METADATA_LIMIT,
        )
        _require_success(exported)
        _validate_browser_dependency_build_sources(build_sources)
        audit_entries = _read_browser_dependency_audit_entries(build_sources)
        snapshot = _parse_browser_dependency_image_export(exported.stdout)
        _validate_browser_dependency_build_sources(build_sources)
        return replace(snapshot, audit_entries=audit_entries)
    finally:
        _close_browser_dependency_build_sources(build_sources)


def _open_frozen_browser_runtime_bundle(
    client: TrustedDockerClient,
    config_root: Path,
    *,
    run_id: str,
) -> _PinnedBrowserRuntimeBundle:
    """Materialize sealed exact bytes; no process or capability is released."""

    runtime = _snapshot_browser_python_runtime_source()
    dependency = _observe_frozen_browser_dependency_source(
        client,
        config_root,
        run_id=run_id,
    )
    entries = _collect_browser_runtime_bundle_entries(runtime, dependency)
    bundle = _seal_browser_runtime_bundle(entries)
    try:
        if (
            bundle.entries != _BROWSER_RUNTIME_BUNDLE_EXPECTED_ENTRIES
            or bundle.regular_bytes
            != _BROWSER_RUNTIME_BUNDLE_EXPECTED_REGULAR_BYTES
            or bundle.size != _BROWSER_RUNTIME_BUNDLE_EXPECTED_BYTES
            or bundle.manifest_sha256
            != _BROWSER_RUNTIME_BUNDLE_EXPECTED_MANIFEST_SHA256
            or bundle.sha256 != _BROWSER_RUNTIME_BUNDLE_EXPECTED_SHA256
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance frozen browser runtime bundle differs"
            )
        bundle._source_token = _BROWSER_FROZEN_RUNTIME_BUNDLE_TOKEN
        _validate_frozen_browser_runtime_bundle(bundle)
        return bundle
    except BaseException:
        bundle.close()
        raise


def create_materializer_volume(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
) -> MaterializerVolumeFingerprint:
    require_docker_name_absent(
        client,
        config_root,
        resource="volume",
        name=invocation.volume_name,
    )
    arguments = build_materializer_volume_create_argv(
        docker_client=client,
        invocation=invocation,
    )
    try:
        result = run_docker_argv(client, config_root, arguments)
        _require_success(result)
        parse_volume_create_output(
            result.stdout,
            expected_name=invocation.volume_name,
        )
    except LocalStagingAcceptanceError:
        # An interrupted client can leave a committed daemon operation.  Only
        # the exact code-owned volume is recoverable; foreign state is retained.
        try:
            observed = _docker_inspect_one(
                client,
                config_root,
                "volume",
                invocation.volume_name,
            )
            return validate_materializer_volume_inspect(
                observed,
                invocation=invocation,
            )
        except LocalStagingAcceptanceError:
            raise LocalStagingAcceptanceError(
                "local acceptance materializer volume creation failed"
            ) from None
    observed = _docker_inspect_one(
        client,
        config_root,
        "volume",
        invocation.volume_name,
    )
    return validate_materializer_volume_inspect(observed, invocation=invocation)


def reattest_materializer_volume(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
    fingerprint: MaterializerVolumeFingerprint,
) -> None:
    observed = _docker_inspect_one(
        client,
        config_root,
        "volume",
        invocation.volume_name,
    )
    if (
        validate_materializer_volume_inspect(observed, invocation=invocation)
        != fingerprint
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer volume changed"
        )


def _recover_materializer_container(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
) -> tuple[str, Mapping[str, Any]]:
    observed = _docker_inspect_one(
        client,
        config_root,
        "container",
        invocation.container_name,
    )
    container_id = validate_materializer_container_inspect(
        observed,
        invocation=invocation,
        expected_state="created",
    )
    materializer_container_envelope_sha256(observed)
    return container_id, observed


def create_materializer_container(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
    fingerprint: MaterializerVolumeFingerprint,
) -> tuple[str, Mapping[str, Any]]:
    reattest_materializer_volume(
        client,
        config_root,
        invocation,
        fingerprint,
    )
    require_docker_name_absent(
        client,
        config_root,
        resource="container",
        name=invocation.container_name,
    )
    arguments = build_materializer_create_argv(
        docker_client=client,
        invocation=invocation,
    )
    try:
        result = run_docker_argv(client, config_root, arguments)
        _require_success(result)
        container_id = parse_container_create_output(result.stdout)
        observed = _docker_inspect_one(
            client,
            config_root,
            "container",
            container_id,
        )
        if (
            validate_materializer_container_inspect(
                observed,
                invocation=invocation,
                expected_state="created",
            )
            != container_id
        ):
            raise LocalStagingAcceptanceError(
                "local acceptance materializer creation differs"
            )
        materializer_container_envelope_sha256(observed)
        return container_id, observed
    except LocalStagingAcceptanceError:
        try:
            return _recover_materializer_container(
                client,
                config_root,
                invocation,
            )
        except LocalStagingAcceptanceError:
            raise LocalStagingAcceptanceError(
                "local acceptance materializer creation failed"
            ) from None


def _container_owned_for_cleanup(
    value: Mapping[str, Any],
    *,
    invocation: MaterializerInvocation,
    container_id: str,
    envelope_sha256: str,
) -> bool:
    try:
        return (
            value["Id"] == container_id
            and value["Image"] == FROZEN_IMAGE_ID
            and value["Name"] == f"/{invocation.container_name}"
            and value["Config"]["Labels"]
            == {
                "buffalo.contract": ACCEPTANCE_CONTRACT,
                "buffalo.run": invocation.run_id,
                "buffalo.role": MATERIALIZER_ROLE,
            }
            and materializer_container_envelope_sha256(value) == envelope_sha256
        )
    except (KeyError, TypeError, LocalStagingAcceptanceError):
        return False


def remove_owned_materializer_container(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
    *,
    container_id: str,
    envelope_sha256: str,
) -> None:
    observed = _docker_inspect_one(
        client,
        config_root,
        "container",
        container_id,
    )
    if not _container_owned_for_cleanup(
        observed,
        invocation=invocation,
        container_id=container_id,
        envelope_sha256=envelope_sha256,
    ):
        raise LocalStagingAcceptanceError(
            "local acceptance materializer cleanup ownership differs"
        )
    arguments = ["container", "rm"]
    if observed["State"]["Running"] is True:
        arguments.append("--force")
    arguments.append(container_id)
    result = run_docker_command(client, config_root, tuple(arguments))
    _require_success(result, stdout=f"{container_id}\n".encode("ascii"))
    require_docker_name_absent(
        client,
        config_root,
        resource="container",
        name=invocation.container_name,
    )


def run_materializer_phase(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
    fingerprint: MaterializerVolumeFingerprint,
    *,
    timeout_seconds: float,
) -> MaterializerPhaseProof:
    container_id = ""
    envelope_sha256 = ""
    try:
        container_id, created = create_materializer_container(
            client,
            config_root,
            invocation,
            fingerprint,
        )
        envelope_sha256 = materializer_container_envelope_sha256(created)
        result = run_docker_command(
            client,
            config_root,
            ("container", "start", "--attach", container_id),
            timeout_seconds=timeout_seconds,
            stdout_limit=_DOCKER_ATTACH_LIMIT,
            stderr_limit=_DOCKER_ATTACH_LIMIT,
        )
        _require_success(result, stdout=b"", stderr=b"")
        exited = _docker_inspect_one(
            client,
            config_root,
            "container",
            container_id,
        )
        validate_materializer_container_inspect(
            exited,
            invocation=invocation,
            expected_state="exited",
        )
        if materializer_container_envelope_sha256(exited) != envelope_sha256:
            raise LocalStagingAcceptanceError(
                "local acceptance materializer envelope changed"
            )
        reattest_materializer_volume(
            client,
            config_root,
            invocation,
            fingerprint,
        )
        return MaterializerPhaseProof(
            contract=ACCEPTANCE_CONTRACT,
            role=MATERIALIZER_ROLE,
            container_id=container_id,
            immutable_envelope_sha256=envelope_sha256,
            started_at=exited["State"]["StartedAt"],
            finished_at=exited["State"]["FinishedAt"],
        )
    finally:
        if container_id and envelope_sha256:
            remove_owned_materializer_container(
                client,
                config_root,
                invocation,
                container_id=container_id,
                envelope_sha256=envelope_sha256,
            )


def remove_owned_materializer_volume(
    client: TrustedDockerClient,
    config_root: Path,
    invocation: MaterializerInvocation,
    fingerprint: MaterializerVolumeFingerprint,
) -> None:
    reattest_materializer_volume(
        client,
        config_root,
        invocation,
        fingerprint,
    )
    references = run_docker_command(
        client,
        config_root,
        (
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--filter",
            f"volume={invocation.volume_name}",
            "--format",
            "{{.ID}}\t{{.Names}}",
        ),
    )
    _require_success(references, stdout=b"")
    removed = run_docker_command(
        client,
        config_root,
        ("volume", "rm", invocation.volume_name),
    )
    _require_success(
        removed,
        stdout=f"{invocation.volume_name}\n".encode("ascii"),
    )
    require_docker_name_absent(
        client,
        config_root,
        resource="volume",
        name=invocation.volume_name,
    )


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    if values:
        return 2
    sys.stderr.write("Buffalo LOCAL staging acceptance is not yet executable\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ACCEPTANCE_CONTRACT",
    "BROWSER_WORKER_HIDDEN_MODE",
    "BROWSER_WORKER_PROTOCOL",
    "BrowserCgroupEvents",
    "BrowserContainmentTextEvidence",
    "BrowserWorkerArguments",
    "BrowserWorkerDescriptorExpectation",
    "BrowserWorkerExpectedAttestation",
    "BrowserWorkerLaunch",
    "BrowserWorkerProcessExpectation",
    "BrowserWorkerProcessStat",
    "BrowserWorkerReady",
    "BrowserWorkerRequest",
    "BrowserWorkerResult",
    "FROZEN_IMAGE_ID",
    "FROZEN_IMAGE_ROOTFS_LAYERS",
    "FROZEN_SOURCE_COMMIT",
    "FROZEN_SOURCE_TREE",
    "LocalStagingAcceptanceError",
    "MaterializerIngress",
    "MaterializerInvocation",
    "MaterializerPhaseProof",
    "MaterializerVolumeFingerprint",
    "ObservedBrowserWorker",
    "PinnedBrowserPythonExecutable",
    "PinnedBrowserWorkerRunner",
    "attest_docker_materializer_runtime",
    "build_materializer_create_argv",
    "build_materializer_volume_create_argv",
    "browser_worker_ready_sha256",
    "browser_worker_request_sha256",
    "build_browser_worker_launch",
    "create_materializer_container",
    "create_materializer_volume",
    "decode_browser_worker_frame",
    "encode_browser_worker_frame",
    "materializer_invocation",
    "new_browser_worker_identifiers",
    "observe_browser_worker_process",
    "open_trusted_docker",
    "open_pinned_browser_python_executable",
    "open_pinned_browser_worker_runner",
    "parse_browser_worker_arguments",
    "parse_browser_cgroup_events",
    "parse_browser_cgroup_path",
    "parse_browser_cgroup_processes",
    "parse_browser_cgroup_threads",
    "parse_browser_containment_text_evidence",
    "parse_browser_namespace_pids",
    "read_browser_worker_frame",
    "read_browser_worker_secret",
    "remove_owned_materializer_container",
    "remove_owned_materializer_volume",
    "require_browser_worker_process_live",
    "run_materializer_phase",
    "validate_browser_worker_ready",
    "validate_browser_worker_ready_attestation",
    "validate_browser_worker_request",
    "validate_browser_worker_result",
    "validate_browser_worker_transition",
    "validate_browser_worker_descriptors",
    "validate_materializer_ingress",
    "write_browser_worker_frame",
    "write_browser_worker_secret",
]
