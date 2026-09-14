"""Checksum-pinned fabricated review packets for the owner demo only."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID


PACKET_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "synthetic_mapping_review_packet.json"
)
PACKET_SHA256 = "a9d08871dcd5b7fc11dd30a9bad2f8552a36c1a55d60d1841ab2ead62c74baae"
PACKET_CONTRACT = "BUFFALO_SYNTHETIC_MAPPING_REVIEW_PACKET_V2"
SOURCE_AUTHORITY_STATE = "NOT_APPROVED"
SOURCE_IMPORT_STATE = "NOT_IMPORT_READY"


class SyntheticPacketError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash(value: Any) -> str:
    data = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(data).hexdigest()


def load_synthetic_mapping_packets() -> list[dict[str, Any]]:
    raw = PACKET_PATH.read_bytes()
    if _hash(raw) != PACKET_SHA256:
        raise SyntheticPacketError("synthetic mapping packet checksum differs")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyntheticPacketError("synthetic mapping packet is malformed") from exc
    if not isinstance(value, dict) or set(value) != {
        "contract",
        "fixture_disclosure",
        "packages",
    }:
        raise SyntheticPacketError("synthetic mapping packet shape differs")
    if value["contract"] != PACKET_CONTRACT or not isinstance(value["packages"], list):
        raise SyntheticPacketError("synthetic mapping packet contract differs")
    if len(value["packages"]) != 2:
        raise SyntheticPacketError("synthetic mapping packet scenario count differs")
    results: list[dict[str, Any]] = []
    keys: set[UUID] = set()
    for source in value["packages"]:
        if not isinstance(source, dict) or set(source) != {
            "candidates",
            "intake_idempotency_key",
            "source_authority_state",
            "source_evidence",
            "source_import_state",
            "source_is_simulation",
            "source_package_id",
            "source_revision",
        }:
            raise SyntheticPacketError("synthetic review-package shape differs")
        if (
            source["source_authority_state"] != SOURCE_AUTHORITY_STATE
            or source["source_import_state"] != SOURCE_IMPORT_STATE
        ):
            raise SyntheticPacketError("synthetic zero-authority contract differs")
        key = UUID(str(source["intake_idempotency_key"]))
        if key in keys:
            raise SyntheticPacketError("synthetic intake key is duplicated")
        keys.add(key)
        candidates = copy.deepcopy(source["candidates"])
        if not isinstance(candidates, list) or not candidates:
            raise SyntheticPacketError("synthetic candidate set is empty")
        artifact_sha = _hash(source["source_evidence"])
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise SyntheticPacketError("synthetic candidate is not an object")
            printed = candidate.pop("printed_occurrence_text", None)
            if not isinstance(printed, str) or not printed:
                raise SyntheticPacketError("synthetic printed occurrence is absent")
            candidate["printed_occurrence_sha256"] = _hash(printed.encode("utf-8"))
            # The write boundary accepts only fully sealed occurrence provenance.
            # Keep these synthetic-adapter facts inside the candidate payload that
            # is hashed below; the intake service must never invent them later.
            candidate["source_table_name"] = "supplier_offers_v5"
            candidate["source_row_key"] = str(candidate["occurrence_key"])
            candidate["source_locator"] = {}
            candidate["source_file_sha256"] = artifact_sha
            origin = candidate.pop("independent_origin_material", None)
            observation = candidate.pop("independent_observation_material", None)
            if (origin is None) != (observation is None):
                raise SyntheticPacketError("synthetic independent evidence is incomplete")
            if origin is not None:
                candidate["independent_linkage_evidence"] = [
                    {
                        "evidence_mode": "DETERMINISTIC_INDEPENDENT",
                        "origin_sha256": _hash(str(origin).encode("utf-8")),
                        "observation_sha256": _hash(str(observation).encode("utf-8")),
                        "fixture_disclosure": "FABRICATED_TEST_EVIDENCE",
                    }
                ]
        relationships = [
            item
            for candidate in candidates
            for item in candidate.get("component_relationships", [])
        ]
        payload_sha = _hash(candidates)
        root_sha = _hash(
            {
                "source_artifact_sha256": artifact_sha,
                "source_payload_sha256": payload_sha,
            }
        )
        relationship_sha = _hash(relationships)
        batch_sha = _hash(
            {
                "source_package_id": source["source_package_id"],
                "source_revision": source["source_revision"],
                "occurrence_keys": [item["occurrence_key"] for item in candidates],
                "source_authority_state": source["source_authority_state"],
                "source_import_state": source["source_import_state"],
            }
        )
        seal_sha = _hash(
            {
                "source_root_sha256": root_sha,
                "relationship_table_sha256": relationship_sha,
                "source_batch_sha256": batch_sha,
            }
        )
        package = {
            "source_package_id": source["source_package_id"],
            "source_revision": source["source_revision"],
            "source_artifact_ref": f"synthetic-packet:{source['source_package_id']}",
            "source_artifact_sha256": artifact_sha,
            "source_root_sha256": root_sha,
            "source_seal_sha256": seal_sha,
            "relationship_table_sha256": relationship_sha,
            "source_batch_sha256": batch_sha,
            "source_payload_sha256": payload_sha,
            "supplier_period_scope": {"kind": "FABRICATED_TEST_PERIOD"},
            "prerequisites": {
                "packet_contract": PACKET_CONTRACT,
                "packet_sha256": PACKET_SHA256,
            },
            "structural_state": "READY",
            "source_evidence_state": "READY",
            "semantic_state": "READY",
            "source_authority_state": source["source_authority_state"],
            "source_import_state": source["source_import_state"],
            "source_is_simulation": bool(source["source_is_simulation"]),
        }
        results.append(
            {
                "intake_idempotency_key": key,
                "package": package,
                "candidates": candidates,
                "fixture_disclosure": value["fixture_disclosure"],
            }
        )
    return results
