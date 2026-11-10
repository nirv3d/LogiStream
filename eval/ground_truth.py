"""
ground_truth.py - the ANSWER KEY for the LogiStream AI agent evaluation.

Every question the agent is tested on lives here, with one SQL query that gets
the correct answer straight from PostgreSQL (the source database, not the lake).
Running this file writes ground_truth.json, which is later used to score the four
setups (LLM alone, RAG only, MCP only, RAG + MCP).
"""
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal

import psycopg2

PG = dict(
    host=os.environ.get("PGHOST", "localhost"),
    port=int(os.environ.get("PGPORT", "5432")),
    dbname=os.environ.get("PGDATABASE", "logistics"),
    user=os.environ.get("PGUSER", "logistics"),
    password=os.environ.get("PGPASSWORD", "logistics"),
)

# ---------- small SQL building blocks ----------
IST = "'Asia/Kolkata'"
TODAY = f"(%(as_of)s::timestamptz AT TIME ZONE {IST})::date"   # day of as_of, Indian time


def ist(col):
    """Calendar date of a timestamp column, in Indian time."""
    return f"({col} AT TIME ZONE {IST})::date"


# a delivered shipment s, joined to its order o and destination hub h
DELIVERED_BY_DEST_HUB = """
    FROM shipments s
    JOIN orders o USING (order_id)
    JOIN hubs h ON h.hub_id = o.destination_hub_id
    WHERE s.actual_arrival IS NOT NULL"""

LATE_PCT = ("round(100.0 * count(*) FILTER (WHERE s.actual_arrival > s.planned_arrival) "
            "/ count(*), 2)")
ON_TIME_PCT = ("round(100.0 * count(*) FILTER (WHERE s.actual_arrival <= s.planned_arrival) "
               "/ count(*), 2)")
AVG_DELIVERY_MIN = ("round((avg(extract(epoch FROM (s.actual_arrival - o.created_at)) / 60))"
                    "::numeric, 2)")

QUESTIONS = []


def q(qid, category, question, kind, needs, sql=None, text=None, note=""):
    """kind: numbers | text | definition | unanswerable
       needs: which setup should be able to answer it: mcp | rag | both | none"""
    QUESTIONS.append(dict(id=qid, category=category, question=question, kind=kind,
                          needs=needs, sql=sql, answer_text=text, note=note))


# ---------- delivery performance ----------
q("D1", "delivery", "What is the average delivery time?", "numbers", "mcp",
  sql=f"""SELECT {AVG_DELIVERY_MIN} AS avg_delivery_minutes
          FROM shipments s JOIN orders o USING (order_id)
          WHERE s.actual_arrival IS NOT NULL""",
  note="End-to-end: order created to actual arrival, in minutes.")

q("D2", "delivery", "What percentage of shipments were delivered late?", "numbers", "mcp",
  sql="""SELECT round(100.0 * count(*) FILTER (WHERE actual_arrival > planned_arrival) / count(*), 2) AS late_pct,
                count(*) FILTER (WHERE actual_arrival > planned_arrival) AS late_shipments,
                count(*) AS delivered_shipments
         FROM shipments WHERE actual_arrival IS NOT NULL""",
  note="Percentage of DELIVERED shipments. Undelivered ones are not counted.")

q("D3", "delivery", "Show all currently delayed shipments.", "numbers", "mcp",
  sql="""SELECT shipment_id, order_id, vehicle_id,
                round(extract(epoch FROM (%(as_of)s::timestamptz - planned_arrival)) / 60)::int AS delayed_minutes
         FROM shipments
         WHERE actual_arrival IS NULL AND planned_arrival < %(as_of)s::timestamptz
         ORDER BY delayed_minutes DESC, shipment_id""",
  note="Score on the SET of shipment ids. Can be empty if nothing is delayed at as_of.")

