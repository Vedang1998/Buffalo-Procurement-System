from __future__ import annotations
from datetime import date

ROLLOVER_SQL = """
DELETE FROM prices WHERE price_state = 'current';
UPDATE prices SET price_state = 'current' WHERE price_state = 'future';
INSERT INTO meta(key,value,updated_at) VALUES ('last_price_rollover', %(month)s, now())
ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now();
"""

def validate_rollover_date(as_of: date, future_months: list[date], unverified_future_rows: int) -> date:
    if as_of.day != 1: raise ValueError("Price rollover is permitted only on the 1st of a month.")
    expected=as_of.replace(day=1); unique=sorted(set(future_months))
    if unique != [expected]: raise ValueError(f"Expected exactly FUTURE month {expected}; found {unique or 'none'}.")
    if unverified_future_rows: raise ValueError(f"Rollover blocked: {unverified_future_rows} FUTURE rows are unverified.")
    return expected

def rollover(conn, as_of: date) -> dict:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT effective_month FROM prices WHERE price_state='future'")
            months=[r[0] for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM prices WHERE price_state='future' AND verified=FALSE")
            month=validate_rollover_date(as_of,months,cur.fetchone()[0])
            cur.execute(ROLLOVER_SQL,{"month":month.isoformat()})
            cur.execute("SELECT COUNT(*) FROM prices WHERE price_state='future'")
            remaining=cur.fetchone()[0]
    return {"rolled_over":True,"new_current_month":month.isoformat(),"future_rows_remaining":remaining}
