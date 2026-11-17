
"""
LogiStream - Logistics Intelligence Dashboard

Analytics page.
Data: Gold Delta tables through DuckDB.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))

from gold_db import connect, run_query


# ----------------------------------------------------------
# PAGE CONFIG
# ----------------------------------------------------------

st.set_page_config(
    page_title="LogiStream | Analytics",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ----------------------------------------------------------
# STYLING
# ----------------------------------------------------------

st.markdown("""
<style>
    .stApp {
        background-color: #0F172A;
        color: #E2E8F0;
    }

    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        max-width: 1500px;
    }

    [data-testid="stSidebar"] {
        background-color: #020617;
    }

    [data-testid="stSidebar"] * {
        color: #CBD5E1;
    }

    h1, h2, h3 {
        color: #F1F5F9 !important;
    }

    p, label, .stMarkdown {
        color: #CBD5E1;
    }

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

    [data-testid="stPlotlyChart"] {
        background-color: #1E293B;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 8px;
    }

    [data-testid="stDataFrame"] {
        background-color: #1E293B;
        border: 1px solid #334155;
        border-radius: 10px;
    }

    .section-caption {
        color: #94A3B8;
        font-size: 0.9rem;
        margin-top: -10px;
        margin-bottom: 15px;
    }

    hr {
        border-color: #334155;
    }

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
def load_table(table):
    """Load a Gold table through the existing read-only query helper."""

    allowed_tables = {
        "fact_shipments",
        "fact_incidents",
        "agg_daily_overview",
        "agg_hub_daily",
    }

    if table not in allowed_tables:
        raise ValueError("Unknown Gold table.")

    result = run_query(
        get_con(),
        f"SELECT * FROM {table}",
        max_rows=20000,
    )

    return pd.DataFrame(
        result["rows"],
        columns=result["columns"],
    ), result["as_of"]


def format_number(value, suffix=""):
    if pd.isna(value):
        return "N/A"

    if suffix == "%":
        return f"{float(value):.1f}%"

    if suffix == " min":
        return f"{float(value):.0f} min"

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


def filter_by_date(df, column, start_date, end_date):
    """Filter a dataframe using a date column."""

    if df.empty or column not in df.columns:
        return df.copy()

    result = df.copy()

    dates = pd.to_datetime(
        result[column],
        errors="coerce",
    ).dt.date

    mask = (
        (dates >= start_date)
        & (dates <= end_date)
    )

    return result.loc[mask].copy()


def weighted_average(group, value_column, weight_column):
    """Calculate a weighted average while ignoring missing values."""

    valid = (
        group[value_column].notna()
        & group[weight_column].notna()
        & (group[weight_column] > 0)
    )

    if not valid.any():
        return np.nan

    return np.average(
        group.loc[valid, value_column],
        weights=group.loc[valid, weight_column],
    )


# ----------------------------------------------------------
# LOAD DATA
# ----------------------------------------------------------

shipments, as_of = load_table("fact_shipments")
daily, _ = load_table("agg_daily_overview")
hub_daily, _ = load_table("agg_hub_daily")
incidents, _ = load_table("fact_incidents")

if shipments.empty:
    st.error("No shipment data found in the Gold layer.")
    st.stop()


# ----------------------------------------------------------
# PREPARE DATE COLUMNS
# ----------------------------------------------------------

shipments["analytics_date"] = pd.to_datetime(
    shipments["arrival_date"],
    errors="coerce",
).dt.date

# Undelivered shipments may not have an arrival date.
# Use order creation date for those records.
order_dates = pd.to_datetime(
    shipments["order_created_at"],
    errors="coerce",
).dt.date

shipments["analytics_date"] = (
    shipments["analytics_date"].fillna(order_dates)
)


# ----------------------------------------------------------
# SIDEBAR
# ----------------------------------------------------------

