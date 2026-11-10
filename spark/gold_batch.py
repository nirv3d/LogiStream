"""
building the GOLD layer from SILVER.


Gold tables written to /lake/gold/:
    fact_orders        one row per order
    fact_shipments     one row per shipment
    fact_incidents     one row per incident (keeps the description text)
    agg_hub_daily      one row per hub per day (Indian time)
    agg_daily_overview one row per day for the whole network
    meta               one row: as_of (latest event time in the lake) and built_at

"""