q("D4", "delivery", "How many shipments were delivered on time today?", "numbers", "mcp",
  sql=f"""SELECT count(*) AS on_time_today FROM shipments
          WHERE actual_arrival IS NOT NULL AND actual_arrival <= planned_arrival
            AND {ist('actual_arrival')} = {TODAY}""",
  note="'Today' = the Indian-time day of as_of.")

q("D5", "delivery", "What is the average delay in minutes for late shipments?", "numbers", "mcp",
  sql="""SELECT round((avg(extract(epoch FROM (actual_arrival - planned_arrival)) / 60))::numeric, 2)
                AS avg_delay_minutes
         FROM shipments WHERE actual_arrival > planned_arrival""")

q("D6", "delivery", "What is the on-time rate for each destination city?", "numbers", "mcp",
  sql=f"""SELECT h.city, count(*) AS deliveries, {ON_TIME_PCT} AS on_time_pct
          {DELIVERED_BY_DEST_HUB}
          GROUP BY h.city ORDER BY on_time_pct DESC, h.city""")

# ---------- hubs ----------
q("H1", "hub", "Which hub has the highest number of late shipments?", "numbers", "mcp",
  sql="""SELECT h.name AS hub, count(*) AS late_deliveries
         FROM shipments s JOIN orders o USING (order_id)
         JOIN hubs h ON h.hub_id = o.destination_hub_id
         WHERE s.actual_arrival > s.planned_arrival
         GROUP BY h.name ORDER BY late_deliveries DESC, h.name""",
  note="Late deliveries are attributed to the DESTINATION hub. First row is the answer.")

q("H2", "hub", "Compare the performance of Tagore Garden Depot and Noida Sector 62 Depot.",
  "numbers", "mcp",
  sql=f"""SELECT h.name AS hub, count(*) AS deliveries,
                 count(*) FILTER (WHERE s.actual_arrival > s.planned_arrival) AS late_deliveries,
                 {ON_TIME_PCT} AS on_time_pct,
                 round((avg(extract(epoch FROM (s.actual_arrival - s.planned_arrival)) / 60)
                        FILTER (WHERE s.actual_arrival > s.planned_arrival))::numeric, 2) AS avg_delay_minutes_late
          {DELIVERED_BY_DEST_HUB}
            AND h.name IN ('Tagore Garden Depot', 'Noida Sector 62 Depot')
          GROUP BY h.name ORDER BY h.name""")

q("H3", "hub", "How many shipments passed through each hub?", "numbers", "mcp",
  sql="""SELECT h.name AS hub, count(*) AS shipments
         FROM shipments s JOIN orders o USING (order_id)
         JOIN hubs h ON h.hub_id IN (o.origin_hub_id, o.destination_hub_id)
         GROUP BY h.name ORDER BY shipments DESC, h.name""",
  note="A shipment passes through its origin hub and its destination hub (no other hubs).")

q("H4", "hub", "Which hub has the highest incident count?", "numbers", "mcp",
  sql="""SELECT h.name AS hub, count(*) AS incidents
         FROM incident_reports i JOIN hubs h USING (hub_id)
         GROUP BY h.name ORDER BY incidents DESC, h.name""")

q("H5", "hub", "Which hub has the longest average processing time?", "numbers", "mcp",
  sql="""SELECT h.name AS hub,
                round((avg(extract(epoch FROM (d.dep - o.created_at)) / 60))::numeric, 2) AS avg_processing_minutes,
                count(*) AS shipments
         FROM shipments s JOIN orders o USING (order_id)
         JOIN (SELECT shipment_id, min(event_time) AS dep FROM shipment_events
               WHERE event_type = 'departed' GROUP BY shipment_id) d USING (shipment_id)
         JOIN hubs h ON h.hub_id = o.origin_hub_id
         GROUP BY h.name ORDER BY avg_processing_minutes DESC, h.name""",
  note="Origin hub processing time: order created to departed. A good answer says what it measures.")

