"""Dependency-light local-session identity primitives.

This module deliberately has no database, application-service, or platform
imports.  The isolated private research viewer can therefore reuse the local
authentication boundary without loading the operational persistence stack.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True)
class Principal:
    principal_ref: str
    role_ref: str
    authn_context_sha256: str

    def validate(self) -> None:
        if not self.principal_ref.strip() or not self.role_ref.strip():
            raise ValueError("named principal and role are required")
        if len(self.authn_context_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.authn_context_sha256
        ):
            raise ValueError("authentication context hash is malformed")


def authentication_context_sha256(
    *, principal_ref: str, role_ref: str, session_ref: str
) -> str:
    """Bind a server-created session to its fixed named principal and role."""

    payload = json.dumps(
        {
            "contract": "BUFFALO_LOCAL_NAMED_SESSION_V1",
            "principal_ref": principal_ref,
            "role_ref": role_ref,
            "session_ref": session_ref,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()
