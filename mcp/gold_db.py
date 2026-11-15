import os
import re
import datetime as dt
import decimal

import duckdb

GOLD_DIR = os.environ.get("LOGISTREAM_GOLD", "D:/logistream/lake/gold")
TABLES = [
    "fact_orders",
    "fact_shipments",
    "fact_incidents",
    "agg_hub_daily",
    "agg_daily_overview",
    "meta",
]
MAX_ROWS = 200


def connect():
    """Open an in-memory DuckDB and create one view per gold table.
    Views only read the Delta files, so nothing here can change the lake."""
    con = duckdb.connect()  # in-memory
    con.execute("INSTALL delta; LOAD delta;")
    con.execute("SET TimeZone='UTC'")
    for t in TABLES:
        con.execute(
            f"CREATE VIEW {t} AS SELECT * FROM delta_scan('{GOLD_DIR}/{t}')"
        )
    return con


def _clean(v):
    """Make a value JSON-friendly."""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def get_as_of(con):
    """The 'now' of the data (latest event time in the lake), as an ISO string."""
    return _clean(con.execute("SELECT max(as_of) FROM meta").fetchone()[0])


_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|copy|install|load|"
    r"pragma|export|import|call|set)\b",
    re.I,
)


def run_query(con, sql, params=None, max_rows=MAX_ROWS):
    """Run one read-only SELECT/WITH query.
    Returns {"as_of", "columns", "rows", "row_count", "truncated"}."""
    s = sql.strip().rstrip(";")
    if ";" in s:
        raise ValueError("Only one statement is allowed.")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise ValueError("Only SELECT queries are allowed.")
    if _FORBIDDEN.search(s):
        raise ValueError("Query contains a forbidden keyword.")
    cur = con.execute(s, params or [])
    cols = [d[0] for d in cur.description]
    rows = cur.fetchmany(max_rows + 1)
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    return {
        "as_of": get_as_of(con),
        "columns": cols,
        "rows": [[_clean(v) for v in r] for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }


def describe(con):
    """Table and column names, handy to give to an LLM or MCP tool."""
    out = {}
    for t in TABLES:
        out[t] = [r[0] for r in con.execute(f"DESCRIBE {t}").fetchall()]
    return out


# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    con = connect()
    print("as_of:", get_as_of(con))

    tests = {
        "Orders by status": """
            SELECT order_status, count(*) AS n
            FROM fact_orders GROUP BY 1 ORDER BY n DESC""",
        "Currently delayed shipments": """
            SELECT count(*) AS currently_delayed
            FROM fact_shipments WHERE is_currently_delayed""",
        "On-time rate by destination hub (overall)": """
            SELECT destination_hub_name,
                   count(*) FILTER (WHERE is_delivered) AS delivered,
                   round(100.0 * count(*) FILTER (WHERE is_on_time)
                         / nullif(count(*) FILTER (WHERE is_delivered), 0), 1)
                         AS on_time_rate_pct
            FROM fact_shipments GROUP BY 1 ORDER BY on_time_rate_pct DESC""",
        "Daily overview": """
            SELECT date, orders_created, deliveries, on_time_rate_pct, incidents
            FROM agg_daily_overview ORDER BY date""",
        "Incidents by city": """
            SELECT city, count(*) AS incidents
            FROM fact_incidents GROUP BY 1 ORDER BY incidents DESC""",
    }
    for name, sql in tests.items():
        res = run_query(con, sql)
        print(f"\n== {name} ==")
        print(res["columns"])
        for r in res["rows"]:
            print(r)

    # safety check: this must be rejected
    try:
        run_query(con, "DROP VIEW fact_orders")
    except ValueError as e:
        print("\nBlocked as expected:", e)
