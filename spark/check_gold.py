import os
import sys
 
import duckdb
import psycopg2
 
LAKE = os.environ.get("LAKE", "D:/logistream/lake").replace("\\", "/")
PG = dict(
    host=os.environ.get("PGHOST", "localhost"),
    port=int(os.environ.get("PGPORT", "5432")),
    dbname=os.environ.get("PGDATABASE", "logistics"),
    user=os.environ.get("PGUSER", "logistics"),
    password=os.environ.get("PGPASSWORD", "logistics"),
)
IST = "Asia/Kolkata"
 
# connections 
con = duckdb.connect()
con.execute("INSTALL delta; LOAD delta;")
con.execute("SET TimeZone='UTC'")
for t in ["fact_orders", "fact_shipments", "fact_incidents",
          "agg_hub_daily", "agg_daily_overview", "meta"]:
    con.execute(f"CREATE VIEW {t} AS SELECT * FROM delta_scan('{LAKE}/gold/{t}')")
 
pg = psycopg2.connect(**PG)
cur = pg.cursor()
cur.execute("SET TIME ZONE 'UTC'")
 
 
def pg_rows(sql, *params):
    cur.execute(sql, params or None)
    return cur.fetchall()
 
 
def gold_rows(sql):
    return con.execute(sql).fetchall()
 
 
def as_dict(rows):
    return {r[0]: (r[1] if len(r) == 2 else tuple(r[1:])) for r in rows}
 
 
def as_row(rows):
    return tuple(rows[0])
 
 
# comparison 
def same(a, b, tol):
    if isinstance(a, tuple):
        return isinstance(b, tuple) and len(a) == len(b) and all(same(x, y, tol) for x, y in zip(a, b))
    if a is None or b is None:
        return a == b
    return abs(float(a) - float(b)) <= tol
 
 
results = []
 
 
def check(name, pg_val, gold_val, tol=0.0):
    if isinstance(pg_val, dict):
        keys = sorted(set(pg_val) | set(gold_val), key=str)
        bad = [(k, pg_val.get(k), gold_val.get(k)) for k in keys
               if not same(pg_val.get(k), gold_val.get(k), tol)]
    else:
        bad = [] if same(pg_val, gold_val, tol) else [("value", pg_val, gold_val)]
    ok = not bad
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    for k, p, g in bad[:5]:
        print(f"         key={k}   postgres={p}   gold={g}")
    if len(bad) > 5:
        print(f"         ... and {len(bad) - 5} more differences")
 
 
d_ist = lambda col: f"({col} AT TIME ZONE '{IST}')::date"
 
# orders 
check("orders: total",
      pg_rows("SELECT count(*) FROM orders")[0][0],
      gold_rows("SELECT count(*) FROM fact_orders")[0][0])
 
check("orders: count by status (incl. currently in transit)",
      as_dict(pg_rows("SELECT status, count(*) FROM orders GROUP BY 1")),
      as_dict(gold_rows("SELECT order_status, count(*) FROM fact_orders GROUP BY 1")))
 
check("orders: (cancelled, total) by customer city -> cancellation rate",
      as_dict(pg_rows("""SELECT c.city, count(*) FILTER (WHERE o.status='cancelled'), count(*)
                         FROM orders o JOIN customers c USING (customer_id) GROUP BY 1""")),
      as_dict(gold_rows("""SELECT customer_city, sum(CAST(is_cancelled AS INT)), count(*)
                           FROM fact_orders GROUP BY 1""")))
 
latest_ids = [r[0] for r in pg_rows("SELECT order_id FROM orders ORDER BY order_id DESC LIMIT 5")]
ids = ",".join(str(i) for i in latest_ids)
check("orders: status of the 5 newest orders (single-order lookup)",
      as_dict(pg_rows(f"SELECT order_id, status FROM orders WHERE order_id IN ({ids})")),
      as_dict(gold_rows(f"SELECT order_id, order_status FROM fact_orders WHERE order_id IN ({ids})")))
 
# shipments 
check("shipments: (total, delivered, late)",
      as_row(pg_rows("""SELECT count(*), count(actual_arrival),
                               count(*) FILTER (WHERE actual_arrival > planned_arrival)
                        FROM shipments""")),
      as_row(gold_rows("""SELECT count(*), sum(CAST(is_delivered AS INT)), sum(CAST(is_late AS INT))
                          FROM fact_shipments""")))
 
check("shipments: average end-to-end delivery minutes (order created -> arrival)",
      pg_rows("""SELECT avg(extract(epoch FROM (s.actual_arrival - o.created_at)) / 60)
                 FROM shipments s JOIN orders o USING (order_id)
                 WHERE s.actual_arrival IS NOT NULL""")[0][0],
      gold_rows("SELECT avg(delivery_minutes) FROM fact_shipments WHERE is_delivered")[0][0],
      tol=0.05)
 
check("shipments: average delay minutes of LATE shipments",
      pg_rows("""SELECT avg(extract(epoch FROM (actual_arrival - planned_arrival)) / 60)
                 FROM shipments WHERE actual_arrival > planned_arrival""")[0][0],
      gold_rows("SELECT avg(delay_minutes) FROM fact_shipments WHERE is_late")[0][0],
      tol=0.05)
 
as_of = gold_rows("SELECT CAST(as_of AS VARCHAR) FROM meta")[0][0]
print(f"         (as_of used for 'currently delayed' = {as_of})")
check("shipments: currently delayed (not delivered, planned arrival before as_of)",
      pg_rows("""SELECT count(*) FROM shipments
                 WHERE actual_arrival IS NULL AND planned_arrival < %s::timestamptz""", as_of)[0][0],
      gold_rows("SELECT count(*) FROM fact_shipments WHERE is_currently_delayed")[0][0])
 
