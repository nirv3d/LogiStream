"""
The dashboard reads the gold Delta tables through the same DuckDB layer the MCP
server uses (mcp\\gold_db.py). It does NOT call the LLM, so it works without
Ollama or the Groq key.
"""
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))
from gold_db import connect, run_query  # noqa: E402

st.set_page_config(page_title="LogiStream", layout="wide")


@st.cache_resource
def get_con():
    return connect()


@st.cache_data(ttl=60)
def query(sql):
    """Run a read-only query and return (DataFrame, as_of)."""
    res = run_query(get_con(), sql, max_rows=5000)
    return pd.DataFrame(res["rows"], columns=res["columns"]), res["as_of"]


# ------------------------------------------------------------------ header
st.title("LogiStream: Delhi-NCR delivery dashboard")

kpi_sql = """
SELECT
  (SELECT count(*) FROM fact_orders)                                  AS orders,
  (SELECT count(*) FILTER (WHERE order_status = 'in_transit') FROM fact_orders) AS in_transit,
  (SELECT count(*) FILTER (WHERE is_cancelled) FROM fact_orders)      AS cancelled,
  (SELECT count(*) FILTER (WHERE is_delivered) FROM fact_shipments)   AS delivered,
  (SELECT round(100.0 * count(*) FILTER (WHERE is_late)
          / nullif(count(*) FILTER (WHERE is_delivered), 0), 1)
     FROM fact_shipments)                                             AS late_pct,
  (SELECT round(100.0 * count(*) FILTER (WHERE is_on_time)
          / nullif(count(*) FILTER (WHERE is_delivered), 0), 1)
     FROM fact_shipments)                                             AS on_time_pct,
  (SELECT round(avg(delivery_minutes) FILTER (WHERE is_delivered), 0)
     FROM fact_shipments)                                             AS avg_delivery_min,
  (SELECT count(*) FROM fact_shipments WHERE is_currently_delayed)    AS currently_delayed
"""
kpi, as_of = query(kpi_sql)
k = kpi.iloc[0]

st.caption(f"Data as of {as_of} (the newest event in the lake). Days are Indian time.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Orders", f"{int(k.orders):,}")
c2.metric("Delivered shipments", f"{int(k.delivered):,}")
c3.metric("Late deliveries", f"{k.late_pct}%")
c4.metric("On-time deliveries", f"{k.on_time_pct}%")

c5, c6, c7, c8 = st.columns(4)
c5.metric("Avg delivery time", f"{int(k.avg_delivery_min)} min")
c6.metric("In transit", f"{int(k.in_transit):,}")
c7.metric("Cancelled", f"{int(k.cancelled):,}")
c8.metric("Currently delayed", f"{int(k.currently_delayed):,}")

st.divider()

# ------------------------------------------------------------ daily trend
daily, _ = query("""
SELECT date, deliveries, late_deliveries,
       round(100.0 * late_deliveries / nullif(deliveries, 0), 1) AS late_pct,
       incidents
FROM agg_daily_overview ORDER BY date""")

left, right = st.columns(2)
with left:
    st.subheader("Late delivery rate by day")
    fig = px.line(daily, x="date", y="late_pct", markers=True,
                  labels={"date": "", "late_pct": "Late %"})
    st.plotly_chart(fig, use_container_width=True)
    st.caption("The newest day is usually partial (it ends at as_of).")

with right:
    st.subheader("Deliveries and late deliveries by day")
    melted = daily.melt(id_vars="date", value_vars=["deliveries", "late_deliveries"],
                        var_name="type", value_name="count")
    fig = px.bar(melted, x="date", y="count", color="type", barmode="group",
                 labels={"date": "", "count": "Shipments"})
    st.plotly_chart(fig, use_container_width=True)

# ------------------------------------------------------------- hub view
hubs, _ = query("""
SELECT destination_hub_name AS hub,
       count(*) FILTER (WHERE is_delivered) AS deliveries,
       count(*) FILTER (WHERE is_late) AS late_deliveries,
       round(100.0 * count(*) FILTER (WHERE is_on_time)
             / nullif(count(*) FILTER (WHERE is_delivered), 0), 1) AS on_time_pct
FROM fact_shipments GROUP BY 1 ORDER BY late_deliveries DESC""")

st.subheader("Hubs (attributed to the destination hub)")
left, right = st.columns(2)
with left:
    fig = px.bar(hubs, x="late_deliveries", y="hub", orientation="h",
                 labels={"late_deliveries": "Late deliveries", "hub": ""})
    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True)
with right:
    st.dataframe(hubs, hide_index=True, use_container_width=True)

# -------------------------------------------------------------- incidents
inc, _ = query("""
SELECT hub_name AS hub, count(*) AS incidents
FROM fact_incidents GROUP BY 1 ORDER BY incidents DESC""")

st.subheader("Incidents by hub")
fig = px.bar(inc, x="incidents", y="hub", orientation="h",
             labels={"incidents": "Incident reports", "hub": ""})
fig.update_layout(yaxis={"categoryorder": "total ascending"})
st.plotly_chart(fig, use_container_width=True)
st.caption("Some areas appear under two names (for example 'Delhi' and 'New Delhi') in the source data.")