#!/usr/bin/env python3
"""Build the zero-authority private V2 forecast research input and workspace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROCUREMENT_ROOT.parent
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.private_research import (  # noqa: E402
    build_private_v2_research_workspace,
)
from procurement_os.private_research_intake import (  # noqa: E402
    read_private_research_intake,
)
from procurement_os.private_research_v2 import (  # noqa: E402
    PrivateResearchV2Error,
    build_private_v2_composite_history,
    build_private_v2_research_input,
    input_manifest_key,
    read_registered_source_identity,
    write_private_v2_research_input,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the registered same-shop source, compose the exact 138-day "
            "history, and publish a private Development Forecast V2 research workspace."
        )
    )
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--base-intake-id", required=True)
    parser.add_argument("--history-extension", type=Path, required=True)
    parser.add_argument("--history-replacement", type=Path, required=True)
    parser.add_argument("--source-identity", type=Path, required=True)
    parser.add_argument(
        "--seed-aliases",
        type=Path,
        default=REPOSITORY_ROOT / "procurement" / "seed" / "variant_aliases.csv",
    )
    parser.add_argument(
        "--phase4-original",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "procurement"
            / "review"
            / "phase4_identity_manifest_corrected.csv"
        ),
    )
    parser.add_argument(
        "--phase4-terminal",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "procurement"
            / "review"
            / "phase4_terminal_disposition_manifest.csv"
        ),
    )
    args = parser.parse_args(argv)

    try:
        base_intake = read_private_research_intake(
            args.private_root, args.base_intake_id
        )
        source_identity = read_registered_source_identity(args.source_identity)
        composite = build_private_v2_composite_history(
            base_intake,
            extension_capture_path=args.history_extension,
            replacement_capture_path=args.history_replacement,
            source_identity=source_identity,
            seed_alias_path=args.seed_aliases,
            original_authority_path=args.phase4_original,
            terminal_authority_path=args.phase4_terminal,
        )
        research_input = build_private_v2_research_input(
            base_intake,
            composite_history=composite,
            source_identity=source_identity,
        )
        write_private_v2_research_input(
            args.private_root,
            research_input,
            source_capture_paths={
                "HISTORY_EXTENSION_54D": args.history_extension,
                "HISTORY_REPLACEMENT_84D": args.history_replacement,
            },
        )
        workspace = build_private_v2_research_workspace(
            args.private_root, research_input["input_id"]
        )
    except (PrivateResearchV2Error, OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    controls = composite["allocation_controls"]
    result = {
        "authority": "PRIVATE_REAL_SOURCE_REVIEW_ONLY",
        "commercial_authority": False,
        "production_activation": False,
        "source_identity_verdict": source_identity["verdict"],
        "input_id": research_input["input_id"],
        "input_manifest_key": input_manifest_key(research_input["input_id"]),
        "workspace_id": workspace["workspace_id"],
        "projection_sha256": workspace["projection_sha256"],
        "history_start": composite["start_date"],
        "history_end": composite["end_date"],
        "history_days": composite["complete_day_count"],
        "raw_row_count": controls["raw_row_count"],
        "direct_current_row_count": controls["direct_current_row_count"],
        "historically_allocated_row_count": controls[
            "historically_allocated_row_count"
        ],
        "absent_current_target_row_count": controls[
            "absent_current_target_row_count"
        ],
        "quarantined_row_count": controls["quarantined_row_count"],
        "eligible_variant_count": controls["eligible_variant_count"],
        "preexistence_unproven_variant_count": controls[
            "preexistence_unproven_variant_count"
        ],
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
