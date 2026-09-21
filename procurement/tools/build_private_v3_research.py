#!/usr/bin/env python3
"""Build the additive exact-ID preexistence V3 private research workspace."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROCUREMENT_ROOT.parent
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.private_research import (  # noqa: E402
    _build_private_v3_research_workspace_from_verified,
)
from procurement_os.private_research_v3 import (  # noqa: E402
    PrivateResearchV3Error,
    build_private_v3_research_input,
    input_manifest_key,
    write_private_v3_research_input,
)
from procurement_os.private_research_v2 import read_private_v2_research_bundle  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bind reviewed exact-ID creation evidence to an immutable V2 parent "
            "and publish a separate zero-authority V3 research workspace."
        )
    )
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--parent-v2-input-id", required=True)
    parser.add_argument(
        "--seed-manifest",
        type=Path,
        default=REPOSITORY_ROOT / "procurement" / "seed" / "manifest.json",
    )
    parser.add_argument(
        "--seed-variants",
        type=Path,
        default=REPOSITORY_ROOT / "procurement" / "seed" / "variants.csv",
    )
    args = parser.parse_args(argv)
    try:
        parent, base = read_private_v2_research_bundle(
            args.private_root, args.parent_v2_input_id
        )
        research_input = build_private_v3_research_input(
            base,
            parent_v2_input=parent,
            seed_manifest_path=args.seed_manifest,
            seed_variants_path=args.seed_variants,
        )
        research_input = write_private_v3_research_input(
            args.private_root,
            research_input,
            seed_manifest_path=args.seed_manifest,
            seed_variants_path=args.seed_variants,
        )
        workspace = _build_private_v3_research_workspace_from_verified(
            args.private_root,
            research_input,
            parent,
            base,
        )
    except (PrivateResearchV3Error, OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    controls = research_input["eligibility_controls"]
    print(
        json.dumps(
            {
                "authority": "PRIVATE_REAL_SOURCE_REVIEW_ONLY",
                "commercial_authority": False,
                "production_activation": False,
                "parent_v2_input_id": args.parent_v2_input_id,
                "input_id": research_input["input_id"],
                "input_manifest_key": input_manifest_key(
                    research_input["input_id"]
                ),
                "workspace_id": workspace["workspace_id"],
                "projection_sha256": workspace["projection_sha256"],
                "catalog_variant_count": controls["catalog_variant_count"],
                "parent_first_day_supported_count": controls[
                    "parent_first_day_supported_count"
                ],
                "added_prewindow_exact_id_count": controls[
                    "added_prewindow_exact_id_count"
                ],
                "eligible_variant_count": controls["eligible_variant_count"],
                "missing_evidence_variant_count": controls[
                    "missing_evidence_variant_count"
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
