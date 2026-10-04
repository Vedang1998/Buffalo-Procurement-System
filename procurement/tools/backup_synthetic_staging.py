#!/usr/bin/env python3
"""Create one private staging Backup V2 while the app service is stopped."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from procurement_os.synthetic_staging_backup_v2 import (
    create_staging_backup_v2,
)
from procurement_os.synthetic_staging_database import (
    EXPECTED_DATABASE,
    target_from_environment,
)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit("explicit synthetic staging backup input is required")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--confirm-database", required=True)
    parser.add_argument("--confirm-service-state", required=True)
    args = parser.parse_args(argv)
    if (
        args.confirm_database != EXPECTED_DATABASE
        or args.confirm_service_state != "STOPPED"
        or os.environ.get("DATABASE_URL")
        or os.environ.get("TEST_DATABASE_URL")
    ):
        raise SystemExit("explicit stopped staging backup boundary is required")

    runtime_url = _required("BUFFALO_SYNTHETIC_BACKUP_RUNTIME_URL")
    provisioner_url = _required("BUFFALO_SYNTHETIC_PROVISIONING_URL")
    target_environment = dict(os.environ)
    target_environment["DATABASE_URL"] = runtime_url
    target = target_from_environment(target_environment)
    manifest = create_staging_backup_v2(
        runtime_url=runtime_url,
        provisioner_url=provisioner_url,
        target=target,
        recovery_root=Path(_required("BUFFALO_STAGING_PRICE_BACKUP_ROOT")),
        storage_root=Path(_required("PROCUREMENT_STORAGE_ROOT")),
        source_root=ROOT.parent,
        batch_id=args.batch_id,
    )
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "backup_label": manifest.parent.name,
                "manifest_sha256": digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
