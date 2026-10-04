"""Authenticated, read-only UI for a sealed private research workspace.

This composition root deliberately does not import the operational procurement
API.  It exposes only authentication, health, and immutable research artifacts.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import html
import hmac
import json
import math
import os
from pathlib import Path
import stat
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from .local_access import (
    _read_secret,
    runtime_config,
)
from .private_research import (
    CONTRACT as V1_WORKSPACE_CONTRACT,
    DATA_MODE as V1_WORKSPACE_DATA_MODE,
    V2_CONTRACT as V2_WORKSPACE_CONTRACT,
    V2_DATA_MODE as DEVELOPMENT_WORKSPACE_DATA_MODE,
    V3_CONTRACT,
    V3_CORRECTED_CONTRACT,
    PrivateResearchError,
    read_private_research_workspace,
)
from .private_research_projection import (
    DATA_MODE as V1_PROJECTION_DATA_MODE,
    PROJECTION_CONTRACT as V1_PROJECTION_CONTRACT,
    V2_DATA_MODE as DEVELOPMENT_PROJECTION_DATA_MODE,
    V2_PROJECTION_CONTRACT,
    V3_PROJECTION_CONTRACT,
    V3_CORRECTED_PROJECTION_CONTRACT,
    PrivateResearchProjectionError,
    _compile_private_research_row_filter,
)
from .staging_research_validation import (
    ResearchSemanticProof,
    StagingResearchValidationError,
    validate_replayed_workspace,
    validate_staging_research_paths,
)


app = FastAPI(
    title="Buffalo private research review",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@dataclass(frozen=True)
class _ArtifactHandle:
    descriptor: int
    identity: tuple[int, ...]
    bytes: int
    sha256: str
    media_type: str


@dataclass(frozen=True)
class _ValidatedWorkspaceSnapshot:
    """One atomically published, fully replayed private workspace."""

    path: str
    workspace: dict[str, object]
    projection: dict[str, object]
    artifact_handles: dict[str, _ArtifactHandle] | None = None
    semantic_proof: ResearchSemanticProof | None = None


_CACHED_WORKSPACE: _ValidatedWorkspaceSnapshot | None = None
_STAGING_STARTUP_FAILURE: str | None = None
_ARTIFACT_CHUNK_BYTES = 1024 * 1024
_BASIC_USERNAME = "private"
_BASIC_CHALLENGE = 'Basic realm="Buffalo private research", charset="UTF-8"'


def _private_review_auth_material():
    config = runtime_config()
    if config.mode != "PRIVATE_REAL_SOURCE_REVIEW":
        raise PermissionError("private review authentication is unavailable")
    try:
        secret = _read_secret(config)
    except OSError as exc:
        raise PermissionError("private review authentication is unavailable") from exc
    return config, secret


def _authorization_password(raw: bytes | None) -> bytes | None:
    if raw is None:
        return None
    try:
        scheme, encoded = raw.decode("ascii").split(" ", 1)
        if scheme.lower() != "basic" or not encoded or encoded != encoded.strip():
            return None
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError, ValueError, binascii.Error):
        return None
    username, separator, password = decoded.partition(":")
    if separator != ":" or username != _BASIC_USERNAME or "\x00" in password:
        return None
    return password.encode("utf-8")


class PrivateResearchBasicAccessMiddleware:
    """Exact-loopback Basic protection space for only this private viewer."""

    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.application(scope, receive, send)
            return
        config = runtime_config()
        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", ""))
        raw_headers = [
            (key.lower(), value) for key, value in scope.get("headers", ())
        ]
        if any(
            sum(1 for key, _ in raw_headers if key == protected) != 1
            for protected in (b"host",)
        ) or any(
            sum(1 for key, _ in raw_headers if key == protected) > 1
            for protected in (b"authorization", b"origin", b"cookie")
        ):
            await self._response(send, 403, b"Duplicate security header refused")
            return
        headers = dict(raw_headers)
        host = headers.get(b"host", b"").decode("latin-1")
        forwarded = any(
            key == b"forwarded" or key.startswith(b"x-forwarded-")
            for key, _ in raw_headers
        )
        client_host = str((scope.get("client") or ("", 0))[0])
        allowed_client = client_host in {"127.0.0.1", "::1"}
        if forwarded or host != f"127.0.0.1:{config.port}" or not allowed_client:
            await self._response(send, 403, b"Exact loopback origin required")
            return
        if method not in {"GET", "HEAD", "OPTIONS"}:
            origin = headers.get(b"origin", b"").decode("latin-1")
            if origin != config.origin:
                await self._response(send, 403, b"Exact same-origin request required")
                return
            await self._response(send, 403, b"Route is not authorized")
            return
        if path != "/health":
            try:
                current_config, expected = _private_review_auth_material()
            except PermissionError:
                await self._response(
                    send, 503, b"Private review authentication is unavailable"
                )
                return
            supplied = _authorization_password(headers.get(b"authorization"))
            if (
                current_config.origin != config.origin
                or supplied is None
                or not hmac.compare_digest(supplied, expected)
            ):
                await self._response(
                    send,
                    401,
                    b"Private review authentication failed",
                    challenge=True,
                )
                return

        async def protected_send(message):
            if message.get("type") == "http.response.start":
                protected_names = {
                    b"cache-control",
                    b"pragma",
                    b"x-content-type-options",
                    b"referrer-policy",
                    b"content-security-policy",
                }
                response_headers = [
                    (key, value)
                    for key, value in message.get("headers", ())
                    if key.lower() not in protected_names
                ]
                response_headers.extend(self._security_headers())
                message["headers"] = response_headers
            await send(message)

        await self.application(scope, receive, protected_send)

    @staticmethod
    def _security_headers():
        return (
            (b"cache-control", b"no-store"),
            (b"pragma", b"no-cache"),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"same-origin"),
            (
                b"content-security-policy",
                b"default-src 'self'; script-src 'self'; "
                b"style-src 'self' 'unsafe-inline'; object-src 'none'; "
                b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            ),
        )

    async def _response(
        self, send, status: int, body: bytes, *, challenge: bool = False
    ) -> None:
        headers = dict(self._security_headers())
        if challenge:
            headers[b"www-authenticate"] = _BASIC_CHALLENGE.encode("ascii")
        response = Response(
            body,
            status_code=status,
            media_type="text/plain",
            headers={
                key.decode("ascii"): value.decode("ascii")
                for key, value in headers.items()
            },
        )
        await response({"type": "http"}, None, send)


def _workspace() -> dict[str, object]:
    global _CACHED_WORKSPACE
    raw = os.getenv("BUFFALO_PRIVATE_RESEARCH_WORKSPACE", "").strip()
    if not raw:
        raise HTTPException(status_code=503, detail="Private research workspace is absent")
    cached = _CACHED_WORKSPACE
    if cached is not None and cached.path == raw:
        return cached.workspace
    staging_paths = None
    try:
        if os.getenv("BUFFALO_STAGING_WORKER_ROLE", "") == "research":
            staging_paths = validate_staging_research_paths(
                os.environ, worker_runtime=True
            )
            if Path(raw) != staging_paths.workspace:
                raise StagingResearchValidationError(
                    "research workspace binding differs"
                )
        if staging_paths is None:
            loaded = read_private_research_workspace(Path(raw))
        else:
            loaded = read_private_research_workspace(
                Path(raw),
                _retain_corrected_artifacts=False,
                lineage_repository=staging_paths.git_dir,
            )
    except (PrivateResearchError, StagingResearchValidationError) as exc:
        raise HTTPException(status_code=503, detail="Private research workspace is invalid") from exc
    projection = loaded.get("projection")
    manifest = loaded.get("manifest")
    tuples = {
        V1_WORKSPACE_CONTRACT: (
            V1_PROJECTION_CONTRACT,
            V1_WORKSPACE_DATA_MODE,
            V1_PROJECTION_DATA_MODE,
        ),
        V2_WORKSPACE_CONTRACT: (
            V2_PROJECTION_CONTRACT,
            DEVELOPMENT_WORKSPACE_DATA_MODE,
            DEVELOPMENT_PROJECTION_DATA_MODE,
        ),
        V3_CONTRACT: (
            V3_PROJECTION_CONTRACT,
            DEVELOPMENT_WORKSPACE_DATA_MODE,
            DEVELOPMENT_PROJECTION_DATA_MODE,
        ),
        V3_CORRECTED_CONTRACT: (
            V3_CORRECTED_PROJECTION_CONTRACT,
            DEVELOPMENT_WORKSPACE_DATA_MODE,
            DEVELOPMENT_PROJECTION_DATA_MODE,
        ),
    }
    expected = (
        tuples.get(manifest.get("contract"))
        if isinstance(manifest, dict)
        else None
    )
    if (
        not isinstance(projection, dict)
        or expected is None
        or manifest.get("data_mode") != expected[1]
        or manifest.get("projection_contract") != expected[0]
        or projection.get("contract") != expected[0]
        or projection.get("data_mode") != expected[2]
    ):
        raise HTTPException(status_code=503, detail="Private research workspace is invalid")
    handles: dict[str, _ArtifactHandle] | None = None
    semantic_proof: ResearchSemanticProof | None = None
    try:
        if staging_paths is not None:
            manifest_path = staging_paths.workspace / "manifest.json"
            manifest_sha256 = _hash_exact_file(manifest_path)
            semantic_proof = validate_replayed_workspace(
                loaded,
                manifest_sha256=manifest_sha256,
            )
            handles = _open_artifact_handles(staging_paths.workspace, manifest)
    except (OSError, StagingResearchValidationError) as exc:
        _close_artifact_handles(handles)
        raise HTTPException(
            status_code=503, detail="Private research workspace is invalid"
        ) from exc
    _CACHED_WORKSPACE = _ValidatedWorkspaceSnapshot(
        raw,
        loaded,
        projection,
        handles,
        semantic_proof,
    )
    return loaded


def _stable_file_identity(info: os.stat_result) -> tuple[int, ...]:
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


def _open_regular_file(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        linked = path.stat(follow_symlinks=False)
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
            or info.st_gid != os.getgid()
            or info.st_nlink != 1
            or _stable_file_identity(info) != _stable_file_identity(linked)
        ):
            raise OSError("private artifact metadata differs")
        os.set_inheritable(descriptor, False)
        return descriptor, info
    except BaseException:
        os.close(descriptor)
        raise


def _descriptor_sha256(descriptor: int, expected_bytes: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while offset < expected_bytes:
        chunk = os.pread(
            descriptor,
            min(_ARTIFACT_CHUNK_BYTES, expected_bytes - offset),
            offset,
        )
        if not chunk:
            raise OSError("private artifact ended early")
        digest.update(chunk)
        offset += len(chunk)
    if os.pread(descriptor, 1, offset):
        raise OSError("private artifact exceeds its declared size")
    return digest.hexdigest()


def _hash_exact_file(path: Path) -> str:
    descriptor, info = _open_regular_file(path)
    try:
        digest = _descriptor_sha256(descriptor, info.st_size)
        if _stable_file_identity(os.fstat(descriptor)) != _stable_file_identity(info):
            raise OSError("private artifact changed while hashing")
        return digest
    finally:
        os.close(descriptor)


def _open_artifact_handles(
    workspace_path: Path, manifest: dict[str, object]
) -> dict[str, _ArtifactHandle]:
    raw_records = manifest.get("artifacts")
    if not isinstance(raw_records, list):
        raise OSError("private artifact inventory differs")
    result: dict[str, _ArtifactHandle] = {}
    try:
        for raw in raw_records:
            if not isinstance(raw, dict):
                raise OSError("private artifact record differs")
            name = raw.get("name")
            size = raw.get("bytes")
            digest = raw.get("sha256")
            media_type = raw.get("media_type")
            if (
                not isinstance(name, str)
                or name in result
                or raw.get("path") != name
                or Path(name).name != name
                or type(size) is not int
                or size < 0
                or not isinstance(digest, str)
                or len(digest) != 64
                or not isinstance(media_type, str)
            ):
                raise OSError("private artifact record differs")
            descriptor, info = _open_regular_file(workspace_path / name)
            try:
                if (
                    info.st_size != size
                    or _descriptor_sha256(descriptor, size) != digest
                    or _stable_file_identity(os.fstat(descriptor))
                    != _stable_file_identity(info)
                ):
                    raise OSError("private artifact content differs")
                result[name] = _ArtifactHandle(
                    descriptor=descriptor,
                    identity=_stable_file_identity(info),
                    bytes=size,
                    sha256=digest,
                    media_type=media_type,
                )
            except BaseException:
                os.close(descriptor)
                raise
        if set(result) != {
            "coverage.json",
            "owner-preview.html",
            "owner-worksheet.csv",
            "projection.json",
        }:
            raise OSError("private artifact inventory differs")
        return result
    except BaseException:
        _close_artifact_handles(result)
        raise


def _close_artifact_handles(
    handles: dict[str, _ArtifactHandle] | None,
) -> None:
    if not handles:
        return
    for handle in handles.values():
        try:
            os.close(handle.descriptor)
        except OSError:
            pass
    handles.clear()


def staging_research_validation_identity() -> str:
    """Return only the current process's fully replayed staging attestation."""

    cached = _CACHED_WORKSPACE
    if (
        os.getenv("BUFFALO_STAGING_WORKER_ROLE", "") != "research"
        or cached is None
        or cached.semantic_proof is None
        or cached.artifact_handles is None
    ):
        raise StagingResearchValidationError("research semantic proof is unavailable")
    try:
        for handle in cached.artifact_handles.values():
            if (
                _stable_file_identity(os.fstat(handle.descriptor)) != handle.identity
                or _descriptor_sha256(handle.descriptor, handle.bytes)
                != handle.sha256
                or _stable_file_identity(os.fstat(handle.descriptor))
                != handle.identity
            ):
                raise StagingResearchValidationError(
                    "research artifact readiness differs"
                )
    except OSError:
        raise StagingResearchValidationError(
            "research artifact readiness differs"
        ) from None
    return cached.semantic_proof.validation_identity


