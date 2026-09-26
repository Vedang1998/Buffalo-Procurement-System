#!/usr/bin/env python3
"""Build the additive coherent-horizon corrected-V3 private workspace."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROCUREMENT_ROOT.parent
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.private_research import (  # noqa: E402
    PrivateResearchError,
    build_private_v3_corrected_research_workspace,
)
from procurement_os.private_research_intake import canonical_json_bytes  # noqa: E402
from procurement_os.private_research_v3 import (  # noqa: E402
    PrivateResearchV3Error,
    read_private_v3_research_bundle,
)
from procurement_os.private_research_v3_corrected import (  # noqa: E402
    PARENT_V3_INPUT_ID,
    PrivateResearchV3CorrectedError,
    accepted_parent_snapshot,
    corrected_input_manifest_key,
    delta_manifest_key,
    write_private_v3_corrected_input,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bind the reviewed exact-ID creation delta to the immutable accepted "
            "V3 parent and publish a zero-authority coherent-horizon successor."
        )
    )
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--creation-evidence-delta", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        before = accepted_parent_snapshot(args.private_root)
        parent_v3, _parent_v2, _base = read_private_v3_research_bundle(
            args.private_root, PARENT_V3_INPUT_ID
        )
        delta, corrected_input = write_private_v3_corrected_input(
            args.private_root,
            parent_v3_input=parent_v3,
            delta_csv_path=args.creation_evidence_delta,
            repo_root=REPOSITORY_ROOT,
        )
        manifest = build_private_v3_corrected_research_workspace(
            args.private_root,
            corrected_input["input_id"],
            repo_root=REPOSITORY_ROOT,
        )
        after = accepted_parent_snapshot(args.private_root)
        if canonical_json_bytes(before) != canonical_json_bytes(after):
            raise PrivateResearchV3CorrectedError(
                "accepted parent changed during corrected build"
            )
    except (
        PrivateResearchError,
        PrivateResearchV3Error,
        PrivateResearchV3CorrectedError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    controls = corrected_input["coverage_controls"]
    print(
        json.dumps(
            {
                "authority": "ZERO_AUTHORITY_RESEARCH_ONLY",
                "commercial_authority": False,
                "production_activation": False,
                "parent_v3_input_id": PARENT_V3_INPUT_ID,
                "creation_delta_id": delta["delta_id"],
                "creation_delta_key": delta_manifest_key(delta["delta_id"]),
                "corrected_input_id": corrected_input["input_id"],
                "corrected_input_key": corrected_input_manifest_key(
                    corrected_input["input_id"]
                ),
                "workspace_id": manifest["workspace_id"],
                "projection_sha256": manifest["projection_sha256"],
                "artifacts": manifest["artifacts"],
                "current_catalog_count": controls["current_catalog_count"],
                "eligible_count": controls["eligible_count"],
                "not_applicable_count": controls["not_applicable_count"],
                "blocked_count": controls["blocked_count"],
                "not_processed_count": controls["not_processed_count"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
