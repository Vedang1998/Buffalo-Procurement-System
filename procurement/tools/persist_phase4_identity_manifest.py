#!/usr/bin/env python3
"""Dry-run or apply the exact owner-approved Phase 4 identity manifest."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.historical_sales_manifest import (
    ManifestExecutionContext,
    dry_run_manifest,
    load_authorized_manifest,
    persist_manifest_decisions,
    require_review_authorization,
)


def connect_database(database_url: str) -> Any:
    import psycopg

    return psycopg.connect(database_url)


def repository_state() -> tuple[str, bool]:
    head = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    committed = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "cat-file", "-e", f"{head}^{{commit}}"],
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 0
    return head, not status and committed and bool(re.fullmatch(r"[0-9a-f]{40}", head))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Controlled Phase 4 identity-decision manifest persistence"
    )
    parser.add_argument("--manifest", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--actor", required=True)
    return parser


def execute(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    args = _parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    actor = str(args.actor).strip()
    if not actor:
        raise ValueError("nonblank manifest actor is required")

    require_review_authorization(
        environment.get("RECONCILIATION_REVIEW_TOKEN"),
        environment.get("PHASE4_REVIEW_TOKEN_INPUT"),
    )
    manifest = load_authorized_manifest(args.manifest)
    head, clean = repository_state()
    if args.apply and not clean:
        raise RuntimeError("apply requires a clean committed Git worktree")
    database_url = environment.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("production database configuration is unavailable")
    context = ManifestExecutionContext(actor=actor, implementation_git_sha=head)

    with connect_database(database_url) as conn:
        if args.dry_run:
            return dry_run_manifest(conn, manifest, context)
        return persist_manifest_decisions(conn, manifest, context)


def _safe_error(exc: BaseException, environment: Mapping[str, str]) -> str:
    message = str(exc).replace("\n", " ")[:1000]
    message = re.sub(r"(?i)postgres(?:ql)?://[^\s]+", "postgresql://[REDACTED]", message)
    for name in (
        "DATABASE_URL",
        "RECONCILIATION_REVIEW_TOKEN",
        "PHASE4_REVIEW_TOKEN_INPUT",
    ):
        value = environment.get(name)
        if value:
            message = message.replace(value, "[REDACTED]")
    return message or type(exc).__name__


def main(argv: Sequence[str] | None = None) -> int:
    try:
        report = execute(argv)
    except BaseException as exc:
        print(
            f"ERROR: {type(exc).__name__}: {_safe_error(exc, os.environ)}",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