def staging_research_startup_failure() -> str | None:
    """Expose only a bounded readiness failure class to the child launcher."""

    if os.getenv("BUFFALO_STAGING_WORKER_ROLE", "") != "research":
        return None
    return _STAGING_STARTUP_FAILURE


def _stream_artifact(handle: _ArtifactHandle):
    descriptor = os.dup(handle.descriptor)
    os.set_inheritable(descriptor, False)
    digest = hashlib.sha256()
    offset = 0
    try:
        if _stable_file_identity(os.fstat(descriptor)) != handle.identity:
            raise OSError("private artifact metadata changed")
        while offset < handle.bytes:
            chunk = os.pread(
                descriptor,
                min(_ARTIFACT_CHUNK_BYTES, handle.bytes - offset),
                offset,
            )
            if not chunk:
                raise OSError("private artifact ended early")
            digest.update(chunk)
            offset += len(chunk)
            yield chunk
        if (
            os.pread(descriptor, 1, offset)
            or digest.hexdigest() != handle.sha256
            or _stable_file_identity(os.fstat(descriptor)) != handle.identity
        ):
            raise OSError("private artifact changed during delivery")
    finally:
        os.close(descriptor)


def _staging_artifact_response(
    name: str, *, attachment: bool
) -> StreamingResponse | None:
    cached = _CACHED_WORKSPACE
    if cached is None or cached.artifact_handles is None:
        return None
    handle = cached.artifact_handles.get(name)
    if handle is None:
        raise HTTPException(status_code=404, detail="Private artifact is absent")
    headers = {"Content-Length": str(handle.bytes)}
    if attachment:
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return StreamingResponse(
        _stream_artifact(handle),
        media_type=handle.media_type,
        headers=headers,
    )


