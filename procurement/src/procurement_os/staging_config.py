"""Exact, secret-safe process configuration for Railway staging."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Mapping

from .staging_access import OwnerPasswordAuthenticator, StagingAccessError


EXPECTED_PROJECT_ID = "5bdb474a-4af8-4187-b1be-be45594a22b2"
EXPECTED_ENVIRONMENT_ID = "5d8f28f5-168c-4eb9-8eef-6c639ae4cf46"
EXPECTED_APP_SERVICE_ID = "cd0daada-9e8c-4f48-a71e-9151dde451c7"
EXPECTED_POSTGRES_SERVICE_ID = "cb33800d-7bc4-4b44-817f-671978ea50ce"
_HEX_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ALLOWED_GATEWAY_ENVIRONMENT = frozenset(
    {
        "BUFFALO_STAGING_ENABLED",
        "BUFFALO_RUNTIME_MODE",
        "BUFFALO_STAGING_EXTERNAL_HOST",
        "BUFFALO_STAGING_OWNER_VERIFIER",
        "BUFFALO_STAGING_VOLUME_ROOT",
        "BUFFALO_STAGING_EXPECTED_COMMIT",
        "BUFFALO_STAGING_POSTGRES_SERVICE_ID",
        "RAILWAY_GIT_COMMIT_SHA",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_ENVIRONMENT_ID",
        "RAILWAY_SERVICE_ID",
        "RAILWAY_REPLICA_ID",
        "PORT",
        # The supervisor may pass only this bounded process-runtime set in
        # addition to the application contract above.
        "PATH",
        "PYTHONPATH",
        "PYTHONUNBUFFERED",
        "LANG",
        "LC_ALL",
        "TZ",
        "HOME",
        "TMPDIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
)


class StagingConfigError(ValueError):
    """The Railway staging process configuration is absent or ambiguous."""


@dataclass(frozen=True)
class StagingConfig:
    project_id: str
    environment_id: str
    app_service_id: str
    postgres_service_id: str
    replica_id: str
    external_host: str
    external_origin: str
    port: int
    volume_root: Path
    expected_source_commit: str
    owner_verifier: str = field(repr=False)


def load_staging_config(
    environ: Mapping[str, str] | None = None,
) -> StagingConfig:
    values = os.environ if environ is None else environ
    unexpected = sorted(
        str(key) for key in values if str(key) not in _ALLOWED_GATEWAY_ENVIRONMENT
    )
    if unexpected:
        raise StagingConfigError("gateway process environment contains an unapproved entry")
    if _value(values, "BUFFALO_STAGING_ENABLED") != "1":
        raise StagingConfigError("Railway staging is not explicitly enabled")
    if _value(values, "BUFFALO_RUNTIME_MODE") != "SYNTHETIC_DEMO":
        raise StagingConfigError("Railway staging runtime mode differs")

    exact_scope = {
        "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
        "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
        "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
        "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
    }
    for key, expected in exact_scope.items():
        if _value(values, key) != expected:
            raise StagingConfigError("Railway staging scope differs")
    replica_id = _value(values, "RAILWAY_REPLICA_ID")
    if not replica_id or len(replica_id) > 256 or any(
        ord(character) < 33 or ord(character) > 126 for character in replica_id
    ):
        raise StagingConfigError("Railway staging replica identity is absent")

    host = _value(values, "BUFFALO_STAGING_EXTERNAL_HOST")
    _validate_dns_host(host)
    try:
        port = int(_value(values, "PORT"))
    except ValueError as exc:
        raise StagingConfigError("Railway staging port is invalid") from exc
    if port < 1 or port > 65_535:
        raise StagingConfigError("Railway staging port is invalid")

    volume_root = Path(_value(values, "BUFFALO_STAGING_VOLUME_ROOT"))
    if (
        not volume_root.is_absolute()
        or volume_root == Path("/")
        or ".." in volume_root.parts
    ):
        raise StagingConfigError("Railway staging volume root is invalid")

    expected_commit = _value(values, "BUFFALO_STAGING_EXPECTED_COMMIT")
    actual_commit = _value(values, "RAILWAY_GIT_COMMIT_SHA")
    if (
        not _HEX_COMMIT.fullmatch(expected_commit)
        or actual_commit != expected_commit
    ):
        raise StagingConfigError("Railway staging source identity differs")

    verifier = _value(values, "BUFFALO_STAGING_OWNER_VERIFIER")
    try:
        OwnerPasswordAuthenticator(verifier)
    except StagingAccessError as exc:
        raise StagingConfigError("Railway staging owner verifier is invalid") from exc

    return StagingConfig(
        project_id=EXPECTED_PROJECT_ID,
        environment_id=EXPECTED_ENVIRONMENT_ID,
        app_service_id=EXPECTED_APP_SERVICE_ID,
        postgres_service_id=EXPECTED_POSTGRES_SERVICE_ID,
        replica_id=replica_id,
        external_host=host,
        external_origin=f"https://{host}",
        port=port,
        volume_root=volume_root,
        expected_source_commit=expected_commit,
        owner_verifier=verifier,
    )


def _validate_dns_host(host: str) -> None:
    if (
        not host
        or host != host.lower()
        or len(host) > 253
        or "." not in host
        or host.endswith(".")
        or any(not _DNS_LABEL.fullmatch(label) for label in host.split("."))
    ):
        raise StagingConfigError("Railway staging external host is invalid")


def _value(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key, "")
    return str(value).strip()
