"""One-shot authenticated database-readiness proof for the synthetic worker."""
from __future__ import annotations

from .staging_research_readiness import (
    MAX_READINESS_PAYLOAD_BYTES,
    ReadinessProcessIdentity,
    ResearchReadinessAbsent,
    ResearchReadinessError,
    ResearchReadinessProof,
    ResearchReadinessReader,
    ResearchReadinessWriter,
    current_readiness_process_identity,
    mint_readiness_frame as _mint_frame,
    mint_readiness_payload as _mint_payload,
    parse_readiness_payload as _parse_payload,
)


READINESS_VERSION = "BUFFALO_STAGING_SYNTHETIC_DATABASE_READINESS_V1"
_READINESS_DOMAIN = b"BUFFALO_STAGING_SYNTHETIC_DATABASE_READINESS_V1\x00"
_FAILURES = frozenset(
    {"TIMEOUT", "DATABASE", "INTEGRITY", "RESOURCE", "OOM", "ACTIVATION"}
)

# The framing, descriptor, and process-identity invariants are shared.  The
# signed role, version, domain, and failure vocabulary are not.
SyntheticReadinessError = ResearchReadinessError
SyntheticReadinessAbsent = ResearchReadinessAbsent
SyntheticReadinessProof = ResearchReadinessProof


def mint_readiness_payload(
    *,
    key: bytes,
    generation: int,
    identity: ReadinessProcessIdentity,
    state: str,
    failure: str | None = None,
    validation_identity: str | None = None,
) -> bytes:
    return _mint_payload(
        key=key,
        generation=generation,
        identity=identity,
        state=state,
        failure=failure,
        validation_identity=validation_identity,
        _role="synthetic",
        _version=READINESS_VERSION,
        _domain=_READINESS_DOMAIN,
        _failures=_FAILURES,
    )


def mint_readiness_frame(
    *,
    key: bytes,
    generation: int,
    identity: ReadinessProcessIdentity,
    state: str,
    failure: str | None = None,
    validation_identity: str | None = None,
) -> bytes:
    return _mint_frame(
        key=key,
        generation=generation,
        identity=identity,
        state=state,
        failure=failure,
        validation_identity=validation_identity,
        _role="synthetic",
        _version=READINESS_VERSION,
        _domain=_READINESS_DOMAIN,
        _failures=_FAILURES,
    )


def parse_readiness_payload(
    message: bytes,
    *,
    key: bytes,
    expected_generation: int,
    expected_identity: ReadinessProcessIdentity,
    expected_validation_identity: str,
) -> SyntheticReadinessProof:
    return _parse_payload(
        message,
        key=key,
        expected_generation=expected_generation,
        expected_identity=expected_identity,
        expected_validation_identity=expected_validation_identity,
        _role="synthetic",
        _version=READINESS_VERSION,
        _domain=_READINESS_DOMAIN,
        _failures=_FAILURES,
    )


class SyntheticReadinessReader(ResearchReadinessReader):
    def __init__(
        self,
        descriptor: int,
        *,
        key: bytes,
        expected_generation: int,
        expected_identity: ReadinessProcessIdentity,
        expected_validation_identity: str,
    ) -> None:
        super().__init__(
            descriptor,
            key=key,
            expected_generation=expected_generation,
            expected_identity=expected_identity,
            expected_validation_identity=expected_validation_identity,
            _role="synthetic",
            _version=READINESS_VERSION,
            _domain=_READINESS_DOMAIN,
            _failures=_FAILURES,
        )


class SyntheticReadinessWriter(ResearchReadinessWriter):
    def __init__(self, descriptor: int, *, key: bytes, generation: int) -> None:
        super().__init__(
            descriptor,
            key=key,
            generation=generation,
            _role="synthetic",
            _version=READINESS_VERSION,
            _domain=_READINESS_DOMAIN,
            _failures=_FAILURES,
        )


__all__ = [
    "MAX_READINESS_PAYLOAD_BYTES",
    "READINESS_VERSION",
    "ReadinessProcessIdentity",
    "SyntheticReadinessAbsent",
    "SyntheticReadinessError",
    "SyntheticReadinessProof",
    "SyntheticReadinessReader",
    "SyntheticReadinessWriter",
    "current_readiness_process_identity",
    "mint_readiness_frame",
    "mint_readiness_payload",
    "parse_readiness_payload",
]
