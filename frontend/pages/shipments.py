import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Locate the existing MCP database helper
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp"))

from gold_db import connect, get_as_of


st.set_page_config(
    page_title="Shipment Explorer | LogiStream",
    page_icon="📦",
    layout="wide",
)


# --------------------------------------------------
# Database
# --------------------------------------------------

@st.cache_resource
def get_connection():
    return connect()


@st.cache_data(ttl=60)
def get_shipments():
    con = get_connection()
    return con.execute("""
        SELECT *
        FROM fact_shipments
        ORDER BY order_created_at DESC
    """).df()


def format_datetime(value):
    if pd.isna(value):
        return "—"
    return pd.to_datetime(value).strftime("%d %b %Y, %I:%M %p")


def format_minutes(value):
    if pd.isna(value):
        return "—"

    minutes = int(value)
    hours, mins = divmod(minutes, 60)

    if hours:
        return f"{hours}h {mins}m"
    return f"{mins} min"


# --------------------------------------------------
# Styling
# --------------------------------------------------

st.markdown("""
<style>
    .stApp {
        background-color: #0F172A;
        color: #E2E8F0;
    }

    section[data-testid="stSidebar"] {
        background-color: #020617;
    }

    div[data-testid="stMetric"] {
        background: #1E293B;
        border: 1px solid #334155;
        padding: 16px;
        border-radius: 10px;
    }

    div[data-testid="stMetricLabel"] {
        color: #94A3B8;
    }

    div[data-testid="stMetricValue"] {
        color: #E2E8F0;
    }

    h1, h2, h3 {
        color: #F1F5F9;
    }

    p, label {
        color: #CBD5E1;
    }

    hr {
        border-color: #334155;
    }
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------
# Header
# --------------------------------------------------

st.title("Shipment Explorer")
st.caption("Search, investigate and monitor individual shipments.")

con = get_connection()
shipments = get_shipments()

if shipments.empty:
    st.warning("No shipment records found in the Gold layer.")
    st.stop()

as_of = get_as_of(con)

st.caption(f"Data as of: {as_of}")

st.divider()


# --------------------------------------------------
# Search
# --------------------------------------------------

st.subheader("Find a shipment")

search_id = st.text_input(
    "Shipment ID",
    placeholder="Enter a shipment ID...",
)

if search_id.strip():

    matching = shipments[
        shipments["shipment_id"].astype(str) == search_id.strip()
    ]

    if matching.empty:
        st.error(f"No shipment found with ID {search_id}.")
    else:
        shipment = matching.iloc[0]

        st.success(f"Shipment #{shipment['shipment_id']} found.")

        # Status
        status = str(shipment["shipment_status"]).replace("_", " ").title()

        st.markdown(f"### {status}")

        # Shipment details
        st.subheader("Shipment overview")

        c1, c2, c3, c4 = st.columns(4)

        c1.metric("Shipment ID", shipment["shipment_id"])
        c2.metric("Order ID", shipment["order_id"])
        c3.metric("Vehicle", shipment["plate_no"] or "—")
        c4.metric(
            "Delivery status",
            "Delivered" if shipment["is_delivered"] else "Not delivered",
        )

        st.divider()

        # Route
        st.subheader("Route")

        origin, destination = st.columns(2)

        with origin:
            st.markdown("**Origin**")
            st.markdown(f"### {shipment['origin_hub_name']}")
            st.caption(shipment["origin_city"])

        with destination:
            st.markdown("**Destination**")
            st.markdown(f"### {shipment['destination_hub_name']}")
            st.caption(shipment["destination_city"])

        st.divider()

        # Delivery performance
        st.subheader("Delivery information")

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Planned arrival",
            format_datetime(shipment["planned_arrival"]),
        )

        c2.metric(
            "Actual arrival",
            format_datetime(shipment["actual_arrival"]),
        )

        c3.metric(
            "Delivery duration",
            format_minutes(shipment["delivery_minutes"]),
        )

        c4.metric(
            "Delay",
            format_minutes(shipment["delay_minutes"]),
        )

        # Additional information
        with st.expander("More shipment details"):
            details = {
                "Vehicle ID": shipment["vehicle_id"],
                "Vehicle plate": shipment["plate_no"],
                "Customer city": shipment["customer_city"],
                "Order created": format_datetime(shipment["order_created_at"]),
                "Departed at": format_datetime(shipment["departed_at"]),
                "Transit duration": format_minutes(shipment["transit_minutes"]),
                "Current delay": format_minutes(shipment["current_delay_minutes"]),
                "Delayed event count": shipment["delayed_event_count"],
            }

            st.dataframe(
                pd.DataFrame(
                    details.items(),
                    columns=["Field", "Value"],
                ),
                hide_index=True,
                use_container_width=True,
            )

        st.divider()

        # Event history placeholder
        st.subheader("Shipment event history")

        st.info(
            "Individual shipment events are not included in fact_shipments. "
            "We'll connect the existing Silver shipment_events table here "
            "to display the actual chronological event history."
        )


# --------------------------------------------------
# Shipment browser
# --------------------------------------------------

st.divider()

st.subheader("Browse all shipments")

col1, col2, col3 = st.columns(3)

with col1:
    statuses = ["All"] + sorted(
        shipments["shipment_status"].dropna().astype(str).unique().tolist()
    )

    selected_status = st.selectbox("Shipment status", statuses)

with col2:
    hubs = ["All"] + sorted(
        shipments["destination_hub_name"].dropna().astype(str).unique().tolist()
    )

    selected_hub = st.selectbox("Destination hub", hubs)

with col3:
    late_only = st.checkbox("Show late shipments only")

filtered = shipments.copy()

if selected_status != "All":
    filtered = filtered[
        filtered["shipment_status"] == selected_status
    ]

if selected_hub != "All":
    filtered = filtered[
        filtered["destination_hub_name"] == selected_hub
    ]

if late_only:
    filtered = filtered[filtered["is_late"] == True]


# Quick counts
c1, c2, c3 = st.columns(3)

c1.metric("Matching shipments", f"{len(filtered):,}")

c2.metric(
    "Delivered",
    f"{int(filtered['is_delivered'].sum()):,}",
)

c3.metric(
    "Late",
    f"{int(filtered['is_late'].sum()):,}",
)


# Display table
display_columns = [
    "shipment_id",
    "order_id",
    "shipment_status",
    "origin_hub_name",
    "destination_hub_name",
    "plate_no",
    "order_created_at",
    "is_delivered",
    "is_late",
    "delay_minutes",
]

st.dataframe(
    filtered[display_columns].sort_values(
        "order_created_at",
        ascending=False,
    ),
    use_container_width=True,
    hide_index=True,
    column_config={
        "shipment_id": "Shipment ID",
        "order_id": "Order ID",
        "shipment_status": "Status",
        "origin_hub_name": "Origin",
        "destination_hub_name": "Destination",
        "plate_no": "Vehicle",
        "order_created_at": "Order created",
        "is_delivered": "Delivered",
        "is_late": "Late",
        "delay_minutes": st.column_config.NumberColumn(
            "Delay (minutes)",
            format="%d min",
        ),
    },
)

st.caption(f"Showing {len(filtered):,} shipments.")
