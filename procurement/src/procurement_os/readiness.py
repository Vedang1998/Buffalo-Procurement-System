from __future__ import annotations

from typing import Any, Iterable


FOUNDATION_APPLICABLE_GATES = frozenset({"CATALOG_SYNC", "SALES_BACKFILL"})
SUPPORTED_READINESS_SCOPE_TYPES = frozenset({"GLOBAL", "VENDOR", "VARIANT", "RUN"})


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


def _scope_applies(
    scope_type: str,
    scope_id: str,
    *,
    vendor_id: str | None,
    variant_id: str | None,
    run_id: str | None,
) -> bool:
    if scope_type not in SUPPORTED_READINESS_SCOPE_TYPES:
        raise ValueError(f"unsupported readiness scope_type: {scope_type!r}")
    if scope_type == "GLOBAL":
        return True
    target = {
        "VENDOR": vendor_id,
        "VARIANT": variant_id,
        "RUN": run_id,
    }.get(scope_type)
    return target is not None and str(scope_id) == str(target)


def _validated_applicable_gate_names(
    names: Iterable[str] | None,
) -> frozenset[str]:
    if names is None or isinstance(names, (str, bytes)):
        raise ValueError(
            "applicable gate names must be an iterable of nonblank strings"
        )
    validated: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("applicable gate names must be nonblank strings")
        validated.add(name.strip())
    return frozenset(validated)


def readiness_gate_blockers(
    gates: list[dict],
    *,
    vendor_id: str | None = None,
    variant_id: str | None = None,
    run_id: str | None = None,
    applicable_gate_names: Iterable[str] = (),
) -> list[dict]:
    """Evaluate required FAIL and missing-evidence conditions for one PO scope.

    Foundation gates are always applicable. Callers may declare additional gate
    names required for their concrete vendor/item/run calculation. WARN is always
    advisory. Existing ``blocks_po`` rows retain their meaning without making all
    seven canonical gate names globally mandatory.
    """
    declared = _validated_applicable_gate_names(applicable_gate_names)
    required_names = FOUNDATION_APPLICABLE_GATES | declared

    applicable_rows = [
        gate
        for gate in gates
        if _scope_applies(
            str(gate["scope_type"]),
            str(gate["scope_id"]),
            vendor_id=vendor_id,
            variant_id=variant_id,
            run_id=run_id,
        )
    ]
    blockers: list[dict] = []
    for gate in applicable_rows:
        required = gate["gate_name"] in required_names or bool(gate["blocks_po"])
        if required and gate["status"] == "FAIL":
            blockers.append({"type": "READINESS_GATE", "detail": gate})

    evidence_names = {str(gate["gate_name"]) for gate in applicable_rows}
    for gate_name in sorted(required_names - evidence_names):
        blockers.append(
            {
                "type": "MISSING_APPLICABLE_GATE",
                "detail": {
                    "gate_name": gate_name,
                    "status": "MISSING",
                    "scope": {
                        "vendor_id": vendor_id,
                        "variant_id": variant_id,
                        "run_id": run_id,
                    },
                },
            }
        )
    return blockers


def exception_applies(
    exception: dict,
    *,
    vendor_id: str | None = None,
    variant_id: str | None = None,
    run_id: str | None = None,
) -> bool:
    """Conjunctively match every populated exception scope dimension.

    A combined vendor/variant exception does not apply to a vendor-only query,
    and a run-scoped exception does not become global when no run is evaluated.
    """
    for field, target in (
        ("vendor_id", vendor_id),
        ("variant_id", variant_id),
        ("run_id", run_id),
    ):
        scoped_value = exception.get(field)
        if scoped_value is not None and str(scoped_value) != str(target):
            return False
    return True


def exception_blockers(
    exceptions: list[dict],
    *,
    vendor_id: str | None = None,
    variant_id: str | None = None,
    run_id: str | None = None,
) -> list[dict]:
    return [
        {"type": "OPEN_EXCEPTION", "detail": exception}
        for exception in exceptions
        if exception["status"] == "OPEN"
        and exception["severity"] in {"HIGH", "CRITICAL"}
        and exception_applies(
            exception,
            vendor_id=vendor_id,
            variant_id=variant_id,
            run_id=run_id,
        )
    ]


def _effective_readiness_gates(conn: Any) -> list[dict]:
    """Overlay CATALOG_SYNC with the one authoritative catalog-run evaluator."""
    from .catalog import authoritative_catalog_gate

    gates = [
        gate
        for gate in readiness_gates(conn)
        if not (
            gate["gate_name"] == "CATALOG_SYNC"
            and gate["scope_type"] == "GLOBAL"
            and gate["scope_id"] == ""
        )
    ]
    gates.append(authoritative_catalog_gate(conn))
    return sorted(
        gates,
        key=lambda gate: (
            not bool(gate["blocks_po"]),
            str(gate["severity"]),
            str(gate["gate_name"]),
            str(gate["scope_type"]),
            str(gate["scope_id"]),
        ),
    )


def po_readiness(
    conn: Any,
    *,
    vendor_id: str | None = None,
    variant_id: str | None = None,
    run_id: str | None = None,
    applicable_gate_names: Iterable[str] = (),
) -> dict:
    """Fail closed for required gates and material exceptions in affected scope.

    Vendor-only readiness is not certification of every prospective PO line.
    Final-PO callers must evaluate each applicable item with both ``vendor_id``
    and ``variant_id`` (and ``run_id`` when relevant).
    """
    declared_gate_names = _validated_applicable_gate_names(applicable_gate_names)
    gates = _effective_readiness_gates(conn)
    blockers = readiness_gate_blockers(
        gates,
        vendor_id=vendor_id,
        variant_id=variant_id,
        run_id=run_id,
        applicable_gate_names=declared_gate_names,
    )

    with conn.cursor() as cur:
        cur.execute(
            """SELECT exception_id,severity,exception_type,message,variant_id,
                      vendor_id,run_id,status
               FROM exceptions
               WHERE status='OPEN' AND severity IN ('HIGH','CRITICAL')
               ORDER BY exception_id"""
        )
        exceptions = [
            {
                "exception_id": row[0],
                "severity": row[1],
                "exception_type": row[2],
                "message": row[3],
                "variant_id": str(row[4]) if row[4] is not None else None,
                "vendor_id": str(row[5]) if row[5] is not None else None,
                "run_id": str(row[6]) if row[6] is not None else None,
                "status": row[7],
            }
            for row in cur.fetchall()
        ]
    blockers.extend(
        exception_blockers(
            exceptions,
            vendor_id=vendor_id,
            variant_id=variant_id,
            run_id=run_id,
        )
    )

    return {
        "po_generation_enabled": not blockers,
        "scope": {
            "vendor_id": vendor_id,
            "variant_id": variant_id,
            "run_id": run_id,
        },
        "applicable_gate_names": sorted(
            FOUNDATION_APPLICABLE_GATES
            | declared_gate_names
        ),
        "blockers": blockers,
        "gates": gates,
    }
