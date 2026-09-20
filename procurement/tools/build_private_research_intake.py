#!/usr/bin/env python3
"""Build and read back one offline private-real-source research intake."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.private_research_intake import (  # noqa: E402
    PrivateResearchIntakeError,
    build_private_research_intake,
    intake_manifest_key,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate normalized or frozen native Shopify catalog/daily-sales "
            "manifests plus exact A1 review evidence; publish a REVIEW_ONLY "
            "content-addressed intake."
        )
    )
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--catalog-manifest", type=Path, required=True)
    parser.add_argument("--daily-sales-manifest", type=Path, required=True)
    parser.add_argument("--a1-package", type=Path, required=True)
    parser.add_argument("--a1-external-evidence-root", type=Path)
    parser.add_argument("--catalog-manifest-sha256")
    parser.add_argument("--daily-sales-manifest-sha256")
    args = parser.parse_args(argv)

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
    except (PrivateResearchIntakeError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    result = {
        "authority": intake["authority"]["status"],
        "contract": intake["contract"],
        "data_mode": intake["data_mode"],
        "intake_id": intake["intake_id"],
        "manifest_key": intake_manifest_key(intake["intake_id"]),
        "variant_count": len(intake["variants"]),
        "vendor_count": len(intake["vendors"]),
        "historical_unjoined_variant_count": intake["coverage"][
            "sales_variants_not_in_current_catalog"
        ],
        "zero_authority": intake["zero_authority"],
    }
    print(
        json.dumps(
            result,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