def _page_app_owned_workspace_rows(
    workspace: dict[str, object],
    *,
    query: str = "",
    vendor: str = "",
    status: str = "",
    stockout: str = "",
    page: int,
    page_size: int,
) -> tuple[int, int, list[dict[str, object]]]:
    """Return one detached page from the exact app-authenticated snapshot."""

    raw = os.getenv("BUFFALO_PRIVATE_RESEARCH_WORKSPACE", "").strip()
    cached = _CACHED_WORKSPACE
    projection = workspace.get("projection")
    if (
        not raw
        or cached is None
        or cached.path != raw
        or workspace is not cached.workspace
        or projection is not cached.projection
        or not isinstance(projection, dict)
    ):
        raise PrivateResearchProjectionError(
            "private projection is not the app-owned validated snapshot"
        )
    start = (page - 1) * page_size
    end = page * page_size
    matched_count = 0
    selected: list[dict[str, object]] = []
    contract = str(projection["contract"])
    rows = (
        projection["owner_worksheet"]
        if contract in {
            V2_PROJECTION_CONTRACT,
            V3_PROJECTION_CONTRACT,
            V3_CORRECTED_PROJECTION_CONTRACT,
        }
        else projection["research_rows"]
    )
    matches = _compile_private_research_row_filter(
        contract,
        query=query,
        vendor=vendor,
        status=status,
        corrected_primary_status_by_id=(
            {
                str(row["shopify_variant_id"]): str(
                    row["primary_forecast_status"]
                )
                for row in projection["coverage_rows"]
            }
            if contract == V3_CORRECTED_PROJECTION_CONTRACT
            else None
        ),
    )
    for row in rows:
        if not matches(row):
            continue
        if stockout and _stockout_evidence_status(dict(row)) != stockout:
            continue
        if start <= matched_count < end:
            selected.append(deepcopy(dict(row)))
        matched_count += 1
    total_pages = max(1, math.ceil(matched_count / page_size))
    return matched_count, total_pages, selected


