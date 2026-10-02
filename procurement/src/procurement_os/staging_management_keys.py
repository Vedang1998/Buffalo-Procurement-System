"""Domain-separated keys for the fixed staging management channels."""
from __future__ import annotations

import hmac


_ACTIVATION_DOMAIN = b"BUFFALO_STAGING_GATEWAY_ACTIVATION_KEY_V1\x00"
_CONTROL_DOMAIN = b"BUFFALO_STAGING_SUPERVISOR_CONTROL_KEY_V1\x00"


class StagingManagementKeyError(ValueError):
    """The boot-scoped gateway management key differs."""


def _derive(master_key: bytes, domain: bytes) -> bytes:
    if type(master_key) is not bytes or len(master_key) != 32:
        raise StagingManagementKeyError("gateway management key is invalid")
    return hmac.digest(master_key, domain, "sha256")


def derive_gateway_activation_key(master_key: bytes) -> bytes:
    """Derive the supervisor-to-gateway activation-channel key."""

    return _derive(master_key, _ACTIVATION_DOMAIN)


def derive_supervisor_control_key(master_key: bytes) -> bytes:
    """Derive the gateway-to-supervisor lifecycle-control key."""

    return _derive(master_key, _CONTROL_DOMAIN)


__all__ = [
    "StagingManagementKeyError",
    "derive_gateway_activation_key",
    "derive_supervisor_control_key",
]
