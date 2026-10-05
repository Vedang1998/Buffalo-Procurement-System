#!/usr/bin/env python3
"""Operator-only export/restore for the immutable staging fixture."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from initialize_synthetic_demo import (  # noqa: E402
    DEVELOPMENT_FORECAST_V2_PROFILE,
    initialize,
)
from procurement_os.synthetic_staging_database import (  # noqa: E402
    EXPECTED_DATABASE,
    FIXTURE_BUSINESS_DATE,
    target_from_environment,
)
from procurement_os.synthetic_staging_transfer import (  # noqa: E402
    create_transfer_artifact,
    prepare_transfer_destination,
    restore_and_transition,
)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit("explicit synthetic transfer input is required")
    return value


def _require_boundary(args: argparse.Namespace) -> None:
    if (
        args.confirm_database != EXPECTED_DATABASE
        or args.confirm_service_state != "STOPPED"
        or os.environ.get("DATABASE_URL")
        or os.environ.get("TEST_DATABASE_URL")
    ):
        raise SystemExit("explicit stopped synthetic transfer boundary is required")


def _read_secret_fd(descriptor: int) -> str:
    if descriptor < 3:
        raise SystemExit("synthetic transfer credential descriptor differs")
    payload = bytearray()
    try:
        while len(payload) <= 256:
            block = os.read(descriptor, min(257 - len(payload), 64))
            if not block:
                break
            payload.extend(block)
    except OSError as exc:
        raise SystemExit("synthetic transfer credential is unavailable") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    try:
        secret = bytes(payload).decode("ascii")
    except UnicodeDecodeError as exc:
        raise SystemExit("synthetic transfer credential differs") from exc
    if (
        not 32 <= len(secret) <= 256
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in secret)
    ):
        raise SystemExit("synthetic transfer credential differs")
    return secret


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for name in ("export", "prepare-target", "restore"):
        selected = subparsers.add_parser(name)
        selected.add_argument("--confirm-database", required=True)
        selected.add_argument("--confirm-service-state", required=True)
    export = subparsers.choices["export"]
    export.add_argument("--label", required=True)
    for name in ("prepare-target", "restore"):
        selected = subparsers.choices[name]
        selected.add_argument("--manifest-sha256", required=True)
        selected.add_argument("--source-commit", required=True)
        selected.add_argument("--source-tree", required=True)
    restore = subparsers.choices["restore"]
    restore.add_argument("--provisioner-secret-fd", required=True, type=int)
    restore.add_argument("--runtime-secret-fd", required=True, type=int)
    args = parser.parse_args(argv)
    _require_boundary(args)

    if args.operation == "export":
        source_url = _required("BUFFALO_SYNTHETIC_TRANSFER_SOURCE_URL")
        initialized = initialize(
            source_url,
            date.fromisoformat(FIXTURE_BUSINESS_DATE),
            profile=DEVELOPMENT_FORECAST_V2_PROFILE,
        )
        if initialized.get("initialized") is not True:
            raise SystemExit("synthetic transfer source was not freshly initialized")
        manifest = create_transfer_artifact(
            source_url=source_url,
            source_admin_url=_required(
                "BUFFALO_SYNTHETIC_TRANSFER_SOURCE_ADMIN_URL"
            ),
            transfer_root=Path(_required("BUFFALO_SYNTHETIC_TRANSFER_ROOT")),
            source_root=ROOT.parent,
            label=args.label,
        )
        print(
            json.dumps(
                {
                    "initialized": bool(initialized["initialized"]),
                    "label": manifest.parent.name,
                    "manifest_sha256": hashlib.sha256(
                        manifest.read_bytes()
                    ).hexdigest(),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0

    runtime_url = _required("BUFFALO_SYNTHETIC_TRANSFER_RUNTIME_URL")
    target_environment = dict(os.environ)
    target_environment["DATABASE_URL"] = runtime_url
    target_environment["BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256"] = (
        args.manifest_sha256
    )
    common = {
        "artifact_root": Path(_required("BUFFALO_SYNTHETIC_TRANSFER_ARTIFACT")),
        "expected_manifest_sha256": args.manifest_sha256,
        "target": target_from_environment(target_environment),
        "source_root": ROOT.parent,
        "expected_source_commit": args.source_commit,
        "expected_source_tree": args.source_tree,
    }
    if args.operation == "prepare-target":
        proof = prepare_transfer_destination(
            maintenance_admin_url=_required(
                "BUFFALO_SYNTHETIC_TRANSFER_MAINTENANCE_ADMIN_URL"
            ),
            **common,
        )
    else:
        proof = restore_and_transition(
            admin_url=_required("BUFFALO_SYNTHETIC_TRANSFER_ADMIN_URL"),
            provisioner_url=_required(
                "BUFFALO_SYNTHETIC_TRANSFER_PROVISIONER_URL"
            ),
            runtime_url=runtime_url,
            provisioner_secret=_read_secret_fd(args.provisioner_secret_fd),
            runtime_secret=_read_secret_fd(args.runtime_secret_fd),
            **common,
        )
    print(json.dumps(proof, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
