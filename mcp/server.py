import os

from mcp.server.fastmcp import FastMCP

from gold_db import connect, describe, run_query

mcp = FastMCP("logistream")

# Set LOGISTREAM_SQL_TOOL=0 to hide the free-form SQL tool (useful to compare
# "controlled tools only" against "tools + SQL" in the evaluation).
ENABLE_SQL_TOOL = os.environ.get("LOGISTREAM_SQL_TOOL", "1") == "1"

_con = None


def con():
    global _con
    if _con is None:
        _con = connect()
    return _con


# "Today" and "now" come from the data (as_of), in Indian time, not the wall clock.
AS_OF = "(SELECT as_of FROM meta)"
TODAY = "(SELECT CAST(as_of AT TIME ZONE 'Asia/Kolkata' AS DATE) FROM meta)"


def _rows(sql, params=None, max_rows=200, note=None):
    res = run_query(con(), sql, params, max_rows)
    out = {
        "as_of": res["as_of"],
        "rows": [dict(zip(res["columns"], r)) for r in res["rows"]],
        "truncated": res["truncated"],
    }
    if note:
        out["note"] = note
    return out


# ------------------------------------------------------------------ orders
@mcp.tool()
def order_lookup(order_id: int) -> dict:
    """Current status of ONE order by its id (created, packed, in_transit,
    delivered or cancelled), with customer city, hubs and timestamps."""
    return _rows(
        """SELECT order_id, order_status, customer_city, origin_hub_name,
                  destination_hub_name, created_at, updated_at, shipment_id
           FROM fact_orders WHERE order_id = ?""",
        [order_id],
        note="Empty rows means no order with that id exists.",
    )


@mcp.tool()
def order_counts() -> dict:
    """Order totals: how many orders are in each status, the overall
    cancellation rate, orders cancelled today and orders created in the last
    hour. 'Today' and 'last hour' are counted from as_of (the newest data
    time), with days in Indian time."""
    return _rows(
        f"""SELECT count(*) AS total_orders,
                   count(*) FILTER (WHERE order_status = 'created')    AS created,
                   count(*) FILTER (WHERE order_status = 'packed')     AS packed,
                   count(*) FILTER (WHERE order_status = 'in_transit') AS in_transit,
                   count(*) FILTER (WHERE order_status = 'delivered')  AS delivered,
                   count(*) FILTER (WHERE order_status = 'cancelled')  AS cancelled,
                   round(100.0 * count(*) FILTER (WHERE is_cancelled) / count(*), 2)
                       AS cancellation_pct,
                   count(*) FILTER (WHERE is_cancelled AND cancelled_date = {TODAY})
                       AS cancelled_today,
                   count(*) FILTER (WHERE created_at > {AS_OF} - INTERVAL 1 HOUR
                                      AND created_at <= {AS_OF}) AS created_last_hour
            FROM fact_orders"""
    )


@mcp.tool()
def cancellation_by_city() -> dict:
    """Cancellation rate per city, highest first. The city is the CUSTOMER's
    city (not the delivery city)."""
    return _rows(
        """SELECT customer_city AS city, count(*) AS orders,
                  count(*) FILTER (WHERE is_cancelled) AS cancelled,
                  round(100.0 * count(*) FILTER (WHERE is_cancelled) / count(*), 2)
                      AS cancellation_pct
           FROM fact_orders GROUP BY 1
           ORDER BY cancellation_pct DESC, city"""
    )


# --------------------------------------------------------------- shipments
@mcp.tool()
def shipment_status(shipment_id: int) -> dict:
    """Status of ONE shipment by id: status, hubs, vehicle, planned and actual
    arrival, and how late it is. There is NO live GPS position in the data, so
    the exact location of a shipment cannot be given."""
    return _rows(
        """SELECT shipment_id, order_id, shipment_status, plate_no,
                  origin_hub_name, destination_hub_name, planned_arrival,
                  actual_arrival, is_delivered, is_late, is_currently_delayed,
                  current_delay_minutes
           FROM fact_shipments WHERE shipment_id = ?""",
        [shipment_id],
        note="No live location exists. Empty rows means no shipment with that id.",
    )


