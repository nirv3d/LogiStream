
"""
LogiStream - Logistics Intelligence Dashboard

Overview page.
Data: Gold Delta tables through DuckDB.
"""

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))

from gold_db import connect, run_query


# ----------------------------------------------------------
# PAGE CONFIG
# ----------------------------------------------------------

st.set_page_config(
    page_title="LogiStream | Overview",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ----------------------------------------------------------
# STYLING
# ----------------------------------------------------------



st.markdown("""
<style>
    /* Main application background */
    .stApp {
        background-color: #0F172A;
        color: #E2E8F0;
    }

    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        max-width: 1500px;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #020617;
    }

    [data-testid="stSidebar"] * {
        color: #CBD5E1;
    }

    /* Main headings */
    h1, h2, h3 {
        color: #F1F5F9 !important;
    }

    p, label, .stMarkdown {
        color: #CBD5E1;
    }

    /* KPI cards */
    [data-testid="stMetric"] {
        background-color: #1E293B;
        border: 1px solid #334155;
        padding: 18px 20px;
        border-radius: 12px;
    }

    [data-testid="stMetricLabel"] {
        color: #94A3B8 !important;
    }

    [data-testid="stMetricValue"] {
        color: #60A5FA !important;
        font-weight: 700;
    }

    /* Chart containers */
    [data-testid="stPlotlyChart"] {
        background-color: #1E293B;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 8px;
    }

    /* DataFrames */
    [data-testid="stDataFrame"] {
        background-color: #1E293B;
        border: 1px solid #334155;
        border-radius: 10px;
    }

    /* Captions */
    .section-caption {
        color: #94A3B8;
        font-size: 0.9rem;
        margin-top: -10px;
        margin-bottom: 15px;
    }

    /* Dividers */
    hr {
        border-color: #334155;
    }

    /* Buttons */
    .stButton > button {
        background-color: #2563EB;
        color: #FFFFFF;
        border: none;
        border-radius: 8px;
    }

    .stButton > button:hover {
        background-color: #1D4ED8;
        color: #FFFFFF;
        border: none;
    }
</style>
""", unsafe_allow_html=True)




# ----------------------------------------------------------
# DATA ACCESS
# ----------------------------------------------------------

@st.cache_resource
def get_con():
    return connect()


@st.cache_data(ttl=60, show_spinner=False)
def query(sql):
    """Run a read-only query and return DataFrame and as_of."""
    res = run_query(get_con(), sql, max_rows=5000)

    return (
        pd.DataFrame(res["rows"], columns=res["columns"]),
        res["as_of"],
    )


def format_number(value, suffix=""):
    if pd.isna(value):
        return "N/A"

    if suffix == "%":
        return f"{float(value):.1f}%"

    return f"{int(value):,}{suffix}"


def show_empty_message(df, message="No data available."):
    if df.empty:
        st.info(message)
        return True
    return False


def style_chart(fig, height=340):
    """Apply consistent dark styling to Plotly charts."""
    fig.update_layout(
        template="plotly_dark",
        height=height,
        margin=dict(l=15, r=15, t=25, b=15),
        font=dict(
            family="Arial",
            color="#E2E8F0",
            size=12,
        ),
        paper_bgcolor="#1E293B",
        plot_bgcolor="#1E293B",
        hoverlabel=dict(
            bgcolor="#0F172A",
            font_color="#E2E8F0",
            font_size=12,
        ),
    )

    fig.update_xaxes(
        showgrid=True,
        gridcolor="#334155",
        zeroline=False,
        color="#94A3B8",
    )

    fig.update_yaxes(
        showgrid=False,
        zeroline=False,
        color="#94A3B8",
    )

    return fig


# ----------------------------------------------------------
# SIDEBAR
# ----------------------------------------------------------

with st.sidebar:

    st.markdown("# 📦 LogiStream")

    st.caption("LOGISTICS INTELLIGENCE")

    st.divider()

    st.markdown("**Data connection**")

    st.success("DuckDB connected")

    st.caption("Source: Gold Delta tables")

    if st.button(
        "↻ Refresh dashboard",
        use_container_width=True,
    ):
        query.clear()
        st.rerun()

    st.caption("Data refresh interval: 60 seconds")

    st.divider()

    st.caption(
        "Analytics powered by DuckDB and Delta Lake."
    )


# ----------------------------------------------------------
# HEADER
# ----------------------------------------------------------

st.title("Overview")

st.markdown(
    '<p class="section-caption">'
    'Monitor logistics operations, delivery performance and '
    'network activity across Delhi-NCR.'
    '</p>',
    unsafe_allow_html=True,
)


# ----------------------------------------------------------
# KPI DATA
# ----------------------------------------------------------

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
    f"Data as of: {as_of} · "
    "Based on the newest event available in the data lake."
)