with st.sidebar:

    st.markdown("# 📦 LogiStream")
    st.caption("LOGISTICS INTELLIGENCE")

    st.divider()

    st.markdown("**Analytics filters**")

    # Determine the available date range.
    date_values = []

    for df, column in [
        (daily, "date"),
        (shipments, "analytics_date"),
        (incidents, "reported_date"),
    ]:
        if not df.empty and column in df.columns:
            values = pd.to_datetime(
                df[column],
                errors="coerce",
            ).dropna()

            if not values.empty:
                date_values.extend(values.dt.date.tolist())

    if date_values:
        min_date = min(date_values)
        max_date = max(date_values)
    else:
        min_date = pd.Timestamp.today().date()
        max_date = min_date

    selected_dates = st.date_input(
        "Date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    if isinstance(selected_dates, (tuple, list)):
        if len(selected_dates) == 2:
            start_date, end_date = selected_dates
        elif len(selected_dates) == 1:
            start_date = end_date = selected_dates[0]
        else:
            start_date, end_date = min_date, max_date
    else:
        start_date = end_date = selected_dates

    # Collect available hubs.
    hub_names = set()

    for df, column in [
        (shipments, "destination_hub_name"),
        (hub_daily, "hub_name"),
        (incidents, "hub_name"),
    ]:
        if not df.empty and column in df.columns:
            hub_names.update(
                df[column].dropna().astype(str).tolist()
            )

    hub_options = ["All hubs"] + sorted(hub_names)

    selected_hub = st.selectbox(
        "Hub",
        hub_options,
    )

    st.divider()

    st.success("DuckDB connected")
    st.caption("Source: Gold Delta tables")
    st.caption("Data refresh interval: 60 seconds")

    if st.button("↻ Refresh analytics", use_container_width=True):
        load_table.clear()
        st.rerun()


# ----------------------------------------------------------
# APPLY FILTERS
# ----------------------------------------------------------

shipments_filtered = filter_by_date(
    shipments,
    "analytics_date",
    start_date,
    end_date,
)

daily_filtered = filter_by_date(
    daily,
    "date",
    start_date,
    end_date,
)

hub_filtered = filter_by_date(
    hub_daily,
    "date",
    start_date,
    end_date,
)

incidents_filtered = filter_by_date(
    incidents,
    "reported_date",
    start_date,
    end_date,
)


if selected_hub != "All hubs":

    shipments_filtered = shipments_filtered[
        shipments_filtered["destination_hub_name"] == selected_hub
    ]

    hub_filtered = hub_filtered[
        hub_filtered["hub_name"] == selected_hub
    ]

    incidents_filtered = incidents_filtered[
        incidents_filtered["hub_name"] == selected_hub
    ]


# ----------------------------------------------------------
# HEADER
# ----------------------------------------------------------

st.title("Analytics")

st.markdown(
    '<p class="section-caption">'
    'Explore delivery performance, vehicle efficiency, '
    'hub operations and incident patterns across the network.'
    '</p>',
    unsafe_allow_html=True,
)

st.caption(
    f"Data as of: {as_of} · "
    f"Period: {start_date} to {end_date}"
)

st.divider()


# ==========================================================
# 1. DELIVERY PERFORMANCE
# ==========================================================

st.subheader("1. Delivery performance")

st.caption(
    "Understand delivery volumes, punctuality and delivery duration."
)


# Filter to completed shipments for delivery metrics.
delivered = shipments_filtered[
    shipments_filtered["is_delivered"].fillna(False).astype(bool)
].copy()

total_delivered = len(delivered)

total_late = int(
    delivered["is_late"].fillna(False).astype(bool).sum()
)

total_on_time = int(
    delivered["is_on_time"].fillna(False).astype(bool).sum()
)

late_rate = (
    100 * total_late / total_delivered
    if total_delivered else np.nan
)

avg_delivery = delivered["delivery_minutes"].mean()


c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "Delivered shipments",
    format_number(total_delivered),
)

c2.metric(
    "Late deliveries",
    format_number(total_late),
)

c3.metric(
    "Late delivery rate",
    format_number(late_rate, "%"),
)