@app.on_event("startup")
def validate_workspace_at_startup() -> None:
    """Perform the expensive immutable replay once before readiness is served."""

    global _STAGING_STARTUP_FAILURE
    _STAGING_STARTUP_FAILURE = None
    try:
        _workspace()
    except MemoryError:
        _STAGING_STARTUP_FAILURE = "OOM"
        raise
    except HTTPException as exc:
        _STAGING_STARTUP_FAILURE = (
            "INTEGRITY"
            if isinstance(exc.__cause__, StagingResearchValidationError)
            else "SEMANTIC"
        )
        raise
    except BaseException:
        _STAGING_STARTUP_FAILURE = "SEMANTIC"
        raise


@app.on_event("shutdown")
def release_workspace_at_shutdown() -> None:
    global _CACHED_WORKSPACE
    cached = _CACHED_WORKSPACE
    _CACHED_WORKSPACE = None
    if cached is not None:
        _close_artifact_handles(cached.artifact_handles)


def _private_review_config():
    """Return usable private-review auth config without exposing secret data."""

    try:
        config, _ = _private_review_auth_material()
    except PermissionError as exc:
        raise HTTPException(
            status_code=503, detail="Private review authentication is unavailable"
        ) from exc
    return config


@app.get("/health")
def health() -> dict[str, object]:
    config = _private_review_config()
    workspace = _workspace()
    manifest = workspace.get("manifest")
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=503, detail="Private manifest is absent")
    return {
        "ok": True,
        "service": "buffalo-private-research-review",
        "mode": config.mode,
        "operational_authority": False,
    }


