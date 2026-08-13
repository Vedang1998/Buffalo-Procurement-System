from __future__ import annotations
from datetime import date

def validate_rollover_date(as_of: date, future_months: list[date], unverified_future_rows: int) -> date:
    if as_of.day != 1: raise ValueError("Price rollover is permitted only on the 1st of a month.")
    expected=as_of.replace(day=1); unique=sorted(set(future_months))
    if unique != [expected]: raise ValueError(f"Expected exactly FUTURE month {expected}; found {unique or 'none'}.")
    if unverified_future_rows: raise ValueError(f"Rollover blocked: {unverified_future_rows} FUTURE rows are unverified.")
    return expected

def rollover(conn, as_of: date) -> dict:
    """Fail closed until the authorized price-book phase builds every guard."""
    raise RuntimeError(
        "Operational price rollover is disabled until the canonical price-book "
        "phase implements completeness, backup, approved-transition, and assertion gates"
    )