check("vehicles: shipments per vehicle",
      as_dict(pg_rows("SELECT vehicle_id, count(*) FROM shipments GROUP BY 1")),
      as_dict(gold_rows("SELECT vehicle_id, count(*) FROM fact_shipments GROUP BY 1")))
 
check("vehicles: late shipments per vehicle",
      as_dict(pg_rows("""SELECT vehicle_id, count(*) FROM shipments
                         WHERE actual_arrival > planned_arrival GROUP BY 1""")),
      as_dict(gold_rows("""SELECT vehicle_id, count(*) FROM fact_shipments
                           WHERE is_late GROUP BY 1""")))
 
# hubs 
check("hubs: late deliveries per destination hub (agg_hub_daily vs raw SQL)",
      as_dict(pg_rows("""SELECT o.destination_hub_id,
                                count(*) FILTER (WHERE s.actual_arrival > s.planned_arrival)
                         FROM shipments s JOIN orders o USING (order_id)
                         WHERE s.actual_arrival IS NOT NULL GROUP BY 1""")),
      as_dict(gold_rows("""SELECT hub_id, sum(late_deliveries) FROM agg_hub_daily
                           GROUP BY 1 HAVING sum(deliveries) > 0""")))
 
check("hubs: total shipments dispatched (departed events, one per shipment)",
      pg_rows("SELECT count(DISTINCT shipment_id) FROM shipment_events WHERE event_type='departed'")[0][0],
      gold_rows("SELECT sum(shipments_dispatched) FROM agg_hub_daily")[0][0])
 
check("hubs: shipments touched (distinct hub, day, shipment; scanned events ignored)",
      pg_rows(f"""SELECT count(*) FROM (
                    SELECT DISTINCT hub_id, {d_ist('event_time')}, shipment_id
                    FROM shipment_events WHERE hub_id IS NOT NULL AND event_type <> 'scanned') t""")[0][0],
      gold_rows("SELECT sum(shipments_touched) FROM agg_hub_daily")[0][0])
 
check("hubs: avg origin hub processing minutes (order created -> departed, all shipments)",
      pg_rows("""SELECT avg(extract(epoch FROM (t.dep - t.created)) / 60) FROM (
                   SELECT s.shipment_id, min(e.event_time) AS dep, o.created_at AS created
                   FROM shipments s
                   JOIN orders o USING (order_id)
                   JOIN shipment_events e ON e.shipment_id = s.shipment_id AND e.event_type = 'departed'
                   GROUP BY s.shipment_id, o.created_at) t""")[0][0],
      gold_rows("""SELECT sum(avg_hub_processing_minutes * shipments_dispatched) / sum(shipments_dispatched)
                   FROM agg_hub_daily WHERE shipments_dispatched > 0""")[0][0],
      tol=0.05)
 
# incidents 
check("incidents: per vehicle",
      as_dict(pg_rows("SELECT vehicle_id, count(*) FROM incident_reports GROUP BY 1")),
      as_dict(gold_rows("SELECT vehicle_id, count(*) FROM fact_incidents GROUP BY 1")))
 
check("incidents: per hub (fact_incidents)",
      as_dict(pg_rows("SELECT hub_id, count(*) FROM incident_reports GROUP BY 1")),
      as_dict(gold_rows("SELECT hub_id, count(*) FROM fact_incidents GROUP BY 1")))
 
check("incidents: per hub (agg_hub_daily)",
      as_dict(pg_rows("SELECT hub_id, count(*) FROM incident_reports WHERE hub_id IS NOT NULL GROUP BY 1")),
      as_dict(gold_rows("SELECT hub_id, sum(incident_count) FROM agg_hub_daily GROUP BY 1 HAVING sum(incident_count) > 0")))
 
# ---------- daily overview (trend questions) ----------
check("daily: (orders created, of which cancelled) per day",
      as_dict(pg_rows(f"""SELECT {d_ist('created_at')}, count(*), count(*) FILTER (WHERE status='cancelled')
                          FROM orders GROUP BY 1""")),
      as_dict(gold_rows("""SELECT date, orders_created, orders_created_cancelled
                           FROM agg_daily_overview WHERE orders_created > 0""")))
 
check("daily: orders cancelled on each day (by updated_at)",
      as_dict(pg_rows(f"""SELECT {d_ist('updated_at')}, count(*) FROM orders
                          WHERE status='cancelled' GROUP BY 1""")),
      as_dict(gold_rows("""SELECT date, orders_cancelled_on_day FROM agg_daily_overview
                           WHERE orders_cancelled_on_day > 0""")))
 
check("daily: (deliveries, late) per arrival day",
      as_dict(pg_rows(f"""SELECT {d_ist('actual_arrival')}, count(*),
                                 count(*) FILTER (WHERE actual_arrival > planned_arrival)
                          FROM shipments WHERE actual_arrival IS NOT NULL GROUP BY 1""")),
      as_dict(gold_rows("""SELECT date, deliveries, late_deliveries FROM agg_daily_overview
                           WHERE deliveries > 0""")))
 
check("daily: incidents per day",
      as_dict(pg_rows(f"SELECT {d_ist('reported_at')}, count(*) FROM incident_reports GROUP BY 1")),
      as_dict(gold_rows("SELECT date, incidents FROM agg_daily_overview WHERE incidents > 0")))
 
# summary 
passed = sum(results)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
 