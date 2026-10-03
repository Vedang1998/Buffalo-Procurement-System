"""Dependency-light process and environment contract for Railway staging."""
from __future__ import annotations

from typing import Mapping


PROCESS_MODULE = "procurement_os.staging_process"

PROCESS_RUNTIME_ENVIRONMENT_NAMES = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PYTHONUNBUFFERED",
        "TMPDIR",
        "TZ",
    }
)
OPTIONAL_CERTIFICATE_ENVIRONMENT_NAMES = frozenset(
    {"SSL_CERT_DIR", "SSL_CERT_FILE"}
)

GATEWAY_REQUIRED_ENVIRONMENT_NAMES = frozenset(
    {
        "BUFFALO_RUNTIME_MODE",
        "BUFFALO_STAGING_ACTIVATION_FD",
        "BUFFALO_STAGING_CONTROL_KEY_DIRECTORY_GID",
        "BUFFALO_STAGING_CONTROL_KEY_DIRECTORY_UID",
        "BUFFALO_STAGING_CONTROL_KEY_FILE",
        "BUFFALO_STAGING_CONTROL_SOCKET_GID",
        "BUFFALO_STAGING_CONTROL_SOCKET_PATH",
        "BUFFALO_STAGING_ENABLED",
        "BUFFALO_STAGING_EXPECTED_COMMIT",
        "BUFFALO_STAGING_EXTERNAL_HOST",
        "BUFFALO_STAGING_OWNER_VERIFIER",
        "BUFFALO_STAGING_POSTGRES_SERVICE_ID",
        "BUFFALO_STAGING_RESEARCH_KEY_FILE",
        "BUFFALO_STAGING_RESEARCH_KEY_DIRECTORY_GID",
        "BUFFALO_STAGING_RESEARCH_KEY_DIRECTORY_UID",
        "BUFFALO_STAGING_RESEARCH_SOCKET_GID",
        "BUFFALO_STAGING_RESEARCH_SOCKET_PATH",
        "BUFFALO_STAGING_RESEARCH_SOCKET_UID",
        "BUFFALO_STAGING_RUNTIME_ROOT",
        "BUFFALO_STAGING_SYNTHETIC_KEY_FILE",
        "BUFFALO_STAGING_SYNTHETIC_KEY_DIRECTORY_GID",
        "BUFFALO_STAGING_SYNTHETIC_KEY_DIRECTORY_UID",
        "BUFFALO_STAGING_SYNTHETIC_SOCKET_GID",
        "BUFFALO_STAGING_SYNTHETIC_SOCKET_PATH",
        "BUFFALO_STAGING_SYNTHETIC_SOCKET_UID",
        "BUFFALO_STAGING_SUPERVISOR_PID",
        "BUFFALO_STAGING_VOLUME_ROOT",
        "PORT",
        "RAILWAY_ENVIRONMENT_ID",
        "RAILWAY_GIT_COMMIT_SHA",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_REPLICA_ID",
        "RAILWAY_SERVICE_ID",
    }
)
GATEWAY_ENVIRONMENT_NAMES = (
    GATEWAY_REQUIRED_ENVIRONMENT_NAMES
    | PROCESS_RUNTIME_ENVIRONMENT_NAMES
    | OPTIONAL_CERTIFICATE_ENVIRONMENT_NAMES
)