@mcp.tool()
def currently_delayed_shipments() -> dict:
    """All shipments that are NOT delivered yet and whose planned arrival time
    has already passed (as of as_of), most delayed first."""
    return _rows(
        """SELECT shipment_id, order_id, plate_no, origin_hub_name,
                  destination_hub_name, planned_arrival, current_delay_minutes
           FROM fact_shipments WHERE is_currently_delayed
           ORDER BY current_delay_minutes DESC, shipment_id"""
    )


@mcp.tool()
def delivery_summary() -> dict:
    """Overall delivery numbers (one row): delivered count, late count, late
    percentage, on-time percentage, average delivery time (order created to
    arrival, in minutes), average delay of late shipments, and shipments
    delivered on time today. Late = delivered after planned arrival.
    Undelivered shipments are not counted."""
    return _rows(
        f"""SELECT count(*) FILTER (WHERE is_delivered) AS delivered,
                   count(*) FILTER (WHERE is_late) AS late_deliveries,
                   round(100.0 * count(*) FILTER (WHERE is_late)
                         / nullif(count(*) FILTER (WHERE is_delivered), 0), 2) AS late_pct,
                   round(100.0 * count(*) FILTER (WHERE is_on_time)
                         / nullif(count(*) FILTER (WHERE is_delivered), 0), 2) AS on_time_pct,
                   round(avg(delivery_minutes) FILTER (WHERE is_delivered), 2)
                         AS avg_delivery_minutes,
                   round(avg(extract(epoch FROM (actual_arrival - planned_arrival)) / 60)
                         FILTER (WHERE is_late), 2) AS avg_delay_minutes_late,
                   count(*) FILTER (WHERE is_on_time AND arrival_date = {TODAY})
                         AS on_time_today
            FROM fact_shipments"""
    )


# -------------------------------------------------------------------- hubs
@mcp.tool()
def hub_performance(hub_names: list[str] | None = None) -> dict:
    """Performance of each hub (or only the named hubs, e.g. to compare two).
    Columns: deliveries and late deliveries and on-time % (attributed to the
    DESTINATION hub), average delay of late deliveries, average hub processing
    minutes (ORIGIN hub: order created to dispatch), incidents, and shipments
    passed through (as origin or destination). Time spent at intermediate hubs
    is NOT available."""
    where, params = "", []
    if hub_names:
        where = "WHERE lower(dest.hub) IN (" + ",".join("?" * len(hub_names)) + ")"
        params = [h.lower() for h in hub_names]
    return _rows(
        f"""WITH dest AS (
                SELECT destination_hub_name AS hub,
                       count(*) FILTER (WHERE is_delivered) AS deliveries,
                       count(*) FILTER (WHERE is_late) AS late_deliveries,
                       round(100.0 * count(*) FILTER (WHERE is_on_time)
                             / nullif(count(*) FILTER (WHERE is_delivered), 0), 2)
                             AS on_time_pct,
                       round(avg(extract(epoch FROM (actual_arrival - planned_arrival)) / 60)
                             FILTER (WHERE is_late), 2) AS avg_delay_minutes_late
                FROM fact_shipments GROUP BY 1),
            orig AS (
                SELECT origin_hub_name AS hub,
                       round(avg(order_to_dispatch_minutes), 2) AS avg_hub_processing_minutes
                FROM fact_shipments GROUP BY 1),
            inc AS (
                SELECT hub_name AS hub, count(*) AS incidents
                FROM fact_incidents GROUP BY 1),
            thru AS (
                SELECT hub, count(*) AS shipments_passed FROM (
                    SELECT shipment_id, origin_hub_name AS hub FROM fact_shipments
                    UNION
                    SELECT shipment_id, destination_hub_name FROM fact_shipments)
                GROUP BY 1)
            SELECT dest.hub, dest.deliveries, dest.late_deliveries, dest.on_time_pct,
                   dest.avg_delay_minutes_late, orig.avg_hub_processing_minutes,
                   coalesce(inc.incidents, 0) AS incidents, thru.shipments_passed
            FROM dest
            LEFT JOIN orig ON orig.hub = dest.hub
            LEFT JOIN inc  ON inc.hub  = dest.hub
            LEFT JOIN thru ON thru.hub = dest.hub
            {where}
            ORDER BY dest.late_deliveries DESC, dest.hub""",
        params,
    )