@app.get("/auth/login")
def login_page() -> RedirectResponse:
    _private_review_config()
    return RedirectResponse(url="/private-research", status_code=303)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/private-research", status_code=303)


def _stockout_evidence_status(row: dict[str, object]) -> str:
    forecast = row.get("forecast")
    if isinstance(forecast, dict):
        reasons = forecast.get("reason_codes", [])
        calculated = forecast.get("status") == "CALCULATED_RESEARCH_ONLY"
    else:
        scenarios = row.get("scenario_results")
        scenario_values = (
            scenarios.values() if isinstance(scenarios, dict) else ()
        )
        reasons = [
            reason
            for scenario in scenario_values
            if isinstance(scenario, dict)
            for reason in scenario.get("reason_codes", [])
        ]
        calculated = any(
            isinstance(scenario, dict)
            and scenario.get("status") == "CALCULATED_RESEARCH_ONLY"
            for scenario in (
                scenarios.values() if isinstance(scenarios, dict) else ()
            )
        )
    if any(
        isinstance(reason, str)
        and (
            "FORECAST_EVIDENCE" in reason
            or "INVENTORY_STATE" in reason
            or "AVAILABILITY" in reason
        )
        for reason in reasons
    ):
        return "NOT_CAPTURED"
    if calculated:
        return "CAPTURED_IN_RESEARCH_INPUT"
    return "INCOMPLETE_OR_UNKNOWN"


def _v2_research_provenance(projection: dict[str, object]) -> dict[str, object]:
    research = projection.get("forecast_research")
    if not isinstance(research, dict):
        return {}
    history = research.get("history")
    identity = research.get("source_identity")
    history_keys = (
        "contract",
        "composite_id",
        "start_date",
        "end_date",
        "complete_day_count",
        "availability_basis",
        "signed_sales_transform",
        "partitions",
        "identity_authority",
        "allocation_controls",
    )
    if projection.get("contract") == "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3":
        history_keys += (
            "parent_v2_input",
            "existence_evidence",
            "eligibility_controls",
        )
    return {
        "input_id": research.get("input_id"),
        "policy": research.get("policy"),
        "source_identity": (
            {
                "verdict": identity.get("verdict"),
                "identity_id": identity.get("identity_id"),
                "evidence_sha256": identity.get("evidence_sha256"),
                "shop": identity.get("shop"),
            }
            if isinstance(identity, dict)
            else None
        ),
        "history": (
            {key: history.get(key) for key in history_keys}
            if isinstance(history, dict)
            else None
        ),
        "scenarios": research.get("scenarios"),
        "sidecars_sha256": research.get("sidecars_sha256"),
    }


def _corrected_research_provenance(
    projection: dict[str, object],
) -> dict[str, object]:
    research = projection.get("joint_forecast_research")
    if not isinstance(research, dict):
        return {}
    return {
        "implementation_lineage": projection.get("implementation_lineage"),
        "corrected_input": projection.get("corrected_input"),
        "parent_projection": projection.get("parent_projection"),
        "policy": research.get("policy"),
        "creation_evidence_delta": research.get("creation_evidence_delta"),
        "source_identity": research.get("source_identity"),
        "history_controls": research.get("history_controls"),
        "membership_controls": research.get("membership_controls"),
        "scenario_dates": research.get("scenario_dates"),
        "sidecars_sha256": research.get("sidecars_sha256"),
    }


def _json_html(value: object) -> str:
    return html.escape(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
        quote=True,
    )