c4.metric(
    "Average delivery time",
    format_number(avg_delivery, " min"),
)


# Daily trends
daily_chart_data = daily_filtered.copy()

if not daily_chart_data.empty:

    daily_chart_data["date"] = pd.to_datetime(
        daily_chart_data["date"],
        errors="coerce",
    )

    daily_chart_data = daily_chart_data.sort_values("date")

    left, right = st.columns(2)

    with left:

        st.markdown("**Daily delivery volume**")

        volume_data = daily_chart_data.melt(
            id_vars="date",
            value_vars=[
                "on_time_deliveries",
                "late_deliveries",
            ],
            var_name="delivery_type",
            value_name="shipments",
        )

        fig = px.bar(
            volume_data,
            x="date",
            y="shipments",
            color="delivery_type",
            barmode="stack",
            color_discrete_map={
                "on_time_deliveries": "#60A5FA",
                "late_deliveries": "#F97316",
            },
            labels={
                "date": "Date",
                "shipments": "Shipments",
                "delivery_type": "Delivery type",
            },
        )

        fig.update_layout(
            xaxis_title="",
            yaxis_title="Shipments",
            legend_title="",
        )

        st.plotly_chart(
            style_chart(fig),
            use_container_width=True,
        )

    with right:

        st.markdown("**On-time delivery rate trend**")

        fig = px.line(
            daily_chart_data,
            x="date",
            y="on_time_rate_pct",
            markers=True,
            labels={
                "date": "Date",
                "on_time_rate_pct": "On-time rate (%)",
            },
        )

        fig.update_traces(
            line=dict(color="#60A5FA", width=3),
            marker=dict(size=7),
        )

        fig.update_layout(
            xaxis_title="",
            yaxis_title="On-time rate (%)",
            hovermode="x unified",
        )

        st.plotly_chart(
            style_chart(fig),
            use_container_width=True,
        )

else:
    st.info("No delivery trend data available for this period.")


# Delivery duration distribution
if not delivered.empty:

    st.markdown("**Delivery duration distribution**")

    duration_data = delivered[
        delivered["delivery_minutes"].notna()
    ].copy()

    if not duration_data.empty:

        fig = px.histogram(
            duration_data,
            x="delivery_minutes",
            nbins=20,
            labels={
                "delivery_minutes": "Delivery duration (minutes)",
                "count": "Number of shipments",
            },
            color_discrete_sequence=["#60A5FA"],
        )

        fig.update_layout(
            xaxis_title="Delivery duration (minutes)",
            yaxis_title="Number of shipments",
        )

        st.plotly_chart(
            style_chart(fig, height=300),
            use_container_width=True,
        )


st.divider()


# ==========================================================
# 2. VEHICLE PERFORMANCE
# ==========================================================

st.subheader("2. Vehicle performance")

st.caption(
    "Rank vehicles by delivery volume, punctuality and average delivery time."
)

if delivered.empty:

    st.info("No completed shipments available for vehicle analysis.")

