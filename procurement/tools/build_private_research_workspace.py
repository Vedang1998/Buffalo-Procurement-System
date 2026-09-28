#!/usr/bin/env python3
"""Build a sealed, zero-authority private research workspace."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
if str(PROCUREMENT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.private_research import build_private_research_workspace
from procurement_os.private_research_intake import build_private_research_intake


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--catalog-manifest", type=Path, required=True)
    parser.add_argument("--daily-sales-manifest", type=Path, required=True)
    parser.add_argument("--a1-package", type=Path, required=True)
    parser.add_argument("--a1-external-evidence-root", type=Path)
    parser.add_argument("--catalog-manifest-sha256")
    parser.add_argument("--daily-sales-manifest-sha256")
    args = parser.parse_args()
    try:
        intake = build_private_research_intake(
            args.private_root,
            catalog_manifest_path=args.catalog_manifest,
            daily_sales_manifest_path=args.daily_sales_manifest,
            a1_package_path=args.a1_package,
            a1_external_evidence_root=args.a1_external_evidence_root,
            expected_catalog_manifest_sha256=args.catalog_manifest_sha256,
            expected_daily_sales_manifest_sha256=args.daily_sales_manifest_sha256,
        )
        manifest = build_private_research_workspace(
            args.private_root, str(intake["intake_id"])
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    workspace_root = (
        args.private_root
        / "private-research"
        / "workspaces"
        / str(manifest["workspace_id"])
    )
    print(
        json.dumps(
            {
                "contract": manifest["contract"],
                "authority": manifest["authority"],
                "operational_authority": False,
                "intake_id": intake["intake_id"],
                "workspace_id": manifest["workspace_id"],
                "workspace_root": str(workspace_root),
                "coverage": intake.get("coverage"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
