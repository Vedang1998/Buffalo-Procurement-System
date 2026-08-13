from __future__ import annotations

from typing import Any


REQUIRED_PO_GATES = (
    "CATALOG_SYNC",
    "SALES_BACKFILL",
    "INVENTORY_HISTORY",
    "VENDOR_RULES",
    "PRICE_COVERAGE",
    "MAPPING_INTEGRITY",
    "OPEN_PO_RECONCILIATION",
)


def readiness_gates(conn: Any, *, scope_type: str | None = None, scope_id: str | None = None) -> list[dict]:
    sql = "SELECT gate_name,scope_type,scope_id,status,severity,blocks_po,message,evidence_json,checked_at FROM readiness_gates"
    where = []
    args = []
    if scope_type is not None:
        where.append("scope_type=%s")
        args.append(scope_type)
    if scope_id is not None:
        where.append("scope_id=%s")
        args.append(scope_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY blocks_po DESC, severity DESC, gate_name"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(args))
        return [
            {
                "gate_name": r[0], "scope_type": r[1], "scope_id": r[2], "status": r[3],
                "severity": r[4], "blocks_po": bool(r[5]), "message": r[6],
                "evidence": r[7] or {}, "checked_at": r[8],
            }
            for r in cur.fetchall()
        ]


def readiness_gate_blockers(
    gates: list[dict],
    *,
    vendor_id: str | None = None,
    variant_id: str | None = None,
) -> list[dict]:
    """Return fail-closed blockers for every required gate in affected scope."""
    blockers: list[dict] = []
    global_required_seen: set[str] = set()
    for gate in gates:
        gate_name = str(gate["gate_name"])
        scope_type = gate["scope_type"]
        applies = scope_type == "GLOBAL"
        if vendor_id and scope_type == "VENDOR" and gate["scope_id"] == str(vendor_id):
            applies = True
        if variant_id and scope_type == "VARIANT" and gate["scope_id"] == str(variant_id):
            applies = True
        if not applies:
            continue

        if scope_type == "GLOBAL" and gate_name in REQUIRED_PO_GATES:
            global_required_seen.add(gate_name)
        if gate_name in REQUIRED_PO_GATES and gate["status"] != "PASS":
            blockers.append({"type": "REQUIRED_GATE_NOT_PASS", "detail": gate})
        elif gate.get("blocks_po") and gate["status"] == "FAIL":
            blockers.append({"type": "READINESS_GATE", "detail": gate})

    for gate_name in REQUIRED_PO_GATES:
        if gate_name not in global_required_seen:
            blockers.append({
                "type": "MISSING_REQUIRED_GATE",
                "detail": {
                    "gate_name": gate_name,
                    "scope_type": "GLOBAL",
                    "scope_id": "",
                    "status": "MISSING",
                    "blocks_po": True,
                },
            })
    return blockers


def po_readiness(conn: Any, *, vendor_id: str | None = None, variant_id: str | None = None) -> dict:
    """Fail closed on explicit readiness gates and unresolved material exceptions.

    Global gates always apply. Scoped gates apply only to the requested vendor/variant.
    This avoids the anti-pattern where one bad C-item disables every vendor PO and causes
    operators to bypass safety controls entirely.
    """
    gates = readiness_gates(conn)
    blockers = readiness_gate_blockers(
        gates, vendor_id=vendor_id, variant_id=variant_id,
    )

    with conn.cursor() as cur:
        clauses = ["status='OPEN'", "severity IN ('HIGH','CRITICAL')"]
        args = []
        if vendor_id is None and variant_id is None:
            # Global foundation status must not be disabled by one item/vendor exception.
            clauses.append("vendor_id IS NULL AND variant_id IS NULL")
        else:
            if vendor_id:
                clauses.append("(vendor_id IS NULL OR vendor_id=%s)")
                args.append(vendor_id)
            if variant_id:
                clauses.append("(variant_id IS NULL OR variant_id=%s)")
                args.append(variant_id)
        cur.execute(
            "SELECT exception_id,severity,exception_type,message,variant_id,vendor_id FROM exceptions WHERE " + " AND ".join(clauses) + " ORDER BY exception_id",
            tuple(args),
        )
        for r in cur.fetchall():
            blockers.append({
                "type": "OPEN_EXCEPTION",
                "detail": {"exception_id": r[0], "severity": r[1], "exception_type": r[2], "message": r[3], "variant_id": r[4], "vendor_id": str(r[5]) if r[5] else None},
            })

    return {
        "po_generation_enabled": not blockers,
        "scope": {"vendor_id": vendor_id, "variant_id": variant_id},
        "blockers": blockers,
        "gates": gates,
    }