@app.get("/private-research", response_class=HTMLResponse)
def private_research_index(
    q: str = "",
    vendor: str = "",
    status: str = "",
    stockout: str = "",
    page: int = 1,
) -> HTMLResponse:
    workspace = _workspace()
    projection = workspace.get("projection")
    manifest = workspace.get("manifest")
    if not isinstance(projection, dict) or not isinstance(manifest, dict):
        raise HTTPException(status_code=503, detail="Private projection is absent")
    if any(len(value) > 200 for value in (q, vendor, status, stockout)):
        raise HTTPException(status_code=422, detail="Private research filter is too long")
    if stockout not in {"", "NOT_CAPTURED", "CAPTURED_IN_RESEARCH_INPUT", "INCOMPLETE_OR_UNKNOWN"}:
        raise HTTPException(status_code=422, detail="Stockout evidence filter differs")
    development_contract = projection.get("contract")
    development_research = (
        development_contract
        in {
            "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V2",
            "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3",
            V3_CORRECTED_PROJECTION_CONTRACT,
        }
        and projection.get("data_mode")
        == "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY"
    )
    development_v3 = (
        development_contract == "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3"
    )
    development_corrected = (
        development_contract == V3_CORRECTED_PROJECTION_CONTRACT
    )
    development_owner_rows = development_v3 or development_corrected
    try:
        matched_count, total_pages, selected = _page_app_owned_workspace_rows(
            workspace,
            query=q,
            vendor=vendor,
            status=status,
            stockout=stockout,
            page=page,
            page_size=50,
        )
    except PrivateResearchProjectionError as exc:
        raise HTTPException(status_code=503, detail="Private projection is invalid") from exc
    if page < 1 or page > total_pages:
        raise HTTPException(status_code=404, detail="Private research page is absent")
    if development_corrected:
        vendor_names = sorted(
            {
                str(name)
                for row in projection.get("owner_worksheet", [])
                if isinstance(row, dict)
                for name in row.get("supplier_names", [])
                if isinstance(name, str) and name
            }
        )
    else:
        vendor_names = sorted(
            {
                str(item)
                for item in projection.get("vendor_names", [])
                if isinstance(item, str) and item
            }
        )
    options = ["<option value=''>All named suppliers</option>"] + [
        f"<option value='{html.escape(name, quote=True)}'"
        f"{' selected' if name == vendor else ''}>{html.escape(name)}</option>"
        for name in vendor_names
    ]
    corrected_status_by_id = (
        {
            str(row.get("shopify_variant_id")): row.get(
                "primary_forecast_status"
            )
            for row in projection.get("coverage_rows", [])
            if isinstance(row, dict)
        }
        if development_corrected
        else {}
    )
    cards = []
    for row in selected:
        raw_variant_id = str(row.get("shopify_variant_id", ""))
        variant_id = html.escape(raw_variant_id)
        title = html.escape(
            " — ".join(
                value
                for value in (
                    str(row.get("product_title") or ""),
                    str(row.get("variant_title") or ""),
                )
                if value
            )
        )
        if development_research:
            suppliers = row.get("supplier_names", [])
            supplier = html.escape(
                ", ".join(str(item) for item in suppliers)
                if isinstance(suppliers, list) and suppliers
                else "No named supplier"
            )
            reasons = row.get("reason_codes", [])
            scenarios = row.get("scenario_results", {})
            stage_status = row.get("stage_status", {})
            recorded_coverage = row.get("recorded_sales_coverage", {})
            captured_stock = row.get("captured_stock_provenance", {})
            recent_sales = row.get("recent_observed_sales", {})
            next_stage = row.get("next_missing_stage", {})
            unapproved_hypotheses = row.get(
                "unapproved_supplier_offer_summary", {}
            )
            result_summary = "".join(
                "<li><strong>"
                + html.escape(scenario_id)
                + ":</strong> model "
                + html.escape(str(result.get("selected_model") or "not calculated"))
                + " · point "
                + html.escape(str(result.get("point_forecast_units") or "—"))
                + (
                    " · protection "
                    + html.escape(str(result.get("protection_units") or "—"))
                    + " · target "
                    + html.escape(str(result.get("target_units") or "—"))
                    + " · Joint H3/H10/H17 confidence "
                    + html.escape(str(result.get("joint_confidence") or "—"))
                    + " · horizon evaluation WAPE "
                    + html.escape(
                        str(result.get("horizon_evaluation_wape") or "—")
                    )
                    if development_corrected
                    else " · target "
                    + html.escape(str(result.get("target_units") or "—"))
                    + " · confidence "
                    + html.escape(str(result.get("confidence") or "—"))
                )
                + " · status "
                + html.escape(
                    str(
                        result.get("primary_status")
                        or corrected_status_by_id.get(raw_variant_id)
                        if development_owner_rows
                        else result.get("status")
                    )
                )
                + "</li>"
                for scenario_id, result in (
                    (
                        (scenario_id, scenarios.get(scenario_id))
                        for scenario_id in ("H3", "H10", "H17")
                    )
                    if development_owner_rows and isinstance(scenarios, dict)
                    else scenarios.items()
                    if isinstance(scenarios, dict)
                    else ()
                )
                if isinstance(result, dict)
            )
            summary = (
                "</h2><p><strong>Named suppliers:</strong> "
                + supplier
                + " · <strong>Stockout evidence:</strong> "
                + _stockout_evidence_status(row)
                + "</p><ul>"
                + result_summary
                + "</ul>"
                + (
                    "<p><strong>Target semantics:</strong> Research demand and "
                    "protection target — not a purchase quantity, case quantity, "
                    "recommended buy, draft, PO, or order.</p>"
                    if development_corrected
                    else ""
                )
                + "<p><strong>Stage status:</strong> "
                + html.escape(
                    json.dumps(stage_status, sort_keys=True, separators=(",", ":"))
                )
                + "</p><p><strong>Recorded sales coverage:</strong> "
                + html.escape(
                    json.dumps(
                        recorded_coverage, sort_keys=True, separators=(",", ":")
                    )
                )
                + "</p><p><strong>Captured stock provenance:</strong> "
                + html.escape(
                    json.dumps(
                        captured_stock, sort_keys=True, separators=(",", ":")
                    )
                )
                + "</p>"
                + (
                    "<p><strong>Existence basis:</strong> "
                    + html.escape(str(row.get("existence_basis") or "not established"))
                    + "</p><p><strong>Recent recorded sales:</strong> "
                    + html.escape(
                        json.dumps(recent_sales, sort_keys=True, separators=(",", ":"))
                    )
                    + "</p><p><strong>Next missing stage:</strong> "
                    + html.escape(
                        json.dumps(next_stage, sort_keys=True, separators=(",", ":"))
                    )
                    + "</p><p><strong>Unapproved supplier/offer summary:</strong> "
                    + html.escape(
                        json.dumps(
                            unapproved_hypotheses,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                    + "</p>"
                    if development_owner_rows
                    else ""
                )
            )
        else:
            supplier = html.escape(
                str(row.get("supplier_name") or "No named supplier")
            )
            reasons = row.get("missing_data_reasons", [])
            summary = (
                "</h2><p><strong>Named hypothesis:</strong> "
                + supplier
                + " · <strong>Join:</strong> "
                + html.escape(str(row.get("join_status", "")))
                + " · <strong>Stockout evidence:</strong> "
                + _stockout_evidence_status(row)
                + "</p>"
            )
        cards.append(
            "<article class='record' data-variant-id='"
            + variant_id
            + "'><h2>Variant "
            + variant_id
            + " — "
            + title
            + summary
            + (
                "<p><strong>Status/reason codes:</strong> "
                if development_corrected
                else "<p><strong>Missing/pending:</strong> "
            )
            + html.escape(", ".join(str(item) for item in reasons) or "none")
            + "</p><details><summary>Evidence and research diagnostics</summary><pre>"
            + _json_html(row)
            + "</pre></details></article>"
        )
    base_params = {"q": q, "vendor": vendor, "status": status, "stockout": stockout}
    navigation = []
    if page > 1:
        navigation.append(
            f"<a href='/private-research?{html.escape(urlencode({**base_params, 'page': page - 1}), quote=True)}'>Previous</a>"
        )
    if page < total_pages:
        navigation.append(
            f"<a href='/private-research?{html.escape(urlencode({**base_params, 'page': page + 1}), quote=True)}'>Next</a>"
        )
    declared = projection.get("declared_coverage", {})
    mode_badge = (
        (
            "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY · CORRECTED JOINT "
            "H3/H10/H17 · ZERO AUTHORITY"
            if development_corrected
            else "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY · H3/H10/H17 ASSUMPTIONS"
        )
        if development_research
        else "PRIVATE_REAL_SOURCE_REVIEW"
    )
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='robots' content='noindex,nofollow'><title>Private real-source research</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:90rem;margin:2rem auto;padding:0 1rem}"
        ".warning{border:3px solid #8b1e1e;background:#fff4f4;padding:1rem;font-weight:700}"
        ".badges span{display:inline-block;border:1px solid #555;padding:.25rem .5rem;margin:.2rem}"
        "form{display:grid;grid-template-columns:repeat(auto-fit,minmax(12rem,1fr));gap:.6rem;align-items:end}"
        "label{display:grid;gap:.2rem}.record{border-top:1px solid #aaa;padding:1rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}"
        "nav{display:flex;gap:1rem}</style></head><body>"
        "<h1>Private real-source procurement research</h1>"
        "<p class='warning'>REVIEW ONLY · UNAPPROVED · ZERO OPERATIONAL AUTHORITY. "
        "No mapping, price, selection, forecast policy, DRAFT, PO, Shopify write, supplier contact, or order is authorized.</p>"
        "<p class='badges'><span>"
        + html.escape(mode_badge)
        + "</span><span>immutable workspace</span>"
        f"<span>projection {html.escape(str(manifest.get('projection_sha256', '')))}</span></p>"
        "<details><summary>Source and scope coverage</summary><pre>"
        + _json_html(declared)
        + "</pre></details>"
        + (
            "<section><h2>"
            + (
                (
                    "Corrected-V3 joint-horizon source, reviewed exact-ID evidence, "
                    "policy, and 138-day controls"
                    if development_corrected
                    else "V3 additive research source, exact-ID existence evidence, policy, and 138-day coverage"
                )
                if development_owner_rows
                else "V2 research source, policy, and 138-day coverage"
            )
            + "</h2><pre>"
            + _json_html(
                _corrected_research_provenance(projection)
                if development_corrected
                else _v2_research_provenance(projection)
            )
            + "</pre></section>"
            if development_research
            else ""
        )
        + (
            "<section><h2>Primary per-horizon coverage outcomes</h2><pre>"
            + _json_html(
                projection.get("joint_forecast_research", {}).get(
                    "primary_status_counts"
                )
                if development_corrected
                and isinstance(projection.get("joint_forecast_research"), dict)
                else projection.get("forecast_primary_status_counts")
            )
            + "</pre></section>"
            if development_owner_rows
            else ""
        )
        + (
            "<section><h2>Grouped decision queue</h2><pre>"
            + _json_html(projection.get("grouped_decision_queue"))
            + "</pre></section>"
            if development_owner_rows
            else ""
        )
        + "<p><a href='/private-research/artifacts/owner-preview.html'>Offline HTML preview</a> · "
        "<a href='/private-research/artifacts/owner-worksheet.csv'>Owner worksheet CSV</a> · "
        "<a href='/private-research/artifacts/coverage.json'>Coverage JSON</a> · "
        "<a href='/private-research/artifacts/projection.json'>Projection JSON</a></p>"
        "<form method='get' action='/private-research'>"
        f"<label>Search<input name='q' value='{html.escape(q, quote=True)}'></label>"
        f"<label>Named supplier<select name='vendor'>{''.join(options)}</select></label>"
        f"<label>Status or reason<input name='status' value='{html.escape(status, quote=True)}'></label>"
        "<label>Stockout evidence<select name='stockout'>"
        + "".join(
            f"<option value='{item}'{' selected' if item == stockout else ''}>{label}</option>"
            for item, label in (
                ("", "Any"),
                ("NOT_CAPTURED", "Not captured"),
                ("CAPTURED_IN_RESEARCH_INPUT", "Captured in research input"),
                ("INCOMPLETE_OR_UNKNOWN", "Incomplete or unknown"),
            )
        )
        + "</select></label><button type='submit'>Filter</button></form>"
        f"<p>Showing {len(selected)} of {matched_count} matched rows · page {page} of {total_pages}.</p>"
        + "".join(cards)
        + f"<nav>{' · '.join(navigation)}</nav>"
        "<p>Quit the private browser process when finished to clear its local HTTP Basic credential cache.</p>"
        "</body></html>"
    )