else:

    vehicle_data = delivered.copy()

    vehicle_data["vehicle"] = (
        vehicle_data["plate_no"]
        .fillna("Unknown vehicle")
        .astype(str)
    )

    vehicle_summary = (
        vehicle_data
        .groupby(["vehicle_id", "vehicle"], dropna=False)
        .agg(
            deliveries=("shipment_id", "count"),
            late_deliveries=("is_late", "sum"),
            on_time_deliveries=("is_on_time", "sum"),
            avg_delivery_minutes=("delivery_minutes", "mean"),
        )
        .reset_index()
    )

    vehicle_summary["late_rate_pct"] = (
        100 * vehicle_summary["late_deliveries"]
        / vehicle_summary["deliveries"]
    )

    vehicle_summary["on_time_rate_pct"] = (
        100 * vehicle_summary["on_time_deliveries"]
        / vehicle_summary["deliveries"]
    )

    left, right = st.columns(2)

    with left:

        st.markdown("**Top vehicles by deliveries**")

        top_vehicles = vehicle_summary.nlargest(
            10,
            "deliveries",
        ).sort_values("deliveries")

        fig = px.bar(
            top_vehicles,
            x="deliveries",
            y="vehicle",
            orientation="h",
            color="deliveries",
            color_continuous_scale=["#1D4ED8", "#60A5FA"],
            labels={
                "deliveries": "Completed deliveries",
                "vehicle": "Vehicle",
            },
            hover_data=["late_rate_pct", "avg_delivery_minutes"],
        )

        fig.update_layout(
            xaxis_title="Completed deliveries",
            yaxis_title="",
            coloraxis_showscale=False,
        )

        st.plotly_chart(
            style_chart(fig, height=380),
            use_container_width=True,
        )

    with right:

        st.markdown("**Highest late-delivery rates**")

        # Require at least two completed shipments to reduce
        # misleading rankings from vehicles with only one delivery.
        eligible_vehicles = vehicle_summary[
            vehicle_summary["deliveries"] >= 2
        ]

        if eligible_vehicles.empty:
            eligible_vehicles = vehicle_summary

        worst_vehicles = eligible_vehicles.nlargest(
            10,
            "late_rate_pct",
        ).sort_values("late_rate_pct")

        fig = px.bar(
            worst_vehicles,
            x="late_rate_pct",
            y="vehicle",
            orientation="h",
            color="late_rate_pct",
            color_continuous_scale=["#F59E0B", "#EF4444"],
            labels={
                "late_rate_pct": "Late delivery rate (%)",
                "vehicle": "Vehicle",
            },
            hover_data=["deliveries", "late_deliveries"],
        )

        fig.update_layout(
            xaxis_title="Late delivery rate (%)",
            yaxis_title="",
            coloraxis_showscale=False,
        )

        st.plotly_chart(
            style_chart(fig, height=380),
            use_container_width=True,
        )

    st.markdown("**Vehicle performance details**")

    vehicle_display = vehicle_summary.sort_values(
        "deliveries",
        ascending=False,
    ).copy()

    vehicle_display["late_rate_pct"] = (
        vehicle_display["late_rate_pct"].round(1)
    )

    vehicle_display["on_time_rate_pct"] = (
        vehicle_display["on_time_rate_pct"].round(1)
    )

    vehicle_display["avg_delivery_minutes"] = (
        vehicle_display["avg_delivery_minutes"].round(0)
    )

    st.dataframe(
        vehicle_display[
            [
                "vehicle",
                "deliveries",
                "late_deliveries",
                "late_rate_pct",
                "on_time_rate_pct",
                "avg_delivery_minutes",
            ]
        ],
        hide_index=True,
        use_container_width=True,
        column_config={
            "vehicle": "Vehicle",
            "deliveries": "Deliveries",
            "late_deliveries": "Late deliveries",
            "late_rate_pct": st.column_config.NumberColumn(
                "Late rate",
                format="%.1f%%",
            ),
            "on_time_rate_pct": st.column_config.NumberColumn(
                "On-time rate",
                format="%.1f%%",
            ),
            "avg_delivery_minutes": st.column_config.NumberColumn(
                "Avg. delivery (min)",
                format="%.0f",
            ),
        },
    )


st.divider()


# ==========================================================
# 3. HUB COMPARISON
# ==========================================================

st.subheader("3. Hub comparison")

st.caption(
    "Compare destination hubs by delivery volume, punctuality "
    "and operational processing time."
)

if hub_filtered.empty:

    st.info("No hub performance data available for this period.")

