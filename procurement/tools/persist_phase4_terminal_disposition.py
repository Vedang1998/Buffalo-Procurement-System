#!/usr/bin/env python3
"""Inspect or persist only the frozen Phase 4 terminal disposition."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.historical_sales_terminal import (  # noqa: E402
    TerminalExecutionContext,
    derive_runtime_execution_git_identity,
    inspect_terminal_state,
    load_terminal_artifact,
    persist_terminal_disposition,
)


TERMINAL_MANIFEST = (
    PROCUREMENT_ROOT / "review" / "phase4_terminal_disposition_manifest.csv"
)
ORIGINAL_MANIFEST = (
    PROCUREMENT_ROOT / "review" / "phase4_identity_manifest_corrected.csv"
)


def connect_database(database_url: str) -> Any:
    import psycopg

    return psycopg.connect(database_url)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Controlled Phase 4 terminal-disposition persistence"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--actor", required=True)
    parser.add_argument("--expected-execution-git-sha")
    parser.add_argument("--terminal-manifest", default=str(TERMINAL_MANIFEST))
    parser.add_argument("--original-manifest", default=str(ORIGINAL_MANIFEST))
    parser.add_argument(
        "--acknowledge-exact-terminal-persistence",
        action="store_true",
        help="Required only with --apply after independent review and authorization.",
    )
    return parser


def _exact_path(value: str, expected: Path, label: str) -> Path:
    resolved = Path(value).expanduser().resolve()
    if resolved != expected.resolve():
        raise ValueError(f"{label} must be the frozen repository artifact")
    return resolved


def execute(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    args = _parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    actor = str(args.actor).strip()
    if not actor:
        raise ValueError("nonblank terminal execution actor is required")
    if args.apply and not args.acknowledge_exact_terminal_persistence:
        raise ValueError("apply requires exact terminal-persistence acknowledgement")

    terminal_path = _exact_path(args.terminal_manifest, TERMINAL_MANIFEST, "terminal manifest")
    original_path = _exact_path(args.original_manifest, ORIGINAL_MANIFEST, "original manifest")
    artifact = load_terminal_artifact(terminal_path, original_path)
    execution = derive_runtime_execution_git_identity(
        args.expected_execution_git_sha
    )
    database_url = environment.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("database configuration is unavailable")
    context = TerminalExecutionContext(
        actor=actor,
        expected_execution_git_sha=args.expected_execution_git_sha,
    )

    with connect_database(database_url) as conn:
        if args.dry_run:
            report = inspect_terminal_state(conn, artifact, execution.git_sha)
            return {
                "mode": "dry-run",
                "execution_git_sha": execution.git_sha,
                **report,
            }
        return {
            "mode": "apply",
            **persist_terminal_disposition(conn, artifact, context),
        }


def _safe_error(exc: BaseException, environment: Mapping[str, str]) -> str:
    message = str(exc).replace("\n", " ")[:1000]
    message = re.sub(
        r"(?i)postgres(?:ql)?://[^\s]+", "postgresql://[REDACTED]", message
    )
    for name in ("DATABASE_URL",):
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
