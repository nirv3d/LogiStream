
"""
LogiStream - Delhi-NCR Logistics Control Tower

Reads Gold Delta tables through the same DuckDB layer used by the MCP server.
The dashboard does not call the LLM, so it works without Ollama or a Groq API key.
"""

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))

from gold_db import connect, run_query  # noqa: E402


# ---------------------------------------------------------- PAGE CONFIG

st.set_page_config(
    page_title="LogiStream | Control Tower",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------- DATA ACCESS

@st.cache_resource
def get_con():
    return connect()


@st.cache_data(ttl=60, show_spinner=False)
def query(sql):
    """Run a read-only query and return (DataFrame, as_of)."""
    res = run_query(get_con(), sql, max_rows=5000)

    return (
        pd.DataFrame(res["rows"], columns=res["columns"]),
        res["as_of"],
    )


def format_number(value, suffix=""):
    """Safely format numbers, including missing values."""
    if pd.isna(value):
        return "N/A"

    if suffix == "%":
        return f"{float(value):.1f}%"

    return f"{int(value):,}{suffix}"


def show_empty_message(df, message="No data available for this section."):
    if df.empty:
        st.info(message)
        return True

    return False


# ---------------------------------------------------------- SIDEBAR

with st.sidebar:
    st.title("📦 LogiStream")

    st.caption("DELHI-NCR LOGISTICS INTELLIGENCE")

    st.divider()

    st.subheader("Data connection")

    st.success("DuckDB connected")

    st.caption("Data source: Gold Delta tables")

    if st.button("🔄 Refresh dashboard", use_container_width=True):
        query.clear()
        st.rerun()

    st.caption("Dashboard automatically refreshes cached queries every 60 seconds.")

    st.divider()

    st.caption("AI assistant powered separately by Groq + RAG + MCP.")


# ---------------------------------------------------------- HEADER

st.title("Logistics Control Tower")

st.markdown(
    "Monitor delivery operations, shipment performance, "
    "hub activity and incident reports across Delhi-NCR."
)


# ---------------------------------------------------------- KPI DATA

kpi_sql = """
SELECT
    (SELECT count(*)
     FROM fact_orders) AS orders,

    (SELECT count(*) FILTER (
        WHERE order_status = 'in_transit'
     )
     FROM fact_orders) AS in_transit,

    (SELECT count(*) FILTER (
        WHERE is_cancelled
     )
     FROM fact_orders) AS cancelled,

    (SELECT count(*) FILTER (
        WHERE is_delivered
     )
     FROM fact_shipments) AS delivered,

    (SELECT count(*) FILTER (
        WHERE is_late
     )
     FROM fact_shipments) AS late_deliveries,

    (SELECT round(
        100.0 * count(*) FILTER (WHERE is_late)
        / nullif(count(*) FILTER (WHERE is_delivered), 0), 1
     )
     FROM fact_shipments) AS late_pct,

    (SELECT round(
        100.0 * count(*) FILTER (WHERE is_on_time)
        / nullif(count(*) FILTER (WHERE is_delivered), 0), 1
     )
     FROM fact_shipments) AS on_time_pct,

    (SELECT round(
        avg(delivery_minutes) FILTER (WHERE is_delivered), 0
     )
     FROM fact_shipments) AS avg_delivery_min,

    (SELECT count(*)
     FROM fact_shipments
     WHERE is_currently_delayed) AS currently_delayed
"""

kpi, as_of = query(kpi_sql)

if kpi.empty:
    st.error("Could not retrieve dashboard KPI data.")
    st.stop()

k = kpi.iloc[0]

st.caption(
    f"Data as of: {as_of} | "
    "Based on the newest event available in the data lake."
)


# ---------------------------------------------------------- KPI CARDS

st.subheader("Operational overview")

c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "Total orders",
    format_number(k["orders"]),
)

c2.metric(
    "Delivered shipments",
    format_number(k["delivered"]),
)

c3.metric(
    "Late delivery rate",
    format_number(k["late_pct"], "%"),
)

c4.metric(
    "On-time delivery rate",
    format_number(k["on_time_pct"], "%"),
)


c5, c6, c7, c8 = st.columns(4)

c5.metric(
    "Average delivery time",
    format_number(k["avg_delivery_min"], " min"),
)

c6.metric(
    "In transit orders",
    format_number(k["in_transit"]),
)

c7.metric(
    "Cancelled orders",
    format_number(k["cancelled"]),
)

c8.metric(
    "Currently delayed shipments",
    format_number(k["currently_delayed"]),
)

st.divider()


# ---------------------------------------------------------- DAILY TRENDS

st.subheader("Delivery trends")

daily, _ = query("""
SELECT
    date,
    deliveries,
    late_deliveries,
    round(
        100.0 * late_deliveries / nullif(deliveries, 0), 1
    ) AS late_pct,
    incidents
FROM agg_daily_overview
ORDER BY date
""")

