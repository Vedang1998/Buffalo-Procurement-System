#!/usr/bin/env python3
"""Operator-only synthetic staging bootstrap/transition; never called at startup."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from procurement_os.synthetic_staging_database import (
    EXPECTED_DATABASE,
    bootstrap_roles,
    provision_contract,
)


def _read_secret_fd(descriptor: int | None) -> str:
    if descriptor is None or descriptor < 3:
        raise SystemExit("synthetic staging credential descriptor differs")
    payload = bytearray()
    try:
        while len(payload) <= 256:
            block = os.read(descriptor, min(257 - len(payload), 64))
            if not block:
                break
            payload.extend(block)
    except OSError as exc:
        raise SystemExit("synthetic staging credential is unavailable") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    try:
        value = bytes(payload).decode("ascii")
    except UnicodeDecodeError as exc:
        raise SystemExit("synthetic staging credential differs") from exc
    if (
        not 32 <= len(value) <= 256
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
    ):
        raise SystemExit("synthetic staging credential differs")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("bootstrap", "provision"))
    parser.add_argument("--confirm-database", required=True)
    parser.add_argument("--provisioner-secret-fd", type=int)
    parser.add_argument("--runtime-secret-fd", type=int)
    args = parser.parse_args(argv)
    variable = (
        "BUFFALO_SYNTHETIC_BOOTSTRAP_URL"
        if args.phase == "bootstrap"
        else "BUFFALO_SYNTHETIC_PROVISIONING_URL"
    )
    url = os.environ.get(variable)
    transfer_manifest_sha256 = os.environ.get(
        "BUFFALO_SYNTHETIC_TRANSFER_MANIFEST_SHA256", ""
    )
    if (
        not url
        or len(transfer_manifest_sha256) != 64
        or any(character not in "0123456789abcdef" for character in transfer_manifest_sha256)
        or args.confirm_database != EXPECTED_DATABASE
    ):
        raise SystemExit("explicit synthetic staging provisioning input is required")
    if os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL"):
        raise SystemExit("ambient application/test database URLs are forbidden")
    with psycopg.connect(url, connect_timeout=5, autocommit=False) as conn:
        if conn.info.dbname != args.confirm_database:
            raise SystemExit("connected provisioning database differs")
        if args.phase == "bootstrap":
            changed = bootstrap_roles(
                conn,
                transfer_manifest_sha256=transfer_manifest_sha256,
                provisioner_secret=_read_secret_fd(args.provisioner_secret_fd),
                runtime_secret=_read_secret_fd(args.runtime_secret_fd),
            )
        else:
            if (
                args.provisioner_secret_fd is not None
                or args.runtime_secret_fd is not None
            ):
                raise SystemExit(
                    "synthetic staging provision does not accept credentials"
                )
            changed = provision_contract(
                conn, transfer_manifest_sha256=transfer_manifest_sha256
            )
        conn.commit()
    disposition = "changed" if changed else "verified-no-op"
    print(f"synthetic staging {args.phase} {disposition}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