else:

    hub_summary = (
        hub_filtered
        .groupby(["hub_id", "hub_name", "city"], dropna=False)
        .agg(
            deliveries=("deliveries", "sum"),
            on_time_deliveries=("on_time_deliveries", "sum"),
            late_deliveries=("late_deliveries", "sum"),
            shipments_dispatched=("shipments_dispatched", "sum"),
        )
        .reset_index()
    )

    hub_summary["on_time_rate_pct"] = (
        100 * hub_summary["on_time_deliveries"]
        / hub_summary["deliveries"].replace(0, np.nan)
    )

    # Calculate weighted averages from daily hub records.
    processing_times = {}
    delivery_times = {}

    for hub_name, group in hub_filtered.groupby("hub_name"):

        processing_times[hub_name] = weighted_average(
            group,
            "avg_hub_processing_minutes",
            "shipments_dispatched",
        )

        delivery_times[hub_name] = weighted_average(
            group,
            "avg_delivery_minutes",
            "deliveries",
        )

    hub_summary["avg_processing_minutes"] = (
        hub_summary["hub_name"].map(processing_times)
    )

    hub_summary["avg_delivery_minutes"] = (
        hub_summary["hub_name"].map(delivery_times)
    )

    left, right = st.columns(2)

    with left:

        st.markdown("**Delivery volume by hub**")

        chart_data = hub_summary.sort_values(
            "deliveries",
            ascending=True,
        )

        fig = px.bar(
            chart_data,
            x="deliveries",
            y="hub_name",
            orientation="h",
            color="deliveries",
            color_continuous_scale=["#1D4ED8", "#60A5FA"],
            labels={
                "deliveries": "Deliveries",
                "hub_name": "Hub",
            },
        )

        fig.update_layout(
            xaxis_title="Completed deliveries",
            yaxis_title="",
            coloraxis_showscale=False,
        )

        st.plotly_chart(
            style_chart(fig, height=380),
            use_container_width=True,
        )

    with right:

        st.markdown("**On-time rate comparison**")

        chart_data = hub_summary.sort_values(
            "on_time_rate_pct",
            ascending=True,
        )

        fig = px.bar(
            chart_data,
            x="on_time_rate_pct",
            y="hub_name",
            orientation="h",
            color="on_time_rate_pct",
            color_continuous_scale=["#F97316", "#60A5FA"],
            labels={
                "on_time_rate_pct": "On-time rate (%)",
                "hub_name": "Hub",
            },
        )

        fig.update_layout(
            xaxis_title="On-time rate (%)",
            yaxis_title="",
            coloraxis_showscale=False,
        )

        st.plotly_chart(
            style_chart(fig, height=380),
            use_container_width=True,
        )

    st.markdown("**Hub operational details**")

    hub_display = hub_summary[
        [
            "hub_name",
            "city",
            "deliveries",
            "late_deliveries",
            "on_time_rate_pct",
            "avg_delivery_minutes",
            "avg_processing_minutes",
        ]
    ].copy()

    hub_display["on_time_rate_pct"] = (
        hub_display["on_time_rate_pct"].round(1)
    )

    hub_display["avg_delivery_minutes"] = (
        hub_display["avg_delivery_minutes"].round(0)
    )

    hub_display["avg_processing_minutes"] = (
        hub_display["avg_processing_minutes"].round(0)
    )

    st.dataframe(
        hub_display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "hub_name": "Hub",
            "city": "City",
            "deliveries": "Deliveries",
            "late_deliveries": "Late deliveries",
            "on_time_rate_pct": st.column_config.NumberColumn(
                "On-time rate",
                format="%.1f%%",
            ),
            "avg_delivery_minutes": st.column_config.NumberColumn(
                "Avg. delivery (min)",
                format="%.0f",
            ),
            "avg_processing_minutes": st.column_config.NumberColumn(
                "Avg. processing (min)",
                format="%.0f",
            ),
        },
    )


st.divider()


# ==========================================================
# 4. INCIDENT ANALYSIS
# ==========================================================

st.subheader("4. Incident analysis")

st.caption(
    "Explore reported incidents by date, hub and vehicle."
)

if incidents_filtered.empty:

    st.info("No incident reports found for the selected filters.")