# ----------------------------------------------------------
# KPI CARDS
# ----------------------------------------------------------

st.subheader("Delivery performance")

c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "Delivered shipments",
    format_number(k["delivered"]),
)

c2.metric(
    "On-time delivery rate",
    format_number(k["on_time_pct"], "%"),
)

c3.metric(
    "Late delivery rate",
    format_number(k["late_pct"], "%"),
)

c4.metric(
    "Average delivery time",
    format_number(k["avg_delivery_min"], " min"),
)

st.subheader("Order activity")

c5, c6, c7, c8 = st.columns(4)

c5.metric(
    "Total orders",
    format_number(k["orders"]),
)

c6.metric(
    "In-transit orders",
    format_number(k["in_transit"]),
)

c7.metric(
    "Cancelled orders",
    format_number(k["cancelled"]),
)

c8.metric(
    "Currently delayed",
    format_number(k["currently_delayed"]),
)


st.divider()


# ----------------------------------------------------------
# DELIVERY TRENDS
# ----------------------------------------------------------

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

        st.markdown("**Late delivery rate**")

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

        fig.update_traces(
            line=dict(width=3),
            marker=dict(size=7),
        )

        fig.update_layout(
            xaxis_title="",
            yaxis_title="Late delivery rate (%)",
            hovermode="x unified",
        )

        st.plotly_chart(
            style_chart(fig),
            use_container_width=True,
        )

        st.caption(
            "The latest day may be partial, ending at the newest "
            "event recorded in the data lake."
        )

    with right:

        st.markdown("**Deliveries vs late deliveries**")

        melted = daily.melt(
            id_vars="date",
            value_vars=[
                "deliveries",
                "late_deliveries",
            ],
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

        st.plotly_chart(
            style_chart(fig),
            use_container_width=True,
        )


st.divider()


# ----------------------------------------------------------
# HUB PERFORMANCE
# ----------------------------------------------------------

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
            hover_data=[
                "deliveries",
                "on_time_pct",
            ],
        )

        fig.update_layout(
            yaxis={"categoryorder": "total ascending"},
            xaxis_title="Late deliveries",
            yaxis_title="",
        )

        st.plotly_chart(
            style_chart(fig, height=380),
            use_container_width=True,
        )

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
            height=380,
        )


st.divider()


# ----------------------------------------------------------
# HUB NETWORK MAP
# ----------------------------------------------------------

st.subheader("Delhi-NCR hub network")

st.caption(
    "Geographical overview of registered logistics hubs. "
    "Marker positions are approximate locality coordinates."
)

hub_locations = pd.DataFrame([
    {
        "hub": "Connaught Place Central",
        "latitude": 28.6315,
        "longitude": 77.2167,
    },
    {
        "hub": "Tagore Garden Depot",
        "latitude": 28.6507,
        "longitude": 77.1023,
    },
    {
        "hub": "Dwarka Depot",
        "latitude": 28.5921,
        "longitude": 77.0460,
    },
    {
        "hub": "Rohini Depot",
        "latitude": 28.7495,
        "longitude": 77.0565,
    },
    {
        "hub": "Noida Sector 62 Depot",
        "latitude": 28.6270,
        "longitude": 77.3720,
    },
    {
        "hub": "Mayur Vihar",
        "latitude": 28.6070,
        "longitude": 77.2955,
    },
    {
        "hub": "Greater Noida Depot",
        "latitude": 28.4752,
        "longitude": 77.5217,
    },
    {
        "hub": "Cyber City Depot",
        "latitude": 28.4944,
        "longitude": 77.0906,
    },
])

st.map(
    hub_locations,
    latitude="latitude",
    longitude="longitude",
    size=180,
    color="#2563EB",
    zoom=9,
    use_container_width=True,
)

with st.expander("View registered hubs"):

    st.dataframe(
        hub_locations[["hub"]],
        hide_index=True,
        use_container_width=True,
    )


st.divider()


# ----------------------------------------------------------
# INCIDENT ANALYTICS
# ----------------------------------------------------------

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

        st.plotly_chart(
            style_chart(fig, height=340),
            use_container_width=True,
        )

    with right:

        st.markdown("**Incident report details**")

        st.dataframe(
            inc,
            hide_index=True,
            use_container_width=True,
            height=340,
        )

    st.caption(
        "Some areas may appear under different names in the source "
        "data, such as Delhi and New Delhi."
    )


# ----------------------------------------------------------
# FOOTER
# ----------------------------------------------------------

st.divider()

st.caption(
    "LogiStream · Logistics Intelligence · "
    "PostgreSQL → Debezium → Redpanda → Spark → "
    "Delta Lake → DuckDB"
)