# ------------------------------------------------------------------ cities
@mcp.tool()
def city_performance() -> dict:
    """Delivery performance per city. The city is the DESTINATION hub's city.
    Columns: deliveries, on-time %, late %, average delivery minutes."""
    return _rows(
        """SELECT destination_hub_city AS city,
                  count(*) FILTER (WHERE is_delivered) AS deliveries,
                  round(100.0 * count(*) FILTER (WHERE is_on_time)
                        / nullif(count(*) FILTER (WHERE is_delivered), 0), 2) AS on_time_pct,
                  round(100.0 * count(*) FILTER (WHERE is_late)
                        / nullif(count(*) FILTER (WHERE is_delivered), 0), 2) AS late_pct,
                  round(avg(delivery_minutes) FILTER (WHERE is_delivered), 2)
                        AS avg_delivery_minutes
           FROM fact_shipments GROUP BY 1 ORDER BY city"""
    )


# ------------------------------------------------------- trends (per day)
@mcp.tool()
def daily_trends() -> dict:
    """One row per day (Indian time): orders created, cancellation rate,
    deliveries, late deliveries, late %, on-time %, average delivery minutes
    and incidents. Use it for 'today vs yesterday' or day-by-day trends. The
    newest day is usually a PARTIAL day (it ends at as_of)."""
    return _rows(
        """SELECT date, orders_created, cancellation_rate_pct, deliveries,
                  late_deliveries, on_time_deliveries, on_time_rate_pct,
                  round(100.0 * late_deliveries / nullif(deliveries, 0), 2) AS late_pct,
                  avg_delivery_minutes, avg_delay_minutes_late, incidents
           FROM agg_daily_overview ORDER BY date"""
    )


# ---------------------------------------------------- vehicles & incidents
@mcp.tool()
def vehicle_ranking(metric: str = "incidents", top_n: int = 10) -> dict:
    """Top vehicles by plate number. metric is one of: 'incidents' (reported
    incidents), 'shipments' (shipments assigned) or 'late_shipments'
    (shipments delivered late)."""
    sources = {
        "incidents": ("fact_incidents", "", "incidents"),
        "shipments": ("fact_shipments", "", "shipments"),
        "late_shipments": ("fact_shipments", "WHERE is_late", "late_shipments"),
    }
    if metric not in sources:
        raise ValueError("metric must be incidents, shipments or late_shipments")
    table, where, name = sources[metric]
    top_n = max(1, min(int(top_n), 50))
    return _rows(
        f"""SELECT plate_no, count(*) AS {name} FROM {table} {where}
            GROUP BY plate_no ORDER BY {name} DESC, plate_no LIMIT {top_n}"""
    )


@mcp.tool()
def incidents_by_hub() -> dict:
    """Number of reported incidents per hub, highest first."""
    return _rows(
        """SELECT hub_name AS hub, city, count(*) AS incidents
           FROM fact_incidents GROUP BY 1, 2
           ORDER BY incidents DESC, hub"""
    )


@mcp.tool()
def incident_descriptions(hub_name: str | None = None, limit: int = 50) -> dict:
    """The free-text description of reported incidents (newest first),
    optionally for one hub. Use it to see what kinds of incidents happen."""
    limit = max(1, min(int(limit), 200))
    if hub_name:
        return _rows(
            f"""SELECT hub_name AS hub, plate_no, reported_at, description
                FROM fact_incidents WHERE lower(hub_name) = lower(?)
                ORDER BY reported_at DESC LIMIT {limit}""",
            [hub_name],
        )
    return _rows(
        f"""SELECT hub_name AS hub, plate_no, reported_at, description
            FROM fact_incidents ORDER BY reported_at DESC LIMIT {limit}"""
    )


# ------------------------------------------------------- schema + free SQL
@mcp.tool()
def describe_schema() -> dict:
    """Tables and column names of the gold layer."""
    return {"tables": describe(con())}


if ENABLE_SQL_TOOL:

    @mcp.tool()
    def run_sql(sql: str) -> dict:
        """Run ONE read-only SELECT (or WITH) query on the gold tables:
        fact_orders, fact_shipments, fact_incidents, agg_hub_daily,
        agg_daily_overview, meta. Call describe_schema first. Max 200 rows.
        Prefer the specific tools when one fits."""
        return _rows(sql)


if __name__ == "__main__":
    mcp.run()  # stdio