@app.get("/private-research/manifest")
def private_research_manifest() -> JSONResponse:
    workspace = _workspace()
    manifest = workspace.get("manifest")
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=503, detail="Private manifest is absent")
    return JSONResponse(manifest)


@app.get("/private-research/projection")
def private_research_projection() -> Response:
    workspace = _workspace()
    projection = workspace.get("projection")
    if not isinstance(projection, dict):
        raise HTTPException(status_code=503, detail="Private projection is absent")
    manifest = workspace.get("manifest")
    if isinstance(manifest, dict) and manifest.get("contract") in {
        V3_CONTRACT,
        V3_CORRECTED_CONTRACT,
    }:
        streamed = _staging_artifact_response("projection.json", attachment=False)
        if streamed is not None:
            return streamed
        artifacts = workspace.get("artifacts")
        projection_bytes = (
            artifacts.get("projection.json")
            if isinstance(artifacts, dict)
            else None
        )
        if not isinstance(projection_bytes, bytes):
            raise HTTPException(
                status_code=503,
                detail="Private projection artifact is absent",
            )
        # V3 projections are large enough that independently rebuilt mappings
        # can have equivalent canonical semantics but different insertion order.
        # Serve the already validated immutable artifact so the browser's raw
        # body commitment is stable and exactly workspace-manifest bound.
        return Response(content=projection_bytes, media_type="application/json")
    return JSONResponse(projection)


@app.get("/private-research/artifacts/{artifact_name}")
def private_research_artifact(artifact_name: str) -> Response:
    media_types = {
        "owner-preview.html": "text/html; charset=utf-8",
        "owner-worksheet.csv": "text/csv; charset=utf-8",
        "projection.json": "application/json",
        "coverage.json": "application/json",
    }
    if artifact_name not in media_types:
        raise HTTPException(status_code=404, detail="Private artifact is not published")
    streamed = _staging_artifact_response(artifact_name, attachment=True)
    if streamed is not None:
        return streamed
    artifacts = _workspace().get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(artifacts.get(artifact_name), bytes):
        raise HTTPException(status_code=404, detail="Private artifact is absent")
    return Response(
        artifacts[artifact_name],
        media_type=media_types[artifact_name],
        headers={"Content-Disposition": f'attachment; filename="{artifact_name}"'},
    )


from .staging_composition import install_access_boundary as _install_access_boundary


_ACCESS_BOUNDARY = _install_access_boundary(
    app,
    local_middleware=PrivateResearchBasicAccessMiddleware,
    expected_worker_role="research",
)