q("H6", "hub", "Which hub has the best on-time delivery rate?", "numbers", "mcp",
  sql=f"""SELECT h.name AS hub, count(*) AS deliveries, {ON_TIME_PCT} AS on_time_pct
          {DELIVERED_BY_DEST_HUB}
          GROUP BY h.name ORDER BY on_time_pct DESC, h.name""")

# ---------- orders ----------
q("O1", "orders", "How many orders were cancelled today?", "numbers", "mcp",
  sql=f"""SELECT count(*) AS cancelled_today FROM orders
          WHERE status = 'cancelled' AND {ist('updated_at')} = {TODAY}""")

q("O2", "orders", "What is the overall cancellation rate?", "numbers", "mcp",
  sql="""SELECT count(*) FILTER (WHERE status = 'cancelled') AS cancelled, count(*) AS total_orders,
                round(100.0 * count(*) FILTER (WHERE status = 'cancelled') / count(*), 2) AS cancellation_pct
         FROM orders""")

q("O3", "orders", "How many orders are still being packed?", "numbers", "mcp",
  sql="SELECT count(*) AS packed_orders FROM orders WHERE status = 'packed'")

q("O4", "orders", "How many orders were created in the last hour?", "numbers", "mcp",
  sql="""SELECT count(*) AS created_last_hour FROM orders
         WHERE created_at > %(as_of)s::timestamptz - interval '1 hour'
           AND created_at <= %(as_of)s::timestamptz""",
  note="'Last hour' is counted back from as_of.")

q("O5", "orders", "How many orders are currently in transit?", "numbers", "mcp",
  sql="SELECT count(*) AS in_transit_orders FROM orders WHERE status = 'in_transit'")

q("O6", "orders", "What is the current status of order 100?", "numbers", "mcp",
  sql="SELECT order_id, status FROM orders WHERE order_id = 100",
  note="Needs at least 100 orders in the evaluation run.")

q("O7", "orders", "Which city has the highest cancellation rate?", "numbers", "mcp",
  sql="""SELECT c.city, count(*) AS orders,
                count(*) FILTER (WHERE o.status = 'cancelled') AS cancelled,
                round(100.0 * count(*) FILTER (WHERE o.status = 'cancelled') / count(*), 2) AS cancellation_pct
         FROM orders o JOIN customers c USING (customer_id)
         GROUP BY c.city ORDER BY cancellation_pct DESC, c.city""",
  note="City = the CUSTOMER's city.")

# ---------- cities ----------
q("C1", "city", "Compare delivery performance across cities.", "numbers", "mcp",
  sql=f"""SELECT h.city, count(*) AS deliveries,
                 {ON_TIME_PCT} AS on_time_pct, {LATE_PCT} AS late_pct,
                 {AVG_DELIVERY_MIN} AS avg_delivery_minutes
          {DELIVERED_BY_DEST_HUB}
          GROUP BY h.city ORDER BY h.city""",
  note="City = the DESTINATION hub's city.")

q("C2", "city", "Which city has the highest average delivery time?", "numbers", "mcp",
  sql=f"""SELECT h.city, {AVG_DELIVERY_MIN} AS avg_delivery_minutes
          {DELIVERED_BY_DEST_HUB}
          GROUP BY h.city ORDER BY avg_delivery_minutes DESC, h.city""")

# ---------- vehicles and incidents ----------
q("V1", "vehicle", "Which vehicle has the most reported incidents?", "numbers", "mcp",
  sql="""SELECT v.plate_no, count(*) AS incidents
         FROM incident_reports i JOIN vehicles v USING (vehicle_id)
         GROUP BY v.plate_no ORDER BY incidents DESC, v.plate_no LIMIT 10""")

q("V2", "vehicle", "Which vehicles were assigned the most shipments?", "numbers", "mcp",
  sql="""SELECT v.plate_no, count(*) AS shipments
         FROM shipments s JOIN vehicles v USING (vehicle_id)
         GROUP BY v.plate_no ORDER BY shipments DESC, v.plate_no LIMIT 10""")