WORKER_BOUNDARY_ENVIRONMENT_NAMES = frozenset(
    {
        "BUFFALO_STAGING_ASSERTION_KEY_FILE",
        "BUFFALO_STAGING_EXTERNAL_ORIGIN",
        "BUFFALO_STAGING_GATEWAY_GID",
        "BUFFALO_STAGING_GATEWAY_PID",
        "BUFFALO_STAGING_GATEWAY_UID",
        "BUFFALO_STAGING_KEY_GENERATION",
        "BUFFALO_STAGING_KEY_DIRECTORY_GID",
        "BUFFALO_STAGING_KEY_DIRECTORY_UID",
        "BUFFALO_STAGING_LISTEN_FD",
        "BUFFALO_STAGING_RUNTIME_ROOT",
        "BUFFALO_STAGING_SOCKET_GID",
        "BUFFALO_STAGING_SOCKET_PATH",
        "BUFFALO_STAGING_WORKER_ROLE",
    }
)
WORKER_REQUIRED_ENVIRONMENT_NAMES = {
    "synthetic": WORKER_BOUNDARY_ENVIRONMENT_NAMES
    | frozenset(
        {
            "BUFFALO_RUNTIME_MODE",
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID",
            "DATABASE_URL",
            "PGPASSFILE",
            "PROCUREMENT_STORAGE_ROOT",
            "RAILWAY_ENVIRONMENT_ID",
            "RAILWAY_PROJECT_ID",
            "RAILWAY_SERVICE_ID",
        }
    ),
    "research": WORKER_BOUNDARY_ENVIRONMENT_NAMES
    | frozenset(
        {
            "BUFFALO_PRIVATE_RESEARCH_WORKSPACE",
            "BUFFALO_RESEARCH_GIT_DIR",
            "BUFFALO_RESEARCH_MANIFEST",
            "BUFFALO_RESEARCH_ROOT",
        }
    ),
}
RESEARCH_LAUNCH_ENVIRONMENT_NAMES = frozenset(
    {"BUFFALO_STAGING_RESEARCH_READINESS_FD"}
)
WORKER_ENVIRONMENT_NAMES = {
    role: required
    | PROCESS_RUNTIME_ENVIRONMENT_NAMES
    | OPTIONAL_CERTIFICATE_ENVIRONMENT_NAMES
    | (RESEARCH_LAUNCH_ENVIRONMENT_NAMES if role == "research" else frozenset())
    for role, required in WORKER_REQUIRED_ENVIRONMENT_NAMES.items()
}


class StagingProcessContractError(ValueError):
    """A child received an incomplete, excessive, or malformed environment."""


def validated_process_environment(
    *, role: str, environ: Mapping[str, str]
) -> dict[str, str]:
    """Validate and copy one role's exact environment without ambient filtering."""

    if role == "gateway":
        allowed = GATEWAY_ENVIRONMENT_NAMES
        required = GATEWAY_REQUIRED_ENVIRONMENT_NAMES
    elif role in WORKER_ENVIRONMENT_NAMES:
        allowed = WORKER_ENVIRONMENT_NAMES[role]
        required = WORKER_REQUIRED_ENVIRONMENT_NAMES[role]
    else:
        raise StagingProcessContractError("staging process role is invalid")
    names = {str(name) for name in environ}
    if names - allowed:
        raise StagingProcessContractError("staging process environment has an unapproved entry")
    if not required.issubset(names):
        raise StagingProcessContractError("staging process environment is incomplete")
    copied: dict[str, str] = {}
    for name in sorted(names):
        value = environ[name]
        if not isinstance(value, str) or "\x00" in value:
            raise StagingProcessContractError("staging process environment is invalid")
        if name in required and not value:
            raise StagingProcessContractError("staging process environment is incomplete")
        copied[name] = value
    if role != "gateway" and copied["BUFFALO_STAGING_WORKER_ROLE"] != role:
        raise StagingProcessContractError("staging worker role differs")
    return copied


def child_argv(*, python_executable: str, role: str) -> tuple[str, ...]:
    """Return the sole executable child command accepted by the supervisor."""

    if role not in {"gateway", "synthetic", "research"}:
        raise ValueError("staging process role is invalid")
    if not python_executable.startswith("/") or "\x00" in python_executable:
        raise ValueError("staging Python executable is invalid")
    return (
        python_executable,
        "-I",
        "-B",
        "-m",
        PROCESS_MODULE,
        role,
    )
