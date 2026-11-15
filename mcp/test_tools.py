"""
test_tools.py - checks the MCP tools against the answer key (ground_truth.json)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import server as s  # noqa: E402

GT_PATH = os.path.join(HERE, "..", "eval", "ground_truth.json")
with open(GT_PATH, encoding="utf-8") as f:
    GT = json.load(f)
Q = {q["id"]: q for q in GT["questions"]}

results = []


def same(a, b):
    if a is None or b is None:
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 0.011
    return str(a) == str(b)


def report(qid, problems):
    ok = not problems
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {qid}  {Q[qid]['question'][:55]}")
    for p in problems[:5]:
        print("        ", p)


def check_one(qid, tool_row, cols):
    """Answer is a single row. cols maps key column -> tool column."""
    ans = Q[qid]["answer"]
    row = ans["rows"][0]
    problems = []
    for kc, tc in cols.items():
        kv = row[ans["columns"].index(kc)]
        if not same(kv, tool_row.get(tc)):
            problems.append(f"{kc}: key={kv}  tool={tool_row.get(tc)}")
    report(qid, problems)


def check_table(qid, tool_rows, cols):
    """Answer is many rows. The first mapped column identifies the row.
    Every row of the key must exist in the tool output with equal values."""
    ans = Q[qid]["answer"]
    first = next(iter(cols))
    by_id = {str(r[cols[first]]): r for r in tool_rows}
    problems = []
    if not ans["rows"]:
        problems.append("key has 0 rows (nothing to compare)")
    for row in ans["rows"]:
        ident = str(row[ans["columns"].index(first)])
        t = by_id.get(ident)
        if t is None:
            problems.append(f"missing in tool: {ident}")
            continue
        for kc, tc in cols.items():
            kv = row[ans["columns"].index(kc)]
            if not same(kv, t[tc]):
                problems.append(f"{ident} {kc}: key={kv}  tool={t[tc]}")
    report(qid, problems)


def main():
    print(f"key as_of  = {GT['as_of']}")
    ds = s.delivery_summary()
    oc = s.order_counts()
    hp = s.hub_performance()
    print(f"tool as_of = {ds['as_of']}\n")

    # delivery
    check_one("D1", ds["rows"][0], {"avg_delivery_minutes": "avg_delivery_minutes"})
    check_one("D2", ds["rows"][0], {"late_pct": "late_pct",
                                    "late_shipments": "late_deliveries",
                                    "delivered_shipments": "delivered"})
    key_ids = {r[0] for r in Q["D3"]["answer"]["rows"]}
    tool_ids = {r["shipment_id"] for r in s.currently_delayed_shipments()["rows"]}
    report("D3", [] if key_ids == tool_ids else
           [f"key has {len(key_ids)} shipments, tool has {len(tool_ids)}",
            f"only in key: {sorted(key_ids - tool_ids)[:10]}",
            f"only in tool: {sorted(tool_ids - key_ids)[:10]}"])
    check_one("D4", ds["rows"][0], {"on_time_today": "on_time_today"})
    check_one("D5", ds["rows"][0], {"avg_delay_minutes": "avg_delay_minutes_late"})
    city = s.city_performance()["rows"]
    check_table("D6", city, {"city": "city", "deliveries": "deliveries",
                             "on_time_pct": "on_time_pct"})

    # hubs
    check_table("H1", hp["rows"], {"hub": "hub", "late_deliveries": "late_deliveries"})
    two = s.hub_performance(["Tagore Garden Depot", "Noida Sector 62 Depot"])["rows"]
    check_table("H2", two, {"hub": "hub", "deliveries": "deliveries",
                            "late_deliveries": "late_deliveries",
                            "on_time_pct": "on_time_pct",
                            "avg_delay_minutes_late": "avg_delay_minutes_late"})
    check_table("H3", hp["rows"], {"hub": "hub", "shipments": "shipments_passed"})
    check_table("H4", hp["rows"], {"hub": "hub", "incidents": "incidents"})
    check_table("H5", hp["rows"], {"hub": "hub",
                                   "avg_processing_minutes": "avg_hub_processing_minutes"})
    check_table("H6", hp["rows"], {"hub": "hub", "deliveries": "deliveries",
                                   "on_time_pct": "on_time_pct"})

    # orders
    o = oc["rows"][0]
    check_one("O1", o, {"cancelled_today": "cancelled_today"})
    check_one("O2", o, {"cancelled": "cancelled", "total_orders": "total_orders",
                        "cancellation_pct": "cancellation_pct"})
    check_one("O3", o, {"packed_orders": "packed"})
    check_one("O4", o, {"created_last_hour": "created_last_hour"})
    check_one("O5", o, {"in_transit_orders": "in_transit"})
    check_one("O6", s.order_lookup(100)["rows"][0],
              {"order_id": "order_id", "status": "order_status"})
    check_table("O7", s.cancellation_by_city()["rows"],
                {"city": "city", "orders": "orders", "cancelled": "cancelled",
                 "cancellation_pct": "cancellation_pct"})

    # cities
    check_table("C1", city, {"city": "city", "deliveries": "deliveries",
                             "on_time_pct": "on_time_pct", "late_pct": "late_pct",
                             "avg_delivery_minutes": "avg_delivery_minutes"})
    check_table("C2", city, {"city": "city", "avg_delivery_minutes": "avg_delivery_minutes"})

    # vehicles
    check_table("V1", s.vehicle_ranking("incidents")["rows"],
                {"plate_no": "plate_no", "incidents": "incidents"})
    check_table("V2", s.vehicle_ranking("shipments")["rows"],
                {"plate_no": "plate_no", "shipments": "shipments"})
    check_table("V3", s.vehicle_ranking("late_shipments")["rows"],
                {"plate_no": "plate_no", "late_shipments": "late_shipments"})

    # trends
    dt = s.daily_trends()["rows"]
    check_table("T1", dt, {"day": "date", "deliveries": "deliveries",
                           "late_deliveries": "late_deliveries", "late_pct": "late_pct"})
    check_table("T2", dt, {"day": "date", "deliveries": "deliveries", "late_pct": "late_pct"})
    check_table("T3", dt, {"day": "date", "deliveries": "deliveries",
                           "avg_delivery_minutes": "avg_delivery_minutes"})

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())