q("V3", "vehicle", "Which vehicles have the most delayed shipments?", "numbers", "mcp",
  sql="""SELECT v.plate_no, count(*) AS late_shipments
         FROM shipments s JOIN vehicles v USING (vehicle_id)
         WHERE s.actual_arrival > s.planned_arrival
         GROUP BY v.plate_no ORDER BY late_shipments DESC, v.plate_no LIMIT 10""",
  note="'Delayed' here means delivered late.")

q("V4", "incident", "What are the most common types of incident?", "text", "both",
  sql="""SELECT CASE
                  WHEN position('broke down' IN description) > 0 THEN 'breakdown'
                  WHEN position('Engine failure' IN description) > 0 THEN 'engine failure'
                  WHEN position('flat tyre' IN description) > 0 THEN 'flat tyre'
                  WHEN position('Waterlogging' IN description) > 0 THEN 'waterlogging'
                  WHEN position('Road closed' IN description) > 0 THEN 'road closed'
                  ELSE 'other' END AS incident_type,
                count(*) AS incidents
         FROM incident_reports GROUP BY 1 ORDER BY incidents DESC, incident_type""",
  note="Descriptions are free text, so types are grouped by their wording.")

# ---------- trends ----------
q("T1", "trend", "Has the late-delivery rate increased today compared with yesterday?",
  "numbers", "mcp",
  sql=f"""SELECT {ist('s.actual_arrival')} AS day, count(*) AS deliveries,
                 count(*) FILTER (WHERE s.actual_arrival > s.planned_arrival) AS late_deliveries,
                 {LATE_PCT} AS late_pct
          FROM shipments s
          WHERE s.actual_arrival IS NOT NULL
            AND {ist('s.actual_arrival')} IN ({TODAY}, {TODAY} - 1)
          GROUP BY 1 ORDER BY 1""",
  note="Answer is yes/no plus both rates. Today may be a partial day.")

q("T2", "trend", "How has the late-delivery rate changed day by day?", "numbers", "mcp",
  sql=f"""SELECT {ist('s.actual_arrival')} AS day, count(*) AS deliveries,
                 {LATE_PCT} AS late_pct
          FROM shipments s WHERE s.actual_arrival IS NOT NULL
          GROUP BY 1 ORDER BY 1""",
  note="Multi-day trend. Works best on a run that covers several days.")

q("T3", "trend", "Are deliveries getting faster or slower: today vs yesterday?", "numbers", "mcp",
  sql=f"""SELECT {ist('s.actual_arrival')} AS day, count(*) AS deliveries,
                 {AVG_DELIVERY_MIN} AS avg_delivery_minutes
          FROM shipments s JOIN orders o USING (order_id)
          WHERE s.actual_arrival IS NOT NULL
            AND {ist('s.actual_arrival')} IN ({TODAY}, {TODAY} - 1)
          GROUP BY 1 ORDER BY 1""")

# ---------- mixed (numbers + text) ----------
q("M1", "mixed", "Which hub has the most incidents, and what do those incident descriptions say?",
  "text", "both",
  sql="""SELECT h.name AS hub, i.reported_at, i.description
         FROM incident_reports i JOIN hubs h USING (hub_id)
         WHERE i.hub_id = (SELECT hub_id FROM incident_reports
                           GROUP BY hub_id ORDER BY count(*) DESC, hub_id LIMIT 1)
         ORDER BY i.reported_at""",
  note="Hub and count come from numbers; the description text is the other half of the answer.")

q("M2", "mixed", "Why is one city performing worse than another?", "text", "both",
  sql="""WITH d AS (
           SELECT h.city, count(*) AS deliveries,
                  round(100.0 * count(*) FILTER (WHERE s.actual_arrival > s.planned_arrival) / count(*), 2) AS late_pct
           FROM shipments s JOIN orders o USING (order_id)
           JOIN hubs h ON h.hub_id = o.destination_hub_id
           WHERE s.actual_arrival IS NOT NULL GROUP BY h.city),
         i AS (
           SELECT h.city, count(*) AS incidents
           FROM incident_reports r JOIN hubs h USING (hub_id) GROUP BY h.city)
         SELECT d.city, d.deliveries, d.late_pct, coalesce(i.incidents, 0) AS incidents
         FROM d LEFT JOIN i USING (city) ORDER BY d.late_pct DESC, d.city""",
  note="Score on evidence: gives late rate and incident numbers per city and does NOT claim a cause the data cannot prove.")