if not show_empty_message(daily, "No daily trend data available."):

    left, right = st.columns(2)

    with left:
        st.markdown("**Late delivery rate by day**")

        fig = px.line(
            daily,
            x="date",
            y="late_pct",
            markers=True,
            labels={
                "date": "Date",
                "late_pct": "Late delivery rate (%)",
            },
            hover_data={
                "deliveries": True,
                "late_deliveries": True,
            },
        )

        fig.update_layout(
            yaxis_title="Late delivery rate (%)",
            xaxis_title="",
            hovermode="x unified",
        )

        st.plotly_chart(fig, use_container_width=True)

        st.caption(
            "The newest day may be a partial day, ending at the latest event time."
        )

    with right:
        st.markdown("**Deliveries vs late deliveries**")

        melted = daily.melt(
            id_vars="date",
            value_vars=["deliveries", "late_deliveries"],
            var_name="type",
            value_name="count",
        )

        fig = px.bar(
            melted,
            x="date",
            y="count",
            color="type",
            barmode="group",
            labels={
                "date": "Date",
                "count": "Shipments",
                "type": "Category",
            },
        )

        fig.update_layout(
            xaxis_title="",
            yaxis_title="Number of shipments",
            legend_title="",
        )

        st.plotly_chart(fig, use_container_width=True)


st.divider()


# ---------------------------------------------------------- HUB PERFORMANCE

st.subheader("Hub performance")

st.caption(
    "Delivery metrics are attributed to the destination hub."
)

hubs, _ = query("""
SELECT
    destination_hub_name AS hub,

    count(*) FILTER (
        WHERE is_delivered
    ) AS deliveries,

    count(*) FILTER (
        WHERE is_late
    ) AS late_deliveries,

    round(
        100.0 * count(*) FILTER (WHERE is_on_time)
        / nullif(count(*) FILTER (WHERE is_delivered), 0), 1
    ) AS on_time_pct

FROM fact_shipments
GROUP BY 1
ORDER BY late_deliveries DESC
""")

if not show_empty_message(hubs, "No hub performance data available."):

    left, right = st.columns([1.2, 1])

    with left:
        st.markdown("**Late deliveries by hub**")

        fig = px.bar(
            hubs,
            x="late_deliveries",
            y="hub",
            orientation="h",
            labels={
                "late_deliveries": "Late deliveries",
                "hub": "Destination hub",
            },
            hover_data=["deliveries", "on_time_pct"],
        )

        fig.update_layout(
            yaxis={"categoryorder": "total ascending"},
            xaxis_title="Late deliveries",
            yaxis_title="",
        )

        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("**Hub performance details**")

        display_hubs = hubs.copy()

        display_hubs["on_time_pct"] = (
            display_hubs["on_time_pct"].apply(
                lambda x: format_number(x, "%")
            )
        )

        st.dataframe(
            display_hubs,
            hide_index=True,
            use_container_width=True,
        )


st.divider()


# ---------------------------------------------------------- DELHI-NCR HUB MAP

st.divider()
st.subheader("Delhi-NCR Hub Network")

st.caption(
    "Geographical overview of the logistics hubs. "
    "Marker positions are approximate locality coordinates."
)

hub_locations = pd.DataFrame([
    {"hub": "Connaught Place Central", "latitude": 28.6315, "longitude": 77.2167},
    {"hub": "Tagore Garden Depot", "latitude": 28.6507, "longitude": 77.1023},
    {"hub": "Dwarka Depot", "latitude": 28.5921, "longitude": 77.0460},
    {"hub": "Rohini Depot", "latitude": 28.7495, "longitude": 77.0565},
    {"hub": "Noida Sector 62 Depot", "latitude": 28.6270, "longitude": 77.3720},
    {"hub": "Mayur Vihar", "latitude": 28.6070, "longitude": 77.2955},
    {"hub": "Greater Noida Depot", "latitude": 28.4752, "longitude": 77.5217},
    {"hub": "Cyber City Depot", "latitude": 28.4944, "longitude": 77.0906},
])

# Display the map using Streamlit's built-in map component.
st.map(
    hub_locations,
    latitude="latitude",
    longitude="longitude",
    size=180,
    color="#16A34A",
    zoom=9,
    use_container_width=True,
)

# Show hub names below the map.
st.caption("Registered logistics hubs")

st.dataframe(
    hub_locations[["hub"]],
    hide_index=True,
    use_container_width=True,
)


# ---------------------------------------------------------- INCIDENT ANALYTICS

st.subheader("Incident analytics")

inc, _ = query("""
SELECT
    hub_name AS hub,
    count(*) AS incidents
FROM fact_incidents
GROUP BY 1
ORDER BY incidents DESC
""")

if not show_empty_message(inc, "No incident reports available."):

    left, right = st.columns([1.2, 1])

    with left:
        st.markdown("**Incident reports by hub**")

        fig = px.bar(
            inc,
            x="incidents",
            y="hub",
            orientation="h",
            labels={
                "incidents": "Incident reports",
                "hub": "Hub",
            },
        )

        fig.update_layout(
            yaxis={"categoryorder": "total ascending"},
            xaxis_title="Number of incidents",
            yaxis_title="",
        )

        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("**Incident report details**")

        st.dataframe(
            inc,
            hide_index=True,
            use_container_width=True,
        )

    st.caption(
        "Some areas may appear under different names in the source data, "
        "such as Delhi and New Delhi."
    )


# ---------------------------------------------------------- FOOTER

st.divider()

st.caption(
    "LogiStream | Real-time logistics intelligence | "
    "PostgreSQL → Debezium → Redpanda → Spark → Delta Lake → DuckDB"
)