else:

    total_incidents = len(incidents_filtered)

    affected_vehicles = incidents_filtered[
        incidents_filtered["vehicle_id"].notna()
    ]["vehicle_id"].nunique()

    affected_hubs = incidents_filtered[
        incidents_filtered["hub_name"].notna()
    ]["hub_name"].nunique()

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "Total incident reports",
        format_number(total_incidents),
    )

    c2.metric(
        "Vehicles involved",
        format_number(affected_vehicles),
    )

    c3.metric(
        "Hubs affected",
        format_number(affected_hubs),
    )

    incident_data = incidents_filtered.copy()

    incident_data["reported_date"] = pd.to_datetime(
        incident_data["reported_date"],
        errors="coerce",
    )

    left, right = st.columns(2)

    with left:

        st.markdown("**Incident reports over time**")

        incident_trend = (
            incident_data
            .dropna(subset=["reported_date"])
            .groupby("reported_date")
            .size()
            .reset_index(name="incidents")
            .sort_values("reported_date")
        )

        if not incident_trend.empty:

            fig = px.line(
                incident_trend,
                x="reported_date",
                y="incidents",
                markers=True,
                labels={
                    "reported_date": "Date",
                    "incidents": "Incident reports",
                },
            )

            fig.update_traces(
                line=dict(color="#F97316", width=3),
                marker=dict(size=7),
            )

            fig.update_layout(
                xaxis_title="",
                yaxis_title="Incident reports",
            )

            st.plotly_chart(
                style_chart(fig),
                use_container_width=True,
            )

        else:
            st.info("No incident date information available.")

    with right:

        st.markdown("**Incidents by hub**")

        incident_hubs = (
            incident_data
            .groupby("hub_name")
            .size()
            .reset_index(name="incidents")
            .sort_values("incidents", ascending=True)
        )

        incident_hubs = incident_hubs.dropna(
            subset=["hub_name"]
        )

        if not incident_hubs.empty:

            fig = px.bar(
                incident_hubs,
                x="incidents",
                y="hub_name",
                orientation="h",
                color="incidents",
                color_continuous_scale=["#F59E0B", "#EF4444"],
                labels={
                    "incidents": "Incident reports",
                    "hub_name": "Hub",
                },
            )

            fig.update_layout(
                xaxis_title="Incident reports",
                yaxis_title="",
                coloraxis_showscale=False,
            )

            st.plotly_chart(
                style_chart(fig),
                use_container_width=True,
            )

        else:
            st.info("No hub information available for incidents.")

    # Vehicle incident rankings
    st.markdown("**Vehicles with the most incident reports**")

    incident_vehicles = (
        incident_data
        .dropna(subset=["plate_no"])
        .groupby("plate_no")
        .size()
        .reset_index(name="incident_reports")
        .sort_values("incident_reports", ascending=False)
        .head(10)
    )

    if not incident_vehicles.empty:

        fig = px.bar(
            incident_vehicles.sort_values("incident_reports"),
            x="incident_reports",
            y="plate_no",
            orientation="h",
            color_discrete_sequence=["#F97316"],
            labels={
                "incident_reports": "Incident reports",
                "plate_no": "Vehicle",
            },
        )

        fig.update_layout(
            xaxis_title="Incident reports",
            yaxis_title="Vehicle",
        )

        st.plotly_chart(
            style_chart(fig, height=300),
            use_container_width=True,
        )

    # Actual descriptions rather than invented categories.
    st.markdown("**Recent incident reports**")

    recent_incidents = incident_data.sort_values(
        "reported_at",
        ascending=False,
    )

    incident_columns = [
        "incident_id",
        "reported_at",
        "hub_name",
        "city",
        "plate_no",
        "description",
    ]

    st.dataframe(
        recent_incidents[incident_columns],
        hide_index=True,
        use_container_width=True,
        column_config={
            "incident_id": "Incident ID",
            "reported_at": "Reported at",
            "hub_name": "Hub",
            "city": "City",
            "plate_no": "Vehicle",
            "description": "Description",
        },
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