# ---------- definitions (RAG) ----------
q("R1", "definition", "What counts as a late shipment?", "definition", "rag",
  text="A shipment is late if it has been delivered and its actual arrival is after its planned arrival. "
       "A shipment that has not arrived yet is not counted as late; if its planned arrival has passed it is "
       "'currently delayed' instead.")

q("R2", "definition", "How is on-time delivery calculated?", "definition", "rag",
  text="On-time rate = on-time deliveries divided by delivered shipments, times 100. A shipment is on time if it "
       "was delivered at or before its planned arrival. Shipments that have not arrived are never on time and "
       "are not in the denominator.")

q("R3", "definition", "What does is_deleted mean?", "definition", "rag",
  text="A silver-layer flag set when the source row was deleted in PostgreSQL. Deleted rows are kept in silver "
       "but left out of gold.")

# ---------- not answerable: the right answer is to say so ----------
q("U1", "unanswerable", "How long do shipments spend at intermediate hubs?", "unanswerable", "none",
  text="Cannot be answered. Shipments go directly from origin to destination, so there are no intermediate hubs "
       "and no arrival events to measure hub dwell time.",
  note="A good answer says the data does not support this. Inventing a number is wrong.")

q("U2", "unanswerable", "Where is shipment 42 right now?", "unanswerable", "none",
  text="Cannot be answered. The data has no live truck location; hub ids on delayed events are not positions.",
  note="Can say whether it is delivered or in transit, but not a location.")

q("U3", "unanswerable", "What is the average delivery cost per order?", "unanswerable", "none",
  text="Cannot be answered. There is no cost or price data in the system.")


# ---------- runner ----------
def jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def main(out_path=None):
    out_path = out_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "ground_truth.json")
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("SET TIME ZONE 'UTC'")
    cur.execute("""SELECT GREATEST((SELECT max(event_time) FROM shipment_events),
                                   (SELECT max(updated_at) FROM orders),
                                   (SELECT max(created_at) FROM orders))""")
    as_of = cur.fetchone()[0]
    print(f"as_of = {as_of}")

    errors = 0
    out = []
    for item in QUESTIONS:
        entry = {k: v for k, v in item.items() if k != "sql"}
        if item["sql"]:
            try:
                cur.execute(item["sql"], {"as_of": as_of})
                cols = [d.name for d in cur.description]
                rows = [[jsonable(v) for v in r] for r in cur.fetchall()]
                entry["answer"] = {"columns": cols, "rows": rows}
                preview = rows[0] if rows else "(no rows)"
                print(f"[ OK ] {item['id']:<3} {item['question'][:60]:<60} -> {preview}")
            except Exception as e:  # keep going so every broken query is reported
                conn.rollback()
                cur.execute("SET TIME ZONE 'UTC'")
                errors += 1
                entry["error"] = str(e)
                print(f"[FAIL] {item['id']:<3} {item['question'][:60]}\n         {e}")
        else:
            print(f"[TEXT] {item['id']:<3} {item['question'][:60]}")
        out.append(entry)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"as_of": as_of.isoformat(), "generated_at": datetime.utcnow().isoformat() + "Z",
                   "questions": out}, f, indent=2, ensure_ascii=False)
    n_sql = sum(1 for i in QUESTIONS if i["sql"])
    print(f"\n{len(QUESTIONS)} questions ({n_sql} with SQL answers, {len(QUESTIONS) - n_sql} text-only), "
          f"{errors} query errors. Wrote {out_path}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
