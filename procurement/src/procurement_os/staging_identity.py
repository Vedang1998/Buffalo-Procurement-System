"""Server-owned identity for the Railway staging gateway and workers."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from .local_identity import Principal


OWNER_PRINCIPAL_REF = "owner:railway-staging:01"
OWNER_ROLE_REF = "RAILWAY_STAGING_OWNER"
STAGING_AUTH_CONTEXT_CONTRACT = "BUFFALO_RAILWAY_STAGING_SESSION_V1"
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class StagingRequestIdentity:
    """One verified gateway session projected into a private worker request."""

    session_digest: str
    principal_ref: str
    role_ref: str
    capabilities: frozenset[str]
    worker_role: str

    def validate(self) -> None:
        if _HEX_64.fullmatch(self.session_digest) is None:
            raise ValueError("staging session digest is invalid")
        if self.principal_ref != OWNER_PRINCIPAL_REF or self.role_ref != OWNER_ROLE_REF:
            raise ValueError("staging owner identity differs")
        if self.worker_role not in {"synthetic", "research"}:
            raise ValueError("staging worker role differs")
        if not self.capabilities or any(
            not isinstance(value, str) or _CAPABILITY.fullmatch(value) is None
            for value in self.capabilities
        ):
            raise ValueError("staging capability set is invalid")

    def principal_for(self, role_ref: str | None = None) -> Principal:
        self.validate()
        selected_role = self.role_ref if role_ref is None else role_ref
        if not isinstance(selected_role, str) or not selected_role.strip():
            raise ValueError("staging action role is invalid")
        payload = json.dumps(
            {
                "contract": STAGING_AUTH_CONTEXT_CONTRACT,
                "principal_ref": self.principal_ref,
                "role_ref": selected_role,
                "session_digest": self.session_digest,
                "worker_role": self.worker_role,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return Principal(
            principal_ref=self.principal_ref,
            role_ref=selected_role,
            authn_context_sha256=hashlib.sha256(payload).hexdigest(),
        )
